#!/usr/bin/env python3
"""verify_SEAM-01: does den-observe refuse den-register's RoomModel camera entry?

    TMPDIR=<scratch dir> python3 -B verify_SEAM-01.py <rung-2-observe checkout> [<rung-1-register checkout>]

The second checkout defaults to a sibling folder named rung-1-register.
Synthetic data only, temp dirs only, no network, no bytecode written into
the checkouts (python -B in every child process).

Steps
 1. den-register (its own synthetic scene, the same path its tests use:
    synthetic take, worksheet, a click per point, run_fit with the proposed
    REGISTER-V1 values closed in memory) writes room-model.jsonl (one line).
 2. den-observe's roommodel.Camera is built from that file's camera entry.
 3. The owner-facing CLI: den-observe replay.py --fake on den-observe's own
    synthetic take, with --room-model pointing at den-register's file.
 4. den-register's entry with den-observe-camera-1 intrinsics swapped in,
    den-register's pose kept: does the pose.camera_frame check refuse next?
 5. Same, with pose.camera_frame set to "rectified_left" too (should build).
 6. Control: den-observe's own synthetic RoomModel builds a Camera.
Prints PASS/FAIL/RESULT lines. "FAIL" here means the defect reproduced.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


def child(code, *args):
    proc = subprocess.run([sys.executable, "-B", "-c", code, *map(str, args)], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


REGISTER = r'''
import io, json, sys, types
sys.dont_write_bytecode = True
reg, tmp, fits = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, reg)
import test_den_register as TR
t = types.SimpleNamespace(tmp=types.SimpleNamespace(name=tmp))
s = TR.Scene(t)
s.click_all()
code, report = s.fit()
out = [{"code": code, "transform_id": report.get("transform_id"), "reasons": report.get("reasons")}]
if fits > 1:
    s.click_all(["p1"], shift={"p1": (0.4, 0)})  # as den-register's own version-bump test does
    code, report = s.fit(now=1_800_000_900.0)
    out.append({"code": code, "transform_id": report.get("transform_id"), "reasons": report.get("reasons")})
print(json.dumps({"fits": out, "room_model": str(s.work / "room-model.jsonl")}))
'''

OBSERVE = r'''
import copy, json, sys
sys.dont_write_bytecode = True
obs, mode, path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, obs)
sys.path.insert(0, obs.rsplit("/", 1)[0] + "/den-take")
import method, roommodel, synth
m = method.load()
bound = m.get("max_error_bound_m")
def build(model):
    tid = roommodel.current_transform_id(model, "den-oak")
    try:
        roommodel.Camera(model, tid, max_error_bound_m=bound)
        return "ACCEPTED " + tid
    except roommodel.RegistrationRefused as exc:
        return "REFUSED " + str(exc)
if mode == "file":
    try:
        model = roommodel.load(path)
    except roommodel.RegistrationRefused as exc:
        print(json.dumps({"result": "REFUSED " + str(exc)})); sys.exit()
    print(json.dumps({"result": build(model)}))
elif mode in ("swap-intrinsics", "swap-both"):
    model = json.loads(open(path, encoding="utf-8").read().splitlines()[-1])
    entry = model["cameras"][-1]
    entry["intrinsics"] = synth.camera_entry()["intrinsics"]
    if mode == "swap-both":
        entry["pose"]["camera_frame"] = "rectified_left"
    print(json.dumps({"result": build(model), "camera_frame": entry["pose"]["camera_frame"]}))
elif mode == "control":
    print(json.dumps({"result": build(synth.room_model())}))
elif mode == "take":
    scene = synth.Scene()
    depth = scene.depth_mm()
    frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(4)]
    folder = synth.make_take(path, frames, depth)
    synth.write_check(folder)
    print(json.dumps({"take": str(folder)}))
'''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    obs_root = Path(sys.argv[1]).resolve()
    reg_root = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else obs_root.parent / "rung-1-register"
    obs = obs_root / "tools" / "den-observe"
    reg = reg_root / "tools" / "den-register"
    for p in (obs, reg):
        if not p.is_dir():
            print(f"FAIL setup: {p} missing")
            return 2
    tmp = Path(tempfile.mkdtemp(prefix="verify-seam01-"))
    print(f"RESULT temp folder {tmp}")

    code, out, err = child(REGISTER, reg, tmp / "reg", 1)
    if code != 0:
        print("FAIL setup: den-register run failed\n" + err[-2000:])
        return 2
    info = json.loads(out.strip().splitlines()[-1])
    rm = Path(info["room_model"])
    print(f"RESULT den-register fit: {info['fits']}")
    lines = rm.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])["cameras"][-1]
    print(f"RESULT den-register room-model.jsonl lines: {len(lines)}")
    print(f"RESULT den-register entry intrinsics keys: {sorted(entry['intrinsics'])}")
    print(f"RESULT den-register entry pose.camera_frame: {entry['pose']['camera_frame']!r}")

    _, out, err = child(OBSERVE, obs, "file", rm)
    r2 = json.loads(out.strip().splitlines()[-1])["result"] if out.strip() else "ERROR " + err[-500:]
    print(("FAIL" if r2.startswith("REFUSED") else "PASS") + f" step2 den-observe Camera(den-register entry): {r2}")

    _, out, err = child(OBSERVE, obs, "take", tmp / "obs-take")
    take = json.loads(out.strip().splitlines()[-1])["take"]
    proc = subprocess.run([sys.executable, "-B", obs / "replay.py", "--take", take, "--room-model", rm, "--fake",
                           "--out", tmp / "observe-out"], capture_output=True, text=True)
    print(("FAIL" if proc.returncode == 2 else "PASS") + f" step3 CLI replay.py --fake with den-register file: exit "
          f"{proc.returncode}: {(proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else ''}")

    _, out, err = child(OBSERVE, obs, "swap-intrinsics", rm)
    r4 = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"result": "ERROR " + err[-500:]}
    print(("FAIL" if "camera_frame" in r4["result"] else "INFO") + f" step4 intrinsics in den-observe form, "
          f"den-register pose kept: {r4['result']}")

    _, out, err = child(OBSERVE, obs, "swap-both", rm)
    r5 = json.loads(out.strip().splitlines()[-1]) if out.strip() else {"result": "ERROR " + err[-500:]}
    print(("PASS" if r5["result"].startswith("ACCEPTED") else "INFO") + f" step5 intrinsics and camera_frame both in "
          f"den-observe form: {r5['result']}")

    _, out, err = child(OBSERVE, obs, "control", "-")
    r6 = json.loads(out.strip().splitlines()[-1])["result"] if out.strip() else "ERROR " + err[-500:]
    print(("PASS" if r6.startswith("ACCEPTED") else "FAIL") + f" step6 control, den-observe synth RoomModel: {r6}")

    reproduced = r2.startswith("REFUSED") and proc.returncode == 2
    print("RESULT SEAM-01 " + ("REPRODUCED: den-observe refuses every den-register camera entry" if reproduced
                               else "NOT reproduced"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
