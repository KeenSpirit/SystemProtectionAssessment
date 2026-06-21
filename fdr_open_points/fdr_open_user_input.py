"""
Feeder selection GUI for open point detection.

This module provides GUI dialogs for feeder selection in the open point
detection workflow. It filters out mesh feeders and presents only radial
feeders for user selection.

Mesh Feeder Detection:
    Uses topological search from feeder cubicle. If external grid is
    found in both upstream and downstream directions, the feeder is
    meshed and excluded from selection.

Functions:
    mesh_feeder_check: Filter feeders to exclude mesh configurations
    get_feeders: Display feeder selection dialog
    populate_feeders: Create feeder checkbox widgets
    window_error: Display error and re-prompt for selection
    exit_script: Clean exit handler
"""

import sys
import tkinter as tk
from tkinter import ttk
from typing import List, Dict
from pf_config import pft


def mesh_feeder_check(app: pft.Application) -> Dict:
    """
    Check for mesh feeders and return only radial feeders.

    Performs topological search from each feeder cubicle to determine
    if the feeder is radial or meshed. Mesh feeders have external
    grids reachable in both upstream and downstream directions.

    Args:
        app: PowerFactory application instance.

    Returns:
        Dictionary mapping feeder objects to feeder names for all
        radial feeders found.

    Side Effects:
        If no radial feeders are found, displays an error dialog and
        the script exits when the user closes it.

    Note:
        Only feeders connected to active (in-service) external grids
        are considered in the mesh detection logic.

    Example:
        >>> radial_dic = mesh_feeder_check(app)
        >>> print(f"Found {len(radial_dic)} radial feeders")
    """
    app.PrintPlain("Checking for mesh feeders...")

    # Get active external grids
    grids_all = app.GetCalcRelevantObjects('*.ElmXnet')
    grids = [
        grid for grid in grids_all
        if grid.GetAttribute('outserv') == 0
    ]

    all_feeders = app.GetCalcRelevantObjects('*.ElmFeeder')

    radial_dic = {}

    for feeder in all_feeders:
        cubicle = feeder.obj_id

        # Topological search: 1=downstream, 0=upstream
        down_devices = cubicle.GetAll(1, 0)
        up_devices = cubicle.GetAll(0, 0)

        grid_downstream = any(item in grids for item in down_devices)
        grid_upstream = any(item in grids for item in up_devices)

        # Mesh feeder: grid found in both directions
        if grid_downstream and grid_upstream:
            continue

        # Radial feeder: grid found in one direction only
        if grid_downstream or grid_upstream:
            radial_dic[feeder] = feeder.GetAttribute('loc_name')

    # Display error if no radial feeders found
    if len(radial_dic) == 0:
        _show_no_radial_feeders_error(app)

    return radial_dic


def _show_no_radial_feeders_error(app: pft.Application) -> None:
    """
    Display error dialog when no radial feeders are found.

    Args:
        app: PowerFactory application instance.
    """
    root = tk.Tk()
    root.geometry("+200+200")
    root.title("11kV fault study")

    ttk.Label(
        root,
        text="No radial feeders were found at the substation"
    ).grid(padx=5, pady=5)

    ttk.Label(
        root,
        text="To run this script, please radialise one or more feeders"
    ).grid(padx=5, pady=5)

    ttk.Button(
        root,
        text='Exit',
        command=lambda: exit_script(root, app)
    ).grid(sticky="s", padx=5, pady=5)

    root.mainloop()


def get_feeders(app: pft.Application, radial_dic: Dict) -> List[str]:
    """
    Display feeder selection dialog and return selected feeders.

    Creates a scrollable dialog with checkboxes for each radial feeder.
    The user must select at least one feeder to proceed.

    Args:
        app: PowerFactory application instance.
        radial_dic: Dictionary of radial feeder objects to names.

    Returns:
        List of selected feeder name strings.

    Side Effects:
        If no feeders are selected, displays an error message and
        re-prompts the user.

    Example:
        >>> feeder_list = get_feeders(app, radial_dic)
        >>> print(f"Selected {len(feeder_list)} feeders")
    """
    radial_list = list(radial_dic.values())
    radial_list.sort()

    root = tk.Tk()

    # Calculate window dimensions
    window_width, window_height, horiz_offset = _calculate_window_dim(
        radial_list
    )
    root.geometry(f"{window_width}x{window_height}+{horiz_offset}+100")
    root.title("11kV Fault Study")

    # Create scrollable canvas
    canvas = tk.Canvas(root, borderwidth=0)
    frame = tk.Frame(canvas)
    vsb = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)

    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.create_window((4, 4), window=frame, anchor="nw")

    frame.bind(
        "<Configure>",
        lambda event, c=canvas: _on_frame_configure(c)
    )

    list_length, var = populate_feeders(app, root, frame, radial_list)

    root.mainloop()

    # Collect selected feeders
    feeder_list = []
    for i in range(list_length):
        if var[i].get() == 1:
            feeder_list.append(radial_list[i])

    # Re-prompt if no feeders selected
    if not feeder_list:
        feeder_list = window_error(app, radial_dic, 3)

    return feeder_list


def _calculate_window_dim(radial_list: List[str]) -> tuple:
    """
    Calculate window dimensions based on feeder list length.

    Args:
        radial_list: List of feeder names.

    Returns:
        Tuple of (width, height, horizontal_offset).
    """
    horiz_offset = 400
    feeder_col = 350
    window_width = feeder_col

    if window_width > 1300:
        horiz_offset = 200
        window_width = 1500

    num_rows = len(radial_list)
    row_height = 32
    row_padding = 100
    window_height = max((num_rows * row_height + row_padding), 480)

    if window_height > 900:
        window_height = 900

    return window_width, window_height, horiz_offset


def _on_frame_configure(canvas: tk.Canvas) -> None:
    """
    Update scroll region when frame is resized.

    Args:
        canvas: Canvas containing the scrollable frame.
    """
    canvas.configure(scrollregion=canvas.bbox("all"))


def populate_feeders(
    app: pft.Application,
    root: tk.Tk,
    frame: tk.Frame,
    radial_list: List[str]
) -> tuple:
    """
    Create feeder checkbox widgets in the frame.

    Args:
        app: PowerFactory application instance.
        root: Root tkinter window.
        frame: Frame to contain checkboxes.
        radial_list: List of feeder names.

    Returns:
        Tuple of (list_length, checkbox_variables).
    """
    ttk.Label(
        frame,
        text="Select all feeders to study:",
        font='Helvetica 14 bold'
    ).grid(columnspan=3, padx=5, pady=5)

    ttk.Label(
        frame,
        text="(mesh feeders excluded)",
        font='Helvetica 12 bold'
    ).grid(columnspan=3, padx=5, pady=5)

    # Create checkbox for each feeder
    list_length = len(radial_list)
    var = []

    for i in range(list_length):
        var.append(tk.IntVar())
        ttk.Checkbutton(
            frame,
            text=radial_list[i],
            variable=var[i]
        ).grid(column=0, sticky="w", padx=30, pady=5)

    # Calculate button row position
    if list_length + 2 > 12:
        row_index = list_length + 2
    else:
        row_index = 12

    # Add buttons
    ttk.Button(
        frame,
        text='Okay',
        command=lambda: root.destroy()
    ).grid(row=row_index, column=0, sticky="w", padx=5, pady=5)

    ttk.Button(
        frame,
        text='Exit',
        command=lambda: exit_script(root, app)
    ).grid(row=row_index, column=1, sticky="w", padx=5, pady=5)

    return list_length, var


def window_error(app: pft.Application, radial_dic: Dict, error_code: int) -> List[str]:
    """
    Display error message and re-prompt for feeder selection.

    Args:
        app: PowerFactory application instance.
        radial_dic: Dictionary of radial feeders.
        error_code: Error type indicator:
            1 = Fault level format error
            2 = Non-numerical value error
            3 = No feeder selected error

    Returns:
        List of selected feeder names from re-prompted dialog.
    """
    if error_code == 1:
        app.PrintPlain("Please enter fault level values in kA, not A")
    elif error_code == 2:
        app.PrintPlain("Please enter numerical values only")
    else:
        app.PrintPlain("Please select at least one feeder to continue")

    feeder_list = get_feeders(app, radial_dic)
    return feeder_list


def exit_script(root: tk.Tk, app: pft.Application) -> None:
    """
    Clean exit handler for GUI dialogs.

    Args:
        root: Tkinter root window to destroy.
        app: PowerFactory application instance.
    """
    app.PrintPlain("User terminated script.")
    root.destroy()
    sys.exit(0)