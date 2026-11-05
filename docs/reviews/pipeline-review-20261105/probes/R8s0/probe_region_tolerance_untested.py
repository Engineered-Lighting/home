#!/usr/bin/env python3
"""The region test's tolerance P is never tested near its value: a mild local
tone map that P = 5% must fail passes once P is loosened, and the suite does
not notice.

    python3 probe_region_tolerance_untested.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. Synthetic data only, no network.

The suite's only local-tone-map case (test_a_local_tone_map_fails_the_region_test
...) puts the left half of the view through s_curve(0.8) and the rest through
s_curve(1.3), which moves the region curves by more than 100%, so any P up to
100% still fails it. This probe uses a mild local tone map instead (left half
through s_curve(1.2), right half s_curve(1.3), as an ISP's local tone mapping
might lift one side by a few percent to tens of percent) and reports the
verdict at METHOD-V1's P = 5% and at P = 50% (the surviving mutant M2 of
mutate_den_light.py, which multiplies the tolerance by 10).
"""
import importlib.util
import math
import random
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
sys.path.insert(0, str(ROOT / "tools/den-light"))
import meter  # noqa: E402
import sweep  # noqa: E402

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("residual_probe", HERE / "probe_residual_gate_untested.py")
helpers = importlib.util.module_from_spec(spec)
sys.argv = sys.argv[:2]
spec.loader.exec_module(helpers)


def s_curve(k):
    return lambda y: 255 * y ** k / (y ** k + 1)


def steps_with(method, left_k, right_k, seed=5):
    rng = random.Random(seed)
    step = method["pixel_step"]
    cols = 640 // step
    n = len(meter.sample_points(step))
    energy = [math.exp(rng.uniform(math.log(0.05), math.log(10))) for _ in range(n)]
    steps, number = [], 0
    for hold in sweep.exposure_plan(8000, 100, method):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(method["frames_per_step"]):
            number += 1
            rgb = ([], [], [])
            for i, e in enumerate(energy):
                curve = s_curve(left_k) if i % cols < cols // 2 else s_curve(right_k)
                for c, gain in enumerate((1.0, 0.9, 0.75)):
                    rgb[c].append(min(255, max(0, round(curve(x / 8 * e * gain) + rng.gauss(0, 0.7)))))
            report = {"exposure_us": hold["exposure_us"], "iso": hold["iso"], "kelvin": hold["white_balance_kelvin"]}
            frames.append({"n": number, "report": report, "nv12": helpers.nv12(meter, rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def main():
    base = meter.load_method()
    method = meter.Method(dict(base.values, pixel_step=8, frames_per_step=3, fit_iterations=15), (), "", "")
    steps = steps_with(method, 1.2, 1.3)
    result = meter.check(steps, method)
    devs = sorted((e["deviation_percent"] for e in result["region_test"]["regions"] if e["tested"]), reverse=True)
    print(f"RESULT mild local tone map at P = {method['region_tolerance_percent']}%: verdict {result['verdict']}, "
          f"reasons {result['reasons']}, residual { {c: round(v, 2) for c, v in result['residual']['codes'].items()} }, "
          f"largest region deviations {[round(d, 1) for d in devs[:4]]}%")
    loose = meter.Method(dict(method.values, region_tolerance_percent=10 * method["region_tolerance_percent"]),
                         (), "", "")
    result2 = meter.check(steps, loose)
    print(f"RESULT same frames at P = {loose['region_tolerance_percent']}% (mutant M2): verdict {result2['verdict']}, "
          f"reasons {result2['reasons']}")
    shown = result["verdict"] == "fail" and result2["verdict"] == "pass"
    print(("FAIL (defect shown)" if shown else "PASS")
          + ": a local tone map the 5% region test fails passes when P is loosened, and no test pins P")


if __name__ == "__main__":
    main()
