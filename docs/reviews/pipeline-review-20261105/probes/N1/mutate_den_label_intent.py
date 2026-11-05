#!/usr/bin/env python3
"""mutate_den_label_intent: do verify_folder's extra-file and canonical checks, and load_split's
split == rule(order) check, survive den-label's WHOLE test suite (not only a narrow subset)?

(INTENT lens verifier. Its own file name, so other reviewers' runners are untouched.)

Copies <checkout>/tools/den-label and <checkout>/tools/den-take into a fresh temp dir under the
given work dir, applies the listed exact text replacements (each must match exactly once), and runs
`python -B -m unittest discover -s . -v` there (the README's command, without -f, so every failing
test is listed). Mode "combined" applies all three mutants at once: since each mutant only removes a
check (fewer problems / refusals), a combined pass means each one survives alone too. Mode "each"
runs the narrow test modules for each mutant one by one with failfast.
Synthetic takes only (the branch's own test helpers); nothing leaves 127.0.0.1; temp dirs only.

Run:
    python3 mutate_den_label_intent.py /path/to/den-label-checkout /path/to/scratch/mut/N1-intent combined
    python3 mutate_den_label_intent.py /path/to/den-label-checkout /path/to/scratch/mut/N1-intent each
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
MODE = sys.argv[3] if len(sys.argv) > 3 else "combined"

MUTANTS = [
    ("M3", "verify_folder no longer reports an extra file in a frozen folder",
     "    for rel in sorted(present - set(expected)):", "    for rel in sorted(set()):",
     ["test_den_label.FreezeTests", "test_den_label.FreezeLogTests", "test_den_label.DryRunTests"]),
    ("M12", "verify_folder no longer checks that the manifest is canonical JSON",
     "    if canonical(manifest) != raw:", "    if False:",
     ["test_den_label.FreezeTests", "test_den_label.FreezeLogTests", "test_den_label.DryRunTests"]),
    ("M4", "load_split drops the split == rule(order) check",
     '                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):',
     '                or entry["order"] != counts[entry["script"]]):',
     ["test_den_label.SplitTests", "test_den_label.SessionTests"]),
]


def fresh(tag):
    work = Path(tempfile.mkdtemp(prefix=f"mut-{tag}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    return work


def apply(work, mutants):
    target = work / "tools" / "den-label" / "n1.py"
    text = target.read_text(encoding="utf-8")
    for mid, _, old, new, _ in mutants:
        if text.count(old) != 1:
            raise SystemExit(f"FAIL mutant {mid} pattern found {text.count(old)} times")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8", newline="\n")


def run(work, args):
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest"] + args, cwd=work / "tools" / "den-label",
                          capture_output=True, text=True, timeout=1800)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "")
    failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
    return proc.returncode, ran, (lines[-1] if lines else ""), failed, time.time() - started


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    if MODE == "combined":
        work = fresh("combined")
        try:
            apply(work, MUTANTS)
            code, ran, last, failed, secs = run(work, ["discover", "-s", ".", "-v"])
            verdict = "caught" if code else "survived"
            print(f"RESULT combined {','.join(m[0] for m in MUTANTS)} full suite: {verdict} :: {ran} {last} ({secs:.0f} s)")
            for line in failed:
                print("  ", line)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    else:
        for m in MUTANTS:
            work = fresh(m[0])
            try:
                apply(work, [m])
                code, ran, last, failed, secs = run(work, ["-f"] + m[4])
                print(f"RESULT {m[0]:4} {'caught' if code else 'survived':9} {m[1]} :: {ran} {last} ({secs:.0f} s)")
            finally:
                shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
