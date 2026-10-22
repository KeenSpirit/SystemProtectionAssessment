"""
Discovery of feeders, protection devices and external grids.

Turns the active PowerFactory project into the inputs the assessment
pipeline needs: the radial feeders, the protection devices on each,
the external grid data, and the assets dataclasses built from them.
Nothing here prompts the user; the interactive path performs its own
selection and then calls the conversion function below.

Functions:
    classify_feeders: Radial feeder names and mesh feeder objects.
    mesh_feeder_check: Radial feeder names, excluding mesh feeders.
    get_active_grids: In-service external grids with a connection.
    get_grid_data: External grid fault level data from the source
        z-data workbooks (batch path).
    get_feeders_devices: Protection devices mapped to feeders/grids.
    get_feeders_switches: Feeder heads and switches as stand-in
        devices, for a study with no relays configured.
    chk_empty_fdrs: Drop feeders that have no protection devices.
    cvrt_fdr_to_dataclass: PowerFactory selections to dataclasses.
"""

import logging
from typing import Any, Dict, List, Tuple

from pf_config import pft
import assets as ast
import criteria
from devices import fuses
from relays import elements
from source_z_data import load_source_z_data
from pipeline.errors import AssessmentError

logger = logging.getLogger(__name__)


def classify_feeders(app: pft.Application) -> Tuple[List[str], List]:
    """
    Split the in-service feeders into radial and mesh.

    A feeder is meshed when an in-service external grid is reachable
    both upstream and downstream of its cubicle. Feeders with no
    elements, or out of service, are ignored.

    Args:
        app: PowerFactory application instance.

    Returns:
        Tuple containing:
            - radial_list: Sorted radial feeder names.
            - mesh_list: Mesh feeder (ElmFeeder) objects.
    """
    logger.info("Checking for radial feeders...")
    grids = [
            grid for grid in app.GetCalcRelevantObjects('*.ElmXnet')
            if grid.outserv == 0
        ]
    all_feeders = [
        fdr for fdr in app.GetCalcRelevantObjects('*.ElmFeeder')
                   if fdr.GetAll()
                   and not fdr.IsOutOfService()
    ]

    radial_list = []
    mesh_list = []
    for feeder in all_feeders:
        if (
            set(feeder.obj_id.GetAll(1, 0)) & set(grids)
            and set(feeder.obj_id.GetAll(0, 0)) & set(grids)
        ):
            mesh_list.append(feeder)
        else:
            radial_list.append(feeder.loc_name )

    if radial_list:
        logger.info("Radial feeders detected.")
    else:
        logger.warning(" No radial feeders detected.")

    return sorted(radial_list), mesh_list


def mesh_feeder_check(app) -> Tuple[List[str], bool]:
    """
    Filter feeders to exclude mesh configurations.

    Args:
        app: PowerFactory application instance.

    Returns:
        Tuple containing:
            - radial_list: Sorted list of radial feeder names.
            - mesh_feeder_check: True if any mesh feeder was found.
    """
    radial_list, mesh_list = classify_feeders(app)
    return radial_list, bool(mesh_list)


def get_active_grids(app: pft.Application) -> List:
    """
    Get the in-service external grids that have a bus connection.

    Args:
        app: PowerFactory application instance.

    Returns:
        List of ElmXnet objects that are in service and connected.
    """
    return [
        grid for grid in app.GetCalcRelevantObjects('*.ElmXnet')
        if grid.outserv == 0
           and grid.GetAttribute('bus1') is not None
    ]


def get_grid_data(grids: List, region: str) -> Dict:
    """
    Collect fault level parameters for each external grid.

    Batch path source: the regional source z-data workbooks. Matched
    grids have their model max/min attributes overwritten with the
    workbook values; unmatched grids keep the model's own values,
    with the minimum reused as the system normal minimum. The
    interactive path keeps its own GUI and master-project source and
    does not call this function.

    Args:
        grids: List of external grid (ElmXnet) objects.
        region: Network region ('SEQ', 'Northern', 'Southern'),
            selecting the workbook to read.

    Returns:
        Dict mapping grid objects to lists of 15 fault level
        parameters: [ikss, rntxn, z2tz1, x0tx1, r0tx0] for max,
        min, and system normal minimum conditions.
    """
    grid_data = {}
    attributes = [
        'ikss', 'rntxn', 'z2tz1', 'x0tx1', 'r0tx0',
        'ikssmin', 'rntxnmin', 'z2tz1min', 'x0tx1min', 'r0tx0min'
    ]

    grid_data_import = load_source_z_data.grid_data_import(region)
    matched = 0

    for grid in grids:
        # The workbooks are keyed by grid name, not by object.
        name = grid.loc_name.strip()
        scenario_dic = grid_data_import.get(name)

        if scenario_dic is None:
            logger.warning(
                "No source z-data for external grid '%s'. Model "
                "values kept.", name
            )
            # Read the model's own values: 5 max, 5 min, then the min
            # values again as the system normal minimum.
            current = [grid.GetAttribute(attr) for attr in attributes]
            grid_data[grid] = current + current[-5:]
            continue

        # Element 0 of each list is the scenario name, not a value.
        grid_data[grid] = (
                scenario_dic['max'][-5:]
                + scenario_dic['min'][-5:]
                + scenario_dic['sn_min'][-5:]
        )

        for i, attr in enumerate(attributes):
            grid.SetAttribute(attr, grid_data[grid][i])
        matched += 1

    if grids and not matched:
        logger.error(
            "None of the %s external grids matched the source z-data "
            "for region '%s'. Every grid is running on model defaults "
            "- check grid naming in the workbook.", len(grids), region
        )
    else:
        logger.info(
            "Source z-data applied to %s of %s external grids.",
            matched, len(grids)
        )

    return grid_data


def get_feeders_devices(app, radial_list: List[str]
) -> Tuple[Dict[str, list], Dict[Any, list]]:
    """
    Get active relays and fuses mapped to feeders and grids.

    Retrieves all configured protection devices and maps them to
    their associated feeders or external grid backup positions.

    Args:
        radial_list: List of radial feeder names.

    Returns:
        Tuple containing:
            - feeder_device_dict: Dict mapping feeder names to
              lists of protection device objects.
            - grid_device_dict: Dict mapping grid objects to lists
              of backup device objects.
    """
    all_relays = elements.get_all_relays(app)
    all_fuses = fuses.get_all_fuses(app)
    devices = all_relays + all_fuses

    feeder_device_dict = {feeder: [] for feeder in radial_list}
    grid_device_dict = {
        grid: []
        for grid in app.GetCalcRelevantObjects('*.ElmXnet')
        if grid.bus1 is not None
    }

    # Precompute each feeder's element set once.
    feeder_elements = {}
    for feeder_name in radial_list:
        feeder_objs = app.GetCalcRelevantObjects(feeder_name + ".ElmFeeder")
        if not feeder_objs:
            logger.warning(
                f"Feeder {feeder_name} could not be retrieved; no devices "
                f"will be mapped to it"
            )
            continue
        feeder_elements[feeder_name] = set(feeder_objs[0].GetAll())

    for device in devices:
        term = device.cbranch
        matched_feeder = next(
            (
                name for name, elems in feeder_elements.items()
                if term in elems
            ),
            None,
        )
        if matched_feeder:
            feeder_device_dict[matched_feeder].append(device)
            continue

        grid_terms = {}
        for grid in grid_device_dict:
            try:
                grid_term = grid.bus1.cterm
            except AttributeError:
                grid_term = None
            if grid_term is None:
                logger.warning(
                    f"Grid {getattr(grid, 'loc_name', grid)} has no usable "
                    "bus1.cterm; no backup devices will be mapped to it"
                )
                continue
            grid_term.SetAttribute("iUsage", 0)
            grid_terms[grid] = grid_term

        for grid, grid_term in grid_terms.items():
            if grid_term == device.cn_bus:
                grid_device_dict[grid].append(device)
                break

    return feeder_device_dict, grid_device_dict


# Switch name prefixes that mark a Regional switch as a protection
# device location (reclosers / sectionalisers and EDO fuses).
_REGIONAL_RELAY_SWITCH_PREFIXES = [
    '(ACR/SECT/LBS)', '(CIRCUIT RECL)',
    '(CIRCUIT RECLOSER)', '(OIL CIRCUIT RECL)'
]
_REGIONAL_FUSE_SWITCH_PREFIXES = ['(EDO FUSE)']


def get_feeders_switches(
    app: pft.Application, feeder_list: List[str], region: str
) -> Tuple[Dict[str, list], Dict[Any, list]]:
    """
    Get the closed switches on each feeder, for a no-relay study.

    Used when protection is not modelled: each feeder's head (the
    ElmFeeder) and its closed switches stand in for the protection
    devices. Regional models only count switches named as reclosers,
    sectionalisers or EDO fuses; SEQ models count every closed
    StaSwitch.

    Args:
        app: PowerFactory application instance.
        feeder_list: Feeder names.
        region: Network region ('SEQ', 'Northern', 'Southern').

    Returns:
        Tuple containing:
            - feeders_switches: Feeder name to [ElmFeeder, switches...].
              SEQ switches are sorted by name.
            - bu_devices: Empty dict (no backup devices without
              relays).
    """
    elm_coups = [
        switch for switch in app.GetCalcRelevantObjects("*.ElmCoup")
        if switch.on_off == 1
    ]
    sta_switches = [
        switch for switch
        in app.GetCalcRelevantObjects("*.StaSwitch")
        if switch.on_off == 1
    ]

    if criteria.is_regional(region):
        relay_switches = [
            switch for switch in elm_coups + sta_switches
            if any(
                substring in switch.loc_name
                for substring in _REGIONAL_RELAY_SWITCH_PREFIXES
            )
        ]
        line_fuse_switches = [
            switch for switch in sta_switches
            if any(
                substring in switch.loc_name
                for substring in _REGIONAL_FUSE_SWITCH_PREFIXES
            )
        ]
        all_switches = relay_switches + line_fuse_switches
    else:
        all_switches = sta_switches

    feeders_switches = {}
    for feeder in feeder_list:
        feeder_elm = app.GetCalcRelevantObjects(feeder + ".ElmFeeder")[0]
        feeder_terms = feeder_elm.GetObjs('ElmTerm')
        feeder_coups = feeder_elm.GetObjs('ElmCoup')
        switch_list = [
            switch for switch in all_switches
            if (
                switch.GetClassName() == 'StaSwitch'
                and switch.fold_id.cterm in feeder_terms
            ) or (
                switch.GetClassName() == 'ElmCoup'
                and switch in feeder_coups
            )
        ]

        if criteria.is_seq(region):
            switch_list.sort(key=lambda switch: switch.loc_name)

        feeders_switches[feeder] = [feeder_elm] + switch_list

    return feeders_switches, {}


def chk_empty_fdrs(app, fdrs_devices: Dict) -> None:
    """
    Check that selected feeders have protection devices.

    Removes feeders with no protection devices from the selection,
    logging a warning for each. If NO feeder has any devices, raises
    AssessmentError so the caller skips the project cleanly instead
    of producing an empty results workbook.

    Args:
        app: PowerFactory application instance. Retained for call-site
            compatibility; no longer used directly.
        fdrs_devices: Dict mapping feeder names to device lists.
            Mutated in place: empty feeders are removed.

    Raises:
        AssessmentError: If no feeder has any protection devices.
    """
    empty_feeders = [
        feeder for feeder, devices in fdrs_devices.items()
        if devices == []
    ]

    if len(empty_feeders) == len(fdrs_devices):
        raise AssessmentError(
            "No protection devices detected in the model for any "
            "selected feeder."
        )

    for empty_feeder in empty_feeders:
        logger.warning(
            f"No protection devices detected for feeder {empty_feeder}; "
            f"it will be excluded from the study."
        )
        del fdrs_devices[empty_feeder]


def cvrt_fdr_to_dataclass(
    app: pft.Application,
    feeders_devices: Dict,
    bu_devices: Dict
) -> List[ast.Feeder]:
    """
    Convert PowerFactory element selections to assets dataclasses.

    Transforms the dictionaries of PowerFactory objects returned by
    user input collection into structured Feeder and Device dataclasses
    for use in the analysis workflow.

    Args:
        app: PowerFactory application instance.
        feeders_devices: Dictionary mapping feeder names to lists of
            protection device PowerFactory objects.
        bu_devices: Dictionary mapping external grid objects to lists
            of backup device PowerFactory objects.

    Returns:
        List of Feeder dataclasses with devices and bu_devices populated.

    Example:
        >>> feeders = cvrt_fdr_to_dataclass(app, fdr_devs, bu_devs)
        >>> for feeder in feeders:
        ...     print(f"{feeder.obj.loc_name}: {len(feeder.devices)} devices")
    """
    # Convert backup devices to dataclasses
    if bu_devices:
        for grid, grid_devices in bu_devices.items():
            bu_devices[grid] = [
                ast.initialise_dev_dataclass(device)
                for device in grid_devices
            ]

    # Convert feeders and their devices to dataclasses
    feeders = []

    for fdr, devs in feeders_devices.items():
        feeder_obj = app.GetCalcRelevantObjects(fdr + ".ElmFeeder")[0]
        feeder = ast.initialise_fdr_dataclass(feeder_obj)

        devices = [ast.initialise_dev_dataclass(dev) for dev in devs]
        feeder.devices = devices
        # Give each feeder its own copy so a future per-feeder mutation
        # of bu_devices cannot bleed across feeders.
        feeder.bu_devices = dict(bu_devices)

        feeders.append(feeder)

    return feeders