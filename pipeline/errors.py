"""
Typed failures raised by the assessment pipeline.

AssessmentError is re-exported from start.py so that the batch layer's
existing `except start.AssessmentError` handling keeps working.
"""


class AssessmentError(RuntimeError):
    """Raised when the active project cannot be assessed.

    Signals a per-project failure (e.g. missing study case) that the
    batch orchestrator should record and skip, rather than a fault
    that should abort the whole batch run.
    """