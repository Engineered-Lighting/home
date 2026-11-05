#!/usr/bin/env python3
"""probe_invalidated-entry-used: does den-observe honour a camera entry marked invalidated?

Rung 1 (tools/den-register, records.invalidated_revision) marks a camera entry
`"invalidated": {...reason: "mount_changed"...}` when check-mount finds the camera
moved; error_bound_m and the pose stay numeric. schemas.json says an invalidated
transform gives null joints_room/room_position. This probe builds den-observe's
own synthetic take and RoomModel, adds rung 1's invalidated marker to the one
camera entry, and replays. PASS = refused (or null geometry); FAIL = valid
Observations written with the invalidated transform.

    python3 probe_invalidated-entry-used.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only, no network (connect allowed to 127.0.0.1 only).
"""
import json
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

work = Path(tempfile.mkdtemp(prefix="probe-invalidated-"))
try:
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    entry = synth.camera_entry()
    # Exactly the marker den-register's check-mount appends on MOUNT CHANGED (register.py run_check_mount).
    entry["invalidated"] = {"ts": 1800002000.0, "reason": "mount_changed", "take": "2026-11-13-1",
                            "take_ended_unix": 1800001000.0, "frame": 1, "check": {"status": "changed"},
                            "recheck": ["the camera's mount"]}
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model([entry])), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
    try:
        run = replay.replay(take, room, work / "out", detections_path=rows, log=lambda *a: None)
    except replay.Refused as exc:
        print(f"RESULT refused: {exc}")
        print("PASS: an invalidated registration is refused")
        sys.exit(0)
    obs = [json.loads(l) for l in (work / "out" / "observations.jsonl").read_text().splitlines()]
    valid = [o for o in obs if o["validity"] == "valid" and o.get("room_position")]
    print(f"RESULT wrote {len(obs)} observations, {len(valid)} valid with a room_position, "
          f"transform_id {run['transform_id']}, gate problems {run['gate']['records_with_problems']}")
    print(f"RESULT first room_position {valid[0]['room_position'] if valid else None}")
    if valid:
        print("FAIL: den-observe used a camera entry rung 1 marked invalidated (mount_changed) and wrote valid geometry")
        sys.exit(1)
    print("PASS")
finally:
    shutil.rmtree(work, ignore_errors=True)
