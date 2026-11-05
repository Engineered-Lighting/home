#!/usr/bin/env python3
"""Verifier probe for finding mutant-timing-threshold-survives (score.py mutant M6b, claude/den-core-live).

    python3 -B verify_mutant-timing-threshold-survives.py <den-core-live checkout root>

Runs two checks and prints their RESULT/PASS/FAIL lines for this mutant:
1. verify_score_mutants.py mutant-timing-threshold-survives: the whole test_rung9 module (the only test module importing score.py) unmutated
   and with the mutant; SURVIVED means no test fails that did not already fail unmutated.
2. verify_mutant_behaviour.py: a synthetic input on which the unmutated and mutated score.py disagree (PASS = the
   mutant is not equivalent, so its survival is a real test gap).
Both copy the checkout's tools into a temp folder (set TMPDIR) and never touch the checkout. Synthetic data only.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
root = sys.argv[1]
subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_score_mutants.py"), root, "mutant-timing-threshold-survives"], check=False)
out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_mutant_behaviour.py"), root],
                     capture_output=True, text=True).stdout
for line in out.splitlines():
    if "M6b " in line or "M6b+" in line or line.startswith("INFO baseline"):
        print(line)
