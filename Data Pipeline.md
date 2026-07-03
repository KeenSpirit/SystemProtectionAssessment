# Data pipeline: dataclass lifecycle

The assessment is built around plain-Python dataclasses in `assets/`.
PowerFactory objects are wrapped early, then every later stage reads
and *progressively populates* the same structures. The workbook at the
end is rendered entirely from these dataclasses.

The single most important thing to understand — and the source of a
past correctness bug — is the **temporal contract**: most fields are
`None` (or empty lists) until a specific stage has run. A function
that reads a field before its populating stage gets `None`, and
depending on the guard, that either raises or silently produces a
"NO DATA" result. When adding code, check this document for *when*
the field you need becomes valid.

---

## Object model

```
Feeder
 ├─ obj: ElmFeeder
 ├─ open_points: [str]                      <- get_open_points
 ├─ bu_devices: {ElmXnet: [Device]}         <- cvrt_fdr_to_dataclass
 └─ devices: [Device]
     ├─ obj / cubicle / cbranch / cn_bus / term   (PF anchors)
     ├─ sect_terms: [Termination]
     ├─ sect_lines: [Line]
     ├─ sect_loads: [Load]
     ├─ us_devices / ds_devices: [Device]
     ├─ ds_capacity, max_ds_tr
     └─ max_fl_* / min_fl_* / min_sn_fl_*   (device-level summaries)

Termination: obj (ElmTerm) + max_fl_3ph/2ph/pg,
             min_fl_3ph/2ph/pg, min_fl_pg10/pg50,
             min_sn_fl_2ph/pg (+ pg10/pg50), construction
Line:        obj (ElmLne), phases, l_l_volts, line_type,
             thermal_rating ("NA" for cables),
             max_fl_* , min_fl_* , min_sn_fl_2ph/pg,
             ph_energy/ph_clear_time/ph_fl,
             pg_energy/pg_clear_time/pg_fl
Load:        obj, term, load_kva, max_ph, max_pg
```

(Field lists abbreviated to the ones with cross-stage significance;
the dataclass docstrings in `assets/` are the authoritative field
reference.)

---

## Population stages, in execution order

Stage owners: **S** = start.py, **F** = fault_study/,
**C** = cond_damage/, **R** = save_results/.

| # | Stage (function) | Populates | Valid from here on |
|---|---|---|---|
| 1 | **S** `get_feeders_devices` | raw PF device -> feeder/grid mapping (dicts of PF objects, no dataclasses yet) | feeder/device membership |
| 2 | **S** `cvrt_fdr_to_dataclass` | `Feeder`, `Device` (and backup `Device`s) with PF anchors: `obj`, `cubicle`, `cbranch`, `cn_bus`, `term`. Each feeder gets its **own copy** of `bu_devices` | identity fields only; every study field is still None/empty |
| 3 | **S->fdr_open_points** `get_open_points` | `feeder.open_points` | open point list |
| 4 | **F** `get_downstream_objects` | `sect_terms` / `sect_loads` / `sect_lines` as **raw PF object lists** (>1 kV terminals; ElmLod for SEQ, ElmTr2 excl. regulators for Regional) | section membership (raw) |
| 5 | **F** `us_ds_device` | `us_devices`, `ds_devices` (uses grid backup devices for head-end) | device hierarchy |
| 6 | **F** `get_ds_capacity` | `ds_capacity` | downstream kVA |
| 7 | **F** `get_device_sections` | `sect_terms/loads/lines` **replaced with dataclass lists**, overlaps between nested sections removed | from here, section lists contain Termination/Line/Load dataclasses, exclusive per device |
| 8 | **F** `short_circuit` + `terminal_fls` (x8 configs) | `Termination.max_fl_*`, `min_fl_*`, `min_fl_pg10/pg50` | terminal max/min fault levels |
| 9 | **F** SN-min block (`reset_min_source_imp` -> studies -> restore, or `copy_min_fls` when grids are equivalent) | `Termination.min_sn_fl_*` | system-normal minimum levels |
| 10 | **F** `append_floating_terms` | additional `Termination`s appended to `sect_terms` for network endpoints | endpoint fault levels |
| 11 | **F** `update_device_data` | `Device.max_fl_*`, `min_fl_*`, `min_sn_fl_*`, `max_ds_tr`; **sorts** `sect_terms` by min_fl_pg desc | device-level summaries |
| 12 | **F** `update_line_data` | `Line.max_fl_*`, `min_fl_*`, `min_sn_fl_2ph/pg`; **sorts** `sect_lines` | line fault levels — the precondition for conductor damage |
| 13 | **C** `cond_damage` | `Line.ph_energy/ph_clear_time/ph_fl`, `pg_energy/pg_clear_time/pg_fl` | damage assessment inputs complete |
| 14 | **R** `save_dataframe` | nothing — read-only render of all of the above | — |

Stages 4–12 run **per feeder** inside the `begin()` loop; stage 13
runs per feeder immediately after its stage 12. So at any moment,
feeders earlier in the list are fully populated while later feeders
are still empty shells — do not write code that reads across feeders
mid-run.

---

## Contracts worth knowing

* **`sect_*` changes type at stage 7.** Before `get_device_sections`
  they hold raw PF objects; after, dataclasses. Anything inserted
  between stages 4 and 7 must handle the raw form.
* **`Line.min_fl_2ph` / `max_fl_2ph` are None until stage 12.**
  `fault_clear_times` (stage 13) explicitly skips lines where these
  are missing, logging per line. If an entire run reports NO DATA in
  the Cond Dmg sheets, suspect stage 12 not having run or having
  failed for that feeder.
* **`thermal_rating` may be the string "NA"** (cables). Damage
  evaluation returns NO DATA for these; arithmetic on the field must
  go through a numeric guard.
* **Sorting is part of the contract.** Stages 11/12 sort `sect_terms`
  and `sect_lines` (min_fl_pg descending); downstream reporting
  assumes this order.
* **`ph_*` fields on SWER lines:** phase-fault assessment is not
  applicable; reporting emits "SWER" based on the line type name.
  Earth-fault fields are populated normally (with SWER current
  transformation applied in stage 13).
* **Energy semantics (stage 13):** `ph_energy`/`pg_energy` are I2t
  **summed across all trips** of the auto-reclose sequence, with the
  per-trip enabled-element set applied. `*_clear_time`/`*_fl` record
  the single trip contributing the most energy. Pass/fail in
  `cond_dmg_results` compares accumulated energy against
  `thermal_rating**2` (1-second rating -> withstand energy in A2s).
* **Backup devices are per-feeder copies** (stage 2). Mutating a
  backup Device affects only that feeder's view.

---

## Where to add things

* A new **per-terminal quantity**: populate in a stage-8-style pass
  (add the (bound, fault type) mapping to `_TERMINAL_FL_ATTR`), field
  on `Termination`, render in `format_detailed_results`.
* A new **per-line assessment**: field on `Line`, populate at stage 13
  (or a sibling stage after 12), render in `cond_dmg_results`.
* A new **device summary**: field on `Device`, populate in
  `update_device_data`, render in `format_study_results`.

Keep computation out of `save_results/` — it renders, it does not
calculate. Keep PF API access out of `assets/` — dataclasses and the
functions that aggregate them should remain testable offline.