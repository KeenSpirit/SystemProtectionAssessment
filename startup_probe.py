"""
Startup timing probe for the interactive SystemProtectionAssessment.

Run this from PowerFactory exactly as you run start.py (same folder, so
the same sys.path), as the FIRST script in a freshly started
PowerFactory session. It times each thing start.py does before its
first dialog appears, in the same order, and prints a table to the
output window. Run it a second time in the same session to see whether
PowerFactory keeps the interpreter (and its imported modules) alive
between script runs.

It changes nothing in the project: the only PowerFactory calls are
ResetCalculation and ClearOutputWindow, which start.py makes anyway.

v2 adds, after the timings: where the loaded modules came from, the
full sys.path, and a file-access latency test comparing the folder the
script was run from with the direct UNC copies of the code. To compare
routes, run this file once from each location (Y: / \\\\client\\Y$ and
the \\\\ntgcca1 UNC path), each time in a freshly started PowerFactory.
"""

import os
import sys
import time

_T0 = time.perf_counter()
_ALREADY_LOADED = {
    name: name in sys.modules
    for name in ("pandas", "pf_config", "pipeline.run", "tkinter")
}

import powerfactory as pf  # noqa: E402

app = pf.GetApplication()
rows = []


def timed(label, func):
    """Run func, record its wall time, and return its result."""
    t = time.perf_counter()
    try:
        result = func()
        note = ""
    except Exception as exc:  # report and carry on
        result = None
        note = f"FAILED: {type(exc).__name__}: {exc}"
    rows.append((label, time.perf_counter() - t, note))
    return result


def _import(name):
    return lambda: __import__(name)


# 1. Typing stubs from \\Ecasd01 (every SPA module imports pf_config)
timed("import pf_config (powerfactorytyping)", _import("pf_config"))

# 2. Light modules start.py needs before the dialog
timed("import pf_protection_helper", _import("pf_protection_helper"))
timed("import config_logging", _import("config_logging.configure_logging"))

# 3. Log file location (Citrix //client/c$ probe + mkdir)
from config_logging import configure_logging as cl  # noqa: E402
log_dir = timed("cl.getpath() (client drive probe)", cl.getpath)

# 4. Third-party libraries currently imported before the dialog
timed("import numpy", _import("numpy"))
timed("import pandas", _import("pandas"))
timed("import openpyxl", _import("openpyxl"))

# 5. The SPA analysis stack itself (project modules on the share)
timed("import pipeline.discovery", _import("pipeline.discovery"))
timed("import pipeline.run", _import("pipeline.run"))
timed("import save_results.save_result", _import("save_results.save_result"))
timed("import save_results.dashboard_writer",
      _import("save_results.dashboard_writer"))

# 6. app_manager entry calls
timed("app.ResetCalculation()", app.ResetCalculation)
timed("app.ClearOutputWindow()", app.ClearOutputWindow)

# 7. Interactive modules and the first Tk window
timed("import interactive.study_selection",
      _import("interactive.study_selection"))
timed("import interactive.study_inputs", _import("interactive.study_inputs"))


def _tk_roundtrip():
    import tkinter as tk  # noqa: TID251 - diagnostic, never run by batch
    root = tk.Tk()
    root.update()
    root.destroy()


timed("tk.Tk() create + destroy", _tk_roundtrip)

total = time.perf_counter() - _T0

# --- Report ------------------------------------------------------------
# Printed after the timings, because ClearOutputWindow above clears it.
app.PrintPlain("=== SPA startup probe ===")
for label, secs, note in rows:
    app.PrintPlain(f"{secs:8.2f} s  {label}  {note}")
app.PrintPlain(f"{total:8.2f} s  TOTAL (probe start to end)")
app.PrintPlain("")
app.PrintPlain(f"Already loaded at probe start: {_ALREADY_LOADED}")
app.PrintPlain(f"sys.dont_write_bytecode = {sys.dont_write_bytecode}")
app.PrintPlain(f"sys.executable = {sys.executable}")
app.PrintPlain(f"sys.path[0] = {sys.path[0] if sys.path else None}")
app.PrintPlain(f"log dir = {log_dir}")

pft_mod = sys.modules.get("powerfactorytyping")
pft_file = getattr(pft_mod, "__file__", None)
if pft_file:
    try:
        size_kb = os.path.getsize(pft_file) / 1024
        app.PrintPlain(f"powerfactorytyping: {pft_file} ({size_kb:,.0f} KB)")
    except OSError:
        app.PrintPlain(f"powerfactorytyping: {pft_file}")
    spec = getattr(pft_mod, "__spec__", None)
    cached = getattr(spec, "cached", None)
    app.PrintPlain(
        f"powerfactorytyping .pyc: {cached} "
        f"(exists={bool(cached) and os.path.exists(cached)})"
    )

start_spec = getattr(sys.modules.get("pipeline.run"), "__spec__", None)
cached = getattr(start_spec, "cached", None)
app.PrintPlain(
    f"SPA .pyc example: {cached} "
    f"(exists={bool(cached) and os.path.exists(cached)})"
)


# --- v2: where modules came from, and file-access latency -------------
UNC_DEV = (
    r"\\ntgcca1\ntdpe\PROTECTION\STAFF\Dan Park\PowerFactory"
    r"\Dan script development\SystemProtectionAssessment"
)
UNC_TEAM = r"\\Ecasd01\WksMgmt\PowerFactory\ScriptsDEV\SystemProtectionAssessment"


def _norm(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _origin_roots():
    """Named roots, most specific first, for grouping module files."""
    import site
    roots = [("script dir (sys.path[0])", sys.path[0] if sys.path else "")]
    try:
        roots.append(("user site-packages", site.getusersitepackages()))
    except Exception:
        pass
    try:
        for i, sp in enumerate(site.getsitepackages()):
            roots.append((f"site-packages[{i}]", sp))
    except Exception:
        pass
    roots.append(("python stdlib", os.path.dirname(os.__file__)))
    roots.append((r"\\Ecasd01 (typing stubs)",
                  r"\\Ecasd01\WksMgmt\PowerFactory\ScriptsDEV"))
    return [(name, _norm(p)) for name, p in roots if p]


def _module_origins():
    counts = {}
    roots = _origin_roots()
    for mod in list(sys.modules.values()):
        f = _norm(getattr(mod, "__file__", None) or "")
        if not f:
            continue
        name = next((n for n, r in roots if f.startswith(r)), "other")
        counts[name] = counts.get(name, 0) + 1
    return counts, roots


def _sample_py_files(folder, limit=15):
    """Up to `limit` .py files from folder and its first-level subfolders."""
    files = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return files
    dirs = []
    for e in entries:
        if e.is_file() and e.name.endswith(".py"):
            files.append(e.path)
        elif e.is_dir() and not e.name.startswith((".", "__")):
            dirs.append(e.path)
    for d in dirs:
        if len(files) >= limit:
            break
        try:
            files += [e.path for e in os.scandir(d)
                      if e.is_file() and e.name.endswith(".py")]
        except OSError:
            continue
    return files[:limit]


def _latency(folder):
    """Mean ms per (stat + 4 KB read) of distinct files, and per dir stat."""
    if not folder or not os.path.isdir(folder):
        return None
    files = _sample_py_files(folder)
    t = time.perf_counter()
    for f in files:
        try:
            os.stat(f)
            with open(f, "rb") as fh:
                fh.read(4096)
        except OSError:
            pass
    per_file = 1000 * (time.perf_counter() - t) / max(len(files), 1)
    t = time.perf_counter()
    for _ in range(20):
        os.stat(folder)
    per_stat = 1000 * (time.perf_counter() - t) / 20
    return per_file, per_stat, len(files)


app.PrintPlain("")
app.PrintPlain("=== Module origins (all modules loaded so far) ===")
origins, roots = _module_origins()
for name, root in roots:
    app.PrintPlain(f"{origins.get(name, 0):6d}  {name}: {root}")
app.PrintPlain(f"{origins.get('other', 0):6d}  other")
for name in ("numpy", "pandas", "openpyxl", "tkinter"):
    mod = sys.modules.get(name)
    app.PrintPlain(f"{name}: {getattr(mod, '__file__', None)}")

app.PrintPlain("")
app.PrintPlain("=== sys.path ===")
for i, p in enumerate(sys.path):
    app.PrintPlain(f"[{i}] {p}")

app.PrintPlain("")
app.PrintPlain("=== File access latency (ms) ===")
pandas_mod = sys.modules.get("pandas")
locations = [
    ("script dir (sys.path[0])", sys.path[0] if sys.path else ""),
    (r"UNC dev copy \\ntgcca1", UNC_DEV),
    (r"UNC team copy \\Ecasd01", UNC_TEAM),
    ("python stdlib", os.path.dirname(os.__file__)),
]
if pandas_mod is not None:
    locations.append(("pandas package", os.path.dirname(pandas_mod.__file__)))
for label, folder in locations:
    result = _latency(folder)
    if result is None:
        app.PrintPlain(f"   n/a   {label}: not reachable ({folder})")
    else:
        per_file, per_stat, n = result
        app.PrintPlain(
            f"{per_file:7.1f} per file read, {per_stat:6.1f} per dir stat "
            f"({n} files)  {label}"
        )
