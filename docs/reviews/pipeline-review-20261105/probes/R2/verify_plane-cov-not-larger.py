#!/usr/bin/env python3
"""verify_plane-cov-not-larger: at what ranges is a plane-lifted joint narrower than a stereo one?

Independent re-check of finding plane-cov-not-larger, without replay: uses
den-observe's own observe.stereo_point / observe.plane_point / Camera with
synth's camera entry (2.2 m up, 20 deg down) and METHOD.md's proposed values
(an unscored run's settings). For a joint of each group at a horizontal range
Y in front of the camera, it lifts the same RGB pixel by stereo (the true
point) and by the group's plane, adds Sigma_cal to both, and reports the
traces and the smallest eigenvalue of (plane - stereo). A plane covariance is
"larger" in the matrix sense only if that eigenvalue is >= 0.

    python3 verify_plane-cov-not-larger.py <rung-2-observe root>

Prints RESULT lines and FAIL if some realistic range gives a plane covariance
that is not larger. Pure computation; no files, no network.
"""
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import camgeom as g  # noqa: E402
import method as method_file  # noqa: E402
import observe  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402

m = method_file.load()
settings = observe.Settings(m)
camera = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=m.get("max_error_bound_m"))
scene = synth.Scene()
k_rgb, d_rgb = g.intrinsics(synth.RGB_K), g.distortion(synth.RGB_DISTORTION)


def eig_min(a):
    """Smallest eigenvalue of a symmetric 3x3 (cyclic Jacobi)."""
    a = [row[:] for row in a]
    for _ in range(60):
        off = sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j)
        if off < 1e-30:
            break
        for p in range(3):
            for q in range(p + 1, 3):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(3):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(3):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
    return min(a[i][i] for i in range(3))


cal = g.isotropic(camera.error_bound_m)
bad = []
for name, j, height in (("ankle", 15, 0.15), ("knee", 13, 0.52), ("hip", 11, 0.95), ("wrist", 9, 0.88)):
    for y in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5):
        room_pt = [0.10, y, height]
        left = g.apply(*scene.left_from_room, room_pt)
        u, v = g.project(g.apply(*synth.RGB_FROM_LEFT, left), k_rgb, d_rgb)
        if not (0 <= u <= 640 and 0 <= v <= 360):
            print(f"RESULT {name:5s} y {y:.1f} m: pixel ({u:.0f},{v:.0f}) outside the RGB frame; skipped")
            continue
        _, s_ind, s_com = observe.stereo_point(camera, settings, left)
        stereo = g.cov_add(s_ind, s_com, cal)
        found, why = observe.plane_point(camera, settings, j, u, v)
        if found is None:
            print(f"RESULT {name:5s} y {y:.1f} m: plane gives {why}")
            continue
        _, p_ind, p_com, how = found
        plane = g.cov_add(p_ind, p_com, cal)
        diff = [[plane[a][b] - stereo[a][b] for b in range(3)] for a in range(3)]
        lam = eig_min(diff)
        ts, tp = sum(stereo[i][i] for i in range(3)), sum(plane[i][i] for i in range(3))
        flag = "" if lam >= -1e-12 else "  <- plane NOT larger"
        print(f"RESULT {name:5s} y {y:.1f} m (left z {left[2]:.2f} m) {how:9s} trace stereo {ts:.5f} "
              f"plane {tp:.5f}; min eig(plane-stereo) {lam:+.2e}{flag}")
        if flag:
            bad.append((name, y))
if bad:
    print(f"FAIL: plane covariance not larger than stereo at {bad}")
    sys.exit(1)
print("PASS")
