#!/usr/bin/env python3
"""(R7 verifier, intent-and-spec lens) Re-checks the four reported surviving card.py mutants.

For each mutant it
  1. shows the mutant is NOT equivalent: it imports the mutated card.py from a temp copy and runs one small
     synthetic input on which the original and the mutant disagree (SEM-ORIG / SEM-MUT lines);
  2. runs the given unittest targets (failfast) in that temp copy and prints caught/survived.

Mutants (the same text changes the findings describe):
  M1-open-proposals-ignored  card.py:144  refuse only open slots that are empty
  M2-held-out-tol-x1.5       card.py:336  held-out pass at |e| <= 1.5 * meter_tolerance_lx
  M4-invariance-tol-x1.5     card.py:293  invariance pass at d <= 1.5 * invariance_tolerance_percent
  M5-drift-x1.5              card.py:309  reading used at drift <= 1.5 * meter_drift_max_percent

Run:
    python3 intent_mutants.py <root of the claude/rung-7-photometry checkout> <work dir> <mutant-id> [test ...]
e.g.
    python3 intent_mutants.py $ROOT $WORK M2-held-out-tol-x1.5 test_card test_photometry.ScoreTests

Copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 into <work dir>/<mutant>/ with an
empty .git folder (the suite has a "photometry folder inside a git checkout" test); the checkout itself is
never written. Synthetic data only; the suite's fakes bind to 127.0.0.1.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT, WORK, MID = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
TESTS = sys.argv[4:]
MUTANTS = {
    "M0-unmutated": ("    if method.open:\n", "    if method.open:\n"),
    "M1-open-proposals-ignored": ("    if method.open:\n",
                                  "    if any(method[n] is None for n in method.open):  # MUTANT\n"),
    "M2-held-out-tol-x1.5": ("abs(error) <= tolerance + 1e-9}", "abs(error) <= 1.5 * tolerance + 1e-9}"),
    "M4-invariance-tol-x1.5": ('d <= v["invariance_tolerance_percent"] + 1e-12',
                               'd <= 1.5 * v["invariance_tolerance_percent"] + 1e-12'),
    "M5-drift-x1.5": ('drift <= v["meter_drift_max_percent"] + 1e-12',
                      'drift <= 1.5 * v["meter_drift_max_percent"] + 1e-12'),
}
SEM = r'''
import sys; sys.path.insert(0, "tools/den-photometry"); import card
base = card.load_method()
owner = dict(commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10, meter_class="x")
closed = base.closed(**owner)
# M1: the three owner slots filled, every other proposal still marked open
partly = base.replace(**owner)
print("M1 open marks left:", len(partly.open), "| ready_problems refuses:", bool(card.ready_problems(partly)))
# M2: a held-out point 14 lx off under a 10 lx tolerance
fit_c = {"C_nc": 100.0, "u_C_nc": 0.0}; fit_r = card.meter_reading(174.533, 174.533, closed)
k = card.k_L_fit(fit_c, fit_r, closed)["k_L"]
chk = card.held_out_check(fit_c, fit_r, k, {"C_nc": 50.0, "u_C_nc": 0.0}, card.meter_reading(87.2665 - 14, 87.2665 - 14, closed), closed)
print("M2 |e| = %.2f lx, tol 10 -> pass %s" % (abs(chk["error_lx"]), chk["pass"]))
# M4: a box whose hold 1 and hold 2 differ by 4.4 %
h = lambda C: {"C_nc": C, "sem_nc": 0.0, "pixels": 400, "flagged": 0, "pixel_frames": 1200, "mean_codes": [100, 100, 100]}
r = card.box_reading(h(102.2), h(97.8), True, closed)
print("M4 d = %.2f %% (tol 3) -> invariance_pass %s" % (r["invariance_pct"], r["invariance_pass"]))
# M5: before 100 lx, after 107 lx (6.8 % apart)
mr = card.meter_reading(100.0, 107.0, closed)
print("M5 drift = %.2f %% (max 5) -> used %s" % (mr["drift_pct"], mr["used"]))
'''


def semantic(copy):
    p = subprocess.run([sys.executable, "-B", "-c", SEM], cwd=copy, capture_output=True, text=True, timeout=120)
    return (p.stdout + p.stderr).strip()


old, new = MUTANTS[MID]
copy = WORK / MID
if copy.exists():
    shutil.rmtree(copy)
for sub in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
    shutil.copytree(ROOT / sub, copy / sub, ignore=shutil.ignore_patterns("__pycache__"))
(copy / ".git").mkdir()
for line in semantic(copy).splitlines():
    print("SEM-ORIG " + line)
target = copy / "tools/den-photometry/card.py"
text = target.read_text(encoding="utf-8")
if text.count(old) != 1:
    print(f"RESULT {MID}: NOT RUN (pattern found {text.count(old)} times)")
    sys.exit(2)
target.write_text(text.replace(old, new), encoding="utf-8")
for line in semantic(copy).splitlines():
    print("SEM-MUT  " + line)
if TESTS:
    start = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *TESTS], cwd=copy / "tools/den-photometry",
                       capture_output=True, text=True, timeout=1500)
    lines = p.stderr.strip().splitlines()
    ran = next((x for x in lines if x.startswith("Ran ")), "?")
    first = next((x for x in lines if x.startswith(("FAIL:", "ERROR:"))), "")
    print(f"RESULT {MID}: {'caught' if p.returncode else 'survived'} ({ran}; {lines[-1] if lines else '?'}; "
          f"{time.time() - start:.0f}s) {first} tests={' '.join(TESTS)}")
shutil.rmtree(copy, ignore_errors=True)
