"""Two PROCESSES hammer one db file concurrently (job heartbeats + video row
inserts). Pass criteria: no exceptions in either worker (exitcode 0) and every
row lands. This is the WAL + busy_timeout + BEGIN IMMEDIATE contract test."""
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videolabeler import db  # noqa: E402
from videolabeler.jobqueue import queue as q  # noqa: E402

ROUNDS = 50


def _writer(db_path: str, tag: str, job_id: str, rounds: int) -> None:
    # runs in a spawned process; sys.path propagates from the parent
    import sys as _sys
    root = str(Path(__file__).resolve().parent.parent)
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from videolabeler import db as vldb
    from videolabeler.jobqueue import queue as vq

    conn = vldb.connect(db_path)
    try:
        for i in range(rounds):
            vq.heartbeat(conn, job_id, progress=(i + 1) / rounds, msg=f"{tag} {i}")
            now = vldb.iso_now()
            with vldb.tx(conn):
                conn.execute(
                    "INSERT INTO videos (id, filename, checksum, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (f"vid_{tag}_{i:04d}", f"{tag}_{i}.mp4", f"{tag}{i:064d}", now, now))
    finally:
        conn.close()


def test_two_process_writers(conn, cfg):
    job_a = q.enqueue(conn, "import", {}, "cpu", "w:a")
    job_b = q.enqueue(conn, "import", {}, "cpu", "w:b")
    # same-millisecond created_at ties break by random id — lease both, any order
    leased = {q.lease(conn, "cpu")["id"], q.lease(conn, "cpu")["id"]}
    assert leased == {job_a["id"], job_b["id"]}

    mp = multiprocessing.get_context("spawn")
    procs = [
        mp.Process(target=_writer, args=(cfg.db_path, "a", job_a["id"], ROUNDS)),
        mp.Process(target=_writer, args=(cfg.db_path, "b", job_b["id"], ROUNDS)),
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert not p.is_alive(), "writer process hung"
        assert p.exitcode == 0, f"writer crashed with exitcode {p.exitcode}"

    n = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    assert n == 2 * ROUNDS
    for jid in (job_a["id"], job_b["id"]):
        row = q.get_job(conn, jid)
        assert row["heartbeat_at"] is not None
        assert row["progress"] == 1.0
