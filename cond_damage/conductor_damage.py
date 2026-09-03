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

logger = logging.getLogger(__name__)

# Fault-current resolution (A) for the worst-case energy search.
# fault_clear_times evaluates clearing time at each fault level
# according to the interval selection:
# INTERVAL = "full": composed of equidistant step sizes between
# min fault level and max fault level.
# INTERVAL = "partial": composed of only the element hisets between
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

    Evaluates each line section protected by each device to determine
    if fault clearing is fast enough to prevent conductor damage. The
    assessment accumulates energy across all trips in the auto-reclose
    sequence.

    Args:
        app: PowerFactory application instance.
        devices: List of Device dataclasses with populated sect_lines.

    Side Effects:
        Populates the following attributes on each Line in sect_lines:
        - ph_energy: Total phase fault energy (A²s)
        - ph_clear_time: Phase fault clearing time for worst case (s)
        - ph_fl: Fault level for worst phase energy (A)
        - pg_energy: Total ground fault energy (A²s)
        - pg_clear_time: Ground fault clearing time for worst case (s)
        - pg_fl: Fault level for worst ground energy (A)

    Note:
        The worst case energy is found by evaluating clearing times
        across the fault current range in steps of 10A, then selecting
        the fault level that produces maximum I²t.

    Example:
        >>> cond_damage(app, feeder.devices)
        >>> for device in feeder.devices:
        ...     for line in device.sect_lines:
        ...         print(f"{line.obj.loc_name}: {line.ph_energy} A²s")
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

        # Phase fault assessment
        line_fault_type = '2-Phase'
        logger.info(
            f"Phase fault conductor damage assessment: {dev_obj.loc_name}"
        )
        # Lines whose accumulated energy is incomplete, reported once
        # per device rather than once per line.
        incomplete_lines = []
        for line in lines:
            reclose.reset_reclosing(dev_obj)
            trip_count = 1
            total_energy = 0
            worst_trip_energy = 0
            # Trips that produced no clearing time. Any entry here means
            # total_energy is missing a contribution of unknown size, so
            # the accumulated figure must not be reported as if complete.
            incomplete_trips = []

            while trip_count <= total_trips:
                block_service_status = reclose.set_enabled_elements(dev_obj)
                try:
                    min_fl_clear_times, _ = fault_clear_times(
                        app, device, line, fl_step, line_fault_type,
                        _prot_elements_for_trip(
                            dev_obj, trip_count, element_cache
                        ),
                    )
                    max_energy, max_fl, max_clear_time = worst_case_energy(
                        line, min_fl_clear_times, line_fault_type, device, False
                    )
                finally:
                    reclose.reset_block_service_status(block_service_status)
                total_energy += max_energy

                if max_clear_time is not None:
                    # Record the fl / clear time of the trip contributing
                    # the most energy, matching the "worst case energy"
                    # column labels in the results output.
                    if max_energy > worst_trip_energy:
                        worst_trip_energy = max_energy
                        line.ph_clear_time = max_clear_time
                        line.ph_fl = max_fl
                else:
                    # No element produced a valid operating time at any
                    # fault level for this trip.
                    incomplete_trips.append(trip_count)

                trip_count = reclose.trip_count(dev_obj, increment=True)

            if incomplete_trips:
                # A failed trip contributes 0 to total_energy, which
                # would understate the accumulated let-through and could
                # turn a genuine FAIL into a PASS. None is honest: it
                # renders as NO DATA in the workbook and as unassessable
                # km on the dashboard.
                line.ph_energy = None
                line.ph_clear_time = None
                line.ph_fl = None
                incomplete_lines.append(
                    (line.obj.loc_name, tuple(incomplete_trips))
                )
            else:
                line.ph_energy = total_energy

        if incomplete_lines:
            trips_seen = sorted({
                trip for _, trips in incomplete_lines for trip in trips
            })
            logger.warning(
                f"{dev_obj.loc_name}: phase fault clearing time could not "
                f"be calculated on trip(s) {trips_seen} for "
                f"{len(incomplete_lines)} of {len(lines)} line(s); their "
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
        for line in lines:
            reclose.reset_reclosing(dev_obj)
            trip_count = 1
            total_energy = 0
            worst_trip_energy = 0
            incomplete_trips = []
            while trip_count <= total_trips:
                block_service_status = reclose.set_enabled_elements(dev_obj)
                try:
                    min_fl_clear_times, device_fault_type = fault_clear_times(
                        app, device, line, fl_step, line_fault_type,
                        _prot_elements_for_trip(
                            dev_obj, trip_count, element_cache
                        ),
                    )

                    # Check if SWER transformation was applied
                    transposition = (line_fault_type != device_fault_type)

                    max_energy, max_fl, max_clear_time = worst_case_energy(
                        line, min_fl_clear_times, line_fault_type, device, transposition
                    )
                finally:
                    reclose.reset_block_service_status(block_service_status)
                total_energy += max_energy

                if max_clear_time is not None:
                    # Record the fl / clear time of the trip contributing
                    # the most energy, matching the "worst case energy"
                    # column labels in the results output.
                    if max_energy > worst_trip_energy:
                        worst_trip_energy = max_energy
                        line.pg_clear_time = max_clear_time
                        line.pg_fl = max_fl
                else:
                    incomplete_trips.append(trip_count)

                trip_count = reclose.trip_count(dev_obj, increment=True)

            if incomplete_trips:
                line.pg_energy = None
                line.pg_clear_time = None
                line.pg_fl = None
                incomplete_lines.append(
                    (line.obj.loc_name, tuple(incomplete_trips))
                )
            else:
                line.pg_energy = total_energy

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
    prot_elements: Optional[Dict] = None
) -> Tuple[Dict[int, Optional[float]], str]:
    """
    Calculate fault clearing times across the fault current range.

    Evaluates clearing time at each fault level from minimum to maximum
    in steps of fl_step. For each fault level, finds the minimum clearing
    time among all active protection elements.

    Args:
        app: PowerFactory application instance.
        device: Device dataclass containing the protection device.
        line: Line dataclass with fault current data.
        fl_step: Fault level step size in Amperes.
        fault_type: '2-Phase', '3-Phase', or 'Phase-Ground'.
        prot_elements: Element dict from get_prot_elements for the
            current trip, supplied by the caller so the walk is not
            repeated for every line. None means "look it up here",
            which keeps the function usable standalone; it must be
            None for fuses, which have no sub-elements.

    Returns:
        Tuple containing:
        - Dictionary mapping fault levels to minimum clearing times
        - Actual fault type used (may differ for SWER)

    Note:
        For phase faults, uses 2-phase minimum and maximum of 2ph/3ph.
        For earth faults, applies SWER transformation if applicable.
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
    if INTERVAL == "full":
        fl_interval = range(min_fl, max_fl + 1, fl_step)
    else:
        hisets = [
            element.GetAttribute("e:cpIpset") - 1 for element in active_elements
                  if element.GetClassName() == 'RelIoc']
        fl_interval = [min_fl, max_fl] + hisets

    # Initialise fault level:min operating time dictionary
    min_fl_clear_times = {fault_level: None for fault_level in fl_interval}
    for element in active_elements:
        for fault_level in fl_interval:
            # Calculate protection operate time for element and fl
            if element.GetClassName() == ElementType.FUSE.value:
                operate_time = trip_time.fuse_clear_time(element, fault_level)
                switch_operate_time = 0
            else:
                element_current = current_conversion.get_measured_current(
                    element, fault_level, fault_type)
                operate_time = trip_time.element_trip_time(element, element_current)
                switch_operate_time = 0.08
            if not operate_time or operate_time <= 0:
                continue
            clear_time = operate_time + switch_operate_time
            # If this is the minimum fault clear time for that fault level,
            # update the dictionary accordingly
            if (min_fl_clear_times[fault_level] is None
                    or clear_time < min_fl_clear_times[fault_level]):
                min_fl_clear_times[fault_level] = round(clear_time, 3)

    return min_fl_clear_times, fault_type


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
        If transposition is True, the returned fault level is
        reverse-transformed to the line's actual current.
    """
    max_energy = 0
    max_fl = None
    max_clear_time = None

    for fl, clear_time in min_fl_clear_times.items():
        if clear_time is None:
            continue

        energy = fl ** 2 * clear_time

        if energy > max_energy:
            max_energy = energy
            max_fl = fl
            max_clear_time = clear_time

    # Reverse SWER transformation for reporting
    if fault_type == 'Phase-Ground' and transposition and max_fl is not None:
        reverse_factor = (math.sqrt(3) * device.l_l_volts) / line.l_l_volts
        max_fl = round(max_fl * reverse_factor)

    return max_energy, max_fl, max_clear_time
