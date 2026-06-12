"""Labels API: ontology doc, segments-canonical labels GET/PUT (optimistic
revision concurrency), per-segment review transitions.

Shapes (contract with the frontend):
  GET  /labels/ontology -> {axes: {activity_primary, posture, quality_flags},
       aux_axes, review_states, custom: [{slug, axis, name, description,
       color, status, usage_count}]}
  GET  /videos/{id}/labels -> {revision, axes: {activity_primary | posture |
       quality | custom: [SEG...]}}  (canonical lane only, sorted by start_s)
  PUT  /videos/{id}/labels {revision, axes (any subset)} ->
       {revision, id_map} | 409 full snapshot {revision, axes} (rebase and
       retry) | 400 {error, details} (nothing written)
  POST /videos/{id}/segments/{segment_id}/review {action} ->
       {revision, segment}
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .. import db, labels, ontology
from ..models import LabelsPutRequest, ReviewRequest

log = logging.getLogger("videolabeler.api.labels")


def _custom_for_ontology(conn) -> list:
    counts = labels.custom_usage_counts(conn)
    return [
        {"slug": r["slug"], "axis": r["axis"], "name": r["name"],
         "description": r["description"], "color": r["color"],
         "status": r["status"], "usage_count": counts.get(r["slug"], 0)}
        for r in conn.execute("SELECT * FROM custom_labels ORDER BY slug")]


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    def _video(conn, video_id: str) -> None:
        row = conn.execute("SELECT id FROM videos WHERE id = ?",
                           (video_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"video {video_id} not found")

    @router.get("/labels/ontology")
    def ontology_doc():
        with db.open_db(cfg.db_path) as conn:
            custom = _custom_for_ontology(conn)
        return {
            "axes": {
                "activity_primary": {"values": list(ontology.ACTIVITY_PRIMARY),
                                     "active": list(ontology.ACTIVE_SET)},
                # no staged activation is defined for posture: all 14 active
                "posture": {"values": list(ontology.POSTURE),
                            "active": list(ontology.POSTURE)},
                "quality_flags": {"values": list(ontology.QUALITY_FLAGS)},
            },
            "aux_axes": list(ontology.AUX_AXES),
            "review_states": list(ontology.REVIEW_STATES),
            "custom": custom,
        }

    @router.get("/videos/{video_id}/labels")
    def get_labels(video_id: str):
        with db.open_db(cfg.db_path) as conn:
            _video(conn, video_id)
            return labels.labels_doc(conn, video_id)

    @router.put("/videos/{video_id}/labels")
    def put_labels(video_id: str, body: LabelsPutRequest):
        with db.open_db(cfg.db_path) as conn:
            _video(conn, video_id)
            try:
                revision, id_map = labels.save_labels(
                    conn, video_id, body.revision, body.axes)
            except labels.LabelValidationError as e:
                return JSONResponse(status_code=400, content={
                    "error": "label validation failed", "details": e.details})
            except labels.RevisionConflict:
                # full server snapshot so the client can rebase its draft
                return JSONResponse(status_code=409,
                                    content=labels.labels_doc(conn, video_id))
            return {"revision": revision, "id_map": id_map}

    @router.post("/videos/{video_id}/segments/{segment_id}/review")
    def review_segment(video_id: str, segment_id: str, body: ReviewRequest):
        with db.open_db(cfg.db_path) as conn:
            try:
                revision, segment = labels.review_segment(
                    conn, video_id, segment_id, body.action)
            except ValueError as e:
                raise HTTPException(400, str(e))
            except KeyError:
                raise HTTPException(
                    404, f"segment {segment_id} not found for {video_id}")
            except labels.SegmentConflict as e:
                raise HTTPException(409, str(e))
            return {"revision": revision, "segment": segment}

    return router
