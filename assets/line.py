"""
Distribution line assets model for protection assessment.

A line represents a network conductor section with fault current data
and conductor damage assessment results.
"""

from dataclasses import dataclass
from typing import Optional, Dict,Union, TYPE_CHECKING

import assets.utils as utils
import criteria
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pf_config import pft


@dataclass
class Line:
    """
    Represents a distribution line with fault current and energy data.

    The Line dataclass captures both the physical characteristics of a
    conductor and the fault study results used for conductor damage assessment.

    Core Attributes (set at initialization):
        obj: The PowerFactory ElmLne object
        phases: Number of phases (1, 2, or 3), or None when the line has
            no type or a type the phase count cannot be read from
        l_l_volts: Line-to-line voltage in kV
        line_type: Conductor type description (e.g., "AAC/S 7/4.50"), or
            "NA" for cable systems and for lines with no type
        thermal_rating: Thermal current rating in Amps, or "NA" for
            cables and for lines with no type or no conductor type
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
    phases: Optional[int]
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

    # Reach factors - populated by populate_line_reach_factors after
    # line fault levels exist (pipeline stage 12).
    reach_factors: Optional[Dict[str, Union[float, str]]] = None


def initialise_line_dataclass(
    elmlne: "pft.ElmLne",
    region: str,
    oh_lines: Optional[set] = None,
) -> Optional[Line]:
    """
    Initialize a Line dataclass from a PowerFactory ElmLne object.

    Extracts conductor information including phase count, voltage level,
    conductor type, and thermal rating from the line and its type definition.

    Args:
        elmlne: The PowerFactory ElmLne object
        region: Network region ('SEQ', 'Northern', 'Southern'), used to
            resolve the dline length units.

    Returns:
        Initialized Line dataclass, or None if elmlne is None.

    Note:
        For cable systems (TypCabsys), thermal_rating returns "NA" as
        cable thermal limits are handled differently.

        A line with no type (typ_id None) is kept, with line_type and
        thermal_rating "NA" and phases None, and a warning naming it.
        It used to raise AttributeError here, which is inside the fault
        study and so cost the whole project (Proserpine, Oct-Nov 2026).
        Downstream it reads as any line without a rating: conductor
        damage NO DATA, unassessable on the dashboard.

    Example:
        >>> elm_lne = feeder.GetObjs("ElmLne")[0]
        >>> line = initialise_line_dataclass(elm_lne)
        >>> print(f"{line.obj.loc_name}: {line.line_type}")
    """
    if elmlne is None:
        return None

    if elmlne.typ_id is None:
        logger.warning(
            "Line %s has no type (%s): kept in its device section with "
            "no conductor type, thermal rating or phase count, so its "
            "conductor damage is reported as no data",
            _full_name(elmlne), _service_state(elmlne),
        )

    line_type, thermal_rating = _get_conductor_info(
        elmlne, oh_lines=oh_lines
    )

    return Line(
        obj=elmlne,
        phases=_get_phases(elmlne),
        l_l_volts=_get_voltage(elmlne),
        line_type=line_type,
        thermal_rating=thermal_rating,
        length=_get_length(elmlne, region),
        constr=determine_line_const(elmlne),
    )


def _full_name(line: "pft.ElmLne") -> str:
    """The line's full path for a log line, or its name if unreadable."""
    try:
        return line.GetFullName()
    except AttributeError:
        return str(getattr(line, "loc_name", line))


def _service_state(line: "pft.ElmLne") -> str:
    """'in service' or 'out of service', for the untyped-line warning."""
    try:
        return "out of service" if line.IsOutOfService() else "in service"
    except AttributeError:
        return "service state unknown"


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


DLINE_TO_KM = {
    criteria.SEQ: 0.001,
    criteria.NORTHERN: 1.0,
    criteria.SOUTHERN: 1.0,
}

# Regions with no unit convention, warned about once each. Without this
# the message would repeat for every line in the project
_UNKNOWN_REGION_WARNED: set = set()


def _get_length(line: "pft.ElmLne", region: str) -> Optional[float]:
    """
    Read a line's circuit length in km.

    An unrecognised region yields None rather than raising: a missing
    unit convention should cost the length metrics, not the conductor
    damage and coordination assessment for the whole project. None
    propagates as an unreadable length, which the fact layer counts in
    oh_lines_no_length and excludes from every km total, so the
    affected project's km figures come out blank rather than wrong.

    Args:
        line: PowerFactory ElmLne object.
        region: 'SEQ', 'Northern' or 'Southern', from
            pf_protection_helper.obtain_region.

    Returns:
        Length in km, or None if the region has no unit convention or
        the attribute is missing or non-numeric.
    """
    scale = DLINE_TO_KM.get(region)
    if scale is None:
        if region not in _UNKNOWN_REGION_WARNED:
            _UNKNOWN_REGION_WARNED.add(region)
            logger.error(
                "No dline unit convention is defined for region %r "
                "(known: %s). Line lengths are unavailable for this "
                "project, so every km-based dashboard metric will be "
                "empty. Add the region's convention to DLINE_TO_KM in "
                "assets/line.py - do not assume one, as the two fleets "
                "differ by a factor of a thousand.",
                region, sorted(DLINE_TO_KM),
            )
        return None

    try:
        return float(line.GetAttribute("dline")) * scale
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
        Number of phases (1, 2, or 3), or None when the line has no type
        (the caller warns) or a type of another class (warned here).
        Either used to raise, which aborted the whole project.
    """
    line_type = line.typ_id
    if line_type is None:
        return None

    construction = line_type.GetClassName()

    if construction == "TypGeo":
        return int(line_type.xy_c[0][0])
    elif construction == "TypCabsys":
        return int(line_type.GetAttribute("nphas")[0])
    elif construction == "TypLne":
        return int(line_type.GetAttribute("nlnph"))
    else:
        logger.warning(
            "Line %s: phase count not read from a %s line type",
            _full_name(line), construction,
        )
        return None


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


def _match_conductor(line_type: str, cond_rating_dict: dict):
    """
    Find the ratings row for a PowerFactory conductor type name.

    Type names are '<conductor>_<voltage>' (e.g. '3/2.75 SC/GZ_22kV').
    An exact match on the part before the last '_' wins. Otherwise the
    longest table name contained in the type name is used, so a short
    name ('7/1.75 HDBC', 'Box') can never shadow a longer one
    ('37/1.75 HDBC', 'Boxing') regardless of row order.
    """
    base = line_type.rsplit("_", 1)[0].strip()
    if base in cond_rating_dict:
        return cond_rating_dict[base]
    matches = [cond for cond in cond_rating_dict if cond and cond in line_type]
    if not matches:
        return None
    return cond_rating_dict[max(matches, key=len)]


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

    A line with no type returns "NA" for both (the caller warns), as
    does a tower-geometry line with no conductor type (warned here).

    Args:
        line: PowerFactory ElmLne object

    Returns:
        Tuple of (line_type: str, thermal_rating: float or "NA")
    """
    line_type = "NA"
    thermal_rating = "NA"

    if line.typ_id is None:
        return line_type, thermal_rating

    construction = line.typ_id.GetClassName()

    cond_rating_dict = utils.conductors_properties()

    if oh_lines is not None and line in oh_lines:
        typ_con = line.GetAttribute("e:pCondCir")
        if typ_con is None:
            _warn_no_conductor(line)
            return line_type, thermal_rating
        line_type = typ_con.loc_name
        data = _match_conductor(line_type, cond_rating_dict)
        if data is not None:
            thermal_rating = float(data[0]) * 1000
    else:
        if construction == "TypGeo":
            typ_con = line.GetAttribute("e:pCondCir")
            if typ_con is None:
                _warn_no_conductor(line)
                return line_type, thermal_rating
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


def _warn_no_conductor(line: "pft.ElmLne") -> None:
    """Warn that a tower-geometry line has no conductor type assigned."""
    logger.warning(
        "Line %s has no conductor type (pCondCir): no thermal rating, so "
        "its conductor damage is reported as no data",
        _full_name(line),
    )
