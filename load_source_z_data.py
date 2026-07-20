"""Load tabulated grid fault-level results from an Excel workbook.

Builds an immutable data structure organised by grid:

    GridResults
      └── Grid (one per unique value in the "Grid" column)
            ├── buses:  tuple of associated bus names (column C)
            └── cases:  one FaultCase per (Bound, Scenario) combination,
                        holding the values from columns F-J

Column A ("Bulk Supply Point") is ignored.

The workbook has a defined format: each grid occupies a contiguous
block of exactly three rows,

    row 1: the Max bound row              -> Bound.MAX
    row 2: the absolute Min bound row     -> Bound.MIN
    row 3: the System Normal Min row      -> Bound.SYSTEM_NORMAL_MIN

The Bound of each row is assigned from its position in the block, so
the distinction between the absolute Min and the System Normal Min is
preserved even when the two rows are identical. The stated bound in
the workbook is validated against the expected position, and a
ValueError is raised if the format is violated.

A grid may appear in more than one block (e.g. listed under two bulk
supply points); duplicate (Bound, Scenario) combinations keep the
first occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

from openpyxl import load_workbook

FILE_PATH = Path(r"{file_dir_input}") / "grid_results_all.xlsx"

ROWS_PER_GRID_BLOCK = 3


class Bound(str, Enum):
    """Bound of a fault case, assigned by row position within a grid block."""

    MAX = "Max"
    MIN = "Min"
    SYSTEM_NORMAL_MIN = "System Normal Min"


# Position within a grid block -> assigned bound.
_BOUND_BY_POSITION: dict[int, Bound] = {
    0: Bound.MAX,
    1: Bound.MIN,
    2: Bound.SYSTEM_NORMAL_MIN,
}

# Position within a grid block -> bound stated in the workbook (column D).
_STATED_BOUND_BY_POSITION: dict[int, str] = {
    0: "Max",
    1: "Min",
    2: "Min",
}


@dataclass(frozen=True)
class FaultCase:
    """Fault study results for one (Bound, Scenario) combination."""

    bound: Bound        # assigned from row position within the grid block
    scenario: str       # column E, e.g. "System Normal", "MGP-TR1 OOS"
    fault_3p: float     # column F, 3P fault level
    r_x: float          # column G, R/X
    z2_z1: float        # column H, Z2/Z1
    x0_x1: float        # column I, X0/X1
    r0_x1: float        # column J, R0/X1

    @property
    def key(self) -> tuple[Bound, str]:
        return (self.bound, self.scenario)


@dataclass(frozen=True)
class Grid:
    """All results for one unique value of the "Grid" column."""

    name: str
    buses: tuple[str, ...]
    cases: tuple[FaultCase, ...]

    def case(self, bound: Bound, scenario: str) -> Optional[FaultCase]:
        """Return the case for a (Bound, Scenario) combination, or None."""
        for fault_case in self.cases:
            if fault_case.key == (bound, scenario):
                return fault_case
        return None

    def case_for_bound(self, bound: Bound) -> Optional[FaultCase]:
        """Return the first case with the given bound, or None.

        Each bound appears at most once per grid under the defined
        three-row format, so this is the natural lookup when the
        scenario is not known in advance.
        """
        for fault_case in self.cases:
            if fault_case.bound == bound:
                return fault_case
        return None


@dataclass(frozen=True)
class GridResults:
    """Top-level container: all grids in the workbook, keyed by name."""

    grids: tuple[Grid, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[Grid]:
        return iter(self.grids)

    def __len__(self) -> int:
        return len(self.grids)

    def grid(self, name: str) -> Optional[Grid]:
        """Return the grid with the given name, or None if absent."""
        for grid in self.grids:
            if grid.name == name:
                return grid
        return None


def _parse_str(value: object) -> Optional[str]:
    """Return a stripped, non-empty string, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_float(value: object) -> Optional[float]:
    """Return the value as a float, or None if not numeric."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_buses(value: object) -> tuple[str, ...]:
    """Split a comma-separated bus cell into a tuple of bus names."""
    text = _parse_str(value)
    if text is None:
        return ()
    return tuple(bus.strip() for bus in text.split(",") if bus.strip())


def _build_fault_case(
    row: tuple, position: int, row_number: int
) -> FaultCase:
    """Build a FaultCase from a row, assigning the bound by position.

    Raises ValueError if the stated bound does not match the expected
    bound for the row's position, or if the value columns cannot be
    parsed.
    """
    stated_bound = _parse_str(row[3])
    expected_bound = _STATED_BOUND_BY_POSITION[position]
    if stated_bound != expected_bound:
        raise ValueError(
            f"Row {row_number}: expected bound '{expected_bound}' at "
            f"position {position + 1} of grid block, found '{stated_bound}'"
        )

    scenario = _parse_str(row[4])
    if scenario is None:
        raise ValueError(f"Row {row_number}: missing scenario")

    values = [_parse_float(cell) for cell in row[5:10]]
    if any(value is None for value in values):
        raise ValueError(f"Row {row_number}: non-numeric value in columns F-J")

    fault_3p, r_x, z2_z1, x0_x1, r0_x1 = values
    return FaultCase(
        bound=_BOUND_BY_POSITION[position],
        scenario=scenario,
        fault_3p=fault_3p,
        r_x=r_x,
        z2_z1=z2_z1,
        x0_x1=x0_x1,
        r0_x1=r0_x1,
    )


def load_grid_results(path: Path = FILE_PATH) -> GridResults:
    """Read the workbook and build the immutable GridResults structure.

    Rows are processed as contiguous three-row blocks per grid
    (Max, Min, System Normal Min). Raises ValueError if the workbook
    deviates from this format.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active

        # grid name -> ordered bus names / ordered {key: FaultCase}
        grid_buses: dict[str, dict[str, None]] = {}
        grid_cases: dict[str, dict[tuple[Bound, str], FaultCase]] = {}

        current_grid: Optional[str] = None
        position = 0

        rows = worksheet.iter_rows(min_row=2, values_only=True)
        for row_number, row in enumerate(rows, start=2):
            # Column A (row[0], Bulk Supply Point) is deliberately ignored.
            grid_name = _parse_str(row[1])
            if grid_name is None:
                raise ValueError(f"Row {row_number}: missing grid name")

            if grid_name != current_grid:
                if current_grid is not None and position != ROWS_PER_GRID_BLOCK:
                    raise ValueError(
                        f"Row {row_number}: grid block for '{current_grid}' "
                        f"has {position} rows, expected {ROWS_PER_GRID_BLOCK}"
                    )
                current_grid = grid_name
                position = 0
            elif position == ROWS_PER_GRID_BLOCK:
                raise ValueError(
                    f"Row {row_number}: grid block for '{current_grid}' "
                    f"has more than {ROWS_PER_GRID_BLOCK} rows"
                )

            fault_case = _build_fault_case(row, position, row_number)
            position += 1

            buses = grid_buses.setdefault(grid_name, {})
            for bus in _parse_buses(row[2]):
                buses.setdefault(bus, None)  # ordered de-duplication

            cases = grid_cases.setdefault(grid_name, {})
            cases.setdefault(fault_case.key, fault_case)

        if current_grid is not None and position != ROWS_PER_GRID_BLOCK:
            raise ValueError(
                f"Final grid block for '{current_grid}' has {position} "
                f"rows, expected {ROWS_PER_GRID_BLOCK}"
            )

        grids = tuple(
            Grid(
                name=name,
                buses=tuple(grid_buses[name]),
                cases=tuple(grid_cases[name].values()),
            )
            for name in grid_cases
        )
        return GridResults(grids=grids)
    finally:
        workbook.close()


if __name__ == "__main__":
    results = load_grid_results()
    print(f"Loaded {len(results)} grids")
    for grid in results:
        print(f"\n{grid.name}")
        print(f"  buses: {', '.join(grid.buses)}")
        for fault_case in grid.cases:
            print(
                f"  [{fault_case.bound.value} | {fault_case.scenario}] "
                f"3P={fault_case.fault_3p:.0f}  R/X={fault_case.r_x:.4f}  "
                f"Z2/Z1={fault_case.z2_z1:.4f}  X0/X1={fault_case.x0_x1:.3f}  "
                f"R0/X1={fault_case.r0_x1:.4f}"
            )