"""
Network termination (terminal) domain model for protection assessment.

A termination represents a network terminal where fault studies are performed
and protection reach is evaluated.
"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from domain.enums import ph_attr_lookup

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


def initialise_term_dataclass(elmterm: "pft.ElmTerm") -> Optional[Termination]:
    """
    Initialize a Termination dataclass from a PowerFactory ElmTerm object.

    Creates a domain model instance for the terminal with basic electrical
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