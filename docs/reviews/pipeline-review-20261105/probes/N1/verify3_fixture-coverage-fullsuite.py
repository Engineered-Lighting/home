#!/usr/bin/env python3
"""verify3_fixture-coverage-fullsuite: does the m16 mutant (freeze_labels' "fixture does not cover these
sessions and checks" refusal replaced by `if False:`) survive den-label's WHOLE test suite?

(INTENT verifier, finding fixture-coverage-refusal-untested. The reporter ran only FreezeTests and
DryRunTests; this runs `unittest discover` over the whole den-label folder, no failfast.)

Copies <checkout>/tools/den-label and tools/den-take into a fresh temp dir under <work dir>, applies the
one exact replacement (must match once), runs the suite, prints caught/survived. Synthetic takes only
(the branch's own helpers); nothing leaves 127.0.0.1. The checkout is never modified.

Run:
    python3 -B verify3_fixture-coverage-fullsuite.py <den-label checkout> <work dir>
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
WORK.mkdir(parents=True, exist_ok=True)
OLD = 'if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:'
NEW = 'if False:'

folder = Path(tempfile.mkdtemp(prefix="m16-full-", dir=WORK))
for sub in ("den-label", "den-take"):
    shutil.copytree(ROOT / "tools" / sub, folder / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
path = folder / "tools/den-label/n1.py"
text = path.read_text(encoding="utf-8")
if text.count(OLD) != 1:
    print(f"FAIL pattern occurs {text.count(OLD)} times")
    sys.exit(2)
path.write_text(text.replace(OLD, NEW), encoding="utf-8")
start = time.time()
proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-p", "test*.py"],
                      cwd=folder / "tools/den-label", capture_output=True, text=True, timeout=3000)
tail = [l for l in proc.stderr.strip().splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
fails = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
result = "caught" if proc.returncode != 0 else "survived"
print(f"RESULT m16-freeze-labels-fixture-coverage-dropped full-suite {result} :: {'; '.join(tail)} "
      f"({time.time() - start:.0f} s)")
for line in fails:
    print("  " + line)
shutil.rmtree(folder, ignore_errors=True)
