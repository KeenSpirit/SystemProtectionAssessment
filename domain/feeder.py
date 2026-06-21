"""
Feeder domain model for protection assessment.

A feeder represents a distribution circuit originating from a substation,
containing protection devices, network topology, and operating parameters.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from pf_config import pft


@dataclass
class Feeder:
    """
    Represents a distribution feeder and its associated protection devices.

    The Feeder is the top-level container for protection assessment, holding
    references to all devices, network topology, and study results.

    Attributes:
        obj: The PowerFactory ElmFeeder object
        cubicle: The feeder's source cubicle (connection point)
        term: The source terminal at the substation
        sys_volts: System nominal voltage in kV

    Populated During Analysis:
        devices: List of protection Device objects on this feeder
        bu_devices: Dictionary of backup devices by grid
        open_points: Network open points (normally open switches)

    Example:
        >>> feeder = initialise_fdr_dataclass(elm_feeder)
        >>> print(f"Feeder {feeder.obj.loc_name} at {feeder.sys_volts}kV")
        >>> for device in feeder.devices:
        ...     print(f"  Device: {device.obj.loc_name}")
    """
    # Core identification - set at initialization
    obj: "pft.ElmFeeder"
    cubicle: "pft.StaCubic"
    term: "pft.ElmTerm"
    sys_volts: float

    # Topology - populated during analysis
    devices: List[Any] = field(default_factory=list)
    bu_devices: Dict[str, Any] = field(default_factory=dict)
    open_points: Dict[str, Any] = field(default_factory=dict)


def initialise_fdr_dataclass(element: "pft.ElmFeeder") -> Feeder:
    """
    Initialize a Feeder dataclass from a PowerFactory ElmFeeder object.

    Extracts the essential feeder information from the PowerFactory object
    and creates a domain model instance for use in protection assessment.

    Args:
        element: The PowerFactory ElmFeeder object

    Returns:
        Initialized Feeder dataclass with core attributes set.
        The devices, bu_devices, and open_points lists are empty
        and will be populated during subsequent analysis steps.

    Example:
        >>> elm_feeder = app.GetCalcRelevantObjects("MyFeeder.ElmFeeder")[0]
        >>> feeder = initialise_fdr_dataclass(elm_feeder)
    """
    return Feeder(
        obj=element,
        cubicle=element.obj_id,
        term=element.cn_bus,
        sys_volts=element.cn_bus.uknom,
    )