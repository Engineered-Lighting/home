#!/usr/bin/env python3
"""verify_crash-exit-code-1: in a SCORED run (every METHOD slot closed), does an undecodable detector-rows
file exit with the same code as a real gate failure?

Builds a synthetic take with a PASS check, a room model and a closed copy of METHOD.md (each open
slot set to its proposed value, as the suite's closed_method does). Runs replay.py --scored as a
subprocess three ways: (1) good rows, (2) rows whose results are 1.2 s late (a real gate FAIL),
(3) the same good rows saved as UTF-16LE with a BOM (PowerShell 5.1 '>' output). Prints exit code,
the last stderr line and whether run.json was written.

    python3 -B verify_crash-exit-code-1.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only, no network; the checkout is not modified.
"""
import json
import re
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

work = Path(tempfile.mkdtemp(prefix="verify-exit-"))
try:
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(3)]
    take = synth.make_take(work / "take", pairs, synth.Scene().depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    text = (here / "METHOD.md").read_text(encoding="utf-8")
    closed = work / "METHOD.md"
    closed.write_text(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2", text, flags=re.M),
                      encoding="utf-8")
    good, late = work / "good.jsonl", work / "late.jsonl"
    detections.write_rows(good, [fake_detector.row(t, rgb_seq=1000 + i) for i, (t, _) in enumerate(pairs)])
    detections.write_rows(late, [fake_detector.row(t, rgb_seq=1000 + i, latency_s=1.2) for i, (t, _) in enumerate(pairs)])
    utf16 = work / "utf16.jsonl"
    utf16.write_bytes(b"\xff\xfe" + good.read_text(encoding="utf-8").encode("utf-16-le"))
    for name, rows in (("good rows", good), ("late rows (real gate FAIL)", late), ("UTF-16 rows", utf16)):
        out = work / ("out-" + rows.stem)
        proc = subprocess.run([sys.executable, "-B", str(here / "replay.py"), "--take", str(take), "--room-model",
                               str(room), "--detections", str(rows), "--out", str(out), "--scored",
                               "--method", str(closed)], capture_output=True, text=True, timeout=300)
        last = (proc.stderr.strip().splitlines() or proc.stdout.strip().splitlines() or [""])[-1]
        print(f"RESULT scored, {name}: exit {proc.returncode}; run.json written: {(out / 'run.json').exists()}; "
              f"last line: {last}")
finally:
    shutil.rmtree(work, ignore_errors=True)
