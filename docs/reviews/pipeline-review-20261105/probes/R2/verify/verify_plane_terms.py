#!/usr/bin/env python3
"""verify_plane_terms: independent check of why the feet plane covariance is narrower than stereo.

Computes, for den-observe's own synthetic camera entry and scene with METHOD.md's
proposed values, the along-ray sigma of the stereo lift (z^2 sigma_disp / (f_left B))
and of the plane lift (sigma_h / |d_z|) for each joint, without running replay.

    python3 verify_plane_terms.py <rung-2-observe checkout root>

Synthetic data only; no files written; no network.
"""
import math
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import method as M  # noqa: E402
import observe  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402

m = M.load()
settings = observe.Settings(m)
cam = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=m.get("max_error_bound_m"))
scene = synth.Scene()
print(f"RESULT f_left {cam.f_left:.1f} px, f_rgb {cam.f_rgb:.1f} px, baseline {cam.baseline_m} m, "
      f"sigma_disp {settings.disparity_sigma_px}, error_bound {cam.error_bound_m}")
for name in synth.JOINTS:
    j = list(synth.JOINTS).index(name)
    u, v = scene.joint_rgb(name)
    truth = scene.joint_room(name)
    # depth along the rectified-left optical axis for the true point
    inv_r, inv_t = None, None
    R, t = cam.room_from_left
    # left = R^T (room - t)
    d = [truth[i] - t[i] for i in range(3)]
    left = [sum(R[r][c] * d[r] for r in range(3)) for c in range(3)]
    z = left[2]
    stereo_along = z * z * settings.disparity_sigma_px / (cam.f_left * cam.baseline_m)
    origin, direction = cam.rgb_ray_room(u, v)
    h, sh = settings.planes[observe.PLANE_GROUP[j]]
    plane_along = sh / abs(direction[2]) if direction[2] else float("inf")
    flag = "<- plane narrower along the ray" if plane_along < stereo_along else ""
    print(f"RESULT j{j:2d} {name:15s} z {z:5.2f} m  stereo sigma_along {stereo_along:.3f} m  "
          f"plane:{h:g} sigma_along {plane_along:.3f} m (d_z {direction[2]:+.3f}) {flag}")
