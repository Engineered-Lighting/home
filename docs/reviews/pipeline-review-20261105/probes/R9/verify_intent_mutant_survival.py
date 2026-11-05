#!/usr/bin/env python3
"""Verifier (intent lens) for the seven 'surviving mutant' findings on score.py, claude/den-core-live.

    python3 -B verify_intent_mutant_survival.py <den-core-live checkout root> [work parent dir] [mutant-id ...]

It copies tools/den-core and den_lighting's custom component (plus perception-lab and the tools/*-latency folder, which
den-core's synthetic evening imports) into a fresh temp folder under [work parent dir] (default: system temp),
never touching the checkout. For a control mutant (expected caught) and each reported mutant it applies one exact
text replacement to score.py, runs test_rung9.ScoreTest (plus RecordTest for M10) WITHOUT failfast, and prints
whether the tests ran (count, skips) and whether they failed (caught) or passed (survived). ScoreTest is the only
test class that calls score.score/main/lines (grep 'score\\.' test_rung9.py); TimingTest only calls
score.replay_timed, which no mutant here touches. Synthetic data only; nothing binds or reads a host.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

MUTANTS = [
    ("CONTROL-M1-gate-open-slot-refusal",
     "    if gate_open:\n        reasons.append(", "    if False:\n        reasons.append(", ["test_rung9.ScoreTest"]),
    ("M6b-timing-bound-10x", "max(ms) < fixed[\"decision_ms_max\"]", "max(ms) < 10 * fixed[\"decision_ms_max\"]",
     ["test_rung9.ScoreTest"]),
    ("M6x-timing-always-ok", "\"timing\": {\"ok\": bool(ms) and max(ms) < fixed[\"decision_ms_max\"],",
     "\"timing\": {\"ok\": True,", ["test_rung9.ScoreTest"]),
    ("M15-timing-empty-passes", "\"timing\": {\"ok\": bool(ms) and max(ms)", "\"timing\": {\"ok\": not ms or max(ms)",
     ["test_rung9.ScoreTest"]),
    ("M7-record-empty-passes", "\"ok\": problem is None and checked > 0,", "\"ok\": problem is None,",
     ["test_rung9.ScoreTest"]),
    ("M8-surrogate-none-passes", "checks[name] = {\"ok\": rate is not None and rate >= floors[name],",
     "checks[name] = {\"ok\": rate is None or rate >= floors[name],", ["test_rung9.ScoreTest"]),
    ("M9-episodes-without-engaged", "            if engaged:\n                found.append((track, t0, e[\"t\"]))",
     "            if True:\n                found.append((track, t0, e[\"t\"]))", ["test_rung9.ScoreTest"]),
    ("M10-record-skip-recipients", "        if set(d.get(\"recipients\") or {}) != lit:", "        if False:",
     ["test_rung9.ScoreTest", "test_rung9.RecordTest"]),
    ("M11-arms-ignore-gaming",
     "mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
     "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}", ["test_rung9.ScoreTest"]),
]
COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab"]


def main():
    root = os.path.abspath(sys.argv[1])
    parent = sys.argv[2] if len(sys.argv) > 2 and os.path.isdir(sys.argv[2]) else None
    only = set(a for a in sys.argv[2:] if not os.path.isdir(a))
    work = tempfile.mkdtemp(prefix="r9-intent-", dir=parent)
    try:
        extra = ["tools/" + d for d in sorted(os.listdir(os.path.join(root, "tools"))) if d.endswith("-latency")]
        for rel in COPY + extra:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        path = os.path.join(core, "score.py")
        original = open(path, encoding="utf-8").read()
        rows = []
        for mid, old, new, tests in MUTANTS:
            if only and mid not in only:
                continue
            if original.count(old) != 1:
                rows.append((mid, "NOT-APPLIED (%d matches)" % original.count(old), "", ""))
                continue
            open(path, "w", encoding="utf-8").write(original.replace(old, new))
            t = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", *tests], cwd=core,
                                  capture_output=True, text=True, timeout=1800)
            open(path, "w", encoding="utf-8").write(original)
            ran = re.search(r"Ran (\d+) tests? in ([\d.]+)s", proc.stderr)
            tail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
            failed = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
            verdict = "caught" if proc.returncode != 0 else "survived"
            rows.append((mid, verdict, "ran=%s %s" % (ran.group(1) if ran else "?", tail), "; ".join(failed)[:300]))
            print("RESULT %-36s %-8s %s %s (%.0fs)" % (mid, verdict, rows[-1][2], rows[-1][3], time.time() - t),
                  flush=True)
        print("\nSUMMARY")
        for mid, verdict, counts, by in rows:
            print("RESULT %-36s %-8s | %s | %s" % (mid, verdict, counts, by))
        control = [r for r in rows if r[0].startswith("CONTROL")]
        if control:
            print("%s: control mutant %s" % ("PASS" if control[0][1] == "caught" else "FAIL", control[0][1]))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
