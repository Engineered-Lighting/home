"""For each __entry__ on a non-living-room camera, find the previous AND
next __exit__ on a different camera within +/- 60s. Report which came first,
the gap, and the score. If the dominant pattern is "entry FIRST, exit LATER",
then Frigate's late __exit__ (due to track timeout) is breaking the stitch."""
from __future__ import annotations
import json, datetime as dt
from collections import Counter
from pathlib import Path

CORPUS = Path(r"C:\Claude\home\tmp\transitions.jsonl")

def ts(s): return dt.datetime.fromisoformat(s).timestamp()

raw = []
with open(CORPUS, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: raw.append(json.loads(line))
        except: pass
raw.sort(key=lambda r: ts(r["ts"]))

exits   = [r for r in raw if r.get("to_zone") == "__exit__"]
entries = [r for r in raw if r.get("from_zone") == "__entry__"]

# For each entry, find nearest exit on a different camera within +-60s
WINDOW = 60.0
buckets = Counter()
sample_lr_dr = []  # entries on dining_room with prior LR exit
for ent in entries:
    t_e = ts(ent["ts"])
    cam_e = ent.get("camera")
    best_before = None
    best_after = None
    for ex in exits:
        if ex.get("camera") == cam_e: continue
        t_x = ts(ex["ts"])
        gap = t_e - t_x   # positive if exit came BEFORE entry
        if abs(gap) > WINDOW: continue
        if gap >= 0:
            if best_before is None or gap < (t_e - ts(best_before["ts"])):
                best_before = ex
        else:
            if best_after is None or -gap < (ts(best_after["ts"]) - t_e):
                best_after = ex

    # Choose the closest (signed)
    if best_before and best_after:
        gap_b = t_e - ts(best_before["ts"])
        gap_a = ts(best_after["ts"]) - t_e
        if gap_b <= gap_a:
            chosen = ("exit_first", gap_b, best_before)
        else:
            chosen = ("entry_first", gap_a, best_after)
    elif best_before:
        chosen = ("exit_first", t_e - ts(best_before["ts"]), best_before)
    elif best_after:
        chosen = ("entry_first", ts(best_after["ts"]) - t_e, best_after)
    else:
        buckets[("no_match", -1)] += 1
        continue

    order, gap, other = chosen
    if gap <= 5:    bucket = "0-5s"
    elif gap <= 10: bucket = "5-10s"
    elif gap <= 30: bucket = "10-30s"
    else:           bucket = "30-60s"
    buckets[(order, bucket)] += 1
    if (cam_e == "dining_room" and other.get("camera") == "living_room"
            and len(sample_lr_dr) < 12):
        sample_lr_dr.append({
            "order": order, "gap_s": round(gap, 1),
            "entry_ts": ent["ts"][:19], "entry_zone": ent.get("to_zone"),
            "entry_score": ent.get("frigate_score"),
            "other_ts": other["ts"][:19], "other_zone": other.get("from_zone"),
            "other_score": other.get("frigate_score"),
        })

print(f"Total entries: {len(entries)}\n")
print(f"For each __entry__, the NEAREST __exit__ on a different camera was:")
print(f"{'order':<14} {'gap_bucket':<10} {'count':>6}")
print("-" * 35)
for k, v in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1])):
    print(f"{k[0]:<14} {k[1]:<10} {v:>6}")
print()
print(f"Total entries with NO exit within +/-{WINDOW}s on any other camera: "
      f"{buckets[('no_match', -1)]}")
print()
print(f"Sample dining_room __entry__ events with nearest living_room __exit__:")
for s in sample_lr_dr:
    print(f"  {s['order']:<12} gap={s['gap_s']:>5.1f}s   "
          f"entry@{s['entry_ts']}->{s['entry_zone']} (sc={s['entry_score']})"
          f"  /  exit@{s['other_ts']} from {s['other_zone']} (sc={s['other_score']})")
