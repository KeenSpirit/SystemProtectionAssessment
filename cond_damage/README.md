# cond_damage – conductor damage assessment

This package checks whether the protection on each feeder section clears faults
quickly enough to keep the conductor below its short-circuit temperature limit
through the whole auto-reclose sequence.

This file outlines the assessment philosophy only: how it works now, and where it
is proposed to go. Known defects and the work needed to get from one to the other
are tracked in `comd_dmg_issues.md`.

---

## 1. Current method

### 1.1 Let-through energy (what the conductor sees)

For each protection device, and for each line in its section:

1. The device is stepped through its reclose sequence, once for phase faults and
   once for earth faults. On each trip, the elements blocked for that trip are set
   out of service.
2. The clearing time is found at a set of fault levels across the line's fault
   range, from the minimum to the maximum. Clearing time is the fastest active
   element: relay operate time plus 80 ms CB time, or fuse total clearing time.
3. The fault level giving the greatest I²t on that trip is taken as the trip's
   worst case.
4. The line's let-through energy is the **sum of the per-trip worst cases**:

   E = Σ over trips of ( I_worst,trip² × t_clear,trip )

Fault currents are symmetrical RMS values; DC offset is not included.

### 1.2 Withstand energy (what the conductor can take)

- **Overhead lines:** the 1-second fault rating I₁ₛ is read from
  `ratings_lookup.csv`. The allowable energy is I₁ₛ² × 1 s.
- **Underground lines:** I₁ₛ is the `Ithr` value on the PowerFactory line type.
- **Cable systems:** not assessed (`NA`).

The overhead ratings come from the adiabatic heating formula:

    I₁ₛ² × 1 s = (m·c) / (R₂₀·α₂₀) × ln[ (1 + α₂₀(T_f − 20)) / (1 + α₂₀(T_i − 20)) ]

where m is mass per unit length, c is specific heat, R₂₀ is DC resistance at
20 °C and α₂₀ its temperature coefficient.

When the table is back-solved, it reproduces with:

- **T_i = 55 °C** for every conductor, and
- **T_f = 200 °C** for AAC and ACSR, **300 °C** for AAAC 1120 and HDBC, and
  **500 °C** for steel.

The accompanying method note ("Fault rating calculation method") says instead that
T_i is each conductor's maximum operating temperature (T_max). That is not the
basis the table was built on. See issue 3.

### 1.3 Verdict

| Result | Meaning |
|---|---|
| PASS | E ≤ allowable energy |
| FAIL | E > allowable energy |
| NO DATA | No rating, or any trip produced no clearing time |
| SWER | Phase fault on a SWER line (not applicable) |

### 1.4 Why summing trips is valid

Under adiabatic heating, the conductor's temperature rise depends only on the
cumulative ∫i²dt, not on how that energy is split between trips. Comparing the
summed I²t with the 1-second withstand is therefore consistent with the rating's
own model. IEC 60865-1 treats repeated short circuits the same way.

The method makes two simplifying assumptions, both conservative:

- **No cooling during dead times.** The conductor is treated as if every trip
  happened back to back.
- **Each trip's worst case may come from a different fault level.** A real fault
  has one current for the whole sequence.

The assumption that drives the result most is the pre-fault temperature T_i (see
1.2), not either of these.

---

## 2. Proposed method

The proposal keeps the cumulative adiabatic check as the screening test. It makes
the rating basis explicit, removes the avoidable conservatism, and adds a second,
more detailed tier for lines that fail or come close.

### 2.1 Explicit, configurable rating basis

- Compute the withstand energy in the script from the conductor properties already
  in `ratings_lookup.csv` (Rdc, α₂₀, mass, specific heat), rather than reading a
  precomputed 1-second rating.
- Hold T_i and the per-material T_f in configuration. The basis is then stated in
  one place, can be audited, and can be changed without regenerating the table.
- The T_i policy is an engineering decision for EQ, made and documented once:
  - a realistic pre-fault temperature (the table's current 55 °C), or
  - the conductor design temperature (much more restrictive), or
  - ambient plus a load-dependent rise.

### 2.2 Tier 1 – cumulative adiabatic screen (refined)

- For each sampled fault level, sum I²t across all trips **at that same level**.
  Then take the maximum over fault levels.
- Compare the result with the withstand from 2.1.
- Lines that pass with margin are finished. Lines that fail, or fall within a set
  margin, go to tier 2.

### 2.3 Tier 2 – temperature stepping through the reclose sequence

For lines from tier 1, track conductor temperature through the actual sequence,
reading the dead times from the recloser model:

1. **Start** at the pre-fault temperature T_pre (the T_i from 2.1).
2. **During each trip (adiabatic heating):** after energy E = I²t,

       1 + α₂₀(T − 20) = (1 + α₂₀(T₀ − 20)) · exp( E · R₂₀ · α₂₀ / (m·c) )

3. **During each dead time (cooling):**

       T → T_pre + (T − T_pre) · e^(−t_dead / τ),   τ = m·c / h_eff

   h_eff is taken from an IEEE 738-style heat balance (convection plus radiation)
   at conservative weather: low wind and high ambient.
4. **Verdict:** compare the peak temperature over the sequence with T_f.

Cooling only matters for small conductors and long dead times. As a rough guide,
τ is about 50–60 s for 7/1.75 AAC or 7/1.25 HDBC, and several minutes for larger
conductors.

### 2.4 Out of scope (noted)

- DC offset. It is significant only for fast trips at high X/R near zone
  substations.
- Cumulative annealing over the conductor's life.

---

## 3. Order of work

1. Fix issue 1 (identical energy on every trip). Until each trip's elements are
   seen correctly, the per-trip refinements in section 2 have no effect.
2. Settle and document the T_i / T_f basis, then correct the method note (issue 3).
3. Compute the withstand in-script (issue 4).
4. Accumulate energy per fault level (issue 5).
5. Add tier 2 (issue 6).