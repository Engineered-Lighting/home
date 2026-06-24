from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .ingest import canonical_json, stable_hash


LINK_WINDOW_SECONDS = 30 * 60
MAX_BRIGHTNESS_GAP_PCT = 10.0


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _brightness_close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return True
    return abs(float(left) - float(right)) <= MAX_BRIGHTNESS_GAP_PCT


def _policy_context(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": row["tv_playing"],
        "asleep": row["asleep"],
        "night_safe": row["night_safe"],
        "user_at_home": row["user_at_home"],
        "light_state": row["light_state"],
    }


def build_preference_episodes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Link touch-time overrides to later final preference captures.

    Candidate aggregation groups by policy context. This pass keeps the
    immediate manual touch as supporting evidence even when the classifier
    context shifts before the pending preference is captured.
    """

    rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE zone IS NOT NULL
          AND zone != 'unknown'
          AND kind IN ('override_event', 'pending_preference')
        ORDER BY ts
        """
    ).fetchall()
    overrides = [row for row in rows if row["kind"] == "override_event"]
    pending = [row for row in rows if row["kind"] == "pending_preference"]
    overrides_by_context_id = {
        row["context_id"]: row
        for row in overrides
        if "context_id" in row.keys() and row["context_id"]
    }

    updated = 0
    linked = 0
    for pref in pending:
        pref_ts = _parse_ts(pref["ts"])
        if pref_ts is None:
            continue
        related_context_id = (
            pref["related_override_context_id"]
            if "related_override_context_id" in pref.keys()
            else None
        )
        link_method = "heuristic"
        linked_overrides: list[sqlite3.Row]
        if related_context_id and related_context_id in overrides_by_context_id:
            linked_overrides = [overrides_by_context_id[related_context_id]]
            link_method = "related_override_context_id"
        else:
            matches: list[tuple[float, sqlite3.Row]] = []
            for override in overrides:
                if override["zone"] != pref["zone"]:
                    continue
                ov_ts = _parse_ts(override["ts"])
                if ov_ts is None:
                    continue
                age_s = (pref_ts - ov_ts).total_seconds()
                if age_s < 0 or age_s > LINK_WINDOW_SECONDS:
                    continue
                if not _brightness_close(
                    override["actual_brightness_pct"],
                    pref["actual_brightness_pct"],
                ):
                    continue
                matches.append((age_s, override))
            matches.sort(key=lambda item: item[0])
            linked_overrides = [row for _, row in matches[:3]]
        if linked_overrides:
            linked += 1

        evidence_ids = [pref["id"], *[row["id"] for row in linked_overrides]]
        start_ts = linked_overrides[0]["ts"] if linked_overrides else pref["ts"]
        end_ts = pref["ts"]
        summary = {
            "pending_observation_id": pref["id"],
            "override_observation_ids": [row["id"] for row in linked_overrides],
            "related_override_context_id": related_context_id,
            "link_method": link_method if linked_overrides else "unlinked",
            "evidence_ids": evidence_ids,
            "policy_context": _policy_context(pref),
            "pending": {
                "ts": pref["ts"],
                "actual_brightness_pct": pref["actual_brightness_pct"],
                "predicted_brightness_pct": pref["predicted_brightness_pct"],
                "delta_pct": pref["delta_pct"],
                "weight": pref["weight"],
                "capture_provenance": _json_loads(pref["capture_provenance_json"], {}),
                "prediction_consistency": _json_loads(pref["prediction_consistency_json"], {}),
                "occupancy": _json_loads(pref["occupancy_json"], {}),
                "evidence_id": pref["evidence_id"] if "evidence_id" in pref.keys() else None,
                "dedupe_key": pref["dedupe_key"] if "dedupe_key" in pref.keys() else None,
                "capture_suppressed_count": (
                    pref["capture_suppressed_count"]
                    if "capture_suppressed_count" in pref.keys()
                    else 0
                ),
                "related_override_context_id": related_context_id,
            },
            "overrides": [
                {
                    "id": row["id"],
                    "context_id": row["context_id"] if "context_id" in row.keys() else None,
                    "ts": row["ts"],
                    "actual_brightness_pct": row["actual_brightness_pct"],
                    "predicted_brightness_pct": row["predicted_brightness_pct"],
                    "delta_pct": row["delta_pct"],
                    "context": _json_loads(row["context_json"], {}),
                }
                for row in linked_overrides
            ],
        }
        episode_id = f"episode:{stable_hash(pref['id'] + canonical_json(evidence_ids))[:24]}"
        conn.execute(
            """
            INSERT INTO episodes (id, start_ts, end_ts, zone, kind, summary_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                start_ts=excluded.start_ts,
                end_ts=excluded.end_ts,
                zone=excluded.zone,
                kind=excluded.kind,
                summary_json=excluded.summary_json
            """,
            (
                episode_id,
                start_ts,
                end_ts,
                pref["zone"],
                "lighting_preference",
                canonical_json(summary),
            ),
        )
        updated += 1
    conn.commit()
    return {"ok": True, "episodes_updated": updated, "episodes_with_overrides": linked}


def linked_evidence_maps(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return pending->overrides and override->pending maps from episodes."""

    pending_to_overrides: dict[str, list[str]] = {}
    override_to_pending: dict[str, list[str]] = {}
    rows = conn.execute(
        """
        SELECT summary_json
        FROM episodes
        WHERE kind = 'lighting_preference'
        """
    ).fetchall()
    for row in rows:
        summary = _json_loads(row["summary_json"], {})
        pending_id = summary.get("pending_observation_id")
        override_ids = summary.get("override_observation_ids") or []
        if pending_id:
            pending_to_overrides[pending_id] = list(override_ids)
        for override_id in override_ids:
            override_to_pending.setdefault(override_id, []).append(pending_id)
    return pending_to_overrides, override_to_pending
