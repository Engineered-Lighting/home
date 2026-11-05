#!/usr/bin/env python3
"""probe_region-test-noise-sensitivity.py

What it checks (rung 8 Stage 0 gate, tools/den-light/meter.py with METHOD-V1's own numbers):
  A camera with ONE global tone curve (the case Stage 0 must call GO) is rendered at several
  per-pixel noise levels (Gaussian, in code values) through the full sweep plan, and the branch's
  meter.check() is run with METHOD-V1's numbers unchanged (pixel_step 4, 3 frames per step,
  30 iterations, R = 4 codes, P = 5 %, 20 samples per supported code). Reports the verdict, the
  residual and the worst region deviation, to show at what noise a truly global camera is still
  called a pass.

How to run:
  python3 -B probe_region-test-noise-sensitivity.py <root of the claude/rung-8-stage0 checkout> [noise ...]
  python3 -B probe_region-test-noise-sensitivity.py <root> --yuv [noise ...]
     (--yuv: the noise is added to the NV12 Y, U and V bytes, as a sensor/ISP produces it, instead of
      to the decoded R, G, B codes; BT.601 then amplifies U/V noise into R by 1.6x and B by 2.0x)

Synthetic data only; no network; nothing written. Slow (about 1-2 minutes per noise level).
Prints one RESULT line per noise level; exit 0 always (it is a measurement).
"""
import sys
import time
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    yuv = "--yuv" in sys.argv[2:]
    levels = [float(v) for v in sys.argv[2:] if v != "--yuv"] or [1.0, 2.0, 3.0]
    import random
    sys.path.insert(0, str(root / "tools" / "den-light"))
    import meter, test_den_light as T  # noqa: E402
    method = meter.load_method()
    for noise in levels:
        start = time.time()
        steps = T.synthetic_steps(method, curves=lambda i: T.s_curve(1.3), noise=0 if yuv else noise,
                                  frames_per_step=method["frames_per_step"], seed=9)
        if yuv:
            rng, step, plane = random.Random(13), method["pixel_step"], 640 * 360
            for st in steps:
                for fr in st["frames"]:
                    nv = bytearray(fr["nv12"])
                    for y in range(0, 360, step):
                        for x in range(0, 640, step):
                            at = plane + (y // 2) * 640 + (x // 2) * 2
                            for k in (y * 640 + x, at, at + 1):
                                nv[k] = min(255, max(0, round(nv[k] + rng.gauss(0, noise))))
                    fr["nv12"] = bytes(nv)
        result = meter.check(steps, method)
        regions = result["region_test"]["regions"] if result["region_test"] else []
        worst = max((e["deviation_percent"] or 0) for e in regions) if regions else None
        failing = sum(1 for e in regions if not e["pass"])
        residual = {c: round(v, 2) for c, v in result["residual"]["codes"].items()} if result["residual"] else None
        print(f"RESULT noise={noise} codes on {'NV12 Y/U/V' if yuv else 'R/G/B'}: verdict {result['verdict'].upper()} reasons {result['reasons']} "
              f"residual {residual} worst region deviation {worst:.2f}% failing region-channels {failing}/48 "
              f"({time.time() - start:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
