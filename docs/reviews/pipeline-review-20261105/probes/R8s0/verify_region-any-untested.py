#!/usr/bin/env python3
"""verify_region-any-untested.py: does a local tone map confined to ONE of the 16 regions fail only
the region test, and does weakening the region aggregation from all() to any() turn it into a PASS?
Also: does the suite's own local-tone-map case fail every region (so all() and any() agree there)?

How to run:
  python3 -B verify_region-any-untested.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only, temp dirs only, no network. Uses the checkout's own test helpers
(test_den_light.render / encode_samples / s_curve / test_method / synthetic_steps). Region 5 (an
inner region of the 4x4 grid) is rendered through s_curve(1.0), every other pixel through s_curve(1.3).
The whole-suite survival of the any() mutant is checked by verify_mutate_den_light.py (ANY).
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
    tmp = Path(tempfile.mkdtemp(prefix="verify-any-"))
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
    owner = meter.sample_regions(method["pixel_step"], 4, 4)
    a, b = t.s_curve(1.0), t.s_curve(1.3)
    steps = steps_with(method, curve_for_pixel=lambda i: a if owner[i] == 5 else b)
    r1 = meter.check(steps, method)
    failing = sorted({(e["region"], e["channel"], round(e["deviation_percent"], 1))
                      for e in r1["region_test"]["regions"] if not e["pass"]})
    print(f"RESULT one-region tone map (region 5): verdict {r1['verdict']}, reasons {r1['reasons']}, residual "
          f"{ {c: round(v, 2) for c, v in r1['residual']['codes'].items()} }, failing {failing}")
    mm = mutant_meter('return {"pass": all(e["pass"] for e in out)', 'return {"pass": any(e["pass"] for e in out)')
    r2 = mm.check(steps, method)
    print(f"RESULT mutant any(): verdict {r2['verdict']}, reasons {r2['reasons']}")
    # the suite's own case (test_a_local_tone_map_fails_the_region_test_and_one_global_curve_passes)
    columns = 640 // 10
    local = t.synthetic_steps(method, curves=lambda i: t.s_curve(0.8) if i % columns < columns // 2 else t.s_curve(1.3))
    r3 = meter.check(local, method)
    entries = r3["region_test"]["regions"]
    print(f"RESULT suite's half-view case: {sum(not e['pass'] for e in entries)} of {len(entries)} region/channel "
          f"entries fail ({sum(not e['tested'] for e in entries)} untested); min deviation of tested "
          f"{min(e['deviation_percent'] for e in entries if e['tested']):.1f}%; under any(): "
          f"{mm.check(local, method)['verdict']}")
    shown = r1["verdict"] == "fail" and r1["reasons"] == ["region test"] and r2["verdict"] == "pass"
    print(("FAIL (defect shown)" if shown else "PASS")
          + ": the one-region tone map fails only the region test and passes once all() becomes any()")


if __name__ == "__main__":
    main()
