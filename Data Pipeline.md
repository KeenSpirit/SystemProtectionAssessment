# Data pipeline: dataclass lifecycle

The assessment is built around plain-Python dataclasses in `assets/`.
PowerFactory objects are wrapped early, then every later stage reads
and *progressively populates* the same structures. Every output — the
results workbook, dashboard facts, colour maps, coordination plots and
the legacy SEQ workbook — is rendered entirely from these dataclasses.

The single most important thing to understand — and the source of a
past correctness bug — is the **temporal contract**: most fields are
`None` (or empty lists) until a specific stage has run. A function
that reads a field before its populating stage gets `None`, and
depending on the guard, that either raises or silently produces a
"NO DATA" result. When adding code, check this document for *when*
the field you need becomes valid.

Both entry points share the pipeline. They differ only in how stages
1–3 choose what to assess (automatically in batch, through dialogs
interactively) and in which outputs are written after stage 17.

---

## Object model

```
Feeder
 ├─ obj: ElmFeeder, cubicle, term, sys_volts
 ├─ open_points: {site: switch}             <- get_open_points
 ├─ bu_devices: {ElmXnet: [Device]}         <- cvrt_fdr_to_dataclass
 └─ devices: [Device]
     ├─ obj / cubicle / term / phases / l_l_volts   (PF anchors)
     ├─ sect_terms: [Termination]
     ├─ sect_lines: [Line]
     ├─ sect_loads: [Tfmr]
     ├─ us_devices / ds_devices: [Device]
     ├─ ds_capacity, max_ds_tr
     ├─ max_fl_* / min_fl_* / min_sn_fl_*   (device-level summaries)
     ├─ reach_factors, reach_factor_terms   (terminal reach)
     ├─ min_device_2ph / min_device_pg      (coordination sweep bounds)
     └─ *_coord_fl / *_coord_margin / *_coord_required, coord_note

Termination: obj (ElmTerm), phases, l_l_volts, constr,
             sequence impedances max_/min_/min_sn_ r0 x0 r1 x1 r2 x2,
             max_fl_3ph/2ph/pg, min_fl_3ph/2ph/pg, min_fl_pg10/pg50,
             min_sn_fl_2ph/pg (+ pg10/pg50)
Line:        obj (ElmLne), phases, l_l_volts, line_type, constr, length,
             thermal_rating ("NA" for cables),
             max_fl_*, min_fl_*, min_sn_fl_2ph/pg,
             ph_energy/ph_clear_time/ph_fl,
             pg_energy/pg_clear_time/pg_fl,
             reach_factors: {key: value}    (line reach)
Tfmr:        obj (ElmLod / ElmTr2 or None), term, load_kva,
             max_ph, max_pg, fuse, insulation, impedance
```

(Field lists abbreviated to the ones with cross-stage significance;
the dataclass docstrings in `assets/` are the authoritative field
reference.)

---

## Population stages, in execution order

Stage owners: **D** = pipeline/discovery, **P** = pipeline/run,
**F** = fault_study/, **R** = relays/reach_factors, **C** =
cond_damage/, **K** = prot_coordination/.

| # | Stage (function) | Populates | Valid from here on |
|---|---|---|---|
| 1 | **D** `get_feeders_devices` (batch, all-relays study) or `get_feeders_switches` (no-relays and legacy studies) | raw PF device -> feeder/grid mapping (dicts of PF objects) | feeder/device membership |
| 2 | **D** `chk_empty_fdrs` | drops feeders with no devices; raises `AssessmentError` if none remain | — |
| 3 | **D** `cvrt_fdr_to_dataclass` | `Feeder`, `Device` (and backup `Device`s) with PF anchors. Each feeder gets its **own copy** of `bu_devices` | identity fields only |
| 4 | **P** `get_open_points` (per feeder) | `feeder.open_points` | open point dict |
| 5 | **F** `get_downstream_objects` (per feeder) | `sect_terms` / `sect_loads` / `sect_lines` as **raw PF object lists** (>1 kV terminals; ElmLod for SEQ, ElmTr2 excl. regulators for Regional) | section membership (raw) |
| 6 | **F** `us_ds_device` (per feeder) | `us_devices`, `ds_devices` (grid backup devices for head-end) | device hierarchy |
| 7 | **F** `get_ds_capacity` (per feeder) | `ds_capacity` | downstream kVA |
| 8 | **F** `get_device_sections` (per feeder) | `sect_terms/loads/lines` **replaced with dataclass lists**, overlaps between nested sections removed; `Line.thermal_rating`, `constr`, `length` | from here, section lists contain dataclasses, exclusive per device |
| 9 | **F** short circuits, **once for all feeders**: Max and Min ground studies, then `terminal_z` | `Termination.max_*` / `min_*` sequence impedances | max/min impedances |
| 10 | **F** SN-min block: `reset_min_source_imp` -> study -> restore, or `copy_min_zs` when the grids' min and SN-min data are equal | `Termination.min_sn_*` impedances | SN-min impedances |
| 11 | **F** `append_floating_terms` (per feeder) | additional `Termination`s for network endpoints, impedance = opposite terminal + full line | endpoint impedances |
| 12 | **F** `build_feeder_fls` -> `assets.build_term_fls` | every `Termination.max_fl_*`, `min_fl_*` (incl. pg10/pg50), `min_sn_fl_*` | terminal fault levels |
| 13 | **F** `fault_impedance.update_node_construction` | `Termination.constr` | earth fault resistance selection |
| 14 | **F** `update_device_data` | `Device.max_fl_*`, `min_fl_*`, `min_sn_fl_*`, `max_ds_tr`; **sorts** `sect_terms` by min_fl_pg desc | device-level summaries |
| 15 | **F** `update_line_data` | `Line.max_fl_*`, `min_fl_*`, `min_sn_fl_2ph/pg`; **sorts** `sect_lines` | line fault levels — precondition for stages 16–17 |
| 16 | **R** `populate_reach_factors`, `populate_line_reach_factors` (per feeder) | `Device.reach_factors`, `reach_factor_terms`, `min_device_2ph/pg`; `Line.reach_factors` | reach factors |
| 17 | **C** `cond_damage` (selected devices), **K** `prot_coordination` (per feeder, optional) | `Line.ph_*` / `pg_*` energy fields; `Device.*_coord_*`, `coord_note` | assessment complete |
| 18 | outputs | nothing — read-only render of all of the above | — |

Stages 5–8 run per feeder; stages 9–10 run once across every feeder;
11–15 run per feeder inside `fault_study()`. Only stages 16–17 run in
`run_assessment`'s per-feeder loop, where a failure excludes that
feeder from the outputs and the rest carry on. So once `fault_study()`
returns, every feeder has fault levels; during stages 16–17 earlier
feeders are fully assessed while later ones are not.

---

## Contracts worth knowing

* **`sect_*` changes type at stage 8.** Before `get_device_sections`
  they hold raw PF objects; after, dataclasses. Anything inserted
  between stages 5 and 8 must handle the raw form.
* **Fault levels are calculated, not read.** The short circuits supply
  sequence impedances; `build_term_fls` applies the c-factor (1.1 max,
  1.0 min) and fault resistance. A terminal with missing impedance
  results gets `None` fault levels; a fault type that cannot exist on
  the terminal (3-phase on one or two phases, 2-phase on one phase)
  gets `0`.
* **Device-level summaries use 0 for "no result".** `update_device_data`
  takes the min/max of the non-None terminal values and falls back to
  0. Outputs that need a positive value (log-axis plots) must check.
* **`max_ds_tr` is always set.** With no downstream transformer it is
  an empty `Tfmr` whose `term` is `None`; test `max_ds_tr.term`, not
  `max_ds_tr`.
* **Minimum earth fault levels used for reach are impedance-selected.**
  `fault_impedance.get_terminal_pg_fault` picks `min_fl_pg`,
  `min_fl_pg10` or `min_fl_pg50` per `criteria.earth_fault_resistance`
  (region and construction). `Device.min_fl_pg` and line minima use
  it, so on Regional models they need not equal any terminal's
  `min_fl_pg`.
* **`Line.min_fl_2ph` / `max_fl_2ph` are None until stage 15.**
  `fault_clear_times` (stage 17) skips lines where these are missing,
  logging per line. If an entire run reports NO DATA in the Cond Dmg
  sheets, suspect stage 15 not having run or having failed.
* **Reach factor alignment.** `Device.reach_factors` lists are aligned
  positionally with `sect_terms` as recorded in `reach_factor_terms`;
  the workbook checks the alignment and recalculates rather than
  render misaligned rows. `Line.reach_factors` is stored per line and
  is left `None` for a device whose lists came back misaligned.
* **Only the terminal pass sets the coordination bounds.**
  `populate_line_reach_factors` calls `device_reach_factors` with
  `record_device_minima=False`. Any new caller of `device_reach_factors`
  outside stage 16 must do the same, or it will overwrite
  `min_device_2ph/pg` (the colour map used to).
* **`thermal_rating` may be the string "NA"** (cables). Arithmetic on
  it goes through `criteria.allowable_energy`.
* **Sorting is part of the contract.** Stages 14/15 sort `sect_terms`
  and `sect_lines` (min_fl_pg descending); reporting assumes it.
* **Energy semantics (stage 17):** `ph_energy`/`pg_energy` are I²t
  **summed across all trips** of the auto-reclose sequence, with the
  per-trip enabled-element set applied. `*_clear_time`/`*_fl` record
  the single trip contributing the most energy. A trip with no
  clearing time leaves the total as no data rather than partial.
* **SWER lines:** phase-fault damage is not applicable
  (`criteria.evaluate_damage` returns SWER); earth fault fields are
  populated normally, with the SWER current transformation applied.
* **Backup devices are per-feeder copies** (stage 3). Mutating a
  backup Device affects only that feeder's view.

---

## Outputs (stage 18)

| Output | Entry point | Reads |
|---|---|---|
| Results workbook (`save_results/save_result.py`) | both | everything; conductor damage sheet if run |
| Dashboard facts (`save_results/dashboard_*`) | batch | device and line reach, damage, coordination, line lengths |
| Colour maps (`interactive/colour_maps.py`) | interactive, all relays | `Line.reach_factors`, line energies |
| Coordination plots (`interactive/oc_plots/`) | interactive, if selected | device fault levels, `max_ds_tr`, hierarchy |
| Legacy SEQ workbook (`interactive/legacy_script/`) | interactive, legacy | terminal, line and load fields |

Every pass/fail judgement in these outputs comes from `criteria.py`.

---

## Where to add things

* A new **per-terminal quantity**: from impedances, extend
  `assets.build_term_fls` (stage 12); field on `Termination`; render
  in `format_detailed_results`.
* A new **per-line assessment**: field on `Line`, populate at stage 17
  (or a sibling stage after 15) and call it from `run_assessment`,
  render in `cond_dmg_results` or a new sheet.
* A new **device summary**: field on `Device`, populate in
  `update_device_data`, render in `format_study_results`.
* A new **threshold or verdict rule**: `criteria.py`, then use it from
  every output that shows the result.
* A new **interactive output**: a module under `interactive/`, called
  from `start._run_selection` after `run_assessment`.

Keep computation out of `save_results/` and `interactive/` — they
render, they do not calculate. Keep PF API access out of `assets/` and
`criteria.py` — dataclasses and the functions that aggregate them
should remain testable offline.