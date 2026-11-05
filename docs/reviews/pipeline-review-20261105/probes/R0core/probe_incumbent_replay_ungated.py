"""probe_incumbent_replay_ungated: rung 0's "den_lighting's incumbent Decisions replay too" is never a FAIL.

Builds invented, real-format copies (a Jetson journal of occupancy edges, a recorder export of the four
bulbs' states and of den_lighting_command events) for one ordinary evening: a person turns one bulb off
from the HA app while the room is occupied, leaves for 50 s and comes back. The live den_lighting holds
that bulb off (a manual hold, ending only after 120 s of vacancy), so it sends no command for it. The
replayed incumbent never forms a hold from real data (no adapter emits `correction`), so on the return it
turns the bulb back on: a command the live integration never sent.

The probe then runs run.py, check.py and validate.py exactly as the README tells the owner to and prints
their verdicts, the live/replayed command counts and the longest live-vs-replay interval. It also counts
how many logged decisions carry an `applied_scene` (the gate's second half). Offline, temp dirs only.

    python3 probe_incumbent_replay_ungated.py /path/to/den-core-checkout
"""
import json
import re
import subprocess
import sys
sys.dont_write_bytecode = True  # never leave __pycache__ in the checkout under test
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import sources  # noqa: E402  (only for the ULID helper, to write hex contexts den_lighting would report)

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
tmp = Path(tempfile.mkdtemp(prefix="probe-incumbent-"))


def edge(t, a, b):
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, t)


def ctx(n):
    return "%032X" % n, sources.ulid("%032X" % n)


def light_row(t, e, state, c="", user=""):
    return json.dumps({"last_updated_ts": t, "entity_id": e, "state": state, "context_id": c,
                       "context_user_id": user, "context_parent_id": ""})


def command(t, e, target, c):
    return json.dumps({"time_fired_ts": t, "event_data": {"entity_id": e, "revision": 9, "target": target,
                                                          "context_id": c}})


journal = [edge(T0 - 10, "unknown", "on"), edge(T0 + 100, "on", "off"), edge(T0 + 150, "off", "on"),
           edge(T0 + 600, "on", "off")]
lights = [light_row(T0 - 3600, e, "on", ctx(1 + n)[0]) for n, e in enumerate(LIGHTS)]
lights.append(light_row(T0 + 50, LIGHTS[2], "off", ctx(50)[0], user=ctx(51)[0]))  # a person, from the app
commands = []
for n, e in enumerate((LIGHTS[0], LIGHTS[1], LIGHTS[3])):  # live vacancy OFF; the held bulb is already off
    commands.append(command(T0 + 720.5, e, OFF, ctx(70 + n)[1]))
    lights.append(light_row(T0 + 720.6, e, "off", ctx(70 + n)[0]))
(tmp / "in").mkdir()
for name, rows in (("occupancy-edges.txt", journal), ("den-bulbs.jsonl", lights),
                   ("den-lighting-commands.jsonl", commands)):
    (tmp / "in" / name).write_text("\n".join(rows) + "\n", "ascii")

out = tmp / "real-1"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "in" / "occupancy-edges.txt"),
                    "--live-commands", str(tmp / "in" / "den-lighting-commands.jsonl"),
                    "--lights", str(tmp / "in" / "den-bulbs.jsonl"), "--policies", "need", "incumbent", "decide",
                    "--out", str(out)], cwd=str(root), capture_output=True, text=True)
print("RESULT: run.py exit %d" % r.returncode)
counts = re.search(r"commands live/replayed: (.*)", r.stdout)
print("RESULT: commands live/replayed: %s" % (counts.group(1) if counts else "?"))
spans = re.findall(r"^\s+(\S+)\s+([+-][\d.]+) s \.\.\s+([+-][\d.]+) s$", r.stdout.split("diff live den_lighting")[-1], re.M)
longest = max(spans, key=lambda s: float(s[2]) - float(s[1])) if spans else None
if longest:
    print("RESULT: longest live-vs-replay interval: %s %.1f s (%s .. %s)" % (
        longest[0], float(longest[2]) - float(longest[1]), longest[1], longest[2]))
log = [json.loads(line) for line in (out / "log.jsonl").read_text("ascii").splitlines()[1:]]
extra = [e for e in log if e["kind"] == "decision" and e["source"] == "incumbent"
         and LIGHTS[2] in e["data"]["requested_scene"] and e["data"]["requested_scene"][LIGHTS[2]]["on"]]
print("RESULT: replayed incumbent ON commands for the person-held bulb: %s"
      % ["%+.1f s" % (e["t"] - T0) for e in extra])
print("RESULT: decisions with applied_scene: %d of %d" % (
    sum("applied_scene" in e["data"] for e in log if e["kind"] == "decision"),
    sum(e["kind"] == "decision" for e in log)))
c = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
print("RESULT: check.py exit %d: %s" % (c.returncode, c.stdout.strip()))
print("RESULT: validate.py exit %d: %s" % (v.returncode, v.stdout.strip().splitlines()[-1]))
held_spans = [sp for sp in spans if sp[0] == "rear_left"]
print("RESULT: live-vs-replay intervals listed for the person-held bulb (rear_left): %d" % len(held_spans))
if extra and c.returncode == 0 and v.returncode == 0:
    print("FAIL: the replayed incumbent sent a command the live den_lighting never sent (rear_left ON at +150 s; "
          "live/replayed 0/1), yet check.py and validate.py both pass (exit 0)%s" % (
              "" if held_spans else ", and the live-vs-replay diff lists no interval at all for that bulb"))
else:
    print("PASS: a live/replay divergence of the incumbent is reported as a failure")
