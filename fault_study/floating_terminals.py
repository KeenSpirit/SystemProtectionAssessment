"""
Floating terminal detection and handling for fault studies.

Floating terminals are network endpoints where a line section has only
one connected terminal within the protection section. These require
special handling in fault studies because the fault must be applied
at a specific location along the line rather than at a busbar.

Functions:
    get_floating_terminals: Find all floating terminals for devices
    find_end_points: Find line sections with only one connection
"""

from typing import List, Dict

from pf_config import pft
import domain as dd


def get_floating_terminals(
    feeder: dd.Feeder,
    devices: List[dd.Device]
) -> Dict:
    """
    Find all floating terminal objects with their associated lines.

    Identifies terminals at the end of feeder sections that are not
    directly connected to protection device terminals. These floating
    terminals require fault current calculations at the line endpoint
    rather than at a busbar.

    Args:
        feeder: Feeder dataclass containing the feeder object.
        devices: List of Device dataclasses with sect_terms populated.

    Returns:
        Nested dictionary structure:
        {device_terminal: {line: floating_terminal, ...}, ...}

        Where:
        - device_terminal: The terminal where the device is located
        - line: The ElmLne object connecting to the floating terminal
        - floating_terminal: The ElmTerm at the end of the line

    Example:
        >>> floating = get_floating_terminals(feeder, devices)
        >>> for dev_term, lines in floating.items():
        ...     for line, float_term in lines.items():
        ...         print(f"{line.loc_name} -> {float_term.loc_name}")
    """
    floating_terms = {}
    floating_lines = find_end_points(feeder)

    for device in devices:
        terms = [term.obj for term in device.sect_terms]
        floating_terms[device.term] = {}

        for line in floating_lines:
            try:
                t1, t2 = line.GetConnectedElements()
            except (ValueError, AttributeError):
                continue

            t3 = line.GetConnectedElements(1, 1, 0)

            # Check if line connects a section terminal to a floating terminal
            if len(t3) == 1 and t3[0] == t2 and t2 in terms and t1 not in terms:
                floating_terms[device.term][line] = t1
            elif len(t3) == 1 and t3[0] == t1 and t1 in terms and t2 not in terms:
                floating_terms[device.term][line] = t2

    return floating_terms


def find_end_points(feeder: pft.ElmFeeder) -> List[pft.ElmLne]:
    """
    Find line sections with only one connection (endpoints).

    Identifies lines at the end of the feeder that have only one
    connected line section. These are potential floating terminal
    locations where special fault calculations are needed.

    Args:
        feeder: The PowerFactory ElmFeeder object to search.

    Returns:
        List of ElmLne objects that are endpoint sections.

    Note:
        A line is considered an endpoint if either terminal has only
        one other line connected, indicating it's at the edge of the
        network topology.
    """
    floating_lines = []

    # Get all the sections that make up the selected feeder
    feeder_lines = feeder.GetObjs('ElmLne')

    for elmlne in feeder_lines:
        # Get lines connected at bus1 terminal
        if (elmlne.GetAttribute('bus1') is not None
                and elmlne.bus1.GetAttribute('cterm') is not None):
            bus1 = [
                x.GetAttribute('obj_id')
                for x in elmlne.bus1.cterm.GetConnectedCubicles()
                if x is not elmlne.GetAttribute('bus1')
                if x.obj_id.GetClassName() == dd.ElementType.LINE.value
            ]
        else:
            bus1 = []

        # Get lines connected at bus2 terminal
        if (elmlne.GetAttribute('bus2') is not None
                and elmlne.bus2.GetAttribute('cterm') is not None):
            bus2 = [
                x.GetAttribute('obj_id')
                for x in elmlne.bus2.cterm.GetConnectedCubicles()
                if x is not elmlne.GetAttribute('bus2')
                if x.obj_id.GetClassName() == dd.ElementType.LINE.value
            ]
        else:
            bus2 = []

        # Check if this is an endpoint line
        is_endpoint = (
            len(bus1) == 1
            or len(bus2) == 1
            or (len(bus1) > 1 and elmlne not in bus1)
            or (len(bus2) > 1 and elmlne not in bus2)
        )

        if is_endpoint:
            floating_lines.append(elmlne)

    return floating_lines
