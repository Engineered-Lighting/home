#!/usr/bin/env python3
"""Verifier's re-run of the seven surviving-mutant findings on den-core's score.py (claude/den-core-live).

    python3 -B verify_score_mutants.py <den-core-live checkout root> [mutant-id ...]

Ids: mutant-timing-threshold-survives, mutant-timing-empty-survives, mutant-record-zero-decisions-survives,
mutant-surrogate-none-passes-survives, mutant-episodes-without-engaged-survives, mutant-recipients-check-survives,
mutant-arms-ignore-gaming-survives (default: all).

Unlike the reporter's runner (narrow class, failfast) this runs the WHOLE test_rung9 module (the only test module in
the checkout that imports score.py) once unmutated and once per mutant, without failfast, and compares the sets of
failing tests: a mutant is CAUGHT only when some test fails that did not fail unmutated (TimingTest's full-size test
is host-speed dependent and fails unmutated on a slow host, so a plain exit code would over-count catches).
It copies tools/den-core, den_lighting, perception-lab and la-latency into a temp folder (set TMPDIR to choose where),
never touching the checkout. Synthetic data only; nothing binds outside 127.0.0.1.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

MUTANTS = {
    "mutant-timing-threshold-survives": (
        "max(ms) < fixed[\"decision_ms_max\"]", "max(ms) < 10 * fixed[\"decision_ms_max\"]"),
    "mutant-timing-empty-survives": (
        "\"timing\": {\"ok\": bool(ms) and max(ms)", "\"timing\": {\"ok\": not ms or max(ms)"),
    "mutant-record-zero-decisions-survives": (
        "\"ok\": problem is None and checked > 0,", "\"ok\": problem is None,"),
    "mutant-surrogate-none-passes-survives": (
        "checks[name] = {\"ok\": rate is not None and rate >= floors[name],",
        "checks[name] = {\"ok\": rate is None or rate >= floors[name],"),
    "mutant-episodes-without-engaged-survives": (
        "            if engaged:\n                found.append((track, t0, e[\"t\"]))",
        "            if True:\n                found.append((track, t0, e[\"t\"]))"),
    "mutant-recipients-check-survives": (
        "        if set(d.get(\"recipients\") or {}) != lit:", "        if False:"),
    "mutant-arms-ignore-gaming-survives": (
        "mine = shown if gaming else {b: requested.get(b, shown[b]) for b in events.LIGHTS}",
        "mine = {b: requested.get(b, shown[b]) for b in events.LIGHTS}"),
}
COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]


def run_module(core):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v", "test_rung9"], cwd=core,
                          capture_output=True, text=True, timeout=1200)
    failed = set()
    for line in proc.stderr.splitlines():
        m = re.match(r"^(FAIL|ERROR): (\S+) \((\S+)\)", line)
        if m:
            failed.add(m.group(3) + "." + m.group(2) if not m.group(3).endswith(m.group(2)) else m.group(3))
    ran = re.search(r"Ran (\d+) tests?", proc.stderr)
    skipped = len(re.findall(r"\.\.\. skipped", proc.stderr))
    return failed, (int(ran.group(1)) if ran else -1), skipped, proc.stderr.strip().splitlines()[-1:]


def main():
    root = os.path.abspath(sys.argv[1])
    wanted = sys.argv[2:] or list(MUTANTS)
    work = tempfile.mkdtemp(prefix="verify-r9-score-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        path = os.path.join(core, "score.py")
        text = open(path, encoding="utf-8").read()
        base_failed, ran, skipped, tail = run_module(core)
        print("BASELINE test_rung9: ran %d, skipped %d, failing %s %s" % (ran, skipped, sorted(base_failed), tail),
              flush=True)
        for fid in wanted:
            old, new = MUTANTS[fid]
            if text.count(old) != 1:
                print("RESULT %-44s NOT-APPLIED (%d matches)" % (fid, text.count(old)), flush=True)
                continue
            open(path, "w", encoding="utf-8").write(text.replace(old, new))
            try:
                failed, ran, skipped, tail = run_module(core)
            finally:
                open(path, "w", encoding="utf-8").write(text)
            new_fail = sorted(failed - base_failed)
            print("RESULT %-44s %s  (ran %d, skipped %d, new failures %s)" % (
                fid, "CAUGHT" if new_fail else "SURVIVED", ran, skipped, new_fail), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
