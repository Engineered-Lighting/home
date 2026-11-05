#!/usr/bin/env python3
"""validate_mutate_gate_thresholds.py: independent re-run (verifier) of finding gate-thresholds-untested.

What it checks (rung 8 Stage 0, tools/den-light):
  Applies each mutant below to a fresh temp copy of tools/den-light (plus the fixture the suite
  reads) and runs the named tests with failfast. A mutant that leaves the tests green "survived".
    1. residual limit R doubled           (meter.py residual comparison)   -> FULL suite
    2. region tolerance P doubled         (meter.py region comparison)     -> FULL suite
    3. residual check removed entirely    (residual_pass = True)           -> FitTests SweepTests RecheckTests
    4. --recheck skips its repeat check   (sweep.py recheck)               -> RecheckTests
  The checkout itself is never modified. Tests use synthetic data and fakes on 127.0.0.1 only.

How to run:
  python3 -B validate_mutate_gate_thresholds.py <root of the claude/rung-8-stage0 checkout> [mutant numbers]

Prints one RESULT line per mutant (caught / survived) and a table.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FULL = None  # discover the whole suite
MUTANTS = [
    ("meter.py", "residual limit R doubled",
     'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
     'residual_pass = all(value <= 2 * method["residual_max_codes"] for value in residual.values())', FULL),
    ("meter.py", "region tolerance P doubled",
     'deviation <= tolerance + 1e-12', 'deviation <= 2 * tolerance + 1e-12', FULL),
    ("meter.py", "residual check removed entirely",
     'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
     'residual_pass = True', "FitTests SweepTests RecheckTests"),
    ("sweep.py", "--recheck skips its repeat check",
     '    if agrees:\n        record.update(verdict_fields', '    if True:\n        record.update(verdict_fields',
     "RecheckTests"),
]


def main():
    root = Path(sys.argv[1]).resolve()
    pick = [int(a) for a in sys.argv[2:]] or list(range(1, len(MUTANTS) + 1))
    rows = []
    for number in pick:
        file, name, old, new, classes = MUTANTS[number - 1]
        with tempfile.TemporaryDirectory(prefix="r8s0-val-mut-") as tmp:
            tool = Path(tmp) / "tools" / "den-light"
            shutil.copytree(root / "tools" / "den-light", tool)
            (Path(tmp) / "tools" / "fixtures").mkdir()
            shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", Path(tmp) / "tools/fixtures/")
            target = tool / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((name, "not-run", f"pattern found {text.count(old)} times"))
                print(f"RESULT {name}: not-run (pattern found {text.count(old)} times)", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            if classes is None:
                args = [sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", str(tool)]
                label = "full suite"
            else:
                args = [sys.executable, "-B", "-m", "unittest", "-f"] + [f"test_den_light.{c}" for c in classes.split()]
                label = classes
            start = time.time()
            proc = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=1500)
            took = time.time() - start
            lines = proc.stderr.strip().splitlines()
            failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
            ran = [l for l in lines if l.startswith("Ran ")]
            result = "caught" if proc.returncode != 0 else "survived"
            detail = (failed[0] if failed else "") + " " + (ran[-1] if ran else "") + " " + (lines[-1] if lines else "")
            rows.append((name, result, detail.strip()))
            print(f"RESULT {name} [{label}] {result} in {took:.0f}s :: {detail.strip()}", flush=True)
    print()
    for name, result, detail in rows:
        print(f"{result:9} | {name} | {detail}")


if __name__ == "__main__":
    main()
