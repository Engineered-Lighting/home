#!/usr/bin/env python3
"""Seam: can rung 2 (den-observe) use the RoomModel camera entry rung 1 writes?

Runs den-register end to end on a synthetic scene (REGISTERED), then hands its
room-model.jsonl to rung 2's own reader (tools/den-observe/roommodel.py on
claude/rung-2-observe) in a separate interpreter:
  a. roommodel.load() on the one-revision file, then on a two-revision file
     (after a second registration);
  b. roommodel.Camera(latest revision, transform_id, max_error_bound_m=0.05);
  c. the same for an entry whose error_bound_m is above 0.05 (leave-one-out);
  d. informational: an `invalidated` entry, augmented to rung 2's form, is it refused?
Prints each refusal text. Synthetic data only; temp dirs only; contacts nothing.

    python3 -B probe_seam_rung2_camera_entry.py <rung-1-register root> [<rung-2-observe root>]
"""
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
R2 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT.parent / "rung-2-observe"
TOOL = ROOT / "tools" / "den-register"
sys.path.insert(0, str(TOOL))
import method  # noqa: E402
import records  # noqa: E402
import register  # noqa: E402
import synthetic  # noqa: E402
import worksheet as worksheet_mod  # noqa: E402

FAILS = []


def closed_method():
    text = (TOOL / "REGISTER-V1.md").read_text(encoding="utf-8")
    text = re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda m: f"{m[1]} = {m[2]}  # closed for a probe",
                  text, flags=re.M)
    return method.parse(text)


def register_scene(folder, offsets=None, shift=None, now=1_800_000_500.0, work=None, take=None):
    depth, left, seen = synthetic.render(synthetic.POINTS)
    if take is None:
        take = synthetic.write_take(folder / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    if work is None:
        work = folder / "work"
        work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet(synthetic.POINTS, offsets=offsets)),
                                             encoding="utf-8")
    m = closed_method()
    sheet = worksheet_mod.load(work / "worksheet.json", need_positions=False)
    session = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid, (u, v, _) in seen.items():
        du, dv = (shift or {}).get(pid, (0, 0))
        session.click({"id": pid, "u": round(u + du, 1), "v": round(v + dv, 1)})
    session.done()
    code, report = register.run_fit(work, take, m=m, now=lambda: now, out=io.StringIO())
    return code, report, work, take


RUNG2 = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import roommodel
path, tid, bound = sys.argv[2], sys.argv[3], float(sys.argv[4])
try:
    model = roommodel.load(path)
    print("LOAD ok")
except roommodel.RegistrationRefused as exc:
    print("LOAD refused:", exc)
    model = None
latest = json.loads(open(sys.argv[5], encoding="utf-8").read())
try:
    cam = roommodel.Camera(latest, tid, max_error_bound_m=bound)
    print("CAMERA ok", cam.transform_id)
except roommodel.RegistrationRefused as exc:
    print("CAMERA refused:", exc)
'''


def rung2(path, tid, latest_json, tmp):
    script = Path(tmp) / "rung2_reader.py"
    script.write_text(RUNG2, encoding="utf-8")
    latest = Path(tmp) / "latest.json"
    latest.write_text(json.dumps(latest_json), encoding="utf-8")
    out = subprocess.run([sys.executable, "-B", "-I", str(script), str(R2 / "tools" / "den-observe"), str(path), tid,
                          "0.05", str(latest)], capture_output=True, text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


if not (R2 / "tools" / "den-observe" / "roommodel.py").exists():
    print(f"RESULT SKIP: rung 2 checkout not found at {R2}")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / "a").mkdir()
    code, report, work, take = register_scene(tmp / "a")
    print(f"RESULT rung 1: {report['result']} {report['transform_id']}")
    latest = records.read_jsonl(work / "room-model.jsonl", "x")[-1]
    entry = latest["cameras"][-1]
    print("RESULT rung 1 entry keys:", sorted(entry), "intrinsics keys:", sorted(entry["intrinsics"]),
          "pose.camera_frame:", repr(entry["pose"]["camera_frame"]))
    text = rung2(work / "room-model.jsonl", report["transform_id"], latest, tmp)
    print("RESULT rung 2 on one revision:", text.replace("\n", " | "))
    ok_b = "CAMERA ok" in text
    print(("PASS" if ok_b else "FAIL") + " rung 2 accepts rung 1's REGISTERED entry")
    if not ok_b:
        FAILS.append("form")

    # a second registration (one click moved) -> two revisions in room-model.jsonl
    code2, report2, work, take = register_scene(tmp / "a", shift={"p1": (0.4, 0)}, now=1_800_000_900.0,
                                                work=work, take=take)
    latest2 = records.read_jsonl(work / "room-model.jsonl", "x")[-1]
    text = rung2(work / "room-model.jsonl", report2["transform_id"], latest2, tmp)
    print("RESULT rung 2 on the two-revision file:", text.replace("\n", " | "))
    ok_a = "LOAD ok" in text
    print(("PASS" if ok_a else "FAIL") + " rung 2 reads rung 1's room-model.jsonl after a second revision")
    if not ok_a:
        FAILS.append("jsonl")

    # high leave-one-out bound
    (tmp / "c").mkdir()
    off = {"p1": [-0.002, -0.0052, -0.015], "p2": [-0.0037, 0.0328, -0.0091], "p3": [-0.002, 0.0169, 0.0112],
           "p4": [0.0114, 0.0129, -0.0295], "c1": [-0.013, 0.0019, 0.0075], "c2": [0.0194, 0.007, -0.0036]}
    code3, report3, work3, _ = register_scene(tmp / "c", offsets=off)
    latest3 = records.read_jsonl(work3 / "room-model.jsonl", "x")[-1]
    text = rung2(work3 / "room-model.jsonl", report3["transform_id"], latest3, tmp)
    print(f"RESULT rung 1: {report3['result']} error_bound_m {latest3['cameras'][-1]['error_bound_m']}; "
          f"rung 2: {text.replace(chr(10), ' | ')}")
    if report3["result"] == "registered" and "above rung 1" in text:
        print("FAIL rung 1 REGISTERED an entry that rung 2 refuses as above rung 1's gate")
        FAILS.append("bound")

    # informational: invalidated entry augmented to rung 2's form
    aug = json.loads(json.dumps(latest))
    e = aug["cameras"][-1]
    k = {"fx": 450.0, "fy": 450.0, "cx": 320.0, "cy": 200.0}
    e["intrinsics"] = {"source": "probe", "rectified_left": k, "rgb": k, "rgb_distortion": [0] * 8,
                       "rgb_from_rectified_left": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                                   "translation_m": [0.0, 0.0, 0.0]},
                       "rgb_size": [640, 360], "depth_size": [640, 400], "stereo_baseline_m": 0.075}
    e["pose"]["camera_frame"] = "rectified_left"
    e["invalidated"] = {"reason": "mount_changed"}
    text = rung2(work / "room-model.jsonl", report["transform_id"], aug, tmp)
    print("RESULT informational, rung 2 on an invalidated entry in its own form:", text.splitlines()[-1])

print("RESULT", "FAIL" if FAILS else "PASS", FAILS)
sys.exit(1 if FAILS else 0)
