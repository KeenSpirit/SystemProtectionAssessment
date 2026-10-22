"""
TOC plot curve label formatting.

Rewrites the local format (IntForm) under every curve label (VisLabel)
on every time-overcurrent plot in the active project, so each label
shows the device's settings in a standard layout.

Relay label format:
    relay name
    CT ratio

    ANSI name
    Curve name
    Current setting
    Time Dial

    ANSI name
    Pickup Current
    Time Setting

Fuse label format:
    fuse name
    fuse type

Functions:
    run: Entry point - update labels in the active project.
    update_curve_labels: Update labels in a given project.
    get_curve_labels: Label variables for one curve label.
    toc_label: Append the inverse time element lines.
    ioc_label: Append the definite time element lines.
"""

from typing import List, Tuple

from pf_config import pft

# (variables, decimal places, show unit) - parallel lists, one entry
# per label line, as IntForm's cvariables/cdecplaces/cshowunit expect.
LabelFormat = Tuple[List[str], List[int], List[int]]


def run(app: pft.Application) -> None:
    """
    Update the TOC plot curve labels in the active project.

    Args:
        app: PowerFactory application instance.
    """
    project = app.GetActiveProject()
    if project is None:
        app.PrintWarn("No Active Project, Ending Script")
        return
    update_curve_labels(app, project)


def update_curve_labels(app: pft.Application, project) -> None:
    """
    Update every TOC plot curve label in a project.

    Args:
        app: PowerFactory application instance.
        project: PowerFactory project (IntPrj) to search.

    Side Effects:
        Sets cvariables, cdecplaces and cshowunit on each label's
        local IntForm. Warns for plots without labels and labels
        without a local format.
    """
    linebarplots = project.GetContents(
        '*.PltLinebarplot', 1
    )
    if not linebarplots:
        app.PrintWarn("No TOC plots were found in the active project")
        return

    for linebarplot in linebarplots:
        vislabels = linebarplot.GetContents('*.VisLabel')

        if not vislabels:
            app.PrintWarn(
                f"No curve labels were found in project {linebarplot} plot"
            )
            continue

        for vislabel in vislabels:
            variables, decimals, show_unit = get_curve_labels(vislabel)
            int_forms = vislabel.GetContents('*.IntForm', 1)

            if not int_forms:
                app.PrintWarn(
                    f"A local format was not found under label {vislabel}"
                )
                app.PrintWarn(
                    f"Please create a local format under {vislabel} and "
                    f"try again"
                )
                continue

            for int_form in int_forms:
                int_form.SetAttribute("cvariables", variables)
                int_form.SetAttribute("cdecplaces", decimals)
                int_form.SetAttribute("cshowunit", show_unit)


def get_curve_labels(vislabel) -> LabelFormat:
    """
    Build the label format for one curve label.

    Args:
        vislabel: PowerFactory VisLabel whose pShown is a fuse or a
            relay protection element.

    Returns:
        Tuple of (variables, decimal places, show unit) lists.
    """
    variables = []
    decimals = []
    show_unit = []

    phase_elements = ["I>t", "I>>t"]
    earth_elements = ["IE>t", "IE>>"]
    nps_elements = ["I2>t", "I2>>"]

    element = vislabel.GetAttribute("pShown")
    if element.GetClassName() == "RelFuse":
        name = 'e:loc_name'
        fuse = 'r:typ_id:e:loc_name'
        variables.extend([name, fuse])
        decimals.extend([0, 0])
        show_unit.extend([0, 0])
        return variables, decimals, show_unit
    element_type = element.GetAttribute("r:typ_id:e:sfiec")
    if element_type in phase_elements:
        toc_element = "I>t"
        ioc_element = "I>>t"
    elif element_type in earth_elements:
        toc_element = "IE>t"
        ioc_element = "IE>>"
    elif element_type in nps_elements:
        toc_element = "I2>t"
        ioc_element = "I2>>"
    else:
        toc_element = None
        ioc_element = None

    parent_relay = element.GetParent()
    blocks = parent_relay.GetAttribute("pdiselm")

    # Build variables
    if blocks[0].GetClassName() == 'StaCt':
        name = 'r:fold_id:loc_name'
        ct = 'r:fold_id:r:pdiselm:0:e:cratio_ct'
        variables.extend([name, ct])
        decimals.extend([0, 0])
        show_unit.extend([0, 0])
    else:
        name = 'r:fold_id:r:fold_id:loc_name'
        ct = 'r:fold_id:r:fold_id:r:pdiselm:0:e:cratio_ct'
        variables.extend([name, ct])
        decimals.extend([0, 0])
        show_unit.extend([0, 0])
    for i, block in enumerate(blocks):
        if block.IsOutOfService():
            continue
        if (block.GetClassName() == 'RelToc'
                and block.GetAttribute("r:typ_id:e:sfiec") == toc_element):
            variables, decimals, show_unit = toc_label(
                i, variables, decimals, show_unit
            )
        if (block.GetClassName() == 'RelIoc'
                and block.GetAttribute("r:typ_id:e:sfiec") == ioc_element):
            variables, decimals, show_unit = ioc_label(
                i, variables, decimals, show_unit
            )
    return variables, decimals, show_unit


def toc_label(
    i: int,
    variables: List[str],
    decimals: List[int],
    show_unit: List[int]
) -> LabelFormat:
    """
    Append the inverse time (TOC) element lines to a label format.

    Args:
        i: Index of the element in the relay's pdiselm list.
        variables: Variable list to extend.
        decimals: Decimal place list to extend.
        show_unit: Show-unit list to extend.

    Returns:
        The extended (variables, decimals, show_unit) lists.
    """
    ansi_name = f'r:fold_id:r:pdiselm:{i}:e:c_sfansi'
    curve_name = f'r:fold_id:r:pdiselm:{i}:r:pcharac:e:loc_name'
    current_setting = f'r:fold_id:r:pdiselm:{i}:e:cpIpset'
    time_dial = f'r:fold_id:r:pdiselm:{i}:e:Tpset'
    variables.extend(['', ansi_name, curve_name, current_setting, time_dial])
    decimals.extend([0, 0, 0, 0, 3])
    show_unit.extend([0, 0, 0, 1, 1])
    return variables, decimals, show_unit


def ioc_label(
    i: int,
    variables: List[str],
    decimals: List[int],
    show_unit: List[int]
) -> LabelFormat:
    """
    Append the definite time (IOC) element lines to a label format.

    Args:
        i: Index of the element in the relay's pdiselm list.
        variables: Variable list to extend.
        decimals: Decimal place list to extend.
        show_unit: Show-unit list to extend.

    Returns:
        The extended (variables, decimals, show_unit) lists.
    """
    ansi_name = f'r:fold_id:r:pdiselm:{i}:e:c_sfansi'
    pickup_current = f'r:fold_id:r:pdiselm:{i}:e:cpIpset'
    time_setting = f'r:fold_id:r:pdiselm:{i}:e:Tset'
    variables.extend(['', ansi_name, pickup_current, time_setting])
    decimals.extend([0, 0, 0, 2])
    show_unit.extend([0, 0, 1, 1])
    return variables, decimals, show_unit
