"""verify_decide-drops-hold-decision (REPRODUCE lens, skeptical verifier).

Checks, independently of the reporter's probe and of synthetic.evening, whether decide.py logs no Decision when a
hold opens at the scene decide already requests for that bulb, and whether validate.py check 4 then fails the run.

Minimal invented inputs (no real data): the four bulbs observed at 180@2700 and occupancy on at t0; at t0+30 a
correction sets the rear-left bulb to the scene it already shows (180@2700). den_lighting's policy.manual() opens a
hold whatever the scene (live, its HA runtime does the same for any app/voice/scene service call, and for a
scene.turn_on it first pins the bulb's *current* scene). A second variant uses a correction to a different scene
(80@2202) as a control: there decide must log a 'hold change' Decision and check 4 must pass.

How to run (synthetic data only; temp folders only; no network):
    python3 -B verify_decide-drops-hold-decision.py /path/to/den-core-checkout
"""
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))
import events  # noqa: E402
import runner  # noqa: E402
import validate  # noqa: E402

T0 = 1793764800.0
LIGHTS = events.LIGHTS
RL = LIGHTS[2]
S = {"on": True, "brightness": 180, "kelvin": 2700}
OTHER = {"on": True, "brightness": 80, "kelvin": 2202}


def case(after):
    items = [(T0, "light_observed", {"entity_id": e, **S, "context": "unknown"}) for e in LIGHTS]
    items.append((T0, "occupancy", {"value": "on", "via": "synthetic"}))
    items.append((T0 + 30, "correction", {"correction_id": "v1", "entity_id": RL, "after": after, "source": "t"}))
    items.append((T0 + 60, "occupancy", {"value": "on", "via": "synthetic"}))  # keeps the span going
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(["incumbent", "decide"]),
                             synthetic=True)
    held = [m["t"] - T0 for m in log if m["kind"] == "mode" and any(h["entity_id"] == RL for h in m["data"]["holds"])]
    dec = [(round(e["t"] - T0, 1), e["data"]["trigger"], [s["channel"] for s in e["data"]["skipped"]],
            e["data"]["inputs"]["hold_set"]["revision"]) for e in log if e["kind"] == "decision" and e["source"] == "decide"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.jsonl"
        events.write(path, header, log)
        lines, passed = validate.report(path)
    return held, dec, lines, passed


bad = 0
for label, after in (("same scene (finding)", S), ("different scene (control)", OTHER)):
    held, dec, lines, passed = case(after)
    holds_line = next(line for line in lines if line.startswith("holds:"))
    print("RESULT %s: mode lists the hold from t0+%s; decide decisions (t, trigger, skipped, hold_set rev): %s"
          % (label, held[0] if held else None, dec))
    print("RESULT %s: %s" % (label, holds_line))
    print("RESULT %s: overall passed=%s" % (label, passed))
    if after is S:
        recorded = any(RL in skipped for _, _, skipped, _ in dec)
        if not recorded and holds_line.startswith("holds: FAIL"):
            print("FAIL: same-scene hold: no decide Decision lists the held bulb in skipped and check 4 fails the run")
            bad += 1
        else:
            print("PASS: same-scene hold recorded or check 4 passes")
    else:
        if holds_line.startswith("holds: OK") and any(t == 30.0 and trig == "hold change" for t, trig, _, _ in dec):
            print("PASS: control: a hold at a new scene is logged as 'hold change' and check 4 passes")
        else:
            print("FAIL: control did not behave as expected")
            bad += 1
print("RESULT: %s" % ("defect reproduced" if bad == 1 else "unexpected (%d)" % bad))
