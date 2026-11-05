#!/usr/bin/env python3
"""verify3_held-out-not-opened-by-n2-dev: with N1's labels frozen as built (both splits in one labels-vN),
does N2's development loader ever open a held-out session's label file?

(INTENT verifier, finding held-out-labels-not-sealed.)

Builds four synthetic sessions with the den-label branch's own test helpers (a1, b1 development; a2, b2
held_out), freezes fixture-v1 and labels-v1 with the unmodified tool, then REMOVES the held-out label files
(labels-v1/sessions/a2.json, b2.json) and calls den-n2's score.load_n1(root, "development", ...). If the
development load still succeeds, N2's development path never opened held-out labels. Then the held_out load
is called to show it does open (and verify) them. Temp dirs only, loopback only.

Run:
    python3 -B verify3_held-out-not-opened-by-n2-dev.py <den-label checkout> <den-n2 checkout> <work dir>
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT, N2ROOT, WORK = (Path(a).resolve() for a in sys.argv[1:4])
WORK.mkdir(parents=True, exist_ok=True)
INNER = r'''
import json, os, sys
from unittest import mock
sys.path.insert(0, os.getcwd())
import test_den_label as T
n1 = T.n1
T.setUpModule()
t = T.FreezeTests("test_canonical_bytes_give_fixed_hashes")
t.setUp()
try:
    ws = t.build()
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
        fixture_line, _ = ws.freeze_fixture()
        labels_line, record, _ = ws.freeze_labels()
    manifest = json.loads((ws.root / "labels-v1" / "labels.json").read_bytes())
    print("INFO labels-v1 sessions:", [(s["take"], s["split"]) for s in manifest["sessions"]])
    for s in manifest["sessions"]:
        if s["split"] == "held_out":
            os.chmod(ws.root / "labels-v1" / s["file"], 0o600)
            os.remove(ws.root / "labels-v1" / s["file"])
            print("INFO removed", s["file"])
    sys.path.insert(0, os.path.join(os.path.dirname(os.getcwd()), "den-n2"))
    import score
    text = fixture_line + "\n" + labels_line + "\n"
    try:
        gold, meta, sessions = score.load_n1(ws.root, "development", text)
        print("RESULT N2 development load SUCCEEDED without the held-out label files:",
              [s.take for s in sessions])
    except score.Refused as exc:
        print("RESULT N2 development load REFUSED:", exc)
    try:
        score.load_n1(ws.root, "held_out", text)
        print("RESULT N2 held_out load succeeded")
    except score.Refused as exc:
        print("RESULT N2 held_out load REFUSED (it opens/verifies held-out files):", exc)
finally:
    t.doCleanups()
    T.tearDownModule()
'''
folder = Path(tempfile.mkdtemp(prefix="heldout-", dir=WORK))
for sub in ("den-label", "den-take"):
    shutil.copytree(ROOT / "tools" / sub, folder / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(N2ROOT / "tools" / "den-n2", folder / "tools" / "den-n2", ignore=shutil.ignore_patterns("__pycache__"))
proc = subprocess.run([sys.executable, "-B", "-c", INNER], cwd=folder / "tools/den-label",
                      capture_output=True, text=True, timeout=1200)
print(proc.stdout.rstrip())
if proc.returncode:
    print("STDERR:", "\n".join(proc.stderr.strip().splitlines()[-10:]))
shutil.rmtree(folder, ignore_errors=True)
