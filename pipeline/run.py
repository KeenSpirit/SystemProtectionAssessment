"""
Assessment stage runner shared by the batch and interactive paths.

run_assessment takes feeders already converted to assets dataclasses
and drives every analysis stage over them, in the order set out in
"Data Pipeline.md". It renders nothing: workbook, dashboard, colour
map and plot output are the caller's job, and all of them read the
values this module leaves on the dataclasses.

Classes:
    AssessmentOptions: Optional studies to run and the devices to run
        them on.

Functions:
    run_assessment: Open points, fault study, then the per-feeder
        stages with failure containment.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import powerfactory as pf

from pf_config import pft
import assets as ast
from fdr_open_points import get_open_points as gop
from fault_study import fault_level_study as fs
from cond_damage import conductor_damage as cd
from prot_coordination import prot_coord as pc
from relays.reach_factors import (populate_reach_factors,
                                  populate_line_reach_factors)

logger = logging.getLogger(__name__)

# Study selection strings still consumed by save_results. Defined once
# here so the options object can produce them; they retire when
# save_results takes AssessmentOptions directly.
FAULT_LEVEL_STUDY = "Fault Level Study (all relays configured in model)"
CONDUCTOR_DAMAGE = "Conductor Damage Assessment"
COORDINATION = "Protection Coordination Assessment"


@dataclass(frozen=True)
class AssessmentOptions:
    """Optional studies to run and the devices to run them on.

    Reach factors always run: they feed the Detailed Results sheet and
    bound the coordination sweep, whatever else is selected.

    Attributes:
        conductor_damage: Run the conductor damage assessment.
        coordination: Run the protection coordination assessment.
        selected: Feeder name to the PowerFactory device objects the
            user selected for conductor damage. None means every
            device on every feeder, which is the batch behaviour.
    """
    conductor_damage: bool = True
    coordination: bool = True
    selected: Optional[Dict[str, List[Any]]] = None

    @property
    def study_selections(self) -> List[str]:
        """Legacy study selection strings for save_results."""
        selections = [FAULT_LEVEL_STUDY]
        if self.conductor_damage:
            selections.append(CONDUCTOR_DAMAGE)
        if self.coordination:
            selections.append(COORDINATION)
        return selections

    def devices_for(self, feeder: ast.Feeder) -> List[ast.Device]:
        """
        Get the devices on a feeder that the optional studies cover.

        Args:
            feeder: Feeder dataclass with devices populated.

        Returns:
            Every device when no selection was made; otherwise the
            devices whose PowerFactory object the user selected on
            this feeder (none if the feeder has no selection entry).
        """
        if self.selected is None:
            return list(feeder.devices)
        chosen = self.selected.get(feeder.obj.loc_name, [])
        return [
            device for device in feeder.devices if device.obj in chosen
        ]


def run_assessment(
    app: pft.Application,
    region: str,
    feeders: List[ast.Feeder],
    external_grid: Dict,
    options: AssessmentOptions,
) -> List[ast.Feeder]:
    """
    Run every analysis stage over the given feeders.

    The fault study runs once across all feeders; a failure there
    propagates, because no feeder has usable results without it. The
    per-feeder stages after it are contained: one bad device or
    element costs its feeder, not the project.

    Args:
        app: PowerFactory application instance.
        region: Network region ('SEQ', 'Northern', 'Southern').
        feeders: Feeder dataclasses from cvrt_fdr_to_dataclass.
        external_grid: Grid object to 15-value fault level data list.
        options: Optional studies to run and their device selection.

    Returns:
        The feeders that completed every stage, in input order. Failed
        feeders are logged with their traceback and left out.

    Raises:
        pf.ExitError: If the user breaks the run in PowerFactory.
    """
    for i, feeder in enumerate(feeders, start=1):
        name = getattr(feeder.obj, "loc_name", str(feeder.obj))

        logger.info(f"[{i}/{len(feeders)}] {name}: open points")
        gop.get_open_points(app, feeder)

    logger.info("All feeders fault study")
    fs.fault_study(
        app, external_grid, region, feeders
    )

    # One bad device or element should cost its feeder, not the project:
    # a failure in these per-feeder stages is logged with its traceback,
    # the feeder is left out of the results, and the rest carry on.
    failed_feeders = []
    for i, feeder in enumerate(feeders, start=1):
        name = getattr(feeder.obj, "loc_name", str(feeder.obj))
        stage = "reach factors"
        try:
            # Reach factors feed both the Detailed Results sheet and the
            # coordination sweep bounds, so this stage runs for every
            # assessment regardless of which studies were selected.
            logger.info(f"[{i}/{len(feeders)}] {name}: reach factors")
            populate_reach_factors(region, feeder.devices)
            populate_line_reach_factors(region, feeder.devices)

            if options.conductor_damage:
                stage = "conductor damage"
                selected_devices = options.devices_for(feeder)
                logger.info(f"[{i}/{len(feeders)}] {name}: conductor damage")
                cd.cond_damage(app, selected_devices)

            if options.coordination:
                stage = "protection coordination"
                logger.info(
                    f"[{i}/{len(feeders)}] {name}: protection coordination"
                )
                pc.prot_coordination(app, feeder.devices)
        except pf.ExitError:
            raise
        except Exception:
            logger.exception(
                f"[{i}/{len(feeders)}] {name}: {stage} failed; feeder "
                f"excluded from the results"
            )
            failed_feeders.append(feeder)

    if failed_feeders:
        failed_ids = {id(f) for f in failed_feeders}
        feeders = [f for f in feeders if id(f) not in failed_ids]
        logger.warning(
            f"{len(failed_feeders)} feeder(s) excluded after a failure: "
            f"{[getattr(f.obj, 'loc_name', str(f.obj)) for f in failed_feeders]}"
        )

    return feeders