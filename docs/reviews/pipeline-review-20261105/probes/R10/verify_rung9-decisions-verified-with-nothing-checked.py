#!/usr/bin/env python3
"""verify_rung9-decisions-verified-with-nothing-checked: den-verify on rung 9's Decisions at den-core-live 64bd23ca.

What it checks (synthetic only, temp dirs, no network). den-core-live was
repinned from 78a6b6a2 to 64bd23ca. At 64bd23ca:
  - incumbent.py (den_lighting@1.0.4) still writes no applied_scene, no
    inputs.light_state / light_model, no predicted, targets or recipients;
    actuator_state is {on, brightness, kelvin}; transitions {entity: 2}.
  - decide.py (decide@v0) now writes applied_scene (status not_sent, readback
    {on, brightness, kelvin}), predicted.surfaces, recipients, and
    inputs.light_model as a dict (model.describe()), not a version string.
The decision records are taken from a den-core-live log.jsonl when one is
given (python3 -B tools/den-core/run.py --synthetic evening --touch
--policies incumbent decide --out <tmp>, run in a copy), else from inline
records with exactly those shapes. Synthetic LightStates follow each decision
at +0.5, +1, +3 and +6 s. den-verify's command line runs with every slot closed.

Run:
    python3 verify_rung9-decisions-verified-with-nothing-checked.py <rung-10-verify root> [den-core log.jsonl]
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
T = 1793080200.0


def inline():
    s = {"on": True, "brightness": 180, "kelvin": 2700}
    inc = {"decision_id": "incumbent-1", "ts": T, "trigger": "mode change", "policy": "den_lighting@1.0.4",
           "actuator_domain": "hue-den-4", "actuation": "replay",
           "inputs": {"person_state": [], "field_support": {}, "hold_set": {"revision": 1, "held": []},
                      "revisions": {"hold_set": 1, "mode": 1},
                      "actuator_state": {e: dict(s, brightness=80) for e in LIGHTS}, "config_sha256": "0" * 64},
           "requested_scene": {e: s for e in LIGHTS}, "transitions": {e: 2 for e in LIGHTS},
           "candidates": [], "skipped": [], "hard_conflicts": [], "status": "requested", "why": "den_lighting command"}
    dec = {"decision_id": "decide-2", "ts": T + 60, "trigger": "mode change", "policy": "decide@v0",
           "actuator_domain": "hue-den-4", "actuation": "replay",
           "inputs": {"person_state": [], "hold_set": {"revision": 1, "held": []},
                      "actuator_state": {e: s for e in LIGHTS},
                      "light_model": {"basis_unit": "relative", "fake": True, "glare": "none", "sha256": "0" * 64,
                                      "version": "fake-plan-v0"}},
           "requested_scene": {e: s for e in LIGHTS},
           "applied_scene": {e: {"readback": s, "status": "not_sent", "steps": []} for e in LIGHTS},
           "transitions": {}, "recipients": {e: {"served": [], "exposed": [], "layers": [], "targets": []}
                                             for e in LIGHTS},
           "predicted": {"surfaces": {"unit": "relative"}, "eyes": []}, "status": "requested"}
    return [inc, dec]


if len(sys.argv) > 2:
    events = [json.loads(line) for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = [e["data"] for e in events if e.get("kind") == "decision"]
    src = f"den-core-live log ({len(decisions)} decisions)"
else:
    decisions = inline()
    src = "inline records in 64bd23ca's shapes"
frames = []
for ts in sorted({d["ts"] + dt for d in decisions for dt in (0.5, 1.0, 3.0, 6.0)}):
    for dt in (0.0,):
        frames.append({"record": "light_state", "contract": "live-v1", "ts": ts, "camera_id": "den-oak",
                       "anchored": False, "exposure": {"normalisation_version": "meter-norm/v1"},
                       "surface_estimates": [{"surface_id": "desk-1", "value": 100.0 + (ts % 7), "unit": "relative",
                                              "clipped_frac": 0.0, "basis": "measured"}],
                       "window_daylight": [], "residual": []})
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
                         "--utc-offset-h", "0", "--out", str(tmp / "out")])
    out = tmp / "out"
    print(f"RESULT source: {src}")
    print(f"RESULT owner sees (exit {code}): {buf.getvalue().strip().splitlines()[0]}")
    decs = [json.loads(x) for x in (out / "decisions.verified.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    by_policy = {}
    for d in decs:
        v = d.get("verify")
        key = d.get("policy")
        state = ("verified, points=%d, offset %.1f s" % (len(v["points"]), v["ts"] - d["ts"])) if v else \
            ("not verified: %s" % (d.get("verify_status") or "")[:90])
        by_policy.setdefault(key, []).append(state)
    for k, v in by_policy.items():
        print(f"RESULT {k}: {len(v)} decision(s); first: {v[0]}")
    print(f"RESULT report decisions.verified={rep['decisions']['verified']} of {rep['decisions']['total']}, "
          f"hit_rate verdict={rep['hit_rate']['verdict']}, frames={rep['frames']}")
    empty = [d for d in decs if d.get("verify") and not d["verify"]["points"]]
    if empty:
        print(f"FAIL {len(empty)} decision(s) ({sorted({d['policy'] for d in empty})}) are written with a verify "
              "block and counted in decisions.verified with no point checked")
    lm = [d for d in decs if not d.get("verify") and "light model {" in (d.get("verify_status") or "")]
    if lm:
        print(f"NOTE {len(lm)} decide@v0 decision(s) are refused for a different reason: inputs.light_model is a "
              "dict at 64bd23ca and den-verify compares it with a version string")
