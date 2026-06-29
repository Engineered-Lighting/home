#!/usr/bin/env python3
"""test-zonelog.py — unit tests for the predictive-lighting zone-transition
logger (addons/predictive-lighting/zonelog.py, Addendum 37 Phase 1).

Feeds synthetic frigate/events messages with controlled timestamps and
checks the committed-zone state machine: most-specific-zone resolution, the
600 ms commit debounce, single-frame flutter rejection, dwell_suspect, the
silent first-commit, and the __exit__ terminal record. Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "zonelog", HERE.parent / "addons" / "predictive-lighting" / "zonelog.py")
zl = importlib.util.module_from_spec(_spec)            # type: ignore
_spec.loader.exec_module(zl)                           # type: ignore

_passed = 0
_failed = 0


def check(name, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def ev(etype, tid, zones, **kw):
    after = {"id": tid, "label": "person", "camera": "living_room",
             "current_zones": zones}
    after.update(kw)
    return {"type": etype, "after": after}


# ───────────────────────── tod / dow buckets ──────────────────────────
import datetime as _dt
def _at(h):
    return _dt.datetime(2026, 5, 20, h, 0, 0).timestamp()

check("tod_bucket: 03:00 -> night", zl.tod_bucket(_at(3)) == "night")
check("tod_bucket: 08:00 -> morning", zl.tod_bucket(_at(8)) == "morning")
check("tod_bucket: 12:00 -> midday", zl.tod_bucket(_at(12)) == "midday")
check("tod_bucket: 16:00 -> afternoon", zl.tod_bucket(_at(16)) == "afternoon")
check("tod_bucket: 20:00 -> evening", zl.tod_bucket(_at(20)) == "evening")
check("tod_bucket: 23:00 -> late", zl.tod_bucket(_at(23)) == "late")

# ─────────────────────────── normalize_zones ──────────────────────────

check("normalize_zones: lowercases + drops unknown",
      zl.normalize_zones(["Sofa", "WHOLE_LIVING_ROOM", "bogus_zone"])
      == ["sofa", "whole_living_room"])
check("normalize_zones: non-list -> []", zl.normalize_zones(None) == [])
check("normalize_zones: sorted + deduped",
      zl.normalize_zones(["sofa", "office", "sofa"]) == ["office", "sofa"])

# ──────────────────────────── most_specific ───────────────────────────

check("most_specific: sub-zone beats aggregate",
      zl.most_specific(["sofa", "whole_living_room"], None) == "sofa")
check("most_specific: aggregate-only falls back to aggregate",
      zl.most_specific(["whole_living_room"], None) == "whole_living_room")
check("most_specific: sticky — keeps committed when still present",
      zl.most_specific(["office", "sofa"], "sofa") == "sofa")
check("most_specific: deterministic tie-break when not sticky",
      zl.most_specific(["office", "sofa"], None) == "office")
check("most_specific: no known zone -> None",
      zl.most_specific([], None) is None)

# ──────────────── first commit is silent (no transition) ──────────────

t = zl.ZoneTracker()
r = t.process(ev("update", "k1", ["sofa"]), 100.0)
check("first sighting: candidate only, no record", r == [])
r = t.process(ev("update", "k1", ["sofa"]), 100.7)
check("first commit emits an __entry__ -> zone record",
      len(r) == 1 and r[0]["from_zone"] == zl.ENTRY
      and r[0]["to_zone"] == "sofa", str(r))
check("entry record: prev_zone None, not dwell_suspect",
      bool(r) and r[0]["prev_zone"] is None
      and r[0]["dwell_suspect"] is False, str(r))

# ─────────────────── a committed zone-to-zone move ────────────────────

r = t.process(ev("update", "k1", ["weights"]), 200.0)
check("new candidate weights: no record yet", r == [])
r = t.process(ev("update", "k1", ["weights"]), 200.7)
check("weights held 0.7s -> commit sofa->weights", len(r) == 1,
      str(r))
if r:
    rec = r[0]
    check("transition record: from_zone/to_zone correct",
          rec["from_zone"] == "sofa" and rec["to_zone"] == "weights",
          str(rec))
    check("transition record: long dwell -> not suspect",
          rec["dwell_suspect"] is False, str(rec["from_dwell_s"]))
    check("transition record: carries track_id + camera",
          rec["track_id"] == "k1" and rec["camera"] == "living_room")

# ───────────────── single-frame flutter is rejected ───────────────────

t2 = zl.ZoneTracker()
t2.process(ev("update", "k2", ["sofa"]), 0.0)
t2.process(ev("update", "k2", ["sofa"]), 0.6)          # commit sofa (silent)
r = t2.process(ev("update", "k2", ["office"]), 5.0)    # one flutter frame
check("flutter: single office frame -> no record", r == [])
r = t2.process(ev("update", "k2", ["sofa"]), 5.2)      # back to sofa fast
check("flutter: back to sofa before debounce -> no transition", r == [])

# ───────────────────────── dwell_suspect flag ─────────────────────────

t3 = zl.ZoneTracker()
t3.process(ev("update", "k3", ["sofa"]), 0.0)
t3.process(ev("update", "k3", ["sofa"]), 0.6)          # commit sofa @0.6
t3.process(ev("update", "k3", ["office"]), 0.7)        # candidate office
r = t3.process(ev("update", "k3", ["office"]), 1.3)    # commit @1.3
check("dwell_suspect: sofa held 0.7s (<1.2) -> suspect True",
      len(r) == 1 and r[0]["dwell_suspect"] is True,
      str(r))

# ─────────────────────── __exit__ on track end ────────────────────────

t4 = zl.ZoneTracker()
t4.process(ev("update", "k4", ["kitchen_sink"]), 0.0)  # unknown -> ignored
t4.process(ev("update", "k4", ["sink"]), 1.0)
t4.process(ev("update", "k4", ["sink"]), 1.7)          # commit sink (silent)
r = t4.process(ev("end", "k4", ["sink"]), 50.0)
check("track end -> __exit__ transition record",
      len(r) == 1 and r[0]["from_zone"] == "sink"
      and r[0]["to_zone"] == zl.EXIT, str(r))
check("track end: track removed from table", t4.active_tracks() == 0)

# ───────────────────── non-person events ignored ──────────────────────

t5 = zl.ZoneTracker()
r = t5.process({"type": "update",
                "after": {"id": "dog1", "label": "dog",
                          "camera": "living_room",
                          "current_zones": ["sofa"]}}, 0.0)
check("non-person label ignored", r == [] and t5.active_tracks() == 0)
check("malformed message -> [] (no crash)", t5.process("garbage", 0.0) == [])
check("missing after -> [] (no crash)", t5.process({"type": "x"}, 0.0) == [])

# ────────────────── prev_zone / prev2_zone history ────────────────────

t6 = zl.ZoneTracker()
t6.process(ev("update", "k6", ["sofa"]), 0.0)
t6.process(ev("update", "k6", ["sofa"]), 0.6)          # commit sofa
t6.process(ev("update", "k6", ["office"]), 10.0)
r1 = t6.process(ev("update", "k6", ["office"]), 10.7)  # sofa->office
t6.process(ev("update", "k6", ["weights"]), 20.0)
r2 = t6.process(ev("update", "k6", ["weights"]), 20.7)  # office->weights
check("history: first move has prev_zone None",
      r1 and r1[0]["prev_zone"] is None, str(r1))
check("history: second move prev_zone=sofa",
      r2 and r2[0]["prev_zone"] == "sofa", str(r2))

# ──────────────────── raw [x,y] foot-position ─────────────────────────

tp = zl.ZoneTracker()
tp.process(ev("update", "kp", ["sofa"], box=[100, 200, 300, 640]), 0.0)
rp = tp.process(ev("update", "kp", ["sofa"], box=[100, 200, 300, 640]), 0.7)
check("record carries normalised foot-position (box bottom-centre)",
      bool(rp) and abs(rp[0]["x"] - 0.1563) < 0.001
      and abs(rp[0]["y"] - 0.8889) < 0.001, str(rp))
rp = tp.process(ev("end", "kp", ["sofa"]), 5.0)
check("position: an event with no box -> x,y are None",
      bool(rp) and rp[0]["x"] is None and rp[0]["y"] is None, str(rp))

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(0 if _failed == 0 else 1)
