"""
Protection reach factor calculations.

This module calculates reach factors for relay devices, which measure
how well a device can detect faults at remote locations in its
protection zone.

Reach Factor = (Minimum Fault Current at Location) / (Device Pickup)

A reach factor > 1.0 means the device can detect faults at that
location. Higher values indicate better protection coverage with
margin for error.

Functions:
    device_reach_factors: Calculate reach factors at multiple locations
    determine_pickup_values: Get effective pickup values for a device
    swer_transform: Transform fault current for SWER systems
"""

import math
from typing import Dict, List, Union, TYPE_CHECKING

from assets.enums import ElementType
from relays.elements import get_prot_elements

if TYPE_CHECKING:
    from pf_config import pft
    from assets.device import Device
    from assets.termination import Termination
    from assets.line import Line

import logging

logger = logging.getLogger(__name__)

# Fuse pickup is taken as twice the fuse rated current: the fuse
# minimum-melt characteristic sits well above rating, and this factor
# approximates the effective pickup used for reach-factor comparison
# against relay elements.
FUSE_PICKUP_RATING_FACTOR = 2

def _safe_ratio(numerator, denominator, divisor: float = 1.0):
    """
    Divide with tolerance for missing fault level data.

    Returns the 'NA' sentinel — already used for unconfigured
    protection functions — when the element has no fault study
    result (None) or the pickup is missing or zero. This keeps a
    single terminal with no result from aborting the whole feeder
    report.

    Args:
        numerator: Fault current in Amperes, or None if the study
            produced no result at that element.
        denominator: Pickup setting in Amperes.
        divisor: Optional sequence factor (3 or sqrt(3)).

    Returns:
        Reach factor rounded to 2 decimals, or 'NA'.
    """
    if numerator is None or not denominator:
        return 'NA'
    return round(numerator / divisor / denominator, 2)


def _element_min_fl_pg(
    region: str,
    element: Union["Termination", "Line"],
    fault_impedance,
    system_normal: bool = False
):
    """
    Get the minimum phase-ground fault level at an element.

    Terminals pass through get_terminal_pg_fault, which applies the
    region and construction dependent fault impedance (0Ω for SEQ,
    50Ω overhead / 10Ω underground for Regional). Lines carry the
    corrected value directly - update_line_data already routes their
    PG levels through the same function - so the two paths agree.

    Args:
        region: Network region ('SEQ', 'Northern', 'Southern').
        element: Termination or Line dataclass.
        fault_impedance: Fault impedance module reference.
        system_normal: Read system normal minima instead of minima.

    Returns:
        Fault current in Amperes, or None where no result exists.
    """
    if element.obj.GetClassName() == ElementType.TERM.value:
        return fault_impedance.get_terminal_pg_fault(
            region, element, system_normal
        )
    return element.min_sn_fl_pg if system_normal else element.min_fl_pg


def _element_min_fl_2ph(
    element: Union["Termination", "Line"],
    system_normal: bool = False
):
    """
    Get the minimum 2-phase fault level at an element.

    Args:
        element: Termination or Line dataclass.
        system_normal: Read system normal minima instead of minima.

    Returns:
        Fault current in Amperes, or None where no result exists.
    """
    return element.min_sn_fl_2ph if system_normal else element.min_fl_2ph


def device_reach_factors(
    region: str,
    device: "Device",
    elements: List[Union["Termination", "Line"]],
    record_device_minima: bool = True
) -> Dict[str, List]:
    """
    Calculate reach factors for a protection device at multiple locations.

    Computes both primary and backup protection reach factors for phase,
    earth, and negative sequence protection functions.

    Args:
        region: Network region ('SEQ', 'Northern', 'Southern').
        device: Protection device dataclass.
        elements: List of Termination or Line dataclasses to evaluate
            reach to.
        record_device_minima: When True (the default), the earth fault
            pass writes device.min_device_2ph and min_device_pg as a
            side effect. Pass False for any call that is not the
            authoritative sect_terms pass - see Side Effects below.

    Returns:
        Dictionary with reach factor data:
        - 'ef_pickup', 'ph_pickup', 'nps_pickup': Primary pickup settings
        - 'ef_rf', 'ph_rf': Primary earth/phase reach factors
        - 'nps_ef_rf', 'nps_ph_rf': Primary NPS reach factors
        - 'bu_ef_pickup', 'bu_ph_pickup', 'bu_nps_pickup': Backup pickups
        - 'bu_ef_rf', 'bu_ph_rf': Backup earth/phase reach factors
        - 'bu_nps_ef_rf', 'bu_nps_ph_rf': Backup NPS reach factors

        Each reach factor list has one value per element in the input
        list. 'NA' indicates the protection function is not available.

    Side Effects:
        With record_device_minima=True, sets device.min_device_2ph and
        device.min_device_pg to the minimum fault levels actually seen
        by the relay across the supplied elements. prot_coordination
        uses these to bound its fault level sweep, so they must come
        from the terminal pass in populate_reach_factors.

        A second call over device.sect_lines would otherwise overwrite
        terminal-derived minima with line-derived ones, silently
        shifting every coordination margin on the feeder. Such calls
        must pass record_device_minima=False. The flag makes this
        independent of the order in which the passes run, which
        sequencing alone would not.

    Example:
        >>> factors = device_reach_factors('SEQ', device, device.sect_terms)
        >>> for i, term in enumerate(device.sect_terms):
        ...     print(f"{term.obj.loc_name}: EF RF = {factors['ef_rf'][i]}")
    """
    # Import here to avoid circular dependency
    from fault_study import fault_impedance

    # Get primary pickup settings
    pickups = determine_pickup_values(device.obj)
    ph_pickup = pickups[0]
    ef_pickup = pickups[1]
    nps_pickup = pickups[2]

    # Effective earth fault pickup (phase elements can see earth faults)
    effective_ef_pickup = _calculate_effective_ef_pickup(ef_pickup, ph_pickup)

    # Calculate primary reach factors
    ef_rf = _calculate_ef_reach_factors(
        region, device, elements, effective_ef_pickup, ph_pickup,
        fault_impedance, record_device_minima
    )
    ph_rf = _calculate_ph_reach_factors(elements, ph_pickup)
    nps_ef_rf, nps_ph_rf = _calculate_nps_reach_factors(
        region, device, elements, nps_pickup, fault_impedance
    )

    primary_results = {
        # Primary pickups (repeated for each element for DataFrame compat)
        'ef_pickup': [ef_pickup] * len(elements),
        'ph_pickup': [ph_pickup] * len(elements),
        'nps_pickup': [nps_pickup] * len(elements),
        # Primary reach factors
        'ef_rf': ef_rf,
        'ph_rf': ph_rf,
        'nps_ef_rf': nps_ef_rf,
        'nps_ph_rf': nps_ph_rf,
    }

    # if device is a fuse, set back-up reach factors equal to primary reach factors
    if device.obj.GetClassName() == ElementType.FUSE.value:
        bu_results = {
            'bu_ef_pickup': primary_results['ef_pickup'],
            'bu_ph_pickup': primary_results['ph_pickup'],
            'bu_nps_pickup': primary_results['nps_pickup'],
            'bu_ef_rf': primary_results['ef_rf'],
            'bu_ph_rf': primary_results['ph_rf'],
            'bu_nps_ef_rf': primary_results['nps_ef_rf'],
            'bu_nps_ph_rf': primary_results['nps_ph_rf'],
        }
    else:
        # Calculate backup reach factors
        bu_results = _calculate_backup_reach_factors(
            region, device, elements, fault_impedance
        )

    # System normal backup reach factors. These answer a different
    # question from the minimum-condition backup factors above -
    # whether backup reach holds under the normal running arrangement
    # rather than under the worst credible outage - and carry their
    # own exception threshold (1.5, against 1.3 for the minimum case).
    #
    # record_device_minima is False on every call here: the
    # authoritative coordination bounds come from the minimum-condition
    # sect_terms pass and must not be overwritten with system normal
    # values.
    if device.obj.GetClassName() == ElementType.FUSE.value:
        sn_raw = {
            'bu_ef_rf': _calculate_ef_reach_factors(
                region, device, elements, effective_ef_pickup, ph_pickup,
                fault_impedance, record_device_minima=False,
                system_normal=True
            ),
            'bu_ph_rf': _calculate_ph_reach_factors(
                elements, ph_pickup, system_normal=True
            ),
        }
        sn_nps_ef_rf, sn_nps_ph_rf = _calculate_nps_reach_factors(
            region, device, elements, nps_pickup, fault_impedance,
            system_normal=True
        )
        sn_raw['bu_nps_ef_rf'] = sn_nps_ef_rf
        sn_raw['bu_nps_ph_rf'] = sn_nps_ph_rf
    else:
        sn_raw = _calculate_backup_reach_factors(
            region, device, elements, fault_impedance, system_normal=True
        )

    sn_results = {
        'sn_bu_ef_rf': sn_raw['bu_ef_rf'],
        'sn_bu_ph_rf': sn_raw['bu_ph_rf'],
        'sn_bu_nps_ef_rf': sn_raw['bu_nps_ef_rf'],
        'sn_bu_nps_ph_rf': sn_raw['bu_nps_ph_rf'],
    }

    return primary_results | bu_results | sn_results


def populate_reach_factors(region: str, devices: List["Device"]) -> None:
    """
    Calculate and store reach factors for each device.

    This is the pipeline stage that owns the reach factor calculation.
    It serves two consumers: format_detailed_results renders the stored
    dictionary, and prot_coordination relies on the min_device_2ph /
    min_device_pg side effects to bound its fault level sweep.

    The stored lists are aligned positionally with sect_terms, so the
    terminal order is recorded alongside them. Anything that re-orders
    or extends sect_terms after this stage invalidates that alignment -
    the reporting layer checks it and recalculates rather than
    rendering misaligned rows.

    Args:
        region: Network region ('SEQ', 'Northern', 'Southern').
        devices: Devices with sect_terms populated - that is, after the
            fault study has completed for this feeder.

    Side Effects:
        Sets reach_factors, reach_factor_terms, min_device_2ph and
        min_device_pg on each device.
    """
    for device in devices:
        device.reach_factors = device_reach_factors(
            region, device, device.sect_terms
        )
        device.reach_factor_terms = list(device.sect_terms)


def populate_line_reach_factors(region: str, devices: List["Device"]) -> None:
    """
    Calculate and store per-line reach factors for each device.

    The line counterpart of populate_reach_factors. Where that stage
    evaluates reach at the section's terminals for the Excel report,
    this one evaluates it at the section's lines so reach exceptions
    can be expressed as a length. Lines carry impedance-corrected
    fault levels from update_line_data, so both passes rest on the
    same fault impedance assumptions.

    Must run after update_line_data (pipeline stage 12) has populated
    line fault levels; earlier, every factor comes back 'NA'. Always
    calls with record_device_minima=False - the coordination sweep
    bounds belong to the terminal pass, regardless of the order the
    two stages run in.

    Args:
        region: Network region ('SEQ', 'Northern', 'Southern').
        devices: Devices with sect_lines populated and line fault
            levels set.

    Side Effects:
        Sets reach_factors on each Line dataclass: the *_rf keys from
        device_reach_factors, one value per family, 'NA' where the
        family cannot be assessed. Devices whose result lists come
        back misaligned with sect_lines store nothing - a partial or
        shifted dict would misattribute reach to the wrong km, which
        is worse than a gap.
    """
    for device in devices:
        lines = device.sect_lines
        if not lines:
            continue

        factors = device_reach_factors(
            region, device, lines, record_device_minima=False
        )

        rf_keys = [key for key in factors if key.endswith('_rf')]

        misaligned = [
            key for key in rf_keys if len(factors[key]) != len(lines)
        ]
        if misaligned:
            logger.error(
                "%s: line reach factor lists misaligned with sect_lines "
                "(%s lines); storing nothing for this device. Keys "
                "affected: %s",
                device.obj.loc_name, len(lines), ", ".join(misaligned)
            )
            continue

        for i, line in enumerate(lines):
            line.reach_factors = {key: factors[key][i] for key in rf_keys}


def determine_pickup_values(
    device_pf: Union["pft.ElmRelay", "pft.RelFuse"]
) -> List[float]:
    """
    Determine the effective pickup values for a protection device.

    For relays, extracts the highest pickup setting from each protection
    function category. For fuses, applies a fuse factor of FUSE_PICKUP_RATING_FACTOR
    x rated current.

    Args:
        device_pf: PowerFactory protection device (ElmRelay or RelFuse).

    Returns:
        List of [phase_pickup, earth_pickup, nps_pickup] in Amperes.
        A value of 0 indicates the function is not configured.

    Note:
        Uses the highest pickup among multiple elements of the same type,
        assuming trip dependency on that particular setting.

    Example:
        >>> pickups = determine_pickup_values(relay)
        >>> ph, ef, nps = pickups
        >>> print(f"Phase: {ph}A, Earth: {ef}A, NPS: {nps}A")
    """
    # Fuse handling - apply factor of FUSE_PICKUP_RATING_FACTOR
    if device_pf.GetClassName() == ElementType.FUSE.value:
        fuse_size = int(device_pf.GetAttribute("r:typ_id:e:irat"))
        return [fuse_size * FUSE_PICKUP_RATING_FACTOR , fuse_size * FUSE_PICKUP_RATING_FACTOR , 0]

    elements = get_prot_elements(device_pf)

    # Phase overcurrent pickup
    oc_elements = elements['oc_idmt_elements']
    if not oc_elements:
        oc_elements = elements['oc_inst_element']

    highest_oc_pickup = 0
    for element in oc_elements:
        pickup = element.GetAttribute("e:cpIpset")
        if pickup > highest_oc_pickup:
            highest_oc_pickup = pickup

    # Earth fault pickup
    ef_elements = elements['ef_idmt_elements']
    if not ef_elements:
        ef_elements = elements['ef_inst_element']

    highest_ef_pickup = 0
    for element in ef_elements:
        pickup = element.GetAttribute("e:cpIpset")
        if pickup > highest_ef_pickup:
            highest_ef_pickup = pickup

    # Negative phase sequence pickup
    nps_elements = elements['nps_idmt_elements'] + elements['nps_inst_elements']

    highest_nps_pickup = 0
    for element in nps_elements:
        pickup = element.GetAttribute("e:cpIpset")
        if pickup > highest_nps_pickup:
            highest_nps_pickup = pickup

    return [
        round(highest_oc_pickup),
        round(highest_ef_pickup),
        round(highest_nps_pickup)
    ]


def swer_transform(
    device: "Device",
    term: "Termination",
    term_fl_pg: float
) -> float:
    """
    Transform fault current for SWER (Single Wire Earth Return) systems.

    SWER lines operate at different voltages than the main distribution
    system. This function converts the fault current seen at a SWER
    terminal to what the upstream protection device sees.

    The transformation accounts for:
    - Voltage ratio between SWER and distribution system
    - Phase transformation (single-phase SWER to 3-phase distribution)

    Args:
        device: Protection device dataclass.
        term: Terminal dataclass at the SWER location.
        term_fl_pg: Phase-ground fault current at terminal in Amperes.

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

    if term_fl_pg is None:
        return None

    # Check if transformation is needed
    voltage_mismatch = term.l_l_volts != device.l_l_volts
    term_single_phase = term.phases == 1
    device_multi_phase = device.phases > 1

    if voltage_mismatch and term_single_phase and device_multi_phase:
        # SWER transformation required
        device_fl = (
            (term.l_l_volts * term_fl_pg / device.l_l_volts) / math.sqrt(3)
        )
    else:
        # No transformation needed
        device_fl = term_fl_pg

    return device_fl


# ============================================================================
# PRIVATE HELPER FUNCTIONS
# ============================================================================

def _calculate_effective_ef_pickup(ef_pickup: float, ph_pickup: float) -> float:
    """
    Calculate effective earth fault pickup considering phase coverage.

    Phase elements can also detect earth faults, so the effective pickup
    is the minimum of earth fault and phase pickups.

    Args:
        ef_pickup: Earth fault element pickup in Amperes.
        ph_pickup: Phase element pickup in Amperes.

    Returns:
        Effective earth fault pickup in Amperes.
    """
    if ef_pickup > 0 and ph_pickup > 0:
        return min(ef_pickup, ph_pickup)
    elif ph_pickup > 0:
        return ph_pickup
    elif ef_pickup > 0:
        return ef_pickup
    return 0


def _calculate_ef_reach_factors(
    region: str,
    device: "Device",
    elements: List,
    effective_ef_pickup: float,
    ph_pickup: float,
    fault_impedance,
    record_device_minima: bool = True,
    system_normal: bool = False
) -> List:
    """
    Calculate earth fault reach factors for all elements.

    Args:
        region: Network region identifier.
        device: Protection device dataclass.
        elements: List of network elements to evaluate.
        effective_ef_pickup: Effective earth fault pickup in Amperes.
        ph_pickup: Phase pickup in Amperes.
        fault_impedance: Fault impedance module reference.
        record_device_minima: When True, write the observed minima back
        to device.min_device_2ph / min_device_pg.

    Returns:
        List of reach factors, one per element. 'NA' if no pickup.
    """
    if effective_ef_pickup <= 0:
        return ['NA'] * len(elements)

    device_min_2ph = None
    device_min_pg = None

    ef_rf = []
    for element in elements:
        element_fl_pg = _element_min_fl_pg(
            region, element, fault_impedance, system_normal
        )

        # No minimum earth fault result at this element; reach cannot
        # be assessed. Lines carry min_fl_pg raw, unlike terminals
        # which pass through get_terminal_pg_fault.
        if element_fl_pg is None:
            ef_rf.append('NA')
            continue

        # Apply SWER transformation if needed
        device_fl = swer_transform(device, element, element_fl_pg)

        # Calculate reach factor
        if device_fl != element_fl_pg:
            # SWER case - device sees 2-phase equivalent
            if device_min_2ph is None or device_fl < device_min_2ph:
                device_min_2ph = device_fl
            rf = _safe_ratio(device_fl, ph_pickup)
        else:
            if device_min_pg is None or device_fl < device_min_pg:
                device_min_pg = device_fl
            rf = _safe_ratio(device_fl, effective_ef_pickup)

        ef_rf.append(rf)

    # Update minimum phase and earth fault currents actually seen by
    # the relay. Only the authoritative sect_terms pass may do this;
    # a supplementary pass over another element set would corrupt the
    # coordination sweep bounds.
    if record_device_minima:
        if device_min_2ph is not None and device_min_2ph < device.min_fl_2ph:
            device.min_device_2ph = device_min_2ph
        else:
            device.min_device_2ph = device.min_fl_2ph
        if device_min_pg is not None:
            device.min_device_pg = device_min_pg
        else:
            device.min_device_pg = device.min_fl_pg

    return ef_rf


def _calculate_ph_reach_factors(
    elements: List,
    ph_pickup: float,
    system_normal: bool = False
) -> List:
    """
    Calculate phase fault reach factors for all elements.

    Args:
        elements: List of network elements to evaluate.
        ph_pickup: Phase pickup in Amperes.

    Returns:
        List of reach factors, one per element. 'NA' if no pickup.
    """
    if ph_pickup <= 0:
        return ['NA'] * len(elements)

    return [
        _safe_ratio(_element_min_fl_2ph(element, system_normal), ph_pickup)
        for element in elements
    ]


def _calculate_nps_reach_factors(
    region: str,
    device: "Device",
    elements: List,
    nps_pickup: float,
    fault_impedance,
    system_normal: bool = False
) -> tuple:
    """
    Calculate NPS reach factors for earth and phase faults.

    Args:
        region: Network region identifier.
        device: Protection device dataclass.
        elements: List of network elements to evaluate.
        nps_pickup: Negative phase sequence pickup in Amperes.
        fault_impedance: Fault impedance module reference.

    Returns:
        Tuple of (nps_ef_rf, nps_ph_rf) lists.
    """
    if nps_pickup <= 0:
        return ['NA'] * len(elements), ['NA'] * len(elements)

    nps_ef_rf = []
    for element in elements:
        element_fl_pg = _element_min_fl_pg(
            region, element, fault_impedance, system_normal
        )

        # No minimum earth fault result at this element; reach cannot
        # be assessed.
        if element_fl_pg is None:
            nps_ef_rf.append('NA')
            continue

        device_fl = swer_transform(device, element, element_fl_pg)

        if device_fl == element_fl_pg:
            # No SWER - device sees earth fault (I2 = If/3)
            rf = _safe_ratio(device_fl, nps_pickup, 3)
        else:
            # SWER - device sees 2-phase equivalent (I2 = If/√3)
            rf = _safe_ratio(device_fl, nps_pickup, math.sqrt(3))

        nps_ef_rf.append(rf)

    # NPS phase fault reach factors
    nps_ph_rf = [
        _safe_ratio(
            _element_min_fl_2ph(element, system_normal),
            nps_pickup,
            math.sqrt(3)
        )
        for element in elements
    ]

    return nps_ef_rf, nps_ph_rf


def _calculate_backup_reach_factors(
    region: str,
    device: "Device",
    elements: List,
    fault_impedance,
    system_normal: bool = False
) -> Dict:
    """
    Calculate backup device reach factors.

    Args:
        region: Network region identifier.
        device: Protection device dataclass.
        elements: List of network elements to evaluate.
        fault_impedance: Fault impedance module reference.
        system_normal: Read system normal minima instead of minimum
            condition values. Pickup settings are unaffected - they
            belong to the backup device, not the study case - so only
            the fault levels change.

    Returns:
        Dictionary with backup reach factor data. Keys are the same
        for both study conditions; the caller relabels the system
        normal results with the sn_ prefix.
    """
    num_elements = len(elements)

    if not device.us_devices:
        # No backup device available
        return {
            'bu_ef_pickup': ['NA'] * num_elements,
            'bu_ph_pickup': ['NA'] * num_elements,
            'bu_nps_pickup': ['NA'] * num_elements,
            'bu_ef_rf': ['NA'] * num_elements,
            'bu_ph_rf': ['NA'] * num_elements,
            'bu_nps_ef_rf': ['NA'] * num_elements,
            'bu_nps_ph_rf': ['NA'] * num_elements,
        }

    # Find the lowest configured pickup setting among all backup
    # devices. determine_pickup_values returns 0 for a function that
    # is not configured, so only positive values are treated as
    # candidates; this makes the result independent of the order in
    # which backup devices are iterated. Falls back to 0 when no
    # backup device has the function configured.
    ef_candidates = []
    ph_candidates = []
    nps_candidates = []

    for bu_device in device.us_devices:
        bu_ph, bu_ef, bu_nps = determine_pickup_values(bu_device.obj)
        if bu_ef > 0:
            ef_candidates.append(bu_ef)
        if bu_ph > 0:
            ph_candidates.append(bu_ph)
        if bu_nps > 0:
            nps_candidates.append(bu_nps)

    bu_ef_pickup = min(ef_candidates) if ef_candidates else 0
    bu_ph_pickup = min(ph_candidates) if ph_candidates else 0
    bu_nps_pickup = min(nps_candidates) if nps_candidates else 0

    # Effective backup earth fault pickup
    effective_bu_ef_pickup = _calculate_effective_ef_pickup(
        bu_ef_pickup, bu_ph_pickup
    )

    # Use first upstream device for SWER transform
    bu_device_for_transform = device.us_devices[0]

    # Backup earth fault reach factors
    bu_ef_rf = _calculate_bu_ef_rf(
        region, elements, bu_device_for_transform, effective_bu_ef_pickup,
        bu_ph_pickup, fault_impedance, system_normal
    )

    # Backup phase reach factors
    if bu_ph_pickup and bu_ph_pickup > 0:
        bu_ph_rf = [
            _safe_ratio(
                _element_min_fl_2ph(element, system_normal), bu_ph_pickup
            )
            for element in elements
        ]
    else:
        bu_ph_rf = ['NA'] * num_elements

    # Backup NPS reach factors
    bu_nps_ef_rf, bu_nps_ph_rf = _calculate_bu_nps_rf(
        region, elements, bu_device_for_transform, bu_nps_pickup,
        fault_impedance, num_elements, system_normal
    )

    return {
        'bu_ef_pickup': [bu_ef_pickup] * num_elements,
        'bu_ph_pickup': [bu_ph_pickup] * num_elements,
        'bu_nps_pickup': [bu_nps_pickup] * num_elements,
        'bu_ef_rf': bu_ef_rf,
        'bu_ph_rf': bu_ph_rf,
        'bu_nps_ef_rf': bu_nps_ef_rf,
        'bu_nps_ph_rf': bu_nps_ph_rf,
    }


def _calculate_bu_ef_rf(
    region: str,
    elements: List,
    bu_device: "Device",
    effective_bu_ef_pickup: float,
    bu_ph_pickup: float,
    fault_impedance,
    system_normal: bool = False
) -> List:
    """
    Calculate backup earth fault reach factors.

    Args:
        region: Network region identifier.
        elements: List of network elements to evaluate.
        bu_device: Backup device for SWER transformation.
        effective_bu_ef_pickup: Effective backup EF pickup in Amperes.
        bu_ph_pickup: Backup phase pickup in Amperes.
        fault_impedance: Fault impedance module reference.
        system_normal: Read system normal minima instead of minimum
            condition values.

    Returns:
        List of backup EF reach factors. 'NA' if no pickup.
    """
    if effective_bu_ef_pickup <= 0:
        return ['NA'] * len(elements)

    bu_ef_rf = []
    for element in elements:
        element_fl_pg = _element_min_fl_pg(
            region, element, fault_impedance, system_normal
        )

        # No earth fault result at this element; reach cannot be
        # assessed.
        if element_fl_pg is None:
            bu_ef_rf.append('NA')
            continue

        bu_device_fl = swer_transform(bu_device, element, element_fl_pg)

        if bu_device_fl != element_fl_pg:
            rf = _safe_ratio(bu_device_fl, bu_ph_pickup)
        else:
            rf = _safe_ratio(bu_device_fl, effective_bu_ef_pickup)

        bu_ef_rf.append(rf)

    return bu_ef_rf


def _calculate_bu_nps_rf(
    region: str,
    elements: List,
    bu_device: "Device",
    bu_nps_pickup: float,
    fault_impedance,
    num_elements: int,
    system_normal: bool = False
) -> tuple:
    """
    Calculate backup NPS reach factors for earth and phase faults.

    Args:
        region: Network region identifier.
        elements: List of network elements to evaluate.
        bu_device: Backup device for SWER transformation.
        bu_nps_pickup: Backup NPS pickup in Amperes.
        fault_impedance: Fault impedance module reference.
        num_elements: Number of elements.
        system_normal: Read system normal minima instead of minimum
            condition values.

    Returns:
        Tuple of (bu_nps_ef_rf, bu_nps_ph_rf) lists.
    """
    if not bu_nps_pickup or bu_nps_pickup <= 0:
        return ['NA'] * num_elements, ['NA'] * num_elements

    bu_nps_ef_rf = []
    for element in elements:
        element_fl_pg = _element_min_fl_pg(
            region, element, fault_impedance, system_normal
        )

        # No earth fault result at this element; reach cannot be
        # assessed.
        if element_fl_pg is None:
            bu_nps_ef_rf.append('NA')
            continue

        bu_device_fl = swer_transform(bu_device, element, element_fl_pg)

        if bu_device_fl == element_fl_pg:
            rf = _safe_ratio(bu_device_fl, bu_nps_pickup, 3)
        else:
            rf = _safe_ratio(bu_device_fl, bu_nps_pickup, math.sqrt(3))

        bu_nps_ef_rf.append(rf)

    bu_nps_ph_rf = [
        _safe_ratio(
            _element_min_fl_2ph(element, system_normal),
            bu_nps_pickup,
            math.sqrt(3)
        )
        for element in elements
    ]

    return bu_nps_ef_rf, bu_nps_ph_rf