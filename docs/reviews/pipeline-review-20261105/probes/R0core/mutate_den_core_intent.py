"""mutate_den_core_intent (verifier, INTENT lens): re-checks two reported surviving mutants of den-core
independently of the reporter's runner.

  C_trunc   check.py compares only min(len(logged), len(replayed)) lines instead of max(...)
  E_match_b events.scene_matches brightness tolerance widened from 1 to 3

For each mutant it (1) applies it to a copy of the checkout made under MUT_DIR (never the checkout itself),
(2) runs the whole den-core suite with failfast, (3) runs a direct behavioural check showing the mutant is not
equivalent (a truncated / appended log for C_trunc; a 2-step brightness difference for E_match_b), then restores.
A control mutant that the suite must catch (CTRL: scene_matches brightness tolerance 1 -> 0) shows the runner
can see a failure at all.

How to run (synthetic data only; temp folders only; no network):
    python3 -B mutate_den_core_intent.py /path/to/den-core-checkout /path/to/mut-dir
Prints a RESULT table: id | suite result | behavioural check.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MUT = Path(sys.argv[2]).resolve()
D = "tools/den-core/"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

MUTANTS = [
    ("C_trunc", D + "check.py", "for index in range(max(len(want), len(got))):",
     "for index in range(min(len(want), len(got))):"),
    ("E_match_b", D + "events.py", 'abs(b - actual["brightness"]) <= 1)', 'abs(b - actual["brightness"]) <= 3)'),
    ("CTRL", D + "events.py", 'abs(b - actual["brightness"]) <= 1)', 'abs(b - actual["brightness"]) <= 0)'),
]

BEHAVIOUR = r'''
import sys, tempfile
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import events, runner, synthetic, check
which = sys.argv[2]
if which == "C_trunc":
    items, _, _ = synthetic.evening(touch=True)
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                             synthetic=True)
    with tempfile.TemporaryDirectory() as tmp:
        full = Path(tmp) / "log.jsonl"
        events.write(full, header, log)
        lines = full.read_text("ascii").split("\n")[:-1]
        trunc = Path(tmp) / "trunc.jsonl"
        trunc.write_text("\n".join(lines[:1000]) + "\n", "ascii")
        r1 = check.check(trunc)
        extra = dict(log[-1]); extra["seq"] = len(log); extra["t"] = log[-1]["t"] + 1.0; extra["kind"] = "tick"
        extra["source"] = "runner"; extra["data"] = {}
        app = Path(tmp) / "appended.jsonl"
        app.write_text("\n".join(lines + [events.dumps(extra)]) + "\n", "ascii")
        r2 = check.check(app)
    print("BEHAVIOUR truncated(1000 of %d lines): ok=%s reason=%s | appended line: ok=%s reason=%s" % (
        len(lines), r1["ok"], r1["reason"], r2["ok"], r2["reason"]))
else:
    a = {"on": True, "brightness": 180, "kelvin": 2700}
    b = {"on": True, "brightness": 182, "kelvin": 2700}
    print("BEHAVIOUR scene_matches(180@2700, 182@2700) = %s (den_lighting rule: False)" % events.scene_matches(a, b))
'''


def copy_tree(dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"), symlinks=True)
    (dst / ".git").write_text("gitdir: /nonexistent\n")  # as the reporter's runner: events.write's refusal test


def suite(root):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core", "-p",
                        "test_*.py"], cwd=str(root), capture_output=True, text=True, timeout=900, env=ENV)
    tail = p.stderr.strip().splitlines()
    first = next((line for line in tail if line.startswith(("FAIL:", "ERROR:"))), "")
    ran = next((line for line in tail if line.startswith("Ran ")), "")
    return p.returncode, first, ran, tail[-1] if tail else "", round(time.time() - t, 1)


def behaviour(root, which):
    p = subprocess.run([sys.executable, "-B", "-c", BEHAVIOUR, str(root / D), which], cwd=str(root),
                       capture_output=True, text=True, timeout=600, env=ENV)
    return (p.stdout + p.stderr).strip().splitlines()[-1]


work = MUT / "copy"
copy_tree(work)
code, first, ran, last, secs = suite(work)
print("baseline: exit=%d %s %s (%ss)" % (code, ran, last, secs))
print("baseline " + behaviour(work, "C_trunc"))
print("baseline " + behaviour(work, "E_match_b"))
if code != 0:
    print("FAIL: baseline suite does not pass in the copy")
    sys.exit(2)
rows = []
for mid, rel, old, new in MUTANTS:
    path = work / rel
    text = path.read_text("utf-8")
    assert text.count(old) == 1, (mid, text.count(old))
    path.write_text(text.replace(old, new), "utf-8")
    try:
        code, first, ran, last, secs = suite(work)
        beh = behaviour(work, "C_trunc" if mid == "C_trunc" else "E_match_b")
    finally:
        path.write_text(text, "utf-8")
    rows.append((mid, "caught" if code else "survived", first or (ran + " " + last), beh))
    print("%-10s %-8s %s | %s (%ss)" % (mid, rows[-1][1], rows[-1][2][:100], beh, secs), flush=True)
print()
for mid, result, by, beh in rows:
    print("RESULT %s | %s | %s | %s" % (mid, result, by, beh))
