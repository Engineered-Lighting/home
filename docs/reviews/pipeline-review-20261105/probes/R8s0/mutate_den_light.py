#!/usr/bin/env python3
"""mutate_den_light.py: mutation check of rung 8 Stage 0's gate logic (tools/den-light).

Each mutant is one exact-string replacement in meter.py or sweep.py, applied to a fresh temp copy of
tools/den-light (plus the fixture the suite reads). The narrowest test class that should catch it is
run with failfast. Prints a caught/survived table.

How to run:
  python3 -B mutate_den_light.py <root of the claude/rung-8-stage0 checkout>

Nothing in the checkout is modified; tests use synthetic data and fakes on 127.0.0.1 only.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    ("meter.py", "gain agreement vacuous on no frames",
     'gain_pass = bool(gains) and all(g["pass"] for g in gains)',
     'gain_pass = all(g["pass"] for g in gains)', "FitTests SweepTests"),
    ("meter.py", "region tolerance P doubled",
     'deviation <= tolerance + 1e-12', 'deviation <= 2 * tolerance + 1e-12', "FitTests"),
    ("meter.py", "residual limit R doubled",
     'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
     'residual_pass = all(value <= 2 * method["residual_max_codes"] for value in residual.values())', "FitTests"),
    ("meter.py", "region deviation uses the best code, not the worst",
     'max(abs(scale * q - 1) for q in ratios)', 'min(abs(scale * q - 1) for q in ratios)', "FitTests"),
    ("meter.py", "an untested region counts as failing",
     '"scale": None, "deviation_percent": None, "pass": True}',
     '"scale": None, "deviation_percent": None, "pass": False}', "FitTests"),
    ("sweep.py", "no repeat check in the live sweep",
     'if not meter.agree(self.means(first["nv12"]), self.means(repeat["nv12"]),',
     'if False and not meter.agree(self.means(first["nv12"]), self.means(repeat["nv12"]),', "SweepTests"),
    ("sweep.py", "settling ignores the hold generation",
     'agreeing = (report["held"] and report["generation"] == generation and meter.gain_agreement(',
     'agreeing = (report["held"] and meter.gain_agreement(', "SweepTests"),
    ("sweep.py", "--recheck skips the repeat check",
     '    if agrees:\n        record.update(verdict_fields', '    if True:\n        record.update(verdict_fields',
     "RecheckTests"),
    ("sweep.py", "light check allows ISO up to twice iso_max",
     'if t < 1 or iso > method["iso_max"]:', 'if t < 1 or iso > 2 * method["iso_max"]:',
     "ExposurePlanTests TooDarkTests"),
    ("sweep.py", "--run ignores open METHOD slots",
     '    if method.open:\n        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))\n    stage0 = run_folder(stage0, token_file, may_hold_sweeps=True)',
     '    stage0 = run_folder(stage0, token_file, may_hold_sweeps=True)', "SweepTests"),
]


def main():
    root = Path(sys.argv[1]).resolve()
    rows = []
    for file, name, old, new, classes in MUTANTS:
        with tempfile.TemporaryDirectory(prefix="r8s0-mut-") as tmp:
            tool = Path(tmp) / "tools" / "den-light"
            shutil.copytree(root / "tools" / "den-light", tool)
            (Path(tmp) / "tools" / "fixtures").mkdir()
            shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", Path(tmp) / "tools/fixtures/")
            target = tool / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((file, name, "not-run", f"pattern found {text.count(old)} times", 0))
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            args = [sys.executable, "-B", "-m", "unittest", "-f"] + [f"test_den_light.{c}" for c in classes.split()]
            start = time.time()
            proc = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=900)
            took = time.time() - start
            tail = proc.stderr.strip().splitlines()
            failed = [l for l in tail if l.startswith(("FAIL:", "ERROR:"))]
            result = "caught" if proc.returncode != 0 else "survived"
            rows.append((file, name, result, (failed[0] if failed else tail[-1] if tail else "")[:110], took))
            print(f"{result:9} {file:9} {name} [{classes}] {took:.0f}s :: {rows[-1][3]}", flush=True)
    print()
    print(f"{'result':9} | {'file':9} | mutant | caught by")
    for file, name, result, by, _ in rows:
        print(f"{result:9} | {file:9} | {name} | {by}")
    survived = [r for r in rows if r[2] == "survived"]
    print(f"RESULT: {len(rows) - len(survived)} caught or not-run, {len(survived)} survived of {len(rows)}")


if __name__ == "__main__":
    main()
