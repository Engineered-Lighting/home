"""verify_live-diff-blind-to-person-change: call run.live_diff directly on a log in which a person turns one
bulb off from the app (recorded with a user context) and the replayed incumbent later turns it back on, while
the live den_lighting (which holds the bulb) sends nothing for it.

Independent of the reporter's probe: it builds the den-event-1 inputs in memory, runs runner.run with the
incumbent only and the plant off (as run.py --lights does), then asks run.live_diff for intervals. It also
runs a control in which the live den_lighting DID send the same ON, to show live_diff is otherwise able to
see that bulb.

    python3 verify_live-diff-blind-to-person-change.py /path/to/den-core-checkout

Offline, in memory, no files written. Prints RESULT lines and FAIL (defect present) or PASS.
"""
import sys

sys.dont_write_bytecode = True
root = sys.argv[1]
sys.path.insert(0, root + "/tools/den-core")
import run  # noqa: E402
import runner  # noqa: E402

T0 = 1793764800.0
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
HELD = LIGHTS[2]
OFF = {"on": False, "brightness": None, "kelvin": None}


def lo(t, e, on, context):
    return (t, "light_observed", {"entity_id": e, "on": on, "brightness": None, "kelvin": None, "context": context})


def build(live_sends_on):
    lights = [lo(T0 - 400, e, True, "unknown") for e in LIGHTS] + [lo(T0 + 50, HELD, False, "person")]
    occ = [(T0 - 400, "occupancy", {"value": "on", "via": "jetson_journal"}),
           (T0 + 100, "occupancy", {"value": "off", "via": "jetson_journal"}),
           (T0 + 150, "occupancy", {"value": "on", "via": "jetson_journal"}),
           (T0 + 600, "occupancy", {"value": "off", "via": "jetson_journal"})]
    live = []
    for n, e in enumerate((LIGHTS[0], LIGHTS[1], LIGHTS[3])):
        live.append((T0 + 720.5, "live_command", {"entity_id": e, "revision": 9, "target": OFF, "context_id": "c%d" % n}))
    if live_sends_on:
        live.append((T0 + 150.5, "live_command", {"entity_id": HELD, "revision": 8,
                                                  "target": {"on": True, "brightness": None, "kelvin": None},
                                                  "context_id": "c-on"}))
        live.append((T0 + 720.5, "live_command", {"entity_id": HELD, "revision": 9, "target": OFF, "context_id": "c-off"}))
    inputs = (runner.tagged(lights, "recorder_lights") + runner.tagged(occ, "occupancy_journal")
              + runner.tagged(live, "recorder_commands"))
    return runner.run(inputs, runner.load_policies(["incumbent"]), plant=False)


header, log = build(live_sends_on=False)
replay_on = ["%+.1f" % (e["t"] - T0) for e in log if e["kind"] == "decision" and e["source"] == "incumbent"
             and HELD in e["data"]["requested_scene"] and e["data"]["requested_scene"][HELD]["on"]]
replay_off = ["%+.1f" % (e["t"] - T0) for e in log if e["kind"] == "decision" and e["source"] == "incumbent"
              and HELD in e["data"]["requested_scene"] and not e["data"]["requested_scene"][HELD]["on"]]
spans = run.live_diff(header, log)
held_spans = [s for s in spans if s["entity_id"] == HELD]
print("RESULT: replayed incumbent ON for %s at %s s, OFF at %s s; live sent nothing for it" % (HELD, replay_on, replay_off))
print("RESULT: live_diff spans: %s" % [(s["entity_id"].split(".")[1], round(s["start"] - T0, 1), round(s["end"] - T0, 1))
                                       for s in spans])
print("RESULT: live_diff spans for %s: %d" % (HELD, len(held_spans)))
h2, l2 = build(live_sends_on=True)
print("RESULT: control (live also sent that ON at +150.5 s): spans for %s: %s" % (
    HELD, [(round(s["start"] - T0, 1), round(s["end"] - T0, 1)) for s in run.live_diff(h2, l2) if s["entity_id"] == HELD]))
if replay_on and not held_spans:
    print("FAIL: the replay turns %s back on and keeps it on until the vacancy OFF, the live den_lighting never "
          "does, and live_diff lists no interval for that bulb" % HELD)
else:
    print("PASS: live_diff shows the person-held bulb's divergence")
