"""
Fault impedance and construction type handling.

This module determines appropriate fault current values based on:
- Network region (SEQ vs Regional)
- Line construction type (overhead vs underground vs SWER)
- Study type (minimum vs system normal minimum)

The key insight is that different regions use different fault impedance
assumptions for earth faults (SEQ: 0 ohms; Regional: 50 ohms overhead,
10 ohms otherwise). The assumptions themselves are defined in
criteria.earth_fault_resistance; this module applies them.
"""

from typing import List
from pf_config import pft
import assets as ast
import criteria


def update_node_construction(devices: List[ast.Device]) -> None:
    """
    Determine and set the construction type for all terminal nodes.

    Examines connected line elements to determine if each terminal is
    connected to overhead (OH), underground (UG), or SWER construction.

    Args:
        devices: List of Device dataclasses with sect_terms populated

    Side Effects:
        Sets the 'constr' attribute on each Termination in device.sect_terms
    """
    all_nodes = _get_all_terms(devices)
    _update_construction(all_nodes)


def _get_all_terms(devices: List[ast.Device]) -> List[ast.Termination]:
    """
    Extract all terminations from device list.

    Args:
        devices: List of Device dataclasses.

    Returns:
        Flat list of all Termination objects from all devices.
    """
    all_nodes = []
    for device in devices:
        terms = device.sect_terms
        all_nodes.extend(terms)
    return all_nodes


def _update_construction(all_nodes: List[ast.Termination]) -> None:
    """
    Set construction type for each node based on connected lines.

    Examines lines connected to each terminal and sets the construction
    type. Priority order: SWER > UG > OH (default).

    Args:
        all_nodes: List of Termination dataclasses to update.

    Side Effects:
        Sets constr attribute on each Termination.
    """
    for node in all_nodes:
        # Skip if already determined
        if node.constr is not None:
            continue

        # Get all lines connected to the node
        line_elements = [ele for ele in node.obj.GetConnectedElements()
                         if ele.GetClassName() == ast.ElementType.LINE.value
                         ]
        # Handle case where upstream connection is not a line (e.g., ElmCoup)
        if not line_elements:
            try:
                substation = node.cpSubstat
                proxy_node = substation.pBusbar
                line_elements = [
                    ele for ele in proxy_node.GetConnectedElements()
                    if ele.GetClassName() == ast.ElementType.LINE.value
                ]
            except (AttributeError, IndexError):
                line_elements = []

        # Determine construction type from connected lines
        for line in line_elements:
            try:
                line_type = line.typ_id
                if 'SWER' in line_type.loc_name:
                    node.constr = "SWER"
                    break
            except AttributeError:
                pass

            if line.IsCable() and node.constr != "OH":
                node.constr = "UG"
            else:
                node.constr = "OH"

        # Default to overhead if no lines found
        if node.constr is None:
            node.constr = "OH"


# Termination fields holding the minimum earth fault level for each
# fault resistance criteria.earth_fault_resistance can return:
# (minimum field, system normal minimum field).
_PG_FAULT_ATTR = {
    0: ('min_fl_pg', 'min_sn_fl_pg'),
    10: ('min_fl_pg10', 'min_sn_fl_pg10'),
    50: ('min_fl_pg50', 'min_sn_fl_pg50'),
}


def get_terminal_pg_fault(region: str,
                          term: ast.Termination,
                          system_normal: bool = False
                          ) -> float:
    """
    Get the appropriate minimum phase-ground fault current for a terminal.

    The fault resistance assumed at the terminal comes from
    criteria.earth_fault_resistance (region and construction); this
    function selects the Termination field calculated for it.

    Args:
        region: Network region identifier ('SEQ', 'Northern', 'Southern')
        term: Termination dataclass with fault current attributes
        system_normal: If True, returns system normal minimum values.
                       If False (default), returns minimum values.

    Returns:
        Minimum phase-ground fault current in Amperes.
        Returns 0 if the required attribute is None.

    Raises:
        ValueError: If the region is unknown, or criteria assumes a
            fault resistance with no matching Termination field.

    Example:
        >>> # Get minimum fault current for reach factor calculation
        >>> min_fl = get_terminal_pg_fault('SEQ', terminal)
        >>>
        >>> # Get system normal minimum for reporting
        >>> sn_min_fl = get_terminal_pg_fault('SEQ', terminal, True)
    """
    resistance = criteria.earth_fault_resistance(region, term.constr)
    try:
        min_attr, sn_min_attr = _PG_FAULT_ATTR[resistance]
    except KeyError:
        raise ValueError(
            f"No Termination fault level field for a {resistance} ohm "
            f"earth fault; add one alongside min_fl_pg10/min_fl_pg50."
        ) from None

    fault_level = getattr(term, sn_min_attr if system_normal else min_attr)
    return fault_level if fault_level is not None else 0
