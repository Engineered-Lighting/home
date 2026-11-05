#!/usr/bin/env python3
"""Mutation runner for rung 8 Stage 1 (tools/den-light): gate, verdict, open-slot and probe-safety logic.

    python3 mutate_den_light.py <branch-checkout-root> [<work-dir>] [mutant-id ...]

For each mutant it copies <root>/tools and <root>/docs (no .git) into a fresh folder under
<work-dir> (default: a new temp dir), applies one exact string replacement, runs the narrowest
unittest selection that should catch it (failfast, from tools/den-light), and prints a
caught/survived table. Synthetic data and the tool's own fakes on 127.0.0.1 only; nothing
contacts another host. The checkout itself is never modified.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    # id, file, old, new, tests (run from tools/den-light), what it breaks
    ("gate-median-boundary", "model.py",
     'median * 100 <= method1["gate_median_percent"]', 'median * 100 < method1["gate_median_percent"]',
     ["test_stage1.ModelTests"], "median exactly at 8% no longer passes"),
    ("gate-drop-median", "model.py",
     'passes = (median is not None and median * 100 <= method1["gate_median_percent"]\n              and p90',
     'passes = (median is not None\n              and p90',
     ["test_stage1.ModelTests", "test_stage1.ReductionTests", "test_stage1.LightModelTests"],
     "the verdict ignores the median"),
    ("gate-drop-p90", "model.py",
     '\n              and p90 * 100 <= method1["gate_p90_percent"])', ')',
     ["test_stage1.ModelTests"], "the verdict ignores p90"),
    ("model-open-slot-refusal-removed", "model.py",
     '        method1.refuse_if_open("A scored light model")\n', '        pass\n',
     ["test_stage1", "test_den_light"], "model.py writes a scored light-model.json with open slots"),
    ("score-denominator-reading", "model.py",
     'step_errors.append(abs(predicted[b] - e) / e)', 'step_errors.append(abs(predicted[b] - e) / v)',
     ["test_stage1.ModelTests", "test_stage1.ReductionTests"], "relative error divides by reading incl. D"),
    ("score-ignores-bracket-flags", "model.py",
     'if b in fitted["excluded"] or (step["index"], b) in flagged:', 'if b in fitted["excluded"]:',
     ["test_stage1.ReductionTests", "test_stage1.ModelTests"], "flagged hold-out pairs are scored"),
    ("score-no-noise-floor", "model.py",
     'if e < method1["noise_floor_multiple"] * noise[b] or e <= 0:', 'if e <= 0:',
     ["test_stage1.ModelTests"], "pairs below the noise floor are scored"),
    ("predict-mired-weights-swapped", "predict.py",
     'return [(a, 1.0 - w), (b, w)]', 'return [(a, w), (b, 1.0 - w)]',
     ["test_stage1.LightModelTests", "test_stage1.ModelTests"], "interpolation in mired runs backwards"),
    ("holdout-may-use-fitted-level", "model.py",
     'if u not in fitted and low <= u <= high:', 'if low <= u <= high:',
     ["test_stage1.ScheduleTests"], "random hold-outs may sit on a fitted level"),
    ("probe-open-slot-refusal-removed", "probe.py",
     '        self.m1.refuse_if_open("A probe session")\n', '        pass\n',
     ["test_stage1.ProbeEndToEndTests.test_a_session_refuses_while_a_slot_is_open"],
     "a probe session starts with open slots"),
    ("guard-context-check-removed", "guard.py",
     '    if before is not None and context not in owner.contexts:\n        return "foreign"',
     '    if False:\n        return "foreign"',
     ["test_stage1.GuardTests"], "a moved last_updated with a foreign context counts as own"),
    ("restore-writes-person-owned-bulbs", "probe.py",
     'bulbs = [e for e in self.owner.tools() if e not in self.owner.unavailable]',
     'bulbs = [e for e in guard.LIGHTS if e not in self.owner.unavailable]',
     ["test_stage1.ProbeEndToEndTests.test_a_foreign_change_is_never_written_again_and_is_listed"],
     "restore overwrites a bulb a person changed"),
]


def copy_tree(root, dest):
    for part in ("tools/den-light", "tools/fixtures", "docs"):
        src = root / part
        if src.exists():
            shutil.copytree(src, dest / part, ignore=shutil.ignore_patterns(".git", "__pycache__"))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).resolve()
    work = Path(argv[2]).resolve() if len(argv) > 2 and not argv[2].startswith("-") and "/" in argv[2] \
        else Path(tempfile.mkdtemp(prefix="mutate_den_light_"))
    only = set(a for a in argv[2:] if "/" not in a)
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    for mid, name, old, new, tests, what in MUTANTS:
        if only and mid not in only:
            continue
        dest = work / f"mutant-{mid}"
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()
        copy_tree(root, dest)
        target = dest / "tools" / "den-light" / name
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            rows.append((mid, name, "not-run", f"anchor found {count} times"))
            print(f"RESULT {mid}: not-run (anchor found {count} times)", flush=True)
            continue
        target.write_text(text.replace(old, new), encoding="utf-8")
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *tests],
                              cwd=dest / "tools" / "den-light", capture_output=True, text=True, timeout=1500)
        took = time.time() - start
        tail = (proc.stderr.strip().splitlines() or [""])[-1]
        first_fail = next((l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
        result = "caught" if proc.returncode != 0 else "survived"
        rows.append((mid, name, result, first_fail or tail))
        print(f"RESULT {mid}: {result} in {took:.0f}s ({' '.join(tests)}) {first_fail or tail}", flush=True)
        shutil.rmtree(dest, ignore_errors=True)
    print("\nmutant | file | result | evidence")
    for row in rows:
        print(" | ".join(row))
    survived = [r for r in rows if r[2] == "survived"]
    print(f"\n{len(rows)} mutants: {len(rows) - len(survived)} caught or not-run, {len(survived)} survived")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
