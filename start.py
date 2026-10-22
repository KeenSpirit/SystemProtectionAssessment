"""
Entry points for the System Protection Assessment.

begin() is the headless batch entry point, called per project by
batch_relay_update inside helper.app_manager. The __main__ block runs
the same path standalone from within PowerFactory.

All discovery and analysis lives in the GUI-free pipeline package;
this module only wires the stages together and writes the outputs.

Functions:
    setup_stdout_logging: Ensure progress logging reaches stdout.
    begin: Assess every radial feeder in the active project.
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
from pipeline.study_case import activate_study_case

logger = logging.getLogger(__name__)

# Re-exported so the batch layer's `except start.AssessmentError`
# handling is unaffected by the move to pipeline.errors.
__all__ = ["AssessmentError", "begin", "setup_stdout_logging"]


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
        begin(app)

    end = time.time()
    run_time = round(end - start, 6)
    app.PrintPlain(f"Script run time: {run_time} seconds")