#!/usr/bin/env python3
"""verify_intent_crash-exits-1: what an owner actually sees when replay.py meets a missing bundle or a stale .partial.

What it checks (unscored runs, as README.md's owner command, via subprocess)
    A  a bundle listed in frames.csv is missing: exit code, last stderr line, and
       whether anything is left in --out.
    B  a stale observations.jsonl.partial is left in --out (a run killed by a power
       cut): exit code and last stderr line of the first rerun, whether that rerun
       removed the stale .partial, and the exit code of a second rerun into the same
       --out (does the failure clear itself?).
    C  for contrast, a truncated (corrupt) bundle: exit code (BundleError path).

How to run
    python3 -B verify_intent_crash-exits-1.py <rung-2-observe checkout root>

Synthetic data only, in a temporary folder that is removed afterwards; nothing connects anywhere.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-observe"
sys.path.insert(0, str(tool))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="verify-crash-"))
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
try:
    scene = synth.Scene()
    depth = scene.depth_mm()
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}

    def make(name):
        folder = work / name
        pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(2)]
        take = synth.make_take(folder / "take", pairs, depth)
        synth.write_check(take)
        room = folder / "room.json"
        room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
        detections.write_rows(folder / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
                                                      for i, (t, _) in enumerate(pairs)])
        return folder, take, room

    def run(folder, take, room):
        proc = subprocess.run([sys.executable, "-B", str(tool / "replay.py"), "--take", str(take), "--room-model",
                               str(room), "--detections", str(folder / "rows.jsonl"), "--out", str(folder / "out")],
                              capture_output=True, text=True, timeout=300, env=env)
        last = (proc.stderr.strip().splitlines() or [""])[-1]
        tb = "Traceback" in proc.stderr
        return proc.returncode, last, tb, (proc.stdout.strip().splitlines() or [""])[-1]

    folder, take, room = make("A")
    victim = sorted((take / "frames").glob("*.npz"))[1]
    victim.unlink()
    code, last, tb, _ = run(folder, take, room)
    left = sorted(p.name for p in (folder / "out").iterdir()) if (folder / "out").exists() else []
    print(f"RESULT A missing bundle: exit {code}, traceback={tb}, last stderr: {last}; --out holds {left}")

    folder, take, room = make("B")
    (folder / "out").mkdir()
    (folder / "out" / "observations.jsonl.partial").write_text("stale\n", encoding="utf-8")
    code, last, tb, _ = run(folder, take, room)
    stale_left = (folder / "out" / "observations.jsonl.partial").exists()
    print(f"RESULT B first rerun: exit {code}, traceback={tb}, last stderr: {last}; stale .partial still there: {stale_left}")
    code2, last2, tb2, out2 = run(folder, take, room)
    print(f"RESULT B second rerun, same --out: exit {code2}, traceback={tb2}, last stdout: {out2}")

    folder, take, room = make("C")
    victim = sorted((take / "frames").glob("*.npz"))[1]
    victim.write_bytes(victim.read_bytes()[:100])
    code, last, tb, _ = run(folder, take, room)
    print(f"RESULT C truncated bundle: exit {code}, traceback={tb}, last stderr: {last}")
finally:
    shutil.rmtree(work, ignore_errors=True)
