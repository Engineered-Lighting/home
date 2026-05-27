from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .ingest import stable_hash


OVERRIDE_SESSION_GAP_SECONDS = 10.0


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_get(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    """Safe column access. sqlite3.Row raises IndexError on missing
    columns; dict raises KeyError. MC.1 added `gaming_active` post-
    deployment, so historical fixtures / pre-migration rows may not
    carry the column. Treat absence as None so the dedupe key still
    groups historical rows together (None == None)."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def policy_context(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": row["tv_playing"],
        # MC.1 — gaming_active context dimension. Defensive read against
        # pre-MC.1 fixtures / rows that don't carry the column.
        "gaming_active": _row_get(row, "gaming_active"),
        "asleep": row["asleep"],
        "night_safe": row["night_safe"],
        "user_at_home": row["user_at_home"],
        "light_state": row["light_state"],
    }


def session_key(row: sqlite3.Row | dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["zone"],
        row["light_entity"],
        row["profile"],
        row["state"],
        row["tv_playing"],
        # MC.1 — gaming_active joins the session key so override events
        # during a Steam game don't fold into the same session as non-
        # gaming events with otherwise-identical context. Defensive read
        # for pre-MC.1 rows (None groups with None — historical sessions
        # remain coherent).
        _row_get(row, "gaming_active"),
        row["asleep"],
        row["night_safe"],
        row["user_at_home"],
    )


def _same_policy_context(row: sqlite3.Row, ctx: dict[str, Any]) -> bool:
    return policy_context(row) == ctx


def compact_override(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ts": row["ts"],
        "zone": row["zone"],
        "light_entity": row["light_entity"],
        "light_state": row["light_state"],
        "actual_brightness_pct": row["actual_brightness_pct"],
        "predicted_brightness_pct": row["predicted_brightness_pct"],
        "delta_pct": row["delta_pct"],
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": row["tv_playing"],
        "gaming_active": _row_get(row, "gaming_active"),
        "asleep": row["asleep"],
        "night_safe": row["night_safe"],
        "user_at_home": row["user_at_home"],
        "shadow_mode": bool(row["shadow_mode"]),
        "context_id": row["context_id"],
    }


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_off_event(row: sqlite3.Row | dict[str, Any]) -> bool:
    actual = _num(row["actual_brightness_pct"])
    return row["light_state"] == "off" or actual == 0


def _intent_for_session(session: list[sqlite3.Row]) -> dict[str, Any]:
    final = session[-1]
    path = [_num(row["actual_brightness_pct"]) for row in session]
    final_actual = _num(final["actual_brightness_pct"])

    tail_start = len(session)
    while tail_start > 0 and _is_off_event(session[tail_start - 1]):
        tail_start -= 1
    positive_before_tail = [
        (idx, row)
        for idx, row in enumerate(session[:tail_start])
        if (_num(row["actual_brightness_pct"]) or 0) > 0
    ]
    automation_tail = bool(
        tail_start < len(session)
        and len(positive_before_tail) >= 2
        and _is_off_event(final)
    )

    if automation_tail:
        landed_idx, landed = positive_before_tail[-1]
        landed_actual = _num(landed["actual_brightness_pct"])
        tail_path = path[tail_start:]
        previous_actual = (
            _num(positive_before_tail[-2][1]["actual_brightness_pct"])
            if len(positive_before_tail) >= 2
            else None
        )
        confidence = "high" if landed_actual == previous_actual else "medium"
        learning_source = "user_landed"
    else:
        landed_idx = len(session) - 1
        landed = final
        landed_actual = final_actual
        tail_path = []
        confidence = "high" if len(session) == 1 or final_actual == 0 else "medium"
        learning_source = "off_session" if _is_off_event(final) else "final_actual"

    return {
        "learning_event": landed,
        "learning_event_index": landed_idx,
        "session_final_actual_pct": final_actual,
        "session_user_landed_pct": landed_actual,
        "automation_tail_detected": automation_tail,
        "session_tail_path": tail_path,
        "session_observed_path": path,
        "intent_confidence": confidence,
        "learning_value_pct": landed_actual,
        "learning_value_source": learning_source,
        "learning_policy_context": policy_context(landed),
    }


def _effective_session_row(session: list[sqlite3.Row]) -> dict[str, Any]:
    intent = _intent_for_session(session)
    base = dict(intent["learning_event"])
    learning_value = intent["learning_value_pct"]
    predicted = _num(base.get("predicted_brightness_pct"))
    base["actual_brightness_pct"] = learning_value
    if learning_value == 0:
        base["light_state"] = "off"
    elif base.get("light_state") == "off":
        base["light_state"] = "on"
    if learning_value is not None and predicted is not None:
        base["delta_pct"] = learning_value - predicted
    base["session_final_actual_pct"] = intent["session_final_actual_pct"]
    base["session_user_landed_pct"] = intent["session_user_landed_pct"]
    base["automation_tail_detected"] = intent["automation_tail_detected"]
    base["learning_value_pct"] = intent["learning_value_pct"]
    base["learning_value_source"] = intent["learning_value_source"]
    base["session_event_count"] = len(session)
    base["session_first_ts"] = session[0]["ts"]
    base["session_last_ts"] = session[-1]["ts"]
    base["override_session_summary"] = session_summary(session, include_events=False)
    return base


def build_override_sessions(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    overrides = [row for row in rows if row["kind"] == "override_event"]
    by_key: dict[tuple[Any, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in overrides:
        by_key[session_key(row)].append(row)

    sessions: list[list[sqlite3.Row]] = []
    for bucket in by_key.values():
        bucket.sort(key=lambda row: parse_ts(row["ts"]) or datetime.min.replace(tzinfo=timezone.utc))
        current: list[sqlite3.Row] = []
        last_ts: datetime | None = None
        for row in bucket:
            ts = parse_ts(row["ts"])
            same_session = (
                bool(current)
                and ts is not None
                and last_ts is not None
                and 0 <= (ts - last_ts).total_seconds() <= OVERRIDE_SESSION_GAP_SECONDS
            )
            if not current or same_session:
                current.append(row)
            else:
                sessions.append(current)
                current = [row]
            last_ts = ts
        if current:
            sessions.append(current)
    sessions.sort(key=lambda session: parse_ts(session[-1]["ts"]) or datetime.min.replace(tzinfo=timezone.utc))
    return sessions


def session_summary(session: list[sqlite3.Row], *, include_events: bool = False) -> dict[str, Any]:
    first = session[0]
    representative = session[-1]
    intent = _intent_for_session(session)
    first_ts = parse_ts(first["ts"])
    last_ts = parse_ts(representative["ts"])
    duration_s = (
        max(0.0, (last_ts - first_ts).total_seconds())
        if first_ts is not None and last_ts is not None
        else None
    )
    summary = {
        "id": "ovsession:" + stable_hash(
            "|".join([representative["id"], first["ts"] or "", str(len(session))])
        )[:24],
        "zone": representative["zone"],
        "light_entity": representative["light_entity"],
        "first_ts": first["ts"],
        "last_ts": representative["ts"],
        "duration_s": duration_s,
        "size": len(session),
        "burst_event_count": max(0, len(session) - 1),
        "session_gap_s": OVERRIDE_SESSION_GAP_SECONDS,
        "policy_context": policy_context(representative),
        "learning_policy_context": intent["learning_policy_context"],
        "first_actual_brightness_pct": first["actual_brightness_pct"],
        "final_actual_brightness_pct": representative["actual_brightness_pct"],
        "final_predicted_brightness_pct": representative["predicted_brightness_pct"],
        "final_delta_pct": representative["delta_pct"],
        "session_final_actual_pct": intent["session_final_actual_pct"],
        "session_user_landed_pct": intent["session_user_landed_pct"],
        "automation_tail_detected": intent["automation_tail_detected"],
        "session_tail_path": intent["session_tail_path"],
        "session_observed_path": intent["session_observed_path"],
        "intent_confidence": intent["intent_confidence"],
        "learning_value_pct": intent["learning_value_pct"],
        "learning_value_source": intent["learning_value_source"],
        "representative": compact_override(representative),
        "learning_representative": compact_override(intent["learning_event"]),
        "event_ids": [row["id"] for row in session],
    }
    if include_events:
        summary["events"] = [compact_override(row) for row in session]
    return summary


def collapse_override_bursts(
    rows: list[sqlite3.Row],
) -> tuple[list[sqlite3.Row], dict[str, Any]]:
    overrides = [row for row in rows if row["kind"] == "override_event"]
    pending = [row for row in rows if row["kind"] != "override_event"]
    if not overrides:
        return rows, {
            "raw_evidence_count": len(rows),
            "raw_override_count": 0,
            "override_session_count": 0,
            "burst_event_count": 0,
            "max_override_burst_size": 0,
            "override_burst_ratio": 0.0,
            "sessions": [],
        }

    sessions = build_override_sessions(overrides)
    representatives = [_effective_session_row(session) for session in sessions]
    effective = sorted(
        [*pending, *representatives],
        key=lambda row: parse_ts(row["ts"]) or datetime.min.replace(tzinfo=timezone.utc),
    )
    burst_event_count = sum(max(0, len(session) - 1) for session in sessions)
    raw_override_count = len(overrides)
    return effective, {
        "raw_evidence_count": len(rows),
        "raw_override_count": raw_override_count,
        "override_session_count": len(sessions),
        "burst_event_count": burst_event_count,
        "max_override_burst_size": max((len(session) for session in sessions), default=0),
        "override_burst_ratio": (
            burst_event_count / raw_override_count if raw_override_count else 0.0
        ),
        "session_gap_s": OVERRIDE_SESSION_GAP_SECONDS,
        "sessions": [
            session_summary(session, include_events=False)
            for session in sessions
            if len(session) > 1
        ][:5],
    }


def candidate_override_sessions(
    conn: sqlite3.Connection,
    candidate: dict[str, Any],
    *,
    include_events: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    ctx = candidate.get("context") or {}
    rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE kind = 'override_event'
          AND zone = ?
        ORDER BY ts
        """,
        (candidate["zone"],),
    ).fetchall()
    sessions = [
        session
        for session in build_override_sessions(rows)
        if session_summary(session).get("learning_policy_context") == ctx
        or session_summary(session).get("policy_context") == ctx
    ]
    out = [
        session_summary(session, include_events=include_events)
        for session in sessions
    ]
    out.sort(key=lambda item: item.get("last_ts") or "", reverse=True)
    return out[:limit]


def list_recent_override_sessions(
    conn: sqlite3.Connection,
    *,
    zone: str | None = None,
    hours: float = 24.0,
    limit: int = 50,
    include_events: bool = False,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE kind = 'override_event'
          AND zone IS NOT NULL
          AND zone != 'unknown'
        ORDER BY ts
        """
    ).fetchall()
    parsed = [ts for ts in (parse_ts(row["ts"]) for row in rows) if ts is not None]
    anchor = max(parsed) if parsed else datetime.now(timezone.utc)
    start = anchor - timedelta(hours=max(1.0, min(float(hours), 168.0)))
    filtered = []
    for row in rows:
        ts = parse_ts(row["ts"])
        if ts is None or not (start <= ts <= anchor):
            continue
        if zone and row["zone"] != zone:
            continue
        filtered.append(row)

    sessions = build_override_sessions(filtered)
    summaries = [session_summary(session, include_events=include_events) for session in sessions]
    summaries.sort(key=lambda item: item.get("last_ts") or "", reverse=True)
    burst_sessions = [item for item in summaries if int(item.get("size") or 0) > 1]
    return {
        "ok": True,
        "hours": hours,
        "zone": zone,
        "window": {
            "start": start.isoformat(),
            "end": anchor.isoformat(),
        },
        "summary": {
            "raw_override_count": len(filtered),
            "override_session_count": len(summaries),
            "burst_session_count": len(burst_sessions),
            "burst_event_count": sum(int(item.get("burst_event_count") or 0) for item in summaries),
            "max_burst_size": max((int(item.get("size") or 0) for item in summaries), default=0),
        },
        "sessions": summaries[: max(1, min(int(limit), 200))],
    }
