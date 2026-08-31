"""
Distribution line assets model for protection assessment.

A line represents a network conductor section with fault current data
and conductor damage assessment results.
"""

from dataclasses import dataclass
from typing import Optional, Union, TYPE_CHECKING

import assets.utils as utils

if TYPE_CHECKING:
    from pf_config import pft

from importlib import reload
reload(utils)

@dataclass
class Line:
    """
    Represents a distribution line with fault current and energy data.

    The Line dataclass captures both the physical characteristics of a
    conductor and the fault study results used for conductor damage assessment.

    Core Attributes (set at initialization):
        obj: The PowerFactory ElmLne object
        phases: Number of phases (1, 2, or 3)
        l_l_volts: Line-to-line voltage in kV
        line_type: Conductor type description (e.g., "AAC/S 7/4.50")
        thermal_rating: Thermal current rating in Amps, or "NA" for cables
        length: Circuit length in km (PF 'dline'), or None if unreadable
        constr: Construction class - "OH", "UG" or "SWER"

    Maximum Fault Currents (populated by fault study):
        max_fl_3ph: Maximum 3-phase fault current (A)
        max_fl_2ph: Maximum 2-phase fault current (A)
        max_fl_pg: Maximum phase-ground fault current (A)

    Minimum Fault Currents (populated by fault study):
        min_fl_3ph: Minimum 3-phase fault current (A)
        min_fl_2ph: Minimum 2-phase fault current (A)
        min_fl_pg: Minimum phase-ground fault current (A)

    System Normal Minimum Fault Currents:
        min_sn_fl_2ph: Minimum system normal 2-phase fault current (A)
        min_sn_fl_pg: Minimum system normal phase-ground fault current (A)

    Energy Calculations (populated during conductor damage analysis):
        ph_energy: Phase fault let-through energy (I²t in A²s)
        ph_clear_time: Phase fault clearing time (seconds)
        ph_fl: Fault level used for phase energy calculation (A)
        pg_energy: Ground fault let-through energy (I²t in A²s)
        pg_clear_time: Ground fault clearing time (seconds)
        pg_fl: Fault level used for ground energy calculation (A)

    Example:
        >>> line = initialise_line_dataclass(elm_lne)
        >>> print(f"Line: {line.obj.loc_name}, Type: {line.line_type}")
        >>> print(f"Thermal rating: {line.thermal_rating}A")
        >>> # After conductor damage analysis:
        >>> if line.ph_energy:
        ...     allowable = line.thermal_rating ** 2
        ...     status = "PASS" if line.ph_energy <= allowable else "FAIL"
    """
    # Core identification - always required
    obj: "pft.ElmLne"
    phases: int
    l_l_volts: float
    line_type: str
    thermal_rating: Union[float, str]  # Can be "NA" for cables
    length: Optional[float] = None
    constr: Optional[str] = None

    # Maximum fault currents - populated by fault study
    max_fl_3ph: Optional[float] = None
    max_fl_2ph: Optional[float] = None
    max_fl_pg: Optional[float] = None

    # Minimum fault currents - populated by fault study
    min_fl_3ph: Optional[float] = None
    min_fl_2ph: Optional[float] = None
    min_fl_pg: Optional[float] = None

    # Minimum system normal fault currents
    min_sn_fl_2ph: Optional[float] = None
    min_sn_fl_pg: Optional[float] = None

    # Energy calculations - populated during conductor damage analysis
    ph_energy: Optional[float] = None
    ph_clear_time: Optional[float] = None
    ph_fl: Optional[float] = None
    pg_energy: Optional[float] = None
    pg_clear_time: Optional[float] = None
    pg_fl: Optional[float] = None


def initialise_line_dataclass(
    elmlne: "pft.ElmLne",
    oh_lines: Optional[set] = None,
) -> Optional[Line]:
    """
    Initialize a Line dataclass from a PowerFactory ElmLne object.

    Extracts conductor information including phase count, voltage level,
    conductor type, and thermal rating from the line and its type definition.

    Args:
        elmlne: The PowerFactory ElmLne object

    Returns:
        Initialized Line dataclass, or None if elmlne is None.

    Note:
        For cable systems (TypCabsys), thermal_rating returns "NA" as
        cable thermal limits are handled differently.

    Example:
        >>> elm_lne = feeder.GetObjs("ElmLne")[0]
        >>> line = initialise_line_dataclass(elm_lne)
        >>> print(f"{line.obj.loc_name}: {line.line_type}")
    """
    if elmlne is None:
        return None

    line_type, thermal_rating = _get_conductor_info(
        elmlne, oh_lines=oh_lines
    )

    return Line(
        obj=elmlne,
        phases=_get_phases(elmlne),
        l_l_volts=_get_voltage(elmlne),
        line_type=line_type,
        thermal_rating=thermal_rating,
        length=_get_length(elmlne),
        constr=determine_line_const(elmlne),
    )


def determine_line_const(line: "pft.ElmLne") -> str:
    """
    Determine the construction class of a line.

    Args:
        line: PowerFactory ElmLne object.

    Returns:
        One of "OH", "UG", "SWER".
    """

    try:
        line_type = line.typ_id
        if line_type is not None and 'SWER' in line_type.loc_name:
            return "SWER"
    except AttributeError:
        pass

    try:
        if line.IsCable() or "CABLE" in line.loc_name:
            return "UG"
        if line.typ_id is not None and line.typ_id.HasAttribute("cohl_"):
            return "UG"
    except AttributeError:
        pass

    return "OH"


def is_overhead(line: Line) -> bool:
    """
    Report whether a line is overhead construction.

    SWER lines are overhead, so both "OH" and "SWER" return True.
    This is the single definition of "overhead" for length
    aggregation; testing ``constr == "OH"`` elsewhere would silently
    drop SWER km from every regional feeder.

    Args:
        line: Line dataclass with constr populated.

    Returns:
        True for overhead (including SWER) construction.
    """
    return line.constr in ("OH", "SWER")


def _get_length(line: "pft.ElmLne") -> Optional[float]:
    """
    Read a line's circuit length in km.

    Args:
        line: PowerFactory ElmLne object.

    Returns:
        Length in km, or None if the attribute is missing or
        non-numeric.
    """
    try:
        return float(line.GetAttribute("dline"))
    except (AttributeError, TypeError, ValueError):
        return None


def _get_phases(line: "pft.ElmLne") -> int:
    """
    Get number of phases from line construction type.

    Handles different line type definitions:
    - TypGeo: Tower geometry definition
    - TypCabsys: Cable system definition
    - TypLne: Standard line type definition

    Args:
        line: PowerFactory ElmLne object

    Returns:
        Number of phases (1, 2, or 3)

    Raises:
        TypeError: If line type is not a recognized construction type
    """
    construction = line.typ_id.GetClassName()

    if construction == "TypGeo":
        return int(line.typ_id.xy_c[0][0])
    elif construction == "TypCabsys":
        return int(line.typ_id.GetAttribute("nphas")[0])
    elif construction == "TypLne":
        return int(line.typ_id.GetAttribute("nlnph"))
    else:
        raise TypeError(f"{construction}: Unhandled construction type")


def _get_voltage(line: "pft.ElmLne") -> float:
    """
    Get line-to-line operating voltage from connected terminals.

    Args:
        line: PowerFactory ElmLne object

    Returns:
        Nominal voltage in kV, or 0.0 if no terminal found
    """
    for term in line.GetConnectedElements():
        try:
            return term.uknom
        except AttributeError:
            pass
    return 0.0


def _get_conductor_info(
    line: "pft.ElmLne",
    oh_lines: Optional[set] = None,
) -> tuple:
    """
    Get conductor type name and thermal rating.

    For overhead lines, extracts the conductor type
    name and obtains 1-second thermal rating from csv file.

    For underground lines, extracts the conductor type
    name and 1-second thermal rating from PowerFactory.

    For cable systems, returns "NA"
    for both values.

    Args:
        line: PowerFactory ElmLne object

    Returns:
        Tuple of (line_type: str, thermal_rating: float or "NA")
    """
    construction = line.typ_id.GetClassName()

    line_type = "NA"
    thermal_rating = "NA"

    cond_rating_dict = utils.conductors_properties()

    if oh_lines is not None and line in oh_lines:
        typ_con = line.GetAttribute("e:pCondCir")
        line_type = typ_con.loc_name
        for cond, data in cond_rating_dict.items():
            if cond in line_type:
                thermal_rating = float(data[0]) * 1000
                break
    else:
        if construction == "TypGeo":
            typ_con = line.GetAttribute("e:pCondCir")
            line_type =  typ_con.loc_name
            thermal_rating = round(typ_con.GetAttribute("e:Ithr"), 3) * 1000
        elif construction == "TypLne":
            typ_con = line.GetAttribute("e:typ_id")
            line_type = typ_con.loc_name
            thermal_rating = round(typ_con.GetAttribute("e:Ithr"), 3) * 1000
        else:
            # Cable systems - thermal rating handled differently
            pass
    return line_type, thermal_rating