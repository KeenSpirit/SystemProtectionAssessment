"""
Feeder open point detection for distribution network topology.

This module identifies normally-open switches on distribution feeders.
Open points define the electrical boundaries between feeders and are
essential for determining protection zones and backup coordination.

Open Point Types:
    - StaSwitch: Switchgear devices in cubicles
    - ElmCoup: Coupling elements between feeders

The project's open switches are the same for every feeder, so they are
listed once (find_open_switches) and each feeder is then tested against
that short list (get_open_points). Listing them inside get_open_points,
as this module used to, re-read every StaSwitch and ElmCoup in the
project for every feeder.

Functions:
    find_open_switches: List the project's open switches once
    get_open_points: Detect open points for a single feeder
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from pf_config import pft
from assets import feeder as fdr

logger = logging.getLogger(__name__)

# Most switch names listed in the "not in a cubicle" warning; the count is
# always given in full.
_MAX_NAMES_IN_WARNING = 10


@dataclass
class OpenSwitches:
    """
    The open switches of a project, read once and reused per feeder.

    Attributes:
        sta_switches: (switch, terminal) for each open StaSwitch whose
            cubicle has a terminal, in project order.
        couplers: (cubicle, switch, connected terminals) for each open
            ElmCoup, in project order.
    """

    sta_switches: List[Tuple[Any, Any]] = field(default_factory=list)
    couplers: List[Tuple[Any, Any, frozenset]] = field(default_factory=list)


def find_open_switches(app: pft.Application) -> OpenSwitches:
    """
    List every open StaSwitch and ElmCoup in the active project.

    Call once per project and pass the result to get_open_points for
    each feeder.

    An open StaSwitch with no terminal cannot be on any feeder and is
    left out. Where the cubicle is simply not connected (cterm empty)
    that is silent, as it always was. Where the switch's parent has no
    cterm attribute at all (it is not a cubicle) the switches are named
    in one warning; reading cterm there used to raise AttributeError
    and stop the whole project's assessment (Cairns, issues log bug #4).

    Args:
        app: PowerFactory application instance.

    Returns:
        OpenSwitches for the project.
    """
    clock = time.perf_counter()
    netdat = app.GetProjectFolder("netdat")
    all_staswitch = netdat.GetContents("*.StaSwitch", 1)
    all_elmcoup = netdat.GetContents("*.ElmCoup", 1)

    found = OpenSwitches()
    not_in_cubicle = []

    for switch in all_staswitch:
        # Open/closed first: most switches are closed, and a closed
        # switch needs no further reads.
        if switch.GetAttribute("on_off") != 0:
            continue
        parent = switch.GetAttribute("fold_id")
        if parent is None or not parent.HasAttribute("cterm"):
            not_in_cubicle.append(switch)
            continue
        terminal = parent.GetAttribute("cterm")
        if terminal is None:
            # Cubicle not connected to a terminal: on no feeder.
            continue
        found.sta_switches.append((switch, terminal))

    for switch in all_elmcoup:
        if switch.GetAttribute("on_off") != 0:
            continue
        found.couplers.append((
            switch.GetAttribute("fold_id"),
            switch,
            frozenset(switch.GetConnectedElements()),
        ))

    if not_in_cubicle:
        names = ", ".join(
            _describe(switch)
            for switch in not_in_cubicle[:_MAX_NAMES_IN_WARNING]
        )
        more = len(not_in_cubicle) - _MAX_NAMES_IN_WARNING
        logger.warning(
            f"Open points: {len(not_in_cubicle)} open StaSwitch(es) are not "
            f"in a cubicle (parent has no cterm) and were left out: "
            f"{names}{f' and {more} more' if more > 0 else ''}"
        )
    logger.info(
        f"Open points: {len(found.sta_switches)} of {len(all_staswitch)} "
        f"StaSwitch and {len(found.couplers)} of {len(all_elmcoup)} ElmCoup "
        f"are open; listed in {time.perf_counter() - clock:.1f} s"
    )
    return found


def _describe(switch: Any) -> str:
    """Switch name with its parent's name and class, for the warning."""
    try:
        parent = switch.GetAttribute("fold_id")
        if parent is None:
            return f"{switch.loc_name} (no parent)"
        return (
            f"{switch.loc_name} (in {parent.loc_name}."
            f"{parent.GetClassName()})"
        )
    except Exception:  # noqa: BLE001 - a description must never raise
        return str(switch)


def get_open_points(
    app: pft.Application,
    feeder: fdr.Feeder,
    open_switches: Optional[OpenSwitches] = None,
) -> None:
    """
    Detect normally-open switches on a feeder.

    A switch is an open point of the feeder when it is in the off (open)
    position and connected to a terminal within the feeder's line
    network.

    Args:
        app: PowerFactory application instance.
        feeder: Feeder dataclass to populate with open points.
        open_switches: The project's open switches from
            find_open_switches. None lists them now, which suits a
            single feeder; pass the list when looping over feeders.

    Side Effects:
        Populates feeder.open_points with a dictionary mapping
        switch identifiers to switch objects.

    Open Point Dictionary Format:
        - StaSwitch: {switch: switch}
        - ElmCoup: {cubicle: elmcoup}

    Example:
        >>> open_switches = find_open_switches(app)
        >>> get_open_points(app, feeder, open_switches)
        >>> for site, switch in feeder.open_points.items():
        ...     print(f"Open point: {switch.loc_name}")
    """
    if open_switches is None:
        open_switches = find_open_switches(app)

    # Terminals connected to the feeder's lines
    feeder_terminals = set()
    for line in feeder.obj.GetObjs('ElmLne'):
        feeder_terminals.update(line.GetConnectedElements())

    open_points = {}

    for switch, terminal in open_switches.sta_switches:
        if terminal in feeder_terminals:
            open_points[switch] = switch

    for cubicle, switch, terminals in open_switches.couplers:
        if not feeder_terminals.isdisjoint(terminals):
            open_points[cubicle] = switch

    feeder.open_points = open_points
