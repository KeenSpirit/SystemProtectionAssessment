import time
import sys
import logging
import logging.config
from pathlib import Path
import powerfactory as pf
from config_logging import configure_logging as cl
from pf_config import pft
import pf_protection_helper as helper
from typing import Any, Dict, List, Tuple, Optional
import assets as ast
import load_source_z_data
from devices import fuses
from relays import elements
from fdr_open_points import get_open_points as gop
from fault_study import fault_level_study as fs
from cond_damage import conductor_damage as cd
from save_results import save_result as sr

logger = logging.getLogger(__name__)


class AssessmentError(RuntimeError):
    """Raised when the active project cannot be assessed.

    Signals a per-project failure (e.g. missing study case) that the
    batch orchestrator should record and skip, rather than a fault
    that should abort the whole batch run.
    """


def setup_stdout_logging(level: int = logging.INFO) -> None:
    """Ensure assessment progress is visible on the console.

    Adds a stdout StreamHandler to the root logger if one is not already
    present. Idempotent and safe on both entry paths:
      * standalone (__main__): complements the file handler from basicConfig
      * imported (called from batch_relay_update): the mastering process has
        already attached a stdout handler, so this is a no-op.

    Only lowers the root level toward INFO if it is currently more
    restrictive; it never raises the level, so a DEBUG root configured by a
    host process is left untouched.
    """
    root = logging.getLogger()

    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    already = any(
        isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) is sys.stdout
        for h in root.handlers
    )
    if not already:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s: %(module)s: Line: %(lineno)d: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S%z",
            )
        )
        root.addHandler(handler)


def begin(
    app: pft.Application,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:

    setup_stdout_logging()
    logger.info("System Protection Assessment started")

    active_study_case = app.GetActiveStudyCase()
    logger.info(f"study case: {active_study_case}")
    if (active_study_case is None
            or active_study_case.loc_name != "All Active Grids Study Case"):
        # Activate "All Active Grids Study Case"
        study_folder = app.GetProjectFolder("study")
        if study_folder is None:
            raise AssessmentError("Project has no study case folder.")

        all_grids_cases = study_folder.GetContents(
            "All Active Grids Study Case"
        )
        if not all_grids_cases:
            raise AssessmentError(
                "Project has no 'All Active Grids Study Case' study case."
            )
        int_case = all_grids_cases[0]
        error_code = int_case.Activate()
        if error_code:
            raise AssessmentError(
                f"Could not activate 'All Active Grids Study Case' "
                f"(Activate() returned {error_code})."
            )
    logger.info("Activated 'All Active Grids Study Case'")

    # Get region and user inputs
    region = helper.obtain_region(app)
    logger.info(f"Region: {region}")

    radial_list, mesh_feeder = mesh_feeder_check(app)
    logger.info(f"{len(radial_list)} radial feeders detected")
    if not radial_list:
        raise AssessmentError("No radial feeders detected in project.")

    feeders_devices, bu_devices = get_feeders_devices(app, radial_list)
    chk_empty_fdrs(app, feeders_devices)

    grids = [
        grid for grid in app.GetCalcRelevantObjects('*.ElmXnet')
        if grid.outserv == 0
           and grid.GetAttribute('bus1') is not None
    ]

    external_grid = get_grid_data(grids, region)

    # Convert to assets dataclasses
    feeders = cvrt_fdr_to_dataclass(app, feeders_devices, bu_devices)
    logger.info(f"{len(feeders)} feeders to assess")

    # Add "Conductor Damage Assessment" to study_selections for a conductor damage assessment
    study_selections = ["Fault Level Study (all relays configured in model)"]
    # Process feeders
    for i, feeder in enumerate(feeders, start=1):
        name = getattr(feeder.obj, "loc_name", str(feeder.obj))

        logger.info(f"[{i}/{len(feeders)}] {name}: open points")
        gop.get_open_points(app, feeder)

    logger.info("All feeders fault study")
    fs.fault_study(
        app, external_grid, region, feeders
    )

    for i, feeder in enumerate(feeders, start=1):
        name = getattr(feeder.obj, "loc_name", str(feeder.obj))
        if "Conductor Damage Assessment" in study_selections:
            selected_devices = [
                device for device in feeder.devices]
            logger.info(f"[{i}/{len(feeders)}] {name}: conductor damage")
            cd.cond_damage(app, selected_devices)

    logger.info("Saving results")
    output_file = sr.save_dataframe(
        app, region, study_selections, external_grid, feeders,
        output_dir=output_dir,
    )
    logger.info("System Protection Assessment complete")

    return {
        "project": app.GetActiveProject().loc_name,
        "region": region,
        "radial_feeders_detected": len(radial_list),
        "feeders_assessed": len(feeders),
        "output_file": output_file,
    }


def mesh_feeder_check(app) -> Tuple[List[str], bool]:
    """
    Filter feeders to exclude mesh configurations.

    Identifies radial feeders by checking external grid
    connectivity. Mesh feeders (connected to grids at both ends)
    are excluded. Also detects lines out of service which may
    affect feeder topology.

    Returns:
        Tuple containing:
            - radial_list: Sorted list of radial feeder names.
            - mesh_feeder_check: True if any lines are out of service.
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

    mesh_feeder_check = False
    if mesh_list:
        mesh_feeder_check = True

    return sorted(radial_list), mesh_feeder_check


def get_grid_data(grids: List, region: str) -> Dict:
    """
    Collect fault level parameters from external grid elements.

    Retrieves maximum and minimum fault level attributes from each
    grid, including system normal minimum values from the master
    project if available.

    Args:
        grids: List of external grid (ElmXnet) objects.

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


# =============================================================================
# SCRIPT EXECUTION
# =============================================================================

if __name__ == '__main__':
    start = time.time()


    # Configure logging
    logging.basicConfig(
        filename=cl.getpath() / 'prot_assess_log.txt',
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    app = pf.GetApplication()

    with helper.app_manager(app, gui=True) as app:
        begin(app)

    end = time.time()
    run_time = round(end - start, 6)
    app.PrintPlain(f"Script run time: {run_time} seconds")