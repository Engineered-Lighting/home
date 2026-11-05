#!/usr/bin/env python3
"""Mutation runner for tools/den-label gate logic (n1.py, label.py).

Each mutant is one exact string replacement (it must occur exactly once). For
each, the script copies <checkout>/tools/den-label and tools/den-take into a
fresh temp folder, applies the mutant, runs the narrowest unittest target with
failfast (-f), and prints a caught/survived table. The checkout is never
modified.

How to run
  python3 mutate_den_label.py <den-label checkout> [work folder] [parallel jobs]
"""
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    ("m1-union-covers-or", "n1.py", "c <= a and b <= d for c, d in cuts", "c <= a or b <= d for c, d in cuts",
     ["test_den_label.SecondPersonTests.test_the_union_test"]),
    ("m2-audited-any", "n1.py", 'return all(any(a <= f["n"] <= b for a, b in ranges) for f in usable)',
     'return any(any(a <= f["n"] <= b for a, b in ranges) for f in usable)',
     ["test_den_label.FixtureTests.test_refuses_an_unaudited_session_and_box_edits_after_the_label_pass_starts"]),
    ("m4-remark-wait-hours", "label.py", "if n1.now() < last_edit + days * 86400:",
     "if n1.now() < last_edit + days * 3600:",
     ["test_den_label.RemarkTests", "test_den_label.RemarkBlindTests"]),
    ("m5-origin-unchecked", "label.py", 'and origin in (None, f"http://127.0.0.1:{port}")', "and True",
     ["test_den_label.FrameServerTests"]),
    ("m7-no-tau-off", "n1.py", "scored_from = max(start, before[-1] + tau_off_s) if before else start",
     "scored_from = start", ["test_den_label.StatusTests", "test_den_label.CaseCountTests"]),
    ("m8-no-first-pass-lock-at-freeze", "n1.py",
     'if first["actor"]["saved_unix"] and first["actor"]["saved_unix"] > remark["actor"]["started_unix"]:',
     "if False:", ["test_den_label.RemarkBlindTests"]),
    ("m11-carried-iff-edge-dropped", "n1.py",
     'if (item["onset_kind"] == "carried") != (a in edges_first):',
     'if False:', ["test_den_label.LabelTests", "test_den_label.ClipKindTests", "test_den_label.FreezeRecheckTests"]),
    ("m12-save-skips-remark-lock", "label.py",
     '        self.check_unlocked()\n        if type(body) is not dict:',
     '        if type(body) is not dict:', ["test_den_label.RemarkBlindTests"]),
    ("m15-union-test-dropped", "n1.py", "elif not covers(person_cuts(newest), required):", "elif False:",
     ["test_den_label.SecondPersonTests"]),
    ("m16-freeze-labels-fixture-coverage-dropped", "n1.py",
     'if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:', "if False:",
     ["test_den_label.FreezeTests", "test_den_label.DryRunTests"]),
]


def run(root, work, mutant):
    name, file, old, new, targets = mutant
    folder = Path(tempfile.mkdtemp(prefix=name + "-", dir=work))
    for sub in ("den-label", "den-take"):
        shutil.copytree(root / "tools" / sub, folder / "tools" / sub)
    path = folder / "tools/den-label" / file
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        return name, file, "not-run", f"pattern occurs {text.count(old)} times"
    path.write_text(text.replace(old, new), encoding="utf-8")
    start = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *targets],
                          cwd=folder / "tools/den-label", capture_output=True, text=True, timeout=900)
    tail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
    caught = proc.returncode != 0
    first_fail = next((l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))), "")
    shutil.rmtree(folder, ignore_errors=True)
    return name, file, "caught" if caught else "survived", f"{tail} ({time.time() - start:.0f} s) {first_fail}"


def main():
    root = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="mutate-den-label-"))
    work.mkdir(parents=True, exist_ok=True)
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(lambda m: run(root, work, m), MUTANTS))
    print(f"{'mutant':34} {'file':9} {'result':9} detail")
    for name, file, result, detail in results:
        print(f"{name:34} {file:9} {result:9} {detail}")
    survived = [r for r in results if r[2] == "survived"]
    print(f"RESULT {len(results) - len(survived)} caught, {len(survived)} survived of {len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
