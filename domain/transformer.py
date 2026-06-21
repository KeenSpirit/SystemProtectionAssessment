"""
Transformer/load domain model for protection assessment.

The Tfmr class represents distribution transformers or aggregated loads
for fusing coordination and protection reach analysis.
"""

from dataclasses import dataclass
from typing import Optional, Union, TYPE_CHECKING

from domain.enums import ElementType

if TYPE_CHECKING:
    from pf_config import pft


@dataclass
class Tfmr:
    """
    Represents a transformer or load for fusing coordination.

    The Tfmr dataclass is used for both individual distribution transformers
    (ElmTr2 in regional models) and aggregated load representations (ElmLod
    in SEQ models). It captures the information needed for fuse selection
    and protection coordination.

    Core Attributes:
        obj: The PowerFactory object (ElmLod or ElmTr2), or None
        term: The HV terminal connection point
        load_kva: Rated capacity in kVA

    Fault Data (populated during analysis):
        max_ph: Maximum phase fault current at transformer (A)
        max_pg: Maximum phase-ground fault current at transformer (A)

    Fusing Information (populated during fuse selection):
        fuse: Associated fuse type/size string
        insulation: Insulation type ("air" or "oil") for RMU selection
        impedance: Transformer impedance class ("high" or "low")

    Example:
        >>> tfmr = initialise_load_dataclass(elm_lod)
        >>> print(f"Transformer: {tfmr.load_kva}kVA at {tfmr.term.loc_name}")
        >>> # For RMU fuse selection:
        >>> tfmr.insulation = "air"
        >>> tfmr.impedance = "low"
    """
    obj: Optional[Union["pft.ElmLod", "pft.ElmTr2"]] = None
    term: Optional["pft.ElmTerm"] = None
    load_kva: Optional[float] = None
    max_ph: Optional[float] = None
    max_pg: Optional[float] = None
    fuse: Optional[str] = None
    insulation: Optional[str] = None
    impedance: Optional[str] = None


def initialise_load_dataclass(
    load: Optional[Union["pft.ElmLod", "pft.ElmTr2"]]
) -> Tfmr:
    """
    Initialize a Tfmr dataclass from a PowerFactory load or transformer.

    Handles both ElmLod (aggregated load) and ElmTr2 (transformer) objects,
    extracting the appropriate terminal reference and capacity value.

    Args:
        load: The PowerFactory object. Supported types:
              - ElmLod: Aggregated load (uses Strat for kVA)
              - ElmTr2: Transformer (uses Snom_a * 1000 for kVA)
              - None: Returns empty Tfmr dataclass

    Returns:
        Initialized Tfmr dataclass. Returns an empty Tfmr if load is None
        or not a recognized type.

    Example:
        >>> # For SEQ aggregated loads:
        >>> elm_lod = feeder.GetObjs("ElmLod")[0]
        >>> tfmr = initialise_load_dataclass(elm_lod)
        >>>
        >>> # For regional transformers:
        >>> elm_tr2 = feeder.GetObjs("ElmTr2")[0]
        >>> tfmr = initialise_load_dataclass(elm_tr2)
    """
    if load is None:
        return Tfmr()

    class_name = load.GetClassName()

    if class_name == ElementType.LOAD.value:
        return Tfmr(
            obj=load,
            term=load.bus1.cterm,
            load_kva=round(load.Strat),
        )

    if class_name == ElementType.TFMR.value:
        return Tfmr(
            obj=load,
            term=load.bushv.cterm,
            load_kva=round(load.Snom_a * 1000),
        )

    # Unrecognized type - return empty
    return Tfmr()