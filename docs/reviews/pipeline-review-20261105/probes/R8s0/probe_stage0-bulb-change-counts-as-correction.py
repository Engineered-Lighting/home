"""probe_stage0-bulb-change-counts-as-correction: verifier probe (intent and spec lens).

What it checks, on synthetic events only (no host, no network, no files written):
  1. The finding's mechanism: an owner HA-app change during Stage 0 step 5 (one bulb on
     at full for the "too dark" refusal) goes through den_lighting's manual(), which
     stores a hold AND a new normal, so the next arrival lights that bulb at full.
  2. The counter-argument: correction-capture's --events line for that change is what
     lets den-core's incumbent replay (tools/den-core/incumbent.py, which turns each
     'correction' event into manual()) follow live den_lighting. Dropping the line, as
     the finding's suggested fix would ("excluded like a probe session": neither counted
     nor written), makes the replay's next-arrival request diverge from live den_lighting.

Run (first arg = root of a checkout holding ha-config/custom_components/den_lighting/policy.py
and tools/den-core/incumbent.py, e.g. the claude/correction-capture worktree):

  python3 -B probe_stage0-bulb-change-counts-as-correction.py <checkout-root>

Prints PASS/FAIL/RESULT lines.
"""
import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path

root = Path(sys.argv[1]).resolve()
policy_path = root / "ha-config" / "custom_components" / "den_lighting" / "policy.py"
sys.path.insert(0, str(root / "tools" / "den-core"))

spec = importlib.util.spec_from_file_location("den_lighting_policy", policy_path)
den = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = den
spec.loader.exec_module(den)
import incumbent  # noqa: E402  den-core's replay of den_lighting

FL = "light.den_front_left"
USUAL = {"on": True, "brightness": 80, "kelvin": 2700}  # the den's usual normal: too dim for the sweep
FULL = {"on": True, "brightness": 255, "kelvin": 2700}  # the owner's "one more bulb at full in the HA app"

# One evening of Stage 0, then the next evening's arrival (seconds).
EVENTS = [
    *[{"t": 0.0, "kind": "light_observed", "data": {"entity_id": e, **USUAL}} for e in den.LIGHTS],
    {"t": 1.0, "kind": "occupancy", "data": {"value": "off"}},
    {"t": 200.0, "kind": "occupancy", "data": {"value": "on"}},      # owner walks in: arrival rule
    {"t": 300.0, "kind": "correction", "data": {"correction_id": "cc-300000", "entity_id": FL,
                                                "after": FULL, "source": "app"}},  # step 5 too-dark fix
    {"t": 400.0, "kind": "occupancy", "data": {"value": "off"}},     # owner steps out for the sweep
    {"t": 530.0, "kind": "tick", "data": {}},                        # 120 s later: vacancy OFF
    {"t": 90000.0, "kind": "occupancy", "data": {"value": "on"}},    # next evening's arrival
]


def live():
    """den_lighting as HA runs it: the owner's change reaches manual() via __init__.py."""
    c = den.Controller()
    c.set_enabled(True, 0.0)
    c.set_gaming_enabled(True, 0.0)
    last = {}
    for ev in EVENTS:
        t, d = ev["t"], ev["data"]
        if ev["kind"] == "light_observed":
            c.observe(d["entity_id"], den.Scene(d["on"], d["brightness"], d["kelvin"]), t)
        elif ev["kind"] == "occupancy":
            c.set_occupancy(d["value"], t)
        elif ev["kind"] == "correction":
            c.manual(d["entity_id"], den.Scene(**d["after"]), t)
        for cmd in c.reconcile(t):
            last[cmd.entity_id] = asdict(cmd.target)
            c.observed[cmd.entity_id] = cmd.target  # the bulb reports what it was sent
    return c, last


def replay(events):
    inc = incumbent.Incumbent(den, {"manifest_version": "1.0.4"})
    last = {}
    for ev in events:
        for out in inc.handle(ev):
            if out["kind"] == "decision":
                for e, scene in out["data"]["requested_scene"].items():
                    last[e] = scene
                    inc.controller.observed[e] = den.Scene(**scene)
    return inc.controller, last


ok = True
c_live, live_last = live()
print("RESULT live den_lighting after Stage 0: hold=%s normal[%s]=%s" % (
    FL in c_live.holds, FL, asdict(c_live.normal[FL])))
print("RESULT live next-arrival request for %s: %s" % (FL, live_last.get(FL)))
if live_last.get(FL, {}).get("brightness") == 255:
    print("PASS mechanism reproduced: the owner's instrument change became den_lighting's normal; "
          "the next arrival lights that bulb at full (README step 5 discloses this side effect)")
else:
    ok = False
    print("FAIL mechanism not reproduced")

c_a, a_last = replay(EVENTS)
c_b, b_last = replay([ev for ev in EVENTS if ev["kind"] != "correction"])
print("RESULT replay WITH correction-capture's --events line: next-arrival request %s" % a_last.get(FL))
print("RESULT replay WITHOUT it (suggested fix): next-arrival request %s" % b_last.get(FL))
if a_last.get(FL) == live_last.get(FL):
    print("PASS replay with the line matches live den_lighting")
else:
    ok = False
    print("FAIL replay with the line does not match live")
if b_last.get(FL) != live_last.get(FL):
    print("PASS dropping the line (the suggested fix) makes the incumbent replay diverge from live: "
          "%s vs live %s" % (b_last.get(FL), live_last.get(FL)))
else:
    print("FAIL dropping the line did not change the replay")
print("RESULT %s" % ("counter-argument holds: the --events line is needed for replay fidelity"
                     if ok else "probe could not establish the counter-argument"))
