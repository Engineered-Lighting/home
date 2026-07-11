from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import connect, init_db
from .jobs import (
    execute_daily_memory,
    execute_evidence_pull,
    execute_ingest_and_aggregate,
    execute_multimodal_capture_pilot,
)


_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "in_flight": None,
    "last_tick_at": None,
    "last_error": None,
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def scheduler_config() -> dict[str, Any]:
    return {
        "enabled": _env_bool("INTELLIGENCE_SCHEDULER_ENABLED", False),
        "evidence_pull_interval_s": _env_float("INTELLIGENCE_EVIDENCE_PULL_INTERVAL_S", 300.0, minimum=60.0),
        "multimodal_capture_interval_s": _env_float("INTELLIGENCE_MULTIMODAL_CAPTURE_INTERVAL_S", 60.0, minimum=30.0),
        "ingest_interval_s": _env_float("INTELLIGENCE_INGEST_INTERVAL_S", 3600.0, minimum=60.0),
        "memory_enabled": _env_bool("INTELLIGENCE_MEMORY_ENABLED", False),
        "memory_interval_s": _env_float("INTELLIGENCE_MEMORY_INTERVAL_S", 86400.0, minimum=300.0),
        "tick_s": _env_float("INTELLIGENCE_SCHEDULER_TICK_S", 60.0, minimum=5.0),
        "initial_delay_s": _env_float("INTELLIGENCE_SCHEDULER_INITIAL_DELAY_S", 60.0, minimum=0.0),
    }


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _last_job_at(conn: sqlite3.Connection, name: str) -> datetime | None:
    row = conn.execute(
        """
        SELECT started_at, finished_at
        FROM jobs
        WHERE name = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    if not row:
        return None
    return _parse_ts(row["finished_at"]) or _parse_ts(row["started_at"])


def _next_due(
    conn: sqlite3.Connection,
    *,
    name: str,
    interval_s: float,
    now: datetime,
) -> datetime:
    last = _last_job_at(conn, name)
    if last is None:
        return now
    return last + timedelta(seconds=interval_s)


def _scheduler_tick() -> dict[str, Any]:
    cfg = scheduler_config()
    now = datetime.now(timezone.utc)
    ran: list[str] = []
    conn = connect()
    try:
        init_db(conn)
        next_evidence_pull = _next_due(
            conn,
            name="evidence_pull",
            interval_s=cfg["evidence_pull_interval_s"],
            now=now,
        )
        next_ingest = _next_due(
            conn,
            name="ingest_and_aggregate",
            interval_s=cfg["ingest_interval_s"],
            now=now,
        )
        next_multimodal_capture = _next_due(
            conn,
            name="multimodal_capture_pilot",
            interval_s=cfg["multimodal_capture_interval_s"],
            now=now,
        )
        next_memory = _next_due(
            conn,
            name="daily_memory",
            interval_s=cfg["memory_interval_s"],
            now=now,
        )
        if next_evidence_pull <= now:
            _state["in_flight"] = "evidence_pull"
            execute_evidence_pull(conn, source="scheduler")
            ran.append("evidence_pull")
            now = datetime.now(timezone.utc)
            next_evidence_pull = _next_due(
                conn,
                name="evidence_pull",
                interval_s=cfg["evidence_pull_interval_s"],
                now=now,
            )
        if next_multimodal_capture <= now:
            _state["in_flight"] = "multimodal_capture_pilot"
            execute_multimodal_capture_pilot(conn, source="scheduler")
            ran.append("multimodal_capture_pilot")
            now = datetime.now(timezone.utc)
            next_multimodal_capture = _next_due(
                conn,
                name="multimodal_capture_pilot",
                interval_s=cfg["multimodal_capture_interval_s"],
                now=now,
            )
        if next_ingest <= now:
            _state["in_flight"] = "ingest_and_aggregate"
            execute_ingest_and_aggregate(conn, source="scheduler")
            ran.append("ingest_and_aggregate")
            now = datetime.now(timezone.utc)
            next_ingest = _next_due(
                conn,
                name="ingest_and_aggregate",
                interval_s=cfg["ingest_interval_s"],
                now=now,
            )
        if cfg["memory_enabled"] and next_memory <= now:
            _state["in_flight"] = "daily_memory"
            execute_daily_memory(conn, source="scheduler")
            ran.append("daily_memory")
            now = datetime.now(timezone.utc)
            next_memory = _next_due(
                conn,
                name="daily_memory",
                interval_s=cfg["memory_interval_s"],
                now=now,
            )
    finally:
        conn.close()
    _state["in_flight"] = None
    _state["last_tick_at"] = _iso(now)
    _state["last_error"] = None
    _state["next_evidence_pull_at"] = _iso(next_evidence_pull)
    _state["next_multimodal_capture_at"] = _iso(next_multimodal_capture)
    _state["next_ingest_at"] = _iso(next_ingest)
    _state["next_memory_at"] = _iso(next_memory)
    return {
        "ran": ran,
        "next_evidence_pull_at": _iso(next_evidence_pull),
        "next_multimodal_capture_at": _iso(next_multimodal_capture),
        "next_ingest_at": _iso(next_ingest),
        "next_memory_at": _iso(next_memory),
    }


async def _loop() -> None:
    cfg = scheduler_config()
    _state.update(
        {
            "enabled": cfg["enabled"],
            "running": True,
            "tick_s": cfg["tick_s"],
            "evidence_pull_interval_s": cfg["evidence_pull_interval_s"],
            "multimodal_capture_interval_s": cfg["multimodal_capture_interval_s"],
            "ingest_interval_s": cfg["ingest_interval_s"],
            "memory_enabled": cfg["memory_enabled"],
            "memory_interval_s": cfg["memory_interval_s"],
        }
    )
    try:
        await asyncio.sleep(cfg["initial_delay_s"])
        while True:
            if scheduler_config()["enabled"]:
                try:
                    await asyncio.to_thread(_scheduler_tick)
                except Exception as exc:
                    _state["last_error"] = str(exc)
                    _state["in_flight"] = None
                    _state["last_tick_at"] = _iso(datetime.now(timezone.utc))
            await asyncio.sleep(scheduler_config()["tick_s"])
    finally:
        _state["running"] = False
        _state["in_flight"] = None


def start_scheduler() -> None:
    global _task
    cfg = scheduler_config()
    _state.update(
        {
            "enabled": cfg["enabled"],
            "tick_s": cfg["tick_s"],
            "evidence_pull_interval_s": cfg["evidence_pull_interval_s"],
            "multimodal_capture_interval_s": cfg["multimodal_capture_interval_s"],
            "ingest_interval_s": cfg["ingest_interval_s"],
            "memory_enabled": cfg["memory_enabled"],
            "memory_interval_s": cfg["memory_interval_s"],
        }
    )
    if not cfg["enabled"] or _task is not None:
        return
    _task = asyncio.create_task(_loop())


async def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    with suppress(asyncio.CancelledError):
        await _task
    _task = None


def scheduler_status(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    cfg = scheduler_config()
    now = datetime.now(timezone.utc)
    close_conn = False
    if conn is None:
        conn = connect()
        init_db(conn)
        close_conn = True
    try:
        next_ingest = _next_due(
            conn,
            name="ingest_and_aggregate",
            interval_s=cfg["ingest_interval_s"],
            now=now,
        )
        next_evidence_pull = _next_due(
            conn,
            name="evidence_pull",
            interval_s=cfg["evidence_pull_interval_s"],
            now=now,
        )
        next_multimodal_capture = _next_due(
            conn,
            name="multimodal_capture_pilot",
            interval_s=cfg["multimodal_capture_interval_s"],
            now=now,
        )
        next_memory = _next_due(
            conn,
            name="daily_memory",
            interval_s=cfg["memory_interval_s"],
            now=now,
        )
        return {
            "enabled": cfg["enabled"],
            "running": bool(_state.get("running")),
            "in_flight": _state.get("in_flight"),
            "last_tick_at": _state.get("last_tick_at"),
            "last_error": _state.get("last_error"),
            "tick_s": cfg["tick_s"],
            "evidence_pull_interval_s": cfg["evidence_pull_interval_s"],
            "multimodal_capture_interval_s": cfg["multimodal_capture_interval_s"],
            "ingest_interval_s": cfg["ingest_interval_s"],
            "memory_enabled": cfg["memory_enabled"],
            "memory_interval_s": cfg["memory_interval_s"],
            "next_evidence_pull_at": _iso(next_evidence_pull),
            "next_multimodal_capture_at": _iso(next_multimodal_capture),
            "next_ingest_at": _iso(next_ingest),
            "next_memory_at": _iso(next_memory) if cfg["memory_enabled"] else None,
        }
    finally:
        if close_conn:
            conn.close()
