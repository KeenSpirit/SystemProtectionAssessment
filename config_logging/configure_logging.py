"""
Logging configuration and path utilities for PowerFactory scripts.

This module provides path resolution for saving PowerFactory results and
logging utilities for debugging script execution. Handles path differences
between Citrix and local installations.

Functions:
    getpath: Resolve output path for PowerFactory results
    log_arguments: Decorator to log function calls with arguments
"""

import logging
import logging.config
from pathlib import Path

import powerfactory as pf  # noqa: F401


def getpath(subdir: str = "PowerFactoryResults") -> Path:
    """
    Return a path for PowerFactory results output.

    Resolves the appropriate output directory based on the execution
    environment. When running via Citrix, uses the client-mapped path.
    For local installations, uses the user's home directory.

    Args:
        subdir: Subdirectory name to create under the base path.
            Defaults to "PowerFactoryResults".

    Returns:
        Path object pointing to the output directory. The directory
        is created if it does not exist.

    Note:
        Citrix path: //client/c$/Users/{username}/{subdir}
        Local path: c:/Users/{username}/{subdir}

    Example:
        >>> output_path = getpath()
        >>> print(output_path)
        WindowsPath('//client/c$/Users/dan.park/PowerFactoryResults')
        >>>
        >>> custom_path = getpath("FaultStudyResults")
        >>> print(custom_path)
        WindowsPath('c:/Users/dan.park/FaultStudyResults')
    """
    user = Path.home().name
    basepath = Path("//client/c$/Users") / user

    try:
        basepath_exists = basepath.exists()
    except OSError as err:
        basepath_exists = False
    if basepath_exists:
        clientpath = basepath / subdir
    else:
        clientpath = Path("c:/Users") / user / subdir

    clientpath.mkdir(exist_ok=True)

    return clientpath


def log_arguments(func):
    """
    Decorator to log function calls with their arguments.

    Wraps a function to log its name and arguments at INFO level
    each time it is called. Useful for debugging and tracing script
    execution flow.

    Args:
        func: The function to wrap with logging.

    Returns:
        Wrapped function that logs calls before executing.

    Example:
        >>> @log_arguments
        ... def calculate_fault(terminal, fault_type):
        ...     return terminal.get_fault(fault_type)
        >>>
        >>> calculate_fault(term1, "3-Phase")
        # Logs: "Function calculate_fault called with arguments: ..."
    """
    def wrapper(*args, **kwargs):
        # Build argument string for logging
        arg_repr = [repr(a) for a in args]
        kwarg_repr = [f"{k}={v!r}" for k, v in kwargs.items()]
        arg_str = ', '.join(arg_repr + kwarg_repr)

        logging.info(
            f"Function {func.__name__} called with arguments: {arg_str}"
        )
        return func(*args, **kwargs)

    return wrapper