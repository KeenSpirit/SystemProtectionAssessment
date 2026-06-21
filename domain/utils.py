"""
Utility functions for domain model operations.

This module contains functions that operate on domain models but don't
belong to a specific model class.
"""

from functools import lru_cache

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.termination import Termination


@lru_cache(maxsize=1)
def conductors_properties():
    """
    A dictionary with all conductor 1 second fault
    current ratings. Cached after first call.
    """
    cond_csv = (
        r"\\ecasd01\WksMgmt\PowerFactory"
        r"\ScriptsDEV\ProtectionAssessment"
        r"\docs"
    )
    csv_open = open(
        f"{cond_csv}\\ratings_lookup.csv",
        "r"
    )
    cond_rating_dict = {}
    for row in csv_open.readlines():
        conductor_type = row.split(",")[0]
        if "Typcon" in conductor_type:
            continue
        fault_rating = row.split(",")[1]
        diameter = row.split(",")[6]
        thermal = row.split(",")[14]
        cond_rating_dict[conductor_type] = [
            fault_rating,
            diameter,
            thermal
        ]
    csv_open.close()
    return cond_rating_dict