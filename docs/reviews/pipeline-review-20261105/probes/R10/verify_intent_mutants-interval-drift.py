#!/usr/bin/env python3
"""Verifier re-run of three den-verify (rung 10) mutants, independently of the reporter.

What it checks: copies tools/den-verify and docs/architecture/live-v1 of the
given checkout into a temp dir, applies each mutant ALONE to its own copy, and
runs the tool's unittest suite on it:
  M10 drift rule drops 'not occ'            (finding mutant-drift-occupancy)
  M11 interval a = 1 - level (5%/95%)        (finding mutant-bootstrap-interval)
  M12 bootstrap never resamples              (finding mutant-bootstrap-interval)
Mode 'narrow' (default) runs only the test class that should catch it; mode
'full' runs baseline + every mutant through the whole suite, in parallel
processes. Synthetic data only (the suite's own fixtures); no network; writes
only inside a temp dir.

Run:
    python3 verify_intent_mutants-interval-drift.py <root of the rung-10-verify checkout> [narrow|full]
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    ("M10-drift-ignores-occupancy", "score.py",
     "if (inc[\"status\"] == \"outside\" and not occ",
     "if (inc[\"status\"] == \"outside\"",
     "test_verify.DaylightAndDrift"),
    ("M11-interval-one-sided", "score.py",
     "a = (1 - m[\"interval_level\"]) / 2",
     "a = (1 - m[\"interval_level\"])",
     "test_verify.HitRateBoundary"),
    ("M12-interval-no-resample", "score.py",
     "pick = [sessions[rng.randrange(len(sessions))] for _ in sessions]",
     "pick = list(sessions)",
     "test_verify.HitRateBoundary"),
]


def cmd(narrow_target):
    if narrow_target:
        return [sys.executable, "-m", "unittest", "-f", narrow_target]
    return [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-f"]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1]).resolve()
    mode = sys.argv[2] if len(sys.argv) > 2 else "narrow"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory() as tmp:
        jobs = []
        variants = [("baseline", None, None, None, None)] + MUTANTS if mode == "full" else MUTANTS
        for mid, fname, old, new, target in variants:
            work = Path(tmp) / mid
            (work / "tools").mkdir(parents=True)
            shutil.copytree(src / "tools" / "den-verify", work / "tools" / "den-verify",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(src / "docs" / "architecture" / "live-v1", work / "docs" / "architecture" / "live-v1")
            if fname:
                f = work / "tools" / "den-verify" / fname
                text = f.read_text(encoding="utf-8")
                n = text.count(old)
                if n != 1:
                    print(f"RESULT {mid} not-applied (pattern found {n} times)")
                    continue
                f.write_text(text.replace(old, new), encoding="utf-8")
            c = cmd(None if mode == "full" else target)
            p = subprocess.Popen(c, cwd=work / "tools" / "den-verify", env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            jobs.append((mid, target if mode != "full" else "whole suite", p, time.time()))
            if mode != "full":
                p.wait()
        for mid, target, p, t0 in jobs:
            out, err = p.communicate(timeout=3600)
            lines = (err or "").strip().splitlines()
            first_fail = next((l for l in lines if l.startswith(("FAIL:", "ERROR:"))), "")
            ran = next((l for l in lines if l.startswith("Ran ")), "")
            verdict = "caught" if p.returncode != 0 else "survived"
            if mid == "baseline":
                verdict = "baseline-pass" if p.returncode == 0 else "baseline-FAIL"
            print(f"RESULT {mid:30} {verdict:9} [{target}] {ran}; last: {lines[-1] if lines else ''} "
                  f"{first_fail} ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
