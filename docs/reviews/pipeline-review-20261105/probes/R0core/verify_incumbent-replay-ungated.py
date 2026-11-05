"""verify_incumbent-replay-ungated: does anything in den-core FAIL when the live den_lighting's commands
contradict the replayed incumbent?

Independent of the reporter's probe. Invented, real-format copies: a journal with the den occupied the whole
time, the four bulbs recorded ON (no person changes at all), and a den_lighting_command export in which the
live den_lighting turns every bulb OFF at +60 s and leaves it off, which the replay never does (the room
stays occupied, so den_lighting's replay keeps them ON). The live bulb states are left out on purpose so
the replay cannot "find" anything. Then run.py, check.py and validate.py are run as the README tells the
owner. If both exit 0 while live_diff itself reports a long interval, nothing gates the live comparison.

    python3 verify_incumbent-replay-ungated.py /path/to/den-core-checkout

Offline: no host is contacted. Temp dirs only. Prints RESULT lines and a final FAIL (defect present) or PASS.
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
import sources  # noqa: E402  (ULID helper only)

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
tmp = Path(tempfile.mkdtemp(prefix="verify-ungated-"))
(tmp / "in").mkdir()


def edge(t, a, b):
    return "occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, t)


# Occupied from well before the span: an off/on pair 200 s apart so the replay sees a real arrival.
journal = [edge(T0 - 400, "unknown", "on"), edge(T0 - 300, "on", "off"), edge(T0 - 100, "off", "on"),
           edge(T0 + 3000, "on", "off")]
lights = [json.dumps({"last_updated_ts": T0 - 3600, "entity_id": e, "state": "on", "context_id": "%032X" % (n + 1),
                      "context_user_id": "", "context_parent_id": ""}) for n, e in enumerate(LIGHTS)]
commands = [json.dumps({"time_fired_ts": T0 + 60.0, "event_data": {
    "entity_id": e, "revision": 3, "target": {"on": False, "brightness": None, "kelvin": None},
    "context_id": sources.ulid("%032X" % (100 + n))}}) for n, e in enumerate(LIGHTS)]
for name, rows in (("edges.txt", journal), ("bulbs.jsonl", lights), ("commands.jsonl", commands)):
    (tmp / "in" / name).write_text("\n".join(rows) + "\n", "ascii")

out = tmp / "r"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "in" / "edges.txt"),
                    "--live-commands", str(tmp / "in" / "commands.jsonl"), "--lights", str(tmp / "in" / "bulbs.jsonl"),
                    "--policies", "need", "incumbent", "decide", "--out", str(out)],
                   cwd=str(root), capture_output=True, text=True)
print("RESULT: run.py exit %d" % r.returncode)
if r.returncode:
    print(r.stderr)
    sys.exit(2)
for line in r.stdout.splitlines():
    if line.startswith("diff live") or line.startswith("  ") and " s .. " in line:
        print("RESULT: run.py | " + line)

import events  # noqa: E402
import run  # noqa: E402
header, log = events.read(out / "log.jsonl")
spans = run.live_diff(header, log)
longest = max((s["end"] - s["start"] for s in spans), default=0.0)
print("RESULT: run.live_diff spans %d, longest %.1f s" % (len(spans), longest))
c = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(out / "log.jsonl")], cwd=str(root),
                   capture_output=True, text=True)
print("RESULT: check.py exit %d: %s" % (c.returncode, c.stdout.strip()))
print("RESULT: validate.py exit %d:\n  %s" % (v.returncode, v.stdout.strip().replace("\n", "\n  ")))
src = (tool / "validate.py").read_text("utf-8") + (tool / "check.py").read_text("utf-8")
print("RESULT: check.py/validate.py mention live_command or live_diff: %s"
      % any(w in src for w in ("live_command", "live_diff", "REFERENCE_KINDS")))
if longest > 600 and c.returncode == 0 and v.returncode == 0:
    print("FAIL: live den_lighting disagrees with the replayed incumbent for %.0f s on every bulb, and check.py "
          "and validate.py both exit 0: the live-vs-replay comparison is never a gate" % longest)
else:
    print("PASS: a live/replay disagreement makes check.py or validate.py fail")
