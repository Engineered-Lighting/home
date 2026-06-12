"""Media endpoints: Range-capable stream + sprite manifest/sheets.

The Range handler is HAND-ROLLED single-range bytes support — Starlette has
no automatic Range on StreamingResponse, and <video> seeking dies without
206/Accept-Ranges. Multi-range requests degrade to the first range (per RFC a
server MAY ignore extra ranges).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import db
from ..models import SpriteManifest

log = logging.getLogger("videolabeler.api.media")

CHUNK = 1024 * 1024


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: Optional[str], size: int) -> Optional[Tuple[int, int]]:
    """'bytes=a-b' -> inclusive (start, end). None -> serve the full file
    (missing/malformed header). Raises RangeNotSatisfiable for ranges entirely
    past EOF (-> 416). Handles open-ended 'a-' and suffix '-n' forms."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()  # single-range only
    start_s, sep, end_s = spec.partition("-")
    if not sep:
        return None
    if start_s == "":
        if not end_s.isdigit():
            return None
        n = int(end_s)
        if n == 0 or size == 0:
            raise RangeNotSatisfiable()
        return (max(0, size - n), size - 1)
    if not start_s.isdigit():
        return None
    start = int(start_s)
    if start >= size:
        raise RangeNotSatisfiable()
    if end_s == "":
        end = size - 1
    elif end_s.isdigit():
        end = min(int(end_s), size - 1)
    else:
        return None
    if end < start:
        return None
    return (start, end)


def _iter_file(path: str, start: int, end: int):
    remaining = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def range_file_response(path: str, range_header: Optional[str]) -> Response:
    size = os.path.getsize(path)
    media_type = mimetypes.guess_type(path)[0] or "video/mp4"
    try:
        rng = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(status_code=416,
                        headers={"Content-Range": f"bytes */{size}",
                                 "Accept-Ranges": "bytes"})
    if rng is None:
        return FileResponse(path, media_type=media_type,
                            headers={"Accept-Ranges": "bytes"})
    start, end = rng
    return StreamingResponse(
        _iter_file(path, start, end), status_code=206, media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        })


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    def _video(conn, video_id: str):
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"video {video_id} not found")
        return row

    @router.get("/videos/{video_id}/stream")
    def stream(video_id: str, request: Request, original: int = 0):
        with db.open_db(cfg.db_path) as conn:
            row = _video(conn, video_id)
            if original:
                if not row["local_path"]:
                    raise HTTPException(404, "original not on disk")
                path = cfg.resolve(row["local_path"])
            else:
                art = conn.execute(
                    "SELECT path FROM video_artifacts WHERE video_id = ?"
                    " AND kind = 'proxy480' AND status = 'ready'",
                    (video_id,)).fetchone()
                if art is None:
                    raise HTTPException(404, "proxy not ready (try ?original=1)")
                path = cfg.resolve(art["path"])
        if not os.path.isfile(path):
            raise HTTPException(404, "media file missing on disk")
        return range_file_response(path, request.headers.get("range"))

    @router.get("/videos/{video_id}/sprite")
    def sprite_manifest(video_id: str):
        with db.open_db(cfg.db_path) as conn:
            _video(conn, video_id)
            art = conn.execute(
                "SELECT meta_json FROM video_artifacts WHERE video_id = ?"
                " AND kind = 'sprite' AND status = 'ready'", (video_id,)).fetchone()
        if art is None:
            raise HTTPException(404, "sprite not ready")
        meta = json.loads(art["meta_json"] or "{}")
        manifest = SpriteManifest(
            tile_w=meta.get("tile_w", 160), tile_h=meta.get("tile_h", 90),
            cols=meta.get("cols", 10), interval_s=meta.get("interval_s", 1),
            count=meta.get("count", 0),
            sheets=[f"/api/video-labeler/videos/{video_id}/sprite/{n}"
                    for n in range(int(meta.get("sheets", 0)))])
        return manifest.model_dump(mode="json")

    @router.get("/videos/{video_id}/sprite/{n}")
    def sprite_sheet(video_id: str, n: str):
        # tolerate both /sprite/0 (contract) and /sprite/0.jpg
        if n.endswith(".jpg"):
            n = n[:-4]
        if not n.isdigit():
            raise HTTPException(404, "sheet index must be an integer")
        path = os.path.join(cfg.thumbs_dir, video_id, f"sheet_{int(n)}.jpg")
        if not os.path.isfile(path):
            raise HTTPException(404, "sheet not found")
        return FileResponse(path, media_type="image/jpeg")

    return router
