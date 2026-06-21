import time
import powerfactory as pf
import logging.config
from pf_config import pft
import pf_protection_helper as helper
from typing import Any, Dict, List, Tuple
import domain as dd
import math
from devices import fuses
from relays import elements
from fdr_open_points import get_open_points as gop
from fault_study import fault_level_study as fs
from cond_damage import conductor_damage as cd
from save_results import save_result as sr


def main(app: pft.Application) -> None:


    # Activate "All Active Grids Study Case"
    study_folder = app.GetProjectFolder("study")
    all_grids_cases = study_folder.GetContents(
        "All Active Grids Study Case"
    )
    int_case = all_grids_cases[0]
    int_case.Activate()


    # Get region and user inputs
    region = helper.obtain_region(app)

    radial_list, mesh_feeder = mesh_feeder_check()

    feeders_devices, bu_devices = get_feeders_devices(radial_list)
    chk_empty_fdrs(feeders_devices)

    grids = [
        grid for grid in app.GetCalcRelevantObjects('*.ElmXnet')
        if grid.outserv == 0
           and grid.GetAttribute('bus1') is not None
    ]

    external_grid = get_grid_data(grids)

    # Convert to domain dataclasses
    feeders = cvrt_fdr_to_dataclass(app, feeders_devices, bu_devices)

    study_selections = ["Fault Level Study (all relays configured in model)"]
    # Process each feeder
    for feeder in feeders:
        gop.get_open_points(app, feeder)
        fs.fault_study(
            app, external_grid, region, feeder, study_selections
        )

        selected_devices = [
            device for device in feeder.devices]

        cd.cond_damage(app, selected_devices)
    sr.save_dataframe(
        app, region, study_selections, external_grid, feeders
    )


def mesh_feeder_check(self) -> Tuple[List[str], bool]:
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
    self.app.PrintPlain("Checking for radial feeders...")
    grids = [
            grid for grid in self.app.GetCalcRelevantObjects('*.ElmXnet')
            if grid.outserv == 0
        ]
    all_feeders = [
        fdr for fdr in self.app.GetCalcRelevantObjects('*.ElmFeeder')
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
        self.app.PrintPlain("Radial feeders detected.")
    else:
        self.show_no_radial_feeders_message()

    mesh_feeder_check = False
    if mesh_list:
        mesh_feeder_check = True

    return sorted(radial_list), mesh_feeder_check


def get_grid_data(self, grids: List) -> Dict:
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

    for grid in grids:
        grid_data[grid] = [
            grid.GetAttribute(attr) for attr in attributes
        ]
        self.app.PrintPlain(
            f'Finding System normal source impedance for {grid}...'
        )
        grid_loc_name = grid.GetAttribute('loc_name')
        master_grid = self.get_master_grid(grid_loc_name)

        if master_grid:
            grid_prw = master_grid.GetAttribute('snssmin')
            ikssmin = grid_prw / (11 * math.sqrt(3))
            master_grid_attr = [
                'rntxnmin', 'z2tz1min', 'x0tx1min', 'r0tx0min'
            ]
            master_grid_imp = [
                master_grid.GetAttribute(attr) for attr in master_grid_attr
            ]
            grid_data[grid].append(ikssmin)
            grid_data[grid].extend(master_grid_imp)

        if len(grid_data[grid]) == 10:
            self.app.PrintPlain(
                f'Could not find system normal source impedance '
                f'for {grid}...'
            )
            grid_data[grid].extend([0, 0, 0, 0, 0])

    return grid_data

def get_feeders_devices(
    self, radial_list: List[str]
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
    all_relays = elements.get_all_relays(self.app)
    all_fuses = fuses.get_all_fuses(self.app)
    devices = all_relays + all_fuses

    feeder_device_dict = {feeder: [] for feeder in radial_list}
    grid_device_dict = {
        grid: []
        for grid in self.app.GetCalcRelevantObjects('*.ElmXnet')
        if grid.bus1 is not None
    }

    for device in devices:
        term = device.cbranch
        feeder = [
            feeder for feeder in radial_list
            if term in self.app.GetCalcRelevantObjects(
                feeder + ".ElmFeeder"
            )[0].GetAll()
        ]
        if feeder:
            feeder_device_dict[feeder[0]].append(device)
            continue

        for grid in grid_device_dict:
            try:
                grid_term = grid.bus1.cterm
                grid_term.SetAttribute("iUsage", 0)
                if grid_term == device.cn_bus:
                    grid_device_dict[grid].append(device)
                    break
            except AttributeError:
                self.app.PrintPlain(grid)
                exit(0)

    return feeder_device_dict, grid_device_dict

def chk_empty_fdrs(self, fdrs_devices: Dict) -> None:
    """
    Check that selected feeders have protection devices.

    Validates that at least one feeder has devices and removes
    empty feeders from the selection with warnings.

    Args:
        fdrs_devices: Dict of feeder names to device lists.

    Raises:
        SystemExit: If no feeders have any protection devices.
    """
    empty_feeders = [
        feeder for feeder, devices in fdrs_devices.items()
        if devices == []
    ]

    if len(empty_feeders) == len(fdrs_devices):
        self.app.PrintError(
            "No protection devices were detected in the model for the "
            "selected feeders. \n"
            "Please add and configure the required protection devices "
            "and re-run the script."
        )
        sys.exit(0)

    for empty_feeder in empty_feeders:
        self.app.PrintWarn(
            f"No protection devices were detected in the model for "
            f"feeder {empty_feeder}. \n"
            "This feeder will be excluded from the study."
        )
        del fdrs_devices[empty_feeder]

def cvrt_fdr_to_dataclass(
    app: pft.Application,
    feeders_devices: Dict,
    bu_devices: Dict
) -> List[dd.Feeder]:
    """
    Convert PowerFactory element selections to domain dataclasses.

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
                dd.initialise_dev_dataclass(device)
                for device in grid_devices
            ]

    # Convert feeders and their devices to dataclasses
    feeders = []

    for fdr, devs in feeders_devices.items():
        feeder_obj = app.GetCalcRelevantObjects(fdr + ".ElmFeeder")[0]
        feeder = dd.initialise_fdr_dataclass(feeder_obj)

        devices = [dd.initialise_dev_dataclass(dev) for dev in devs]
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
        main(app)

    end = time.time()
    run_time = round(end - start, 6)
    app.PrintPlain(f"Script run time: {run_time} seconds")