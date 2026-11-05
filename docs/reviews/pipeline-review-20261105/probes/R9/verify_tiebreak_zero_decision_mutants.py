#!/usr/bin/env python3
"""Tiebreak probe for mutant-timing-empty-survives (M15) and mutant-record-zero-decisions-survives (M7).

    python3 -B verify_tiebreak_zero_decision_mutants.py <den-core-live checkout root> [--no-tests]

Part 1 (mutation): copies tools/den-core plus den_lighting's component, perception-lab and la-latency into a temp
folder (never the checkout), applies M7, M15 and M7+M15 to score.py, and runs the whole test_rung9 module except
TimingTest (whose full-size P1/P2 cases fail at baseline on this CPU) with failfast. Prints caught/survived.
Part 2 (impact): in the same kind of temp copy, builds a non-synthetic, slots-filled, non-fake-model log (the test
suite's own scored_log construct) from the synthetic evening with every occupancy event removed, so decide fails
closed and makes zero decisions while need still emits reading episodes. Scores it with a filled gate (proposed
floors) and with the anticipation floor at 0, under base / M7 / M15 / M7+M15, and prints each check and the verdict.
Synthetic data and temp folders only; nothing contacts a host. Prints RESULT lines.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]
M7 = ('"ok": problem is None and checked > 0,', '"ok": problem is None,')
M15 = ('"timing": {"ok": bool(ms) and max(ms)', '"timing": {"ok": not ms or max(ms)')
VARIANTS = {"base": [], "M7": [M7], "M15": [M15], "M7+M15": [M7, M15]}
CLASSES = ["test_rung9.LightModelTest", "test_rung9.RecordTest", "test_rung9.HardConstraintTest",
           "test_rung9.ActuationTest", "test_rung9.ScoreTest", "test_rung9.FixturesTest"]

IMPACT = r'''
import json, sys
sys.dont_write_bytecode = True
core = sys.argv[1]; tmp = sys.argv[2]
sys.path.insert(0, core)
import decide, events, light_model, runner, score, synthetic
from pathlib import Path
model = light_model.fake(); model.update(version="probe-lm-1", fake=False)
slots = {n: {**e, "value": e["proposed"], "status": "set"} for n, e in decide.DEFAULT_CONFIG["slots"].items()}
items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
items = [x for x in items if x[1] != "occupancy"]
header, log = runner.run(runner.tagged(items, "synthetic"),
                         runner.load_policies(["need", "incumbent", "decide"], {"decide": {"slots": slots, "light_model": model}}),
                         synthetic=False, sources=[{"name": "probe:no-occupancy"}])
path = Path(tmp) / "zero.jsonl"; events.write(path, header, log)
print("INFO decide decisions in log:", sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide"),
      "need episodes:", len(score.episodes(log)))
for floor in ("proposed", 0.0):
    gate = json.loads(score.GATE.read_text("utf-8"))
    for entry in list(gate["parameters"].values()) + [s["floor"] for s in gate["surrogates"].values()]:
        entry.update(value=entry["proposed"], status="set")
    gate["parameters"]["compensation"]["value"] = "none-v0"
    if floor != "proposed":
        gate["surrogates"]["anticipation"]["floor"]["value"] = floor
    gp = Path(tmp) / "gate.json"; gp.write_text(json.dumps(gate), "ascii")
    record, reasons = score.score(path, gp)
    c = record["checks"]
    print("RESULT floor=%s refusals=%s %s verdict=%s" % (floor, reasons, " ".join(
        "%s=%s" % (k, "OK" if v["ok"] else "FAIL") for k, v in c.items()), record["verdict"]))
    print("  timing text: %s" % c["timing"]["text"])
'''


def make_copy(root, mutants):
    work = tempfile.mkdtemp(prefix="r9-tiebreak-")
    for rel in COPY:
        src = os.path.join(root, rel)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
    path = os.path.join(work, "tools", "den-core", "score.py")
    text = open(path, encoding="utf-8").read()
    for old, new in mutants:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    open(path, "w", encoding="utf-8").write(text)
    return work


def main():
    root = os.path.abspath(sys.argv[1])
    run_tests = "--no-tests" not in sys.argv
    for name, mutants in VARIANTS.items():
        work = make_copy(root, mutants)
        try:
            core = os.path.join(work, "tools", "den-core")
            if run_tests and name != "base":
                t = time.time()
                p = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *CLASSES], cwd=core,
                                   capture_output=True, text=True, timeout=1200)
                last = (p.stderr.strip().splitlines() or [""])[-1]
                ran = [l for l in p.stderr.splitlines() if l.startswith("Ran ")]
                first = next((l for l in p.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
                print("RESULT mutation %-7s %s (%s; %s) %.0fs %s" % (
                    name, "caught" if p.returncode else "SURVIVED", ran[0] if ran else "?", last, time.time() - t, first),
                    flush=True)
            tmp = tempfile.mkdtemp(prefix="r9-tiebreak-log-")
            try:
                p = subprocess.run([sys.executable, "-B", "-c", IMPACT, core, tmp], capture_output=True, text=True,
                                   timeout=600)
                print("== impact under %s" % name)
                print(p.stdout.rstrip())
                if p.returncode:
                    print("ERROR", p.stderr[-2000:])
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
