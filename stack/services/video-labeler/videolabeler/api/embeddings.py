"""M4 ML surface: embed/cluster triggers + embed-space evidence lookups.

POST /embed {video_ids: [...] | all_pending: true, model?}:
  per target video -- HOLDOUT IS INCLUDED (embeddings are not labels; the
  holdout firewall lives in cluster runs / neighbor candidates), deleted and
  windowless videos are skipped with a reason; personcrop targets missing
  perception get a perceive job carrying then_embed (the perceive tail
  chains the embed job, then_prelabel style). model accepts a variant
  (personcrop_v1 | wholeframe_v1, default personcrop_v1).

POST /cluster {model?} -> gpu-lane cluster job over the CURRENT non-holdout
  corpus (idempotency includes the member count, so a grown corpus makes a
  new run).

GET /segments/{segment_id}/neighbors?k=8&model=&exclude_same_video=1:
  embed-space nearest REVIEWED NON-HOLDOUT windows for the M3 evidence
  panel. Query = mean pooled_window vector of the segment's overlapping
  analysis windows; candidates = windows overlapping any current
  reviewed/accepted activity/posture segment on a non-holdout video,
  excluding the query video (or just the query segment's own footprint when
  exclude_same_video=0). Cosine distance over L2-normalized vectors.

GET /videos/{id}/window-signals?model= -> per-window {cluster_id,
  cluster_dist, is_outlier} from the LATEST ready cluster run -- the M3
  review-queue triage hook (cluster outliers rank for human review).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import db
from ..embeddings import store
from ..jobs import cluster, embed, perceive
from ..models import ClusterRequest, EmbedRequest, Job

log = logging.getLogger("videolabeler.api.embeddings")

REVIEWED_STATES = ("reviewed", "accepted")


def _window_labels(conn, window) -> dict:
    """Max-overlap reviewed/accepted current segment value per axis."""
    out = {"activity": None, "posture": None}
    best = {"activity": 0.0, "posture": 0.0}
    rows = conn.execute(
        "SELECT axis, value, start_s, end_s FROM segments"
        " WHERE video_id = ? AND is_current = 1"
        " AND review_state IN ('reviewed', 'accepted')"
        " AND axis IN ('activity', 'posture')"
        " AND start_s < ? AND end_s > ?",
        (window["video_id"], window["end_s"], window["start_s"]))
    for s in rows:
        overlap = (min(s["end_s"], window["end_s"])
                   - max(s["start_s"], window["start_s"]))
        if overlap > best[s["axis"]]:
            best[s["axis"]] = overlap
            out[s["axis"]] = s["value"]
    return out


def build_router(cfg) -> APIRouter:
    router = APIRouter(prefix="/api/video-labeler")

    def _model_id(model: Optional[str]) -> str:
        """None -> default personcrop model; a variant name -> the
        configured backbone's model_id; a full '<backbone>@<variant>' id
        passes through (deployed flexibility, e.g. after a fallback)."""
        if not model:
            return embed.model_id_for(cfg, embed.DEFAULT_VARIANT)
        if "@" in model:
            return model
        if model in embed.VARIANTS:
            return embed.model_id_for(cfg, model)
        raise HTTPException(
            400, f"unknown model '{model}' (a variant"
                 f" {'/'.join(embed.VARIANTS)} or a full model_id)")

    def _variant_of(model: Optional[str]) -> str:
        if not model:
            return embed.DEFAULT_VARIANT
        variant = model.partition("@")[2] if "@" in model else model
        if "@" in model and model.partition("@")[0] != cfg.vjepa_model_name:
            raise HTTPException(
                400, f"model_id '{model}' does not match the configured"
                     f" backbone '{cfg.vjepa_model_name}'")
        if variant not in embed.VARIANTS:
            raise HTTPException(
                400, f"unknown embed variant '{variant}'"
                     f" (one of: {', '.join(embed.VARIANTS)})")
        return variant

    @router.post("/embed")
    def trigger_embed(body: EmbedRequest):
        if not body.video_ids and not body.all_pending:
            raise HTTPException(400, "provide video_ids or all_pending: true")
        variant = _variant_of(body.model)
        model_id = embed.model_id_for(cfg, variant)
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
                # all_pending: any non-deleted video (HOLDOUT INCLUDED) with
                # at least one window lacking a pooled_window row
                targets = conn.execute(
                    "SELECT DISTINCT v.* FROM videos v"
                    " JOIN analysis_windows w ON w.video_id = v.id"
                    " WHERE v.import_status != 'deleted'"
                    " AND NOT EXISTS (SELECT 1 FROM embeddings e"
                    "   WHERE e.analysis_window_id = w.id"
                    "   AND e.model_id = ? AND e.kind = 'pooled_window')"
                    " ORDER BY v.created_at, v.id", (model_id,)).fetchall()
            for row in targets:
                vid = row["id"]
                if row["import_status"] == "deleted":
                    skipped.append({"video_id": vid, "reason": "deleted"})
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
                if missing and variant == embed.VARIANT_PERSONCROP:
                    job = perceive.enqueue_for_video(conn, vid,
                                                     then_embed=variant)
                else:
                    job = embed.enqueue_for_video(conn, cfg, vid, variant)
                jobs.append(Job.from_row(job).model_dump(mode="json"))
        return {"jobs": jobs, "skipped": skipped, "model_id": model_id}

    @router.post("/cluster")
    def trigger_cluster(body: Optional[ClusterRequest] = None):
        model_id = _model_id(body.model if body else None)
        with db.open_db(cfg.db_path) as conn:
            n = cluster.member_count(conn, model_id)
            if n == 0:
                raise HTTPException(
                    409, f"no pooled_window embeddings for {model_id}"
                         " -- POST /embed first")
            job = cluster.enqueue(conn, model_id)
            return {"job": Job.from_row(job).model_dump(mode="json"),
                    "model_id": model_id, "n_windows": n}

    @router.get("/segments/{segment_id}/neighbors")
    def neighbors(segment_id: str, k: int = 8, model: Optional[str] = None,
                  exclude_same_video: int = 1):
        k = max(1, min(int(k), 50))
        model_id = _model_id(model)
        with db.open_db(cfg.db_path) as conn:
            seg = conn.execute("SELECT * FROM segments WHERE id = ?",
                               (segment_id,)).fetchone()
            if seg is None:
                raise HTTPException(404, f"segment {segment_id} not found")
            base = {"segment_id": segment_id, "model_id": model_id, "k": k}
            qrefs = conn.execute(
                "SELECT e.shard_path, e.row_index FROM embeddings e"
                " JOIN analysis_windows w ON w.id = e.analysis_window_id"
                " WHERE e.model_id = ? AND e.kind = 'pooled_window'"
                " AND w.video_id = ? AND w.start_s < ? AND w.end_s > ?",
                (model_id, seg["video_id"], seg["end_s"],
                 seg["start_s"])).fetchall()
            if not qrefs:
                return {**base, "query_windows": 0, "neighbors": [],
                        "note": "segment windows not embedded yet"
                                " (POST /embed)"}
            qvec = store.l2_normalize(store.mean_vector(store.read_rows(
                cfg.data_dir,
                [(r["shard_path"], r["row_index"]) for r in qrefs])))

            sql = (
                "SELECT w.id AS window_id, w.video_id, w.start_s, w.end_s,"
                " e.shard_path, e.row_index"
                " FROM analysis_windows w"
                " JOIN videos v ON v.id = w.video_id"
                " JOIN embeddings e ON e.analysis_window_id = w.id"
                "   AND e.model_id = ? AND e.kind = 'pooled_window'"
                # HOLDOUT FIREWALL: holdout windows are embedded but are
                # never neighbor candidates (red-team: leak path)
                " WHERE v.is_holdout = 0 AND v.import_status != 'deleted'"
                " AND EXISTS (SELECT 1 FROM segments s"
                "   WHERE s.video_id = w.video_id AND s.is_current = 1"
                "   AND s.review_state IN ('reviewed', 'accepted')"
                "   AND s.axis IN ('activity', 'posture')"
                "   AND s.start_s < w.end_s AND s.end_s > w.start_s)")
            args: list = [model_id]
            if exclude_same_video:
                sql += " AND w.video_id != ?"
                args.append(seg["video_id"])
            else:
                # never hand a segment back its own footprint
                sql += (" AND NOT (w.video_id = ? AND w.start_s < ?"
                        " AND w.end_s > ?)")
                args += [seg["video_id"], seg["end_s"], seg["start_s"]]
            cands = conn.execute(sql, args).fetchall()
            if not cands:
                return {**base, "query_windows": len(qrefs), "neighbors": [],
                        "note": "no reviewed non-holdout windows embedded"
                                " under this model yet"}
            vecs = store.read_rows(
                cfg.data_dir,
                [(c["shard_path"], c["row_index"]) for c in cands])
            scored = sorted(
                ((store.cosine_distance(qvec, store.l2_normalize(v)), c)
                 for c, v in zip(cands, vecs)), key=lambda x: x[0])
            out = []
            for dist, c in scored[:k]:
                out.append({
                    "window_id": c["window_id"],
                    "video_id": c["video_id"],
                    "start_s": c["start_s"],
                    "end_s": c["end_s"],
                    "t_center": round((c["start_s"] + c["end_s"]) / 2, 3),
                    "distance": round(dist, 6),
                    "labels": _window_labels(conn, c),
                    "keyframes": (f"/api/video-labeler/videos/{c['video_id']}"
                                  f"/windows/{c['window_id']}/keyframes"),
                })
            return {**base, "query_windows": len(qrefs), "neighbors": out}

    @router.get("/videos/{video_id}/window-signals")
    def window_signals(video_id: str, model: Optional[str] = None):
        model_id = _model_id(model)
        with db.open_db(cfg.db_path) as conn:
            if conn.execute("SELECT 1 FROM videos WHERE id = ?",
                            (video_id,)).fetchone() is None:
                raise HTTPException(404, f"video {video_id} not found")
            run = conn.execute(
                "SELECT id FROM cluster_runs WHERE model_id = ?"
                " AND status = 'ready' ORDER BY created_at DESC, id DESC"
                " LIMIT 1", (model_id,)).fetchone()
            rows = conn.execute(
                "SELECT w.id, w.start_s, w.end_s, m.cluster_idx, m.distance,"
                " m.is_outlier FROM analysis_windows w"
                " LEFT JOIN cluster_members m ON m.analysis_window_id = w.id"
                "   AND m.cluster_run_id = ?"
                " WHERE w.video_id = ? ORDER BY w.start_s",
                (run["id"] if run else "", video_id)).fetchall()
            return {
                "video_id": video_id,
                "model_id": model_id,
                "cluster_run_id": run["id"] if run else None,
                "windows": [{
                    "window_id": r["id"],
                    "start_s": r["start_s"],
                    "end_s": r["end_s"],
                    "cluster_id": r["cluster_idx"],
                    "cluster_dist": r["distance"],
                    "is_outlier": (bool(r["is_outlier"])
                                   if r["cluster_idx"] is not None else None),
                } for r in rows],
            }

    return router
