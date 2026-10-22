# SystemProtectionAssessment

Automated fault level studies, protection reach, conductor damage and
coordination assessments for DIgSILENT PowerFactory distribution
network models (Energex / Ergon).

This repository now holds both ways of running the assessment:

* **Batch** — the final stage of the IPStoPFMastering pipeline,
  assessing every radial feeder in each of ~80 projects, headless.
* **Interactive** — run from within PowerFactory by a user, who
  picks a study or tool and then the feeders and devices to assess.

```
IPStoPFMastering (ips_to_pf_mastering.py)
    └── batch_relay_update.py          per-project orchestration
            ├── IPStoPF                IPS settings -> PF relay models
            └── SystemProtectionAssessment (this repo)
                    └── start.begin(app)         batch entry point

PowerFactory script object -> start.py __main__
                    └── start.main(app)          interactive entry point
```

Both entry points drive the same GUI-free core (`pipeline/` and the
analysis packages), so a feeder assessed interactively and in batch
gets the same results. Subtransmission models are out of scope
(distribution feeders only).

---

## What it does

For each radial feeder assessed:

1. **Fault level study** — sequence impedances from project-wide
   short-circuit runs (maximum, minimum, system normal minimum), from
   which 3-phase, 2-phase and earth fault levels (with 0, 10 and 50 Ω
   fault resistance) are calculated at every terminal in each
   protection device's section, including floating terminals at
   network endpoints.
2. **Reach factors** — primary, backup and system normal backup reach
   of every device, at its section's terminals and lines.
3. **Conductor damage assessment** — let-through energy (I²t)
   accumulated across the full auto-reclose sequence for every line
   section, compared against conductor thermal withstand.
4. **Protection coordination assessment** — grading margins between
   each device and its backup across the fault level range.
5. **Outputs** — the results workbook; in batch, dashboard fact CSVs;
   interactively, optional diagram colour maps, coordination plots,
   or the legacy SEQ workbook.

All pass/fail thresholds and verdict rules live in `criteria.py`, so
the workbook, dashboard and colour maps always agree.

---

## Entry paths

### 1. Batch: `start.begin(app)`, called from batch_relay_update

```python
with helper.app_manager(app, gui=False) as app:
    summary = start.begin(app, output_dir=ASSESSMENT_OUTPUT_DIR)
```

* `helper.app_manager` (pf_protection_helper) provides the validated
  context: ResetCalculation, echo off, GUI updates off, user-break
  handling, cleanup on exit.
* Always assesses under "All Active Grids Study Case", all radial
  feeders with at least one protection device, with conductor damage
  and coordination. External grid impedances come from the
  `source_z_data` workbooks.
* `begin()` returns a summary dict:
  `{project, region, radial_feeders_detected, feeders_assessed,
  output_file, dashboard}`.
* Per-project failures raise `start.AssessmentError` (re-exported from
  `pipeline.errors`). The batch layer records these and continues
  with the next project.
* No GUI code is imported or executed on this path.

### 2. Interactive: `start.main(app)`, the `__main__` block

Run start.py from a PowerFactory script object. The first dialog
offers:

| Selection | What runs |
|---|---|
| Find PowerFactory Project | Finds the master project for a substation acronym |
| Find Feeder Open Points | Lists normally-open points on selected feeders |
| Fault Level Study (SEQ legacy script) | Switch selection, fault study, legacy SEQ workbook |
| Fault Level Study (No relays configured) | Switch selection, fault study, results workbook |
| Fault Level Study (All relays configured) | Device selection, fault study, colour maps, results workbook; optionally conductor damage, coordination assessment, coordination plots |
| Update TOC Plot Curve Labels | Rewrites curve label formats on every TOC plot |

For the fault level studies:

* **Study case** — the user's active study case is kept; if none is
  active, "All Active Grids Study Case" is activated (as in batch).
  Before the study the all-grids case is briefly activated and the
  user's case restored, which loads floating terminals.
* **Grid data** — entered in the feeder dialog, pre-filled from the
  model and, for the system normal minimum, the master project. The
  values entered are written to the external grids.
* **Messages** — core logging is routed to the PowerFactory output
  window for the run (`interactive/pf_output.py`), so warnings such
  as a feeder excluded after a failure are visible.
* **Exit** in any dialog ends the run cleanly ("User terminated
  script."). After the workbook is saved, cancelling the RMU fuse
  dialog only skips the remaining plots.

---

## Repository layout

```
start.py                 Entry points: begin() (batch), main()
                         (interactive), setup_stdout_logging.
criteria.py              Every pass/fail rule and regional assumption:
                         region tests, earth fault resistance, reach
                         thresholds, coordination margins, conductor
                         damage verdict. Pure; testable offline.
pipeline/                GUI-free core shared by both entry points.
    errors.py            AssessmentError.
    study_case.py        Study case activation policy; all-grids cycle.
    discovery.py         Radial/mesh feeders, devices, switches,
                         external grids, PF objects -> dataclasses.
    run.py               AssessmentOptions and run_assessment: open
                         points, fault study, then per-feeder stages
                         with failure containment.
assets/                  Dataclasses (Feeder, Device, Termination, Line,
                         Tfmr) and initialisers; analytical fault level
                         calculation from sequence impedances. See
                         "Data Pipeline.md" for the population
                         lifecycle.
fault_study/             Short-circuit runs, impedance extraction,
                         floating terminals, fault impedance selection,
                         device and line fault level summaries.
relays/                  Relay discovery, protection elements, reclose
                         state, current conversion, trip times, reach
                         factors.
devices/                 Fuse discovery and fuse selection tables.
cond_damage/             I²t across the auto-reclose sequence.
prot_coordination/       Grading margin assessment.
save_results/            Results workbook, conductor damage sheet,
                         dashboard facts and CSV writer.
source_z_data/           External grid source impedance workbooks
                         (batch grid data) and their build tools.
fdr_open_points/         Open point detection.
interactive/             Everything with a dialog. Never imported by
                         the batch path.
    study_selection.py   First dialog.
    study_inputs.py      Feeder, grid data and device dialogs.
    pf_output.py         Logging -> PowerFactory output window.
    dialogs.py           UserCancelled / CancelFlag for Exit buttons.
    colour_maps.py       Diagram colouring schemes.
    oc_plots/            Time-overcurrent coordination plots and the
                         RMU fuse specification dialog.
    legacy_script/       Legacy SEQ workbook (to be retired).
    tools/               Project finder, open point viewer, relay
                         model checks, TOC curve labels.
config_logging/          Path utilities for the standalone log file.
ruff.toml                Lint rules, including the tkinter ban.
```

External dependency: `pf_protection_helper` (shared with IPStoPF)
for `app_manager`, `obtain_region`, temporary variations.

---

## Execution flow

### Batch

```
begin(app, output_dir, dashboard_dir, ...)
  ├─ setup_stdout_logging()
  ├─ activate_study_case(app)                 All Active Grids (AssessmentError if absent)
  ├─ obtain_region(app)                       'SEQ' | 'Northern' | 'Southern'
  ├─ discovery.mesh_feeder_check(app)         radial feeders only
  ├─ discovery.get_feeders_devices(...)       device -> feeder/grid map
  ├─ discovery.chk_empty_fdrs(...)            drop empty; raise if all empty
  ├─ discovery.get_grid_data(grids, region)   source z-data workbooks
  ├─ discovery.cvrt_fdr_to_dataclass(...)     PF objects -> dataclasses
  ├─ run_assessment(...)                      see below
  ├─ save_dataframe(...)                      workbook; returns filepath
  └─ write_dashboard_facts(...)               if dashboard_dir given
```

### Interactive fault level study

```
main(app)
  └─ get_study_selection -> activate_study_case(keep_active=True)
     -> model_checks -> study_inputs.get_input (feeders, grid data,
     devices or switches) -> cvrt_fdr_to_dataclass -> cycle_all_grids
     -> run_assessment
       ├─ legacy:      legacy SEQ workbook, then stop
       └─ otherwise:   colour maps (all relays) -> save_dataframe
                       -> coordination plots (if selected)
```

### run_assessment (shared)

```
  ├─ for each feeder: get_open_points
  ├─ fault_study(all feeders)                 one set of short circuits
  └─ for each feeder (a failure excludes that feeder only):
        populate_reach_factors, populate_line_reach_factors
        -> cond_damage (selected devices) -> prot_coordination
```

Each stage logs an INFO milestone; per-feeder stages log
`[i/N] <feeder>: <stage>`.

---

## Key invariants (read before modifying)

These encode failures we have already had once. Keep them true.

* **Both entry points run inside `app_manager`.** ResetCalculation,
  echo and GUI-update state are only guaranteed there.
* **No tkinter outside `interactive/`.** The batch path must never
  block on a dialog. `ruff check` enforces it (TID251); `start.main()`
  imports the interactive package inside the function for the same
  reason.
* **Never call `exit()` / `sys.exit()` in library code.** It kills the
  entire batch, or the whole interactive run. Per-project failures
  raise `AssessmentError`; dialog Exit buttons raise `UserCancelled`
  through `CancelFlag` (tkinter swallows exceptions raised directly
  inside a button callback). `sys.exit` is acceptable only inside
  `if __name__ == "__main__"`.
* **Pass/fail rules live in `criteria.py` only.** Compare regions with
  `criteria.is_seq` / `is_regional`, never string literals: a renamed
  region must fail loudly, not silently take an else branch.
* **Outputs render; they do not calculate.** The workbook, dashboard,
  colour maps and plots read values already on the dataclasses
  (e.g. `Line.reach_factors`), never recalculate them.
* **Model mutations must be restored in `finally`.** PowerFactory
  persists `SetAttribute` immediately. Current mutate/restore pairs:
  `reset_min_source_imp` (grid impedances), Regional LV terminals out
  of service during the short circuits (fault_level_study),
  `set_enabled_elements` / `reset_block_service_status` (element
  outserv, reclose), recloser `starttimeframe` (`reset_reclosing`),
  the study case deactivated while plotting. Deliberate, persistent
  writes: batch source z-data and interactive grid data entry are
  written to the external grids.
* **Progress goes through `logging`, not `app.PrintPlain`.** PrintPlain
  is invisible in headless batch runs; interactively, logging reaches
  the output window through `PFOutputHandler`. Module pattern:
  `logger = logging.getLogger(__name__)`. New top-level package
  namespaces must be added to the level-pinning list in
  ips_to_pf_mastering.py.
* **Expensive PF topology walks are precomputed.** `GetContents` /
  `GetAll` never belong inside a per-device loop; build the set once
  (see `get_feeders_devices`, `get_device_sections`,
  `get_feeders_switches`).
* **Query functions do not mutate the model.** If a discovery/getter
  function needs a model change, that is a design smell — hoist it,
  make it explicit, and guard it.

---

## Output

### Results workbook

```
Fault Study Results {project name} {YYYYMMDD-HHMMSS}.xlsx
```

* Batch: written to `ASSESSMENT_OUTPUT_DIR` (batch_relay_update);
  directory is created if absent. The path is logged and returned in
  the `begin()` summary.
* Interactive / `output_dir=None`: per-user path probe —
  `//client/c$/LocalData/{user}` (Citrix) falling back to
  `c:/LocalData/{user}`. The path is shown in the output window.

The study case name is recorded on the General Information sheet, not
in the filename.

### Batch only

* Dashboard facts: `dashboard_data/runs/<run_id>/devices_<project>.csv`
  and `dashboard_data/latest_lines/<project>.csv` under the dashboard
  root (see `save_results/dashboard_writer.py`).

### Interactive only

* **Colour maps** (all relays study): per-feeder user-defined diagram
  colouring schemes for primary/backup reach and, if run, conductor
  damage, encoded in line `dpl1`–`dpl3`.
* **Coordination plots**: a timestamped folder of plot pages per
  feeder in the active project.
* **Legacy SEQ workbook**: `{substation} fault level study
  {timestamp}.xlsx`, saved to the SEQ protection drive, falling back
  to the user's home folder.

---

## Failure handling

| Event | Batch | Interactive |
|---|---|---|
| Missing "All Active Grids Study Case" (when needed) | `AssessmentError`; project recorded as skipped | Error in output window; run ends |
| No radial feeders / no protection devices | `AssessmentError`; as above | Notice; run ends |
| Feeder with no devices (others populated) | Warning; feeder excluded | As batch |
| Failure in a per-feeder stage | Traceback logged; feeder excluded; run continues | As batch; shown in output window |
| Failure in the fault study or save | Propagates; project recorded failed; `finally` guards restore model state | Traceback in output window |
| User presses Exit | n/a | "User terminated script."; nothing further runs |
| Plot failure for a feeder | n/a | Error shown; other feeders still plot; workbook already saved |

---

## Known limitations / parked work

* Mesh feeders are excluded from assessment (radial only).
* `min_fl_*` aggregations assume numeric fault levels; a `None` from
  result extraction on exotic topologies can surface as a TypeError
  (tracked; report with the traceback if seen).
* Clearing-time interpolation fetches curve data from the PF API per
  fault-level step; a caching refactor is planned if 80-project
  runtimes require it.
* `importlib.reload()` calls in several modules are artifacts of
  in-PowerFactory development, pending removal.
* The legacy SEQ study is carried until it is retired.

See `ASSUMPTIONS.md` for modelling assumptions and `CONTRIBUTING.md`
for code standards. For the live fix queue, see the maintainer's
notes / project tracker.