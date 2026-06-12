"""video-labeler FastAPI service (M0: ingest + media + jobs).

Wires together (and nothing more — the logic lives in the modules):
  * db migrations + startup stale-job recovery   (videolabeler/db.py, jobqueue/queue.py)
  * JobRunner: per-lane subprocess job execution  (jobqueue/runner.py, cpu=2 gpu=1)
  * REST routers                                  (videolabeler/api/{videos,media,jobs}.py)
  * idle WAL checkpoint loop

Endpoints:
  GET  /healthz   {ok, db, jobs_running, gpu_free_gb, disk_free_gb, wal_size,
                   gpu_exclusive, uptime_s}
  POST /api/video-labeler/import/manual   {batch_name?} -> {job}
  GET/DELETE /api/video-labeler/videos[/{id}]
  GET  /api/video-labeler/videos/{id}/stream?original=0   (single-range 206)
  GET  /api/video-labeler/videos/{id}/sprite[/{n}]
  GET  /api/video-labeler/jobs?state=&type=  ·  POST /jobs/{id}/cancel|/retry

ENV (defaults): DATA_DIR=/data PORT=8099 MIN_FREE_VRAM_GB=6 (unused in M0)
  INBOX_DIR={DATA_DIR}/inbox
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from videolabeler import db as vldb
from videolabeler.api import jobs as jobs_api
from videolabeler.api import media as media_api
from videolabeler.api import videos as videos_api
from videolabeler.config import Config
from videolabeler.jobqueue import queue as jobq
from videolabeler.jobqueue.runner import JobRunner

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("videolabeler.main")

WAL_CHECKPOINT_IDLE_S = 60.0

cfg = Config()
runner = JobRunner(cfg)
started = time.time()
_bg_tasks: list[asyncio.Task] = []


def _gpu_free_gb():
    """NVML free VRAM; None on hosts without a GPU/driver (graceful)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return round(pynvml.nvmlDeviceGetMemoryInfo(h).free / 2**30, 1)
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


async def _wal_checkpoint_loop() -> None:
    """TRUNCATE-checkpoint the WAL when no job is running, so the wal file
    cannot grow without bound across long import/encode sessions."""
    while True:
        await asyncio.sleep(WAL_CHECKPOINT_IDLE_S)
        try:
            with vldb.open_db(cfg.db_path) as conn:
                if jobq.running_count(conn) == 0:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            log.exception("wal checkpoint pass failed")


async def _startup() -> None:
    cfg.ensure_dirs()
    with vldb.open_db(cfg.db_path) as conn:
        applied = vldb.apply_migrations(conn)
        if applied:
            log.info("applied migrations: %s", applied)
        recovered = jobq.recover_stale(conn)
        if recovered["requeued"] or recovered["failed"]:
            log.warning("startup job recovery: %s", recovered)
    runner.start()
    _bg_tasks.append(asyncio.create_task(_wal_checkpoint_loop(), name="wal-checkpoint"))
    log.info("video-labeler up: DATA_DIR=%s port=%d", cfg.data_dir, cfg.port)


async def _shutdown() -> None:
    await runner.stop()
    for t in _bg_tasks:
        t.cancel()
    for t in _bg_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(title="video-labeler", version="0.1.0", lifespan=lifespan)
# the Home app's webview calls these endpoints cross-origin; without CORS
# headers every fetch() dies as "Failed to fetch"
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(videos_api.build_router(cfg))
app.include_router(media_api.build_router(cfg))
app.include_router(jobs_api.build_router(cfg))


@app.get("/healthz")
async def healthz():
    db_ok = True
    jobs_running = 0
    try:
        with vldb.open_db(cfg.db_path) as conn:
            jobs_running = jobq.running_count(conn)
    except Exception:
        db_ok = False
    try:
        disk_free_gb = round(shutil.disk_usage(cfg.data_dir).free / 2**30, 1)
    except OSError:
        disk_free_gb = 0.0
    return {
        "ok": db_ok,
        "db": db_ok,
        "jobs_running": jobs_running,
        "gpu_free_gb": _gpu_free_gb(),
        "disk_free_gb": disk_free_gb,
        "wal_size": vldb.wal_size(cfg.db_path),
        "gpu_exclusive": False,  # M2 wires the eviction/deadman machinery
        "uptime_s": round(time.time() - started, 1),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=cfg.port)
