#!/usr/bin/env python3
"""Seam probe: can rung 2 (den-observe) use the camera entry rung 1 (den-register) writes?

Checks, on a synthetic scene only (no camera, no network):
  1. den-register registers a synthetic camera (closed method, proposed values) and
     appends one RoomModel revision to room-model.jsonl.
  2. That revision is handed, as rung 2's README says (--room-model <file>, one JSON
     object), to den-observe's roommodel.Camera(room_model, transform_id,
     max_error_bound_m=0.05).
  3. A second registration plus a mount invalidation is made, and the file is read
     by rung 2's loader as written (JSON Lines, several lines).
  4. rung 2 is given an entry rung 1 marked `invalidated` (with the missing
     den-observe-camera-1 fields filled in by hand) to see whether it honours it.

Run:
  python3 probe_seam-rung2-refuses-entry.py <rung-1 checkout root> [<rung-2 checkout root>]
The rung-2 root defaults to <rung-1 root>/../rung-2-observe.
Prints RESULT lines; FAIL means the seam does not connect.
"""
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root1 = Path(sys.argv[1]).resolve()
root2 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root1.parent / "rung-2-observe"
tool1 = root1 / "tools" / "den-register"
tool2 = root2 / "tools" / "den-observe"
sys.path.insert(0, str(tool1))
import method, records, register, synthetic  # noqa: E402

text = (tool1 / "REGISTER-V1.md").read_text(encoding="utf-8")
text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", r"\1 = \2  # closed for the probe", text, flags=re.M)
M = method.parse(text)


def make_scene(tmp, name="take1", ended=None):
    depth, left, seen = synthetic.render(synthetic.POINTS)
    data = synthetic.bundle(synthetic.metadata(), depth, left)
    take = synthetic.write_take(tmp / name, [data] * 3)
    if ended is not None:
        p = take / "take.json"
        t = json.loads(p.read_text(encoding="utf-8"))
        t["ended_unix"] = ended
        p.write_text(json.dumps(t), encoding="utf-8")
    return take, seen


def click_all(work, take, seen):
    sheet = register.worksheet_file.load(work / "worksheet.json", need_positions=False)
    n = register.frame_number(take, None, work)
    s = register.ClickSession(work, take, n, sheet, M)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()


RUNG2 = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import roommodel
path, tid = sys.argv[2], sys.argv[3]
try:
    model = roommodel.load(path)
    cam = roommodel.Camera(model, tid, max_error_bound_m=0.05)
    print("ACCEPTED", cam.transform_id)
except roommodel.RegistrationRefused as exc:
    print("REFUSED", exc)
'''


def rung2(path, tid):
    out = subprocess.run([sys.executable, "-I", "-c", RUNG2, str(tool2), str(path), tid],
                         capture_output=True, text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


fails = 0
with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    work = tmp / "work"
    work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    take, seen = make_scene(tmp)
    click_all(work, take, seen)
    code, report = register.run_fit(work, take, m=M, now=lambda: 1_800_000_500.0, out=io.StringIO())
    print(f"rung 1 fit: exit {code}, result {report['result']}, transform_id {report['transform_id']}")
    lines = (work / "room-model.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])["cameras"][-1]
    print("rung 1 entry keys:", sorted(entry))
    print("rung 1 intrinsics keys:", sorted(entry["intrinsics"]))
    print("rung 1 pose.camera_frame:", repr(entry["pose"]["camera_frame"]))
    single = tmp / "room-model.json"
    single.write_text(lines[-1] + "\n", encoding="utf-8")
    got = rung2(single, report["transform_id"])
    print("rung 2 on rung 1's single revision:", got)
    if not got.startswith("ACCEPTED"):
        fails += 1
        print("FAIL seam: rung 2 refuses the camera entry rung 1 writes")
    else:
        print("PASS seam: rung 2 accepts rung 1's entry")

    # rung 2 handed the JSON Lines file itself, after a mount invalidation (two lines).
    later, _ = make_scene(tmp, "later", ended=1_800_000_900.0)
    # move the camera: a different background depth makes most cells change
    depth, left, _ = synthetic.render(synthetic.POINTS, background_mm=2500)
    data = synthetic.bundle(synthetic.metadata(), depth, left)
    moved = synthetic.write_take(tmp / "moved", [data] * 3)
    code2, rep2 = register.run_check_mount(work, moved, m=M, now=lambda: 1_800_001_000.0, out=io.StringIO())
    print(f"rung 1 check-mount: exit {code2}, result {rep2['result']}")
    got = rung2(work / "room-model.jsonl", report["transform_id"])
    print("rung 2 on rung 1's room-model.jsonl as written:", got)
    if not got.startswith("ACCEPTED"):
        print("NOTE rung 2 cannot read the JSON Lines file rung 1 writes (it json.load()s one object)")

    # Does rung 2 honour `invalidated`? Fill the den-observe-camera-1 fields by hand.
    inval = json.loads((work / "room-model.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    cam = inval["cameras"][-1]
    print("rung 1 invalidated entry carries 'invalidated':", "invalidated" in cam)
    k = cam["intrinsics"]
    kk = {"fx": k["fx"], "fy": k["fy"], "cx": k["cx"], "cy": k["cy"]}
    cam["intrinsics"] = {"source": "probe", "rectified_left": kk, "rgb": kk, "rgb_distortion": [0.0] * 8,
                         "rgb_from_rectified_left": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                                     "translation_m": [0.0375, 0.0, 0.0]},
                         "stereo_baseline_m": 0.075, "depth_size": [640, 400], "rgb_size": [640, 360]}
    cam["pose"]["camera_frame"] = "rectified_left"
    filled = tmp / "filled.json"
    filled.write_text(json.dumps(inval) + "\n", encoding="utf-8")
    got = rung2(filled, report["transform_id"])
    print("rung 2 on an entry rung 1 invalidated (form filled by hand):", got)
    if got.startswith("ACCEPTED"):
        fails += 1
        print("FAIL seam: rung 2 accepts a transform rung 1 marked invalidated (mount_changed)")
print(f"RESULT {'FAIL' if fails else 'PASS'} ({fails} seam failures)")
