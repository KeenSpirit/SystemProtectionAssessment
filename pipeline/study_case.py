"""
Study case activation policy for the assessment entry paths.

Batch runs always assess under "All Active Grids Study Case". The
interactive path keeps whichever study case the user has selected,
and falls back to the batch behaviour when none is selected.

Functions:
    activate_study_case: Apply the policy and return the active case.
    cycle_all_grids: Briefly activate the all-grids case, then return
        to the given case, so floating terminals load.
"""

import logging

from pf_config import pft
from pipeline.errors import AssessmentError

logger = logging.getLogger(__name__)

ALL_GRIDS_STUDY_CASE = "All Active Grids Study Case"


def activate_study_case(
    app: pft.Application,
    keep_active: bool = False,
) -> pft.IntCase:
    """
    Ensure a study case suitable for assessment is active.

    Args:
        app: PowerFactory application instance.
        keep_active: If True and a study case is already active, keep
            it (interactive policy). If False, or if no study case is
            active, activate "All Active Grids Study Case" (batch
            policy).

    Returns:
        The study case that is active when the function returns.

    Raises:
        AssessmentError: If "All Active Grids Study Case" is needed but
            the project has no study folder, the case does not exist,
            or it fails to activate.
    """
    active_study_case = app.GetActiveStudyCase()
    logger.info(f"study case: {active_study_case}")

    if keep_active and active_study_case is not None:
        logger.info(
            f"Keeping user-selected study case "
            f"'{active_study_case.loc_name}'"
        )
        return active_study_case

    if (active_study_case is None
            or active_study_case.loc_name != ALL_GRIDS_STUDY_CASE):
        _activate(_all_grids_case(app))
    logger.info(f"Activated '{ALL_GRIDS_STUDY_CASE}'")

    return app.GetActiveStudyCase()


def cycle_all_grids(app: pft.Application, study_case: pft.IntCase) -> None:
    """
    Activate the all-grids study case, then re-activate study_case.

    Run before the fault study on the interactive path, when the user's
    own study case is active: activating every grid loads the floating
    terminals into their line-connected elements, which the user's case
    alone may not. Does nothing if study_case is already the all-grids
    case.

    Args:
        app: PowerFactory application instance.
        study_case: The study case to finish on.

    Raises:
        AssessmentError: If the all-grids case is missing or either
            activation fails.
    """
    if study_case.loc_name == ALL_GRIDS_STUDY_CASE:
        return
    _activate(_all_grids_case(app))
    _activate(study_case)
    logger.info(
        f"Cycled '{ALL_GRIDS_STUDY_CASE}' to load floating terminals; "
        f"'{study_case.loc_name}' is active"
    )


def _all_grids_case(app: pft.Application) -> pft.IntCase:
    """
    Find the project's "All Active Grids Study Case".

    Args:
        app: PowerFactory application instance.

    Returns:
        The all-grids IntCase.

    Raises:
        AssessmentError: If the project has no study folder or no such
            case.
    """
    study_folder = app.GetProjectFolder("study")
    if study_folder is None:
        raise AssessmentError("Project has no study case folder.")

    all_grids_cases = study_folder.GetContents(ALL_GRIDS_STUDY_CASE)
    if not all_grids_cases:
        raise AssessmentError(
            f"Project has no '{ALL_GRIDS_STUDY_CASE}' study case."
        )
    return all_grids_cases[0]


def _activate(int_case: pft.IntCase) -> None:
    """
    Activate a study case.

    Args:
        int_case: The study case to activate.

    Raises:
        AssessmentError: If Activate() reports an error.
    """
    error_code = int_case.Activate()
    if error_code:
        raise AssessmentError(
            f"Could not activate '{int_case.loc_name}' "
            f"(Activate() returned {error_code})."
        )