#!/usr/bin/env python3
"""Answer: 'does the predictor anticipate living room -> dining room -> kitchen?'

Two parts:
  1. Count how many such transitions actually appear in the corpus (post-
     stitch). If the data has zero LR->DR events, the model can't possibly
     have learned the pattern.
  2. Train the predictor on the corpus (same walk-forward as the backtest),
     then query its current beliefs at the LR and DR starting points to see
     what it predicts.
"""
from __future__ import annotations
import json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from predictor import (Predictor, clean_transitions, load_adjacency,
                       stitch_transitions, to_epoch, transitions_since)

CORPUS = r"C:\Claude\home\tmp\transitions.jsonl"
SINCE = "2026-05-21T18:20:00+00:00"
MIN_SCORE = 0.80

LIVING_ROOM_ZONES = {"front_left", "whole_living_room", "sofa", "office"}
DINING_ROOM_ZONES = {"dining_right", "dining_left", "whole_dining_room"}
KITCHEN_ZONES = {"island_left", "sink", "whole_kitchen"}

def room_of(zone: str) -> str:
    if zone in LIVING_ROOM_ZONES: return "LIVING"
    if zone in DINING_ROOM_ZONES: return "DINING"
    if zone in KITCHEN_ZONES: return "KITCHEN"
    if zone == "__exit__": return "EXIT"
    if zone == "__entry__": return "ENTRY"
    return f"OTHER:{zone}"

def main():
    repo = Path(__file__).resolve().parent.parent
    adj = load_adjacency(str(repo / "predictor" / "seed_adjacency.yaml"))

    with open(CORPUS, "r", encoding="utf-8") as f:
        raw = [json.loads(l) for l in f if l.strip()]
    windowed = transitions_since(raw, SINCE)
    cleaned = clean_transitions(windowed, MIN_SCORE)
    recs = stitch_transitions(cleaned)

    # 1. Count cross-room transitions (post-stitch)
    cross = defaultdict(int)
    for r in recs:
        fr = room_of(r["from_zone"])
        tr = room_of(r["to_zone"])
        if fr != tr and fr.startswith(("LIVING", "DINING", "KITCHEN")) \
                    and tr.startswith(("LIVING", "DINING", "KITCHEN")):
            cross[(fr, tr)] += 1

    print(f"corpus: {len(recs)} stitched records")
    print()
    print("CROSS-ROOM TRANSITIONS observed in corpus:")
    for k in sorted(cross.keys()):
        print(f"  {k[0]:<10} -> {k[1]:<10}  {cross[k]:>4}")
    if not cross:
        print("  (none — stitching didn't recover any cross-camera moves)")

    # 2. Train the predictor on the whole corpus, then query
    pred = Predictor(adjacency=adj)
    for r in recs:
        ts = to_epoch(r["ts"])
        pred.update(r["from_zone"], r["tod_bucket"], r["to_zone"], ts)

    # Query: starting points the user mentioned
    import time
    now = time.time()
    for start, tod in [("whole_living_room", "midday"),
                       ("whole_living_room", "evening"),
                       ("front_left",        "evening"),
                       ("sofa",              "evening"),
                       ("dining_right",      "evening"),
                       ("dining_left",       "evening"),
                       ("whole_dining_room", "evening")]:
        out = pred.predict(start, tod, now)
        top5 = out["predictions"][:5]
        print()
        print(f"FROM {start!r} @ {tod}:")
        for p in top5:
            zone = p["zone"]
            prob = p.get("p", p.get("probability", 0))
            room = room_of(zone)
            print(f"  {prob:6.1%}  {zone:<22}  ({room})")

if __name__ == "__main__":
    raise SystemExit(main())
