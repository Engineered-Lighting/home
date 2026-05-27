from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any

from .episodes import linked_evidence_maps
from .ingest import canonical_json, stable_hash, utc_now
from .override_sessions import build_override_sessions, collapse_override_bursts, session_summary
from .quality import (
    DUPLICATE_PENDING_BLOCK_RATIO,
    candidate_quality_warnings,
    pending_duplicate_stats,
)


MIN_NON_SHADOW = 5
MIN_DAYS = 2
MAX_DELTA_VARIANCE = 100.0
MAX_OPPOSITE_RATE = 0.25
MAX_SCRUB_RATIO = 0.35


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
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _variance(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if len(vals) < 2:
        return 0.0 if vals else None
    avg = sum(vals) / len(vals)
    return sum((v - avg) ** 2 for v in vals) / len(vals)


def _candidate_context(row: sqlite3.Row) -> dict[str, Any]:
    # MC.1 — mirror policy_context() in override_sessions.py. Both must
    # agree on the dict key set so candidate_override_sessions() can
    # join candidates to sessions by `==`-compare. Defensive read on
    # gaming_active (added post-deploy; historical rows are None).
    try:
        gaming = row["gaming_active"]
    except (IndexError, KeyError):
        gaming = None
    return {
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": row["tv_playing"],
        "gaming_active": gaming,
        "asleep": row["asleep"],
        "night_safe": row["night_safe"],
        "user_at_home": row["user_at_home"],
        "light_state": row["light_state"],
    }


def _context_key(zone: str, ctx: dict[str, Any]) -> str:
    return stable_hash(zone + "|" + canonical_json(ctx))[:24]


def _suppressed(
    suppressions: list[dict[str, Any]],
    *,
    zone: str,
    ctx: dict[str, Any],
) -> bool:
    for rule in suppressions:
        if rule.get("zone") and rule.get("zone") != zone:
            continue
        rule_ctx = rule.get("context") or {}
        if all(ctx.get(k) == v for k, v in rule_ctx.items()):
            return True
    return False


def _load_suppressions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT zone, context_json
        FROM preference_suppression_rules
        WHERE active = 1
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append({"zone": row["zone"], "context": _json_loads(row["context_json"], {})})
    return out


def _blockers(
    *,
    non_shadow_count: int,
    effective_non_shadow_weight: float,
    days_seen: int,
    delta_variance: float | None,
    shadow_ratio: float,
    opposite_rate: float,
    scrub_ratio: float,
    duplicate_pending_ratio: float,
    suppressed: bool,
) -> list[str]:
    blockers: list[str] = []
    if non_shadow_count < MIN_NON_SHADOW:
        blockers.append("fewer than 5 non-shadow observations")
    elif effective_non_shadow_weight < MIN_NON_SHADOW:
        blockers.append("insufficient quality-adjusted evidence")
    if days_seen < MIN_DAYS:
        blockers.append("insufficient age across days")
    if delta_variance is None:
        blockers.append("no numeric brightness delta yet")
    elif delta_variance > MAX_DELTA_VARIANCE:
        blockers.append("high variance")
    if shadow_ratio > 0.5:
        blockers.append("mostly shadow-mode")
    if opposite_rate > MAX_OPPOSITE_RATE:
        blockers.append("conflicting opposite-direction overrides")
    if scrub_ratio > MAX_SCRUB_RATIO:
        blockers.append("dominated by rapid slider-scrub events")
    if duplicate_pending_ratio > DUPLICATE_PENDING_BLOCK_RATIO:
        blockers.append("duplicate-heavy pending captures")
    if suppressed:
        blockers.append("suppressed by user")
    return blockers


def _scrub_ratio(timestamps: list[str | None]) -> float:
    parsed = sorted(t for t in (_parse_ts(ts) for ts in timestamps) if t is not None)
    if len(parsed) < 2:
        return 0.0
    close = 0
    for left, right in zip(parsed, parsed[1:]):
        if abs((right - left).total_seconds()) < 8:
            close += 1
    return close / max(1, len(parsed) - 1)


def _session_count(row: sqlite3.Row | dict[str, Any]) -> int:
    try:
        return max(1, int(row["session_event_count"] or 1))
    except (KeyError, TypeError, ValueError):
        return 1


def _session_id_for_row(row: sqlite3.Row | dict[str, Any]) -> str | None:
    if not isinstance(row, dict):
        return None
    summary = row.get("override_session_summary") or row.get("pending_related_override_session")
    if isinstance(summary, dict):
        return summary.get("id")
    return None


def _override_session_stats(group: list[sqlite3.Row | dict[str, Any]]) -> dict[str, Any]:
    override_rows = [row for row in group if row["kind"] == "override_event"]
    raw_override_count = sum(_session_count(row) for row in override_rows)
    burst_event_count = sum(max(0, _session_count(row) - 1) for row in override_rows)
    sessions = [
        row["override_session_summary"]
        for row in override_rows
        if isinstance(row, dict) and row.get("override_session_summary") and _session_count(row) > 1
    ]
    return {
        "raw_evidence_count": len(group) + burst_event_count,
        "raw_override_count": raw_override_count,
        "override_session_count": len(override_rows),
        "burst_event_count": burst_event_count,
        "max_override_burst_size": max((_session_count(row) for row in override_rows), default=0),
        "override_burst_ratio": (
            burst_event_count / raw_override_count if raw_override_count else 0.0
        ),
        "sessions": sessions[:5],
    }


def _evidence_hierarchy(group: list[sqlite3.Row | dict[str, Any]]) -> dict[str, Any]:
    pending_count = sum(1 for row in group if row["kind"] == "pending_preference")
    session_intent_count = sum(1 for row in group if row["kind"] == "override_event")
    automation_tail_session_ids = {
        session_id
        for row in group
        if isinstance(row, dict)
        and bool(row.get("automation_tail_detected") or row.get("pending_intent_reconciled"))
        for session_id in [_session_id_for_row(row)]
        if session_id
    }
    automation_tail_unidentified = sum(
        1
        for row in group
        if isinstance(row, dict)
        and bool(row.get("automation_tail_detected") or row.get("pending_intent_reconciled"))
        and not _session_id_for_row(row)
    )
    if pending_count > 0:
        tier = "pending_preference"
    elif session_intent_count > 0:
        tier = "session_intent"
    else:
        tier = "raw_observation"
    return {
        "pending_evidence_count": pending_count,
        "session_intent_count": session_intent_count,
        "automation_tail_session_count": len(automation_tail_session_ids) + automation_tail_unidentified,
        "evidence_tier": tier,
    }


def _override_session_intents_by_context_id(
    override_rows: list[sqlite3.Row],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for session in build_override_sessions(override_rows):
        summary = session_summary(session, include_events=False)
        for row in session:
            context_id = row["context_id"] if "context_id" in row.keys() else None
            if context_id:
                out[str(context_id)] = summary
    return out


def _reconciled_pending_rows(
    pending_rows: list[sqlite3.Row],
    session_by_context_id: dict[str, dict[str, Any]],
) -> list[sqlite3.Row | dict[str, Any]]:
    out: list[sqlite3.Row | dict[str, Any]] = []
    for row in pending_rows:
        related_context_id = (
            row["related_override_context_id"]
            if "related_override_context_id" in row.keys()
            else None
        )
        session = session_by_context_id.get(str(related_context_id)) if related_context_id else None
        if not session or not session.get("automation_tail_detected"):
            out.append(row)
            continue
        learning_value = session.get("learning_value_pct")
        if learning_value is None:
            out.append(row)
            continue
        item = dict(row)
        original_context = _candidate_context(row)
        learning_context = dict(session.get("learning_policy_context") or {})
        item["pending_intent_reconciled"] = True
        item["pending_original_actual_brightness_pct"] = row["actual_brightness_pct"]
        item["pending_original_delta_pct"] = row["delta_pct"]
        item["pending_original_policy_context"] = original_context
        item["pending_reconciled_policy_context"] = learning_context
        item["pending_related_override_session"] = session
        item["automation_tail_detected"] = True
        item["learning_value_pct"] = learning_value
        item["learning_value_source"] = session.get("learning_value_source")
        item["actual_brightness_pct"] = learning_value
        predicted = row["predicted_brightness_pct"]
        try:
            item["delta_pct"] = float(learning_value) - float(predicted) if predicted is not None else row["delta_pct"]
        except (TypeError, ValueError):
            item["delta_pct"] = row["delta_pct"]
        for key, value in learning_context.items():
            if key in {
                "profile",
                "state",
                "tv_playing",
                "gaming_active",
                "asleep",
                "night_safe",
                "user_at_home",
                "light_state",
            }:
                item[key] = value
        if not learning_context.get("light_state"):
            item["light_state"] = "off" if float(learning_value) == 0 else "on"
        item["context_json"] = canonical_json({
            **_json_loads(row["context_json"], {}),
            "reconciled_from_automation_tail": True,
            "original_policy_context": original_context,
            "learning_policy_context": learning_context,
        })
        out.append(item)
    return out


def _row_extra_evidence_ids(row: sqlite3.Row | dict[str, Any]) -> list[str]:
    if not isinstance(row, dict):
        return []
    summary = row.get("pending_related_override_session")
    if not isinstance(summary, dict):
        return []
    ids: list[str] = []
    learning = summary.get("learning_representative") or {}
    if isinstance(learning, dict) and learning.get("id"):
        ids.append(learning["id"])
    for event_id in summary.get("event_ids") or []:
        if event_id not in ids:
            ids.append(event_id)
    return ids


def aggregate_lighting_preferences(conn: sqlite3.Connection) -> dict[str, Any]:
    raw_rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE zone IS NOT NULL
          AND zone != 'unknown'
          AND kind IN ('override_event', 'pending_preference')
        ORDER BY ts
        """
    ).fetchall()
    override_rows = [row for row in raw_rows if row["kind"] == "override_event"]
    pending_rows = [row for row in raw_rows if row["kind"] != "override_event"]
    session_by_context_id = _override_session_intents_by_context_id(override_rows)
    effective_pending_rows = _reconciled_pending_rows(pending_rows, session_by_context_id)
    effective_overrides, _ = collapse_override_bursts(override_rows)
    rows = sorted(
        [*effective_pending_rows, *effective_overrides],
        key=lambda row: _parse_ts(row["ts"]) or datetime.min,
    )
    suppressions = _load_suppressions(conn)
    pending_to_overrides, override_to_pending = linked_evidence_maps(conn)
    groups: dict[tuple[str, str], list[sqlite3.Row | dict[str, Any]]] = defaultdict(list)
    contexts: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        zone = row["zone"]
        ctx = _candidate_context(row)
        key = (zone, canonical_json(ctx))
        contexts[key] = ctx
        groups[key].append(row)

    updated = 0
    revisions = 0
    now = utc_now()
    for (zone, ctx_json), group in groups.items():
        ctx = contexts[(zone, ctx_json)]
        candidate_id = f"pref:{_context_key(zone, ctx)}"
        override_session_stats = _override_session_stats(group)
        evidence_hierarchy = _evidence_hierarchy(group)
        actuals = [row["actual_brightness_pct"] for row in group if row["actual_brightness_pct"] is not None]
        predicted = [row["predicted_brightness_pct"] for row in group if row["predicted_brightness_pct"] is not None]
        deltas = [row["delta_pct"] for row in group if row["delta_pct"] is not None]
        timestamps = [row["ts"] for row in group]
        days = {str(row["ts"])[:10] for row in group if row["ts"]}
        shadow_count = sum(1 for row in group if row["shadow_mode"])
        non_shadow_count = len(group) - shadow_count
        effective_non_shadow_weight = sum(
            float(row["weight"] or 0)
            for row in group
            if not row["shadow_mode"]
        )
        nonzero_deltas = [d for d in deltas if d != 0]
        positive = sum(1 for d in nonzero_deltas if d > 0)
        negative = sum(1 for d in nonzero_deltas if d < 0)
        opposite_rate = (
            min(positive, negative) / len(nonzero_deltas)
            if nonzero_deltas
            else 0.0
        )
        delta_variance = _variance(deltas)
        shadow_ratio = shadow_count / len(group) if group else 0.0
        scrub_ratio = _scrub_ratio(timestamps)
        duplicate_stats = pending_duplicate_stats(group)
        duplicate_pending_ratio = float(duplicate_stats.get("duplicate_ratio") or 0)
        capture_suppressed_total = int(duplicate_stats.get("capture_suppressed_total") or 0)
        dedupe_key_count = int(duplicate_stats.get("dedupe_key_count") or 0)
        is_suppressed = _suppressed(suppressions, zone=zone, ctx=ctx)
        linked_overrides = {
            override_id
            for row in group
            if row["kind"] == "pending_preference"
            for override_id in pending_to_overrides.get(row["id"], [])
        }
        linked_pending = {
            pending_id
            for row in group
            if row["kind"] == "override_event"
            for pending_id in override_to_pending.get(row["id"], [])
        }
        blockers = _blockers(
            non_shadow_count=non_shadow_count,
            effective_non_shadow_weight=effective_non_shadow_weight,
            days_seen=len(days),
            delta_variance=delta_variance,
            shadow_ratio=shadow_ratio,
            opposite_rate=opposite_rate,
            scrub_ratio=scrub_ratio,
            duplicate_pending_ratio=duplicate_pending_ratio,
            suppressed=is_suppressed,
        )
        quality_warnings = candidate_quality_warnings(
            group,
            linked_override_count=len(linked_overrides),
            linked_pending_count=len(linked_pending),
        )
        if override_session_stats["burst_event_count"] > 0:
            quality_warnings.append({
                "code": "override_burst_collapsed",
                "severity": "info",
                "message": "rapid override burst was collapsed to final-value sessions for scoring",
                "details": override_session_stats,
            })
        status = "candidate" if not blockers else "blocked"
        median_actual = median(actuals) if actuals else None
        median_predicted = median(predicted) if predicted else None
        median_delta = median(deltas) if deltas else None
        target: dict[str, Any]
        if ctx.get("light_state") == "off" or median_actual == 0:
            target = {"light_state": "off", "brightness_pct": 0}
        else:
            target = {"light_state": "on", "brightness_pct": median_actual}
        evidence_ids = []
        group_extra_evidence = [
            evidence_id
            for row in group
            for evidence_id in _row_extra_evidence_ids(row)
        ]
        for evidence_id in [
            *[row["id"] for row in group],
            *group_extra_evidence,
            *sorted(linked_overrides),
            *sorted(linked_pending),
        ]:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
        metrics = {
            "evidence_count": len(group),
            "raw_evidence_count": override_session_stats["raw_evidence_count"],
            "raw_override_count": override_session_stats["raw_override_count"],
            "override_session_count": override_session_stats["override_session_count"],
            "burst_event_count": override_session_stats["burst_event_count"],
            "max_override_burst_size": override_session_stats["max_override_burst_size"],
            "override_burst_ratio": override_session_stats["override_burst_ratio"],
            "override_session_stats": override_session_stats,
            "evidence_hierarchy": evidence_hierarchy,
            "pending_evidence_count": evidence_hierarchy["pending_evidence_count"],
            "session_intent_count": evidence_hierarchy["session_intent_count"],
            "automation_tail_session_count": evidence_hierarchy["automation_tail_session_count"],
            "evidence_tier": evidence_hierarchy["evidence_tier"],
            "supporting_evidence_count": max(0, len(evidence_ids) - len(group)),
            "linked_override_count": len(linked_overrides),
            "linked_pending_count": len(linked_pending),
            "duplicate_pending": duplicate_stats,
            "capture_suppressed_total": capture_suppressed_total,
            "dedupe_key_count": dedupe_key_count,
            "non_shadow_count": non_shadow_count,
            "effective_non_shadow_weight": effective_non_shadow_weight,
            "days_seen": len(days),
            "median_actual_brightness_pct": median_actual,
            "median_predicted_brightness_pct": median_predicted,
            "median_delta_pct": median_delta,
            "delta_variance": delta_variance,
            "shadow_mode_ratio": shadow_ratio,
            "opposite_direction_rate": opposite_rate,
            "duplicate_pending_ratio": duplicate_pending_ratio,
            "scrub_ratio": scrub_ratio,
            "suggested_target": target,
            "quality_warnings": quality_warnings,
        }
        conn.execute(
            """
            INSERT INTO preference_candidates
                (id, zone, context_json, status, evidence_count,
                 raw_evidence_count, raw_override_count, override_session_count,
                 burst_event_count, max_override_burst_size, override_burst_ratio,
                 pending_evidence_count, session_intent_count,
                 automation_tail_session_count, evidence_tier,
                 supporting_evidence_count, linked_override_count, linked_pending_count,
                 capture_suppressed_total, dedupe_key_count,
                 non_shadow_count,
                 days_seen, median_actual_brightness_pct,
                 median_predicted_brightness_pct, median_delta_pct,
                 delta_variance, shadow_mode_ratio, opposite_direction_rate,
                 duplicate_pending_ratio,
                 suggested_target_json, blockers_json, quality_warnings_json,
                 evidence_ids_json,
                 first_seen, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                evidence_count=excluded.evidence_count,
                raw_evidence_count=excluded.raw_evidence_count,
                raw_override_count=excluded.raw_override_count,
                override_session_count=excluded.override_session_count,
                burst_event_count=excluded.burst_event_count,
                max_override_burst_size=excluded.max_override_burst_size,
                override_burst_ratio=excluded.override_burst_ratio,
                pending_evidence_count=excluded.pending_evidence_count,
                session_intent_count=excluded.session_intent_count,
                automation_tail_session_count=excluded.automation_tail_session_count,
                evidence_tier=excluded.evidence_tier,
                supporting_evidence_count=excluded.supporting_evidence_count,
                linked_override_count=excluded.linked_override_count,
                linked_pending_count=excluded.linked_pending_count,
                capture_suppressed_total=excluded.capture_suppressed_total,
                dedupe_key_count=excluded.dedupe_key_count,
                non_shadow_count=excluded.non_shadow_count,
                days_seen=excluded.days_seen,
                median_actual_brightness_pct=excluded.median_actual_brightness_pct,
                median_predicted_brightness_pct=excluded.median_predicted_brightness_pct,
                median_delta_pct=excluded.median_delta_pct,
                delta_variance=excluded.delta_variance,
                shadow_mode_ratio=excluded.shadow_mode_ratio,
                opposite_direction_rate=excluded.opposite_direction_rate,
                duplicate_pending_ratio=excluded.duplicate_pending_ratio,
                suggested_target_json=excluded.suggested_target_json,
                blockers_json=excluded.blockers_json,
                quality_warnings_json=excluded.quality_warnings_json,
                evidence_ids_json=excluded.evidence_ids_json,
                first_seen=excluded.first_seen,
                last_seen=excluded.last_seen,
                updated_at=excluded.updated_at
            """,
            (
                candidate_id,
                zone,
                ctx_json,
                status,
                len(group),
                override_session_stats["raw_evidence_count"],
                override_session_stats["raw_override_count"],
                override_session_stats["override_session_count"],
                override_session_stats["burst_event_count"],
                override_session_stats["max_override_burst_size"],
                override_session_stats["override_burst_ratio"],
                evidence_hierarchy["pending_evidence_count"],
                evidence_hierarchy["session_intent_count"],
                evidence_hierarchy["automation_tail_session_count"],
                evidence_hierarchy["evidence_tier"],
                max(0, len(evidence_ids) - len(group)),
                len(linked_overrides),
                len(linked_pending),
                capture_suppressed_total,
                dedupe_key_count,
                non_shadow_count,
                len(days),
                median_actual,
                median_predicted,
                median_delta,
                delta_variance,
                shadow_ratio,
                opposite_rate,
                duplicate_pending_ratio,
                canonical_json(target),
                canonical_json(blockers),
                canonical_json(quality_warnings),
                canonical_json(evidence_ids),
                min(t for t in timestamps if t) if any(timestamps) else None,
                max(t for t in timestamps if t) if any(timestamps) else None,
                now,
            ),
        )
        revision_hash = stable_hash(
            canonical_json(metrics)
            + canonical_json(blockers)
            + canonical_json(quality_warnings)
            + canonical_json(evidence_ids)
        )
        last = conn.execute(
            """
            SELECT revision_hash
            FROM preference_candidate_revisions
            WHERE candidate_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        if last is None or last["revision_hash"] != revision_hash:
            conn.execute(
                """
                INSERT INTO preference_candidate_revisions
                    (candidate_id, created_at, status, metrics_json,
                     blockers_json, evidence_ids_json, revision_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    now,
                    status,
                    canonical_json(metrics),
                    canonical_json(blockers),
                    canonical_json(evidence_ids),
                    revision_hash,
                ),
            )
            revisions += 1
        updated += 1
    conn.commit()
    return {
        "ok": True,
        "groups": len(groups),
        "candidates_updated": updated,
        "revisions_added": revisions,
    }
