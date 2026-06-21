"""
Protection device domain model for protection assessment.

A device represents a protection element (relay or fuse) with its
electrical context, fault current data, and network topology.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, TYPE_CHECKING

from domain.enums import ElementType, ph_attr_lookup

if TYPE_CHECKING:
    from pf_config import pft


@dataclass
class Device:
    """
    Represents a protection device (relay or fuse) with its electrical context.

    The Device dataclass captures both the static configuration of a protection
    device and the dynamic results from fault studies and network analysis.

    Core Attributes (set at initialization):
        obj: The PowerFactory device object (ElmRelay or RelFuse)
        cubicle: The device's cubicle location in the network
        term: The connected terminal
        phases: Number of phases (1, 2, or 3)
        l_l_volts: Line-to-line voltage in kV

    Capacity Data (populated during analysis):
        ds_capacity: Total downstream transformer capacity in kVA
        max_ds_tr: Largest downstream transformer (Tfmr dataclass)

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

    Topology (populated by network tracing):
        sect_terms: Downstream terminals in this protection section
        sect_loads: Downstream loads/transformers in this section
        sect_lines: Lines in this protection section
        us_devices: Upstream protection devices (backup protection)
        ds_devices: Downstream protection devices

    Example:
        >>> device = initialise_dev_dataclass(elm_relay)
        >>> print(f"Device {device.obj.loc_name} at {device.l_l_volts}kV")
        >>> print(f"Max 3ph fault: {device.max_fl_3ph}A")
    """
    # Core identification - always required
    obj: Any
    cubicle: "pft.StaCubic"
    term: "pft.ElmTerm"
    phases: int
    l_l_volts: float

    # Capacity data - populated during analysis
    ds_capacity: Optional[float] = None
    max_ds_tr: Optional[Any] = None  # Will be Tfmr type

    # Maximum fault currents - populated by fault study
    max_fl_3ph: Optional[float] = None
    max_fl_2ph: Optional[float] = None
    max_fl_pg: Optional[float] = None

    # Minimum fault currents - populated by fault study
    min_fl_3ph: Optional[float] = None
    min_fl_2ph: Optional[float] = None
    min_fl_pg: Optional[float] = None

    # Minimum system normal fault currents - populated by fault study
    min_sn_fl_2ph: Optional[float] = None
    min_sn_fl_pg: Optional[float] = None

    # Topology - populated by network tracing
    sect_terms: List[Any] = field(default_factory=list)
    sect_loads: List[Any] = field(default_factory=list)
    sect_lines: List[Any] = field(default_factory=list)
    us_devices: List[Any] = field(default_factory=list)
    ds_devices: List[Any] = field(default_factory=list)


def initialise_dev_dataclass(element: Any) -> Optional[Device]:
    """
    Initialize a Device dataclass from a PowerFactory protection device.

    Handles different device types (ElmRelay, RelFuse, ElmFeeder, ElmCoup)
    and extracts the appropriate cubicle reference for each type.

    Args:
        element: The PowerFactory device object. Supported types:
                 - ElmRelay: Protection relay
                 - RelFuse: Fuse element
                 - ElmFeeder: Feeder (uses obj_id as cubicle)
                 - ElmCoup: Coupler/switch (uses bus1 as cubicle)

    Returns:
        Initialized Device dataclass, or None if element is None.

    Example:
        >>> elm_relay = app.GetCalcRelevantObjects("*.ElmRelay")[0]
        >>> device = initialise_dev_dataclass(elm_relay)
        >>> print(device.phases, device.l_l_volts)
    """
    if element is None:
        return None

    # Determine the cubicle based on element type
    class_name = element.GetClassName()

    if class_name == ElementType.FEEDER.value:
        cubicle = element.obj_id
    elif class_name == ElementType.COUPLER.value:
        cubicle = element.bus1
    else:
        # Standard case: ElmRelay, RelFuse, etc.
        cubicle = element.fold_id

    return Device(
        obj=element,
        cubicle=cubicle,
        term=cubicle.cterm,
        phases=ph_attr_lookup(cubicle.cterm.phtech),
        l_l_volts=round(cubicle.cterm.uknom, 2),
    )