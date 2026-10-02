"""

For the range of fault currents seen by the primary device,
Calculate the primary device trip time
Calculate the back-up device trip time
Calculate the grading margin
Calculate  minimum grading margin
Store results
"""
import logging
logger = logging.getLogger(__name__)

import math
from typing import Dict, List, Optional, Tuple, Any

from pf_config import pft
from relays import current_conversion, elements, reclose, trip_time
from assets.enums import ElementType


FL_STEP_AMPS = 10

# A backup trip operating in less than this fraction of its reference
# (lockout) trip's time at a fault level is treated as a fast, fuse
# saving trip and is not graded against. See delayed_trip_time.
FAST_TRIP_RATIO = 0.5

# Required coordination margins in seconds. A relay-relay pair needs
# 300 ms; any pair with a fuse on either side (fuse-fuse, fuse primary
# with relay backup, relay primary with fuse backup) needs 100 ms.
# Times compared: relay trip time (no switch operate time) for every
# relay; fuse total clear time, except a backup fuse behind a relay
# primary, which uses minimum melt. See required_margin.
RELAY_RELAY_MARGIN_S = 0.3
FUSE_PAIR_MARGIN_S = 0.1


def is_fuse_device(device) -> bool:
    """True when the Device dataclass wraps a RelFuse."""
    return device.obj.GetClassName() == ElementType.FUSE.value


def required_margin(primary_is_fuse: bool, backup_is_fuse: bool) -> float:
    """
    Minimum coordination margin for a primary/backup pair.

    Pure function: no PowerFactory access, testable offline.

    Args:
        primary_is_fuse: True when the primary device is a fuse.
        backup_is_fuse: True when the backup device is a fuse.

    Returns:
        RELAY_RELAY_MARGIN_S for two relays, otherwise
        FUSE_PAIR_MARGIN_S.
    """
    if primary_is_fuse or backup_is_fuse:
        return FUSE_PAIR_MARGIN_S
    return RELAY_RELAY_MARGIN_S


def prot_coordination(app: pft.Application, devices: List):
    fl_step = FL_STEP_AMPS

    for device in devices:
        dev_obj = device.obj
        total_trips = reclose.get_device_trips(dev_obj)

        logger.info(
            f"Protection coordination assessment: {dev_obj.loc_name}"
        )

        reclose.reset_reclosing(dev_obj)
        trip_count = 1
        worst_ph_coord_fl = None
        worst_ph_coord_margin = None
        worst_ph_coord_required = None
        worst_pg_coord_fl = None
        worst_pg_coord_margin = None
        worst_pg_coord_required = None
        primary_is_fuse = is_fuse_device(device)

        max_phase_fl = trip_time.max_phase_fl(device)
        skip_ph_coord = not device.min_device_2ph or not max_phase_fl
        skip_pg_coord = not device.min_device_pg or not device.max_fl_pg

        # Eligible backups: same-cubicle devices are not backups, and a
        # device is never its own backup. Duplicate references to the
        # same PF object are dropped.
        eligible_bu_devices = []
        seen_bu_objs = set()
        for bu_device in device.us_devices:
            if bu_device.cubicle == device.cubicle:
                continue
            bu_obj = bu_device.obj
            if bu_obj is dev_obj or bu_obj in seen_bu_objs:
                continue
            seen_bu_objs.add(bu_obj)
            eligible_bu_devices.append(bu_device)

        if skip_ph_coord:
            logger.info(f"{dev_obj.loc_name} phase coordination skipped: "
                        f"missing fault level / pickup data")
        if skip_pg_coord:
            logger.info(f"{dev_obj.loc_name} ground coordination skipped: "
                        f"missing fault level / pickup data")
        # Nothing to grade against: record why and skip the primary's
        # trip-time sweep, whose results could not be used.
        if not eligible_bu_devices or (skip_ph_coord and skip_pg_coord):
            device.coord_note = coord_note(
                device, eligible_bu_devices, skip_ph_coord, skip_pg_coord
            )
            continue

        ph_min_fl = ph_max_fl = None
        pg_min_fl = pg_max_fl = None
        if not skip_ph_coord:
            ph_min_fl = int(device.min_device_2ph)
            ph_max_fl = int(max_phase_fl)
        if not skip_pg_coord:
            pg_min_fl = int(device.min_device_pg)
            pg_max_fl = int(device.max_fl_pg)

        # Each backup's active elements on every trip of its reclose
        # sequence, captured once. The backup is restored to trip 1 with
        # its original element status before the primary is assessed, so
        # nothing is left switched while the primary's trips are stepped.
        # A fuse or a relay without reclosing has a single trip.
        bu_ph_candidates = []
        bu_pg_candidates = []
        # Required margin for this primary against each backup, keyed
        # on id() because Device dataclasses are not hashable.
        bu_required = {}
        # Fuse times are total clear, except a backup fuse behind a
        # relay primary, which is graded on its minimum melt. Relay
        # times are trip times (no switch operate time) throughout.
        bu_fuse_curve = (
            trip_time.FUSE_TOTAL_CLEAR if primary_is_fuse
            else trip_time.FUSE_MIN_MELT
        )
        # Backup fuses that should be graded on minimum melt but whose
        # type has only the total clear curve.
        no_min_melt = []
        for bu_device in eligible_bu_devices:
            backup_is_fuse = is_fuse_device(bu_device)
            bu_required[id(bu_device)] = required_margin(
                primary_is_fuse, backup_is_fuse
            )
            if (backup_is_fuse and not primary_is_fuse
                    and not trip_time.fuse_has_min_melt(bu_device.obj)):
                no_min_melt.append(str(bu_device.obj.loc_name))
                logger.warning(
                    f"{dev_obj.loc_name}: backup fuse "
                    f"{bu_device.obj.loc_name} has no minimum melt curve; "
                    f"graded on its total clear curve"
                )
            swer = False
            bu_fault_type = 'Phase-Ground'
            if not skip_pg_coord:
                # Check whether the device is SWER. If so, BU device
                # trip time must consider the FL seen by the bu device.
                swer = swer_check(device, bu_device)
                bu_fault_type = '2-Phase' if swer else 'Phase-Ground'
            fault_types = []
            if not skip_ph_coord:
                fault_types.append('2-Phase')
            if not skip_pg_coord and bu_fault_type not in fault_types:
                fault_types.append(bu_fault_type)
            per_trip = backup_trip_elements(bu_device, fault_types)
            if not skip_ph_coord:
                bu_ph_candidates.append((bu_device, per_trip['2-Phase']))
            if not skip_pg_coord:
                bu_pg_candidates.append(
                    (bu_device, swer, bu_fault_type, per_trip[bu_fault_type])
                )

        # Backup times depend only on the fault level, not on the
        # primary's trip, so each is calculated once per fault level.
        # Each entry is (fastest backup time, required margin for that
        # primary/backup pair), or (None, None) if no backup operates.
        bu_ph_time_cache = {}
        bu_pg_time_cache = {}

        def fastest_backup(timed):
            # timed: [(time or None, required margin), ...]. On a tie
            # the stricter margin is kept.
            best = (None, None)
            for t, req in timed:
                if t is None:
                    continue
                if (best[0] is None or t < best[0]
                        or (t == best[0] and req > best[1])):
                    best = (t, req)
            return best

        def bu_ph_time(fl):
            if fl not in bu_ph_time_cache:
                bu_ph_time_cache[fl] = fastest_backup(
                    (delayed_trip_time(
                        trip_elements, fl, '2-Phase', bu_fuse_curve),
                     bu_required[id(bu_device)])
                    for bu_device, trip_elements in bu_ph_candidates
                )
            return bu_ph_time_cache[fl]

        def bu_pg_time(fl):
            if fl not in bu_pg_time_cache:
                timed = []
                for bu_device, swer, bu_fault_type, trip_elements in bu_pg_candidates:
                    bu_fault_level = (
                        swer_transform(device, bu_device, fl) if swer else fl
                    )
                    timed.append((
                        delayed_trip_time(
                            trip_elements, bu_fault_level, bu_fault_type,
                            bu_fuse_curve
                        ),
                        bu_required[id(bu_device)],
                    ))
                bu_pg_time_cache[fl] = fastest_backup(timed)
            return bu_pg_time_cache[fl]

        # Backup instantaneous pickups are sampled either side too, so a
        # delayed-trip high set is not stepped over by the grid.
        bu_ph_hisets = [
            c for _, trips in bu_ph_candidates for els in trips
            for c in hiset_currents(els)
        ]
        bu_pg_hisets = [
            c for _, swer, _, trips in bu_pg_candidates if not swer
            for els in trips for c in hiset_currents(els)
        ]

        while trip_count <= total_trips:
            block_service_status = reclose.set_enabled_elements(dev_obj)
            try:
                if not skip_ph_coord:
                    # Select only the elements capable of detecting the fault type
                    # and enabled for the current auto-reclose iteration
                    active_elements = get_active_elements(device, '2-Phase')

                    # Sample the grid plus the points either side of
                    # each instantaneous pickup, where operate times
                    # step discontinuously.
                    ph_fl_samples = sample_fault_levels(
                        ph_min_fl, ph_max_fl, fl_step,
                        hiset_currents(active_elements) + bu_ph_hisets
                    )
                    for fl in ph_fl_samples:
                        dev_time = elements_time(active_elements, fl, '2-Phase')
                        bu_time, required = bu_ph_time(fl)
                        if dev_time is None or bu_time is None:
                            continue
                        coord_margin = bu_time - dev_time
                        # Worst point = largest shortfall against the
                        # pair's required margin. Same as smallest raw
                        # margin unless fuse and relay backups govern
                        # at different currents.
                        if (worst_ph_coord_margin is None
                                or coord_margin - required
                                < worst_ph_coord_margin - worst_ph_coord_required):
                            worst_ph_coord_fl = fl
                            worst_ph_coord_margin = coord_margin
                            worst_ph_coord_required = required

                if not skip_pg_coord:
                    active_elements = get_active_elements(device, 'Phase-Ground')
                    pg_fl_samples = sample_fault_levels(
                        pg_min_fl, pg_max_fl, fl_step,
                        hiset_currents(active_elements) + bu_pg_hisets
                    )
                    for fl in pg_fl_samples:
                        dev_time = elements_time(active_elements, fl, 'Phase-Ground')
                        bu_time, required = bu_pg_time(fl)
                        if dev_time is None or bu_time is None:
                            continue
                        coord_margin = bu_time - dev_time
                        if (worst_pg_coord_margin is None
                                or coord_margin - required
                                < worst_pg_coord_margin - worst_pg_coord_required):
                            worst_pg_coord_fl = fl
                            worst_pg_coord_margin = coord_margin
                            worst_pg_coord_required = required
            finally:
                reclose.reset_block_service_status(block_service_status)
            trip_count = reclose.trip_count(dev_obj, increment=True)

        # Update device worst_coord_margin and worst_coord_fl
        device.ph_coord_fl = worst_ph_coord_fl
        device.ph_coord_margin = worst_ph_coord_margin
        device.ph_coord_required = worst_ph_coord_required
        device.pg_coord_fl = worst_pg_coord_fl
        device.pg_coord_margin = worst_pg_coord_margin
        device.pg_coord_required = worst_pg_coord_required
        device.coord_note = coord_note(
            device, eligible_bu_devices, skip_ph_coord, skip_pg_coord
        )
        if no_min_melt:
            fallback = (
                    "Backup fuse " + ", ".join(no_min_melt)
                    + " has no minimum melt curve - graded on total clear"
            )
            device.coord_note = "; ".join(
                part for part in (device.coord_note, fallback) if part
            )

        # Leave the recloser at trip 1 rather than trips+1 so the
        # assessment does not persist counter drift into the model.
        reclose.reset_reclosing(dev_obj)


def elements_time(
        prot_elements: List,
        fl: float,
        fault_type: str,
        fuse_curve: str = trip_time.FUSE_TOTAL_CLEAR
) -> Optional[float]:
    """
    Fastest operate time of a set of elements at a fault level.

    Relay elements give their trip time (no switch operate time).
    A fuse gives its time on fuse_curve.

    Args:
        prot_elements: Active elements (or a single-item fuse list).
        fl: Fault current in amps.
        fault_type: '2-Phase' or 'Phase-Ground'.
        fuse_curve: trip_time.FUSE_TOTAL_CLEAR (default) or
            trip_time.FUSE_MIN_MELT.

    Returns:
        The fastest positive operate time in seconds, or None if no
        element operates.
    """
    fastest = None
    for element in prot_elements:
        if element.GetClassName() == ElementType.FUSE.value:
            operate_time = trip_time.fuse_curve_time(element, fl, fuse_curve)
        else:
            element_current = current_conversion.get_measured_current(
                element, fl, fault_type)
            operate_time = trip_time.element_trip_time(element, element_current)
        if not operate_time or operate_time <= 0:
            continue
        if fastest is None or operate_time < fastest:
            fastest = operate_time
    return fastest


def backup_trip_elements(bu_device, fault_types: List[str]) -> Dict[str, List[List]]:
    """
    A backup's active elements on each trip of its reclose sequence.

    Steps the backup through trips 1..N with set_enabled_elements,
    collecting the elements enabled on each trip, and restores every
    element's original service status after each trip and the trip
    counter to 1 at the end. Trip times are calculated later from the
    element settings, which do not depend on service status.

    Args:
        bu_device: Backup Device dataclass.
        fault_types: Fault types to collect elements for.

    Returns:
        {fault_type: [elements on trip 1, elements on trip 2, ...]}.
        Fuses and relays without reclosing give one list per type.
    """
    bu_obj = bu_device.obj
    try:
        total = max(1, int(reclose.get_device_trips(bu_obj) or 1))
    except (TypeError, ValueError):
        total = 1

    per_trip = {fault_type: [] for fault_type in fault_types}
    reclose.reset_reclosing(bu_obj)
    try:
        for trip in range(1, total + 1):
            status = reclose.set_enabled_elements(bu_obj)
            try:
                for fault_type in fault_types:
                    per_trip[fault_type].append(
                        get_active_elements(bu_device, fault_type)
                    )
            finally:
                reclose.reset_block_service_status(status)
            if trip < total:
                reclose.trip_count(bu_obj, increment=True)
    finally:
        reclose.reset_reclosing(bu_obj)
    return per_trip


def delayed_trip_time(
        trip_elements: List[List],
        fl: float,
        fault_type: str,
        fuse_curve: str = trip_time.FUSE_TOTAL_CLEAR
) -> Optional[float]:
    """
    Backup operate time at a fault level, graded on its delayed trips.

    In a fuse saving scheme the first trip(s) of a recloser are fast and
    deliberately beat the downstream fuse; grading is against the later,
    delayed trips. A trip counts as fast at this fault level when it
    operates in less than FAST_TRIP_RATIO of the time of the reference
    trip (the last trip that operates, normally the lockout trip). The
    fastest of the remaining trips is returned. A backup whose trips all
    run the same curves has no fast trips, so every trip is kept and the
    result equals its first-trip time.

    Args:
        trip_elements: Active elements per trip, from backup_trip_elements.
        fl: Fault current seen by the backup, in amps.
        fault_type: '2-Phase' or 'Phase-Ground'.
        fuse_curve: Curve used if the backup is a fuse; see
            elements_time.

    Returns:
        The backup's delayed-trip operate time in seconds, or None if no
        trip operates at this fault level.
    """
    times = [
        elements_time(elements_, fl, fault_type, fuse_curve)
        for elements_ in trip_elements
    ]
    operating = [t for t in times if t is not None]
    if not operating:
        return None
    reference = operating[-1]
    delayed = [t for t in operating if t >= FAST_TRIP_RATIO * reference]
    return min(delayed)


def coord_note(device, eligible_bu_devices, skip_ph, skip_pg) -> str:
    """
    Explain why a coordination margin is blank.

    A backup listed in the summary can still give no margin: relays in
    the same cubicle are listed as each other's backup (for reach
    factors) but are never graded against each other, and the upstream
    transformer or bus relay is often not in the model. Without a note
    those devices show a backup and an empty margin, which reads like a
    failed calculation.

    Args:
        device: Device after prot_coordination has set its margins.
        eligible_bu_devices: Backups actually graded against.
        skip_ph: True if phase coordination was skipped for missing data.
        skip_pg: True if ground coordination was skipped for missing data.

    Returns:
        '' when both margins were calculated, otherwise the reason(s).
    """
    if device.ph_coord_margin is not None and device.pg_coord_margin is not None:
        return ""
    if not device.us_devices:
        return "No backup device found"
    if not eligible_bu_devices:
        return ("No upstream backup modelled - listed backup shares this "
                "device's cubicle and is not graded against it")

    reasons = []
    for label, skipped, margin in (
            ("Phase", skip_ph, device.ph_coord_margin),
            ("Earth", skip_pg, device.pg_coord_margin),
    ):
        if margin is not None:
            continue
        if skipped:
            reasons.append(f"{label}: missing fault level / pickup data")
        else:
            reasons.append(
                f"{label}: no fault current at which both devices operate"
            )
    return "; ".join(reasons)


def get_active_elements(device, fault_type):
    device_obj = device.obj
    if device_obj.GetClassName() == ElementType.FUSE.value:
        active_elements = [device_obj]
    else:
        all_elements = elements.get_prot_elements(device_obj)
        active_elements = elements.get_active_elements(all_elements, fault_type)
    return active_elements


def swer_check(
        ds_device: "Device",
        us_device: "Device",
        ):
    """
    Return True if ds_device is SWER

    :param ds_device:
    :param us_device:
    :return:
    """

    # Check if transformation is needed
    voltage_mismatch = ds_device.l_l_volts != us_device.l_l_volts
    term_single_phase = ds_device.phases == 1
    device_multi_phase = us_device.phases > 1

    if voltage_mismatch and term_single_phase and device_multi_phase:
        return True
    return False


def swer_transform(
        ds_device: "Device",
        us_device: "Device",
        ds_device_fl_pg: float
    ) -> Tuple[int, str]:
    """
    Transform fault current for SWER (Single Wire Earth Return) systems.

    SWER lines operate at different voltages than the main distribution
    system. This function converts the fault current seen at a SWER
    terminal to what the upstream protection device sees.

    The transformation accounts for:
    - Voltage ratio between SWER and distribution system
    - Phase transformation (single-phase SWER to 3-phase distribution)

    Args:
        ds_device: Protection device dataclass.
        us_device: Protection device dataclass.
        ds_device_fl_pg: Phase-ground fault current at terminal in Amperes.

    Returns:
        Fault current as seen by the device in Amperes.
        Returns the original value if no SWER transformation needed.

    Transformation:
        device_fl = (term_volts × term_fl) / (device_volts × √3)

    Example:
        >>> device_current = swer_transform(device, swer_term, 500)
        >>> # If SWER at 12.7kV and device at 22kV:
        >>> # device_current = (12.7 × 500) / (22 × 1.732) ≈ 167A
    """

    if ds_device_fl_pg is None:
        return None

    # SWER transformation required
    us_device_fl = (
            (ds_device.l_l_volts * ds_device_fl_pg / us_device.l_l_volts) / math.sqrt(3)
    )

    return us_device_fl

def hiset_currents(prot_elements: List) -> List[float]:
    """
    Instantaneous pickup settings for the RelIoc elements in a list.

    These are treated as fault-level values. That is exact for
    elements whose measured current equals the fault current, and
    approximate for those with a conversion factor (NPS elements see
    If/3 for an earth fault, If/sqrt(3) for a phase fault). The
    residual error is bounded by the step grid, which brackets every
    discontinuity to within one step regardless.
    """
    return [
        element.GetAttribute("e:cpIpset")
        for element in prot_elements
        if element.GetClassName() == 'RelIoc'
    ]


def sample_fault_levels(
        min_fl: int,
        max_fl: int,
        fl_step: int,
        extra_currents: List
    ) -> List[int]:
    """
    Fault levels to evaluate between min_fl and max_fl.

    A fixed grid of fl_step, plus both interval endpoints and the
    points either side of each supplied pickup current. range()
    excludes max_fl unless the span is an exact multiple of the step,
    and trip time changes steeply either side of an instantaneous
    pickup, so those points are added explicitly rather than being
    left to fall between grid steps.
    """
    if max_fl < min_fl:
        return []

    samples = set(range(min_fl, max_fl + 1, fl_step))
    samples.add(max_fl)

    for current in extra_currents:
        if current is None:
            continue
        for value in (int(current) - 1, int(current)):
            if min_fl <= value <= max_fl:
                samples.add(value)

    return sorted(samples)