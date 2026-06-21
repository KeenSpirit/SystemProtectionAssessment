"""
Domain enumerations for PowerFactory protection assessment.

This module contains all enumeration types used throughout the protection
assessment system, plus simple lookup functions with no external dependencies.
"""

from enum import Enum
from typing import Optional


class ElementType(Enum):
    """PowerFactory element class names."""
    TERM = "ElmTerm"
    LOAD = "ElmLod"
    TFMR = "ElmTr2"
    LINE = "ElmLne"
    GRID = "ElmXnet"
    COUPLER = "ElmCoup"
    SWITCH = "StaSwitch"
    RELAY = "ElmRelay"
    FUSE = "RelFuse"
    FEEDER = "ElmFeeder"


class ConstructionType(Enum):
    """Line construction types affecting fault impedance calculations."""
    OVERHEAD = "OH"
    UNDERGROUND = "UG"
    SWER = "SWER"


class FaultType(Enum):
    """Types of faults for protection analysis."""
    THREE_PHASE = "3-Phase"
    TWO_PHASE = "2-Phase"
    PHASE_GROUND = "Phase-Ground"


# =============================================================================
# LOOKUP FUNCTIONS
# =============================================================================

# Phase technology attribute mapping
# Maps PowerFactory phtech attribute values to number of phases
_PHASE_MAPPING = {
    1: {6, 7, 8},      # Single phase
    2: {2, 3, 4, 5},   # Two phase
    3: {0, 1},         # Three phase
}


def ph_attr_lookup(phtech: int) -> Optional[int]:
    """
    Convert PowerFactory terminal phase technology attribute to phase count.

    Args:
        phtech: The phtech attribute value from an ElmTerm object.
                Values 0-8 represent different phase configurations.

    Returns:
        Number of phases (1, 2, or 3), or None if phtech is not recognized.

    Example:
        >>> ph_attr_lookup(0)  # 3-phase ABC
        3
        >>> ph_attr_lookup(6)  # Single phase A
        1
    """
    for phases, attr_set in _PHASE_MAPPING.items():
        if phtech in attr_set:
            return phases
    return None