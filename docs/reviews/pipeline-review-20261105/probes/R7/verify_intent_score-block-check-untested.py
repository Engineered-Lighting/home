#!/usr/bin/env python3
"""verify_intent_score-block-check-untested (intent-lens verifier, finding score-block-check-untested).

Checks two things about photometry.py score_root's per-capture METHOD block check
(`cap.get("method_block_sha256") != method.block_sha256`, which METHOD-V1 "What may change" promises:
"a score refuses captures taken under another block"):

  suite  : with the check removed (mutant M9), does test_photometry (the only module that runs
           score_root) still pass?  Prints RESULT M9 caught/SURVIVED.
  direct : is the check load-bearing?  In an unmutated copy and in the M9 copy, build the tests' own
           two-day workspace (synthetic frames, the tests' fake camera on 127.0.0.1), then rewrite
           day-20261111/capture-1/capture.json's method_block_sha256 as if that capture folder had been
           copied in from a photometry folder made under another block, and score.  Prints REFUSED/SCORED.
  grep   : does any test name the refusal text?

    python3 -B verify_intent_score-block-check-untested.py <branch-root> <work-dir> suite|direct|grep

The branch checkout is never modified: everything happens in copies under <work-dir> (each copy gets
an empty .git folder, as the tests expect the tool to sit inside a checkout).  TMPDIR for the tests is
a fresh folder under <work-dir>/tmp, outside any checkout.  No real host is contacted.
"""
import os
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
REFUSAL = "taken under another method or meter-norm/v1"


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


def env_for(name):
    tmp = WORK / "tmp" / name
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    return dict(os.environ, TMPDIR=str(tmp))


DIRECT = textwrap.dedent('''
    import json
    import test_photometry as tp  # imports card first, which puts tools/den-light on the path
    import sweep
    t = tp.ScoreTests("test_two_days_pass_and_write_the_records")
    t.setUp()
    try:
        with tp.workspace() as ws:
            t.two_days(ws)
            cap = ws.root / "day-20261111" / "capture-1" / "capture.json"
            rec = json.loads(cap.read_text(encoding="utf-8"))
            rec["method_block_sha256"] = "e" * 64
            cap.write_text(json.dumps(rec), encoding="utf-8")
            try:
                folder, report = ws.score()
                print("SCORED gate=" + report["gate"] + " with a capture recorded under another METHOD block")
            except sweep.Refused as exc:
                print("REFUSED " + str(exc))
    finally:
        t.doCleanups()
''')


def main():
    if MODE == "grep":
        hits = [p.name for p in (ROOT / "tools/den-photometry").glob("test_*.py")
                if REFUSAL in p.read_text(encoding="utf-8") or "method_block_sha256\"] =" in p.read_text(encoding="utf-8")]
        print("RESULT tests naming the score's capture-block refusal: " + (", ".join(hits) if hits else "none"))
    elif MODE == "suite":
        dest = tree(WORK / "suite-m9", True)
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_photometry"],
                              cwd=dest / "tools/den-photometry", capture_output=True, text=True, timeout=3000,
                              env=env_for("suite-m9"))
        tail = proc.stderr.strip().splitlines()[-4:]
        print("\n".join(tail))
        fails = [l for l in proc.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        print("FIRST FAILURE: " + (fails[0] if fails else "none"))
        print("RESULT M9 " + ("caught" if proc.returncode else "SURVIVED test_photometry") + f" (exit {proc.returncode})")
    else:
        for name, mutate in (("orig", False), ("m9", True)):
            dest = tree(WORK / ("direct-" + name), mutate)
            proc = subprocess.run([sys.executable, "-B", "-c", DIRECT], cwd=dest / "tools/den-photometry",
                                  capture_output=True, text=True, timeout=900, env=env_for("direct-" + name))
            out = (proc.stdout.strip() or proc.stderr.strip()[-800:])
            print(f"RESULT {name}: {out}")


if __name__ == "__main__":
    main()
