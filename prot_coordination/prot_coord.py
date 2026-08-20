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
from relays import current_conversion, elements, reclose
from assets.enums import ElementType


FL_STEP_AMPS = 10


def _max_phase_fl(obj: Any) -> Optional[float]:
    """
    Highest available maximum phase fault level for an object.

    Either the 2-phase or 3-phase maximum may be None where that
    scenario produced no result. The available value is used rather
    than aborting the obj; None is returned only when both are
    missing.
    """
    candidates = [
        fl for fl in (obj.max_fl_2ph, obj.max_fl_3ph) if fl is not None
    ]
    return max(candidates) if candidates else None


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
        worst_pg_coord_fl = None
        worst_pg_coord_margin = None

        max_phase_fl = _max_phase_fl(device)
        ph_min_fl = int(device.min_device_2ph)
        ph_max_fl = int(max_phase_fl)
        ph_fl_interval = range(ph_min_fl, ph_max_fl + 1, fl_step)
        pg_min_fl = int(device.min_device_pg)
        pg_max_fl = int(device.max_fl_pg)
        pg_fl_interval = range(pg_min_fl, pg_max_fl + 1, fl_step)


        while trip_count <= total_trips:
            block_service_status = reclose.set_enabled_elements(dev_obj)
            try:
                fault_type = '2-Phase'
                # Select only the elements capable of detecting the fault type
                # and enabled for the current auto-reclose iteration
                active_elements = get_active_elements(device, fault_type)

                dev_fl_trip_register = {}
                bu_fl_trip_register = {}
                for fl in ph_fl_interval:
                    dev_fl_trip_register[fl] = None
                    for element in active_elements:
                        # Calculate protection operate time for element and fl
                        if element.GetClassName() == ElementType.FUSE.value:
                            operate_time = fuse_clear_time(element, fl)
                        else:
                            element_current = current_conversion.get_measured_current(
                                element, fl, fault_type)
                            operate_time = element_trip_time(element, element_current)
                        if not operate_time or operate_time <= 0:
                            continue
                        if dev_fl_trip_register[fl] is None or operate_time < dev_fl_trip_register[fl]:
                            dev_fl_trip_register[fl] = operate_time

                    bu_fl_trip_register[fl] = None
                    for bu_device in device.us_devices:
                        # If the bu_device is in the same cubicle, ignore it
                        if bu_device.obj.cubicle == device.obj.cubicle:
                            continue
                        bu_active_elements = get_active_elements(bu_device, fault_type)
                        for element in bu_active_elements:
                            # Calculate protection operate time for element and fl
                            if element.GetClassName() == ElementType.FUSE.value:
                                operate_time = fuse_clear_time(element, fl)
                            else:
                                element_current = current_conversion.get_measured_current(
                                    element, fl, fault_type)
                                operate_time = element_trip_time(element, element_current)
                            if not operate_time or operate_time <= 0:
                                continue
                            if bu_fl_trip_register[fl] is None or operate_time < bu_fl_trip_register[fl]:
                                bu_fl_trip_register[fl] = operate_time

                for fl, time in dev_fl_trip_register.items():
                    coord_margin = bu_fl_trip_register[fl] - time
                    if coord_margin is None:
                        continue
                    if worst_ph_coord_margin is None or coord_margin < worst_ph_coord_margin:
                        worst_ph_coord_fl = fl
                        worst_ph_coord_margin = coord_margin

                fault_type = 'Phase-Ground'
                # Select only the elements capable of detecting the fault type
                # and enabled for the current auto-reclose iteration
                active_elements = get_active_elements(device, fault_type)

                dev_fl_trip_register = {}
                bu_fl_trip_register = {}
                for fl in pg_fl_interval:
                    dev_fl_trip_register[fl] = None
                    for element in active_elements:
                        # Calculate protection operate time for element and fl
                        if element.GetClassName() == ElementType.FUSE.value:
                            operate_time = fuse_clear_time(element, fl)
                        else:
                            element_current = current_conversion.get_measured_current(
                                element, fl, fault_type)
                            operate_time = element_trip_time(element, element_current)
                        if not operate_time or operate_time <= 0:
                            continue
                        if dev_fl_trip_register[fl] is None or operate_time < dev_fl_trip_register[fl]:
                            dev_fl_trip_register[fl] = operate_time

                    bu_fl_trip_register[fl] = None
                    for bu_device in device.us_devices:
                        # If the bu_device is in the same cubicle, ignore it
                        if bu_device.obj.cubicle == device.obj.cubicle:
                            continue
                        # Check whether the device is SWER.
                        # If so, BU device trip time needs to consider FL seen by bu device
                        if swer_check(device, bu_device):
                            fault_type = '2-Phase'
                            fault_level = swer_transform(device, bu_device, fl)
                        else:
                            fault_type = 'Phase-Ground'
                            fault_level = fl
                        bu_active_elements = get_active_elements(bu_device, fault_type)
                        for element in bu_active_elements:
                            # Calculate protection operate time for element and fl
                            if element.GetClassName() == ElementType.FUSE.value:
                                operate_time = fuse_clear_time(element, fault_level)
                            else:
                                element_current = current_conversion.get_measured_current(
                                    element, fault_level, fault_type)
                                operate_time = element_trip_time(element, element_current)
                            if not operate_time or operate_time <= 0:
                                continue
                            if bu_fl_trip_register[fault_level] is None or operate_time < bu_fl_trip_register[fault_level]:
                                bu_fl_trip_register[fault_level] = operate_time

                for fl, time in dev_fl_trip_register.items():
                    coord_margin = bu_fl_trip_register[fl] - time
                    if coord_margin is None:
                        continue
                    if worst_pg_coord_margin is None or coord_margin < worst_pg_coord_margin:
                        worst_pg_coord_fl = fl
                        worst_pg_coord_margin = coord_margin
            finally:
                reclose.reset_block_service_status(block_service_status)
            trip_count = reclose.trip_count(dev_obj, increment=True)

        # Update device worst_coord_margin and worst_coord_fl
        device.ph_coord_fl = worst_ph_coord_fl
        device.ph_coord_margin = worst_ph_coord_margin
        device.pg_coord_fl = worst_pg_coord_fl
        device.pg_coord_margin = worst_pg_coord_margin

        # Leave the recloser at trip 1 rather than trips+1 so the
        # assessment does not persist counter drift into the model.
        reclose.reset_reclosing(dev_obj)


def get_active_elements(device, fault_type):
    device_obj = device.obj
    if device_obj.GetClassName() == ElementType.FUSE.value:
        active_elements = [device_obj]
    else:
        all_elements = elements.get_prot_elements(device_obj)
        active_elements = elements.get_active_elements(all_elements, fault_type)
    return active_elements

# =============================================================================
# FAULT CLEARING TIME CALCULATION
# =============================================================================


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

    curve_col = 1
    for index, value in enumerate(range(1, curve_count+1)):
        if time_dial == curve_var[0][value]:
            curve_col = value
            break

    if i_ip < curve_var[0][0]:
        return None

    if i_ip > curve_var[number_of_rows - 1][0]:
        return curve_var[number_of_rows - 1][curve_col]

    # Linear interpolation
    k = 0
    while k < (number_of_rows - 1):
        if curve_var[k][0] <= i_ip <= curve_var[k + 1][0]:
            x_ratio = (
                (i_ip - curve_var[k][0])
                / (curve_var[k + 1][0] - curve_var[k][0])
            )
            y_diff = curve_var[k][curve_col] - curve_var[k + 1][curve_col]
            return (curve_var[k][curve_col] - y_diff * x_ratio) * time_dial
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