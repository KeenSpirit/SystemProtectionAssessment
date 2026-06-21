"""
Short-circuit analysis execution and result extraction.

This module provides functions to execute PowerFactory short-circuit
calculations and extract fault current results from terminals and lines.

Functions:
    short_circuit: Execute a short-circuit study with given parameters
    get_terminal_current: Extract maximum phase current from a terminal
    get_line_current: Extract maximum phase current from a line
"""

from typing import Optional
from pf_config import pft
from fault_study import study_templates
from importlib import reload
reload(study_templates)


def short_circuit(
    app: pft.Application,
    bound: str,
    f_type: str,
    consider_prot: str,
    location: Optional[pft.ElmLne] = None,
    relative: int = 0
) -> int:
    """
    Configure and execute a short-circuit calculation.

    Sets up the PowerFactory short-circuit command module with the
    specified parameters and executes the calculation.

    Args:
        app: PowerFactory application instance.
        bound: Fault level bound - 'Max' or 'Min'.
        f_type: Fault type - '3-Phase', '2-Phase', or 'Phase-Ground'.
        consider_prot: Protection consideration - 'None' or 'All'.
        location: Element for fault location. None for all busbars.
        relative: Fault distance from terminal as percentage (0-99).

    Returns:
        Execution result code from ComShc.Execute().

    Example:
        >>> # Maximum 3-phase fault at all busbars
        >>> short_circuit(app, 'Max', '3-Phase', 'All')
        >>>
        >>> # Minimum ground fault at specific line location
        >>> short_circuit(app, 'Min', 'Phase-Ground', 'None', line, 50)
    """
    comshc = app.GetFromStudyCase("Short_Circuit.ComShc")
    study_templates.apply_sc(
        comshc, bound, f_type, consider_prot, location, relative
    )

    return comshc.Execute()


def get_terminal_current(elmterm: pft.ElmTerm) -> float:
    """
    Extract the maximum phase fault current from a terminal.

    Reads the short-circuit current results for all three phases
    and returns the maximum value. Currents are converted from kA
    to Amperes.

    Args:
        elmterm: PowerFactory ElmTerm object with fault study results.

    Returns:
        Maximum phase current in Amperes, or 0 if no results available.

    Note:
        Must be called after a short-circuit calculation has been
        executed. The terminal must be part of the calculation scope.
    """
    def _check_att(obj, attribute):
        """Check attribute exists and return scaled value."""
        if obj.HasAttribute(attribute):
            terminal_fl = round(obj.GetAttribute(attribute) * 1000)
        else:
            terminal_fl = 0
        return terminal_fl

    ia = _check_att(elmterm, 'm:Ikss:A')
    ib = _check_att(elmterm, 'm:Ikss:B')
    ic = _check_att(elmterm, 'm:Ikss:C')

    return max(ia, ib, ic)


def get_line_current(elmlne: pft.ElmLne) -> Optional[float]:
    """
    Extract the maximum phase fault current from a line.

    Reads short-circuit current results from both line terminals
    (bus1 and bus2) for all three phases and returns the maximum
    value. Currents are converted from kA to Amperes.

    Args:
        elmlne: PowerFactory ElmLne object with fault study results.

    Returns:
        Maximum phase current in Amperes, or None if no results
        available.

    Note:
        Used for floating terminal fault calculations where the
        fault is applied at a specific location along the line.
    """
    currents = []

    if elmlne.HasAttribute('bus1'):
        currents.extend([
            elmlne.GetAttribute('m:Ikss:bus1:A'),
            elmlne.GetAttribute('m:Ikss:bus1:B'),
            elmlne.GetAttribute('m:Ikss:bus1:C')
        ])

    if elmlne.HasAttribute('bus2'):
        currents.extend([
            elmlne.GetAttribute('m:Ikss:bus2:A'),
            elmlne.GetAttribute('m:Ikss:bus2:B'),
            elmlne.GetAttribute('m:Ikss:bus2:C')
        ])

    return round(max(currents) * 1000) if currents else None

