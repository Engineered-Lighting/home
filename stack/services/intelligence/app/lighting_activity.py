from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .ingest import canonical_json, stable_hash, utc_now


EPISODE_GAP_SECONDS = 10.0


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


def _decode(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _state_from_row(row: sqlite3.Row, prefix: str) -> dict[str, Any]:
    return {
        "state": row[f"{prefix}_state"],
        "brightness_pct": row[f"{prefix}_brightness_pct"],
        "color_temp_kelvin": row[f"{prefix}_color_temp_kelvin"],
    }


def _path_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "ts": row["ts"],
        "trigger_attribute": row["trigger_attribute"],
        "from": _state_from_row(row, "from"),
        "to": _state_from_row(row, "to"),
        "source_hint": row["source_hint"],
        "source_confidence": row["source_confidence"],
        "command_id": row["command_id"],
    }


def _command_lookup(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM lighting_command_events WHERE command_id IS NOT NULL").fetchall()
    return {row["command_id"]: row for row in rows if row["command_id"]}


def _predicted_brightness(row: sqlite3.Row) -> float | None:
    payload = _decode(row["payload_json"], {})
    predicted = payload.get("predicted") if isinstance(payload, dict) else {}
    if not isinstance(predicted, dict):
        return None
    try:
        return float(predicted.get("brightness_pct"))
    except (TypeError, ValueError):
        return None


def _classify_source(
    session: list[sqlite3.Row],
    commands: dict[str, sqlite3.Row],
) -> tuple[str, str, str | None]:
    representative = session[-1]
    command_id = representative["command_id"]
    if command_id and command_id in commands:
        command = commands[command_id]
        source = (command["source"] or "").strip().lower()
        if source == "hardware":
            return "physical_or_bridge", "medium", "explicit_command:hardware"
        if source == "app":
            return "home_app_ai", "high", "explicit_command:app"
        if source == "voice":
            return "home_app_ai", "medium", "explicit_command:voice"
        return "home_app_ai", "medium", "explicit_command"

    hints = [row["source_hint"] for row in session if row["source_hint"]]
    hint = hints[-1] if hints else None
    if hint == "home_app_ai":
        return "home_app_ai", "medium", hint
    if hint == "automation_or_script":
        pred = _predicted_brightness(representative)
        to_bri = representative["to_brightness_pct"]
        if pred is not None and to_bri is not None and abs(float(to_bri) - pred) <= 5:
            return "living_lights_automation", "medium", hint
        return "script_or_scene", "low", hint
    if hint == "home_app_or_ha_user":
        return "home_app_direct", "low", hint
    if hint == "physical_or_bridge_or_unknown":
        return "physical_or_bridge", "low", hint
    return "unknown_source", "low", hint


def _interpret(session: list[sqlite3.Row], source_class: str) -> str:
    representative = session[-1]
    if representative["command_id"]:
        return "explicit_command"
    if source_class == "living_lights_automation":
        return "system_action"
    pred = _predicted_brightness(representative)
    to_bri = representative["to_brightness_pct"]
    if (
        source_class in {"physical_or_bridge", "home_app_direct", "unknown_source"}
        and pred is not None
        and to_bri is not None
        and abs(float(to_bri) - pred) >= 10
    ):
        return "preference_correction"
    return "not_used_for_learning"


def _episode_summary(
    session: list[sqlite3.Row],
    commands: dict[str, sqlite3.Row],
) -> dict[str, Any]:
    first = session[0]
    last = session[-1]
    first_ts = parse_ts(first["ts"])
    last_ts = parse_ts(last["ts"])
    duration_s = (
        max(0.0, (last_ts - first_ts).total_seconds())
        if first_ts is not None and last_ts is not None
        else None
    )
    source_class, source_confidence, source_hint = _classify_source(session, commands)
    interpretation = _interpret(session, source_class)
    path = [_path_item(row) for row in session]
    raw_ids = [row["id"] for row in session]
    representative = {
        "event_id": last["event_id"],
        "ts": last["ts"],
        "from": _state_from_row(first, "from"),
        "to": _state_from_row(last, "to"),
        "predicted_brightness_pct": _predicted_brightness(last),
        "command_id": last["command_id"],
    }
    episode_id = "lightep:" + stable_hash("|".join([
        first["id"],
        last["id"],
        str(len(session)),
        first["ts"] or "",
        last["ts"] or "",
    ]))[:24]
    return {
        "id": episode_id,
        "zone": last["zone"],
        "entity_id": last["entity_id"],
        "light_entity": last["light_entity"],
        "start_ts": first["ts"],
        "end_ts": last["ts"],
        "duration_s": duration_s,
        "event_count": len(session),
        "raw_event_ids": raw_ids,
        "observed_path": path,
        "first_state": _state_from_row(first, "from"),
        "last_state": _state_from_row(last, "to"),
        "representative": representative,
        "command_id": last["command_id"],
        "source_class": source_class,
        "source_hint": source_hint,
        "source_confidence": source_confidence,
        "interpretation": interpretation,
        "collapse_reason": "single_event" if len(session) == 1 else "same_entity_window",
    }


def _build_sessions(rows: list[sqlite3.Row]) -> list[list[sqlite3.Row]]:
    by_entity: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_entity[row["entity_id"] or row["light_entity"] or "unknown"].append(row)
    sessions: list[list[sqlite3.Row]] = []
    for bucket in by_entity.values():
        bucket.sort(key=lambda row: parse_ts(row["ts"]) or datetime.min.replace(tzinfo=timezone.utc))
        current: list[sqlite3.Row] = []
        last_ts: datetime | None = None
        last_command_id: str | None = None
        for row in bucket:
            ts = parse_ts(row["ts"])
            command_id = row["command_id"]
            same_command = bool(current and command_id and command_id == last_command_id)
            same_window = (
                bool(current)
                and ts is not None
                and last_ts is not None
                and 0 <= (ts - last_ts).total_seconds() <= EPISODE_GAP_SECONDS
            )
            if not current or same_command or same_window:
                current.append(row)
            else:
                sessions.append(current)
                current = [row]
            last_ts = ts
            last_command_id = command_id
        if current:
            sessions.append(current)
    sessions.sort(key=lambda session: parse_ts(session[-1]["ts"]) or datetime.min.replace(tzinfo=timezone.utc))
    return sessions


def rebuild_lighting_change_episodes(
    conn: sqlite3.Connection,
    *,
    hours: float | None = None,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM lighting_change_events
        WHERE ts IS NOT NULL
        ORDER BY ts
        """
    ).fetchall()
    if hours:
        parsed = [ts for ts in (parse_ts(row["ts"]) for row in rows) if ts is not None]
        anchor = max(parsed) if parsed else datetime.now(timezone.utc)
        start = anchor - timedelta(hours=max(1.0, float(hours)))
        rows = [
            row for row in rows
            if (parse_ts(row["ts"]) is not None and start <= parse_ts(row["ts"]) <= anchor)
        ]
    commands = _command_lookup(conn)
    sessions = _build_sessions(rows)
    episodes = [_episode_summary(session, commands) for session in sessions]
    conn.execute("DELETE FROM lighting_change_episodes")
    now = utc_now()
    for ep in episodes:
        conn.execute(
            """
            INSERT OR REPLACE INTO lighting_change_episodes
                (id, zone, entity_id, light_entity, start_ts, end_ts, duration_s,
                 event_count, raw_event_ids_json, observed_path_json,
                 first_state_json, last_state_json, representative_json,
                 command_id, source_class, source_hint, source_confidence,
                 interpretation, collapse_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ep["id"],
                ep["zone"],
                ep["entity_id"],
                ep["light_entity"],
                ep["start_ts"],
                ep["end_ts"],
                ep["duration_s"],
                ep["event_count"],
                canonical_json(ep["raw_event_ids"]),
                canonical_json(ep["observed_path"]),
                canonical_json(ep["first_state"]),
                canonical_json(ep["last_state"]),
                canonical_json(ep["representative"]),
                ep["command_id"],
                ep["source_class"],
                ep["source_hint"],
                ep["source_confidence"],
                ep["interpretation"],
                ep["collapse_reason"],
                now,
            ),
        )
    raw_count = len(rows)
    folded = sum(max(0, ep["event_count"] - 1) for ep in episodes)
    return {
        "ok": True,
        "raw_event_count": raw_count,
        "episode_count": len(episodes),
        "events_collapsed": folded,
        "collapse_ratio": (folded / raw_count) if raw_count else 0.0,
        "unknown_source_count": sum(1 for ep in episodes if ep["source_class"] == "unknown_source"),
    }


def _episode_from_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["raw_event_ids"] = _decode(d.pop("raw_event_ids_json", None), [])
    d["observed_path"] = _decode(d.pop("observed_path_json", None), [])
    d["first_state"] = _decode(d.pop("first_state_json", None), {})
    d["last_state"] = _decode(d.pop("last_state_json", None), {})
    d["representative"] = _decode(d.pop("representative_json", None), {})
    return d


def list_recent_lighting_activity(
    conn: sqlite3.Connection,
    *,
    zone: str | None = None,
    hours: float = 24.0,
    limit: int = 80,
    include_raw: bool = False,
) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM lighting_change_episodes ORDER BY end_ts"
    ).fetchall()
    parsed = [ts for ts in (parse_ts(row["end_ts"]) for row in rows) if ts is not None]
    anchor = max(parsed) if parsed else datetime.now(timezone.utc)
    start = anchor - timedelta(hours=max(1.0, min(float(hours), 168.0)))
    filtered = []
    for row in rows:
        ts = parse_ts(row["end_ts"])
        if ts is None or not (start <= ts <= anchor):
            continue
        if zone and row["zone"] != zone:
            continue
        filtered.append(row)
    filtered.sort(key=lambda row: row["end_ts"] or "", reverse=True)
    episodes = [_episode_from_row(row) for row in filtered[: max(1, min(int(limit), 300))]]
    if include_raw:
        for ep in episodes:
            raw_ids = ep.get("raw_event_ids") or []
            if not raw_ids:
                ep["raw_events"] = []
                continue
            placeholders = ",".join("?" for _ in raw_ids)
            raw_rows = conn.execute(
                f"SELECT * FROM lighting_change_events WHERE id IN ({placeholders}) ORDER BY ts",
                raw_ids,
            ).fetchall()
            ep["raw_events"] = [dict(r) for r in raw_rows]
    return {
        "ok": True,
        "hours": hours,
        "zone": zone,
        "window": {"start": start.isoformat(), "end": anchor.isoformat()},
        "summary": {
            "episode_count": len(filtered),
            "raw_event_count": sum(int(row["event_count"] or 0) for row in filtered),
            "events_collapsed": sum(max(0, int(row["event_count"] or 0) - 1) for row in filtered),
            "unknown_source_count": sum(1 for row in filtered if row["source_class"] == "unknown_source"),
            "interpretation_counts": dict(_counts(row["interpretation"] for row in filtered)),
            "source_counts": dict(_counts(row["source_class"] for row in filtered)),
        },
        "episodes": episodes,
    }


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        out[key] = out.get(key, 0) + 1
    return out
