"""probe_live_incumbent_not_gated: the ladder's rung-0 gate says den_lighting's incumbent Decisions replay too. In
den-core the live den_lighting's commands enter only as `live_command` reference lines; the replayed incumbent is
den_lighting's policy.py alone. This probe builds a small invented real-data-shaped replay (journal edges, live
den_lighting_command export rows and recorded bulb states, all made up, in a temp folder) in which person A switches
one bulb on during the vacancy countdown, so the live den_lighting holds it (its HA runtime turns an unexplained bulb
change into a hold) and turns it off 120 s after the touch, while the replayed policy.py turns it off 120 s after the
vacancy started. It then runs run.py, check.py and validate.py.

Expected if the gate were enforced: some check fails while the replayed incumbent disagrees with the live one.
Observed (RESULT lines): run.py prints the disagreement interval(s), but check.py prints OK and validate.py prints
overall OK, both exit 0.

How to run (synthetic files only; temp folders only; no network):
    python3 -B probe_live_incumbent_not_gated.py /path/to/den-core-checkout
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
TOOL = ROOT / "tools" / "den-core"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
DEN = "binary_sensor.den_oak_person_occupancy"
T0 = 1793764800.0
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
OFF = {"on": False, "brightness": None, "kelvin": None}
LIT = {"on": True, "brightness": None, "kelvin": None}


def ulid(n):
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def edge(t, a, b):
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, t)


def command(t, entity, target, n):
    return json.dumps({"time_fired_ts": t, "event_data": {"entity_id": entity, "revision": 7, "target": target,
                                                          "context_id": ulid(n)}})


def row(t, entity, state, n, user=0):
    return json.dumps({"last_updated_ts": t, "entity_id": entity, "state": state, "context_id": "%032X" % n,
                       "context_user_id": "%032X" % user if user else "", "context_parent_id": ""})


def run(*argv):
    p = subprocess.run([sys.executable, "-B", *map(str, argv)], cwd=str(ROOT), capture_output=True, text=True,
                       env=ENV)
    return p.returncode, p.stdout + p.stderr


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    # Occupied from T0-5, empty from T0+20, A comes back at T0+400.
    (tmp / "edges.txt").write_text("\n".join([edge(T0 - 5, "unknown", "on"), edge(T0 + 20, "on", "off"),
                                              edge(T0 + 400, "off", "on")]) + "\n", "ascii")
    cmds = [command(T0 - 3600, e, LIT, 100 + i) for i, e in enumerate(LIGHTS) if e != LIGHTS[2]]
    rows = [row(T0 - 3600, e, "on", 100 + i) for i, e in enumerate(LIGHTS) if e != LIGHTS[2]]
    rows.append(row(T0 - 3600, LIGHTS[2], "off", 99))
    rows.append(row(T0 + 100, LIGHTS[2], "on", 200, user=201))  # A switches the rear-left bulb on in the countdown
    for i, e in enumerate(LIGHTS):  # live: vacancy OFF at the tick after T0+140 for the free bulbs
        if e != LIGHTS[2]:
            cmds.append(command(T0 + 141.5, e, OFF, 300 + i))
            rows.append(row(T0 + 141.6, e, "off", 300 + i))
    cmds.append(command(T0 + 221.5, LIGHTS[2], OFF, 400))  # live: the held bulb 120 s after the touch
    rows.append(row(T0 + 221.6, LIGHTS[2], "off", 400))
    for i, e in enumerate(LIGHTS):  # live: last-level ON on arrival
        cmds.append(command(T0 + 400.5, e, LIT, 500 + i))
    (tmp / "commands.jsonl").write_text("\n".join(cmds) + "\n", "ascii")
    (tmp / "lights.jsonl").write_text("\n".join(rows) + "\n", "ascii")
    out = tmp / "replay"
    code, text = run(TOOL / "run.py", "--journal", tmp / "edges.txt", "--live-commands", tmp / "commands.jsonl",
                     "--lights", tmp / "lights.jsonl", "--policies", "need", "incumbent", "decide", "--out", out)
    print("run.py exit %d" % code)
    print("\n".join("  | " + line for line in text.splitlines()))
    m = re.search(r"diff live den_lighting vs incumbent replay \(a null level matches any\): (\d+) interval", text)
    intervals = int(m.group(1)) if m else -1
    long_spans = [line for line in text.splitlines() if re.match(r"\s+rear_left\s+[+-]", line)]
    code_c, text_c = run(TOOL / "check.py", out / "log.jsonl")
    code_v, text_v = run(TOOL / "validate.py", out / "log.jsonl")
    print("check.py exit %d: %s" % (code_c, text_c.strip()))
    print("validate.py exit %d:" % code_v)
    print("\n".join("  | " + line for line in text_v.splitlines()))
    # Independent comparison: when did each side command the rear-left bulb (A's bulb)?
    sys.path.insert(0, str(TOOL))
    import events  # noqa: E402
    import run as den_run  # noqa: E402
    header, log = events.read(out / "log.jsonl")
    first = min(e["t"] for e in log)
    replayed = [(round(e["t"] - T0, 1), e["data"]["requested_scene"][LIGHTS[2]]["on"]) for e in log
                if e["kind"] == "decision" and e["source"] == "incumbent" and LIGHTS[2] in e["data"]["requested_scene"]]
    live = [(round(e["t"] - T0, 1), e["data"]["target"]["on"]) for e in log
            if e["kind"] == "live_command" and e["data"]["entity_id"] == LIGHTS[2]]
    spans = [(round(s["start"] - T0, 1), round(s["end"] - T0, 1)) for s in den_run.live_diff(header, log)
             if s["entity_id"] == LIGHTS[2]]
    print("RESULT: rear-left commands (s from T0, on): replayed=%s live=%s" % (replayed, live))
    print("RESULT: run.live_diff spans for rear-left (s from T0): %s" % spans)
    print("RESULT: live-vs-replay disagreement intervals=%d (all bulbs)" % intervals)
    print("RESULT: check.py exit=%d validate.py exit=%d" % (code_c, code_v))
    bad = 0
    early = [t for t, on in replayed if not on and t < 200] and [t for t, on in live if not on and t > 200]
    if early and not any(a <= 150 and b >= 200 for a, b in spans):
        print("FAIL: the replay turns A's bulb off at %s s, the live den_lighting only at %s s, yet run.live_diff "
              "reports no interval covering that gap" % ([t for t, on in replayed if not on], [t for t, on in live
                                                                                              if not on]))
        bad += 1
    if code_c == 0 and code_v == 0:
        print("FAIL: check.py and validate.py both pass although the replayed incumbent's commands are not the live "
              "den_lighting's (no check compares them)")
        bad += 1
    print("RESULT: %s" % ("FAIL (%d)" % bad if bad else "PASS"))
    sys.exit(1 if bad else 0)
