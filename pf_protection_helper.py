"""
PowerFactory application context managers and utilities.

This module provides context managers for managing PowerFactory application
state and utility functions used throughout the protection assessment
scripts.

Context Managers:
    app_manager: Manage PowerFactory application lifecycle
    project_manager: Create temporary library folders
    temporary_variation: Create and manage temporary network variations

Utility Functions:
    obtain_region: Determine network region from project path

Usage:
    from pf_protection_helper import app_manager, obtain_region

    with app_manager(app, gui=True) as app:
        region = obtain_region(app)
        # ... perform analysis ...
"""

import uuid
from contextlib import contextmanager
from typing import Generator

from pf_config import pft


__all__ = [
    'app_manager',
    'project_manager',
    'temporary_variation',
    'obtain_region',
]


# =============================================================================
# APPLICATION CONTEXT MANAGERS
# =============================================================================

@contextmanager
def app_manager(
    app: pft.Application,
    clear: bool = True,
    gui: bool = False,
    echo_on: bool = False,
    cache: bool = False
) -> Generator[pft.Application, None, None]:
    """
    Context manager for PowerFactory application lifecycle.

    Manages application state during script execution, handling output
    settings, GUI updates, caching, and cleanup on exit.

    Args:
        app: PowerFactory application instance.
        clear: If True, clear output window on entry. Default True.
        gui: If True, enable GUI updates during execution. Default False.
        echo_on: If True, enable full echo output. Default False.
        cache: If True, enable write cache. Default False.
            WARNING: Only use cache=True if you understand the impacts.

    Yields:
        The configured PowerFactory application instance.

    Side Effects:
        On entry:
            - Resets calculation state
            - Clears output window (if clear=True)
            - Configures echo settings
            - Sets GUI update and cache modes
            - Enables user break

        On exit:
            - Restores echo to full output
            - Re-enables GUI updates
            - Disables user break
            - Writes cached changes to database (if cache was enabled)
            - Clears recycle bin
            - Releases application reference

    Example:
        >>> with app_manager(app, gui=True) as app:
        ...     # GUI updates visible during this block
        ...     run_fault_study(app)
    """
    try:
        app.ResetCalculation()

        if clear:
            app.ClearOutputWindow()

        if echo_on:
            app.EchoOn()
        else:
            echo = app.GetFromStudyCase('ComEcho')
            echo.iopt_err = True
            echo.iopt_wrng = False
            echo.iopt_info = False
            echo.iopt_oth = True
            app.EchoOff()

        app.SetGuiUpdateEnabled(1 if gui else 0)
        app.SetWriteCacheEnabled(1 if cache else 0)
        app.SetUserBreakEnabled(1)

        yield app

    finally:
        app.EchoOn()
        app.SetGuiUpdateEnabled(1)
        app.SetUserBreakEnabled(0)

        if app.IsWriteCacheEnabled():
            app.WriteChangesToDb()
            app.SetWriteCacheEnabled(0)

        app.ClearRecycleBin()
        del app


@contextmanager
def project_manager(
    app: pft.Application
) -> Generator[pft.DataObject, None, None]:
    """
    Context manager for temporary library folder creation.

    Creates a temporary folder in the project's local library for
    storing temporary type objects. The folder is automatically
    deleted when the context exits.

    Args:
        app: PowerFactory application instance.

    Yields:
        IntFolder object in the local library for temporary types.

    Example:
        >>> with project_manager(app) as temp_lib:
        ...     # Create temporary fuse types in temp_lib
        ...     fuse_type = temp_lib.CreateObject('TypFuse', 'TempFuse')
        ...     # ... use fuse_type ...
        >>> # temp_lib and contents automatically deleted
    """
    temporary_library = None

    try:
        temporary_library = app.GetLocalLibrary().CreateObject(
            'IntFolder', 'Temp Types'
        )
        yield temporary_library

    finally:
        if temporary_library is not None:
            temporary_library.Delete()


@contextmanager
def temporary_variation(
    app: pft.Application
) -> Generator[pft.DataObject, None, None]:
    """
    Context manager for temporary network variation creation.

    Creates a temporary variation scheme for making reversible changes
    to network topology or parameters. The variation is automatically
    deactivated and deleted when the context exits.

    Args:
        app: PowerFactory application instance.

    Yields:
        IntScheme variation object that can be modified.

    Note:
        The variation name is a unique UUID to prevent conflicts.
        Changes made within the variation are isolated from the
        base network state.

    Example:
        >>> with temporary_variation(app) as variation:
        ...     # Modify network state within variation
        ...     switch.SetAttribute('on_off', 0)
        ...     run_contingency_analysis(app)
        >>> # Network restored to original state
    """
    variation_name = str(uuid.uuid1())
    variation_time = app.GetActiveStudyCase().GetAttribute('iStudyTime')
    net_dat = app.GetProjectFolder("netmod")
    variation_folder = net_dat.GetContents("Variations")[0]
    variation = None

    try:
        variation = variation_folder.CreateObject("IntScheme", variation_name)
        variation.Activate()
        variation.NewStage(variation_name, variation_time, 1)
        yield variation

    finally:
        if variation is not None:
            variation.Deactivate()
            variation.Delete()


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def obtain_region(app: pft.Application) -> str:
    """
    Determine the network region from the active project's base path.

    Examines the derived base project path to identify whether the
    current model is from SEQ or Regional Models.

    Args:
        app: PowerFactory application instance.

    Returns:
        Region identifier string:
            - 'SEQ' for South East Queensland models
            - 'Regional Models' for regional network models

    Raises:
        RuntimeError: If the region cannot be determined from the
            project path.

    Note:
        Region detection is based on path string matching:
            - Path containing 'Regional Models' -> 'Regional Models'
            - Path containing 'SEQ' -> 'SEQ'

    Example:
        >>> region = obtain_region(app)
        >>> if region == 'SEQ':
        ...     fault_resistance = 0
        >>> else:
        ...     fault_resistance = 50  # OH assumption
    """
    project = app.GetActiveProject()
    derived_proj = project.der_baseproject
    der_proj_name = derived_proj.GetFullName()

    if 'Regional Models' in der_proj_name:
        return 'Regional Models'

    if 'SEQ' in der_proj_name:
        return 'SEQ'

    msg = (
        "The appropriate region for the model could not be found. "
        "Please contact the script administrator to resolve this issue."
    )
    raise RuntimeError(msg)


def active_lines(app: pft.Application, reset: bool) -> list:
    """
    Return all the active lines in the project.
    """
    if reset:
        app.ResetCalculation()
    all_active_lines = []
    for grid in app.GetSummaryGrid().GetContents():
        all_active_lines += [
            line
            for line in grid.obj_id.GetContents("*.ElmLne")
            if "HV" in line.loc_name or "TR" in line.loc_name or "LN" in line.loc_name
            if not line.IsOutOfService()
            if line.IsEnergized()
        ]
    return all_active_lines


def create_obj(parent, obj_name: str, obj_class: str):

    obj = parent.GetContents(f"{obj_name}.{obj_class}")

    if not obj:
        obj = parent.CreateObject(obj_class, obj_name)
    else:
        obj = obj[0]
    return obj