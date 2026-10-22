"""
GUI-free assessment pipeline shared by every entry path.

This package holds everything between "an active PowerFactory project"
and "populated Feeder dataclasses ready to render". It must never
import tkinter or block on user input: the batch path runs it
headless across many projects, and the interactive path reaches it
only after its own dialogs have collected the user's selections.

Modules:
    errors: AssessmentError, the typed per-project failure.
    study_case: Study case activation policy for both entry paths.
    discovery: Radial feeder, device and external grid discovery,
        and conversion of PowerFactory objects to assets dataclasses.
    run: AssessmentOptions and run_assessment, the stage runner with
        per-feeder failure containment.
"""