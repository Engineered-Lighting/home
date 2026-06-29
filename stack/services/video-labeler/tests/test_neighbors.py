"""GET /segments/{id}/neighbors (sqlite-only; vectors planted straight into
the fp16 shard store): response shape for the M3 evidence panel, cosine
ordering, REVIEWED-only candidates, the HOLDOUT firewall, same-video
exclusion (default + opt-out), k capping, and the not-embedded fallback."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.embeddings import store  # noqa: E402

MODEL_ID = "vjepa2_1_vit_base_384@personcrop_v1"


def _seed_video(conn, vid, holdout=0):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s, fps,"
            " is_holdout, import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, 20.0, 10.0, ?, 'ready', ?, ?)",
            (vid, f"{vid}.mp4", f"sum_{vid}", holdout, now, now))


def _seed_window(conn, cfg, writer, vid, start, end, vec,
                 model_id=MODEL_ID):
    now = db.iso_now()
    wid = f"aw_{vid}_{int(start * 1000)}"
    with db.tx(conn):
        conn.execute(
            "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
            " person_presence, prelabel_status, created_at)"
            " VALUES (?, ?, ?, ?, 0.8, 'pending', ?)",
            (wid, vid, start, end, now))
        rel, idx = writer.add(vec)
        conn.execute(
            "INSERT INTO embeddings (analysis_window_id, model_id, kind,"
            " clip_idx, shard_path, row_index, dim, cache_key, created_at)"
            " VALUES (?, ?, 'pooled_window', 0, ?, ?, ?, ?, ?)",
            (wid, model_id, rel, idx, len(vec), f"ck_{wid}_{model_id}", now))
    return wid


def _seed_segment(conn, vid, axis, start, end, value,
                  review_state="reviewed", seg_id=None):
    now = db.iso_now()
    seg_id = seg_id or f"seg_{vid}_{axis}_{int(start * 1000)}"
    with db.tx(conn):
        conn.execute(
            "INSERT INTO segments (id, video_id, axis, start_s, end_s, value,"
            " review_state, source, is_current, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'human', 1, ?, ?)",
            (seg_id, vid, axis, start, end, value, review_state, now, now))
    return seg_id


@pytest.fixture
def api(conn, cfg):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from videolabeler.api import embeddings as embeddings_api
    app = FastAPI()
    app.include_router(embeddings_api.build_router(cfg))
    return TestClient(app)


@pytest.fixture
def corpus(conn, cfg):
    """Query video Q + candidate videos A (reviewed), B (unreviewed),
    H (holdout, reviewed). Vectors are crafted so distances are knowable:
    the query is [1,0,0,0]."""
    w = store.ShardWriter(cfg.data_dir, MODEL_ID)
    _seed_video(conn, "vid_q")
    _seed_video(conn, "vid_a")
    _seed_video(conn, "vid_b")
    _seed_video(conn, "vid_h", holdout=1)
    ws = {
        "q1": _seed_window(conn, cfg, w, "vid_q", 0.0, 8.0, [1, 0, 0, 0]),
        # vid_q second window, same video -> excluded by default
        "q2": _seed_window(conn, cfg, w, "vid_q", 8.0, 16.0, [1, 0, 0, 0]),
        # vid_a: a1 identical (dist 0), a2 orthogonal (dist 1)
        "a1": _seed_window(conn, cfg, w, "vid_a", 0.0, 8.0, [2, 0, 0, 0]),
        "a2": _seed_window(conn, cfg, w, "vid_a", 8.0, 16.0, [0, 3, 0, 0]),
        # vid_b: identical vector but NO reviewed segment -> never a candidate
        "b1": _seed_window(conn, cfg, w, "vid_b", 0.0, 8.0, [1, 0, 0, 0]),
        # holdout: identical vector + reviewed -> firewalled in SQL
        "h1": _seed_window(conn, cfg, w, "vid_h", 0.0, 8.0, [1, 0, 0, 0]),
    }
    w.flush()
    seg_q = _seed_segment(conn, "vid_q", "activity", 2.0, 6.0, "cooking",
                          review_state="needs_review")
    _seed_segment(conn, "vid_q", "activity", 9.0, 15.0, "cooking")  # reviewed
    _seed_segment(conn, "vid_a", "activity", 0.0, 8.0, "cooking")
    _seed_segment(conn, "vid_a", "posture", 0.0, 7.0, "standing",
                  review_state="accepted")
    _seed_segment(conn, "vid_a", "activity", 8.0, 16.0, "phone_use")
    # vid_b only has an unreviewed (prelabel) segment
    _seed_segment(conn, "vid_b", "activity", 0.0, 8.0, "cooking",
                  review_state="prelabel")
    _seed_segment(conn, "vid_h", "activity", 0.0, 8.0, "cooking")
    return {"windows": ws, "seg_q": seg_q}


def test_neighbors_shape_ordering_and_firewalls(api, corpus):
    r = api.get(f"/api/video-labeler/segments/{corpus['seg_q']}/neighbors")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == MODEL_ID
    assert body["query_windows"] == 1
    got = body["neighbors"]
    # only vid_a qualifies: vid_b unreviewed, vid_h holdout, vid_q same-video
    assert [n["window_id"] for n in got] == [corpus["windows"]["a1"],
                                             corpus["windows"]["a2"]]
    a1, a2 = got
    assert a1["distance"] == pytest.approx(0.0, abs=1e-3)
    assert a2["distance"] == pytest.approx(1.0, abs=1e-3)
    assert a1["video_id"] == "vid_a"
    assert a1["t_center"] == pytest.approx(4.0)
    # max-overlap reviewed labels per axis, shaped for the evidence panel
    assert a1["labels"] == {"activity": "cooking", "posture": "standing"}
    assert a2["labels"] == {"activity": "phone_use", "posture": None}
    assert a1["keyframes"].endswith(
        f"/videos/vid_a/windows/{corpus['windows']['a1']}/keyframes")
    assert {"start_s", "end_s"} <= set(a1)


def test_neighbors_k_caps_results(api, corpus):
    r = api.get(f"/api/video-labeler/segments/{corpus['seg_q']}/neighbors?k=1")
    assert r.status_code == 200
    got = r.json()["neighbors"]
    assert len(got) == 1
    assert got[0]["window_id"] == corpus["windows"]["a1"]


def test_neighbors_same_video_optin_still_skips_own_footprint(api, corpus):
    r = api.get(f"/api/video-labeler/segments/{corpus['seg_q']}/neighbors"
                "?exclude_same_video=0&k=10")
    assert r.status_code == 200
    ids = [n["window_id"] for n in r.json()["neighbors"]]
    # q2 (reviewed, same video, no overlap with the query span) is now in;
    # q1 (the query segment's own window) never is
    assert corpus["windows"]["q2"] in ids
    assert corpus["windows"]["q1"] not in ids
    assert corpus["windows"]["h1"] not in ids  # firewall holds regardless


def test_neighbors_404_unknown_segment(api, corpus):
    r = api.get("/api/video-labeler/segments/seg_nope/neighbors")
    assert r.status_code == 404


def test_neighbors_unembedded_segment_returns_note(api, conn, cfg, corpus):
    _seed_video(conn, "vid_u")
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
            " prelabel_status, created_at) VALUES ('aw_vid_u_0', 'vid_u',"
            " 0.0, 8.0, 'pending', ?)", (now,))
    sid = _seed_segment(conn, "vid_u", "activity", 1.0, 5.0, "cooking",
                        review_state="needs_review")
    r = api.get(f"/api/video-labeler/segments/{sid}/neighbors")
    assert r.status_code == 200
    body = r.json()
    assert body["query_windows"] == 0
    assert body["neighbors"] == []
    assert "not embedded" in body["note"]


def test_neighbors_rejects_bogus_model(api, corpus):
    r = api.get(f"/api/video-labeler/segments/{corpus['seg_q']}/neighbors"
                "?model=bogus")
    assert r.status_code == 400
