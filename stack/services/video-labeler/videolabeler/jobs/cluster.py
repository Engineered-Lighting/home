"""cluster job (gpu lane for serialization with embed -- the math is CPU):
MiniBatchKMeans over the pooled_window embeddings of NON-HOLDOUT windows.

Pipeline: pooled_window vectors (one per window for the model_id) ->
L2-normalize -> PCA-64 -> MiniBatchKMeans k=clamp(sqrt(n), 20, 200) ->
cluster_runs + cluster_members rows (distance = euclidean distance to the
assigned centroid in PCA space; the top ~5% distances are flagged
is_outlier for review-queue triage / the window-signals endpoint).

HOLDOUT FIREWALL (red-team): holdout windows ARE embedded (cheap, needed
for eventual eval) but are excluded HERE, in SQL -- they never join a
cluster run, so cluster-derived triage/neighbor surfaces cannot leak them.

Idempotency key cluster:{model_id}:{n_windows} -- re-POSTing /cluster after
more windows were embedded makes a NEW run; identical corpus state dedupes.
sklearn/numpy imports live inside _fit_clusters only; tests monkeypatch it
and exercise everything else sqlite-only.
"""
from __future__ import annotations

import json
import logging
import math
import uuid

from .. import db
from ..embeddings import store
from ..jobqueue import queue as q
from ..jobqueue import states

log = logging.getLogger("videolabeler.jobs.cluster")

PCA_DIM = 64
K_MIN, K_MAX = 20, 200
OUTLIER_FRAC = 0.95  # members above this distance quantile flag is_outlier
SEED = 0
CHUNK_ROWS = 200


def k_for(n: int) -> int:
    """k = clamp(sqrt(n), 20, 200), never above n."""
    return max(1, min(max(K_MIN, min(K_MAX, round(math.sqrt(n)))), n))


def idempotency_key(model_id: str, n_windows: int) -> str:
    return f"cluster:{model_id}:{n_windows}"


def member_count(conn, model_id: str) -> int:
    """Non-holdout, non-deleted windows with a pooled_window embedding."""
    return conn.execute(
        "SELECT COUNT(DISTINCT e.analysis_window_id) FROM embeddings e"
        " JOIN analysis_windows w ON w.id = e.analysis_window_id"
        " JOIN videos v ON v.id = w.video_id"
        " WHERE e.model_id = ? AND e.kind = 'pooled_window'"
        " AND v.is_holdout = 0 AND v.import_status != 'deleted'",
        (model_id,)).fetchone()[0]


def enqueue(conn, model_id: str):
    """Idempotent cluster enqueue keyed on the current corpus size;
    terminal jobs under the same key are retried."""
    n = member_count(conn, model_id)
    job = q.enqueue(conn, "cluster", {"model_id": model_id, "n_windows": n},
                    lane=states.LANE_GPU,
                    idempotency_key=idempotency_key(model_id, n))
    if job["state"] in states.TERMINAL:
        job = q.retry(conn, job["id"])
    return job


def _fit_clusters(vectors: list, k: int, pca_dim: int, seed: int) -> tuple:
    """L2-normalized vectors -> (cluster assignments, distance to the
    assigned centroid in PCA space). The ONLY sklearn/numpy touchpoint --
    tests monkeypatch this."""
    import numpy as np
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.decomposition import PCA

    x = np.asarray(vectors, dtype="float32")
    d = min(pca_dim, x.shape[0], x.shape[1])
    if d < x.shape[1]:
        x = PCA(n_components=d, random_state=seed).fit_transform(x)
    km = MiniBatchKMeans(n_clusters=k, random_state=seed,
                         n_init="auto").fit(x)
    dists = np.linalg.norm(x - km.cluster_centers_[km.labels_], axis=1)
    return [int(v) for v in km.labels_], [float(v) for v in dists]


def outlier_threshold(distances: list, frac: float = OUTLIER_FRAC) -> float:
    """Distance quantile above which members flag is_outlier."""
    s = sorted(distances)
    return s[int(frac * (len(s) - 1))]


def run(job, ctx) -> None:
    conn = ctx.conn
    cfg = ctx.cfg
    model_id = q.payload(job)["model_id"]
    rows = conn.execute(
        "SELECT e.analysis_window_id AS wid, e.shard_path, e.row_index"
        " FROM embeddings e"
        " JOIN analysis_windows w ON w.id = e.analysis_window_id"
        " JOIN videos v ON v.id = w.video_id"
        " WHERE e.model_id = ? AND e.kind = 'pooled_window'"
        " AND v.is_holdout = 0 AND v.import_status != 'deleted'"
        " ORDER BY e.analysis_window_id", (model_id,)).fetchall()
    if not rows:
        raise RuntimeError(f"no pooled_window embeddings for {model_id}"
                           " -- run POST /embed first")
    n = len(rows)
    ctx.heartbeat(progress=0.05,
                  msg=f"loading {n} pooled_window vector(s)")
    vectors = [store.l2_normalize(v) for v in store.read_rows(
        cfg.data_dir, [(r["shard_path"], r["row_index"]) for r in rows])]

    k = k_for(n)
    ctx.heartbeat(progress=0.2,
                  msg=f"PCA-{PCA_DIM} + MiniBatchKMeans k={k} over {n}")
    assignments, distances = _fit_clusters(vectors, k, PCA_DIM, SEED)
    threshold = outlier_threshold(distances)

    run_id = "cr_" + uuid.uuid4().hex[:12]
    now = db.iso_now()
    params = {"n_windows": n, "pca_dim": PCA_DIM, "seed": SEED,
              "outlier_frac": OUTLIER_FRAC,
              "outlier_threshold": round(threshold, 6),
              "job_id": ctx.job_id}
    with db.tx(conn):
        conn.execute(
            "INSERT INTO cluster_runs (id, model_id, k, params_json, status,"
            " created_at) VALUES (?, ?, ?, ?, 'running', ?)",
            (run_id, model_id, k, json.dumps(params, sort_keys=True), now))
    for i in range(0, n, CHUNK_ROWS):
        with db.tx(conn):
            for r, cid, dist in list(zip(rows, assignments,
                                         distances))[i:i + CHUNK_ROWS]:
                conn.execute(
                    "INSERT OR REPLACE INTO cluster_members (cluster_run_id,"
                    " analysis_window_id, cluster_idx, distance, is_outlier)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (run_id, r["wid"], cid, round(dist, 6),
                     1 if dist > threshold else 0))
        ctx.heartbeat(progress=0.3 + 0.65 * min(1.0, (i + CHUNK_ROWS) / n),
                      msg=f"members {min(i + CHUNK_ROWS, n)}/{n}")
    with db.tx(conn):
        conn.execute("UPDATE cluster_runs SET status = 'ready',"
                     " finished_at = ? WHERE id = ?", (db.iso_now(), run_id))
    n_out = sum(1 for d in distances if d > threshold)
    ctx.heartbeat(progress=1.0,
                  msg=f"run {run_id}: k={k}, {n} member(s), {n_out} outlier(s)")
    log.info("cluster %s: run %s k=%d n=%d outliers=%d",
             model_id, run_id, k, n, n_out)
