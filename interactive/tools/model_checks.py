"""
Pre-study model validation for PowerFactory protection assessment.

This module performs validation checks on protection devices before
running fault studies. It identifies common configuration issues that
would cause incorrect results or script failures.

Validation Checks:
    - Relay type assignment (typ_id must be set)
    - CT connection (cpCt must be assigned)
    - Measurement element type assignment
    - CT/relay phase count matching

Functions:
    run: Entry point - check every active relay in the model
    relay_checks: Validate a list of relays
    relay_type_check: Verify relay type assignment
    ct_phase_check: Verify CT configuration and phase matching
"""

from typing import Dict, List

from pf_config import pft
from relays import elements


def run(app: pft.Application) -> None:
    """
    Check every active relay in the model and report any issues.

    Args:
        app: PowerFactory application instance.

    Side Effects:
        Prints a warning to the output window if issues are found.
    """
    relay_checks(app, elements.get_all_relays(app))


def relay_checks(app: pft.Application, relays: List) -> None:
    """
    Validate relay configuration before fault study execution.

    Performs pre-study checks on all relays to identify configuration
    issues that would affect study results. Issues are collected and
    reported as warnings in the PowerFactory output window.

    Args:
        app: PowerFactory application instance.
        relays: List of relay objects (may include other element types).

    Side Effects:
        Prints warning messages to output window if issues are found.

    Validation Checks:
        1. Relay type (typ_id) must be assigned
        2. CT (cpCt) must be connected
        3. CT phase count must match relay measurement type

    Example:
        >>> all_relays = elements.get_all_relays(app)
        >>> relay_checks(app, all_relays)
        Warnings: {'RELAY1': ['RELAY1 has no CT assigned']}
    """
    relay_issues_detected: Dict[str, List[str]] = {}

    for device in relays:
        if device.GetClassName() != 'ElmRelay':
            continue

        relay_issues_detected = relay_type_check(device, relay_issues_detected)

        if device.loc_name not in relay_issues_detected:
            relay_issues_detected = ct_phase_check(device, relay_issues_detected)

    if relay_issues_detected:
        app.PrintWarn(f"Warnings: {relay_issues_detected}")


def relay_type_check(
    elmrelay: pft.ElmRelay,
    relay_issues_detected: Dict[str, List[str]]
) -> Dict[str, List[str]]:
    """
    Verify that a relay has a relay type assigned.

    Each relay must have a typ_id attribute pointing to a valid relay
    type definition. Without this, the relay cannot be properly analyzed.

    Args:
        elmrelay: PowerFactory ElmRelay object to check.
        relay_issues_detected: Dictionary to accumulate detected issues.

    Returns:
        Updated issues dictionary with any new issues added.

    Example:
        >>> issues = relay_type_check(relay, {})
        >>> if 'RELAY1' in issues:
        ...     print("Relay type not assigned")
    """
    if elmrelay.GetAttribute("e:typ_id") is None:
        _add_issue(
            relay_issues_detected,
            elmrelay.loc_name,
            f"{elmrelay} has no typ_id assigned"
        )

    return relay_issues_detected


def ct_phase_check(
    elmrelay: pft.ElmRelay,
    relay_issues_detected: Dict[str, List[str]]
) -> Dict[str, List[str]]:
    """
    Verify CT connection and phase count matching for a relay.

    Each relay must have a CT connected, and the CT phase count must
    match the relay's measurement element type:
        - 3-phase CT requires 3-phase measurement type
        - 1-phase CT requires 1-phase measurement type

    Args:
        elmrelay: PowerFactory ElmRelay object to check.
        relay_issues_detected: Dictionary to accumulate detected issues.

    Returns:
        Updated issues dictionary with any new issues added.

    Measurement Type Categories:
        3-phase types: '3rms', '3drm', '3pui', '3dui', 'ulil'
        1-phase types: '1rms', '1pui', '1ph', 'selr'

    Example:
        >>> issues = ct_phase_check(relay, {})
        >>> if relay.loc_name in issues:
        ...     print("CT phase mismatch detected")
    """
    # Check CT is present
    if elmrelay.GetAttribute("e:cpCt") is None:
        _add_issue(
            relay_issues_detected,
            elmrelay.loc_name,
            f"{elmrelay} has no CT assigned"
        )
        return relay_issues_detected

    # Get CT and measurement type information
    ct = elmrelay.GetAttribute("e:cpCt")
    ct_phases = ct.GetAttribute("e:iphase")

    # A missing measurement block or type is itself a model issue to
    # report, not a reason to stop checking the remaining relays.
    measures = elmrelay.GetContents("*.RelMeasure")
    typmeas = measures[0].GetAttribute("typ_id") if measures else None
    if typmeas is None:
        _add_issue(
            relay_issues_detected,
            elmrelay.loc_name,
            f"{elmrelay} has no measurement element type assigned"
        )
        return relay_issues_detected
    measure_type = typmeas.GetAttribute("atype")

    # Phase count categorization
    three_phase_types = ['3rms', '3drm', '3pui', '3dui', 'ulil']
    one_phase_types = ['1rms', '1pui', '1ph', 'selr']

    # Check for mismatches
    if ct_phases == 3 and measure_type in one_phase_types:
        _add_issue(
            relay_issues_detected,
            ct.loc_name,
            f"{elmrelay} measured phase count != CT phase count"
        )

    if ct_phases == 1 and measure_type in three_phase_types:
        _add_issue(
            relay_issues_detected,
            ct.loc_name,
            f"{elmrelay} measured phase count != CT phase count"
        )

    return relay_issues_detected


def _add_issue(
    dictionary: Dict[str, List[str]],
    key: str,
    value: str
) -> Dict[str, List[str]]:
    """
    Add an issue to the issues dictionary.

    Appends the value to an existing key's list, or creates a new
    list if the key doesn't exist.

    Args:
        dictionary: Issues dictionary to update.
        key: Key for the issue (typically device name).
        value: Issue description string.

    Returns:
        Updated dictionary.
    """
    try:
        dictionary[key].append(value)
    except KeyError:
        dictionary[key] = [value]

    return dictionary