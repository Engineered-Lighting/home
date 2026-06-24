"""Pre-flight analysis: do Frigate person `update` events for a MOVING person
carry the velocity / trajectory data we need for kinematic projection?

Looks at the fresh raw-sample.jsonl captured during the user's LR<->DR walks
and reports:
1. How many person update events vs total. Cadence per track.
2. average_estimated_speed and velocity_angle distribution for person events.
3. path_data structure for person events: does it grow over the life of the
   track? Are positions different (i.e. person actually moving)?
4. Can we derive velocity from path_data deltas? Compute it for a few tracks.
"""
import json
from collections import defaultdict
from pathlib import Path

PATH = Path(r"C:\Claude\home\tmp\raw-sample-fresh.jsonl")
events = [json.loads(l) for l in open(PATH, encoding="utf-8") if l.strip()]
print(f"total events captured: {len(events)}")

# Counts
by_label = defaultdict(int)
by_label_update = defaultdict(int)
for e in events:
    a = e.get("after", {})
    by_label[a.get("label")] += 1
    if e.get("type") == "update":
        by_label_update[a.get("label")] += 1
print("\nEvents by label:")
for lab in sorted(by_label.keys(), key=lambda k: -by_label[k]):
    print(f"  {lab:<18} total={by_label[lab]:>4}  updates={by_label_update[lab]:>4}")

# Focus on person events
person_events = [e for e in events
                 if (e.get("after") or {}).get("label") == "person"]
print(f"\nPerson events: {len(person_events)}")

# Group by track id
by_track = defaultdict(list)
for e in person_events:
    a = e["after"]
    by_track[a.get("id")].append(e)
print(f"Unique person tracks: {len(by_track)}")
print()
print(f"{'track_id':<32} {'events':>6} {'cam':<12} {'duration_s':>10}")
for tid, evs in sorted(by_track.items(), key=lambda kv: -len(kv[1]))[:8]:
    cams = set(e["after"].get("camera") for e in evs)
    start = min(e["after"].get("start_time", 0) for e in evs)
    end = max(e["after"].get("frame_time", 0) for e in evs)
    dur = end - start if end and start else 0
    print(f"  {tid[:30]:<32} {len(evs):>6} {','.join(cams):<12} {dur:>10.2f}")

# Velocity fields on person update events
print("\n=== Velocity fields on person update events ===")
person_updates = [e for e in person_events if e.get("type") == "update"]
print(f"person update events: {len(person_updates)}")
nonzero_speed = sum(1 for e in person_updates
                   if e["after"].get("current_estimated_speed", 0) != 0)
nonzero_angle = sum(1 for e in person_updates
                   if e["after"].get("velocity_angle", 0) != 0)
nonzero_avg = sum(1 for e in person_updates
                 if e["after"].get("average_estimated_speed", 0) != 0)
print(f"  current_estimated_speed non-zero: {nonzero_speed} ({100*nonzero_speed/max(1,len(person_updates)):.1f}%)")
print(f"  average_estimated_speed non-zero: {nonzero_avg} ({100*nonzero_avg/max(1,len(person_updates)):.1f}%)")
print(f"  velocity_angle non-zero:          {nonzero_angle} ({100*nonzero_angle/max(1,len(person_updates)):.1f}%)")

# Sample some
print("\n=== Sample of person update events (first 10) ===")
for e in person_updates[:10]:
    a = e["after"]
    pd = a.get("path_data", [])
    print(f"  cam={a.get('camera'):<12} id={a.get('id')[:18]} "
          f"speed={a.get('current_estimated_speed'):>6} "
          f"angle={a.get('velocity_angle'):>6} "
          f"motionless={a.get('motionless_count'):>4} "
          f"path_data_len={len(pd):>3}")

# Compute velocity from path_data on the longest track
print("\n=== Derive velocity from path_data (longest person track) ===")
longest_track_id = max(by_track.keys(), key=lambda k: len(by_track[k]))
longest_events = sorted(by_track[longest_track_id], key=lambda e: e["after"].get("frame_time", 0))
print(f"track {longest_track_id[:30]}, {len(longest_events)} events")
# Pull the path_data from the LAST event (most complete)
final = longest_events[-1]["after"]
pd = final.get("path_data") or []
print(f"path_data on final event: {len(pd)} points")
if len(pd) >= 2:
    pd_sorted = sorted(pd, key=lambda pt: pt[1])
    # Print first / last / a few in middle
    print("First 3 points:")
    for pt in pd_sorted[:3]:
        print(f"  {pt}")
    print("Last 3 points:")
    for pt in pd_sorted[-3:]:
        print(f"  {pt}")
    # Compute velocity between consecutive recent points
    print("\nVelocity between consecutive points (last 10):")
    for i in range(max(0, len(pd_sorted) - 10), len(pd_sorted) - 1):
        (x0, y0), t0 = pd_sorted[i]
        (x1, y1), t1 = pd_sorted[i + 1]
        dt = t1 - t0
        dx = x1 - x0
        dy = y1 - y0
        if dt > 0:
            from math import hypot, atan2, degrees
            v = hypot(dx, dy) / dt
            a = degrees(atan2(dy, dx))
            print(f"  dt={dt:>6.3f}s  dx={dx:>+7.4f}  dy={dy:>+7.4f}  speed_norm={v:>6.3f} norm/s  angle={a:>+7.1f}°")
