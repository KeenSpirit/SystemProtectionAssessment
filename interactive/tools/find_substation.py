"""
Substation-to-project lookup utility for PowerFactory.

Finds which PowerFactory master project contains a given substation,
based on its acronym, by searching the substation study cases of every
master project in the selected region.

Supported Regions:
    - SEQ: South East Queensland models
    - Regional North: Northern regional models
    - Regional South: Southern regional models

Functions:
    run: Entry point - prompt, search and report.
    sub_selection: Dialog for region and substation input.
    all_substations: Map each master project to its substations.
    find_project: Find the project containing a substation.
"""

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from pf_config import pft
from interactive.dialogs import CancelFlag, UserCancelled

# Region choice in the dialog -> master project folder in the database.
MASTER_PROJECT_FOLDERS = {
    "SEQ": "Publisher\\MasterProjects\\SEQ Models",
    "Regional North": "Publisher\\MasterProjects\\Regional Models\\Northern",
    "Regional South": "Publisher\\MasterProjects\\Regional Models\\Southern",
}


def run(app: pft.Application) -> None:
    """
    Find the PowerFactory project containing a given substation.

    Displays the input dialog, searches the project database and
    prints the result to the PowerFactory output window.

    Args:
        app: PowerFactory application instance.

    Example:
        >>> run(app)
        Substation ABC belongs to PowerFactory project Abermain.
    """
    try:
        region, substation = sub_selection(app)
    except UserCancelled:
        app.PrintPlain("User terminated script.")
        return

    projects_subs = all_substations(app, region)
    if projects_subs is None:
        return

    project = find_project(projects_subs, substation)

    if project is not None:
        app.PrintPlain(
            f"Substation {substation} belongs to PowerFactory "
            f"project {project}."
        )
    else:
        app.PrintPlain(
            f"A matching project for substation {substation} "
            f"could not be found."
        )


def sub_selection(app: pft.Application) -> Tuple[str, str]:
    """
    Display the dialog for region and substation input.

    Re-prompts until the input passes validation.

    Args:
        app: PowerFactory application instance.

    Returns:
        Tuple of (region, substation acronym in upper case).

    Raises:
        UserCancelled: If the user presses Exit.

    Validation Rules:
        - Substation must be non-empty
        - Only alphabetical characters allowed
        - SEQ: Maximum 3 characters
        - Regional: Maximum 4 characters
    """
    root = tk.Tk()
    root.title("Find Project")
    root.geometry("+800+200")
    flag = CancelFlag()

    # Region selection
    ttk.Label(
        root,
        text="Select Region:",
        font='Helvetica 14 bold'
    ).grid(row=0, column=0, columnspan=3, sticky="w", padx=5, pady=5)

    selection = tk.StringVar(value="0")

    ttk.Radiobutton(
        root, text="SEQ", variable=selection, value="0"
    ).grid(row=1, column=0, sticky="w", padx=5, pady=2)

    ttk.Radiobutton(
        root, text="Regional Northern", variable=selection, value="1"
    ).grid(row=2, column=0, sticky="w", padx=5, pady=2)

    ttk.Radiobutton(
        root, text="Regional Southern", variable=selection, value="2"
    ).grid(row=3, column=0, sticky="w", padx=5, pady=2)

    # Substation entry
    name_var = tk.StringVar()
    ttk.Label(
        root,
        text="Enter the Substation Acronym:",
        font='Helvetica 14 bold'
    ).grid(row=4, columnspan=3, sticky="w", padx=5, pady=(15, 5))

    ttk.Entry(
        root, textvariable=name_var, font=('calibre', 10, 'normal')
    ).grid(row=5, columnspan=3, sticky="w", padx=5, pady=5)

    # Input criteria frame
    criteria_frame = ttk.LabelFrame(
        root, text="Input Criteria", padding=(10, 5)
    )
    criteria_frame.grid(
        row=6, column=0, columnspan=3, sticky="ew", padx=5, pady=10
    )

    criteria_text = [
        "• The substation must have a PowerFactory study case assigned",
        "• Only alphabetical characters are allowed (case insensitive)",
        "• The input for SEQ must be three characters long",
        "• The input for Regional must be four characters long"
    ]

    for i, text in enumerate(criteria_text):
        ttk.Label(
            criteria_frame, text=text, font=('calibre', 10, 'normal')
        ).grid(row=i, column=0, sticky="w", pady=2)

    ttk.Label(
        root,
        text="The result will be displayed in the Output Window.",
        font=('calibre', 10, 'normal')
    ).grid(row=7, column=0, columnspan=3, sticky="w", padx=5, pady=5)

    # Buttons
    ttk.Button(
        root, text='Okay', command=lambda: root.destroy()
    ).grid(row=8, column=0, sticky="w", padx=5, pady=5)

    ttk.Button(
        root, text='Exit', command=lambda: flag.cancel(root)
    ).grid(row=8, column=1, sticky="w", padx=5, pady=5)

    root.mainloop()
    flag.raise_if_cancelled()

    # Map selection to region string
    region_mapping = {
        "0": "SEQ",
        "1": "Regional North",
        "2": "Regional South"
    }
    region = region_mapping[selection.get()]

    substation = name_var.get()

    # Validate input
    if len(substation) < 1:
        return error_message(app, 2)

    if not substation.isalpha():
        return error_message(app, 3)

    is_seq = region == "SEQ"
    is_regional = region in ("Regional North", "Regional South")

    if (is_seq and len(substation) > 3) or (
            is_regional and len(substation) > 4):
        return error_message(app, 1)

    return region, substation.upper()


def all_substations(
    app: pft.Application,
    region: str
) -> Optional[Dict[str, List[str]]]:
    """
    Map each master project in a region to its substation acronyms.

    Study case names are expected to have the substation acronym as
    the first space-separated token.

    Args:
        app: PowerFactory application instance.
        region: 'SEQ', 'Regional North' or 'Regional South'.

    Returns:
        Dict of project name to substation acronyms, e.g.
        {"Abermain": ["ABY", "BDB"]}, or None if the region's master
        project folder cannot be found (reported to the output
        window).
    """
    projects_subs = {}

    user = app.GetCurrentUser()
    database = user.GetAttribute('fold_id')

    app.PrintPlain(
        f"Obtaining a list of all {region} Master Project models..."
    )

    folders = database.GetContents(MASTER_PROJECT_FOLDERS[region])
    if not folders:
        app.PrintPlain(
            f"Could not obtain a list of all {region} Master Project models."
        )
        return None
    models = folders[0]

    projects = models.GetContents("*.IntPrj")

    # Extract substations from each project's study cases
    for project in projects:
        study_cases = project.GetContents("Study Cases")
        intcases = []

        for study_case in study_cases:
            sub_study_cases = study_case.GetContents("Substation Study Cases")

            for sub_study_case in sub_study_cases:
                intcases.extend([
                    intcase.GetAttribute('loc_name').split()[0]
                    for intcase in sub_study_case.GetContents("*.IntCase")
                ])

        projects_subs[project.GetAttribute('loc_name')] = intcases

    return projects_subs


def find_project(
    projects_subs: Dict[str, List[str]],
    substation: str
) -> Optional[str]:
    """
    Find the project containing a given substation acronym.

    Args:
        projects_subs: Project name to substation acronyms.
        substation: Substation acronym (case insensitive).

    Returns:
        Project name if found, None otherwise.

    Example:
        >>> find_project({"Abermain": ["ABY"]}, "aby")
        'Abermain'
    """
    substation = substation.upper()

    for project, sub_list in projects_subs.items():
        if substation in sub_list:
            return project

    return None


def error_message(app: pft.Application, error_code: int) -> Tuple[str, str]:
    """
    Report an input error and re-prompt.

    Args:
        app: PowerFactory application instance.
        error_code: 1 = input too long, 2 = empty input,
            3 = non-alphabetical characters.

    Returns:
        Tuple of (region, substation) from the re-prompted dialog.

    Raises:
        UserCancelled: If the user presses Exit on the re-prompt.
    """
    if error_code == 1:
        app.PrintPlain(
            "Please input 3 characters or less for SEQ, "
            "or 4 characters or less for Regional"
        )
    elif error_code == 2:
        app.PrintPlain("Please input a substation acronym to proceed")
    else:
        app.PrintPlain("Please input alphabetical characters only")

    return sub_selection(app)
