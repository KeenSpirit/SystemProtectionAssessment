"""
Relay element retrieval and filtering.

This module provides functions to retrieve relay elements (relays, fuses)
from the PowerFactory model and filter them by type and capability.

Functions:
    get_all_relays: Retrieve all active relays from the model
    get_prot_elements: Get protection elements from a relay device
    get_active_elements: Filter elements by fault type capability
"""

from typing import Dict, List, Union, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from pf_config import pft

logger = logging.getLogger(__name__)

# Only warn once per (class, attribute) — this runs per element, per
# device, per fault study, and would otherwise flood the JSONL log.
_ATTR_WARNED: set = set()


def _safe_attr(obj, attribute: str, default=None):
    """
    Resolve a (possibly chained) PowerFactory attribute, tolerating a
    broken chain.

    GetAttribute("r:typ_id:e:sfiec") raises AttributeError when the
    element has no type assigned, or when the assigned type class does
    not carry the requested field. Both are model-data conditions, not
    code faults, so return `default` and let the caller filter the
    element out rather than killing the project.
    """
    try:
        return obj.GetAttribute(attribute)
    except AttributeError:
        key = (obj.GetClassName(), attribute)
        if key not in _ATTR_WARNED:
            _ATTR_WARNED.add(key)
            logger.warning(
                "%s cannot resolve '%s' (first seen on %s); "
                "such elements are excluded from pickup determination",
                key[0], attribute, obj.loc_name,
            )
        return default


def _is_definite_time(element) -> bool:
    """
    True when the element's characteristic is a definite-time curve.

    An element with no characteristic assigned is treated as *not*
    definite time, preserving the original filter's intent of keeping
    everything that isn't explicitly definite time.
    """
    charac = getattr(element, "pcharac", None)
    if charac is None:
        return False
    return "definite" in charac.loc_name.lower()


def get_all_relays(app: "pft.Application") -> List["pft.ElmRelay"]:
    """
    Retrieve all active, relevant relays from the PowerFactory model.

    Filters relays to include only those that are:
    - Under the network model folder
    - Connected to a calculation-relevant grid
    - Located in a StaCubic (cubicle)
    - Not out of service

    Args:
        app: PowerFactory application instance

    Returns:
        List of ElmRelay objects meeting all filter criteria

    Example:
        >>> relays = get_all_relays(app)
        >>> print(f"Found {len(relays)} active relays")
    """
    net_mod = app.GetProjectFolder("netmod")
    all_relays = net_mod.GetContents("*.ElmRelay", True)

    relays = [
        relay
        for relay in all_relays
        if relay.cpGrid
        if relay.cpGrid.IsCalcRelevant()
        if relay.GetParent().GetClassName() == "StaCubic"
        if not relay.IsOutOfService()
    ]
    return relays


def get_prot_elements(
    device_pf: "pft.ElmRelay"
) -> Dict[str, List[Union["pft.RelToc", "pft.RelIoc"]]]:
    """
    Retrieve all active relay elements from a relay device.

    Extracts time overcurrent (RelToc) and instantaneous overcurrent (RelIoc)
    elements, categorizing them by relay function:
    - oc_*: Phase overcurrent elements
    - ef_*: Earth fault elements
    - nps_*: Negative phase sequence elements

    Args:
        device_pf: PowerFactory ElmRelay object

    Returns:
        Dictionary with keys:
        - 'oc_idmt_elements': Phase overcurrent IDMT elements
        - 'oc_inst_element': Phase overcurrent instantaneous elements
        - 'ef_idmt_elements': Earth fault IDMT elements
        - 'ef_inst_element': Earth fault instantaneous elements
        - 'nps_idmt_elements': Negative sequence IDMT elements
        - 'nps_inst_elements': Negative sequence instantaneous elements

    Example:
        >>> elements = get_prot_elements(relay)
        >>> print(f"Found {len(elements['ef_idmt_elements'])} EF IDMT elements")
    """

    #TODO: Cover no type id case. Return what?

    # Get all IDMT elements that are in service
    idmt_elements = [
        idmt_element
        for idmt_element in device_pf.GetContents("*.RelToc", True)
        if not idmt_element.GetAttribute("e:outserv")
    ]

    # Phase overcurrent IDMT elements (I>t characteristic, not definite time)
    oc_idmt_elements = [
        element
        for element in idmt_elements
        if _safe_attr(element, "r:typ_id:e:sfiec") == "I>t"
        if not _is_definite_time(element)
    ]

    # Phase overcurrent instantaneous elements
    oc_inst_element = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if _safe_attr(element, "r:typ_id:e:sfiec") == "I>>"
        if not element.IsOutOfService()
        if _safe_attr(element, "r:typ_id:e:irecltarget")
    ]

    # Earth fault IDMT elements
    ef_idmt_elements = [
        element
        for element in idmt_elements
        if _safe_attr(element, "r:typ_id:e:sfiec") == "IE>t"
        if not _is_definite_time(element)
    ]

    # Earth fault instantaneous elements
    ef_inst_element = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if _safe_attr(element, "r:typ_id:e:sfiec") == "IE>>"
        if not element.IsOutOfService()
        if _safe_attr(element, "r:typ_id:e:irecltarget")
    ]

    # Negative phase sequence IDMT elements
    nps_idmt_elements = [
        element
        for element in idmt_elements
        if _safe_attr(element, "r:typ_id:e:sfiec") == "I2>t"
    ]

    # Negative phase sequence instantaneous elements
    nps_inst_elements = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if _safe_attr(element, "r:typ_id:e:sfiec") == "I2>>"
        if _safe_attr(element, "r:typ_id:e:irecltarget")
        if not element.IsOutOfService()
    ]

    return {
        'oc_idmt_elements': oc_idmt_elements,
        'oc_inst_element': oc_inst_element,
        'ef_idmt_elements': ef_idmt_elements,
        'ef_inst_element': ef_inst_element,
        'nps_idmt_elements': nps_idmt_elements,
        'nps_inst_elements': nps_inst_elements,
    }


def get_active_elements(
    elements: Dict[str, Union["pft.RelToc", "pft.RelIoc"]],
    fault_type: str
) -> List[Union["pft.RelToc", "pft.RelIoc"]]:
    """
    Filter relay elements to those capable of detecting a specific fault type.

    Different fault types are detected by different element combinations:
    - 3-Phase: Only phase overcurrent elements
    - 2-Phase: Phase overcurrent + negative sequence elements
    - Phase-Ground: All elements (phase, earth, negative sequence)

    Args:
        elements: Dictionary of relay elements from get_prot_elements()
        fault_type: One of '3-Phase', '2-Phase', or 'Phase-Ground'

    Returns:
        List of relay elements capable of detecting the fault type

    Example:
        >>> elements = get_prot_elements(relay)
        >>> ef_elements = get_active_elements(elements, 'Phase-Ground')
    """
    if fault_type == '3-Phase':
        # Only phase elements are active for balanced 3-phase faults
        active_elements = (
            elements['oc_idmt_elements'] +
            elements['oc_inst_element']
        )
    elif fault_type == '2-Phase':
        # Phase and negative sequence elements for 2-phase faults
        active_elements = (
            elements['oc_idmt_elements'] +
            elements['oc_inst_element'] +
            elements['nps_idmt_elements'] +
            elements['nps_inst_elements']
        )
    else:
        # 'Phase-Ground' - all elements can detect earth faults
        active_elements = [
            item
            for sublist in elements.values()
            for item in sublist
        ]

    return active_elements