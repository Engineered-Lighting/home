#!/usr/bin/env python3
"""probe_newest-registration-for-old-take: which registration lifts a take recorded before a re-registration?

Scenario (rung 1's documented flow): the camera is registered (entry A), a take
is recorded, later the camera is bumped and re-registered (entry B, a newer
version, 10 cm to the side). Replaying the earlier take must use A, the
registration in force at its capture time. This probe renders the take with A's
pose, writes a RoomModel holding A and B, and replays:
  1. by default (no --transform-id),
  2. with --transform-id naming A.
PASS = A is used (or the run refuses as ambiguous); FAIL = B's pose silently
lifts the earlier take into valid Observations, and A is refused as "stale".

    python3 probe_newest-registration-for-old-take.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
"""
import json
import math
import shutil
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
_real = socket.socket.connect
socket.socket.connect = lambda s, a, *k, **kw: (_real(s, a, *k, **kw) if a[0] == "127.0.0.1"
                                                else (_ for _ in ()).throw(AssertionError(a)))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-newest-reg-"))
bad = 0
try:
    pose_a = synth.camera_pose()                       # where the camera was when the take was recorded
    pose_b = synth.camera_pose(position=(0.10, 0.0, 2.2))  # after the bump: 10 cm along x
    scene = synth.Scene(room_from_left=pose_a)
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model([
        synth.camera_entry(version="2026-11-12", room_from_left=pose_a),
        synth.camera_entry(version="2026-11-20", room_from_left=pose_b)])), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
    truth = [sum(scene.joint_room(n)[i] for n in ("left_shoulder", "right_shoulder", "left_hip", "right_hip")) / 4
             for i in range(3)]
    run = replay.replay(take, room, work / "out", detections_path=rows, log=lambda *a: None)
    obs = [json.loads(l) for l in (work / "out" / "observations.jsonl").read_text().splitlines()]
    rp = obs[0]["room_position"]
    err = math.dist([rp["x"], rp["y"], rp["z"]], truth)
    sd = math.sqrt(rp["cov"][0][0])
    print(f"RESULT default replay used {run['transform_id']}; validity {obs[0]['validity']}; "
          f"torso error {err:.3f} m vs truth; sigma_x {sd:.3f} m; gate problems {run['gate']['records_with_problems']}")
    if run["transform_id"].endswith("2026-11-20") and obs[0]["validity"] == "valid":
        bad += 1
    try:
        replay.replay(take, room, work / "out-a", detections_path=rows, transform_id="den-oak@2026-11-12",
                      log=lambda *a: None)
        print("RESULT --transform-id den-oak@2026-11-12 accepted")
    except replay.Refused as exc:
        print(f"RESULT --transform-id den-oak@2026-11-12 refused: {exc}")
        bad += 1
finally:
    shutil.rmtree(work, ignore_errors=True)
if bad:
    print("FAIL: the newest registration at replay time lifts a take captured under an earlier one; "
          "the one in force at capture is refused as stale")
    sys.exit(1)
print("PASS")
