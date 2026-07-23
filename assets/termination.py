"""
Network termination (terminal) assets model for protection assessment.

A termination represents a network terminal where fault studies are performed
and protection reach is evaluated.
"""

import math
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from assets.enums import ph_attr_lookup
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pf_config import pft


@dataclass
class Termination:
    """
    Represents a network terminal with fault current data.

    The Termination dataclass captures fault study results at a specific
    network location, used for protection coordination and reach analysis.

    Core Attributes (set at initialization):
        obj: The PowerFactory ElmTerm object
        phases: Number of phases (1, 2, or 3)
        l_l_volts: Line-to-line voltage in kV

    Construction Type (determined during analysis):
        constr: Construction type string ("OH", "UG", or "SWER")
                Used to determine appropriate fault impedance values.

    Maximum Fault Currents (populated by fault study):
        max_fl_3ph: Maximum 3-phase fault current (A)
        max_fl_2ph: Maximum 2-phase fault current (A)
        max_fl_pg: Maximum phase-ground fault current (A)

    Minimum Fault Currents (populated by fault study):
        min_fl_3ph: Minimum 3-phase fault current (A)
        min_fl_2ph: Minimum 2-phase fault current (A)
        min_fl_pg: Minimum phase-ground fault current (A)

    Impedance-Specific Minimum Fault Currents:
        min_fl_pg10: Minimum PG fault with 10 ohm fault resistance (A)
        min_fl_pg50: Minimum PG fault with 50 ohm fault resistance (A)

    System Normal Minimum Fault Currents:
        min_sn_fl_2ph: Minimum system normal 2-phase fault current (A)
        min_sn_fl_pg: Minimum system normal phase-ground fault current (A)
        min_sn_fl_pg10: Min sys normal PG fault with 10 ohm resistance (A)
        min_sn_fl_pg50: Min sys normal PG fault with 50 ohm resistance (A)

    Example:
        >>> term = initialise_term_dataclass(elm_term)
        >>> # After fault studies:
        >>> print(f"Max PG fault at {term.obj.loc_name}: {term.max_fl_pg}A")
    """
    # Core identification - always required
    obj: "pft.ElmTerm"
    phases: int
    l_l_volts: float

    # Construction type - determined during analysis
    constr: Optional[str] = None

    # Maximum fault currents - populated by fault study
    max_fl_3ph: Optional[float] = None
    max_fl_2ph: Optional[float] = None
    max_fl_pg: Optional[float] = None

    # Minimum fault currents - populated by fault study
    min_fl_3ph: Optional[float] = None
    min_fl_2ph: Optional[float] = None
    min_fl_pg: Optional[float] = None

    # Impedance-specific minimum fault currents (regional models)
    min_fl_pg10: Optional[float] = None
    min_fl_pg50: Optional[float] = None

    # System normal minimum fault currents
    min_sn_fl_2ph: Optional[float] = None
    min_sn_fl_pg: Optional[float] = None
    min_sn_fl_pg10: Optional[float] = None
    min_sn_fl_pg50: Optional[float] = None

    # Impedance values
    max_r0: Optional[float] = None
    max_x0: Optional[float] = None
    max_r1: Optional[float] = None
    max_x1: Optional[float] = None
    max_r2: Optional[float] = None
    max_x2: Optional[float] = None
    min_r0: Optional[float] = None
    min_x0: Optional[float] = None
    min_r1: Optional[float] = None
    min_x1: Optional[float] = None
    min_r2: Optional[float] = None
    min_x2: Optional[float] = None
    min_sn_r0: Optional[float] = None
    min_sn_x0: Optional[float] = None
    min_sn_r1: Optional[float] = None
    min_sn_x1: Optional[float] = None
    min_sn_r2: Optional[float] = None
    min_sn_x2: Optional[float] = None


def initialise_term_dataclass(elmterm: "pft.ElmTerm") -> Optional[Termination]:
    """
    Initialize a Termination dataclass from a PowerFactory ElmTerm object.

    Creates a assets model instance for the terminal with basic electrical
    parameters. Fault current values are populated later by fault studies.

    Args:
        elmterm: The PowerFactory ElmTerm object

    Returns:
        Initialized Termination dataclass, or None if elmterm is None.

    Example:
        >>> elm_term = device.cubicle.cterm
        >>> term = initialise_term_dataclass(elm_term)
        >>> print(f"Terminal: {term.obj.loc_name}, {term.phases}ph, {term.l_l_volts}kV")
    """
    if elmterm is None:
        return None

    return Termination(
        obj=elmterm,
        phases=ph_attr_lookup(elmterm.phtech),
        l_l_volts=round(elmterm.uknom, 2),
    )


def _z_available(*values) -> bool:
    """
    True if every impedance component is present and non-degenerate.

    Guards against both missing study results (None) and an all-zero
    impedance set, which would divide by zero.
    """
    if any(v is None for v in values):
        return False
    return any(v != 0 for v in values)


def build_term_fls(term: Termination):

    c_min_v = term.l_l_volts
    c_max_v = term.l_l_volts * 1.1

    # Maximums
    if term.phases == 3 and _z_available(term.max_r1, term.max_x1):
        term.max_fl_3ph = get_3p_fault(c_max_v, term.max_r1, term.max_x1)
    elif term.phases != 3:
        term.max_fl_3ph = 0
    else:
        term.max_fl_3ph = None
    if term.phases > 1 and _z_available(term.max_r1, term.max_x1, term.max_r2, term.max_x2):
        term.max_fl_2ph = get_2p_fault(c_max_v, term.max_r1, term.max_x1, term.max_r2, term.max_x2)
    elif term.phases == 1:
        term.max_fl_2ph = 0
    else:
        term.max_fl_2ph = None
    if _z_available(term.max_r0, term.max_x0, term.max_r1, term.max_x1, term.max_r2, term.max_x2):
        term.max_fl_pg = get_pg_fault(
            c_max_v, term.max_r0, term.max_x0, term.max_r1, term.max_x1, term.max_r2, term.max_x2, 0)
    else:
        term.max_fl_pg = None

    # Minimums
    if term.phases == 3 and _z_available(term.min_r1, term.min_x1):
        term.min_fl_3ph = get_3p_fault(c_min_v, term.min_r1, term.min_x1)
    elif term.phases != 3:
        term.min_fl_3ph = 0
    else:
        term.min_fl_3ph = None
    if term.phases > 1 and _z_available(term.min_r1, term.min_x1, term.min_r2, term.min_x2):
        term.min_fl_2ph = get_2p_fault(c_min_v, term.min_r1, term.min_x1, term.min_r2, term.min_x2)
    elif term.phases == 1:
        term.min_fl_2ph = 0
    else:
        term.min_fl_2ph = None
    if _z_available(term.min_r0, term.min_x0, term.min_r1, term.min_x1, term.min_r2, term.min_x2):
        term.min_fl_pg = get_pg_fault(
            c_min_v, term.min_r0, term.min_x0, term.min_r1, term.min_x1, term.min_r2, term.min_x2, 0)
        term.min_fl_pg10 = get_pg_fault(
            c_min_v, term.min_r0, term.min_x0, term.min_r1, term.min_x1, term.min_r2, term.min_x2, 10)
        term.min_fl_pg50 = get_pg_fault(
            c_min_v, term.min_r0, term.min_x0, term.min_r1, term.min_x1, term.min_r2, term.min_x2, 50)
    else:
        term.min_fl_pg = None
        term.min_fl_pg10 = None
        term.min_fl_pg50 = None

    # System Normal minimums
    if term.phases > 1 and _z_available(term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2):
        term.min_sn_fl_2ph = get_2p_fault(c_min_v, term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2)
    elif term.phases == 1:
        term.min_sn_fl_2ph = 0
    else:
        term.min_sn_fl_2ph = None
    if _z_available(term.min_sn_r0, term.min_sn_x0, term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2):
        term.min_sn_fl_pg = get_pg_fault(
            c_min_v, term.min_sn_r0, term.min_sn_x0, term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2, 0)
        term.min_sn_fl_pg10 = get_pg_fault(
            c_min_v, term.min_sn_r0, term.min_sn_x0, term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2, 10)
        term.min_sn_fl_pg50 = get_pg_fault(
            c_min_v, term.min_sn_r0, term.min_sn_x0, term.min_sn_r1, term.min_sn_x1, term.min_sn_r2, term.min_sn_x2, 50)
    else:
        term.min_sn_fl_pg = None
        term.min_sn_fl_pg10 = None
        term.min_sn_fl_pg50 = None


def get_3p_fault(v, r1, x1) -> float:
    """

    :param v:
    :param r1:
    :param x1:
    :return:
    """
    z1 = math.sqrt(r1**2 + x1**2)
    current = v / (math.sqrt(3) * z1)
    return  round(current * 1000)


def get_2p_fault(v, r1, x1, r2, x2) -> float:
    """

    :param v:
    :param r1:
    :param x1:
    :param r2:
    :param x2:
    :return:
    """
    z = math.sqrt((r1+r2)**2 + (x1+x2)**2)
    current = v / z
    return round(current * 1000)


def get_pg_fault(v, r0, x0, r1, x1, r2, x2, rf) -> float:
    """

    :param v:
    :param r0:
    :param r1:
    :param r2:
    :param rf:
    :param x0:
    :param x1:
    :param x2:
    :return:
    """
    l_g_v = v / math.sqrt(3)
    z = math.sqrt((r0 + r1 + r2 + 3 * rf) ** 2 + (x0 + x1 + x2) ** 2)
    current = 3 * l_g_v / z
    return round(current * 1000)