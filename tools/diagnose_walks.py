"""diagnose_walks.py — per-walk diagnostic for the kinematic anticipator.

For each retrospectively-identified walk in eval-anticipator.jsonl, prints
a timeline view: the anticipated state changes during the walk window AND
the per-event debug records from the anticipator (path_data_len_s, speed,
angle, target_room_raw/official, suppressed_reason). Tells you EXACTLY why
each walk hit or missed.

Then aggregates patterns across walks: "of the missed walks, how many had
hysteresis_no_majority as the last suppressed_reason before arrival?" —
the diagnostic that picks the next tuning move.

Usage:
  python tools/diagnose_walks.py                           # all walks
  python tools/diagnose_walks.py --walk 3                  # single walk
  python tools/diagnose_walks.py --filter missed           # only misses
  python tools/diagnose_walks.py --filter wrong-room       # walks with
                                                            # wrong-room
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_anticipator import (
    load_events, load_zone_to_room, identify_walks, replay_brightness,
    VACANT_DAY_PCT, VACANT_NIGHT_PCT, ANTICIPATED_PCT, WALK_PRE_WINDOW_S,
)


DEFAULT_EVENTS = "C:/Claude/home/tmp/eval-anticipator.jsonl"
DEFAULT_SPATIAL = "C:/Claude/home/tmp/spatial_model_deployed.json"


def _short(s, n=10):
    s = str(s or "")
    return s[:n] + "…" if len(s) > n else s


def _format_debug_event(evt, walk_t0):
    """Pretty-print a mqtt_debug record relative to a walk's source_off."""
    body = evt.get("body") or {}
    ts = body.get("ts") or evt.get("ts") or 0
    rel = ts - walk_t0
    cam = (body.get("camera") or "?")[:14]
    track = _short(body.get("track_id"), 14)
    path_len = body.get("path_data_len_s")
    speed = body.get("derived_speed")
    angle = body.get("derived_angle_deg")
    foot = body.get("foot_xy")
    raw = body.get("target_room_raw") or "—"
    official = body.get("target_room_official") or "—"
    suppressed = body.get("suppressed_reason") or "—"
    evt_type = body.get("event_type") or "?"
    zones = body.get("current_zones") or []
    zones_short = ",".join(zones)[:30]
    pl_str = f"{path_len:>5.1f}" if path_len is not None else "  ?  "
    sp_str = f"{speed:>5.2f}" if speed is not None else "  ?  "
    an_str = f"{angle:>+4.0f}°" if angle is not None else "   ?"
    return (f"  t={rel:>+7.2f}s  {evt_type:<6} cam={cam:<13} "
            f"track={track:<16} "
            f"path={pl_str}s speed={sp_str} angle={an_str}  "
            f"raw={raw:<13} ofc={official:<13} "
            f"sup={suppressed:<22} zones=[{zones_short}]")


def _format_anticipated_event(evt, walk_t0):
    ts = evt.get("ts", 0)
    rel = ts - walk_t0
    room = evt.get("room")
    state = evt.get("state")
    return f"  t={rel:>+7.2f}s  ANTICIPATED  {room:<13} = {state}"


def collect_walk_view(events, walk):
    """Return the slice of events relevant to one walk's window."""
    t0 = walk.source_off_ts
    t1 = (walk.target_on_ts or walk.source_off_ts) + 5.0
    pre = t0 - WALK_PRE_WINDOW_S
    out = []
    for e in events:
        ts = e.get("ts", 0)
        if pre <= ts <= t1:
            out.append(e)
    return out


def classify_walk(walk, view, vacant_floor: float, anticipated_pct: float,
                  tau_s: float = 1.5):
    """Decide hit/miss/wrong-room verdict for a walk + extract diagnostics."""
    if walk.target_room is None:
        return {
            "verdict": "orphan",
            "lead_time_s": None,
            "target_predictions": [],
            "wrong_room_predictions": [],
            "brightness_at_arrival": None,
            "last_suppressed_reason_before_arrival": None,
        }
    if walk.excluded_reason:
        return {
            "verdict": f"excluded_{walk.excluded_reason}",
            "lead_time_s": None,
            "target_predictions": [],
            "wrong_room_predictions": [],
            "brightness_at_arrival": None,
            "last_suppressed_reason_before_arrival": None,
        }

    target_predictions = []
    wrong_room_predictions = []
    for e in view:
        if e.get("source") != "mqtt_anticipated":
            continue
        ts = e.get("ts", 0)
        if ts > walk.target_on_ts:
            continue
        if str(e.get("state")).upper() != "ON":
            continue
        room = e.get("room")
        if room == walk.target_room:
            target_predictions.append(e)
        else:
            wrong_room_predictions.append(e)

    # Brightness replay
    preds = [
        {"ts": e.get("ts"), "room": e.get("room"), "state": e.get("state")}
        for e in view if e.get("source") == "mqtt_anticipated"
    ]
    bri = replay_brightness(preds, walk, tau_s=tau_s,
                            anticipated_pct=anticipated_pct,
                            vacant_floor=vacant_floor)

    # The last "suppressed_reason" on a debug event before arrival is highly
    # diagnostic — it tells us WHY the anticipator gave up.
    last_suppressed = None
    for e in view:
        if e.get("source") != "mqtt_debug":
            continue
        body = e.get("body") or {}
        ts = body.get("ts") or e.get("ts", 0)
        if ts > walk.target_on_ts:
            continue
        sup = body.get("suppressed_reason")
        if sup:
            last_suppressed = sup

    # Verdict
    first_target = target_predictions[0] if target_predictions else None
    if first_target:
        lead = walk.target_on_ts - first_target["ts"]
        if bri["brightness_at_arrival"] >= VACANT_DAY_PCT:
            verdict = "hit"
        else:
            verdict = "fired_too_late"
    else:
        lead = None
        verdict = "miss" if not wrong_room_predictions else "wrong_room_only"

    return {
        "verdict": verdict,
        "lead_time_s": round(lead, 2) if lead is not None else None,
        "target_predictions": target_predictions,
        "wrong_room_predictions": wrong_room_predictions,
        "brightness_at_arrival": bri["brightness_at_arrival"],
        "last_suppressed_reason_before_arrival": last_suppressed,
    }


def print_walk_detail(idx, walk, view, classification, vacant_floor: float):
    """Full timeline for one walk."""
    gap = ((walk.target_on_ts or walk.source_off_ts) - walk.source_off_ts)
    target = walk.target_room or "—"
    excl = f" [EXCLUDED: {walk.excluded_reason}]" if walk.excluded_reason else ""
    print(f"\n=== Walk #{idx+1} — {walk.source_room} → {target}  "
          f"({walk.bucket}, gap={gap:.1f}s){excl} ===")
    print(f"  source_off  ts={walk.source_off_ts:.2f}")
    if walk.target_on_ts:
        print(f"  target_on   ts={walk.target_on_ts:.2f}")
    print(f"  verdict: {classification['verdict']}", end="")
    if classification["lead_time_s"] is not None:
        print(f"  lead_time={classification['lead_time_s']}s", end="")
    if classification["brightness_at_arrival"] is not None:
        print(f"  brightness@arrival(τ=1.5,vacant={vacant_floor:.0f})="
              f"{classification['brightness_at_arrival']:.1f}%", end="")
    print()
    if classification["last_suppressed_reason_before_arrival"]:
        print(f"  last suppressed_reason before arrival: "
              f"{classification['last_suppressed_reason_before_arrival']}")
    if classification["wrong_room_predictions"]:
        rooms = sorted({p["room"] for p in classification["wrong_room_predictions"]})
        print(f"  ⚠ wrong-room predictions fired: {rooms}")

    # Timeline of anticipated + debug events
    print()
    print("  Timeline (relative to source_off):")
    last_was_anticipated = False
    for e in view:
        src = e.get("source")
        if src == "mqtt_anticipated":
            print(_format_anticipated_event(e, walk.source_off_ts))
            last_was_anticipated = True
        elif src == "mqtt_debug":
            print(_format_debug_event(e, walk.source_off_ts))
        # We skip ha_ws and other sources to keep output focused.


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", default=DEFAULT_EVENTS)
    ap.add_argument("--spatial", default=DEFAULT_SPATIAL)
    ap.add_argument("--walk", type=int, default=None,
                    help="Show only walk with this 1-based index.")
    ap.add_argument("--filter",
                    choices=["all", "hit", "miss", "wrong-room",
                             "fired-too-late", "excluded"],
                    default="all")
    ap.add_argument("--vacant-floor", type=float, default=VACANT_NIGHT_PCT)
    ap.add_argument("--anticipated-pct", type=float, default=ANTICIPATED_PCT)
    args = ap.parse_args()

    events = load_events(args.events)
    z2r = load_zone_to_room(args.spatial)
    walks = identify_walks(events, z2r)
    print(f"Loaded {len(events)} events. Identified {len(walks)} walks.")
    if not walks:
        return 0

    # Pre-classify each walk
    classifications = []
    for w in walks:
        view = collect_walk_view(events, w)
        c = classify_walk(w, view, args.vacant_floor, args.anticipated_pct)
        classifications.append((w, view, c))

    # Compact summary first
    print()
    print(f"{'#':>3}  {'source → target':<32} {'bucket':<10} "
          f"{'verdict':<22} {'lead':>6}  {'bri':>5}  {'last suppressed':<24}")
    print("-" * 120)
    for i, (w, _, c) in enumerate(classifications):
        src = w.source_room
        tgt = w.target_room or "—"
        sym = f"{src} → {tgt}"[:32]
        lead = f"{c['lead_time_s']:.1f}s" if c['lead_time_s'] is not None else "  —"
        bri = (f"{c['brightness_at_arrival']:.0f}"
               if c['brightness_at_arrival'] is not None else "  —")
        last = (c['last_suppressed_reason_before_arrival'] or "—")[:24]
        print(f"{i+1:>3}  {sym:<32} {w.bucket:<10} {c['verdict']:<22} "
              f"{lead:>6}  {bri:>5}  {last:<24}")

    # Filter for detail view
    def _matches_filter(c) -> bool:
        if args.filter == "all":
            return True
        if args.filter == "hit":
            return c["verdict"] == "hit"
        if args.filter == "miss":
            return c["verdict"] in ("miss", "wrong_room_only",
                                    "fired_too_late")
        if args.filter == "wrong-room":
            return bool(c["wrong_room_predictions"])
        if args.filter == "fired-too-late":
            return c["verdict"] == "fired_too_late"
        if args.filter == "excluded":
            return c["verdict"].startswith("excluded_")
        return True

    if args.walk is not None:
        # Show detail for the requested walk
        i = args.walk - 1
        if 0 <= i < len(classifications):
            w, view, c = classifications[i]
            print_walk_detail(i, w, view, c, args.vacant_floor)
        else:
            print(f"\nWalk #{args.walk} out of range (1..{len(classifications)})")
    elif args.filter != "all":
        print(f"\nShowing detail for filter={args.filter}:")
        for i, (w, view, c) in enumerate(classifications):
            if _matches_filter(c):
                print_walk_detail(i, w, view, c, args.vacant_floor)

    # Aggregate diagnostics
    print()
    print("=" * 60)
    print("AGGREGATE DIAGNOSTICS")
    print("=" * 60)

    verdict_counts = Counter(c["verdict"] for _, _, c in classifications)
    print("\nVerdict distribution:")
    for v, n in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        print(f"  {v:<28} {n}")

    # last-suppressed-reason for non-hit walks
    suppressed_by_verdict: dict[str, Counter] = defaultdict(Counter)
    for _, _, c in classifications:
        v = c["verdict"]
        sup = c["last_suppressed_reason_before_arrival"] or "(no debug record)"
        suppressed_by_verdict[v][sup] += 1
    print("\nLast suppressed_reason before arrival, by verdict:")
    for v, ctr in suppressed_by_verdict.items():
        print(f"  {v}:")
        for sup, n in ctr.most_common():
            print(f"    {sup:<28} {n}")

    # Wrong-room target distribution
    wrong_rooms = Counter()
    for _, _, c in classifications:
        for p in c["wrong_room_predictions"]:
            wrong_rooms[p.get("room")] += 1
    if wrong_rooms:
        print("\nWrong-room predictions by target (count of walks affected):")
        for r, n in wrong_rooms.most_common():
            print(f"  {r:<20} {n}")

    # Lead-time distribution on hits
    lead_times = [c["lead_time_s"] for _, _, c in classifications
                  if c["verdict"] == "hit" and c["lead_time_s"] is not None]
    if lead_times:
        lead_times.sort()
        p50_i = len(lead_times) // 2
        print(f"\nLead-time on hits: n={len(lead_times)}  "
              f"min={lead_times[0]:.2f}  "
              f"p50={lead_times[p50_i]:.2f}  "
              f"max={lead_times[-1]:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
