"""
Study case activation policy for the assessment entry paths.

Batch runs always assess under "All Active Grids Study Case". The
interactive path keeps whichever study case the user has selected,
and falls back to the batch behaviour when none is selected.

Functions:
    activate_study_case: Apply the policy and return the active case.
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
        study_folder = app.GetProjectFolder("study")
        if study_folder is None:
            raise AssessmentError("Project has no study case folder.")

        all_grids_cases = study_folder.GetContents(ALL_GRIDS_STUDY_CASE)
        if not all_grids_cases:
            raise AssessmentError(
                f"Project has no '{ALL_GRIDS_STUDY_CASE}' study case."
            )
        int_case = all_grids_cases[0]
        error_code = int_case.Activate()
        if error_code:
            raise AssessmentError(
                f"Could not activate '{ALL_GRIDS_STUDY_CASE}' "
                f"(Activate() returned {error_code})."
            )
    logger.info(f"Activated '{ALL_GRIDS_STUDY_CASE}'")

    return app.GetActiveStudyCase()