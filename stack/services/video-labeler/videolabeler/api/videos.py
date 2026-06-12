"""Video listing/detail/soft-delete + manual import trigger."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import db
from ..jobqueue import queue as q
from ..jobqueue import states
from ..models import ImportManualRequest, Job, Video, VideoDetail

log = logging.getLogger("videolabeler.api.videos")

_LIST_SQL = (
    "SELECT v.*,"
    " EXISTS(SELECT 1 FROM video_artifacts a WHERE a.video_id = v.id"
    "        AND a.kind = 'proxy480' AND a.status = 'ready') AS has_proxy,"
    " EXISTS(SELECT 1 FROM video_artifacts a WHERE a.video_id = v.id"
    "        AND a.kind = 'sprite' AND a.status = 'ready') AS has_sprite"
    " FROM videos v"
)


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    @router.get("/videos")
    def list_videos():
        with db.open_db(cfg.db_path) as conn:
            rows = conn.execute(
                _LIST_SQL + " WHERE v.import_status != 'deleted'"
                " ORDER BY v.created_at DESC, v.id").fetchall()
            return {"videos": [Video.from_row(r).model_dump(mode="json") for r in rows]}

    @router.get("/videos/{video_id}")
    def get_video(video_id: str):
        with db.open_db(cfg.db_path) as conn:
            row = conn.execute(_LIST_SQL + " WHERE v.id = ?", (video_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"video {video_id} not found")
            art = conn.execute(
                "SELECT path FROM video_artifacts WHERE video_id = ? AND kind = 'proxy480'"
                " AND status = 'ready'", (video_id,)).fetchone()
            detail = VideoDetail.from_row(row, proxy_path=art["path"] if art else None)
            return {"video": detail.model_dump(mode="json")}

    @router.delete("/videos/{video_id}")
    def delete_video(video_id: str):
        # soft delete: artifacts/originals are GC'd by a later cleanup pass
        with db.open_db(cfg.db_path) as conn:
            row = conn.execute("SELECT id FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                raise HTTPException(404, f"video {video_id} not found")
            with db.tx(conn):
                conn.execute(
                    "UPDATE videos SET import_status = 'deleted', updated_at = ?"
                    " WHERE id = ?", (db.iso_now(), video_id))
            fresh = conn.execute(_LIST_SQL + " WHERE v.id = ?", (video_id,)).fetchone()
            return {"video": VideoDetail.from_row(fresh).model_dump(mode="json")}

    @router.post("/import/manual")
    def import_manual(body: Optional[ImportManualRequest] = None):
        batch = (body.batch_name if body and body.batch_name
                 else f"manual-{time.strftime('%Y%m%d')}")
        with db.open_db(cfg.db_path) as conn:
            job = q.enqueue(conn, "import", {"batch_name": batch},
                            lane=states.LANE_CPU, idempotency_key=f"import:{batch}")
            if job["state"] in states.TERMINAL:
                # same batch re-posted after a finished run: re-scan the inbox
                job = q.retry(conn, job["id"])
            return {"job": Job.from_row(job).model_dump(mode="json")}

    return router
