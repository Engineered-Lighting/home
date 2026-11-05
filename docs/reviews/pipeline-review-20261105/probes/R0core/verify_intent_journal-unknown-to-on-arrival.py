"""verify_intent_journal-unknown-to-on-arrival: is the replayed arrival after 'off ... from=unknown to=on'
silent, and is it wrong?

Checks three things on a den-core checkout, offline, synthetic, temp dirs only:
  1. run.py's journal gap report prints nothing for a 'from=unknown to=on' line that follows a logged off
     (the reported defect), while it does print for the same line after an on (the documented case).
  2. The Jetson reader's two dropout causes give den_lighting different inputs: a stale Jetson poll leaves
     den_lighting's MQTT input off -> on (an arrival, the replay is right); an HA entity that went
     unavailable gives den_lighting off -> unknown -> on (no arrival, the replay is wrong).
  3. Whether the live-vs-replay diff (live_diff) shows the divergence when live commands are given.

    python3 verify_intent_journal-unknown-to-on-arrival.py /path/to/den-core-checkout
"""
import json
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import incumbent  # noqa: E402

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")


def run(edges, commands=(), extra_lights=()):
    tmp = Path(tempfile.mkdtemp(prefix="verify-unknown-"))
    (tmp / "edges.txt").write_text("".join("occupancy edge: entity=%s from=%s to=%s observed_at=%.3f\n"
                                           % (DEN, a, b, T0 + t) for t, a, b in edges), "ascii")
    rows = [{"last_updated_ts": T0 - 3600, "entity_id": e, "state": "off" if e == LIGHTS[2] else "on",
             "context_id": "%032X" % (n + 1), "context_user_id": "", "context_parent_id": ""}
            for n, e in enumerate(LIGHTS)] + list(extra_lights)
    (tmp / "bulbs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "ascii")
    (tmp / "commands.jsonl").write_text("".join(json.dumps(c) + "\n" for c in commands), "ascii")
    r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "edges.txt"),
                        "--live-commands", str(tmp / "commands.jsonl"), "--lights", str(tmp / "bulbs.jsonl"),
                        "--policies", "incumbent", "--out", str(tmp / "out")], cwd=str(tmp), capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


after_off = run([(-10, "unknown", "on"), (100, "on", "off"), (1000, "unknown", "on")])
after_on = run([(-10, "unknown", "on"), (100, "unknown", "on")])
print("RESULT 1a: gap warning for off then from=unknown to=on: %s" % ("dropout" in after_off or "never logged" in after_off))
print("RESULT 1b: gap warning for on then from=unknown to=on: %s" % ("dropout" in after_on or "never logged" in after_on))

den = incumbent.load_policy(incumbent.find_policy())


def controller(path):
    c = den.Controller()
    c.set_enabled(True, T0 - 10)
    for e in LIGHTS:
        c.observe(e, den.Scene(e != LIGHTS[2]), T0 - 10)
    c.set_occupancy("on", T0 - 10); c.reconcile(T0 - 10)
    c.set_occupancy("off", T0 + 100); c.reconcile(T0 + 220)
    for t, v in path:
        c.set_occupancy(v, T0 + t)
    return sorted(cmd.entity_id.split(".")[1] for cmd in c.reconcile(T0 + 1000) if cmd.target.on)


print("RESULT 2a: stale Jetson poll (den_lighting saw off -> on) ONs at +1000: %s" % controller([(1000, "on")]))
print("RESULT 2b: HA entity unavailable (off -> unknown -> on) ONs at +1000: %s"
      % controller([(500, "unknown"), (1000, "on")]))

# 3. live commands for the unavailable case: the vacancy OFFs at +220 only (both sides agree on those).
cmds, lights = [], []
for n, e in enumerate(LIGHTS):
    if e == LIGHTS[2]:
        continue
    ctx = "%032X" % (100 + n)
    cmds.append({"time_fired_ts": T0 + 220.0, "event_data": {"entity_id": e, "revision": n + 1, "context_id": ctx,
                                                             "target": {"on": False, "brightness": None, "kelvin": None}}})
    lights.append({"last_updated_ts": T0 + 220.5, "entity_id": e, "state": "off", "context_id": ctx,
                   "context_user_id": "", "context_parent_id": ""})
out3 = run([(-10, "unknown", "on"), (100, "on", "off"), (1000, "unknown", "on")], cmds, lights)
diff_lines = [line for line in out3.splitlines() if "diff live" in line or line.startswith("  ")]
print("RESULT 3: live diff section:\n" + "\n".join(diff_lines))
print("RESULT 3: live diff shows a rear_left interval: %s" % any(line.strip().startswith("rear_left") for line in diff_lines))
print("RESULT 3: gap warning printed: %s" % ("dropout" in out3 or "never logged" in out3))
