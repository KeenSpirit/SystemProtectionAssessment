"""
Domain models for PowerFactory protection assessment.

This package contains the core domain models (dataclasses) used throughout
the protection assessment system. Each model represents a distinct concept
in the electrical network domain.

Modules:
    enums: Element types, construction types, fault types
    feeder: Distribution feeder model
    device: Protection device model (relays, fuses)
    termination: Network terminal model
    line: Distribution line model
    transformer: Transformer/load model for fusing
    utils: Utility functions for domain operations

Usage:
    # Import the entire domain package (recommended for backward compatibility)
    import domain as ast

    # Or import specific items
    from domain import Device, Feeder, ElementType
    from domain.termination import Termination, initialise_term_dataclass

Backward Compatibility:
    This package provides the same interface as the original script_classes.py
    module. Existing code using `import script_classes as dd` can be migrated
    by changing to `import domain as ast` with minimal other changes.

Example:
    >>> import assets as ast
    >>>
    >>> # Create assets objects from PowerFactory elements
    >>> feeder = ast.initialise_fdr_dataclass(elm_feeder)
    >>> device = ast.initialise_dev_dataclass(elm_relay)
    >>>
    >>> # Check element types
    >>> if device.obj.GetClassName() == ast.ElementType.RELAY.value:
    ...     print("This is a relay")
"""

# =============================================================================
# ENUMERATIONS
# =============================================================================

from assets.enums import (
    ElementType,
    ConstructionType,
    FaultType,
    ph_attr_lookup,
)

# =============================================================================
# DOMAIN MODELS
# =============================================================================

from assets.feeder import Feeder, initialise_fdr_dataclass
from assets.device import Device, initialise_dev_dataclass
from assets.termination import Termination, initialise_term_dataclass
from assets.line import Line, initialise_line_dataclass
from assets.transformer import Tfmr, initialise_load_dataclass

# =============================================================================
# UTILITIES
# =============================================================================

from assets.utils import conductors_properties

# =============================================================================
# PUBLIC API
# =============================================================================

__all__ = [
    # Enums
    "ElementType",
    "ConstructionType",
    "FaultType",
    "ph_attr_lookup",
    # Domain models
    "Feeder",
    "Device",
    "Termination",
    "Line",
    "Tfmr",
    # Initializers
    "initialise_fdr_dataclass",
    "initialise_dev_dataclass",
    "initialise_term_dataclass",
    "initialise_line_dataclass",
    "initialise_load_dataclass",
    # Utilities
    "conductors_properties",
]