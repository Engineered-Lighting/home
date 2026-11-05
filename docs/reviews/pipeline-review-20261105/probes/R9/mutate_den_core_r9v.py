#!/usr/bin/env python3
"""Mutation runner for den-core's rung 9 gate logic (score.py, decide.py) on claude/den-core-live.

    python3 -B mutate_den_core_r9v.py <den-core-live checkout root> [mutant-id ...]

It copies tools/den-core, den_lighting's custom component, perception-lab and la-latency from the checkout into a
temp folder (never touching the checkout), then for each mutant: applies one exact text replacement to one file,
runs the narrowest unittest selection that should catch it with failfast (-f), records caught (tests fail) or
survived (tests pass), and restores the file. It prints a caught/survived table. Synthetic data only; the tests
themselves bind nothing outside 127.0.0.1 and read no host.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

MUTANTS = [
    # id, file, old, new, tests
    ("M1-gate-open-slot-refusal", "score.py",
     "    if gate_open:\n        reasons.append(",
     "    if False:\n        reasons.append(",
     ["test_rung9.ScoreTest"]),
    ("M2-config-open-slot-refusal", "score.py",
     "    if config_open:\n",
     "    if False:\n",
     ["test_rung9.ScoreTest"]),
    ("M3-fake-model-refusal", "score.py",
     "    if model.fake:\n",
     "    if False:\n",
     ["test_rung9.ScoreTest"]),
    ("M4-anticipation-drop-rise", "score.py",
     "e >= (1 + rise) * start if start > 0 else e > 0",
     "e >= start if start > 0 else e >= 0",
     ["test_rung9.ScoreTest"]),
    ("M5-stability-include-window", "score.py",
     "if t >= t0 + window and t < t1 and c == \"need\"",
     "if t >= t0 and t <= t1 and c == \"need\"",
     ["test_rung9.ScoreTest"]),
    ("M6-timing-bound-10x", "score.py",
     "max(ms) < fixed[\"decision_ms_max\"]",
     "max(ms) < 10 * fixed[\"decision_ms_max\"]",
     ["test_rung9.ScoreTest", "test_rung9.TimingTest"]),
    ("M7-record-empty-passes", "score.py",
     "\"ok\": problem is None and checked > 0,",
     "\"ok\": problem is None,",
     ["test_rung9.ScoreTest"]),
    ("M8-surrogate-none-passes", "score.py",
     "checks[name] = {\"ok\": rate is not None and rate >= floors[name],",
     "checks[name] = {\"ok\": rate is None or rate >= floors[name],",
     ["test_rung9.ScoreTest"]),
    ("M9-episodes-without-engaged", "score.py",
     "            if engaged:\n                found.append((track, t0, e[\"t\"]))",
     "            if True:\n                found.append((track, t0, e[\"t\"]))",
     ["test_rung9.ScoreTest"]),
    ("M10-record-skip-recipients", "score.py",
     "        if set(d.get(\"recipients\") or {}) != lit:",
     "        if False:",
     ["test_rung9.ScoreTest", "test_rung9.RecordTest"]),
    ("M11-arms-ignore-gaming", "score.py",
     "mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
     "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
     ["test_rung9.ScoreTest"]),
    ("M12-compensation-mismatch-refusal", "score.py",
     "    if wanted[\"status\"] != \"open\" and wanted[\"value\"] != config[\"compensation\"][\"option\"]:",
     "    if False:",
     ["test_rung9.ScoreTest"]),
    ("M13-decide-held-not-pinned-none-v0", "decide.py",
     "    requested.update({b: _scene(h[\"pinned\"]) for b, h in held.items()})\n    why = \"%s: chose %s at cost %d",
     "    requested.update({b: _scene(state.get(\"actuator_state\", {}).get(b)) for b, h in held.items()})\n    why = \"%s: chose %s at cost %d",
     ["test_rung9.FixturesTest"]),
    ("M14-surrogate-unknown-counts-as-miss", "score.py",
     "        if not before or before[0][1] is None:\n            unknown += 1\n            continue",
     "        if not before or before[0][1] is None:\n            hits[\"anticipation\"].append(False)\n            unknown += 1\n            continue",
     ["test_rung9.ScoreTest"]),
    ("M6b-timing-bound-10x-scoretest", "score.py",
     "max(ms) < fixed[\"decision_ms_max\"]",
     "max(ms) < 10 * fixed[\"decision_ms_max\"]",
     ["test_rung9.ScoreTest"]),
    ("M15-timing-empty-passes", "score.py",
     "\"timing\": {\"ok\": bool(ms) and max(ms)",
     "\"timing\": {\"ok\": not ms or max(ms)",
     ["test_rung9.ScoreTest"]),
]

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]


def main():
    root = os.path.abspath(sys.argv[1])
    only = set(sys.argv[2:])
    work = tempfile.mkdtemp(prefix="mut-den-core-r9v-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        rows = []
        for mid, fname, old, new, tests in MUTANTS:
            if only and mid not in only:
                continue
            path = os.path.join(core, fname)
            text = open(path, encoding="utf-8").read()
            if text.count(old) != 1:
                rows.append((mid, fname, "not-applied (%d matches)" % text.count(old), "", 0.0))
                continue
            open(path, "w", encoding="utf-8").write(text.replace(old, new))
            t = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *tests], cwd=core,
                                  capture_output=True, text=True, timeout=900)
            took = time.time() - t
            open(path, "w", encoding="utf-8").write(text)
            tail = (proc.stderr.strip().splitlines() or [""])[-1]
            caught = proc.returncode != 0
            first = ""
            for line in proc.stderr.splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    first = line
                    break
            rows.append((mid, fname, "caught" if caught else "survived", first or tail, took))
            print("%-38s %-10s %-9s %6.1fs %s" % (mid, fname, rows[-1][2], took, rows[-1][3]), flush=True)
        print("\nRESULT table")
        for mid, fname, result, by, took in rows:
            print("RESULT %-38s %-10s %s  | %s" % (mid, fname, result, by))
        survived = [r[0] for r in rows if r[2] == "survived"]
        print("SURVIVED: %s" % (", ".join(survived) or "none"))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
