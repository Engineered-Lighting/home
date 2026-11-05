"""verify_validate-check3-inforce-untested (skeptical verifier, INTENT lens).

Checks whether the reported surviving mutant V_inforce of tools/den-core/validate.py is a real (non-equivalent)
weakening of check 3 ("only the look"), as validate.py's docstring defines it: "each decide decision in force for
that bulb (the latest at the interval's start, and any during it)".

Mutant (applied in memory only, to the source text; nothing is written to the checkout):
    in_force = [... d["t"] <= start][-1:] + [... start < d["t"] < end]   ->   in_force = [... d["t"] <= start][-1:]

Case: one clean synthetic evening (need, incumbent, decide), built as test_validate.evening() builds it. Take the
longest run.diff() interval for light.den_rear_left (a reading-look interval). Insert, strictly inside it, a copy of
decide's in-force decision that
  (a) chooses the 'base' candidate (not the look) and requests 120@3000K for the bulb, or
  (b) chooses the look but requests 120@3000K for the bulb.
Either keeps the bulb differing from den_lighting, so the interval stays open around the inserted decision.
Expected of the real check 3: FAIL in both cases. If the mutant reports OK, the mutant is not equivalent and the
test suite's survival of it is a real coverage gap.

How to run (synthetic data only; in memory; no network):
    python3 -B verify_validate-check3-inforce-untested.py /path/to/den-core-checkout
"""
import copy
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
HERE = ROOT / "tools" / "den-core"
sys.path.insert(0, str(HERE))

import run  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
import validate  # noqa: E402

OLD = 'in_force = [d for d in mine if d["t"] <= start][-1:] + [d for d in mine if start < d["t"] < end]'
NEW = 'in_force = [d for d in mine if d["t"] <= start][-1:]'
RL = "light.den_rear_left"
ODD = {"on": True, "brightness": 120, "kelvin": 3000}


def mutant():
    src = (HERE / "validate.py").read_text("utf-8")
    assert src.count(OLD) == 1, "mutation pattern not found exactly once"
    spec = importlib.util.spec_from_loader("validate_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(HERE / "validate.py")
    exec(compile(src.replace(OLD, NEW), "validate_mutant.py", "exec"), module.__dict__)
    return module


def main():
    items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0)
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["need", "incumbent", "decide"]),
                             synthetic=True, sources=[{"name": "synthetic:evening", "params": {}}])
    first = log[0]["t"]
    spans = [s for s in run.diff(header, log) if s["entity_id"] == RL]
    span = max(spans, key=lambda s: s["end"] - s["start"])
    t = round((span["start"] + span["end"]) / 2, 1)
    print("RESULT: %d diff interval(s) for %s; longest %+.1f s .. %+.1f s; inserting at %+.1f s" % (
        len(spans), RL, span["start"] - first, span["end"] - first, t - first))
    mut = mutant()
    print("RESULT: unedited evening: real check 3 %s | mutant %s" % (
        validate.only_the_look(header, log)[0], mut.only_the_look(header, log)[0]))
    bad = 0
    for label, candidates in (
            ("(a) chooses base, requests 120@3000K", [{"scene_id": "base", "cost": 0, "why": "x"},
                                                      {"scene_id": "look:reading@0", "cost": 3, "why": "y"}]),
            ("(b) chooses the look, requests 120@3000K", None)):
        event = copy.deepcopy([d for d in log if d["kind"] == "decision" and d["source"] == "decide"
                               and d["t"] <= t][-1])
        event["t"] = event["data"]["ts"] = t
        event["data"]["requested_scene"][RL] = dict(ODD)
        if candidates is not None:
            event["data"]["candidates"] = candidates
        n = max(i for i, e in enumerate(log) if e["t"] <= t) + 1
        edited = log[:n] + [event] + log[n:]
        inside = [s for s in run.diff(header, edited) if s["entity_id"] == RL and s["start"] < t < s["end"]]
        real, fake = validate.only_the_look(header, edited), mut.only_the_look(header, edited)
        print("RESULT: %s: strictly inside an interval: %s; real check 3: %s: %s" % (
            label, bool(inside), real[0], real[1][:150]))
        print("RESULT: %s: mutant check 3: %s: %s" % (label, fake[0], fake[1][:150]))
        if real[0] == "FAIL" and fake[0] == "OK" and inside:
            bad += 1
            print("FAIL: the mutant passes a decision inside an interval that the real check 3 fails")
        else:
            print("PASS: the mutant and the real check agree here")
    print("RESULT: mutant non-equivalent in %d of 2 cases" % bad)


main()
