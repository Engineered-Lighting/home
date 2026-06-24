from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _row_ts(row: sqlite3.Row | dict[str, Any], key: str = "ts") -> datetime | None:
    try:
        return _parse_ts(row[key])
    except (KeyError, IndexError):
        return None


def _in_window(
    row: sqlite3.Row | dict[str, Any],
    *,
    start: datetime,
    end: datetime,
    key: str = "ts",
) -> bool:
    ts = _row_ts(row, key)
    return ts is not None and start <= ts <= end


def _observation_context(row: sqlite3.Row) -> dict[str, Any]:
    return _json_loads(row["context_json"], {})


def _capture_provenance(row: sqlite3.Row) -> dict[str, Any]:
    return _json_loads(row["capture_provenance_json"], {})


def _prediction_consistency(row: sqlite3.Row) -> dict[str, Any]:
    return _json_loads(row["prediction_consistency_json"], {})


def _occupancy(row: sqlite3.Row) -> dict[str, Any]:
    return _json_loads(row["occupancy_json"], {})


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _compact_observation(row: sqlite3.Row) -> dict[str, Any]:
    capture = _capture_provenance(row)
    consistency = _prediction_consistency(row)
    occupancy = _occupancy(row)
    return {
        "id": row["id"],
        "kind": row["kind"],
        "ts": row["ts"],
        "zone": row["zone"],
        "profile": row["profile"],
        "state": row["state"],
        "light_entity": row["light_entity"],
        "light_state": row["light_state"],
        "actual_brightness_pct": row["actual_brightness_pct"],
        "predicted_brightness_pct": row["predicted_brightness_pct"],
        "delta_pct": row["delta_pct"],
        "shadow_mode": bool(row["shadow_mode"]),
        "context_id": row["context_id"],
        "evidence_id": row["evidence_id"],
        "dedupe_key": row["dedupe_key"],
        "dedupe_window_s": row["dedupe_window_s"],
        "capture_suppressed_count": _int(row["capture_suppressed_count"]),
        "related_override_context_id": row["related_override_context_id"],
        "capture_trigger": capture.get("capture_trigger"),
        "prediction_mismatch_pct": consistency.get("prediction_mismatch_pct"),
        "occupancy_state": occupancy.get("state"),
        "occupancy_age_s": occupancy.get("age_s"),
        "occupancy_flicker_count_5min": row["occupancy_flicker_count_5min"],
    }


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "zone": row["zone"],
        "context": _json_loads(row["context_json"], {}),
        "status": row["status"],
        "evidence_count": row["evidence_count"],
        "raw_evidence_count": row["raw_evidence_count"] if "raw_evidence_count" in row.keys() else row["evidence_count"],
        "raw_override_count": row["raw_override_count"] if "raw_override_count" in row.keys() else 0,
        "override_session_count": row["override_session_count"] if "override_session_count" in row.keys() else 0,
        "burst_event_count": row["burst_event_count"] if "burst_event_count" in row.keys() else 0,
        "max_override_burst_size": row["max_override_burst_size"] if "max_override_burst_size" in row.keys() else 0,
        "override_burst_ratio": row["override_burst_ratio"] if "override_burst_ratio" in row.keys() else 0,
        "pending_evidence_count": row["pending_evidence_count"] if "pending_evidence_count" in row.keys() else 0,
        "session_intent_count": row["session_intent_count"] if "session_intent_count" in row.keys() else 0,
        "automation_tail_session_count": row["automation_tail_session_count"] if "automation_tail_session_count" in row.keys() else 0,
        "evidence_tier": row["evidence_tier"] if "evidence_tier" in row.keys() else None,
        "supporting_evidence_count": row["supporting_evidence_count"],
        "linked_override_count": row["linked_override_count"],
        "linked_pending_count": row["linked_pending_count"],
        "capture_suppressed_total": row["capture_suppressed_total"],
        "dedupe_key_count": row["dedupe_key_count"],
        "non_shadow_count": row["non_shadow_count"],
        "days_seen": row["days_seen"],
        "median_actual_brightness_pct": row["median_actual_brightness_pct"],
        "median_predicted_brightness_pct": row["median_predicted_brightness_pct"],
        "median_delta_pct": row["median_delta_pct"],
        "delta_variance": row["delta_variance"],
        "shadow_mode_ratio": row["shadow_mode_ratio"],
        "opposite_direction_rate": row["opposite_direction_rate"],
        "duplicate_pending_ratio": row["duplicate_pending_ratio"],
        "blockers": _json_loads(row["blockers_json"], []),
        "quality_warnings": _json_loads(row["quality_warnings_json"], []),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "updated_at": row["updated_at"],
    }


def _candidate_revision_changes(
    rows: list[sqlite3.Row],
    *,
    start: datetime,
    end: datetime,
    limit: int = 12,
) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row["candidate_id"]].append(row)

    changes: list[dict[str, Any]] = []
    new_count = 0
    for candidate_id, revs in grouped.items():
        revs.sort(key=lambda r: int(r["id"]), reverse=True)
        latest = revs[0]
        latest_ts = _parse_ts(latest["created_at"])
        if latest_ts is None or not (start <= latest_ts <= end):
            continue

        latest_metrics = _json_loads(latest["metrics_json"], {})
        latest_blockers = _json_loads(latest["blockers_json"], [])
        item: dict[str, Any] = {
            "candidate_id": candidate_id,
            "created_at": latest["created_at"],
            "status": latest["status"],
            "evidence_count": latest_metrics.get("evidence_count"),
            "capture_suppressed_total": latest_metrics.get("capture_suppressed_total"),
            "duplicate_pending_ratio": latest_metrics.get("duplicate_pending_ratio"),
            "blockers": latest_blockers,
        }
        if len(revs) == 1:
            item["change_kind"] = "new"
            item["blockers_added"] = latest_blockers
            item["blockers_cleared"] = []
            item["evidence_count_delta"] = latest_metrics.get("evidence_count")
            new_count += 1
        else:
            previous = revs[1]
            previous_metrics = _json_loads(previous["metrics_json"], {})
            previous_blockers = _json_loads(previous["blockers_json"], [])
            latest_blocker_set = set(latest_blockers)
            previous_blocker_set = set(previous_blockers)
            item["change_kind"] = "updated"
            item["previous_status"] = previous["status"]
            item["blockers_added"] = sorted(latest_blocker_set - previous_blocker_set)
            item["blockers_cleared"] = sorted(previous_blocker_set - latest_blocker_set)
            item["evidence_count_delta"] = (
                _int(latest_metrics.get("evidence_count"))
                - _int(previous_metrics.get("evidence_count"))
            )
        changes.append(item)

    changes.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return changes[:limit], new_count


def _health_for_zone(zone: dict[str, Any]) -> str:
    duplicate_ratio = float(zone.get("max_duplicate_pending_ratio") or 0.0)
    pending = int(zone.get("pending_preferences") or 0)
    linked = int(zone.get("linked_episode_count") or 0)
    unlinked = int(zone.get("unlinked_episode_count") or 0)
    if duplicate_ratio > 0.6 or (pending >= 5 and unlinked > linked):
        return "needs_attention"
    if pending > 0 and int(zone.get("m4_record_count") or 0) == 0:
        return "watch"
    if pending > 0 and linked == 0:
        return "watch"
    return "ok"


def build_soak_review(conn: sqlite3.Connection, hours: float = 24.0) -> dict[str, Any]:
    """Build a read-only evidence hygiene review for the current soak window."""

    observation_rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE zone IS NOT NULL
          AND zone != 'unknown'
        ORDER BY ts
        """
    ).fetchall()
    candidate_rows = conn.execute("SELECT * FROM preference_candidates").fetchall()
    episode_rows = conn.execute(
        """
        SELECT *
        FROM episodes
        WHERE kind = 'lighting_preference'
        ORDER BY end_ts
        """
    ).fetchall()
    revision_rows = conn.execute(
        """
        SELECT *
        FROM preference_candidate_revisions
        ORDER BY id DESC
        """
    ).fetchall()

    parsed_observation_ts = [ts for ts in (_row_ts(row) for row in observation_rows) if ts is not None]
    if parsed_observation_ts:
        anchor = max(parsed_observation_ts)
    else:
        anchor = datetime.now(timezone.utc)
    window_hours = max(1.0, min(float(hours), 168.0))
    start = anchor - timedelta(hours=window_hours)
    previous_start = start - timedelta(hours=window_hours)

    current_rows = [row for row in observation_rows if _in_window(row, start=start, end=anchor)]
    previous_rows = [row for row in observation_rows if _in_window(row, start=previous_start, end=start)]

    current_overrides = [row for row in current_rows if row["kind"] == "override_event"]
    current_pending = [row for row in current_rows if row["kind"] == "pending_preference"]
    previous_overrides = [row for row in previous_rows if row["kind"] == "override_event"]
    previous_pending = [row for row in previous_rows if row["kind"] == "pending_preference"]
    m4_rows = [
        row for row in current_pending
        if row["evidence_id"] or row["dedupe_key"] or row["related_override_context_id"]
    ]

    zones: dict[str, dict[str, Any]] = {}

    def zone_state(zone_name: str) -> dict[str, Any]:
        item = zones.setdefault(
            zone_name,
            {
                "zone": zone_name,
                "observations": 0,
                "overrides": 0,
                "pending_preferences": 0,
                "m4_record_count": 0,
                "capture_suppressed_total": 0,
                "dedupe_keys": set(),
                "linked_episode_count": 0,
                "direct_link_episode_count": 0,
                "heuristic_link_episode_count": 0,
                "unlinked_episode_count": 0,
                "ready_candidates": 0,
                "blocked_candidates": 0,
                "candidate_count": 0,
                "raw_override_count": 0,
                "override_session_count": 0,
                "burst_event_count": 0,
                "max_override_burst_size": 0,
                "pending_evidence_count": 0,
                "session_intent_count": 0,
                "automation_tail_session_count": 0,
                "top_blockers": Counter(),
                "max_duplicate_pending_ratio": 0.0,
                "latest_pending": None,
                "latest_natural_pending": None,
                "latest_manual_pending": None,
                "latest_override": None,
            },
        )
        return item

    for row in current_rows:
        zone = row["zone"]
        item = zone_state(zone)
        item["observations"] += 1
        if row["kind"] == "override_event":
            item["overrides"] += 1
            compact = _compact_observation(row)
            existing = item.get("latest_override")
            if existing is None or (_parse_ts(compact["ts"]) or anchor) > (_parse_ts(existing["ts"]) or previous_start):
                item["latest_override"] = compact
        elif row["kind"] == "pending_preference":
            item["pending_preferences"] += 1
            item["capture_suppressed_total"] += _int(row["capture_suppressed_count"])
            if row["evidence_id"] or row["dedupe_key"] or row["related_override_context_id"]:
                item["m4_record_count"] += 1
            if row["dedupe_key"]:
                item["dedupe_keys"].add(row["dedupe_key"])
            compact = _compact_observation(row)
            existing = item.get("latest_pending")
            if existing is None or (_parse_ts(compact["ts"]) or anchor) > (_parse_ts(existing["ts"]) or previous_start):
                item["latest_pending"] = compact
            trigger = compact.get("capture_trigger")
            if trigger == "vacancy_settle":
                existing = item.get("latest_natural_pending")
                if existing is None or (_parse_ts(compact["ts"]) or anchor) > (_parse_ts(existing["ts"]) or previous_start):
                    item["latest_natural_pending"] = compact
            elif trigger == "manual_automation_trigger":
                existing = item.get("latest_manual_pending")
                if existing is None or (_parse_ts(compact["ts"]) or anchor) > (_parse_ts(existing["ts"]) or previous_start):
                    item["latest_manual_pending"] = compact

    episode_link_counts = Counter()
    for episode in episode_rows:
        if not _in_window(episode, start=start, end=anchor, key="end_ts"):
            continue
        zone = episode["zone"]
        item = zone_state(zone)
        summary = _json_loads(episode["summary_json"], {})
        link_method = summary.get("link_method") or "unlinked"
        if link_method == "related_override_context_id":
            item["linked_episode_count"] += 1
            item["direct_link_episode_count"] += 1
            episode_link_counts["direct"] += 1
        elif link_method == "heuristic":
            item["linked_episode_count"] += 1
            item["heuristic_link_episode_count"] += 1
            episode_link_counts["heuristic"] += 1
        else:
            item["unlinked_episode_count"] += 1
            episode_link_counts["unlinked"] += 1

    candidates = [_candidate(row) for row in candidate_rows]
    for candidate in candidates:
        item = zone_state(candidate["zone"])
        item["candidate_count"] += 1
        if candidate["status"] == "candidate":
            item["ready_candidates"] += 1
        else:
            item["blocked_candidates"] += 1
        for blocker in candidate.get("blockers") or []:
            item["top_blockers"][blocker] += 1
        item["raw_override_count"] += _int(candidate.get("raw_override_count"))
        item["override_session_count"] += _int(candidate.get("override_session_count"))
        item["burst_event_count"] += _int(candidate.get("burst_event_count"))
        item["max_override_burst_size"] = max(
            item["max_override_burst_size"],
            _int(candidate.get("max_override_burst_size")),
        )
        item["pending_evidence_count"] += _int(candidate.get("pending_evidence_count"))
        item["session_intent_count"] += _int(candidate.get("session_intent_count"))
        item["automation_tail_session_count"] += _int(candidate.get("automation_tail_session_count"))
        duplicate_ratio = _num(candidate.get("duplicate_pending_ratio")) or 0.0
        item["max_duplicate_pending_ratio"] = max(
            item["max_duplicate_pending_ratio"],
            duplicate_ratio,
        )

    zone_out: list[dict[str, Any]] = []
    health_order = {"needs_attention": 0, "watch": 1, "ok": 2}
    for item in zones.values():
        item["dedupe_key_count"] = len(item.pop("dedupe_keys"))
        item["top_blockers"] = [
            {"blocker": blocker, "count": count}
            for blocker, count in item["top_blockers"].most_common(5)
        ]
        item["health"] = _health_for_zone(item)
        zone_out.append(item)
    zone_out.sort(
        key=lambda item: (
            health_order.get(item["health"], 9),
            -int(item.get("observations") or 0),
            item["zone"],
        )
    )

    latest_pending = sorted(
        (_compact_observation(row) for row in current_pending),
        key=lambda item: item.get("ts") or "",
        reverse=True,
    )[:8]
    latest_overrides = sorted(
        (_compact_observation(row) for row in current_overrides),
        key=lambda item: item.get("ts") or "",
        reverse=True,
    )[:8]
    candidate_changes, new_candidate_count = _candidate_revision_changes(
        revision_rows,
        start=start,
        end=anchor,
    )

    ready_candidates = sum(1 for row in candidate_rows if row["status"] == "candidate")
    blocked_candidates = sum(1 for row in candidate_rows if row["status"] == "blocked")
    blocker_counts = Counter()
    for candidate in candidates:
        for blocker in candidate.get("blockers") or []:
            blocker_counts[blocker] += 1

    summary = {
        "observation_count": len(current_rows),
        "override_count": len(current_overrides),
        "pending_count": len(current_pending),
        "m4_record_count": len(m4_rows),
        "capture_suppressed_total": sum(_int(row["capture_suppressed_count"]) for row in current_pending),
        "linked_episode_count": episode_link_counts["direct"] + episode_link_counts["heuristic"],
        "direct_link_episode_count": episode_link_counts["direct"],
        "heuristic_link_episode_count": episode_link_counts["heuristic"],
        "unlinked_episode_count": episode_link_counts["unlinked"],
        "zone_count": len(zone_out),
        "candidate_count": len(candidate_rows),
        "ready_candidate_count": ready_candidates,
        "blocked_candidate_count": blocked_candidates,
        "burst_event_count": sum(_int(candidate.get("burst_event_count")) for candidate in candidates),
        "override_session_count": sum(_int(candidate.get("override_session_count")) for candidate in candidates),
        "pending_evidence_count": sum(_int(candidate.get("pending_evidence_count")) for candidate in candidates),
        "session_intent_count": sum(_int(candidate.get("session_intent_count")) for candidate in candidates),
        "automation_tail_session_count": sum(_int(candidate.get("automation_tail_session_count")) for candidate in candidates),
        "top_blockers": [
            {"blocker": blocker, "count": count}
            for blocker, count in blocker_counts.most_common(8)
        ],
    }
    change = {
        "observation_delta": len(current_rows) - len(previous_rows),
        "override_delta": len(current_overrides) - len(previous_overrides),
        "pending_delta": len(current_pending) - len(previous_pending),
        "new_candidate_count": new_candidate_count,
        "candidate_revision_count": len(candidate_changes),
    }

    return {
        "ok": True,
        "hours": window_hours,
        "anchor_ts": _iso(anchor),
        "window": {"start": _iso(start), "end": _iso(anchor)},
        "previous_window": {"start": _iso(previous_start), "end": _iso(start)},
        "summary": summary,
        "change": change,
        "zones": zone_out,
        "candidate_changes": candidate_changes,
        "latest_pending_preferences": latest_pending,
        "latest_overrides": latest_overrides,
    }
