"""
Feeder, external grid and device selection dialogs.

Collects the user's inputs for an interactive fault level study:

1. Feeder Selection: radial feeders (mesh feeders excluded) with
   checkboxes for multi-selection.

2. External Grid Data: maximum, minimum and system normal minimum
   fault level parameters for each external grid, pre-filled from the
   model and, for the system normal minimum, the master project.
   The values entered are written to the grids.

3. Device Selection: protection devices (or, with no relays modelled,
   switches) per feeder.

Discovery of radial feeders, devices and switches comes from
pipeline.discovery, so the interactive and batch paths assess the same
things; this module only adds the dialogs and the grid data entry.

Classes:
    StudyInputs: Everything the dialogs collect.
    FaultLevelStudy: The dialog sequence.

Functions:
    get_input: Run the dialog sequence and return StudyInputs.
"""

import math
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

from pf_config import pft
import criteria
from assets.enums import ElementType
from pipeline import discovery
from pipeline.errors import AssessmentError
from interactive.dialogs import CancelFlag
from interactive.study_selection import Study


@dataclass
class StudyInputs:
    """Inputs collected by the interactive dialogs.

    Attributes:
        feeders_devices: Feeder name to the PowerFactory devices to
            assess. With no relays modelled, these are the switches
            the user selected.
        bu_devices: External grid to its backup devices (empty with no
            relays modelled).
        selected: Feeder name to the devices the user selected; the
            optional assessments run on these.
        external_grid: Grid object to its 15 fault level values, as
            entered.
    """
    feeders_devices: Dict[str, List[Any]]
    bu_devices: Dict[Any, List[Any]]
    selected: Dict[str, List[Any]]
    external_grid: Dict[Any, List[float]]


class FaultLevelStudy:
    """
    Run the input dialogs for an interactive fault level study.

    Attributes:
        app: PowerFactory application instance for model queries
            and output messaging.

    Example:
        >>> inputs = FaultLevelStudy(app).main('SEQ', Study.ALL_RELAYS)
    """

    def __init__(self, app: pft.Application) -> None:
        """
        Initialize the dialog sequence.

        Args:
            app: PowerFactory application instance.
        """
        self.app = app

    def main(self, region: str, study: Study) -> StudyInputs:
        """
        Run feeder/grid selection, then device selection.

        Args:
            region: Network region ('SEQ', 'Northern', 'Southern').
            study: Study.ALL_RELAYS, or Study.NO_RELAYS / Study.LEGACY
                (both select switches as stand-in devices).

        Returns:
            The collected inputs.

        Raises:
            AssessmentError: If there are no radial feeders, or no
                selected feeder has any protection device.
            UserCancelled: If the user presses Exit in any dialog.
        """
        radial_list, mesh_list = discovery.classify_feeders(self.app)
        if not radial_list:
            self.show_no_radial_feeders_message()
            raise AssessmentError("No radial feeders detected in project.")
        self.report_mesh_feeders(mesh_list)

        feeder_list, external_grid = self.feeders_external_grid(
            radial_list, bool(mesh_list)
        )

        if study is Study.ALL_RELAYS:
            feeders_devices, bu_devices = discovery.get_feeders_devices(
                self.app, feeder_list
            )
            discovery.chk_empty_fdrs(self.app, feeders_devices)
            selected = self.run_window(
                list(feeders_devices), feeders_devices, region,
                relays_configured=True
            )
        else:
            feeders_devices, bu_devices = discovery.get_feeders_switches(
                self.app, feeder_list, region
            )
            discovery.chk_empty_fdrs(self.app, feeders_devices)
            selected = self.run_window(
                list(feeders_devices), feeders_devices, region,
                relays_configured=False
            )
            feeders_devices = selected

        return StudyInputs(feeders_devices, bu_devices, selected, external_grid)

    def center_window(
        self, root: tk.Tk, width: int, height: int
    ) -> None:
        """
        Center a tkinter window on the user's screen.

        Args:
            root: The tkinter root window to center.
            width: Desired window width in pixels.
            height: Desired window height in pixels.
        """
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        root.geometry(f"{width}x{height}+{x}+{y}")

    def report_mesh_feeders(self, mesh_list: List) -> None:
        """
        Warn about mesh feeders and their out-of-service lines.

        Args:
            mesh_list: Mesh feeder (ElmFeeder) objects.
        """
        if not mesh_list:
            return
        self.app.PrintWarn(
            "The following feeders were identified as meshed (indicating "
            "that an external grid is in service.\n"
            "both upstream and downstream of the feeder cubicle):"
        )
        for feeder in mesh_list:
            self.app.PrintWarn(f"{feeder.loc_name}")
            lines_oos = self.get_lines_oos(feeder)
            if lines_oos:
                str_lines = [line.loc_name for line in lines_oos]
                self.app.PrintWarn(
                    f"{feeder.loc_name} has the following lines out of "
                    f"service:\n"
                    f"{str_lines} \n"
                    "If any of these lines serve as open points, the feeder "
                    "will be treated as meshed.\n"
                    "To remedy this, return the lines to service and place "
                    "an open switch at one end of each line."
                )

    def get_lines_oos(self, feeder: pft.ElmFeeder) -> List:
        """
        Get lines out of service for a given feeder.

        Identifies HV, TR, and LN prefixed lines that are out of
        service and connected to the specified feeder.

        Args:
            feeder: PowerFactory feeder element to check.

        Returns:
            List of out-of-service line elements connected to the
            feeder. Empty list if none found.
        """
        oos_lines = []
        line_prefixes = ["HV", "TR", "LN"]

        for grid in self.app.GetSummaryGrid().GetContents():
            oos_lines += [
                line
                for line in grid.obj_id.GetContents("*.ElmLne")
                if any(
                    string in line.GetAttribute("loc_name")
                    for string in line_prefixes
                )
                if line.IsOutOfService()
            ]

        fdr_lines_oos = []
        for line in oos_lines:
            line_terms = line.GetConnectedElements()
            if any(term in feeder.GetObjs('ElmTerm') for term in line_terms):
                fdr_lines_oos.append(line)

        return fdr_lines_oos

    def show_no_radial_feeders_message(self) -> None:
        """
        Display a dialog saying no radial feeders were found.

        The caller ends the study once the dialog is closed.
        """
        root = tk.Tk()
        root.title("Distribution fault study")

        window_width = 400
        window_height = 150
        self.center_window(root, window_width, window_height)

        ttk.Label(
            root, text="No radial feeders were found at the substation"
        ).grid(padx=5, pady=5)
        ttk.Label(
            root,
            text="To run this script, please radialise one or more feeders"
        ).grid(padx=5, pady=5)
        ttk.Button(
            root, text='Exit', command=root.destroy
        ).grid(sticky="s", padx=5, pady=5)

        root.mainloop()

    def feeders_external_grid(
        self, radial_list: List[str], mesh_feeders: bool
    ) -> Tuple[List[str], Dict[Any, List[float]]]:
        """
        Display combined feeder selection and external grid data dialog.

        Re-prompts until at least one feeder is selected and every grid
        value is numeric and in kA.

        Args:
            radial_list: List of available radial feeder names.
            mesh_feeders: True if mesh feeders were found.

        Returns:
            Tuple containing:
                - feeder_list: List of user-selected feeder names.
                - new_grid_data: Dict mapping grid objects to lists of
                  validated fault level parameters.

        Raises:
            UserCancelled: If the user presses Exit.
        """
        root = tk.Tk()
        flag = CancelFlag()

        def _window_dim():
            grid_cols = len(discovery.get_active_grids(self.app))
            column_width = 360
            feeder_col = 285
            window_width = max((grid_cols * column_width + feeder_col), 700)
            if window_width > 1300:
                window_width = 1500
            window_height = 600
            return window_width, window_height

        window_width, window_height = _window_dim()
        self.center_window(root, window_width, window_height)
        root.title("Distribution Fault Study")

        main_frame = tk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        canvas, inner_frame = self.setup_scrollable_frame(main_frame)

        button_frame = tk.Frame(root)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

        list_length, var, grid_entries, grids = self.populate_feeders(
            root, inner_frame, radial_list, button_frame, flag, mesh_feeders
        )

        # Resize window to fit content
        root.update_idletasks()
        content_h = inner_frame.winfo_reqheight() + 20
        buttons_h = button_frame.winfo_reqheight() + 12
        chrome_h = 24
        desired_h = content_h + buttons_h + chrome_h

        screen_h = root.winfo_screenheight()
        final_h = min(desired_h, int(screen_h * 0.90))
        self.center_window(root, window_width, final_h)

        root.mainloop()
        flag.raise_if_cancelled()

        feeder_list = [
            radial_list[i] for i in range(list_length) if var[i].get() == 1
        ]
        if not feeder_list:
            return self.window_error(radial_list, 3, mesh_feeders)

        new_grid_data = self.collect_grid_data(grid_entries)
        if new_grid_data is None:
            return self.window_error(radial_list, 2, mesh_feeders)
        if not self.validate_grid_data(new_grid_data):
            return self.window_error(radial_list, 1, mesh_feeders)

        self.update_grid_data(grids, new_grid_data)

        return feeder_list, new_grid_data

    def setup_scrollable_frame(
        self, parent: tk.Frame
    ) -> Tuple[tk.Canvas, tk.Frame]:
        """
        Create a scrollable frame within the parent container.

        Sets up a canvas with vertical and horizontal scrollbars that
        dynamically show/hide based on content size.

        Args:
            parent: Parent tkinter frame to contain the scrollable
                area.

        Returns:
            Tuple containing:
                - canvas: The canvas widget for scrolling.
                - inner_frame: The frame inside the canvas for content.
        """
        canvas = tk.Canvas(parent, borderwidth=0)
        inner_frame = tk.Frame(canvas)
        vsb = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        hsb = tk.Scrollbar(parent, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        canvas.pack(fill="both", expand=True)
        canvas.create_window((4, 4), window=inner_frame, anchor="nw")

        inner_frame.bind(
            "<Configure>",
            lambda event: self.onFrameConfigure(canvas, vsb, hsb)
        )
        canvas.bind(
            "<Configure>",
            lambda event: self.onCanvasConfigure(canvas, vsb, hsb)
        )

        return canvas, inner_frame

    @staticmethod
    def onFrameConfigure(
        canvas: tk.Canvas, vsb: tk.Scrollbar, hsb: tk.Scrollbar
    ) -> None:
        """
        Update scroll region and show/hide scrollbars as needed.

        Called when the inner frame is resized to update the canvas
        scroll region.

        Args:
            canvas: The canvas widget.
            vsb: Vertical scrollbar widget.
            hsb: Horizontal scrollbar widget.
        """
        canvas.configure(scrollregion=canvas.bbox("all"))
        FaultLevelStudy.update_scrollbars(canvas, vsb, hsb)

    @staticmethod
    def onCanvasConfigure(
        canvas: tk.Canvas, vsb: tk.Scrollbar, hsb: tk.Scrollbar
    ) -> None:
        """
        Handle canvas resize events.

        Updates scrollbar visibility when the canvas is resized.

        Args:
            canvas: The canvas widget.
            vsb: Vertical scrollbar widget.
            hsb: Horizontal scrollbar widget.
        """
        FaultLevelStudy.update_scrollbars(canvas, vsb, hsb)

    @staticmethod
    def update_scrollbars(
        canvas: tk.Canvas, vsb: tk.Scrollbar, hsb: tk.Scrollbar
    ) -> None:
        """
        Show or hide scrollbars based on content size vs canvas size.

        Dynamically displays scrollbars only when content exceeds the
        visible canvas area.

        Args:
            canvas: The canvas widget.
            vsb: Vertical scrollbar widget.
            hsb: Horizontal scrollbar widget.
        """
        scrollregion = canvas.cget("scrollregion")
        if not scrollregion:
            return

        x1, y1, x2, y2 = map(float, scrollregion.split())
        content_width = x2 - x1
        content_height = y2 - y1

        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()

        if content_height > canvas_height:
            vsb.pack(side="right", fill="y", before=canvas)
        else:
            vsb.pack_forget()

        if content_width > canvas_width:
            hsb.pack(side="bottom", fill="x", before=canvas)
        else:
            hsb.pack_forget()

        canvas.pack(fill="both", expand=True)

    def populate_feeders(
        self,
        root: tk.Tk,
        frame: tk.Frame,
        radial_list: List[str],
        button_frame: tk.Frame,
        flag: CancelFlag,
        mesh_feeders: bool = False
    ) -> Tuple[int, List[tk.IntVar], Dict, List]:
        """
        Create feeder selection and external grid entry widgets.

        Args:
            root: Root tkinter window.
            frame: Frame to contain the widgets.
            radial_list: List of radial feeder names.
            button_frame: Frame for action buttons.
            flag: Cancel flag the Exit button sets.
            mesh_feeders: True if mesh feeders were found.

        Returns:
            Tuple containing:
                - list_length: Number of feeders in the list.
                - var: List of IntVar checkbox variables.
                - grid_entries: Dict of grid entry field variables.
                - grids: List of active external grid objects.
        """
        ttk.Label(
            frame,
            text="Select all feeders to study:",
            font='Helvetica 14 bold'
        ).grid(row=0, column=0, columnspan=3, padx=5, pady=5, sticky="w")

        ttk.Label(
            frame,
            text="(mesh feeders excluded)",
            font='Helvetica 10 bold'
        ).grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="w")

        current_row = 2
        if mesh_feeders:
            ttk.Label(
                frame,
                text="Note: mesh feeders were detected.",
                font='Helvetica 10 bold',
                wraplength=250
            ).grid(
                row=current_row, column=0, columnspan=3,
                padx=5, pady=2, sticky="w"
            )
            current_row += 1
            ttk.Label(
                frame,
                text="See output window for more information",
                font='Helvetica 10 bold',
                wraplength=250
            ).grid(
                row=current_row, column=0, columnspan=3,
                padx=5, pady=2, sticky="w"
            )
            current_row += 1

        grids = discovery.get_active_grids(self.app)
        grid_data = self.get_grid_data(grids)
        self.app.PrintPlain("Please enter the requested inputs.")

        feeder_frame = tk.Frame(frame)
        feeder_frame.grid(
            row=current_row, column=0, columnspan=4, sticky="nw", padx=5, pady=5
        )

        var = self.create_feeder_checkboxes(feeder_frame, radial_list)

        frame.columnconfigure(4, minsize=100)

        grid_entries = self.create_external_grid_interface(
            frame, grids, grid_data, current_row
        )

        ttk.Button(
            button_frame, text='Okay', command=root.destroy
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            button_frame, text='Exit', command=lambda: flag.cancel(root)
        ).pack(side=tk.LEFT, padx=5)

        return len(radial_list), var, grid_entries, grids

    def get_master_grid(self, grid_loc_name: str):
        """
        Retrieve master project grid data for system normal minimum.

        Searches the derived base project for matching external grid
        elements to obtain system normal minimum fault level data.

        Args:
            grid_loc_name: Location name of the grid to find.

        Returns:
            The master project grid object if found, False otherwise.
        """
        project = self.app.GetActiveProject()
        derived_proj = project.GetAttribute('der_baseproject')

        if derived_proj is None:
            return False

        net_dat = derived_proj.GetContents("Network Model\\Network Data")
        for network in net_dat:
            elm_nets = network.GetContents("*.ElmNet")
            for elm_net in elm_nets:
                elm_substats = elm_net.GetContents("*.ElmSubstat")
                for elm_substat in elm_substats:
                    grids = elm_substat.GetContents(
                        f'{grid_loc_name}.ElmXnet', 1
                    )
                    if grids:
                        master_proj_grids = grids[0]
                        self.app.PrintPlain(
                            f'Gathered master grid data for {grid_loc_name}.'
                        )
                        return master_proj_grids

        self.app.PrintPlain(
            f'Could not find master grid data for {grid_loc_name}.'
        )
        return False

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

    def create_feeder_checkboxes(
        self, feeder_frame: tk.Frame, radial_list: List[str]
    ) -> List[tk.IntVar]:
        """
        Create checkbox widgets for feeder selection.

        Args:
            feeder_frame: Frame to contain the checkboxes.
            radial_list: List of feeder names to display.

        Returns:
            List of IntVar variables linked to each checkbox.
        """
        var = []
        for i, feeder in enumerate(radial_list):
            var.append(tk.IntVar())
            ttk.Checkbutton(
                feeder_frame, text=feeder, variable=var[-1]
            ).grid(row=i, column=0, sticky="w", padx=25, pady=5)
        return var

    def create_external_grid_interface(
        self,
        frame: tk.Frame,
        grids: List,
        grid_data: Dict,
        start_row: int = 0
    ) -> Dict:
        """
        Create external grid parameter entry interface.

        Generates labeled entry fields for fault level parameters
        organized by grid element.

        Args:
            frame: Parent frame for the interface.
            grids: List of external grid objects.
            grid_data: Dict of current grid parameter values.
            start_row: Starting row for grid placement.

        Returns:
            Dict mapping grid objects to lists of entry field
            variables.
        """
        ttk.Label(
            frame,
            text="Enter external grid data:",
            font='Helvetica 14 bold'
        ).grid(column=5, columnspan=3, sticky="w", row=0, padx=5, pady=5)

        grid_entries_frame = tk.Frame(frame)
        grid_entries_frame.grid(
            row=start_row, column=5, columnspan=10,
            sticky="nw", padx=5, pady=5
        )

        grid_entries = {}
        for i, grid in enumerate(grids):
            grid_entries[grid] = self.create_grid_entries(
                grid_entries_frame, grid, grid_data[grid], i * 3
            )
        return grid_entries

    def create_grid_entries(
        self,
        grid_frame: tk.Frame,
        grid: Any,
        data: List[float],
        column: int
    ) -> List[tk.DoubleVar]:
        """
        Create entry fields for a single external grid's parameters.

        Generates three labeled frames (Maximum, Minimum, System Normal
        Minimum) each containing five parameter entry fields.

        Args:
            grid_frame: Parent frame for the entry fields.
            grid: External grid object.
            data: List of 15 current parameter values.
            column: Starting column position.

        Returns:
            List of 15 DoubleVar variables for the entry fields.
        """
        grid_entries = []
        labels = [
            "P-P-P fault max.", "R/X max.", "Z2/Z1 max.",
            "X0/X1 max.", "R0/X0 max.",
            "P-P-P fault min.", "R/X min.", "Z2/Z1 min.",
            "X0/X1 min.", "R0/X0 min."
        ]

        # Maximum values frame
        max_frame = tk.LabelFrame(
            grid_frame,
            text=f"{grid.loc_name} Maximum Values",
            relief="solid", bd=1, padx=5, pady=5
        )
        max_frame.grid(
            row=0, column=column, columnspan=3, padx=5, pady=5, sticky="ew"
        )

        for i in range(5):
            label, value = labels[i], data[i]
            value = round(value, 6)
            tk.Label(max_frame, text=label).grid(
                row=i, column=0, pady=2, sticky="w"
            )
            var = tk.DoubleVar(value=value)
            tk.Entry(max_frame, textvariable=var).grid(
                row=i, column=1, pady=2, padx=(5, 0)
            )
            if i == 0:
                tk.Label(max_frame, text='kA').grid(
                    row=i, column=2, ipadx=5, pady=2
                )
            grid_entries.append(var)

        # Minimum values frame
        min_frame = tk.LabelFrame(
            grid_frame,
            text=f"{grid.loc_name} Minimum Values",
            relief="solid", bd=1, padx=5, pady=5
        )
        min_frame.grid(
            row=1, column=column, columnspan=3, padx=5, pady=5, sticky="ew"
        )

        for i in range(5, 10):
            label, value = labels[i], data[i]
            value = round(value, 6)
            tk.Label(min_frame, text=label).grid(
                row=i - 5, column=0, pady=2, sticky="w"
            )
            var = tk.DoubleVar(value=value)
            tk.Entry(min_frame, textvariable=var).grid(
                row=i - 5, column=1, pady=2, padx=(5, 0)
            )
            if i == 5:
                tk.Label(min_frame, text='kA').grid(
                    row=i - 5, column=2, ipadx=5, pady=2
                )
            grid_entries.append(var)

        # System normal minimum frame
        sys_norm_min_frame = tk.LabelFrame(
            grid_frame,
            text=f"{grid.loc_name} Sys Norm Minimum Values",
            relief="solid", bd=1, padx=5, pady=5
        )
        sys_norm_min_frame.grid(
            row=2, column=column, columnspan=3, padx=5, pady=5, sticky="ew"
        )

        for i in range(10, 15):
            label, value = labels[i - 5], data[i]
            value = round(value, 6)
            tk.Label(sys_norm_min_frame, text=label).grid(
                row=i - 10, column=0, pady=2, sticky="w"
            )
            var = tk.DoubleVar(value=value)
            tk.Entry(sys_norm_min_frame, textvariable=var).grid(
                row=i - 10, column=1, pady=2, padx=(5, 0)
            )
            if i == 10:
                tk.Label(sys_norm_min_frame, text='kA').grid(
                    row=i - 10, column=2, ipadx=5, pady=2
                )
            grid_entries.append(var)

        if data[10] != 0:
            tk.Label(
                sys_norm_min_frame,
                text="Default Values Copied From Master Project"
            ).grid(row=6, columnspan=3, pady=2)

        return grid_entries

    def collect_grid_data(self, grid_entries: Dict) -> Optional[Dict]:
        """
        Read the values from the grid entry fields.

        Args:
            grid_entries: Dict of grid objects to entry field lists.

        Returns:
            Dict mapping grid objects to lists of entered values, or
            None if any field is not a number.
        """
        new_grid_data = {}
        for grid in grid_entries:
            try:
                new_grid_data[grid] = [
                    item.get() for item in grid_entries[grid]
                ]
            except Exception:
                return None
        return new_grid_data

    def validate_grid_data(self, new_grid_data: Dict) -> bool:
        """
        Validate that fault level values are in correct units.

        Checks that fault current values are in kA (not A) by
        verifying they are less than 100.

        Args:
            new_grid_data: Dict of grid parameter values to validate.

        Returns:
            True if all values are valid, False otherwise.
        """
        return all(
            new_grid_data[grid][0] <= 100
            and new_grid_data[grid][5] <= 100
            and new_grid_data[grid][10] <= 100
            for grid in new_grid_data
        )

    def update_grid_data(self, grids: List, new_grid_data: Dict) -> None:
        """
        Apply validated grid data back to PowerFactory objects.

        Updates external grid element attributes with user-entered
        values and sets c-factor values.

        Args:
            grids: List of external grid objects to update.
            new_grid_data: Dict of validated parameter values.
        """
        attributes = [
            'ikss', 'rntxn', 'z2tz1', 'x0tx1', 'r0tx0',
            'ikssmin', 'rntxnmin', 'z2tz1min', 'x0tx1min', 'r0tx0min'
        ]

        for grid in grids:
            try:
                for attr, value in zip(attributes, new_grid_data[grid]):
                    setattr(grid, attr, value)
            except AttributeError:
                pass
            grid.cmax = 1.1
            grid.cmin = 1

    def window_error(
        self, radial_list: List[str], error_code: int, mesh_feeders: bool
    ) -> Tuple[List[str], Dict]:
        """
        Report an input error and re-prompt.

        Args:
            radial_list: List of radial feeder names.
            error_code: Error type:
                1 = Fault level in wrong units (A instead of kA)
                2 = Non-numerical value entered
                3 = No feeder selected
            mesh_feeders: True if mesh feeders were found.

        Returns:
            Result of re-prompting the feeders_external_grid dialog.

        Raises:
            UserCancelled: If the user presses Exit on the re-prompt.
        """
        error_messages = {
            1: "Please enter fault level values in kA, not A",
            2: "Please enter numerical values only",
            3: "Please select at least one feeder to continue"
        }
        self.app.PrintPlain(error_messages[error_code])
        return self.feeders_external_grid(radial_list, mesh_feeders)

    def populate(
        self,
        frame: tk.Frame,
        feeder_list: List[str],
        feeders_switches: Dict,
        region: str,
        button_frame: tk.Frame,
        relays_configured: bool,
        flag: CancelFlag
    ) -> Tuple[List[tk.IntVar], Dict[str, List[str]]]:
        """
        Create device selection checkboxes for each feeder.

        Generates a grid of checkboxes organized by feeder columns
        for user device selection.

        Args:
            frame: Parent frame for the checkboxes.
            feeder_list: List of feeder names.
            feeders_switches: Dict of feeder names to device lists.
            region: Network region for display name formatting.
            button_frame: Frame for action buttons.
            relays_configured: True if relays are pre-configured.
            flag: Cancel flag the Exit button sets.

        Returns:
            Tuple containing:
                - var: List of IntVar checkbox variables.
                - fdr_sw_locname: Dict mapping feeders to device
                  display names.
        """
        if criteria.is_regional(region):
            fdr_sw_locname = {
                feeder: [switch.loc_name for switch in switches]
                for feeder, switches in feeders_switches.items()
            }
        else:
            fdr_sw_locname = {feeder: [] for feeder in feeders_switches}
            for feeder, switches in feeders_switches.items():
                for switch in switches:
                    if switch.GetClassName() == ElementType.FEEDER.value:
                        cubicle = switch.obj_id
                    else:
                        cubicle = switch.fold_id
                    switch_term = cubicle.cterm.loc_name
                    display_name = (
                        switch_term[:-5]
                        if switch_term.endswith("_Term")
                        else switch_term
                    )
                    fdr_sw_locname[feeder].append(display_name)

        if relays_configured:
            ttk.Label(
                frame,
                text="Select all protection devices to study:",
                font='Helvetica 12 bold'
            ).grid(columnspan=8, padx=5, pady=5)
        else:
            ttk.Label(
                frame,
                text="Select ALL protection devices:",
                font='Helvetica 12 bold'
            ).grid(columnspan=8, padx=5, pady=5)

        for idx, fid in enumerate(feeder_list):
            ttk.Label(frame, text=fid).grid(
                row=1, column=idx, sticky='W', padx=10, pady=5
            )

        var = []

        for feeder, switch_list in fdr_sw_locname.items():
            col = list(fdr_sw_locname).index(feeder)
            for i, switch in enumerate(switch_list):
                var.append(tk.IntVar())
                ttk.Checkbutton(
                    frame, text=switch, variable=var[-1]
                ).grid(row=i + 4, column=col, sticky='W', padx=10, pady=5)

        def select_all():
            for checkbox_var in var:
                checkbox_var.set(1)

        def unselect_all():
            for checkbox_var in var:
                checkbox_var.set(0)

        ttk.Button(
            button_frame, text='Select All', command=select_all
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            button_frame, text='Unselect All', command=unselect_all
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            button_frame,
            text='Okay',
            command=lambda: button_frame.master.destroy()
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            button_frame,
            text='Exit',
            command=lambda: flag.cancel(button_frame.master)
        ).pack(side=tk.LEFT, padx=5)

        return var, fdr_sw_locname

    def run_window(
        self,
        feeder_list: List[str],
        feeders_devices: Dict[str, List[Any]],
        region: str,
        relays_configured: bool
    ) -> Dict[str, List[Any]]:
        """
        Display device selection window and collect user choices.

        Creates a scrollable window with device checkboxes organized
        by feeder and returns the user's selection.

        Args:
            feeder_list: List of feeder names.
            feeders_devices: Dict mapping feeders to device lists.
            region: Network region for display formatting.
            relays_configured: True if relays are pre-configured.

        Returns:
            Dict mapping feeder names to lists of selected device
            objects.

        Raises:
            UserCancelled: If the user presses Exit.
        """

        self.app.PrintPlain("Please make a study selection...")

        def _window_dim(feeder_list, feeders_switches, region):
            num_columns = len(feeder_list)
            if criteria.is_regional(region):
                column_width = 230
            else:
                column_width = 120
            col_padding = 0
            window_width = max(
                (num_columns * column_width + col_padding), 600
            )

            if window_width > 1300:
                window_width = 1500

            num_rows = max(
                len(lst) for lst in feeders_switches.values()
            )
            row_height = 32
            row_padding = 100
            window_height = max(
                (num_rows * row_height + row_padding), 350
            )

            if window_height > 900:
                window_height = 900

            return window_width, window_height

        root = tk.Tk()
        flag = CancelFlag()
        root.title("Distribution Fault Study")
        window_width, window_height = _window_dim(
            feeder_list, feeders_devices, region
        )
        self.center_window(root, window_width, window_height)

        main_frame = tk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        canvas, frame = self.setup_scrollable_frame(main_frame)

        button_frame = tk.Frame(root)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

        var, fdr_dev_locname = self.populate(
            frame, feeder_list, feeders_devices,
            region, button_frame, relays_configured, flag
        )

        canvas.pack(expand=True, fill=tk.BOTH)
        root.mainloop()
        flag.raise_if_cancelled()

        # Build list of selected devices
        acr_fuse_list = [
            dev for feeder in fdr_dev_locname
            for i, dev in enumerate(feeders_devices[feeder])
            if var[
                sum(
                    len(fdr_dev_locname[f])
                    for f in list(fdr_dev_locname)[
                        :list(fdr_dev_locname).index(feeder)
                    ]
                ) + i
            ].get() == 1
        ]

        feeders_relays = {
            feeder: [
                switch for switch in switches
                if switch in acr_fuse_list
            ]
            for feeder, switches in feeders_devices.items()
        }

        return feeders_relays


def get_input(
    app: pft.Application, region: str, study: Study
) -> StudyInputs:
    """
    Run the input dialogs for an interactive fault level study.

    Args:
        app: PowerFactory application instance.
        region: Network region ('SEQ', 'Northern', 'Southern').
        study: Study.ALL_RELAYS, or Study.NO_RELAYS / Study.LEGACY
            (both select switches as stand-in devices).

    Returns:
        The collected inputs.

    Raises:
        AssessmentError: If there is nothing to assess.
        UserCancelled: If the user presses Exit in any dialog.
    """
    return FaultLevelStudy(app).main(region, study)
