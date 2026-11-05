"""probe_journal_unknown_arrival: a journal 'from=unknown to=on' after a logged off is replayed as an arrival.

The occupancy reader logs no edge into unknown; a dropout that starts while the den is vacant and ends with
a person back logs only 'from=unknown to=on'. sources.occupancy_journal maps it to a plain 'on', so the
replayed den_lighting goes off -> on (an arrival after >= 120 s: last-level ON, which also turns on a bulb
whose normal scene is off), while a den_lighting that saw the dropout went unknown -> on (no arrival).
run.py's gap report covers only spans that start at an 'on', so it prints no warning here. Offline, temp only.

    python3 probe_journal_unknown_arrival.py /path/to/den-core-checkout
"""
import json
import subprocess
import sys
sys.dont_write_bytecode = True  # never leave __pycache__ in the checkout under test
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
sys.path.insert(0, str(tool))
import incumbent  # noqa: E402

edges = ["occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, T0 + t)
         for t, a, b in ((-10, "unknown", "on"), (100, "on", "off"), (1000, "unknown", "on"))]
lights = [json.dumps({"last_updated_ts": T0 - 3600, "entity_id": e, "state": "off" if e == LIGHTS[2] else "on",
                      "context_id": "%032X" % (n + 1), "context_user_id": "", "context_parent_id": ""})
          for n, e in enumerate(LIGHTS)]
tmp = Path(tempfile.mkdtemp(prefix="probe-unknown-"))
(tmp / "edges.txt").write_text("\n".join(edges) + "\n", "ascii")
(tmp / "bulbs.jsonl").write_text("\n".join(lights) + "\n", "ascii")
(tmp / "commands.jsonl").write_text("", "ascii")
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "edges.txt"), "--live-commands",
                    str(tmp / "commands.jsonl"), "--lights", str(tmp / "bulbs.jsonl"), "--policies", "incumbent",
                    "--out", str(tmp / "out")], cwd=str(root), capture_output=True, text=True)
assert r.returncode == 0, r.stderr
log = [json.loads(x) for x in (tmp / "out" / "log.jsonl").read_text("ascii").splitlines()[1:]]
ons = [(e["t"] - T0, sorted(b.split(".")[1] for b, s in e["data"]["requested_scene"].items() if s["on"]))
       for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]
print("RESULT: replayed incumbent decisions with ONs: %s" % ons)
print("RESULT: run.py warned about the unknown->on span: %s" % ("dropout" in r.stdout or "never logged" in r.stdout))
# What den_lighting does if it saw the dropout: off -> unknown -> on, same bulbs and times.
den = incumbent.load_policy(incumbent.find_policy())
c = den.Controller()
c.set_enabled(True, T0 - 10)
for e in LIGHTS:
    c.observe(e, den.Scene(e != LIGHTS[2]), T0 - 10)
c.set_occupancy("on", T0 - 10); c.reconcile(T0 - 10)
c.set_occupancy("off", T0 + 100); c.reconcile(T0 + 220)
c.set_occupancy("unknown", T0 + 500); c.reconcile(T0 + 500)
c.set_occupancy("on", T0 + 1000)
print("RESULT: den_lighting through unknown -> on commands ON for: %s" % sorted(
    cmd.entity_id.split(".")[1] for cmd in c.reconcile(T0 + 1000) if cmd.target.on))
rear = [b for _, bulbs in ons for b in bulbs if b == "den_rear_left"]
print("FAIL: the replay turns on den_rear_left (normal off) as an arrival, with no warning" if rear and
      "dropout" not in r.stdout else "PASS")
