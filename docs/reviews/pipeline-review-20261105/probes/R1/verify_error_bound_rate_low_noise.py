#!/usr/bin/env python3
"""Verifier context for error-bound-over-gate-registered: at lower, careful-tape noise levels
(0.5 and 0.75 cm per axis) and with the README's default of four fit points versus six fit
points, how often does a gate-passing fit write error_bound_m > 0.05 (LOO dominated)?
Uses the tool's own rigid.fit on the synthetic room; seeded; contacts nothing.

    python3 -B verify_error_bound_rate_low_noise.py <root of the rung-1-register checkout>
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-register"))
import rigid  # noqa: E402
import synthetic  # noqa: E402

R, t = synthetic.camera_pose()
P = dict(synthetic.POINTS)
P.update({"p5": [3.8, 2.8, 0.0], "p6": [1.8, 3.2, 1.5]})
cam = {k: synthetic.to_camera(R, t, v) for k, v in P.items()}
for fit in (("p1", "p2", "p3", "p4"), ("p1", "p2", "p3", "p4", "p5", "p6")):
    for sigma in (0.005, 0.0075, 0.01):
        rng = random.Random(7)
        passed = over = 0
        for _ in range(2000):
            room = {k: [P[k][i] + rng.gauss(0, sigma) for i in range(3)] for k in P}
            res = rigid.fit([cam[k] for k in fit], [room[k] for k in fit])
            chk = [math.dist(rigid.apply(res["rotation"], res["translation"], cam[k]), room[k]) for k in ("c1", "c2")]
            if res["max_m"] <= 0.05 and max(chk) <= 0.05:
                passed += 1
                over += max([res["max_m"], res["loo_max_m"]] + chk) > 0.05
        print(f"RESULT {len(fit)} fit points, {sigma*100:.2f} cm/axis: {passed}/2000 pass; "
              f"{over} ({over/max(passed,1):.1%}) write error_bound_m > 0.05")
