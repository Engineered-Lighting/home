#!/usr/bin/env python3
"""verify3_fixture-coverage-scenario: is freeze_labels' "fixture does not cover these sessions and checks"
refusal load-bearing inside N1, and does N2's loader catch a stale fixture/labels pair anyway?

(INTENT verifier, finding fixture-coverage-refusal-untested.)

Scenario (synthetic takes from the branch's own test helpers, all under a temp dir, loopback only):
four sessions labelled, fixture-v1 frozen; the owner then records and labels a fifth session (a3) and runs
freeze labels without freezing the fixture again. Run once on an unmodified copy and once on a copy with
the m16 mutant (`if False:` in place of the coverage test). On the mutant, the produced pair is handed to
den-n2's score.load_n1 (copied from the den-n2 checkout, which carries the same tools/den-label) to see
whether N2 refuses it.

Run:
    python3 -B verify3_fixture-coverage-scenario.py <den-label checkout> <den-n2 checkout> <work dir>
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT, N2ROOT, WORK = (Path(a).resolve() for a in sys.argv[1:4])
WORK.mkdir(parents=True, exist_ok=True)
OLD = 'if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:'

INNER = r'''
import io, os, sys
from unittest import mock
sys.path.insert(0, os.getcwd())
import test_den_label as T
n1 = T.n1
T.setUpModule()
t = T.FreezeTests("test_canonical_bytes_give_fixed_hashes")
t.setUp()
try:
    ws = t.build()
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
        fixture_line, _ = ws.freeze_fixture()
    print("INFO", fixture_line.split(" sha256")[0], [s["take"] for s in __import__("json").loads(
        (ws.root / "fixture-v1" / "fixture.json").read_bytes())["sessions"]])
    T.staged_take(t.takes, "a3", "hard-a", T.BASE_UNIX + 2 * T.DAY)
    added, notes = ws.scan()
    print("INFO registered later:", [(e["session"], e["split"]) for e in added])
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 11 * T.DAY):
        t.boxes_done(ws, "a3")
        t.labels_done(ws, "a3")
    with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 12 * T.DAY):
        try:
            labels_line, record, _ = ws.freeze_labels()
        except n1.Refused as exc:
            print("RESULT freeze labels REFUSED:", exc)
            sys.exit(0)
    print("RESULT freeze labels SUCCEEDED:", labels_line.split(" sha256")[0])
    print("INFO EvalRecord gold_set:", record["gold_set"]["n1"]["fixture"]["version"], "fixture version named")
    sys.path.insert(0, os.path.join(os.path.dirname(os.getcwd()), "den-n2"))
    import score
    text = fixture_line + "\n" + labels_line + "\n"
    for split in ("development", "held_out"):
        try:
            score.load_n1(ws.root, split, text)
            print(f"RESULT N2 load_n1({split}) ACCEPTED the stale pair")
        except score.Refused as exc:
            print(f"RESULT N2 load_n1({split}) REFUSED: {exc}")
finally:
    t.doCleanups()
    T.tearDownModule()
'''


def run(tag, mutate):
    folder = Path(tempfile.mkdtemp(prefix=f"scenario-{tag}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, folder / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(N2ROOT / "tools" / "den-n2", folder / "tools" / "den-n2",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if mutate:
        path = folder / "tools/den-label/n1.py"
        text = path.read_text(encoding="utf-8")
        assert text.count(OLD) == 1
        path.write_text(text.replace(OLD, "if False:"), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", "-c", INNER], cwd=folder / "tools/den-label",
                          capture_output=True, text=True, timeout=1200)
    print(f"== {tag} (exit {proc.returncode})")
    for line in proc.stdout.splitlines():
        print("  " + line)
    if proc.returncode:
        print("  STDERR: " + "\n  ".join(proc.stderr.strip().splitlines()[-8:]))
    shutil.rmtree(folder, ignore_errors=True)
    return proc.stdout


orig = run("original", False)
mut = run("mutant-m16", True)
print("RESULT original refuses:", "freeze labels REFUSED" in orig and "does not cover" in orig)
print("RESULT mutant freezes against the stale fixture:", "freeze labels SUCCEEDED" in mut)
print("RESULT N2 refuses the mutant's pair in some split:", "REFUSED: fixture-v1 does not hold" in mut)
