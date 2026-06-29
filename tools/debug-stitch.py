"""Diagnostic — find why cross-camera walks aren't being captured.

Looks at the RAW transitions.jsonl (no stitch, no filters) and answers:
1. How many __exit__ events does each camera emit?
2. How many __entry__ events does each camera receive?
3. For each __exit__ on camera A, is there an __entry__ on a different camera
   within 1s, 5s, 10s, 30s, 60s, 300s? (Histogram the stitch-window gap.)
4. Are the track_ids continuous across cameras, or does each camera generate
   its own new track_id (which would explain why naive stitching fails)?
"""
from __future__ import annotations
import json
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime

CORPUS = Path(r"C:\Claude\home\tmp\transitions.jsonl")

def iso_to_epoch(s):
    return datetime.fromisoformat(s).timestamp()

def main():
    raw = []
    with open(CORPUS, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except Exception:
                pass

    print(f"raw events: {len(raw)}\n")

    # 1+2: __exit__ / __entry__ counts by camera
    by_cam = defaultdict(lambda: {"total": 0, "exits": 0, "entries": 0,
                                   "intra": 0})
    for r in raw:
        c = r.get("camera", "?")
        bc = by_cam[c]
        bc["total"] += 1
        if r.get("to_zone") == "__exit__":
            bc["exits"] += 1
        elif r.get("from_zone") == "__entry__":
            bc["entries"] += 1
        else:
            bc["intra"] += 1

    print(f"{'camera':<14}  {'total':>6}  {'exits':>6}  {'entries':>7}  {'intra':>6}")
    print("-" * 60)
    for c, bc in sorted(by_cam.items(), key=lambda kv: -kv[1]["total"]):
        print(f"{c:<14}  {bc['total']:>6}  {bc['exits']:>6}  {bc['entries']:>7}  {bc['intra']:>6}")
    print()

    # 3: For each __exit__, find the nearest __entry__ on a DIFFERENT camera
    # within +/-300s, regardless of track_id. Bucket by gap size.
    exits  = [(iso_to_epoch(r["ts"]), r) for r in raw if r.get("to_zone") == "__exit__"]
    entries = [(iso_to_epoch(r["ts"]), r) for r in raw if r.get("from_zone") == "__entry__"]
    entries.sort()
    print(f"total __exit__:  {len(exits)}")
    print(f"total __entry__: {len(entries)}\n")

    # For each exit, find smallest positive gap to ANY entry on a DIFFERENT camera
    buckets = {"<=1s": 0, "1-5s": 0, "5-10s": 0, "10-30s": 0,
               "30-60s": 0, "60-300s": 0, ">300s": 0, "no_entry_after": 0}
    sample_lr_to_dr = []
    for ts_x, rec_x in exits:
        cam_x = rec_x.get("camera")
        # find first entry on a different camera after this exit
        best = None
        for ts_e, rec_e in entries:
            if ts_e < ts_x:
                continue
            if rec_e.get("camera") == cam_x:
                continue
            best = (ts_e - ts_x, rec_e)
            break
        if best is None:
            buckets["no_entry_after"] += 1
            continue
        gap, rec_e = best
        if gap <= 1:    buckets["<=1s"] += 1
        elif gap <= 5:  buckets["1-5s"] += 1
        elif gap <= 10: buckets["5-10s"] += 1
        elif gap <= 30: buckets["10-30s"] += 1
        elif gap <= 60: buckets["30-60s"] += 1
        elif gap <= 300:buckets["60-300s"] += 1
        else:           buckets[">300s"] += 1
        # capture some living_room->dining_room examples for inspection
        if (cam_x == "living_room" and rec_e.get("camera") == "dining_room"
                and len(sample_lr_to_dr) < 10):
            sample_lr_to_dr.append({
                "gap_s": round(gap, 2),
                "exit_ts": rec_x["ts"],
                "exit_zone": rec_x.get("from_zone"),
                "exit_track": rec_x.get("track_id"),
                "entry_ts": rec_e["ts"],
                "entry_zone": rec_e.get("to_zone"),
                "entry_track": rec_e.get("track_id"),
            })

    print("Gap from __exit__ to nearest __entry__ on a DIFFERENT camera:")
    for k, v in buckets.items():
        pct = 100 * v / len(exits) if exits else 0
        bar = "#" * int(pct / 2)
        print(f"  {k:>16}: {v:>5}  {pct:5.1f}%  {bar}")
    print()

    print("First 10 living_room->dining_room exit/entry candidates (any gap):")
    for s in sample_lr_to_dr:
        print(f"  gap={s['gap_s']:>6.1f}s   exit {s['exit_zone']} ({s['exit_track'][:18]})"
              f"  ->  entry {s['entry_zone']} ({s['entry_track'][:18]})")

    print()
    # 4: do track_ids continue across cameras?
    # For each track_id, what set of cameras saw it?
    tracks_by_camera = defaultdict(set)
    for r in raw:
        tid = r.get("track_id")
        cam = r.get("camera")
        if tid and cam:
            tracks_by_camera[tid].add(cam)
    multi_cam_tracks = sum(1 for cams in tracks_by_camera.values() if len(cams) > 1)
    total_tracks = len(tracks_by_camera)
    print(f"track_ids that appear on >1 camera: {multi_cam_tracks} / {total_tracks} "
          f"({100*multi_cam_tracks/total_tracks:.1f}%)")
    print("(If 0%, Frigate assigns a NEW track_id per camera — that's the stitch obstacle.)")

if __name__ == "__main__":
    main()
