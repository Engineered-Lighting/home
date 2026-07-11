from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .db import connect, default_db_path, init_db, rows_to_dicts
from .decisions import apply_decision, list_decisions
from .evidence_sources import list_evidence_sources
from .evidence_links import build_evidence_link_audit
from .experiments import (
    build_activation_manifest,
    build_activation_preflight,
    evaluate_experiment_design,
    get_experiment_detail,
    list_activation_manifests,
    list_activation_preflights,
    list_experiment_evaluations,
    list_experiments,
)
from .ingest import canonical_json, utc_now
from .lighting_activity import list_recent_lighting_activity, rebuild_lighting_change_episodes
from .jobs import (
    execute_daily_memory,
    execute_evidence_pull,
    execute_experiment_design_generation,
    execute_ingest_and_aggregate,
    execute_multimodal_capture_pilot,
    execute_proposal_generation,
)
from .memory import get_journal, get_wiki_claim_detail, list_journals, list_wiki_claims
from .multimodal import (
    accept_qwen_label_eval_item,
    audit_label_eval_item,
    capture_packet_frames,
    create_training_clip_manifest,
    create_observation_packet,
    create_recent_observation_packets,
    delete_packet_frames,
    get_frame_path,
    get_label_eval_set,
    get_observation_packet,
    get_training_clip_manifest,
    label_eval_queue,
    label_eval_status,
    label_next_packets,
    label_observation_packet,
    list_label_eval_sets,
    list_qwen_prompt_trials,
    list_training_clip_manifests,
    list_observation_packets,
    multimodal_status,
    observation_map,
    qwen_prompt_gate,
    record_packet_audit,
    run_qwen_prompt_trial,
    score_label_eval_set,
    seed_label_eval_set,
    start_ring_buffer,
    stop_ring_buffer,
    validate_qwen_labels,
)
from .override_sessions import candidate_override_sessions, list_recent_override_sessions
from .proposals import get_proposal_detail, list_proposals
from .readiness import evaluate_candidate_readiness, fetch_candidate_row, proposal_preview
from .review import build_soak_review
from .sandbox import explain_synthetic_experiment_chain, run_synthetic_experiment_chain
from .scheduler import scheduler_status, start_scheduler, stop_scheduler


INTELLIGENCE_READ_ONLY = os.environ.get("INTELLIGENCE_READ_ONLY", "1").strip().lower() not in {
    "0", "false", "no", "off",
}

app = FastAPI(title="Home Intelligence Core", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def quarantine_mutations(request: Request, call_next):
    """Keep the legacy evidence lab read-only unless an operator overrides it."""
    if INTELLIGENCE_READ_ONLY and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        return JSONResponse(
            {"error": "capability_disabled", "detail": "legacy Intelligence is read-only"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)


@contextmanager
def db_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


class SuppressionIn(BaseModel):
    zone: str | None = None
    candidate_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    suppress_entire_zone: bool = False


class DecisionIn(BaseModel):
    decision: str
    actor: str = "home_app"
    reason: str | None = None
    note: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)


class ObservationPacketCaptureIn(BaseModel):
    observation_id: str
    capture_frames: bool = True
    force: bool = False
    trigger: str = "home_app"
    post_frame_delay_s: float = Field(default=0.0, ge=0.0, le=15.0)


class ObservationPacketAuditIn(BaseModel):
    action: str
    actor: str = "home_app"
    note: str | None = None
    body: dict[str, Any] = Field(default_factory=dict)


class QwenLabelValidationIn(BaseModel):
    payload: dict[str, Any]


class LabelEvalSeedIn(BaseModel):
    name: str = "heldout_v1"
    target_size: int = Field(default=50, ge=1, le=200)
    require_valid_labels: bool = False


class LabelEvalAuditIn(BaseModel):
    actor: str = "home_app"
    note: str | None = None
    human_labels: dict[str, Any] = Field(default_factory=dict)


class QwenPromptTrialIn(BaseModel):
    candidate_name: str = "candidate"
    eval_set_id: str | None = None
    candidate_prompt: dict[str, Any] = Field(default_factory=dict)
    candidate_labels_by_item: dict[str, Any] = Field(default_factory=dict)


class TrainingClipManifestIn(BaseModel):
    packet_id: str
    tier: str = "short_clip"
    actor: str = "home_app"


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    import json

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


def _observation(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["context"] = _decode_json(d.pop("context_json", None), {})
    d["predictions_by_zone"] = _decode_json(d.pop("predictions_by_zone_json", None), {})
    d["capture_provenance"] = _decode_json(d.pop("capture_provenance_json", None), {})
    d["prediction_consistency"] = _decode_json(d.pop("prediction_consistency_json", None), {})
    d["occupancy"] = _decode_json(d.pop("occupancy_json", None), {})
    return d


def _observation_policy_context(observation: dict[str, Any]) -> dict[str, Any]:
    # MC.1 — must mirror _candidate_context() in aggregation.py and
    # policy_context() in override_sessions.py exactly. Mismatched key
    # sets break the candidate ↔ evidence `==`-compare that determines
    # primary vs supporting evidence_role.
    return {
        "profile": observation.get("profile"),
        "state": observation.get("state"),
        "tv_playing": observation.get("tv_playing"),
        "gaming_active": observation.get("gaming_active"),
        "asleep": observation.get("asleep"),
        "night_safe": observation.get("night_safe"),
        "user_at_home": observation.get("user_at_home"),
        "light_state": observation.get("light_state"),
    }


def _evidence_observation(row: sqlite3.Row) -> dict[str, Any]:
    d = _observation(row)
    raw = {
        "id": d.pop("raw_id", None),
        "source": d.pop("raw_source", None),
        "source_path": d.pop("raw_source_path", None),
        "line_no": d.pop("raw_line_no", None),
        "source_hash": d.pop("raw_source_hash", None),
        "redacted_payload": _decode_json(d.pop("raw_redacted_payload_json", None), {}),
    }
    d["raw_event"] = raw
    return d


@app.on_event("startup")
async def startup() -> None:
    with db_conn() as conn:
        init_db(conn)
    start_scheduler()
    start_ring_buffer()


@app.on_event("shutdown")
async def shutdown() -> None:
    await stop_ring_buffer()
    await stop_scheduler()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        counts = {}
        for table in (
            "raw_events",
            "preference_observations",
            "preference_candidates",
            "lighting_decisions",
            "lighting_change_events",
            "lighting_command_events",
            "lighting_change_episodes",
            "journals",
            "wiki_claims",
            "jobs",
            "proposals",
            "experiments",
            "decisions",
            "observation_packets",
            "observation_frames",
            "qwen_label_results",
            "training_clip_manifests",
            "observation_audits",
            "label_eval_sets",
            "label_eval_items",
            "qwen_prompt_trials",
        ):
            counts[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        evidence_sources = list_evidence_sources(conn)
    return {
        "ok": True,
        "db": str(default_db_path()),
        "counts": counts,
        "evidence_sources": evidence_sources,
    }


@app.post("/api/intelligence/ingest/run")
def run_ingest() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_ingest_and_aggregate(conn, source="manual")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/evidence/pull/run")
def run_evidence_pull() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_evidence_pull(conn, source="manual")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/evidence-sources")
def evidence_sources() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, "evidence_sources": list_evidence_sources(conn)}


@app.get("/api/intelligence/today")
def today() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        counts = {
            "observations": conn.execute("SELECT COUNT(*) AS c FROM preference_observations").fetchone()["c"],
            "candidates": conn.execute("SELECT COUNT(*) AS c FROM preference_candidates").fetchone()["c"],
            "ready_candidates": conn.execute(
                "SELECT COUNT(*) AS c FROM preference_candidates WHERE status = 'candidate'"
            ).fetchone()["c"],
            "blocked_candidates": conn.execute(
                "SELECT COUNT(*) AS c FROM preference_candidates WHERE status = 'blocked'"
            ).fetchone()["c"],
            "suppression_rules": conn.execute(
                "SELECT COUNT(*) AS c FROM preference_suppression_rules WHERE active = 1"
            ).fetchone()["c"],
            "episodes": conn.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"],
            "proposals": conn.execute(
                "SELECT COUNT(*) AS c FROM proposals WHERE status = 'draft'"
            ).fetchone()["c"],
            "experiments": conn.execute(
                "SELECT COUNT(*) AS c FROM experiments WHERE status = 'inactive_design'"
            ).fetchone()["c"],
            "decisions": conn.execute("SELECT COUNT(*) AS c FROM decisions").fetchone()["c"],
            "observation_packets": conn.execute(
                "SELECT COUNT(*) AS c FROM observation_packets"
            ).fetchone()["c"],
            "qwen_labels": conn.execute(
                "SELECT COUNT(*) AS c FROM qwen_label_results WHERE status = 'valid'"
            ).fetchone()["c"],
            "label_eval_sets": conn.execute(
                "SELECT COUNT(*) AS c FROM label_eval_sets"
            ).fetchone()["c"],
            "label_eval_items": conn.execute(
                "SELECT COUNT(*) AS c FROM label_eval_items"
            ).fetchone()["c"],
            "qwen_prompt_trials": conn.execute(
                "SELECT COUNT(*) AS c FROM qwen_prompt_trials"
            ).fetchone()["c"],
            "training_clip_manifests": conn.execute(
                "SELECT COUNT(*) AS c FROM training_clip_manifests"
            ).fetchone()["c"],
            "lighting_change_events": conn.execute(
                "SELECT COUNT(*) AS c FROM lighting_change_events"
            ).fetchone()["c"],
            "lighting_command_events": conn.execute(
                "SELECT COUNT(*) AS c FROM lighting_command_events"
            ).fetchone()["c"],
            "lighting_change_episodes": conn.execute(
                "SELECT COUNT(*) AS c FROM lighting_change_episodes"
            ).fetchone()["c"],
        }
        candidate_rows = conn.execute(
            """
            SELECT *
            FROM preference_candidates
            ORDER BY
              CASE status WHEN 'candidate' THEN 0 ELSE 1 END,
              evidence_count DESC,
              updated_at DESC
            LIMIT 8
            """
        ).fetchall()
        last_job = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        evidence_sources = list_evidence_sources(conn)
    return {
        "ok": True,
        "counts": counts,
        "top_candidates": [_candidate(r) for r in candidate_rows],
        "last_job": dict(last_job) if last_job else None,
        "evidence_sources": evidence_sources,
    }


@app.get("/api/intelligence/soak-review")
def soak_review(hours: float = Query(24.0, ge=1.0, le=168.0)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        review = build_soak_review(conn, hours=hours)
        review["evidence_sources"] = list_evidence_sources(conn)
        return review


@app.get("/api/intelligence/override-sessions")
def override_sessions(
    zone: str | None = None,
    hours: float = Query(24.0, ge=1.0, le=168.0),
    limit: int = Query(50, ge=1, le=200),
    include_events: bool = False,
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return list_recent_override_sessions(
            conn,
            zone=zone,
            hours=hours,
            limit=limit,
            include_events=include_events,
        )


@app.post("/api/intelligence/lighting-activity/rebuild")
def rebuild_lighting_activity() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return rebuild_lighting_change_episodes(conn)


@app.get("/api/intelligence/lighting-activity")
def lighting_activity(
    zone: str | None = None,
    hours: float = Query(24.0, ge=1.0, le=168.0),
    limit: int = Query(80, ge=1, le=300),
    include_raw: bool = False,
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return list_recent_lighting_activity(
            conn,
            zone=zone,
            hours=hours,
            limit=limit,
            include_raw=include_raw,
        )


@app.get("/api/intelligence/evidence-link-audit")
def evidence_link_audit(
    zone: str | None = None,
    hours: float = Query(24.0, ge=1.0, le=168.0),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return build_evidence_link_audit(conn, zone=zone, hours=hours, limit=limit)


@app.post("/api/intelligence/memory/run")
def run_memory(date: str | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_daily_memory(conn, date=date, source="manual")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/scheduler")
def scheduler() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, "scheduler": scheduler_status(conn)}


@app.get("/api/intelligence/journals")
def journals(limit: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, "journals": list_journals(conn, limit=limit)}


@app.get("/api/intelligence/journals/{journal_date}")
def journal_detail(journal_date: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            journal = get_journal(conn, journal_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not journal:
            raise HTTPException(status_code=404, detail="journal not found")
        return {"ok": True, "journal": journal}


@app.get("/api/intelligence/wiki-claims")
def wiki_claims(
    limit: int = Query(50, ge=1, le=200),
    review_state: str | None = None,
    claim_type: str | None = None,
    zone: str | None = None,
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "claims": list_wiki_claims(
                conn,
                limit=limit,
                review_state=review_state,
                claim_type=claim_type,
                zone=zone,
                min_confidence=min_confidence,
            ),
        }


@app.get("/api/intelligence/wiki-claims/{claim_id}")
def wiki_claim_detail(claim_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        detail = get_wiki_claim_detail(conn, claim_id)
        if not detail:
            raise HTTPException(status_code=404, detail="wiki claim not found")
        detail["decisions"] = list_decisions(
            conn,
            subject_type="wiki_claim",
            subject_id=claim_id,
            limit=50,
        )
        return {"ok": True, **detail}


@app.post("/api/intelligence/wiki-claims/{claim_id}/decisions")
def wiki_claim_decision(claim_id: str, body: DecisionIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return apply_decision(
                conn,
                subject_type="wiki_claim",
                subject_id=claim_id,
                decision=body.decision,
                actor=body.actor,
                reason=body.reason,
                note=body.note,
                body=body.body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/jobs")
def jobs(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = rows_to_dicts(rows)
    for row in out:
        row["details"] = _decode_json(row.pop("details_json", None), {})
    return {"ok": True, "jobs": out}


@app.get("/api/intelligence/candidates")
def candidates(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    sql = "SELECT * FROM preference_candidates"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY evidence_count DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(sql, params).fetchall()
    return {"ok": True, "candidates": [_candidate(r) for r in rows]}


@app.get("/api/intelligence/candidates/{candidate_id}")
def candidate_detail(candidate_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT * FROM preference_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        candidate = _candidate(row)
        evidence_ids = candidate.get("evidence_ids") or []
        evidence: list[dict[str, Any]] = []
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            rows = conn.execute(
                f"""
                SELECT
                    po.*,
                    re.id AS raw_id,
                    re.source AS raw_source,
                    re.source_path AS raw_source_path,
                    re.line_no AS raw_line_no,
                    re.source_hash AS raw_source_hash,
                    re.redacted_payload_json AS raw_redacted_payload_json
                FROM preference_observations po
                JOIN raw_events re ON re.id = po.raw_event_id
                WHERE po.id IN ({placeholders})
                ORDER BY po.ts
                """,
                evidence_ids,
            ).fetchall()
            evidence = [_evidence_observation(r) for r in rows]
            candidate_context = candidate.get("context") or {}
            for item in evidence:
                policy_context = _observation_policy_context(item)
                item["policy_context"] = policy_context
                item["evidence_role"] = (
                    "primary" if policy_context == candidate_context else "supporting"
                )
        decisions = conn.execute(
            """
            SELECT *
            FROM lighting_decisions
            WHERE zone = ?
            ORDER BY ts DESC
            LIMIT 20
            """,
            (candidate["zone"],),
        ).fetchall()
        episode_rows = conn.execute(
            """
            SELECT *
            FROM episodes
            WHERE zone = ?
              AND kind = 'lighting_preference'
            ORDER BY end_ts DESC
            LIMIT 100
            """,
            (candidate["zone"],),
        ).fetchall()
        revisions = conn.execute(
            """
            SELECT *
            FROM preference_candidate_revisions
            WHERE candidate_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (candidate_id,),
        ).fetchall()
        decision_rows = list_decisions(
            conn,
            subject_type="candidate",
            subject_id=candidate_id,
            limit=50,
        )
        sessions = candidate_override_sessions(
            conn,
            candidate,
            include_events=True,
            limit=20,
        )
    decision_out = rows_to_dicts(decisions)
    for decision in decision_out:
        decision["payload"] = _decode_json(decision.pop("payload_json", None), {})
    revision_out = rows_to_dicts(revisions)
    for revision in revision_out:
        revision["metrics"] = _decode_json(revision.pop("metrics_json", None), {})
        revision["blockers"] = _decode_json(revision.pop("blockers_json", None), [])
        revision["evidence_ids"] = _decode_json(revision.pop("evidence_ids_json", None), [])
    evidence_id_set = set(candidate.get("evidence_ids") or [])
    episode_out = []
    for episode in rows_to_dicts(episode_rows):
        summary = _decode_json(episode.pop("summary_json", None), {})
        if not evidence_id_set.intersection(summary.get("evidence_ids") or []):
            continue
        episode["summary"] = summary
        episode_out.append(episode)
    return {
        "ok": True,
        "candidate": candidate,
        "evidence": evidence,
        "override_sessions": sessions,
        "episodes": episode_out,
        "lighting_decisions": decision_out,
        "revisions": revision_out,
        "decisions": decision_rows,
    }


@app.post("/api/intelligence/candidates/{candidate_id}/decisions")
def candidate_decision(candidate_id: str, body: DecisionIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return apply_decision(
                conn,
                subject_type="candidate",
                subject_id=candidate_id,
                decision=body.decision,
                actor=body.actor,
                reason=body.reason,
                note=body.note,
                body=body.body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/candidates/{candidate_id}/readiness")
def candidate_readiness(candidate_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        row = fetch_candidate_row(conn, candidate_id)
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        candidate = _candidate(row)
    return {
        "ok": True,
        "candidate": candidate,
        "readiness": evaluate_candidate_readiness(candidate),
    }


@app.get("/api/intelligence/proposal-preview/{candidate_id}")
def candidate_proposal_preview(candidate_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        row = fetch_candidate_row(conn, candidate_id)
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        candidate = _candidate(row)
    readiness = evaluate_candidate_readiness(candidate)
    return {
        "ok": True,
        "candidate": candidate,
        "readiness": readiness,
        "proposal_preview": proposal_preview(candidate, readiness),
    }


@app.post("/api/intelligence/proposals/run")
def run_proposals() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_proposal_generation(conn, source="manual")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/proposals")
def proposals(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, "proposals": list_proposals(conn, status=status, limit=limit)}


@app.get("/api/intelligence/proposals/{proposal_id}")
def proposal_detail(proposal_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        detail = get_proposal_detail(conn, proposal_id)
        if not detail:
            raise HTTPException(status_code=404, detail="proposal not found")
        return {"ok": True, **detail}


@app.post("/api/intelligence/experiments/design/run")
def run_experiment_designs() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_experiment_design_generation(conn, source="manual")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/experiments")
def experiments(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, "experiments": list_experiments(conn, status=status, limit=limit)}


@app.get("/api/intelligence/experiments/evaluations")
def experiment_evaluations(
    status: str | None = "inactive_design",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "evaluations": list_experiment_evaluations(conn, status=status, limit=limit),
        }


@app.get("/api/intelligence/experiments/preflights")
def experiment_preflights(
    status: str | None = "inactive_design",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "preflights": list_activation_preflights(conn, status=status, limit=limit),
        }


@app.get("/api/intelligence/experiments/manifests")
def experiment_manifests(
    status: str | None = "inactive_design",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "manifests": list_activation_manifests(conn, status=status, limit=limit),
        }


@app.get("/api/intelligence/experiments/{experiment_id}")
def experiment_detail(experiment_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        detail = get_experiment_detail(conn, experiment_id)
        if not detail:
            raise HTTPException(status_code=404, detail="experiment not found")
        return {"ok": True, **detail}


@app.get("/api/intelligence/experiments/{experiment_id}/evaluation")
def experiment_evaluation(
    experiment_id: str,
    lookback_days: int = Query(14, ge=1, le=90),
    trial_start: str | None = None,
    trial_end: str | None = None,
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        evaluation = evaluate_experiment_design(
            conn,
            experiment_id,
            lookback_days=lookback_days,
            trial_start=trial_start,
            trial_end=trial_end,
        )
        if not evaluation:
            raise HTTPException(status_code=404, detail="experiment not found")
        return {"ok": True, "evaluation": evaluation}


@app.get("/api/intelligence/experiments/{experiment_id}/preflight")
def experiment_preflight(
    experiment_id: str,
    lookback_days: int = Query(14, ge=1, le=90),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        preflight = build_activation_preflight(conn, experiment_id, lookback_days=lookback_days)
        if not preflight:
            raise HTTPException(status_code=404, detail="experiment not found")
        return {"ok": True, "preflight": preflight}


@app.get("/api/intelligence/experiments/{experiment_id}/manifest")
def experiment_manifest(
    experiment_id: str,
    lookback_days: int = Query(14, ge=1, le=90),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        manifest = build_activation_manifest(conn, experiment_id, lookback_days=lookback_days)
        if not manifest:
            raise HTTPException(status_code=404, detail="experiment not found")
        return {"ok": True, "manifest": manifest}


@app.post("/api/intelligence/sandbox/experiment-chain/run")
def synthetic_experiment_chain(candidate_id: str | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return run_synthetic_experiment_chain(conn, candidate_id=candidate_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/sandbox/experiment-chain/explain")
def synthetic_experiment_chain_explain(candidate_id: str | None = None) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return explain_synthetic_experiment_chain(conn, candidate_id=candidate_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/proposals/{proposal_id}/decisions")
def proposal_decision(proposal_id: str, body: DecisionIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return apply_decision(
                conn,
                subject_type="proposal",
                subject_id=proposal_id,
                decision=body.decision,
                actor=body.actor,
                reason=body.reason,
                note=body.note,
                body=body.body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/decisions")
def decisions(
    subject_type: str | None = None,
    subject_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "decisions": list_decisions(
                conn,
                subject_type=subject_type,
                subject_id=subject_id,
                limit=limit,
            ),
        }


@app.post("/api/intelligence/decisions")
def create_decision(
    subject_type: str,
    subject_id: str,
    body: DecisionIn,
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return apply_decision(
                conn,
                subject_type=subject_type,
                subject_id=subject_id,
                decision=body.decision,
                actor=body.actor,
                reason=body.reason,
                note=body.note,
                body=body.body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/observations")
def observations(
    candidate_id: str | None = None,
    zone: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    params: list[Any] = []
    where: list[str] = []
    if candidate_id:
        with db_conn() as conn:
            init_db(conn)
            row = conn.execute(
                "SELECT evidence_ids_json FROM preference_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="candidate not found")
            ids = _decode_json(row["evidence_ids_json"], [])
            if not ids:
                return {"ok": True, "observations": []}
            placeholders = ",".join("?" for _ in ids)
            where.append(f"id IN ({placeholders})")
            params.extend(ids)
    if zone:
        where.append("zone = ?")
        params.append(zone)
    sql = "SELECT * FROM preference_observations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(sql, params).fetchall()
    return {"ok": True, "observations": [_observation(r) for r in rows]}


@app.get("/api/intelligence/episodes")
def episodes(
    zone: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    params: list[Any] = []
    sql = "SELECT * FROM episodes"
    if zone:
        sql += " WHERE zone = ?"
        params.append(zone)
    sql += " ORDER BY end_ts DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(sql, params).fetchall()
    out = rows_to_dicts(rows)
    for row in out:
        row["summary"] = _decode_json(row.pop("summary_json", None), {})
    return {"ok": True, "episodes": out}


@app.get("/api/intelligence/events")
def events(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM raw_events
            ORDER BY ingest_ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = rows_to_dicts(rows)
    for row in out:
        row["redacted_payload"] = _decode_json(row.pop("redacted_payload_json", None), {})
    return {"ok": True, "events": out}


@app.get("/api/intelligence/lighting-decisions")
def lighting_decisions(
    zone: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    params: list[Any] = []
    sql = "SELECT * FROM lighting_decisions"
    if zone:
        sql += " WHERE zone = ?"
        params.append(zone)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(sql, params).fetchall()
    out = rows_to_dicts(rows)
    for row in out:
        row["payload"] = _decode_json(row.pop("payload_json", None), {})
    return {"ok": True, "lighting_decisions": out}


@app.get("/api/intelligence/multimodal/status")
def get_multimodal_status() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return multimodal_status(conn)


@app.post("/api/intelligence/multimodal/pilot/run")
def run_multimodal_pilot() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return execute_multimodal_capture_pilot(conn, source="manual")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/qwen-labels/validate")
def validate_qwen_label_payload(body: QwenLabelValidationIn) -> dict[str, Any]:
    valid, normalized, errors = validate_qwen_labels(body.payload)
    return {"ok": valid, "normalized": normalized, "errors": errors}


@app.post("/api/intelligence/qwen-labels/run")
def run_qwen_labels(limit: int = Query(1, ge=1, le=10)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return label_next_packets(conn, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/qwen-labels/prompt-gate")
def get_qwen_prompt_gate() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return qwen_prompt_gate(conn)


@app.get("/api/intelligence/qwen-labels/prompt-trials")
def get_qwen_prompt_trials(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return list_qwen_prompt_trials(conn, limit=limit)


@app.post("/api/intelligence/qwen-labels/prompt-trials/run")
def run_qwen_prompt_trial_endpoint(body: QwenPromptTrialIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return run_qwen_prompt_trial(
                conn,
                candidate_name=body.candidate_name,
                eval_set_id=body.eval_set_id,
                candidate_prompt=body.candidate_prompt,
                candidate_labels_by_item=body.candidate_labels_by_item,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/label-evals/status")
def get_label_evals_status() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return label_eval_status(conn)


@app.get("/api/intelligence/label-evals")
def get_label_evals(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return list_label_eval_sets(conn, limit=limit)


@app.post("/api/intelligence/label-evals/seed")
def seed_label_eval(body: LabelEvalSeedIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return seed_label_eval_set(
                conn,
                name=body.name,
                target_size=body.target_size,
                require_valid_labels=body.require_valid_labels,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/label-evals/queue")
def get_label_eval_queue(
    set_id: str | None = None,
    status: str = Query("unaudited", pattern="^(unaudited|audited|all)$"),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return label_eval_queue(conn, set_id=set_id, status=status, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/intelligence/label-evals/{set_id}")
def get_label_eval(set_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return get_label_eval_set(conn, set_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/intelligence/label-evals/{set_id}/score")
def get_label_eval_score(set_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return score_label_eval_set(conn, set_id)


@app.post("/api/intelligence/label-evals/items/{item_id}/accept-qwen")
def accept_label_eval_qwen(item_id: str, body: LabelEvalAuditIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return accept_qwen_label_eval_item(
                conn,
                item_id,
                actor=body.actor,
                note=body.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/intelligence/label-evals/items/{item_id}/audit")
def audit_label_eval(item_id: str, body: LabelEvalAuditIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return audit_label_eval_item(
                conn,
                item_id,
                human_labels=body.human_labels,
                actor=body.actor,
                note=body.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/intelligence/training-clips/manifests")
def get_training_clip_manifests(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return list_training_clip_manifests(conn, status=status, limit=limit)


@app.post("/api/intelligence/training-clips/manifests")
def create_training_clip_manifest_endpoint(body: TrainingClipManifestIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return create_training_clip_manifest(
                conn,
                packet_id=body.packet_id,
                tier=body.tier,
                actor=body.actor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/training-clips/manifests/{manifest_id}")
def get_training_clip_manifest_endpoint(manifest_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return get_training_clip_manifest(conn, manifest_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/intelligence/observation-packets")
def observation_packets(
    zone: str | None = None,
    activity: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {
            "ok": True,
            "observation_packets": list_observation_packets(
                conn,
                zone=zone,
                activity=activity,
                limit=limit,
            ),
        }


@app.get("/api/intelligence/observation-packets/map")
def observation_packets_map(limit: int = Query(300, ge=1, le=1000)) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        return {"ok": True, **observation_map(conn, limit=limit)}


@app.post("/api/intelligence/observation-packets/capture")
def capture_observation_packet(body: ObservationPacketCaptureIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            packet = create_observation_packet(
                conn,
                observation_id=body.observation_id,
                capture_frames=body.capture_frames,
                force=body.force,
                trigger=body.trigger,
                post_frame_delay_s=body.post_frame_delay_s,
            )
            return {"ok": True, **packet}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/observation-packets/capture/recent")
def capture_recent_observation_packets(
    limit: int = Query(5, ge=1, le=25),
    kind: str | None = None,
    capture_frames: bool = False,
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return create_recent_observation_packets(
                conn,
                limit=limit,
                kind=kind,
                capture_frames=capture_frames,
                trigger="recent_batch_api",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/intelligence/observation-packets/{packet_id}")
def observation_packet_detail(packet_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return {"ok": True, **get_observation_packet(conn, packet_id)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/intelligence/observation-packets/{packet_id}/frames/capture")
def capture_observation_packet_frames(
    packet_id: str,
    post_frame_delay_s: float = Query(0.0, ge=0.0, le=15.0),
) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return capture_packet_frames(
                conn,
                packet_id=packet_id,
                post_frame_delay_s=post_frame_delay_s,
                allow_current_fallback=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/intelligence/observation-packets/{packet_id}/frames")
def delete_observation_packet_frames(packet_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return delete_packet_frames(conn, packet_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/intelligence/observation-packets/{packet_id}/label")
def label_observation_packet_endpoint(packet_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return label_observation_packet(conn, packet_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/intelligence/observation-packets/{packet_id}/audits")
def audit_observation_packet(packet_id: str, body: ObservationPacketAuditIn) -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        try:
            return record_packet_audit(
                conn,
                packet_id=packet_id,
                action=body.action,
                actor=body.actor,
                note=body.note,
                body=body.body,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/intelligence/observation-frames/{frame_id}/image")
def observation_frame_image(frame_id: str) -> FileResponse:
    with db_conn() as conn:
        init_db(conn)
        try:
            path = get_frame_path(conn, frame_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/intelligence/suppressions")
def suppressions() -> dict[str, Any]:
    with db_conn() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM preference_suppression_rules
            ORDER BY created_at DESC
            """
        ).fetchall()
    out = rows_to_dicts(rows)
    for row in out:
        row["context"] = _decode_json(row.pop("context_json", None), {})
    return {"ok": True, "suppressions": out}


@app.post("/api/intelligence/suppressions")
def create_suppression(body: SuppressionIn) -> dict[str, Any]:
    if not body.zone and not body.candidate_id:
        raise HTTPException(status_code=400, detail="zone or candidate_id is required")
    created = utc_now()
    with db_conn() as conn:
        init_db(conn)
        zone = body.zone
        context = body.context or {}
        if body.candidate_id:
            row = conn.execute(
                "SELECT zone, context_json FROM preference_candidates WHERE id = ?",
                (body.candidate_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="candidate not found")
            zone = zone or row["zone"]
            if not context:
                context = _decode_json(row["context_json"], {})
        if not context and not body.suppress_entire_zone:
            raise HTTPException(
                status_code=400,
                detail="context is required unless suppress_entire_zone is true",
            )
        cur = conn.execute(
            """
            INSERT INTO preference_suppression_rules
                (candidate_id, zone, context_json, reason, created_at, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                body.candidate_id,
                zone,
                canonical_json(context),
                body.reason,
                created,
            ),
        )
        conn.commit()
    return {
        "ok": True,
        "suppression": {
            "id": cur.lastrowid,
            "candidate_id": body.candidate_id,
            "zone": zone,
            "context": context,
            "reason": body.reason,
            "created_at": created,
            "active": 1,
        },
    }
