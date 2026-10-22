"""
Legacy SEQ fault level study output.

Reshapes the assessed feeders into the nested dictionaries the legacy
SEQ fault level workbook expects, then writes that workbook. Carried
unchanged in layout until the legacy study is retired.

Functions:
    bridge_results: Build and save the legacy workbook.
    get_conductor: Conductor type and 1 s thermal rating of a line.
    substation_name: Substation name used in titles and the file name.
"""

import math

from interactive.legacy_script import legacy_workbook as sr


def _site(terms, attr: str, value):
    """
    Name the first terminal whose attribute equals a device summary value.

    The device summaries are taken from these terminals, so a match
    normally exists. It does not where no terminal had a result (the
    summary is then 0) or where the summary is impedance-corrected
    (Regional minimum earth faults); "NA" is returned instead of
    raising IndexError.

    Args:
        terms: The device's sect_terms.
        attr: Termination attribute name, e.g. 'max_fl_3ph'.
        value: The device-level value to locate.

    Returns:
        Terminal name, or "NA".
    """
    return next(
        (term.obj.loc_name for term in terms
         if getattr(term, attr, None) == value),
        "NA"
    )


def bridge_results(app, external_grid, feeders):
    """
    Build and save the legacy SEQ fault level workbook.

    Args:
        app: PowerFactory application instance.
        external_grid: Grid object to its 15 fault level values.
        feeders: Feeder dataclasses after run_assessment.
    """

    sub_name = substation_name(app)

    feeders_devices_inrush = {}
    results_max_3p = {}
    results_max_2p = {}
    results_max_pg = {}
    results_min_2p = {}
    results_min_3p = {}
    results_min_pg = {}
    result_sys_norm_min_2p = {}
    result_sys_norm_min_pg = {}
    feeders_sections_trmax_size = {}
    results_max_tr_3p = {}
    results_max_tr_pg = {}
    results_all_max_3p = {}
    results_all_max_2p = {}
    results_all_max_pg = {}
    results_all_min_3p = {}
    results_all_min_2p = {}
    results_all_min_pg = {}
    result_all_sys_norm_min_2p = {}
    result_all_sys_norm_min_pg = {}
    feeders_devices_load = {}
    results_lines_max_3p = {}
    results_lines_max_2p = {}
    results_lines_max_pg = {}
    results_lines_min_3p = {}
    results_lines_min_2p = {}
    results_lines_min_pg = {}
    result_lines_sys_norm_min_2p = {}
    result_lines_sys_norm_min_pg = {}
    result_lines_type = {}
    result_lines_therm_rating = {}
    fdrs_open_switches = {}

    for fdr in feeders:
        feeder = fdr.obj.loc_name
        devices = fdr.devices
        devices_inrush = {}
        devices_max_3p = {}
        devices_max_2p = {}
        devices_max_pg = {}
        devices_min_2p = {}
        devices_min_3p = {}
        devices_min_pg = {}
        devices_sys_norm_min_2p = {}
        devices_sys_norm_min_pg = {}
        sections_trmax_size = {}
        devices_max_tr_3p = {}
        devices_max_tr_pg = {}
        devices_all_max_3p = {}
        devices_all_max_2p = {}
        devices_all_max_pg = {}
        devices_all_min_3p = {}
        devices_all_min_2p = {}
        devices_all_min_pg = {}
        devices_all_sys_norm_min_2p = {}
        devices_all_sys_norm_min_pg = {}
        devices_load = {}
        devices_lines_max_3p = {}
        devices_lines_max_2p = {}
        devices_lines_max_pg = {}
        devices_lines_min_3p = {}
        devices_lines_min_2p = {}
        devices_lines_min_pg = {}
        devices_lines_sys_norm_min_2p = {}
        devices_lines_sys_norm_min_pg = {}
        devices_lines_type = {}
        devices_lines_therm_rating = {}
        for device in devices:
            devices_inrush[device.obj.loc_name] = device.ds_capacity * 12 / (11 * math.sqrt(3))
            devices_max_3p[device.obj.loc_name] = {_site(device.sect_terms, 'max_fl_3ph', device.max_fl_3ph): device.max_fl_3ph}
            devices_max_2p[device.obj.loc_name] = {_site(device.sect_terms, 'max_fl_2ph', device.max_fl_2ph): device.max_fl_2ph}
            devices_max_pg[device.obj.loc_name] = {_site(device.sect_terms, 'max_fl_pg', device.max_fl_pg): device.max_fl_pg}
            devices_min_3p[device.obj.loc_name] = {_site(device.sect_terms, 'min_fl_3ph', device.min_fl_3ph): device.min_fl_3ph}
            devices_min_2p[device.obj.loc_name] = {_site(device.sect_terms, 'min_fl_2ph', device.min_fl_2ph): device.min_fl_2ph}
            devices_min_pg[device.obj.loc_name] = {_site(device.sect_terms, 'min_fl_pg', device.min_fl_pg): device.min_fl_pg}
            devices_sys_norm_min_2p[device.obj.loc_name] = {_site(device.sect_terms, 'min_sn_fl_2ph', device.min_sn_fl_2ph): device.min_sn_fl_2ph}
            devices_sys_norm_min_pg[device.obj.loc_name] = {_site(device.sect_terms, 'min_sn_fl_pg', device.min_sn_fl_pg): device.min_sn_fl_pg}
            try:
                sections_trmax_size[device.obj.loc_name] = {[load.obj.loc_name for load in device.sect_loads if load.obj == device.max_ds_tr.obj][0]: device.max_ds_tr.load_kva}
                devices_max_tr_3p[device.obj.loc_name] = {
                    [load.obj.loc_name for load in device.sect_loads if load.obj == device.max_ds_tr.obj][
                        0]: device.max_ds_tr.max_ph}
                devices_max_tr_pg[device.obj.loc_name] = {
                    [load.obj.loc_name for load in device.sect_loads if load.obj == device.max_ds_tr.obj][
                        0]: device.max_ds_tr.max_pg}
            except IndexError:
                sections_trmax_size[device.obj.loc_name] = {"NA": "NA"}
                devices_max_tr_3p[device.obj.loc_name] = {"NA": "NA"}
                devices_max_tr_pg[device.obj.loc_name] = {"NA": "NA"}
            devices_all_max_3p[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'max_fl_3ph', 0) for term in device.sect_terms}
            devices_all_max_2p[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'max_fl_2ph', 0) for term in device.sect_terms}
            devices_all_max_pg[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'max_fl_pg', 0) for term in device.sect_terms}
            devices_all_min_3p[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'min_fl_3ph', 0) for term in device.sect_terms}
            devices_all_min_2p[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'min_fl_2ph', 0) for term in device.sect_terms}
            devices_all_min_pg[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'min_fl_pg', 0) for term in device.sect_terms}
            devices_all_sys_norm_min_2p[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'min_sn_fl_2ph', 0) for term in device.sect_terms}
            devices_all_sys_norm_min_pg[device.obj.loc_name] = {term.obj.loc_name: getattr(term, 'min_sn_fl_pg', 0) for term in device.sect_terms}

            loads = {term.obj.loc_name: "" for term in device.sect_terms}
            sect_loads = [load.term.loc_name for load in device.sect_loads]
            for load, value in loads.items():
                if load in sect_loads:
                    value = [draw.load_kva for draw in device.sect_loads if draw.term.loc_name == load][0]
                    loads[load] = value

            devices_load[device.obj.loc_name] = loads
            devices_lines_max_3p[device.obj.loc_name] = {line.obj: getattr(line, 'max_fl_3ph', 0) for line in device.sect_lines}
            devices_lines_max_2p[device.obj.loc_name] = {line.obj: getattr(line, 'max_fl_2ph', 0) for line in device.sect_lines}
            devices_lines_max_pg[device.obj.loc_name] = {line.obj: getattr(line, 'max_fl_pg', 0) for line in device.sect_lines}
            devices_lines_min_3p[device.obj.loc_name] = {line.obj: getattr(line, 'min_fl_3ph', 0) for line in device.sect_lines}
            devices_lines_min_2p[device.obj.loc_name] = {line.obj: getattr(line, 'min_fl_2ph', 0) for line in device.sect_lines}
            devices_lines_min_pg[device.obj.loc_name] = {line.obj: getattr(line, 'min_fl_pg', 0) for line in device.sect_lines}
            devices_lines_sys_norm_min_2p[device.obj.loc_name] = {line.obj: getattr(line, 'min_sn_fl_2ph', 0) for line in device.sect_lines}
            devices_lines_sys_norm_min_pg[device.obj.loc_name] = {line.obj: getattr(line, 'min_sn_fl_pg', 0) for line in device.sect_lines}
            devices_lines_type[device.obj.loc_name] = {line.obj: getattr(line, 'line_type', 0) for line in device.sect_lines}
            devices_lines_therm_rating[device.obj.loc_name] = {line.obj: _rating_amps(line.obj) for line in device.sect_lines}
        feeders_devices_inrush[feeder] = devices_inrush
        results_max_3p[feeder] = devices_max_3p
        results_max_2p[feeder] = devices_max_2p
        results_max_pg[feeder] = devices_max_pg
        results_min_3p[feeder] = devices_min_3p
        results_min_2p[feeder] = devices_min_2p
        results_min_pg[feeder] = devices_min_pg
        result_sys_norm_min_2p[feeder] = devices_sys_norm_min_2p
        result_sys_norm_min_pg[feeder] = devices_sys_norm_min_pg
        feeders_sections_trmax_size[feeder] = sections_trmax_size
        results_max_tr_3p[feeder] = devices_max_tr_3p
        results_max_tr_pg[feeder] = devices_max_tr_pg
        results_all_max_3p[feeder] = devices_all_max_3p
        results_all_max_2p[feeder] = devices_all_max_2p
        results_all_max_pg[feeder] = devices_all_max_pg
        results_all_min_3p[feeder] = devices_all_min_3p
        results_all_min_2p[feeder] = devices_all_min_2p
        results_all_min_pg[feeder] = devices_all_min_pg
        result_all_sys_norm_min_2p[feeder] = devices_all_sys_norm_min_2p
        result_all_sys_norm_min_pg[feeder] = devices_all_sys_norm_min_pg
        feeders_devices_load[feeder] = devices_load
        results_lines_max_3p[feeder] = devices_lines_max_3p
        results_lines_max_2p[feeder] = devices_lines_max_2p
        results_lines_max_pg[feeder] = devices_lines_max_pg
        results_lines_min_3p[feeder] = devices_lines_min_3p
        results_lines_min_2p[feeder] = devices_lines_min_2p
        results_lines_min_pg[feeder] = devices_lines_min_pg
        result_lines_sys_norm_min_2p[feeder] = devices_lines_sys_norm_min_2p
        result_lines_sys_norm_min_pg[feeder] = devices_lines_sys_norm_min_pg
        result_lines_type[feeder] = devices_lines_type
        result_lines_therm_rating[feeder] = devices_lines_therm_rating
        fdrs_open_switches[feeder] = fdr.open_points

    output = sr.output_results(app, sub_name, external_grid, feeders_devices_inrush,
                            results_max_3p, results_max_2p, results_max_pg, results_min_2p, results_min_3p,
                            results_min_pg,
                            result_sys_norm_min_2p, result_sys_norm_min_pg, feeders_sections_trmax_size,
                            results_max_tr_3p,
                            results_max_tr_pg, results_all_max_3p, results_all_max_2p, results_all_max_pg,
                            results_all_min_3p,
                            results_all_min_2p, results_all_min_pg, result_all_sys_norm_min_2p,
                            result_all_sys_norm_min_pg,
                            feeders_devices_load, results_lines_max_3p, results_lines_max_2p, results_lines_max_pg,
                            results_lines_min_3p, results_lines_min_2p, results_lines_min_pg,
                            result_lines_sys_norm_min_2p,
                            result_lines_sys_norm_min_pg, result_lines_type, result_lines_therm_rating,
                            fdrs_open_switches)
    sr.save_results(app, sub_name, output)

def _rating_amps(line):
    """
    Conductor 1 s thermal rating in A, or "NA" where there is none.

    Args:
        line: PowerFactory ElmLne.

    Returns:
        Rating in A, or "NA" (cable systems, towers, unknown types).
        Previously "NA" was multiplied by 1000, writing a 2000 character
        string into the workbook.
    """
    rating = get_conductor(line)[1]
    return rating if isinstance(rating, str) else rating * 1000


def get_conductor(line):
    """Looks at the tower geometry or the cable system to return the conductor type and thermal rating
    """
    construction = line.typ_id.GetClassName()

    if construction == 'TypGeo':
        TypCon = line.GetAttribute("e:pCondCir")
        conductor_type = TypCon.GetAttribute('loc_name')
        thermal_rating = round(TypCon.GetAttribute("e:Ithr"), 3)
    elif construction == 'TypCabsys':
        conductor_type = 'NA'
        thermal_rating = 'NA'
    elif construction == "TypLne":
        conductor_type = line.typ_id.GetAttribute('loc_name')
        thermal_rating = round(line.typ_id.GetAttribute("e:Ithr"), 3)
    elif construction == "TypTow":
        conductor_type = 'NA'
        thermal_rating = 'NA'
    else:
        conductor_type = 'NA'
        thermal_rating = 'NA'
    return [conductor_type, thermal_rating]


def substation_name(app):
    """Get the substation name"""

    sub_names = app.GetCalcRelevantObjects('*.ElmNet')
    subs = []
    for sub in sub_names:
        if sub.loc_name != "New Elements" and sub.loc_name != "Summary Grid" and sub.loc_name != "Boundary Subs":
            subs.append(sub.loc_name + " 11kV")
    sub_name = '_'.join(subs)
    return sub_name
