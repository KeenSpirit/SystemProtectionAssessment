"""
Study type selection dialog.

The first dialog of an interactive run: the user picks one study or
tool, plus optional assessments for the full relay study.

Available Studies:
    - Find PowerFactory Project: Locate project by substation acronym
    - Find Feeder Open Points: Identify normally open points
    - Fault Level Study (legacy): SEQ legacy output format
    - Fault Level Study (no relays): Assessment without relay models
    - Fault Level Study (all relays): Full protection assessment
    - Update TOC Plot Curve Labels

Optional assessments (with 'all relays' only):
    - Conductor Damage Assessment
    - Protection Coordination Assessment
    - Protection Relay Coordination Plot

Classes:
    Study: The study or tool selected.
    StudySelection: The selected study and optional assessments.

Functions:
    get_study_selection: Show the dialog and return the selection.
"""

import tkinter as tk
from dataclasses import dataclass
from enum import Enum
from tkinter import ttk

from pf_config import pft
from interactive.dialogs import CancelFlag


class Study(Enum):
    """A study or tool offered in the selection dialog."""
    FIND_PROJECT = "Find PowerFactory Project"
    OPEN_POINTS = "Find Feeder Open Points"
    LEGACY = "Fault Level Study (legacy)"
    NO_RELAYS = "Fault Level Study (no relays configured in model)"
    ALL_RELAYS = "Fault Level Study (all relays configured in model)"
    CURVE_LABELS = "Update TOC Plot Curve Labels"


@dataclass(frozen=True)
class StudySelection:
    """The user's choices from the selection dialog.

    Attributes:
        study: The selected study or tool.
        conductor_damage: Run the conductor damage assessment.
        coordination: Run the protection coordination assessment.
        coordination_plot: Draw protection relay coordination plots.

    The three options are only ever True with Study.ALL_RELAYS.
    """
    study: Study
    conductor_damage: bool = False
    coordination: bool = False
    coordination_plot: bool = False


# Radio button value -> study, in display order.
_STUDY_BUTTONS = [
    ("0", "Find PowerFactory Project", Study.FIND_PROJECT),
    ("1", "Find Feeder Open Points", Study.OPEN_POINTS),
    ("2", "Fault Level Study (SEQ legacy script)", Study.LEGACY),
    ("3", "Fault Level Study (No relays configured in model)",
     Study.NO_RELAYS),
    ("4", "Fault Level Study (All relays configured in model)",
     Study.ALL_RELAYS),
]
_ALL_RELAYS_VALUE = "4"


def get_study_selection(app: pft.Application) -> StudySelection:
    """
    Display the study selection dialog and return the user's choice.

    The optional assessment checkboxes are only enabled while the
    'All relays configured' study is selected. Re-prompts if the user
    presses Okay without selecting a study.

    Args:
        app: PowerFactory application instance for output messaging.

    Returns:
        The selected study and optional assessments.

    Raises:
        UserCancelled: If the user presses Exit.
    """
    root = tk.Tk()
    root.title("Protection Assessment")
    root.geometry("+800+200")
    flag = CancelFlag()

    selection = tk.StringVar()
    selection.set('var')

    conductor_damage_var = tk.BooleanVar(value=False)
    coordination_var = tk.BooleanVar(value=False)
    coordination_plot_var = tk.BooleanVar(value=False)

    ttk.Label(
        root,
        text="Select study to undertake:",
        font='Helvetica 14 bold'
    ).grid(row=0, columnspan=3, sticky="w", padx=5, pady=5)

    for row, (value, text, _) in enumerate(_STUDY_BUTTONS, start=1):
        tk.Radiobutton(
            root,
            text=text,
            value=value,
            variable=selection,
            command=lambda: on_radio_change()
        ).grid(row=row, column=0, sticky="w", padx=30, pady=5)

    option_boxes = []
    option_rows = [
        ("Conductor Damage Assessment", conductor_damage_var),
        ("Protection Coordination Assessment", coordination_var),
        ("Protection Relay Coordination Plot", coordination_plot_var),
    ]
    for row, (text, variable) in enumerate(option_rows, start=6):
        checkbox = tk.Checkbutton(
            root, text=text, variable=variable, state='disabled'
        )
        checkbox.grid(row=row, column=0, sticky="w", padx=50, pady=2)
        option_boxes.append((checkbox, variable))

    tk.Radiobutton(
        root,
        text="Update TOC Plot Curve Labels",
        value="5",
        variable=selection,
        command=lambda: on_radio_change()
    ).grid(row=9, column=0, sticky="w", padx=30, pady=5)

    def on_radio_change():
        """Enable/disable checkboxes based on radio button selection."""
        all_relays = selection.get() == _ALL_RELAYS_VALUE
        for checkbox, variable in option_boxes:
            if all_relays:
                checkbox.config(state='normal')
            else:
                variable.set(False)
                checkbox.config(state='disabled')

    info_frame = ttk.LabelFrame(root, text="Information", padding=(10, 5))
    info_frame.grid(
        row=10, column=0, columnspan=3, sticky="ew", padx=10, pady=10
    )

    info_text = (
        "At script completion, the location of saved study results files\n"
        "will be displayed in the PowerFactory output window."
    )
    ttk.Label(info_frame, text=info_text, font=('Helvetica', 9)).pack(
        anchor="w"
    )

    ttk.Button(
        root, text='Okay', command=lambda: root.destroy()
    ).grid(row=11, column=0, sticky="w", padx=5, pady=5)

    ttk.Button(
        root, text='Exit', command=lambda: flag.cancel(root)
    ).grid(row=11, column=1, sticky="w", padx=5, pady=5)

    root.mainloop()
    flag.raise_if_cancelled()

    studies = {value: study for value, _, study in _STUDY_BUTTONS}
    studies["5"] = Study.CURVE_LABELS
    study = studies.get(selection.get())

    if study is None:
        app.PrintPlain("Please select a study to continue")
        return get_study_selection(app)

    if study is not Study.ALL_RELAYS:
        return StudySelection(study)

    return StudySelection(
        study,
        conductor_damage=conductor_damage_var.get(),
        coordination=coordination_var.get(),
        coordination_plot=coordination_plot_var.get(),
    )
