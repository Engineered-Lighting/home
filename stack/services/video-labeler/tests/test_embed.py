"""embed job (sqlite-only -- the encoder is injected through
embeddings.backbone.set_embedder_factory, clip decode and the vram probe
are monkeypatched): clip planning math, person-crop-primary plan + cache
keys, per-clip + window-mean rows in fp16 shards, cache-key skip on re-run,
crash-resume via the flush-on-failure path, CUDA OOM batch auto-halving,
the vram fail-fast, holdout INCLUSION, and the POST /embed endpoint."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.embeddings import backbone, store  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import embed  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402

VID = "vid_emb"
MODEL_ID = "vjepa2_1_vit_base_384@personcrop_v1"

POSE_OK = {"max_persons": 1, "median_bbox": [0.4, 0.2, 0.6, 0.8],
           "bbox_area_frac": 0.12}
POSE_NONE = {"max_persons": 0, "median_bbox": None, "bbox_area_frac": None}


class OutOfMemoryError(Exception):
    """Stand-in matched by name, exactly like torch.cuda.OutOfMemoryError."""


class MockEmbedder:
    model_id_base = "vjepa2_1_vit_base_384"
    img_size = 384
    dim = 8

    def __init__(self, fail_oom=0):
        self.fail_oom = fail_oom
        self.attempts = []   # clip-batch size of EVERY call (incl. OOM)
        self.calls = []      # clip-batch size of successful calls
        self._counter = 0.0

    def embed_clips(self, clips):
        self.attempts.append(len(clips))
        if self.fail_oom > 0:
            self.fail_oom -= 1
            raise OutOfMemoryError("mock CUDA OOM")
        self.calls.append(len(clips))
        out = []
        for _ in clips:
            self._counter += 1.0
            out.append([self._counter] * self.dim)
        return out


@pytest.fixture
def mock_embedder():
    def install(mock):
        backbone.set_embedder_factory(lambda cfg: mock)
        return mock
    yield install
    backbone.set_embedder_factory(None)


@pytest.fixture(autouse=True)
def _no_vram_check(monkeypatch):
    monkeypatch.setattr(embed, "_free_vram_gb", lambda: None)


@pytest.fixture(autouse=True)
def _fake_decode(monkeypatch):
    """Stand-in clips: the mock embedder never looks inside them."""
    def fake(cfg, video_row, plan, img_size):
        return [("clip", plan["window_id"], c["clip_idx"])
                for c in plan["clips"]]
    monkeypatch.setattr(embed, "_decode_window_clips", fake)


def _seed(conn, vid=VID, duration=20.0, fps=10.0, wins=None, holdout=0):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, local_path,"
            " duration_s, fps, width, height, is_holdout, import_status,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 640, 480, ?, 'ready', ?, ?)",
            (vid, "kitchen_cam.mp4", f"sum_{vid}",
             f"videos/originals/{vid}/{vid}.mp4", duration, fps, holdout,
             now, now))
        for w in (wins or []):
            pose = w.get("pose", POSE_OK)
            conn.execute(
                "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
                " person_presence, motion_energy, pose_summary_json,"
                " prelabel_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?,"
                " 'pending', ?)",
                (f"aw_{vid}_{int(w['start'] * 1000)}", vid, w["start"],
                 w["end"], w.get("presence", 0.8), w.get("motion", 0.2),
                 json.dumps(pose) if pose is not None else None, now))
    return vid


def _run_job(conn, cfg, vid, variant=embed.DEFAULT_VARIANT):
    embed.enqueue_for_video(conn, cfg, vid, variant)
    job = q.lease(conn, "gpu")
    state = job_run.execute(conn, cfg, job["id"])
    return state, q.get_job(conn, job["id"])


def _rows(conn, vid, model_id=MODEL_ID):
    return conn.execute(
        "SELECT e.* FROM embeddings e JOIN analysis_windows w"
        " ON w.id = e.analysis_window_id WHERE w.video_id = ?"
        " AND e.model_id = ? ORDER BY w.start_s, e.kind DESC, e.clip_idx",
        (vid, model_id)).fetchall()


# ------------------------------------------------------------ planning math

def test_plan_clips_even_placement_and_step():
    clips, step = embed.plan_clips(0.0, 8.0, 10.0, 20.0)
    assert step == 1  # round(10 / 7.5) = 1
    assert len(clips) == 3 and all(len(c) == 16 for c in clips)
    assert clips[0][0] == 0 and clips[0][-1] == 15
    assert clips[2][-1] == 79  # last clip ends on the window's last frame
    assert clips[1][0] == (clips[0][0] + clips[2][0]) // 2  # evenly placed
    # 30 fps -> frame_step 4 (the official 16x4 recipe geometry)
    clips30, step30 = embed.plan_clips(0.0, 8.0, 30.0, 20.0)
    assert step30 == 4
    assert clips30[0] == list(range(0, 64, 4))


def test_plan_clips_short_window_repeats_last_frame():
    clips, _step = embed.plan_clips(0.0, 2.0, 2.0, 2.0)
    # 4 source frames for a 16-frame clip: indices clamp to the last frame
    assert clips[0][:4] == [0, 1, 2, 3]
    assert set(clips[0][4:]) == {3}
    # never past the video end
    assert all(ix <= 3 for c in clips for ix in c)


def test_plan_window_personcrop_vs_fallback_vs_wholeframe(conn, cfg):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (VID,)).fetchone()
    win = conn.execute("SELECT * FROM analysis_windows WHERE video_id = ?",
                       (VID,)).fetchone()
    pc = embed.plan_window(MODEL_ID, row, win, "personcrop_v1", POSE_OK)
    assert pc["mode"] == "personcrop"
    left, top, right, bottom = pc["crop_box"]
    assert right - left == bottom - top  # square
    assert 0 <= left and right <= 640 and 0 <= top and bottom <= 480
    # bbox 0.2h on 480px -> side = 288*1.8 clamped to frame height
    assert bottom - top == 480
    assert len(pc["clips"]) == 3
    assert pc["clips"][0]["timestamps"][0] == pytest.approx(0.0)
    fb = embed.plan_window(MODEL_ID, row, win, "personcrop_v1", POSE_NONE)
    assert fb["mode"] == "wholeframe_fallback" and fb["crop_box"] is None
    wf = embed.plan_window(MODEL_ID, row, win, "wholeframe_v1", POSE_OK)
    assert wf["mode"] == "wholeframe" and wf["crop_box"] is None
    # crop box participates in the cache key
    assert pc["window_key"] != fb["window_key"]
    assert {c["cache_key"] for c in pc["clips"]}.isdisjoint(
        {c["cache_key"] for c in fb["clips"]})


def test_upright_dims_swap_on_rotation():
    assert embed.upright_dims(1920, 1080, 0) == (1920, 1080)
    assert embed.upright_dims(1920, 1080, 90) == (1080, 1920)
    assert embed.upright_dims(1920, 1080, 270) == (1080, 1920)
    assert embed.upright_dims(1920, 1080, 180) == (1920, 1080)


# ------------------------------------------------------------- happy path

def test_embed_writes_clip_rows_window_mean_and_shards(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0},
                      {"start": 4.0, "end": 12.0, "pose": POSE_NONE,
                       "presence": 0.0}])
    mock = mock_embedder(MockEmbedder())
    state, job = _run_job(conn, cfg, VID)
    assert state == states.SUCCEEDED, job["error"]
    assert mock.calls == [6]  # 2 windows x 3 clips, one batch

    rows = _rows(conn, VID)
    assert len(rows) == 8  # (3 pooled_clip + 1 pooled_window) x 2
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    assert len(by_kind["pooled_clip"]) == 6
    assert len(by_kind["pooled_window"]) == 2
    assert {r["clip_idx"] for r in by_kind["pooled_clip"]} == {0, 1, 2}
    assert all(r["dim"] == 8 and r["cache_key"] for r in rows)

    # window 1: clips [1.0..], [2.0..], [3.0..] -> mean [2.0..] (fp16-exact)
    w1 = f"aw_{VID}_0"
    mean_row = next(r for r in by_kind["pooled_window"]
                    if r["analysis_window_id"] == w1)
    vec = store.read_rows(cfg.data_dir,
                          [(mean_row["shard_path"], mean_row["row_index"])])[0]
    assert vec == [2.0] * 8

    # frames_json: exact indices + crop box on the person window, fallback
    # mode on the no-person one
    clip0 = next(r for r in by_kind["pooled_clip"]
                 if r["analysis_window_id"] == w1 and r["clip_idx"] == 0)
    meta = json.loads(clip0["frames_json"])
    assert meta["frame_indices"] == list(range(0, 16))
    assert meta["mode"] == "personcrop" and meta["crop_box"] is not None
    assert meta["preproc"] == embed.PREPROC_VERSION
    w2_mean = next(r for r in by_kind["pooled_window"]
                   if r["analysis_window_id"] == f"aw_{VID}_4000")
    assert json.loads(w2_mean["frames_json"])["mode"] == "wholeframe_fallback"

    shard = Path(cfg.data_dir, *rows[0]["shard_path"].split("/"))
    assert shard.is_file()


def test_rerun_skips_via_cache_keys(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    mock = mock_embedder(MockEmbedder())
    state, job = _run_job(conn, cfg, VID)
    assert state == states.SUCCEEDED
    assert mock.calls == [3]
    q.retry(conn, job["id"])
    rerun = q.lease(conn, "gpu")
    assert job_run.execute(conn, cfg, rerun["id"]) == states.SUCCEEDED
    assert mock.calls == [3]  # encoder never touched again
    assert len(_rows(conn, VID)) == 4  # no duplicates
    fresh = q.get_job(conn, rerun["id"])
    assert "already embedded" in fresh["progress_msg"]


def test_failure_flushes_done_windows_and_resume_embeds_the_rest(
        conn, cfg, mock_embedder, monkeypatch):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0},
                      {"start": 4.0, "end": 12.0},
                      {"start": 8.0, "end": 16.0}])
    mock = mock_embedder(MockEmbedder())
    monkeypatch.setattr(embed, "EMBED_BATCH_WINDOWS", 1)
    real_decode = embed._decode_window_clips

    def explode_on_last(cfg_, video_row, plan, img_size):
        if plan["window_id"] == f"aw_{VID}_8000":
            raise RuntimeError("decoder ate a corrupt GOP")
        return real_decode(cfg_, video_row, plan, img_size)

    monkeypatch.setattr(embed, "_decode_window_clips", explode_on_last)
    state, job = _run_job(conn, cfg, VID)
    assert state == states.FAILED
    assert "corrupt GOP" in job["error"]
    # the two finished windows were flushed on the failure path
    assert len(_rows(conn, VID)) == 8
    assert q.checkpoint_of(q.get_job(conn, job["id"]))["embedded"] == 2

    monkeypatch.setattr(embed, "_decode_window_clips", real_decode)
    q.retry(conn, job["id"])
    rerun = q.lease(conn, "gpu")
    assert job_run.execute(conn, cfg, rerun["id"]) == states.SUCCEEDED
    assert mock.calls == [3, 3, 3]  # only the failed window on resume
    assert len(_rows(conn, VID)) == 12


# ------------------------------------------------------------ OOM + guards

def test_cuda_oom_halves_batch_down_to_one(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": s, "end": s + 8.0}
                      for s in (0.0, 4.0, 8.0, 12.0)])
    mock = mock_embedder(MockEmbedder(fail_oom=2))
    state, job = _run_job(conn, cfg, VID)
    assert state == states.SUCCEEDED, job["error"]
    # batch 4 (12 clips) OOM -> 2 (6) OOM -> 1 (3) x 4 windows
    assert mock.attempts == [12, 6, 3, 3, 3, 3]
    assert len(_rows(conn, VID)) == 16


def test_cuda_oom_at_batch_one_fails_honestly(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    mock_embedder(MockEmbedder(fail_oom=99))
    state, job = _run_job(conn, cfg, VID)
    assert state == states.FAILED
    assert "OOM at window batch 1" in job["error"]


def test_non_oom_encoder_error_is_not_swallowed(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    broken = MockEmbedder()

    def boom(clips):
        raise RuntimeError("shape mismatch")
    broken.embed_clips = boom
    mock_embedder(broken)
    state, job = _run_job(conn, cfg, VID)
    assert state == states.FAILED
    assert "shape mismatch" in job["error"]


def test_insufficient_vram_fails_before_loading(conn, cfg, mock_embedder,
                                                monkeypatch):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    mock = mock_embedder(MockEmbedder())
    monkeypatch.setattr(embed, "_free_vram_gb", lambda: 2.0)
    state, job = _run_job(conn, cfg, VID)
    assert state == states.FAILED
    assert "insufficient_vram" in job["error"]
    assert mock.attempts == []  # never reached the encoder
    assert _rows(conn, VID) == []


def test_holdout_video_is_embedded(conn, cfg, mock_embedder):
    """Embeddings are NOT labels: holdout is embedded (the firewall lives in
    cluster runs / neighbor candidates)."""
    _seed(conn, holdout=1, wins=[{"start": 0.0, "end": 8.0}])
    mock_embedder(MockEmbedder())
    state, job = _run_job(conn, cfg, VID)
    assert state == states.SUCCEEDED, job["error"]
    assert len(_rows(conn, VID)) == 4


def test_perception_missing_fails_personcrop(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0, "pose": None}])
    mock_embedder(MockEmbedder())
    state, job = _run_job(conn, cfg, VID)
    assert state == states.FAILED
    assert "perceive" in job["error"]


def test_wholeframe_variant_has_its_own_model_id(conn, cfg, mock_embedder):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    mock_embedder(MockEmbedder())
    state, _job = _run_job(conn, cfg, VID, variant="wholeframe_v1")
    assert state == states.SUCCEEDED
    wf_id = "vjepa2_1_vit_base_384@wholeframe_v1"
    rows = _rows(conn, VID, model_id=wf_id)
    assert len(rows) == 4
    assert json.loads(rows[0]["frames_json"])["mode"] == "wholeframe"
    assert _rows(conn, VID, model_id=MODEL_ID) == []


# ------------------------------------------------------- POST /embed API

@pytest.fixture
def api(conn, cfg):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from videolabeler.api import embeddings as embeddings_api
    app = FastAPI()
    app.include_router(embeddings_api.build_router(cfg))
    return TestClient(app)


def test_post_embed_requires_targets(api):
    assert api.post("/api/video-labeler/embed", json={}).status_code == 400


def test_post_embed_rejects_unknown_model(api, conn):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    r = api.post("/api/video-labeler/embed",
                 json={"video_ids": [VID], "model": "bogus_v9"})
    assert r.status_code == 400


def test_post_embed_enqueues_gpu_job(api, conn, cfg):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0}])
    r = api.post("/api/video-labeler/embed", json={"video_ids": [VID]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == MODEL_ID
    assert [j["type"] for j in body["jobs"]] == ["embed"]
    assert body["jobs"][0]["lane"] == "gpu"
    key = embed.idempotency_key(VID, MODEL_ID)
    assert conn.execute("SELECT 1 FROM jobs WHERE idempotency_key = ?",
                        (key,)).fetchone() is not None


def test_post_embed_chains_perceive_when_pose_missing(api, conn):
    _seed(conn, wins=[{"start": 0.0, "end": 8.0, "pose": None}])
    r = api.post("/api/video-labeler/embed", json={"video_ids": [VID]})
    assert r.status_code == 200
    assert [j["type"] for j in r.json()["jobs"]] == ["perceive"]
    row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                       (f"perceive:{VID}",)).fetchone()
    assert q.payload(row)["then_embed"] == "personcrop_v1"


def test_post_embed_all_pending_includes_holdout(api, conn, cfg,
                                                 mock_embedder):
    _seed(conn, vid="vid_ea", wins=[{"start": 0.0, "end": 8.0}])
    _seed(conn, vid="vid_eb", holdout=1, wins=[{"start": 0.0, "end": 8.0}])
    _seed(conn, vid="vid_ec", wins=[{"start": 0.0, "end": 8.0}])
    mock_embedder(MockEmbedder())
    state, _ = _run_job(conn, cfg, "vid_ec")  # already embedded -> not pending
    assert state == states.SUCCEEDED
    r = api.post("/api/video-labeler/embed", json={"all_pending": True})
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    vids = {json.loads(conn.execute(
        "SELECT payload_json FROM jobs WHERE id = ?",
        (j["id"],)).fetchone()[0])["video_id"] for j in jobs}
    assert vids == {"vid_ea", "vid_eb"}  # holdout INCLUDED, done one absent
