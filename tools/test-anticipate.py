"""Unit + replay test for anticipate.py.

Three sections:
  1. _ray_exits_edge geometry — pure math.
  2. _derive_velocity on a fake path_data trajectory.
  3. Replay the fresh raw-sample.jsonl through Anticipator and print every
     prediction. Hand-verify against the known topology
     (LR east -> dining_room, DR east -> kitchen, etc.).
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "addons" / "predictive-lighting"))
import anticipate as A


def almost(a, b, tol=1e-6):
    return abs(a - b) < tol


# ----------------------------------------------------------------------
# 1. Geometry
# ----------------------------------------------------------------------
print("=== ray_exits_edge tests ===")
cases = [
    # (point, angle_deg, expected_edge)
    ((0.5, 0.5),    0, "east"),    # east
    ((0.5, 0.5),  180, "west"),    # west
    ((0.5, 0.5),   90, "south"),   # +y in image-space = south (down)
    ((0.5, 0.5),  -90, "north"),
    ((0.5, 0.5),   45, "east"),    # NE-ish but east edge is closer? actually east first because we're center
    ((0.1, 0.5),  180, "west"),    # near-west, going west
    ((0.9, 0.5),    0, "east"),    # near-east, going east
    ((0.5, 0.9),   90, "south"),   # near-south, going south
    ((0.5, 0.1),  -90, "north"),
]
fails = 0
for pt, deg, expected in cases:
    got = A._ray_exits_edge(pt, math.radians(deg))
    ok = got == expected
    print(f"  point={pt} angle={deg:>+4}deg -> {got!r:>10}  expected {expected!r}  {'OK' if ok else 'FAIL'}")
    if not ok:
        fails += 1
assert fails == 0, f"{fails} ray-cast geometry tests failed"

# ----------------------------------------------------------------------
# 2. _derive_velocity
# ----------------------------------------------------------------------
print("\n=== derive_velocity tests ===")
# A straight east walk: x from 0.2 to 0.8 over 3 seconds (steps of 0.5s)
pd = [
    [[0.2, 0.5], 1000.0],
    [[0.3, 0.5], 1000.5],
    [[0.5, 0.5], 1001.5],
    [[0.7, 0.5], 1002.5],
    [[0.8, 0.5], 1003.0],
]
v = A._derive_velocity(pd)
assert v is not None, "expected non-None velocity"
speed, angle, point, ts = v
print(f"  east-walk:  speed={speed:.3f} norm/s  angle={math.degrees(angle):.1f}deg  point={point}  ts={ts}")
assert almost(angle, 0.0, 0.1), f"expected angle ~0 (east), got {math.degrees(angle):.1f}"
assert 0.05 < speed < 1.0, f"expected ~0.3 norm/s, got {speed}"

# A south walk
pd = [
    [[0.5, 0.2], 2000.0],
    [[0.5, 0.4], 2000.5],
    [[0.5, 0.6], 2001.5],
    [[0.5, 0.8], 2002.5],
]
v = A._derive_velocity(pd)
speed, angle, _, _ = v
print(f"  south-walk: speed={speed:.3f} norm/s  angle={math.degrees(angle):.1f}deg")
assert almost(angle, math.pi / 2, 0.1), f"expected angle ~90 (south), got {math.degrees(angle):.1f}"

# Stationary -> None
pd = [
    [[0.5, 0.5], 3000.0],
    [[0.5, 0.5], 3001.0],
    [[0.5, 0.5], 3002.0],
]
v = A._derive_velocity(pd)
speed, _, _, _ = v
print(f"  stationary: speed={speed:.6f} (should be ~0)")
assert speed < 0.01

# Too-short window -> None
pd = [
    [[0.5, 0.5], 4000.0],
    [[0.6, 0.5], 4000.1],
]
v = A._derive_velocity(pd)
print(f"  short-dt:   {v} (expected None)")
assert v is None

# ----------------------------------------------------------------------
# 3. Replay against fresh raw-sample.jsonl
# ----------------------------------------------------------------------
print("\n=== Replay fresh raw-sample.jsonl through Anticipator ===")
spatial = json.loads(Path(r"C:\Claude\home\tmp\spatial_model_deployed.json").read_text(encoding="utf-8"))
ant = A.Anticipator(spatial)
print(f"  rooms registered: {ant.rooms}")
print(f"  cameras with zones: {sorted(ant.cameras.keys())}")
print(f"  zone_to_room mappings: {len(ant.zone_to_room)}")

events = [json.loads(l) for l in open(r"C:\Claude\home\tmp\raw-sample-fresh.jsonl", encoding="utf-8") if l.strip()]
person_events = [e for e in events if (e.get("after") or {}).get("label") == "person"]
print(f"  person events in replay: {len(person_events)}")
print()

all_changes = []
debug_count = 0
for e in person_events:
    changes, debug = ant.process(e)
    all_changes.extend(changes)
    if debug is not None:
        debug_count += 1
    if changes:
        a = e["after"]
        pd = a.get("path_data") or []
        v = A._derive_velocity(pd) if pd else None
        if v:
            speed, angle, pt, _ = v
            angle_deg = math.degrees(angle)
            print(f"  cam={a.get('camera'):<12} pt=({pt[0]:.2f},{pt[1]:.2f}) "
                  f"speed={speed:.2f} angle={angle_deg:>+6.1f}deg  changes={changes}")
print(f"\nDebug records produced: {debug_count} / {len(person_events)}")

print(f"\nTotal room state changes emitted: {len(all_changes)}")
print(f"Per-track final targets: {ant._track_target}")
print(f"Per-room track sets: {dict(ant._room_tracks)}")
