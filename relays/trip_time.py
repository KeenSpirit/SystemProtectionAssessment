from typing import Dict, List, Optional, Tuple, Any


def max_phase_fl(obj: Any) -> Optional[float]:
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

# =============================================================================
# FUSE CLEARING TIME
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