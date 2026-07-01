"""
Auto-reclose sequence management for protection analysis.

This module provides functions to manage auto-reclose sequences during
fault analysis, including trip counting, element blocking, and state
reset.

Functions:
    get_device_trips: Get total trips in auto-reclose sequence
    reset_reclosing: Reset sequence to first trip
    trip_count: Get or increment current trip number
    set_enabled_elements: Configure elements for current trip
    reset_block_service_status: Restore element service status
"""

from typing import Dict, Optional, Union, TYPE_CHECKING

from assets.enums import ElementType

if TYPE_CHECKING:
    from pf_config import pft


def get_device_trips(device: Union["pft.ElmRelay", "pft.RelFuse"]) -> int:
    """
    Get the total number of trips in a device's auto-reclose sequence.

    For relays with auto-reclose enabled, returns the lockout count
    (number of trips before lockout). For fuses or relays without
    auto-reclose, returns 1.

    Args:
        device: PowerFactory protection device (ElmRelay or RelFuse)

    Returns:
        Number of trips in the protection sequence (1 to N)

    Example:
        >>> trips = get_device_trips(relay)
        >>> print(f"Device has {trips} trips before lockout")
    """
    if device.GetClassName() == ElementType.RELAY.value:
        elmrecls = device.GetChildren(0, '*.RelRecl', 1)

        if len(elmrecls) == 0:
            return 1

        recloser = elmrecls[0]
        if recloser.outserv or recloser.reclnotactive:
            return 1

        return recloser.GetAttribute("oplockout")

    # Device is a fuse - single trip only
    return 1


def reset_reclosing(elmrelay: "pft.ElmRelay") -> None:
    """
    Reset the auto-reclose sequence to the first trip.

    Sets the starttimeframe attribute to 1, preparing the relay
    for a new fault analysis sequence from the first trip.

    Args:
        elmrelay: PowerFactory ElmRelay object

    Note:
        Only affects ElmRelay objects. Has no effect on fuses.

    Example:
        >>> reset_reclosing(relay)
        >>> # Now analyze from first trip
    """
    if elmrelay.GetClassName() == ElementType.RELAY.value:
        elmrecls = elmrelay.GetChildren(0, '*.RelRecl', 1)
        for recloser in elmrecls:
            recloser.starttimeframe = 1


def trip_count(
    device: Union["pft.ElmRelay", "pft.RelFuse"],
    increment: bool = True
) -> int:
    """
    Get or increment the current trip number in the auto-reclose sequence.

    Args:
        device: PowerFactory protection device (ElmRelay or RelFuse)
        increment: If True, increment the trip counter before returning.
                   If False, return the current trip number.

    Returns:
        The trip number (after incrementing if requested).
        Returns 1 for fuses or relays without auto-reclose.
        Returns 2 when incrementing from initial state.

    Example:
        >>> # Get current trip without incrementing
        >>> current = trip_count(relay, increment=False)
        >>>
        >>> # Move to next trip
        >>> next_trip = trip_count(relay, increment=True)
    """
    elmrecls = device.GetChildren(0, '*.RelRecl', 1)

    if len(elmrecls) == 0:
        # No recloser - return appropriate value
        return 2 if increment else 1

    assert len(elmrecls) == 1, (
            f'{device.loc_name} Multiple reclose elements found'
            )

    recloser = elmrecls[0]
    if recloser.outserv or recloser.reclnotactive:
        return 2 if increment else 1

    if increment:
        recloser.starttimeframe += 1

    return recloser.starttimeframe


def set_enabled_elements(
    device: Union["pft.ElmRelay", "pft.RelFuse"]
) -> Optional[Dict]:
    """
    Configure relay elements based on current auto-reclose trip number.

    Auto-reclosers can block or enable different relay elements for
    each trip in the sequence. This function reads the block logic from
    the recloser type and sets element out-of-service status accordingly.

    Args:
        device: PowerFactory protection device (ElmRelay or RelFuse)

    Returns:
        Dictionary mapping element objects to their original outserv status,
        allowing later restoration via reset_block_service_status().
        Returns None if device has no recloser or is a fuse.

    Block Logic Values:
        - 1.0, 2.0: Element is enabled for this trip
        - Other: Element is blocked (set out of service)

    Example:
        >>> # Before analyzing a specific trip
        >>> original_status = set_enabled_elements(relay)
        >>> # ... perform analysis ...
        >>> reset_block_service_status(original_status)
    """
    if device.GetClassName() != ElementType.RELAY.value:
        return None

    elmrecls = device.GetChildren(0, '*.RelRecl', 1)
    if len(elmrecls) == 0:
        return None

    assert len(elmrecls) == 1, (
        f'{device.loc_name} Multiple reclose elements found'
    )
    recloser = elmrecls[0]

    # Get block logic configuration from recloser type
    logic = recloser.GetAttribute("ilogic")
    recloser_type = recloser.GetAttribute("typ_id")
    type_block_id = recloser_type.GetAttribute("blockid")

    if type_block_id is None:
        return None

    # Map relay elements to their block logic
    net_elements = device.GetAttribute("pdiselm")
    reclose_blocks = {}

    for i, block in enumerate(type_block_id):
        block_logic = logic[i]
        for element in net_elements:
            if element is not None and block in element.loc_name:
                reclose_blocks[element] = block_logic

    # Determine current trip index
    trip_number = recloser.starttimeframe
    list_index = 0 if trip_number <= 1 else trip_number - 1

    # Save original status and apply new status
    block_service_status = {}
    for block, block_logic_list in reclose_blocks.items():
        block_service_status[block] = block.GetAttribute('outserv')

        # Check if element should be enabled for this trip
        block_state = block_logic_list[list_index]
        if block_state in [1.0, 2.0]:
            block.SetAttribute('outserv', 0)  # Enable
        else:
            block.SetAttribute('outserv', 1)  # Disable

    return block_service_status


def reset_block_service_status(block_service_status: Optional[Dict]) -> None:
    """
    Restore relay elements to their original out-of-service status.

    Reverses the changes made by set_enabled_elements(), restoring each
    element to its previous outserv state.

    Args:
        block_service_status: Dictionary from set_enabled_elements() mapping
                              elements to their original outserv values.
                              If None, no action is taken.

    Example:
        >>> original = set_enabled_elements(relay)
        >>> # ... perform analysis ...
        >>> reset_block_service_status(original)
    """
    if block_service_status:
        for block, original_status in block_service_status.items():
            block.SetAttribute('outserv', original_status)