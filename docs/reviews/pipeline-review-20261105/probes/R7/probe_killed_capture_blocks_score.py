#!/usr/bin/env python3
"""A capture killed mid-hold (window closed, PC asleep, power cut) leaves capture-<n>\\ with frames but no
capture.json, and every later --score then refuses until the folder is deleted by hand (README has no step).

The probe makes a photometry folder in a temp dir with the tool's own synthetic Stage 0 response, takes one
kept capture against den-photometry's FakeCamera on 127.0.0.1, types its row, then starts a second capture
in a child process and SIGKILLs it while it is holding (frames on disk). It then runs score_root.

Synthetic data and 127.0.0.1 only. POSIX only (uses SIGKILL).
Run:
    python3 probe_killed_capture_blocks_score.py <root of the branch checkout>
"""
import csv
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-photometry"
sys.path.insert(0, str(TOOL))
import card  # noqa: E402
import photometry  # noqa: E402
import sweep  # noqa: E402
import synthetic as syn  # noqa: E402

CHILD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import photometry, sweep, synthetic as syn
day, url, token = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
den = syn.den_method(frames_per_step=400, run_limit_s=120)
photometry.capture(day, sweep.Camera(url, syn.TOKEN), token, syn.photometry_method(), den, point="desk",
                   illuminant="bulbs", blinds="closed", light="four bulbs", say=lambda *a: None)
"""

with tempfile.TemporaryDirectory(prefix="r7kill-") as tmp:
    tmp = Path(tmp)
    root = tmp / "den-photometry"
    root.mkdir()
    token = tmp / "private" / "camera-token.txt"
    token.parent.mkdir()
    token.write_text(syn.TOKEN)
    stage0 = tmp / "den-light" / "stage0-20261108" / "sweep-1"
    stage0.mkdir(parents=True)
    den, m = syn.den_method(), syn.photometry_method()
    (stage0 / "response.json").write_text(json.dumps(syn.stage0_response(den)), encoding="utf-8")
    schemas = json.loads((ROOT / "docs/architecture/live-v1/schemas.json").read_text(encoding="utf-8"))
    room = tmp / "room_model.jsonl"
    room.write_text(json.dumps(next(r for r in schemas["records"] if r["id"] == "room_model")["example"]) + "\n")
    day = photometry.new_day(root, stage0, token, m, den, "20261110")
    fake = syn.FakeCamera({"background": 20.0, "rects": [((300, 150, 339, 189), 100.0)]})
    try:
        _, rec, _ = photometry.capture(day, sweep.Camera(fake.url, syn.TOKEN), token, m, den, point="counter-1",
                                       illuminant="bulbs", blinds="closed", light="four bulbs", say=lambda *a: None)
        print("capture 1:", rec["outcome"])
        table = list(csv.reader((day / "worksheet.csv").read_text(encoding="utf-8").splitlines()))
        table[1][6:9] = ["174.533", "174.533", "305 155 334 184"]
        out = io.StringIO()
        csv.writer(out, lineterminator="\r\n").writerows(table)
        (day / "worksheet.csv").write_bytes(out.getvalue().encode())
        _, report = photometry.score_root(root, room, token, m, den, say=lambda *a: None)
        print("score before the killed capture:", report["gate"])
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(TOOL), str(day), fake.url, str(token)])
        frames = day / "capture-2" / "frames"
        deadline = time.time() + 60
        while time.time() < deadline and not (frames.is_dir() and len(list(frames.iterdir())) >= 3):
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        left = sorted(p.name for p in (day / "capture-2").iterdir())
        print("capture-2 after SIGKILL holds:", left, "frames:", len(list(frames.iterdir())))
        try:
            photometry.score_root(root, room, token, m, den, say=lambda *a: None)
            blocked, why = False, "score ran"
        except sweep.Refused as exc:
            blocked, why = True, str(exc)
        print("score after the killed capture:", "REFUSED: " + why if blocked else why)
    finally:
        fake.close()
    ok = "capture.json" not in left and blocked
    print(("FAIL: one interrupted capture blocks every later score until its folder is deleted by hand"
           if ok else "PASS: the score skips or reports the orphan capture"))
    print("RESULT: " + ("DEFECT REPRODUCED" if ok else "not reproduced"))
    sys.exit(1 if ok else 0)
