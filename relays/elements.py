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

if TYPE_CHECKING:
    from pf_config import pft


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
        if element.GetAttribute("r:typ_id:e:sfiec") == "I>t"
        if "definite" not in element.pcharac.loc_name.lower()
    ]

    # Phase overcurrent instantaneous elements
    oc_inst_element = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if element.GetAttribute("r:typ_id:e:sfiec") == "I>>"
        if not element.IsOutOfService()
        if element.GetAttribute("r:typ_id:e:irecltarget")
    ]

    # Earth fault IDMT elements
    ef_idmt_elements = [
        element
        for element in idmt_elements
        if element.GetAttribute("r:typ_id:e:sfiec") == "IE>t"
        if "definite" not in element.pcharac.loc_name.lower()
    ]

    # Earth fault instantaneous elements
    ef_inst_element = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if element.GetAttribute("r:typ_id:e:sfiec") == "IE>>"
        if not element.IsOutOfService()
        if element.GetAttribute("r:typ_id:e:irecltarget")
    ]

    # Negative phase sequence IDMT elements
    nps_idmt_elements = [
        element
        for element in idmt_elements
        if element.GetAttribute("r:typ_id:e:sfiec") == "I2>t"
    ]

    # Negative phase sequence instantaneous elements
    nps_inst_elements = [
        element
        for element in device_pf.GetContents("*.RelIoc", True)
        if element.GetAttribute("r:typ_id:e:sfiec") == "I2>>"
        if element.GetAttribute("r:typ_id:e:irecltarget")
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