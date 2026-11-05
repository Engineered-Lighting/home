#!/usr/bin/env python3
"""verify_gate-logic-mutants-survive: do the 6 reported surviving mutants survive the WHOLE suite,
and does each one really change den-observe's output (i.e. it is not an equivalent mutant)?

The original runner (mutate_den_observe.py) ran some mutants only against one test class.
This one runs every mutant against the full discovered suite of tools/den-observe (50 tests at
ab491a88), without failfast, plus an unmutated control. Then, in the same copy, it runs a small
behaviour script that reuses the suite's own synthetic fixtures (test_observe.Case) and prints
what the mutant changes. A mutant that both passes the full suite and changes the behaviour
line relative to the control is a real, uncaught regression.

    python3 -B verify_gate-logic-mutants-survive.py <rung-2-observe checkout root> [mut-dir]

Copies go to mut-dir (default: <scratchpad>/mut/R2-reproduce); the checkout is never touched.
Synthetic data only; temp dirs only; no network.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
MUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[2] / "mut" / "R2-reproduce"

MUTANTS = [
    ("control", None, None, None),
    ("one-sided-gap", "observe.py", "if abs(gap) > settings.max_gap_s:", "if gap > settings.max_gap_s:"),
    ("no-stereo-check", "observe.py",
     'if fresh and fresh.get("depth") != "used" and any(e.get("lift") == "stereo" for e in joints or []):',
     "if False:"),
    ("common-n2", "observe.py", "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / n)",
     "common = _scale(g.cov_add(*(p[2] for p in used)), 1.0 / (n * n))"),
    ("out-of-frame", "observe.py",
     "if score < settings.score_min or not (0 <= u <= width and 0 <= v <= height):",
     "if score < settings.score_min:"),
    ("no-below-floor", "observe.py", "if reason is None and xyz[2] + 3 * math.sqrt(cov[2][2]) < 0:", "if False:"),
    ("ts-mismatch", "replay.py", 'if take.rows[index]["t_host"] != frame["capture_ts"]:', "if False:"),
]

BEHAVIOUR = r'''
import json, sys, copy
sys.path.insert(0, ".")
import test_observe as T
import observe, fake_detector, detections, synth, replay
T.setUpModule()
out = {}
try:
    _, r = T.Case("b-gap", [(5000.0, 5000.1)]).run()          # depth 100 ms AFTER its RGB
    out["gap+100ms"] = (r[0]["freshness"]["depth"], sum(e["lift"] == "stereo" for e in r[0]["joints_room"]))
    _, r = T.Case("b-chk").run()
    bad = copy.deepcopy(r[0]); bad["freshness"]["depth"] = "too_old"   # stereo joints, depth flagged unused
    out["check_record(stereo,too_old)"] = observe.check_record(bad, lambda t: True)
    _, r = T.Case("b-plane", T.frames(1, stale=(0,))).run()  # plane fallback torso
    out["plane_torso_cov_trace"] = round(T.trace(r[0]["room_position"]["cov"]), 6)
    row = fake_detector.row(5000.0, rgb_seq=1000, pixels=T.PIXELS)
    row["detections"][0]["keypoints"][9] = [700.0, row["detections"][0]["keypoints"][9][1], 0.9]  # u > 640
    detections.validate(row)
    _, r = T.Case("b-oof", T.frames(1), rows=[row]).run()
    e = r[0]["joints_room"][9]
    out["joint9_u700"] = (e["status"], e["lift"])
    room = synth.room_model([synth.camera_entry(room_from_left=synth.camera_pose(position=(0.0, 0.0, -1.0)))])
    _, r = T.Case("b-floor", T.frames(1), room=room).run()   # whole scene 3.2 m under the floor
    out["below_floor"] = (r[0]["validity"], r[0]["room_position"]["z"])
    try:
        rows = [fake_detector.row(5000.010, rgb_seq=1000, pixels=T.PIXELS)]  # take t_host is 5000.000
        _, r = T.Case("b-ts", T.frames(1), rows=rows).run()
        out["row_ts_10ms_off"] = ("accepted", r[0]["capture_ts"] - r[0]["depth_ts"])
    except replay.Refused as exc:
        out["row_ts_10ms_off"] = ("refused", str(exc)[:60])
finally:
    T.tearDownModule()
print("BEHAVIOUR " + json.dumps(out, sort_keys=True))
'''


def run(name, rel, old, new):
    work = MUT / name
    if work.exists():
        shutil.rmtree(work)
    for part in ("tools/den-observe", "tools/den-take", "docs/architecture"):
        shutil.copytree(root / part, work / part, ignore=shutil.ignore_patterns("__pycache__"))
    if rel:
        path = work / "tools" / "den-observe" / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return name, "not-run", f"pattern found {text.count(old)} times", ""
        path.write_text(text.replace(old, new), encoding="utf-8")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    here = work / "tools" / "den-observe"
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "."], cwd=here,
                          capture_output=True, text=True, timeout=900, env=env)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "?")
    verdict = "caught" if proc.returncode else "survived"
    failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
    beh = subprocess.run([sys.executable, "-B", "-c", BEHAVIOUR], cwd=here, capture_output=True, text=True,
                         timeout=900, env=env)
    bline = next((l for l in beh.stdout.splitlines() if l.startswith("BEHAVIOUR")), "BEHAVIOUR-ERROR " +
                 (beh.stderr.strip().splitlines() or [""])[-1])
    return name, verdict, f"{ran}; {lines[-1] if lines else ''}" + (f"; first: {failed[0]}" if failed else ""), bline


MUT.mkdir(parents=True, exist_ok=True)
results = [run(*m) for m in MUTANTS]
control_beh = results[0][3]
for name, verdict, detail, bline in results:
    print(f"{name:16s} | full suite: {verdict:8s} | {detail}")
    print(f"{'':16s} | {bline}")
survivors = [r for r in results[1:] if r[1] == "survived"]
changed = [r for r in survivors if r[3] != control_beh]
print(f"RESULT control {results[0][1]}; mutants survived full suite {len(survivors)} of {len(results) - 1}; "
      f"of those, behaviour differs from control in {len(changed)}")
print("PASS" if not survivors else "FAIL: surviving non-equivalent mutants on gate logic")
