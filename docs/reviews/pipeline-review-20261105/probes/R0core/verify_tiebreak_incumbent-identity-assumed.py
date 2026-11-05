"""verify_tiebreak_incumbent-identity-assumed (skeptical verifier, TIEBREAK lens).

Finding: incumbent.Incumbent forces den_lighting's automatic and gaming switches on at the first event, and labels
itself with the checkout's manifest version; 'if the live gaming switch is off ... replays ... model a different
incumbent'.

What this probe checks (synthetic data only, in memory):
  1. Which den-core adapters can emit gaming_sample at all (source scan of sources.py and synthetic.py).
  2. On real-data-shaped inputs (the synthetic evening's fact lines, occupancy as journal edges, the four bulbs as
     recorded on/off-only states, plant off; no gaming samples, as every real adapter gives), the committed
     incumbent (gaming switch forced on) against a copy whose gaming switch is held off (a live switch that is
     off): are the incumbent's requested scenes, its mode events (less revision) and decide's requests the same?
  3. The same with the synthetic game samples (synthetic only, never evidence): there the switch does matter.
  4. The automatic switch held off: the incumbent sends nothing (what a paused live den_lighting does).

How to run:
    python3 -B verify_tiebreak_incumbent-identity-assumed.py /path/to/den-core-checkout
"""
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))

import events  # noqa: E402
import incumbent  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
from synthetic import T0  # noqa: E402


def emitters():
    found = {}
    for name in ("sources.py", "synthetic.py"):
        text = (ROOT / "tools" / "den-core" / name).read_text("utf-8")
        found[name] = len(re.findall(r'"gaming_sample"', text))
    return found


def real_shaped(game):
    items, _, _ = synthetic.evening(game=game)
    facts = [(t, k, d) for t, k, d in items if k == "facts"]
    edges = [(t, k, {"value": d["value"], "via": "jetson_journal"}) for t, k, d in items if k == "occupancy"]
    games = [(t, k, d) for t, k, d in items if k == "gaming_sample"]

    def states(t, on):
        return [(t, "light_observed", {"entity_id": e, "on": on, "brightness": None, "kelvin": None,
                                       "context": "own"}) for e in events.LIGHTS]
    recorded = states(T0 - 600, True) + states(T0 - 470.2, False) + states(T0 - 60.1, True) + states(T0 + 1569.8, False)
    inputs = (runner.tagged(recorded, "recorder_lights") + runner.tagged(facts, "fact_log")
              + runner.tagged(edges, "occupancy_journal"))
    if game:
        inputs += runner.tagged(games, "synthetic")
    return inputs


def policies(gaming_switch=None, automatic=None):
    pols = runner.load_policies(["need", "incumbent", "decide"])
    inc = next(p for p in pols if p.id == "incumbent")
    c = inc.controller
    if gaming_switch is not None:
        orig_g = c.set_gaming_enabled
        c.set_gaming_enabled = lambda enabled, now: orig_g(gaming_switch, now)
    if automatic is not None:
        orig_a = c.set_enabled
        c.set_enabled = lambda enabled, now: orig_a(automatic, now)
    return pols


def summary(log):
    inc = [(e["t"], sorted((b, tuple(sorted(s.items()))) for b, s in e["data"]["requested_scene"].items()))
           for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]
    dec = [(e["t"], sorted((b, tuple(sorted(s.items()))) for b, s in e["data"]["requested_scene"].items()))
           for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    modes = [(e["t"], {k: v for k, v in e["data"].items() if k != "revision"})
             for e in log if e["kind"] == "mode"]
    return inc, dec, modes


def plant_shaped(game):
    """The synthetic evening's first bulb states (with levels), its fact lines and occupancy edges, plant on."""
    items, _, _ = synthetic.evening(game=game)
    keep = [(t, k, d) for t, k, d in items if k in ("light_observed", "facts")]
    edges = [(t, k, {"value": d["value"], "via": "jetson_journal"}) for t, k, d in items if k == "occupancy"]
    games = [(t, k, d) for t, k, d in items if k == "gaming_sample"]
    inputs = runner.tagged(keep, "fact_log") + runner.tagged(edges, "occupancy_journal")
    if game:
        inputs += runner.tagged(games, "synthetic")
    return inputs


def main():
    print("RESULT 1: gaming_sample literals per file: %s" % emitters())
    for shape, build, plant in (("recorded on/off bulbs, plant off", real_shaped, False),
                                ("bulbs seen once with levels, plant on", plant_shaped, True)):
        for game in (False, True):
            inputs = build(game)
            _, forced = runner.run(inputs, policies(), plant=plant)
            _, off = runner.run(inputs, policies(gaming_switch=False), plant=plant)
            _, paused = runner.run(inputs, policies(automatic=False), plant=plant)
            a, b = summary(forced), summary(off)
            label = "WITH synthetic game samples" if game else "no gaming samples (as every real adapter)"
            print("RESULT %s | %s: incumbent decisions forced-on=%d gaming-switch-off=%d automatic-off=%d; "
                  "same incumbent requests=%s; same decide requests=%s; same modes (less revision)=%s"
                  % (shape, label, len(a[0]), len(b[0]),
                     sum(1 for e in paused if e["kind"] == "decision" and e["source"] == "incumbent"),
                     a[0] == b[0], a[1] == b[1], a[2] == b[2]))


if __name__ == "__main__":
    main()
