"""verify_diff-two-way-untested (skeptical verifier, INTENT lens).

The reported surviving mutant RD_both makes run._both one-way: events.scene_matches(decide, den_lighting) instead
of matching each way. scene_matches treats a null TARGET field as matching anything, while a null ACTUAL field never
matches a set target field. So the one-way rule still flags the README's own example (decide asks for a level,
den_lighting's last-level ON has none); it only stops flagging the reverse (decide requests a null level while
den_lighting wants a level).

This probe asks whether that reverse case is reachable: it runs run.diff() over every log shape the den-core tests
build (synthetic evenings with and without touch/game, several seeds, clean and noisy; a facts-only replay with
assumed occupancy; facts with journal occupancy, with and without recorded on/off-only bulbs and the plant off),
counts the comparisons where the two directions disagree (and, of those, the decide-null/den-level ones the
one-way rule stops flagging), and compares the spans of the real and the one-way rule.
It then builds the reverse case by hand (decide's latest request edited to a null level) to show the mutant is not
equivalent on such a log.

How to run (synthetic data only; in memory; no network):
    python3 -B verify_diff-two-way-untested.py /path/to/den-core-checkout
"""
import copy
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))

import events  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
from synthetic import T0  # noqa: E402

POLICIES = ["need", "incumbent", "decide"]
REAL_BOTH = run._both
SEEN = {"calls": 0, "asym": [], "reverse": []}


def counting(a, b):
    SEEN["calls"] += 1
    if events.scene_matches(a, b) != events.scene_matches(b, a):
        SEEN["asym"].append((a, b))
    if events.scene_matches(a, b) and not events.scene_matches(b, a):  # the direction one-way stops flagging
        SEEN["reverse"].append((a, b))
    return REAL_BOTH(a, b)


def one_way(a, b):
    return events.scene_matches(a, b)


def logs():
    for seed in (20261103, 1, 2, 3):
        for clean in (True, False):
            for opts in ({}, {"touch": True}, {"game": True}, {"touch": True, "game": True}):
                rates = {"unclear_rate": 0, "invalid_rate": 0, "fail_rate": 0} if clean else {}
                items, _, _ = synthetic.evening(seed=seed, **rates, **opts)
                yield ("evening seed=%s clean=%s %s" % (seed, clean, opts),
                       runner.run(runner.tagged(items, "synthetic"), runner.load_policies(POLICIES), synthetic=True))
    items, _, _ = synthetic.evening()
    facts = [item for item in items if item[1] == "facts"]
    yield "facts-only, assumed occupancy", runner.run(runner.tagged(facts, "fact_log"), runner.load_policies(POLICIES),
                                                      assume_occupied=True)
    edges = [(t, kind, {"value": d["value"], "via": "jetson_journal"}) for t, kind, d in items if kind == "occupancy"]
    base = runner.tagged(facts, "fact_log") + runner.tagged(edges, "occupancy_journal")
    yield "facts + journal, plant on", runner.run(base, runner.load_policies(POLICIES))

    def states(t, on):
        return [(t, "light_observed", {"entity_id": e, "on": on, "brightness": None, "kelvin": None,
                                       "context": "own"}) for e in events.LIGHTS]
    recorded = states(T0 - 600, True) + states(T0 - 470.2, False) + states(T0 - 60.1, True) + states(T0 + 1569.8, False)
    yield "facts + journal + recorded bulbs, plant off", runner.run(runner.tagged(recorded, "recorder_lights") + base,
                                                                    runner.load_policies(POLICIES), plant=False)


def main():
    total_asym, total_rev, total_diff, last = 0, 0, 0, None
    for name, (header, log) in logs():
        SEEN["calls"], SEEN["asym"], SEEN["reverse"] = 0, [], []
        run._both = counting
        spans_real = run.diff(header, log)
        run._both = one_way
        spans_one = run.diff(header, log)
        run._both = REAL_BOTH
        total_asym += len(SEEN["asym"])
        total_rev += len(SEEN["reverse"])
        total_diff += spans_real != spans_one
        print("RESULT: %-55s comparisons=%5d asymmetric=%d (decide-null/den-level=%d) spans real/one-way=%d/%d same=%s" % (
            name, SEEN["calls"], len(SEEN["asym"]), len(SEEN["reverse"]), len(spans_real), len(spans_one),
            spans_real == spans_one))
        last = (header, log) if "evening" in name and last is None else last
    print("RESULT: over all logs: asymmetric comparisons=%d, of which decide-null/den-level=%d; logs where one-way spans "
          "differ=%d" % (total_asym, total_rev, total_diff))
    # Hand-built reverse case: decide's base decision edited to request a null-level ON for one bulb.
    header, log = last
    edited = copy.deepcopy(log)
    rl = "light.den_rear_left"
    base = [d for d in edited if d["kind"] == "decision" and d["source"] == "decide"
            and d["data"]["requested_scene"].get(rl, {}).get("on") and "base" in d["data"]["why"]]
    base[0]["data"]["requested_scene"][rl] = {"on": True, "brightness": None, "kelvin": None}
    real = [s for s in run.diff(header, edited) if s["entity_id"] == rl and s["start"] == base[0]["t"]]
    run._both = one_way
    one = [s for s in run.diff(header, edited) if s["entity_id"] == rl and s["start"] == base[0]["t"]]
    run._both = REAL_BOTH
    print("RESULT: hand-edited null-level decide request at %+.1f s: real rule opens %s, one-way opens %s" % (
        base[0]["t"] - log[0]["t"], real, one))
    if total_rev == 0:
        print("PASS: no log den-core builds reaches the direction the one-way mutant stops flagging "
              "(every asymmetric comparison is decide-level vs den-null, which one-way still flags)")
    else:
        print("FAIL: the reverse direction is reachable on %d comparison(s)" % total_rev)


main()
