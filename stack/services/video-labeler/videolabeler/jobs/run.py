"""Worker entrypoint: `python -m videolabeler.jobs.run --job-id X`.

The runner leases the job (state='running') BEFORE spawning this process.
Here: open an own db connection, dispatch by type, heartbeat every <=15s via
a side thread (own connection — sqlite conns are not shared across threads),
honor cooperative cancel, finalize succeeded/cancelled/failed. Exit code 0
whenever the job was finalized; non-zero means "crashed without finalizing"
and the runner requeues/fails it.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import threading
import traceback

from .. import db
from ..config import Config
from ..jobqueue import queue as q
from ..jobqueue import states

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("videolabeler.jobs")

HEARTBEAT_PERIOD_S = 10.0


class JobCancelled(Exception):
    """Raised inside job code when cancel_requested is observed."""


class HeartbeatThread(threading.Thread):
    """Bumps heartbeat_at every HEARTBEAT_PERIOD_S even while the job is deep
    in ffmpeg/sha work, and surfaces cancel_requested via cancel_event."""

    def __init__(self, db_path: str, job_id: str, cancel_event: threading.Event):
        super().__init__(name=f"hb-{job_id}", daemon=True)
        self.db_path = db_path
        self.job_id = job_id
        self.cancel_event = cancel_event
        self._stopped = threading.Event()

    def stop(self) -> None:
        self._stopped.set()

    def run(self) -> None:
        conn = db.connect(self.db_path)
        try:
            while not self._stopped.wait(HEARTBEAT_PERIOD_S):
                try:
                    row = conn.execute(
                        "SELECT state, cancel_requested FROM jobs WHERE id = ?",
                        (self.job_id,)).fetchone()
                    if row is None or row["state"] != states.RUNNING:
                        self.cancel_event.set()
                        return
                    if row["cancel_requested"]:
                        self.cancel_event.set()
                    with db.tx(conn):
                        conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
                                     (db.iso_now(), self.job_id))
                except sqlite3.Error:
                    log.exception("heartbeat write failed (will retry)")
        finally:
            conn.close()


class JobContext:
    """Passed to job modules: heartbeat/checkpoint/cancel + db/cfg access."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config, job_id: str,
                 cancel_event: threading.Event | None = None):
        self.conn = conn
        self.cfg = cfg
        self.job_id = job_id
        self.cancel_event = cancel_event or threading.Event()

    def heartbeat(self, progress: float | None = None, msg: str | None = None) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()
        row = q.heartbeat(self.conn, self.job_id, progress, msg)
        if row is not None and row["cancel_requested"]:
            self.cancel_event.set()
            raise JobCancelled()

    def checkpoint(self, data: dict) -> None:
        q.checkpoint(self.conn, self.job_id, data)
        if self.cancel_event.is_set():
            raise JobCancelled()

    def load_checkpoint(self) -> dict:
        row = q.get_job(self.conn, self.job_id)
        return q.checkpoint_of(row) if row is not None else {}

    def get_video(self, video_id: str):
        return self.conn.execute("SELECT * FROM videos WHERE id = ?",
                                 (video_id,)).fetchone()


def _handlers() -> dict:
    # imported lazily so a syntax error in one job module fails ITS jobs only
    from . import import_manual, perceive, prelabel, probe, proxy, sprite, windows
    return {
        "import": import_manual.run,
        "probe": probe.run,
        "proxy": proxy.run,
        "sprite": sprite.run,
        "windows": windows.run,
        "perceive": perceive.run,
        "prelabel": prelabel.run,
    }


def execute(conn: sqlite3.Connection, cfg: Config, job_id: str,
            cancel_event: threading.Event | None = None) -> str:
    """Dispatch + finalize one already-leased job; returns the final state.
    Tests call this inline (no runner, no heartbeat thread)."""
    job = q.get_job(conn, job_id)
    if job is None:
        raise SystemExit(f"job {job_id} not found")
    if job["state"] != states.RUNNING:
        log.warning("job %s is %s, expected running; skipping", job_id, job["state"])
        return job["state"]
    handler = _handlers().get(job["type"])
    ctx = JobContext(conn, cfg, job_id, cancel_event)
    if handler is None:
        q.fail(conn, job_id, f"unknown job type: {job['type']}")
        return states.FAILED
    try:
        handler(job, ctx)
        q.finish(conn, job_id)
        return states.SUCCEEDED
    except JobCancelled:
        q.finalize_cancel(conn, job_id)
        log.info("job %s cancelled cooperatively", job_id)
        return states.CANCELLED
    except Exception:
        tb = traceback.format_exc()
        q.fail(conn, job_id, tb[-3500:])
        log.exception("job %s failed", job_id)
        return states.FAILED


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)

    cfg = Config()
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    cancel_event = threading.Event()
    hb = HeartbeatThread(cfg.db_path, args.job_id, cancel_event)
    hb.start()
    try:
        state = execute(conn, cfg, args.job_id, cancel_event)
        log.info("job %s -> %s", args.job_id, state)
        return 0
    finally:
        hb.stop()
        hb.join(timeout=5)
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
