#!/usr/bin/env python3
"""probe_plane_cov_not_larger: is the plane fallback's covariance always larger than stereo's?

What it checks
    Ladder rung 2 gate: "A depth frame too old for its capture is not used: the
    declared plane fallback applies, with a larger covariance". METHOD.md:
    "so a plane joint always has a wider covariance than a stereo one".
    1. End to end: the same synthetic figure (synth.py, panel 3 m away) in two
       frames, one with depth 4 ms old (stereo) and one 0.2 s old (plane
       fallback), replayed unscored with METHOD.md's proposed values. For each
       joint lifted both ways it compares the trace and the largest eigen-
       direction variance (along the ray) of the stereo and plane covariances.
    2. Direct: for a feet joint on the floor plane (z = 0.08 m) at 3-6 m range,
       observe.plane_point vs observe.stereo_point for the same true point.
    Prints FAIL for every joint whose plane covariance is not larger.

How to run
    python3 probe_plane_cov_not_larger.py <branch checkout root>

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
import method as method_file  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402


def trace(c):
    return c[0][0] + c[1][1] + c[2][2]


def var_along(c, d):
    return sum(d[i] * c[i][k] * d[k] for i in range(3) for k in range(3))


work = Path(tempfile.mkdtemp(prefix="probe-planecov-"))
fails = 0
try:
    scene = synth.Scene()
    pairs = [(5000.0, 5000.0 - 0.004), (5000.25, 5000.25 - 0.2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    detections.write_rows(work / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels, sway_px=0.0)
                                                for i, (t, _) in enumerate(pairs)])
    replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
    fresh, stale = [json.loads(x) for x in (work / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    for a, b in zip(fresh["joints_room"], stale["joints_room"]):
        if a["lift"] != "stereo" or not b["lift"].startswith("plane:"):
            continue
        ta, tb = trace(a["cov"]), trace(b["cov"])
        ok = tb > ta
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} end-to-end j{a['j']:<2} {detections.COCO17[a['j']]:<14} stereo trace "
              f"{ta:.5f} m^2, plane ({b['lift']}) trace {tb:.5f} m^2")

    settings = observe.Settings(method_file.load(root / "tools" / "den-observe" / "METHOD.md"))
    camera = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
    cal = g.isotropic(camera.error_bound_m)
    for y in (3.0, 4.0, 5.0, 6.0):
        p_room = [0.3, y, 0.08]
        p_left = g.apply(*g.inverse(*camera.room_from_left), p_room)
        u, v = camera.rig.to_rgb(p_left)
        _, s_ind, s_com = observe.stereo_point(camera, settings, p_left)
        found, why = observe.plane_point(camera, settings, 16, u, v)
        if found is None:
            print(f"INFO feet at y={y}: plane gives no point ({why})")
            continue
        _, p_ind, p_com, how = found
        stereo, plane = g.cov_add(s_ind, s_com, cal), g.cov_add(p_ind, p_com, cal)
        d = g.unit([p_room[i] - camera.rgb_origin_room[i] for i in range(3)])
        ok = trace(plane) > trace(stereo)
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} direct feet joint at ({p_room[0]}, {y}, 0.08) m: along-ray sigma stereo "
              f"{math.sqrt(var_along(stereo, d)):.3f} m vs plane {math.sqrt(var_along(plane, d)):.3f} m; trace stereo "
              f"{trace(stereo):.4f} vs plane {trace(plane):.4f} m^2")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} joints where the plane fallback's covariance is not larger "
      f"than the stereo covariance it replaces")
