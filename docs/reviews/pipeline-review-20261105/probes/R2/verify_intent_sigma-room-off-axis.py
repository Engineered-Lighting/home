#!/usr/bin/env python3
"""verify_intent_sigma-room-off-axis: Monte Carlo check of den-observe's stereo covariance off-axis.

Stereo noise is a disparity error, so it moves the rectified-left point along its
left-camera ray with the left z changing by sigma_z = z^2 sigma_disp / (f_left B).
For points on-axis and at the RGB frame corner (synthetic camera from synth.py,
proposed disparity_sigma_px), this draws 200,000 noisy depths, measures the empirical
along-ray variance of the point, and compares it with the along-ray variance of
observe.stereo_point's depth term (pixel_sigma_px set to 0 so only depth noise counts).

    python3 -B verify_intent_sigma-room-off-axis.py <rung-2-observe checkout root>

Synthetic geometry only; no files, no network.
"""
import math
import random
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import camgeom as g  # noqa: E402
import method as method_file  # noqa: E402
import observe  # noqa: E402
import roommodel  # noqa: E402
import synth  # noqa: E402

settings = observe.Settings(method_file.load())
settings.pixel_sigma_px = 0.0  # isolate the depth term
cam = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
rng = random.Random(1)
for label, (u, v, z_rgb) in (("on-axis RGB (320,180) 3 m", (320.0, 180.0, 3.0)),
                             ("corner RGB (620,340) 3 m", (620.0, 340.0, 3.0))):
    ray = cam.rig.rgb_ray(u, v)
    p = g.apply(*cam.rig.left_from_rgb, [ray[0] * z_rgb, ray[1] * z_rgb, z_rgb])
    z = p[2]
    sigma_z = z * z * settings.disparity_sigma_px / (cam.f_left * cam.baseline_m)
    d = g.unit(p)
    samples = []
    for _ in range(200000):
        zn = z + rng.gauss(0.0, sigma_z)
        q = [c * zn / z for c in p]  # same left pixel, new depth
        samples.append(sum((q[i] - p[i]) * d[i] for i in range(3)))
    empirical = sum(s * s for s in samples) / len(samples)
    _, independent, _ = observe.stereo_point(cam, settings, p)
    d_room = g.mat_vec(cam.room_from_left[0], d)
    tool = sum(d_room[i] * independent[i][j] * d_room[j] for i in range(3) for j in range(3))
    print(f"RESULT {label}: along-ray variance empirical {empirical:.6f}, tool {tool:.6f}, "
          f"ratio tool/empirical {tool / empirical:.3f}")
