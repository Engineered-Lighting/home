from __future__ import annotations

import sqlite3
from typing import Any

from .aggregation import aggregate_lighting_preferences
from .episodes import build_preference_episodes
from .evidence_sources import pull_ha_evidence
from .experiments import generate_experiment_designs
from .ingest import canonical_json, utc_now
from .lighting_activity import rebuild_lighting_change_episodes
from .memory import build_daily_memory
from .multimodal import run_pilot_capture
from .proposals import generate_proposals


def record_job(
    conn: sqlite3.Connection,
    *,
    name: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO jobs (name, status, started_at, finished_at, details_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, status, started_at, finished_at, canonical_json(details or {})),
    )


def execute_ingest_and_aggregate(
    conn: sqlite3.Connection,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        ingest = pull_ha_evidence(conn)
        lighting_activity = rebuild_lighting_change_episodes(conn)
        episodes = build_preference_episodes(conn)
        aggregate = aggregate_lighting_preferences(conn)
        finished = utc_now()
        details = {
            "source": source,
            "ingest": ingest,
            "lighting_activity": lighting_activity,
            "episodes": episodes,
            "aggregate": aggregate,
        }
        record_job(
            conn,
            name="ingest_and_aggregate",
            status="ok" if ingest.get("ok") and aggregate.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="ingest_and_aggregate",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)},
        )
        conn.commit()
        raise


def execute_evidence_pull(
    conn: sqlite3.Connection,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        ingest = pull_ha_evidence(conn)
        lighting_activity = rebuild_lighting_change_episodes(conn)
        finished = utc_now()
        details = {
            "source": source,
            "ingest": ingest,
            "lighting_activity": lighting_activity,
        }
        record_job(
            conn,
            name="evidence_pull",
            status="ok" if ingest.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="evidence_pull",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)[:500]},
        )
        conn.commit()
        raise


def execute_daily_memory(
    conn: sqlite3.Connection,
    *,
    date: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        memory = build_daily_memory(conn, date=date)
        finished = utc_now()
        details = {"source": source, **memory}
        record_job(
            conn,
            name="daily_memory",
            status="ok" if memory.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="daily_memory",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)},
        )
        conn.commit()
        raise


def execute_proposal_generation(
    conn: sqlite3.Connection,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        proposals = generate_proposals(conn)
        finished = utc_now()
        details = {"source": source, **proposals}
        record_job(
            conn,
            name="proposal_generation",
            status="ok" if proposals.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="proposal_generation",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)},
        )
        conn.commit()
        raise


def execute_experiment_design_generation(
    conn: sqlite3.Connection,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        experiments = generate_experiment_designs(conn)
        finished = utc_now()
        details = {"source": source, **experiments}
        record_job(
            conn,
            name="experiment_design_generation",
            status="ok" if experiments.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="experiment_design_generation",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)},
        )
        conn.commit()
        raise


def execute_multimodal_capture_pilot(
    conn: sqlite3.Connection,
    *,
    source: str = "manual",
) -> dict[str, Any]:
    started = utc_now()
    try:
        pilot = run_pilot_capture(conn)
        finished = utc_now()
        details = {"source": source, "pilot": pilot}
        record_job(
            conn,
            name="multimodal_capture_pilot",
            status="ok" if pilot.get("ok") else "warn",
            started_at=started,
            finished_at=finished,
            details=details,
        )
        conn.commit()
        return {"ok": True, "started_at": started, "finished_at": finished, **details}
    except Exception as exc:
        finished = utc_now()
        record_job(
            conn,
            name="multimodal_capture_pilot",
            status="error",
            started_at=started,
            finished_at=finished,
            details={"source": source, "error": str(exc)[:500]},
        )
        conn.commit()
        raise
