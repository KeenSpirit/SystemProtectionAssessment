"""
Fuse retrieval and creation for protection assessment.

This module provides functions for working with fuses in PowerFactory
models, including retrieval of existing fuses, creation of new fuses
for distribution transformers, and fuse type determination.

Functions:
    get_all_fuses: Retrieve all active line fuses from the model
    create_fuse: Create a fuse for a distribution transformer
    get_fuse_element: Match transformer size to appropriate fuse type
    f_types: Get available fuse types from the library
    determine_fuse_type: Determine if a fuse is a line fuse
"""


from importlib import reload
from pf_config import pft
from typing import Optional, List

from devices import fuse_mapping as fm
import pf_protection_helper as helper
import domain as dd
reload(fm)
reload(dd)


def get_all_fuses(app: pft.Application) -> List[pft.RelFuse]:
    """
    Retrieve all active line fuses from the PowerFactory model.

    Filters fuses to include only those that are:
    - Under the network model folder
    - Connected to a calculation-relevant grid
    - Located in a cubicle with a terminal connection
    - Connected to an energized terminal
    - Not out of service
    - Classified as line fuses (not distribution transformer fuses)

    Args:
        app: PowerFactory application instance.

    Returns:
        List of RelFuse objects meeting all filter criteria.

    Note:
        Distribution transformer fuses are excluded as they are not
        treated as protection devices in the protection section analysis.

    Example:
        >>> fuses = get_all_fuses(app)
        >>> print(f"Found {len(fuses)} active line fuses")
    """
    net_mod = app.GetProjectFolder("netmod")
    all_fuses = net_mod.GetContents("*.RelFuse", True)
    fuses = [
        fuse
        for fuse in all_fuses
        if fuse.cpGrid
        if fuse.cpGrid.IsCalcRelevant()
        if fuse.fold_id.HasAttribute("cterm")
        if fuse.fold_id.cterm.IsEnergized()
        if not fuse.IsOutOfService()
        if determine_fuse_type(fuse)
    ]
    return fuses


def create_fuse(
        app: pft.Application,
        ds_tr: Optional[dd.Tfmr],
        tr_term_dataclass: dd.Termination,
        sys_volts: str
) -> List[pft.RelFuse]:
    """
    Create a fuse element for a distribution transformer.

    Creates a new RelFuse object in the equipment folder with the
    appropriate fuse type based on the transformer size and location.
    If a fuse with the same name already exists, returns the existing
    fuse instead of creating a duplicate.

    Args:
        app: PowerFactory application instance.
        ds_tr: Transformer dataclass (Tfmr) for the downstream
            transformer. If None, returns empty list.
        tr_term_dataclass: Termination dataclass for the transformer's
            HV terminal.
        sys_volts: System voltage string for fuse selection.

    Returns:
        List containing the created or existing RelFuse object.
        Returns empty list if ds_tr is None or no matching fuse type.

    Example:
        >>> fuse_list = create_fuse(app, tfmr, term_dc, "11")
        >>> if fuse_list:
        ...     print(f"Fuse created: {fuse_list[0].loc_name}")
    """
    if ds_tr is None:
        return []

    typfuse = get_fuse_element(app, ds_tr, tr_term_dataclass, sys_volts)
    if typfuse is None:
        return []

    fuse_name = ds_tr.term.cpSubstat.loc_name
    equip = app.GetProjectFolder("equip")
    protection = helper.create_obj(equip, "Protection", "IntFolder")
    fuse_folder = helper.create_obj(protection, "Fuses", "IntFolder")
    lib_contents = fuse_folder.GetContents("*.RelFuse", 0)

    if lib_contents:
        for fuse in lib_contents:
            if fuse.loc_name == fuse_name:
                return [fuse]

    rel_fuse = fuse_folder.CreateObject("RelFuse", f'{fuse_name}')
    rel_fuse.loc_name = fuse_name
    rel_fuse.SetAttribute("typ_id", typfuse)
    return [rel_fuse]


def get_fuse_element(
        app: pft.Application,
        tfmr: dd.Tfmr,
        tr_term_dataclass: dd.Termination,
        system_volts: str
) -> Optional[pft.TypFuse]:
    """
    Match a transformer to the appropriate fuse type.

    Selects the correct fuse type based on:
    - Network region (SEQ or Regional)
    - Line construction type (SWER, overhead, underground/RMU)
    - Terminal voltage level (11kV, 22kV, 33kV)
    - Transformer size (kVA)
    - Number of phases
    - Transformer insulation type (air or oil) for RMU fuses

    Matching criteria per Technical Instruction TS0013i: RMU Fuse
    Selection Guide.

    Args:
        app: PowerFactory application instance.
        tfmr: Transformer dataclass with load_kva and insulation info.
        tr_term_dataclass: Termination dataclass for the transformer's
            HV terminal.
        system_volts: System voltage string for SWER isolation
            transformer selection.

    Returns:
        TypFuse object matching the transformer, or None if no match.

    Note:
        SEQ models use Energex fuse mapping tables.
        Regional models use Ergon Energy fuse mapping tables.
    """

    def _safe_string(dic, key):
        """Safely retrieve fuse string from mapping dictionary."""
        try:
            fuse_string = dic[key]
        except KeyError:
            fuse_string = None
        return fuse_string

    fuse_object = None
    region = helper.obtain_region(app)
    term = tr_term_dataclass
    if region == 'SEQ':
        if term.constr == dd.ConstructionType.SWER.value:
            fuse_string = _safe_string(fm.ex_SWER_f_sizes, tfmr.load_kva)
        elif term.constr == dd.ConstructionType.OVERHEAD.value:
            # OH fuse
            if term.phases == 1:
                fuse_string = _safe_string(fm.ex_pole_1p_fuses, tfmr.load_kva)
            else:
                fuse_string = _safe_string(fm.ex_pole_3p_fuses, tfmr.load_kva)
            fuse_types = f_types(app, 0)
        else:
            # RMU fuse
            if tfmr.insulation == 'air':
                if tfmr.load_kva < 750:
                    key = tfmr.load_kva
                elif tfmr.impedance == 'high':
                    key = f"{tfmr.load_kva} HighZ"
                else:
                    key = f"{tfmr.load_kva} LowZ"
                fuse_string = _safe_string(fm.ex_rmu_air_fuses, key)
            else:
                fuse_string = _safe_string(fm.ex_rmu_oil_fuses, tfmr.load_kva)
            fuse_types = f_types(app, 1)
    else:    # region == 'Regional Models':
        fuse_types = f_types(app, 0)
        term_volts = round(term.l_l_volts, 2)
        if term.constr == dd.ConstructionType.SWER.value:
            if tfmr.load_kva >= 100:
                # SWER isolating transformer
                if round(term_volts) == 11 and round(system_volts) == 11:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_11_11, tfmr.load_kva)
                elif round(term_volts) == 22 and round(system_volts) == 11:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_11_127, tfmr.load_kva)
                elif round(term_volts) == 33 and round(system_volts) == 11:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_11_191, tfmr.load_kva)
                elif round(term_volts) == 22 and round(system_volts) == 22:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_22_127, tfmr.load_kva)
                elif round(term_volts) == 33 and round(system_volts) == 22:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_22_191, tfmr.load_kva)
                elif round(term_volts) == 22 and round(system_volts) == 33:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_33_127, tfmr.load_kva)
                else: # term_volts == 33 and system_volts == 33:
                    fuse_string = _safe_string(fm.ee_swer_isol_tr_33_191, tfmr.load_kva)
            else:
                # SWER transformer
                if round(term_volts) == 11:
                    fuse_string = _safe_string(fm.ee_swer_dist_tr_11, tfmr.load_kva)
                elif round(term_volts) == 22:
                    fuse_string = _safe_string(fm.ee_swer_dist_tr_127, tfmr.load_kva)
                else: # 33kV
                    fuse_string = _safe_string(fm.ee_swer_dist_tr_191, tfmr.load_kva)
        else:
            # TR HV fuse
            # Assumed that all TR HV fuses are EDO, because pf doesn't currently contain any OH current limiting fuses
            if round(term_volts) == 11:
                if term.phases == 1:
                    fuse_string = _safe_string(fm.ee_tr_11_1p, tfmr.load_kva)
                else:
                    fuse_string = _safe_string(fm.ee_tr_11_3p, tfmr.load_kva)
            elif round(term_volts) == 22:
                if term.phases == 1:
                    fuse_string = _safe_string(fm.ee_tr_22_1p, tfmr.load_kva)
                else:
                    fuse_string = _safe_string(fm.ee_tr_22_3p, tfmr.load_kva)
            else:   # 33kV
                if term.phases == 1:
                    fuse_string = _safe_string(fm.ee_tr_33_1p, tfmr.load_kva)
                else:
                    fuse_string = _safe_string(fm.ee_tr_33_3p, tfmr.load_kva)
    for fuse in fuse_types:
        if fuse.loc_name == fuse_string:
            fuse_object = fuse
            break
    return fuse_object


def f_types(app: pft.Application, recursive: int):
    """
    Retrieve available fuse types from the global library.

    Args:
        app: PowerFactory application instance.
        recursive: Search depth (0 = current folder only,
            1 = include subfolders).

    Returns:
        List of TypFuse objects from the Ergon library.

    Example:
        >>> fuse_types = f_types(app, 0)
        >>> print(f"Found {len(fuse_types)} fuse types")
    """
    ergon_lib = app.GetGlobalLibrary()
    fuse_folder = ergon_lib.SearchObject(r"\ErgonLibrary\Protection\Fuses.IntFolder")
    fuse_types = fuse_folder.GetContents("*.TypFuse", recursive)
    return fuse_types


def determine_fuse_type(fuse: pft.RelFuse) -> bool:
    """
    Determine if a fuse is a line fuse (not a transformer fuse).

    This function examines the fuse location to classify it as either:
    - Line fuse: Located in the main network, included in protection
    - Transformer fuse: Located at a distribution transformer, excluded

    Classification logic:
    1. If fuse is in System Overview terminal → line fuse
    2. If fuse is in a line cubicle → line fuse
    3. If fuse name is not in its parent switch object → line fuse
    4. If fuse's secondary substation contains a transformer → TR fuse

    Args:
        fuse: PowerFactory RelFuse object.

    Returns:
        True if the fuse is a line fuse (to be included).
        False if the fuse is a transformer fuse (to be excluded).

    Note:
        Only line fuses are treated as protection devices in the
        protection section analysis.
    """

    # Check if fuse has the required attribute path
    fuse_active = fuse.HasAttribute("r:fold_id:r:obj_id:e:loc_name")
    if not fuse_active:
        return True

    fuse_grid = fuse.cpGrid
    # Check if fuse is in a line cubicle (System Overview)
    if (
            fuse.GetAttribute("r:fold_id:r:cterm:r:fold_id:e:loc_name")
            == fuse_grid.loc_name
    ):
        # This would indicate it is in a line cubical
        return True

    # Check if fuse is in a switch object
    if fuse.loc_name not in fuse.GetAttribute("r:fold_id:r:obj_id:e:loc_name"):
        return True

    # Check if secondary substation contains a transformer
    secondary_sub = fuse.fold_id.cterm.fold_id
    contents = secondary_sub.GetContents()
    for content in contents:
        if content.GetClassName() == "ElmTr2":
            return False
    else:
        return True