#!/usr/bin/env python3
"""Verifier probe for SEAM-15: non-UTF-8 detector rows crash den-observe's replay.py.

Builds, in a temp dir, a synthetic R take, den-observe's own synthetic RoomModel
and den-detections-1 rows (all with den-observe's synth/fake_detector/detections
modules), then runs replay.py --detections:
  a) rows as written (UTF-8, LF)                        -> control, expect exit 0
  b) rows re-encoded as PowerShell 5.1 '>' writes them  -> UTF-16LE + BOM + CRLF
  c) rows re-encoded as cp1252 with one non-ASCII byte in a model name (Set-Content default)
  d) rows with one line that is not JSON (UTF-8)        -> control for the handled path, expect exit 2
replay.py documents: 0 written, 1 scored and the gate failed, 2 refused.
A run that exits 1 with a Python traceback on an unscored run is the defect.
Runs replay.py as a subprocess with python3 -B (no __pycache__ in the checkout).

Run:
  TMPDIR=<scratch tmp> python3 -B probe_SEAM-15_spec.py <rung-2-observe root>
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

R2 = Path(sys.argv[1]).resolve()
OBS = R2 / "tools" / "den-observe"
work = Path(tempfile.mkdtemp(prefix="probe-seam-15-"))
print(f"RESULT work folder {work}")

SETUP = textwrap.dedent(r'''
import json, sys
from pathlib import Path
OBS = Path(sys.argv[1]); out = Path(sys.argv[2])
sys.path.insert(0, str(OBS))
import synth, fake_detector, detections
scene = synth.Scene()
depth = scene.depth_mm()
frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
folder = synth.make_take(out / "take", frames, depth)
synth.write_check(folder)
(out / "room-model.json").write_text(json.dumps(synth.room_model()), encoding="utf-8")
pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(frames)]
detections.write_rows(out / "rows-utf8.jsonl", rows)
print(json.dumps({"take": str(folder)}))
''')


def run(args):
    return subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True)


setup = run(["-c", SETUP, str(OBS), str(work)])
if setup.returncode != 0:
    print("RESULT setup failed:\n" + setup.stdout + setup.stderr)
    sys.exit(3)
take = json.loads(setup.stdout.strip().splitlines()[-1])["take"]
utf8 = (work / "rows-utf8.jsonl").read_text(encoding="utf-8")
(work / "rows-utf16.jsonl").write_bytes(b"\xff\xfe" + utf8.replace("\n", "\r\n").encode("utf-16-le"))
cp = utf8.replace("fake-scripted-1", "fake-scripted-é1", 1)
(work / "rows-cp1252.jsonl").write_bytes(cp.replace("\n", "\r\n").encode("cp1252"))
bad = utf8.split("\n")
bad[1] = "this is not json"
(work / "rows-badline.jsonl").write_text("\n".join(bad), encoding="utf-8")

cases = [("a control UTF-8 rows", "rows-utf8.jsonl", {0}),
         ("b UTF-16LE+BOM+CRLF rows (PowerShell 5.1 '>')", "rows-utf16.jsonl", {2}),
         ("c cp1252 rows with one non-ASCII byte", "rows-cp1252.jsonl", {2}),
         ("d control: a non-JSON line (handled path)", "rows-badline.jsonl", {2})]
for n, (label, name, want) in enumerate(cases):
    p = run([str(OBS / "replay.py"), "--take", take, "--room-model", str(work / "room-model.json"),
             "--detections", str(work / name), "--out", str(work / f"out-{n}")])
    tb = "Traceback" in p.stderr
    last = (p.stderr.strip().splitlines() or p.stdout.strip().splitlines() or [""])[-1]
    print(f"{'PASS' if p.returncode in want and not tb else 'FAIL'} {label}: exit {p.returncode}, "
          f"traceback {tb}: {last[:160]}")
