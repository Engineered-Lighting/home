#!/usr/bin/env python3
"""probe_cov_along_ray: recompute a stereo joint's Sigma_room independently and compare with den-observe.

What it checks
    schemas.json Observation.joints_room says `cov` is A5's
        Sigma_room = R J Sigma_meas J^T R^T + Sigma_cal.
    This probe builds that from first principles for a constructed rectified-left
    pixel (u, v) at stereo depth Z, with its own code (not den-observe's):
        point   = ((u - cx) Z / fx, (v - cy) Z / fy, Z)            (pinhole deprojection)
        J       = d(point)/d(u, v, Z) = [[Z/fx, 0, (u-cx)/fx], [0, Z/fy, (v-cy)/fy], [0, 0, 1]]
        Sigma_meas = diag(su^2, sv^2, sZ^2), with
            su = pixel_sigma_px * fx_left / fx_rgb   (the RGB keypoint's 4 px, in left pixels)
            sv = pixel_sigma_px * fy_left / fy_rgb
            sZ = Z^2 * disparity_sigma_px / (fx_left * B)   (stereo depth error)
        Sigma_room = R J Sigma_meas J^T R^T + error_bound_m^2 I
    and compares it with the covariance den-observe writes for that point
    (observe.stereo_point + observe._finish, exactly what lift_joint writes).
    The tool's METHOD.md mapping (sigma_px -> across = sigma_px Z / f_rgb,
    sigma_disp -> along = Z^2 sigma_disp / (f_left B)) is the same as this
    Jacobian on the optical axis, so the on-axis case must match; off axis the
    depth error moves the point along the ray by |r| dZ with r = (x, y, 1),
    so the along-ray standard deviation is |r| sZ, not sZ.

How to run
    python3 probe_cov_along_ray.py <branch checkout root>
    e.g. python3 probe_cov_along_ray.py .../scratchpad/wt/rung-2-observe

Synthetic numbers only (synth.py's camera entry and METHOD.md's proposed
values). Nothing is written; nothing connects anywhere.
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


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def tr(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def along(cov, d):
    return sum(d[i] * cov[i][j] * d[j] for i in range(3) for j in range(3))


meth = method_file.load(root / "tools" / "den-observe" / "METHOD.md")
settings = observe.Settings(meth)
camera = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
fx, fy, cx, cy = (synth.LEFT_K[k] for k in ("fx", "fy", "cx", "cy"))
frgb_x, frgb_y = synth.RGB_K["fx"], synth.RGB_K["fy"]
B, eb = synth.BASELINE_M, camera.error_bound_m
spx, sdisp = settings.pixel_sigma_px, settings.disparity_sigma_px
R = camera.room_from_left[0]  # used only to rotate my camera-frame result into the room frame
print(f"INFO settings: pixel_sigma_px={spx} disparity_sigma_px={sdisp} fx_left={fx} fx_rgb={frgb_x} B={B} "
      f"error_bound_m={eb}")

worst = 0.0
fails = 0
for (u, v, Z) in ((320.0, 200.0, 3.0), (320.0, 200.0, 5.0), (600.0, 200.0, 3.0), (20.0, 20.0, 3.0),
                  (620.0, 380.0, 4.0), (100.0, 300.0, 2.0)):
    point = [(u - cx) * Z / fx, (v - cy) * Z / fy, Z]
    J = [[Z / fx, 0.0, (u - cx) / fx], [0.0, Z / fy, (v - cy) / fy], [0.0, 0.0, 1.0]]
    su, sv = spx * fx / frgb_x, spx * fy / frgb_y
    sZ = Z * Z * sdisp / (fx * B)
    meas = [[su * su, 0, 0], [0, sv * sv, 0], [0, 0, sZ * sZ]]
    cam = matmul(matmul(J, meas), tr(J))
    room = matmul(matmul(R, cam), tr(R))
    mine = [[room[i][j] + (eb * eb if i == j else 0.0) for j in range(3)] for i in range(3)]

    xyz, independent, common = observe.stereo_point(camera, settings, point)
    entry, _ = observe._finish({"j": 5}, camera, xyz, independent, common, "supported", "stereo")
    tool = entry["cov"]

    ray = [point[0] / Z, point[1] / Z, 1.0]
    n = math.sqrt(sum(c * c for c in ray))
    d_room = [sum(R[i][k] * ray[k] / n for k in range(3)) for i in range(3)]
    a_mine, a_tool = math.sqrt(along(mine, d_room)), math.sqrt(along(tool, d_room))
    ratio = a_tool / a_mine
    maxdiff = max(abs(mine[i][j] - tool[i][j]) for i in range(3) for j in range(3))
    rel = maxdiff / max(abs(mine[i][i]) for i in range(3))
    ok = rel < 1e-3
    fails += not ok
    worst = max(worst, rel)
    print(f"{'PASS' if ok else 'FAIL'} pixel=({u:.0f},{v:.0f}) Z={Z} |r|={n:.3f}: along-ray sigma "
          f"independent {a_mine:.4f} m, den-observe {a_tool:.4f} m (ratio {ratio:.3f}); "
          f"max |dSigma| {maxdiff:.2e} m^2 ({100 * rel:.1f}% of the largest variance)")
    # the depth-only part of the along-ray sigma (without Sigma_cal)
    if not ok:
        dep_mine = math.sqrt(max(0.0, along(mine, d_room) - eb * eb))
        dep_tool = math.sqrt(max(0.0, along(tool, d_room) - eb * eb))
        print(f"      without Sigma_cal: independent {dep_mine:.4f} m vs den-observe {dep_tool:.4f} m "
              f"(den-observe {100 * (1 - dep_tool / dep_mine):.0f}% smaller)")
print(f"RESULT {'MISMATCH' if fails else 'MATCH'}: {fails} of 6 constructed points differ by more than 0.1% "
      f"(worst {100 * worst:.1f}% of the largest variance); on-axis points must match")
