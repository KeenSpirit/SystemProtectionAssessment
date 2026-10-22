"""
Time-overcurrent coordination plot generation for PowerFactory.

This module creates time-overcurrent (TOC) characteristic plots in the
PowerFactory graphics board. Plots show relay curves, fault level markers,
and downstream transformer fuse curves for protection coordination review.

Plot Features:
    - Primary device curves with unique colours
    - Upstream and downstream device curves
    - Minimum and maximum fault level markers
    - Downstream transformer fuse curves (if applicable)
    - Separate plots for phase and earth faults

The fault study records 0 for a device-level fault level it could not
calculate, and every device carries a max_ds_tr (empty when it has no
downstream transformer). Plots and markers are skipped, with a message,
where those values are missing rather than failing on a log axis.

Functions:
    plot_all_relays: Main entry point for plot generation
    plot_bounds: The fault level range a plot would span
    update_ds_tr_data: Collect RMU fuse specifications for SEQ
    create_colour_dic: Assign unique colours to devices
    create_plot: Generate a single coordination plot
    create_plot_folder: Create timestamped folder for plots
"""

import time
from typing import Dict, List, Optional, Tuple

from pf_config import pft
import assets as ast
import criteria
from devices import fuses as ds
from pf_protection_helper import create_obj
from interactive.oc_plots import get_rmu_fuses as grf
from interactive.oc_plots import plot_settings


def plot_all_relays(
    app: pft.Application,
    region: str,
    feeder: ast.Feeder,
    selected_devices: List[ast.Device]
) -> None:
    """
    Generate time-overcurrent coordination plots for selected devices.

    Creates separate phase and earth fault plots for each terminal
    location with relay devices. Plots are saved to a timestamped
    folder in the PowerFactory graphics board.

    Args:
        app: PowerFactory application instance.
        region: Network region ('SEQ', 'Northern', 'Southern').
        feeder: Feeder dataclass containing device topology.
        selected_devices: List of Device dataclasses to plot.

    Raises:
        UserCancelled: If the user exits the RMU fuse dialog.

    Side Effects:
        - Creates plot folder in the project
        - Generates plot pages for each device group
        - For SEQ, prompts for RMU fuse specifications
        - Deactivates the study case while plotting; it is always
          re-activated, including after an error

    Note:
        Devices sharing a parent terminal are plotted together.
        Phase plots are only created if minimum 2-phase fault > 0.
    """
    app.PrintPlain(
        f"Generating device time overcurrent plots for "
        f"{feeder.obj.loc_name}..."
    )

    # Collect RMU fuse data before touching the study case, so cancelling
    # the dialog leaves nothing to undo.
    update_ds_tr_data(app, region, selected_devices)

    study_case = app.GetActiveStudyCase()
    app.GetFromStudyCase("Graphics Board.SetDesktop")
    project = app.GetActiveProject()

    study_case.Deactivate()
    try:
        plot_folder = create_plot_folder(feeder, project)

        # Create colour mapping for all devices
        colour_dic = create_colour_dic(feeder.devices)

        # Group devices by parent terminal (sorted, so plots are always
        # created in the same order)
        terminals = sorted(
            set(device.term.loc_name for device in selected_devices)
        )
        device_dic = {
            terminal: [
                device for device in selected_devices
                if device.term.loc_name == terminal
            ]
            for terminal in terminals
        }

        # Generate plots for each terminal group
        for devices in device_dic.values():
            has_relays = any(
                device.obj.GetClassName() == ast.ElementType.RELAY.value
                for device in devices
            )

            if has_relays:

                # Earth fault plot
                create_plot(
                    app, plot_folder, colour_dic, devices,
                    feeder.sys_volts, f_type='Ground'
                )

                # Phase fault plot (if applicable)
                if any((device.min_fl_2ph or 0) > 0 for device in devices):
                    create_plot(
                        app, plot_folder, colour_dic, devices,
                        feeder.sys_volts, f_type='Phase'
                    )

            else:
                app.PrintPlain(
                    f'No relays in feeder {feeder.obj.loc_name} to plot.'
                )
    finally:
        study_case.Activate()

    directory = plot_folder.GetFullName()
    app.PrintPlain(
        f"Time overcurrent plots saved in PowerFactory to:\n{directory}"
    )


def plot_bounds(
    device: ast.Device, f_type: str
) -> Optional[Tuple[float, float]]:
    """
    The fault level range a plot for this device would span.

    Args:
        device: The first device on the plot, which sets the axis.
        f_type: 'Ground' or 'Phase'.

    Returns:
        (minimum, maximum) fault level in Amperes, or None if either is
        missing or not positive.
    """
    if f_type == 'Ground':
        low, high = device.min_fl_pg, device.max_fl_pg
    else:
        low = device.min_fl_2ph
        high = max(device.max_fl_2ph or 0, device.max_fl_3ph or 0)
    if not low or not high or low <= 0 or high <= 0:
        return None
    return low, high


# =============================================================================
# TRANSFORMER DATA COLLECTION
# =============================================================================

def _tr_name(max_ds_tr: ast.Tfmr) -> str:
    """
    Name a downstream transformer by its substation, or its terminal.

    Args:
        max_ds_tr: Transformer dataclass with term populated.

    Returns:
        The substation name, or the terminal name if the terminal is
        not in a substation.
    """
    substation = getattr(max_ds_tr.term, "cpSubstat", None)
    if substation is not None:
        return substation.loc_name
    return max_ds_tr.term.loc_name


def update_ds_tr_data(
    app: pft.Application,
    region: str,
    device_list: List[ast.Device]
) -> None:
    """
    Collect RMU fuse specifications for SEQ downstream transformers.

    For SEQ region, prompts user via GUI to enter insulation type
    (air/oil) and impedance class (high/low) for RMU-connected
    transformers ≥750kVA.

    Args:
        app: PowerFactory application instance.
        region: Network region ('SEQ', 'Northern', 'Southern').
        device_list: List of Device dataclasses to process.

    Raises:
        UserCancelled: If the user exits the RMU fuse dialog.

    Side Effects:
        Updates max_ds_tr.insulation and max_ds_tr.impedance
        attributes on Device objects.

    Note:
        Only applies to SEQ region. Regional models use standard
        fuse selection without user input.
    """
    if not criteria.is_seq(region):
        return

    tr_strings_dic = {}

    for device in device_list:
        if device.obj.GetClassName() != ast.ElementType.RELAY.value:
            continue

        max_ds_tr = device.max_ds_tr
        if max_ds_tr is None or max_ds_tr.term is None:
            continue

        tr_name = _tr_name(max_ds_tr)

        # Skip substations (SP prefix)
        if tr_name[:2] == "SP":
            continue

        # Encode transformer size for GUI lookup
        if max_ds_tr.load_kva in [1000, 1500]:
            max_tr_string = tr_name + "_1"
        else:
            max_tr_string = tr_name + "_0"

        tr_strings_dic[device.obj] = max_tr_string

    if not tr_strings_dic:
        return

    # Prompt user for specifications
    max_tr_strings = list(dict.fromkeys(tr_strings_dic.values()))
    app.PrintPlain("Please enter distribution transformer fuse specification")
    results = grf.get_transformer_specifications(max_tr_strings)

    # Apply results to device objects
    for device_object, string in tr_strings_dic.items():
        device = [d for d in device_list if d.obj == device_object][0]
        string_result = results[string]
        max_ds_tr = device.max_ds_tr
        max_ds_tr.insulation = string_result['insulation']
        max_ds_tr.impedance = string_result['impedance']


# =============================================================================
# COLOUR ASSIGNMENT
# =============================================================================

def create_colour_dic(devices: List[ast.Device]) -> Dict:
    """
    Assign unique colours to each device for plot curves.

    Ensures all devices (primary, upstream, downstream) have distinct
    colours when plotted together.

    Args:
        devices: List of all Device dataclasses in feeder.

    Returns:
        Dictionary mapping device objects to colour indices.

    Note:
        Colour index 0 is white, so assignments start at 1.
    """
    all_devices = []

    for device in devices:
        all_devices.append(device)
        all_devices.extend(device.us_devices)
        all_devices.extend(device.ds_devices)

    # Remove duplicates while preserving order
    unique_devices = []
    for device in all_devices:
        if device not in unique_devices:
            unique_devices.append(device)

    # Colour 0 is white, 1 is black, start with 2 (Red)
    colour_dic = {device.obj: i + 2 for i, device in enumerate(unique_devices)}

    return colour_dic


def create_plot(
    app: pft.Application,
    plot_folder: pft.IntFolder,
    colour_dic: Dict,
    devices: List[ast.Device],
    sys_volts: float,
    f_type: str
) -> None:
    """
    Create a single time-overcurrent coordination plot.

    Generates a plot page containing:
    - Primary device characteristic curves
    - Upstream and downstream device curves
    - Minimum and maximum fault level markers
    - Downstream transformer fuse curve (if applicable)

    Skipped, with a message, if the first device has no positive
    minimum and maximum fault level for the fault type.

    Args:
        app: PowerFactory application instance.
        plot_folder: Target folder for plot.
        colour_dic: Device to colour index mapping.
        devices: List of devices at same terminal to plot.
        sys_volts: System voltage for fuse selection.
        f_type: 'Ground' or 'Phase' fault type.
    """
    date_string = time.strftime("%Y%m%d")

    # Build device name string
    devices_name = ""
    for device in devices:
        devices_name = devices_name + "_" + device.obj.loc_name

    if plot_bounds(devices[0], f_type) is None:
        app.PrintWarn(
            f"{devices_name} {f_type} coordination plot skipped: no "
            f"{f_type.lower()} fault level result for "
            f"{devices[0].obj.loc_name}."
        )
        return

    # Create plot page
    folder_name = f"{devices_name} {f_type} Coord Plot {date_string}"
    grppage = create_obj(plot_folder, folder_name, "GrpPage")
    drawing_format = create_obj(grppage, "Drawing Format", "SetGrfpage")
    plot_settings.setup_drawing_format(drawing_format)

    # Configure plot settings
    pltlinebarplot = grppage.GetOrInsertPlot(folder_name, 10, 1)
    plot_settings.axis_settings(pltlinebarplot, f_type, devices)
    pltlinebarplot.GetLegend()
    plot_name = f"{devices_name} {f_type} Coord Plot {date_string}"
    plot_settings.title_settings(pltlinebarplot, plot_name)
    pltovercurrent = pltlinebarplot.GetDataSource()
    plot_settings.setup_toc_plot(pltovercurrent, f_type)

    # Add fault level markers
    _add_fault_markers(pltlinebarplot, devices, devices_name, f_type)

    # Add downstream transformer fuse
    ds_fuse = _add_transformer_fuse(
        app, pltlinebarplot, devices, sys_volts, f_type
    )

    # Add all device curves to plot
    us_devices = [d.obj for d in devices[0].us_devices]
    ds_devices = [d.obj for d in devices[0].ds_devices]
    all_devices = [d.obj for d in devices] + us_devices + ds_devices + ds_fuse

    for device in all_devices:
        if device.GetClassName() == 'ElmRelay':
            colour = colour_dic[device]
        else:
            colour = 10  # Fuse colour

        # Relays are added to the pltovercurrent object
        pltovercurrent.AddCurve(device, colour, 1, 80.0)


def create_plot_folder(
    feeder: ast.Feeder,
    parent: pft.DataObject
) -> pft.IntFolder:
    """
    Create timestamped folder for coordination plots.

    Args:
        feeder: Feeder dataclass for naming.
        parent: Object to create the folder in (the active project).

    Returns:
        IntFolder object for storing plots.
    """
    date_string = time.strftime("%Y%m%d-%H%M%S")
    title = f'{date_string} {feeder.obj.loc_name} Prot Coord Plots'

    return parent.CreateObject("IntFolder", title)


def _add_marker(plot, object_name: str, label: str, value) -> None:
    """
    Add a fault level marker line, if there is a value to mark.

    Args:
        plot: Target plot.
        object_name: Name for the VisXvalue object.
        label: Marker label text.
        value: Fault current in Amperes; no marker if missing or 0.
    """
    if not value or value <= 0:
        return
    marker = plot.CreateObject("VisXvalue", object_name)
    plot_settings.xvalue_settings(marker, label, value)


def _add_fault_markers(
    plot,
    devices: List[ast.Device],
    devices_name: str,
    f_type: str
) -> None:
    """
    Add minimum and maximum fault level marker lines to plot.

    Devices with no result (0) are left out of the minimum and maximum.

    Args:
        plot: Target plot.
        devices: List of devices for fault level extraction.
        devices_name: Combined device name string.
        f_type: 'Ground' or 'Phase' fault type.
    """
    if f_type == 'Ground':
        mins = [d.min_fl_pg for d in devices if d.min_fl_pg]
        maxes = [d.max_fl_pg for d in devices if d.max_fl_pg]
        labels = ('PG Min FL', 'PG Max FL')
    else:
        mins = [d.min_fl_2ph for d in devices if d.min_fl_2ph]
        maxes = [
            max(d.max_fl_3ph or 0, d.max_fl_2ph or 0) for d in devices
        ]
        labels = ("Ph Min FL", "Ph Max FL")

    _add_marker(
        plot, f'{devices_name} min fl', labels[0], min(mins, default=None)
    )
    _add_marker(
        plot, f'{devices_name} max fl', labels[1], max(maxes, default=None)
    )


def _add_transformer_fuse(
    app: pft.Application,
    plot,
    devices: List[ast.Device],
    sys_volts: float,
    f_type: str
) -> List:
    """
    Add downstream transformer fuse curve and marker to plot.

    Args:
        app: PowerFactory application instance.
        plot: Target plot.
        devices: List of devices to find max downstream transformer.
        sys_volts: System voltage for fuse selection.
        f_type: 'Ground' or 'Phase' fault type.

    Returns:
        List containing the fuse element, or empty list if none.
    """
    # Find device with a downstream transformer
    device = next(
        (
            d for d in devices
            if d.max_ds_tr is not None and d.max_ds_tr.term is not None
        ),
        None
    )

    if device is None:
        return []

    max_ds_tr = device.max_ds_tr

    # Add transformer fault level marker
    tr_name = _tr_name(max_ds_tr)
    if f_type == 'Ground':
        _add_marker(
            plot, f'{tr_name} max fl', 'DS TR PG Max FL', max_ds_tr.max_pg
        )
    else:
        _add_marker(
            plot, f'{tr_name} max fl', "DS TR Ph Max FL", max_ds_tr.max_ph
        )

    # Create/get fuse element
    tr_term_dataclass = next(
        (t for t in device.sect_terms if t.obj == max_ds_tr.term),
        None
    )

    if tr_term_dataclass is None:
        return []

    ds_fuse = ds.create_fuse(app, max_ds_tr, tr_term_dataclass, sys_volts)

    if not ds_fuse:
        app.PrintPlain(
            f'Could not find fuse element for {tr_name} in PowerFactory'
        )
        return []

    return ds_fuse
