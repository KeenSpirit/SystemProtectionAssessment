"""
Conductor damage assessment for protection coordination.

This module evaluates whether protection devices clear faults fast enough
to prevent conductor thermal damage. It calculates let-through energy
(I²t) across the complete auto-reclose sequence and compares against
conductor thermal ratings.

The assessment considers:
- Multiple trips in auto-reclose sequences
- Different protection elements active per trip
- SWER voltage transformation for mixed-voltage protection
- Both phase and earth fault scenarios

Functions:
    cond_damage: Main entry point for conductor damage assessment
    fault_clear_times: Calculate clearing times across fault range
    swer_fault_range: Adjust the fault-level range for SWER lines
    worst_case_energy: Find maximum energy fault condition
    fuse_clear_time: Calculate fuse operating time
    element_trip_time: Calculate relay element operating time
"""

import logging
import math
from typing import Dict, List, Optional, Tuple, Any

from pf_config import pft
from relays import current_conversion, elements, reclose, trip_time
from assets.enums import ElementType
import criteria

logger = logging.getLogger(__name__)

# Fault-current resolution (A) for the worst-case energy search.
# fault_clear_times evaluates clearing time at each fault level
# according to the interval selection:
# INTERVAL = "full": composed of equidistant step sizes between
# min fault level and max fault level.
# INTERVAL = "partial": composed of only the active element hisets between
# min fault level and max fault level.
INTERVAL = "partial"
# When INTERVAL = "full", fault_clear_times evaluates clearing time
# at each fault level from line minimum to maximum in steps of size
# FL_STEP_AMPS; the step that yields the greatest I2t is reported.
# Smaller = finer peak detection but proportionally more PF curve
# evaluations per line per trip. 10 A is
# the validated production value.
FL_STEP_AMPS = 10


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def _prot_elements_for_trip(
    dev_obj: Any,
    trip_count: int,
    cache: Dict[int, Optional[Dict]]
) -> Optional[Dict]:
    """
    Get a device's protection elements for the current trip, cached.

    get_prot_elements performs four recursive GetContents walks and
    filters on out-of-service state, so its result depends on which
    elements reclose.set_enabled_elements has blocked for this trip -
    but not on which line is being assessed. Previously it ran once per
    line per trip per fault type; the same walk repeated for every line
    in a section.

    The cache is per device and keyed on trip number only. Both the
    phase and earth passes reach a given trip through the same
    reset_reclosing / set_enabled_elements sequence, so trip N presents
    the same device state in either pass and the entry is shared.

    Callers must invoke this only while the trip's element state is
    applied - that is, after set_enabled_elements and before
    reset_block_service_status.

    Args:
        dev_obj: PowerFactory relay or fuse object.
        trip_count: Current trip number in the reclose sequence.
        cache: Per-device dict, mutated in place.

    Returns:
        The element dict from get_prot_elements, or None for fuses,
        which carry no sub-elements and are handled directly by
        fault_clear_times.
    """
    if dev_obj.GetClassName() == ElementType.FUSE.value:
        return None

    if trip_count not in cache:
        cache[trip_count] = elements.get_prot_elements(dev_obj)
    return cache[trip_count]


def cond_damage(app: pft.Application, devices: List) -> None:
    """
    Perform conductor damage assessment for selected protection devices.

    For each device, steps through its auto-reclose sequence once for
    phase faults and once for earth faults. On each trip the elements
    blocked for that trip are set out of service, the clearing time is
    found at a set of fault levels across each section line's fault
    range (see fault_clear_times), and the fault level giving the
    greatest I2t is taken as that trip's worst case. Each line's total
    energy is the sum of its per-trip worst cases, which
    cond_dmg_results compares against the conductor's 1-second thermal
    withstand (I_thr^2 x 1 s).

    Args:
        app: PowerFactory application instance.
        devices: List of Device dataclasses with populated sect_lines
            and line fault levels.

    Side Effects:
        Populates the following attributes on each Line in sect_lines:
        - ph_energy: Phase fault I2t summed over all trips (A2s)
        - ph_clear_time: Clearing time of the trip contributing the
          most phase energy (s)
        - ph_fl: Fault level of that trip's worst case (A)
        - pg_energy, pg_clear_time, pg_fl: The same for earth faults.
          On a SWER line fed through an isolating transformer, the
          energy and fault level are line-side values.
        Energy, clearing time and fault level are all None when any
        trip produced no clearing time (reported as NO DATA), and the
        phase values are None on SWER lines (reported as SWER).
        Leaves each recloser reset to trip 1 and every blocked element
        restored to its original service state.

    Note:
        Summing each trip's worst case is conservative: each trip may
        peak at a different fault level, whereas a real fault has one
        current for the whole sequence.

    Example:
        >>> cond_damage(app, feeder.devices)
        >>> for device in feeder.devices:
        ...     for line in device.sect_lines:
        ...         print(f"{line.obj.loc_name}: {line.ph_energy} A2s")
    """
    fl_step = FL_STEP_AMPS

    for device in devices:
        dev_obj = device.obj
        lines = device.sect_lines
        total_trips = reclose.get_device_trips(dev_obj)
        # Protection elements per trip, reused across every line in
        # this device's section and across both fault passes. Scoped to
        # the device so a new device never sees another's elements.
        element_cache = {}
        # Element clearing times and instantaneous pickups, keyed on
        # (element, fault type, fault level). Same device scope, and
        # shared across trips: blocking changes outserv, not curves.
        clear_time_cache = {}

        # Phase fault assessment
        line_fault_type = '2-Phase'
        logger.info(
            f"Phase fault conductor damage assessment: {dev_obj.loc_name}"
        )
        # Lines whose accumulated energy is incomplete, reported once
        # per device rather than once per line.
        incomplete_lines = []
        # Trips outer, lines inner. set_enabled_elements and
        # reset_block_service_status write outserv on every blockable
        # element of the relay; run per line they cost lines x trips
        # round trips per fault type, when the enabled-element state for
        # a given trip is the same for every line in the section. The
        # arithmetic is unchanged - only the order in which it runs.
        reclose.reset_reclosing(dev_obj)
        trip_count = 1
        # Per-line accumulators, indexed in step with `lines`.
        total_energy = [0] * len(lines)
        worst_trip_energy = [0] * len(lines)
        # Trips that produced no clearing time, per line. Any entry
        # means that line's total is missing a contribution of unknown
        # size and must not be reported as if complete.
        incomplete_trips = [[] for _ in lines]
        # A single-wire SWER line cannot carry a phase-to-phase fault, so
        # it is left out of the phase pass. Its phase verdict is reported
        # as "SWER" by cond_dmg_results, not as a failed calculation.
        swer_lines = [criteria.is_swer_line(line) for line in lines]

        while trip_count <= total_trips:
            block_service_status = reclose.set_enabled_elements(dev_obj)
            try:
                prot_elements = _prot_elements_for_trip(
                    dev_obj, trip_count, element_cache
                )
                for i, line in enumerate(lines):
                    if swer_lines[i]:
                        continue
                    min_fl_clear_times, _ = fault_clear_times(
                        app, device, line, fl_step, line_fault_type,
                        prot_elements, clear_time_cache,
                    )
                    max_energy, max_fl, max_clear_time = worst_case_energy(
                        line, min_fl_clear_times, line_fault_type, device, False
                    )
                    total_energy[i] += max_energy

                    if max_clear_time is not None:
                        # Record the fl / clear time of the trip
                        # contributing the most energy, matching the
                        # "worst case energy" column labels in the
                        # results output.
                        if max_energy > worst_trip_energy[i]:
                            worst_trip_energy[i] = max_energy
                            line.ph_clear_time = max_clear_time
                            line.ph_fl = max_fl
                    else:
                        # No element produced a valid operating time at
                        # any fault level for this trip.
                        incomplete_trips[i].append(trip_count)
            finally:
                reclose.reset_block_service_status(block_service_status)

            trip_count = reclose.trip_count(dev_obj, increment=True)

        for i, line in enumerate(lines):
            if swer_lines[i]:
                line.ph_energy = None
                line.ph_clear_time = None
                line.ph_fl = None
                continue
            if incomplete_trips[i]:
                # A failed trip contributes 0 to that line's total,
                # A failed trip contributes 0 to that line's total,
                # which would understate the accumulated let-through and
                # could turn a genuine FAIL into a PASS. None is honest:
                # it renders as NO DATA in the workbook and as
                # unassessable km on the dashboard.
                line.ph_energy = None
                line.ph_clear_time = None
                line.ph_fl = None
                incomplete_lines.append(
                    (line.obj.loc_name, tuple(incomplete_trips[i]))
                )
            else:
                line.ph_energy = total_energy[i]

        if incomplete_lines:
            trips_seen = sorted({
                trip for _, trips in incomplete_lines for trip in trips
            })
            logger.warning(
                f"{dev_obj.loc_name}: phase fault clearing time could not "
                f"be calculated on trip(s) {trips_seen} for "
                f"{len(incomplete_lines)} of "
                f"{len(lines) - sum(swer_lines)} non-SWER line(s); their "
                f"phase energy is reported as no data rather than a "
                f"partial total. Device has {total_trips} trip(s). Check "
                f"whether any phase element is enabled on those trips."
            )

        # Earth fault assessment
        line_fault_type = 'Phase-Ground'
        logger.info(
            f"Earth fault conductor damage assessment: {dev_obj.loc_name}"
        )
        incomplete_lines = []
        reclose.reset_reclosing(dev_obj)
        trip_count = 1
        total_energy = [0] * len(lines)
        worst_trip_energy = [0] * len(lines)
        incomplete_trips = [[] for _ in lines]

        while trip_count <= total_trips:
            block_service_status = reclose.set_enabled_elements(dev_obj)
            try:
                prot_elements = _prot_elements_for_trip(
                    dev_obj, trip_count, element_cache
                )
                for i, line in enumerate(lines):
                    min_fl_clear_times, device_fault_type = fault_clear_times(
                        app, device, line, fl_step, line_fault_type,
                        prot_elements, clear_time_cache,
                    )

                    # Check if SWER transformation was applied. This is
                    # per line, not per trip - swer_fault_range decides
                    # it from the line's own type and phase count.
                    transposition = (line_fault_type != device_fault_type)

                    max_energy, max_fl, max_clear_time = worst_case_energy(
                        line, min_fl_clear_times, line_fault_type, device,
                        transposition
                    )
                    total_energy[i] += max_energy

                    if max_clear_time is not None:
                        # Record the fl / clear time of the trip
                        # contributing the most energy, matching the
                        # "worst case energy" column labels in the
                        # results output.
                        if max_energy > worst_trip_energy[i]:
                            worst_trip_energy[i] = max_energy
                            line.pg_clear_time = max_clear_time
                            line.pg_fl = max_fl
                    else:
                        incomplete_trips[i].append(trip_count)
            finally:
                reclose.reset_block_service_status(block_service_status)

            trip_count = reclose.trip_count(dev_obj, increment=True)

        for i, line in enumerate(lines):
            if incomplete_trips[i]:
                line.pg_energy = None
                line.pg_clear_time = None
                line.pg_fl = None
                incomplete_lines.append(
                    (line.obj.loc_name, tuple(incomplete_trips[i]))
                )
            else:
                line.pg_energy = total_energy[i]

        # Leave the recloser at trip 1 rather than trips+1 so the
        # assessment does not persist counter drift into the model.
        reclose.reset_reclosing(dev_obj)

        if incomplete_lines:
            trips_seen = sorted({
                trip for _, trips in incomplete_lines for trip in trips
            })
            logger.warning(
                f"{dev_obj.loc_name}: earth fault clearing time could not "
                f"be calculated on trip(s) {trips_seen} for "
                f"{len(incomplete_lines)} of {len(lines)} line(s); their "
                f"ground energy is reported as no data rather than a "
                f"partial total. Device has {total_trips} trip(s). Check "
                f"whether any earth element is enabled on those trips."
            )


# =============================================================================
# FAULT CLEARING TIME CALCULATION
# =============================================================================

def fault_clear_times(
    app: pft.Application,
    device: Any,
    line: Any,
    fl_step: int,
    fault_type: str,
    prot_elements: Optional[Dict] = None,
    clear_time_cache: Optional[Dict] = None
) -> Tuple[Dict[int, Optional[float]], str]:
    """
    Find the fastest clearing time at each sampled fault level.

    The fault levels sampled depend on INTERVAL:
    - "partial" (production): the line's minimum and maximum fault
      level, plus the fault level just below each active RelIoc
      element's pickup where that lies strictly inside the range. The
      pickup is converted from the element's measured quantity to fault
      current first (_hiset_sample_fl).
    - "full": every fl_step amps from minimum to maximum.
    At each level the clearing time is the minimum over the active
    elements: relay operate time plus 80 ms CB time, or fuse total
    clearing time.

    Fault ranges:
    - Phase ('2-Phase'/'3-Phase'): min_fl_2ph up to the greater of
      max_fl_2ph and max_fl_3ph.
    - Earth ('Phase-Ground'): min_fl_pg up to max_fl_pg, transformed to
      a device-side '2-Phase' fault when a multi-phase device protects
      a single-phase SWER line (swer_fault_range).

    Args:
        app: PowerFactory application instance.
        device: Device dataclass containing the protection device.
        line: Line dataclass with fault current data.
        fl_step: Step size in amps, used only when INTERVAL is "full".
        fault_type: '2-Phase', '3-Phase', or 'Phase-Ground'.
        prot_elements: Element dict from get_prot_elements for the
            current trip, supplied by the caller so the walk is not
            repeated for every line. None means "look it up here",
            which keeps the function usable standalone; it must be
            None for fuses, which have no sub-elements.
        clear_time_cache: Per-device cache of element clearing times,
            pickups and pickup sample levels, supplied by the caller so
            that values common to every line in a section are evaluated
            once. None creates a throwaway cache for this call.

    Returns:
        Tuple containing:
        - Dict mapping each sampled fault level (A) to its minimum
          clearing time (s), or None where no element operates. Empty
          when the line has no fault level data.
        - The fault type actually used, which is '2-Phase' after a SWER
          transformation (the levels are then device-side currents).
    """

    max_phase_fl = trip_time.max_phase_fl(line)
    if line.min_fl_2ph is None or max_phase_fl is None:
        logger.info(
            f"{device.obj.loc_name} {fault_type} conductor damage "
            f"skipped: missing line fault level data."
        )
        return {}, fault_type

    if fault_type in ['2-Phase', '3-Phase']:
        min_fl = line.min_fl_2ph
        max_fl = max_phase_fl
    else:
        # Check if this is a SWER line,
        # and does the device see the same current?
        min_fl, max_fl, fault_type = swer_fault_range(
            device, line, fault_type
        )

    # Select only the elements capable of detecting the fault type
    # and enabled for the current auto-reclose iteration
    device_obj = device.obj
    if device_obj.GetClassName() == ElementType.FUSE.value:
        active_elements = [device_obj]
    else:
        if prot_elements is None:
            prot_elements = elements.get_prot_elements(device_obj)
        # get_active_elements is a list concatenation and depends on
        # fault_type, which swer_fault_range may have changed above -
        # so it stays per call, unlike the walk that produced its input.
        active_elements = elements.get_active_elements(
            prot_elements, fault_type
        )

    # Create a list of fault levels in the interval of min and max fault
    # currents.
    # range() requires integers
    min_fl = int(min_fl)
    max_fl = int(max_fl)
    if clear_time_cache is None:
        clear_time_cache = {}

    if INTERVAL == "full":
        fl_interval = range(min_fl, max_fl + 1, fl_step)
    else:
        # Sample just below each instantaneous step, but only where the
        # step lies inside this line's fault range.
        hisets = []
        for element in active_elements:
            if element.GetClassName() != 'RelIoc':
                continue
            sample_fl = _hiset_sample_fl(
                element, fault_type, clear_time_cache
            )
            if sample_fl is not None and min_fl < sample_fl < max_fl:
                hisets.append(sample_fl)
        fl_interval = [min_fl, max_fl] + hisets

    # A hiset can coincide with min_fl or max_fl. dict.fromkeys drops
    # the repeat while preserving first-occurrence order, so the result
    # dict's key order is unchanged from before this cache existed -
    # worst_case_energy resolves an exact energy tie to whichever fault
    # level it meets first.
    fl_interval = list(dict.fromkeys(fl_interval))

    # Initialise fault level:min operating time dictionary
    min_fl_clear_times = {fault_level: None for fault_level in fl_interval}
    for element in active_elements:
        # Hoisted: the class cannot change between fault levels, and
        # GetClassName is a PF call.
        is_fuse = element.GetClassName() == ElementType.FUSE.value
        for fault_level in fl_interval:
            clear_time = _element_clear_time(
                element, is_fuse, fault_type, fault_level, clear_time_cache
            )
            if clear_time is None:
                continue
            # If this is the minimum fault clear time for that fault level,
            # update the dictionary accordingly
            if (min_fl_clear_times[fault_level] is None
                    or clear_time < min_fl_clear_times[fault_level]):
                min_fl_clear_times[fault_level] = clear_time

    return min_fl_clear_times, fault_type


def _element_hiset(element: Any, cache: Dict) -> float:
    """
    Instantaneous pickup of a RelIoc element, cached per device.

    Args:
        element: PowerFactory RelIoc object.
        cache: Per-device cache dict, mutated in place.

    Returns:
        The e:cpIpset attribute value.
    """
    key = ('hiset', element)
    if key not in cache:
        cache[key] = element.GetAttribute("e:cpIpset")
    return cache[key]


def _hiset_sample_fl(
        element: Any,
        fault_type: str,
        cache: Dict
) -> Optional[int]:
    """
    Fault level just below a RelIoc element's pickup, cached per device.

    cpIpset is in the element's measured quantity, not fault current:
    an I2 element set to 1000 A picks up at a 2-phase fault of about
    1732 A, and an I0 element at three times its setting. The pickup is
    divided by the element's measured/fault ratio, which is constant
    for a given element and fault type because get_measured_current is
    linear in fault level.

    Args:
        element: PowerFactory RelIoc object.
        fault_type: Fault type after any SWER transformation.
        cache: Per-device cache dict, mutated in place.

    Returns:
        The largest whole fault level (A) at which the element does not
        yet pick up, or None when the element cannot see this fault
        type (e.g. NPS on a 3-phase fault) or has no usable pickup.
    """
    key = ('hiset_fl', element, fault_type)
    if key in cache:
        return cache[key]

    result = None
    pickup = _element_hiset(element, cache)
    ratio = current_conversion.get_measured_current(element, 1.0, fault_type)
    if pickup and pickup > 0 and ratio and ratio > 0:
        # Largest integer strictly below pickup / ratio. For a pickup
        # that converts to an exact integer N this is N - 1.
        result = math.ceil(round(pickup / ratio, 6)) - 1

    cache[key] = result
    return result


def _element_clear_time(
    element: Any,
    is_fuse: bool,
    fault_type: str,
    fault_level: int,
    cache: Dict
) -> Optional[float]:
    """
    Clearing time for one element at one fault level, cached per device.

    The result depends only on the element, the fault type (which sets
    the current conversion factor) and the fault level - not on the
    line being assessed, and not on the reclose trip: blocking changes
    an element's outserv, not its curve or settings, and a blocked
    element is absent from active_elements rather than evaluated.

    With INTERVAL = "partial" the fault levels are the line's minimum
    and maximum plus the device's instantaneous pickups. The pickups
    are constant across the section, so without a cache each one is
    re-evaluated for every line the device protects.

    Args:
        element: Protection element, or the fuse itself.
        is_fuse: True when element is the device and a fuse.
        fault_type: Fault type after any SWER transformation.
        fault_level: Fault current in Amperes.
        cache: Per-device cache dict, mutated in place.

    Returns:
        Clearing time in seconds, or None when the element does not
        operate at this fault level.
    """
    key = ('clear', element, fault_type, fault_level)
    if key in cache:
        return cache[key]

    if is_fuse:
        operate_time = trip_time.fuse_clear_time(element, fault_level)
        switch_operate_time = 0
    else:
        element_current = current_conversion.get_measured_current(
            element, fault_level, fault_type)
        operate_time = trip_time.element_trip_time(element, element_current)
        switch_operate_time = 0.08

    if not operate_time or operate_time <= 0:
        result = None
    else:
        result = round(operate_time + switch_operate_time, 3)

    cache[key] = result
    return result


def swer_fault_range(
    device: Any,
    line: Any,
    fault_type: str
) -> Tuple[int, int, str]:
    """
    Transform fault currents for SWER line protection.

    When a multi-phase protection device protects a single-phase SWER
    line, the fault current seen by the device is transformed based on
    the voltage ratio. The device sees this as a 2-phase equivalent.

    Formula: I_device = (V_swer × I_swer) / (V_device × √3)

    Args:
        app: PowerFactory application instance.
        device: Device dataclass with voltage and phase information.
        line: Line dataclass to check for SWER.
        fault_type: Original fault type string.

    Returns:
        Tuple containing:
        - Minimum fault level (transformed if SWER)
        - Maximum fault level (transformed if SWER)
        - Fault type ('2-Phase' if transformed, original otherwise)
    """

    min_fl = line.min_fl_pg
    max_fl = line.max_fl_pg

    line_type = line.obj.typ_id

    # Check if SWER transformation applies
    is_swer = (
        'SWER' in line_type.loc_name
        and line.phases == 1
        and device.phases > 1
    )

    if is_swer:
        voltage_ratio = line.l_l_volts / device.l_l_volts
        transform_factor = voltage_ratio / math.sqrt(3)

        min_fl = round(min_fl * transform_factor)
        max_fl = round(max_fl * transform_factor)
        fault_type = '2-Phase'

    return min_fl, max_fl, fault_type


# =============================================================================
# ENERGY CALCULATION
# =============================================================================

def worst_case_energy(
    line: Any,
    min_fl_clear_times: Dict[int, Optional[float]],
    fault_type: str,
    device: Any,
    transposition: bool
) -> Tuple[float, Optional[int], Optional[float]]:
    """
    Find the fault condition producing maximum let-through energy.

    Evaluates I²t energy for each fault level and returns the worst
    case combination.

    Args:
        line: Line dataclass for reverse transformation.
        min_fl_clear_times: Dict mapping fault levels to clearing times.
        fault_type: Fault type string (for reference).
        device: Device dataclass for SWER reverse transformation.
        transposition: True if SWER transformation was applied.

    Returns:
        Tuple containing:
        - Maximum energy in A²s
        - Fault level producing maximum energy (A)
        - Clearing time at maximum energy (s)

    Note:
        If transposition is True, the fault levels in min_fl_clear_times
        are device-side currents. Each is reverse-transformed to the
        line's actual current before its energy is calculated, so both
        the energy and the returned fault level are on the line side -
        the same basis as the line's thermal rating they are compared
        against.
    """
    max_energy = 0
    max_fl = None
    max_clear_time = None

    # Device-side to line-side current factor. 1 unless a SWER
    # transformation was applied in swer_fault_range.
    reverse_factor = 1
    if fault_type == 'Phase-Ground' and transposition:
        if not line.l_l_volts:
            # No line voltage to reverse the transformation with. Report
            # no clearing time so the caller marks the line incomplete
            # (NO DATA) rather than rating it on device-side current.
            return 0, None, None
        reverse_factor = (math.sqrt(3) * device.l_l_volts) / line.l_l_volts

    for fl, clear_time in min_fl_clear_times.items():
        if clear_time is None:
            continue

        line_fl = fl * reverse_factor
        energy = line_fl ** 2 * clear_time

        if energy > max_energy:
            max_energy = energy
            max_fl = line_fl
            max_clear_time = clear_time

    if max_fl is not None and reverse_factor != 1:
        max_fl = round(max_fl)

    return max_energy, max_fl, max_clear_time
