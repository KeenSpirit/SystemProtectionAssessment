"""
Feeder open point detection for distribution network topology.

This module identifies normally-open switches on distribution feeders.
Open points define the electrical boundaries between feeders and are
essential for determining protection zones and backup coordination.

Open Point Types:
    - StaSwitch: Switchgear devices in cubicles
    - ElmCoup: Coupling elements between feeders

Functions:
    main: Entry point for standalone open point detection
    get_open_points: Detect open points for a single feeder
"""

from typing import Dict, List, TYPE_CHECKING

from pf_config import pft
from domain.enums import ElementType
from domain import feeder as fdr
from fdr_open_points import fdr_open_user_input as foui
from importlib import reload

reload(foui)


def main(app: pft.Application) -> None:
    """
    Entry point for standalone feeder open point detection.

    Displays feeder selection dialog, identifies open points for each
    selected feeder, and prints results to the PowerFactory output
    window.

    Args:
        app: PowerFactory application instance.

    Side Effects:
        - Clears output window
        - Prints open point results to output window
        - Updates Feeder.open_points attribute

    Note:
        Only radial feeders are available for selection. Mesh feeders
        are automatically excluded by the mesh_feeder_check function.

    Example:
        >>> main(app)
        Feeder FDR01 open points:
            SW001
            SW002 / ElmCoup
        NOTE: Open points to adjacent bulk supply substations...
    """
    # Enable user break for long operations
    app.SetEnableUserBreak(1)
    app.ClearOutputWindow()

    # Get radial feeders and user selection
    radial_dic = foui.mesh_feeder_check(app)
    feeder_list = foui.get_feeders(app, radial_dic)

    # Process each selected feeder
    for feeder_name in feeder_list:
        feeder_obj = app.GetCalcRelevantObjects(
            feeder_name + ".ElmFeeder"
        )[0]
        feeder = fdr.initialise_fdr_dataclass(feeder_obj)

        get_open_points(app, feeder)

        # Print results
        app.PrintPlain(f"Feeder {feeder.obj} open points:")

        if feeder.open_points:
            for site, switch in feeder.open_points.items():
                if switch.GetClassName() == ElementType.SWITCH.value:
                    app.PrintPlain(f"\t{switch}")
                else:
                    app.PrintPlain(f"\t{site} / {switch}")
        else:
            app.PrintPlain("\t(None detected)")

    app.PrintPlain(
        "NOTE: Open points to adjacent bulk supply substations may not be "
        "detected unless the relevant grids are active under the current "
        "project"
    )


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

