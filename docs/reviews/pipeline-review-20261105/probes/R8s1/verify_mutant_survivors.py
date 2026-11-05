#!/usr/bin/env python3
"""Verifier (reproduce lens) for the R8s1 surviving-mutant findings in tools/den-light/model.py.

    python3 verify_mutant_survivors.py <branch-checkout-root> <work-dir> [mutant-id ...]

Checks, independently of the reporter's runner, whether each mutant survives EVERY test that can
reach the mutated code (not only a narrow class):
  - score() is reached only from test_stage1 ModelTests, ReductionTests, LightModelTests and
    ProbeEndToEndTests.test_a_full_session_restores_den_lighting_and_the_model_passes (via
    run_session/fit_and_score); test_den_light never imports model. Score mutants run all of those,
    plus MethodStage1Tests and ScheduleTests, without failfast so every failure is counted.
  - the run_session refusal mutant runs the whole of test_stage1 and test_den_light.
Each mutant is applied to a fresh copy of tools/den-light, tools/fixtures and docs under <work-dir>;
the checkout is never modified. Synthetic data and the tool's own 127.0.0.1 fakes only.
Prints 'RESULT <id>: survived|caught ...' and the unittest summary line.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCORE_TESTS = ["test_stage1.MethodStage1Tests", "test_stage1.ScheduleTests", "test_stage1.ModelTests",
               "test_stage1.ReductionTests", "test_stage1.LightModelTests",
               "test_stage1.ProbeEndToEndTests.test_a_full_session_restores_den_lighting_and_the_model_passes"]
MUTANTS = {
    "gate-drop-median": ("model.py",
        'passes = (median is not None and median * 100 <= method1["gate_median_percent"]\n              and p90',
        'passes = (median is not None\n              and p90', SCORE_TESTS),
    "gate-median-boundary": ("model.py",
        'median * 100 <= method1["gate_median_percent"]', 'median * 100 < method1["gate_median_percent"]',
        SCORE_TESTS),
    "gate-p90-boundary": ("model.py",
        'p90 * 100 <= method1["gate_p90_percent"]', 'p90 * 100 < method1["gate_p90_percent"]', SCORE_TESTS),
    "score-denominator-reading": ("model.py",
        'step_errors.append(abs(predicted[b] - e) / e)', 'step_errors.append(abs(predicted[b] - e) / v)',
        SCORE_TESTS),
    "score-ignores-bracket-flags": ("model.py",
        'if b in fitted["excluded"] or (step["index"], b) in flagged:', 'if b in fitted["excluded"]:',
        SCORE_TESTS),
    "model-open-slot-refusal-removed": ("model.py",
        '        method1.refuse_if_open("A scored light model")\n', '        pass\n',
        ["test_stage1", "test_den_light"]),
}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    root, work = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    ids = argv[3:] or list(MUTANTS)
    work.mkdir(parents=True, exist_ok=True)
    for mid in ids:
        name, old, new, tests = MUTANTS[mid]
        dest = work / f"mutant-{mid}"
        if dest.exists():
            shutil.rmtree(dest)
        for part in ("tools/den-light", "tools/fixtures", "docs"):
            if (root / part).exists():
                shutil.copytree(root / part, dest / part, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        target = dest / "tools" / "den-light" / name
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"RESULT {mid}: not-run (anchor found {text.count(old)} times)", flush=True)
            continue
        target.write_text(text.replace(old, new), encoding="utf-8")
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", *tests], cwd=dest / "tools" / "den-light",
                              capture_output=True, text=True, timeout=3000)
        lines = proc.stderr.strip().splitlines()
        ran = next((l for l in lines if l.startswith("Ran ")), "?")
        fails = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
        verdict = "caught" if proc.returncode else "survived"
        print(f"RESULT {mid}: {verdict} in {time.time() - start:.0f}s; {ran}; {lines[-1] if lines else ''}; "
              f"failures={fails}", flush=True)
        shutil.rmtree(dest, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
