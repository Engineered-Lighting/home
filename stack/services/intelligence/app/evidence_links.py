from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .override_sessions import build_override_sessions, parse_ts, session_summary


LINK_LOOKBACK_SECONDS = 30 * 60
BRIGHTNESS_GAP_PCT = 1.0


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _brightness_gap(left: Any, right: Any) -> float | None:
    l_num = _num(left)
    r_num = _num(right)
    if l_num is None or r_num is None:
        return None
    return l_num - r_num


def _audit_status(flags: list[str]) -> str:
    if "automation_tail_capture_gap" in flags:
        return "reconciled"
    if "related_context_not_found" in flags or "missing_related_override_context_id" in flags:
        return "unlinked"
    return "linked"


def _compact_session(match: dict[str, Any]) -> dict[str, Any]:
    summary = match["summary"]
    learning = summary.get("learning_representative") or {}
    representative = summary.get("representative") or {}
    return {
        "id": summary.get("id"),
        "match_role": match.get("role"),
        "match_index": match.get("index"),
        "first_ts": summary.get("first_ts"),
        "last_ts": summary.get("last_ts"),
        "session_event_count": summary.get("size"),
        "session_first_context_id": match.get("first_context_id"),
        "session_last_context_id": representative.get("context_id"),
        "learning_context_id": learning.get("context_id"),
        "automation_tail_detected": bool(summary.get("automation_tail_detected")),
        "learning_value_pct": summary.get("learning_value_pct"),
        "learning_value_source": summary.get("learning_value_source"),
        "session_user_landed_pct": summary.get("session_user_landed_pct"),
        "session_final_actual_pct": summary.get("session_final_actual_pct"),
        "session_observed_path": summary.get("session_observed_path") or [],
        "session_tail_path": summary.get("session_tail_path") or [],
        "intent_confidence": summary.get("intent_confidence"),
    }


def _session_lookup(overrides: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for session in build_override_sessions(overrides):
        summary = session_summary(session, include_events=False)
        last_index = len(session) - 1
        context_ids = [
            str(row["context_id"])
            for row in session
            if "context_id" in row.keys() and row["context_id"]
        ]
        for idx, row in enumerate(session):
            context_id = _row_value(row, "context_id")
            if not context_id:
                continue
            if idx == 0 and idx == last_index:
                role = "only"
            elif idx == 0:
                role = "first"
            elif idx == last_index:
                role = "last"
            else:
                role = "intermediate"
            lookup[str(context_id)] = {
                "summary": summary,
                "role": role,
                "index": idx,
                "first_context_id": context_ids[0] if context_ids else None,
            }
    return lookup


def _audit_pending(row: sqlite3.Row, lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    related = _row_value(row, "related_override_context_id")
    captured = _num(_row_value(row, "actual_brightness_pct"))
    predicted = _num(_row_value(row, "predicted_brightness_pct"))
    flags: list[str] = []
    match = lookup.get(str(related)) if related else None
    session = None
    learning_value = captured
    learning_source = "captured_state"
    if not related:
        flags.append("missing_related_override_context_id")
    elif not match:
        flags.append("related_context_not_found")
    else:
        summary = match["summary"]
        session = _compact_session(match)
        if summary.get("automation_tail_detected") and summary.get("learning_value_pct") is not None:
            learning_value = _num(summary.get("learning_value_pct"))
            learning_source = "session_learning_value"
            gap = _brightness_gap(learning_value, captured)
            if gap is not None and abs(gap) > BRIGHTNESS_GAP_PCT:
                flags.append("automation_tail_capture_gap")
        elif summary.get("learning_value_pct") is not None:
            learning_value = captured
            learning_source = "captured_state"

    learning_delta = None
    if learning_value is not None and predicted is not None:
        learning_delta = learning_value - predicted
    return {
        "pending_observation_id": row["id"],
        "evidence_id": _row_value(row, "evidence_id"),
        "ts": _row_value(row, "ts"),
        "zone": _row_value(row, "zone"),
        "light_entity": _row_value(row, "light_entity"),
        "related_override_context_id": related,
        "captured_actual_brightness_pct": captured,
        "captured_predicted_brightness_pct": predicted,
        "captured_delta_pct": _num(_row_value(row, "delta_pct")),
        "aggregation_value_pct": learning_value,
        "aggregation_delta_pct": learning_delta,
        "aggregation_value_source": learning_source,
        "captured_vs_aggregation_gap_pct": _brightness_gap(learning_value, captured),
        "status": _audit_status(flags),
        "flags": flags,
        "matched_session": session,
    }


def build_evidence_link_audit(
    conn: sqlite3.Connection,
    *,
    hours: float = 24.0,
    zone: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE kind IN ('override_event', 'pending_preference')
          AND zone IS NOT NULL
          AND zone != 'unknown'
        ORDER BY ts
        """
    ).fetchall()
    parsed = [ts for ts in (parse_ts(row["ts"]) for row in rows) if ts is not None]
    anchor = max(parsed) if parsed else datetime.now(timezone.utc)
    window_hours = max(1.0, min(float(hours), 168.0))
    start = anchor - timedelta(hours=window_hours)
    override_start = start - timedelta(seconds=LINK_LOOKBACK_SECONDS)
    pending_rows: list[sqlite3.Row] = []
    override_rows: list[sqlite3.Row] = []
    for row in rows:
        ts = parse_ts(row["ts"])
        if ts is None:
            continue
        if zone and row["zone"] != zone:
            continue
        if row["kind"] == "pending_preference" and start <= ts <= anchor:
            pending_rows.append(row)
        elif row["kind"] == "override_event" and override_start <= ts <= anchor:
            override_rows.append(row)

    lookup = _session_lookup(override_rows)
    audited = [_audit_pending(row, lookup) for row in pending_rows]
    audited.sort(key=lambda item: item.get("ts") or "", reverse=True)
    match_roles: dict[str, int] = {}
    for item in audited:
        role = ((item.get("matched_session") or {}).get("match_role")) or "none"
        match_roles[role] = match_roles.get(role, 0) + 1
    summary = {
        "pending_count": len(audited),
        "linked_count": sum(1 for item in audited if item["status"] in {"linked", "reconciled"}),
        "unlinked_count": sum(1 for item in audited if item["status"] == "unlinked"),
        "automation_tail_linked_count": sum(
            1
            for item in audited
            if (item.get("matched_session") or {}).get("automation_tail_detected")
        ),
        "reconciled_count": sum(1 for item in audited if item["status"] == "reconciled"),
        "capture_gap_count": sum(
            1
            for item in audited
            if "automation_tail_capture_gap" in (item.get("flags") or [])
        ),
        "missing_related_override_context_id": sum(
            1
            for item in audited
            if "missing_related_override_context_id" in (item.get("flags") or [])
        ),
        "related_context_not_found": sum(
            1
            for item in audited
            if "related_context_not_found" in (item.get("flags") or [])
        ),
        "match_roles": match_roles,
    }
    return {
        "ok": True,
        "hours": window_hours,
        "zone": zone,
        "window": {"start": start.isoformat(), "end": anchor.isoformat()},
        "summary": summary,
        "items": audited[: max(1, min(int(limit), 500))],
    }
