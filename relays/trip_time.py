"""
Operating times of fuses and relay elements.

Pure calculation on the settings and curve data of PowerFactory
objects: the time a fuse, an IDMT element (RelToc) or an instantaneous
element (RelIoc) takes to operate at a given current. Conductor damage
and protection coordination both take their times from here.

An element whose settings or curve cannot give a time is left out, not
allowed to stop the feeder: element_trip_time logs one warning naming
the relay and element, then returns None, which every caller already
reads as "does not operate".

Functions:
    max_phase_fl: Highest maximum phase fault level of an object
    fuse_has_min_melt: Whether a fuse has a minimum melt curve
    fuse_clear_time: Fuse total clearing time
    fuse_min_melt_time: Fuse minimum melt time
    fuse_curve_time: Fuse time on a selected curve
    element_trip_time: Relay element operating time
    reset_element_warnings: Start a new assessment's warning list
"""

import logging
import math
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


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


# TypFuse e:pmelt characteristic layout (Hermite, i_curves):
#   2 curves: curve 1 (columns 0-1) = minimum melt,
#             curve 2 (columns 2-3) = total clear.
#   1 curve:  taken as total clear. There is no minimum melt curve;
#             fuse_min_melt_time falls back to this curve and
#             fuse_has_min_melt returns False so callers can flag it.
FUSE_TOTAL_CLEAR = 'total_clear'
FUSE_MIN_MELT = 'min_melt'


def _fuse_characteristic(fuse: Any) -> Optional[Any]:
    """The fuse type's e:pmelt characteristic, or None."""
    type_fuse = fuse.GetAttribute("e:typ_id")
    return type_fuse.GetAttribute("e:pmelt") if type_fuse else None


def fuse_has_min_melt(fuse: Any) -> bool:
    """True when the fuse type carries a separate minimum melt curve."""
    typechatoc = _fuse_characteristic(fuse)
    if typechatoc is None:
        return False
    return typechatoc.GetAttribute("e:i_curves") == 2


def fuse_clear_time(fuse: Any, flt_cur: float) -> Optional[float]:
    """Fuse total clearing time (curve 2, or the only curve)."""
    return fuse_curve_time(fuse, flt_cur, FUSE_TOTAL_CLEAR)


def fuse_min_melt_time(fuse: Any, flt_cur: float) -> Optional[float]:
    """
    Fuse minimum melt time (curve 1 of a two-curve characteristic).

    A single-curve characteristic is total clear, so this falls back
    to it; check fuse_has_min_melt to know whether it did.
    """
    return fuse_curve_time(fuse, flt_cur, FUSE_MIN_MELT)


def fuse_curve_time(
    fuse: Any,
    flt_cur: float,
    curve: str = FUSE_TOTAL_CLEAR
) -> Optional[float]:
    """
    Calculate a fuse operating time for a given fault current.

    Interpolates linearly on the fuse time-current characteristic
    curve. Only Hermite Polynomial curves (type 6) are supported.

    Args:
        fuse: RelFuse element with associated TypFuse.
        flt_cur: Fault current in Amperes.
        curve: FUSE_TOTAL_CLEAR or FUSE_MIN_MELT.

    Returns:
        Time in seconds on the selected curve, or None if:
        - Fault current below minimum pickup
        - Unsupported curve type
        - Unsupported curve count

    Note:
        Fuse curves are read from the TypFuse e:pmelt characteristic.
        See the layout note above for which curve is which.
    """

    op_time = None

    typechatoc = _fuse_characteristic(fuse)
    if typechatoc is None:
        # A fuse type with no melt characteristic cannot produce a
        # clearing time; report no data rather than failing the feeder.
        return op_time
    # curve type
    curve_type = typechatoc.GetAttribute("e:i_type")
    # curve equation variables
    curve_var = typechatoc.GetAttribute("e:vmat")
    number_of_rows = len(curve_var)

    # Only Hermite Polynomial supported
    if curve_type != 6:
        return op_time

    curve_count = typechatoc.GetAttribute("e:i_curves")

    # p is the current column of the selected curve; time is p + 1.
    if curve_count == 1:
        p = 0
    elif curve_count == 2:
        p = 0 if curve == FUSE_MIN_MELT else 2
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


# Elements already reported in this assessment, as (element full name,
# reason). element_trip_time runs for every element at every fault level
# of every line, so an unusable element would otherwise log the same
# line thousands of times. Cleared by reset_element_warnings at the
# start of each assessment, so each project, and each re-run in the
# same PowerFactory session, reports its own elements.
_ELEMENT_WARNED: set = set()

# What a bad setting, a missing object or malformed curve data raises
# inside the calculation. Deliberately not Exception: a user break
# (powerfactory.ExitError) must still stop the run.
_ELEMENT_ERRORS = (
    ArithmeticError, AttributeError, IndexError, TypeError, ValueError
)

# I/Ip no further than this above 1 is "at pickup". An inverse curve
# runs to infinite time there and rounding can make its denominator
# exactly zero, so no time is the correct result, not a data fault,
# and it is not reported.
_AT_PICKUP_TOL = 1e-6


def reset_element_warnings() -> None:
    """
    Forget which elements have been reported.

    Call at the start of each assessment so every unusable element is
    reported once per assessment.
    """
    _ELEMENT_WARNED.clear()


def _element_label(element: Any) -> str:
    """
    'Relay/Sub-relay/Element' for log messages.

    Reclosers hold each trip's curves in a sub-relay ('Trip 1'), so the
    element's own name is not enough to find it. Never raises.
    """
    try:
        names = [element.loc_name]
        parent = element.GetParent()
        depth = 0
        while (parent is not None and depth < 10
               and parent.GetClassName() == 'ElmRelay'):
            names.append(parent.loc_name)
            parent = parent.GetParent()
            depth += 1
        return "/".join(reversed(names))
    except Exception:
        return str(element)


def _warn_element(element: Any, reason: str, message: str) -> None:
    """
    Log an unusable element, once per element and reason.

    Args:
        element: RelToc or RelIoc element.
        reason: Short key for the kind of problem; one warning is
            logged per element for each key.
        message: What is wrong and how the element is treated.
    """
    try:
        identity = element.GetFullName()
    except Exception:
        identity = _element_label(element)

    key = (identity, reason)
    if key in _ELEMENT_WARNED:
        return
    _ELEMENT_WARNED.add(key)

    try:
        class_name = element.GetClassName()
    except Exception:
        class_name = type(element).__name__
    logger.warning(f"{_element_label(element)} ({class_name}): {message}")


def _usable_pickup(element: Any) -> Optional[float]:
    """
    The element's primary pickup, or None when it cannot be used.

    A pickup of zero (or less, or missing) is not a setting an element
    can operate on. In the IDMT formulas it is a division by zero; on
    an instantaneous element every current exceeds it, so the element
    would trip in its minimum time for every fault.

    Args:
        element: RelToc or RelIoc element.

    Returns:
        e:cpIpset in primary amps when it is above zero, else None
        with a warning.
    """
    pickup = element.GetAttribute("e:cpIpset")
    if pickup is not None and pickup > 0:
        return pickup

    _warn_element(
        element, 'pickup',
        f"pickup (cpIpset) is {pickup!r} A; element left out of trip "
        f"times (treated as not operating)"
    )
    return None


def _curve_operates(element: Any, denominator: Any, i_ip: float) -> bool:
    """
    Whether an inverse curve's denominator gives an operating time.

    At or below zero the current is at or below the curve's asymptote
    and the element does not operate. Right at pickup that is expected
    and silent; further above pickup it means the characteristic's
    parameters are not what the formula expects, and is reported.

    Args:
        element: RelToc element, for the warning.
        denominator: Denominator of the curve formula. Complex when a
            negative base was raised to a fractional power.
        i_ip: Current as a multiple of pickup (I/Ip).

    Returns:
        True when the denominator is a real number above zero.
    """
    if not isinstance(denominator, complex) and denominator > 0:
        return True

    if i_ip - 1 > _AT_PICKUP_TOL:
        _warn_element(
            element, 'denominator',
            f"curve gives no operating time at {i_ip:.4g} x pickup "
            f"(formula denominator {denominator!r}); treated as not "
            f"operating at such currents. Check the parameters of its "
            f"characteristic"
        )
    return False


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

    One unusable element must not stop its feeder (issues log bug #11:
    a RelToc with a zero pickup raised ZeroDivisionError and the whole
    feeder was dropped from the results). Such an element is reported
    once per assessment, by relay and element name, and gives None.

    Args:
        element: RelToc or RelIoc element.
        flt_cur: Fault current in Amperes.

    Returns:
        Operating time in seconds, or None if:
        - Fault current below pickup
        - Unsupported curve type
        - The element cannot give a time: pickup zero or missing, no
          characteristic assigned, current at or below the curve's
          asymptote, or settings or curve data that fail in the
          calculation (each logged as a warning)

    Note:
        For RelIoc elements, returns the minimum operate time if
        the fault current is at or above the pickup setting.
    """
    class_name = element.GetClassName()
    if class_name == 'RelToc':
        calculate = _calculate_toc_time
    elif class_name == 'RelIoc':
        calculate = _calculate_ioc_time
    else:
        return None

    try:
        op_time = calculate(element, flt_cur)
        if op_time is not None and (
            isinstance(op_time, complex) or not math.isfinite(op_time)
        ):
            raise ValueError(f"result {op_time!r} is not a time")
    except _ELEMENT_ERRORS as exc:
        _warn_element(
            element, type(exc).__name__,
            f"trip time could not be calculated at {flt_cur!r} A "
            f"({type(exc).__name__}: {exc}); treated as not operating. "
            f"Check its settings and characteristic"
        )
        return None

    return op_time


def _calculate_toc_time(element: Any, flt_cur: float) -> Optional[float]:
    """
    Calculate IDMT relay element operating time.

    Args:
        element: RelToc element.
        flt_cur: Fault current in Amperes.

    Returns:
        Operating time in seconds, or None if below pickup, or if the
        pickup is unusable, no characteristic is assigned or the
        current is at or below the curve's asymptote.
    """
    pickup = _usable_pickup(element)
    if pickup is None or flt_cur <= pickup:
        return None

    curve_char = element.GetAttribute("e:pcharac")
    if curve_char is None:
        _warn_element(
            element, 'curve',
            "no characteristic curve assigned (pcharac); element left "
            "out of trip times (treated as not operating)"
        )
        return None

    time_dial = element.GetAttribute("e:Tpset")
    curve_type = curve_char.GetAttribute("e:i_type")
    curve_var = curve_char.GetAttribute("e:vmat")

    i_ip = flt_cur / pickup

    # Definite time
    if curve_type == 0:
        return time_dial * curve_var[0][0]

    # IEC 255-3
    if curve_type == 1:
        a1, a2, a3 = curve_var[0][0], curve_var[1][0], curve_var[2][0]
        denominator = i_ip ** a2 - a3
        if not _curve_operates(element, denominator, i_ip):
            return None
        return time_dial * a1 / denominator

    # ANSI/IEEE
    if curve_type == 2:
        a1, a2 = curve_var[0][0], curve_var[1][0]
        a3, a4 = curve_var[2][0], curve_var[3][0]
        denominator = i_ip ** a2 - a3
        if not _curve_operates(element, denominator, i_ip):
            return None
        return time_dial * (a1 / denominator + a4)

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
            denominator = (i_ip - a3) ** a4
            if not _curve_operates(element, denominator, i_ip):
                return None
            return ((a1 + a2) / denominator) * time_dial / 24000
        else:
            denominator = i_ip - 1
            if not _curve_operates(element, denominator, i_ip):
                return None
            return (a5 / denominator) * time_dial / 24000

    # Hermite Polynomial
    if curve_type == 6:
        return _calculate_hermite_toc_time(
            curve_char, curve_var, i_ip, time_dial
        )

    # Special Equation
    if curve_type == 8:
        a1, a2, a3 = curve_var[0][0], curve_var[1][0], curve_var[2][0]
        b1, b2, b3 = curve_var[3][0], curve_var[4][0], curve_var[5][0]
        denominator = (i_ip + b1) ** b2 + b3
        if not _curve_operates(element, denominator, i_ip):
            return None
        return (
            (time_dial * a1) / denominator
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
        Minimum operate time if at or above pickup, None otherwise.
        None also when the pickup is unusable: every current is at or
        above a zero pickup, so the element would otherwise give its
        minimum operate time for every fault.
    """
    pickup = _usable_pickup(element)
    if pickup is None or flt_cur < pickup:
        return None

    return element.GetAttribute("e:cptotime")
