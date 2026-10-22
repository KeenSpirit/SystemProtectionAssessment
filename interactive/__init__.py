"""
Interactive (in-PowerFactory) frontend for the protection assessment.

Everything that opens a tkinter dialog or otherwise waits on the user
lives under this package, and nothing here is imported by the batch
path. Core packages (pipeline, assets, fault_study, relays, ...) must
never import from interactive.

Modules:
    dialogs: UserCancelled and the CancelFlag helper for Exit buttons.
    pf_output: Route core logging to the PowerFactory output window.
    study_selection: First dialog - which study or tool to run.
    study_inputs: Feeder, external grid and device selection dialogs.

Subpackages:
    tools: Self-contained utilities (project finder, open point
        viewer, relay model checks, TOC curve label update).
"""
