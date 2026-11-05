#!/usr/bin/env python3
"""verify_fit-clip-boundary-untested.py: does the den-light suite catch the fit keeping a pixel AT its clip limit?

Mutant: in meter.py Samples.add_frame, `if r >= lr or g >= lg or b >= lb:` becomes
`if r > lr or g > lg or b > lb:` (METHOD-V1 step 2 says a pixel is flagged when z >= clip_limit).

Steps:
  1. Semantic check (no suite): on the pinned code and on the mutant, add one frame whose pixel 0 has R
     exactly at the limit (250) and ask meter_norm_value about the same codes. Shows whether the fit's
     sample set and meter-norm/v1 disagree on that pixel.
  2. Runs FitTests (failfast), then the whole suite (failfast), on a copy with the mutant applied.

How to run:
  python3 -B verify_fit-clip-boundary-untested.py <root of claude/rung-8-stage0 checkout> [work dir]
The work dir (default: a fresh temp dir) receives the mutated copy; nothing in the checkout changes.
Synthetic data only; the suite's fakes bind to 127.0.0.1.
"""
import importlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OLD = "            if r >= lr or g >= lg or b >= lb:\n                self.flagged += 1"
NEW = "            if r > lr or g > lg or b > lb:\n                self.flagged += 1"


def semantic(tool_dir, label):
    sys.path.insert(0, str(tool_dir))
    for name in ("meter",):
        sys.modules.pop(name, None)
    meter = importlib.import_module("meter")
    sys.path.pop(0)
    samples = meter.Samples(2)
    samples.add_frame(([250, 100], [90, 90], [80, 80]), 1.0, (250, 250, 250))
    params, _ = meter.meter_norm_parameters(meter.load_method(),
                                            {c: [z / 128 for z in range(256)] for c in "rgb"})
    mn = meter.meter_norm_value(250, 90, 80, 10.0, 1.0, params)
    in_fit = 0 in list(samples.pix)
    print(f"RESULT {label}: pixel with R=250 (limit 250) in fit samples={in_fit}, flagged={samples.flagged}, "
          f"meter_norm_value={'None (flagged)' if mn is None else mn}")
    return in_fit, mn is None


def run(tool, classes):
    args = [sys.executable, "-B", "-m", "unittest", "-f"] + classes
    start = time.time()
    proc = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=1200)
    lines = proc.stderr.strip().splitlines()
    ran = [l for l in lines if l.startswith("Ran ")]
    failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
    verdict = "caught" if proc.returncode != 0 else "survived"
    print(f"RESULT mutant {verdict} by {' '.join(classes) or 'whole suite'} ({time.time() - start:.0f} s): "
          f"{(ran[-1] if ran else '')} {lines[-1] if lines else ''} {failed[0] if failed else ''}", flush=True)
    return verdict


def main():
    root = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="r8s0-clip-"))
    work.mkdir(parents=True, exist_ok=True)
    copy = Path(tempfile.mkdtemp(prefix="clip-", dir=work))
    tool = copy / "tools" / "den-light"
    shutil.copytree(root / "tools" / "den-light", tool)
    (copy / "tools" / "fixtures").mkdir()
    shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", copy / "tools/fixtures/")
    target = tool / "meter.py"
    text = target.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        print(f"FAIL pattern found {text.count(OLD)} times; mutant not applied")
        return 2
    base_in_fit, base_flag = semantic(root / "tools" / "den-light", "pinned")
    target.write_text(text.replace(OLD, NEW), encoding="utf-8")
    mut_in_fit, mut_flag = semantic(tool, "mutant")
    if mut_in_fit and mut_flag:
        print("RESULT mutant makes the fit keep a pixel that meter-norm/v1 flags (disagreement)")
    narrow = run(tool, ["test_den_light.FitTests"])
    whole = run(tool, [])
    print(("FAIL (defect shown): boundary mutant survives the whole suite" if whole == "survived"
           else "PASS: the suite catches the boundary mutant"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
