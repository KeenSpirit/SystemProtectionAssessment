"""
Comprehensive fault level analysis for PowerFactory distribution networks.

This module orchestrates the complete fault study workflow:
1. Build device hierarchy and identify downstream objects
2. Run short-circuit studies (max/min, 3ph/2ph/ground)
3. Extract fault currents for terminals and lines
4. Handle floating terminals at network endpoints
5. Update device protection settings with results

The main entry point is the fault_study() function which coordinates
all analysis steps for a feeder.

Functions:
    fault_study: Main orchestration function for fault analysis
    get_downstream_objects: Populate device downstream components
    us_ds_device: Establish upstream/downstream device relationships
    get_ds_capacity: Calculate downstream transformer capacity
    get_device_sections: Partition network into protection sections
    terminal_fls: Extract terminal fault currents from study results
    append_floating_terms: Handle floating terminal fault calculations
    grid_equivalence_check: Check if min equals system normal min
    reset_min_source_imp: Toggle external grid impedance values
    copy_min_fls: Copy min fault levels to system normal fields
    update_device_data: Populate device fault current summaries
    update_line_data: Populate line fault current data
"""

from typing import List, Dict, Union

import logging

logger = logging.getLogger(__name__)

from pf_config import pft
import pf_protection_helper as helper
from fault_study import analysis, study_templates, fault_impedance
from fault_study import floating_terminals as ft
import assets as ast
from importlib import reload

reload(analysis)
reload(ft)
reload(ast)
reload(fault_impedance)

def fault_study(
    app: pft.Application,
    external_grid: Dict,
    region: str,
    feeders: List[ast.Feeder]
) -> None:
    """
    Perform comprehensive fault level analysis for a list of feeders.

    Orchestrates the complete fault study workflow including topology
    analysis, short-circuit calculations, and result extraction.

    Args:
        app: PowerFactory application instance.
        external_grid: Dictionary of external grid objects and parameters.
        region: Network region ('SEQ' or 'Regional Models').
        feeders: List of feeder dataclasses to analyze.
        study_selections: List of selected study types.

    Side Effects:
        Populates fault current data on all Device, Termination, and Line
        dataclasses within the feeder structure.

    Study Configurations:
        Maximum studies: 3-Phase, 2-Phase, Ground
        Minimum studies: 3-Phase, 2-Phase, Ground, Ground Z10, Ground Z50
        System normal minimum: 2-Phase, Ground, Ground Z10, Ground Z50
    """

    logger.info(f"Fault level study begins")



    for feeder in feeders:
        # Build device topology
        get_downstream_objects(app, region, feeder.devices)
        us_ds_device(feeder.devices, feeder.bu_devices)
        get_ds_capacity(feeder.devices)
        get_device_sections(app, feeder.devices)

    # Define study configurations
    study_configs = [
        ('Max', 'Ground'), ('Max', '3-Phase'), ('Max', '2-Phase'),
        ('Min', 'Ground'), ('Min', '3-Phase'), ('Min', '2-Phase'),
        ('Min', 'Ground Z10'), ('Min', 'Ground Z50'),
    ]
    sn_study_configs = [
        ('Min', '2-Phase'), ('Min', 'Ground'), ('Min', 'Ground Z10'),
        ('Min', 'Ground Z50'),
    ]

    # Set all LV terminals OOS to speed up all-terminal short-circuit calculation
    if region == "Regional Models":
        terminals = app.GetCalcRelevantObjects("*.ElmTerm")
        term_dic = {}
        for term in terminals:
            if term.GetAttribute("uknom") < 1:
                term_dic[term] = term.GetAttribute("outserv")
                term.SetAttribute("outserv", 1)

    # Execute main fault studies
    for bound, fault_type in study_configs:
        analysis.short_circuit(app, bound, fault_type, consider_prot='None')
        for feeder in feeders:
            terminal_fls(feeder.devices, bound=bound, f_type=fault_type)

    # Handle system normal minimum studies
    if grid_equivalence_check(external_grid):
        for feeder in feeders:
            copy_min_fls(feeder.devices)
    else:
        # Grid impedances are mutated for the SN-min studies; the
        # finally guarantees restoration even if a study raises, so
        # an aborted feeder cannot leave the model with system-normal
        # source impedances.
        reset_min_source_imp(external_grid, sys_norm_min=True)
        try:
            for bound, fault_type in sn_study_configs:
                analysis.short_circuit(app, bound, fault_type, consider_prot='None')
                for feeder in feeders:
                    terminal_fls(feeder.devices, bound='SN_Min', f_type=fault_type)
        finally:
            reset_min_source_imp(external_grid, sys_norm_min=False)

    # Restore all Lv terminations to original state
    if region == "Regional Models":
        for term, status in term_dic.values():
            term.SetAttribute("outserv", status)

    for feeder in feeders:
        # Determine construction types for fault impedance selection
        fault_impedance.update_node_construction(feeder.devices)

        # Handle floating terminals
        floating_terms = ft.get_floating_terminals(feeder.obj, feeder.devices)
        append_floating_terms(app, external_grid, feeder.devices, floating_terms, consider_prot='None')

        # Update device and line data with results
        update_device_data(region, feeder.devices)
        update_line_data(app, region, feeder.devices)


def get_downstream_objects(
    app: pft.Application,
    region: str,
    devices: List[ast.Device]
) -> None:
    """
    Populate device objects with their downstream network components.

    Traverses the network topology from each device to identify
    downstream terminals, loads/transformers, and line segments.

    Args:
        app: PowerFactory application instance.
        region: Network region ('SEQ' or 'Regional Models').
        devices: List of Device dataclasses to populate.

    Side Effects:
        Updates sect_terms, sect_loads, and sect_lines for each device.

    Note:
        - Terminals must have voltage > 1kV to be included
        - SEQ region uses ElmLod for loads
        - Regional models use ElmTr2 (excluding regulators)
    """
    all_grids = app.GetCalcRelevantObjects('*.ElmXnet')
    grids = [grid for grid in all_grids if grid.outserv == 0]

    for device in devices:
        terminals = [device.term]
        loads = []
        lines = []

        down_devices = device.cubicle.GetAll(1, 0)

        # If external grid is downstream, search in opposite direction
        if any(item in grids for item in down_devices):
            down_objs = device.cubicle.GetAll(0, 0)
        else:
            down_objs = down_devices

        for obj in down_objs:
            class_name = obj.GetClassName()

            if class_name == ast.ElementType.TERM.value and obj.uknom > 1:
                terminals.append(obj)
            if class_name == ast.ElementType.LOAD.value and region == 'SEQ':
                loads.append(obj)
            if class_name == ast.ElementType.TFMR.value and region == 'Regional Models':
                load_type = obj.typ_id
                if "Regulators" not in load_type.GetFullName():
                    loads.append(obj)
            if class_name == ast.ElementType.LINE.value:
                lines.append(obj)

        device.sect_terms = terminals
        device.sect_loads = loads
        device.sect_lines = lines


def us_ds_device(
    devices: List[ast.Device],
    bu_devices: Dict
) -> None:
    """
    Establish upstream and downstream device relationships.

    Determines which devices are upstream (backup) and downstream of
    each device based on terminal inclusion in protection sections.

    Args:
        devices: List of Device dataclasses.
        bu_devices: Dictionary of backup devices by external grid.

    Side Effects:
        Populates us_devices and ds_devices lists for each device.
    """
    for device in devices:
        us_devices = []

        for other_device in devices:
            if other_device == device:
                continue
            if device.term in other_device.sect_terms:
                us_devices.append(other_device)

        if us_devices:
            # Select device with smallest section as immediate backup
            bu_device = min(us_devices, key=lambda item: len(item.sect_terms))
            device.us_devices.append(bu_device)
            bu_device.ds_devices.append(device)

        # Check external grid backup devices
        if not device.us_devices:
            connected = device.cubicle.GetAll(1, 0) + device.cubicle.GetAll(0, 0)
            if bu_devices:
                for grid, grid_devices in bu_devices.items():
                    if grid in connected:
                        device.us_devices.extend(grid_devices)


def get_ds_capacity(devices: List[ast.Device]) -> None:
    """
    Calculate total downstream transformer capacity for each device.

    Sums the kVA ratings of all loads/transformers downstream of
    each protection device.

    Args:
        devices: List of Device dataclasses with sect_loads populated.

    Side Effects:
        Sets ds_capacity attribute on each device.
    """
    def _get_load(obj: pft.ElmLod) -> float:
        """Extract kVA rating from load or transformer."""
        if obj.GetClassName() == ast.ElementType.LOAD.value:
            return obj.Strat
        return obj.Snom_a * 1000

    for device in devices:
        device.ds_capacity = round(
            sum([_get_load(obj) for obj in device.sect_loads])
        )


def get_device_sections(app: pft.Application, devices: List[ast.Device]) -> None:
    """
    Partition network into protection sections for each device.

    Removes overlapping elements between device sections so each
    element is assigned to only its most immediate protection device.

    Args:
        app: PowerFactory application instance.
        devices: List of Device dataclasses with downstream objects.

    Side Effects:
        Converts sect_terms, sect_loads, sect_lines to dataclass lists
        with only the elements belonging to each device's section.
    """
    def _sections(devices_objs):
        """Remove overlapping elements from larger sections."""
        sorted_keys = sorted(
            devices_objs,
            key=lambda k: len(devices_objs[k]),
            reverse=True
        )

        for i, key1 in enumerate(sorted_keys):
            for key2 in sorted_keys[i + 1:]:
                set1 = set(devices_objs[key1])
                set2 = set(devices_objs[key2])
                common_elements = set1 & set2 - {key2}
                devices_objs[key1] = [
                    elem for elem in devices_objs[key1]
                    if elem not in common_elements
                ]
        return devices_objs

    # Build dictionaries for sectioning
    devices_terms = {device.term: device.sect_terms for device in devices}
    devices_loads = {device.term: device.sect_loads for device in devices}
    devices_lines = {device.term: device.sect_lines for device in devices}

    oh_lines_set = set(
        ln for ln in helper.active_lines(app, True)
        if not ln.IsCable()
        if "CABLE" not in ln.loc_name
        if ln.typ_id is not None
        if not ln.typ_id.HasAttribute("cohl_")
    )

    sectioned_terms = _sections(devices_terms)
    sectioned_loads = _sections(devices_loads)
    sectioned_lines = _sections(devices_lines)

    # Apply sectioning and convert to dataclasses
    for device in devices:
        section_terms = sectioned_terms[device.term]
        dataclass_terms = [
            ast.initialise_term_dataclass(elmterm)
            for elmterm in section_terms
        ]
        device.sect_terms = dataclass_terms

        section_loads = sectioned_loads[device.term]
        dataclass_loads = [
            ast.initialise_load_dataclass(elmlod)
            for elmlod in section_loads
        ]
        device.sect_loads = dataclass_loads

        section_lines = sectioned_lines[device.term]
        dataclass_lines = [
            ast.initialise_line_dataclass(
                elmlne, oh_lines=oh_lines_set
            )
            for elmlne in section_lines
        ]
        device.sect_lines = dataclass_lines


# Maps a (bound, fault type) study combination to the Termination
# attribute that receives the resulting fault current.
# An unrecognised combination raises rather than silently writing
# to the 2-phase attribute.
_TERMINAL_FL_ATTR = {
    ('Max', 'Ground'): 'max_fl_pg',
    ('Max', '3-Phase'): 'max_fl_3ph',
    ('Max', '2-Phase'): 'max_fl_2ph',
    ('Min', 'Ground'): 'min_fl_pg',
    ('Min', 'Ground Z10'): 'min_fl_pg10',
    ('Min', 'Ground Z50'): 'min_fl_pg50',
    ('Min', '3-Phase'): 'min_fl_3ph',
    ('Min', '2-Phase'): 'min_fl_2ph',
    ('SN_Min', 'Ground'): 'min_sn_fl_pg',
    ('SN_Min', 'Ground Z10'): 'min_sn_fl_pg10',
    ('SN_Min', 'Ground Z50'): 'min_sn_fl_pg50',
    ('SN_Min', '2-Phase'): 'min_sn_fl_2ph',
}


def terminal_fls(
    devices: List[ast.Device],
    bound: str,
    f_type: str
) -> None:
    """
    Extract terminal fault currents from short-circuit study results.

    Reads fault current results from PowerFactory and stores them
    in the appropriate Termination dataclass attributes.

    Args:
        devices: List of Device dataclasses with sect_terms.
        bound: Study bound - 'Max', 'Min', or 'SN_Min'.
        f_type: Fault type - 'Ground', 'Ground Z10', 'Ground Z50',
            '3-Phase', or '2-Phase'.

    Raises:
        ValueError: If the (bound, f_type) combination is not
            recognised.

    Side Effects:
        Sets fault current attributes on Termination dataclasses.
    """
    attribute = _TERMINAL_FL_ATTR.get((bound, f_type))
    if attribute is None:
        raise ValueError(
            f"Unknown fault study combination: bound={bound!r}, "
            f"f_type={f_type!r}"
        )

    for device in devices:
        for terminal in device.sect_terms:
            current = analysis.get_terminal_current(terminal.obj)
            setattr(terminal, attribute, current)


def append_floating_terms(
    app: pft.Application,
    external_grid: Dict,
    devices: List[ast.Device],
    floating_terms: Dict,
    consider_prot: str
) -> None:
    """
    Calculate fault currents for floating terminals.

    Floating terminals require fault calculations at specific line
    locations rather than at busbars. This function performs those
    calculations and adds the results to device section terminals.

    Args:
        app: PowerFactory application instance.
        external_grid: Dictionary of external grid parameters.
        devices: List of Device dataclasses.
        floating_terms: Dictionary from get_floating_terminals().
        consider_prot: Protection consideration mode.

    Side Effects:
        Appends Termination dataclasses to device.sect_terms.
    """
    for dev, lines in floating_terms.items():
        for line, elmterm in lines.items():
            # Determine fault location percentage
            if line.bus1.cterm == elmterm:
                ppro = 1
            else:
                ppro = 99

            termination = ast.initialise_term_dataclass(elmterm)

            # Run fault studies at the line location
            study_configs = [
                ('Max', '3-Phase', 'max_fl_3ph'),
                ('Max', '2-Phase', 'max_fl_2ph'),
                ('Max', 'Ground', 'max_fl_pg'),
                ('Min', '3-Phase', 'min_fl_3ph'),
                ('Min', '2-Phase', 'min_fl_2ph'),
                ('Min', 'Ground', 'min_fl_pg'),
                ('Min', 'Ground Z10', 'min_fl_pg10'),
                ('Min', 'Ground Z50', 'min_fl_pg50'),
            ]

            for bound, fault_type, attribute in study_configs:
                analysis.short_circuit(
                    app, bound, fault_type, consider_prot,
                    location=line, relative=ppro
                )
                current = analysis.get_line_current(line)
                if current is None:
                    logger.warning(
                        f"Floating terminal fault study returned no result: "
                        f"{elmterm.loc_name} on line {line.loc_name} "
                        f"({bound} {fault_type}); value left as None"
                    )
                setattr(termination, attribute, current)

            # Handle system normal minimum
            if grid_equivalence_check(external_grid):
                termination.min_sn_fl_pg = termination.min_fl_pg
                termination.min_sn_fl_pg10 = termination.min_fl_pg10
                termination.min_sn_fl_pg50 = termination.min_fl_pg50
                termination.min_sn_fl_2ph = termination.min_fl_2ph
            else:
                sn_configs = [
                    ('Min', '2-Phase', 'min_sn_fl_2ph'),
                    ('Min', 'Ground', 'min_sn_fl_pg'),
                    ('Min', 'Ground Z10', 'min_sn_fl_pg10'),
                    ('Min', 'Ground Z50', 'min_sn_fl_pg50'),
                ]
                reset_min_source_imp(external_grid, sys_norm_min=True)
                for bound, fault_type, attribute in sn_configs:
                    analysis.short_circuit(
                        app, bound, fault_type, consider_prot,
                        location=line, relative=ppro
                    )
                    current = analysis.get_line_current(line)
                    if current is None:
                        logger.warning(
                            f"Floating terminal fault study returned no result: "
                            f"{elmterm.loc_name} on line {line.loc_name} "
                            f"({bound} {fault_type}); value left as None"
                        )
                    setattr(termination, attribute, current)
                reset_min_source_imp(external_grid, sys_norm_min=False)

            # Add to device section terminals
            sect_terms = [
                device.sect_terms for device in devices
                if device.term == dev
            ][0]
            sect_terms.append(termination)

    # Reset short-circuit command to default state
    comshc = app.GetFromStudyCase("Short_Circuit.ComShc")
    study_templates.apply_sc(comshc, bound='Max', f_type='Ground',
                             consider_prot='All')


def grid_equivalence_check(new_grid_data: Dict) -> bool:
    """
    Check if minimum equals system normal minimum grid impedance.

    If the grid impedance values are identical, system normal minimum
    calculations can be skipped by copying the minimum values.

    Args:
        new_grid_data: Dictionary of external grid parameters.

    Returns:
        True if minimum and system normal minimum are identical.
    """
    for grid, attributes in new_grid_data.items():
        for i in range(5, 10):
            if attributes[i] != attributes[i + 5]:
                return False
    return True


_original_grid_outserv: Dict = {}


def reset_min_source_imp(new_grid_data: Dict,
    sys_norm_min: bool = False
) -> None:
    """
    Toggle external grid impedance between minimum and system normal.

    Temporarily modifies external grid parameters for system normal
    minimum fault calculations.

    Args:
        new_grid_data: Dictionary of external grid parameters.
        sys_norm_min: If True, set system normal minimum values.
            If False, restore standard minimum values.

    Side Effects:
        Modifies external grid PowerFactory attributes.
    """

    for grid, attributes in new_grid_data.items():
        if sys_norm_min:
            if attributes[10] <= 0:
                # Record current service state before forcing the grid
                # out of service, so the restore call can return it
                # exactly rather than re-energising it.
                _original_grid_outserv[grid] = grid.GetAttribute('outserv')
                grid.SetAttribute('outserv', 1)
            else:
                grid.SetAttribute('ikssmin', attributes[10])
                grid.SetAttribute('rntxnmin', attributes[11])
                grid.SetAttribute('z2tz1min', attributes[12])
                grid.SetAttribute('x0tx1min', attributes[13])
                grid.SetAttribute('r0tx0min', attributes[14])
        else:
            grid.SetAttribute('ikssmin', attributes[5])
            grid.SetAttribute('rntxnmin', attributes[6])
            grid.SetAttribute('z2tz1min', attributes[7])
            grid.SetAttribute('x0tx1min', attributes[8])
            grid.SetAttribute('r0tx0min', attributes[9])

            # Restore the original service state only for grids this
            # function forced out of service; grids it never touched
            # keep their current state.
            if grid in _original_grid_outserv:
                grid.SetAttribute(
                    'outserv', _original_grid_outserv.pop(grid)
                )


def copy_min_fls(devices: List[ast.Device]) -> None:
    """
    Copy minimum fault levels to system normal minimum fields.

    Used when grid impedance values are identical for minimum and
    system normal minimum calculations.

    Args:
        devices: List of Device dataclasses with populated terminals.

    Side Effects:
        Sets min_sn_fl_* attributes on Termination dataclasses.
    """
    for device in devices:
        for terminal in device.sect_terms:
            terminal.min_sn_fl_pg = terminal.min_fl_pg
            terminal.min_sn_fl_pg10 = terminal.min_fl_pg10
            terminal.min_sn_fl_pg50 = terminal.min_fl_pg50
            terminal.min_sn_fl_2ph = terminal.min_fl_2ph


def _line_safe_min(sequence) -> Union[int, float]:
    """Return minimum of non-None values, or 0 if none exist."""
    values = [v for v in sequence if v is not None]
    return min(values) if values else 0


def _line_safe_max(sequence) -> Union[int, float]:
    """Return maximum of non-None values, or 0 if none exist."""
    values = [v for v in sequence if v is not None]
    return max(values) if values else 0


def update_device_data(region: str, devices: List[ast.Device]) -> None:
    """
    Populate device-level fault current summaries from section data.

    Calculates maximum and minimum fault currents across all terminals
    in each device's protection section.

    Args:
        region: Network region for fault impedance selection.
        devices: List of Device dataclasses with populated terminals.

    Side Effects:
        Sets fault current attributes and max_ds_tr on each device.
        Sorts sect_terms by min_fl_pg descending.
    """

    def _safe_max(sequence: List) -> Union[int, float]:
        """Return maximum of non-None values, or 0 if none exist."""
        values = [v for v in sequence if v is not None]
        return max(values) if values else 0

    def _safe_min(sequence: List) -> Union[int, float]:
        """Return minimum of non-None values, or 0 if none exist."""
        values = [v for v in sequence if v is not None]
        return min(values) if values else 0

    for device in devices:
        # Find largest downstream transformer
        max_tr_size = _safe_max(
            [load.load_kva for load in device.sect_loads]
        )
        max_ds_trs = [
            load for load in device.sect_loads
            if load.load_kva == max_tr_size
        ]

        # Transformer must have terminal in section
        sect_terms = [term.obj for term in device.sect_terms]
        max_ds_trs = [
            load for load in max_ds_trs
            if load.term in sect_terms
        ]

        # Initialize max transformer
        try:
            max_ds_tr = max_ds_trs[0]
        except IndexError:
            max_ds_tr = ast.initialise_load_dataclass(None)

        # Find transformer with highest fault level
        max_fl_pg = 0
        for tr in max_ds_trs:
            term_dataclass = [
                t for t in device.sect_terms if t.obj == tr.term
            ][0]
            if term_dataclass.max_fl_pg >= max_fl_pg:
                max_fl_pg = term_dataclass.max_fl_pg
                tr.term = term_dataclass.obj
                tr.max_pg = term_dataclass.max_fl_pg
                tr.max_ph = max(
                    term_dataclass.max_fl_3ph, term_dataclass.max_fl_2ph
                )
                max_ds_tr = tr
        device.max_ds_tr = max_ds_tr

        # Calculate device fault current summaries
        device.max_fl_3ph = _safe_max(
            [term.max_fl_3ph for term in device.sect_terms]
        )
        device.max_fl_2ph = _safe_max(
            [term.max_fl_2ph for term in device.sect_terms]
        )
        device.max_fl_pg = _safe_max(
            [term.max_fl_pg for term in device.sect_terms]
        )
        device.min_fl_3ph = _safe_min(
            [term.min_fl_3ph for term in device.sect_terms
             if term.min_fl_3ph and term.min_fl_3ph > 0]
        )
        device.min_fl_2ph = _safe_min(
            [term.min_fl_2ph for term in device.sect_terms
             if term.min_fl_2ph and term.min_fl_2ph > 0]
        )
        device.min_fl_pg = _safe_min(
            [fault_impedance.get_terminal_pg_fault(region, term)
             for term in device.sect_terms if term.min_fl_pg
             and term.min_fl_pg > 0]
        )
        device.min_sn_fl_2ph = _safe_min(
            [term.min_sn_fl_2ph for term in device.sect_terms
             if term.min_sn_fl_2ph and term.min_sn_fl_2ph > 0]
        )
        device.min_sn_fl_pg = _safe_min(
            [fault_impedance.get_terminal_pg_fault(region, term, True)
             for term in device.sect_terms if term.min_sn_fl_pg
             and term.min_sn_fl_pg > 0]
        )

        # Sort terminals by minimum fault level. min_fl_pg may be
        # None on floating terminals when the minimum fault study
        # produced no results (e.g. external grid minimums are zero);
        # sort those last rather than crashing on None comparison.
        device.sect_terms = sorted(
            device.sect_terms,
            key=lambda term: (
                term.min_fl_pg is not None,
                term.min_fl_pg if term.min_fl_pg is not None else 0
            ),
            reverse=True
        )


def update_line_data(
    app: pft.Application,
    region: str,
    devices: List[ast.Device]
) -> None:
    """
    Populate line fault current data for conductor damage assessment.

    Calculates maximum and minimum fault currents seen by each line
    for faults occurring within the device's protection section.

    Args:
        app: PowerFactory application instance.
        region: Network region for fault impedance selection.
        devices: List of Device dataclasses with populated terminals.

    Side Effects:
        Sets fault current attributes on Line dataclasses.
        Sorts sect_lines by min_fl_pg descending.
    """
    all_grids = app.GetCalcRelevantObjects('*.ElmXnet')
    grids = [grid for grid in all_grids if grid.outserv == 0]

    for device in devices:
        lines = device.sect_lines
        sect_term_obs = [term.obj for term in device.sect_terms]

        for line in lines:
            elmlne = line.obj
            lne_cubs = [
                cub for cub in [elmlne.bus1, elmlne.bus2]
                if cub is not None
            ]
            lne_term_obs = [cub.cterm for cub in lne_cubs]

            if any(terms in sect_term_obs for terms in lne_term_obs):
                line_terms = [
                    term for term in device.sect_terms
                    if term.obj in lne_term_obs
                ]

                # Maximum fault currents at line terminals. max_fl_*
                # may be None on floating terminals where the maximum
                # study produced no result for that line (e.g. 3-phase
                # or 2-phase studies on single-phase SWER spurs).
                line.max_fl_3ph = _line_safe_max(
                    term.max_fl_3ph for term in line_terms
                )
                line.max_fl_2ph = _line_safe_max(
                    term.max_fl_2ph for term in line_terms
                )
                line.max_fl_pg = _line_safe_max(
                    term.max_fl_pg for term in line_terms
                )

                # Find the terminal carrying the highest max_fl_pg,
                # treating None as lowest, so a terminal is always
                # selected even when no value equals the defaulted
                # line maximum.
                lne_max_term_obj = max(
                    line_terms,
                    key=lambda term: (
                        term.max_fl_pg is not None,
                        term.max_fl_pg if term.max_fl_pg is not None else 0
                    )
                ).obj
                max_lne_cub = [
                    cub for cub in lne_cubs
                    if cub.cterm == lne_max_term_obj
                ][0]

                down_elements = max_lne_cub.GetAll(1, 0)
                if any(item in grids for item in down_elements):
                    down_objs = max_lne_cub.GetAll(0, 0)
                else:
                    down_objs = down_elements

                line_ds_terms = [
                    obj for obj in down_objs
                    if obj.GetClassName() == ast.ElementType.TERM.value
                ]

                # Calculate minimum fault currents
                if line_ds_terms:
                    line.min_fl_3ph = _line_safe_min(
                        term.min_fl_3ph for term in device.sect_terms
                        if term.obj in line_ds_terms
                    )
                    line.min_fl_2ph = _line_safe_min(
                        term.min_fl_2ph for term in device.sect_terms
                        if term.obj in line_ds_terms
                    )
                    line.min_sn_fl_2ph = _line_safe_min(
                        term.min_sn_fl_2ph for term in device.sect_terms
                        if term.obj in line_ds_terms
                    )
                    line.min_fl_pg = _line_safe_min(
                        fault_impedance.get_terminal_pg_fault(region, term)
                        for term in device.sect_terms
                        if term.obj in line_ds_terms
                        and term.min_fl_pg and term.min_fl_pg > 0
                    )
                    line.min_sn_fl_pg = _line_safe_min(
                        [fault_impedance.get_terminal_pg_fault(region, term, True)
                         for term in device.sect_terms
                         if term.obj in line_ds_terms]
                    )
                else:
                    # Use line terminal values if no downstream terminals.
                    # Minimum fault values may be None where the minimum
                    # study produced no results (e.g. external grid
                    # minimums are zero); those terminals are skipped
                    # rather than crashing the min() comparison.
                    line.min_fl_3ph = _line_safe_min(
                        term.min_fl_3ph for term in line_terms
                    )
                    line.min_fl_2ph = _line_safe_min(
                        term.min_fl_2ph for term in line_terms
                    )
                    line.min_sn_fl_2ph = _line_safe_min(
                        term.min_sn_fl_2ph for term in line_terms
                    )
                    line.min_fl_pg = _line_safe_min(
                        fault_impedance.get_terminal_pg_fault(region, term)
                        for term in line_terms
                        if term.min_fl_pg and term.min_fl_pg > 0
                    )
                    line.min_sn_fl_pg = _line_safe_min(
                        fault_impedance.get_terminal_pg_fault(region, term, True)
                        for term in line_terms
                        if term.min_sn_fl_pg and term.min_sn_fl_pg > 0
                    )
            else:
                # Line not in section - set all to zero
                line.max_fl_3ph = 0
                line.max_fl_2ph = 0
                line.max_fl_pg = 0
                line.min_fl_3ph = 0
                line.min_fl_2ph = 0
                line.min_fl_pg = 0
                line.min_sn_fl_2ph = 0
                line.min_sn_fl_pg = 0

        # Sort lines by minimum fault level
        device.sect_lines = sorted(
            device.sect_lines,
            key=lambda line: line.min_fl_pg,
            reverse=True
        )