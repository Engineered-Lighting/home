"""verify_mutants_R0core (REPRODUCE lens, skeptical verifier): re-runs two reported surviving mutants of den-core
independently, plus one positive control that must be caught, so a 'survived' result cannot come from a runner
that never sees failures.

Mutants:
  C_trunc   tools/den-core/check.py   range(max(len(want), len(got))) -> range(min(...))   (finding check-truncation-untested)
  E_match_b tools/den-core/events.py  scene_matches brightness tolerance <= 1 -> <= 3        (finding scene-matches-tolerance-untested)
  CTRL_code tools/den-core/check.py   the 'code changed' refusal disabled                  (control: must be caught)

Each mutant is applied to a copy of the checkout under
  <scratchpad>/mut/R0core-reproduce/copy   (never to the checkout itself)
and the whole den-core suite is run with failfast:
  python3 -B -m unittest discover -f -s tools/den-core -p "test_*.py"
The copy gets a `.git` marker file at its root so events.write's refusal test behaves as in a real checkout.

How to run (synthetic data only; no network):
    python3 -B verify_mutants_R0core.py /path/to/den-core-checkout [MUTANT_ID ...]
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
MUT = Path(__file__).resolve().parents[2] / "mut" / "R0core-reproduce"
D = "tools/den-core/"

MUTANTS = [
    ("CTRL_code", D + "check.py", 'if events.sha256_file(module.__file__) != entry["code_sha256"]:', "if False:"),
    ("C_trunc", D + "check.py", "for index in range(max(len(want), len(got))):",
     "for index in range(min(len(want), len(got))):"),
    ("E_match_b", D + "events.py", 'abs(b - actual["brightness"]) <= 1)', 'abs(b - actual["brightness"]) <= 3)'),
]


def suite(root):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core", "-p",
                        "test_*.py"], cwd=str(root), capture_output=True, text=True, timeout=900,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines = p.stderr.strip().splitlines()
    first = next((line for line in lines if line.startswith(("FAIL:", "ERROR:"))), "")
    ran = next((line for line in lines if line.startswith("Ran ")), "")
    return p.returncode, first, ran, lines[-1] if lines else "", round(time.time() - t, 1)


MUT.mkdir(parents=True, exist_ok=True)
work = MUT / "copy"
if work.exists():
    shutil.rmtree(work)
shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"), symlinks=True)
(work / ".git").write_text("gitdir: /nonexistent\n")
code, first, ran, last, secs = suite(work)
print("baseline: exit=%d %s | %s (%ss)" % (code, ran, last, secs))
if code != 0:
    print("FAIL: baseline suite does not pass in the copy; stopping")
    sys.exit(2)
for mid, rel, old, new in MUTANTS:
    if ONLY and mid not in ONLY:
        continue
    path = work / rel
    text = path.read_text("utf-8")
    if text.count(old) != 1:
        print("RESULT %s | %s | not-run | pattern found %d times" % (mid, rel, text.count(old)))
        continue
    path.write_text(text.replace(old, new), "utf-8")
    try:
        code, first, ran, last, secs = suite(work)
    finally:
        path.write_text(text, "utf-8")
    print("RESULT %s | %s | %s | %s | %s (%ss)" % (mid, rel, "caught" if code else "survived", first or last, ran,
                                                   secs), flush=True)
