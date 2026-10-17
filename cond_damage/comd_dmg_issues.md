# Conductor damage assessment – open issues

See `README.md` for the current and proposed assessment philosophy. Issues 3–6
are the steps that take the current method to the proposed one.

**Dependencies**
- Issue 1 blocks issues 5 and 6. Both depend on each trip's elements being seen
  correctly.
- Issue 3 blocks issue 4. The basis has to be decided before it is coded.

## 1. Every reclose trip gives the same worst-case energy

**Status:** open – verify before trusting multi-trip results.

**Evidence**
- In all three workbooks, `Total energy = worst-case fault level² × clearing time × trips`.
- This holds for 5,209 of the 5,213 multi-trip results, counting phase and earth separately. In other words, every trip produced exactly the same worst-case I²t.
- The 4 rows that didn't match were the SWER rows fixed by problem 2.
- For reclosers with fast trips followed by delayed trips, identical energy on every trip is implausible.

**Possible causes**
1. The block logic (`ilogic`) really is the same for every trip in these models.
2. The worst case always lands on an instantaneous step that is enabled on every trip.
3. `set_enabled_elements` writes `outserv`, but `get_prot_elements` reads it back as `e:outserv` / `IsOutOfService()`. That read may not see the change while the PowerFactory write cache is enabled for the SPA stage.

**Check**
- Pick one recloser with distinct fast and delayed curves.
- Log `trip_count`, the names of the active elements, and the clearing time at the line minimum on each trip.
- Then compare with the write cache on and off.

**Code:** `reclose.set_enabled_elements`, `elements.get_prot_elements`, `conductor_damage._prot_elements_for_trip`.

## 2. SWER conversion applied where there is no isolating transformer

**Status:** open.

- `swer_fault_range` converts the current whenever the line type name contains "SWER", the line has 1 phase and the device has more than 1.
- It doesn't check whether there is actually a voltage change between the device and the line.
- Example: DR-07 `HV_LINE_10629592_1` (7/1.75 HDBC_11kV, device DUROSS-FB56-J01-J101). The line terminal and the device are both at 11 kV, yet the device is treated as seeing I/√3 as a 2-phase fault. It actually sees the full current as an earth fault.
- **Options:**
  - skip the conversion when `line.l_l_volts == device.l_l_volts`; or
  - fix the line type in the model, if it shouldn't be SWER.
- **Assumption to confirm:** the conversion assumes SWER terminals carry a line-to-line `uknom` (e.g. 33 kV for 19.1 kV SWER), as PowerFactory does by convention. Check this on one SWER terminal per region.

## 3. Rating basis in `ratings_lookup.csv` doesn't match its method note

**Status:** open – engineering decision needed.

**Evidence**
- The method note says the 1-second ratings use the adiabatic formula with T_i = the conductor's maximum operating temperature (T_max).
- When all 344 rows are back-solved from their own Rdc, α₂₀, mass and c, every row reproduces with **T_i = 55 °C** (±0.3 °C). T_f is as the note states: 200 °C for AAC and ACSR, 300 °C for AAAC and HDBC, 500 °C for steel.
- The 150 °C and 160 °C T_max groups give the same implied T_i. That could not happen if T_max had been the starting point.
- The note's formula and T_f values are correct. Only the T_i statement is wrong.

**Impact**
- If T_i = T_max were used, as the note says, the allowable I²t would be about 1/3 to 1/4 of the current values for aluminium and copper, and about 1/6 for steel.

  | T_max group | Rating ratio (table ÷ note basis) | I²t ratio |
  |---|---|---|
  | 150 (ACSR) | 1.82 | 3.3 |
  | 160 (AAC/ACSR) | 2.05 | 4.2 |
  | 200 (Cu) | 1.72 | 3.0 |
  | 220 (AAAC) | 1.93 | 3.7 |
  | 400 (steel) | 2.53 | 6.4 |

- T_i therefore drives the outcome far more than any other assumption in the assessment.

**Actions**
- Decide the T_i policy:
  - a realistic pre-fault temperature (55 °C, as now), or
  - the design temperature, or
  - ambient plus a load-dependent rise.
- Confirm it against EQ's standard and the overhead line rating tool.
- Confirm the T_f limits against the same standard. IEC 60865-1 recommends lower short-circuit limits for bare Cu and AAAC than the 300 °C used here.
- Correct the method note to state the basis actually used. Its references to CIGRE TB 207/601 and IEEE 738 are line-rating methods, not short-circuit methods; IEC 60865-1 is the closer reference.

**Evidence file:** `ratings_check.csv` lists, for each conductor, the table rating, the rating on the note's basis, the ratio, and the implied T_i.

## 4. Compute the withstand in the script

**Status:** proposed – depends on issue 3.

**Current**
- `assets.utils.conductors_properties` reads only columns 0, 1, 6 and 14 (name, 1-s rating, diameter, `Limited`).
- `_allowable_energy` returns I₁ₛ² × 1 s.
- The rating basis is fixed inside the CSV and can't be seen from the code.

**Proposed**
- Read Rdc, α₂₀ (`Alpha[20]`), mass, specific heat (`C[20]`) and material from `ratings_lookup.csv`.
- Hold T_i and a material → T_f map as module constants or config.
- Compute the allowable I²t with the adiabatic formula.
- Optionally include the specific-heat temperature coefficient (`A[c]`). It is unused at present, which is slightly conservative.
- Keep the 1-s rating column for cross-checking. Log any conductor where the computed and tabulated ratings differ by more than a set tolerance at the configured basis.

**Related defects in `conductors_properties`**
- Its docstring says "Cached after first call", but nothing is cached. It reopens the network CSV for every line in `_get_conductor_info`. Add `functools.lru_cache` or build the dict once.
- The header row isn't skipped. The `Typcon` check doesn't match the actual header text, so the dict gains a "Conductor Name / Stranding Type" entry.
- Parsing with `row.split(",")` will break on any conductor name that contains a comma. Use `csv` or `pandas`.

**Underground lines**
- UG lines use PowerFactory `Ithr`, whose T_i/T_f basis is unknown. Record it, or exclude UG lines from the comparison until it is known.

**Code:** `assets/utils.py`, `assets/line.py::_get_conductor_info`, `save_results/cond_dmg_results.py::_allowable_energy`.

## 5. Accumulate energy per fault level across trips

**Status:** proposed – depends on issue 1.
(Promoted from the former minor point "Summing each trip's worst case is conservative".)

**Current**
- Each trip's worst-case I²t is found independently, possibly at a different fault level on each trip, and the worst cases are summed.

**Proposed**
- Keep `{fault_level: clear_time}` for every trip.
- For each fault level, sum I² × t_clear across all trips.
- Report the maximum over fault levels, with the fault level and per-trip clearing times that produce it.
- Every trip must be evaluated at the same set of fault levels. With `INTERVAL = "partial"`, take the union of all trips' sample levels.

**Effect**
- Removes avoidable conservatism. The result can't be higher than the current method's, so it can only turn FAILs into PASSes.

**Code:** `conductor_damage.cond_damage` accumulators, `fault_clear_times`, `worst_case_energy`.

## 6. Tier 2 – temperature stepping with dead-time cooling

**Status:** proposed – depends on issues 1, 3 and 5.

**Scope**
- Only lines that fail tier 1, or fall within a set margin of it. Tier 1 stays the screen.

**Method**
1. Start at T_pre (the T_i from issue 3).
2. On each trip, apply adiabatic heating:
   `1 + α(T − 20) = (1 + α(T₀ − 20)) · exp(E · R₂₀ · α / (m·c))`
3. On each dead time, apply exponential cooling:
   `T → T_pre + (T − T_pre) · e^(−t_dead/τ)`, with `τ = m·c / h_eff`.
4. Fail if the peak temperature exceeds T_f.

**Inputs needed**
- Dead times per shot, read from the recloser model. The PowerFactory attribute that holds them still needs to be identified.
- h_eff from an IEEE 738-style convection and radiation balance, at agreed conservative weather (wind speed, ambient, emissivity).

**Expected effect (rough, 0.6 m/s wind, 40 °C ambient)**
- τ ≈ 60 s for 7/1.75 AAC, 50 s for 7/1.25 HDBC, 175 s for 7/3.75 AAC, 290 s for 19/3.25 AAC.
- Fraction of earlier temperature rise still present after a dead time:

  | Conductor | 5 s | 15 s | 30 s | 60 s |
  |---|---|---|---|---|
  | 7/1.75 AAC | 0.92 | 0.78 | 0.61 | 0.37 |
  | 7/3.75 AAC | 0.97 | 0.92 | 0.84 | 0.71 |

- Material benefit only for small conductors with long final dead times.

**Structure**
- Keep the heating and cooling maths in a pure module (no PF calls) so it can be tested offline with pytest.
- Add a verdict column for tier 2, and keep the tier 1 result alongside it.

## Minor points

### Definite-time `RelToc` elements ignored for phase and earth
- `get_prot_elements` drops `RelToc` elements on a definite-time curve (`_is_definite_time`) from the phase and earth IDMT lists, but not from the NPS list.
- Where a definite-time stage is the fastest element, the clearing time comes out too slow (conservative).
- Where it is the only earth element on a trip, the line shows NO DATA.

### `RelIoc` elements counted only if the type has `irecltarget` set
- Instantaneous elements whose type doesn't have this flag set are left out of conductor damage entirely.
- Confirm this filter is intended for conductor damage rather than just for reclose logic.

### Instantaneous elements with zero total operate time ignored
- `_calculate_ioc_time` returns `cptotime`.
- `_element_clear_time` treats any operate time ≤ 0 as "doesn't operate".
- So an element whose `cptotime` is 0 is left out, rather than counted as clearing in 0 s plus the 80 ms CB time.

### 3-phase maximum converted as a 2-phase fault
- The phase pass uses `'2-Phase'` for the whole range, including where the maximum is the 3-phase value.
- At that level, NPS elements are given credit for operating (I2 = If/√3) on what is a balanced fault, where they would see no NPS current.
- This can make the clearing time at the maximum faster than reality, and the energy lower (non-conservative).

### Lines with no section terminal get a 0–0 A range
- In `update_line_data`, a line with neither end on a section terminal gets all fault levels set to 0.
- Since the problem 1 fix these show NO DATA rather than a calculated energy.
- They may still be worth logging, as they point to a section-tracing gap.

### Fuse times interpolated linearly between curve points
- `fuse_curve_time` interpolates linearly in linear current/time space between the Hermite points.
- On a curve that is straight on log-log axes, this overstates the time between points, which is conservative.
- Interpolating in log-log space would be closer.

### DC offset not included in let-through energy
- Energy uses symmetrical RMS current only.
- On fast trips (instantaneous or fuse-saving) close to zone substations at high X/R, the DC component adds to I²t (IEC 60865-1 `m` factor). This is non-conservative.
- Probably small for distribution feeders. Check the worst X/R case on one feeder before deciding whether to model it.