"""import job: scan INBOX_DIR, dedupe on sha256, move into originals, insert
videos rows, enqueue a probe job per video.

Crash-resume contract: the per-file checkpoint {"processed": {name: vid}} is
written AFTER move+insert+enqueue, and the videos row (with its final
local_path) is inserted BEFORE the file is moved — so a crash at any point is
repaired on re-run: a checksum hit whose destination file is missing while the
inbox copy still exists completes the move instead of skipping.

Sidecar provenance: '{filename}.meta.json' (or '{stem}.meta.json') next to the
video merges {drive_file_id, mime, source_batch, is_holdout} into the row and
travels with the original.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import shutil
import time
from pathlib import Path

from .. import db
from ..jobqueue import queue as q

log = logging.getLogger("videolabeler.jobs.import")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
HASH_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _sidecar_for(path: Path) -> Path | None:
    for cand in (path.with_name(path.name + ".meta.json"),
                 path.with_suffix(".meta.json")):
        if cand.is_file():
            return cand
    return None


def _load_sidecar(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        log.warning("unreadable sidecar %s — ignored", path)
        return {}


def run(job, ctx) -> None:
    cfg = ctx.cfg
    conn = ctx.conn
    payload = q.payload(job)
    batch = payload.get("batch_name") or f"manual-{time.strftime('%Y%m%d')}"
    processed: dict = dict(ctx.load_checkpoint().get("processed") or {})

    inbox = Path(cfg.inbox_dir)
    files = sorted(p for p in inbox.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXTS) if inbox.is_dir() else []
    total = len(files)
    imported = duplicates = 0

    for i, src in enumerate(files):
        if src.name in processed:
            continue
        ctx.heartbeat(progress=i / max(total, 1), msg=f"hashing {src.name}")
        sha = _sha256(src)
        vid = "vid_" + sha[:16]

        existing = conn.execute("SELECT id, local_path FROM videos WHERE checksum = ?",
                                (sha,)).fetchone()
        if existing is not None:
            dest = Path(cfg.resolve(existing["local_path"])) if existing["local_path"] else None
            if dest is not None and not dest.exists():
                # crash repair: row landed but the move did not — finish it
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                log.info("repaired interrupted import for %s", existing["id"])
            else:
                duplicates += 1
                log.info("duplicate in inbox skipped: %s (checksum of %s)",
                         src.name, existing["id"])
            processed[src.name] = existing["id"]
            ctx.checkpoint({"processed": processed})
            continue

        sidecar = _sidecar_for(src)
        meta = _load_sidecar(sidecar)
        rel = f"videos/originals/{vid}/{src.name}"
        dest = Path(cfg.resolve(rel))
        now = db.iso_now()
        with db.tx(conn):
            conn.execute(
                "INSERT INTO videos (id, drive_file_id, filename, size_bytes, mime,"
                " checksum, local_path, source_batch, is_holdout, import_status,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported', ?, ?)",
                (vid, meta.get("drive_file_id"), src.name, src.stat().st_size,
                 meta.get("mime") or mimetypes.guess_type(src.name)[0],
                 sha, rel, meta.get("source_batch") or batch,
                 1 if meta.get("is_holdout") else 0, now, now))
            conn.execute(
                "INSERT OR IGNORE INTO video_label_state (video_id, revision, updated_at)"
                " VALUES (?, 0, ?)", (vid, now))
        q.enqueue(conn, "probe", {"video_id": vid}, lane="cpu",
                  idempotency_key=f"probe:{vid}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        if sidecar is not None and sidecar.exists():
            shutil.move(str(sidecar), str(dest.parent / sidecar.name))

        processed[src.name] = vid
        imported += 1
        ctx.checkpoint({"processed": processed})
        log.info("imported %s -> %s (batch=%s)", src.name, vid, batch)

    ctx.heartbeat(progress=1.0,
                  msg=f"imported {imported}, duplicates {duplicates}, scanned {total}")
