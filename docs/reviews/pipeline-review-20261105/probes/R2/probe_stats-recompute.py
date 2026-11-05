#!/usr/bin/env python3
"""probe_stats-recompute: recompute rung 2's numbers independently of den-observe's code paths.

1. Sigma_room = R J Sigma_meas J^T R^T + Sigma_cal for a constructed stereo point,
   J the Jacobian of (u, v, z) -> camera point (u, v in RGB pixels with
   sigma_px, z with sigma_z = z^2 sigma_disp / (f_left B)), against
   observe.stereo_point + Sigma_cal, on-axis and at an off-axis pixel.
2. The 67 ms depth boundary: observe.depth_state at 66.9, 67.0 and 67.1 ms.
3. The torso reference point: mean of supported shoulders and hips.
4. Plane lift at a declared height: z of the lifted point equals h; unsupported null.
Prints RESULT lines with tool and hand values; FAIL where they disagree by >5 %.

    python3 probe_stats-recompute.py <rung-2-observe checkout root>

Synthetic geometry only; no files, no network.
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
cam = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
R = cam.room_from_left[0]
fails = 0


def hand_sigma(point_left):
    x, y, z = point_left
    xb, yb = x / z, y / z
    sz = z * z * settings.disparity_sigma_px / (cam.f_left * cam.baseline_m)
    fx_rgb, fy_rgb = cam.rig.rgb_k[0], cam.rig.rgb_k[1]
    J = [[z / fx_rgb, 0.0, xb], [0.0, z / fy_rgb, yb], [0.0, 0.0, 1.0]]
    S = [[settings.pixel_sigma_px ** 2, 0, 0], [0, settings.pixel_sigma_px ** 2, 0], [0, 0, sz ** 2]]
    cam_cov = g.mat_mul(g.mat_mul(J, S), g.transpose(J))
    return g.cov_add(g.cov_rotate(R, cam_cov), g.isotropic(cam.error_bound_m))


def along(cov, d):
    u = g.unit(d)
    return sum(u[i] * cov[i][j] * u[j] for i in range(3) for j in range(3))


def left_point_of_rgb(u, v, z_rgb):
    """The rectified-left point on RGB pixel (u, v)'s ray at RGB depth z_rgb (inside the RGB frame)."""
    ray = cam.rig.rgb_ray(u, v)
    return g.apply(*cam.rig.left_from_rgb, [ray[0] * z_rgb, ray[1] * z_rgb, z_rgb])


for label, (ur, vr, z) in (("RGB pixel (320,180) z=3.0", (320.0, 180.0, 3.0)),
                           ("RGB pixel (620,340) z=3.0 (frame corner)", (620.0, 340.0, 3.0)),
                           ("RGB pixel (620,340) z=4.5 (frame corner)", (620.0, 340.0, 4.5)),
                           ("RGB pixel (480,180) z=3.0 (half way to the edge)", (480.0, 180.0, 3.0))):
    p = left_point_of_rgb(ur, vr, z)
    _, independent, common = observe.stereo_point(cam, settings, p)
    tool = g.cov_add(independent, common, g.isotropic(cam.error_bound_m))
    hand = hand_sigma(p)
    ray_room = g.mat_vec(R, p)
    ta, ha = along(tool, ray_room), along(hand, ray_room)
    tt, ht = sum(tool[i][i] for i in range(3)), sum(hand[i][i] for i in range(3))
    ok = abs(ta / ha - 1) <= 0.05 and abs(tt / ht - 1) <= 0.05
    fails += not ok
    print(f"RESULT Sigma_room {label}: along-ray var tool {ta:.6f} hand {ha:.6f} (ratio {ta / ha:.3f}); "
          f"trace tool {tt:.6f} hand {ht:.6f} (ratio {tt / ht:.3f}) {'MATCH' if ok else 'MISMATCH'}")

# 2. depth boundary
for gap_ms in (66.9, 67.0, 67.1):
    t_rgb = 5000.25
    row = {"frame": {"capture_ts": t_rgb}}
    meta = {"t_depth": t_rgb - gap_ms / 1000.0}

    class D:
        mm = [1]
    state, gap = observe.depth_state(row, meta, D(), settings)
    want = "used" if gap_ms <= 67.0 else "too_old"
    fails += state != want
    print(f"RESULT depth gap {gap_ms} ms -> tool {state} (gap {gap!r}); schema 'more than 67 ms is not used' -> {want} "
          f"{'MATCH' if state == want else 'MISMATCH'}")

# 3. torso reference point (stereo lifts for 5, 6, 11; 12 unsupported)
lifted = {5: ([1.0, 2.0, 1.4], g.isotropic(0.01), g.isotropic(0.0)),
          6: ([1.4, 2.0, 1.4], g.isotropic(0.01), g.isotropic(0.0)),
          11: ([1.1, 2.1, 0.9], g.isotropic(0.01), g.isotropic(0.0))}
joints = [{"lift": "none"} for _ in range(17)]
for j in lifted:
    joints[j] = {"lift": "stereo"}
xyz, cov = observe.torso_point(cam, lifted, joints)
hand = [sum(lifted[j][0][i] for j in lifted) / 3 for i in range(3)]
var_hand = 3 * 0.01 ** 2 / 9 + cam.error_bound_m ** 2
ok = math.dist(xyz, hand) < 1e-12 and abs(cov[0][0] - var_hand) < 1e-12
fails += not ok
print(f"RESULT torso point tool {xyz} hand {hand}; var_x tool {cov[0][0]:.8f} hand {var_hand:.8f} {'MATCH' if ok else 'MISMATCH'}")

# 4. plane lift at a declared height
u, v = 300.0, 330.0
found, why = observe.plane_point(cam, settings, 15, u, v)
h = settings.planes["feet"][0]
ok = found is not None and abs(found[0][2] - h) < 1e-9 and found[3] == f"plane:{h:g}"
fails += not ok
print(f"RESULT plane lift ankle pixel ({u},{v}): z tool {found[0][2] if found else None} declared {h}; lift {found[3] if found else why} "
      f"{'MATCH' if ok else 'MISMATCH'}")
entry, parts = observe.lift_joint(cam, settings, None, False, 15, [u, v, 0.1], cam.rig.rgb_size)
ok = entry == {"j": 15, "xyz": None, "cov": None, "status": "unsupported", "lift": "none"} and parts is None
fails += not ok
print(f"RESULT low-score joint -> {entry} {'MATCH' if ok else 'MISMATCH'}")
print("FAIL" if fails else "PASS", f"({fails} mismatches)")
sys.exit(1 if fails else 0)
