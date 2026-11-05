#!/usr/bin/env python3
"""How long Stage 0's fit (meter.check) takes with METHOD-V1's own numbers.

    python3 probe_fit_runtime.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. Builds a synthetic sweep with
METHOD-V1's numbers unchanged (pixel_step 4 -> 160x90 samples, 3 frames per
step, 12 steps, 30 fit rounds), as the PC would after the release, and times
meter.check(). sweep.py prints "Fitting the response (under a minute)..." and
README step 6 says "It then fits for up to a minute". Synthetic data only.
"""
import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("residual_probe", HERE / "probe_residual_gate_untested.py")
helpers = importlib.util.module_from_spec(spec)
sys.argv = sys.argv[:2]
spec.loader.exec_module(helpers)
meter, sweep = helpers.load(ROOT / "tools/den-light")
method = meter.load_method()
print(f"RESULT METHOD-V1 numbers: pixel_step {method['pixel_step']}, frames_per_step {method['frames_per_step']}, "
      f"fit_iterations {method['fit_iterations']}")
t = time.monotonic()
steps = helpers.build_steps(meter, sweep, method, 0.0)
print(f"RESULT synthetic frames built in {time.monotonic() - t:.0f} s")
t, c = time.monotonic(), time.process_time()
result = meter.check(steps, method)
wall, took = time.monotonic() - t, time.process_time() - c
print(f"RESULT meter.check took {took:.0f} s of CPU ({wall:.0f} s wall on this shared machine; "
      f"verdict {result['verdict']}, samples {result['samples']})")
print(("FAIL" if took > 60 else "PASS") + f": the fit {'exceeds' if took > 60 else 'fits'} the README's 'up to a minute' on this machine")
