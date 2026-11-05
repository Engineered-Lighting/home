#!/usr/bin/env python3
"""probe_adaptive-global-tone-passes-stage0.py

What it checks (rung 8 Stage 0 premise, tools/den-light/meter.py):
  Stage 0 is the go/no-go for "can the camera meter light": one inverse response must fit an
  exposure sweep of a fixed scene (residual) and the same fit per region must agree (region test).
  An exposure sweep of a fixed scene only ever scales every pixel's irradiance by the same factor.
  So an ISP whose GLOBAL tone curve adapts to the frame's overall brightness (partial
  exposure compensation, gain s = (frame mean / reference)^-a) is indistinguishable from a static
  curve: the sweep fits perfectly with a distorted curve f* = (F^-1)^(1/(1-a)) and every region
  agrees. Stage 1 then holds ONE exposure and changes the scene (bulbs on and off), where the
  adaptation moves the curve and superposition breaks.

  The probe renders the Stage 0 sweep through such a camera (a = 0.1 and 0.3, and a = 0 as the control),
  runs the branch's own meter.check() on it, then uses the fitted curve to linearise a two-bulb
  superposition test at the held exposure (four corner bulbs; basis = each bulb alone at full minus
  all-off; hold-out = all four at full, one of the plan's fixed hold-outs),
  pooling block relative errors as the plan's gate does (median <= 8%, p90 <= 19%).

How to run:
  python3 -B probe_adaptive-global-tone-passes-stage0.py <root of the claude/rung-8-stage0 checkout>

Synthetic data only, no network, nothing written to the checkout. Exit 0 = reproduced (Stage 0
passes for a = 0.3 while the superposition errors break the rung 8 gate; the a = 0 control passes both).
"""
import math
import random
import statistics
import sys
from pathlib import Path


def pct(values, q):
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root / "tools" / "den-light"))
    import meter, sweep, test_den_light as T  # noqa: E402  (imported read-only; -B writes no .pyc)

    base = meter.load_method()
    method = meter.Method({**base.values, "pixel_step": 10, "fit_iterations": 20}, (), base.file_sha256,
                          base.block_sha256)
    step = method["pixel_step"]
    points = meter.sample_points(step)
    n = len(points)
    curve = T.s_curve(1.3)
    gains = (1.0, 0.9, 0.75)
    t_pick, iso_pick = 8000, 100
    x_pick = t_pick / 1000 * iso_pick / 100
    rng0 = random.Random(5)
    energy = [math.exp(rng0.uniform(math.log(0.05), math.log(10))) for _ in range(n)]
    mean_full = statistics.fmean(energy)
    # Four bulbs, one near each corner of the view (as the den's front/rear left/right), each with
    # a broad falloff plus a little bounce, and dim stray light. energy is the all-four-full scene.
    corners = [(0, 0), (640, 0), (0, 360), (640, 360)]
    raw = [[0.06 + math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * 260.0 ** 2)) for (x, y) in points]
           for cx, cy in corners]
    total = [sum(r[i] for r in raw) for i in range(n)]
    bulbs = [[e * r[i] / total[i] for i, e in enumerate(energy)] for r in raw]
    stray = [0.03 * e for e in energy]
    def camera(scene, x, a, rng):
        """The ISP: one global tone curve after a frame-adaptive gain (a = 0: a static curve)."""
        s = (x * statistics.fmean(scene) / (x_pick * mean_full)) ** (-a)
        return T.render(scene, gains, x * s, lambda i: curve, rng, 0.7)

    ok = True
    for a in (0.0, 0.1, 0.3):
        rng = random.Random(7)
        steps, number = [], 0
        for hold in sweep.exposure_plan(t_pick, iso_pick, method):
            x = hold["exposure_us"] / 1000 * hold["iso"] / 100
            frames = []
            for _ in range(2):
                number += 1
                report = {"exposure_us": hold["exposure_us"] + 10, "iso": hold["iso"],
                          "kelvin": hold["white_balance_kelvin"]}
                rgb = camera(energy, x, a, rng)
                frames.append({"n": number, "report": report, "nv12": T.encode_samples(rgb, step)})
            steps.append({"hold": hold, "frames": frames})
        result = meter.check(steps, method)
        res = {c: round(v, 2) for c, v in result["residual"]["codes"].items()} if result["residual"] else None
        worst = max((e["deviation_percent"] or 0) for e in result["region_test"]["regions"]) if result["region_test"] else None
        tested = sum(e["tested"] for e in result["region_test"]["regions"]) if result["region_test"] else 0
        print(f"a={a}: Stage 0 verdict {result['verdict'].upper()} reasons {result['reasons']} "
              f"residual {res} region tested {tested}/48 worst deviation {worst:.2f}%")
        f = result["curves"]["g"] if result["curves"] else None
        if f is None:
            ok = False
            continue

        # Stage 1 at one held exposure: decode what the camera writes, linearise with the fitted f.
        def reading(scene):
            frames = [camera(scene, x_pick, a, rng) for _ in range(3)]
            per_pixel = []
            for i in range(n):
                vals = [f[fr[1][i]] / x_pick for fr in frames if fr[1][i] < method["clip_limit_g"]]
                per_pixel.append(statistics.fmean(vals) if vals else None)
            return per_pixel
        off = reading(stray)
        alone = [reading([b + o for b, o in zip(bulb, stray)]) for bulb in bulbs]
        lab = reading([sum(b[i] for b in bulbs) + stray[i] for i in range(n)])   # hold-out: all four at full
        la = None
        # 8 x 8 sampled-pixel blocks, as Stage 1 averages blocks
        cols, rows = 640 // step, 360 // step
        errors = []
        for by in range(0, rows, 8):
            for bx in range(0, cols, 8):
                idx = [y * cols + x for y in range(by, min(rows, by + 8)) for x in range(bx, min(cols, bx + 8))]
                if any(v[i] is None for v in [off, lab] + alone for i in idx):
                    continue
                d = statistics.fmean(off[i] for i in idx)
                e_true = statistics.fmean(lab[i] for i in idx) - d
                e_hat = sum(statistics.fmean(l[i] for i in idx) - d for l in alone)
                if e_true > 0:
                    errors.append(abs(e_hat - e_true) / e_true)
        med, p90 = pct(errors, 0.5), pct(errors, 0.9)
        gate = med <= 0.08 and p90 <= 0.19
        print(f"a={a}: superposition at the held exposure over {len(errors)} blocks: median {100*med:.1f}% "
              f"p90 {100*p90:.1f}% -> rung 8 gate {'met' if gate else 'NOT met'}")
        if a == 0.0:
            ok &= result["verdict"] == "pass" and gate
        elif a == 0.3:
            ok &= result["verdict"] == "pass" and not gate
    print("RESULT: Stage 0 says GO for a camera whose global tone curve adapts to the frame; the same camera "
          "cannot superpose bulbs at one held exposure" if ok else "RESULT: not reproduced")
    print("PASS (reproduced)" if ok else "FAIL (not reproduced)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
