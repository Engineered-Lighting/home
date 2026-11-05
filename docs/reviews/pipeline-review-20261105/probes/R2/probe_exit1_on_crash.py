#!/usr/bin/env python3
"""probe_exit1_on_crash: replay.py's documented exit codes vs what an unreadable take does.

What it checks
    README/replay.py: "Exit codes: 0 written, 1 scored and the gate failed,
    2 refused (nothing written)." The probe builds a synthetic take, then
      case A: deletes one bundle file that frames.csv lists;
      case B: leaves a stale observations.jsonl.partial in --out (an earlier
              run killed by a power cut);
    and runs `python replay.py --take ... --room-model ... --detections ... --out ...`
    (unscored) as the owner would, printing the exit code and the last stderr line.
    An unscored run can never mean "the gate failed", so exit 1 here is the
    interpreter's crash code being read as a gate verdict.

How to run
    python3 probe_exit1_on_crash.py <branch checkout root>

Synthetic data only, in a temporary folder that is removed afterwards.
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

work = Path(tempfile.mkdtemp(prefix="probe-exit1-"))
fails = 0
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
                              capture_output=True, text=True, timeout=300)
        last = (proc.stderr.strip().splitlines() or [""])[-1]
        return proc.returncode, last

    folder, take, room = make("missing-bundle")
    victim = sorted((take / "frames").glob("*.npz"))[1]
    victim.unlink()
    code, last = run(folder, take, room)
    bad = code == 1
    fails += bad
    print(f"{'FAIL' if bad else 'PASS'} A missing bundle {victim.name}: exit {code} ({last})")

    folder, take, room = make("stale-partial")
    (folder / "out").mkdir()
    (folder / "out" / "observations.jsonl.partial").write_text("", encoding="utf-8")
    code, last = run(folder, take, room)
    bad = code == 1
    fails += bad
    print(f"{'FAIL' if bad else 'PASS'} B stale observations.jsonl.partial in --out: exit {code} ({last})")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} unscored runs exited 1 ('scored and the gate failed')")
