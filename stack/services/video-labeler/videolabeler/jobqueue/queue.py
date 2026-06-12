"""Queue operations over the jobs table.

All functions take an open connection and run SHORT BEGIN IMMEDIATE
transactions (two processes write this table concurrently). Idempotency:
enqueue() with a key that already exists returns the existing job regardless
of its state — callers that want a re-run go through retry().
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Optional

from .. import db
from . import states


def _new_id() -> str:
    return "job_" + uuid.uuid4().hex[:12]


def get_job(conn, job_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(conn, state: Optional[str] = None, type: Optional[str] = None,
              limit: int = 200) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs"
    where, args = [], []
    if state:
        where.append("state = ?")
        args.append(state)
    if type:
        where.append("type = ?")
        args.append(type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return conn.execute(sql, args).fetchall()


def payload(row) -> dict:
    return json.loads(row["payload_json"]) if row["payload_json"] else {}


def checkpoint_of(row) -> dict:
    return json.loads(row["checkpoint_json"]) if row["checkpoint_json"] else {}


def enqueue(conn, type: str, payload: Optional[dict] = None, lane: str = states.LANE_CPU,
            idempotency_key: Optional[str] = None, max_attempts: int = 3) -> sqlite3.Row:
    if idempotency_key is not None:
        row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                           (idempotency_key,)).fetchone()
        if row is not None:
            return row
    jid = _new_id()
    now = db.iso_now()
    try:
        with db.tx(conn):
            conn.execute(
                "INSERT INTO jobs (id, type, state, lane, payload_json,"
                " idempotency_key, max_attempts, created_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)",
                (jid, type, lane, json.dumps(payload or {}), idempotency_key,
                 max_attempts, now))
    except sqlite3.IntegrityError:
        # two writers raced on the same idempotency key — return the winner
        row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                           (idempotency_key,)).fetchone()
        if row is not None:
            return row
        raise
    return get_job(conn, jid)


def lease(conn, lane: str) -> Optional[sqlite3.Row]:
    """Atomically claim the oldest queued job in a lane (BEGIN IMMEDIATE so
    two runner processes cannot double-lease)."""
    with db.tx(conn):
        row = conn.execute(
            "SELECT id FROM jobs WHERE state = 'queued' AND lane = ?"
            " ORDER BY created_at, id LIMIT 1", (lane,)).fetchone()
        if row is None:
            return None
        now = db.iso_now()
        conn.execute(
            "UPDATE jobs SET state = 'running', started_at = ?, heartbeat_at = ?,"
            " attempts = attempts + 1, error = NULL WHERE id = ?",
            (now, now, row["id"]))
    return get_job(conn, row["id"])


def heartbeat(conn, job_id: str, progress: Optional[float] = None,
              msg: Optional[str] = None) -> Optional[sqlite3.Row]:
    """Bump heartbeat_at (+ optional progress/message); returns the fresh row
    so callers can observe cancel_requested cooperatively."""
    sets, args = ["heartbeat_at = ?"], [db.iso_now()]
    if progress is not None:
        sets.append("progress = ?")
        args.append(max(0.0, min(1.0, float(progress))))
    if msg is not None:
        sets.append("progress_msg = ?")
        args.append(msg)
    args.append(job_id)
    with db.tx(conn):
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    return get_job(conn, job_id)


def checkpoint(conn, job_id: str, data: dict) -> None:
    with db.tx(conn):
        conn.execute("UPDATE jobs SET checkpoint_json = ?, heartbeat_at = ? WHERE id = ?",
                     (json.dumps(data), db.iso_now(), job_id))


def finish(conn, job_id: str) -> None:
    with db.tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'succeeded', progress = 1.0, finished_at = ?"
            " WHERE id = ?", (db.iso_now(), job_id))


def fail(conn, job_id: str, error: str) -> None:
    with db.tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
            (error[:4000], db.iso_now(), job_id))


def finalize_cancel(conn, job_id: str) -> None:
    """Worker-side: the job observed cancel_requested and stopped."""
    with db.tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE id = ?",
            (db.iso_now(), job_id))


def cancel(conn, job_id: str) -> sqlite3.Row:
    """queued -> cancelled immediately; running -> cancel_requested flag the
    worker honors at its next heartbeat/checkpoint. Terminal -> ValueError."""
    row = get_job(conn, job_id)
    if row is None:
        raise KeyError(job_id)
    if row["state"] == states.QUEUED:
        with db.tx(conn):
            conn.execute(
                "UPDATE jobs SET state = 'cancelled', cancel_requested = 1,"
                " finished_at = ? WHERE id = ? AND state = 'queued'",
                (db.iso_now(), job_id))
    elif row["state"] == states.RUNNING:
        with db.tx(conn):
            conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
    else:
        raise ValueError(f"job {job_id} is already {row['state']}")
    return get_job(conn, job_id)


def retry(conn, job_id: str) -> sqlite3.Row:
    """Reset a TERMINAL job to queued (checkpoint preserved for resume)."""
    row = get_job(conn, job_id)
    if row is None:
        raise KeyError(job_id)
    if row["state"] not in states.TERMINAL:
        raise ValueError(f"job {job_id} is {row['state']}, not terminal")
    with db.tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'queued', error = NULL, progress = 0,"
            " progress_msg = NULL, cancel_requested = 0, attempts = 0,"
            " started_at = NULL, finished_at = NULL, heartbeat_at = NULL"
            " WHERE id = ?", (job_id,))
    return get_job(conn, job_id)


def requeue(conn, job_id: str, reason: str = "") -> None:
    """Runner-side: a worker exited without finalizing — put the job back
    (checkpoint preserved); attempts were already counted at lease."""
    with db.tx(conn):
        conn.execute(
            "UPDATE jobs SET state = 'queued', started_at = NULL,"
            " heartbeat_at = NULL, progress_msg = ? WHERE id = ? AND state = 'running'",
            (reason or None, job_id))


def recover_stale(conn, now: Optional[float] = None, ttl_s: float = 90.0) -> dict:
    """running jobs whose heartbeat is older than ttl go back to queued
    (attempts < max, checkpoint preserved) or to failed (attempts exhausted)."""
    cutoff = db.iso_at((now if now is not None else time.time()) - ttl_s)
    requeued, failed = [], []
    with db.tx(conn):
        rows = conn.execute(
            "SELECT id, attempts, max_attempts FROM jobs WHERE state = 'running'"
            " AND (heartbeat_at IS NULL OR heartbeat_at < ?)", (cutoff,)).fetchall()
        for r in rows:
            if r["attempts"] < r["max_attempts"]:
                conn.execute(
                    "UPDATE jobs SET state = 'queued', started_at = NULL,"
                    " heartbeat_at = NULL WHERE id = ?", (r["id"],))
                requeued.append(r["id"])
            else:
                conn.execute(
                    "UPDATE jobs SET state = 'failed', finished_at = ?,"
                    " error = 'stale heartbeat: attempts exhausted' WHERE id = ?",
                    (db.iso_now(), r["id"]))
                failed.append(r["id"])
    return {"requeued": requeued, "failed": failed}


def running_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE state = 'running'").fetchone()[0]
