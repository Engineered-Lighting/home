"""probe_real_game_invisible: a Steam game in a real-data replay is invisible to every den-core check.

Invented, real-format copies of an evening in which the live den_lighting ran its gaming scene (38/255 at
each bulb's warmest white, as its den_lighting_command events show) from +300 s to +1500 s, with the person
stepping out for 300 s mid-game. No den-core adapter emits `gaming_sample` from real files, so the replayed
incumbent never games: decide (whose option is 'non-gaming-stratified: no decisions while mode.gaming')
decides inside the live game, `diff` ("gaming left out") leaves nothing out, validate's quiet check reports
0.0 s of game, and live_diff's null-level rule matches the live 38/255 commands against the replay's levelless
ONs. Offline, temp dirs only.

    python3 probe_real_game_invisible.py /path/to/den-core-checkout
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
import sources  # noqa: E402

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
WARMEST = (1000, 1000, 2202, 2202)
GAME, END = (300.0, 1500.0)
tmp = Path(tempfile.mkdtemp(prefix="probe-game-"))
n = [100]


def ctx():
    n[0] += 1
    return "%032X" % n[0], sources.ulid("%032X" % n[0])


def edge(t, a, b):
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, t)


journal = [edge(T0 - 10, "unknown", "on"), edge(T0 + 600, "on", "off"), edge(T0 + 900, "off", "on"),
           edge(T0 + 2000, "on", "off")]
lights, commands = [], []
for e in LIGHTS:
    h, _ = ctx()
    lights.append(json.dumps({"last_updated_ts": T0 - 3600, "entity_id": e, "state": "on", "context_id": h,
                              "context_user_id": "", "context_parent_id": ""}))


def live(t, e, target):
    h, u = ctx()
    commands.append(json.dumps({"time_fired_ts": T0 + t, "event_data": {"entity_id": e, "revision": 5,
                                                                        "target": target, "context_id": u}}))
    lights.append(json.dumps({"last_updated_ts": T0 + t + 0.1, "entity_id": e,
                              "state": "on" if target["on"] else "off", "context_id": h, "context_user_id": "",
                              "context_parent_id": ""}))


for e, k in zip(LIGHTS, WARMEST):
    live(GAME, e, {"on": True, "brightness": 38, "kelvin": k})        # game starts: gaming scene
    live(720.5, e, {"on": False, "brightness": None, "kelvin": None})  # vacancy OFF mid-game
    live(900.5, e, {"on": True, "brightness": 38, "kelvin": k})       # back: gaming scene again
    live(END, e, {"on": True, "brightness": 180, "kelvin": 2700})     # game ends: pre-game snapshot
    live(2120.5, e, {"on": False, "brightness": None, "kelvin": None})
(tmp / "in").mkdir()
for name, rows in (("edges.txt", journal), ("bulbs.jsonl", sorted(lights, key=lambda r: json.loads(r)["last_updated_ts"])),
                   ("commands.jsonl", commands)):
    (tmp / "in" / name).write_text("\n".join(rows) + "\n", "ascii")
out = tmp / "real-game"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "in" / "edges.txt"),
                    "--live-commands", str(tmp / "in" / "commands.jsonl"), "--lights", str(tmp / "in" / "bulbs.jsonl"),
                    "--policies", "need", "incumbent", "decide", "--out", str(out)], cwd=str(root),
                   capture_output=True, text=True)
assert r.returncode == 0, r.stderr
log = [json.loads(line) for line in (out / "log.jsonl").read_text("ascii").splitlines()[1:]]
first = log[0]["t"]
gaming_modes = sum(1 for e in log if e["kind"] == "mode" and e["data"]["gaming"])
in_game = [e for e in log if e["kind"] == "decision" and e["source"] == "decide" and T0 + GAME <= e["t"] < T0 + END]
print("RESULT: replayed modes with gaming=true: %d" % gaming_modes)
print("RESULT: decide decisions inside the live game: %s" % ["%+.1f s %s" % (e["t"] - T0, e["data"]["why"].split(":")[0])
                                                          for e in in_game])
spans = re.findall(r"^\s+(\S+)\s+([+-][\d.]+) s \.\.\s+([+-][\d.]+) s$", r.stdout.split("diff live den_lighting")[-1], re.M)
print("RESULT: live-vs-replay intervals: %d, longest %.1f s" % (
    len(spans), max((float(b) - float(a) for _, a, b in spans), default=0.0)))
v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
quiet = next(line for line in v.stdout.splitlines() if line.startswith("quiet"))
print("RESULT: validate.py exit %d; %s" % (v.returncode, quiet))
if gaming_modes == 0 and in_game and v.returncode == 0:
    print("FAIL: a 1200 s live game is invisible: the replay never games, decide decides inside it, and "
          "validate passes with '0.0 s of game'")
else:
    print("PASS")
