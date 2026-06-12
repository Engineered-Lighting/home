"""prelabel job (sqlite-only -- the VLM client is injected through
vlm.client.set_client_factory, keyframes + the vram probe are
monkeypatched): suggestion insertion without a revision bump, JSON
retry/giveup paths (vlm_failed + sidecar), 'other'->unknown hint routing,
disagreement cross-checks + confidence capping, resume via the per-window
done-marker, the vram guard, holdout refusal, the ACTIVE_SET+'other' enum,
and the POST /prelabel chaining endpoint."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db, labels, ontology  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import prelabel  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402
from videolabeler.vlm import client as vlm_client  # noqa: E402
from videolabeler.vlm import schema  # noqa: E402

VID = "vid_pre"

POSE_OK = {"max_persons": 1, "median_bbox": [0.4, 0.2, 0.6, 0.8],
           "bbox_area_frac": 0.12,
           "keypoint_visibility_summary": {"mean_visible_frac": 0.9,
                                           "n_detections": 6}}


def _p2(**over):
    doc = {"activity_primary": "cooking", "activity_other_hint": None,
           "posture": "standing", "quality_flags": [],
           "uncertainty": {"activity": 0.2, "posture": 0.3},
           "evidence": "person at the stove holding a pan",
           "evidence_timestamps": [1.0, 3.0]}
    doc.update(over)
    return json.dumps(doc)


class MockVlm:
    """pass-1 calls are json_mode=False, pass-2 json_mode=True."""

    def __init__(self, pass2_responses, pass1_text="a person at the counter"):
        self.model = "mock-vlm"
        self.pass1_text = pass1_text
        self.pass2_responses = list(pass2_responses)
        self.pass1_calls = []
        self.pass2_calls = []

    def chat(self, messages, json_mode=False):
        if not json_mode:
            self.pass1_calls.append(messages)
            return self.pass1_text
        self.pass2_calls.append(messages)
        if not self.pass2_responses:
            raise AssertionError("mock pass-2 responses exhausted")
        return self.pass2_responses.pop(0)


@pytest.fixture
def mock_vlm():
    def install(mock):
        vlm_client.set_client_factory(lambda cfg: mock)
        return mock
    yield install
    vlm_client.set_client_factory(None)


@pytest.fixture(autouse=True)
def _no_vram_check(monkeypatch):
    monkeypatch.setattr(prelabel, "_free_vram_gb", lambda: None)


@pytest.fixture(autouse=True)
def _fake_keyframes(monkeypatch, request):
    if "real_keyframes" in request.keywords:
        return

    def fake(cfg, video_row, window, pose_summary):
        kdir = Path(cfg.keyframes_dir) / video_row["id"] / window["id"]
        kdir.mkdir(parents=True, exist_ok=True)
        frames = []
        for k in range(2):
            (kdir / f"frame_{k}.jpg").write_bytes(b"\xff\xd8fakejpg")
            frames.append({"t": window["start_s"] + k + 0.5,
                           "frame": f"frame_{k}.jpg", "crop": None})
        meta = {"video_id": video_row["id"], "window_id": window["id"],
                "frames": frames, "created_at": db.iso_now()}
        (kdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta
    monkeypatch.setattr(prelabel, "_ensure_keyframes", fake)


def _seed(conn, vid=VID, duration=12.0, wins=None, holdout=0):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, local_path,"
            " duration_s, is_holdout, import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
            (vid, "kitchen_cam.mp4", f"sum_{vid}",
             f"videos/originals/{vid}/{vid}.mp4", duration, holdout, now, now))
        for w in (wins or []):
            pose = w.get("pose", POSE_OK)
            conn.execute(
                "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
                " person_presence, motion_energy, pose_summary_json,"
                " prelabel_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"aw_{vid}_{int(w['start'] * 1000)}", vid, w["start"],
                 w["end"], w.get("presence"), w.get("motion", 0.2),
                 json.dumps(pose) if pose is not None else None,
                 w.get("status", "pending"), now))
    return vid


def _run_job(conn, cfg, vid):
    prelabel.enqueue_for_video(conn, cfg, vid)
    job = q.lease(conn, "gpu")
    state = job_run.execute(conn, cfg, job["id"])
    return state, q.get_job(conn, job["id"])


def _suggestions(conn, vid, axis):
    return conn.execute(
        "SELECT * FROM segments WHERE video_id = ? AND axis = ?"
        " AND is_current = 1 ORDER BY start_s", (vid, axis)).fetchall()


# --------------------------------------------------------------- happy path

def test_valid_json_inserts_suggestions_without_revision_bump(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.8}])
    mock = mock_vlm(MockVlm([_p2()]))
    state, job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED, job["error"]
    assert len(mock.pass1_calls) == 1 and len(mock.pass2_calls) == 1

    acts = _suggestions(conn, vid, "activity")
    assert len(acts) == 1
    a = acts[0]
    assert (a["start_s"], a["end_s"]) == (0.0, 8.0)  # window geometry
    assert a["value"] == "cooking"
    assert a["review_state"] == "prelabel" and a["source"] == "vlm"
    assert a["confidence"] == pytest.approx(0.8)  # 1 - uncertainty.activity
    ev = json.loads(a["evidence_json"])
    assert ev["window_id"] == f"aw_{vid}_0"
    assert ev["disagreement"] == 0.0
    assert ev["evidence_timestamps"] == [1.0, 3.0]

    pos = _suggestions(conn, vid, "posture")
    assert [p["value"] for p in pos] == ["standing"]
    assert pos[0]["confidence"] == pytest.approx(0.7)
    assert _suggestions(conn, vid, "quality") == []  # empty flags -> no row

    # VLM suggestion writes NEVER bump the revision
    assert labels.get_revision(conn, vid) == 0

    win = conn.execute("SELECT * FROM analysis_windows WHERE video_id = ?",
                       (vid,)).fetchone()
    assert win["prelabel_status"] == prelabel.done_key(cfg.vlm_model)

    side = json.loads(Path(cfg.prelabels_dir, vid, f"aw_{vid}_0.json")
                      .read_text(encoding="utf-8"))
    assert side["pass1"] == "a person at the counter"
    assert side["pass2"]["activity_primary"] == "cooking"
    assert side["pass2_raw"] and side["error"] is None
    assert side["checks"] == {"pose_geometry": 0.0, "motion": 0.0,
                              "adjacent": 0.0}


def test_malformed_then_valid_json_retries_with_error_appended(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.8}])
    mock = mock_vlm(MockVlm(["sorry, no JSON here", _p2()]))
    state, job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED, job["error"]
    assert len(mock.pass2_calls) == 2
    retry_text = mock.pass2_calls[1][1]["content"][0]["text"]
    assert "failed validation" in retry_text
    assert "sorry, no JSON here" in retry_text  # prior reply echoed back
    assert [s["value"] for s in _suggestions(conn, vid, "activity")] == ["cooking"]


def test_persistent_malformed_marks_vlm_failed_with_sidecar(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.8}])
    mock = mock_vlm(MockVlm(["junk one", "junk two", "junk three"]))
    state, job = _run_job(conn, cfg, vid)
    # every eligible window failed -> the job fails loudly
    assert state == states.FAILED
    assert "vlm_failed" in job["error"]
    assert len(mock.pass2_calls) == 3  # 1 + MAX_JSON_RETRIES
    win = conn.execute("SELECT * FROM analysis_windows WHERE video_id = ?",
                       (vid,)).fetchone()
    assert win["prelabel_status"] == "vlm_failed"
    assert _suggestions(conn, vid, "activity") == []
    side = json.loads(Path(cfg.prelabels_dir, vid, f"aw_{vid}_0.json")
                      .read_text(encoding="utf-8"))
    assert side["pass2"] is None
    assert side["pass2_raw"] == ["junk one", "junk two", "junk three"]
    assert "invalid after 3 attempt(s)" in side["error"]


def test_other_becomes_unknown_with_hint_preserved(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.8}])
    mock_vlm(MockVlm([_p2(activity_primary="other",
                          activity_other_hint="folding laundry")]))
    state, _job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    a = _suggestions(conn, vid, "activity")[0]
    assert a["value"] == "unknown"  # no auto-invented custom labels
    assert json.loads(a["evidence_json"])["other_hint"] == "folding laundry"


def test_quality_flags_row_and_pose_forced_multiple_people(conn, cfg, mock_vlm):
    pose2 = dict(POSE_OK, max_persons=2)
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.9,
                             "pose": pose2}])
    mock_vlm(MockVlm([_p2(quality_flags=["dark"])]))
    state, _job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    qrows = _suggestions(conn, vid, "quality")
    assert len(qrows) == 1
    assert json.loads(qrows[0]["value"]) == ["dark", "multiple_people"]
    assert qrows[0]["confidence"] == pytest.approx(0.7)  # 1 - max(unc)


# ----------------------------------------------- disagreement + confidence

def test_disagreement_check_units():
    tall = {"median_bbox": [0.4, 0.1, 0.6, 0.9]}   # h/w = 4.0
    wide = {"median_bbox": [0.1, 0.4, 0.9, 0.6]}   # h/w = 0.25
    assert prelabel.pose_geometry_check("lying_down", tall) == 1.0
    assert prelabel.pose_geometry_check("standing", wide) == 1.0
    assert prelabel.pose_geometry_check("walking", wide) == 1.0
    assert prelabel.pose_geometry_check("standing", tall) == 0.0
    assert prelabel.pose_geometry_check("lying_down", wide) == 0.0
    assert prelabel.pose_geometry_check("standing", {}) == 0.0
    assert prelabel.pose_geometry_check("standing", None) == 0.0

    assert prelabel.motion_check("exercising", 0.01) == 1.0
    assert prelabel.motion_check("sleeping", 0.5) == 1.0
    assert prelabel.motion_check("exercising", 0.3) == 0.0
    assert prelabel.motion_check("cooking", 0.0) == 0.0  # neither set
    assert prelabel.motion_check("sleeping", None) == 0.0

    assert prelabel.adjacency_check("cooking", "walking") == 0.5
    assert prelabel.adjacency_check("cooking", "cooking") == 0.0
    assert prelabel.adjacency_check("cooking", None) == 0.0

    assert prelabel.disagreement_of(
        {"pose_geometry": 0.0, "motion": 1.0, "adjacent": 0.5}) == 1.0
    assert prelabel.disagreement_of({}) == 0.0

    assert prelabel.cap_confidence(0.9, 0.5) == 0.5
    assert prelabel.cap_confidence(0.9, 0.0) == 0.9
    assert prelabel.cap_confidence(0.3, 1.0) == 0.3  # already below the cap


def test_confidence_capped_when_disagreement_fires(conn, cfg, mock_vlm):
    # watching_tv (low-motion class) over a high-motion window -> check fires
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.9,
                             "motion": 0.6}])
    mock_vlm(MockVlm([_p2(activity_primary="watching_tv",
                          posture="sitting_reclined",
                          uncertainty={"activity": 0.1, "posture": 0.2})]))
    state, _job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    a = _suggestions(conn, vid, "activity")[0]
    p = _suggestions(conn, vid, "posture")[0]
    assert a["confidence"] == 0.5  # would be 0.9 -- capped
    assert p["confidence"] == 0.5  # cap applies across axes of the window
    ev = json.loads(a["evidence_json"])
    assert ev["disagreement"] == 1.0
    assert ev["checks"]["motion"] == 1.0


def test_adjacent_window_discontinuity_is_recorded(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[
        {"start": 0.0, "end": 8.0, "presence": 0.9},
        {"start": 4.0, "end": 12.0, "presence": 0.9},
    ])
    mock_vlm(MockVlm([_p2(activity_primary="cooking"),
                      _p2(activity_primary="phone_use")]))
    state, _job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    acts = _suggestions(conn, vid, "activity")
    ev0 = json.loads(acts[0]["evidence_json"])
    ev1 = json.loads(acts[1]["evidence_json"])
    assert ev0["checks"]["adjacent"] == 0.0  # no previous window
    assert ev1["checks"]["adjacent"] == 0.5  # cooking -> phone_use
    assert acts[1]["confidence"] == 0.5  # capped by the soft signal


# ----------------------------------------------------- resume / guards

def test_resume_skips_windows_already_done_for_same_prompt(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[
        {"start": 0.0, "end": 8.0, "presence": 0.9,
         "status": prelabel.done_key(cfg.vlm_model)},
        {"start": 4.0, "end": 12.0, "presence": 0.9},
    ])
    mock = mock_vlm(MockVlm([_p2()]))
    state, _job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    assert len(mock.pass1_calls) == 1  # only the pending window hit the VLM
    acts = _suggestions(conn, vid, "activity")
    assert [(a["start_s"], a["end_s"]) for a in acts] == [(4.0, 12.0)]


def test_rerun_replaces_window_suggestions_not_duplicates(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    mock_vlm(MockVlm([_p2()]))
    state, job = _run_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    # force a fresh pass over the same window (as a crash-resume would)
    with db.tx(conn):
        conn.execute("UPDATE analysis_windows SET prelabel_status = 'pending'"
                     " WHERE video_id = ?", (vid,))
    vlm_client.set_client_factory(
        lambda cfg: MockVlm([_p2(activity_primary="eating_drinking")]))
    q.retry(conn, job["id"])
    rerun = q.lease(conn, "gpu")
    assert job_run.execute(conn, cfg, rerun["id"]) == states.SUCCEEDED
    acts = _suggestions(conn, vid, "activity")
    assert [a["value"] for a in acts] == ["eating_drinking"]  # replaced


def test_insufficient_vram_fails_the_job(conn, cfg, mock_vlm, monkeypatch):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    mock = mock_vlm(MockVlm([_p2()]))
    monkeypatch.setattr(prelabel, "_free_vram_gb", lambda: 10.0)
    state, job = _run_job(conn, cfg, vid)
    assert state == states.FAILED
    assert "insufficient_vram: run a gpu-exclusive window first" in job["error"]
    assert mock.pass1_calls == []  # failed BEFORE any VLM call
    win = conn.execute("SELECT prelabel_status FROM analysis_windows"
                       " WHERE video_id = ?", (vid,)).fetchone()
    assert win["prelabel_status"] == "pending"  # untouched


def test_perception_missing_fails_fast(conn, cfg, mock_vlm):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": None}])
    mock_vlm(MockVlm([_p2()]))
    state, job = _run_job(conn, cfg, vid)
    assert state == states.FAILED
    assert "perceive" in job["error"]


def test_holdout_video_is_refused(conn, cfg, mock_vlm):
    vid = _seed(conn, holdout=1,
                wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    mock_vlm(MockVlm([_p2()]))
    state, job = _run_job(conn, cfg, vid)
    assert state == states.FAILED
    assert "holdout" in job["error"]


# ------------------------------------------------------- schema contract

def test_vlm_activity_enum_is_active_set_plus_other():
    assert schema.VLM_ACTIVITY_VALUES == tuple(ontology.ACTIVE_SET) + ("other",)
    assert "other" not in ontology.ACTIVITY_PRIMARY
    assert schema.VLM_POSTURE_VALUES == tuple(ontology.POSTURE)


def test_parse_pass2_tolerates_fences_and_prose():
    fenced = "```json\n" + _p2() + "\n```"
    assert schema.parse_pass2(fenced).activity_primary == "cooking"
    inline = "Here is my answer:\n" + _p2() + "\nHope that helps!"
    assert schema.parse_pass2(inline).posture == "standing"


def test_parse_pass2_rejects_bad_values():
    with pytest.raises(ValueError, match="activity_primary"):
        schema.parse_pass2(_p2(activity_primary="juggling"))
    with pytest.raises(ValueError, match="posture"):
        schema.parse_pass2(_p2(posture="hovering"))
    with pytest.raises(ValueError, match="quality_flags"):
        schema.parse_pass2(_p2(quality_flags=["bogus_flag"]))
    with pytest.raises(ValueError):
        schema.parse_pass2(_p2(uncertainty={"activity": 1.5, "posture": 0.1}))
    with pytest.raises(ValueError, match="no JSON object"):
        schema.parse_pass2("definitely not json")
    with pytest.raises(ValueError):
        schema.parse_pass2("")


# ------------------------------------------------------ POST /prelabel API

@pytest.fixture
def api(conn, cfg):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from videolabeler.api import prelabel as prelabel_api
    app = FastAPI()
    app.include_router(prelabel_api.build_router(cfg))
    return TestClient(app)


def test_post_prelabel_requires_targets(api):
    assert api.post("/api/video-labeler/prelabel", json={}).status_code == 400


def test_post_prelabel_chains_perceive_when_pose_missing(api, conn, cfg):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": None,
                             "pose": None}])
    r = api.post("/api/video-labeler/prelabel", json={"video_ids": [vid]})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert [j["type"] for j in jobs] == ["perceive"]
    row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                       (f"perceive:{vid}",)).fetchone()
    assert q.payload(row)["then_prelabel"] is True


def test_post_prelabel_enqueues_gpu_job_when_perception_present(api, conn, cfg):
    vid = _seed(conn, wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    r = api.post("/api/video-labeler/prelabel", json={"video_ids": [vid]})
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert [j["type"] for j in jobs] == ["prelabel"]
    assert jobs[0]["lane"] == "gpu"
    key = prelabel.idempotency_key(vid, cfg.vlm_model)
    assert conn.execute("SELECT 1 FROM jobs WHERE idempotency_key = ?",
                        (key,)).fetchone() is not None


def test_post_prelabel_skips_holdout_and_unknown(api, conn):
    vid = _seed(conn, holdout=1,
                wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    r = api.post("/api/video-labeler/prelabel",
                 json={"video_ids": [vid, "vid_nope"]})
    assert r.status_code == 200
    body = r.json()
    assert body["jobs"] == []
    reasons = {s["video_id"]: s["reason"] for s in body["skipped"]}
    assert "holdout" in reasons[vid]
    assert reasons["vid_nope"] == "not found"


def test_post_prelabel_all_pending_excludes_holdout(api, conn):
    a = _seed(conn, vid="vid_pa",
              wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    _seed(conn, vid="vid_pb", holdout=1,
          wins=[{"start": 0.0, "end": 8.0, "presence": 0.9}])
    _seed(conn, vid="vid_pc",
          wins=[{"start": 0.0, "end": 8.0, "presence": 0.9,
                 "status": "done:qwen2.5vl:32b:v1"}])  # nothing pending
    r = api.post("/api/video-labeler/prelabel", json={"all_pending": True})
    assert r.status_code == 200
    body = r.json()
    assert len(body["jobs"]) == 1  # only vid_pa
    assert body["jobs"][0]["type"] == "prelabel"
    assert conn.execute(
        "SELECT 1 FROM jobs WHERE idempotency_key LIKE ?",
        (f"prelabel:{a}:%",)).fetchone() is not None
