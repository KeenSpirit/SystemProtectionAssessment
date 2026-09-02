"""
Dashboard fact assembly for the Power BI protection dashboard.

Pure module: no PowerFactory API access, no file IO. It walks fully
populated Feeder dataclasses and returns flat rows; the batch writer
(ProtectionBatchRunner side) adds run identity and persists them. This
keeps every rule in here testable offline with pytest against stub
dataclasses.

Two tables come out, sharing the feeder/device key:

    device rows - one per protection device. Coordination fields plus
        overhead-km attributions for conductor damage and reach, per
        tier. Device sections are exclusive (get_device_sections strips
        overlaps), so feeder and region totals are plain sums of these
        rows - there is no separate feeder table to reconcile.

    line rows - one per sectioned line. The raw reach factors, damage
        verdicts and derived statuses, for drill-through and for
        re-cutting history against different thresholds without
        re-running 80 projects.

Only overhead length is in scope (constr 'OH' or 'SWER'); underground
lines appear in line rows for completeness but contribute no km.

Exception semantics
-------------------
A line has a REACH exception at a tier when either fault class fails
as a pair:

    EF pair:  dedicated earth element AND the NPS earth view
    PH pair:  dedicated phase element AND the NPS phase view

A pair fails when neither member covers the line. Per value:

    COVERED       numeric reach factor >= tier threshold
    SHORT         numeric reach factor  < tier threshold
    NO COVERAGE   'NA' because the pickup is not configured - the
                  function does not exist, so it cannot cover
    UNKNOWN       'NA' with a configured pickup (fault level missing),
                  or a reach factor of 0.0, which _line_safe_min
                  produces from an absent result and which is
                  therefore data absence, not a measurement

Pair status: COVERED if either member is COVERED; EXCEPTION if at
least one member is SHORT and the other is SHORT or NO COVERAGE;
otherwise UNASSESSABLE (any UNKNOWN present, or both NO COVERAGE).

Line status per tier: EXCEPTION if either pair is an exception,
COVERED if both pairs are covered, else UNASSESSABLE.

Conductor damage reuses the verdicts from cond_dmg_results verbatim:
FAIL -> exception km, PASS -> ok, anything else (NO DATA, SWER) ->
unassessable km. "Any damage" is the per-line OR of the two classes,
never the sum of their kms, so a line failing both counts its length
once.

Thresholds
----------
Primary reach: 2.0 (SEQ) / 1.7 (Regional Models). Backup: 1.3.
System normal backup: 1.5. Coordination margin: 0.3 s - not applied
here; margins are emitted raw and the dashboard applies the line.
Every row carries the thresholds that were applied, so historic rows
remain interpretable if the standards move.

ASSUMPTIONS.md entries this module introduces:
    * Unconfigured pickup counts as "no coverage" in the pair rule; a
      measured-short partner therefore yields an exception. Both
      members unconfigured yields unassessable, not exception.
    * A reach factor of 0.0 is treated as absent data (unassessable),
      never as an exception.
    * A tier with one pair covered and the other unassessable is
      unassessable for the line - partial assessment is not coverage.
    * SWER lines are overhead for length purposes; their phase-fault
      damage verdict ('SWER') lands in unassessable km.
"""

import logging
from typing import Dict, List, Optional, Tuple

from assets.line import is_overhead
from save_results.cond_dmg_results import _evaluate_damage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------

PRIMARY_RF_THRESHOLD = {
    'SEQ': 2.0,
    'Regional Models': 1.7,
}
BACKUP_RF_THRESHOLD = 1.3
SN_BACKUP_RF_THRESHOLD = 1.5
COORD_MARGIN_THRESHOLD_S = 0.3  # informational; applied in the dashboard

# Reach tiers: tier name -> (ef main key, ef nps key, ph main key,
# ph nps key, pickup prefix). The pickup prefix selects which stored
# pickups decide "configured": primary functions for the primary tier,
# backup functions for both backup tiers (system normal changes the
# fault levels, not the settings).
_TIERS = {
    'pri': ('ef_rf', 'nps_ef_rf', 'ph_rf', 'nps_ph_rf', ''),
    'bu': ('bu_ef_rf', 'bu_nps_ef_rf', 'bu_ph_rf', 'bu_nps_ph_rf', 'bu_'),
    'sn_bu': ('sn_bu_ef_rf', 'sn_bu_nps_ef_rf',
              'sn_bu_ph_rf', 'sn_bu_nps_ph_rf', 'bu_'),
}

COVERED = 'COVERED'
EXCEPTION = 'EXCEPTION'
UNASSESSABLE = 'UNASSESSABLE'


# ---------------------------------------------------------------------
# Region assignment
# ---------------------------------------------------------------------

def determine_region(feeder_name: str) -> str:
    """
    Determine the dashboard region for a feeder.

    PLACEHOLDER - to be replaced with the production implementation.
    Distinct from the pipeline region ('SEQ' / 'Regional Models'),
    which selects fault impedance and thresholds; this one drives the
    per-region grouping in the dashboard.

    Args:
        feeder_name: Feeder name as it appears in the model.

    Returns:
        Region label for dashboard grouping.
    """
    return 'UNASSIGNED'


# ---------------------------------------------------------------------
# Value classification
# ---------------------------------------------------------------------

def _numeric_rf(value) -> Optional[float]:
    """
    Coerce a stored reach factor to a usable float.

    Returns None for the 'NA' sentinel, for anything non-numeric, and
    for values <= 0: _line_safe_min emits 0 where no fault study
    result exists, so a zero here is absence of data, not a
    measurement of zero reach.

    Args:
        value: Stored reach factor (float or 'NA').

    Returns:
        Positive float, or None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _pickup(device, key: str) -> Optional[float]:
    """
    Read a stored pickup value from the device's reach factor dict.

    The pickup lists repeat one value per terminal; the first entry is
    the setting. Returns None when the reach factor stage did not run
    or the key is absent - callers must treat that as "cannot tell",
    not as "not configured".

    Args:
        device: Device dataclass.
        key: Pickup key, e.g. 'ef_pickup' or 'bu_nps_pickup'.

    Returns:
        Pickup in Amperes, or None if unavailable.
    """
    factors = getattr(device, 'reach_factors', None)
    if not factors:
        return None
    values = factors.get(key)
    if not values:
        return None
    value = values[0]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _configured(device, function: str, prefix: str) -> Optional[bool]:
    """
    Report whether a protection function is configured on the device.

    'Configured' follows the calculation layer's own reachability
    rules: the earth fault path runs off the effective EF pickup, so
    EF counts as configured when either the EF or the PH pickup is
    positive; PH and NPS require their own pickup.

    Args:
        device: Device dataclass.
        function: 'ef', 'ph' or 'nps'.
        prefix: '' for primary pickups, 'bu_' for backup pickups.

    Returns:
        True / False, or None when the stored pickups are unavailable
        and no determination can be made.
    """
    if function == 'ef':
        ef = _pickup(device, prefix + 'ef_pickup')
        ph = _pickup(device, prefix + 'ph_pickup')
        if ef is None and ph is None:
            return None
        return (ef or 0) > 0 or (ph or 0) > 0

    pickup = _pickup(device, prefix + f'{function}_pickup')
    if pickup is None:
        return None
    return pickup > 0


def _pair_status(
    main_value,
    nps_value,
    main_configured: Optional[bool],
    nps_configured: Optional[bool],
    threshold: float,
) -> str:
    """
    Combine a dedicated element and its NPS partner into a pair status.

    Args:
        main_value: Stored reach factor of the dedicated element.
        nps_value: Stored reach factor of the NPS view.
        main_configured: Whether the dedicated function exists, or
            None when undeterminable.
        nps_configured: Whether the NPS function exists, or None.
        threshold: Tier threshold.

    Returns:
        COVERED, EXCEPTION or UNASSESSABLE.
    """
    main = _numeric_rf(main_value)
    nps = _numeric_rf(nps_value)

    # Anyone covering settles it.
    if (main is not None and main >= threshold) or \
            (nps is not None and nps >= threshold):
        return COVERED

    main_short = main is not None and main < threshold
    nps_short = nps is not None and nps < threshold

    # A member contributes to an exception if it is measured short, or
    # if it demonstrably does not exist (configured is exactly False -
    # None means "cannot tell" and blocks the exception).
    main_fails = main_short or (main is None and main_configured is False)
    nps_fails = nps_short or (nps is None and nps_configured is False)

    if (main_short or nps_short) and main_fails and nps_fails:
        return EXCEPTION

    return UNASSESSABLE


def _line_reach_status(device, line, tier: str, threshold: float) -> str:
    """
    Reach status of one line at one tier.

    Args:
        device: Owning Device dataclass (for pickup lookups).
        line: Line dataclass with reach_factors populated.
        tier: Key into _TIERS.
        threshold: Tier threshold.

    Returns:
        COVERED, EXCEPTION or UNASSESSABLE.
    """
    factors = getattr(line, 'reach_factors', None)
    if not factors:
        return UNASSESSABLE

    ef_key, nps_ef_key, ph_key, nps_ph_key, prefix = _TIERS[tier]

    ef_pair = _pair_status(
        factors.get(ef_key), factors.get(nps_ef_key),
        _configured(device, 'ef', prefix),
        _configured(device, 'nps', prefix),
        threshold,
    )
    ph_pair = _pair_status(
        factors.get(ph_key), factors.get(nps_ph_key),
        _configured(device, 'ph', prefix),
        _configured(device, 'nps', prefix),
        threshold,
    )

    if EXCEPTION in (ef_pair, ph_pair):
        return EXCEPTION
    if ef_pair == COVERED and ph_pair == COVERED:
        return COVERED
    return UNASSESSABLE


def _damage_status(line, fault_type: str) -> str:
    """
    Conductor damage status of one line for one fault class.

    Reuses the workbook verdict so the dashboard can never disagree
    with the Excel output.

    Args:
        line: Line dataclass with energy fields populated.
        fault_type: 'Phase' or 'Ground'.

    Returns:
        COVERED (pass), EXCEPTION (fail) or UNASSESSABLE.
    """
    verdict = _evaluate_damage(line, fault_type=fault_type)
    if verdict == 'FAIL':
        return EXCEPTION
    if verdict == 'PASS':
        return COVERED
    return UNASSESSABLE


def _combine_any(status_a: str, status_b: str) -> str:
    """
    OR two statuses for the "any damage" roll-up.

    Args:
        status_a: First status.
        status_b: Second status.

    Returns:
        EXCEPTION if either is, COVERED if both are, else UNASSESSABLE.
    """
    if EXCEPTION in (status_a, status_b):
        return EXCEPTION
    if status_a == COVERED and status_b == COVERED:
        return COVERED
    return UNASSESSABLE


# ---------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------

def build_dashboard_facts(
    pipeline_region: str,
    feeders: List,
    include_line_rows: bool = True,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Assemble dashboard fact rows for one project's feeders.

    Args:
        pipeline_region: 'SEQ' or 'Regional Models' - selects the
            primary reach threshold and is recorded on every row.
        feeders: Fully populated Feeder dataclasses (all pipeline
            stages complete, including populate_line_reach_factors
            and cond_damage).
        include_line_rows: Emit the line-grain table as well. Device
            rows alone carry everything the dashboard aggregates;
            line rows add drill-through and threshold re-cutting.

    Returns:
        Tuple of (device_rows, line_rows) as lists of flat dicts.
        Run identity (run id, project, timestamps) is deliberately
        absent - the batch writer owns it.
    """
    pri_threshold = PRIMARY_RF_THRESHOLD.get(pipeline_region, 2.0)
    tier_thresholds = {
        'pri': pri_threshold,
        'bu': BACKUP_RF_THRESHOLD,
        'sn_bu': SN_BACKUP_RF_THRESHOLD,
    }

    device_rows: List[Dict] = []
    line_rows: List[Dict] = []

    for feeder in feeders:
        feeder_name = str(feeder.obj.loc_name)
        region = determine_region(feeder_name)

        for device in feeder.devices:
            device_name = str(device.obj.loc_name)
            backup_names = ', '.join(
                str(d.obj.loc_name) for d in device.us_devices
            )

            oh_km = 0.0
            no_length = 0
            dmg_km = {'ph': 0.0, 'pg': 0.0, 'any': 0.0}
            dmg_un_km = {'ph': 0.0, 'pg': 0.0, 'any': 0.0}
            reach_km = {'pri': 0.0, 'bu': 0.0, 'sn_bu': 0.0}
            reach_un_km = {'pri': 0.0, 'bu': 0.0, 'sn_bu': 0.0}

            for line in device.sect_lines:
                if line is None:
                    continue

                ph_status = _damage_status(line, 'Phase')
                pg_status = _damage_status(line, 'Ground')
                any_status = _combine_any(ph_status, pg_status)
                tier_status = {
                    tier: _line_reach_status(
                        device, line, tier, tier_thresholds[tier]
                    )
                    for tier in _TIERS
                }

                overhead = is_overhead(line)
                length = line.length if overhead else None

                if overhead:
                    if length is None:
                        no_length += 1
                    else:
                        oh_km += length
                        for cls, status in (('ph', ph_status),
                                            ('pg', pg_status),
                                            ('any', any_status)):
                            if status == EXCEPTION:
                                dmg_km[cls] += length
                            elif status == UNASSESSABLE:
                                dmg_un_km[cls] += length
                        for tier, status in tier_status.items():
                            if status == EXCEPTION:
                                reach_km[tier] += length
                            elif status == UNASSESSABLE:
                                reach_un_km[tier] += length

                if include_line_rows:
                    factors = getattr(line, 'reach_factors', None) or {}
                    row = {
                        'feeder': feeder_name,
                        'device': device_name,
                        'region': region,
                        'line': str(line.obj.loc_name),
                        'constr': line.constr,
                        'overhead': overhead,
                        'length_km': line.length,
                        'line_type': line.line_type,
                        'ph_dmg_status': ph_status,
                        'pg_dmg_status': pg_status,
                        'pri_reach_status': tier_status['pri'],
                        'bu_reach_status': tier_status['bu'],
                        'sn_bu_reach_status': tier_status['sn_bu'],
                    }
                    for tier_keys in _TIERS.values():
                        for key in tier_keys[:4]:
                            row[key] = factors.get(key)
                    line_rows.append(row)

            if no_length:
                logger.warning(
                    "%s/%s: %s overhead line(s) have no readable length; "
                    "their km are absent from every total",
                    feeder_name, device_name, no_length,
                )

            device_rows.append({
                'feeder': feeder_name,
                'device': device_name,
                'region': region,
                'pipeline_region': pipeline_region,
                'backup_devices': backup_names,
                'ph_coord_margin_s': device.ph_coord_margin,
                'ph_coord_fl_a': device.ph_coord_fl,
                'pg_coord_margin_s': device.pg_coord_margin,
                'pg_coord_fl_a': device.pg_coord_fl,
                'coord_margin_threshold_s': COORD_MARGIN_THRESHOLD_S,
                'pri_rf_threshold': pri_threshold,
                'bu_rf_threshold': BACKUP_RF_THRESHOLD,
                'sn_bu_rf_threshold': SN_BACKUP_RF_THRESHOLD,
                'oh_km': round(oh_km, 4),
                'oh_lines_no_length': no_length,
                'ph_dmg_km': round(dmg_km['ph'], 4),
                'pg_dmg_km': round(dmg_km['pg'], 4),
                'any_dmg_km': round(dmg_km['any'], 4),
                'ph_dmg_unassessable_km': round(dmg_un_km['ph'], 4),
                'pg_dmg_unassessable_km': round(dmg_un_km['pg'], 4),
                'any_dmg_unassessable_km': round(dmg_un_km['any'], 4),
                'pri_reach_km': round(reach_km['pri'], 4),
                'bu_reach_km': round(reach_km['bu'], 4),
                'sn_bu_reach_km': round(reach_km['sn_bu'], 4),
                'pri_reach_unassessable_km': round(reach_un_km['pri'], 4),
                'bu_reach_unassessable_km': round(reach_un_km['bu'], 4),
                'sn_bu_reach_unassessable_km': round(reach_un_km['sn_bu'], 4),
            })

    return device_rows, line_rows