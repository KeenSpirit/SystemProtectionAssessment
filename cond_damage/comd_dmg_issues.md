# Conductor damage assessment – open issues

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

### Summing each trip's worst case is conservative
- Each trip's worst case may come from a different fault level, whereas a real fault has one current through the whole sequence.
- This is now stated in the `cond_damage` docstring.
- If it causes marginal FAILs, the alternative is to sum energy per fault level across trips and then take the maximum.

### Lines with no section terminal get a 0–0 A range
- In `update_line_data`, a line with neither end on a section terminal gets all fault levels set to 0.
- Since the problem 1 fix these show NO DATA rather than a calculated energy.
- They may still be worth logging, as they point to a section-tracing gap.

### Fuse times interpolated linearly between curve points
- `fuse_curve_time` interpolates linearly in linear current/time space between the Hermite points.
- On a curve that is straight on log-log axes, this overstates the time between points, which is conservative.
- Interpolating in log-log space would be closer.