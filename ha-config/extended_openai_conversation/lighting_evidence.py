"""Read-only helpers for exporting lighting evidence JSONL files."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVIDENCE_SOURCES = {
    "preferences": Path("/config/lighting_preferences_pending.jsonl"),
    "decisions": Path("/config/lighting_decisions.log"),
    "activity": Path("/config/lighting_activity.jsonl"),
}

DEFAULT_EXPORT_BYTES = 256 * 1024
MAX_EXPORT_BYTES = 1024 * 1024
STATUS_TAIL_SCAN_BYTES = 1024 * 1024


def _iso_from_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _fingerprint(stat_result: os.stat_result) -> str:
    return f"{stat_result.st_dev}:{stat_result.st_ino}"


def _parse_json_objects(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        if isinstance(obj, dict):
            out.append(obj)
        idx = end
    return out


def _compact_payload(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if not obj:
        return None
    observed = obj.get("observed") or obj.get("captured_state") or {}
    predicted = obj.get("predicted") or obj.get("what_automation_would_have_done") or {}
    context = obj.get("context") or {}
    return {
        "ts": obj.get("ts"),
        "kind": obj.get("kind"),
        "zone": obj.get("primary_zone") or obj.get("zone"),
        "light_entity": obj.get("light_entity"),
        "actual_brightness_pct": observed.get("brightness_pct"),
        "predicted_brightness_pct": predicted.get("brightness_pct"),
        "profile": context.get("profile"),
        "state": context.get("state"),
        "context_id": obj.get("context_id"),
        "evidence_id": obj.get("evidence_id"),
    }


def _latest_payload(path: Path, *, max_scan_bytes: int = STATUS_TAIL_SCAN_BYTES) -> tuple[dict[str, Any] | None, int, bool]:
    size = path.stat().st_size
    if size <= 0:
        return None, 0, False
    scanned = 0
    hit_cap = False
    buf = b""
    with path.open("rb") as f:
        f.seek(size - 1)
        drop_final_partial = f.read(1) != b"\n"
        first_chunk = True
        pos = size
        while pos > 0:
            if scanned >= max_scan_bytes:
                hit_cap = True
                break
            read_size = min(64 * 1024, pos, max_scan_bytes - scanned)
            pos -= read_size
            scanned += read_size
            f.seek(pos)
            buf = f.read(read_size) + buf
            lines = buf.split(b"\n")
            if pos > 0:
                buf = lines[0]
                lines = lines[1:]
            else:
                buf = b""
            if first_chunk:
                first_chunk = False
                if drop_final_partial and lines:
                    lines = lines[:-1]
            for line in reversed(lines):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payloads = _parse_json_objects(raw)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if payloads:
                    return payloads[-1], scanned, hit_cap
    return None, scanned, hit_cap


def source_status(source: str, path: Path | None = None) -> dict[str, Any]:
    if source not in EVIDENCE_SOURCES and path is None:
        raise ValueError(f"unknown evidence source: {source}")
    evidence_path = path or EVIDENCE_SOURCES[source]
    if not evidence_path.exists():
        return {
            "source": source,
            "path": str(evidence_path),
            "exists": False,
            "size": 0,
            "mtime": None,
            "mtime_iso": None,
            "fingerprint": None,
            "latest": None,
            "scan": {"bytes": 0, "hit_cap": False},
        }
    stat_result = evidence_path.stat()
    latest, scanned, hit_cap = _latest_payload(evidence_path)
    return {
        "source": source,
        "path": str(evidence_path),
        "exists": True,
        "size": stat_result.st_size,
        "mtime": stat_result.st_mtime,
        "mtime_iso": _iso_from_timestamp(stat_result.st_mtime),
        "fingerprint": _fingerprint(stat_result),
        "latest": _compact_payload(latest),
        "scan": {"bytes": scanned, "hit_cap": hit_cap},
    }


def all_source_status() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            source: source_status(source)
            for source in sorted(EVIDENCE_SOURCES)
        },
    }


def export_source_lines(
    source: str,
    *,
    offset: int = 0,
    max_bytes: int = DEFAULT_EXPORT_BYTES,
    path: Path | None = None,
) -> dict[str, Any]:
    if source not in EVIDENCE_SOURCES and path is None:
        raise ValueError(f"unknown evidence source: {source}")
    evidence_path = path or EVIDENCE_SOURCES[source]
    max_bytes = max(1, min(int(max_bytes or DEFAULT_EXPORT_BYTES), MAX_EXPORT_BYTES))
    offset_in = max(0, int(offset or 0))
    if not evidence_path.exists():
        return {
            "ok": True,
            "source": source,
            "path": str(evidence_path),
            "exists": False,
            "offset_in": offset_in,
            "offset_out": 0,
            "eof": True,
            "reset_required": offset_in != 0,
            "lines": [],
            "file": None,
        }

    stat_result = evidence_path.stat()
    size = stat_result.st_size
    file_info = {
        "size": size,
        "mtime": stat_result.st_mtime,
        "mtime_iso": _iso_from_timestamp(stat_result.st_mtime),
        "fingerprint": _fingerprint(stat_result),
    }
    if offset_in > size:
        return {
            "ok": True,
            "source": source,
            "path": str(evidence_path),
            "exists": True,
            "offset_in": offset_in,
            "offset_out": 0,
            "eof": False,
            "reset_required": True,
            "lines": [],
            "file": file_info,
        }

    lines: list[dict[str, Any]] = []
    with evidence_path.open("rb") as f:
        current = offset_in
        if current > 0:
            f.seek(current - 1)
            if f.read(1) != b"\n":
                f.seek(current)
                skipped = f.readline()
                current += len(skipped)
        if current >= size:
            return {
                "ok": True,
                "source": source,
                "path": str(evidence_path),
                "exists": True,
                "offset_in": offset_in,
                "offset_out": current,
                "eof": current >= size,
                "reset_required": False,
                "lines": [],
                "file": file_info,
            }
        f.seek(current)
        data = f.read(min(max_bytes, size - current))

    pos = 0
    consumed = 0
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        raw = data[pos:nl]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        byte_start = current + pos
        byte_end = current + nl + 1
        if raw.strip():
            lines.append({
                "byte_start": byte_start,
                "byte_end": byte_end,
                "raw": raw.decode("utf-8", errors="replace"),
            })
        pos = nl + 1
        consumed = pos

    offset_out = current + consumed
    return {
        "ok": True,
        "source": source,
        "path": str(evidence_path),
        "exists": True,
        "offset_in": offset_in,
        "offset_out": offset_out,
        "eof": offset_out >= size,
        "reset_required": False,
        "lines": lines,
        "file": file_info,
        "max_bytes": max_bytes,
    }
