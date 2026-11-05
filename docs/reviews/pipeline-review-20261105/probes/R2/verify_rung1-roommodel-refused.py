#!/usr/bin/env python3
"""verify_rung1-roommodel-refused: feed den-observe a room-model.jsonl that rung 1's own code wrote.

What it checks
    Instead of a hand-built entry, this runs rung 1's real registration path
    (tools/den-register test harness Scene: synthetic take, clicks, run_fit)
    in a subprocess, once and then a second time with one click moved (a
    re-registration), so room-model.jsonl has one line and then two lines.
    It copies the file after each fit and gives each to den-observe's
    roommodel.load / current_transform_id / Camera (max_error_bound_m 0.05),
    printing whether den-observe accepts it.

How to run
    python3 -B verify_rung1-roommodel-refused.py <rung-2 checkout root> <rung-1 checkout root>

Synthetic data only; everything is written in a temporary folder that is
removed afterwards. No network, no camera.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

rung2 = Path(sys.argv[1]).resolve()
rung1 = Path(sys.argv[2]).resolve()
work = Path(tempfile.mkdtemp(prefix="verify-rung1-"))

GEN = r'''
import sys, types, tempfile, shutil
from pathlib import Path
tool = Path(sys.argv[1]) / "tools" / "den-register"
sys.path.insert(0, str(tool))
import test_den_register as t
out = Path(sys.argv[2])
holder = types.SimpleNamespace(tmp=tempfile.TemporaryDirectory())
s = t.Scene(holder)
s.click_all()
code, report = s.fit()
print("fit 1 exit", code, report.get("result"), report.get("transform_id"))
shutil.copy(s.work / "room-model.jsonl", out / "one-line.jsonl")
s.click_all(["p1"], shift={"p1": (0.4, 0)})
code, report = s.fit(now=1_800_000_900.0)
print("fit 2 exit", code, report.get("result"), report.get("transform_id"))
shutil.copy(s.work / "room-model.jsonl", out / "two-line.jsonl")
holder.tmp.cleanup()
'''

fails = 0
try:
    gen = subprocess.run([sys.executable, "-B", "-c", GEN, str(rung1), str(work)], capture_output=True, text=True,
                         timeout=300)
    print("INFO rung 1 generator:", gen.stdout.strip().replace("\n", " | "), gen.stderr.strip()[-400:])
    sys.path.insert(0, str(rung2 / "tools" / "den-observe"))
    import roommodel  # noqa: E402
    for name in ("one-line.jsonl", "two-line.jsonl"):
        path = work / name
        lines = [ln for ln in path.read_text(encoding="utf-8").split("\n") if ln.strip()]
        last = json.loads(lines[-1])
        cam = [c for c in last["cameras"] if c.get("camera_id") == "den-oak"][0]
        print(f"INFO {name}: {len(lines)} line(s); entry intrinsics keys {sorted(cam['intrinsics'])}; "
              f"pose.camera_frame {cam['pose']['camera_frame']!r}; version {cam['version']!r}; "
              f"error_bound_m {cam['error_bound_m']}")
        for label, target in ((f"{name} as written", path), (f"{name} last line only", None)):
            if target is None:
                target = work / (name + ".last.json")
                target.write_text(lines[-1], encoding="utf-8")
            try:
                model = roommodel.load(target)
                tid = roommodel.current_transform_id(model, "den-oak")
                roommodel.Camera(model, tid, max_error_bound_m=0.05)
                print(f"PASS {label}: den-observe accepted {tid}")
            except roommodel.RegistrationRefused as exc:
                fails += 1
                print(f"FAIL {label}: refused -- {exc}")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} of 4 rung-1-written RoomModels refused by den-observe")
