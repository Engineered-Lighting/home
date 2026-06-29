from __future__ import annotations

import json
import sqlite3
from typing import Any

from .aggregation import aggregate_lighting_preferences
from .ingest import canonical_json, utc_now
from .proposals import generate_proposals


PROPOSAL_STATUSES = {
    "approve": "approved_for_experiment",
    "approved_for_experiment": "approved_for_experiment",
    "reject": "rejected",
    "rejected": "rejected",
    "snooze": "snoozed",
    "snoozed": "snoozed",
    "edit": "edit_requested",
    "edit_requested": "edit_requested",
    "do_not_learn": "suppressed",
    "suppress": "suppressed",
    "suppressed": "suppressed",
}

CLAIM_REVIEW_STATES = {
    "approve": "confirmed",
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "reject": "rejected",
    "rejected": "rejected",
    "snooze": "snoozed",
    "snoozed": "snoozed",
    "edit": "edit_requested",
    "edit_requested": "edit_requested",
    "correction": "correction_requested",
    "wrong": "correction_requested",
    "do_not_learn": "suppressed",
    "suppress": "suppressed",
}


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _decision_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["body"] = _decode_json(d.pop("body_json", None), {})
    return d


def list_decisions(
    conn: sqlite3.Connection,
    *,
    subject_type: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if subject_type:
        where.append("subject_type = ?")
        params.append(subject_type)
    if subject_id:
        where.append("subject_id = ?")
        params.append(subject_id)
    sql = "SELECT * FROM decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_decision_row(row) for row in rows]


def _candidate_from_proposal(conn: sqlite3.Connection, proposal: sqlite3.Row) -> sqlite3.Row | None:
    candidate_id = proposal["candidate_id"]
    if not candidate_id:
        return None
    return conn.execute(
        "SELECT * FROM preference_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()


def _create_suppression_for_candidate(
    conn: sqlite3.Connection,
    *,
    candidate: sqlite3.Row,
    reason: str | None,
    created_at: str,
) -> dict[str, Any]:
    context = _decode_json(candidate["context_json"], {})
    cur = conn.execute(
        """
        INSERT INTO preference_suppression_rules
            (candidate_id, zone, context_json, reason, created_at, active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            candidate["id"],
            candidate["zone"],
            canonical_json(context),
            reason or "suppressed by decision ledger",
            created_at,
        ),
    )
    return {
        "id": cur.lastrowid,
        "candidate_id": candidate["id"],
        "zone": candidate["zone"],
        "context": context,
        "reason": reason or "suppressed by decision ledger",
        "created_at": created_at,
        "active": 1,
    }


def _record_decision(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    decision: str,
    actor: str,
    reason: str | None,
    note: str | None,
    prior_state: str | None,
    new_state: str | None,
    body: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO decisions
            (subject_type, subject_id, decision, actor, reason, note,
             prior_state, new_state, body_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subject_type,
            subject_id,
            decision,
            actor,
            reason,
            note,
            prior_state,
            new_state,
            canonical_json(body),
            created_at,
        ),
    )
    return {
        "id": cur.lastrowid,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "decision": decision,
        "actor": actor,
        "reason": reason,
        "note": note,
        "prior_state": prior_state,
        "new_state": new_state,
        "body": body,
        "created_at": created_at,
    }


def apply_decision(
    conn: sqlite3.Connection,
    *,
    subject_type: str,
    subject_id: str,
    decision: str,
    actor: str = "home_app",
    reason: str | None = None,
    note: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_subject = subject_type.strip().lower()
    normalized_decision = decision.strip().lower()
    payload = dict(body or {})
    created_at = utc_now()
    prior_state: str | None = None
    new_state: str | None = None
    suppression: dict[str, Any] | None = None
    refresh: dict[str, Any] | None = None

    if normalized_subject == "proposal":
        proposal = conn.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (subject_id,),
        ).fetchone()
        if not proposal:
            raise ValueError("proposal not found")
        prior_state = proposal["status"]
        new_state = PROPOSAL_STATUSES.get(normalized_decision)
        if not new_state:
            raise ValueError(f"unsupported proposal decision: {decision}")
        candidate = _candidate_from_proposal(conn, proposal)
        if new_state == "suppressed" and candidate:
            suppression = _create_suppression_for_candidate(
                conn,
                candidate=candidate,
                reason=reason or note,
                created_at=created_at,
            )
        proposal_body = _decode_json(proposal["body_json"], {})
        proposal_body["last_decision"] = {
            "decision": normalized_decision,
            "actor": actor,
            "reason": reason,
            "note": note,
            "created_at": created_at,
            "activation": False,
        }
        if new_state == "approved_for_experiment":
            proposal_body["approval_scope"] = {
                "experiment_design_only": True,
                "ha_writes": False,
                "automation_edits": False,
                "activation": False,
            }
        if normalized_decision == "snooze" and payload.get("snooze_until"):
            proposal_body["snooze_until"] = payload["snooze_until"]
        conn.execute(
            """
            UPDATE proposals
            SET status = ?,
                body_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_state, canonical_json(proposal_body), created_at, subject_id),
        )
        if suppression:
            conn.execute(
                "UPDATE proposals SET status = 'suppressed', updated_at = ? WHERE candidate_id = ?",
                (created_at, candidate["id"]),
            )
            aggregate = aggregate_lighting_preferences(conn)
            proposals = generate_proposals(conn)
            refresh = {"aggregate": aggregate, "proposals": proposals}

    elif normalized_subject == "candidate":
        candidate = conn.execute(
            "SELECT * FROM preference_candidates WHERE id = ?",
            (subject_id,),
        ).fetchone()
        if not candidate:
            raise ValueError("candidate not found")
        prior_state = candidate["status"]
        if normalized_decision in {"do_not_learn", "suppress", "suppressed"}:
            new_state = "suppressed"
            suppression = _create_suppression_for_candidate(
                conn,
                candidate=candidate,
                reason=reason or note,
                created_at=created_at,
            )
            conn.execute(
                "UPDATE proposals SET status = 'suppressed', updated_at = ? WHERE candidate_id = ?",
                (created_at, subject_id),
            )
            aggregate = aggregate_lighting_preferences(conn)
            proposals = generate_proposals(conn)
            refresh = {"aggregate": aggregate, "proposals": proposals}
        elif normalized_decision in {"approve", "approved_for_proposal"}:
            new_state = "approved_for_proposal_review"
        elif normalized_decision in {"reject", "rejected"}:
            new_state = "rejected"
        elif normalized_decision in {"snooze", "snoozed"}:
            new_state = "snoozed"
        elif normalized_decision in {"edit", "edit_requested"}:
            new_state = "edit_requested"
        else:
            raise ValueError(f"unsupported candidate decision: {decision}")

    elif normalized_subject == "wiki_claim":
        claim = conn.execute(
            "SELECT * FROM wiki_claims WHERE id = ?",
            (subject_id,),
        ).fetchone()
        if not claim:
            raise ValueError("wiki claim not found")
        prior_state = claim["review_state"]
        new_state = CLAIM_REVIEW_STATES.get(normalized_decision)
        if not new_state:
            raise ValueError(f"unsupported wiki claim decision: {decision}")
        conn.execute(
            """
            UPDATE wiki_claims
            SET review_state = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_state, created_at, subject_id),
        )
    else:
        raise ValueError(f"unsupported subject type: {subject_type}")

    payload.update(
        {
            "safety": {
                "ha_writes": False,
                "automation_edits": False,
                "cloud_calls": False,
                "policy_activation": False,
            },
            "suppression": suppression,
            "refresh": refresh,
        }
    )
    record = _record_decision(
        conn,
        subject_type=normalized_subject,
        subject_id=subject_id,
        decision=normalized_decision,
        actor=actor,
        reason=reason,
        note=note,
        prior_state=prior_state,
        new_state=new_state,
        body=payload,
        created_at=created_at,
    )
    conn.commit()
    return {
        "ok": True,
        "decision": record,
        "suppression": suppression,
        "refresh": refresh,
    }
