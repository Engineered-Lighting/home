#!/usr/bin/env python3
"""verify_cov-along-ray-missing-ray-length: isolate den-observe's stereo depth term and compare with |r| sigma_Z.

What it checks
    For a rectified-left pixel (u, v) at depth Z the stereo point is P = Z r with
    r = ((u-cx)/fx, (v-cy)/fy, 1), so a depth error dZ moves P by r dZ: the
    along-ray standard deviation of the depth term is |r| sigma_Z, where
    sigma_Z = Z^2 sigma_disp / (f_left B). The probe sets pixel_sigma_px to 0
    (so only the depth term is left), calls observe.stereo_point, rotates the
    result back into the camera frame and reads its along-ray sigma. It prints
    the ratio den-observe / (|r| sigma_Z): 1.000 means the Jacobian is honoured.

How to run
    python3 -B verify_cov-along-ray-missing-ray-length.py <branch checkout root>

Synthetic camera (synth.py) and METHOD.md's proposed disparity_sigma_px; nothing
is written, nothing connects anywhere.
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

settings = observe.Settings(method_file.load(root / "tools" / "den-observe" / "METHOD.md"))
settings.pixel_sigma_px = 0.0
camera = roommodel.Camera(synth.room_model(), "den-oak@2026-11-12", max_error_bound_m=0.05)
fx, fy, cx, cy = (synth.LEFT_K[k] for k in ("fx", "fy", "cx", "cy"))
R = camera.room_from_left[0]
bad = 0
for u, v, Z in ((320.0, 200.0, 3.0), (600.0, 200.0, 3.0), (20.0, 20.0, 3.0), (620.0, 380.0, 4.0)):
    r = [(u - cx) / fx, (v - cy) / fy, 1.0]
    n = math.sqrt(sum(c * c for c in r))
    point = [c * Z for c in r]
    _, independent, _ = observe.stereo_point(camera, settings, point)
    cam = g.cov_rotate(g.transpose(R), independent)  # back into the rectified-left frame
    d = [c / n for c in r]
    along = math.sqrt(sum(d[i] * cam[i][j] * d[j] for i in range(3) for j in range(3)))
    sigma_z = Z * Z * settings.disparity_sigma_px / (camera.f_left * camera.baseline_m)
    expected = n * sigma_z
    ratio = along / expected
    ok = abs(ratio - 1.0) < 1e-3
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'} pixel ({u:.0f},{v:.0f}) Z={Z}: |r|={n:.4f} sigma_Z={sigma_z:.4f} m; "
          f"den-observe along-ray {along:.4f} m, Jacobian |r| sigma_Z {expected:.4f} m, ratio {ratio:.3f} "
          f"(1/|r| = {1 / n:.3f})")
print(f"RESULT {'FAIL' if bad else 'PASS'}: {bad} of 4 points where the depth term is not |r| sigma_Z along the ray")
