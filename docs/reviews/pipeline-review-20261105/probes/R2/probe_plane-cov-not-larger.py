#!/usr/bin/env python3
"""probe_plane-cov-not-larger: is the plane fallback's covariance always larger than stereo's?

Rung 2's gate: a too-old depth frame is not used and "the declared plane
fallback applies, with a larger covariance"; METHOD.md says "a plane joint
always has a wider covariance than a stereo one". This probe replays one frame
with fresh depth and the same frame with depth 0.2 s too old (den-observe's own
synthetic room: camera 2.2 m up, figure 3 m away, the proposed slot values),
and compares each joint's covariance (variance along its largest axis, trace,
determinant) between the stereo and the plane lift.
PASS = every plane joint's covariance is wider; FAIL = some plane joint claims
less uncertainty than the stereo lift of the same pixel.

    python3 probe_plane-cov-not-larger.py <rung-2-observe checkout root>

Synthetic data only, temp dirs only; connect is allowed to 127.0.0.1 only.
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
import camgeom as g  # noqa: E402
import detections  # noqa: E402
import fake_detector  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="probe-planecov-"))
try:
    scene = synth.Scene()
    pairs = [(5000.0, 5000.0 - 0.004), (5000.25, 5000.25 - 0.2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = work / "rows.jsonl"
    detections.write_rows(rows, [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, sway_px=0.0)
                                 for i, (t, _) in enumerate(pairs)])
    replay.replay(take, room, work / "out", detections_path=rows, log=lambda *a: None)
    fresh, stale = [json.loads(l) for l in (work / "out" / "observations.jsonl").read_text().splitlines()]
    names = list(synth.JOINTS)
    narrower = []
    for a, b in zip(fresh["joints_room"], stale["joints_room"]):
        if a["lift"] != "stereo" or not b["lift"].startswith("plane:"):
            continue
        ta, tb = sum(a["cov"][i][i] for i in range(3)), sum(b["cov"][i][i] for i in range(3))
        da, db = g.det3(a["cov"]), g.det3(b["cov"])
        smaller = tb < ta or db < da
        print(f"RESULT j{a['j']:2d} {names[a['j']]:15s} stereo trace {ta:.5f} det {da:.3e} | "
              f"{b['lift']:10s} trace {tb:.5f} det {db:.3e} {'<- plane NOT wider' if smaller else ''}")
        if smaller:
            narrower.append(names[a["j"]])
    print(f"RESULT freshness depth: fresh={fresh['freshness']['depth']} stale={stale['freshness']['depth']}")
finally:
    shutil.rmtree(work, ignore_errors=True)
if narrower:
    print(f"FAIL: plane fallback covariance is not larger than stereo for {narrower}")
    sys.exit(1)
print("PASS")
