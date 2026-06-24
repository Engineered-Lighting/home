"""eval_anticipator.py — Phase 2 eval harness, Components 2 + 3.

Offline analyzer that reads /share/predictive-lighting/eval-anticipator.jsonl
(produced by the eval_logger inside the predictive-lighting addon) and
computes the metric dashboard the adversarial-reviewed plan specifies:

  - Walk identification (direct / paused / chained / orphan buckets,
    with target_dwell ≥ 5 s and manual-cooldown exclusions).
  - Brightness-at-arrival via ramp replay, with τ-sweep over {1.0, 1.5, 2.0}.
  - Effective recall = % walks with brightness_at_arrival ≥ VACANT_DAY_PCT.
  - Spurious-light-minutes per day (anticipated ON outside walk windows).
  - Wrong-room-during-walk rate.
  - Flap-weighted stability.
  - Predictability ceiling (from debug-stream path_data_len_s).

Honest caveat: brightness-at-arrival is computed under a SIMPLIFIED LINEAR
RAMP MODEL — it does NOT model adaptive_lighting modulation, pass_through
debounce, manual_override interactions, or per-bulb non-linearity. The
trustworthy signal is τ-robustness, not the headline percentage.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ─────────────────────────────────────────────────────────────────────────
# Constants — anchored to existing system where possible.
# ─────────────────────────────────────────────────────────────────────────

# From build-living-lights-yaml.py — the existing vacant baseline.
VACANT_DAY_PCT = 50
VACANT_NIGHT_PCT = 12
# Anticipated brightness target (the eventual pilot YAML's anticipated_pct).
# Choosing = VACANT_DAY_PCT means anticipation is a no-op against the day
# baseline (only matters at night) — the conservative wire-anyway default.
ANTICIPATED_PCT = 50

# Walk-identification thresholds (acknowledged arbitrary; reported in report).
DIRECT_PAUSED_BOUNDARY_S = 8.0
TARGET_DWELL_MIN_S = 5.0
CHAINED_GAP_MAX_S = 5.0
ORPHAN_WINDOW_S = 60.0
MANUAL_COOLDOWN_S = 60.0
WALK_PRE_WINDOW_S = 60.0  # how far back to look for anticipated publishes

# τ-sweep — three candidate vacant→anticipated transition durations.
TAUS_DEFAULT = [1.0, 1.5, 2.0]


# ─────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Walk:
    source_off_ts: float
    target_on_ts: Optional[float]       # None for orphan
    source_room: str
    target_room: Optional[str]
    bucket: str                          # direct / paused / chained / orphan
    excluded_reason: Optional[str] = None  # None / manual_cooldown / short_dwell


# ─────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────

def load_events(path: str | Path) -> list[dict]:
    """Load JSONL events in chronological order, tolerating torn final lines."""
    out: list[dict] = []
    bad = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        print(f"  note: skipped {bad} malformed JSONL line(s)")
    out.sort(key=lambda e: e.get("ts") or 0.0)
    return out


def load_zone_to_room(spatial_model_path: str | Path) -> dict[str, str]:
    """Read zone_to_room mapping from spatial_model.json, lowercased keys."""
    d = json.loads(Path(spatial_model_path).read_text(encoding="utf-8"))
    return {k.lower(): v for k, v in (d.get("zone_to_room") or {}).items()}


# ─────────────────────────────────────────────────────────────────────────
# Walk identification
# ─────────────────────────────────────────────────────────────────────────

# Match entity_id "binary_sensor.<zone>_person_occupancy" but NOT the _stable.
def _zone_from_occ_entity(entity_id: str) -> Optional[str]:
    if not entity_id.endswith("_person_occupancy"):
        return None
    z = entity_id[len("binary_sensor."):-len("_person_occupancy")]
    # Skip the room-level rollup sensors (which look like
    # binary_sensor.<camera>_person_occupancy). Heuristic: rooms (cameras)
    # don't appear in zone_to_room; sub-zones do. Caller resolves at lookup.
    return z


def _is_manual_cooldown_entity(entity_id: str) -> bool:
    return entity_id.startswith("input_datetime.living_lights_zone_") \
        and entity_id.endswith("_last_manual_at")


def identify_walks(events: list[dict],
                   zone_to_room: dict[str, str]) -> list[Walk]:
    """Identify retrospective walks from the JSONL event stream.

    A walk starts when ANY zone in a room flips OFF, AND no other zone in
    that room is ON. A walk completes when ANY zone in a DIFFERENT room
    flips ON within ORPHAN_WINDOW_S of the source-off.

    Buckets:
      direct  — completion within DIRECT_PAUSED_BOUNDARY_S
      paused  — completion between boundary and ORPHAN_WINDOW_S
      chained — second walk starts within CHAINED_GAP_MAX_S of first's end
      orphan  — no completion within ORPHAN_WINDOW_S

    Exclusions:
      manual_cooldown — any zone in source OR target room had a
        last_manual_at event within MANUAL_COOLDOWN_S of the walk window
    """
    # 1. Track per-zone occupancy state through time. Build per-room
    #    occupancy from zone OR semantics.
    zone_state: dict[str, bool] = {}  # zone (lowercase) → True/False
    room_for_zone: dict[str, str] = zone_to_room
    # last_manual_at events for cooldown gating
    manual_events: list[tuple[float, str]] = []  # (ts, room)

    # Helpers
    def _room_occupied(room: str) -> bool:
        return any(
            zone_state.get(z, False)
            for z, r in room_for_zone.items() if r == room
        )

    walks: list[Walk] = []
    last_source_off: dict[str, float] = {}  # room → ts of most recent off
    last_walk_end_by_room: dict[str, float] = {}  # for chained detection

    for evt in events:
        src = evt.get("source")
        ts = evt.get("ts") or 0.0
        if src == "ha_ws":
            eid = evt.get("entity_id") or ""
            new = evt.get("new")
            # Cooldown event?
            if _is_manual_cooldown_entity(eid):
                # The entity name includes the zone slug
                # input_datetime.living_lights_zone_<zone>_last_manual_at
                prefix = "input_datetime.living_lights_zone_"
                suffix = "_last_manual_at"
                z = eid[len(prefix):-len(suffix)].lower()
                r = room_for_zone.get(z)
                if r:
                    manual_events.append((ts, r))
                continue
            # Occupancy event?
            zone = _zone_from_occ_entity(eid)
            if not zone:
                continue
            # Stable variant has the suffix doubled — skip; we use the raw.
            if zone.endswith("_person_occupancy"):
                continue
            zlower = zone.lower()
            room = room_for_zone.get(zlower)
            if not room:
                continue
            prev_room_occupied = _room_occupied(room)
            zone_state[zlower] = (new == "on")
            curr_room_occupied = _room_occupied(room)

            # Room just went vacant?
            if prev_room_occupied and not curr_room_occupied:
                last_source_off[room] = ts

            # Room just got occupied?
            if not prev_room_occupied and curr_room_occupied:
                # Find a pending source-off in another room within window.
                best_source: Optional[str] = None
                best_off_ts = -1.0
                for src_room, off_ts in last_source_off.items():
                    if src_room == room:
                        continue
                    if ts - off_ts > ORPHAN_WINDOW_S:
                        continue
                    if off_ts > best_off_ts:
                        best_off_ts = off_ts
                        best_source = src_room
                if best_source is not None:
                    bucket = ("direct"
                              if (ts - best_off_ts) <= DIRECT_PAUSED_BOUNDARY_S
                              else "paused")
                    # Promote to chained if a previous walk ended very recently
                    if last_walk_end_by_room.get(best_source, -1e9) >= \
                            best_off_ts - CHAINED_GAP_MAX_S:
                        bucket = "chained"
                    walks.append(Walk(
                        source_off_ts=best_off_ts,
                        target_on_ts=ts,
                        source_room=best_source,
                        target_room=room,
                        bucket=bucket,
                    ))
                    last_walk_end_by_room[room] = ts
                    # consume the source-off so it can't pair again
                    last_source_off.pop(best_source, None)

    # 2. Convert un-paired source-offs into orphan walks (if old enough).
    #    We use the LAST event's ts as the "present" cutoff.
    present_ts = events[-1].get("ts") if events else 0.0
    for room, off_ts in last_source_off.items():
        if present_ts - off_ts > ORPHAN_WINDOW_S:
            walks.append(Walk(
                source_off_ts=off_ts,
                target_on_ts=None,
                source_room=room,
                target_room=None,
                bucket="orphan",
            ))

    # 3. Apply manual-cooldown exclusion.
    for w in walks:
        # Window of concern: [source_off - 60, target_on + 60] (or just
        # +60 after source_off for orphans)
        win_start = w.source_off_ts - MANUAL_COOLDOWN_S
        win_end = (w.target_on_ts or w.source_off_ts) + MANUAL_COOLDOWN_S
        rooms = {w.source_room}
        if w.target_room:
            rooms.add(w.target_room)
        for m_ts, m_room in manual_events:
            if m_room in rooms and win_start <= m_ts <= win_end:
                w.excluded_reason = "manual_cooldown"
                break

    # 4. Target-dwell filter — drop walks where the user immediately left
    #    target room (transient transit). We approximate "dwell" by looking
    #    for a subsequent source-off on target_room within the next
    #    TARGET_DWELL_MIN_S window. If found, mark short_dwell.
    walks.sort(key=lambda w: w.source_off_ts)
    for w in walks:
        if w.target_room is None or w.target_on_ts is None:
            continue
        if w.excluded_reason:
            continue
        # Look for a future event where target room goes vacant.
        # Easier: scan events after target_on_ts and see when room next
        # becomes unoccupied (zone-OR-vacancy).
        z_state2 = dict(zone_state)  # we'll replay forward from this snapshot
        # Actually replay from start using same zone_state logic — simpler
        # to compute target-vacancy ts directly.
        target_vacant_ts = None
        local_state: dict[str, bool] = {}
        for evt in events:
            t = evt.get("ts") or 0.0
            if t < w.target_on_ts:
                continue
            if t > w.target_on_ts + TARGET_DWELL_MIN_S + 5.0:
                break  # past concern window
            if evt.get("source") != "ha_ws":
                continue
            eid = evt.get("entity_id") or ""
            zone = _zone_from_occ_entity(eid)
            if not zone or zone.endswith("_person_occupancy"):
                continue
            zlower = zone.lower()
            r = room_for_zone.get(zlower)
            if r != w.target_room:
                continue
            local_state[zlower] = (evt.get("new") == "on")
            any_on = any(local_state.get(z, False)
                         for z, rr in room_for_zone.items()
                         if rr == w.target_room)
            if not any_on:
                target_vacant_ts = t
                break
        if target_vacant_ts is not None and \
                (target_vacant_ts - w.target_on_ts) < TARGET_DWELL_MIN_S:
            w.excluded_reason = "short_dwell"

    return walks


# ─────────────────────────────────────────────────────────────────────────
# Brightness ramp replay (simplified linear model)
# ─────────────────────────────────────────────────────────────────────────

def replay_brightness(predictions: list[dict],
                      walk: Walk,
                      tau_s: float,
                      anticipated_pct: float = ANTICIPATED_PCT,
                      vacant_floor: float = VACANT_DAY_PCT) -> dict:
    """Replay the anticipated state stream for `walk.target_room` through
    a SIMPLIFIED LINEAR RAMP. Returns brightness-at-arrival + flap_count.

    Predictions: list of `{ts, room, state}` events filtered to
    walk.target_room within [source_off - WALK_PRE_WINDOW_S, target_on].

    NOTE: this is a cartoon model that does NOT capture adaptive_lighting,
    pass_through debounce, manual_override interactions, or non-linear bulb
    response. Use τ-robustness as the trust signal, not the headline number.
    """
    if walk.target_room is None or walk.target_on_ts is None:
        return {"brightness_at_arrival": vacant_floor,
                "flap_count": 0, "prediction_on_count": 0}

    # State at the START of the walk window.
    brightness = vacant_floor
    state = "off"
    last_change_ts = walk.source_off_ts - WALK_PRE_WINDOW_S
    last_change_brightness = vacant_floor

    flap_count = 0
    on_count = 0

    relevant = [p for p in predictions
                if p.get("room") == walk.target_room
                and p.get("ts", 0) >= walk.source_off_ts - WALK_PRE_WINDOW_S
                and p.get("ts", 0) <= walk.target_on_ts]
    relevant.sort(key=lambda p: p.get("ts") or 0.0)

    def bri_at(t: float) -> float:
        """Linear ramp from last_change_brightness toward target."""
        if state == "on":
            target = anticipated_pct
        else:
            target = vacant_floor
        dt = t - last_change_ts
        if dt >= tau_s:
            return float(target)
        frac = dt / tau_s
        return last_change_brightness + (target - last_change_brightness) * frac

    for p in relevant:
        t = p.get("ts", 0)
        new_state = "on" if str(p.get("state")).upper() == "ON" else "off"
        if new_state == state:
            continue
        # Snapshot current brightness at the moment of change.
        cur_b = bri_at(t)
        last_change_brightness = cur_b
        last_change_ts = t
        state = new_state
        if state == "on":
            on_count += 1
        flap_count += 1

    brightness = bri_at(walk.target_on_ts)
    return {
        "brightness_at_arrival": round(brightness, 2),
        "flap_count": flap_count,
        "prediction_on_count": on_count,
    }


# ─────────────────────────────────────────────────────────────────────────
# End-to-end analysis + report
# ─────────────────────────────────────────────────────────────────────────

def collect_predictions(events: list[dict]) -> list[dict]:
    """Pull mqtt_anticipated events. Returns list of {ts, room, state}."""
    out = []
    for e in events:
        if e.get("source") == "mqtt_anticipated":
            out.append({"ts": e.get("ts"), "room": e.get("room"),
                        "state": e.get("state")})
    return out


def spurious_minutes_per_day(predictions: list[dict], walks: list[Walk],
                             total_observed_s: float) -> dict:
    """Total seconds where any anticipated_<room>=ON outside a confirmed
    walk window. Returns absolute seconds + per-day projection."""
    # Build walk windows (per target room).
    walk_windows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for w in walks:
        if w.excluded_reason or w.target_room is None:
            continue
        walk_windows[w.target_room].append((
            w.source_off_ts - WALK_PRE_WINDOW_S,
            (w.target_on_ts or w.source_off_ts) + 2.0,
        ))

    # Scan predictions per room, compute ON-time outside any window.
    on_ts_by_room: dict[str, Optional[float]] = defaultdict(lambda: None)
    spurious_total_s = 0.0
    for p in sorted(predictions, key=lambda p: p.get("ts") or 0.0):
        room = p.get("room")
        ts = p.get("ts") or 0.0
        st = str(p.get("state")).upper()
        if st == "ON":
            on_ts_by_room[room] = ts
        elif st == "OFF":
            on_start = on_ts_by_room[room]
            if on_start is not None:
                # Compute "outside-walk-window" portion of [on_start, ts].
                windows = walk_windows.get(room, [])
                segs = _subtract_windows((on_start, ts), windows)
                spurious_total_s += sum(b - a for a, b in segs)
                on_ts_by_room[room] = None

    days = max(total_observed_s / 86400.0, 1e-6)
    return {
        "spurious_seconds_total": round(spurious_total_s, 1),
        "spurious_minutes_per_day": round(spurious_total_s / 60.0 / days, 2),
        "days_observed": round(days, 3),
    }


def _subtract_windows(seg: tuple[float, float],
                      windows: list[tuple[float, float]]
                      ) -> list[tuple[float, float]]:
    """Subtract a list of (start, end) windows from a single (start, end)
    segment. Returns remaining sub-segments."""
    remaining = [seg]
    for w_start, w_end in windows:
        next_rem = []
        for s, e in remaining:
            if w_end <= s or w_start >= e:
                next_rem.append((s, e))
            else:
                if w_start > s:
                    next_rem.append((s, w_start))
                if w_end < e:
                    next_rem.append((w_end, e))
        remaining = next_rem
    return remaining


def analyze(events_path: str | Path,
            spatial_model_path: str | Path,
            taus: list[float] = TAUS_DEFAULT,
            anticipated_pct: float = ANTICIPATED_PCT,
            vacant_floor: float = VACANT_DAY_PCT) -> dict:
    """Top-level analysis. Returns the full metric dashboard dict."""
    events = load_events(events_path)
    z2r = load_zone_to_room(spatial_model_path)
    walks = identify_walks(events, z2r)
    predictions = collect_predictions(events)

    if not events:
        return {"error": "no events"}
    total_observed_s = (events[-1].get("ts", 0) - events[0].get("ts", 0))

    # Per-walk brightness across τ sweep
    walk_results = []
    for w in walks:
        if w.excluded_reason or w.target_room is None:
            walk_results.append({
                "walk": w,
                "brightness_by_tau": {},
                "flap_count": 0,
                "skipped_reason": w.excluded_reason or "orphan",
            })
            continue
        bri_by_tau = {}
        flap_count = None
        for tau in taus:
            r = replay_brightness(predictions, w, tau,
                                  anticipated_pct, vacant_floor)
            bri_by_tau[tau] = r["brightness_at_arrival"]
            flap_count = r["flap_count"]  # tau-invariant; same predictions
        walk_results.append({
            "walk": w,
            "brightness_by_tau": bri_by_tau,
            "flap_count": flap_count,
        })

    # Effective recall per τ. Threshold is VACANT_DAY_PCT — the anchor the
    # plan committed to. "Did the prediction make brightness-at-arrival at
    # least as bright as the lights would have been WITHOUT any prediction
    # in a daytime-vacant room?" Same threshold regardless of vacant_floor
    # so the metric stays comparable across day/night sim runs.
    scored_walks = [r for r in walk_results if not r.get("skipped_reason")]
    recall_by_tau = {}
    for tau in taus:
        if not scored_walks:
            recall_by_tau[tau] = 0.0
            continue
        hits = sum(1 for r in scored_walks
                   if r["brightness_by_tau"][tau] >= VACANT_DAY_PCT)
        recall_by_tau[tau] = round(hits / len(scored_walks), 4)

    # Wrong-room-during-walk: for each scored walk, did any
    # anticipated_<other_room> fire ON within the walk window?
    wrong_room_count = 0
    for r in scored_walks:
        w = r["walk"]
        win_start = w.source_off_ts - WALK_PRE_WINDOW_S
        win_end = w.target_on_ts
        for p in predictions:
            t = p.get("ts", 0)
            if not (win_start <= t <= win_end):
                continue
            if str(p.get("state")).upper() == "ON" \
                    and p.get("room") != w.target_room:
                wrong_room_count += 1
                break
    wrong_room_rate = (wrong_room_count / max(len(scored_walks), 1))

    # Predictability ceiling: % person events with path_data_len_s >= 2.0
    debug_records = [e for e in events if e.get("source") == "mqtt_debug"]
    pre_exit = [e for e in debug_records
                if (e.get("body") or {}).get("path_data_len_s") is not None]
    ready = sum(1 for e in pre_exit
                if (e["body"]["path_data_len_s"] or 0) >= 2.0)
    predictability_ceiling = (ready / max(len(pre_exit), 1)) if pre_exit else 0.0

    # Flap-weighted stability — avg flaps per scored walk
    if scored_walks:
        mean_flap = sum(r["flap_count"] or 0 for r in scored_walks) \
                    / len(scored_walks)
    else:
        mean_flap = 0.0

    # Spurious-light-minutes
    spurious = spurious_minutes_per_day(predictions, walks, total_observed_s)

    # Bucket counts
    bucket_counts = Counter()
    for w in walks:
        bucket_counts[w.bucket if not w.excluded_reason
                      else f"excluded_{w.excluded_reason}"] += 1

    return {
        "events_loaded": len(events),
        "days_observed": spurious["days_observed"],
        "walks_total": len(walks),
        "walks_scored": len(scored_walks),
        "bucket_counts": dict(bucket_counts),
        "recall_by_tau": recall_by_tau,
        "wrong_room_rate": round(wrong_room_rate, 4),
        "wrong_room_count": wrong_room_count,
        "flap_weighted_stability_mean": round(mean_flap, 3),
        "predictability_ceiling": round(predictability_ceiling, 4),
        "predictability_sample_n": len(pre_exit),
        "spurious_minutes_per_day": spurious["spurious_minutes_per_day"],
        "spurious_seconds_total": spurious["spurious_seconds_total"],
        "walk_results": walk_results,
    }


def render_report(analysis: dict, taus: list[float] = TAUS_DEFAULT) -> str:
    """Render a markdown report from the analysis dict."""
    def _classify(metric: str, value: float) -> str:
        bands = {
            "recall": [(0.60, "🟢"), (0.40, "🟡")],   # else 🔴
            "tau_robust": [(10.0, "🟢"), (25.0, "🟡")],
            "spurious_min": [(5.0, "🟢"), (20.0, "🟡")],
            "wrong_room": [(0.10, "🟢"), (0.25, "🟡")],
            "flap": [(1.5, "🟢"), (3.0, "🟡")],
        }
        b = bands[metric]
        if metric in ("recall",):
            # higher is better
            if value >= b[0][0]: return b[0][1]
            if value >= b[1][0]: return b[1][1]
            return "🔴"
        else:
            # lower is better
            if value <= b[0][0]: return b[0][1]
            if value <= b[1][0]: return b[1][1]
            return "🔴"

    recall = analysis["recall_by_tau"]
    recall_tau15 = recall.get(1.5, 0)
    tau_robust = max(recall.values()) - min(recall.values()) if recall else 0
    spurious = analysis["spurious_minutes_per_day"]
    wrong_room = analysis["wrong_room_rate"]
    flap = analysis["flap_weighted_stability_mean"]

    lines: list[str] = []
    lines.append("# Eval-anticipator report")
    lines.append("")
    lines.append(f"- Events loaded: {analysis['events_loaded']:,}")
    lines.append(f"- Observation window: {analysis['days_observed']:.2f} days")
    lines.append(f"- Walks identified: {analysis['walks_total']} "
                 f"(scored: {analysis['walks_scored']})")
    lines.append("")
    lines.append("## Go-gate dashboard")
    lines.append("")
    lines.append("| Metric | Value | Verdict |")
    lines.append("|---|---|---|")
    lines.append(f"| Effective recall (τ=1.5) | {recall_tau15:.1%} | "
                 f"{_classify('recall', recall_tau15)} |")
    lines.append(f"| τ-robustness (max recall delta across τ) | "
                 f"{tau_robust*100:.1f} pp | "
                 f"{_classify('tau_robust', tau_robust*100)} |")
    lines.append(f"| Spurious-light-minutes / day | {spurious:.2f} | "
                 f"{_classify('spurious_min', spurious)} |")
    lines.append(f"| Wrong-room-during-walk rate | {wrong_room:.1%} | "
                 f"{_classify('wrong_room', wrong_room)} |")
    lines.append(f"| Flap-weighted stability (changes/walk) | {flap:.2f} | "
                 f"{_classify('flap', flap)} |")
    lines.append("")
    lines.append("**Calibration note**: brightness-at-arrival is computed "
                 "under a simplified linear ramp model that ignores "
                 "`adaptive_lighting`, manual_override cooldowns, "
                 "`pass_through` debounce, and per-bulb non-linearity. The "
                 "absolute recall number is UNCALIBRATED. The trustworthy "
                 "signal is the τ-robustness row, not the headline %.")
    lines.append("")
    lines.append("## τ-sweep")
    lines.append("")
    lines.append("| τ (s) | Effective recall |")
    lines.append("|---|---|")
    for tau in taus:
        lines.append(f"| {tau} | {recall.get(tau, 0):.1%} |")
    lines.append("")
    lines.append("## Walk buckets")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("|---|---|")
    for bucket, count in sorted(analysis["bucket_counts"].items()):
        lines.append(f"| {bucket} | {count} |")
    lines.append("")
    lines.append("## Auxiliary")
    lines.append("")
    lines.append(f"- Predictability ceiling: "
                 f"{analysis['predictability_ceiling']:.1%} "
                 f"(n={analysis['predictability_sample_n']:,})")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events", default="C:/Claude/home/tmp/eval-anticipator.jsonl")
    ap.add_argument("--spatial",
                    default="C:/Claude/home/tmp/spatial_model_deployed.json")
    ap.add_argument("--out", default="C:/Claude/home/tools/eval-anticipator-data/report.md")
    # The "anticipated" state's brightness target. 50 == day-vacant-floor →
    # no-op at day; at NIGHT the user goes from 12 (vacant_night) up to 50.
    ap.add_argument("--anticipated-pct", type=float, default=50.0)
    # Default simulates the NIGHT case (vacant=12) — when anticipation has
    # the most room to lift brightness and is the user-perceptible scenario.
    # For day-eval, pass --vacant-floor 50 (anticipation is a no-op at day).
    ap.add_argument("--vacant-floor", type=float, default=VACANT_NIGHT_PCT)
    args = ap.parse_args()
    result = analyze(args.events, args.spatial,
                     anticipated_pct=args.anticipated_pct,
                     vacant_floor=args.vacant_floor)
    if "error" in result:
        print(f"ERROR: {result['error']}")
        raise SystemExit(1)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(result), encoding="utf-8")
    print(f"WROTE {out_path}")
    print(render_report(result))
