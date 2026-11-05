#!/usr/bin/env python3
"""Verifier side-probe for SEAM-02's suggested fix ("take the newest revision").

den-register marks a moved camera by appending a RoomModel revision whose camera
entry carries `invalidated` (records.invalidated_revision; REGISTER-V1.md "A moved
camera"). This checks whether den-observe's replay.py refuses such an entry when it
is handed as a one-document RoomModel (what "read the newest line" would give it,
or what an owner gets by copying the last line into a .json as a workaround).
Uses den-observe's own synthetic take and camera entry (den-observe-camera-1 form),
adds `invalidated` to the entry, and runs replay.py --fake.
  expect exit 2 (refused); exit 0 means an invalidated registration is used silently.

Run:
  TMPDIR=<scratch tmp> python3 -B probe_SEAM-02-invalidated_spec.py <rung-2-observe root>
"""
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

R2 = Path(sys.argv[1]).resolve()
OBS = R2 / "tools" / "den-observe"
work = Path(tempfile.mkdtemp(prefix="probe-seam-02-inv-"))
SETUP = textwrap.dedent(r'''
import json, sys
from pathlib import Path
OBS = Path(sys.argv[1]); out = Path(sys.argv[2])
sys.path.insert(0, str(OBS))
import synth
scene = synth.Scene()
frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
folder = synth.make_take(out / "take", frames, scene.depth_mm())
synth.write_check(folder)
rm = synth.room_model()
rm["cameras"][0]["invalidated"] = {"by": "check-mount", "result": "changed", "recheck": ["mount"]}
(out / "room-model-invalidated.json").write_text(json.dumps(rm), encoding="utf-8")
print(json.dumps({"take": str(folder)}))
''')
s = subprocess.run([sys.executable, "-B", "-c", SETUP, str(OBS), str(work)], capture_output=True, text=True)
if s.returncode:
    print("RESULT setup failed:\n" + s.stdout + s.stderr)
    sys.exit(3)
take = json.loads(s.stdout.strip().splitlines()[-1])["take"]
p = subprocess.run([sys.executable, "-B", str(OBS / "replay.py"), "--take", take, "--room-model",
                    str(work / "room-model-invalidated.json"), "--fake", "--out", str(work / "out")],
                   capture_output=True, text=True)
line = (p.stdout.strip().splitlines() or p.stderr.strip().splitlines() or [""])[0]
print(f"{'PASS' if p.returncode == 2 else 'FAIL'} invalidated camera entry: exit {p.returncode}: {line[:160]}")
