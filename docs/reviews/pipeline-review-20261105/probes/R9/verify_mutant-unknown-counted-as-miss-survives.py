#!/usr/bin/env python3
"""Verify finding mutant-unknown-counted-as-miss-survives (rung 9, tools/den-core/score.py surrogates()).

    python3 -B verify_mutant-unknown-counted-as-miss-survives.py <den-core-live checkout root>

What it checks, all in a temp copy (the checkout is never touched; synthetic data only; nothing binds or reads a
host):
 1. COVERAGE: runs test_rung9.ScoreTest on the unmodified copy with score.surrogates wrapped, and prints the
    'unknown' count of every surrogates() call the tests make (are unknown episodes ever produced by a test?).
 2. MUTANTS: applies each mutant to score.py in the copy and runs test_rung9 (the only test module that imports
    score) with failfast; prints caught/survived and the unittest summary line (so a skipped run cannot pose as a
    survivor):
      U1: an unknown start (before[0][1] is None) counts as an anticipation miss instead of being excluded
      U2: an unknown rise inside the window (any e is None) counts as an anticipation miss instead of excluded
 3. NON-EQUIVALENCE: calls surrogates() directly, original vs U1, on a hand-made timeline with one scored hit
    episode and one episode that starts before any light level is known; prints both rates.
"""
import os
import shutil
import subprocess
import sys
import tempfile

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]

U1_OLD = "        if not before or before[0][1] is None:\n            unknown += 1\n            continue"
U1_NEW = ("        if not before or before[0][1] is None:\n            hits[\"anticipation\"].append(False)\n"
          "            unknown += 1\n            continue")
U2_OLD = "        if any(e is None for _, e, _ in up):\n            unknown += 1\n            continue"
U2_NEW = ("        if any(e is None for _, e, _ in up):\n            hits[\"anticipation\"].append(False)\n"
          "            unknown += 1\n            continue")

DRIVER = r'''
import sys, unittest
sys.path.insert(0, ".")
import score, test_rung9
seen = []
orig = score.surrogates
def wrapped(*a, **k):
    out = orig(*a, **k)
    seen.append(out["anticipation"]["unknown"])
    return out
score.surrogates = wrapped
suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_rung9.ScoreTest)
res = unittest.TextTestRunner(stream=open("/dev/null", "w"), verbosity=0).run(suite)
print("COVERAGE tests_run=%d failures=%d errors=%d skipped=%d" % (res.testsRun, len(res.failures), len(res.errors),
                                                                 len(res.skipped)))
print("COVERAGE surrogates_calls=%d unknown_counts=%s max_unknown=%s" % (len(seen), seen, max(seen) if seen else None))
'''

DIRECT = r'''
import sys
sys.path.insert(0, ".")
import score, light_model
m = light_model.fake(); m.update(version="t", fake=False)
model = light_model.LightModel(m)
L = light_model.LIGHTS
OFF = {"on": False, "brightness": None, "kelvin": None}
DIM = {"on": True, "brightness": 60, "kelvin": 2700}
BRIGHT = {"on": True, "brightness": 220, "kelvin": 2700}
unknown_scene = {L[0]: None, L[1]: OFF, L[2]: OFF, L[3]: OFF}          # front left never seen yet
dim = {L[0]: DIM, L[1]: OFF, L[2]: OFF, L[3]: OFF}
bright = {L[0]: BRIGHT, L[1]: OFF, L[2]: OFF, L[3]: OFF}
timeline = [(0.0, unknown_scene, "mode"), (20.0, dim, "mode"), (105.0, bright, "need"), (300.0, dim, "mode")]
found = [("A", 5.0, 50.0),       # starts while the front left's level is unknown
         ("B", 100.0, 200.0)]    # starts dim, decide raises it at 105 s
out = score.surrogates(timeline, found, lambda track: ["desk-1"], model, 10, 0.25, 0.25)
print("DIRECT anticipation=%s" % out["anticipation"])
'''


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-r9-unknown-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        path = os.path.join(core, "score.py")
        text = open(path, encoding="utf-8").read()

        p = run([sys.executable, "-B", "-c", DRIVER], core)
        print(p.stdout.strip() or p.stderr.strip()[-2000:])

        p = run([sys.executable, "-B", "-c", DIRECT], core)
        print("ORIGINAL " + (p.stdout.strip() or p.stderr.strip()[-2000:]))
        open(path, "w", encoding="utf-8").write(text.replace(U1_OLD, U1_NEW))
        p = run([sys.executable, "-B", "-c", DIRECT], core)
        print("U1       " + (p.stdout.strip() or p.stderr.strip()[-2000:]))
        open(path, "w", encoding="utf-8").write(text)

        for mid, old, new in (("U1-unknown-start-counts-as-miss", U1_OLD, U1_NEW),
                              ("U2-unknown-rise-counts-as-miss", U2_OLD, U2_NEW)):
            if text.count(old) != 1:
                print("RESULT %s not-applied (%d matches)" % (mid, text.count(old)))
                continue
            open(path, "w", encoding="utf-8").write(text.replace(old, new))
            p = run([sys.executable, "-B", "-m", "unittest", "-f", "test_rung9.ScoreTest",
                     "test_rung9.RecordTest", "test_rung9.LightModelTest"], core)
            open(path, "w", encoding="utf-8").write(text)
            lines = p.stderr.strip().splitlines()
            summary = " / ".join(l for l in lines if l.startswith(("Ran ", "OK", "FAILED")))
            print("RESULT %s %s  | %s" % (mid, "caught" if p.returncode else "survived", summary))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
