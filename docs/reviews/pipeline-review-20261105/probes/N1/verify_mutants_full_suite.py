#!/usr/bin/env python3
"""verify_mutants_full_suite: do den-label's verify extra-file check, its canonical-manifest check and
load_split's split == rule(order) check survive removal against the WHOLE den-label suite?

(REPRODUCE lens, verifier for findings verify-extra-file-canonical-untested and split-rule-recheck-untested.
The original runner only ran a narrow test subset per mutant; this one runs the full discover suite.)

For each mutant set: copies <checkout>/tools/den-label and <checkout>/tools/den-take into a fresh temp dir
under the given work dir, applies the exact text replacements (each must match exactly once), runs
`python3 -B -m unittest discover -s . -p test*.py` in the copied den-label folder (no failfast, so the
count of failing tests is shown) and records caught (some test fails) or survived (all pass).
Synthetic takes only (the branch's own helpers); nothing leaves 127.0.0.1.

Run:
    python3 verify_mutants_full_suite.py /path/to/den-label-checkout /path/to/mut/work/dir [set-id ...]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
ONLY = set(sys.argv[3:])

M3 = ('    for rel in sorted(present - set(expected)):', '    for rel in sorted(set()):')
M12 = ('    if canonical(manifest) != raw:', '    if False:')
M4 = ('                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):',
      '                or entry["order"] != counts[entry["script"]]):')

SETS = [
    ("ALL", "M3+M12+M4 together", [M3, M12, M4]),
    ("M3", "verify: no extra-file report", [M3]),
    ("M12", "verify: no canonical-manifest check", [M12]),
    ("M4", "load_split: no split == rule(order) check", [M4]),
]


def run(sid, what, repls):
    work = Path(tempfile.mkdtemp(prefix=f"vmut-{sid}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "tools" / "den-label" / "n1.py"
    text = target.read_text(encoding="utf-8")
    for old, new in repls:
        if text.count(old) != 1:
            return sid, what, "not-run", f"pattern found {text.count(old)} times"
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8", newline="\n")
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", ".", "-p", "test*.py"],
                          cwd=work / "tools" / "den-label", capture_output=True, text=True, timeout=3000)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "")
    tail = lines[-1] if lines else ""
    result = "caught" if proc.returncode != 0 else "survived"
    if proc.returncode != 0:
        (WORK / f"vmut-{sid}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return sid, what, result, f"{ran}; {tail} ({time.time() - started:.0f} s)"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in SETS:
        if ONLY and s[0] not in ONLY:
            continue
        row = run(*s)
        rows.append(row)
        print(f"{row[0]:4} {row[2]:9} {row[1]} :: {row[3]}", flush=True)
    print("\nTABLE")
    for sid, what, result, note in rows:
        print(f"{sid:4} | {result:9} | {what} | {note}")
    print(f"RESULT caught {sum(r[2] == 'caught' for r in rows)} survived {sum(r[2] == 'survived' for r in rows)}")


if __name__ == "__main__":
    main()
