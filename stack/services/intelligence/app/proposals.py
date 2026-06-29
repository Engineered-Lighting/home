from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import rows_to_dicts
from .ingest import canonical_json, stable_hash, utc_now
from .readiness import evaluate_candidate_readiness, proposal_preview


REVIEW_LOCKED_STATUSES = {
    "approved_for_experiment",
    "rejected",
    "snoozed",
    "suppressed",
    "edit_requested",
}


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["context"] = _decode_json(d.pop("context_json", None), {})
    d["suggested_target"] = _decode_json(d.pop("suggested_target_json", None), {})
    d["blockers"] = _decode_json(d.pop("blockers_json", None), [])
    d["quality_warnings"] = _decode_json(d.pop("quality_warnings_json", None), [])
    d["evidence_ids"] = _decode_json(d.pop("evidence_ids_json", None), [])
    return d


def _proposal_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["body"] = _decode_json(d.pop("body_json", None), {})
    d["readiness"] = _decode_json(d.pop("readiness_json", None), {})
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


def _proposal_id(candidate: dict[str, Any]) -> str:
    seed = "|".join(
        [
            "lighting_preference_experiment",
            str(candidate.get("id") or ""),
            canonical_json(candidate.get("context") or {}),
        ]
    )
    return f"proposal:{stable_hash(seed)[:24]}"


def _body_signature(body: dict[str, Any]) -> str:
    comparable = dict(body)
    comparable.pop("generated_at", None)
    return stable_hash(canonical_json(comparable))


def _brief_blockers(readiness: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    blockers = readiness.get("hard_blockers") or []
    return [
        {
            "code": blocker.get("code"),
            "message": blocker.get("message"),
            "actual": blocker.get("actual"),
            "expected": blocker.get("expected"),
        }
        for blocker in blockers[:limit]
    ]


def build_proposal_body(candidate: dict[str, Any], readiness: dict[str, Any]) -> dict[str, Any]:
    preview = proposal_preview(candidate, readiness)
    target = (candidate.get("suggested_target") or {}).copy()
    median_predicted = candidate.get("median_predicted_brightness_pct")
    median_actual = candidate.get("median_actual_brightness_pct")
    median_delta = candidate.get("median_delta_pct")
    duplicate_ratio = candidate.get("duplicate_pending_ratio")
    opposite_rate = candidate.get("opposite_direction_rate")
    warnings = candidate.get("quality_warnings") or []
    evidence_ids = candidate.get("evidence_ids") or []
    generated_at = utc_now()
    return {
        "kind": "lighting_preference_experiment",
        "proposal_version": 1,
        "generated_at": generated_at,
        "candidate_id": candidate.get("id"),
        "candidate_revision": {
            "updated_at": candidate.get("updated_at"),
            "evidence_count": candidate.get("evidence_count"),
            "evidence_tier": candidate.get("evidence_tier"),
            "pending_evidence_count": candidate.get("pending_evidence_count"),
            "session_intent_count": candidate.get("session_intent_count"),
            "automation_tail_session_count": candidate.get("automation_tail_session_count"),
            "days_seen": candidate.get("days_seen"),
            "linked_override_count": candidate.get("linked_override_count"),
            "linked_pending_count": candidate.get("linked_pending_count"),
            "capture_suppressed_total": candidate.get("capture_suppressed_total"),
            "dedupe_key_count": candidate.get("dedupe_key_count"),
            "duplicate_pending_ratio": duplicate_ratio,
        },
        "title": preview["title"],
        "summary": (
            f"{candidate.get('zone', 'unknown')} has repeated manual corrections "
            f"from a median predicted {median_predicted}% to observed {median_actual}% "
            f"in this context."
        ),
        "affected": {
            "zone": candidate.get("zone"),
            "entities": [],
            "scope": "lighting preference only",
        },
        "context": candidate.get("context") or {},
        "proposed_rule": {
            "rule_type": "brightness_target",
            "target": target,
            "current_median_predicted_brightness_pct": median_predicted,
            "observed_median_actual_brightness_pct": median_actual,
            "median_delta_pct": median_delta,
            "implementation_constraint": "shadow scoring or helper parameter only; no YAML rewrite in this phase",
        },
        "why_now": {
            "readiness": readiness.get("status"),
            "passed_gates": [
                gate.get("code")
                for gate in readiness.get("gates", [])
                if gate.get("passed")
            ],
            "evidence_count": candidate.get("evidence_count"),
            "evidence_tier": candidate.get("evidence_tier"),
            "pending_evidence_count": candidate.get("pending_evidence_count"),
            "session_intent_count": candidate.get("session_intent_count"),
            "automation_tail_session_count": candidate.get("automation_tail_session_count"),
            "non_shadow_count": candidate.get("non_shadow_count"),
            "days_seen": candidate.get("days_seen"),
            "linked_override_count": candidate.get("linked_override_count"),
            "quality_warnings": warnings,
        },
        "why_not_proposed_before": {
            "answer": "the readiness gate blocks proposals until all hard gates pass",
            "current_blockers": _brief_blockers(readiness),
            "cleared": readiness.get("ready") is True,
        },
        "expected_benefit": (
            "reduce repeated manual light corrections in this exact context while "
            "keeping the change bounded to one zone/context"
        ),
        "opposite_direction_risk": {
            "rate": opposite_rate,
            "description": (
                "risk is elevated if future corrections move in the opposite direction; "
                "the experiment must watch that metric explicitly"
            ),
        },
        "success_metric": preview["success_metric"],
        "rollback": preview["rollback"],
        "adversarial_critique": preview["adversarial_critique"],
        "safety": {
            "ha_writes": False,
            "automation_edits": False,
            "cloud_calls": False,
            "activation_requires_human": True,
            "allowed_next_step": "human-reviewed shadow experiment design",
        },
        "available_actions": [
            {"action": "approve", "enabled": True, "reason": "approves experiment design only; no activation"},
            {"action": "reject", "enabled": True, "reason": "records a durable rejection"},
            {"action": "edit", "enabled": True, "reason": "records that the proposal needs revision"},
            {"action": "snooze", "enabled": True, "reason": "records a durable snooze decision"},
            {"action": "do_not_learn", "enabled": True, "reason": "creates a suppression rule only"},
        ],
        "evidence_ids": evidence_ids,
        "source_preview": preview,
    }


def generate_proposals(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM preference_candidates
        ORDER BY
          CASE status WHEN 'candidate' THEN 0 ELSE 1 END,
          evidence_count DESC,
          updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    now = utc_now()
    created = 0
    updated = 0
    unchanged = 0
    stale_marked = 0
    generated: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for row in rows:
        candidate = _candidate(row)
        readiness = evaluate_candidate_readiness(candidate)
        existing = conn.execute(
            "SELECT status, body_json FROM proposals WHERE candidate_id = ?",
            (candidate["id"],),
        ).fetchone()

        if not readiness.get("ready"):
            blocked.append(
                {
                    "candidate_id": candidate["id"],
                    "zone": candidate["zone"],
                    "status": candidate["status"],
                    "target": candidate.get("suggested_target") or {},
                    "readiness": readiness,
                    "blockers": _brief_blockers(readiness, limit=8),
                }
            )
            if existing and existing["status"] == "draft":
                body = _decode_json(existing["body_json"], {})
                body["stale_reason"] = "candidate no longer passes readiness gates"
                body["current_readiness"] = readiness
                conn.execute(
                    """
                    UPDATE proposals
                    SET status = 'stale_blocked',
                        body_json = ?,
                        readiness_json = ?,
                        updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (
                        canonical_json(body),
                        canonical_json(readiness),
                        now,
                        candidate["id"],
                    ),
                )
                stale_marked += 1
            continue

        proposal_id = _proposal_id(candidate)
        body = build_proposal_body(candidate, readiness)
        title = body["title"]
        evidence_ids = body["evidence_ids"]
        body_hash = _body_signature(body)
        prior_hash = None
        if existing:
            if existing["status"] in REVIEW_LOCKED_STATUSES:
                unchanged += 1
                continue
            prior_hash = _body_signature(_decode_json(existing["body_json"], {}))
        if existing and existing["status"] == "draft" and prior_hash == body_hash:
            unchanged += 1
            generated.append(
                {
                    "id": proposal_id,
                    "candidate_id": candidate["id"],
                    "zone": candidate["zone"],
                    "title": title,
                    "status": "draft",
                    "evidence_count": len(evidence_ids),
                }
            )
            continue
        conn.execute(
            """
            INSERT INTO proposals
                (id, candidate_id, status, title, body_json, readiness_json,
                 evidence_ids_json, created_at, updated_at)
            VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                candidate_id = excluded.candidate_id,
                status = 'draft',
                title = excluded.title,
                body_json = excluded.body_json,
                readiness_json = excluded.readiness_json,
                evidence_ids_json = excluded.evidence_ids_json,
                updated_at = excluded.updated_at
            """,
            (
                proposal_id,
                candidate["id"],
                title,
                canonical_json(body),
                canonical_json(readiness),
                canonical_json(evidence_ids),
                now,
                now,
            ),
        )
        if not existing:
            created += 1
        else:
            updated += 1
        generated.append(
            {
                "id": proposal_id,
                "candidate_id": candidate["id"],
                "zone": candidate["zone"],
                "title": title,
                "status": "draft",
                "evidence_count": len(evidence_ids),
            }
        )

    conn.commit()
    return {
        "ok": True,
        "generated_at": now,
        "candidate_count": len(rows),
        "ready_count": len(generated),
        "blocked_count": len(blocked),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "stale_marked": stale_marked,
        "proposals": generated,
        "blocked_candidates": blocked,
    }


def list_proposals(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = "SELECT * FROM proposals"
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_proposal_row(row) for row in rows]


def get_proposal_detail(conn: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM proposals WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if not row:
        return None
    proposal = _proposal_row(row)
    candidate = None
    if proposal.get("candidate_id"):
        candidate_row = conn.execute(
            "SELECT * FROM preference_candidates WHERE id = ?",
            (proposal["candidate_id"],),
        ).fetchone()
        if candidate_row:
            candidate = _candidate(candidate_row)
    evidence_ids = proposal.get("evidence_ids") or []
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
    reviews = rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM reviews
            WHERE proposal_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (proposal_id,),
        ).fetchall()
    )
    for review in reviews:
        review["body"] = _decode_json(review.pop("body_json", None), {})
    decisions = rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM decisions
            WHERE subject_type = 'proposal'
              AND subject_id = ?
            ORDER BY id DESC
            LIMIT 50
            """,
            (proposal_id,),
        ).fetchall()
    )
    for decision in decisions:
        decision["body"] = _decode_json(decision.pop("body_json", None), {})
    return {
        "proposal": proposal,
        "candidate": candidate,
        "evidence": evidence,
        "reviews": reviews,
        "decisions": decisions,
    }
