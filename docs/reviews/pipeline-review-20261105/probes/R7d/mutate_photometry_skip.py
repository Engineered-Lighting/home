#!/usr/bin/env python3
"""mutate_photometry_skip: mutants on the delta 1931c831..9a5023e3 (score_root's skip logic and the
report's Stage 0 response hashes), plus two mutants re-checked from the R7 reviews at 1931c831.

Each mutant is applied to a temp copy of tools/den-photometry (with tools/den-light and
docs/architecture/live-v1 beside it, and a .git folder so RefusalTests see a checkout); the
narrowest test classes run with failfast; a caught/survived table is printed.

    python3 -B mutate_photometry_skip.py <branch-root> [work-dir] [comma-separated mutant ids]

The branch checkout itself is never modified.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="mut-skip-"))
SCORE = ["test_photometry.ScoreTests", "test_photometry.WorksheetTests"]

# (id, file, old, new, test targets in order)
MUTANTS = [
    ("N1-row-for-non-kept-capture-not-refused", "photometry.py",
     '        for n in sorted(set(rows) - set(kept)):\n'
     '            raise Refused(f"{day.name}\\\\{WORKSHEET} has a row for capture {n}, which is not a kept capture")\n',
     '',
     SCORE),
    ("N2-break-after-interrupted-folder", "photometry.py",
     '"reason": "no capture.json (interrupted)"})\n                continue',
     '"reason": "no capture.json (interrupted)"})\n                break',
     SCORE),
    ("N3-error-outcome-counted-as-kept", "photometry.py",
     '            if cap.get("outcome") == "kept":\n                kept[int(match.group(1))] = cap\n            else:',
     '            if cap.get("outcome") in ("kept", "error"):\n                kept[int(match.group(1))] = cap\n            else:',
     SCORE),
    ("N4-response-hashes-not-recorded", "photometry.py",
     '"stage0_response_sha256": sorted(set(responses)),',
     '"stage0_response_sha256": [],',
     SCORE),
    ("R-M9-score-skips-capture-block-check", "photometry.py",
     'if (cap.get("schema") != SCHEMAS["capture"] or cap.get("method_block_sha256") != method.block_sha256',
     'if (cap.get("schema") != SCHEMAS["capture"]',
     ["test_photometry.WorksheetTests", "test_photometry.RefusalTests", "test_photometry.ScoreTests"]),
    ("R-M2-held-out-tol-x1.5", "card.py",
     '"pass": None if tolerance is None else abs(error) <= tolerance + 1e-9}',
     '"pass": None if tolerance is None else abs(error) <= 1.5 * tolerance + 1e-9}',
     ["test_card", "test_photometry.ScoreTests", "test_photometry.WorksheetTests"]),
]


def tree(dest):
    for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()


def main():
    rows = []
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    for mid, fname, old, new, targets in MUTANTS:
        if only and mid not in only:
            continue
        dest = WORK / mid
        if dest.exists():
            shutil.rmtree(dest)
        tree(dest)
        path = dest / "tools/den-photometry" / fname
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            rows.append((mid, fname, "not-run", f"pattern found {text.count(old)} times"))
            print(f"{mid:42s} not-run  pattern found {text.count(old)} times", flush=True)
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        result, by = "survived", "-"
        for target in targets:
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", target],
                                  cwd=dest / "tools/den-photometry", capture_output=True, text=True, timeout=1500)
            if proc.returncode != 0:
                tail = [line for line in proc.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
                result, by = "caught", f"{target}: {tail[0] if tail else 'nonzero exit'}"
                break
            ran = [line for line in proc.stderr.splitlines() if line.startswith("Ran ")]
            by = f"{by if by != '-' else ''}{target} {ran[0] if ran else ''} OK; "
        rows.append((mid, fname, result, by))
        print(f"{mid:42s} {result:8s} {by}", flush=True)
    print("\nMUTANT                                     RESULT   CAUGHT BY / RAN")
    for mid, fname, result, by in rows:
        print(f"{mid:42s} {result:8s} {by}")
    survived = [r for r in rows if r[2] == "survived"]
    print(f"RESULT {len(rows) - len(survived)} caught or not-run, {len(survived)} survived of {len(rows)}")


if __name__ == "__main__":
    main()
