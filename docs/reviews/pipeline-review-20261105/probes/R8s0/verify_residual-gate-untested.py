#!/usr/bin/env python3
"""verify_residual-gate-untested.py: is Stage 0's residual criterion (R) the only detector of a
sweep with mid-sweep flicker, and does disabling it turn that sweep into a PASS?

How to run:
  python3 -B verify_residual-gate-untested.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only, temp dirs only, no network. Uses the checkout's own test helpers
(test_den_light.render / encode_samples / s_curve / test_method) to build a full sweep (11 steps +
the repeat) through ONE global tone curve, frames reporting the held exposure/ISO/white balance
(gain agreement passes), with a per-frame brightness factor U(1-f, 1+f) on steps 2..11 only (step 1
and the repeat are steady, so the live repeat check agrees). Then:
  1. meter.check() from the checkout -> expected FAIL with reasons == ['residual'].
  2. the same frames through a temp copy of meter.py with `residual_pass = True` -> verdict?
The whole-suite survival of that mutant is checked separately by verify_mutate_den_light.py (RES).
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


def steps_with(method, *, curve_for_pixel, frame_scale, seed=5, frames_per_step=2):
    rng = random.Random(seed)
    step = method["pixel_step"]
    n = len(meter.sample_points(step))
    energy = [2.718281828 ** rng.uniform(-2.9957, 2.3026) for _ in range(n)]  # log-uniform 0.05..10
    steps, number = [], 0
    for index, hold in enumerate(sweep.exposure_plan(8000, 100, method), 1):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(frames_per_step):
            number += 1
            s = frame_scale(index, rng)
            rgb = t.render(energy, (1.0, 0.9, 0.75), x * s, curve_for_pixel, rng, 0.7)
            report = {"exposure_us": hold["exposure_us"], "iso": hold["iso"], "kelvin": hold["white_balance_kelvin"]}
            frames.append({"n": number, "report": report, "nv12": t.encode_samples(rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def mutant_meter(old, new):
    tmp = Path(tempfile.mkdtemp(prefix="verify-res-"))
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
    curve = t.s_curve(1.3)
    steady = steps_with(method, curve_for_pixel=lambda i: curve, frame_scale=lambda k, rng: 1.0)
    r0 = meter.check(steady, method)
    print(f"RESULT steady sweep: verdict {r0['verdict']}, residual "
          f"{ {c: round(v, 2) for c, v in r0['residual']['codes'].items()} } (R = {method['residual_max_codes']})")
    flick = steps_with(method, curve_for_pixel=lambda i: curve,
                       frame_scale=lambda k, rng: rng.uniform(0.8, 1.2) if 2 <= k <= 11 else 1.0)
    r1 = meter.check(flick, method)
    regions = meter.sample_regions(method["pixel_step"], 4, 4)
    means = lambda nv: meter.region_means(meter.decode(nv, method["pixel_step"]), regions, 16)
    agree = meter.agree(means(flick[0]["frames"][0]["nv12"]), means(flick[-1]["frames"][0]["nv12"]),
                        method["noise_tolerance_codes"])
    print(f"RESULT flicker on steps 2-11: repeat check agrees {agree}, gain agreement {r1['gain_agreement']['pass']}, "
          f"residual { {c: round(v, 2) for c, v in r1['residual']['codes'].items()} }, region test "
          f"{r1['region_test']['pass']}, verdict {r1['verdict']}, reasons {r1['reasons']}")
    mm = mutant_meter('residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
                      'residual_pass = True')
    r2 = mm.check(flick, method)
    print(f"RESULT mutant residual_pass = True, same frames: verdict {r2['verdict']}, reasons {r2['reasons']}")
    shown = r1["verdict"] == "fail" and r1["reasons"] == ["residual"] and agree and r2["verdict"] == "pass"
    print(("FAIL (defect shown)" if shown else "PASS")
          + ": the residual is the only criterion failing this sweep, and with it disabled the sweep passes")


if __name__ == "__main__":
    main()
