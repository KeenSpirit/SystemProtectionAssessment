"""
Self-contained interactive utilities.

Each module exposes run(app), which performs the whole action and
reports to the PowerFactory output window. run() handles the user
pressing Exit, so callers only need to call it.

Modules:
    find_substation: Find the master project holding a substation.
    open_points: Show the normally-open points on selected feeders.
    model_checks: Pre-study relay configuration checks.
    curve_labels: Update TOC plot curve label formats.
"""
