"""
Coordination element diagnostic for SystemProtectionAssessment.

Runs inside PowerFactory against one or more relays and reports, for each:

  1. the reclose element: trips to lockout, the block names on its type
     and the per-trip logic table (ilogic)
  2. which relay slot elements each reclose block lands on, using the same
     name match as relays/reclose.set_enabled_elements, and whether the
     match is a protection element or a sub-relay container
  3. every RelToc / RelIoc in the relay (sub-relays included) with its
     own out-of-service flag, IsOutOfService(), enclosing sub-relays,
     pickup, time setting, curve, and how relays/elements.get_prot_elements
     classifies it (or why it is left out)
  4. the elements SPA would treat as active on each trip, the fastest of
     them at a set of test currents, and the backup time the coordination
     check would grade against (prot_coord.delayed_trip_time)

Step 4 is worked out from the flags and the logic table. Nothing is
switched. It is shown two ways:

  own flags        each element judged on its own out-of-service flag
                   after the reclose blocks are applied
  container flags  an element is also out when an enclosing sub-relay is
                   out

If no reclose block lands on a sub-relay and no sub-relay is out of
service the two are identical and only one is printed.

Element classification, current conversion and trip times use SPA's own
functions (relays.elements, relays.current_conversion, relays.trip_time),
so the times are the ones the assessment calculates.

By default it writes nothing to the model. Safe to run on any project.

live=True adds one more section: it steps the relay through its trips
with SPA's own reclose.set_enabled_elements, exactly as an assessment
does, records what get_prot_elements returns on each trip, and restores
every flag and the trip counter. That switches out-of-service flags for
a moment, so use it only on a derived copy, never on a master project.

--------------------------------------------------------------------------
USAGE (PowerFactory Python console or a ComPython object, run from the
SystemProtectionAssessment folder so its modules import)
--------------------------------------------------------------------------
    import diag_coord_elements as dce

    # one relay, default test currents
    dce.run("RC-1272720")

    # several relays, chosen currents (amps of fault current)
    dce.run(["RC-1272720", "RC-1198080"], currents=[50, 80, 150, 500])

    # with the live comparison (derived copy only)
    dce.run("RC-1272720", currents=[50, 80, 150, 500], live=True)

Relays are found by loc_name among the relays that sit in a cubicle of the
network model. One text report is written per run.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from relays import current_conversion, elements, trip_time

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Where the report is written. None -> the local output dir, then temp.
REPORT_DIR: Optional[str] = None

DEFAULT_CURRENTS = [50, 100, 200, 400, 800, 1600, 3200]

FAULT_TYPES = ("2-Phase", "Phase-Ground")

# Same value as prot_coordination.prot_coord.FAST_TRIP_RATIO. Copied, not
# imported, so this script does not pull in the typing stubs.
FAST_TRIP_RATIO = 0.5

# sfiec -> SPA category, per relays.elements.get_prot_elements.
TOC_CATEGORY = {
    "I>t": "oc_idmt_elements",
    "IE>t": "ef_idmt_elements",
    "I2>t": "nps_idmt_elements",
}
IOC_CATEGORY = {
    "I>>": "oc_inst_element",
    "IE>>": "ef_inst_element",
    "I2>>": "nps_inst_elements",
}
# Definite-time RelToc are dropped from these two lists only.
NO_DEFINITE_TIME = ("oc_idmt_elements", "ef_idmt_elements")

# Categories used for each fault type, per relays.elements.get_active_elements.
ACTIVE_CATEGORIES = {
    "2-Phase": (
        "oc_idmt_elements", "oc_inst_element",
        "nps_idmt_elements", "nps_inst_elements",
    ),
    "Phase-Ground": (
        "oc_idmt_elements", "oc_inst_element",
        "ef_idmt_elements", "ef_inst_element",
        "nps_idmt_elements", "nps_inst_elements",
    ),
}


# ==========================================================================
# Small helpers
# ==========================================================================

def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """GetAttribute that returns default instead of raising."""
    try:
        return obj.GetAttribute(name)
    except Exception:
        return default


def _name(obj: Any) -> str:
    return str(getattr(obj, "loc_name", obj))


def _cls(obj: Any) -> str:
    try:
        return obj.GetClassName()
    except Exception:
        return "?"


def _flag(value: Any) -> str:
    """Out-of-service flag as text."""
    if value is None:
        return "?"
    return "OUT" if value else "in"


def _num(value: Any, fmt: str = "{:.3f}") -> str:
    if value is None:
        return "-"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return str(value)


class Report:
    """Accumulates the report text."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def w(self, text: str = "") -> None:
        self.lines.append(text)

    def head(self, text: str) -> None:
        self.w()
        self.w("=" * 78)
        self.w(text)
        self.w("=" * 78)

    def sub(self, text: str) -> None:
        self.w()
        self.w(text)
        self.w("-" * len(text))

    def render(self) -> str:
        return "\n".join(self.lines)


# ==========================================================================
# Locating relays
# ==========================================================================

def cubicle_relays(app: Any) -> List[Any]:
    """Relays that sit directly in a cubicle of the network model."""
    net_mod = app.GetProjectFolder("netmod")
    return [
        relay for relay in net_mod.GetContents("*.ElmRelay", True)
        if _cls(relay.GetParent()) == "StaCubic"
    ]


def find_relay(all_relays: Sequence[Any], device_name: str) -> Any:
    """Exactly one relay by loc_name; a unique partial match is accepted."""
    matches = [r for r in all_relays if r.loc_name == device_name]
    if not matches:
        matches = [r for r in all_relays if device_name in r.loc_name]
    if not matches:
        raise RuntimeError(f"No relay matching '{device_name}'")
    if len(matches) > 1:
        names = ", ".join(_name(r) for r in matches[:8])
        raise RuntimeError(
            f"{len(matches)} relays match '{device_name}': {names}"
        )
    return matches[0]


# ==========================================================================
# Reading the relay
# ==========================================================================

def _ancestors(relay: Any, element: Any) -> List[Any]:
    """Objects between the relay and the element, outermost first."""
    chain = []
    parent = element.GetParent()
    guard = 0
    while parent is not None and parent != relay and guard < 10:
        chain.append(parent)
        parent = parent.GetParent()
        guard += 1
    chain.reverse()
    return chain


def read_elements(relay: Any) -> List[Dict[str, Any]]:
    """One record per RelToc / RelIoc anywhere inside the relay."""
    records = []
    found = (
        list(relay.GetContents("*.RelToc", True))
        + list(relay.GetContents("*.RelIoc", True))
    )
    for element in found:
        cls = _cls(element)
        chain = _ancestors(relay, element)
        curve = _attr(element, "e:pcharac") if cls == "RelToc" else None
        try:
            is_oos = bool(element.IsOutOfService())
        except Exception:
            is_oos = None
        records.append({
            "obj": element,
            "cls": cls,
            "path": "\\".join([_name(o) for o in chain] + [_name(element)]),
            "chain": chain,
            "own_out": bool(_attr(element, "e:outserv", 0)),
            "is_oos": is_oos,
            "sfiec": _attr(element, "r:typ_id:e:sfiec"),
            "atype": _attr(element, "r:typ_id:e:atype"),
            "irecl": _attr(element, "r:typ_id:e:irecltarget"),
            "pickup": _attr(element, "e:cpIpset"),
            "tdial": _attr(element, "e:Tpset") if cls == "RelToc" else None,
            "cptotime": _attr(element, "e:cptotime") if cls == "RelIoc" else None,
            "curve": _name(curve) if curve is not None else None,
            "curve_type": _attr(curve, "e:i_type") if curve is not None else None,
        })
    return records


def classify(record: Dict[str, Any], out: bool) -> Tuple[Optional[str], str]:
    """
    SPA category for an element given its out-of-service state.

    Mirrors relays.elements.get_prot_elements. Returns (category, reason);
    category is None when SPA leaves the element out, and reason says why.
    """
    sfiec = record["sfiec"]
    if record["cls"] == "RelToc":
        category = TOC_CATEGORY.get(sfiec)
        if category is None:
            return None, f"function '{sfiec}' is not one SPA reads"
        if out:
            return None, "out of service"
        if (category in NO_DEFINITE_TIME and record["curve"]
                and "definite" in record["curve"].lower()):
            return None, "definite-time curve (left out of oc/ef IDMT)"
        return category, ""
    category = IOC_CATEGORY.get(sfiec)
    if category is None:
        return None, f"function '{sfiec}' is not one SPA reads"
    if out:
        return None, "out of service"
    if not record["irecl"]:
        return None, "no reclose target on its type"
    return category, ""


def spa_categories_now(relay: Any) -> Dict[Any, str]:
    """{element: category} from SPA's own get_prot_elements, as the model stands."""
    result = {}
    for category, items in elements.get_prot_elements(relay).items():
        for item in items:
            result[item] = category
    return result


# ==========================================================================
# Reclose logic
# ==========================================================================

def read_reclose(relay: Any) -> Dict[str, Any]:
    """The relay's RelRecl settings, read the way relays.reclose reads them."""
    info: Dict[str, Any] = {
        "count": 0, "obj": None, "trips": 1, "blocks": [], "logic": [],
        "active": False, "note": "",
    }
    reclosers = relay.GetChildren(0, "*.RelRecl", 1)
    info["count"] = len(reclosers)
    if not reclosers:
        info["note"] = "no RelRecl in the relay: SPA treats it as one trip"
        return info
    recloser = reclosers[0]
    info["obj"] = recloser
    info["outserv"] = _attr(recloser, "outserv")
    info["reclnotactive"] = _attr(recloser, "reclnotactive")
    info["oplockout"] = _attr(recloser, "oplockout")
    info["starttimeframe"] = _attr(recloser, "starttimeframe")
    rec_type = _attr(recloser, "typ_id")
    info["type"] = _name(rec_type) if rec_type is not None else None
    info["blocks"] = list(_attr(rec_type, "blockid", []) or []) if rec_type else []
    info["logic"] = [list(row) for row in (_attr(recloser, "ilogic", []) or [])]
    if info["outserv"] or info["reclnotactive"]:
        info["note"] = (
            "reclosing out of service or not active: SPA counts one trip, "
            "but set_enabled_elements still applies the trip 1 column"
        )
    else:
        info["active"] = True
        try:
            info["trips"] = max(1, int(info["oplockout"] or 1))
        except (TypeError, ValueError):
            info["trips"] = 1
    return info


def match_blocks(relay: Any, info: Dict[str, Any]) -> Tuple[Dict[Any, Dict], List[Dict]]:
    """
    Reclose block -> slot element matches, as set_enabled_elements makes them.

    Returns (switched, rows). switched maps each matched slot object to
    {"logic": [...], "blocks": [names]}; when two blocks match the same
    object the later one wins, as in SPA. rows is one entry per block for
    the report.
    """
    slot_elements = [e for e in (_attr(relay, "pdiselm", []) or []) if e is not None]
    switched: Dict[Any, Dict] = {}
    rows = []
    for i, block in enumerate(info["blocks"]):
        logic = info["logic"][i] if i < len(info["logic"]) else None
        hits = [e for e in slot_elements if block in e.loc_name]
        rows.append({"block": block, "logic": logic, "hits": hits})
        if logic is None:
            continue
        for element in hits:
            entry = switched.setdefault(element, {"logic": logic, "blocks": []})
            entry["logic"] = logic
            entry["blocks"].append(block)
    return switched, rows


def trip_flags(switched: Dict[Any, Dict], trip: int) -> Tuple[Dict[Any, bool], List[str]]:
    """{slot object: out of service} after the blocks are applied for a trip."""
    index = 0 if trip <= 1 else trip - 1
    flags = {}
    problems = []
    for obj, entry in switched.items():
        if index >= len(entry["logic"]):
            problems.append(
                f"{_name(obj)}: logic table has {len(entry['logic'])} "
                f"columns, trip {trip} is beyond it (SPA raises here)"
            )
            continue
        flags[obj] = entry["logic"][index] not in (1.0, 2.0)
    return flags, problems


def element_out(record: Dict[str, Any], flags: Dict[Any, bool], inherit: Any) -> bool:
    """
    Out-of-service state of an element for a trip under one reading.

    inherit: False = own flag only; True = enclosing sub-relays count for
    every element; "ioc" = they count for RelIoc only (what SPA would see
    if IsOutOfService() follows the sub-relay, since RelToc is read on
    e:outserv and RelIoc on IsOutOfService()).
    """
    out = flags.get(record["obj"], record["own_out"])
    if out or not inherit:
        return out
    if inherit == "ioc" and record["cls"] != "RelIoc":
        return out
    for container in record["chain"]:
        if flags.get(container, bool(_attr(container, "e:outserv", 0))):
            return True
    return False


# ==========================================================================
# Times
# ==========================================================================

def element_time(record: Dict[str, Any], current: float, fault_type: str):
    """(time or None, error text) for one element, using SPA's functions."""
    try:
        measured = current_conversion.get_measured_current(
            record["obj"], current, fault_type
        )
        return trip_time.element_trip_time(record["obj"], measured), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def fastest(records: List[Dict], current: float, fault_type: str):
    """Fastest positive time among records, and the record that gave it."""
    best_time, best_record = None, None
    for record in records:
        operate, _ = element_time(record, current, fault_type)
        if not operate or operate <= 0:
            continue
        if best_time is None or operate < best_time:
            best_time, best_record = operate, record
    return best_time, best_record


def delayed_time(times: List[Optional[float]]) -> Optional[float]:
    """prot_coord.delayed_trip_time applied to one time per trip."""
    operating = [t for t in times if t is not None]
    if not operating:
        return None
    reference = operating[-1]
    return min(t for t in operating if t >= FAST_TRIP_RATIO * reference)


# ==========================================================================
# Report sections
# ==========================================================================

def _section_reclose(rep: Report, info: Dict[str, Any], rows: List[Dict],
                     switched: Dict[Any, Dict]) -> None:
    rep.sub("1. Reclose element")
    if info["obj"] is None:
        rep.w(f"  {info['note']}")
        return
    if info["count"] > 1:
        rep.w(f"  WARNING: {info['count']} RelRecl objects; SPA asserts on this.")
    rep.w(f"  RelRecl            : {_name(info['obj'])}  (type {info['type']})")
    rep.w(f"  out of service     : {_flag(info['outserv'])}")
    rep.w(f"  reclnotactive      : {info['reclnotactive']}")
    rep.w(f"  oplockout          : {info['oplockout']}")
    rep.w(f"  starttimeframe     : {info['starttimeframe']}")
    rep.w(f"  trips SPA will step: {info['trips']}")
    if info["note"]:
        rep.w(f"  NOTE: {info['note']}")

    rep.sub("2. Reclose blocks and the slot elements they switch")
    if not rows:
        rep.w("  The RelRecl type has no blocks (blockid is empty).")
        return
    if len(info["logic"]) != len(info["blocks"]):
        rep.w(
            f"  WARNING: {len(info['blocks'])} blocks on the type but "
            f"{len(info['logic'])} rows in ilogic."
        )
    rep.w("  logic values: 1 = trip and reclose, 2 = trip to lockout, "
          "anything else = blocked")
    for row in rows:
        logic = "no ilogic row" if row["logic"] is None else str(row["logic"])
        rep.w(f"  block '{row['block']}'  logic {logic}")
        if not row["hits"]:
            rep.w("      -> NO slot element name contains this block name; "
                  "it switches nothing")
        for hit in row["hits"]:
            cls = _cls(hit)
            extra = ""
            if cls == "ElmRelay":
                inside = (len(hit.GetContents("*.RelToc", True))
                          + len(hit.GetContents("*.RelIoc", True)))
                extra = (f"  <-- SUB-RELAY CONTAINER holding {inside} "
                         f"RelToc/RelIoc")
            elif cls not in ("RelToc", "RelIoc"):
                extra = "  <-- not a RelToc/RelIoc"
            rep.w(f"      -> {_name(hit)} ({cls}, now "
                  f"{_flag(_attr(hit, 'e:outserv'))}){extra}")
    doubles = {o: e for o, e in switched.items() if len(e["blocks"]) > 1}
    for obj, entry in doubles.items():
        rep.w(f"  NOTE: {_name(obj)} is matched by blocks "
              f"{entry['blocks']}; the last one sets its logic.")


def _section_elements(rep: Report, relay: Any, records: List[Dict]) -> None:
    rep.sub("3. Every RelToc / RelIoc in the relay, as the model stands")
    now = spa_categories_now(relay)
    rep.w("  own = element's own flag; IsOOS = IsOutOfService(); "
          "encl = enclosing sub-relays that are out")
    rep.w("")
    disagree = []
    hierarchy_seen = []
    for record in records:
        encl_out = [
            _name(c) for c in record["chain"] if _attr(c, "e:outserv", 0)
        ]
        # State as SPA reads it today: RelToc on its own flag, RelIoc on
        # IsOutOfService().
        out_now = (record["own_out"] if record["cls"] == "RelToc"
                   else bool(record["is_oos"]))
        category, reason = classify(record, out_now)
        spa = now.get(record["obj"])
        if spa != category:
            disagree.append((record["path"], category, spa))
        if record["is_oos"] is not None and record["is_oos"] != record["own_out"]:
            hierarchy_seen.append(record["path"])
        verdict = f"SPA: {category}" if category else f"left out: {reason}"
        rep.w(f"  {record['path']}")
        rep.w(
            f"      {record['cls']}  function {record['sfiec']}  "
            f"measures {record['atype']}  own {_flag(record['own_out'])}  "
            f"IsOOS {_flag(record['is_oos'])}  "
            f"encl {', '.join(encl_out) if encl_out else '-'}"
        )
        if record["cls"] == "RelToc":
            rep.w(
                f"      pickup {_num(record['pickup'], '{:.1f}')} A  "
                f"time setting {_num(record['tdial'])}  "
                f"curve '{record['curve']}' (i_type {record['curve_type']})"
            )
        else:
            rep.w(
                f"      pickup {_num(record['pickup'], '{:.1f}')} A  "
                f"operate time {_num(record['cptotime'])} s  "
                f"reclose target {record['irecl']}"
            )
        rep.w(f"      {verdict}")
    rep.w("")
    if hierarchy_seen:
        rep.w("  IsOutOfService() differs from the element's own flag on:")
        for path in hierarchy_seen:
            rep.w(f"      {path}")
        rep.w("  so IsOutOfService() takes the enclosing objects into account.")
    else:
        rep.w("  IsOutOfService() equals the own flag on every element here "
              "(no evidence either way on whether it follows a sub-relay).")
    if disagree:
        rep.w("  SELF-CHECK FAILED: this script's classification differs from "
              "relays.elements.get_prot_elements for:")
        for path, mine, spa in disagree:
            rep.w(f"      {path}: script {mine}, SPA {spa}")
        rep.w("  Treat section 4 with caution and send this report back.")
    else:
        rep.w("  Self-check: classification matches "
              "relays.elements.get_prot_elements for every element.")


def _active_for_trip(records, flags, inherit):
    """{fault type: [records]} that SPA would treat as active."""
    active = {fault_type: [] for fault_type in FAULT_TYPES}
    for record in records:
        category, _ = classify(record, element_out(record, flags, inherit))
        if category is None:
            continue
        for fault_type in FAULT_TYPES:
            if category in ACTIVE_CATEGORIES[fault_type]:
                active[fault_type].append(record)
    return active


def _simulate(records, switched, trips, inherit):
    """[(active per fault type, problems)] for trips 1..N."""
    result = []
    for trip in range(1, trips + 1):
        flags, problems = trip_flags(switched, trip)
        result.append((_active_for_trip(records, flags, inherit), problems))
    return result


def _write_model(rep: Report, title: str, per_trip, currents) -> None:
    rep.w()
    rep.w(f"  [{title}]")
    for number, (active, problems) in enumerate(per_trip, start=1):
        for problem in problems:
            rep.w(f"    Trip {number}: PROBLEM {problem}")
        for fault_type in FAULT_TYPES:
            names = [r["path"] for r in active[fault_type]]
            first = [r["path"] for r in per_trip[0][0][fault_type]]
            if number > 1 and names == first:
                rep.w(f"    Trip {number} {fault_type}: {len(names)} active "
                      f"- SAME AS TRIP 1")
                continue
            rep.w(f"    Trip {number} {fault_type}: {len(names)} active")
            for name in names:
                rep.w(f"        {name}")
    for fault_type in FAULT_TYPES:
        rep.w()
        rep.w(f"    {fault_type}: fastest element on each trip, and the "
              f"backup time SPA grades against")
        for current in currents:
            times = []
            parts = []
            for number, (active, _) in enumerate(per_trip, start=1):
                operate, record = fastest(active[fault_type], current, fault_type)
                times.append(operate)
                if operate is None:
                    parts.append(f"T{number} no trip")
                else:
                    parts.append(
                        f"T{number} {operate:.3f} s ({record['path']})"
                    )
            graded = delayed_time(times)
            rep.w(f"      {current:>7} A: graded as "
                  f"{_num(graded)} s   |  " + "; ".join(parts))


def _section_trips(rep: Report, records, switched, info, currents) -> None:
    rep.sub("4. Active elements and times per trip (worked out, nothing switched)")
    trips = info["trips"]
    own = _simulate(records, switched, trips, inherit=False)
    inherited = _simulate(records, switched, trips, inherit=True)

    def names(per_trip):
        return [
            {ft: sorted(r["path"] for r in active[ft]) for ft in FAULT_TYPES}
            for active, _ in per_trip
        ]

    if names(own) == names(inherited):
        rep.w("  Own flags and container flags give the same active elements.")
        _write_model(rep, "active elements", own, currents)
    else:
        rep.w("  The two readings DIFFER: a sub-relay container is switched "
              "or out of service.")
        _write_model(rep, "own flags (how SPA reads RelToc today)", own, currents)
        _write_model(rep, "container flags inherited", inherited, currents)

    errors = []
    for record in records:
        for fault_type in FAULT_TYPES:
            _, error = element_time(record, currents[0], fault_type)
            if error:
                errors.append(f"{record['path']} ({fault_type}): {error}")
    if errors:
        rep.w()
        rep.w("  Elements whose time could not be calculated:")
        for error in sorted(set(errors)):
            rep.w(f"      {error}")
    return own, inherited


def _section_live(rep: Report, relay: Any, info, own, inherited,
                  records, switched) -> None:
    """Step the relay with SPA's own functions and compare with section 4."""
    rep.sub("5. Live comparison (SPA's own set_enabled_elements; flags restored)")
    from relays import reclose

    recloser = info["obj"]
    original_frame = _attr(recloser, "starttimeframe") if recloser else None
    seen = []
    reclose.reset_reclosing(relay)
    try:
        for trip in range(1, info["trips"] + 1):
            status = reclose.set_enabled_elements(relay)
            try:
                got = elements.get_prot_elements(relay)
                seen.append({
                    fault_type: sorted(
                        _path_of(relay, e)
                        for e in elements.get_active_elements(got, fault_type)
                    )
                    for fault_type in FAULT_TYPES
                })
            finally:
                reclose.reset_block_service_status(status)
            if trip < info["trips"]:
                reclose.trip_count(relay, increment=True)
    finally:
        reclose.reset_reclosing(relay)
        if recloser is not None and original_frame is not None:
            recloser.starttimeframe = original_frame

    def names(per_trip):
        return [
            {ft: sorted(r["path"] for r in active[ft]) for ft in FAULT_TYPES}
            for active, _ in per_trip
        ]

    for number, trip_seen in enumerate(seen, start=1):
        for fault_type in FAULT_TYPES:
            if number > 1 and trip_seen[fault_type] == seen[0][fault_type]:
                rep.w(f"    Trip {number} {fault_type}: "
                      f"{len(trip_seen[fault_type])} active - SAME AS TRIP 1")
                continue
            rep.w(f"    Trip {number} {fault_type}: "
                  f"{len(trip_seen[fault_type])} active")
            for name in trip_seen[fault_type]:
                rep.w(f"        {name}")
    rep.w()
    mixed = names(_simulate(records, switched, info["trips"], inherit="ioc"))
    if seen == names(own) == names(inherited):
        rep.w("  Live result matches section 4.")
    elif seen == names(own):
        rep.w("  Live result matches the OWN FLAGS reading: switching a "
              "sub-relay does not take its elements out for SPA.")
    elif seen == names(inherited):
        rep.w("  Live result matches the CONTAINER FLAGS reading.")
    elif seen == mixed:
        rep.w("  Live result: RelIoc elements follow their sub-relay but "
              "RelToc elements do not. SPA reads RelToc on e:outserv and "
              "RelIoc on IsOutOfService(), so a blocked trip keeps its "
              "time-overcurrent elements.")
    else:
        rep.w("  Live result matches NEITHER reading in section 4: SPA is "
              "not seeing the flags it has just written. Send this report "
              "back.")


def _path_of(relay: Any, element: Any) -> str:
    return "\\".join(
        [_name(o) for o in _ancestors(relay, element)] + [_name(element)]
    )


# ==========================================================================
# Entry point
# ==========================================================================

def diagnose(relay: Any, currents: Sequence[float], live: bool = True) -> str:
    rep = Report()
    rep.head(f"COORDINATION ELEMENT DIAGNOSTIC: {_name(relay)}")
    relay_type = _attr(relay, "typ_id")
    rep.w(f"  relay type : {_name(relay_type) if relay_type is not None else None}")
    rep.w(f"  cubicle    : {_name(relay.GetParent())}")
    rep.w(f"  outserv    : {_flag(_attr(relay, 'e:outserv'))}")
    rep.w(f"  currents   : {list(currents)} A")

    info = read_reclose(relay)
    switched, rows = match_blocks(relay, info)
    _section_reclose(rep, info, rows, switched)

    records = read_elements(relay)
    _section_elements(rep, relay, records)

    own, inherited = _section_trips(rep, records, switched, info, list(currents))

    if live:
        _section_live(rep, relay, info, own, inherited, records, switched)
    return rep.render()


def _report_path() -> str:
    if REPORT_DIR:
        base = REPORT_DIR
    else:
        base = r"//client/c$/LocalData/PowerFactory Output Folders"
        if not os.path.isdir(base):
            base = tempfile.gettempdir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(base, f"coord_element_diagnostic_{stamp}.txt")


def run(
    device_names,
    currents: Optional[Sequence[float]] = None,
    live: bool = False,
    app: Any = None,
    write_file: bool = True,
) -> str:
    """
    Run the diagnostic on one relay name or a list of names.

    Returns the report text, prints it to the output window and writes it
    to one text file. A relay that cannot be found or read is reported
    and the rest still run.
    """
    if app is None:
        import powerfactory
        app = powerfactory.GetApplication()
    if isinstance(device_names, str):
        device_names = [device_names]
    currents = list(currents) if currents else DEFAULT_CURRENTS

    project = app.GetActiveProject()
    header = [
        f"Project : {_name(project) if project is not None else None}",
        f"Live    : {'YES - flags switched and restored' if live else 'no - read only'}",
    ]
    all_relays = cubicle_relays(app)
    parts = ["\n".join(header)]
    for device_name in device_names:
        try:
            relay = find_relay(all_relays, device_name)
            parts.append(diagnose(relay, currents, live))
        except Exception as exc:
            parts.append(
                f"\n{'=' * 78}\n{device_name}: FAILED "
                f"{type(exc).__name__}: {exc}\n{'=' * 78}"
            )
    text = "\n".join(parts)

    for line in text.splitlines():
        app.PrintPlain(line)

    if write_file:
        path = _report_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            app.PrintPlain(f"Report written to {path}")
        except OSError as exc:
            app.PrintWarn(f"Could not write report: {exc}")
    return text


if __name__ == "__main__":
    # Bundaberg-GinGin derived copy from the 20 Nov run. The first three
    # are graded as tripping in 0.02 s at 48-80 A; RC-85090 is a control
    # that grades with sensible times.
    run(
        ["RC-1272720", "RC-1198080", "RC-80501", "RC-85090"],
        currents=[50, 80, 150, 300, 600, 1200],
    )
