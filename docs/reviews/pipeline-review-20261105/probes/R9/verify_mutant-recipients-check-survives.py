#!/usr/bin/env python3
"""Verifier probe for finding mutant-recipients-check-survives (score.py mutant M10, claude/den-core-live).

    python3 -B verify_mutant-recipients-check-survives.py <den-core-live checkout root>

Runs two checks and prints their RESULT/PASS/FAIL lines for this mutant:
1. verify_score_mutants.py mutant-recipients-check-survives: the whole test_rung9 module (the only test module importing score.py) unmutated
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
subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_score_mutants.py"), root, "mutant-recipients-check-survives"], check=False)
out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "verify_mutant_behaviour.py"), root],
                     capture_output=True, text=True).stdout
for line in out.splitlines():
    if "M10 " in line or "M10+" in line or line.startswith("INFO baseline"):
        print(line)
