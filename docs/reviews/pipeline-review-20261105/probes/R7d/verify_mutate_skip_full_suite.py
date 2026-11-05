#!/usr/bin/env python3
"""verify_mutate_skip_full_suite: independent re-check (verifier, intent lens) of finding
unreadable-kept-capture-guard-untested. Applies three mutants to score_root's skip logic, each to
its own temp copy of tools/den-photometry (with tools/den-light and docs/architecture/live-v1
beside it, plus an empty .git folder as the reporter's runner does), and runs the WHOLE
den-photometry suite (unittest discover, failfast) for each mutant in parallel. Prints a
caught/survived table.

    python3 -B verify_mutate_skip_full_suite.py <branch-root> <work-dir>

Synthetic data and fakes on 127.0.0.1 only (the suite's own). The branch checkout is never modified.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()

MUTANTS = [
    ("N1-row-for-non-kept-capture-not-refused",
     '        for n in sorted(set(rows) - set(kept)):\n'
     '            raise Refused(f"{day.name}\\\\{WORKSHEET} has a row for capture {n}, which is not a kept capture")\n',
     ''),
    ("N2-break-after-interrupted-folder",
     '"reason": "no capture.json (interrupted)"})\n                continue',
     '"reason": "no capture.json (interrupted)"})\n                break'),
    ("N3-error-outcome-counted-as-kept",
     '            if cap.get("outcome") == "kept":\n                kept[int(match.group(1))] = cap\n            else:',
     '            if cap.get("outcome") in ("kept", "error"):\n                kept[int(match.group(1))] = cap\n            else:'),
]


def main():
    procs = []
    for mid, old, new in MUTANTS:
        dest = WORK / mid
        if dest.exists():
            shutil.rmtree(dest)
        for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
            shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (dest / ".git").mkdir()
        path = dest / "tools/den-photometry/photometry.py"
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"{mid:42s} not-run  pattern found {text.count(old)} times", flush=True)
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        log = open(WORK / f"{mid}.log", "w")
        proc = subprocess.Popen([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s",
                                 "tools/den-photometry", "-v"], cwd=dest, stdout=log, stderr=subprocess.STDOUT)
        procs.append((mid, proc, log))
    rows = []
    for mid, proc, log in procs:
        code = proc.wait(timeout=1800)
        log.close()
        text = (WORK / f"{mid}.log").read_text()
        ran = [line for line in text.splitlines() if line.startswith("Ran ")]
        bad = [line for line in text.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
        result = "survived" if code == 0 else "caught"
        rows.append((mid, result, (ran[0] if ran else "") + (" " + bad[0] if bad else "")))
    print("MUTANT                                     RESULT   RAN / CAUGHT BY")
    for mid, result, by in rows:
        print(f"{mid:42s} {result:8s} {by}")
    print(f"RESULT {sum(r[1] == 'survived' for r in rows)} survived of {len(rows)} (whole suite)")


if __name__ == "__main__":
    main()
