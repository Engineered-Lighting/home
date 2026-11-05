#!/usr/bin/env python3
"""probe_crash-exit-code-1: do unreadable inputs refuse with exit 2, or crash with exit 1 ("gate failed")?

replay.py documents exit codes 0 written, 1 scored and the gate failed, 2 refused.
This probe runs replay.py as a subprocess on a synthetic take with:
  (a) detector rows saved as UTF-16LE with a BOM, which is what Windows
      PowerShell 5.1's `>` redirection writes;
  (b) rows with a NaN/Infinity-free but non-UTF-8 byte (a cp1252 'e acute');
and reports the exit code and the last stderr line.
PASS = exit 2 with a "refused:" line; FAIL = a traceback with exit 1.

    python3 probe_crash-exit-code-1.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; no network.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
here = root / "tools" / "den-observe"
sys.path.insert(0, str(here))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-exit-"))
bad = 0
try:
    scene = synth.Scene()
    pairs = [(5000.0, 5000.0 - 0.004)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    good = work / "rows.jsonl"
    detections.write_rows(good, [fake_detector.row(5000.0, rgb_seq=1000)])
    text = good.read_text(encoding="utf-8")
    cases = {"utf16-bom (PowerShell 5.1 '>')": b"\xff\xfe" + text.encode("utf-16-le"),
             "cp1252 byte in a class name": text.replace('"person"', '"café"').encode("cp1252")}
    for name, data in cases.items():
        rows = work / f"rows-{len(name)}.jsonl"
        rows.write_bytes(data)
        out = work / f"out-{len(name)}"
        proc = subprocess.run([sys.executable, "-B", str(here / "replay.py"), "--take", str(take), "--room-model",
                               str(room), "--detections", str(rows), "--out", str(out)],
                              capture_output=True, text=True, timeout=120)
        last = (proc.stderr.strip().splitlines() or [""])[-1]
        print(f"RESULT {name}: exit {proc.returncode}; stderr last line: {last}")
        if proc.returncode != 2:
            bad += 1
finally:
    shutil.rmtree(work, ignore_errors=True)
if bad:
    print("FAIL: an unreadable rows file crashes replay.py with exit 1, the code README gives to 'scored and the gate failed'")
    sys.exit(1)
print("PASS")
