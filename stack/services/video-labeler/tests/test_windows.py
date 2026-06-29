"""Analysis-window grid: pure grid math (durations/edges), job idempotency,
M2 columns left NULL, probe fan-out chaining (ffprobe monkeypatched -- no
ffmpeg binary needed; sqlite-only)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402
from videolabeler.jobs import windows  # noqa: E402


def _seed_video(conn, vid="vid_w", duration=20.0):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, local_path, duration_s,"
            " import_status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'probed', ?, ?)",
            (vid, f"{vid}.mp4", f"sum_{vid}", f"videos/originals/{vid}/{vid}.mp4",
             duration, now, now))
    return vid


def _run_windows_job(conn, cfg, vid):
    q.enqueue(conn, "windows", {"video_id": vid}, "cpu", f"windows:{vid}")
    job = q.lease(conn, "cpu")
    return job, job_run.execute(conn, cfg, job["id"])


def test_grid_durations_and_edges():
    assert windows.grid(None) == []
    assert windows.grid(0) == []
    assert windows.grid(1.9) == []  # whole video shorter than 2s -> no grid
    assert windows.grid(2.0) == [(0.0, 2.0)]
    assert windows.grid(5.0) == [(0.0, 5.0)]
    assert windows.grid(8.0) == [(0.0, 8.0)]
    # final window clamped to the end; emission stops once it reaches it
    assert windows.grid(9.0) == [(0.0, 8.0), (4.0, 9.0)]
    assert windows.grid(12.0) == [(0.0, 8.0), (4.0, 12.0)]
    assert windows.grid(13.0) == [(0.0, 8.0), (4.0, 12.0), (8.0, 13.0)]
    g = windows.grid(60.0)
    assert len(g) == 14
    assert g[0] == (0.0, 8.0) and g[-1] == (52.0, 60.0)
    assert all(end - start >= windows.MIN_WINDOW_S for start, end in g)
    assert all(g[i + 1][0] - g[i][0] == windows.STRIDE_S for i in range(len(g) - 1))


def test_windows_job_creates_grid_rows(conn, cfg):
    vid = _seed_video(conn, duration=20.0)
    _, state = _run_windows_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    rows = conn.execute("SELECT * FROM analysis_windows WHERE video_id = ?"
                        " ORDER BY start_s", (vid,)).fetchall()
    assert [(r["start_s"], r["end_s"]) for r in rows] == \
        [(0.0, 8.0), (4.0, 12.0), (8.0, 16.0), (12.0, 20.0)]
    assert [r["id"] for r in rows] == [f"aw_{vid}_0", f"aw_{vid}_4000",
                                       f"aw_{vid}_8000", f"aw_{vid}_12000"]
    for r in rows:  # M2 fills these; M1 leaves the machine grid bare
        assert r["person_presence"] is None
        assert r["motion_energy"] is None
        assert r["pose_summary_json"] is None
        assert r["prelabel_status"] == "pending"


def test_windows_job_is_idempotent_on_rerun(conn, cfg):
    vid = _seed_video(conn, duration=20.0)
    job, state = _run_windows_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    q.retry(conn, job["id"])
    rerun = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, rerun["id"]) == states.SUCCEEDED
    n = conn.execute("SELECT COUNT(*) FROM analysis_windows WHERE video_id = ?",
                     (vid,)).fetchone()[0]
    assert n == 4  # immutable grid: re-run inserts nothing new


def test_windows_job_skips_sub_2s_video(conn, cfg):
    vid = _seed_video(conn, vid="vid_tiny", duration=1.5)
    _, state = _run_windows_job(conn, cfg, vid)
    assert state == states.SUCCEEDED
    assert conn.execute("SELECT COUNT(*) FROM analysis_windows").fetchone()[0] == 0


def test_windows_job_fails_without_probe(conn, cfg):
    vid = _seed_video(conn, vid="vid_np", duration=None)
    _, state = _run_windows_job(conn, cfg, vid)
    assert state == states.FAILED
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert "probe must run first" in job["error"]


def test_probe_fans_out_windows_job(conn, cfg, monkeypatch):
    from videolabeler.media import ffmpeg
    fake = {"format": {"duration": "12.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264",
                         "width": 320, "height": 240,
                         "avg_frame_rate": "10/1"}]}
    monkeypatch.setattr(ffmpeg, "ffprobe", lambda path: fake)
    vid = _seed_video(conn, vid="vid_pf", duration=None)
    q.enqueue(conn, "probe", {"video_id": vid}, "cpu", f"probe:{vid}")
    job = q.lease(conn, "cpu")
    assert job_run.execute(conn, cfg, job["id"]) == states.SUCCEEDED
    keys = {r["idempotency_key"] for r in conn.execute(
        "SELECT idempotency_key FROM jobs WHERE state = 'queued'")}
    assert {f"proxy:{vid}:480p", f"sprite:{vid}", f"windows:{vid}"} <= keys
    row = conn.execute("SELECT duration_s FROM videos WHERE id = ?",
                       (vid,)).fetchone()
    assert row["duration_s"] == 12.0
