#!/usr/bin/env python3
"""verify_intent_mutants.py: independent re-run (verifier, intent lens) of the mutants behind
findings residual-gate-untested (M1), region-tolerance-untested (M2), region-any-untested (M4)
and recheck-open-refusal-masked (M13) on claude/rung-8-stage0's tools/den-light.

Each mutant is one exact-string replacement applied to a fresh temp copy of tools/den-light
(plus tools/fixtures, which the suite reads). The WHOLE suite (python -m unittest -f test_den_light)
runs on the copy. 'survived' means every test passed with the mutant in place.

How to run:
  python3 -B verify_intent_mutants.py <root of the claude/rung-8-stage0 checkout> [M1 M2 ...]

Nothing in the checkout is modified. Tests use synthetic data and fakes on 127.0.0.1 only.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = {
    "M1": ("meter.py", "residual gate never fails",
           'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
           'residual_pass = True'),
    "M2": ("meter.py", "region tolerance P loosened 10x (percent/fraction slip)",
           'tolerance = method["region_tolerance_percent"] / 100.0',
           'tolerance = method["region_tolerance_percent"] / 10.0'),
    "M4": ("meter.py", "region test passes if ANY region passes",
           'return {"pass": all(e["pass"] for e in out),',
           'return {"pass": any(e["pass"] for e in out),'),
    "M13": ("sweep.py", "open-slot refusal removed from recheck()",
            '    if method.open:\n        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))\n    folder = Path(folder)',
            '    folder = Path(folder)'),
    "M1b": ("meter.py", "residual limit R doubled (boundary)",
            'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
            'residual_pass = all(value <= 2 * method["residual_max_codes"] for value in residual.values())'),
}


def main():
    root = Path(sys.argv[1]).resolve()
    wanted = sys.argv[2:] or list(MUTANTS)
    for key in wanted:
        file, name, old, new = MUTANTS[key]
        with tempfile.TemporaryDirectory(prefix="r8s0-verify-") as tmp:
            tool = Path(tmp) / "tools" / "den-light"
            shutil.copytree(root / "tools" / "den-light", tool)
            shutil.copytree(root / "tools" / "fixtures", Path(tmp) / "tools" / "fixtures")
            target = tool / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"RESULT {key} not-run: pattern found {text.count(old)} times", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            start = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_light"],
                                  cwd=tool, capture_output=True, text=True, timeout=3000)
            took = time.time() - start
            lines = proc.stderr.strip().splitlines()
            failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
            ran = [l for l in lines if l.startswith("Ran ")]
            verdict = "caught" if proc.returncode != 0 else "survived"
            print(f"RESULT {key} {verdict} ({took:.0f} s): {name} -- "
                  f"{failed[0] if failed else ''} {ran[0] if ran else ''} {lines[-1] if lines else ''}", flush=True)


if __name__ == "__main__":
    main()
