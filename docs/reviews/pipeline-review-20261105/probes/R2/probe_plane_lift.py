#!/usr/bin/env python3
"""probe_plane_lift: the declared-plane lift, recomputed independently.

What it checks
    For RGB pixels of the synthetic camera (synth.py's entry: pose, RGB
    intrinsics and 8-term distortion, RGB-from-rectified-left), the probe takes
    den-observe's plane point for each joint group (observe.plane_point) and
    checks it with its own forward model, not den-observe's code:
      1. the point lies on the declared plane: z == h of the joint's group
         (head 0-4, arms 5-10, legs 11-14, feet 15-16, METHOD.md proposals);
      2. projecting the point back (room -> rectified-left -> RGB, pinhole,
         then the OpenCV rational distortion written out here) gives the
         original pixel (to 1e-4 px);
      3. it lies in front of the RGB camera;
      4. its covariance is sigma_across^2 (I - d d^T) + (sigma_h/|d_z|)^2 d d^T
         + error_bound^2 I, with d the unit ray, sigma_across = 4 px * range / f_rgb;
      5. the lift string is plane:<h>.
    Also a ray that cannot meet a plane below the camera (pixel above the
    horizon for the feet plane) must give no point.

How to run
    python3 probe_plane_lift.py <branch checkout root>

Synthetic numbers only; nothing is written.
"""
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import method as method_file  # noqa: E402
import observe  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402

meth = method_file.load(root / "tools" / "den-observe" / "METHOD.md")
settings = observe.Settings(meth)
camera = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)

# --- my own forward model -------------------------------------------------
(Rr, tr_) = synth.camera_pose()                 # room_from_left
Rl, tl = synth.RGB_FROM_LEFT                    # rgb_from_left
K = synth.RGB_K
k1, k2, p1, p2, k3, k4, k5, k6 = synth.RGB_DISTORTION


def room_to_left(p):
    q = [p[i] - tr_[i] for i in range(3)]
    return [sum(Rr[k][i] * q[k] for k in range(3)) for i in range(3)]  # R^T (p - t)


def left_to_rgb(p):
    return [sum(Rl[i][k] * p[k] for k in range(3)) + tl[i] for i in range(3)]


def pixel(p_room):
    c = left_to_rgb(room_to_left(p_room))
    x, y = c[0] / c[2], c[1] / c[2]
    r2 = x * x + y * y
    rad = (1 + k1 * r2 + k2 * r2 ** 2 + k3 * r2 ** 3) / (1 + k4 * r2 + k5 * r2 ** 2 + k6 * r2 ** 3)
    xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return K["fx"] * xd + K["cx"], K["fy"] * yd + K["cy"], c[2]


# RGB origin in the room: left point o_l with rgb_from_left(o_l) = 0 -> o_l = -Rl^T tl
o_left = [-sum(Rl[k][i] * tl[k] for k in range(3)) for i in range(3)]
o_room = [sum(Rr[i][k] * o_left[k] for k in range(3)) + tr_[i] for i in range(3)]

groups = {0: "head", 6: "arms", 12: "legs", 16: "feet"}
fails = 0
for (u, v) in ((320.0, 180.0), (100.0, 300.0), (600.0, 340.0), (320.0, 359.0), (40.0, 60.0)):
    for j, name in groups.items():
        h, sh = settings.planes[name]
        found, why = observe.plane_point(camera, settings, j, u, v)
        if found is None:
            print(f"INFO pixel ({u:.0f},{v:.0f}) joint {j} ({name}, h={h}): no point ({why})")
            continue
        p, indep, common, how = found
        pu, pv, depth = pixel(p)
        reproj = math.hypot(pu - u, pv - v)
        d = [p[i] - o_room[i] for i in range(3)]
        s = math.sqrt(sum(c * c for c in d))
        d = [c / s for c in d]
        across = settings.pixel_sigma_px * s / camera.f_rgb
        al = sh / abs(d[2])
        eb = camera.error_bound_m
        mine = [[across ** 2 * ((i == k) - d[i] * d[k]) + al ** 2 * d[i] * d[k] + (eb ** 2 if i == k else 0)
                 for k in range(3)] for i in range(3)]
        tool = [[indep[i][k] + common[i][k] + (eb ** 2 if i == k else 0) for k in range(3)] for i in range(3)]
        dcov = max(abs(mine[i][k] - tool[i][k]) for i in range(3) for k in range(3))
        ok = (abs(p[2] - h) < 1e-9 and reproj < 1e-4 and depth > 0 and dcov < 1e-9
              and how == f"plane:{h:g}")
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} pixel ({u:.0f},{v:.0f}) joint {j} ({name}): z={p[2]:.6f} vs h={h}, "
              f"reprojection {reproj:.2e} px, range {s:.3f} m, along-ray sigma {al:.3f} m, |dcov| {dcov:.1e}, {how}")
# a pixel looking above the horizon cannot meet the feet plane in front of the camera
top_found, why = observe.plane_point(camera, settings, 16, 320.0, 0.0)
_, _, _ = pixel([0.0, 10.0, 2.2 + 10.0 * math.tan(math.radians(1))])
ok = top_found is None
print(f"{'PASS' if ok else 'FAIL'} pixel (320,0) feet: {why if top_found is None else 'a point'} "
      f"(the ray at the top row points {'up' if top_found is None else '?'})")
fails += not ok
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} plane-lift checks wrong")
