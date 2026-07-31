"""
Conductor damage assessment result formatting for Excel output.

This module formats conductor damage assessment results into a pandas
DataFrame suitable for Excel export. Pass/fail status is evaluated by
comparing the accumulated let-through energy (I²t summed across all
trips in the auto-reclose sequence) against the conductor's thermal
withstand energy, derived from its 1-second thermal rating.

Functions:
    cond_damage_results: Format one feeder's conductor damage rows
"""

from typing import List, Optional

import pandas as pd

from relays import reclose


# Column order for the flat Cond Dmg Results table. 'Feeder' identifies
# the row; the remaining columns are unchanged from the per-feeder
# sheets this replaces.
COND_DAMAGE_COLUMNS = [
    'Feeder',
    'Device',
    'Trips',
    'Line',
    'Line Type',
    'Worst case energy ph flt lvl',
    'Worst case energy ph flt clear time',
    'Total ph energy',
    'Allowable energy',
    'Phase fault conductor damage',
    'Worst case energy gnd flt lvl',
    'Worst case energy gnd flt clear time',
    'Total gnd energy',
    'Ground fault conductor damage',
]


def cond_damage_results(feeder) -> pd.DataFrame:
    """
    Format one feeder's conductor damage results as rows of the flat
    table.

    Produces one row per line section per device, with the feeder name
    repeated on every row so that all feeders stack into a single
    table.

    Args:
        feeder: Feeder dataclass with ``obj`` and ``devices``, each
            device having populated sect_lines.

    Returns:
        DataFrame with exactly the ``COND_DAMAGE_COLUMNS`` columns:
        - Feeder: Feeder name
        - Device: Protection device name
        - Trips: Number of trips in auto-reclose sequence
        - Line: Line section name
        - Line Type: Conductor type description
        - Worst case energy ph flt lvl: Phase fault current of the trip
          contributing the most energy (A)
        - Worst case energy ph flt clear time: Clearing time of that
          trip (s)
        - Total ph energy: Phase I2t accumulated across all trips (A2s)
        - Allowable energy: Conductor thermal withstand energy (A2s),
          or blank where the thermal rating is missing
        - Phase fault conductor damage: PASS/FAIL/NO DATA/SWER
        - Worst case energy gnd flt lvl: Ground fault current of the
          trip contributing the most energy (A)
        - Worst case energy gnd flt clear time: Clearing time of that
          trip (s)
        - Total gnd energy: Ground I2t accumulated across all trips
          (A2s)
        - Ground fault conductor damage: PASS/FAIL/NO DATA/SWER

    Note:
        SWER lines return "SWER" for phase fault assessment as phase
        faults are not applicable to single-wire earth return systems.

    Example:
        >>> df = cond_damage_results(feeder)
        >>> df.to_excel(writer, sheet_name='Cond Dmg Results')
    """
    feeder_name = str(feeder.obj.loc_name)
    line_list = []

    for device in feeder.devices:
        trips = reclose.get_device_trips(device.obj)
        list_length = len(device.sect_lines)

        line_df = pd.DataFrame({
            "Feeder": [feeder_name] * list_length,
            "Device": [device.obj.loc_name] * list_length,
            "Trips": trips,
            "Line": [line.obj.loc_name for line in device.sect_lines],
            "Line Type": [line.line_type for line in device.sect_lines],
            "Worst case energy ph flt lvl": [
                line.ph_fl for line in device.sect_lines
            ],
            "Worst case energy ph flt clear time": [
                line.ph_clear_time for line in device.sect_lines
            ],
            "Total ph energy": [
                line.ph_energy for line in device.sect_lines
            ],
            "Allowable energy": [
                _allowable_energy(line.thermal_rating)
                for line in device.sect_lines
            ],
            "Phase fault conductor damage": [
                _evaluate_damage(line, fault_type='Phase')
                for line in device.sect_lines
            ],
            "Worst case energy gnd flt lvl": [
                line.pg_fl for line in device.sect_lines
            ],
            "Worst case energy gnd flt clear time": [
                line.pg_clear_time for line in device.sect_lines
            ],
            "Total gnd energy": [
                line.pg_energy for line in device.sect_lines
            ],
            "Ground fault conductor damage": [
                _evaluate_damage(line, fault_type='Ground')
                for line in device.sect_lines
            ],
        })
        line_list.append(line_df)

        if not line_list:
            return pd.DataFrame(columns=COND_DAMAGE_COLUMNS)

        cond_damage_df = pd.concat(line_list, ignore_index=True)
        return cond_damage_df.reindex(columns=COND_DAMAGE_COLUMNS)


def _allowable_energy(thermal_rating) -> Optional[float]:
    """
    Calculate the conductor thermal withstand energy in A²s.

    The 1-second thermal rating I_thr implies a withstand energy of
    I_thr² × 1s (from I²t = constant).

    Args:
        thermal_rating: Conductor 1-second thermal rating in Amperes.
            May be "NA" for cable systems.

    Returns:
        Withstand energy in A²s, or None if the rating is missing
        or non-numeric.
    """
    try:
        return float(thermal_rating) ** 2
    except (ValueError, TypeError):
        return None


def _evaluate_damage(line, fault_type: str) -> str:
    """
    Evaluate conductor damage pass/fail status for a line section.

    Compares the total let-through energy accumulated across ALL trips
    in the auto-reclose sequence (populated by cond_damage) against the
    conductor's thermal withstand energy.

    Args:
        line: Line dataclass with accumulated energy and thermal data.
        fault_type: 'Phase' or 'Ground' fault evaluation.

    Returns:
        Assessment result string:
        - "PASS": Accumulated energy within thermal withstand
        - "FAIL": Accumulated energy exceeds thermal withstand
        - "NO DATA": Missing thermal rating or no energy computed
        - "SWER": Phase fault on SWER line (not applicable)
    """
    # Check for SWER line - phase faults not applicable
    if fault_type == 'Phase':
        try:
            line_type = line.obj.typ_id
            if line_type and 'SWER' in line_type.loc_name:
                return "SWER"
        except AttributeError:
            pass

    if fault_type == 'Phase':
        energy = line.ph_energy
    else:
        energy = line.pg_energy

    allowable = _allowable_energy(line.thermal_rating)

    if allowable is None or not energy:
        return "NO DATA"
    if energy > allowable:
        return "FAIL"
    return "PASS"