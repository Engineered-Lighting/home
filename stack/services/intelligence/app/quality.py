from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from statistics import median
from typing import Any


PREDICTION_MISMATCH_WARN_PCT = 10.0
OCCUPANCY_AGE_WARN_S = 10.0
OVERRIDE_PENDING_DISAGREE_PCT = 10.0
DUPLICATE_PENDING_WINDOW_S = 10 * 60
DUPLICATE_PENDING_BUCKET_PCT = 3.0
DUPLICATE_PENDING_WARN_RATIO = 0.35
DUPLICATE_PENDING_BLOCK_RATIO = 0.60
DUPLICATE_PENDING_MIN_COUNT = 5


def _json_loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _bucket_pct(value: Any) -> int | None:
    numeric = _float(value)
    if numeric is None:
        return None
    return int(round(numeric / DUPLICATE_PENDING_BUCKET_PCT) * DUPLICATE_PENDING_BUCKET_PCT)


def _warning(
    code: str,
    severity: str,
    message: str,
    *,
    evidence_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if evidence_id:
        out["evidence_id"] = evidence_id
    if details:
        out["details"] = details
    return out


def pending_duplicate_stats(group: list[sqlite3.Row]) -> dict[str, Any]:
    pending = [row for row in group if row["kind"] == "pending_preference"]
    suppressed_total = sum(int(row["capture_suppressed_count"] or 0) for row in pending)
    dedupe_keys = {
        row["dedupe_key"]
        for row in pending
        if "dedupe_key" in row.keys() and row["dedupe_key"]
    }
    if len(pending) < 2:
        return {
            "pending_count": len(pending),
            "duplicate_count": 0,
            "duplicate_ratio": 0.0,
            "hygiene_adjusted_duplicate_ratio": 0.0,
            "capture_suppressed_total": suppressed_total,
            "dedupe_key_count": len(dedupe_keys),
            "max_cluster_size": len(pending),
            "cluster_count": len(pending),
        }
    rows = sorted(
        pending,
        key=lambda row: _parse_ts(row["ts"]) or datetime.min,
    )
    clusters: list[list[sqlite3.Row]] = []
    current: list[sqlite3.Row] = []
    current_key: tuple[Any, ...] | None = None
    last_ts: datetime | None = None
    for row in rows:
        ts = _parse_ts(row["ts"])
        dedupe_key = row["dedupe_key"] if "dedupe_key" in row.keys() else None
        key = (
            dedupe_key or "__no_dedupe_key__",
            _bucket_pct(row["actual_brightness_pct"]),
            _bucket_pct(row["predicted_brightness_pct"]),
            row["light_state"],
        )
        window_s = _float(row["dedupe_window_s"] if "dedupe_window_s" in row.keys() else None)
        if window_s is None:
            window_s = DUPLICATE_PENDING_WINDOW_S
        same_key = key == current_key
        close_enough = (
            ts is not None
            and last_ts is not None
            and 0 <= (ts - last_ts).total_seconds() <= window_s
        )
        if current and same_key and close_enough:
            current.append(row)
        else:
            if current:
                clusters.append(current)
            current = [row]
            current_key = key
        if ts is not None:
            last_ts = ts
    if current:
        clusters.append(current)
    duplicate_count = sum(max(0, len(cluster) - 1) for cluster in clusters)
    adjusted_denominator = len(pending) + suppressed_total
    return {
        "pending_count": len(pending),
        "duplicate_count": duplicate_count,
        "duplicate_ratio": duplicate_count / len(pending),
        "hygiene_adjusted_duplicate_ratio": (
            duplicate_count / adjusted_denominator if adjusted_denominator else 0.0
        ),
        "capture_suppressed_total": suppressed_total,
        "dedupe_key_count": len(dedupe_keys),
        "max_cluster_size": max((len(cluster) for cluster in clusters), default=0),
        "cluster_count": len(clusters),
        "window_s": DUPLICATE_PENDING_WINDOW_S,
        "brightness_bucket_pct": DUPLICATE_PENDING_BUCKET_PCT,
    }


def _compact_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for warning in warnings:
        key = (
            str(warning.get("code") or "warning"),
            str(warning.get("severity") or "info"),
            str(warning.get("message") or ""),
        )
        if key not in grouped:
            grouped[key] = {
                "code": key[0],
                "severity": key[1],
                "message": key[2],
                "details": {"count": 0, "evidence_ids": [], "examples": []},
            }
            order.append(key)
        bucket = grouped[key]
        details = bucket["details"]
        details["count"] += 1
        evidence_id = warning.get("evidence_id")
        if evidence_id and evidence_id not in details["evidence_ids"]:
            details["evidence_ids"].append(evidence_id)
            details["evidence_ids"] = details["evidence_ids"][:8]
        example = warning.get("details")
        if example and example not in details["examples"]:
            details["examples"].append(example)
            details["examples"] = details["examples"][:3]
    compacted: list[dict[str, Any]] = []
    for key in order:
        item = grouped[key]
        details = item.get("details") or {}
        if details.get("count") == 1:
            examples = details.get("examples") or []
            evidence_ids = details.get("evidence_ids") or []
            if examples:
                item["details"] = examples[0]
            else:
                item.pop("details", None)
            if evidence_ids:
                item["evidence_id"] = evidence_ids[0]
        compacted.append(item)
    return compacted


def observation_quality_warnings(row: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    r = dict(row)
    warnings: list[dict[str, Any]] = []
    evidence_id = r.get("id")
    kind = r.get("kind")
    zone = r.get("zone")
    ctx = _json_loads(r.get("context_json"), {}) if isinstance(r.get("context_json"), str) else (r.get("context") or {})
    capture_provenance = (
        _json_loads(r.get("capture_provenance_json"), {})
        if isinstance(r.get("capture_provenance_json"), str)
        else (r.get("capture_provenance") or {})
    )
    prediction_consistency = (
        _json_loads(r.get("prediction_consistency_json"), {})
        if isinstance(r.get("prediction_consistency_json"), str)
        else (r.get("prediction_consistency") or {})
    )
    occupancy = (
        _json_loads(r.get("occupancy_json"), {})
        if isinstance(r.get("occupancy_json"), str)
        else (r.get("occupancy") or {})
    )
    predictions = (
        _json_loads(r.get("predictions_by_zone_json"), {})
        if isinstance(r.get("predictions_by_zone_json"), str)
        else (r.get("predictions_by_zone") or {})
    )

    if kind == "pending_preference":
        if r.get("pending_intent_reconciled"):
            warnings.append(_warning(
                "pending_intent_reconciled",
                "info",
                "pending preference was scored with the linked override session's user-landed value",
                evidence_id=evidence_id,
                details={
                    "original_actual_brightness_pct": r.get("pending_original_actual_brightness_pct"),
                    "learning_value_pct": r.get("learning_value_pct"),
                    "learning_value_source": r.get("learning_value_source"),
                    "related_override_context_id": r.get("related_override_context_id"),
                },
            ))

        mismatch = _float(prediction_consistency.get("prediction_mismatch_pct"))
        primary_prediction = _float(prediction_consistency.get("primary_prediction_pct"))
        would_have_done = _float(prediction_consistency.get("would_have_done_brightness_pct"))
        if mismatch is None:
            zone_prediction = predictions.get(zone) if isinstance(predictions, dict) else None
            if isinstance(zone_prediction, dict):
                primary_prediction = _float(zone_prediction.get("predicted_pct"))
                would_have_done = _float(r.get("predicted_brightness_pct"))
                if primary_prediction is not None and would_have_done is not None:
                    mismatch = primary_prediction - would_have_done
        if mismatch is not None and abs(mismatch) > PREDICTION_MISMATCH_WARN_PCT:
            warnings.append(_warning(
                "prediction_mismatch",
                "warn",
                "pending preference prediction disagrees with current zone prediction",
                evidence_id=evidence_id,
                details={
                    "primary_prediction_pct": primary_prediction,
                    "would_have_done_brightness_pct": would_have_done,
                    "prediction_mismatch_pct": mismatch,
                },
            ))

        capture_trigger = capture_provenance.get("capture_trigger") or ctx.get("capture_trigger")
        if capture_trigger in {"manual_automation_trigger", "automation_trigger", "manual_trigger"}:
            warnings.append(_warning(
                "manual_capture_trigger",
                "warn",
                "pending preference was captured by manual automation trigger",
                evidence_id=evidence_id,
                details={"capture_trigger": capture_trigger},
            ))
        elif not capture_trigger:
            warnings.append(_warning(
                "capture_provenance_missing",
                "info",
                "pending preference does not include explicit capture_trigger provenance",
                evidence_id=evidence_id,
                details={"source": ctx.get("source")},
            ))

        occupancy_age = _float(occupancy.get("age_s"))
        if occupancy_age is None:
            occupancy_age = _float(ctx.get("occupancy_age_s"))
        if occupancy_age is None:
            occupancy_age = _float(ctx.get("dwell_s"))
        if occupancy_age is not None and occupancy_age < OCCUPANCY_AGE_WARN_S:
            warnings.append(_warning(
                "occupancy_unstable",
                "warn",
                "pending preference captured with a very short occupancy dwell/age",
                evidence_id=evidence_id,
                details={
                    "occupancy_age_s": occupancy_age,
                    "occupancy_entity_id": occupancy.get("entity_id"),
                    "occupancy_state": occupancy.get("state"),
                },
            ))

        flicker_count = _float(occupancy.get("flicker_count_5min"))
        if flicker_count is None:
            flicker_count = _float(ctx.get("occupancy_flicker_count"))
        if flicker_count is None:
            flicker_count = _float(ctx.get("recent_occupancy_flicker_count"))
        if flicker_count is not None and flicker_count >= 2:
            warnings.append(_warning(
                "occupancy_flicker",
                "warn",
                "recent occupancy flicker may make this final preference noisy",
                evidence_id=evidence_id,
                details={"occupancy_flicker_count": flicker_count},
            ))

    if r.get("shadow_mode"):
        warnings.append(_warning(
            "shadow_mode_evidence",
            "info",
            "evidence came from shadow mode and should be weighted lower",
            evidence_id=evidence_id,
        ))

    return warnings


def candidate_quality_warnings(
    group: list[sqlite3.Row],
    *,
    linked_override_count: int = 0,
    linked_pending_count: int = 0,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for row in group:
        warnings.extend(observation_quality_warnings(row))

    duplicate_stats = pending_duplicate_stats(group)
    if (
        duplicate_stats["pending_count"] >= DUPLICATE_PENDING_MIN_COUNT
        and duplicate_stats["duplicate_ratio"] > DUPLICATE_PENDING_WARN_RATIO
    ):
        warnings.append(_warning(
            "duplicate_pending_density",
            "warn",
            "candidate has many near-identical pending preference captures",
            details=duplicate_stats,
        ))

    kinds = {row["kind"] for row in group}
    if "pending_preference" not in kinds and linked_pending_count == 0:
        warnings.append(_warning(
            "no_pending_preference",
            "info",
            "candidate has immediate overrides but no final pending preference evidence yet",
        ))
    if "override_event" not in kinds and linked_override_count == 0:
        warnings.append(_warning(
            "no_override_event",
            "info",
            "candidate has final preference captures but no immediate override evidence yet",
        ))

    override_actuals = [
        row["actual_brightness_pct"]
        for row in group
        if row["kind"] == "override_event" and row["actual_brightness_pct"] is not None
    ]
    pending_actuals = [
        row["actual_brightness_pct"]
        for row in group
        if row["kind"] == "pending_preference" and row["actual_brightness_pct"] is not None
    ]
    if override_actuals and pending_actuals:
        diff = median(pending_actuals) - median(override_actuals)
        if abs(diff) > OVERRIDE_PENDING_DISAGREE_PCT:
            warnings.append(_warning(
                "override_pending_disagreement",
                "warn",
                "final pending preference brightness disagrees with immediate override brightness",
                details={
                    "median_override_actual_pct": median(override_actuals),
                    "median_pending_actual_pct": median(pending_actuals),
                    "difference_pct": diff,
                },
            ))

    return _compact_warnings(warnings)
