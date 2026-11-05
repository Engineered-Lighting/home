#!/usr/bin/env python3
"""Mutation runner for tools/den-take (rung R, VALIDATE lens): check.py gate logic and take.py.

    python3 -B mutate_den_take_validate.py <rung-r checkout> <den-take-script checkout> [--full] [--jobs=N] [--only=M01,M02]

Each mutant is one exact text replacement in check.py or take.py, applied to a fresh
temp copy of tools/den-take under MUT_DIR (the pinned checkouts are never touched).
The narrow test selection runs with unittest -f; with --full, a mutant that survives
the narrow selection is re-run against the whole test module (-f). Synthetic data
only: the suite uses its own fake camera on 127.0.0.1. Prints one RESULT line per
mutant as it finishes and a caught/survived table at the end.
"""
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUT_DIR = os.environ.get("MUT_DIR", "$SCRATCH/mut/R-validate")

# (id, branch r=rung-r s=den-take-script, file, old, new, narrow test selectors)
MUTANTS = [
    ("M01", "r", "check.py", 'elapsed = abs(delta) + (start["width_s"] + end["width_s"]) / 2', 'elapsed = abs(delta)',
     ["test_den_take.FaultTests"]),
    ("M02", "r", "check.py", 'and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]',
     'and -slope * 1e6 <= m["max_two_clock_slope_ppm"]', ["test_den_take.FaultTests"]),
    ("M03", "r", "check.py", 'and health_sessions == [next(iter(sessions))] * 2,',
     'and health_sessions[0] == next(iter(sessions)),', ["test_den_take.FaultTests", "test_den_take.TakeScriptTests"]),
    ("M04", "r", "check.py", 'if any(f["stale"] for f in window):', 'if False:', ["test_den_take.FaultTests"]),
    ("M05", "r", "check.py", 'chosen = next((a for a in attempts if a["readable"]), None)',
     'chosen = next((a for a in reversed(attempts) if a["readable"]), None)',
     ["test_den_take.SlateRuleTests", "test_den_take.CutTests"]),
    ("M06", "r", "check.py", 'if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == steps - 1:', 'if False:',
     ["test_den_take.SlateFitTests", "test_den_take.FaultTests"]),
    ("M07", "r", "check.py", 'and residual <= m["max_two_clock_line_s"],', 'and True,', ["test_den_take.FaultTests"]),
    ("M08", "r", "check.py", '"pass": bool(frames) and max_step <= m["max_two_clock_step_s"]', '"pass": bool(frames)',
     ["test_den_take.FaultTests"]),
    ("M09", "r", "check.py", '"retained": verdict == "PASS" and take.get("commissioning") is False,',
     '"retained": verdict == "PASS",', ["test_den_take.FaultTests"]),
    ("M10", "r", "check.py", '"pass": separation is not None and separation <= m["max_slate_separation_s"],',
     '"pass": True,', ["test_den_take.FaultTests"]),
    ("M11", "r", "check.py", 'delta = wrap(end["c_s"] - start["c_s"], m["slate_period_s"])',
     'delta = end["c_s"] - start["c_s"]', ["test_den_take.FaultTests"]),
    ("M12", "r", "check.py", 'if flashes < m["min_flashes"]:', 'if flashes < m["min_flashes"] - 3:',
     ["test_den_take.SlateFitTests", "test_den_take.MethodTests"]),
    ("M13", "r", "check.py", 'or len(rows) != (take.get("counts") or {}).get("kept")):', 'or False):',
     ["test_den_take.FaultTests", "test_den_take.TakeScriptTests"]),
    ("M14", "r", "take.py", 'take["complete"] = take["stop_reason"] is None', 'take["complete"] = True',
     ["test_den_take.TakeScriptTests"]),
    ("M15", "s", "take.py", 'if self.pause(begin + block["at_s"] - clock(), until=enter):\n                return',
     'if self.pause(begin + block["at_s"] - clock(), until=enter):\n                pass',
     ["test_den_take.StagedScriptTests"]),
    ("M16", "s", "take.py", '    if end > settings.free_max_s:', '    if False:',
     ["test_den_take.StagedScriptTests", "test_den_take.ScriptFileTests"]),
    ("M17", "r", "take.py", 'if len({hashlib.sha256(rgb).digest() for _, rgb in rgbs}) < len(rgbs):', 'if False:',
     ["test_den_take.SlateFitTests"]),
    ("M18", "r", "check.py", "if not cues:  # take.py's first write", "if False:  # take.py's first write",
     ["test_den_take.TakeScriptTests"]),
]


def run(work, selectors):
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *selectors], cwd=work,
                          capture_output=True, text=True, timeout=1500)
    first = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))][:1]
    ran = [l for l in proc.stderr.splitlines() if l.startswith("Ran ")][-1:]
    return proc.returncode, time.time() - started, " ".join(first or ran)


def run_mutant(mutant, roots, full):
    mid, branch, name, old, new, narrow = mutant
    src = Path(roots[branch]) / "tools" / "den-take"
    with tempfile.TemporaryDirectory(prefix=f"mut-{mid}-", dir=MUT_DIR) as tmp:
        work = Path(tmp) / "den-take"
        shutil.copytree(src, work, ignore=shutil.ignore_patterns("__pycache__"))
        path = work / name
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return mid, "not-run", f"old text found {text.count(old)} times", ""
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
        code, seconds, detail = run(work, narrow)
        if code:
            return mid, "caught", f"{' '.join(narrow)} ({seconds:.0f}s)", detail
        if not full:
            return mid, "survived", f"{' '.join(narrow)} ({seconds:.0f}s)", detail
        code, seconds, detail = run(work, ["test_den_take"])
        return mid, "caught" if code else "survived", f"full module ({seconds:.0f}s)", detail


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    full, jobs, only = "--full" in argv, 2, None
    for a in argv:
        if a.startswith("--jobs="):
            jobs = int(a.split("=", 1)[1])
        if a.startswith("--only="):
            only = set(a.split("=", 1)[1].split(","))
    roots = {"r": args[0], "s": args[1] if len(args) > 1 else args[0]}
    chosen = [m for m in MUTANTS if not only or m[0] in only]
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_mutant, m, roots, full) for m in chosen]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results[result[0]] = result
            print(f"RESULT {result[0]}: {result[1]} by {result[2]} {result[3]}", flush=True)
    print("\nmutant | result | change | where")
    for mid, branch, name, old, new, _ in chosen:
        r = results[mid]
        print(f"{mid} | {r[1]} | {name}: {old.strip()[:60]!r} -> {new.strip()[:40]!r} | {r[2]}")
    print(f"\nSUMMARY caught={sum(r[1] == 'caught' for r in results.values())} "
          f"survived={sum(r[1] == 'survived' for r in results.values())} "
          f"not-run={sum(r[1] == 'not-run' for r in results.values())}")


if __name__ == "__main__":
    main(sys.argv[1:])
