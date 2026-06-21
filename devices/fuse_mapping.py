"""
Fuse selection mapping tables for distribution transformer protection.

This module contains lookup dictionaries that map transformer sizes
(kVA) to appropriate fuse types for both Ergon Energy (Regional) and
Energex (SEQ) networks.

Data source: Technical Instruction TSD0019k

Dictionary Naming Convention:
    - ee_*: Ergon Energy (Regional models)
    - ex_*: Energex (SEQ models)
    - *_1p: Single-phase transformers
    - *_3p: Three-phase transformers
    - *_swer_*: SWER system fuses
    - *_isol_*: SWER isolating transformers
    - *_dist_*: SWER distribution transformers
    - *_tr_*: Standard distribution transformers
    - *_rmu_*: Ring main unit fuses

Voltage Suffix Convention (for SWER):
    - _11_11: 11kV terminal, 11kV system
    - _11_127: 22kV terminal, 11kV system (12.7kV SWER)
    - _11_191: 33kV terminal, 11kV system (19.1kV SWER)
    - _22_127: 22kV terminal, 22kV system
    - _22_191: 33kV terminal, 22kV system
    - _33_127: 22kV terminal, 33kV system
    - _33_191: 33kV terminal, 33kV system

Usage:
    from devices import fuse_mapping as fm

    # Get fuse string for a 200kVA transformer
    fuse_name = fm.ee_tr_11_3p.get(200, None)
"""

# ============================================================================
# ERGON ENERGY FUSES (Regional Models)
# ============================================================================

# ----------------------------------------------------------------------------
# SWER Isolating Transformer Fuses (FL < 8kA)
# ----------------------------------------------------------------------------

ee_swer_isol_tr_11_11 = {
    100: 'FUSE LINK 11/22/33kV 25A CLASS K',
    200: 'FUSE LINK 11/22/33kV 40A CLASS K'
}

ee_swer_isol_tr_11_127 = {
    100: 'FUSE LINK 11/22/33kV 25A CLASS K',
    200: 'FUSE LINK 11/22/33kV 40A CLASS K'
}

ee_swer_isol_tr_11_191 = {
    100: 'FUSE LINK 11/22/33kV 31A CLASS K',
    200: 'FUSE LINK 11/22/33kV 50A CLASS K'
}

ee_swer_isol_tr_22_127 = {
    100: 'FUSE LINK 11/22/33kV 20A CLASS K',
    200: 'FUSE LINK 11/22/33kV 31A CLASS K'
}

ee_swer_isol_tr_22_191 = {
    100: 'FUSE LINK 11/22/33kV 20A CLASS K',
    200: 'FUSE LINK 11/22/33kV 31A CLASS K'
}

ee_swer_isol_tr_33_127 = {
    100: 'FUSE LINK 11/22/33kV 16A CLASS K',
    200: 'FUSE LINK 11/22/33kV 25A CLASS K'
}

ee_swer_isol_tr_33_191 = {
    100: 'FUSE LINK 11/22/33kV 16A CLASS K',
    200: 'FUSE LINK 11/22/33kV 25A CLASS K'
}

# ----------------------------------------------------------------------------
# SWER Distribution Transformer Fuses
# ----------------------------------------------------------------------------

ee_swer_dist_tr_11 = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    50: 'FUSE LINK 11/22/33kV 10A CLASS K',
    75: 'FUSE LINK 11/22/33kV  10/30A CLASS K'
}

ee_swer_dist_tr_127 = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV 10A CLASS K',
    75: '1FUSE LINK 11/22/33kV 0/30A CLASS K'
}

ee_swer_dist_tr_191 = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    75: 'FUSE LINK 11/22/33kV  6/20A CLASS K'
}

# ----------------------------------------------------------------------------
# Standard Distribution Transformer HV Fuses - 11kV (EDO, FL < 8kA)
# Assumes TR LV fuse
# ----------------------------------------------------------------------------
ee_tr_11_1p = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    50: 'FUSE LINK 11/22/33kV 16A CLASS K'
}
ee_tr_11_3p = {
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    63: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    100: 'FUSE LINK 11/22/33kV 16A CLASS K',
    200: 'FUSE LINK 11/22/33kV 25A CLASS K',
    300: 'FUSE LINK 11/22/33kV 31A CLASS K',
    315: 'FUSE LINK 11/22/33kV 31A CLASS K',
    500: 'FUSE LINK 11/22/33kV 50A CLASS K',
    750: 'FUSE LINK 11/22/33kV 50A CLASS K'
}

# ----------------------------------------------------------------------------
# Standard Distribution Transformer HV Fuses - 22kV (EDO, FL < 6kA)
# Assumes TR LV fuse
# ----------------------------------------------------------------------------
ee_tr_22_1p = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV 16A CLASS K'
}
ee_tr_22_3p = {
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    63: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    100: 'FUSE LINK 11/22/33kV  6/20A CLASS K',
    200: 'FUSE LINK 11/22/33kV 16A CLASS K',
    300: 'FUSE LINK 11/22/33kV 20A CLASS K',
    315: 'FUSE LINK 11/22/33kV 20A CLASS K',
    500: 'FUSE LINK 11/22/33kV 31A CLASS K',
    750: 'FUSE LINK 11/22/33kV 50A CLASS K'
}

# ----------------------------------------------------------------------------
# Standard Distribution Transformer HV Fuses - 33kV (EDO, FL < 4kA)
# Assumes TR LV fuse
# ----------------------------------------------------------------------------
ee_tr_33_1p = {
    10: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV  3/10A CLASS K'
}

ee_tr_33_3p = {
    25: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    50: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    63: 'FUSE LINK 11/22/33kV 3/10A  CLASS K',
    100: 'FUSE LINK 11/22/33kV  3/10A CLASS K',
    200: 'FUSE LINK 11/22/33kV 12A CLASS K',
    300: 'FUSE LINK 11/22/33kV 16A CLASS K',
    315: 'FUSE LINK 11/22/33kV 16A CLASS K',
    500: 'FUSE LINK 11/22/33kV 20A CLASS K',
    750: 'FUSE LINK 11/22/33kV 50A CLASS K'
}

# ============================================================================
# ENERGEX FUSES (SEQ Models)
# ============================================================================

# ----------------------------------------------------------------------------
# SWER Fuses
# ----------------------------------------------------------------------------

ex_SWER_f_sizes = {
    10: 'FUSE LINK 11/22/33kV 3A CLASS K',
    25: 'FUSE LINK 11/22/33kV 3A CLASS K'
}

# ----------------------------------------------------------------------------
# Pole-Mounted Transformer Fuses - Single Phase
# ----------------------------------------------------------------------------

ex_pole_1p_fuses = {
    0: None,
    10: 'FUSE LINK 11/22/33kV 8A CLASS T',
    15: 'FUSE LINK 11/22/33kV 8A CLASS T',
    25: 'FUSE LINK 11/22/33kV 8A CLASS T',
    50: 'FUSE LINK 11/22/33kV 16A CLASS K',
    63: 'FUSE LINK 11/22/33kV 16A CLASS K',
}

# ----------------------------------------------------------------------------
# Pole-Mounted Transformer Fuses - Three Phase
# ----------------------------------------------------------------------------

ex_pole_3p_fuses = {
    0: None,
    10: 'FUSE LINK 11/22/33kV 8A CLASS T',
    15: 'FUSE LINK 11/22/33kV 8A CLASS T',
    25: 'FUSE LINK 11/22/33kV 8A CLASS T',
    50: 'FUSE LINK 11/22/33kV 8A CLASS T',
    63: 'FUSE LINK 11/22/33kV 8A CLASS T',
    100: 'FUSE LINK 11/22/33kV 16A CLASS K',
    200: 'FUSE LINK 11/22/33kV 20A CLASS K',
    300: 'FUSE LINK 11/22/33kV 25A CLASS K',
    315: 'FUSE LINK 11/22/33kV 25A CLASS K',
    500: 'FUSE LINK 11/22/33kV 40A CLASS K',
    750: 'FUSE LINK 11/22/33kV 50A CLASS K',
    1000: 'FUSE LINK 11/22/33kV 65A CLASS K',
    1500: 'FUSE LINK 11/22/33kV 80A CLASS K',
}

# ----------------------------------------------------------------------------
# RMU Air-Insulated Fuses
# Keys for 750kVA+ include impedance class: "750 LowZ" or "750 HighZ"
# ----------------------------------------------------------------------------

ex_rmu_air_fuses = {
    0: None,
    50: 'FUSE LINK LV/11kV SIBA 30 004 03  6.3',
    100: 'FUSE LINK LV/11kV SIBA 30 004 03  10',
    200: 'FUSE LINK LV/11kV SIBA 30 004 03  16',
    300: 'FUSE LINK LV/11kV SIBA 30 004 03  25',
    315: 'FUSE LINK LV/11kV SIBA 30 004 03  25',
    500: 'FUSE LINK LV/11kV SIBA 30 004 03  40',
    '750 LowZ': 'FUSE LINK LV/11kV SIBA 30 012 03  63',
    '750 HighZ': 'FUSE LINK LV/11kV SIBA 30 012 03  63',
    '1000 LowZ': 'FUSE LINK LV/11kV Bussmann 12FXLSJ80',
    '1000 HighZ': 'FUSE LINK LV/11kV SIBA 30 012 03  63',
    '1500 LowZ': 'FUSE LINK LV/11kV SIBA 30 521 03  100',
    '1500 HighZ': 'FUSE LINK LV/11kV Bussmann 12FXLSJ80'
}

# ----------------------------------------------------------------------------
# RMU Oil-Insulated Fuses
# ----------------------------------------------------------------------------

ex_rmu_oil_fuses = {
    0: None,
    50: 'FUSE LINK LV/11kV Bussmann 12OEFMA10',
    100: 'FUSE LINK LV/11kV Bussmann 12OEFMA10',
    200: 'FUSE LINK LV/11kV SIBA 30 144 36  20',
    300: 'FUSE LINK LV/11kV SIBA 30 144 36  31.5',
    315: 'FUSE LINK LV/11kV SIBA 30 144 36  31.5',
    500: 'FUSE LINK LV/11kV SIBA 30 144 36  40',
    750: 'FUSE LINK LV/11kV Bussmann 12OEFMA63',
    1000: 'FUSE LINK LV/11kV SIBA 30 144 36  80',
    1500: 'FUSE LINK LV/11kV SIBA 30 237 36  125',
}