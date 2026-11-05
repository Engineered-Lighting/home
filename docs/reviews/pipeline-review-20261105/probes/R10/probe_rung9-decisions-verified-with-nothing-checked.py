#!/usr/bin/env python3
"""probe_rung9-decisions-verified-with-nothing-checked: den-verify on Decisions as rung 9 writes them.

What it checks (synthetic only, temp dirs, no network). claude/den-core-live
@78a6b6a2 writes Decision records (tools/den-core/decide.py Decide.handle and
incumbent.py Incumbent.handle) with:
  inputs.actuator_state {entity: {on, brightness, kelvin}}   (no level/cct_k)
  inputs.hold_set.held[{entity_id, pinned {on, brightness, kelvin}, since, lifetime}]
  requested_scene {entity: {on, brightness, kelvin}}, transitions {entity: s},
  status "requested"; no applied_scene, no inputs.light_state/light_model,
  no predicted, targets or recipients.
The probe writes two such records (decide@v0 and den_lighting@1.0.4) and four
den-photometry-shaped LightStates (record/contract envelope, unit "relative"),
then runs den-verify's command line with every slot closed. A tool that cannot
check anything should say so; the probe reports what it says instead.

Run:
    python3 probe_rung9-decisions-verified-with-nothing-checked.py <root of the rung-10-verify checkout>
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-verify"))
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402
from records import write_jsonl  # noqa: E402

LIGHTS = syn.HUE
t = syn.T0


def scene(on, b=None, k=None):
    return {"on": on, "brightness": b, "kelvin": k}


def rung9(did, ts, policy, requested, transitions, holds=()):
    return {"decision_id": did, "ts": ts, "trigger": "person_state change", "policy": policy,
            "actuator_domain": "hue-den-4",
            "inputs": {"person_state": ["A@%s" % ts], "field_support": {},
                       "hold_set": {"revision": 2, "held": list(holds)}, "revisions": {"hold_set": 2, "mode": 2},
                       "actuator_state": {e: scene(True, 80, 2700) for e in LIGHTS}, "config_sha256": "0" * 64},
            "requested_scene": requested, "transitions": transitions, "candidates": [], "skipped": [],
            "hard_conflicts": [], "status": "requested", "why": "synthetic"}


def photometry_ls(ts, value):
    return {"record": "light_state", "contract": "live-v1", "ts": ts, "camera_id": "den-oak", "room_version": "x",
            "exposure": {"time_ms": 16.6, "gain": 4.0, "black_level": None, "wb_locked": True, "linear": True,
                         "normalisation_version": "meter-norm/v1"},
            "anchored": False,
            "surface_estimates": [{"surface_id": "desk-1", "value": value, "unit": "relative", "clipped_frac": 0.0,
                                   "rho_status": "measured", "basis": "measured"}],
            "window_daylight": [], "sun": None, "blinds": "unknown", "residual": []}


decisions = [
    rung9("decide-1", t + 1.0, "decide@v0", {e: scene(True, 200, 3000) for e in LIGHTS}, {}),
    rung9("incumbent-2", t + 30.0, "den_lighting@1.0.4", {LIGHTS[0]: scene(False)}, {LIGHTS[0]: 2},
          holds=[{"entity_id": LIGHTS[1], "pinned": scene(True, 40, 2700), "since": t + 20, "lifetime": "room_vacant_2_minutes"}]),
]
frames = [photometry_ls(t, 100.0), photometry_ls(t + 1.5, 140.0), photometry_ls(t + 6, 250.0), photometry_ls(t + 31, 60.0)]
model = syn.hue_model({"desk-1": {e: 100.0 for e in LIGHTS}})
text = method.closed_copy(method.lf_text(root / "tools" / "den-verify" / "VERIFY-V1.md"))
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    write_jsonl(tmp / "d.jsonl", decisions)
    write_jsonl(tmp / "l.jsonl", frames)
    (tmp / "m.json").write_text(json.dumps(model), encoding="utf-8")
    (tmp / "VERIFY-V1.md").write_text(text, encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--method", str(tmp / "VERIFY-V1.md"), "run", "--decisions", str(tmp / "d.jsonl"),
                         "--light-states", str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"),
                         "--utc-offset-h", "-7", "--out", str(tmp / "out")])
    out = tmp / "out"
    print(f"RESULT owner sees (exit {code}): {buf.getvalue().strip().splitlines()[0]}")
    decs = [json.loads(l) for l in (out / "decisions.verified.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    states = [json.loads(l) for l in (out / "light_states.verified.jsonl").read_text(encoding="utf-8").splitlines()]
    for d in decs:
        v = d.get("verify")
        print(f"RESULT {d['decision_id']}: verify.light_state={v and v['light_state']} (ts offset "
              f"{v and round(v['ts'] - t, 2)} s), points={v and v['points']}, within_tolerance={v and v['within_tolerance']}")
    print(f"RESULT report frames: {rep['frames']}")
    print(f"RESULT LightState residuals: {[(round(s['ts'] - t, 1), s['residual'], s.get('residual_status')) for s in states]}")
    zero_pts = [d for d in decs if d.get("verify") and not d["verify"]["points"]]
    if zero_pts and rep["decisions"]["verified"] == len(decs):
        print(f"FAIL {len(zero_pts)} of {len(decs)} decisions are written and counted as 'verified' with no point checked")
    inc = next(d for d in decs if d["decision_id"] == "incumbent-2")
    if inc.get("verify") and inc["verify"]["ts"] - (t + 30.0) < 2.0 + 1.0:
        print("FAIL the den_lighting decision's verify frame is taken inside its logged 2 s transition "
              "(transitions{} is not read; no steps -> settled at ts)")
