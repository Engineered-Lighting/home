#!/usr/bin/env python3
"""probe_torso_point: the keypoint Observation's room_position, recomputed independently.

What it checks
    schemas.json Observation.room_position: "For keypoints it is the torso
    reference point (the mean of the supported shoulders and hips)".
    The probe writes synthetic R takes (synth.py, den-take's writer) whose depth
    has holes over chosen torso joints, replays each with replay.replay
    (unscored, default METHOD.md) and, from the written record alone, computes
    its own torso point: the mean of joints_room xyz for j in {5, 6, 11, 12}
    with status 'supported' (stereo); if there are none, of those 'uncertain'
    (plane), as METHOD.md declares. It compares that with room_position, and
    for the all-stereo case also checks room_position.cov against
    sum_j (cov_j - Sigma_cal) / n^2 + Sigma_cal (independent stereo noise,
    calibration added once). Cases:
      all4      no holes: 4 stereo torso joints
      right2    holes over left shoulder and left hip: 2 stereo + 2 plane
      one       holes over 3 torso joints: 1 stereo + 3 plane
      stale     depth 0.2 s old: 4 plane torso joints
      none      no torso joint detected at all: no room_position, rejected

How to run
    python3 probe_torso_point.py <branch checkout root>

Synthetic data only, in a temporary folder that is removed afterwards.
"""
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import camgeom as g  # noqa: E402
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

TORSO = {"left_shoulder": 5, "right_shoulder": 6, "left_hip": 11, "right_hip": 12}
work = Path(tempfile.mkdtemp(prefix="probe-torso-"))
scene = synth.Scene()
PIXELS = {n: scene.joint_rgb(n) for n in synth.JOINTS}


def hole(name):
    u, v = g.project(scene.joint_left(name), g.intrinsics(synth.LEFT_K))
    return (int(u) - 14, int(v) - 14, int(u) + 14, int(v) + 14)


def run(name, holes=(), stale=False, pixels=PIXELS):
    folder = work / name
    t = 5000.0
    take = synth.make_take(folder / "take", [(t, t - (0.2 if stale else 0.004))], scene.depth_mm(holes=list(holes)))
    synth.write_check(take)
    room = folder / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    detections.write_rows(folder / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000, pixels=pixels)])
    replay.replay(take, room, folder / "out", detections_path=folder / "rows.jsonl", log=lambda *a: None)
    return json.loads((folder / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])


fails = 0
try:
    eb = synth.camera_entry()["error_bound_m"]
    cases = {"all4": dict(), "right2": dict(holes=[hole("left_shoulder"), hole("left_hip")]),
             "one": dict(holes=[hole("left_shoulder"), hole("left_hip"), hole("right_hip")]),
             "stale": dict(stale=True),
             "none": dict(pixels={n: (None if n in TORSO else p) for n, p in PIXELS.items()})}
    for name, kwargs in cases.items():
        obs = run(name, **kwargs)
        joints = {e["j"]: e for e in obs["joints_room"]}
        torso = [joints[j] for j in TORSO.values()]
        stereo = [e for e in torso if e["status"] == "supported"]
        plane = [e for e in torso if e["status"] == "uncertain"]
        used = stereo or plane
        rp = obs.get("room_position")
        if not used:
            ok = rp is None and obs["validity"].startswith("rejected:")
            print(f"{'PASS' if ok else 'FAIL'} {name}: no torso joint -> room_position {rp}, validity {obs['validity']}")
            fails += not ok
            continue
        mean = [sum(e["xyz"][i] for e in used) / len(used) for i in range(3)]
        got = [rp["x"], rp["y"], rp["z"]]
        err = math.dist(mean, got)
        ok = err < 1e-5
        detail = ""
        if name == "all4":
            n = len(stereo)
            want = [[sum(e["cov"][i][k] - (eb * eb if i == k else 0) for e in stereo) / n ** 2
                     + (eb * eb if i == k else 0) for k in range(3)] for i in range(3)]
            dcov = max(abs(want[i][k] - rp["cov"][i][k]) for i in range(3) for k in range(3))
            ok = ok and dcov < 1e-8
            detail = f", |dcov| {dcov:.1e}"
        truth = [sum(scene.joint_room(nm)[i] for nm, j in TORSO.items() if joints[j] in used) / len(used)
                 for i in range(3)]
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}: {len(stereo)} stereo + {len(plane)} plane torso joints; "
              f"mean of the {'supported' if stereo else 'plane'} ones {[round(c, 4) for c in mean]} vs "
              f"room_position {[round(c, 4) for c in got]} (|d| {err:.1e} m{detail}); "
              f"distance to the true mean of those joints {math.dist(got, truth):.3f} m; validity {obs['validity']}")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} torso cases differ from the independent mean")
