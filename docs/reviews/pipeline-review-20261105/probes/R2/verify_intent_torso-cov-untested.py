#!/usr/bin/env python3
"""verify_intent_torso-cov-untested: does the den-observe suite pin the torso room_position covariance?

What it checks
    1. Applies each torso-covariance mutant below to a temp copy of tools/den-observe,
       tools/den-take and docs/architecture/live-v1 (never the checkout) and runs the
       WHOLE den-observe suite with failfast. 'survived' = all tests pass.
    2. On the unmutated code, builds the suite's own synthetic case (3 frames, all-stereo
       torso, 3 cm registration bound) and prints how much room_position.cov changes
       under each mutant, so the size of the miss is visible (the variance the tracker
       would weigh).

How to run
    python3 -B verify_intent_torso-cov-untested.py <rung-2-observe checkout root> [work dir]

Synthetic data only (the suite's own fakes); temp dirs only; nothing connects anywhere.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
work_base = Path(sys.argv[2]) if len(sys.argv) > 2 else None
MUTANTS = [
    ("T1-torso-no-cal", "return xyz, g.cov_add(independent, common, g.isotropic(camera.error_bound_m))",
     "return xyz, g.cov_add(independent, common)"),
    ("T2-torso-indep-over-n", "independent = _scale(g.cov_add(*(p[1] for p in used)), 1.0 / (n * n))",
     "independent = _scale(g.cov_add(*(p[1] for p in used)), 1.0 / n)"),
    ("T3-torso-cal-twice", "return xyz, g.cov_add(independent, common, g.isotropic(camera.error_bound_m))",
     "return xyz, g.cov_add(independent, common, g.isotropic(camera.error_bound_m), g.isotropic(camera.error_bound_m))"),
]


def copy_tree(dst):
    for rel in ("tools/den-observe", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(root / rel, dst / rel, ignore=shutil.ignore_patterns("__pycache__"))


def suite(tool):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "."], cwd=tool,
                          capture_output=True, text=True, timeout=900, env=env)
    tail = " ".join(l for l in proc.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED")))
    first = next((l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
    return proc.returncode == 0, tail, first


MEASURE = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import detections, fake_detector, replay, synth
work = Path(sys.argv[2])
scene = synth.Scene(); depth = scene.depth_mm()
pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(1)]
take = synth.make_take(work / "take", pairs, depth); synth.write_check(take)
room = work / "room.json"; room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
detections.write_rows(work / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
obs = json.loads((work / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])
print(json.dumps({"cov": obs["room_position"]["cov"], "lifts": [e["lift"] for e in obs["joints_room"]]}))
'''


def measure(tool, tmp):
    script = tmp / "measure.py"
    script.write_text(MEASURE, encoding="utf-8")
    w = Path(tempfile.mkdtemp(prefix="m-", dir=tmp))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-B", str(script), str(tool), str(w)], capture_output=True, text=True,
                          timeout=300, env=env)
    if proc.returncode:
        return None, proc.stderr.strip().splitlines()[-1]
    return json.loads(proc.stdout.strip().splitlines()[-1]), ""


tmp = Path(tempfile.mkdtemp(prefix="verify-torso-", dir=work_base))
try:
    base = tmp / "base"
    copy_tree(base)
    ok, tail, first = suite(base / "tools" / "den-observe")
    print(f"BASELINE whole suite: {'PASS' if ok else 'FAIL'} ({tail}) {first}", flush=True)
    ref, err = measure(base / "tools" / "den-observe", tmp)
    print(f"BASELINE torso cov diag: {[ref['cov'][i][i] for i in range(3)] if ref else err}", flush=True)
    survived = 0
    for mid, old, new in MUTANTS:
        w = tmp / mid
        copy_tree(w)
        path = w / "tools" / "den-observe" / "observe.py"
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"RESULT {mid}: NOT RUN (original text occurs {text.count(old)} times)")
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        got, err = measure(w / "tools" / "den-observe", tmp)
        diag = [got["cov"][i][i] for i in range(3)] if got else err
        ratio = [round(got["cov"][i][i] / ref["cov"][i][i], 3) for i in range(3)] if got and ref else None
        ok, tail, first = suite(w / "tools" / "den-observe")
        survived += ok
        print(f"RESULT {mid}: {'SURVIVED' if ok else 'CAUGHT'} ({tail}) {first}; torso cov diag {diag}, "
              f"ratio to unmutated {ratio}", flush=True)
    print(f"RESULT summary: {survived} of {len(MUTANTS)} torso-covariance mutants survive the whole suite")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
