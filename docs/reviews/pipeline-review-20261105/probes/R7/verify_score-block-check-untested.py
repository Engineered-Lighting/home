#!/usr/bin/env python3
"""verify_score-block-check-untested: does any test catch removing the score's per-capture
method_block_sha256 check (photometry.py, score_root), and is that check load-bearing?

    python3 -B verify_score-block-check-untested.py <branch-root> <work-dir> suite   # M9 + full suite, failfast
    python3 -B verify_score-block-check-untested.py <branch-root> <work-dir> direct  # functional probe, both copies

Copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 into <work-dir>/{orig,m9}
(with a .git folder, as the tests expect the tool inside a checkout), applies the mutant to m9 only,
and never touches the branch checkout. Synthetic data, fake camera on 127.0.0.1 only (the tests' own).
"""
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
MODE = sys.argv[3]
OLD = 'if (cap.get("schema") != SCHEMAS["capture"] or cap.get("method_block_sha256") != method.block_sha256'
NEW = 'if (cap.get("schema") != SCHEMAS["capture"]'


def tree(dest, mutate):
    if dest.exists():
        shutil.rmtree(dest)
    for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()
    if mutate:
        path = dest / "tools/den-photometry/photometry.py"
        text = path.read_text(encoding="utf-8")
        assert text.count(OLD) == 1, "pattern not found exactly once"
        path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    return dest


DIRECT = textwrap.dedent('''
    import json, sys
    import test_photometry as tp  # imports card first, which puts tools/den-light on the path
    import sweep
    t = tp.ScoreTests("test_two_days_pass_and_write_the_records")
    t.setUp()
    try:
        with tp.workspace() as ws:
            t.two_days(ws)
            cap = ws.root / "day-20261111" / "capture-1" / "capture.json"
            rec = json.loads(cap.read_text(encoding="utf-8"))
            # as if this capture folder came from a photometry folder made under another METHOD block
            rec["method_block_sha256"] = "e" * 64
            cap.write_text(json.dumps(rec), encoding="utf-8")
            try:
                folder, report = ws.score()
                print("SCORED gate=" + report["gate"] + " (no refusal for a capture under another block)")
            except sweep.Refused as exc:
                print("REFUSED " + str(exc))
    finally:
        t.doCleanups()
''')


def main():
    if MODE == "suite":
        dest = tree(WORK / "m9", True)
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry", "-f"],
                              cwd=dest, capture_output=True, text=True, timeout=3000)
        tail = proc.stderr.strip().splitlines()[-4:]
        print("\n".join(tail))
        fails = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        print("FIRST FAILURE: " + (fails[0] if fails else "none"))
        print("RESULT M9 " + ("caught" if proc.returncode else "SURVIVED the full suite") + f" (exit {proc.returncode})")
    else:
        for name, mutate in (("orig", False), ("m9", True)):
            dest = tree(WORK / ("direct-" + name), mutate)
            proc = subprocess.run([sys.executable, "-B", "-c", DIRECT], cwd=dest / "tools/den-photometry",
                                  capture_output=True, text=True, timeout=900)
            out = (proc.stdout.strip() or proc.stderr.strip()[-800:])
            print(f"RESULT {name}: {out}")


main()
