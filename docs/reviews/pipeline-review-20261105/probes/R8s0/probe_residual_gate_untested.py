#!/usr/bin/env python3
"""Stage 0's residual criterion (R) is the only one that sees mid-sweep flicker,
and the suite never shows it failing.

    python3 probe_residual_gate_untested.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. Synthetic data only, temp dirs
only, no network.

1. Builds a synthetic sweep (12 steps, the repeat last) through one global
   S-shaped tone curve, as the suite's own synthetic_steps() does (METHOD-V1's
   numbers except pixel_step 8 and 15 fit rounds, for speed), but with a
   per-frame brightness factor of 0.8..1.2 on steps 2 to 11 (mains or bulb
   flicker at short exposures; the first step and the repeat are steady, so the
   repeat check passes). The frames report the held exposure and ISO exactly,
   so gain agreement passes.
2. Runs meter.check() from the checkout: expected verdict FAIL with only
   "residual" among the reasons (the region test passes: every region sees the
   same flicker).
3. Copies tools/den-light to a temp dir, applies the mutant
   `residual_pass = True` (the residual gate never fails) and runs the same
   check: the verdict becomes PASS. The test suite passes with this mutant
   (see mutate_den_light.py, M1), so nothing guards the gate.
"""
import importlib.util
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)


def load(dirpath):
    sys.path.insert(0, str(dirpath))
    try:
        for name in ("meter", "sweep"):
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location("meter", dirpath / "meter.py")
        meter = importlib.util.module_from_spec(spec)
        sys.modules["meter"] = meter
        spec.loader.exec_module(meter)
        spec = importlib.util.spec_from_file_location("sweep", dirpath / "sweep.py")
        sweep = importlib.util.module_from_spec(spec)
        sys.modules["sweep"] = sweep
        spec.loader.exec_module(sweep)
        return meter, sweep
    finally:
        sys.path.pop(0)


def encode_pixel(r, g, b):
    clip = lambda v: min(255, max(0, v))
    return (clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16),
            clip(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128),
            clip(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128))


def nv12(meter, rgb, step):
    nv = bytearray(meter.NV12_BYTES)
    k = 0
    for y in range(0, 360, step):
        for x in range(0, 640, step):
            yy, u, v = encode_pixel(rgb[0][k], rgb[1][k], rgb[2][k])
            k += 1
            nv[y * 640 + x] = yy
            at = 640 * 360 + (y // 2) * 640 + (x // 2) * 2
            nv[at], nv[at + 1] = u, v
    return bytes(nv)


def build_steps(meter, sweep, method, flicker, seed=5):
    rng = random.Random(seed)
    step = method["pixel_step"]
    n = len(meter.sample_points(step))
    energy = [math.exp(rng.uniform(math.log(0.05), math.log(10))) for _ in range(n)]
    curve = lambda y: 255 * y ** 1.3 / (y ** 1.3 + 1)
    plan = sweep.exposure_plan(8000, 100, method)
    steps, number = [], 0
    for index, hold in enumerate(plan, 1):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(method["frames_per_step"]):
            number += 1
            s = rng.uniform(1 - flicker, 1 + flicker) if 2 <= index <= 11 else 1.0
            rgb = ([], [], [])
            for e in energy:
                for c, gain in enumerate((1.0, 0.9, 0.75)):
                    rgb[c].append(min(255, max(0, round(curve(x / 8 * e * gain * s) + rng.gauss(0, 0.7)))))
            report = {"exposure_us": hold["exposure_us"], "iso": hold["iso"], "kelvin": hold["white_balance_kelvin"]}
            frames.append({"n": number, "report": report, "nv12": nv12(meter, rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def verdict(meter, sweep, steps_builder):
    base = meter.load_method()
    method = meter.Method(dict(base.values, pixel_step=8, frames_per_step=3, fit_iterations=15), (), "", "")
    steps = steps_builder(meter, sweep, method)
    result = meter.check(steps, method)
    first, repeat = steps[0]["frames"][0]["nv12"], steps[-1]["frames"][0]["nv12"]
    regions = meter.sample_regions(8, 4, 4)
    agree = meter.agree(meter.region_means(meter.decode(first, 8), regions, 16),
                        meter.region_means(meter.decode(repeat, 8), regions, 16), method["noise_tolerance_codes"])
    return result, agree, method


def main():
    meter, sweep = load(ROOT / "tools/den-light")
    steady, agree0, m = verdict(meter, sweep, lambda mt, sw, me: build_steps(mt, sw, me, 0.0))
    print(f"RESULT steady sweep: verdict {steady['verdict']}, residual "
          f"{ {c: round(v, 2) for c, v in steady['residual']['codes'].items()} } (R = {m['residual_max_codes']}), "
          f"region test {steady['region_test']['pass']}")
    flick, agree1, m = verdict(meter, sweep, lambda mt, sw, me: build_steps(mt, sw, me, 0.2))
    print(f"RESULT flicker on steps 2-11: repeat check agrees={agree1}, gain agreement "
          f"{flick['gain_agreement']['pass']}, residual { {c: round(v, 2) for c, v in flick['residual']['codes'].items()} }, "
          f"region test {flick['region_test']['pass']} (untested {flick['region_test']['untested']}), "
          f"verdict {flick['verdict']}, reasons {flick['reasons']}")
    only_residual = flick["verdict"] == "fail" and flick["reasons"] == ["residual"] and agree1
    print(("PASS" if only_residual else "FAIL") + ": the residual is the only criterion that fails the flickering sweep")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "den-light"
        shutil.copytree(ROOT / "tools/den-light", copy)
        src = (copy / "meter.py").read_text()
        old = 'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())'
        assert src.count(old) == 1
        (copy / "meter.py").write_text(src.replace(old, "residual_pass = True"))
        meter2, sweep2 = load(copy)
        mutated, _, _ = verdict(meter2, sweep2, lambda mt, sw, me: build_steps(mt, sw, me, 0.2))
    print(f"RESULT mutant residual_pass=True on the same flickering sweep: verdict {mutated['verdict']}")
    print(("FAIL (defect shown)" if mutated["verdict"] == "pass" else "PASS")
          + ": with the residual gate disabled, the flickering sweep is a Stage 0 "
          + mutated["verdict"].upper() + " (pass would be a wrong go)")


if __name__ == "__main__":
    main()
