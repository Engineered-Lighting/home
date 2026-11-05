#!/usr/bin/env python3
"""probe_null_not_zero: unsupported joints are null, never zero-filled.

What it checks
    schemas.json Observation.joints_room: "A joint is never zero-filled: an
    unsupported joint has null xyz and cov." The probe writes one synthetic take
    and one detector row whose 17 keypoints mix every way a joint can lack
    support, replays it (unscored, default METHOD.md, keypoint_score_min 0.4),
    and checks each joints_room entry against what it should be:
      j0  null keypoint               -> unsupported, xyz/cov null, lift none
      j1  score 0.39 (< 0.4)          -> unsupported null
      j2  score exactly 0.4           -> supported or uncertain (has 2D support)
      j3  u = -0.5 (left of frame)    -> unsupported null
      j4  v = 360.5 (below frame)     -> unsupported null
      j5  u = 640.0 (on the edge)     -> lifted (0 <= u <= width)
      j9  pixel (2, 2) far corner     -> lifted or unsupported null, never zero
      j10 score 0.0                   -> unsupported null
      the rest at their true pixels   -> lifted by stereo
    and that no entry anywhere has xyz [0,0,0] or an all-zero cov, and that the
    record's keypoints array keeps a null keypoint as [null, null, null].
    It also runs observe.check_record on a copy with one unsupported joint
    zero-filled, which must be reported.

How to run
    python3 probe_null_not_zero.py <branch checkout root>

Synthetic data only, in a temporary folder that is removed afterwards.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import detections  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-null-"))
fails = 0
try:
    scene = synth.Scene()
    t = 5000.0
    take = synth.make_take(work / "take", [(t, t - 0.004)], scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    kps = [[*scene.joint_rgb(n), 0.9] for n in detections.COCO17]
    kps[0] = None
    kps[1][2] = 0.39
    kps[2][2] = 0.4
    kps[3] = [-0.5, kps[3][1], 0.9]
    kps[4] = [kps[4][0], 360.5, 0.9]
    kps[5] = [640.0, kps[5][1], 0.9]
    kps[9] = [2.0, 2.0, 0.9]
    kps[10][2] = 0.0
    row = {"schema": "den-detections-1",
           "frame": {"capture_ts": t, "clock": "host_monotonic", "rgb_seq": 1000, "width": 640, "height": 360},
           "available_ts": t + 0.3,
           "detector": {"boxes": "synthetic-boxes", "keypoints": "synthetic-pose", "keypoint_layout": "coco17"},
           "detections": [{"id": "p0", "class": "person", "score": 0.9, "box_xyxy": [150.0, 20.0, 500.0, 350.0],
                           "keypoints": kps}]}
    detections.write_rows(work / "rows.jsonl", [row])
    replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
    obs = json.loads((work / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()[0])
    joints = {e["j"]: e for e in obs["joints_room"]}
    null = {"xyz": None, "cov": None, "status": "unsupported", "lift": "none"}
    expect_null = (0, 1, 3, 4, 10)
    for j in range(17):
        e = joints[j]
        is_null = all(e[k] == v for k, v in null.items())
        zero = e["xyz"] == [0, 0, 0] or (e["cov"] is not None and all(c == 0 for r in e["cov"] for c in r))
        if j in expect_null:
            ok = is_null
        elif j == 9:
            ok = is_null or (e["status"] in ("supported", "uncertain") and e["xyz"] is not None)
        else:
            ok = (not is_null) and e["status"] in ("supported", "uncertain") and e["xyz"] is not None
        ok = ok and not zero
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} j{j:<2} keypoint {kps[j] if kps[j] is None else [round(c, 2) for c in kps[j]]}: "
              f"status {e['status']}, lift {e['lift']}, xyz {e['xyz']}")
    ok = obs["keypoints"][0] == [None, None, None]
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} keypoints[0] kept as {obs['keypoints'][0]}")
    zeroed = json.loads(json.dumps(obs))
    zeroed["joints_room"][1].update(xyz=[0.0, 0.0, 0.0], cov=[[0.0] * 3] * 3)
    problems = observe.check_record(zeroed, lambda tid: tid == obs["transform_id"])
    ok = any("unsupported but not null" in p for p in problems)
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} check_record on a zero-filled unsupported joint: {problems}")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} null-not-zero checks wrong")
