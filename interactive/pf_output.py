"""
Route core logging to the PowerFactory output window.

The core packages report progress and problems through logging, never
app.PrintPlain, so that batch runs capture them on stdout. In an
interactive run nobody watches stdout: without this handler a feeder
dropped after a failure would vanish from the results with no message
in PowerFactory.

Classes:
    PFOutputHandler: logging.Handler writing to the output window.

Functions:
    attached: Context manager attaching the handler for one run.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from pf_config import pft


class PFOutputHandler(logging.Handler):
    """Forward log records to the PowerFactory output window."""

    def __init__(self, app: pft.Application) -> None:
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        """
        Write one record with the Print method matching its level.

        Args:
            record: The log record to write.
        """
        try:
            msg = self.format(record)
            if record.levelno >= logging.ERROR:
                self._app.PrintError(msg)
            elif record.levelno >= logging.WARNING:
                self._app.PrintWarn(msg)
            else:
                self._app.PrintPlain(msg)
        except Exception:
            self.handleError(record)


@contextmanager
def attached(
    app: pft.Application, level: int = logging.INFO
) -> Iterator[PFOutputHandler]:
    """
    Attach a PFOutputHandler to the root logger for the duration.

    PowerFactory keeps the interpreter alive between script runs, so
    any handler left behind by an earlier (possibly aborted) run is
    removed first; otherwise every message would print twice, through
    a stale application reference. Handlers are matched by class name,
    so one created from an earlier copy of this module is caught too.

    Args:
        app: PowerFactory application instance.
        level: Lowest level written to the output window.

    Yields:
        The attached handler.
    """
    root = logging.getLogger()
    for handler in [
        h for h in root.handlers
        if type(h).__name__ == PFOutputHandler.__name__
    ]:
        root.removeHandler(handler)

    handler = PFOutputHandler(app)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)

    previous_level = root.level
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
