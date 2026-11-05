#!/usr/bin/env python3
"""verify_unreadable-kept-capture-guard-untested: independent re-check (verifier) of finding
"unreadable-kept-capture-guard-untested". Mutation runner on den-photometry's score_root skip logic.

For each mutant below, copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 of
the given checkout into <work-dir>/<mutant> (plus an empty .git folder so the suite's "inside a git
checkout" refusal test still sees a checkout), applies the mutant (the pattern must match exactly
once), and runs the WHOLE den-photometry suite (unittest discover, failfast) - not only the score
classes - all mutants in parallel. "baseline" is the unmutated copy. Prints a caught/survived table.

  M1 the refusal of a worksheet row whose capture is not kept is deleted (photometry.py:610-611)
  M2 'continue' after filing a folder as interrupted becomes 'break'
  M3 an 'error' capture counts as kept

Run:
    python3 -B verify_unreadable-kept-capture-guard-untested.py <root of the branch checkout> <work-dir>
The branch checkout itself is never modified. Synthetic data and the suite's 127.0.0.1 fake only.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()

MUTANTS = [
    ("baseline", None, None),
    ("M1-no-row-refusal",
     '        for n in sorted(set(rows) - set(kept)):\n'
     '            raise Refused(f"{day.name}\\\\{WORKSHEET} has a row for capture {n}, which is not a kept capture")\n',
     ''),
    ("M2-break-after-interrupted",
     '"no capture.json (interrupted)"})\n                continue\n',
     '"no capture.json (interrupted)"})\n                break\n'),
    ("M3-error-counts-as-kept",
     '            if cap.get("outcome") == "kept":\n',
     '            if cap.get("outcome") in ("kept", "error"):\n'),
]


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    procs = []
    for mid, old, new in MUTANTS:
        dest = WORK / mid
        if dest.exists():
            shutil.rmtree(dest)
        for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
            shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (dest / ".git").mkdir()
        if old is not None:
            path = dest / "tools/den-photometry/photometry.py"
            text = path.read_text(encoding="utf-8")
            count = text.count(old)
            if count != 1:
                print(f"{mid:30s} NOT-RUN pattern found {count} times")
                continue
            path.write_text(text.replace(old, new), encoding="utf-8")
        log = open(WORK / f"{mid}.log", "w")
        p = subprocess.Popen([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-photometry"],
                             cwd=dest, stdout=log, stderr=subprocess.STDOUT)
        procs.append((mid, p, log, time.time()))
    rows = []
    for mid, p, log, t0 in procs:
        code = p.wait(timeout=1800)
        log.close()
        text = (WORK / f"{mid}.log").read_text()
        ran = [line for line in text.splitlines() if line.startswith("Ran ")]
        first_fail = [line for line in text.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
        if mid == "baseline":
            result = "OK" if code == 0 else "BASELINE-BROKEN"
        else:
            result = "survived" if code == 0 else "caught"
        rows.append((mid, result, (ran[0] if ran else "?") + (f"; {first_fail[0]}" if first_fail else "")))
        print(f"{mid:30s} {result:9s} {rows[-1][2]}  ({time.time() - t0:.0f} s)", flush=True)
    print("\nMUTANT                         RESULT    DETAIL")
    for mid, result, detail in rows:
        print(f"{mid:30s} {result:9s} {detail}")
    survived = [r for r in rows if r[1] == "survived"]
    print(f"RESULT {len(survived)} of {len(rows) - 1} mutants survived the whole den-photometry suite")


if __name__ == "__main__":
    main()
