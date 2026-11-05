#!/usr/bin/env python3
"""verify_crash-exits-1: replay.py exit codes when a take is unreadable or --out holds a stale .partial.

What it checks (independent re-run of finding crash-exits-1)
    replay.py documents "Exit codes: 0 written, 1 scored and the gate failed,
    2 refused (nothing written)". On a synthetic take it runs the owner's
    unscored command line (`replay.py --take --room-model --detections --out`):
      A  one bundle listed in frames.csv deleted from disk
      B  a stale observations.jsonl.partial left in --out (a killed run);
         then B is run a second time, to see whether the failing run removed
         the stale file (self-heal on the next attempt)
      C  a bundle whose bytes are not a zip at all (control: BundleError path)
    and prints exit code, whether stderr is a traceback, and what --out holds.

How to run
    python3 -B verify_crash-exits-1.py <branch checkout root>

Synthetic data only, in a temporary folder removed afterwards. Nothing connects anywhere.
"""
import json
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

work = Path(tempfile.mkdtemp(prefix="verify-crash-exits-1-"))
scene = synth.Scene()
DEPTH = scene.depth_mm()
PIXELS = {n: scene.joint_rgb(n) for n in synth.JOINTS}


def make(name):
    folder = work / name
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(3)]
    take = synth.make_take(folder / "take", pairs, DEPTH)
    synth.write_check(take)
    room = folder / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    detections.write_rows(folder / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=PIXELS)
                                                  for i, (t, _) in enumerate(pairs)])
    return folder, take, room


def run(folder, take, room):
    proc = subprocess.run([sys.executable, "-B", str(tool / "replay.py"), "--take", str(take), "--room-model",
                           str(room), "--detections", str(folder / "rows.jsonl"), "--out", str(folder / "out")],
                          capture_output=True, text=True, timeout=300)
    err = proc.stderr.strip().splitlines()
    held = sorted(p.name for p in (folder / "out").iterdir()) if (folder / "out").exists() else []
    return proc.returncode, "Traceback" in proc.stderr, (err[-1] if err else ""), held


try:
    folder, take, room = make("A-missing-bundle")
    sorted((take / "frames").glob("*.npz"))[1].unlink()
    code, tb, last, held = run(folder, take, room)
    print(f"RESULT A missing bundle: exit {code}, traceback={tb}, out={held}, last={last!r}")

    folder, take, room = make("B-stale-partial")
    (folder / "out").mkdir()
    (folder / "out" / "observations.jsonl.partial").write_text('{"half": ', encoding="utf-8")
    code, tb, last, held = run(folder, take, room)
    print(f"RESULT B stale partial, 1st run: exit {code}, traceback={tb}, out={held}, last={last!r}")
    code, tb, last, held = run(folder, take, room)
    print(f"RESULT B stale partial, 2nd run: exit {code}, traceback={tb}, out={held}")

    folder, take, room = make("C-not-a-zip")
    sorted((take / "frames").glob("*.npz"))[1].write_bytes(b"not a zip")
    code, tb, last, held = run(folder, take, room)
    print(f"RESULT C corrupt bundle (control): exit {code}, traceback={tb}, out={held}, last={last!r}")
finally:
    shutil.rmtree(work, ignore_errors=True)
