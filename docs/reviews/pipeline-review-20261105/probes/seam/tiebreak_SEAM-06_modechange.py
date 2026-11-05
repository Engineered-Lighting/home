#!/usr/bin/env python3
"""tiebreak_SEAM-06_modechange: side check for the SEAM-06 tiebreak. den-core's README says trigger 'mode change'
is 'for occupancy and game edges', but decide.py labels every light_observed (bulb read-back) 'mode change', and
decide.py's compensation 'mode' form drops the P1-P3 bound whenever trigger == 'mode change'. Does a plain bulb
read-back after a hold opens make P1 re-plan without its bound?

    python3 -B tiebreak_SEAM-06_modechange.py <den-core-live checkout root>

In-process, synthetic events, decide's own fake light model; no files, network or hardware.
Flow per option: 4 bulbs read back at 180 -> mode (normal 180, occupancy on) -> occupancy on -> need-like
PersonState for 'room' -> mode with a hold on the front-left bulb pinned at 60 ('hold change') -> a light_observed
read-back of that bulb at 60 (labelled 'mode change'). Prints each decision's trigger and the free bulbs' levels.
"""
import sys
from pathlib import Path

core = Path(sys.argv[1]).resolve() / "tools" / "den-core"
sys.dont_write_bytecode = True
sys.path.insert(0, str(core))
import decide  # noqa: E402

T0 = 1793764800.0
L = decide.LIGHTS
SCENE = {"on": True, "brightness": 180, "kelvin": 2700}


def ev(t, kind, data):
    return {"schema": "den-event-1", "t": float(t), "seq": 0, "kind": kind, "source": "x", "data": data}


def flow():
    out = [ev(T0, "light_observed", {"entity_id": b, **SCENE, "context": "own"}) for b in L]
    out.append(ev(T0, "mode", {"policy": "den_lighting@1.0.4", "revision": 1, "gaming": False, "occupancy": "on",
                               "holds": [], "normal": {b: dict(SCENE) for b in L}}))
    out.append(ev(T0 + 1, "occupancy", {"value": "on", "via": "synthetic"}))
    out.append(ev(T0 + 2, "person_state", {
        "schema_version": "person_state/v1", "state_id": "room@%.3f" % (T0 + 2), "emitted_ts": T0 + 2,
        "track_id": "room", "identity": "unbound",
        "focused_engagement": {"state": "none", "since_s": None, "onset_ts": None, "confidence": None,
                               "kind": "reading"},
        "field_support": {"focused_engagement": {"last_support_capture_ts": T0 + 1.5, "value_set_ts": T0 + 2,
                                                 "support_status": "observed"}}, "flags": []}))
    pinned = {"on": True, "brightness": 60, "kelvin": 2700}
    out.append(ev(T0 + 10, "mode", {"policy": "den_lighting@1.0.4", "revision": 2, "gaming": False,
                                    "occupancy": "on", "normal": {b: dict(SCENE) for b in L},
                                    "holds": [{"entity_id": L[0], "pinned": pinned, "since": T0 + 10,
                                               "lifetime": "room_vacant_2_minutes"}]}))
    out.append(ev(T0 + 11, "light_observed", {"entity_id": L[0], **pinned, "context": "person"}))
    return out


for option in ("P1", "P0"):
    pol = decide.make_policy({"compensation": {"option": option}})
    print("RESULT option %s (compensation_form slot proposed=%r)" % (
        option, pol.config["slots"]["compensation_form"]["proposed"]))
    for e in flow():
        for o in pol.handle(e):
            if o["kind"] != "decision":
                continue
            d = o["data"]
            free = {b.split(".")[1]: (s or {}).get("brightness") for b, s in sorted(d["requested_scene"].items())
                    if b != L[0]}
            print("RESULT   t=+%-4.0f input=%-15s trigger=%-20r free bulbs %s | %s" % (
                e["t"] - T0, e["kind"], d["trigger"], free, d["why"][:90]))
