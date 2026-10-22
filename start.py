"""
Entry points for the System Protection Assessment.

begin() is the headless batch entry point, called per project by
batch_relay_update inside helper.app_manager. main() is the interactive
entry point, run from within PowerFactory by the __main__ block: the
user picks a study or tool, then the feeders, grid data and devices.

All discovery and analysis lives in the GUI-free pipeline package;
this module only wires the stages together and writes the outputs.
The interactive package (tkinter) is imported inside main() only, so
the batch path never loads it.

Functions:
    setup_stdout_logging: Ensure progress logging reaches stdout.
    begin: Assess every radial feeder in the active project.
    main: Interactive study selection and assessment.
"""

import time
import sys
import logging
import logging.config
from pathlib import Path
import powerfactory as pf
from config_logging import configure_logging as cl
from pf_config import pft
import pf_protection_helper as helper
from typing import Any, Dict, Optional
from save_results import save_result as sr
from save_results import dashboard_writer
from pipeline import discovery
from pipeline.errors import AssessmentError
from pipeline.run import AssessmentOptions, run_assessment
from pipeline.study_case import activate_study_case, cycle_all_grids

logger = logging.getLogger(__name__)

# Re-exported so the batch layer's `except start.AssessmentError`
# handling is unaffected by the move to pipeline.errors.
__all__ = ["AssessmentError", "begin", "main", "setup_stdout_logging"]


def setup_stdout_logging(level: int = logging.INFO) -> None:
    """Ensure assessment progress is visible on the console.

    Adds a stdout StreamHandler to the root logger if one is not already
    present. Idempotent and safe on both entry paths:
      * standalone (__main__): complements the file handler from basicConfig
      * imported (called from batch_relay_update): the mastering process has
        already attached a stdout handler, so this is a no-op.

    Only lowers the root level toward INFO if it is currently more
    restrictive; it never raises the level, so a DEBUG root configured by a
    host process is left untouched.
    """
    root = logging.getLogger()

    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    already = any(
        isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) is sys.stdout
        for h in root.handlers
    )
    if not already:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s: %(module)s: Line: %(lineno)d: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S%z",
            )
        )
        root.addHandler(handler)


def begin(
    app: pft.Application,
    output_dir: Optional[Path] = None,
    dashboard_dir: Optional[Path] = None,
    dashboard_run_id: Optional[str] = None,
    dashboard_project_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Assess every radial feeder in the active project.

    Must run inside helper.app_manager. Feeder selection is automatic:
    every radial feeder with at least one protection device.

    Args:
        app: PowerFactory application instance.
        output_dir: Workbook output directory. None uses the legacy
            per-user path probe.
        dashboard_dir: Dashboard fact output directory. None skips
            the dashboard output.
        dashboard_run_id: Batch run identifier for the dashboard
            facts. None uses a timestamp.
        dashboard_project_name: Region-qualified project name from the
            batch runner. None uses the plain project name.

    Returns:
        Summary dict: project, region, radial_feeders_detected,
        feeders_assessed, output_file and dashboard.

    Raises:
        AssessmentError: If the project cannot be assessed (missing
            study case, no radial feeders, no protection devices).
    """

    setup_stdout_logging()
    logger.info("System Protection Assessment started")

    activate_study_case(app)

    # Get region and user inputs
    region = helper.obtain_region(app)
    logger.info(f"Region: {region}")

    radial_list, mesh_feeder = discovery.mesh_feeder_check(app)
    logger.info(f"{len(radial_list)} radial feeders detected")
    if not radial_list:
        raise AssessmentError("No radial feeders detected in project.")

    feeders_devices, bu_devices = discovery.get_feeders_devices(
        app, radial_list
    )
    discovery.chk_empty_fdrs(app, feeders_devices)

    grids = discovery.get_active_grids(app)
    external_grid = discovery.get_grid_data(grids, region)

    # Convert to assets dataclasses
    feeders = discovery.cvrt_fdr_to_dataclass(
        app, feeders_devices, bu_devices
    )
    logger.info(f"{len(feeders)} feeders to assess")

    options = AssessmentOptions(conductor_damage=True, coordination=True)
    feeders = run_assessment(app, region, feeders, external_grid, options)

    logger.info("Saving results")
    output_file = sr.save_dataframe(
        app, region, options.study_selections, external_grid, feeders,
        output_dir=output_dir,
    )

    project_name = app.GetActiveProject().loc_name
    # The batch runner passes a region-qualified name when two projects
    # share one (#22); standalone runs use the plain project name.
    fact_project_name = dashboard_project_name or str(project_name)

    # Dashboard fact output A failed write is logged and reported as None,
    # so the batch manifest can distinguish "assessed, dashboard missing"
    # from "assessed".
    dashboard_result = None
    if dashboard_dir is not None:
        run_id = dashboard_run_id or time.strftime("%Y%m%d_%H%M%S")
        try:
            dashboard_result = dashboard_writer.write_dashboard_facts(
                dashboard_dir,
                run_id,
                fact_project_name,
                region,
                feeders,
            )
        except Exception:
            logger.exception(
                "Dashboard fact write failed for %s; workbook results "
                "are unaffected", fact_project_name
            )

    logger.info("System Protection Assessment complete")

    return {
        "project": project_name,
        "region": region,
        "radial_feeders_detected": len(radial_list),
        "feeders_assessed": len(feeders),
        "output_file": output_file,
        "dashboard": dashboard_result,
    }


def main(app: pft.Application) -> None:
    """
    Interactive entry point: run the study or tool the user selects.

    Must run inside helper.app_manager. Core progress and warnings are
    routed to the PowerFactory output window for the duration.

    Args:
        app: PowerFactory application instance.

    Side Effects:
        Depending on the selection: prints tool results, writes the
        user's grid data to the external grids, and saves the results
        workbook to the per-user output folder.
    """
    # Imported here so the batch path never imports tkinter.
    from interactive.dialogs import UserCancelled
    from interactive.pf_output import attached

    with attached(app):
        try:
            _run_selection(app)
        except UserCancelled:
            app.PrintPlain("User terminated script.")
        except AssessmentError as exc:
            app.PrintError(str(exc))


def _run_selection(app: pft.Application) -> None:
    """
    Show the study selection dialog and carry out the choice.

    Args:
        app: PowerFactory application instance.

    Raises:
        UserCancelled: If the user presses Exit in any dialog.
        AssessmentError: If the project cannot be assessed.
    """
    from interactive import study_inputs
    from interactive.study_selection import Study, get_study_selection
    from interactive.tools import (
        curve_labels, find_substation, model_checks, open_points
    )

    selection = get_study_selection(app)

    if selection.study is Study.FIND_PROJECT:
        find_substation.run(app)
        return

    project = app.GetActiveProject()
    if project is None:
        app.PrintWarn("No Active Project, Ending Script")
        return

    if selection.study is Study.OPEN_POINTS:
        open_points.run(app)
        return

    if selection.study is Study.CURVE_LABELS:
        curve_labels.update_curve_labels(app, project)
        return

    if selection.study is Study.LEGACY:
        app.PrintWarn(
            "The legacy fault level study has not been merged into this "
            "script yet. Please run it from protection_assessment."
        )
        return

    # Fault level study, with or without relays modelled
    study_case = activate_study_case(app, keep_active=True)
    model_checks.run(app)

    region = helper.obtain_region(app)
    inputs = study_inputs.get_input(app, region, selection.study)

    feeders = discovery.cvrt_fdr_to_dataclass(
        app, inputs.feeders_devices, inputs.bu_devices
    )
    cycle_all_grids(app, study_case)

    options = AssessmentOptions(
        conductor_damage=selection.conductor_damage,
        coordination=selection.coordination,
        selected=inputs.selected,
    )
    feeders = run_assessment(
        app, region, feeders, inputs.external_grid, options
    )

    if selection.coordination_plot:
        app.PrintWarn(
            "Protection relay coordination plots have not been merged into "
            "this script yet; none were drawn."
        )
    app.PrintWarn(
        "Colour maps have not been merged into this script yet; none were "
        "applied."
    )

    sr.save_dataframe(
        app, region, options.study_selections, inputs.external_grid,
        feeders,
    )


# =============================================================================
# SCRIPT EXECUTION
# =============================================================================

if __name__ == '__main__':
    start = time.time()


    # Configure logging
    logging.basicConfig(
        filename=cl.getpath() / 'prot_assess_log.txt',
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    app = pf.GetApplication()

    with helper.app_manager(app, gui=True) as app:
        main(app)

    end = time.time()
    run_time = round(end - start, 6)
    app.PrintPlain(f"Script run time: {run_time} seconds")