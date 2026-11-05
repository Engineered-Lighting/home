#!/usr/bin/env python3
"""Verifier (intent lens) for the R8s1 surviving-mutant findings in tools/den-light/model.py.

    python3 verify_intent_mutants_reach.py <branch-checkout-root> <work-dir> [mutant-id ...]

Each mutant is applied to a fresh copy of tools/den-light, tools/fixtures and docs under <work-dir>
(the checkout is never modified) and run against EVERY test in test_stage1 that reaches model.score()
or model.run_session(): ModelTests, ReductionTests, LightModelTests, MethodStage1Tests and the end-to-end
ProbeEndToEndTests.test_a_full_session_restores_den_lighting_and_the_model_passes (test_den_light never
imports model). Synthetic data and the tool's own 127.0.0.1 fakes only. No failfast: every failure counts.
Prints 'RESULT <id>: caught|survived' and the unittest summary line.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

REACH = ["test_stage1.MethodStage1Tests", "test_stage1.ModelTests", "test_stage1.ReductionTests",
         "test_stage1.LightModelTests",
         "test_stage1.ProbeEndToEndTests.test_a_full_session_restores_den_lighting_and_the_model_passes"]
MUTANTS = {
    "gate-drop-median": ('passes = (median is not None and median * 100 <= method1["gate_median_percent"]\n'
                         '              and p90', 'passes = (median is not None\n              and p90'),
    "gate-median-boundary": ('median * 100 <= method1["gate_median_percent"]',
                             'median * 100 < method1["gate_median_percent"]'),
    "score-denominator-reading": ('step_errors.append(abs(predicted[b] - e) / e)',
                                  'step_errors.append(abs(predicted[b] - e) / v)'),
    "score-ignores-bracket-flags": ('if b in fitted["excluded"] or (step["index"], b) in flagged:',
                                    'if b in fitted["excluded"]:'),
    "model-open-slot-refusal-removed": ('        method1.refuse_if_open("A scored light model")\n', '        pass\n'),
}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    root, work = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    for mid in (argv[3:] or list(MUTANTS)):
        old, new = MUTANTS[mid]
        dest = work / f"mutant-{mid}"
        if dest.exists():
            shutil.rmtree(dest)
        for part in ("tools/den-light", "tools/fixtures", "docs"):
            if (root / part).exists():
                shutil.copytree(root / part, dest / part, ignore=shutil.ignore_patterns("__pycache__"))
        target = dest / "tools" / "den-light" / "model.py"
        text = target.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"RESULT {mid}: not-run (anchor found {text.count(old)} times)", flush=True)
            continue
        target.write_text(text.replace(old, new), encoding="utf-8")
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", *REACH], cwd=dest / "tools" / "den-light",
                              capture_output=True, text=True, timeout=1800)
        lines = proc.stderr.strip().splitlines()
        ran = next((l for l in lines if l.startswith("Ran ")), "")
        fails = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
        verdict = "caught" if proc.returncode else "survived"
        print(f"RESULT {mid}: {verdict} in {time.time() - start:.0f}s; {ran}; {lines[-1] if lines else ''}; "
              f"{fails[:3]}", flush=True)
        shutil.rmtree(dest, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
