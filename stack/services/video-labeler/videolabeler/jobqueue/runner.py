"""Asyncio job runner living in the API process.

Per-lane concurrency (cpu=2, gpu=1); every job executes as a SUBPROCESS
(`python -m videolabeler.jobs.run --job-id X`) for crash isolation and, later,
full VRAM reclaim. The runner only watches exit codes — success/failure is
finalized by the worker itself; if a worker dies without finalizing (state
still 'running'), the job is requeued while attempts < max_attempts, else
failed. Stale-heartbeat recovery also runs here periodically so a runner
restart cannot strand 'running' rows.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from pathlib import Path

from .. import db
from . import queue as q
from . import states

log = logging.getLogger("videolabeler.runner")

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LANES = {states.LANE_CPU: 2, states.LANE_GPU: 1}
RECOVER_EVERY_S = 30.0


class JobRunner:
    def __init__(self, cfg, lanes: dict | None = None, poll_s: float = 1.0):
        self.cfg = cfg
        self.lanes = dict(lanes or DEFAULT_LANES)
        self.poll_s = poll_s
        self._running: dict[str, int] = {lane: 0 for lane in self.lanes}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_recover = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="job-runner")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _loop(self) -> None:
        log.info("job runner started (lanes=%s)", self.lanes)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("runner iteration failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)

    def _tick(self) -> None:
        with db.open_db(self.cfg.db_path) as conn:
            now = time.time()
            if now - self._last_recover > RECOVER_EVERY_S:
                self._last_recover = now
                res = q.recover_stale(conn)
                if res["requeued"] or res["failed"]:
                    log.warning("stale recovery: requeued=%s failed=%s",
                                res["requeued"], res["failed"])
            for lane, cap in self.lanes.items():
                while self._running[lane] < cap:
                    job = q.lease(conn, lane)
                    if job is None:
                        break
                    self._running[lane] += 1
                    log.info("leased %s (%s, lane=%s, attempt %d/%d)",
                             job["id"], job["type"], lane,
                             job["attempts"], job["max_attempts"])
                    asyncio.create_task(
                        self._watch(job["id"], lane),
                        name=f"job-{job['id']}")

    async def _watch(self, job_id: str, lane: str) -> None:
        try:
            env = dict(os.environ)
            env["DATA_DIR"] = self.cfg.data_dir
            env["INBOX_DIR"] = self.cfg.inbox_dir
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "videolabeler.jobs.run", "--job-id", job_id,
                cwd=str(SERVICE_ROOT), env=env)
            rc = await proc.wait()
            with db.open_db(self.cfg.db_path) as conn:
                row = q.get_job(conn, job_id)
                if row is not None and row["state"] == states.RUNNING:
                    # worker crashed before finalizing
                    if row["attempts"] < row["max_attempts"]:
                        q.requeue(conn, job_id,
                                  reason=f"worker exited rc={rc} without finalizing")
                        log.warning("%s requeued after worker rc=%s", job_id, rc)
                    else:
                        q.fail(conn, job_id,
                               f"worker exited rc={rc} without finalizing"
                               " (attempts exhausted)")
                        log.error("%s failed after worker rc=%s", job_id, rc)
                elif row is not None:
                    log.info("%s finished: %s", job_id, row["state"])
        except Exception:
            log.exception("watcher for %s failed", job_id)
        finally:
            self._running[lane] -= 1
