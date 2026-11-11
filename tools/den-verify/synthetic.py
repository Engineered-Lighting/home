"""Synthetic records for rung 10's tests, written from schemas.json.

Nothing here is real data. The illustrative evening (A at the counter, B in
the armchair) comes straight from the Decision, LightState and Correction
examples in docs/architecture/live-v1/schemas.json; the synthetic light model
is built so that it reproduces the walkthrough's predictions: 510 lx at
counter-1 for the decision's applied scene and 368 lx once A's dim pins F1.spot
at 50% (case V11). The Hue fixtures use den_lighting's four bulbs.

Light models are in the shape rung 8 Stage 1 writes (`light_model()` in
tools/den-light/model.py; tools/den-light/README.md), so rung 10's tests run
rung 8's own predict() over them.
"""
import copy
import json

from records import SCHEMAS

UTC_OFFSET_S = -7 * 3600  # the walkthrough's evening: A@1793066610.0 is 19:03:30 local
T0 = 1793066609.0         # ls-190329, the frame the walkthrough decision read
HUE = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
HUE_PAIRS = (HUE[:2], HUE[2:])  # front and rear share a dimming curve, as rung 8 fits them
MIRED = "linear in mired between the captured temperatures, clamped outside them"


def example(record_id):
    data = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    for r in data["records"]:
        if r["id"] == record_id:
            return copy.deepcopy(r["example"])
    raise KeyError(record_id)


def hue_channel(entity_id):
    """rung 8's channel key for a den bulb: `<entity without light.>.white`, as probe.py names it."""
    return f"{entity_id.split('.', 1)[1]}.white"


def sample_point(point_id, kind, xyz, excluded=None):
    return {"point_id": point_id, "kind": kind, "xyz": xyz, "normal": None, "rect": None, "excluded": excluded}


def channel(fixture_id, channel_id, entity_id, kind, basis, levels, g, cct_range_k=None, shared_with=None):
    """One channel: `basis` maps kelvin -> {point_id: value at full}; `levels`/`g` its dimming table."""
    return {"channel_key": f"{fixture_id}.{channel_id}", "fixture_id": fixture_id, "channel_id": channel_id,
            "entity_id": entity_id, "kind": kind, "cct_range_k": cct_range_k,
            "basis": basis, "levels": levels, "g": g, "shared_with": shared_with}


def light_model_record(version, room_version, points, channels, unit, shared_curve=None):
    """A light-model.json with the fields predict() and rung 10 read, named as model.light_model names them.

    A point with `excluded` set has None in every basis, as the fit writes it. A
    v1 model's points are camera blocks with kind and xyz None; these carry both,
    so rung 10's geometry is exercised. The session's provenance (room_view,
    anchor, sessions, method hashes) is left out: nothing here reads it.
    """
    basis, cct, dimming, listed = {}, {}, {}, []
    for c in channels:
        key = c["channel_key"]
        listed.append({k: c[k] for k in ("channel_key", "fixture_id", "channel_id", "entity_id", "kind",
                                         "cct_range_k")})
        kelvins = list(c["basis"])
        keys = [f"{key}@{k}K" for k in kelvins]
        for k, bk in zip(kelvins, keys):
            basis[bk] = {p["point_id"]: None if p["excluded"] else float(c["basis"][k].get(p["point_id"], 0.0))
                         for p in points}
        cct[key] = {"kelvin": kelvins, "basis_keys": keys, "entity_id": c["entity_id"], "interpolation": MIRED}
        dimming[key] = {"levels": c["levels"], "g": c["g"], "shared_with": c["shared_with"],
                        "command_unit": "Hue brightness 0 to 254", "monotone": "pool-adjacent-violators"}
    return {"record": "light_model", "contract": "live-v1", "version": version, "room_version": room_version,
            "meter_norm": {"version": "meter-norm/v1", "parameters_sha256": None},
            "sample_points": points, "channels": listed, "basis": basis, "basis_unit": unit, "spot_model": None,
            "dimming": dimming, "shared_curve": shared_curve or {}, "cct": cct, "bounce": "measured",
            "glare": None, "error": {"median_pct": None, "p90_pct": None, "note": "synthetic model for tests"}}


def v11_model():
    """A light-model.json for the walkthrough's channels: F1 and F2 as schemas.json's fixture names them."""
    points = [sample_point("counter-1", "task", [1.2, 0.8, 0.9]),
              sample_point("armchair-book", "task", [3.5, 2.0, 0.6])]
    return light_model_record("lm-den-3", "den-v4", points, [
        # g(0.5) = 0.5 and g(0.7) = 485/686, so the spot gives 343 lx and 485 lx at counter-1
        channel("F1", "spot", None, "gimbal spot", {3500: {"counter-1": 686.0, "armchair-book": 8.4866}},
                [0, 127, 177.8, 254], [0, 0.5, 485.0 / 686.0, 1]),
        # one captured temperature: the walkthrough's radial fill has no cct_k
        channel("F1", "radial_4+5", None, "radial", {2700: {"counter-1": 100.0, "armchair-book": 80.0}},
                [0, 88.9, 254], [0, 0.25, 1]),
        # 685 so the applied scene gives armchair-book the decision's own 300: 0.4 x 685 + 20 + the spot's 6 lx spill
        channel("F2", "spot", None, "gimbal spot", {2700: {"counter-1": 0.0, "armchair-book": 685.0}},
                [0, 114.3, 254], [0, 0.4, 1]),
    ], unit="lx")


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

def hue_model(points, unit="relative", kelvins=(2202, 6500), excluded=()):
    """den_lighting's four bulbs, each a linear dimming curve shared with its pair, basis from `points`.

    `points` maps point_id -> {entity_id: value at full}; the cool end is 10%
    brighter so mired interpolation is exercised. A point in `excluded` is one
    the fit excluded ("mask").
    """
    partner = {a: b for a, b in HUE_PAIRS}
    partner.update({b: a for a, b in HUE_PAIRS})
    pts = [sample_point(p, "task", [float(i), 0.0, 0.8], "mask" if p in excluded else None)
           for i, p in enumerate(points)]
    channels = [channel(e.split(".", 1)[1], "white", e, "tunable_white",
                        {k: {p: float(v.get(e, 0.0)) * (1.0 + 0.1 * i) for p, v in points.items()}
                         for i, k in enumerate(kelvins)},
                        [0, 254], [0, 1], cct_range_k=[2202, 6500], shared_with=hue_channel(partner[e]))
                for e in HUE]
    return light_model_record("lm-hue-test", "den-test", pts, channels, unit,
                       shared_curve={"+".join(pair): True for pair in HUE_PAIRS})


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
