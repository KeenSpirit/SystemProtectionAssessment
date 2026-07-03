# SystemProtectionAssessment

Automated fault level studies and conductor damage assessments for
DIgSILENT PowerFactory distribution network models (Energex / Ergon).

This repository is the final stage of the IPStoPFMastering pipeline:

```
IPStoPFMastering (ips_to_pf_mastering.py)
    └── batch_relay_update.py          per-project orchestration
            ├── IPStoPF                IPS settings -> PF relay models
            └── SystemProtectionAssessment (this repo)
                    └── start.begin(app)
```

It can also run standalone from within PowerFactory (see *Entry
paths* below). The batch path is the primary, supported path.

---

## What it does

For each radial feeder in the active project:

1. **Fault level study** — short-circuit calculations (max / min /
   system-normal-min; 3-phase, 2-phase, ground, ground+10R, ground+50R)
   at every terminal in each protection device's section, including
   floating terminals at network endpoints.
2. **Conductor damage assessment** — let-through energy (I2t)
   accumulated across the full auto-reclose sequence for every line
   section, compared against conductor thermal withstand.
3. **Excel output** — one workbook per project: General Information,
   Summary Results, per-feeder Detailed Results and Cond Dmg Res
   sheets.

Subtransmission models are out of scope (distribution feeders only).

---

## Entry paths

There are two ways in. They now share the same validated
initialisation; historically they did not, which caused divergent
behaviour — see *Key invariants*.

### 1. Batch (primary): called from batch_relay_update

```python
with helper.app_manager(app, gui=False) as app:
    summary = start.begin(app, output_dir=ASSESSMENT_OUTPUT_DIR)
```

* `helper.app_manager` (pf_protection_helper) provides the validated
  context: ResetCalculation, echo off, GUI updates off, user-break
  handling, cleanup on exit.
* `begin()` returns a summary dict:
  `{project, region, radial_feeders_detected, feeders_assessed,
  output_file}`.
* Per-project failures raise `start.AssessmentError` (typed, known
  reason — e.g. missing study case, no radial feeders, no protection
  devices). The batch layer records these in `failed_projects` and
  continues with the next project. Any other exception is caught by
  the batch layer's generic handler, logged with traceback, and also
  recorded.
* No GUI code executes on this path. Feeder selection is automatic:
  all radial feeders with at least one protection device.

### 2. Standalone: run inside PowerFactory

The `__main__` block in start.py runs the interactive flow, including
the tkinter feeder/open-point selection UI in `fdr_open_points/`.
This path is retained for engineers running ad-hoc assessments on a
single project. Do not add GUI dependencies anywhere reachable from
`begin()` — the batch path must never block on user input.

---

## Repository layout

```
start.py                 Entry point. begin(), study case activation,
                         feeder/device discovery, AssessmentError,
                         stdout logging setup.
fault_study/
    fault_level_study.py Orchestrates the study per feeder: topology,
                         short circuits, terminal/line fault levels,
                         SN-min impedance toggling, floating terminals.
    analysis.py          ComShc execution and result extraction
                         (get_terminal_current, get_line_current).
    fault_impedance.py   Regional fault impedance / construction logic.
    floating_terminals.py Endpoint terminals requiring line-location
                         faults.
    study_templates.py   Short-circuit command configuration.
cond_damage/
    conductor_damage.py  I2t energy across the auto-reclose sequence;
                         clearing time interpolation per fault level.
relays/
    elements.py          Relay discovery and protection elements.
    fuses.py             Fuse discovery.
    reclose.py           Auto-reclose sequence state: trip counter,
                         per-trip element enablement.
    current_conversion.py CT/primary-secondary conversions.
    reach_factors.py     Pickup and reach factor calculations for
                         reporting.
assets/                  Dataclasses (Feeder, Device, Termination,
                         Line, Load) and initialisers. See
                         DATA_PIPELINE.md for the population
                         lifecycle.
fdr_open_points/         Open point detection (batch) and the tkinter
                         selection UI (standalone only).
save_results/
    save_result.py       Workbook assembly, output path resolution.
    cond_dmg_results.py  Conductor damage sheet formatting; pass/fail
                         on accumulated energy vs thermal withstand.
config_logging/          Logging/path utilities (legacy; batch runs
                         use start.setup_stdout_logging).
```

External dependency: `pf_protection_helper` (shared with IPStoPF)
for `app_manager`, `obtain_region`, temporary variations.

---

## Execution flow (batch)

```
begin(app, output_dir)
  ├─ setup_stdout_logging()
  ├─ activate "All Active Grids Study Case"   (AssessmentError if absent)
  ├─ obtain_region(app)                       'SEQ' | 'Regional Models'
  ├─ mesh_feeder_check(app)                   radial feeders only
  ├─ get_feeders_devices(app, radial_list)    device -> feeder/grid map
  ├─ chk_empty_fdrs(...)                      drop empty; raise if all empty
  ├─ get_grid_data(app, grids)                source impedance table
  ├─ cvrt_fdr_to_dataclass(...)               PF objects -> dataclasses
  ├─ for each feeder:
  │     get_open_points -> fault_study -> cond_damage
  └─ save_dataframe(...)                      workbook; returns filepath
```

Each stage logs an INFO milestone; per-feeder stages log
`[i/N] <feeder>: <stage>`. In a healthy run the captured stdout
should never be silent for longer than one device's processing time.

---

## Key invariants (read before modifying)

These encode failures we have already had once. Keep them true.

* **`begin()` must run inside `app_manager`.** ResetCalculation, echo
  and GUI-update state are only guaranteed there. Calling `begin()`
  bare reproduces the historical batch/standalone divergence.
* **Never call `exit()` / `sys.exit()` in library code.** It kills the
  entire 80-project batch, not the current project. Per-project
  failures raise `AssessmentError`; unexpected errors just propagate.
  `sys.exit` is acceptable only inside `if __name__ == "__main__"`.
* **Model mutations must be restored in `finally`.** PowerFactory
  persists `SetAttribute` immediately — there is no pending save to
  discard on crash. Current mutate/restore pairs:
  `reset_min_source_imp` (grid impedances, fault_level_study),
  `set_enabled_elements` / `reset_block_service_status` (element
  outserv, reclose), recloser `starttimeframe`
  (reset via `reset_reclosing` after each device). Any new mutation
  follows the same pattern — or better, uses a temporary variation.
* **Progress goes through `logging`, not `app.PrintPlain`.** PrintPlain
  is invisible in headless batch runs. Module pattern:
  `logger = logging.getLogger(__name__)`. New top-level package
  namespaces must be added to the level-pinning list in
  ips_to_pf_mastering.py.
* **Expensive PF topology walks are precomputed.** `GetContents` /
  `GetAll` never belong inside a per-device loop; build the set once
  (see `get_feeders_devices`, `get_device_sections`).
* **Query functions do not mutate the model.** If a discovery/getter
  function needs a model change, that is a design smell — hoist it,
  make it explicit, and guard it.

---

## Output

One workbook per project:

```
Fault Study Results {project name} {YYYYMMDD-HHMMSS}.xlsx
```

* Batch: written to `ASSESSMENT_OUTPUT_DIR` (batch_relay_update);
  directory is created if absent. The path of every written file is
  logged and returned in the `begin()` summary.
* Standalone / `output_dir=None`: legacy per-user path probe —
  `//client/c$/LocalData/{user}` (Citrix) falling back to
  `c:/LocalData/{user}`.

The study case name is recorded on the General Information sheet, not
in the filename (in batch it is identical for every project).

---

## Failure handling contract

| Event | Behaviour |
|---|---|
| Missing "All Active Grids Study Case" | `AssessmentError`; project recorded as skipped; batch continues |
| No radial feeders / no protection devices | `AssessmentError`; as above |
| Feeder with no devices (others populated) | Warning logged; feeder excluded; run continues |
| Unhandled exception in a stage | Propagates to batch layer; traceback logged; project recorded failed; model state restored by `finally` guards; batch continues |
| Output directory unreachable at save | Exception -> project recorded failed (results are computed but not persisted) |

The batch run summary lists every project not fully assessed, with
reason where known.

---

## Known limitations / parked work

* Mesh feeders are silently excluded from assessment (radial only).
* `min_fl_*` aggregations assume numeric fault levels; a `None` from
  result extraction on exotic topologies can surface as a TypeError
  (tracked; report with the traceback if seen).
* Clearing-time interpolation fetches curve data from the PF API per
  fault-level step; a caching refactor is planned if 80-project
  runtimes require it.
* `importlib.reload()` calls in several modules are artifacts of
  in-PowerFactory development, pending removal.

For the live fix queue, see the maintainer's notes / project tracker.