"""
Feeder open point viewer.

Lets the user pick radial feeders and prints the normally-open points
on each to the PowerFactory output window. Radial feeder discovery
and open point detection come from the core (pipeline.discovery and
fdr_open_points), so this module only adds the feeder dialog and the
report.

Functions:
    run: Entry point - select feeders, detect and report open points.
    get_feeders: Feeder selection dialog.
    populate_feeders: Feeder checkbox widgets.
"""

import tkinter as tk
from tkinter import ttk
from typing import List, Tuple

from pf_config import pft
from assets.enums import ElementType
from assets import feeder as fdr
from fdr_open_points.get_open_points import get_open_points
from pipeline.discovery import mesh_feeder_check
from interactive.dialogs import CancelFlag, UserCancelled


def run(app: pft.Application) -> None:
    """
    Show the normally-open points on user-selected radial feeders.

    Args:
        app: PowerFactory application instance.

    Side Effects:
        Prints open point results to the PowerFactory output window.

    Example:
        >>> run(app)
        Feeder FDR01 open points:
            SW001
            SW002 / ElmCoup
        NOTE: Open points to adjacent bulk supply substations...
    """
    radial_list, _ = mesh_feeder_check(app)
    if not radial_list:
        _show_no_radial_feeders(app)
        return

    try:
        feeder_list = get_feeders(app, radial_list)
    except UserCancelled:
        app.PrintPlain("User terminated script.")
        return

    for feeder_name in feeder_list:
        feeder_obj = app.GetCalcRelevantObjects(
            feeder_name + ".ElmFeeder"
        )[0]
        feeder = fdr.initialise_fdr_dataclass(feeder_obj)

        get_open_points(app, feeder)

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


def _show_no_radial_feeders(app: pft.Application) -> None:
    """
    Tell the user no radial feeders were found.

    Args:
        app: PowerFactory application instance.
    """
    app.PrintWarn("No radial feeders were found at the substation.")

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
        command=lambda: root.destroy()
    ).grid(sticky="s", padx=5, pady=5)

    root.mainloop()


def get_feeders(app: pft.Application, radial_list: List[str]) -> List[str]:
    """
    Display the feeder selection dialog and return the selection.

    Re-prompts until at least one feeder is selected.

    Args:
        app: PowerFactory application instance.
        radial_list: Sorted radial feeder names.

    Returns:
        Selected feeder names.

    Raises:
        UserCancelled: If the user presses Exit.
    """
    root = tk.Tk()
    flag = CancelFlag()

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

    list_length, var = populate_feeders(root, frame, radial_list, flag)

    root.mainloop()
    flag.raise_if_cancelled()

    feeder_list = [
        radial_list[i] for i in range(list_length) if var[i].get() == 1
    ]

    if not feeder_list:
        app.PrintPlain("Please select at least one feeder to continue")
        feeder_list = get_feeders(app, radial_list)

    return feeder_list


def _calculate_window_dim(radial_list: List[str]) -> Tuple[int, int, int]:
    """
    Calculate window dimensions from the feeder list length.

    Args:
        radial_list: Feeder names.

    Returns:
        Tuple of (width, height, horizontal offset).
    """
    horiz_offset = 400
    window_width = 350

    num_rows = len(radial_list)
    row_height = 32
    row_padding = 100
    window_height = max((num_rows * row_height + row_padding), 480)

    if window_height > 900:
        window_height = 900

    return window_width, window_height, horiz_offset


def _on_frame_configure(canvas: tk.Canvas) -> None:
    """
    Update the scroll region when the frame is resized.

    Args:
        canvas: Canvas containing the scrollable frame.
    """
    canvas.configure(scrollregion=canvas.bbox("all"))


def populate_feeders(
    root: tk.Tk,
    frame: tk.Frame,
    radial_list: List[str],
    flag: CancelFlag
) -> Tuple[int, List[tk.IntVar]]:
    """
    Create the feeder checkboxes and buttons.

    Args:
        root: Root tkinter window.
        frame: Frame to contain the checkboxes.
        radial_list: Feeder names.
        flag: Cancel flag the Exit button sets.

    Returns:
        Tuple of (number of feeders, checkbox variables).
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

    list_length = len(radial_list)
    var = []

    for i in range(list_length):
        var.append(tk.IntVar())
        ttk.Checkbutton(
            frame,
            text=radial_list[i],
            variable=var[i]
        ).grid(column=0, sticky="w", padx=30, pady=5)

    # Keep the buttons below a short list
    row_index = max(list_length + 2, 12)

    ttk.Button(
        frame,
        text='Okay',
        command=lambda: root.destroy()
    ).grid(row=row_index, column=0, sticky="w", padx=5, pady=5)

    ttk.Button(
        frame,
        text='Exit',
        command=lambda: flag.cancel(root)
    ).grid(row=row_index, column=1, sticky="w", padx=5, pady=5)

    return list_length, var
