"""End-to-end inline pipeline (no runner): synthetic inbox video ->
import_manual -> probe -> proxy -> sprite, all executed via jobs.run.execute
on leased jobs. Skips cleanly when ffmpeg/ffprobe are missing."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from conftest import make_test_video, requires_ffmpeg  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402
from videolabeler.jobs import run as job_run  # noqa: E402

pytestmark = requires_ffmpeg


def _drain(conn, cfg, lane="cpu") -> list:
    """Lease + execute queued jobs until the lane is empty; return final rows."""
    finished = []
    while True:
        job = q.lease(conn, lane)
        if job is None:
            return finished
        state = job_run.execute(conn, cfg, job["id"])
        finished.append((job["type"], state))


def test_import_probe_proxy_sprite(conn, cfg):
    inbox = Path(cfg.inbox_dir)
    make_test_video(inbox / "clip1.mp4", duration=4)
    (inbox / "clip1.mp4.meta.json").write_text(
        json.dumps({"drive_file_id": "drv123"}), encoding="utf-8")

    import_job = q.enqueue(conn, "import", {"batch_name": "testbatch"}, "cpu",
                           "import:testbatch")
    finished = _drain(conn, cfg)
    assert ("import", states.SUCCEEDED) in finished
    assert ("probe", states.SUCCEEDED) in finished
    assert ("proxy", states.SUCCEEDED) in finished
    assert ("sprite", states.SUCCEEDED) in finished
    assert all(s == states.SUCCEEDED for _, s in finished), finished

    video = conn.execute("SELECT * FROM videos").fetchone()
    assert video is not None
    assert video["import_status"] == "ready"
    assert video["filename"] == "clip1.mp4"
    assert video["source_batch"] == "testbatch"
    assert video["drive_file_id"] == "drv123"  # sidecar provenance merged
    assert 3.0 <= video["duration_s"] <= 5.0
    assert 9.0 <= video["fps"] <= 11.0
    assert video["width"] == 320 and video["height"] == 240
    assert video["id"] == "vid_" + video["checksum"][:16]

    # inbox emptied (video + sidecar moved next to the original)
    assert list(Path(cfg.inbox_dir).iterdir()) == []
    original = Path(cfg.resolve(video["local_path"]))
    assert original.is_file()
    assert (original.parent / "clip1.mp4.meta.json").is_file()

    # proxy exists and is +faststart (moov atom within the first 64KB)
    proxy = Path(cfg.resolve(f"proxies/{video['id']}.mp4"))
    assert proxy.is_file()
    head = proxy.read_bytes()[:65536]
    assert b"moov" in head, "proxy is not faststart — seeking would stall"
    assert not Path(str(proxy) + ".part").exists()

    # artifact rows
    arts = {r["kind"]: r for r in conn.execute(
        "SELECT * FROM video_artifacts WHERE video_id = ?", (video["id"],))}
    assert arts["proxy480"]["status"] == "ready"
    assert arts["sprite"]["status"] == "ready"

    # sprite manifest sane: 4s @ interval 1 -> a handful of tiles, one sheet
    meta = json.loads(arts["sprite"]["meta_json"])
    assert meta["tile_w"] == 160 and meta["tile_h"] == 90 and meta["cols"] == 10
    assert meta["interval_s"] == 1
    assert 3 <= meta["count"] <= 6
    assert meta["sheets"] == 1
    assert (Path(cfg.thumbs_dir) / video["id"] / "sheet_0.jpg").is_file()

    # label-state row seeded at revision 0
    state_row = conn.execute("SELECT * FROM video_label_state WHERE video_id = ?",
                             (video["id"],)).fetchone()
    assert state_row is not None and state_row["revision"] == 0

    # import job finished with a summary message
    final_import = q.get_job(conn, import_job["id"])
    assert final_import["progress"] == 1.0
    assert "imported 1" in final_import["progress_msg"]


def test_reimport_dedupes_on_checksum(conn, cfg):
    import shutil

    inbox = Path(cfg.inbox_dir)
    make_test_video(inbox / "clip1.mp4", duration=4)
    q.enqueue(conn, "import", {"batch_name": "b1"}, "cpu", "import:b1")
    assert all(s == states.SUCCEEDED for _, s in _drain(conn, cfg))
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1

    # identical bytes under a new name -> duplicate, no second row
    video = conn.execute("SELECT * FROM videos").fetchone()
    shutil.copyfile(cfg.resolve(video["local_path"]), inbox / "clip1_copy.mp4")
    q.enqueue(conn, "import", {"batch_name": "b2"}, "cpu", "import:b2")
    assert all(s == states.SUCCEEDED for _, s in _drain(conn, cfg))
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
