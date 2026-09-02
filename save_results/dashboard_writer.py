"""
CSV persistence for the Power BI dashboard facts.

This module owns the on-disk schema. build_dashboard_facts produces
dicts; this writer fixes the column order, prepends run identity, and
writes CSVs whose shape never varies from run to run - Power BI's
Folder connector combines files by position and silently misbehaves
when columns drift, so the schema lives here as explicit constants
rather than being inferred from whatever keys a row happens to have.

Layout under the dashboard root (the ProtectionBatchRunner directory):

    dashboard_data/
        runs/<run_id>/devices_<project>.csv   accumulating history,
                                              one file per project per
                                              run - the trend source
        latest_lines/<project>.csv            overwritten every run -
                                              current-state drill-through
                                              and threshold re-cutting

The run manifest is written by the batch runner, which owns run
identity and per-project outcomes; this module only ever writes fact
files for the single active project.

Writes are atomic (temp file then os.replace) so a crash mid-write
cannot leave a truncated CSV to poison the folder combine. Note the
latest_lines folder is only ever added to or overwritten: a project
removed from the fleet leaves its last lines file behind until deleted
by hand.
"""

import csv
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from save_results.dashboard_facts import build_dashboard_facts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------

IDENTITY_COLUMNS = ['run_id', 'written', 'project']

DEVICE_FACT_COLUMNS = [
    'feeder', 'device', 'region', 'pipeline_region', 'backup_devices',
    'ph_coord_margin_s', 'ph_coord_fl_a',
    'pg_coord_margin_s', 'pg_coord_fl_a',
    'coord_margin_threshold_s',
    'pri_rf_threshold', 'bu_rf_threshold', 'sn_bu_rf_threshold',
    'oh_km', 'oh_lines_no_length',
    'ph_dmg_km', 'pg_dmg_km', 'any_dmg_km',
    'ph_dmg_unassessable_km', 'pg_dmg_unassessable_km',
    'any_dmg_unassessable_km',
    'pri_reach_km', 'bu_reach_km', 'sn_bu_reach_km',
    'pri_reach_unassessable_km', 'bu_reach_unassessable_km',
    'sn_bu_reach_unassessable_km',
]

LINE_FACT_COLUMNS = [
    'feeder', 'device', 'region', 'line', 'constr', 'overhead',
    'length_km', 'line_type',
    'ph_dmg_status', 'pg_dmg_status',
    'pri_reach_status', 'bu_reach_status', 'sn_bu_reach_status',
    'ef_rf', 'nps_ef_rf', 'ph_rf', 'nps_ph_rf',
    'bu_ef_rf', 'bu_nps_ef_rf', 'bu_ph_rf', 'bu_nps_ph_rf',
    'sn_bu_ef_rf', 'sn_bu_nps_ef_rf', 'sn_bu_ph_rf', 'sn_bu_nps_ph_rf',
]

DEVICE_COLUMNS = IDENTITY_COLUMNS + DEVICE_FACT_COLUMNS
LINE_COLUMNS = IDENTITY_COLUMNS + LINE_FACT_COLUMNS


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def write_dashboard_facts(
    dashboard_root: Path,
    run_id: str,
    project_name: str,
    pipeline_region: str,
    feeders: List,
    include_line_rows: bool = True,
) -> Dict[str, Optional[str]]:
    """
    Build and persist dashboard facts for the active project.

    Args:
        dashboard_root: The dashboard_data directory. Created if
            absent, along with runs/<run_id> and latest_lines.
        run_id: Fleet run identifier (the batch runner mints one per
            run; standalone callers can pass a timestamp).
        project_name: Active project name; recorded on every row and
            used (sanitised) in filenames.
        pipeline_region: 'SEQ' or 'Regional Models'.
        feeders: Fully populated Feeder dataclasses.
        include_line_rows: Also refresh latest_lines/<project>.csv.

    Returns:
        Dict with 'devices_csv' and 'lines_csv' paths (str or None)
        and 'device_rows' / 'line_rows' counts, suitable for inclusion
        in the begin() summary.
    """
    device_rows, line_rows = build_dashboard_facts(
        pipeline_region, feeders, include_line_rows=include_line_rows
    )

    written = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S%z')
    identity = {
        'run_id': run_id,
        'written': written,
        'project': project_name,
    }
    for row in device_rows:
        row.update(identity)
    for row in line_rows:
        row.update(identity)

    safe_name = _sanitise(project_name)

    run_dir = Path(dashboard_root) / 'runs' / _sanitise(run_id)
    devices_path = run_dir / f'devices_{safe_name}.csv'
    _atomic_write_csv(devices_path, DEVICE_COLUMNS, device_rows, 'device')
    logger.info(
        'Dashboard facts: %s device row(s) -> %s',
        len(device_rows), devices_path,
    )

    lines_path = None
    if include_line_rows:
        lines_path = (
            Path(dashboard_root) / 'latest_lines' / f'{safe_name}.csv'
        )
        _atomic_write_csv(lines_path, LINE_COLUMNS, line_rows, 'line')
        logger.info(
            'Dashboard facts: %s line row(s) -> %s',
            len(line_rows), lines_path,
        )

    return {
        'devices_csv': str(devices_path),
        'lines_csv': str(lines_path) if lines_path else None,
        'device_rows': len(device_rows),
        'line_rows': len(line_rows),
    }


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------

def _sanitise(name: str) -> str:
    """
    Make a string safe for use in a filename.

    Args:
        name: Raw project or run name.

    Returns:
        String with every run of non-word characters collapsed to '_'.
    """
    return re.sub(r'[^\w\-]+', '_', str(name)).strip('_')


def _check_schema(columns: List[str], rows: List[Dict], label: str) -> None:
    """
    Log any drift between the fixed schema and the produced rows.

    Extra keys are dropped (extrasaction='ignore'); missing keys are
    written blank. Either way the file shape stays constant - but
    drift means dashboard_facts and this writer have diverged and one
    of them needs updating, so it is logged loudly rather than passed
    over.

    Args:
        columns: The fixed column list.
        rows: Produced rows.
        label: 'device' or 'line', for the log message.
    """
    if not rows:
        return
    row_keys = set(rows[0])
    expected = set(columns)
    extra = row_keys - expected
    missing = expected - row_keys
    if extra:
        logger.error(
            '%s rows carry keys absent from the CSV schema (dropped): %s. '
            'dashboard_facts has grown a column the writer does not know.',
            label, ', '.join(sorted(extra)),
        )
    if missing:
        logger.error(
            '%s rows are missing schema keys (written blank): %s.',
            label, ', '.join(sorted(missing)),
        )


def _atomic_write_csv(
    path: Path, columns: List[str], rows: List[Dict], label: str
) -> None:
    """
    Write a CSV via a temp file and os.replace.

    A crash mid-write leaves only a .tmp file, never a truncated CSV;
    Power BI's folder combine sees either the old complete file or the
    new complete file.

    Args:
        path: Destination path.
        columns: Fixed column order.
        rows: Row dicts.
        label: 'device' or 'line', for schema drift logging.
    """
    _check_schema(columns, rows, label)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix('.csv.tmp')

    with open(tmp_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, extrasaction='ignore', restval=''
        )
        writer.writeheader()
        writer.writerows(rows)

    os.replace(tmp_path, path)