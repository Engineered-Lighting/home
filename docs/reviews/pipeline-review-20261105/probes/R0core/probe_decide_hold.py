"""probe_decide_hold: decide.py's scene choice on constructed hold cases, against hand-computed expectations, and
what happens when a hold opens at the scene decide already requests for that bulb.

Part A (decide alone, events built by hand): normal scene 180@2700 on all four bulbs, occupancy on; a hold opens on
the rear-left bulb at 80@2202; A starts reading; the room empties; 120 s later the free bulbs go off while the held
one stays pinned; the incumbent's mode then drops the hold and the bulb goes off. Each step's requested scene,
skipped list and chosen candidate is compared with a hand expectation.

Part B (runner with need, incumbent, decide on the synthetic evening): person A touches the rear-left bulb at T0+600
(not a reading block) and sets it to the scene it already shows (180@2700), as a scene button or an app tap that
re-applies the current scene does. den_lighting opens a hold. The probe lists decide's decisions after the touch and
runs validate.py's report on the log.

How to run (synthetic data only; temp folders only; no network):
    python3 -B probe_decide_hold.py /path/to/den-core-checkout
"""
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import decide  # noqa: E402
import events  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
import validate  # noqa: E402

LIGHTS = events.LIGHTS
FL, FR, RL, RR = LIGHTS
LIT = {"on": True, "brightness": 180, "kelvin": 2700}
PIN = {"on": True, "brightness": 80, "kelvin": 2202}
LOOK = {"on": True, "brightness": 255, "kelvin": 2700}
OFF = {"on": False, "brightness": None, "kelvin": None}
T = 1793764800.0
bad = 0


def ev(kind, data, t):
    return {"schema": events.SCHEMA, "t": t, "seq": 0, "kind": kind, "source": "x", "data": data}


def mode(t, occupancy, holds, rev):
    return ev("mode", {"policy": "den_lighting@1.0.4", "revision": rev, "gaming": False, "occupancy": occupancy,
                       "holds": holds, "normal": {e: LIT for e in LIGHTS}}, t)


def person(t, state):
    return ev("person_state", {"state_id": "room@%.3f" % t, "focused_engagement": {"state": state, "kind": "reading"},
                               "field_support": {"focused_engagement": {"support_status": "observed"}}}, t)


hold = [{"entity_id": RL, "pinned": PIN, "since": T + 10, "lifetime": "room_vacant_2_minutes"}]
steps = [  # (event, expected requested scene or None for no decision, expected skipped channels, expected chosen)
    (mode(T, "unknown", [], 1), None, None, None),
    (ev("occupancy", {"value": "on", "via": "x"}, T + 1), {e: LIT for e in LIGHTS}, [], "base"),
    (mode(T + 10, "on", hold, 3), {FL: LIT, FR: LIT, RL: PIN, RR: LIT}, [RL], "base"),
    (person(T + 20, "engaged"), {FL: LOOK, FR: LOOK, RL: PIN, RR: LOOK}, [RL], "look:reading@0"),
    (mode(T + 100, "off", hold, 4), None, None, None),
    (ev("occupancy", {"value": "off", "via": "x"}, T + 100), None, None, None),
    (ev("tick", {}, T + 219.9), None, None, None),
    (ev("tick", {}, T + 220.0), {FL: OFF, FR: OFF, RL: PIN, RR: OFF}, [RL], "off"),
    (mode(T + 230, "off", [], 5), {e: OFF for e in LIGHTS}, [], "off"),
]
policy = decide.make_policy()
for n, (event, want, skipped, chosen) in enumerate(steps):
    out = policy.handle(event)
    got = out[0]["data"] if out else None
    if want is None:
        ok = got is None
        text = "no decision" if ok else "decision %s" % got["requested_scene"]
    else:
        pick = min(got["candidates"], key=lambda c: c["cost"])["scene_id"] if got else None
        ok = (got is not None and got["requested_scene"] == want and [s["channel"] for s in got["skipped"]] == skipped
              and pick == chosen)
        text = ("requested %s skipped %s chosen %s trigger %s" % (
            {validate.run.short(k): validate.scene(v) for k, v in got["requested_scene"].items()},
            [s["channel"] for s in got["skipped"]], pick, got["trigger"]) if got else "no decision")
    print("%s: A%d %s at T%+.1f -> %s" % ("PASS" if ok else "FAIL", n, event["kind"], event["t"] - T, text))
    bad += not ok

# Part B: a hold opened at the scene decide already requests for the bulb.
items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0)
items.append((synthetic.T0 + 600, "correction", {"correction_id": "probe-touch", "entity_id": RL, "after": LIT,
                                                  "source": "synthetic"}))
header, log = runner.run(runner.tagged(sorted(items, key=lambda i: i[0]), "synthetic"),
                         runner.load_policies(["need", "incumbent", "decide"]), synthetic=True)
held = [m for m in log if m["kind"] == "mode" and any(h["entity_id"] == RL for h in m["data"]["holds"])]
after = [e for e in log if e["kind"] == "decision" and e["source"] == "decide" and synthetic.T0 + 600 <= e["t"]
         < (held[-1]["t"] if held else synthetic.T0 + 600) + 1]
print("RESULT: B mode lists the rear-left hold from T0%+.1f to T0%+.1f" % (
    held[0]["t"] - synthetic.T0, held[-1]["t"] - synthetic.T0) if held else "RESULT: B no hold")
print("RESULT: B decide decisions while it is held: %s" % [
    (round(e["t"] - synthetic.T0, 1), e["data"]["trigger"], [s["channel"] for s in e["data"]["skipped"]],
     e["data"]["inputs"]["hold_set"]["held"] != []) for e in after])
first_with_hold = next((e for e in after if any(s["channel"] == RL for s in e["data"]["skipped"])), None)
print("RESULT: B first decide decision that records the hold: %s" % (
    "T0%+.1f" % (first_with_hold["t"] - synthetic.T0) if first_with_hold else "none"))
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "log.jsonl"
    events.write(path, header, log)
    lines, passed = validate.report(path)
for line in lines:
    print("  | " + line)
holds_line = next(line for line in lines if line.startswith("holds:"))
if holds_line.startswith("holds: FAIL"):
    bad += 1
    print("FAIL: B decide behaved as designed (one Decision per change of requested scene), but no Decision records "
          "the hold and validate.py fails check 4")
elif first_with_hold is None:
    bad += 1
    print("FAIL: B no decide decision records the hold")
else:
    print("PASS: B the hold is recorded and check 4 passes")
print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
sys.exit(1 if bad else 0)
