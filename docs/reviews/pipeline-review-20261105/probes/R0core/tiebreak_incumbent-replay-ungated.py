"""tiebreak_incumbent-replay-ungated: can check.py's or validate.py's verdict depend on the live den_lighting
side at all?

The ladder's rung 0 gate ends "den_lighting's incumbent Decisions replay too", and rung 0's tested list makes
those Decisions den_lighting's live commands (actuation = shown). den-core logs the live commands only as
`live_command` reference lines. This probe makes one real-format run (invented journal, bulb states and a
den_lighting_command export in which the live and replayed sides agree), then writes a second copy of the same
log in which every live_command's target is inverted (ON <-> OFF), re-serialised canonically. The live/replay
comparison (run.live_diff) changes from agreeing to disagreeing, and then check.py and validate.py run on both
logs. It also counts decisions whose actuation is `shown`. If both verdicts are identical and exit 0 for both
logs, then nothing in den-core's checks depends on whether the live den_lighting's decisions replay.

    python3 -B tiebreak_incumbent-replay-ungated.py /path/to/den-core-checkout

Offline. Temp dirs only. Nothing is written in the checkout. Prints RESULT lines, then FAIL (the verdicts ignore
the live side) or PASS.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import events  # noqa: E402
import run  # noqa: E402
import sources  # noqa: E402

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
OFF = {"on": False, "brightness": None, "kelvin": None}
tmp = Path(tempfile.mkdtemp(prefix="tiebreak-ungated-"))
(tmp / "in").mkdir()


def edge(t, a, b):
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, t)


# Occupied from the start (unknown -> on: no arrival ON), the room empties at +600 s, and the occupancy entity
# goes unavailable at +3000 s (so the replayed span runs to +3150 s and nobody decides after the vacancy OFF).
# The live den_lighting sends the vacancy OFF to all four bulbs at +720.5 s; the replay sends the same OFF on its
# 2 s tick at +720 s. So the two sides agree in the original export. No bulb rows are recorded after the
# commands, so live_diff cannot "find" a bulb at either side's target.
journal = [edge(T0 - 10, "unknown", "on"), edge(T0 + 600, "on", "off"), edge(T0 + 3000, "off", "unavailable")]
lights = [json.dumps({"last_updated_ts": T0 - 3600, "entity_id": e, "state": "on", "context_id": "%032X" % (n + 1),
                      "context_user_id": "", "context_parent_id": ""}) for n, e in enumerate(LIGHTS)]
commands = [json.dumps({"time_fired_ts": T0 + 720.5, "event_data": {
    "entity_id": e, "revision": 4, "target": OFF, "context_id": sources.ulid("%032X" % (200 + n))}})
    for n, e in enumerate(LIGHTS)]
for name, rows in (("edges.txt", journal), ("bulbs.jsonl", lights), ("commands.jsonl", commands)):
    (tmp / "in" / name).write_text("\n".join(rows) + "\n", "ascii")

out = tmp / "r"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "in" / "edges.txt"),
                    "--live-commands", str(tmp / "in" / "commands.jsonl"), "--lights", str(tmp / "in" / "bulbs.jsonl"),
                    "--policies", "need", "incumbent", "decide", "--out", str(out)],
                   cwd=str(root), capture_output=True, text=True)
print("RESULT: run.py exit %d" % r.returncode)
if r.returncode:
    print(r.stdout, r.stderr)
    sys.exit(2)
original = out / "log.jsonl"

# The same log, with every live_command target inverted. Everything else is byte-identical.
lines = original.read_bytes().decode("ascii").split("\n")[:-1]
flipped_lines, flipped = [lines[0]], 0
for line in lines[1:]:
    obj = json.loads(line)
    if obj["kind"] == "live_command":
        on = not obj["data"]["target"]["on"]
        obj["data"]["target"] = {"on": on, "brightness": 200 if on else None, "kelvin": 2700 if on else None}
        flipped += 1
        line = events.dumps(obj)
    flipped_lines.append(line)
mutant = tmp / "flipped" / "log.jsonl"
mutant.parent.mkdir()
mutant.write_bytes(("\n".join(flipped_lines) + "\n").encode("ascii"))
print("RESULT: live_command lines inverted in the copy: %d" % flipped)

verdicts = {}
for label, path in (("original", original), ("live inverted", mutant)):
    header, log = events.read(path)
    spans = run.live_diff(header, log)
    longest = max((s["end"] - s["start"] for s in spans), default=0.0)
    shown = sum(1 for e in log if e["kind"] == "decision" and e["data"].get("actuation") == "shown")
    actuations = sorted({e["data"].get("actuation") for e in log if e["kind"] == "decision"})
    c = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(path)], cwd=str(root),
                       capture_output=True, text=True)
    v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(path)], cwd=str(root),
                       capture_output=True, text=True)
    verdicts[label] = (c.returncode, v.returncode, round(sum(sp["end"] - sp["start"] for sp in spans), 1))
    print("RESULT: [%s] live_diff spans %d, longest %.1f s, total %.1f s; decisions with actuation=shown: %d "
          "(actuations seen: %s)" % (label, len(spans), longest, verdicts[label][2], shown, actuations))
    print("RESULT: [%s] check.py exit %d: %s" % (label, c.returncode, c.stdout.strip()))
    print("RESULT: [%s] validate.py exit %d: %s" % (label, v.returncode, v.stdout.strip().splitlines()[-1]))

o, m = verdicts["original"], verdicts["live inverted"]
if o[2] != m[2] and o[:2] == m[:2] == (0, 0):
    print("FAIL: inverting every live den_lighting command changes the live/replay comparison (%.1f s -> %.1f s of "
          "disagreement summed over bulbs) but "
          "neither check.py nor validate.py changes its verdict (both exit 0 either way): no den-core check "
          "depends on whether the live den_lighting's decisions replay" % (o[2], m[2]))
elif o[2] == m[2]:
    print("RESULT: inconclusive: the inversion did not change the live/replay comparison")
else:
    print("PASS: a live/replay disagreement changes a den-core verdict")
