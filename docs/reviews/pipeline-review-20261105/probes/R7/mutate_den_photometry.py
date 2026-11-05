#!/usr/bin/env python3
"""mutate_den_photometry: apply each gate-logic mutant to a temp copy of tools/den-photometry (with
tools/den-light and docs/architecture/live-v1 beside it), run the narrowest test module(s) with
failfast, and print a caught/survived table.

    python3 -B mutate_den_photometry.py <branch-root> [work-dir] [comma-separated mutant ids]

work-dir defaults to a new temp dir. The branch checkout itself is never modified.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="mut-photometry-"))

# (id, file, old, new, test targets in order)
MUTANTS = [
    ("M1-heldout-any", "card.py",
     'held_result = "pass" if all(p["pass"] for g in groups.values() for p in g["points"]) else "fail"',
     'held_result = "pass" if any(p["pass"] for g in groups.values() for p in g["points"]) else "fail"',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M2-no-daylight-coverage", "card.py",
     'if not any(c["illuminant"] == v["daylight_class"] for c in held):',
     'if False:',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M3-min-days-off-by-one", "card.py",
     'if len(days) < v["min_days"]:',
     'if len(days) < v["min_days"] - 1:',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M4-fit-repeats-held-out", "card.py",
     'if fit is not None and cap["day"] == fit["day"] and cap["point"] == v["fit_point"]:',
     'if False:',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M5-roundtrip-ignores-version", "card.py",
     'and abs(E_back - E) <= v["round_trip_E_back_tolerance_lx"] and matches)',
     'and abs(E_back - E) <= v["round_trip_E_back_tolerance_lx"])',
     ["test_card"]),
    ("M6-anchored-ignores-heldout", "card.py",
     'anchored = trip["result"] == "pass" and invariance["result"] == "pass" and held_result == "pass"',
     'anchored = trip["result"] == "pass" and invariance["result"] == "pass"',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M7-heldout-one-sided", "card.py",
     '"pass": None if tolerance is None else abs(error) <= tolerance + 1e-9}',
     '"pass": None if tolerance is None else error <= tolerance + 1e-9}',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M8-invariance-tol-doubled", "card.py",
     '"invariance_pass": d <= v["invariance_tolerance_percent"] + 1e-12',
     '"invariance_pass": d <= 2 * v["invariance_tolerance_percent"] + 1e-12',
     ["test_card", "test_photometry.ScoreTests"]),
    ("M9-score-skips-capture-block-check", "photometry.py",
     'if (cap.get("schema") != SCHEMAS["capture"] or cap.get("method_block_sha256") != method.block_sha256',
     'if (cap.get("schema") != SCHEMAS["capture"]',
     ["test_photometry.WorksheetTests", "test_photometry.RefusalTests", "test_photometry.ScoreTests"]),
    ("M10-worksheet-labels-unchecked", "photometry.py",
     'for name in FILLED:\n        if row[name] != str(cap.get(name)):',
     'for name in ():\n        if row[name] != str(cap.get(name)):',
     ["test_photometry.WorksheetTests", "test_photometry.ScoreTests"]),
    ("M11-repeat-check-removed", "photometry.py",
     'if not meter.agree(self.means(first["nv12"]), self.means(repeat["nv12"]),\n'
     '                               self.method["noise_tolerance_codes"]):\n'
     '                raise sweep.Void("repeat_mismatch"',
     'if False:\n                raise sweep.Void("repeat_mismatch"',
     ["test_photometry.CaptureTests"]),
]


def tree(dest):
    for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()  # the tests expect the tool to live inside a git checkout (RefusalTests)


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
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        result, by = "survived", "-"
        for target in targets:
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", target],
                                  cwd=dest / "tools/den-photometry", capture_output=True, text=True, timeout=900)
            if proc.returncode != 0:
                tail = [line for line in proc.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
                result, by = "caught", f"{target}: {tail[0] if tail else 'nonzero exit'}"
                break
        rows.append((mid, fname, result, by))
        print(f"{mid:38s} {result:8s} {by}", flush=True)
    print("\nMUTANT                                 RESULT   CAUGHT BY")
    for mid, fname, result, by in rows:
        print(f"{mid:38s} {result:8s} {by}")
    survived = [r for r in rows if r[2] == "survived"]
    print(f"RESULT {len(rows) - len(survived)} caught or not-run, {len(survived)} survived of {len(rows)}")


if __name__ == "__main__":
    main()
