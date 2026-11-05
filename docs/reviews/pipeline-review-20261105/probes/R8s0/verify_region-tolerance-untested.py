#!/usr/bin/env python3
"""verify_region-tolerance-untested.py: is the region tolerance P (METHOD-V1: 5%) exercised near its
value? A mild local tone map (left half s_curve(1.2), right half s_curve(1.3)) is scored at P = 5%
and with the mutant `tolerance = percent / 10.0` (P effectively 50%).

How to run:
  python3 -B verify_region-tolerance-untested.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only, temp dirs only, no network. Uses the checkout's own test helpers. Also prints
the deviations in the suite's own local-tone-map case and in its sweep_method() settings, to show
why a 10x looser P is not noticed. Whole-suite survival of the mutant: verify_mutate_den_light.py (TOL).
"""
import importlib.util
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
TOOL = ROOT / "tools/den-light"
sys.path.insert(0, str(TOOL))
import test_den_light as t  # noqa: E402
meter, sweep = t.meter, t.sweep


def steps_with(method, *, curve_for_pixel, seed=5, frames_per_step=2):
    rng = random.Random(seed)
    step = method["pixel_step"]
    n = len(meter.sample_points(step))
    energy = [2.718281828 ** rng.uniform(-2.9957, 2.3026) for _ in range(n)]
    steps, number = [], 0
    for hold in sweep.exposure_plan(8000, 100, method):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(frames_per_step):
            number += 1
            rgb = t.render(energy, (1.0, 0.9, 0.75), x, curve_for_pixel, rng, 0.7)
            report = {"exposure_us": hold["exposure_us"], "iso": hold["iso"], "kelvin": hold["white_balance_kelvin"]}
            frames.append({"n": number, "report": report, "nv12": t.encode_samples(rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def mutant_meter(old, new):
    tmp = Path(tempfile.mkdtemp(prefix="verify-tol-"))
    src = (TOOL / "meter.py").read_text(encoding="utf-8")
    assert src.count(old) == 1, "pattern not unique"
    (tmp / "meter_mut.py").write_text(src.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("meter_mut", tmp / "meter_mut.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    shutil.rmtree(tmp, ignore_errors=True)
    return mod


def main():
    method = t.test_method(pixel_step=10, fit_iterations=20)
    columns = 640 // 10
    a, b = t.s_curve(1.2), t.s_curve(1.3)
    steps = steps_with(method, curve_for_pixel=lambda i: a if i % columns < columns // 2 else b)
    r1 = meter.check(steps, method)
    devs = sorted((e["deviation_percent"] for e in r1["region_test"]["regions"] if e["tested"]), reverse=True)
    print(f"RESULT mild local tone map at P = {method['region_tolerance_percent']}%: verdict {r1['verdict']}, "
          f"reasons {r1['reasons']}, residual { {c: round(v, 2) for c, v in r1['residual']['codes'].items()} }, "
          f"largest deviations {[round(d, 1) for d in devs[:4]]}%")
    mm = mutant_meter('tolerance = method["region_tolerance_percent"] / 100.0',
                      'tolerance = method["region_tolerance_percent"] / 10.0')
    r2 = mm.check(steps, method)
    print(f"RESULT mutant P x10 on the same frames: verdict {r2['verdict']}, reasons {r2['reasons']}")
    local = t.synthetic_steps(method, curves=lambda i: t.s_curve(0.8) if i % columns < columns // 2 else t.s_curve(1.3))
    r3 = meter.check(local, method)
    tested = [e["deviation_percent"] for e in r3["region_test"]["regions"] if e["tested"]]
    failing = [e["deviation_percent"] for e in r3["region_test"]["regions"] if e["tested"] and not e["pass"]]
    print(f"RESULT suite's half-view case: tested deviations min {min(tested):.1f}% max {max(tested):.1f}%; "
          f"failing ones min {min(failing):.1f}% (P = 5% -> 50% under the mutant; sweep_method's 10% -> 100%); "
          f"under the mutant: {mm.check(local, method)['verdict']}")
    shown = r1["verdict"] == "fail" and r1["reasons"] == ["region test"] and r2["verdict"] == "pass"
    print(("FAIL (defect shown)" if shown else "PASS")
          + ": a mild local tone map the 5% region test fails passes when P is loosened 10x")


if __name__ == "__main__":
    main()
