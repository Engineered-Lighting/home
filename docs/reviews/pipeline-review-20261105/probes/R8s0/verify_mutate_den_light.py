#!/usr/bin/env python3
"""verify_mutate_den_light.py: independent re-run of the four "untested gate" mutants reported for
rung 8 Stage 0 (tools/den-light), each against the WHOLE test suite (not a narrow class).

Findings covered (mutant id -> finding id):
  RES  -> residual-gate-untested        meter.py: residual_pass = True
  ANY  -> region-any-untested           meter.py: region test passes if any region passes
  TOL  -> region-tolerance-untested     meter.py: tolerance P loosened 10x (percent / 10)
  OPEN -> recheck-open-refusal-masked   sweep.py: recheck() no longer refuses open METHOD numbers

Each mutant is one exact-string replacement applied to a fresh copy of tools/den-light (plus the
fixture the suite reads) made under <mut-dir>. The whole suite (test_den_light) is run with
failfast; "caught" = suite failed, "survived" = whole suite passed. Synthetic data and fakes on
127.0.0.1 only (the suite's own); nothing in the checkout is modified.

How to run:
  python3 -B verify_mutate_den_light.py <root of the claude/rung-8-stage0 checkout> [<mut-dir>] [ID ...]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = {
    "RES": ("meter.py", "residual gate never fails",
            'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
            'residual_pass = True'),
    "ANY": ("meter.py", "region test passes if any region passes",
            'return {"pass": all(e["pass"] for e in out)',
            'return {"pass": any(e["pass"] for e in out)'),
    "TOL": ("meter.py", "region tolerance P loosened 10x",
            'tolerance = method["region_tolerance_percent"] / 100.0',
            'tolerance = method["region_tolerance_percent"] / 10.0'),
    "OPEN": ("sweep.py", "recheck() skips the open-number refusal",
             '    """Recompute a saved sweep\'s repeat check and verdict; writes recheck-<n>.json beside response.json."""\n'
             '    if method.open:\n'
             '        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))\n',
             '    """Recompute a saved sweep\'s repeat check and verdict; writes recheck-<n>.json beside response.json."""\n'),
}


def main():
    root = Path(sys.argv[1]).resolve()
    mut_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.gettempdir())
    wanted = sys.argv[3:] or list(MUTANTS)
    mut_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for mid in wanted:
        file, name, old, new = MUTANTS[mid]
        tmp = Path(tempfile.mkdtemp(prefix=f"mut-{mid}-", dir=mut_dir))
        try:
            tool = tmp / "tools" / "den-light"
            shutil.copytree(root / "tools" / "den-light", tool)
            (tmp / "tools" / "fixtures").mkdir()
            shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", tmp / "tools/fixtures/")
            target = tool / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((mid, name, "not-run", f"pattern found {text.count(old)} times", 0))
                print(f"RESULT {mid} not-run: pattern found {text.count(old)} times", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            args = [sys.executable, "-B", "-m", "unittest", "-f", "test_den_light"]
            start = time.time()
            proc = subprocess.run(args, cwd=tool, capture_output=True, text=True, timeout=1800)
            took = time.time() - start
            lines = proc.stderr.strip().splitlines()
            failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
            ran = [l for l in lines if l.startswith("Ran ")]
            result = "caught" if proc.returncode != 0 else "survived"
            detail = (failed[0] if failed else "") + " | " + (ran[0] if ran else "") + " | " + (lines[-1] if lines else "")
            rows.append((mid, name, result, detail, took))
            print(f"RESULT {mid} {result} ({took:.0f} s): {name} :: {detail}", flush=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("mutant | result | description | detail")
    for mid, name, result, detail, _ in rows:
        print(f"{mid} | {result} | {name} | {detail}")
    survived = [r for r in rows if r[2] == "survived"]
    print(f"RESULT: {len(survived)} survived of {len(rows)}")


if __name__ == "__main__":
    main()
