from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingest import (
    canonical_json,
    ha_config_dir,
    ingest_jsonl_file,
    ingest_payload,
    iter_json_objects,
    utc_now,
)


SOURCE_MAP = {
    "preferences": {
        "ha_source": "preferences",
        "local_source": "lighting_preferences_pending",
        "local_name": "lighting_preferences_pending.jsonl",
        "env_name": "INTELLIGENCE_PREFERENCES_JSONL",
    },
    "decisions": {
        "ha_source": "decisions",
        "local_source": "lighting_decisions",
        "local_name": "lighting_decisions.log",
        "env_name": "INTELLIGENCE_LIGHTING_DECISIONS_LOG",
    },
    "activity": {
        "ha_source": "activity",
        "local_source": "lighting_activity",
        "local_name": "lighting_activity.jsonl",
        "env_name": "INTELLIGENCE_LIGHTING_ACTIVITY_JSONL",
    },
}

DEFAULT_PAGE_BYTES = 256 * 1024
MAX_PAGES_PER_PULL = 40
DEFAULT_DECISIONS_INITIAL_TAIL_BYTES = 2 * 1024 * 1024


class EvidencePullError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class HaEvidenceClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            self.base_url + path + query,
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise EvidencePullError("auth_failed", f"HA auth failed with HTTP {exc.code}") from exc
            raise EvidencePullError("unreachable", f"HA returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise EvidencePullError("unreachable", str(exc.reason)) from exc
        except TimeoutError as exc:
            raise EvidencePullError("unreachable", "HA request timed out") from exc


def _ha_base_url() -> str | None:
    raw = os.environ.get("INTELLIGENCE_HA_BASE_URL")
    return raw.rstrip("/") if raw else None


def _ha_token() -> str | None:
    return os.environ.get("INTELLIGENCE_HA_TOKEN") or os.environ.get("HA_TOKEN")


def _redact_error(text: str) -> str:
    token = _ha_token()
    if token:
        text = text.replace(token, "<redacted>")
    return text[:500]


def _parse_dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_ts(a: str | None, b: str | None) -> str | None:
    da = _parse_dt(a)
    db = _parse_dt(b)
    if da is None:
        return b
    if db is None:
        return a
    return b if db >= da else a


def _cursor_row(conn: sqlite3.Connection, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_cursors WHERE source = ?",
        (source,),
    ).fetchone()


def _upsert_cursor(
    conn: sqlite3.Connection,
    *,
    source: str,
    transport: str,
    status: str,
    offset: int = 0,
    source_size: int = 0,
    source_mtime: str | None = None,
    file_fingerprint: str | None = None,
    latest_source_ts: str | None = None,
    latest_ingested_ts: str | None = None,
    lag_bytes: int = 0,
    last_success_at: str | None = None,
    last_error: str | None = None,
) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO source_cursors
            (source, transport, file_fingerprint, offset, source_size,
             source_mtime, latest_source_ts, latest_ingested_ts, lag_bytes,
             status, last_success_at, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            transport=excluded.transport,
            file_fingerprint=excluded.file_fingerprint,
            offset=excluded.offset,
            source_size=excluded.source_size,
            source_mtime=excluded.source_mtime,
            latest_source_ts=excluded.latest_source_ts,
            latest_ingested_ts=excluded.latest_ingested_ts,
            lag_bytes=excluded.lag_bytes,
            status=excluded.status,
            last_success_at=excluded.last_success_at,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """,
        (
            source,
            transport,
            file_fingerprint,
            int(offset or 0),
            int(source_size or 0),
            source_mtime,
            latest_source_ts,
            latest_ingested_ts,
            int(lag_bytes or 0),
            status,
            last_success_at,
            last_error,
            now,
        ),
    )


def _source_latest_from_status(item: dict[str, Any]) -> str | None:
    latest = item.get("latest") or {}
    return latest.get("ts")


def _initial_offset(source: str, source_size: int) -> int:
    if source == "decisions":
        tail_bytes = int(os.environ.get(
            "INTELLIGENCE_DECISIONS_INITIAL_TAIL_BYTES",
            str(DEFAULT_DECISIONS_INITIAL_TAIL_BYTES),
        ))
        return max(0, int(source_size or 0) - max(0, tail_bytes))
    return 0


def _ingest_export_lines(
    conn: sqlite3.Connection,
    *,
    source: str,
    lines: list[dict[str, Any]],
    source_path: str,
) -> dict[str, Any]:
    stats = {"read": len(lines), "ingested": 0, "errors": 0, "latest_ts": None}
    ingest_source = f"ha_api:{SOURCE_MAP[source]['local_source']}"
    for record in lines:
        raw = str(record.get("raw") or "").strip()
        if not raw:
            continue
        try:
            payloads = iter_json_objects(raw)
            for part_no, payload in enumerate(payloads, start=1):
                source_id = f"{source}:{record.get('byte_start')}:{record.get('byte_end')}:{part_no}"
                ingest_payload(
                    conn,
                    source=ingest_source,
                    source_id=source_id,
                    payload=payload,
                    raw_text=canonical_json(payload),
                    source_path=source_path,
                    line_no=None,
                )
                stats["ingested"] += 1
                stats["latest_ts"] = _max_ts(stats.get("latest_ts"), payload.get("ts"))
        except Exception as exc:
            stats["errors"] += 1
            stats.setdefault("error_samples", []).append({
                "byte_start": record.get("byte_start"),
                "error": str(exc)[:240],
            })
            if len(stats.get("error_samples", [])) > 5:
                stats["error_samples"] = stats["error_samples"][:5]
    return stats


def _pull_ha_source(
    conn: sqlite3.Connection,
    *,
    client: Any,
    source: str,
    status_payload: dict[str, Any],
) -> dict[str, Any]:
    status_item = (status_payload.get("sources") or {}).get(source) or {}
    source_size = int(status_item.get("size") or 0)
    source_mtime = status_item.get("mtime_iso")
    source_fingerprint = status_item.get("fingerprint")
    latest_source_ts = _source_latest_from_status(status_item)
    cursor = _cursor_row(conn, source)
    offset = int(cursor["offset"]) if cursor else _initial_offset(source, source_size)
    previous_fingerprint = cursor["file_fingerprint"] if cursor else None
    reset_reason = None
    if cursor and previous_fingerprint and source_fingerprint and previous_fingerprint != source_fingerprint:
        reset_reason = "fingerprint_changed"
    if cursor and source_size < int(cursor["offset"] or 0):
        reset_reason = "file_shrank"
    if reset_reason:
        offset = _initial_offset(source, source_size)

    total = {"source": source, "transport": "ha_api", "read": 0, "ingested": 0, "errors": 0}
    page_bytes = int(os.environ.get("INTELLIGENCE_HA_EXPORT_PAGE_BYTES", str(DEFAULT_PAGE_BYTES)))
    pages = 0
    latest_ingested_ts = cursor["latest_ingested_ts"] if cursor else None
    source_path = f"ha_api://lighting_evidence/{source}"
    while pages < MAX_PAGES_PER_PULL:
        pages += 1
        exported = client.get_json(
            "/api/extended_openai_conversation/lighting_evidence/export",
            {
                "source": SOURCE_MAP[source]["ha_source"],
                "offset": offset,
                "max_bytes": page_bytes,
            },
        )
        if exported.get("reset_required"):
            reset_reason = "export_reset_required"
            offset = _initial_offset(source, source_size)
            break
        stats = _ingest_export_lines(
            conn,
            source=source,
            lines=exported.get("lines") or [],
            source_path=source_path,
        )
        total["read"] += int(stats.get("read", 0))
        total["ingested"] += int(stats.get("ingested", 0))
        total["errors"] += int(stats.get("errors", 0))
        if stats.get("error_samples"):
            total.setdefault("error_samples", []).extend(stats["error_samples"])
            total["error_samples"] = total["error_samples"][:5]
        latest_ingested_ts = _max_ts(latest_ingested_ts, stats.get("latest_ts"))
        next_offset = int(exported.get("offset_out") or offset)
        if next_offset <= offset:
            break
        offset = next_offset
        file_info = exported.get("file") or {}
        source_size = int(file_info.get("size") or source_size)
        source_mtime = file_info.get("mtime_iso") or source_mtime
        source_fingerprint = file_info.get("fingerprint") or source_fingerprint
        if exported.get("eof"):
            break

    lag_bytes = max(0, source_size - offset)
    if total["errors"]:
        status = "behind" if lag_bytes else "fresh_ingested"
    elif total["ingested"] > 0:
        status = "fresh_ingested" if lag_bytes == 0 else "behind"
    else:
        status = "fresh_idle" if lag_bytes == 0 else "behind"
    success_at = utc_now()
    _upsert_cursor(
        conn,
        source=source,
        transport="ha_api",
        status=status,
        offset=offset,
        source_size=source_size,
        source_mtime=source_mtime,
        file_fingerprint=source_fingerprint,
        latest_source_ts=latest_source_ts,
        latest_ingested_ts=latest_ingested_ts,
        lag_bytes=lag_bytes,
        last_success_at=success_at,
        last_error=reset_reason,
    )
    total.update({
        "ok": total["errors"] == 0,
        "status": status,
        "offset": offset,
        "source_size": source_size,
        "lag_bytes": lag_bytes,
        "latest_source_ts": latest_source_ts,
        "latest_ingested_ts": latest_ingested_ts,
        "reset_reason": reset_reason,
        "pages": pages,
    })
    return total


def pull_ha_evidence(
    conn: sqlite3.Connection,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    base_url = _ha_base_url()
    token = _ha_token()
    if client is None:
        if not base_url or not token:
            return ingest_local_fallback(conn)
        client = HaEvidenceClient(base_url, token)

    try:
        status_payload = client.get_json(
            "/api/extended_openai_conversation/lighting_evidence/status"
        )
    except EvidencePullError as exc:
        now = utc_now()
        for source in SOURCE_MAP:
            cursor = _cursor_row(conn, source)
            _upsert_cursor(
                conn,
                source=source,
                transport="ha_api",
                status=exc.status,
                offset=int(cursor["offset"]) if cursor else 0,
                source_size=int(cursor["source_size"]) if cursor else 0,
                source_mtime=cursor["source_mtime"] if cursor else None,
                file_fingerprint=cursor["file_fingerprint"] if cursor else None,
                latest_source_ts=cursor["latest_source_ts"] if cursor else None,
                latest_ingested_ts=cursor["latest_ingested_ts"] if cursor else None,
                lag_bytes=int(cursor["lag_bytes"]) if cursor else 0,
                last_success_at=cursor["last_success_at"] if cursor else None,
                last_error=_redact_error(str(exc)),
            )
        conn.commit()
        return {
            "ok": False,
            "transport": "ha_api",
            "status": exc.status,
            "error": _redact_error(str(exc)),
            "sources": [],
            "finished_at": now,
        }

    sources = []
    for source in SOURCE_MAP:
        try:
            sources.append(
                _pull_ha_source(conn, client=client, source=source, status_payload=status_payload)
            )
        except EvidencePullError as exc:
            cursor = _cursor_row(conn, source)
            _upsert_cursor(
                conn,
                source=source,
                transport="ha_api",
                status=exc.status,
                offset=int(cursor["offset"]) if cursor else 0,
                source_size=int(cursor["source_size"]) if cursor else 0,
                source_mtime=cursor["source_mtime"] if cursor else None,
                file_fingerprint=cursor["file_fingerprint"] if cursor else None,
                latest_source_ts=cursor["latest_source_ts"] if cursor else None,
                latest_ingested_ts=cursor["latest_ingested_ts"] if cursor else None,
                lag_bytes=int(cursor["lag_bytes"]) if cursor else 0,
                last_success_at=cursor["last_success_at"] if cursor else None,
                last_error=_redact_error(str(exc)),
            )
            sources.append({
                "source": source,
                "transport": "ha_api",
                "ok": False,
                "status": exc.status,
                "error": _redact_error(str(exc)),
            })
        except Exception as exc:  # noqa: BLE001
            cursor = _cursor_row(conn, source)
            _upsert_cursor(
                conn,
                source=source,
                transport="ha_api",
                status="unreachable",
                offset=int(cursor["offset"]) if cursor else 0,
                source_size=int(cursor["source_size"]) if cursor else 0,
                source_mtime=cursor["source_mtime"] if cursor else None,
                file_fingerprint=cursor["file_fingerprint"] if cursor else None,
                latest_source_ts=cursor["latest_source_ts"] if cursor else None,
                latest_ingested_ts=cursor["latest_ingested_ts"] if cursor else None,
                lag_bytes=int(cursor["lag_bytes"]) if cursor else 0,
                last_success_at=cursor["last_success_at"] if cursor else None,
                last_error=_redact_error(str(exc)),
            )
            sources.append({
                "source": source,
                "transport": "ha_api",
                "ok": False,
                "status": "unreachable",
                "error": _redact_error(str(exc)),
            })
    conn.commit()
    return {
        "ok": all(item.get("ok") for item in sources),
        "transport": "ha_api",
        "status": "ok" if all(item.get("ok") for item in sources) else "warn",
        "sources": sources,
        "status_payload": {
            "generated_at": status_payload.get("generated_at"),
            "sources": {
                key: {
                    "exists": value.get("exists"),
                    "size": value.get("size"),
                    "mtime_iso": value.get("mtime_iso"),
                    "latest": value.get("latest"),
                }
                for key, value in (status_payload.get("sources") or {}).items()
            },
        },
    }


def ingest_local_fallback(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = ha_config_dir()
    runs = []
    for source, meta in SOURCE_MAP.items():
        env_name = meta.get("env_name") or f"INTELLIGENCE_{source.upper()}_JSONL"
        path = Path(os.environ.get(env_name, str(cfg / meta["local_name"])))
        stats = ingest_jsonl_file(conn, path, source=meta["local_source"])
        size = path.stat().st_size if path.exists() else 0
        mtime = (
            datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            if path.exists()
            else None
        )
        latest_row = conn.execute(
            "SELECT MAX(source_ts) AS ts FROM raw_events WHERE source = ?",
            (meta["local_source"],),
        ).fetchone()
        _upsert_cursor(
            conn,
            source=source,
            transport="local_file",
            status="fallback_snapshot",
            offset=size,
            source_size=size,
            source_mtime=mtime,
            file_fingerprint=None,
            latest_source_ts=latest_row["ts"] if latest_row else None,
            latest_ingested_ts=latest_row["ts"] if latest_row else None,
            lag_bytes=0,
            last_success_at=utc_now(),
            last_error=None if path.exists() else "local fallback file missing",
        )
        runs.append({"source": source, "transport": "local_file", **stats})
    conn.commit()
    return {
        "ok": all(int(item.get("errors", 0)) == 0 for item in runs),
        "transport": "local_file",
        "status": "fallback_snapshot",
        "sources": runs,
        "ingested": sum(int(r.get("ingested", 0)) for r in runs),
        "errors": sum(int(r.get("errors", 0)) for r in runs),
    }


def list_evidence_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM source_cursors ORDER BY source"
    ).fetchall()
    sources = [dict(row) for row in rows]
    configured_transport = "ha_api" if _ha_base_url() and _ha_token() else "local_file"
    for source in SOURCE_MAP:
        if not any(row["source"] == source for row in sources):
            sources.append({
                "source": source,
                "transport": configured_transport,
                "file_fingerprint": None,
                "offset": 0,
                "source_size": 0,
                "source_mtime": None,
                "latest_source_ts": None,
                "latest_ingested_ts": None,
                "lag_bytes": 0,
                "status": "not_pulled" if configured_transport == "ha_api" else "fallback_snapshot",
                "last_success_at": None,
                "last_error": None,
                "updated_at": None,
            })
    statuses = {row.get("status") for row in sources}
    if "auth_failed" in statuses:
        overall = "auth_failed"
    elif "unreachable" in statuses:
        overall = "unreachable"
    elif any(row.get("transport") == "local_file" for row in sources):
        overall = "fallback_snapshot"
    elif "behind" in statuses:
        overall = "behind"
    elif "fresh_ingested" in statuses:
        overall = "fresh_ingested"
    elif "fresh_idle" in statuses:
        overall = "fresh_idle"
    else:
        overall = "not_pulled"
    if any(row.get("transport") == "ha_api" for row in sources):
        mode = "HA API only"
    else:
        mode = "fallback snapshot"
    return {
        "ok": overall not in {"auth_failed", "unreachable"},
        "mode": mode,
        "configured_transport": configured_transport,
        "overall_status": overall,
        "sources": sources,
    }
