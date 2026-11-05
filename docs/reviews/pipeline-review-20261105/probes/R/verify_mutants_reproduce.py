#!/usr/bin/env python3
"""Independent re-run (REPRODUCE lens) of the three surviving check.py mutants behind the
findings b2-after-health-untested (M03), b4-positive-slope-untested (M02) and
b5-row-count-untested (M13).

    python3 -B verify_mutants_reproduce.py <checkout root> [--only=M02,M03,M13] [--jobs=3]

For each mutant: copy <root>/tools/den-take to a fresh temp dir under MUT_DIR (the pinned
checkout is never touched), apply one exact text replacement to check.py (refuse if the
old text is not found exactly once), confirm the copy really differs, then run the WHOLE
test module with unittest -f. Synthetic data only; the suite's fake camera binds to
127.0.0.1. Prints a RESULT line per mutant: 'survived' (suite still green) or 'caught'
(with the first failing test), plus the unittest 'Ran N tests' line.
"""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUT_DIR = os.environ.get(
    "MUT_DIR", "$SCRATCH/mut/R-reproduce")

MUTANTS = {
    "M02": ('and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]',
            'and -slope * 1e6 <= m["max_two_clock_slope_ppm"]'),
    "M03": ('and health_sessions == [next(iter(sessions))] * 2,',
            'and health_sessions[0] == next(iter(sessions)),'),
    "M13": ('or len(rows) != (take.get("counts") or {}).get("kept")):',
            'or False):'),
}


def run_one(mid, root):
    old, new = MUTANTS[mid]
    src = Path(root) / "tools" / "den-take"
    os.makedirs(MUT_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"verify-{mid}-", dir=MUT_DIR) as tmp:
        work = Path(tmp) / "den-take"
        shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__"))
        path = work / "check.py"
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return mid, "not-run", f"old text found {text.count(old)} times", ""
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
        assert path.read_text(encoding="utf-8") != text and new in path.read_text(encoding="utf-8")
        started = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_take"], cwd=work,
                              capture_output=True, text=True, timeout=1800)
        lines = proc.stderr.splitlines()
        first = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))][:1]
        ran = [l for l in lines if l.startswith("Ran ")][-1:]
        tail = [l for l in lines if l in ("OK",) or l.startswith("FAILED")][-1:]
        status = "caught" if proc.returncode else "survived"
        return mid, status, f"{time.time() - started:.0f}s exit={proc.returncode}", " ".join(first + ran + tail)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    only, jobs = list(MUTANTS), 3
    for a in argv:
        if a.startswith("--only="):
            only = a.split("=", 1)[1].split(",")
        if a.startswith("--jobs="):
            jobs = int(a.split("=", 1)[1])
    root = args[0]
    print(f"checkout {root}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for fut in concurrent.futures.as_completed([pool.submit(run_one, m, root) for m in only]):
            mid, status, where, detail = fut.result()
            print(f"RESULT {mid}: {status} ({where}) {detail}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
