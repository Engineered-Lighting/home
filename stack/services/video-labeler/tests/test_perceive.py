"""perceive job (sqlite-only -- _analyze_video is monkeypatched, no
torch/av/ffmpeg): pure aggregation + span merging, windows->perceive
chaining, per-window stat persistence, the no_person auto-prelabel
(merged spans, source='rule', revision untouched), re-run idempotency,
and the then_prelabel chain into the gpu lane."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db, labels  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import perceive  # noqa: E402
from videolabeler.jobs import prelabel  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402
from videolabeler.jobs import windows  # noqa: E402

VID = "vid_per"


def _seed_video(conn, vid=VID, duration=24.0, **cols):
    now = db.iso_now()
    row = {"filename": f"{vid}.mp4", "checksum": f"sum_{vid}",
           "local_path": f"videos/originals/{vid}/{vid}.mp4",
           "duration_s": duration, "import_status": "probed", **cols}
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, local_path,"
            " duration_s, is_holdout, import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, row["filename"], row["checksum"], row["local_path"],
             row["duration_s"], row.get("is_holdout", 0),
             row["import_status"], now, now))
    return vid


def _make_windows(conn, cfg, vid):
    q.enqueue(conn, "windows", {"video_id": vid}, "cpu", f"windows:{vid}")
    job = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, job["id"]) == states.SUCCEEDED


def _person(bbox=(0.4, 0.2, 0.6, 0.8), kp=0.9):
    x1, y1, x2, y2 = bbox
    return {"bbox": [x1, y1, x2, y2],
            "area_frac": (x2 - x1) * (y2 - y1), "kp_frac": kp}


def _fake_analyze(presence_by_start, motion=0.2, pose=None):
    """_analyze_video stand-in keyed on window start_s."""
    def fake(cfg, src_path, rotation_deg, wins, duration_s=None, progress=None):
        out = {}
        for wid, ws, we in wins:
            p = presence_by_start.get(ws, 0.0)
            summary = (pose or {"max_persons": 1,
                                "median_bbox": [0.4, 0.2, 0.6, 0.8],
                                "bbox_area_frac": 0.12,
                                "keypoint_visibility_summary":
                                    {"mean_visible_frac": 0.9,
                                     "n_detections": 5}})
            out[wid] = {"person_presence": p,
                        "motion_energy": motion if p else 0.0,
                        "pose_summary": summary if p else
                        {"max_persons": 0, "median_bbox": None,
                         "bbox_area_frac": None,
                         "keypoint_visibility_summary": None}}
        return out
    return fake


# ------------------------------------------------------------ pure helpers

def test_merge_no_person_spans():
    f = perceive.merge_no_person_spans
    assert f([]) == []
    # all empty -> one span covering the union of the overlapping grid
    assert f([(0, 8, 0.0), (4, 12, 0.0), (8, 16, 0.0)]) == [(0, 16)]
    # person in the middle splits the run
    assert f([(0, 8, 0.0), (4, 12, 0.5), (8, 16, 0.0)]) == [(0, 8), (8, 16)]
    # trailing run is flushed
    assert f([(0, 8, 0.7), (4, 12, 0.0), (8, 16, 0.0)]) == [(4, 16)]
    # presence None (perception missing) is NOT no_person
    assert f([(0, 8, None), (4, 12, 0.0)]) == [(4, 12)]
    # nothing empty
    assert f([(0, 8, 0.1), (4, 12, 1.0)]) == []


def test_aggregate_window_stats():
    records = [
        {"t": 0.5, "persons": [], "diff": None},
        {"t": 1.5, "persons": [_person()], "diff": 0.10},
        {"t": 2.5, "persons": [_person((0.0, 0.0, 0.2, 0.3), kp=0.5),
                               _person((0.3, 0.1, 0.7, 0.9), kp=0.7)],
         "diff": 0.30},
        {"t": 9.0, "persons": [], "diff": 0.0},
    ]
    out = perceive.aggregate_window_stats(
        records, [("w0", 0.0, 8.0), ("w1", 8.0, 16.0), ("w2", 16.0, 24.0)])
    w0 = out["w0"]
    assert w0["person_presence"] == round(2 / 3, 4)
    assert w0["motion_energy"] == round((0.10 + 0.30) / 2, 4)
    s = w0["pose_summary"]
    assert s["max_persons"] == 2
    # dominant per frame: frame1 person, frame2 the LARGER (0.3..0.7) one;
    # median of two bboxes = midpoint per coordinate
    assert s["median_bbox"] == [0.35, 0.15, 0.65, 0.85]
    assert s["bbox_area_frac"] == round((0.12 + 0.32) / 2, 4)
    assert s["keypoint_visibility_summary"]["mean_visible_frac"] == 0.8
    assert s["keypoint_visibility_summary"]["n_detections"] == 2
    # window with one empty frame
    w1 = out["w1"]
    assert w1["person_presence"] == 0.0
    assert w1["pose_summary"]["median_bbox"] is None
    # window with NO sampled frames at all
    w2 = out["w2"]
    assert w2["person_presence"] == 0.0
    assert w2["motion_energy"] == 0.0


# ------------------------------------------------------------- job behavior

def test_windows_tail_enqueues_perceive(conn, cfg):
    vid = _seed_video(conn, duration=20.0)
    _make_windows(conn, cfg, vid)
    row = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                       (f"perceive:{vid}",)).fetchone()
    assert row is not None
    assert row["state"] == states.QUEUED
    assert row["lane"] == "cpu"
    assert q.payload(row) == {"video_id": vid}


def test_perceive_updates_windows_and_merges_no_person(conn, cfg, monkeypatch):
    vid = _seed_video(conn, duration=24.0)  # grid starts 0,4,8,12,16
    _make_windows(conn, cfg, vid)
    monkeypatch.setattr(
        perceive, "_analyze_video",
        _fake_analyze({0.0: 0.0, 4.0: 0.0, 8.0: 0.6, 12.0: 0.8, 16.0: 0.0},
                      motion=0.25))
    job = q.lease(conn, "cpu")  # the windows-tail perceive job
    assert job["type"] == "perceive"
    assert job_run.execute(conn, cfg, job["id"]) == states.SUCCEEDED

    rows = conn.execute("SELECT * FROM analysis_windows WHERE video_id = ?"
                        " ORDER BY start_s", (vid,)).fetchall()
    assert [r["person_presence"] for r in rows] == [0.0, 0.0, 0.6, 0.8, 0.0]
    assert rows[2]["motion_energy"] == 0.25
    pose = json.loads(rows[2]["pose_summary_json"])
    assert pose["max_persons"] == 1
    assert pose["median_bbox"] == [0.4, 0.2, 0.6, 0.8]
    assert [r["prelabel_status"] for r in rows] == \
        ["no_person", "no_person", "pending", "pending", "no_person"]

    # no_person auto-prelabel: ADJACENT empties merged, not 8s confetti
    sugg = conn.execute(
        "SELECT * FROM segments WHERE video_id = ? AND is_current = 1"
        " ORDER BY axis, start_s", (vid,)).fetchall()
    by_axis = {}
    for s in sugg:
        by_axis.setdefault(s["axis"], []).append(s)
    for axis in ("activity", "posture"):
        spans = [(s["start_s"], s["end_s"]) for s in by_axis[axis]]
        assert spans == [(0.0, 12.0), (16.0, 24.0)]
        for s in by_axis[axis]:
            assert s["value"] == "no_person"
            assert s["source"] == "rule"
            assert s["review_state"] == "prelabel"
            assert s["confidence"] == 0.95
    # rule suggestions never bump the revision and stay out of the labels doc
    assert labels.get_revision(conn, vid) == 0
    doc = labels.labels_doc(conn, vid)
    assert doc["axes"]["activity_primary"] == []


def test_perceive_rerun_does_not_duplicate_rule_suggestions(conn, cfg, monkeypatch):
    vid = _seed_video(conn, duration=12.0)  # windows (0,8),(4,12)
    _make_windows(conn, cfg, vid)
    monkeypatch.setattr(perceive, "_analyze_video",
                        _fake_analyze({0.0: 0.0, 4.0: 0.0}))
    job = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, job["id"]) == states.SUCCEEDED
    q.retry(conn, job["id"])
    rerun = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, rerun["id"]) == states.SUCCEEDED
    current = conn.execute(
        "SELECT axis, COUNT(*) AS n FROM segments WHERE video_id = ?"
        " AND is_current = 1 GROUP BY axis", (vid,)).fetchall()
    assert {r["axis"]: r["n"] for r in current} == \
        {"activity": 1, "posture": 1}
    retired = conn.execute(
        "SELECT COUNT(*) FROM segments WHERE video_id = ? AND is_current = 0",
        (vid,)).fetchone()[0]
    assert retired == 2  # first run's pair, retired by the re-run


def test_perceive_chains_prelabel_when_payload_asks(conn, cfg, monkeypatch):
    vid = _seed_video(conn, duration=12.0)
    now = db.iso_now()
    with db.tx(conn):
        for start, end in windows.grid(12.0):
            conn.execute(
                "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
                " prelabel_status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                (windows.window_id(vid, start), vid, start, end, now))
    monkeypatch.setattr(perceive, "_analyze_video",
                        _fake_analyze({0.0: 0.5, 4.0: 0.5}))
    perceive.enqueue_for_video(conn, vid, then_prelabel=True)
    job = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, job["id"]) == states.SUCCEEDED
    key = prelabel.idempotency_key(vid, cfg.vlm_model)
    chained = conn.execute("SELECT * FROM jobs WHERE idempotency_key = ?",
                           (key,)).fetchone()
    assert chained is not None
    assert chained["state"] == states.QUEUED
    assert chained["lane"] == "gpu"


def test_enqueue_for_video_grafts_chain_and_retries_terminal(conn, cfg):
    vid = _seed_video(conn, duration=12.0)
    plain = q.enqueue(conn, "perceive", {"video_id": vid}, "cpu",
                      f"perceive:{vid}")
    assert not q.payload(plain).get("then_prelabel")
    grafted = perceive.enqueue_for_video(conn, vid, then_prelabel=True)
    assert grafted["id"] == plain["id"]
    assert q.payload(grafted)["then_prelabel"] is True
    # terminal job under the same key is retried so the chain can heal
    leased = q.lease(conn, "cpu")
    q.fail(conn, leased["id"], "boom")
    again = perceive.enqueue_for_video(conn, vid, then_prelabel=True)
    assert again["id"] == plain["id"]
    assert again["state"] == states.QUEUED
