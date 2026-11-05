#!/usr/bin/env python3
"""Verifier probe for SEAM-01 and SEAM-02 (seam: den-register -> den-observe).

What it checks, on synthetic data in temp dirs only (no network, no camera):
  1. den-register (rung 1) registers a synthetic scene with its own code path
     (test-style closed method = proposed values), giving room-model.jsonl
     with ONE revision; then a second fit (one click moved) gives TWO revisions.
  2. den-observe (rung 2) replay.py --fake is run, as the owner would, with
       a) den-observe's own synthetic RoomModel (.json)          -> control, expect exit 0
       b) den-register's 1-revision room-model.jsonl              -> SEAM-01
       c) den-register's entry with intrinsics patched into den-observe-camera-1
          form but den-register's pose.camera_frame string kept    -> SEAM-01 second refusal
       d) den-observe's own (compatible) RoomModel written as JSON Lines with
          TWO revisions (newer camera version on line 2)           -> SEAM-02 isolated from SEAM-01
       e) den-register's 2-revision room-model.jsonl              -> SEAM-02 as reported
       f) den-observe's own RoomModel as a ONE-line .jsonl        -> control, expect exit 0
Each den tool runs in its own subprocess (python3 -B, so no __pycache__ is
written into the checkouts).

Run:
  TMPDIR=<scratch tmp> python3 -B probe_SEAM-01-02_spec.py <rung-2-observe root> <rung-1-register root>
Prints RESULT lines and PASS/FAIL per check ("FAIL" = the seam defect reproduces).
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

R2 = Path(sys.argv[1]).resolve()
R1 = Path(sys.argv[2]).resolve()
OBS = R2 / "tools" / "den-observe"
REG = R1 / "tools" / "den-register"
work = Path(tempfile.mkdtemp(prefix="probe-seam-01-02-"))
print(f"RESULT work folder {work}")

REGISTER = textwrap.dedent(r'''
import io, json, re, sys
from pathlib import Path
REG = Path(sys.argv[1]); out = Path(sys.argv[2])
sys.path.insert(0, str(REG))
import method, register, synthetic, worksheet, records
text = (REG / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for the probe", text, flags=re.M)
m = method.parse(text)
depth, left, seen = synthetic.render(synthetic.POINTS)
data = synthetic.bundle(synthetic.metadata(), depth, left)
take = synthetic.write_take(out / "take1", [data] * 3)
w = out / "register"; w.mkdir()
(w / "worksheet.json").write_text(json.dumps(synthetic.worksheet(synthetic.POINTS, synthetic.FIT, synthetic.CHECKS, None)), encoding="utf-8")
def click(shift=None):
    sheet = worksheet.load(w / "worksheet.json", need_positions=False)
    s = register.ClickSession(w, take, register.frame_number(take, None, w), sheet, m)
    for pid, (u, v, _) in seen.items():
        du, dv = (shift or {}).get(pid, (0, 0))
        s.click({"id": pid, "u": round(u + du, 1), "v": round(v + dv, 1)})
    s.done()
click()
buf = io.StringIO()
code1, rep1 = register.run_fit(w, take, m=m, now=lambda: 1_800_000_500.0, out=buf)
one = (w / "room-model.jsonl").read_text(encoding="utf-8")
(out / "room-model-1rev.jsonl").write_text(one, encoding="utf-8", newline="\n")
click({"p1": (0.4, 0)})
code2, rep2 = register.run_fit(w, take, m=m, now=lambda: 1_800_000_900.0, out=buf)
two = (w / "room-model.jsonl").read_text(encoding="utf-8")
(out / "room-model-2rev.jsonl").write_text(two, encoding="utf-8", newline="\n")
print(json.dumps({"fit1": code1, "tid1": rep1.get("transform_id"), "fit2": code2, "tid2": rep2.get("transform_id"),
                  "lines_after_fit2": len([l for l in two.split("\n") if l.strip()])}))
''')

OBSERVE_SETUP = textwrap.dedent(r'''
import json, sys
from pathlib import Path
OBS = Path(sys.argv[1]); out = Path(sys.argv[2])
sys.path.insert(0, str(OBS))
import synth
scene = synth.Scene()
depth = scene.depth_mm()
frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
folder = synth.make_take(out / "take-observe", frames, depth)
synth.write_check(folder)
rm = synth.room_model()
(out / "room-model-observe.json").write_text(json.dumps(rm), encoding="utf-8")
(out / "room-model-observe-1line.jsonl").write_text(json.dumps(rm) + "\n", encoding="utf-8", newline="\n")
rm2 = json.loads(json.dumps(rm)); rm2["version"] = "den-synth-2"; rm2["cameras"][0]["version"] = "2026-11-13"
(out / "room-model-observe-2rev.jsonl").write_text(json.dumps(rm) + "\n" + json.dumps(rm2) + "\n", encoding="utf-8", newline="\n")
print(json.dumps({"take": str(folder)}))
''')


def run(args, **kw):
    return subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True, **kw)


reg = run(["-c", REGISTER, str(REG), str(work)])
if reg.returncode != 0:
    print("RESULT den-register setup failed:\n" + reg.stdout + reg.stderr)
    sys.exit(3)
info = json.loads(reg.stdout.strip().splitlines()[-1])
print(f"RESULT den-register: fit1 exit {info['fit1']} {info['tid1']}; fit2 exit {info['fit2']} {info['tid2']}; "
      f"room-model.jsonl lines after fit2: {info['lines_after_fit2']}")

obs = run(["-c", OBSERVE_SETUP, str(OBS), str(work)])
if obs.returncode != 0:
    print("RESULT den-observe setup failed:\n" + obs.stdout + obs.stderr)
    sys.exit(3)
take = json.loads(obs.stdout.strip().splitlines()[-1])["take"]

# c) den-register entry with intrinsics re-formed into den-observe-camera-1, pose left as den-register wrote it.
reg1 = json.loads((work / "room-model-1rev.jsonl").read_text(encoding="utf-8").splitlines()[0])
obs_rm = json.loads((work / "room-model-observe.json").read_text(encoding="utf-8"))
patched = json.loads(json.dumps(reg1))
cam = patched["cameras"][0]
cam["intrinsics"] = dict(obs_rm["cameras"][0]["intrinsics"])
(work / "room-model-register-intrinsics-patched.json").write_text(json.dumps(patched), encoding="utf-8")
print(f"RESULT den-register camera entry keys: intrinsics {sorted(reg1['cameras'][0]['intrinsics'])}; "
      f"pose.camera_frame {reg1['cameras'][0]['pose'].get('camera_frame')!r}")

replay = str(OBS / "replay.py")
cases = [
    ("a control: den-observe synth RoomModel (.json)", "room-model-observe.json", 0),
    ("b SEAM-01: den-register room-model.jsonl, 1 revision", "room-model-1rev.jsonl", 0),
    ("c SEAM-01: den-register entry, intrinsics patched, pose as written", "room-model-register-intrinsics-patched.json", 0),
    ("d SEAM-02: den-observe-form RoomModel as 2-revision JSON Lines", "room-model-observe-2rev.jsonl", 0),
    ("e SEAM-02: den-register room-model.jsonl, 2 revisions", "room-model-2rev.jsonl", 0),
    ("f control: den-observe synth RoomModel as 1-line .jsonl", "room-model-observe-1line.jsonl", 0),
]
for n, (label, name, want) in enumerate(cases):
    out = work / f"out-{n}"
    p = run([replay, "--take", take, "--room-model", str(work / name), "--fake", "--out", str(out)])
    msg = (p.stderr.strip().splitlines() or [""])[-1] if p.returncode else (p.stdout.strip().splitlines() or [""])[0]
    verdict = "PASS" if p.returncode == want else "FAIL"
    print(f"{verdict} {label}: exit {p.returncode}: {msg}")
