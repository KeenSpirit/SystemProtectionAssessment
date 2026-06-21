"""
Conductor damage assessment result formatting for Excel output.

This module formats conductor damage assessment results into a pandas
DataFrame suitable for Excel export. It calculates allowable fault
levels based on conductor thermal ratings and evaluates pass/fail
status for each line section.

Functions:
    cond_damage_results: Format conductor damage data as DataFrame
"""

import math
from typing import List

import pandas as pd

from relays import reclose


def cond_damage_results(devices: List) -> pd.DataFrame:
    """
    Format conductor damage assessment results for Excel export.

    Creates a DataFrame containing conductor damage evaluation for all
    line sections protected by each device. Includes fault levels,
    clearing times, allowable limits, and pass/fail status.

    Args:
        devices: List of Device dataclasses with populated sect_lines.

    Returns:
        DataFrame with columns:
        - Device: Protection device name
        - Trips: Number of trips in auto-reclose sequence
        - Line: Line section name
        - Line Type: Construction type string
        - Worst case energy ph flt lvl: Maximum phase fault current (A)
        - Worst case energy ph flt clear time: Phase clearing time (s)
        - Allowable phase fault level: Thermal limit for phase (A)
        - Phase fault conductor damage: PASS/FAIL/NO DATA/SWER
        - Worst case energy gnd flt lvl: Maximum ground fault current (A)
        - Worst case energy gnd flt clear time: Ground clearing time (s)
        - Allowable ground fault level: Thermal limit for ground (A)
        - Ground fault conductor damage: PASS/FAIL/NO DATA/SWER

    Note:
        SWER lines return "SWER" for phase fault assessment as phase
        faults are not applicable to single-wire earth return systems.

    Example:
        >>> df = cond_damage_results(feeder.devices)
        >>> df.to_excel(writer, sheet_name='Conductor Damage')
    """
    line_list = []

    for device in devices:
        trips = reclose.get_device_trips(device.obj)
        list_length = len(device.sect_lines)

        line_df = pd.DataFrame({
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
            "Allowable phase fault level": [
                _calculate_allowable_fl(line.thermal_rating, line.ph_clear_time, trips)
                for line in device.sect_lines
            ],
            "Phase fault conductor damage": [
                _evaluate_damage(line, trips, fault_type='Phase')
                for line in device.sect_lines
            ],
            "Worst case energy gnd flt lvl": [
                line.pg_fl for line in device.sect_lines
            ],
            "Worst case energy gnd flt clear time": [
                line.pg_clear_time for line in device.sect_lines
            ],
            "Allowable ground fault level": [
                _calculate_allowable_fl(line.thermal_rating, line.pg_clear_time, trips)
                for line in device.sect_lines
            ],
            "Ground fault conductor damage": [
                _evaluate_damage(line, trips, fault_type='Ground')
                for line in device.sect_lines
            ],
        })
        line_list.append(line_df)

    cond_damage_df = pd.concat(line_list)
    return cond_damage_df


def _calculate_allowable_fl(
    thermal_rating: float,
    clear_time: float,
    trips: int
) -> float:
    """
    Calculate the maximum allowable fault current for conductor protection.

    Uses the I²t thermal withstand formula to determine the maximum
    fault current that can flow for the given clearing time without
    exceeding the conductor's thermal limit.

    Formula: I_allowable = I_thermal / sqrt(t_clear × n_trips)

    Args:
        thermal_rating: Conductor 1-second thermal rating in Amperes.
        clear_time: Fault clearing time per trip in seconds.
        trips: Number of trips in the auto-reclose sequence.

    Returns:
        Maximum allowable fault current in Amperes, rounded to nearest
        integer. Returns None if calculation fails (e.g., zero time).
    """
    try:
        allowable_fl = round(thermal_rating / math.sqrt(clear_time * trips))
    except (ValueError, ZeroDivisionError, TypeError):
        allowable_fl = None
    return allowable_fl


def _evaluate_damage(line, trips: int, fault_type: str) -> str:
    """
    Evaluate conductor damage pass/fail status for a line section.

    Compares the actual fault current against the allowable thermal
    limit to determine if conductor damage would occur.

    Args:
        line: Line dataclass with fault current and thermal data.
        trips: Number of trips in the auto-reclose sequence.
        fault_type: 'Phase' or 'Ground' fault evaluation.

    Returns:
        Assessment result string:
        - "PASS": Fault current within thermal limits
        - "FAIL": Fault current exceeds thermal limits
        - "NO DATA": Missing thermal rating or clearing time
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

    thermal_rating = line.thermal_rating

    if fault_type == 'Phase':
        fl = line.ph_fl
        clear_time = line.ph_clear_time
    else:
        fl = line.pg_fl
        clear_time = line.pg_clear_time

    acceptable_fl = _calculate_allowable_fl(thermal_rating, clear_time, trips)

    if not acceptable_fl:
        return "NO DATA"
    elif fl > acceptable_fl:
        return "FAIL"
    else:
        return "PASS"