#!/usr/bin/env python3
"""Independent mutation re-run (rung R, INTENT lens) for three test-coverage findings on check.py.

    python3 -B mutate_den_take_intent.py <branch checkout root> [--jobs=N] [--only=b2,b4]

Mutants (each one exact text replacement in tools/den-take/check.py, must occur exactly once):
  b2  B2 ignores the /health taken after the take   (health_sessions[0] == s)
  b4  B4 slope limit made one-sided                  (-slope * 1e6 <= max)
  b5  B5 frames.csv row count vs counts.kept removed

Each mutant is applied to a fresh copy of tools/den-take under
scratchpad/mut/R-intent/ (the pinned checkout is never touched) and the WHOLE test
module runs there (python3 -B -m unittest -f test_den_take). Synthetic data only:
the suite's own fake camera binds 127.0.0.1. A control run of the unmutated copy
is included. Prints one RESULT line per run and a caught/survived table.
"""
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUT_ROOT = Path("$SCRATCH/mut/R-intent")

MUTANTS = {
    "control": None,
    "b2": ('and health_sessions == [next(iter(sessions))] * 2,',
           'and health_sessions[0] == next(iter(sessions)),'),
    "b4": ('and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]',
           'and -slope * 1e6 <= m["max_two_clock_slope_ppm"]'),
    "b5": ('            or len(rows) != (take.get("counts") or {}).get("kept")):',
           '            ):'),
}


def run(root, key):
    MUT_ROOT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{key}-", dir=MUT_ROOT))
    dst = work / "den-take"
    shutil.copytree(Path(root) / "tools" / "den-take", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    mutant = MUTANTS[key]
    if mutant:
        path = dst / "check.py"
        text = path.read_text(encoding="utf-8")
        if text.count(mutant[0]) != 1:
            return key, "ERROR", f"pattern found {text.count(mutant[0])} times", 0.0
        path.write_text(text.replace(mutant[0], mutant[1]), encoding="utf-8")
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_den_take"],
                          cwd=dst, capture_output=True, text=True, timeout=3000)
    took = time.monotonic() - started
    tail = proc.stderr.strip().splitlines()
    ran = next((l for l in tail if l.startswith("Ran ")), "?")
    failed = [l for l in tail if l.startswith(("FAIL:", "ERROR:"))]
    status = "survived" if proc.returncode == 0 else "caught"
    if key == "control":
        status = "control-OK" if proc.returncode == 0 else "control-FAILED"
    detail = f"{ran}; " + (" | ".join(failed) if failed else (tail[-1] if tail else ""))
    shutil.rmtree(work, ignore_errors=True)
    return key, status, detail, took


def main(argv):
    root = argv[1]
    jobs = next((int(a.split("=", 1)[1]) for a in argv[2:] if a.startswith("--jobs=")), 4)
    only = next((a.split("=", 1)[1].split(",") for a in argv[2:] if a.startswith("--only=")), list(MUTANTS))
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        for key, status, detail, took in pool.map(lambda k: run(root, k), only):
            print(f"RESULT {key}: {status} ({took:.0f}s) {detail}", flush=True)
            results.append((key, status))
    print("\nmutant   | outcome")
    for key, status in results:
        print(f"{key:8} | {status}")


if __name__ == "__main__":
    main(sys.argv)
