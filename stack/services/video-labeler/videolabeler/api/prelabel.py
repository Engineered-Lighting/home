"""ML surface (M2): POST /prelabel (perceive -> prelabel job chaining) +
GET /calibration (per-axis acceptance curves + the bulk-accept gate).

POST /prelabel {video_ids: [...]} or {all_pending: true}:
  per target video -- holdout videos are SKIPPED (plan: holdout is never
  prelabeled; it gets a human-only eval pass), unprobed/windowless videos
  are skipped with a reason; videos with perception missing get a perceive
  job carrying then_prelabel=True (the perceive tail chains the prelabel
  job, probe->proxy style); videos with perception present get the prelabel
  job directly. Terminal jobs under the same idempotency key are retried --
  per-window done-markers make re-runs cheap.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import db, labels
from ..jobs import perceive, prelabel
from ..models import Job, PrelabelRequest

log = logging.getLogger("videolabeler.api.prelabel")


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    @router.post("/prelabel")
    def trigger_prelabel(body: PrelabelRequest):
        if not body.video_ids and not body.all_pending:
            raise HTTPException(400, "provide video_ids or all_pending: true")
        jobs, skipped = [], []
        with db.open_db(cfg.db_path) as conn:
            if body.video_ids:
                targets = []
                for vid in body.video_ids:
                    row = conn.execute("SELECT * FROM videos WHERE id = ?",
                                       (vid,)).fetchone()
                    if row is None:
                        skipped.append({"video_id": vid, "reason": "not found"})
                    else:
                        targets.append(row)
            else:
                # all_pending: any non-deleted, non-holdout video with at
                # least one window still awaiting a prelabel verdict
                targets = conn.execute(
                    "SELECT DISTINCT v.* FROM videos v"
                    " JOIN analysis_windows w ON w.video_id = v.id"
                    " WHERE v.import_status != 'deleted' AND v.is_holdout = 0"
                    " AND (w.person_presence IS NULL"
                    "      OR w.prelabel_status IN ('pending', 'vlm_failed'))"
                    " ORDER BY v.created_at, v.id").fetchall()
            for row in targets:
                vid = row["id"]
                if row["import_status"] == "deleted":
                    skipped.append({"video_id": vid, "reason": "deleted"})
                    continue
                if row["is_holdout"]:
                    skipped.append({"video_id": vid,
                                    "reason": "holdout (never prelabeled)"})
                    continue
                n_windows = conn.execute(
                    "SELECT COUNT(*) FROM analysis_windows WHERE video_id = ?",
                    (vid,)).fetchone()[0]
                if n_windows == 0:
                    skipped.append({"video_id": vid,
                                    "reason": "no analysis windows yet"
                                              " (probe/windows pending)"})
                    continue
                missing = conn.execute(
                    "SELECT COUNT(*) FROM analysis_windows WHERE video_id = ?"
                    " AND pose_summary_json IS NULL", (vid,)).fetchone()[0]
                if missing:
                    job = perceive.enqueue_for_video(conn, vid,
                                                     then_prelabel=True)
                else:
                    job = prelabel.enqueue_for_video(conn, cfg, vid)
                jobs.append(Job.from_row(job).model_dump(mode="json"))
        return {"jobs": jobs, "skipped": skipped}

    @router.get("/calibration")
    def calibration(axis: Optional[str] = None):
        with db.open_db(cfg.db_path) as conn:
            try:
                return labels.calibration_doc(conn, api_axis=axis)
            except ValueError as e:
                raise HTTPException(400, str(e))

    return router
