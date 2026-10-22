# Assumptions

Technical assumptions, constraints, and dependencies for the System
Protection Assessment (batch and interactive). Thresholds and verdict
rules quoted here are defined in `criteria.py`; if the two disagree,
the code is right and this file needs updating.

## PowerFactory Model Requirements

### Study Case Configuration

- **Batch:** the project must contain "All Active Grids Study Case";
  it is always activated. A missing case skips the project.
- **Interactive:** the user's active study case is used. If none is
  active, "All Active Grids Study Case" is activated instead. Before
  the fault study the all-grids case is briefly activated and the
  user's case restored, so floating terminals load; the all-grids
  case must therefore exist unless it is already the active case.
- At least one in-service external grid (ElmXnet) with a bus
  connection must be present.
- Feeders must be radial. A feeder is meshed when an in-service
  external grid is reachable both upstream and downstream of its
  cubicle; mesh feeders are excluded. Feeders with no elements, or
  out of service, are ignored.
- Lines placed out of service may make a feeder appear meshed if they
  serve as open points.

### Protection Device Configuration

#### Relays (ElmRelay)

- Must have a relay type (`typ_id`) assigned
- Must have a CT (`cpCt`) connected
- Must have a measurement element (`RelMeasure`) with a type
- CT phase count must match the relay measurement element phase count:
  - 3-phase CT requires 3-phase measurement type
  - 1-phase CT requires 1-phase measurement type
- Relay must be located in a cubicle (`StaCubic`)
- Relay must not be out of service

The interactive fault studies report violations of the first four as
warnings before running (`interactive/tools/model_checks.py`).

#### Fuses (RelFuse)

- Only line fuses are protection devices; distribution transformer
  fuses are excluded from protection section analysis
- A fuse with no fuse type is excluded, with a warning
- Fuse type must have a time-current characteristic using the Hermite
  Polynomial curve representation (type 6); other curve types are not
  supported for clearing times
- Must be connected to an energized terminal

#### Auto-Reclosers

- Auto-reclose elements (`RelRecl`) define the trip sequence
- The `oplockout` attribute determines total trips before lockout
- Block logic in the recloser type controls which elements are active
  for each trip in the sequence
- Relays without auto-reclose are treated as single-trip devices

#### No-relay and legacy studies (interactive)

- The feeder head (ElmFeeder) and the feeder's closed switches stand
  in for protection devices
- SEQ: every closed `StaSwitch`, sorted by name
- Regional: closed `ElmCoup` / `StaSwitch` objects whose names contain
  `(ACR/SECT/LBS)`, `(CIRCUIT RECL)`, `(CIRCUIT RECLOSER)` or
  `(OIL CIRCUIT RECL)`, and `StaSwitch` objects containing
  `(EDO FUSE)`

### Network Topology

- Terminals must have nominal voltage > 1 kV to be included in
  fault study results
- Line construction class (`Line.constr`):
  - "SWER" if the line type name contains "SWER"
  - "UG" if the line is a cable (`IsCable()`), its name contains
    "CABLE", or its type has a cable attribute (`cohl_`)
  - "OH" otherwise
- Terminal construction (`Termination.constr`): "SWER" if a connected
  line's type contains "SWER"; "UG" if connected only to cables; "OH"
  otherwise, including when no line is connected
- Floating terminals (end-of-line with a single connection) are
  detected; their impedance is the opposite terminal's impedance plus
  the full line impedance

## Regional Differences

### Region Detection

The region comes from the derived base project path
(`pf_protection_helper.obtain_region`):

- Path containing "Regional Models" and "Northern" → `'Northern'`
- Path containing "Regional Models" and "Southern" → `'Southern'`
- Path containing "SEQ" → `'SEQ'`

Otherwise a `RuntimeError` is raised. Code tests regions with
`criteria.is_seq` / `criteria.is_regional`.

### SEQ Models

| Aspect | SEQ Assumption |
|--------|----------------|
| Load representation | Aggregated loads (`ElmLod`) |
| Minimum earth fault resistance | 0 Ω everywhere |
| Primary reach threshold | 2.0 |
| Backup reach threshold | 1.3 |
| System normal backup reach threshold | 1.5 |
| Fuse selection | Energex fuse mapping tables; RMU fuses need insulation and impedance class (interactive dialog) |

### Regional Models (Northern, Southern)

| Aspect | Regional Assumption |
|--------|---------------------|
| Load representation | Individual transformers (`ElmTr2`), regulators excluded |
| Minimum earth fault resistance | 50 Ω on overhead ("OH") terminals; 10 Ω on all others, including SWER and undetermined construction |
| Primary reach threshold | 1.7 |
| Backup reach threshold | 1.3 |
| System normal backup reach threshold | 1.5 |
| Short-circuit runs | In-service terminals below 1 kV are taken out of service for the runs and restored afterwards |
| Fuse selection | Ergon Energy fuse mapping tables |

The 10 Ω assumption for Regional SWER terminals reproduces the
original behaviour and is pending confirmation.

## Fault Study

### Method

Short-circuit runs supply each terminal's sequence impedances
(`m:R0`, `m:X0`, `m:R1`, `m:X1`, `m:R2`, `m:X2`); fault levels are
then calculated from those impedances. One set of runs covers every
feeder in the project.

- Calculation method: Complete
- Loads, line capacitance and shunts ignored
- Fault location: all busbars and junction nodes
- Protection not considered in the runs
- Runs: maximum ground fault, minimum ground fault, and (unless the
  grids' minimum and system normal minimum data are identical) a
  system normal minimum ground fault with the system normal source
  impedances applied

### Fault Level Calculation

With `V` the terminal nominal line-to-line voltage, `c` the voltage
factor and `Rf` the fault resistance:

```
3-phase  = c·V / (√3 · |Z1|)
2-phase  = c·V / |Z1 + Z2|
P-G      = 3 · (c·V/√3) / |Z0 + Z1 + Z2 + 3·Rf|
```

| Study Type | c-Factor |
|------------|----------|
| Maximum fault level | 1.1 |
| Minimum fault level | 1.0 |

| Fault Type | Attribute | Notes |
|------------|-----------|-------|
| 3-Phase | `_3ph` | 0 on terminals with fewer than three phases |
| 2-Phase | `_2ph` | 0 on single-phase terminals |
| Phase-Ground | `_pg` | Rf = 0 |
| Phase-Ground 10 Ω | `_pg10` | Minimum and system normal minimum only |
| Phase-Ground 50 Ω | `_pg50` | Minimum and system normal minimum only |

A terminal with no impedance result gets no fault levels (`None`);
device-level summaries use 0 when none of their terminals has a
result.

### External Grid Data

- **Batch:** maximum, minimum and system normal minimum source data
  come from the regional source z-data workbooks and are written to
  the matched grids. Unmatched grids keep the model's values, with the
  minimum reused as the system normal minimum.
- **Interactive:** entered in the feeder dialog, pre-filled from the
  model; the system normal minimum is pre-filled from the master
  project grid's `snssmin`, converted to a current at 11 kV. The values
  entered are written to the grids.
- If the minimum and system normal minimum data are identical for
  every grid, the minimum impedances are copied to the system normal
  fields instead of running the extra study.
- A grid whose system normal minimum fault level is zero or negative
  is placed out of service for the system normal study, then restored.

## Protection Reach Calculations

### Reach Factor Definition

```
Reach Factor = Minimum Fault Current at Location / Device Pickup Setting
```

A reach factor is "NA" where the pickup is not configured or the
location has no fault level result.

### Pickup Determination

- Relays: for each function (phase OC, earth fault), the highest
  pickup among active IDMT elements, or among instantaneous elements
  if there are no IDMT elements; NPS uses IDMT and instantaneous
  elements together
- Fuses: rated current × 2
- Effective earth fault pickup: the lower of the earth fault and phase
  pickups, since phase elements also see earth faults
- Backup pickup: the lowest configured pickup among the device's
  upstream devices; a fuse's backup values equal its primary values

### Minimum Earth Fault Level Used

`fault_impedance.get_terminal_pg_fault` selects the minimum earth
fault level for the fault resistance in *Regional Differences*.

### SWER Transformation

When a multi-phase device at a different voltage protects a
single-phase (SWER) location:

```
Device Current = (SWER Voltage × SWER Fault Current / Device Voltage) / √3
```

The device sees the fault as a 2-phase equivalent rather than earth
fault.

### Active Elements by Fault Type

| Fault Type | Active Protection Functions |
|------------|----------------------------|
| 3-Phase | Phase OC only |
| 2-Phase | Phase OC + NPS |
| Phase-Ground | Phase OC + Earth Fault + NPS |

## Conductor Damage Assessment

### Energy Calculation

Total let-through energy is accumulated across all trips in the
auto-reclose sequence:

```
Total Energy = Σ (Fault Current² × Clearing Time) for each trip
```

If any trip has no clearing time, the line's total is reported as no
data rather than a partial total.

### Clearing Time Components

- Relay: operate time from the time-current characteristic plus a
  fixed 80 ms circuit breaker opening time
- Fuse: total clearing time from the fuse characteristic

### Thermal Rating Source

- Overhead lines: 1-second rating from `ratings_lookup.csv`, matched
  on the conductor name (exact name before the voltage suffix first,
  otherwise the longest contained name)
- Other tower-geometry and line types: `Ithr` from the conductor type
- Cable systems: not assessed ("NA")

### Pass/Fail Criteria (`criteria.evaluate_damage`)

```
Allowable Energy = Thermal Rating² × 1 second
FAIL     if Total Energy > Allowable Energy
PASS     if Total Energy ≤ Allowable Energy
NO DATA  if the rating is missing or non-numeric, or the energy is
         missing or zero
SWER     for phase faults on a SWER line (not applicable)
```

Earth faults on SWER lines are assessed normally.

## Protection Coordination Assessment

- Grading margin required (`criteria.required_margin`): 0.3 s for a
  relay-relay pair, 0.1 s if either device is a fuse
- Times compared: relay trip time (no breaker operate time); fuse
  total clearing time, except a backup fuse behind a relay primary,
  which uses minimum melt
- The fault level range is swept in 10 A steps from the minimum the
  device must detect (`min_device_2ph` / `min_device_pg`, set by the
  terminal reach factor stage) to the device's maximum fault level;
  a fault type with either bound missing is not assessed
- A backup trip faster than half its lockout trip's time at a fault
  level is treated as a fuse-saving trip and not graded against

## Dashboard Facts (batch)

- An unconfigured pickup counts as "no coverage" in the pair rule; a
  measured-short partner therefore yields an exception. Both members
  unconfigured yields unassessable, not exception.
- A reach factor of 0.0 is treated as absent data (unassessable),
  never as an exception.
- A tier with one pair covered and the other unassessable is
  unassessable for the line — partial assessment is not coverage.
- SWER lines are overhead for length purposes. Their reach is
  assessed on the EF pair alone, and their phase-fault damage verdict
  ('SWER') is not applicable: reported in `ph_dmg_not_applicable_km`,
  excluded from exceptions and from the assessed denominator.

## Fuse Selection

### Mapping Tables

Fuse selection uses lookup tables based on:

- Region (SEQ or Regional)
- Transformer kVA rating
- Line construction type (OH, UG, SWER)
- Voltage level
- Number of phases
- RMU insulation type (air or oil) - SEQ only
- Transformer impedance class (high or low) - SEQ RMU only

### Source Documents

- SEQ: Energex fuse tables
- Regional: Ergon Energy Technical Instruction TSD0019k

## Output Assumptions

### Results Workbook Location

- Batch: the output directory given by the batch runner
- Interactive: the user's local directory, attempting in order:
  1. `//client/c$/LocalData/{username}/` (Citrix client mapping)
  2. `c:/LocalData/{username}/` (local installation)

### PowerFactory Colour Scheme Encoding (interactive)

DPL attributes (`dpl1`, `dpl2`, `dpl3`) encode pairs of assessment
results using a 4×4 grid:

```
Value = (Row Condition - 1) × 4 + Column Condition
```

Where conditions are: Pass=1, Fail=2, No Data=3, SWER=4

| DPL Attribute | Row Assessment | Column Assessment |
|---------------|----------------|-------------------|
| `dpl1` | Phase Primary Reach | Phase Backup Reach |
| `dpl2` | Earth Primary Reach | Earth Backup Reach |
| `dpl3` | Phase Conductor Damage | Earth Conductor Damage |

Reach uses the higher of the main and NPS reach factor for the line;
"NA" shows No Data. Conductor damage follows `criteria.evaluate_damage`.

### Time-Current Plot Settings (interactive)

- Plot scale: Logarithmic (both axes)
- Y-axis range: 0.01 to 10 seconds
- X-axis range: the first device's minimum to maximum fault level for
  the fault type, rounded out to whole decades; the plot is skipped if
  either is missing
- Page format: A4 Landscape

### Legacy SEQ Workbook (interactive)

- Saved to `\\client\Y$\PROTECTION\PowerFactory\Fault Level Studies`,
  falling back to the user's home folder
- Inrush is estimated at 12 × downstream capacity at 11 kV

## Dependencies

### Python Packages

| Package | Purpose |
|---------|---------|
| `pandas`, `numpy` | DataFrame operations, Excel export |
| `openpyxl` | Excel workbook formatting; source z-data workbooks |
| `tkinter` | Dialogs (interactive path only) |
| `logging` | Progress, warnings and errors |

### PowerFactory Version

Script developed and tested with PowerFactory 2023 and later.
Earlier versions may have API differences.

### Network Path Dependencies

- PowerFactory typing stubs: `\\Ecasd01\WksMgmt\PowerFactory\ScriptsDEV\PowerFactoryTyping`
- Conductor ratings: `\\ecasd01\WksMgmt\PowerFactory\ScriptsDEV\SystemProtectionAssessment\data\ratings_lookup.csv`
- Ergon global library: required for fuse type lookups

## Known Limitations

1. **Mesh feeders excluded** - Only radial feeders can be studied
2. **Single protection section** - Devices define sections by downstream
   topology; parallel paths may cause unexpected section boundaries
3. **Fixed breaker time** - 80 ms assumed for all circuit breakers
4. **Cable thermal rating** - Not assessed (requires different methodology)
5. **Voltage regulator exclusion** - Transformers in "Regulators" folder
   are excluded from load calculations
6. **11 kV assumptions** - The interactive master project system normal
   minimum conversion and the legacy inrush estimate assume 11 kV
7. **Zero reach factor** - The dashboard treats a reach factor of 0.0
   as absent data; the colour map shows it as Fail
