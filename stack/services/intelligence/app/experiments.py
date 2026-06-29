from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .ingest import canonical_json, stable_hash, utc_now


REGENERATABLE_STATUSES = {"inactive_design"}


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _proposal(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["body"] = _decode_json(d.pop("body_json", None), {})
    d["readiness"] = _decode_json(d.pop("readiness_json", None), {})
    d["evidence_ids"] = _decode_json(d.pop("evidence_ids_json", None), [])
    return d


def _candidate(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["context"] = _decode_json(d.pop("context_json", None), {})
    d["suggested_target"] = _decode_json(d.pop("suggested_target_json", None), {})
    d["blockers"] = _decode_json(d.pop("blockers_json", None), [])
    d["quality_warnings"] = _decode_json(d.pop("quality_warnings_json", None), [])
    d["evidence_ids"] = _decode_json(d.pop("evidence_ids_json", None), [])
    return d


def _experiment_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["body"] = _decode_json(d.pop("body_json", None), {})
    d["evidence_ids"] = _decode_json(d.pop("evidence_ids_json", None), [])
    return d


def _observation(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["context"] = _decode_json(d.pop("context_json", None), {})
    d["predictions_by_zone"] = _decode_json(d.pop("predictions_by_zone_json", None), {})
    d["capture_provenance"] = _decode_json(d.pop("capture_provenance_json", None), {})
    d["prediction_consistency"] = _decode_json(d.pop("prediction_consistency_json", None), {})
    d["occupancy"] = _decode_json(d.pop("occupancy_json", None), {})
    return d


def _lighting_decision(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = _decode_json(d.pop("payload_json", None), {})
    return d


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _context_matches(row: sqlite3.Row, context: dict[str, Any]) -> bool:
    for key in (
        "profile",
        "state",
        "tv_playing",
        "asleep",
        "night_safe",
        "user_at_home",
        "light_state",
    ):
        if key in context and row[key] != context.get(key):
            return False
    return True


def _decision_matches(decision: dict[str, Any], context: dict[str, Any]) -> bool:
    payload = decision.get("payload") or {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    profile = inputs.get("tod_state") or inputs.get("tod_bucket")
    if context.get("state") is not None and decision.get("to_state") != context.get("state"):
        return False
    if context.get("profile") is not None and profile != context.get("profile"):
        return False
    return True


def _suppression_matches(
    row: sqlite3.Row,
    *,
    experiment: dict[str, Any],
    zone: str | None,
    context: dict[str, Any],
) -> bool:
    if row["candidate_id"] and row["candidate_id"] != experiment.get("candidate_id"):
        return False
    if row["zone"] and zone and row["zone"] != zone:
        return False
    rule_context = _decode_json(row["context_json"], {})
    if not rule_context:
        return bool(row["candidate_id"] and row["candidate_id"] == experiment.get("candidate_id"))
    return all(context.get(k) == v for k, v in rule_context.items())


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


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _slug(value: str | None) -> str:
    text = "".join(
        ch.lower() if ch.isalnum() else "_"
        for ch in str(value or "unknown")
    )
    parts = [part for part in text.split("_") if part]
    return "_".join(parts) or "unknown"


def _experiment_id(proposal_id: str) -> str:
    return f"experiment:{stable_hash('lighting_preference_experiment|' + proposal_id)[:24]}"


def _body_signature(body: dict[str, Any]) -> str:
    comparable = dict(body)
    comparable.pop("generated_at", None)
    return stable_hash(canonical_json(comparable))


def build_experiment_design(
    proposal: dict[str, Any],
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    body = proposal.get("body") or {}
    proposed_rule = body.get("proposed_rule") or {}
    context = body.get("context") or {}
    affected = body.get("affected") or {}
    evidence_ids = proposal.get("evidence_ids") or body.get("evidence_ids") or []
    generated_at = utc_now()
    zone = affected.get("zone") or (candidate or {}).get("zone")
    return {
        "kind": "lighting_preference_experiment_design",
        "design_version": 1,
        "generated_at": generated_at,
        "proposal_id": proposal.get("id"),
        "candidate_id": proposal.get("candidate_id"),
        "title": f"Inactive experiment design: {proposal.get('title')}",
        "status": "inactive_design",
        "hypothesis": (
            f"In {zone or 'the selected zone'}, applying the proposed target in "
            "the exact approved context should reduce manual lighting corrections "
            "without increasing opposite-direction overrides."
        ),
        "affected": affected,
        "context": context,
        "proposed_rule": proposed_rule,
        "baseline": {
            "lookback_days": 14,
            "source": "preference_observations and episodes before any future activation",
            "candidate_metrics": {
                "evidence_count": (candidate or {}).get("evidence_count"),
                "days_seen": (candidate or {}).get("days_seen"),
                "linked_override_count": (candidate or {}).get("linked_override_count"),
                "median_actual_brightness_pct": (candidate or {}).get("median_actual_brightness_pct"),
                "median_predicted_brightness_pct": (candidate or {}).get("median_predicted_brightness_pct"),
                "median_delta_pct": (candidate or {}).get("median_delta_pct"),
                "opposite_direction_rate": (candidate or {}).get("opposite_direction_rate"),
                "duplicate_pending_ratio": (candidate or {}).get("duplicate_pending_ratio"),
            },
        },
        "trial_design": {
            "duration_days": 5,
            "minimum_matching_context_observations": 5,
            "implementation_mode": "not_implemented_in_m13",
            "future_allowed_modes": [
                "shadow scoring",
                "helper parameter trial after separate approval",
            ],
            "activation_required": True,
            "activation_endpoint_present": False,
        },
        "success_metrics": {
            "primary": (
                "manual override rate in the matching context decreases versus baseline"
            ),
            "guardrails": [
                "opposite-direction override rate does not increase",
                "overall manual override rate for the zone does not increase",
                "duplicate pending captures are excluded from outcome scoring",
                "user suppression or rejection immediately stops the experiment path",
            ],
            "failure_conditions": [
                "opposite-direction override rate increases",
                "manual override rate does not improve after the full window",
                "new quality warnings dominate the evidence",
                "user marks the context do_not_learn",
            ],
        },
        "rollback": body.get("rollback") or {
            "condition": "any failure condition is met",
            "action": "do nothing because M13 never activates policy changes",
        },
        "analysis_plan": {
            "compare": "baseline window vs future trial window",
            "group_by": ["zone", "profile", "state", "tv_playing", "asleep", "night_safe", "user_at_home"],
            "data_sources": [
                "preference_observations",
                "override_events",
                "episodes",
                "lighting_decisions",
            ],
            "requires_human_review_before_activation": True,
        },
        "safety": {
            "ha_writes": False,
            "automation_edits": False,
            "cloud_calls": False,
            "policy_activation": False,
            "can_start_experiment": False,
            "activation_endpoint": None,
        },
        "evidence_ids": evidence_ids,
    }


def generate_experiment_designs(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, Any]:
    proposal_rows = conn.execute(
        """
        SELECT *
        FROM proposals
        WHERE status = 'approved_for_experiment'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    now = utc_now()
    created = 0
    updated = 0
    unchanged = 0
    skipped = 0
    designs: list[dict[str, Any]] = []

    for proposal_row in proposal_rows:
        proposal = _proposal(proposal_row)
        candidate_row = None
        if proposal.get("candidate_id"):
            candidate_row = conn.execute(
                "SELECT * FROM preference_candidates WHERE id = ?",
                (proposal["candidate_id"],),
            ).fetchone()
        candidate = _candidate(candidate_row)
        experiment_id = _experiment_id(proposal["id"])
        body = build_experiment_design(proposal, candidate)
        evidence_ids = body.get("evidence_ids") or []
        existing = conn.execute(
            "SELECT status, body_json FROM experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        body_hash = _body_signature(body)
        prior_hash = _body_signature(_decode_json(existing["body_json"], {})) if existing else None
        if existing and existing["status"] not in REGENERATABLE_STATUSES:
            skipped += 1
            continue
        if existing and prior_hash == body_hash:
            unchanged += 1
            designs.append(
                {
                    "id": experiment_id,
                    "proposal_id": proposal["id"],
                    "candidate_id": proposal.get("candidate_id"),
                    "status": existing["status"],
                    "title": body["title"],
                    "evidence_count": len(evidence_ids),
                }
            )
            continue
        conn.execute(
            """
            INSERT INTO experiments
                (id, proposal_id, candidate_id, status, body_json,
                 evidence_ids_json, created_at, updated_at)
            VALUES (?, ?, ?, 'inactive_design', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                proposal_id = excluded.proposal_id,
                candidate_id = excluded.candidate_id,
                status = 'inactive_design',
                body_json = excluded.body_json,
                evidence_ids_json = excluded.evidence_ids_json,
                updated_at = excluded.updated_at
            """,
            (
                experiment_id,
                proposal["id"],
                proposal.get("candidate_id"),
                canonical_json(body),
                canonical_json(evidence_ids),
                now,
                now,
            ),
        )
        if existing:
            updated += 1
        else:
            created += 1
        designs.append(
            {
                "id": experiment_id,
                "proposal_id": proposal["id"],
                "candidate_id": proposal.get("candidate_id"),
                "status": "inactive_design",
                "title": body["title"],
                "evidence_count": len(evidence_ids),
            }
        )

    conn.commit()
    return {
        "ok": True,
        "generated_at": now,
        "approved_proposal_count": len(proposal_rows),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "experiments": designs,
    }


def list_experiments(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = "SELECT * FROM experiments"
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_experiment_row(row) for row in rows]


def get_experiment_detail(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM experiments WHERE id = ?",
        (experiment_id,),
    ).fetchone()
    if not row:
        return None
    experiment = _experiment_row(row)
    proposal = None
    if experiment.get("proposal_id"):
        proposal_row = conn.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (experiment["proposal_id"],),
        ).fetchone()
        if proposal_row:
            proposal = _proposal(proposal_row)
    evidence_ids = experiment.get("evidence_ids") or []
    evidence: list[dict[str, Any]] = []
    if evidence_ids:
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = conn.execute(
            f"""
            SELECT *
            FROM preference_observations
            WHERE id IN ({placeholders})
            ORDER BY ts
            """,
            evidence_ids,
        ).fetchall()
        evidence = [_observation(r) for r in rows]
    decisions = conn.execute(
        """
        SELECT *
        FROM decisions
        WHERE subject_type = 'experiment'
          AND subject_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (experiment_id,),
    ).fetchall()
    decision_out = [dict(row) for row in decisions]
    for decision in decision_out:
        decision["body"] = _decode_json(decision.pop("body_json", None), {})
    return {
        "experiment": experiment,
        "proposal": proposal,
        "evidence": evidence,
        "decisions": decision_out,
    }


def evaluate_experiment_design(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    lookback_days: int = 14,
    trial_start: str | None = None,
    trial_end: str | None = None,
) -> dict[str, Any] | None:
    detail = get_experiment_detail(conn, experiment_id)
    if not detail:
        return None
    experiment = detail["experiment"]
    proposal = detail.get("proposal") or {}
    body = experiment.get("body") or {}
    context = body.get("context") or {}
    affected = body.get("affected") or {}
    zone = affected.get("zone") or experiment.get("candidate_id")
    trial_design = body.get("trial_design") or {}
    safety = body.get("safety") or {}
    min_observations = int(trial_design.get("minimum_matching_context_observations") or 5)

    observation_rows = conn.execute(
        """
        SELECT *
        FROM preference_observations
        WHERE zone = ?
        ORDER BY ts
        """,
        (zone,),
    ).fetchall() if zone else []
    matching_rows = [row for row in observation_rows if _context_matches(row, context)]
    matching = [_observation(row) for row in matching_rows]
    matching_ids = [row["id"] for row in matching_rows]
    matching_pending = [row for row in matching_rows if row["kind"] == "pending_preference"]
    matching_overrides = [row for row in matching_rows if row["kind"] == "override_event"]
    eligible_rows = [
        row for row in matching_rows
        if not row["shadow_mode"]
        and not (
            row["kind"] == "pending_preference"
            and row["capture_suppressed_count"]
            and row["capture_suppressed_count"] > 0
        )
    ]
    distinct_days = {
        str(row["ts"])[:10]
        for row in eligible_rows
        if row["ts"]
    }
    linked_override_ids = {
        row["related_override_context_id"]
        for row in matching_pending
        if row["related_override_context_id"]
    }
    deltas = [
        float(row["delta_pct"])
        for row in matching_rows
        if row["delta_pct"] is not None
    ]
    negative = sum(1 for delta in deltas if delta < 0)
    positive = sum(1 for delta in deltas if delta > 0)
    opposite_count = min(positive, negative)
    duplicate_saved = sum(int(row["capture_suppressed_count"] or 0) for row in matching_pending)
    shadow_count = sum(1 for row in matching_rows if row["shadow_mode"])

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))
    decision_rows = conn.execute(
        """
        SELECT *
        FROM lighting_decisions
        WHERE zone = ?
        ORDER BY ts
        """,
        (zone,),
    ).fetchall() if zone else []
    recent_decisions: list[dict[str, Any]] = []
    matching_decisions: list[dict[str, Any]] = []
    for row in decision_rows:
        parsed_ts = _parse_ts(row["ts"])
        if parsed_ts is not None and parsed_ts < cutoff:
            continue
        decision = _lighting_decision(row)
        recent_decisions.append(decision)
        if _decision_matches(decision, context):
            matching_decisions.append(decision)

    suppression_rows = conn.execute(
        """
        SELECT *
        FROM preference_suppression_rules
        WHERE active = 1
        """
    ).fetchall()
    active_suppressions = [
        dict(row)
        for row in suppression_rows
        if _suppression_matches(row, experiment=experiment, zone=zone, context=context)
    ]
    proposal_decisions = conn.execute(
        """
        SELECT *
        FROM decisions
        WHERE subject_type = 'proposal'
          AND subject_id = ?
        ORDER BY id DESC
        """,
        (experiment.get("proposal_id"),),
    ).fetchall() if experiment.get("proposal_id") else []
    experiment_decisions = conn.execute(
        """
        SELECT *
        FROM decisions
        WHERE subject_type = 'experiment'
          AND subject_id = ?
        ORDER BY id DESC
        """,
        (experiment_id,),
    ).fetchall()
    terminal_decisions = [
        dict(row)
        for row in [*proposal_decisions, *experiment_decisions]
        if row["new_state"] in {"rejected", "suppressed", "archived"}
        or row["decision"] in {"reject", "do_not_learn", "archive"}
    ]

    gates: list[dict[str, Any]] = []
    _add_gate(
        gates,
        code="inactive_design",
        passed=experiment.get("status") == "inactive_design",
        message="experiment is an inactive design, not a running trial",
        actual=experiment.get("status"),
        expected="inactive_design",
    )
    _add_gate(
        gates,
        code="proposal_still_approved",
        passed=proposal.get("status") == "approved_for_experiment",
        message="source proposal is still approved for design review",
        actual=proposal.get("status"),
        expected="approved_for_experiment",
    )
    _add_gate(
        gates,
        code="safe_design_flags",
        passed=(
            safety.get("ha_writes") is False
            and safety.get("automation_edits") is False
            and safety.get("policy_activation") is False
            and safety.get("can_start_experiment") is False
        ),
        message="design explicitly has no HA writes, automation edits, or policy activation",
        actual=safety,
        expected="all write/activation flags false",
    )
    _add_gate(
        gates,
        code="activation_endpoint_absent",
        passed=trial_design.get("activation_endpoint_present") is False,
        message="M14 exposes evaluation only; no start endpoint exists",
        actual=trial_design.get("activation_endpoint_present"),
        expected=False,
    )
    _add_gate(
        gates,
        code="enough_matching_observations",
        passed=len(eligible_rows) >= min_observations,
        message="baseline has enough matching, non-shadow observations",
        actual=len(eligible_rows),
        expected=f">= {min_observations}",
    )
    _add_gate(
        gates,
        code="linked_manual_overrides",
        passed=len(linked_override_ids) > 0 or len(matching_overrides) > 0,
        message="baseline includes manual override evidence, not only final captures",
        actual=max(len(linked_override_ids), len(matching_overrides)),
        expected=">= 1",
    )
    _add_gate(
        gates,
        code="multi_day_baseline",
        passed=len(distinct_days) >= 2,
        message="baseline spans at least two days",
        actual=len(distinct_days),
        expected=">= 2",
    )
    _add_gate(
        gates,
        code="not_suppressed",
        passed=not active_suppressions,
        message="no active suppression rule blocks this experiment context",
        actual=len(active_suppressions),
        expected=0,
    )
    _add_gate(
        gates,
        code="no_terminal_review_decision",
        passed=not terminal_decisions,
        message="proposal/experiment has not been rejected, archived, or marked do_not_learn",
        actual=len(terminal_decisions),
        expected=0,
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
    review_ready = not hard_blockers
    trial_window_supplied = bool(trial_start and trial_end)
    outcome_status = "not_started"
    outcome_reason = "inactive design has no trial window and no activation marker"
    if trial_window_supplied:
        outcome_status = "trial_window_supplied_but_no_activation_record"
        outcome_reason = (
            "M14 can describe the scoring plan, but the system does not yet "
            "record activated trial windows"
        )

    evaluation = {
        "experiment_id": experiment_id,
        "generated_at": utc_now(),
        "readiness": {
            "ready_for_activation_review": review_ready,
            "status": "ready_for_activation_review" if review_ready else "not_ready",
            "gates": gates,
            "hard_blockers": hard_blockers,
            "thresholds": {
                "minimum_matching_context_observations": min_observations,
                "minimum_baseline_days": 2,
                "activation_endpoint_required_for_start": True,
            },
        },
        "activation": {
            "can_start_experiment": False,
            "activation_endpoint_present": False,
            "reason": "M14 is evaluation-only; future trial activation requires a separate milestone and approval gate",
        },
        "baseline": {
            "lookback_days": lookback_days,
            "zone": zone,
            "context": context,
            "matching_preference_observation_count": len(matching_rows),
            "eligible_observation_count": len(eligible_rows),
            "pending_preference_count": len(matching_pending),
            "override_event_count": len(matching_overrides),
            "linked_override_count": len(linked_override_ids),
            "distinct_days": sorted(distinct_days),
            "shadow_observation_count": shadow_count,
            "duplicate_suppression_saved_count": duplicate_saved,
            "opposite_direction_count": opposite_count,
            "matching_lighting_decision_count": len(matching_decisions),
            "zone_lighting_decision_count": len(recent_decisions),
            "override_rate_per_matching_decision": _safe_rate(len(matching_overrides), len(matching_decisions)),
            "override_rate_per_zone_decision": _safe_rate(len(matching_overrides), len(recent_decisions)),
            "evidence_ids": matching_ids,
        },
        "outcome": {
            "status": outcome_status,
            "reason": outcome_reason,
            "trial_start": trial_start,
            "trial_end": trial_end,
            "comparison_ready": False,
            "success_metric": body.get("success_metrics", {}).get("primary"),
            "guardrails": body.get("success_metrics", {}).get("guardrails") or [],
            "planned_failure_conditions": body.get("success_metrics", {}).get("failure_conditions") or [],
            "scoring_plan": {
                "primary": "compare matching-context manual override rate before vs during trial",
                "guardrail": "fail if opposite-direction or zone-wide override rate increases",
                "exclusions": [
                    "shadow-mode evidence",
                    "duplicate pending captures",
                    "contexts suppressed by the user",
                ],
            },
        },
        "safety": {
            "ha_writes": False,
            "automation_edits": False,
            "cloud_calls": False,
            "policy_activation": False,
        },
        "recent_matching_observations": matching[-10:],
    }
    return evaluation


def list_experiment_evaluations(
    conn: sqlite3.Connection,
    *,
    status: str | None = "inactive_design",
    limit: int = 50,
) -> list[dict[str, Any]]:
    experiments = list_experiments(conn, status=status, limit=limit)
    out: list[dict[str, Any]] = []
    for experiment in experiments:
        evaluation = evaluate_experiment_design(conn, experiment["id"])
        if not evaluation:
            continue
        out.append(
            {
                "experiment": experiment,
                "readiness": evaluation["readiness"],
                "activation": evaluation["activation"],
                "baseline": {
                    key: evaluation["baseline"].get(key)
                    for key in (
                        "zone",
                        "matching_preference_observation_count",
                        "eligible_observation_count",
                        "override_event_count",
                        "linked_override_count",
                        "distinct_days",
                        "matching_lighting_decision_count",
                    )
                },
                "outcome": evaluation["outcome"],
            }
        )
    return out


def _preflight_id(experiment_id: str, evaluation: dict[str, Any]) -> str:
    seed = "activation_preflight|" + experiment_id + "|" + canonical_json(
        {
            "baseline": evaluation.get("baseline") or {},
            "readiness_status": (evaluation.get("readiness") or {}).get("status"),
        }
    )
    return f"preflight:{stable_hash(seed)[:24]}"


def build_activation_preflight(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    lookback_days: int = 14,
) -> dict[str, Any] | None:
    detail = get_experiment_detail(conn, experiment_id)
    if not detail:
        return None
    evaluation = evaluate_experiment_design(
        conn,
        experiment_id,
        lookback_days=lookback_days,
    )
    if not evaluation:
        return None
    experiment = detail["experiment"]
    body = experiment.get("body") or {}
    context = body.get("context") or {}
    affected = body.get("affected") or {}
    proposed_rule = body.get("proposed_rule") or {}
    baseline = evaluation.get("baseline") or {}
    rollback = body.get("rollback") or {}
    preflight_id = _preflight_id(experiment_id, evaluation)

    baseline_snapshot = {
        "id": f"baseline:{stable_hash(preflight_id + '|baseline')[:24]}",
        "status": "computed_read_only",
        "source": "current intelligence SQLite state",
        "lookback_days": lookback_days,
        "zone": baseline.get("zone"),
        "context": baseline.get("context"),
        "matching_observations": baseline.get("matching_preference_observation_count"),
        "eligible_observations": baseline.get("eligible_observation_count"),
        "linked_overrides": baseline.get("linked_override_count"),
        "distinct_days": baseline.get("distinct_days") or [],
        "evidence_ids": baseline.get("evidence_ids") or [],
    }
    rollback_package = {
        "id": f"rollback:{stable_hash(preflight_id + '|rollback')[:24]}",
        "status": "spec_only",
        "checkpoint_required": True,
        "checkpoint_present": False,
        "condition": rollback.get("condition") or "experiment guardrail fails",
        "action": rollback.get("action") or "restore previous helper/policy value",
        "requires_captured_prior_state": True,
        "prior_state_present": False,
    }
    monitor_spec = {
        "id": f"monitor:{stable_hash(preflight_id + '|monitor')[:24]}",
        "status": "spec_only",
        "job_present": False,
        "cadence": "hourly during future trial window",
        "metrics": [
            "matching-context manual override rate",
            "opposite-direction override rate",
            "zone-wide manual override rate",
            "suppression or rejection decisions",
        ],
        "stop_conditions": body.get("success_metrics", {}).get("failure_conditions") or [],
    }
    required_artifacts = [
        {
            "artifact": "human_trial_activation_approval",
            "status": "missing_future_decision",
            "required": True,
            "description": "separate explicit approval to start a concrete trial window",
        },
        {
            "artifact": "activation_endpoint",
            "status": "missing_future_api",
            "required": True,
            "description": "no start endpoint exists in M15",
        },
        {
            "artifact": "implementation_manifest",
            "status": "missing_future_artifact",
            "required": True,
            "description": "exact helper/shadow parameter writes must be enumerated before any activation",
        },
        {
            "artifact": "rollback_checkpoint",
            "status": "missing_future_artifact",
            "required": True,
            "description": "prior helper/policy values must be captured before trial start",
        },
        {
            "artifact": "monitor_job",
            "status": "missing_future_artifact",
            "required": True,
            "description": "trial monitoring job must exist before activation",
        },
        {
            "artifact": "baseline_snapshot",
            "status": "computed_read_only",
            "required": True,
            "description": "current baseline is computed for review but not persisted as an activation checkpoint",
            "id": baseline_snapshot["id"],
        },
        {
            "artifact": "rollback_spec",
            "status": "computed_read_only",
            "required": True,
            "description": "rollback intent is available but lacks captured prior state",
            "id": rollback_package["id"],
        },
        {
            "artifact": "monitor_spec",
            "status": "computed_read_only",
            "required": True,
            "description": "monitoring intent is available but not scheduled",
            "id": monitor_spec["id"],
        },
    ]

    gates: list[dict[str, Any]] = []
    _add_gate(
        gates,
        code="evaluation_ready_for_activation_review",
        passed=bool(evaluation.get("readiness", {}).get("ready_for_activation_review")),
        message="experiment passes read-only evaluation gates",
        actual=evaluation.get("readiness", {}).get("status"),
        expected="ready_for_activation_review",
    )
    _add_gate(
        gates,
        code="explicit_activation_approval_present",
        passed=False,
        message="a separate human activation approval is required and does not exist yet",
        actual=False,
        expected=True,
    )
    _add_gate(
        gates,
        code="activation_endpoint_present",
        passed=False,
        message="M15 still has no experiment activation endpoint",
        actual=False,
        expected=True,
    )
    _add_gate(
        gates,
        code="implementation_manifest_present",
        passed=False,
        message="future activation must enumerate exact helper/shadow writes before start",
        actual=False,
        expected=True,
    )
    _add_gate(
        gates,
        code="rollback_checkpoint_present",
        passed=False,
        message="future activation must capture prior state before start",
        actual=False,
        expected=True,
    )
    _add_gate(
        gates,
        code="monitor_job_present",
        passed=False,
        message="future activation must install a read-only outcome monitor before start",
        actual=False,
        expected=True,
    )
    _add_gate(
        gates,
        code="bounded_scope",
        passed=bool(affected.get("zone") and context),
        message="experiment scope is bounded to one zone/context",
        actual={"zone": affected.get("zone"), "context_keys": sorted(context.keys())},
        expected="zone plus non-empty context",
    )
    _add_gate(
        gates,
        code="m15_no_write_path",
        passed=True,
        message="M15 preflight is read-only and cannot write HA or policy state",
        actual={"ha_writes": False, "automation_edits": False, "policy_activation": False},
        expected="no writes",
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
    preflight = {
        "id": preflight_id,
        "experiment_id": experiment_id,
        "generated_at": utc_now(),
        "status": "blocked_missing_activation_artifacts",
        "can_activate": False,
        "activation_api_present": False,
        "reason": "M15 defines the required activation package but intentionally cannot start experiments",
        "review_ready": bool(evaluation.get("readiness", {}).get("ready_for_activation_review")),
        "gates": gates,
        "hard_blockers": hard_blockers,
        "required_artifacts": required_artifacts,
        "baseline_snapshot": baseline_snapshot,
        "rollback_package": rollback_package,
        "monitor_spec": monitor_spec,
        "implementation_manifest": {
            "status": "missing_future_artifact",
            "allowed_modes": body.get("trial_design", {}).get("future_allowed_modes") or [],
            "proposed_rule": proposed_rule,
            "disallowed": [
                "YAML automation rewrite",
                "unbounded multi-zone policy change",
                "cloud-only activation dependency",
            ],
        },
        "activation_request_template": {
            "status": "template_only",
            "requires_user_confirmation": True,
            "requires_preflight_rerun": True,
            "requires_fresh_backup": True,
            "requires_rollback_checkpoint": True,
            "requires_monitor_job": True,
            "trial_duration_days": body.get("trial_design", {}).get("duration_days"),
            "scope": {
                "affected": affected,
                "context": context,
            },
        },
        "safety": {
            "ha_writes": False,
            "automation_edits": False,
            "cloud_calls": False,
            "policy_activation": False,
            "can_start_experiment": False,
        },
    }
    return preflight


def list_activation_preflights(
    conn: sqlite3.Connection,
    *,
    status: str | None = "inactive_design",
    limit: int = 50,
) -> list[dict[str, Any]]:
    experiments = list_experiments(conn, status=status, limit=limit)
    out: list[dict[str, Any]] = []
    for experiment in experiments:
        preflight = build_activation_preflight(conn, experiment["id"])
        if not preflight:
            continue
        out.append(
            {
                "experiment": experiment,
                "preflight": {
                    "id": preflight["id"],
                    "status": preflight["status"],
                    "can_activate": preflight["can_activate"],
                    "review_ready": preflight["review_ready"],
                    "hard_blockers": preflight["hard_blockers"],
                    "required_artifacts": preflight["required_artifacts"],
                },
                "baseline_snapshot": {
                    "id": preflight["baseline_snapshot"]["id"],
                    "eligible_observations": preflight["baseline_snapshot"]["eligible_observations"],
                    "linked_overrides": preflight["baseline_snapshot"]["linked_overrides"],
                    "distinct_days": preflight["baseline_snapshot"]["distinct_days"],
                },
            }
        )
    return out


def _manifest_id(experiment_id: str, preflight_id: str) -> str:
    return f"manifest:{stable_hash('activation_manifest|' + experiment_id + '|' + preflight_id)[:24]}"


def build_activation_manifest(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    lookback_days: int = 14,
) -> dict[str, Any] | None:
    detail = get_experiment_detail(conn, experiment_id)
    if not detail:
        return None
    preflight = build_activation_preflight(
        conn,
        experiment_id,
        lookback_days=lookback_days,
    )
    if not preflight:
        return None
    experiment = detail["experiment"]
    body = experiment.get("body") or {}
    context = body.get("context") or {}
    affected = body.get("affected") or {}
    proposed_rule = body.get("proposed_rule") or {}
    target = proposed_rule.get("target") or {}
    zone = affected.get("zone") or preflight.get("baseline_snapshot", {}).get("zone") or "unknown"
    zone_slug = _slug(zone)
    context_hash = stable_hash(canonical_json(context))[:10]
    manifest_id = _manifest_id(experiment_id, preflight["id"])
    helper_prefix = f"home_intel_exp_{zone_slug}_{context_hash}"
    target_brightness = target.get("brightness_pct")
    light_state = target.get("light_state") or ("off" if target_brightness == 0 else "on")
    trial_duration_days = body.get("trial_design", {}).get("duration_days")

    write_set = [
        {
            "id": "write:target_brightness_pct",
            "kind": "ha_service_call",
            "domain": "input_number",
            "service": "set_value",
            "target": {
                "entity_id": f"input_number.{helper_prefix}_target_brightness_pct",
            },
            "data": {
                "value": target_brightness,
            },
            "requires_existing_entity": True,
            "hypothetical_only": True,
            "source": "experiment.body.proposed_rule.target.brightness_pct",
        },
        {
            "id": "write:target_light_state",
            "kind": "ha_service_call",
            "domain": "input_text",
            "service": "set_value",
            "target": {
                "entity_id": f"input_text.{helper_prefix}_target_light_state",
            },
            "data": {
                "value": light_state,
            },
            "requires_existing_entity": True,
            "hypothetical_only": True,
            "source": "experiment.body.proposed_rule.target.light_state",
        },
        {
            "id": "write:context_matcher",
            "kind": "ha_service_call",
            "domain": "input_text",
            "service": "set_value",
            "target": {
                "entity_id": f"input_text.{helper_prefix}_context_json",
            },
            "data": {
                "value": canonical_json(context),
            },
            "requires_existing_entity": True,
            "hypothetical_only": True,
            "source": "experiment.body.context",
        },
        {
            "id": "write:trial_window_days",
            "kind": "ha_service_call",
            "domain": "input_number",
            "service": "set_value",
            "target": {
                "entity_id": f"input_number.{helper_prefix}_trial_duration_days",
            },
            "data": {
                "value": trial_duration_days,
            },
            "requires_existing_entity": True,
            "hypothetical_only": True,
            "source": "experiment.body.trial_design.duration_days",
        },
        {
            "id": "write:trial_enabled",
            "kind": "ha_service_call",
            "domain": "input_boolean",
            "service": "turn_on",
            "target": {
                "entity_id": f"input_boolean.{helper_prefix}_enabled",
            },
            "data": {},
            "requires_existing_entity": True,
            "hypothetical_only": True,
            "source": "future explicit activation decision",
        },
    ]
    shadow_parameter_patch = {
        "id": "patch:shadow_parameter_trial",
        "kind": "sidecar_shadow_config_patch",
        "path": f"/data/runtime/intelligence/experiments/{manifest_id}.json",
        "operation": "write_json",
        "hypothetical_only": True,
        "payload": {
            "experiment_id": experiment_id,
            "manifest_id": manifest_id,
            "zone": zone,
            "context": context,
            "target": target,
            "duration_days": trial_duration_days,
            "success_metrics": body.get("success_metrics") or {},
        },
    }
    rollback_checkpoint_schema = {
        "id": f"rollback-checkpoint:{stable_hash(manifest_id + '|checkpoint')[:24]}",
        "status": "schema_only",
        "must_capture_before_apply": True,
        "required_fields": [
            "captured_at",
            "manifest_id",
            "experiment_id",
            "helper_entity_states",
            "shadow_config_hash",
            "prior_policy_revision",
            "baseline_snapshot_id",
        ],
        "helper_entity_states": [
            {
                "entity_id": item["target"]["entity_id"],
                "required": True,
                "state": None,
                "attributes": None,
            }
            for item in write_set
            if item.get("kind") == "ha_service_call"
        ],
        "restore_plan": [
            {
                "entity_id": item["target"]["entity_id"],
                "restore": "restore captured prior state and attributes",
            }
            for item in write_set
            if item.get("kind") == "ha_service_call"
        ],
        "checkpoint_present": False,
    }
    validation_plan = {
        "must_pass_before_apply": [
            "rerun activation preflight",
            "verify explicit activation approval",
            "verify all helper entities exist",
            "capture rollback checkpoint",
            "install monitor job",
            "verify fresh intelligence DB backup",
        ],
        "post_apply_smoke": [
            "confirm trial_enabled helper state",
            "confirm shadow config hash",
            "confirm monitor job scheduled",
            "confirm no YAML automation files changed",
        ],
    }
    return {
        "id": manifest_id,
        "kind": "lighting_experiment_activation_manifest",
        "manifest_version": 1,
        "generated_at": utc_now(),
        "experiment_id": experiment_id,
        "preflight_id": preflight["id"],
        "status": "draft_read_only",
        "can_apply": False,
        "apply_endpoint_present": False,
        "reason": "M16 generates an exact future write manifest but intentionally has no apply path",
        "scope": {
            "affected": affected,
            "zone": zone,
            "context_hash": context_hash,
            "context_matcher": {
                "match_type": "exact_policy_context",
                "context": context,
            },
        },
        "proposed_change": {
            "implementation_mode": "helper_parameter_or_shadow_scoring_trial",
            "target": target,
            "current_median_predicted_brightness_pct": proposed_rule.get("current_median_predicted_brightness_pct"),
            "observed_median_actual_brightness_pct": proposed_rule.get("observed_median_actual_brightness_pct"),
            "median_delta_pct": proposed_rule.get("median_delta_pct"),
        },
        "write_set": write_set,
        "shadow_parameter_patch": shadow_parameter_patch,
        "rollback_checkpoint_schema": rollback_checkpoint_schema,
        "validation_plan": validation_plan,
        "required_preflight_state": {
            "preflight_status": preflight["status"],
            "preflight_can_activate": preflight["can_activate"],
            "missing_artifact_count": len(
                [
                    artifact
                    for artifact in preflight.get("required_artifacts", [])
                    if str(artifact.get("status") or "").startswith("missing")
                ]
            ),
        },
        "safety": {
            "ha_writes": False,
            "automation_edits": False,
            "cloud_calls": False,
            "policy_activation": False,
            "can_start_experiment": False,
            "can_apply_manifest": False,
        },
    }


def list_activation_manifests(
    conn: sqlite3.Connection,
    *,
    status: str | None = "inactive_design",
    limit: int = 50,
) -> list[dict[str, Any]]:
    experiments = list_experiments(conn, status=status, limit=limit)
    out: list[dict[str, Any]] = []
    for experiment in experiments:
        manifest = build_activation_manifest(conn, experiment["id"])
        if not manifest:
            continue
        out.append(
            {
                "experiment": experiment,
                "manifest": {
                    "id": manifest["id"],
                    "status": manifest["status"],
                    "can_apply": manifest["can_apply"],
                    "apply_endpoint_present": manifest["apply_endpoint_present"],
                    "write_count": len(manifest["write_set"]),
                    "shadow_patch": manifest["shadow_parameter_patch"],
                    "rollback_checkpoint_id": manifest["rollback_checkpoint_schema"]["id"],
                    "missing_artifact_count": manifest["required_preflight_state"]["missing_artifact_count"],
                },
            }
        )
    return out
