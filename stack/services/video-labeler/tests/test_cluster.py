"""cluster job (sqlite-only -- _fit_clusters is monkeypatched with pure
python; sklearn only runs in a guarded smoke test): membership rows +
distances, the HOLDOUT/deleted exclusion firewall in SQL, outlier flagging,
k clamping, corpus-size idempotency, POST /cluster, and the
window-signals triage endpoint."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.embeddings import store  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import cluster  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402

MODEL_ID = "vjepa2_1_vit_base_384@personcrop_v1"


def _seed_video(conn, vid, holdout=0, status="ready"):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s, fps,"
            " is_holdout, import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, 20.0, 10.0, ?, ?, ?, ?)",
            (vid, f"{vid}.mp4", f"sum_{vid}", holdout, status, now, now))


def _seed_windows(conn, cfg, vid, vectors, model_id=MODEL_ID):
    """One analysis window + pooled_window embedding row per vector."""
    now = db.iso_now()
    writer = store.ShardWriter(cfg.data_dir, model_id)
    wids = []
    with db.tx(conn):
        for i, vec in enumerate(vectors):
            wid = f"aw_{vid}_{i * 4000}"
            conn.execute(
                "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
                " person_presence, prelabel_status, created_at)"
                " VALUES (?, ?, ?, ?, 0.8, 'pending', ?)",
                (wid, vid, i * 4.0, i * 4.0 + 8.0, now))
            rel, idx = writer.add(vec)
            conn.execute(
                "INSERT INTO embeddings (analysis_window_id, model_id, kind,"
                " clip_idx, shard_path, row_index, dim, cache_key, created_at)"
                " VALUES (?, ?, 'pooled_window', 0, ?, ?, ?, ?, ?)",
                (wid, model_id, rel, idx, len(vec),
                 f"ck_{model_id}_{wid}", now))
            wids.append(wid)
    writer.flush()
    return wids


@pytest.fixture
def fake_fit(monkeypatch):
    """Deterministic stand-in: cluster by sign of dim 0, distance = row
    order (so the outlier threshold is predictable)."""
    seen = {}

    def fit(vectors, k, pca_dim, seed):
        seen["n"] = len(vectors)
        seen["k"] = k
        return ([0 if v[0] >= 0 else 1 for v in vectors],
                [float(i) for i in range(len(vectors))])
    monkeypatch.setattr(cluster, "_fit_clusters", fit)
    return seen


def _run_job(conn, cfg, model_id=MODEL_ID):
    cluster.enqueue(conn, model_id)
    job = q.lease(conn, "gpu")
    state = job_run.execute(conn, cfg, job["id"])
    return state, q.get_job(conn, job["id"])


def test_k_clamp():
    assert cluster.k_for(4) == 4          # never above n
    assert cluster.k_for(100) == 20       # sqrt(100)=10 -> floor 20
    assert cluster.k_for(2500) == 50      # sqrt
    assert cluster.k_for(10**6) == 200    # ceiling


def test_cluster_membership_and_holdout_exclusion(conn, cfg, fake_fit):
    _seed_video(conn, "vid_ca")
    _seed_video(conn, "vid_ch", holdout=1)
    _seed_video(conn, "vid_cd", status="deleted")
    live = _seed_windows(conn, cfg, "vid_ca",
                         [[1.0, 0.0], [0.5, 0.5], [-1.0, 0.0], [-0.5, 0.5]])
    _seed_windows(conn, cfg, "vid_ch", [[1.0, 0.0]])  # holdout: embedded...
    _seed_windows(conn, cfg, "vid_cd", [[1.0, 0.0]])  # deleted video

    state, job = _run_job(conn, cfg)
    assert state == states.SUCCEEDED, job["error"]
    assert fake_fit["n"] == 4  # ...but NEVER clustered
    assert fake_fit["k"] == 4

    run = conn.execute("SELECT * FROM cluster_runs WHERE model_id = ?",
                       (MODEL_ID,)).fetchone()
    assert run["status"] == "ready" and run["finished_at"]
    params = json.loads(run["params_json"])
    assert params["n_windows"] == 4 and params["pca_dim"] == 64

    members = conn.execute(
        "SELECT * FROM cluster_members WHERE cluster_run_id = ?"
        " ORDER BY analysis_window_id", (run["id"],)).fetchall()
    assert [m["analysis_window_id"] for m in members] == sorted(live)
    by_wid = {m["analysis_window_id"]: m for m in members}
    assert by_wid[live[0]]["cluster_idx"] == 0   # [1.0, 0.0]
    assert by_wid[live[2]]["cluster_idx"] == 1   # [-1.0, 0.0]
    assert all(m["distance"] is not None for m in members)
    # fake distances follow row (window-id) order 0..3; threshold =
    # sorted[int(.95*3)] = 2.0 -> only the dist-3 member flags
    assert [by_wid[w]["is_outlier"] for w in sorted(live)] == [0, 0, 0, 1]


def test_cluster_without_embeddings_fails(conn, cfg, fake_fit):
    _seed_video(conn, "vid_ce")
    state, job = _run_job(conn, cfg)
    assert state == states.FAILED
    assert "POST /embed" in job["error"]


def test_idempotency_key_tracks_corpus_size(conn, cfg, fake_fit):
    _seed_video(conn, "vid_cf")
    _seed_windows(conn, cfg, "vid_cf", [[1.0, 0.0], [0.0, 1.0]])
    j1 = cluster.enqueue(conn, MODEL_ID)
    j2 = cluster.enqueue(conn, MODEL_ID)
    assert j1["id"] == j2["id"]  # same corpus -> same job
    assert j1["idempotency_key"] == f"cluster:{MODEL_ID}:2"
    _seed_video(conn, "vid_cg")  # corpus grows -> a NEW run is due
    _seed_windows(conn, cfg, "vid_cg", [[0.5, 0.5]])
    j3 = cluster.enqueue(conn, MODEL_ID)
    assert j3["id"] != j1["id"]
    assert j3["idempotency_key"] == f"cluster:{MODEL_ID}:3"


def test_fit_clusters_sklearn_smoke(tmp_path):
    pytest.importorskip("sklearn")
    pytest.importorskip("numpy")
    vecs = ([[1.0, 0.0, 0.0]] * 5) + ([[0.0, 1.0, 0.0]] * 5)
    labels, dists = cluster._fit_clusters(vecs, 2, 64, 0)
    assert len(labels) == 10 and len(dists) == 10
    assert len(set(labels[:5])) == 1 and len(set(labels[5:])) == 1
    assert labels[0] != labels[5]
    assert all(d == pytest.approx(0.0, abs=1e-5) for d in dists)


# --------------------------------------------- window-signals triage hook

@pytest.fixture
def api(conn, cfg):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from videolabeler.api import embeddings as embeddings_api
    app = FastAPI()
    app.include_router(embeddings_api.build_router(cfg))
    return TestClient(app)


def test_post_cluster_endpoint(api, conn, cfg, fake_fit):
    _seed_video(conn, "vid_cj")
    _seed_windows(conn, cfg, "vid_cj", [[1.0, 0.0]])
    r = api.post("/api/video-labeler/cluster", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == MODEL_ID
    assert body["n_windows"] == 1
    assert body["job"]["type"] == "cluster" and body["job"]["lane"] == "gpu"


def test_post_cluster_409_without_embeddings(api, conn):
    r = api.post("/api/video-labeler/cluster", json={})
    assert r.status_code == 409


def test_window_signals_returns_latest_run(api, conn, cfg, fake_fit):
    _seed_video(conn, "vid_ck")
    wids = _seed_windows(conn, cfg, "vid_ck", [[1.0, 0.0], [-1.0, 0.0]])
    state, _job = _run_job(conn, cfg)
    assert state == states.SUCCEEDED
    r = api.get("/api/video-labeler/videos/vid_ck/window-signals")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == MODEL_ID
    assert body["cluster_run_id"]
    wins = {w["window_id"]: w for w in body["windows"]}
    assert set(wins) == set(wids)
    assert wins[wids[0]]["cluster_id"] == 0
    assert wins[wids[1]]["cluster_id"] == 1
    assert wins[wids[1]]["cluster_dist"] == 1.0
    assert wins[wids[1]]["is_outlier"] is True  # threshold idx int(.95*1)=0
    assert {"start_s", "end_s"} <= set(wins[wids[0]])


def test_window_signals_nulls_without_run_and_404_unknown_video(api, conn, cfg):
    _seed_video(conn, "vid_cl")
    _seed_windows(conn, cfg, "vid_cl", [[1.0, 0.0]])
    r = api.get("/api/video-labeler/videos/vid_cl/window-signals")
    assert r.status_code == 200
    body = r.json()
    assert body["cluster_run_id"] is None
    assert body["windows"][0]["cluster_id"] is None
    assert body["windows"][0]["is_outlier"] is None
    assert api.get("/api/video-labeler/videos/vid_nope/window-signals"
                   ).status_code == 404
