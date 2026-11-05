"""Synthetic records for rung 10's tests, written from schemas.json.

Nothing here is real data. The illustrative evening (A at the counter, B in
the armchair) comes straight from the Decision, LightState and Correction
examples in docs/architecture/live-v1/schemas.json; the fake light model is
built so that it reproduces the walkthrough's predictions: 510 lx at counter-1
for the decision's applied scene and 368 lx once A's dim pins F1.spot at 50%
(case V11). The Hue fixtures use den_lighting's four bulbs.
"""
import copy
import json

from records import SCHEMAS

UTC_OFFSET_S = -7 * 3600  # the walkthrough's evening: A@1793066610.0 is 19:03:30 local
T0 = 1793066609.0         # ls-190329, the frame the walkthrough decision read
HUE = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")


def example(record_id):
    data = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    for r in data["records"]:
        if r["id"] == record_id:
            return copy.deepcopy(r["example"])
    raise KeyError(record_id)


def v11_model():
    """A light-model.json in rung 8's plan's shape for the walkthrough's channels."""
    return {
        "version": "lm-den-3", "room_version": "den-v4", "meter_norm": "meter-norm/v1",
        "sample_points": [
            {"point_id": "counter-1", "kind": "task", "xyz": [1.2, 0.8, 0.9], "normal": None, "rect": None},
            {"point_id": "armchair-book", "kind": "task", "xyz": [3.5, 2.0, 0.6], "normal": None, "rect": None},
        ],
        "basis_unit": "lx",
        "basis": {
            "F1.spot": {"3500": {"counter-1": 686.0, "armchair-book": 8.4866}},
            "F1.radial_4+5": {"2700": {"counter-1": 100.0, "armchair-book": 80.0}},
            "F2.spot": {"2700": {"counter-1": 0.0, "armchair-book": 700.0}},
        },
        "dimming": {
            # g(0.5) = 0.5 and g(0.7) = 485/686, so the spot gives 343 lx and 485 lx at counter-1
            "F1.spot": [[0, 0], [127, 0.5], [177.8, 485.0 / 686.0], [254, 1]],
            "F1.radial_4+5": [[0, 0], [88.9, 0.25], [254, 1]],
            "F2.spot": [[0, 0], [114.3, 0.4], [254, 1]],
        },
        "bounce": "measured", "glare": None,
        "error": {"median_pct": None, "p90_pct": None, "note": "fake model for tests"},
    }


def light_state(ts, values, anchored=True, windows=None, basis=None, clipped=None, unit=None, ident=None):
    rec = example("light_state")
    rec.pop("anchored_status", None)
    rec["ts"] = ts
    rec["anchored"] = anchored
    unit = unit or ("lx" if anchored else "nc")
    rec["surface_estimates"] = [
        {"surface_id": s, "value": v, "unit": unit, "clipped_frac": (clipped or {}).get(s, 0.0),
         "rho_status": "assumed", "basis": (basis or {}).get(s, "measured")}
        for s, v in values.items()]
    if windows is not None:
        rec["window_daylight"] = [{"window_id": w, "level": lv, "unit": "fraction of noon"}
                                  for w, lv in windows.items()]
    rec["residual"] = []
    if ident:
        rec["id"] = ident
    return rec


def v11_evening():
    """The walkthrough: decision d-190330-f1f2, A's dim c-190405 and four frames."""
    decision = example("decision")
    correction = example("correction")
    before = example("light_state")  # ls-190329: counter-1 25 lx, armchair-book 300 lx
    frames = [
        before,
        light_state(T0 + 2.0, {"counter-1": 250, "armchair-book": 303}),   # 19:03:31, mid-fade
        light_state(T0 + 4.0, {"counter-1": 470, "armchair-book": 310}),   # ls-190333, verify frame
        light_state(T0 + 40.0, {"counter-1": 357, "armchair-book": 305}),  # ls-190409, after A's dim
    ]
    for f in frames[1:]:
        f["window_daylight"] = copy.deepcopy(before["window_daylight"])
    return [decision], frames, [correction]


# -- Hue fixtures --------------------------------------------------------------

def hue_model(points, unit="relative", kelvins=(2202, 6500)):
    """Four bulbs, a shared linear dimming curve, basis[bulb][k][point] from `points`.

    `points` maps point_id -> {bulb: value at full}; the cool end is 10% brighter
    so mired interpolation is exercised.
    """
    basis = {}
    for b in HUE:
        basis[b] = {}
        for i, k in enumerate(kelvins):
            basis[b][str(k)] = {p: float(v.get(b, 0.0)) * (1.0 + 0.1 * i) for p, v in points.items()}
    return {
        "version": "lm-hue-test", "room_version": "den-test", "meter_norm": "meter-norm/v1",
        "sample_points": [{"point_id": p, "kind": "task", "xyz": [float(i), 0.0, 0.8], "normal": None,
                           "rect": None} for i, p in enumerate(points)],
        "basis_unit": unit, "basis": basis,
        "dimming": {HUE[0]: [[0, 0], [254, 1]], **{b: {"same_as": HUE[0]} for b in HUE[1:]}},
        "bounce": "measured", "glare": None, "error": {"note": "fake model for tests"},
    }


def hue_decision(did, ts, before, applied, ref, policy="den_lighting@1.0.4", targets=None,
                 recipients=None, eyes=(), cct_k=2700, model="lm-hue-test", hold_set=None):
    """A Decision on hue-den-4.

    before: {bulb: level}; applied: {bulb: {"status", "level", "steps": [...]}}.
    A step is {"kind", "sent_at", ...}; result defaults to a done read-back.
    """
    rec = {
        "decision_id": did, "ts": ts, "trigger": "person_state change", "policy": policy,
        "actuator_domain": "hue-den-4", "actuation": "synthetic",
        "inputs": {
            "person_state": [f"A@{ts}"], "light_state": ref, "light_model": model,
            "hold_set": hold_set or {"revision": 1, "held": []}, "revisions": {"hold_set": 1, "mode": 1},
            "actuator_state": {b: {"on": lv > 0, "level": lv, "v_hat": lv, "cct_k": cct_k,
                                   "motion_status": "settled", "source": "read-back", "as_of": ts}
                               for b, lv in before.items()},
        },
        "targets": targets or {},
        "requested_scene": {b: {"level": a["level"], "cct_k": cct_k} for b, a in applied.items()},
        "applied_scene": {},
        "skipped": [], "status": "applied", "hard_conflicts": [],
        "recipients": recipients or {},
        "predicted": {"surfaces": {}, "eyes": [{"track_id": t} for t in eyes]},
    }
    for b, a in applied.items():
        steps = []
        for s in a.get("steps") or []:
            s = dict(s)
            s.setdefault("revisions", {"hold_set": 1, "mode": 1})
            if s.get("sent_at") is not None:
                s.setdefault("result", {"status": "done", "at": s["sent_at"] + 0.2,
                                        "readback": {"level": a["level"], "cct_k": cct_k}})
            steps.append(s)
        rec["applied_scene"][b] = {"status": a.get("status", "sent"),
                                   "readback": {"level": a["level"], "cct_k": cct_k}, "steps": steps}
    return rec
