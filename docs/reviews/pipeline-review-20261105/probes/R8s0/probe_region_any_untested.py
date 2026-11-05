#!/usr/bin/env python3
"""The region test's "every tested region must pass" rule is never tested: a
local tone map confined to one region of the 4x4 grid fails the region test,
but with the rule weakened to "any region passes" (surviving mutant M4 of
mutate_den_light.py) the same sweep is a Stage 0 PASS, and the suite does not
notice.

    python3 probe_region_any_untested.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. Synthetic data only, temp dirs
only, no network.

Why the suite misses it: its only local-tone-map case sends half the view
through a very different curve, so the global fit sits between the two and
every one of the 16 regions fails; "all" and "any" then agree.
"""
import importlib.util
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("residual_probe", HERE / "probe_residual_gate_untested.py")
helpers = importlib.util.module_from_spec(spec)
sys.argv = sys.argv[:2]
spec.loader.exec_module(helpers)


def s_curve(k):
    return lambda y: 255 * y ** k / (y ** k + 1)


def steps_with(meter, sweep, method, region_k, other_k, seed=5):
    rng = random.Random(seed)
    step = method["pixel_step"]
    points = meter.sample_points(step)
    owner = meter.sample_regions(step, 4, 4)
    energy = [math.exp(rng.uniform(math.log(0.05), math.log(10))) for _ in points]
    steps, number = [], 0
    for hold in sweep.exposure_plan(8000, 100, method):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(method["frames_per_step"]):
            number += 1
            rgb = ([], [], [])
            for i, e in enumerate(energy):
                curve = s_curve(region_k) if owner[i] == 5 else s_curve(other_k)  # one inner region
                for c, gain in enumerate((1.0, 0.9, 0.75)):
                    rgb[c].append(min(255, max(0, round(curve(x / 8 * e * gain) + rng.gauss(0, 0.7)))))
            report = {"exposure_us": hold["exposure_us"], "iso": hold["iso"], "kelvin": hold["white_balance_kelvin"]}
            frames.append({"n": number, "report": report, "nv12": helpers.nv12(meter, rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def run(meter, sweep):
    base = meter.load_method()
    method = meter.Method(dict(base.values, pixel_step=8, frames_per_step=3, fit_iterations=15), (), "", "")
    steps = steps_with(meter, sweep, method, 1.0, 1.3)
    return meter.check(steps, method)


def main():
    meter, sweep = helpers.load(ROOT / "tools/den-light")
    result = run(meter, sweep)
    failing = [(e["region"], e["channel"], round(e["deviation_percent"], 1))
               for e in result["region_test"]["regions"] if not e["pass"]]
    print(f"RESULT one-region local tone map: verdict {result['verdict']}, reasons {result['reasons']}, "
          f"residual { {c: round(v, 2) for c, v in result['residual']['codes'].items()} }, failing regions {failing}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "den-light"
        shutil.copytree(ROOT / "tools/den-light", copy)
        src = (copy / "meter.py").read_text()
        old = 'return {"pass": all(e["pass"] for e in out)'
        assert src.count(old) == 1
        (copy / "meter.py").write_text(src.replace(old, 'return {"pass": any(e["pass"] for e in out)'))
        meter2, sweep2 = helpers.load(copy)
        mutated = run(meter2, sweep2)
    print(f"RESULT mutant M4 (any region passes) on the same frames: verdict {mutated['verdict']}, "
          f"reasons {mutated['reasons']}")
    shown = result["verdict"] == "fail" and result["reasons"] == ["region test"] and mutated["verdict"] == "pass"
    print(("FAIL (defect shown)" if shown else "PASS")
          + ": the one-region tone map fails the region test, and passes once 'every region' becomes 'any region'")


if __name__ == "__main__":
    main()
