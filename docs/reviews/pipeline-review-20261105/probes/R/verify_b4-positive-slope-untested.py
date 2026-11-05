#!/usr/bin/env python3
"""verify b4-positive-slope-untested: is a one-sided B4 slope check (mutant M02) observable?

    python3 -B verify_b4-positive-slope-untested.py <rung-r checkout root>

Builds synthetic takes with the checkout's own make_take helper (temp dir only, no
camera, nothing on the network): device_drift_ppm=+2000 (d = t_host - t_device FALLS,
the only sign the suite tests) and device_drift_ppm=-2000 (d RISES). Runs check.py's
run_check with the real code and with M02 applied in memory
('abs(slope) * 1e6 <= max' -> '-slope * 1e6 <= max'). Prints PASS/FAIL per expectation
and a RESULT line. Expectation if the finding holds: the real code FAILs B4 for both
signs; the mutant FAILs only the falling d and PASSes the rising d (so a test with
the rising sign would kill M02, and the suite has none).
"""
import io
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import test_den_take as t  # noqa: E402  (helpers only; no test runs)

OLD = 'and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]'
NEW = 'and -slope * 1e6 <= m["max_two_clock_slope_ppm"]'
source = Path(check.__file__).read_text(encoding="utf-8")
assert source.count(OLD) == 1, "M02 old text not found exactly once"
mutant = types.ModuleType("check_m02")
mutant.__file__ = check.__file__
exec(compile(source.replace(OLD, NEW), check.__file__, "exec"), mutant.__dict__)

def run(module, folder):
    code, report, _ = module.run_check(folder, ask=lambda: "none", show=lambda p: None,
                                       out=io.StringIO(), console=t.Console())
    return report["verdict"], report["failures"], round(report["rules"]["B4_two_clock"]["slope_ppm"], 1)

tmp = Path(tempfile.mkdtemp(prefix="verify-b4-"))
ok = True
try:
    for drift, label in ((2000, "d falls"), (-2000, "d rises")):
        base = t.make_take(tmp, device_drift_ppm=drift)
        for name, module in (("real", check), ("M02", mutant)):
            folder = tmp / f"{drift}-{name}"
            shutil.copytree(base, folder)
            verdict, failures, slope = run(module, folder)
            expect = ("FAIL", ["B4_two_clock"]) if (name == "real" or drift > 0) else ("PASS", [])
            good = (verdict, failures) == expect
            ok &= good
            print(f"{'PASS' if good else 'FAIL'} drift {drift:+d} ppm ({label}, slope {slope:+.1f} ppm) "
                  f"{name}: verdict {verdict} failures {failures} (expected {expect})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("RESULT", "REPRODUCED: M02 changes the verdict only for a rising d, which no suite test builds"
      if ok else "NOT REPRODUCED")
