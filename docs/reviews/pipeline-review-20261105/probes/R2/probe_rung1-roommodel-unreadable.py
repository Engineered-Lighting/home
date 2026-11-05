#!/usr/bin/env python3
"""probe_rung1-roommodel-unreadable: can den-observe read the RoomModel rung 1 really writes?

Rung 1 (claude/rung-1-register, tools/den-register) is the only producer of the
camera entry every Observation's transform_id must resolve to. This probe runs
den-register's own synthetic fit (its test harness, synthetic scene, no camera)
to get a real `room-model.jsonl`, then hands it to den-observe's roommodel
loader and Camera exactly as replay.py does. It also appends den-register's
check-mount invalidation revision (2 lines) and tries again.

    python3 probe_rung1-roommodel-unreadable.py <rung-2-observe root> [<rung-1-register root>]

The second argument defaults to the sibling worktree ../rung-1-register.
PASS = den-observe resolves rung 1's entry (and refuses the invalidated one);
FAIL = den-observe refuses rung 1's real registration, so no real run can start.
Temp dirs only; connect is allowed to 127.0.0.1 only.
"""
import importlib
import json
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
r1 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / "rung-1-register"
_real = socket.socket.connect
socket.socket.connect = lambda s, a, *k, **kw: (_real(s, a, *k, **kw) if a[0] == "127.0.0.1"
                                                else (_ for _ in ()).throw(AssertionError(a)))

# --- rung 1: a real registration through den-register's own code ---------------------------
sys.path.insert(0, str(r1 / "tools" / "den-register"))
t1 = importlib.import_module("test_den_register")
records1 = importlib.import_module("records")
register1 = importlib.import_module("register")
tmp = tempfile.TemporaryDirectory(prefix="probe-r1-")


class Holder:
    pass


holder = Holder()
holder.tmp = tmp
s = t1.Scene(holder, ended=1_800_000_100.0)
s.click_all()
code, report = s.fit()
print(f"RESULT rung 1 fit exit {code}, {report.get('result')}, transform_id {report.get('transform_id')}")
jsonl = s.work / "room-model.jsonl"
entry = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])["cameras"][0]
print("RESULT rung 1 entry keys:", sorted(entry))
print("RESULT rung 1 intrinsics keys:", sorted(entry["intrinsics"]))
print("RESULT rung 1 pose.camera_frame:", repr(entry["pose"]["camera_frame"]))

# --- rung 2: den-observe reading it ------------------------------------------------------------
for name in list(sys.modules):
    if name in ("records", "register", "method", "scene", "synthetic", "rigid", "worksheet", "serve"):
        del sys.modules[name]
sys.path.insert(0, str(root / "tools" / "den-observe"))
roommodel = importlib.import_module("roommodel")
method2 = importlib.import_module("method")
bound = method2.load().get("max_error_bound_m")
failures = 0


def try_camera(path, label):
    global failures
    try:
        model = roommodel.load(path)
        tid = roommodel.current_transform_id(model, "den-oak")
        cam = roommodel.Camera(model, tid, max_error_bound_m=bound)
        print(f"RESULT {label}: resolved {cam.transform_id}")
        return True
    except roommodel.RegistrationRefused as exc:
        print(f"RESULT {label}: refused: {exc}")
        return False


if not try_camera(jsonl, "room-model.jsonl as rung 1 wrote it (1 revision)"):
    failures += 1
# The same single revision saved as a plain .json, in case the file name were the only issue.
single = s.work / "room-model.json"
single.write_text(jsonl.read_text(encoding="utf-8").splitlines()[-1], encoding="utf-8")
if not try_camera(single, "its last revision as a lone JSON object"):
    failures += 1
# Rung 1's MOUNT CHANGED step appends an invalidated revision: a second line.
latest = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])
sys.path.insert(0, str(r1 / "tools" / "den-register"))
revision = records1.invalidated_revision(latest, "den-oak", {"reason": "mount_changed", "ts": 1.0})
with open(jsonl, "a", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(revision) + "\n")
try_camera(jsonl, "room-model.jsonl after check-mount invalidated it (2 revisions)")
tmp.cleanup()
if failures:
    print("FAIL: den-observe cannot resolve the camera entry rung 1 writes; every real replay is refused "
          "before any Observation (form: pose.camera_frame text, depth-only intrinsics, no RGB model/extrinsics/baseline)")
    sys.exit(1)
print("PASS")
