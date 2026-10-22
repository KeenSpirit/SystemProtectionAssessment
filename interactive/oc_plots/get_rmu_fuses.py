"""
RMU transformer fuse specification GUI dialog.

This module provides a GUI for users to specify insulation type and
impedance class for RMU-connected distribution transformers in SEQ
models. This information is required for correct fuse selection per
Technical Instruction TS0013i: RMU Fuse Selection Guide.

Fuse Selection Criteria:
    - Insulation type: Air or Oil insulated RMU
    - Impedance class: High or Low (only for air-insulated ≥750kVA)

Classes:
    TransformerSpecificationGUI: Main GUI window class

Functions:
    get_transformer_specifications: Entry point for collecting specs
"""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List

from interactive.dialogs import UserCancelled


class TransformerSpecificationGUI:
    """
    GUI dialog for collecting transformer fuse specifications.

    Displays a scrollable list of transformers requiring specification
    of insulation type (air/oil) and optionally impedance class
    (high/low for air-insulated transformers ≥750kVA).

    Attributes:
        items_list: List of transformer identifier strings.
        user_inputs: Dictionary of collected specifications.
        root: Tkinter root window.
        insulation_vars: Dict of StringVars for insulation selection.
        impedance_vars: Dict of StringVars for impedance selection.
        impedance_frames: Dict of frames for impedance widgets.

    Example:
        >>> gui = TransformerSpecificationGUI(['TR001_1', 'TR002_0'])
        >>> results = gui.run()
        >>> print(results['TR001_1']['insulation'])
        'air'
    """

    def __init__(self, items_list: List[str]) -> None:
        """
        Initialize the transformer specification GUI.

        Args:
            items_list: List of transformer identifier strings.
                The last character indicates if impedance selection
                is required ('1' = required, '0' = not required).
        """
        self.items_list = items_list
        self.user_inputs = {}
        self.cancelled = False
        self.root = tk.Tk()
        # Closing the window counts as Exit (it used to crash on the
        # destroy in run() and leave no inputs).
        self.root.protocol("WM_DELETE_WINDOW", self.exit_application)
        self.setup_window()
        self.create_widgets()

    def setup_window(self) -> None:
        """Configure the main window size and position."""
        self.root.title("Transformer Fuse Specification")

        # Calculate window size based on items
        base_height = 200
        item_height = 80
        max_display_items = 8

        display_items = min(len(self.items_list), max_display_items)
        window_height = base_height + (display_items * item_height)
        window_width = 600

        # Center on screen
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2

        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.root.resizable(True, True)

    def create_widgets(self) -> None:
        """Create all GUI widgets."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # Header
        header_text = (
            "Please enter the requested data for the following "
            "downstream distribution transformers:"
        )
        header_label = ttk.Label(
            main_frame,
            text=header_text,
            wraplength=550,
            justify=tk.CENTER
        )
        header_label.grid(row=0, column=0, pady=(0, 20), sticky=(tk.W, tk.E))

        # Scrollable frame
        self.create_scrollable_frame(main_frame)

        # Bottom frame
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(row=2, column=0, pady=(20, 0), sticky=(tk.W, tk.E))
        bottom_frame.columnconfigure(1, weight=1)

        # Reference text
        ref_text = (
            "Refer to Technical Instruction TS0013i: "
            "RMU Fuse Selection Guide for further information."
        )
        ref_label = ttk.Label(
            bottom_frame,
            text=ref_text,
            font=("TkDefaultFont", 9)
        )
        ref_label.grid(row=0, column=0, columnspan=3, pady=(0, 10))

        # Buttons
        ok_button = ttk.Button(
            bottom_frame,
            text="Okay",
            command=self.validate_and_close
        )
        ok_button.grid(row=1, column=0, padx=(0, 10), sticky=tk.W)

        exit_button = ttk.Button(
            bottom_frame,
            text="Exit",
            command=self.exit_application
        )
        exit_button.grid(row=1, column=2, sticky=tk.E)

    def create_scrollable_frame(self, parent: ttk.Frame) -> None:
        """
        Create scrollable frame for transformer input widgets.

        Args:
            parent: Parent frame to contain scrollable area.
        """
        # Canvas and scrollbar
        self.canvas = tk.Canvas(parent, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            parent, orient="vertical", command=self.canvas.yview
        )
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.canvas_parent = parent

        # Configure scrolling
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.update_scroll_region()
        )

        self.canvas.create_window(
            (0, 0), window=self.scrollable_frame, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # Grid canvas
        self.canvas.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # Mouse wheel bindings
        self.canvas.bind(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        )
        self.canvas.bind(
            "<Button-4>",
            lambda e: self.canvas.yview_scroll(-1, "units")
        )
        self.canvas.bind(
            "<Button-5>",
            lambda e: self.canvas.yview_scroll(1, "units")
        )

        # Create input widgets
        self.create_input_widgets(self.scrollable_frame)

        # Schedule scrollbar check
        self.root.after(100, self.check_scrollbar_needed)

    def update_scroll_region(self) -> None:
        """Update scroll region and check if scrollbar is needed."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.root.after_idle(self.check_scrollbar_needed)

    def check_scrollbar_needed(self) -> None:
        """Show or hide scrollbar based on content height."""
        self.canvas.update_idletasks()

        bbox = self.canvas.bbox("all")
        if bbox:
            content_height = bbox[3] - bbox[1]
            canvas_height = self.canvas.winfo_height()

            if content_height > canvas_height:
                self.scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))
                self.canvas_parent.columnconfigure(1, weight=0)
            else:
                self.scrollbar.grid_remove()
                self.canvas_parent.columnconfigure(1, weight=0)

    def create_input_widgets(self, parent: ttk.Frame) -> None:
        """
        Create input widgets for each transformer item.

        Args:
            parent: Parent frame for widgets.
        """
        self.insulation_vars = {}
        self.impedance_vars = {}
        self.impedance_frames = {}

        for i, item in enumerate(self.items_list):
            # Item frame (strip encoding suffix for display)
            display_name = item[:-2] if len(item) > 2 else item
            item_frame = ttk.LabelFrame(
                parent, text=f"Item: {display_name}", padding="10"
            )
            item_frame.grid(row=i, column=0, pady=5, padx=5, sticky=(tk.W, tk.E))
            parent.columnconfigure(0, weight=1)

            # Insulation type selection
            insulation_frame = ttk.Frame(item_frame)
            insulation_frame.grid(
                row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10)
            )

            ttk.Label(
                insulation_frame, text="Insulation Type:"
            ).grid(row=0, column=0, sticky=tk.W)

            self.insulation_vars[item] = tk.StringVar()

            air_radio = ttk.Radiobutton(
                insulation_frame,
                text="Air Insulated",
                variable=self.insulation_vars[item],
                value="air",
                command=lambda i=item: self.on_insulation_change(i)
            )
            air_radio.grid(row=1, column=0, sticky=tk.W, padx=(20, 0))

            oil_radio = ttk.Radiobutton(
                insulation_frame,
                text="Oil Insulated",
                variable=self.insulation_vars[item],
                value="oil",
                command=lambda i=item: self.on_insulation_change(i)
            )
            oil_radio.grid(row=1, column=1, sticky=tk.W, padx=(20, 0))

            # Impedance selection frame (initially hidden)
            impedance_frame = ttk.Frame(item_frame)
            impedance_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
            self.impedance_frames[item] = impedance_frame

            ttk.Label(
                impedance_frame, text="Impedance Type:"
            ).grid(row=0, column=0, sticky=tk.W)

            self.impedance_vars[item] = tk.StringVar()

            high_radio = ttk.Radiobutton(
                impedance_frame,
                text="High Impedance",
                variable=self.impedance_vars[item],
                value="high"
            )
            high_radio.grid(row=1, column=0, sticky=tk.W, padx=(20, 0))

            low_radio = ttk.Radiobutton(
                impedance_frame,
                text="Low Impedance",
                variable=self.impedance_vars[item],
                value="low"
            )
            low_radio.grid(row=1, column=1, sticky=tk.W, padx=(20, 0))

            # Initially hide impedance frame
            self.toggle_impedance_frame(item, False)

    def on_insulation_change(self, item: str) -> None:
        """
        Handle insulation type selection change.

        Shows or hides impedance selection based on insulation type
        and transformer size encoding.

        Args:
            item: Transformer identifier string.
        """
        insulation_type = self.insulation_vars[item].get()

        if insulation_type == "air":
            # Check if impedance selection required (encoded in last char)
            try:
                last_digit = int(item[-1])
                show_impedance = (last_digit == 1)
            except (ValueError, IndexError):
                show_impedance = False

            self.toggle_impedance_frame(item, show_impedance)

            if not show_impedance:
                self.impedance_vars[item].set("")
        else:
            self.toggle_impedance_frame(item, False)
            self.impedance_vars[item].set("")

        self.root.after_idle(self.check_scrollbar_needed)

    def toggle_impedance_frame(self, item: str, show: bool) -> None:
        """
        Show or hide impedance selection frame.

        Args:
            item: Transformer identifier string.
            show: True to show, False to hide.
        """
        if show:
            self.impedance_frames[item].grid()
        else:
            self.impedance_frames[item].grid_remove()

    def validate_inputs(self) -> List[str]:
        """
        Validate that all required inputs are provided.

        Returns:
            List of error messages for missing inputs.
        """
        missing_items = []

        for item in self.items_list:
            insulation_type = self.insulation_vars[item].get()

            if not insulation_type:
                display_name = item[:-2] if len(item) > 2 else item
                missing_items.append(
                    f"{display_name}: No insulation type selected"
                )
            elif insulation_type == "air":
                # Check if impedance required
                try:
                    last_digit = int(item[-1])
                    impedance_required = (last_digit == 1)
                except (ValueError, IndexError):
                    impedance_required = False

                if impedance_required:
                    impedance_type = self.impedance_vars[item].get()
                    if not impedance_type:
                        display_name = item[:-2] if len(item) > 2 else item
                        missing_items.append(
                            f"{display_name}: No impedance type selected"
                        )

        return missing_items

    def collect_inputs(self) -> Dict[str, Dict]:
        """
        Collect all user inputs into a dictionary.

        Returns:
            Dictionary mapping item names to specification dicts:
            {'insulation': str, 'impedance': str or None}
        """
        inputs = {}

        for item in self.items_list:
            insulation_type = self.insulation_vars[item].get()
            inputs[item] = {"insulation": insulation_type}

            if insulation_type == "air":
                impedance_type = self.impedance_vars[item].get()
                inputs[item]["impedance"] = impedance_type if impedance_type else None
            else:
                inputs[item]["impedance"] = None

        return inputs

    def validate_and_close(self) -> None:
        """Validate inputs and close if valid."""
        missing_items = self.validate_inputs()

        if missing_items:
            error_message = (
                "Please provide the following missing information:\n\n"
            )
            error_message += "\n".join(missing_items)
            messagebox.showerror("Missing Information", error_message)
        else:
            self.user_inputs = self.collect_inputs()
            self.root.quit()

    def exit_application(self) -> None:
        """Record the cancellation and leave the main loop."""
        self.cancelled = True
        self.root.quit()

    def run(self) -> Dict[str, Dict]:
        """
        Run the GUI and return user inputs.

        Returns:
            Dictionary of transformer specifications.

        Raises:
            UserCancelled: If the user pressed Exit or closed the window.
        """
        self.root.mainloop()
        self.root.destroy()
        if self.cancelled:
            raise UserCancelled()
        return self.user_inputs


def get_transformer_specifications(items_list: List[str]) -> Dict[str, Dict]:
    """
    Display GUI and collect transformer fuse specifications.

    Main entry point for collecting insulation type and impedance
    class for RMU-connected distribution transformers.

    Args:
        items_list: List of transformer identifier strings.
            Format: '{transformer_name}_{size_flag}'
            where size_flag is '1' for ≥750kVA (impedance required)
            or '0' for <750kVA (no impedance required).

    Returns:
        Dictionary mapping transformer identifiers to specification
        dicts with keys 'insulation' and 'impedance'.

    Raises:
        UserCancelled: If the user pressed Exit or closed the window.

    Example:
        >>> specs = get_transformer_specifications(['TR001_1', 'TR002_0'])
        >>> print(specs['TR001_1'])
        {'insulation': 'air', 'impedance': 'low'}
    """
    if not items_list:
        return {}

    gui = TransformerSpecificationGUI(items_list)
    return gui.run()