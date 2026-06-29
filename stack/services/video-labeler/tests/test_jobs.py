"""Queue semantics: idempotent enqueue, lane leasing, stale recovery with
checkpoint preservation, cooperative cancel, retry."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402
from videolabeler.jobqueue import states  # noqa: E402


def test_enqueue_idempotent(conn):
    j1 = q.enqueue(conn, "probe", {"video_id": "vid_a"}, "cpu", "probe:vid_a")
    j2 = q.enqueue(conn, "probe", {"video_id": "vid_a"}, "cpu", "probe:vid_a")
    assert j1["id"] == j2["id"]
    assert q.payload(j2) == {"video_id": "vid_a"}
    j3 = q.enqueue(conn, "probe", {"video_id": "vid_b"}, "cpu", "probe:vid_b")
    assert j3["id"] != j1["id"]


def test_lease_respects_lanes(conn):
    cpu = q.enqueue(conn, "probe", {}, "cpu", "k:cpu")
    gpu = q.enqueue(conn, "prelabel", {}, "gpu", "k:gpu")
    leased_gpu = q.lease(conn, "gpu")
    assert leased_gpu["id"] == gpu["id"]
    assert leased_gpu["state"] == states.RUNNING
    assert leased_gpu["attempts"] == 1
    assert leased_gpu["heartbeat_at"] is not None
    assert q.lease(conn, "gpu") is None  # nothing queued in the gpu lane
    leased_cpu = q.lease(conn, "cpu")
    assert leased_cpu["id"] == cpu["id"]


def test_lease_oldest_first(conn):
    a = q.enqueue(conn, "probe", {}, "cpu", "old")
    with db.tx(conn):
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?",
                     (db.iso_at(0.0), a["id"]))
    q.enqueue(conn, "probe", {}, "cpu", "new")
    assert q.lease(conn, "cpu")["id"] == a["id"]


def test_recover_stale_requeues_and_preserves_checkpoint(conn):
    job = q.enqueue(conn, "import", {}, "cpu", "imp:1")
    job = q.lease(conn, "cpu")
    q.checkpoint(conn, job["id"], {"processed": {"a.mp4": "vid_x"}})

    # fresh heartbeat -> untouched
    res = q.recover_stale(conn, ttl_s=90)
    assert res == {"requeued": [], "failed": []}

    with db.tx(conn):
        conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
                     (db.iso_at(time.time() - 300), job["id"]))
    res = q.recover_stale(conn, ttl_s=90)
    assert res["requeued"] == [job["id"]]
    row = q.get_job(conn, job["id"])
    assert row["state"] == states.QUEUED
    assert row["attempts"] == 1  # counted at lease, kept across requeue
    assert q.checkpoint_of(row) == {"processed": {"a.mp4": "vid_x"}}


def test_recover_stale_fails_after_max_attempts(conn):
    job = q.enqueue(conn, "import", {}, "cpu", "imp:2", max_attempts=1)
    job = q.lease(conn, "cpu")
    with db.tx(conn):
        conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
                     (db.iso_at(time.time() - 300), job["id"]))
    res = q.recover_stale(conn, ttl_s=90)
    assert res["failed"] == [job["id"]]
    row = q.get_job(conn, job["id"])
    assert row["state"] == states.FAILED
    assert "stale heartbeat" in row["error"]


def test_cancel_queued_is_immediate(conn):
    job = q.enqueue(conn, "probe", {}, "cpu", "c:1")
    row = q.cancel(conn, job["id"])
    assert row["state"] == states.CANCELLED
    assert row["finished_at"] is not None


def test_cancel_running_is_cooperative_flag(conn):
    q.enqueue(conn, "probe", {}, "cpu", "c:2")
    job = q.lease(conn, "cpu")
    row = q.cancel(conn, job["id"])
    assert row["state"] == states.RUNNING  # worker finalizes at next heartbeat
    assert row["cancel_requested"] == 1
    hb = q.heartbeat(conn, job["id"], progress=0.5)
    assert hb["cancel_requested"] == 1
    q.finalize_cancel(conn, job["id"])
    assert q.get_job(conn, job["id"])["state"] == states.CANCELLED


def test_retry_resets_terminal_job(conn):
    q.enqueue(conn, "probe", {}, "cpu", "r:1")
    job = q.lease(conn, "cpu")
    q.fail(conn, job["id"], "boom")
    row = q.retry(conn, job["id"])
    assert row["state"] == states.QUEUED
    assert row["error"] is None
    assert row["attempts"] == 0
    assert row["finished_at"] is None


def test_retry_refuses_active_job(conn):
    job = q.enqueue(conn, "probe", {}, "cpu", "r:2")
    try:
        q.retry(conn, job["id"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_heartbeat_updates_progress(conn):
    q.enqueue(conn, "probe", {}, "cpu", "h:1")
    job = q.lease(conn, "cpu")
    row = q.heartbeat(conn, job["id"], progress=0.25, msg="working")
    assert row["progress"] == 0.25
    assert row["progress_msg"] == "working"
