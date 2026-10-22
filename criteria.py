"""
Assessment criteria: the single source of pass/fail rules.

Every threshold, regional assumption and verdict rule that decides
how a result is judged lives here, so the Excel workbook, the
dashboard facts and (once grafted) the interactive colour map all
apply the same rules. Change a standard here and every output moves
together.

This module is pure: no PowerFactory API calls beyond reading the
line type name, no pandas, no I/O. It can be imported and tested
offline.

Regions:
    SEQ, NORTHERN, SOUTHERN: Values returned by obtain_region.
    is_seq, is_regional: Region tests. Use these rather than comparing
        region strings, so a renamed or added region fails loudly in
        one place instead of silently taking an else branch.

Fault impedance:
    earth_fault_resistance: Minimum earth fault resistance assumed for
        a region and construction.

Reach:
    primary_rf_threshold, BACKUP_RF_THRESHOLD, SN_BACKUP_RF_THRESHOLD.

Coordination:
    RELAY_RELAY_MARGIN_S, FUSE_PAIR_MARGIN_S, required_margin.

Conductor damage:
    is_swer_line, allowable_energy, evaluate_damage and the verdict
    strings PASS, FAIL, NO_DATA, SWER.
"""

from typing import Any, Optional

# =============================================================================
# REGIONS
# =============================================================================

SEQ = 'SEQ'
NORTHERN = 'Northern'
SOUTHERN = 'Southern'

REGIONAL_REGIONS = frozenset({NORTHERN, SOUTHERN})
KNOWN_REGIONS = frozenset({SEQ}) | REGIONAL_REGIONS


def is_seq(region: str) -> bool:
    """True for the SEQ (Energex) network region."""
    return region == SEQ


def is_regional(region: str) -> bool:
    """True for a Regional (Ergon) network region."""
    return region in REGIONAL_REGIONS


def _require_known(region: str) -> None:
    """Raise ValueError naming the known regions if region is not one."""
    if region not in KNOWN_REGIONS:
        raise ValueError(
            f"Unknown network region {region!r}; expected one of "
            f"{sorted(KNOWN_REGIONS)}. Add it to criteria.py."
        )


# =============================================================================
# FAULT IMPEDANCE
# =============================================================================

# Minimum earth fault resistance in ohms. SEQ assumes a bolted fault
# everywhere. Regional assumes 50 ohms on overhead construction and
# 10 ohms elsewhere. "Elsewhere" includes SWER and any terminal whose
# construction is undetermined: this preserves the behaviour of the
# original fault_impedance.get_terminal_pg_fault, which tested only
# for 'OH'.
SEQ_EF_RESISTANCE_OHM = 0
REGIONAL_OH_EF_RESISTANCE_OHM = 50
REGIONAL_OTHER_EF_RESISTANCE_OHM = 10


def earth_fault_resistance(region: str, constr: Optional[str]) -> int:
    """
    Minimum earth fault resistance assumed at a location.

    Args:
        region: Network region from obtain_region.
        constr: Construction class at the location: 'OH', 'UG', 'SWER'
            or None if undetermined.

    Returns:
        Fault resistance in ohms. Termination dataclasses carry
        minimum earth fault levels for 0, 10 and 50 ohms only, so a
        value outside that set needs a matching field first.

    Raises:
        ValueError: If the region is not known.
    """
    _require_known(region)
    if is_seq(region):
        return SEQ_EF_RESISTANCE_OHM
    if constr == 'OH':
        return REGIONAL_OH_EF_RESISTANCE_OHM
    return REGIONAL_OTHER_EF_RESISTANCE_OHM


# =============================================================================
# REACH FACTOR THRESHOLDS
# =============================================================================

# A reach factor at or above the threshold is covered.
PRIMARY_RF_THRESHOLD = {
    SEQ: 2.0,
    NORTHERN: 1.7,
    SOUTHERN: 1.7,
}
BACKUP_RF_THRESHOLD = 1.3
SN_BACKUP_RF_THRESHOLD = 1.5


def primary_rf_threshold(region: str) -> float:
    """
    Primary reach factor threshold for a region.

    Args:
        region: Network region from obtain_region.

    Returns:
        Minimum acceptable primary reach factor.

    Raises:
        ValueError: If the region is not known.
    """
    _require_known(region)
    return PRIMARY_RF_THRESHOLD[region]


# =============================================================================
# COORDINATION MARGINS
# =============================================================================

# Required coordination margins in seconds. A relay-relay pair needs
# 300 ms; any pair with a fuse on either side (fuse-fuse, fuse primary
# with relay backup, relay primary with fuse backup) needs 100 ms.
# Times compared: relay trip time (no switch operate time) for every
# relay; fuse total clear time, except a backup fuse behind a relay
# primary, which uses minimum melt. See required_margin.
RELAY_RELAY_MARGIN_S = 0.3
FUSE_PAIR_MARGIN_S = 0.1


def required_margin(primary_is_fuse: bool, backup_is_fuse: bool) -> float:
    """
    Minimum coordination margin for a primary/backup pair.

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


# =============================================================================
# CONDUCTOR DAMAGE
# =============================================================================

PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO DATA"
SWER = "SWER"


def is_swer_line(line: Any) -> bool:
    """
    True for a SWER line (line type name contains 'SWER').

    Args:
        line: Line dataclass; its obj is the PowerFactory ElmLne.

    Returns:
        True if the line's type name contains 'SWER'. False if it
        does not, or if the line has no type.
    """
    try:
        line_type = line.obj.typ_id
        return bool(line_type) and 'SWER' in line_type.loc_name
    except AttributeError:
        return False


def allowable_energy(thermal_rating: Any) -> Optional[float]:
    """
    Conductor thermal withstand energy in A²s.

    The 1-second thermal rating I_thr implies a withstand energy of
    I_thr² × 1 s (from I²t = constant).

    Args:
        thermal_rating: Conductor 1-second thermal rating in Amperes.
            May be "NA" for cable systems.

    Returns:
        Withstand energy in A²s, or None if the rating is missing or
        non-numeric.
    """
    try:
        return float(thermal_rating) ** 2
    except (ValueError, TypeError):
        return None


def evaluate_damage(line: Any, fault_type: str) -> str:
    """
    Conductor damage verdict for a line section.

    Compares the let-through energy accumulated across all trips of
    the auto-reclose sequence (populated by cond_damage) against the
    conductor's thermal withstand energy.

    Args:
        line: Line dataclass with accumulated energy and thermal data.
        fault_type: 'Phase' or 'Ground'.

    Returns:
        PASS if within withstand, FAIL if exceeded, NO_DATA if the
        rating is missing or no energy was computed, SWER for a phase
        fault on a SWER line (not applicable).
    """
    if fault_type == 'Phase' and is_swer_line(line):
        return SWER

    if fault_type == 'Phase':
        energy = line.ph_energy
    else:
        energy = line.pg_energy

    allowable = allowable_energy(line.thermal_rating)

    if allowable is None or not energy:
        return NO_DATA
    if energy > allowable:
        return FAIL
    return PASS
