"""Probe: den-verify against the Decision records den-core-live's incumbent adapter really writes.

What it checks
  Ladder rung 10 is tested on "scripted scene changes on the Hue bulbs and the
  displayed incumbent scenes". The incumbent's scenes are logged by
  den-core-live tools/den-core/incumbent.py as `den_lighting@1.0.4` Decisions
  in the hue-den-4 dialect: requested_scene {entity: {on, brightness, kelvin}},
  `transitions` {entity: s}, inputs.actuator_state {entity: scene | null},
  holds pinned as {on, brightness, kelvin}, and no applied_scene, no step log,
  no inputs.light_state. den-verify reads applied_scene[].readback.level,
  applied_scene[].steps[].sent_at, actuator_state[].level and level_pct.

  This probe drives den_lighting's real policy.py through incumbent.py offline
  (no Home Assistant), takes the Decisions it emits, and runs den-verify's CLI
  (closed copy of VERIFY-V1.md, den-verify's own fake model keyed by the four
  entity ids) over them with synthetic metering frames:
    A. a vacancy-off Decision issued while one bulb was never observed
       (actuator_state null): den-verify crashes with an uncaught traceback;
    B. Decisions with every bulb observed: every verify block has no points,
       the verify frame is taken 1 s after the command although den_lighting
       fades over 2 s (no step log -> no mid-fade window), every bulb reads as
       off in the before state (`brightness`, not `level`), and the verdict is
       "insufficient".

Run
  python3 probe_incumbent-decisions-unscoreable.py <den-verify checkout root> <den-core-live checkout root>
Synthetic data only; nothing contacts Home Assistant or any host; temp dirs only;
writes no __pycache__ into either checkout.
"""
import contextlib
import io
import json
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
CORE = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-verify"))
sys.path.insert(1, str(CORE / "tools" / "den-core"))

import incumbent  # noqa: E402  (den-core-live)
import method  # noqa: E402
import synthetic as syn  # noqa: E402
import verify as cli  # noqa: E402

HUE = syn.HUE


def drive(observe):
    pol = incumbent.make_policy()
    t = syn.T0
    evs = [{"t": t, "kind": "occupancy", "data": {"value": "on"}}]
    for i, e in enumerate(observe):
        evs.append({"t": t + 0.1 * (i + 1), "kind": "light_observed",
                    "data": {"entity_id": e, "on": True, "brightness": 200, "kelvin": 2700}})
    evs.append({"t": t + 10, "kind": "occupancy", "data": {"value": "off"}})
    evs.append({"t": t + 131, "kind": "tick", "data": {}})
    evs.append({"t": t + 300, "kind": "occupancy", "data": {"value": "on"}})
    out = []
    for ev in evs:
        out += [o["data"] for o in pol.handle(ev) if o["kind"] == "decision"]
    return out


def run_cli(decisions, frames, tmp):
    tmp = Path(tmp)
    model = syn.hue_model({"desk-1": {e: 100.0 for e in HUE}})
    model["version"] = "lm-hue-test"
    (tmp / "d.jsonl").write_text("".join(json.dumps(d) + "\n" for d in decisions), encoding="utf-8")
    (tmp / "l.jsonl").write_text("".join(json.dumps(f) + "\n" for f in frames), encoding="utf-8")
    (tmp / "m.json").write_text(json.dumps(model), encoding="utf-8")
    (tmp / "V.md").write_text(method.closed_copy(method.lf_text(ROOT / "tools/den-verify/VERIFY-V1.md")),
                              encoding="utf-8")
    argv = ["--method", str(tmp / "V.md"), "run", "--decisions", str(tmp / "d.jsonl"), "--light-states",
            str(tmp / "l.jsonl"), "--model", str(tmp / "m.json"), "--utc-offset-h", "-7", "--out", str(tmp / "out")]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue(), tmp / "out"


def frames_for(decisions):
    fr = []
    for d in decisions:
        fr.append(syn.light_state(d["ts"] - 1.0, {"desk-1": 300.0}, anchored=False))
        fr.append(syn.light_state(d["ts"] + 1.0, {"desk-1": 150.0}, anchored=False))  # mid den_lighting's 2 s fade
        fr.append(syn.light_state(d["ts"] + 6.0, {"desk-1": 20.0}, anchored=False))
    return fr


# A: one bulb never observed
dA = drive(HUE[:3])
print(f"A: incumbent emitted {len(dA)} decision(s); policy={dA[0]['policy']}; keys={sorted(dA[0])}")
print(f"A: actuator_state={json.dumps(dA[0]['inputs']['actuator_state'])}; transitions={dA[0]['transitions']}")
crashed = False
with tempfile.TemporaryDirectory() as tmp:
    try:
        run_cli(dA, frames_for(dA), tmp)
        print("RESULT A: den-verify finished")
    except Exception as e:  # noqa: BLE001
        crashed = True
        tb = traceback.extract_tb(e.__traceback__)[-1]
        print(f"RESULT A: den-verify crashed: {type(e).__name__}: {e} at {Path(tb.filename).name}:{tb.lineno}")

# B: every bulb observed
dB = drive(HUE)
with tempfile.TemporaryDirectory() as tmp:
    code, said, out = run_cli(dB, frames_for(dB), tmp)
    print("RESULT B: exit", code, "|", said.strip().replace("\n", " | "))
    ver = [json.loads(x) for x in (out / "decisions.verified.jsonl").read_text("utf-8").splitlines()]
    rep = json.loads((out / "report.json").read_text("utf-8"))
    mid = 0
    for d in ver:
        v = d["verify"]
        if v is None:
            print(f"RESULT B: {d['decision_id']} verify null: {d.get('verify_status')}")
            continue
        dt = v["ts"] - d["ts"]
        mid += dt < 2.0
        print(f"RESULT B: {d['decision_id']} transitions={d['transitions']} verify frame {v['light_state']} "
              f"{dt:+.1f} s after the command; points={v['points']}")
    print(f"RESULT B: frames excluded={rep['frames']['excluded']}; hit_rate verdict={rep['hit_rate']['verdict']}")

bad = crashed and rep["hit_rate"]["verdict"] == "insufficient" and mid > 0
print("FAIL: den-verify cannot score the incumbent's logged Decisions (crash on a null actuator entry; "
      "no points; mid-fade frame taken as the verify frame)" if bad else "PASS")
