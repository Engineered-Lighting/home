#!/usr/bin/env python3
"""Spec-lens verification of finding mutant-unknown-counted-as-miss-survives (rung 9, tools/den-core/score.py).

    python3 -B verify_spec_mutant-unknown-counted-as-miss-survives.py <den-core-live checkout root>

The gate (rung9-gate-v0.json, surrogates.anticipation.definition) documents the rule under test: "The rate is the
mean over episodes whose light is predictable (no unknown level)". This probe checks, in a temp copy only (the
checkout is never touched; synthetic data; nothing binds or reads a host):
 1. COVERAGE: does any ScoreTest call of surrogates() ever see an unknown episode?
 2. MUTANTS (each run against test_rung9 ScoreTest, RecordTest, LightModelTest, FixturesTest with failfast):
      U1: an unknown start counts as an anticipation miss (the reported mutant M14)
      U3: an unknown start counts as an anticipation AND stability hit (the reverse direction)
 3. IMPACT: surrogates() on a hand-made timeline where every episode starts before any level is known, original
    vs U3: the original gives rate None (score.py's check then fails), U3 gives 1.0 (the check would pass).
Prints RESULT lines.
"""
import os
import shutil
import subprocess
import sys
import tempfile

COPY = ["tools/den-core", "ha-config/custom_components/den_lighting", "stack/services/perception-lab",
        "tools/la-latency"]
OLD = "        if not before or before[0][1] is None:\n            unknown += 1\n            continue"
U1 = ("        if not before or before[0][1] is None:\n            hits[\"anticipation\"].append(False)\n"
      "            unknown += 1\n            continue")
U3 = ("        if not before or before[0][1] is None:\n            hits[\"anticipation\"].append(True)\n"
      "            hits[\"stability\"].append(True)\n            unknown += 1\n            continue")

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
res = unittest.TextTestRunner(stream=open(__import__("os").devnull, "w"), verbosity=0).run(suite)
print("COVERAGE tests_run=%d failures=%d errors=%d skipped=%d surrogates_calls=%d max_unknown=%s"
      % (res.testsRun, len(res.failures), len(res.errors), len(res.skipped), len(seen), max(seen) if seen else None))
'''

DIRECT = r'''
import sys
sys.path.insert(0, ".")
import score, light_model
m = light_model.fake(); m.update(version="t", fake=False)
model = light_model.LightModel(m)
L = light_model.LIGHTS
OFF = {"on": False, "brightness": None, "kelvin": None}
NOLEVEL = {"on": True, "brightness": None, "kelvin": None}   # a recorded ON with no level, as the HA export gives
LOOK = {"on": True, "brightness": 255, "kelvin": 2700}
before = {L[0]: NOLEVEL, L[1]: NOLEVEL, L[2]: OFF, L[3]: OFF}
look = {L[0]: LOOK, L[1]: LOOK, L[2]: OFF, L[3]: OFF}
timeline = [(0.0, before, "mode"), (106.0, look, "need"), (300.0, before, "mode")]
found = [("room", 100.0, 250.0)]
out = score.surrogates(timeline, found, lambda track: ["desk-1"], model, 10, 0.2, 0.1)
a, s = out["anticipation"], out["stability"]
ok = all(r is not None and r >= f for r, f in ((a["rate"], 0.5), (s["rate"], 0.9)))
print("anticipation rate=%s scored=%d unknown=%d; stability rate=%s; checks at proposed floors would be %s"
      % (a["rate"], a["scored"], a["unknown"], s["rate"], "OK" if ok else "FAIL"))
'''


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)


def main():
    root = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="verify-spec-r9-unknown-")
    try:
        for rel in COPY:
            src = os.path.join(root, rel)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(work, rel), ignore=shutil.ignore_patterns("__pycache__"))
        core = os.path.join(work, "tools", "den-core")
        path = os.path.join(core, "score.py")
        text = open(path, encoding="utf-8").read()
        if text.count(OLD) != 1:
            print("RESULT not-applied: %d matches" % text.count(OLD))
            return 1
        p = run([sys.executable, "-B", "-c", DRIVER], core)
        print("RESULT " + (p.stdout.strip() or p.stderr.strip()[-1500:]))
        for name, new in (("ORIGINAL", None), ("U3", U3)):
            open(path, "w", encoding="utf-8").write(text if new is None else text.replace(OLD, new))
            p = run([sys.executable, "-B", "-c", DIRECT], core)
            print("RESULT IMPACT %-8s %s" % (name, p.stdout.strip() or p.stderr.strip()[-1500:]))
        open(path, "w", encoding="utf-8").write(text)
        for mid, new in (("U1-unknown-start-counts-as-miss", U1), ("U3-unknown-start-counts-as-hit", U3)):
            open(path, "w", encoding="utf-8").write(text.replace(OLD, new))
            p = run([sys.executable, "-B", "-m", "unittest", "-f", "test_rung9.ScoreTest", "test_rung9.RecordTest",
                     "test_rung9.LightModelTest", "test_rung9.FixturesTest"], core)
            open(path, "w", encoding="utf-8").write(text)
            summary = " / ".join(l for l in p.stderr.strip().splitlines() if l.startswith(("Ran ", "OK", "FAILED")))
            print("RESULT %s %s  | %s" % (mid, "caught" if p.returncode else "survived", summary))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
