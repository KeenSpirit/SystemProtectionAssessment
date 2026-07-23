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
from relays import current_conversion, elements, reclose
from assets.enums import ElementType

logger = logging.getLogger(__name__)

# Fault-current resolution (A) for the worst-case energy search.
# fault_clear_times evaluates clearing time at each fault level from
# line minimum to maximum in steps of this size; the step that yields
# the greatest I2t is reported. Smaller = finer peak detection but
# proportionally more PF curve evaluations per line per trip. 10 A is
# the validated production value; raising it is the first lever if
# conductor-damage runtime dominates a batch run.
FL_STEP_AMPS = 10


def _max_phase_fl(line: Any) -> Optional[float]:
    """
    Highest available maximum phase fault level for a line.

    Either the 2-phase or 3-phase maximum may be None where that
    scenario produced no result. The available value is used rather
    than aborting the line; None is returned only when both are
    missing.
    """
    candidates = [
        fl for fl in (line.max_fl_2ph, line.max_fl_3ph) if fl is not None
    ]
    return max(candidates) if candidates else None


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

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

        # Phase fault assessment
        line_fault_type = '2-Phase'
        logger.info(
            f"Phase fault conductor damage assessment: {dev_obj.loc_name}"
        )
        for line in lines:
            reclose.reset_reclosing(dev_obj)
            trip_count = 1
            total_energy = 0
            worst_trip_energy = 0

            while trip_count <= total_trips:
                block_service_status = reclose.set_enabled_elements(dev_obj)
                try:
                    min_fl_clear_times, _ = fault_clear_times(
                        app, device, line, fl_step, line_fault_type
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
                    logging.info(
                        f"{dev_obj.loc_name} {line_fault_type} trip "
                        f"{trip_count} fault clearing time calculation "
                        f"error."
                    )

                trip_count = reclose.trip_count(dev_obj, increment=True)

            line.ph_energy = total_energy

        # Earth fault assessment
        line_fault_type = 'Phase-Ground'
        logger.info(
            f"Earth fault conductor damage assessment: {dev_obj.loc_name}"
        )
        for line in lines:
            reclose.reset_reclosing(dev_obj)
            trip_count = 1
            total_energy = 0
            worst_trip_energy = 0
            while trip_count <= total_trips:
                block_service_status = reclose.set_enabled_elements(dev_obj)
                try:
                    min_fl_clear_times, device_fault_type = fault_clear_times(
                        app, device, line, fl_step, line_fault_type
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
                    logging.info(
                        f"{dev_obj.loc_name} {line_fault_type} trip "
                        f"{trip_count} fault clearing time calculation "
                        f"error."
                    )

                trip_count = reclose.trip_count(dev_obj, increment=True)

            line.pg_energy = total_energy

            # Leave the recloser at trip 1 rather than trips+1 so the
            # assessment does not persist counter drift into the model.
            reclose.reset_reclosing(dev_obj)


# =============================================================================
# FAULT CLEARING TIME CALCULATION
# =============================================================================

def fault_clear_times(
    app: pft.Application,
    device: Any,
    line: Any,
    fl_step: int,
    fault_type: str
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

    Returns:
        Tuple containing:
        - Dictionary mapping fault levels to minimum clearing times
        - Actual fault type used (may differ for SWER)

    Note:
        For phase faults, uses 2-phase minimum and maximum of 2ph/3ph.
        For earth faults, applies SWER transformation if applicable.
    """

    max_phase_fl = _max_phase_fl(line)
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

    # range() requires integers
    min_fl = int(min_fl)
    max_fl = int(max_fl)

    device_obj = device.obj
    # Create a list of fault levels in the interval of min and max fault
    # currents. Two intervals may be assessed:
    # fl_interval_1 is composed of equidistant step sizes between
    # min fault level and max fault level
    # fl_interval_2 is composed of only the element hisets between
    # min fault level and max fault level

    # Create fault level range
    fl_interval_1 = range(min_fl, max_fl + 1, fl_step)
    # Initialise fl_interval_2
    # fl_interval_2 = [min_fl, max_fl]

    # Select only the elements capable of detecting the fault type
    # and enabled for the current auto-reclose iteration
    if device_obj.GetClassName() == ElementType.FUSE.value:
        active_elements = [device_obj]
    else:
        all_elements = elements.get_prot_elements(device_obj)
        active_elements = elements.get_active_elements(all_elements, fault_type)
        # hisets = [
        #     element.GetAttribute("e:cpIpset") - 1 for element in active_elements
        #           if element.GetClassName() == 'RelIoc']
        # fl_interval_2 = fl_interval_2 + hisets

    # Initialise fault level:min operating time dictionary
    min_fl_clear_times = {fault_level: None for fault_level in fl_interval_1}
    for element in active_elements:
        for fault_level in fl_interval_1:
            # Calculate protection operate time for element and fl
            if element.GetClassName() == ElementType.FUSE.value:
                operate_time = fuse_clear_time(element, fault_level)
                switch_operate_time = 0
            else:
                element_current = current_conversion.get_measured_current(
                    element, fault_level, fault_type)
                operate_time = element_trip_time(element, element_current)
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

    min_fl = line.min_fl_2ph
    max_fl = _max_phase_fl(line)

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



def fuse_clear_time(fuse: Any, flt_cur: float) -> Optional[float]:
    """
    Calculate fuse total clearing time for a given fault current.

    Interpolates linearly on the fuse time-current characteristic
    curve. Only Hermite Polynomial curves (type 6) are supported.

    Args:
        fuse: RelFuse element with associated TypFuse.
        flt_cur: Fault current in Amperes.

    Returns:
        Total clearing time in seconds, or None if:
        - Fault current below minimum pickup
        - Unsupported curve type
        - Unsupported curve count

    Note:
        Fuse curves are read from the TypFuse melt characteristic.
        The function uses linear interpolation between curve points.
    """

    op_time = None

    type_fuse = fuse.GetAttribute("e:typ_id")
    # melt curve
    typechatoc = type_fuse.GetAttribute("e:pmelt")
    # curve type
    curve_type = typechatoc.GetAttribute("e:i_type")
    # curve equation variables
    curve_var = typechatoc.GetAttribute("e:vmat")
    number_of_rows = len(curve_var)

    # Only Hermite Polynomial supported
    if curve_type != 6:
        return op_time

    curve_count = typechatoc.GetAttribute("e:i_curves")

    if curve_count == 1:
        p = curve_count - 1
    elif curve_count == 2:
        p = curve_count
    else:
        return op_time

    # Check fault current bounds
    if flt_cur < curve_var[0][p]:
        return op_time

    if flt_cur > curve_var[number_of_rows - 1][p]:
        return curve_var[number_of_rows - 1][p + 1]

    # Linear interpolation
    k = 0
    while k < (number_of_rows - 1):
        if curve_var[k][p] <= flt_cur <= curve_var[k + 1][p]:
            op_time = _interpolate_fuse_time(curve_var, k, p, flt_cur)
            break
        k += 1

    return op_time


def _interpolate_fuse_time(
    curve_var: List,
    k: int,
    p: int,
    flt_cur: float
) -> float:
    """
    Linear interpolation for fuse clearing time.

    Args:
        curve_var: Curve variable matrix from TypChaTime.
        k: Lower bound index in curve.
        p: Column index for current values.
        flt_cur: Fault current to interpolate.

    Returns:
        Interpolated clearing time in seconds.
    """
    x_ratio = (flt_cur - curve_var[k][p]) / (curve_var[k + 1][p] - curve_var[k][p])
    y_diff = curve_var[k][p + 1] - curve_var[k + 1][p + 1]
    return curve_var[k][p + 1] - (y_diff * x_ratio)


# =============================================================================
# RELAY ELEMENT CLEARING TIME
# =============================================================================

def element_trip_time(element: Any, flt_cur: float) -> Optional[float]:
    """
    Calculate relay element operating time for a given fault current.

    Supports multiple curve types for IDMT (RelToc) elements and
    instantaneous (RelIoc) elements.

    Supported IDMT Curve Types:
        - Type 0: Definite time
        - Type 1: IEC 255-3 (Standard Inverse, Very Inverse, etc.)
        - Type 2: ANSI/IEEE
        - Type 3: ANSI/IEEE squared
        - Type 4: ABB/Westinghouse
        - Type 6: Hermite Polynomial
        - Type 8: Special Equation

    Args:
        element: RelToc or RelIoc element.
        flt_cur: Fault current in Amperes.

    Returns:
        Operating time in seconds, or None if:
        - Fault current below pickup
        - Unsupported curve type

    Note:
        For RelIoc elements, returns the minimum operate time if
        the fault current exceeds the pickup setting.
    """
    op_time = None

    if element.GetClassName() == 'RelToc':
        op_time = _calculate_toc_time(element, flt_cur)
    elif element.GetClassName() == 'RelIoc':
        op_time = _calculate_ioc_time(element, flt_cur)

    return op_time


def _calculate_toc_time(element: Any, flt_cur: float) -> Optional[float]:
    """
    Calculate IDMT relay element operating time.

    Args:
        element: RelToc element.
        flt_cur: Fault current in Amperes.

    Returns:
        Operating time in seconds, or None if below pickup.
    """
    pickup = element.GetAttribute("e:cpIpset")

    if flt_cur <= pickup:
        return None

    time_dial = element.GetAttribute("e:Tpset")
    curve_char = element.GetAttribute("e:pcharac")
    curve_type = curve_char.GetAttribute("e:i_type")
    curve_var = curve_char.GetAttribute("e:vmat")

    i_ip = flt_cur / pickup

    # Definite time
    if curve_type == 0:
        return time_dial * curve_var[0][0]

    # IEC 255-3
    if curve_type == 1:
        a1, a2, a3 = curve_var[0][0], curve_var[1][0], curve_var[2][0]
        return time_dial * a1 / (i_ip ** a2 - a3)

    # ANSI/IEEE
    if curve_type == 2:
        a1, a2 = curve_var[0][0], curve_var[1][0]
        a3, a4 = curve_var[2][0], curve_var[3][0]
        return time_dial * (a1 / (i_ip ** a2 - a3) + a4)

    # ANSI/IEEE squared
    if curve_type == 3:
        a1, a2 = curve_var[0][0], curve_var[1][0]
        return (time_dial * a1 + a2) / (i_ip ** 2)

    # ABB/Westinghouse
    if curve_type == 4:
        a1, a2 = curve_var[0][0], curve_var[1][0]
        a3, a4 = curve_var[2][0], curve_var[3][0]
        a5 = curve_var[4][0]

        if i_ip >= 1.5:
            return ((a1 + a2) / ((i_ip - a3) ** a4)) * time_dial / 24000
        else:
            return (a5 / (i_ip - 1)) * time_dial / 24000

    # Hermite Polynomial
    if curve_type == 6:
        return _calculate_hermite_toc_time(
            curve_char, curve_var, i_ip, time_dial
        )

    # Special Equation
    if curve_type == 8:
        a1, a2, a3 = curve_var[0][0], curve_var[1][0], curve_var[2][0]
        b1, b2, b3 = curve_var[3][0], curve_var[4][0], curve_var[5][0]
        return (
            (time_dial * a1) / ((i_ip + b1) ** b2 + b3)
            + time_dial * a2 + a3
        )

    # Unsupported curve type
    return None


def _calculate_hermite_toc_time(
    curve_char: Any,
    curve_var: List,
    i_ip: float,
    time_dial: float
) -> Optional[float]:
    """
    Calculate operating time for Hermite Polynomial IDMT curves.

    Args:
        curve_char: TypChaTime characteristic object.
        curve_var: Curve variable matrix.
        i_ip: Current as multiple of pickup (I/Ip).
        time_dial: Time multiplier setting.

    Returns:
        Operating time in seconds, or None if outside curve range.
    """
    number_of_rows = len(curve_var)
    curve_count = curve_char.GetAttribute("e:i_curves")

    if curve_count > 1:
        return None

    if i_ip < curve_var[0][0]:
        return None

    if i_ip > curve_var[number_of_rows - 1][0]:
        return curve_var[number_of_rows - 1][1]

    # Linear interpolation
    k = 0
    while k < (number_of_rows - 1):
        if curve_var[k][0] <= i_ip <= curve_var[k + 1][0]:
            x_ratio = (
                (i_ip - curve_var[k][0])
                / (curve_var[k + 1][0] - curve_var[k][0])
            )
            y_diff = curve_var[k][1] - curve_var[k + 1][1]
            return (curve_var[k][1] - y_diff * x_ratio) * time_dial
        k += 1

    return None


def _calculate_ioc_time(element: Any, flt_cur: float) -> Optional[float]:
    """
    Calculate instantaneous relay element operating time.

    Args:
        element: RelIoc element.
        flt_cur: Fault current in Amperes.

    Returns:
        Minimum operate time if above pickup, None otherwise.
    """
    min_time = element.GetAttribute("e:cptotime")
    pickup = element.GetAttribute("e:cpIpset")

    if flt_cur >= pickup:
        return min_time

    return None