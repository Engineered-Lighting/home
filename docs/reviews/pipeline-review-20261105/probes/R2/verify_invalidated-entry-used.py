#!/usr/bin/env python3
"""verify_invalidated-entry-used: rung 1's own invalidation vs den-observe, through rung 1's own code.

Independent re-check of finding invalidated-entry-used. Builds den-observe's
synthetic take and RoomModel (den-observe-camera-1 form, so the form seam is
taken out of the picture), then applies rung 1's records.invalidated_revision()
(the exact function check-mount uses on MOUNT CHANGED) to that RoomModel, asks
rung 1's records.resolve() what the transform is (it reports invalidated), and
replays the take through den-observe with the invalidated revision.

    python3 verify_invalidated-entry-used.py <rung-2-observe root> [<rung-1-register root>]

PASS = den-observe refuses (or writes null geometry); FAIL = valid Observations
with room geometry from a transform rung 1 says is invalidated.
Synthetic data only, temp dirs only; connect allowed to 127.0.0.1 only.
"""
import importlib
import json
import shutil
import socket
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
r1 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / "rung-1-register"
_real = socket.socket.connect
socket.socket.connect = lambda s, a, *k, **kw: (_real(s, a, *k, **kw) if a[0] == "127.0.0.1"
                                                else (_ for _ in ()).throw(AssertionError(a)))

# rung 1's records module (it imports rung 1's scene and rigid)
sys.path.insert(0, str(r1 / "tools" / "den-register"))
records1 = importlib.import_module("records")
for name in ("records", "scene", "rigid", "method"):
    sys.modules.pop(name, None)
sys.path.pop(0)

sys.path.insert(0, str(root / "tools" / "den-observe"))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="verify-invalidated-"))
code = 0
try:
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    base = synth.room_model()
    marker = {"ts": 1800002000.0, "reason": "mount_changed", "take": "later-1", "take_ended_unix": 1800001000.0,
              "frame": 1, "check": {"status": "changed"}, "recheck": ["the camera's mount"]}
    revised = records1.invalidated_revision(base, "den-oak", marker)
    found = records1.resolve([base, revised], "den-oak@2026-11-12")
    print(f"RESULT rung 1 resolve: invalidated={bool(found['invalidated'])} "
          f"reason={(found['invalidated'] or {}).get('reason')}")
    room = work / "room.json"
    room.write_text(json.dumps(revised), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)])
    try:
        run = replay.replay(take, room, work / "out", detections_path=rows, log=lambda *a: None)
    except replay.Refused as exc:
        print(f"RESULT den-observe refused: {exc}")
        print("PASS")
    else:
        obs = [json.loads(x) for x in (work / "out" / "observations.jsonl").read_text().splitlines()]
        valid = [o for o in obs if o["validity"] == "valid" and o.get("room_position")]
        nonnull_joints = sum(1 for o in obs for e in (o.get("joints_room") or []) if e.get("xyz") is not None)
        print(f"RESULT den-observe wrote {len(obs)} obs, {len(valid)} valid with room_position, "
              f"{nonnull_joints} non-null joints_room xyz, transform_id {run['transform_id']}, "
              f"gate pass {run['gate']['pass']}, problems {run['gate']['records_with_problems']}")
        if valid:
            print("FAIL: den-observe used a transform rung 1's resolve reports as invalidated")
            code = 1
finally:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(code)
