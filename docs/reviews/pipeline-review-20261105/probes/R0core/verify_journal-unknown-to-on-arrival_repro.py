"""verify_journal-unknown-to-on-arrival: does a journal 'from=unknown to=on' after a logged off replay as an
arrival (last-level ON for a bulb whose normal scene is off), and does run.py stay silent about it?

Two replays through runner.run with the plant ON (so the replayed bulbs follow den_lighting's own commands):
  A: occupancy exactly as sources.occupancy_journal maps the invented journal
     (unknown->on at -10 s, on->off at +100 s, unknown->on at +1000 s).
  B: the same, plus occupancy 'unknown' at +500 s: what a den_lighting that saw the HA entity become
     unavailable during the vacancy was handed (its state_changed sets unknown for a non on/off state).
It prints the incumbent's ON commands per decision in each, and run.py's stdout warnings for A.
Synthetic data only, temp dirs only, no host.

    python3 -B verify_journal-unknown-to-on-arrival.py /path/to/den-core-checkout
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
sys.path.insert(0, str(tool))
import incumbent  # noqa: E402
import runner  # noqa: E402
import sources  # noqa: E402

T0 = 1793764800.0
DEN = "binary_sensor.den_oak_person_occupancy"
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
tmp = Path(tempfile.mkdtemp(prefix="verify-unknown-"))
edges = ["occupancy edge: entity=%s from=%s to=%s observed_at=%.3f" % (DEN, a, b, T0 + t)
         for t, a, b in ((-10, "unknown", "on"), (100, "on", "off"), (1000, "unknown", "on"))]
(tmp / "edges.txt").write_text("\n".join(edges) + "\n", "ascii")
items, gaps = sources.occupancy_journal([tmp / "edges.txt"])
print("RESULT: journal items:", [(t - T0, d["value"]) for t, _, d in items], "gaps:", gaps)

# Bulbs observed before the first edge: three on at a level, rear_left off (its normal scene is off).
bulbs = [(T0 - 20, "light_observed", {"entity_id": e, "on": e != LIGHTS[2], "brightness": 100 if e != LIGHTS[2] else None,
                                       "kelvin": 3000 if e != LIGHTS[2] else None, "context": "unknown"}) for e in LIGHTS]


def ons(extra):
    inputs = runner.tagged(bulbs, "synthetic") + runner.tagged(items + extra, "occupancy_journal")
    _, log = runner.run(inputs, [incumbent.make_policy()])
    return [(e["t"] - T0, sorted(b.split(".")[1] for b, s in e["data"]["requested_scene"].items() if s["on"]),
             sorted(b.split(".")[1] for b, s in e["data"]["requested_scene"].items() if not s["on"]))
            for e in log if e["kind"] == "decision" and e["source"] == "incumbent"]


a = ons([])
b = ons([(T0 + 500, "occupancy", {"value": "unknown", "via": "jetson_journal"})])
print("RESULT: A (journal as mapped)  decisions (t, ONs, OFFs):", a)
print("RESULT: B (off->unknown->on)   decisions (t, ONs, OFFs):", b)
a_rear = any("den_rear_left" in on for t, on, _ in a if t >= 1000)
b_rear = any("den_rear_left" in on for t, on, _ in b if t >= 1000)
print("RESULT: rear_left (normal off) turned ON at +1000 s: A=%s B=%s" % (a_rear, b_rear))

r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--journal", str(tmp / "edges.txt"),
                    "--policies", "incumbent", "--out", str(tmp / "out")],
                   cwd=str(tmp), capture_output=True, text=True)
warn = [line for line in r.stdout.splitlines() if line.startswith("occupancy_journal:")]
print("RESULT: run.py exit %d; occupancy_journal warning lines: %s" % (r.returncode, warn))
print("FAIL: replay makes an arrival ON that the unknown path does not, and run.py warns only about the first edge"
      if a_rear and not b_rear and not any("dropout" in w or "never logged" in w for w in warn) else "PASS")
