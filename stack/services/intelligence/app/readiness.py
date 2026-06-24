from __future__ import annotations

import sqlite3
from typing import Any

from .ingest import canonical_json, stable_hash


MIN_PRIMARY_EVIDENCE = 5
MIN_DURABLE_PENDING_PREFERENCES = MIN_PRIMARY_EVIDENCE
MIN_DAYS_SEEN = 2
MIN_LINKED_OVERRIDES = 1
MIN_EFFECT_SIZE_PCT = 10.0
MAX_DELTA_VARIANCE = 100.0
MAX_OPPOSITE_RATE = 0.25
MAX_SHADOW_RATIO = 0.50
MAX_DUPLICATE_PENDING_RATIO = 0.35

BLOCKING_WARNING_CODES = {
    "duplicate_pending_density",
    "prediction_mismatch",
    "manual_capture_trigger",
    "occupancy_flicker",
    "no_pending_preference",
    "no_override_event",
}


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _add_gate(
    gates: list[dict[str, Any]],
    *,
    code: str,
    passed: bool,
    message: str,
    actual: Any = None,
    expected: Any = None,
    severity: str = "hard",
) -> None:
    gate = {
        "code": code,
        "passed": bool(passed),
        "severity": severity,
        "message": message,
    }
    if actual is not None:
        gate["actual"] = actual
    if expected is not None:
        gate["expected"] = expected
    gates.append(gate)


def evaluate_candidate_readiness(candidate: dict[str, Any]) -> dict[str, Any]:
    """Strict read-only gate before any proposal or experiment preview."""

    gates: list[dict[str, Any]] = []
    blockers = list(candidate.get("blockers") or [])
    warnings = list(candidate.get("quality_warnings") or [])
    warning_codes = {w.get("code") for w in warnings}
    blocking_warnings = sorted(code for code in warning_codes if code in BLOCKING_WARNING_CODES)

    median_delta = _num(candidate.get("median_delta_pct"))
    delta_variance = _num(candidate.get("delta_variance"))
    opposite_rate = _num(candidate.get("opposite_direction_rate")) or 0.0
    shadow_ratio = _num(candidate.get("shadow_mode_ratio")) or 0.0
    duplicate_ratio = _num(candidate.get("duplicate_pending_ratio")) or 0.0
    evidence_tier = candidate.get("evidence_tier")
    pending_evidence_count = int(candidate.get("pending_evidence_count") or 0)
    evidence_hierarchy = {
        "evidence_tier": evidence_tier,
        "pending_evidence_count": pending_evidence_count,
        "session_intent_count": int(candidate.get("session_intent_count") or 0),
        "automation_tail_session_count": int(candidate.get("automation_tail_session_count") or 0),
    }

    _add_gate(
        gates,
        code="candidate_not_blocked",
        passed=not blockers and candidate.get("status") == "candidate",
        message="candidate has no aggregation blockers",
        actual=blockers,
        expected=[],
    )
    _add_gate(
        gates,
        code="durable_pending_preference",
        passed=(
            evidence_tier == "pending_preference"
            and pending_evidence_count >= MIN_DURABLE_PENDING_PREFERENCES
        ),
        message="candidate needs durable pending_preference evidence before proposal readiness",
        actual=evidence_hierarchy,
        expected=(
            "evidence_tier=pending_preference and "
            f"pending_evidence_count >= {MIN_DURABLE_PENDING_PREFERENCES}"
        ),
    )
    _add_gate(
        gates,
        code="enough_primary_evidence",
        passed=int(candidate.get("evidence_count") or 0) >= MIN_PRIMARY_EVIDENCE,
        message="candidate has enough primary evidence in its policy context",
        actual=candidate.get("evidence_count") or 0,
        expected=f">= {MIN_PRIMARY_EVIDENCE}",
    )
    _add_gate(
        gates,
        code="enough_days_seen",
        passed=int(candidate.get("days_seen") or 0) >= MIN_DAYS_SEEN,
        message="candidate has been observed across enough days",
        actual=candidate.get("days_seen") or 0,
        expected=f">= {MIN_DAYS_SEEN}",
    )
    _add_gate(
        gates,
        code="linked_manual_override",
        passed=int(candidate.get("linked_override_count") or 0) >= MIN_LINKED_OVERRIDES,
        message="candidate has at least one linked manual override touch",
        actual=candidate.get("linked_override_count") or 0,
        expected=f">= {MIN_LINKED_OVERRIDES}",
    )
    _add_gate(
        gates,
        code="effect_size",
        passed=median_delta is not None and abs(median_delta) >= MIN_EFFECT_SIZE_PCT,
        message="median correction is large enough to matter",
        actual=median_delta,
        expected=f"abs(delta) >= {MIN_EFFECT_SIZE_PCT}",
    )
    _add_gate(
        gates,
        code="low_variance",
        passed=delta_variance is not None and delta_variance <= MAX_DELTA_VARIANCE,
        message="corrections are consistent enough to test",
        actual=delta_variance,
        expected=f"<= {MAX_DELTA_VARIANCE}",
    )
    _add_gate(
        gates,
        code="low_opposite_direction_rate",
        passed=opposite_rate <= MAX_OPPOSITE_RATE,
        message="opposite-direction corrections are rare",
        actual=opposite_rate,
        expected=f"<= {MAX_OPPOSITE_RATE}",
    )
    _add_gate(
        gates,
        code="low_shadow_ratio",
        passed=shadow_ratio <= MAX_SHADOW_RATIO,
        message="evidence is mostly real behavior, not shadow-mode",
        actual=shadow_ratio,
        expected=f"<= {MAX_SHADOW_RATIO}",
    )
    _add_gate(
        gates,
        code="low_duplicate_density",
        passed=duplicate_ratio <= MAX_DUPLICATE_PENDING_RATIO,
        message="pending preference captures are not dominated by duplicates",
        actual=duplicate_ratio,
        expected=f"<= {MAX_DUPLICATE_PENDING_RATIO}",
    )
    _add_gate(
        gates,
        code="no_blocking_quality_warnings",
        passed=not blocking_warnings,
        message="candidate has no proposal-blocking quality warnings",
        actual=blocking_warnings,
        expected=[],
    )

    hard_blockers = [
        {
            "code": gate["code"],
            "message": gate["message"],
            "actual": gate.get("actual"),
            "expected": gate.get("expected"),
        }
        for gate in gates
        if gate["severity"] == "hard" and not gate["passed"]
    ]
    soft_warnings = [
        warning
        for warning in warnings
        if warning.get("code") not in BLOCKING_WARNING_CODES
    ]
    ready = not hard_blockers
    return {
        "ready": ready,
        "status": "ready_for_proposal" if ready else "not_ready",
        "candidate_id": candidate.get("id"),
        "zone": candidate.get("zone"),
        "gates": gates,
        "hard_blockers": hard_blockers,
        "soft_warnings": soft_warnings,
        "thresholds": {
            "min_primary_evidence": MIN_PRIMARY_EVIDENCE,
            "min_durable_pending_preferences": MIN_DURABLE_PENDING_PREFERENCES,
            "min_days_seen": MIN_DAYS_SEEN,
            "min_linked_overrides": MIN_LINKED_OVERRIDES,
            "min_effect_size_pct": MIN_EFFECT_SIZE_PCT,
            "max_delta_variance": MAX_DELTA_VARIANCE,
            "max_opposite_rate": MAX_OPPOSITE_RATE,
            "max_shadow_ratio": MAX_SHADOW_RATIO,
            "max_duplicate_pending_ratio": MAX_DUPLICATE_PENDING_RATIO,
            "blocking_warning_codes": sorted(BLOCKING_WARNING_CODES),
        },
    }


def proposal_preview(candidate: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    target = candidate.get("suggested_target") or {}
    context = candidate.get("context") or {}
    median_actual = candidate.get("median_actual_brightness_pct")
    median_predicted = candidate.get("median_predicted_brightness_pct")
    median_delta = candidate.get("median_delta_pct")
    candidate_hash = stable_hash(candidate.get("id", "") + canonical_json(context))[:16]
    preview = {
        "id": f"proposal-preview:{candidate_hash}",
        "kind": "lighting_preference_experiment",
        "status": "preview_only",
        "ready": readiness.get("ready"),
        "title": (
            f"Test {candidate.get('zone', 'unknown')} target "
            f"{target.get('brightness_pct', 'unknown')}% for matching context"
        ),
        "affected_zone": candidate.get("zone"),
        "context": context,
        "proposed_rule": {
            "scope": "helper_or_shadow_parameter_only",
            "target": target,
            "current_median_predicted_brightness_pct": median_predicted,
            "observed_median_actual_brightness_pct": median_actual,
            "median_delta_pct": median_delta,
        },
        "why_now": {
            "evidence_count": candidate.get("evidence_count"),
            "evidence_tier": candidate.get("evidence_tier"),
            "pending_evidence_count": candidate.get("pending_evidence_count"),
            "session_intent_count": candidate.get("session_intent_count"),
            "automation_tail_session_count": candidate.get("automation_tail_session_count"),
            "supporting_evidence_count": candidate.get("supporting_evidence_count"),
            "linked_override_count": candidate.get("linked_override_count"),
            "capture_suppressed_total": candidate.get("capture_suppressed_total"),
            "dedupe_key_count": candidate.get("dedupe_key_count"),
            "duplicate_pending_ratio": candidate.get("duplicate_pending_ratio"),
            "days_seen": candidate.get("days_seen"),
            "readiness_status": readiness.get("status"),
        },
        "success_metric": (
            "manual override rate in the matching context decreases without "
            "increasing opposite-direction overrides"
        ),
        "guardrails": [
            "preview only; no HA writes from this endpoint",
            "human approval required before any experiment starts",
            "start in shadow scoring or helper-parameter trial, not YAML rewrite",
            "ignore duplicate pending captures when evaluating outcomes",
        ],
        "rollback": {
            "condition": (
                "opposite-direction override rate increases, manual override "
                "rate does not improve, or user suppresses the context"
            ),
            "action": "restore previous target/helper value and mark experiment failed",
        },
        "adversarial_critique": readiness.get("hard_blockers") or [
            {
                "code": "needs_human_review",
                "message": "even ready candidates require human review before activation",
            }
        ],
    }
    if not readiness.get("ready"):
        preview["blocked_reason"] = "readiness gates failed"
        blocker_codes = {
            blocker.get("code")
            for blocker in readiness.get("hard_blockers", [])
        }
        if "durable_pending_preference" in blocker_codes:
            preview["blocked_reason"] = "needs durable pending preference confirmation"
    return preview


def fetch_candidate_row(conn: sqlite3.Connection, candidate_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM preference_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
