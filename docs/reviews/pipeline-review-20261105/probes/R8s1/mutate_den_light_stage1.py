#!/usr/bin/env python3
"""mutate_den_light_stage1: applies each mutant to a temp copy of tools/den-light (Stage 1 files), runs the
narrowest test classes that should catch it with unittest -f, and prints a caught/survived table.

How to run (synthetic tests only; fakes bind 127.0.0.1; temp dirs only):
    python3 -B mutate_den_light_stage1.py <root of the claude/rung-8-stage1 checkout>
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUTANTS = [
    ("model.py", "median * 100 <= method1[\"gate_median_percent\"]", "median * 100 < method1[\"gate_median_percent\"]",
     "gate median: <= becomes < (boundary)", ["test_stage1.ModelTests"]),
    ("model.py", "    if scored:\n        method1.refuse_if_open(\"A scored light model\")\n", "",
     "run_session no longer refuses a scored run with open slots",
     ["test_stage1.MethodStage1Tests", "test_stage1.ModelTests", "test_stage1.ReductionTests",
      "test_stage1.LightModelTests", "test_stage1.ProbeCliTests"]),
    ("model.py", "FITTED_KINDS = (\"all_off\", \"drift\", \"basis\", \"dimming\", \"holdout\")",
     "FITTED_KINDS = (\"all_off\", \"drift\", \"basis\", \"dimming\")",
     "session mask ignores hold-out captures", ["test_stage1.ReductionTests"]),
    ("model.py", "if e < method1[\"noise_floor_multiple\"] * noise[b] or e <= 0:", "if e <= 0:",
     "noise floor dropped from the score", ["test_stage1.ModelTests"]),
    ("model.py", "        values[0], values[-1] = 0.0, 1.0\n", "",
     "dimming table endpoints no longer forced to g(0)=0, g(full)=1", ["test_stage1.ModelTests",
                                                                        "test_stage1.LightModelTests"]),
    ("model.py", "    if len(blocks) > 1 and blocks", "    if len(blocks) > 1 and blocks",  # placeholder no-op guard
     None, None),
    ("guard.py", "    if before is not None and context not in owner.contexts:\n        return \"foreign\"",
     "    pass", "own/foreign: a moved last_updated with a foreign context counts as own",
     ["test_stage1.GuardTests"]),
    ("guard.py", "        if s[\"on\"] or arrival:", "        if s[\"on\"]:",
     "restore plan ignores den_lighting's arrival rule",
     ["test_stage1.GuardTests"]),
    ("probe.py", "        for attempt in (1, 2):", "        for attempt in (1,):",
     "restore without the second attempt",
     ["test_stage1.ProbeEndToEndTests.test_a_late_off_report_is_restored_once_more"]),
    ("predict.py", "                if row[p] is None:\n                    out[p] = None",
     "                if row[p] is None:\n                    out[p] += 0.0",
     "predict treats an excluded point as 0 instead of None", ["test_stage1.LightModelTests"]),
]


def run(mutant):
    name, old, new, label, tests = mutant
    if label is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "repo"
        shutil.copytree(ROOT / "tools", dest / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "docs" / "architecture", dest / "docs" / "architecture")
        path = dest / "tools" / "den-light" / name
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return (label, f"not applied ({text.count(old)} matches)", "", 0.0)
        path.write_text(text.replace(old, new), encoding="utf-8")
        t0 = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *tests],
                              cwd=dest / "tools" / "den-light", capture_output=True, text=True, timeout=900)
        tail = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        result = "survived" if proc.returncode == 0 else "caught"
        return (label, result, tail[0] if tail else "", time.time() - t0)


def main():
    rows = []
    for mutant in MUTANTS:
        row = run(mutant)
        if row is None:
            continue
        rows.append(row)
        print(f"{row[1]:10s} {row[3]:6.1f}s  {mutant[0]}: {row[0]}  {row[2]}", flush=True)
    print("RESULT", sum(r[1] == "caught" for r in rows), "caught,", sum(r[1] == "survived" for r in rows),
          "survived of", len(rows))


if __name__ == "__main__":
    main()
