# Contributing

Guidelines for contributing to the System Protection Assessment
project (batch and interactive).

## Reporting Issues

Use the repository's issue tracker to report bugs or problems.

When reporting issues, include:

- The substation/feeder that failed
- Batch or interactive; for interactive, the study type selected
- Any error messages from the PowerFactory output window
- Screenshots if relevant

Bug patches should be reviewed by at least one other person before release
to PROD. Commits should reference the issue number being fixed.

All resolved bugs should be added to unit testing to prevent regression.

## Code Standards

### Line Length Limits

| Content Type | Maximum Characters |
|--------------|-------------------|
| Code statements | 88 |
| Docstrings | 80 |
| Comments | 80 |

Use line continuation or string concatenation to comply with these limits.

```python
# Good: Code within 88 characters
result = calculate_fault_current(terminal, fault_type, impedance_value)

# Good: Long line broken appropriately
result = calculate_fault_current(
    terminal, fault_type, impedance_value, include_motor_contribution=True
)

# Good: Docstring within 80 characters
def calculate_reach_factor(device, terminal):
    """
    Calculate the reach factor for a protection device.

    The reach factor represents the ratio of minimum fault current
    at the terminal to the device pickup setting. Values greater
    than the regional threshold indicate adequate protection.

    Args:
        device: Protection device dataclass with pickup settings.
        terminal: Network terminal with fault current data.

    Returns:
        Reach factor as a float, or 'NA' if pickup is not configured.
    """
```

### Docstring Style

Use Google-style docstrings with the following structure:

```python
def function_name(arg1: Type1, arg2: Type2) -> ReturnType:
    """
    Brief one-line description of the function.

    Extended description if needed, explaining the purpose,
    algorithm, or important considerations. Keep lines within
    80 characters.

    Args:
        arg1: Description of first argument.
        arg2: Description of second argument.

    Returns:
        Description of the return value.

    Raises:
        ExceptionType: When and why this exception is raised.

    Example:
        >>> result = function_name(value1, value2)
        >>> print(result)
    """
```

### Module Docstrings

Every module should have a docstring at the top explaining:

- Purpose of the module
- Key classes or functions provided
- Usage context within the project

```python
"""
Protection reach factor calculations.

This module calculates reach factors for relay devices, which measure
how well a device can detect faults at remote locations in its
protection zone.

Reach Factor = (Minimum Fault Current at Location) / (Device Pickup)

A reach factor > 1.0 means the device can detect faults at that
location. Higher values indicate better coverage with margin.
"""
```

### Type Hints

Use type hints for function signatures. For PowerFactory types, use
the `TYPE_CHECKING` pattern to avoid runtime import issues:

```python
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pf_config import pft


def get_terminals(device: "pft.ElmRelay") -> List["pft.ElmTerm"]:
    ...
```

### Import Organization

Organize imports in the following order, with blank lines between groups:

1. Standard library imports
2. Third-party imports
3. Local application imports

```python
import math
import sys
from typing import Dict, List, Optional

import pandas as pd

from assets.enums import ElementType
from fault_study import analysis
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Modules | lowercase_underscore | `fault_study.py` |
| Classes | PascalCase | `FaultCurrents` |
| Functions | lowercase_underscore | `get_terminal_current` |
| Constants | UPPERCASE_UNDERSCORE | `BACKUP_RF_THRESHOLD` |
| Dataclasses | PascalCase | `Device`, `Termination` |

### Linting

Run `ruff check .` before committing. `ruff.toml` pins a small rule
set (pyflakes, syntax errors) plus one architectural rule: tkinter may
only be imported under `interactive/`.

### Architecture Rules

- Nothing reachable from `start.begin()` may import tkinter or wait on
  user input; dialogs belong in `interactive/`, and `start.main()`
  imports that package inside the function
- Thresholds, regional assumptions and pass/fail rules belong in
  `criteria.py`; compare regions with `criteria.is_seq` /
  `criteria.is_regional`, never string literals
- Outputs (`save_results/`, `interactive/`) render values already on
  the dataclasses; they do not calculate
- No `sys.exit()` outside `if __name__ == "__main__"`; dialog Exit
  buttons use `interactive.dialogs.CancelFlag`
- Report progress with `logging`, not `app.PrintPlain`
- Restore any model change in a `finally` block

See the *Key invariants* section of README.md for the reasons.

### Asset Model Guidelines

When working with the dataclasses in the `assets/` package:

- Use dataclasses for data containers
- Keep initialization functions separate from dataclasses
- Use `Optional[Type]` for fields populated after initialization
- Document all attributes in the class docstring

## Pull Requests

### Workflow

1. Clone the `master` branch for the latest code
2. Create a feature branch: `git checkout -b feature/description`
3. Make changes following the code standards above
4. Test changes in PowerFactory with representative models
5. Create a pull request to merge into `master`

If you don't have collaborator permissions, fork the repository first.

### PR Checklist

Before submitting a pull request:

- [ ] Code follows line length limits (88 for code, 80 for docs)
- [ ] `ruff check .` reports nothing new
- [ ] All functions have docstrings
- [ ] Type hints are included for function signatures
- [ ] Changes tested with SEQ and Regional models (if applicable)
- [ ] Changes to the core tested on both entry points: a batch run
      (`begin()`) and an interactive run (`main()`)
- [ ] No new warnings in PowerFactory output window
- [ ] ASSUMPTIONS.md updated if behaviour assumptions changed
- [ ] "Data Pipeline.md" updated if a stage or field lifecycle changed

## Releases

Release process for deploying to PowerFactory PROD:

1. Ensure all unit tests pass
2. Merge `master` into `release` branch
3. Increment version number using [Semantic Versioning](http://semver.org/):
   - MAJOR: Breaking changes to user workflow or output format
   - MINOR: New features, backward compatible
   - PATCH: Bug fixes, backward compatible
4. Tag the release commit with the version number
5. Update deployment on PowerFactory script server

## Project Architecture

### Package Responsibilities

| Package | Responsibility |
|---------|----------------|
| `start.py` | Entry points: `begin()` (batch), `main()` (interactive) |
| `pipeline/` | GUI-free discovery, study case policy and stage runner |
| `criteria.py` | Thresholds, regional assumptions, verdict rules |
| `assets/` | Data models (dataclasses) and fault level arithmetic |
| `fault_study/` | Short-circuit runs and fault level population |
| `relays/` | Protection elements, reclose, trip times, reach factors |
| `devices/` | Fuse discovery and fuse selection |
| `cond_damage/` | Conductor damage (I²t) assessment |
| `prot_coordination/` | Grading margin assessment |
| `save_results/` | Results workbook and dashboard facts |
| `source_z_data/` | External grid source impedance data (batch) |
| `interactive/` | Dialogs, colour maps, plots, tools, legacy output |

### Key Design Principles

1. **Separation of concerns** - Domain models hold data, separate modules
   contain business logic
2. **Backward compatibility** - Refactoring maintains existing interfaces
3. **Regional abstraction** - SEQ vs Regional differences encapsulated
   in `criteria.py` and dedicated functions
4. **One core, two frontends** - batch and interactive runs share
   `pipeline/` and the analysis packages; only selection and outputs
   differ
5. **Immutability where appropriate** - Use frozen dataclasses for
   fault current containers to prevent accidental modification

## Maintainers

- **Primary:** dan.park@energyq.com.au
- **Secondary:** (vacant)