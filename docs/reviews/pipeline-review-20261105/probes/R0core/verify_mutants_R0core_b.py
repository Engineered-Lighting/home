"""verify_mutants_R0core_b (REPRODUCE lens, skeptical verifier): independently re-runs three reported surviving
mutants of den-core's validate/run checks, plus one positive control that must be caught, so that a 'survived'
result cannot come from a runner that never sees failures.

Mutants (exact text replacements, each must match exactly once):
  V_inforce  tools/den-core/validate.py  check 3 keeps only the decision in force at the interval start
             (finding validate-check3-inforce-untested)
  V_holdvac  tools/den-core/validate.py  check 4 skips every occupancy-off instant
             (finding validate-check4-vacancy-untested)
  RD_both    tools/den-core/run.py       _both() compares one way only
             (finding diff-two-way-untested)
  CTRL_look  tools/den-core/validate.py  check 3 stops checking the look's scene (control: must be caught)

Each mutant is applied to a fresh copy of the checkout under
  <scratchpad>/mut/R0core-reproduce/vb/copy   (never to the checkout itself)
and the whole den-core suite is run with failfast, bytecode off:
  python3 -B -m unittest discover -f -s tools/den-core -p "test_*.py"
The copy gets a `.git` marker file so events.write's refusal test behaves as in a real checkout.

How to run (synthetic data only; no network):
    python3 -B verify_mutants_R0core_b.py /path/to/den-core-checkout [MUTANT_ID ...]
Prints a RESULT line per mutant: caught / survived, with the suite's last line or first failing test.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
ONLY = set(sys.argv[2:])
MUT = Path(__file__).resolve().parents[2] / "mut" / "R0core-reproduce" / "vb"
D = "tools/den-core/"

MUTANTS = [
    ("CTRL_look", D + "validate.py", "if look is None or not events.scene_matches(look, wanted):", "if look is None:"),
    ("V_inforce", D + "validate.py",
     'in_force = [d for d in mine if d["t"] <= start][-1:] + [d for d in mine if start < d["t"] < end]',
     'in_force = [d for d in mine if d["t"] <= start][-1:]'),
    ("V_holdvac", D + "validate.py",
     'or occupancy == "off" and (vacant_since is None or t - vacant_since < vacancy_s)):',
     'or occupancy == "off"):'),
    ("RD_both", D + "run.py", "    return events.scene_matches(a, b) and events.scene_matches(b, a)",
     "    return events.scene_matches(a, b)"),
]


def suite(root):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core", "-p",
                        "test_*.py"], cwd=str(root), capture_output=True, text=True, timeout=1200,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines = p.stderr.strip().splitlines()
    first = next((line for line in lines if line.startswith(("FAIL:", "ERROR:"))), "")
    ran = next((line for line in reversed(lines) if line.startswith("Ran ")), "")
    return p.returncode, first, ran, lines[-1] if lines else "", round(time.time() - t, 1)


def fresh_copy():
    work = MUT / "copy"
    if work.exists():
        shutil.rmtree(work)
    MUT.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"), symlinks=True)
    (work / ".git").write_text("gitdir: /nonexistent\n")
    return work


work = fresh_copy()
code, first, ran, last, secs = suite(work)
print("baseline: exit=%d %s | %s (%ss)" % (code, ran, last, secs), flush=True)
if code != 0:
    print("FAIL: baseline suite does not pass in the copy; stopping")
    sys.exit(2)
for mid, rel, old, new in MUTANTS:
    if ONLY and mid not in ONLY:
        continue
    path = work / rel
    text = path.read_text("utf-8")
    if text.count(old) != 1:
        print("RESULT %s | %s | not-run | pattern found %d times" % (mid, rel, text.count(old)), flush=True)
        continue
    path.write_text(text.replace(old, new), "utf-8")
    try:
        code, first, ran, last, secs = suite(work)
    finally:
        path.write_text(text, "utf-8")
    print("RESULT %s | %s | %s | %s | %s (%ss)" % (mid, rel, "caught" if code != 0 else "survived", first or last,
                                                   ran, secs), flush=True)
shutil.rmtree(work)
