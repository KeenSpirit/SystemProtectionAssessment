"""
Feeder open point detection for distribution network topology.

This module identifies normally-open switches on distribution feeders.
Open points define the electrical boundaries between feeders and are
essential for determining protection zones and backup coordination.

Open Point Types:
    - StaSwitch: Switchgear devices in cubicles
    - ElmCoup: Coupling elements between feeders

Functions:
    get_open_points: Detect open points for a single feeder
"""

from pf_config import pft
from assets import feeder as fdr


def get_open_points(app: pft.Application, feeder: fdr.Feeder) -> None:
    """
    Detect normally-open switches on a feeder.

    Searches all StaSwitch and ElmCoup objects in the network data
    folder and identifies those that are:
    - In the off (open) position
    - Connected to a terminal within the feeder's line network

    Args:
        app: PowerFactory application instance.
        feeder: Feeder dataclass to populate with open points.

    Side Effects:
        Populates feeder.open_points with a dictionary mapping
        switch identifiers to switch objects.

    Open Point Dictionary Format:
        - StaSwitch: {switch: switch}
        - ElmCoup: {cubicle: elmcoup}

    Example:
        >>> get_open_points(app, feeder)
        >>> for site, switch in feeder.open_points.items():
        ...     print(f"Open point: {switch.loc_name}")
    """
    netdat = app.GetProjectFolder("netdat")
    all_staswitch = netdat.GetContents("*.StaSwitch", 1)
    all_elmcoup = netdat.GetContents("*.ElmCoup", 1)

    # Build list of terminals connected to feeder lines
    line_list = feeder.obj.GetObjs('ElmLne')
    terminal_list = []

    for line in line_list:
        terminal_list.extend(line.GetConnectedElements())

    terminal_list = list(set(terminal_list))

    # Find open StaSwitch objects
    open_switches = {}

    for switch in all_staswitch:
        cubicle = switch.GetAttribute("fold_id")
        switch_terminal = cubicle.GetAttribute("cterm")

        is_open = switch.GetAttribute("on_off") == 0
        is_on_feeder = switch_terminal in terminal_list

        if is_open and is_on_feeder:
            open_switches[switch] = switch

    # Find open ElmCoup objects
    for switch in all_elmcoup:
        terminals = switch.GetConnectedElements()

        is_open = switch.GetAttribute("on_off") == 0
        is_on_feeder = any(term in terminals for term in terminal_list)

        if is_open and is_on_feeder:
            cubicle = switch.GetAttribute("fold_id")
            open_switches[cubicle] = switch

    feeder.open_points = open_switches

