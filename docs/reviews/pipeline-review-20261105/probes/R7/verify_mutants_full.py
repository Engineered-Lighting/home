#!/usr/bin/env python3
"""(R7 reproduce lens) Re-runs four reported surviving mutants of tools/den-photometry/card.py against the
WHOLE den-photometry suite (not a narrow subset), each in its own temp copy, and prints caught/survived.

Mutants (same text changes the reports describe):
  M1-open-proposals-ignored  card.py:144  refuse only open slots that are empty
  M2-held-out-tol-x1.5       card.py:336  held-out pass at |e| <= 1.5 * meter_tolerance_lx
  M4-invariance-tol-x1.5     card.py:293  invariance pass at d <= 1.5 * invariance_tolerance_percent
  M5-drift-x1.5              card.py:309  reading used at drift <= 1.5 * meter_drift_max_percent

Run:
    python3 verify_mutants_full.py <root of the branch checkout> <work dir> [mutant-id ...]

Copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 into <work dir>/<mutant>/ (plus
an empty .git folder so the suite's "inside a git checkout" test sees a checkout), applies the mutant, runs
`python3 -B -m unittest discover -s tools/den-photometry` there (no failfast, so the full count is shown)
and prints one RESULT line per mutant. The checkout itself is never modified. The suite uses only synthetic
data and 127.0.0.1 fakes.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
PICK = sys.argv[3:]
CARD = "tools/den-photometry/card.py"
MUTANTS = {
    "M0-unmutated": (CARD, "    if method.open:\n", "    if method.open:\n"),
    "M1-open-proposals-ignored": (CARD, "    if method.open:\n",
                                  "    if any(method[n] is None for n in method.open):  # MUTANT\n"),
    "M2-held-out-tol-x1.5": (CARD, "abs(error) <= tolerance + 1e-9}", "abs(error) <= 1.5 * tolerance + 1e-9}"),
    "M4-invariance-tol-x1.5": (CARD, 'd <= v["invariance_tolerance_percent"] + 1e-12',
                               'd <= 1.5 * v["invariance_tolerance_percent"] + 1e-12'),
    "M5-drift-x1.5": (CARD, 'drift <= v["meter_drift_max_percent"] + 1e-12',
                      'drift <= 1.5 * v["meter_drift_max_percent"] + 1e-12'),
}

for mid in PICK or list(MUTANTS):
    rel, old, new = MUTANTS[mid]
    copy = WORK / mid
    if copy.exists():
        shutil.rmtree(copy)
    for sub in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / sub, copy / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / ".git").mkdir()
    target = copy / rel
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        print(f"RESULT {mid}: NOT RUN (pattern found {text.count(old)} times)")
        continue
    target.write_text(text.replace(old, new), encoding="utf-8")
    start = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry"],
                          cwd=copy, capture_output=True, text=True, timeout=1500)
    took = time.time() - start
    lines = (proc.stderr or "").strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "?")
    fails = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
    verdict = "caught" if proc.returncode else "survived"
    print(f"RESULT {mid}: {verdict} ({ran}; last line {lines[-1] if lines else '?'}; {took:.0f}s)")
    for l in fails[:5]:
        print(f"   {l}")
    sys.stdout.flush()
