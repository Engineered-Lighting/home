#!/usr/bin/env python3
"""verify_sigma-room-off-axis: is den-observe's stereo depth variance along the viewing ray right off-axis?

Independent of any hand-written Jacobian: for a rectified-left pixel (u, v) at depth z, the
stereo measurement is a disparity d = f_left*B/z with sigma_disp. The point is
camgeom.deproject(u, v, f_left*B/d, K_left) (the tool's own deprojection). Its derivative with
respect to d is taken by central finite differences; the along-ray standard deviation of the
depth term is |dP/dd| * sigma_disp. That is compared with the depth-only along-ray standard
deviation den-observe uses in observe.stereo_point (pixel term set to 0, no Sigma_cal).

    python3 -B verify_sigma-room-off-axis.py <rung-2-observe checkout root>

Synthetic geometry only (synth's camera); no files, no network.
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

settings = observe.Settings(method_file.load())
settings.pixel_sigma_px = 0.0  # isolate the depth term
cam = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
K = cam.rig.left_k
fB = cam.f_left * cam.baseline_m
R = cam.room_from_left[0]
worst = 1.0
for (u, v, z) in ((320.0, 200.0, 3.0), (480.0, 200.0, 3.0), (630.0, 390.0, 3.0), (630.0, 390.0, 4.5)):
    d, h = fB / z, 1e-6
    p = g.deproject(u, v, z, K)
    dp = [(a - b) / (2 * h) for a, b in zip(g.deproject(u, v, fB / (d + h), K), g.deproject(u, v, fB / (d - h), K))]
    sigma_true = math.sqrt(sum(c * c for c in dp)) * settings.disparity_sigma_px
    _, independent, _ = observe.stereo_point(cam, settings, p)
    ray = g.unit(g.mat_vec(R, p))
    var_tool = sum(ray[i] * independent[i][j] * ray[j] for i in range(3) for j in range(3))
    ratio = var_tool / sigma_true ** 2
    worst = min(worst, ratio)
    print(f"RESULT left pixel ({u:.0f},{v:.0f}) z={z}: |P|/z={math.dist(p, [0, 0, 0]) / z:.3f}; along-ray depth sd "
          f"tool {math.sqrt(var_tool):.4f} m, finite-difference {sigma_true:.4f} m; variance ratio {ratio:.3f}")
print(f"RESULT smallest variance ratio tool/true {worst:.3f}")
print("FAIL: tool under-states off-axis along-ray depth variance" if worst < 0.95 else "PASS")
