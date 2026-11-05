#!/usr/bin/env python3
"""verify-intent_fit-clip-boundary-untested.py: is the fit's clip flag at exactly the limit covered by tests?

Checks two mutants of tools/den-light/meter.py, each on its own temp copy (nothing in the checkout changes):
  A. Samples.add_frame (the response fit's sample filter):  `r >= lr or g >= lg or b >= lb`  ->  `>`
  B. meter_norm_value (meter-norm/v1 per-pixel value):       `r >= limits[0] ...`             ->  `>`
     (control: the finding says this boundary IS tested)
For each mutant: a semantic check (does a pixel at exactly the limit 250 enter the fit / get a value),
then the narrowest class (failfast), then the whole suite (failfast) if the narrow class does not catch it.
METHOD-V1 step 2: a pixel is flagged if z >= clip_limit.

How to run:
  python3 -B verify-intent_fit-clip-boundary-untested.py <root of claude/rung-8-stage0 checkout> [work dir]
Synthetic data only; the suite's fakes bind to 127.0.0.1. Prints RESULT/PASS/FAIL lines.
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    ("A-fit-filter", "            if r >= lr or g >= lg or b >= lb:\n                self.flagged += 1",
     "            if r > lr or g > lg or b > lb:\n                self.flagged += 1", "FitTests"),
    ("B-meter-norm", "    if r >= limits[0] or g >= limits[1] or b >= limits[2]:\n        return None",
     "    if r > limits[0] or g > limits[1] or b > limits[2]:\n        return None", "DecodeTests"),
]


def load(tool, tag):
    spec = importlib.util.spec_from_file_location(f"meter_{tag}", tool / "meter.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(tool))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def semantic(tool, tag):
    meter = load(tool, tag)
    s = meter.Samples(2)
    s.add_frame(([250, 100], [90, 90], [80, 80]), 1.0, (250, 250, 250))
    params, _ = meter.meter_norm_parameters(meter.load_method(), {c: [z / 128 for z in range(256)] for c in "rgb"})
    v = meter.meter_norm_value(250, 90, 80, 10.0, 1.0, params)
    print(f"RESULT {tag}: pixel R=250 (limit 250): in fit samples={0 in list(s.pix)} flagged={s.flagged}; "
          f"meter_norm_value={'None' if v is None else round(v, 6)}", flush=True)


def run(tool, classes):
    args = [sys.executable, "-B", "-m", "unittest", "-f"] + [f"test_den_light.{c}" for c in classes]
    t0 = time.time()
    p = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=1500)
    lines = p.stderr.strip().splitlines()
    ran = [l for l in lines if l.startswith("Ran ")]
    bad = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
    verdict = "caught" if p.returncode else "survived"
    print(f"RESULT {verdict} by {' '.join(classes) or 'whole suite'} ({time.time() - t0:.0f} s): "
          f"{ran[-1] if ran else ''} | {lines[-1] if lines else ''} | {bad[0] if bad else ''}", flush=True)
    return verdict


def main():
    root = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="r8s0-intent-"))
    work.mkdir(parents=True, exist_ok=True)
    semantic(root / "tools" / "den-light", "pinned")
    table = []
    for name, old, new, narrow in MUTANTS:
        copy = Path(tempfile.mkdtemp(prefix=name + "-", dir=work))
        tool = copy / "tools" / "den-light"
        shutil.copytree(root / "tools" / "den-light", tool)
        (copy / "tools" / "fixtures").mkdir()
        shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", copy / "tools/fixtures/")
        text = (tool / "meter.py").read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"FAIL {name}: pattern found {text.count(old)} times; not applied")
            table.append((name, "not-run"))
            continue
        (tool / "meter.py").write_text(text.replace(old, new), encoding="utf-8")
        semantic(tool, name)
        classes = [narrow] if narrow else []
        # the narrow class may not exist; fall back to the whole suite
        src = (tool / "test_den_light.py").read_text(encoding="utf-8")
        if narrow and f"class {narrow}(" not in src:
            classes = []
        v = run(tool, classes)
        if v == "survived" and classes:
            v = run(tool, [])
        table.append((name, v))
    print()
    for name, v in table:
        print(f"RESULT table | {name:14} | {v}")
    a = dict(table).get("A-fit-filter")
    print("FAIL (gap shown): fit-filter boundary mutant survives the whole suite" if a == "survived"
          else "PASS: suite catches the fit-filter boundary mutant")


if __name__ == "__main__":
    main()
