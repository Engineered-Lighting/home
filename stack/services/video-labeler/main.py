"""video-labeler FastAPI service (M0 ingest + media + jobs; M1 labels;
M2 perception + VLM prelabel).

Wires together (and nothing more — the logic lives in the modules):
  * db migrations + startup stale-job recovery   (videolabeler/db.py, jobqueue/queue.py)
  * JobRunner: per-lane subprocess job execution  (jobqueue/runner.py, cpu=2 gpu=1)
  * REST routers (videolabeler/api/{videos,media,jobs,labels,custom_labels,
                  prelabel}.py)
  * idle WAL checkpoint loop

Endpoints:
  GET  /healthz   {ok, db, jobs_running, gpu_free_gb, disk_free_gb, wal_size,
                   gpu_exclusive, uptime_s}
  POST /api/video-labeler/import/manual   {batch_name?} -> {job}
  GET/DELETE /api/video-labeler/videos[/{id}]
  GET  /api/video-labeler/videos/{id}/stream?original=0   (single-range 206)
  GET  /api/video-labeler/videos/{id}/sprite[/{n}]
  GET  /api/video-labeler/jobs?state=&type=  ·  POST /jobs/{id}/cancel|/retry
  GET  /api/video-labeler/labels/ontology
  GET/PUT /api/video-labeler/videos/{id}/labels   (revision concurrency, 409 snapshot)
  POST /api/video-labeler/videos/{id}/segments/{segId}/review   {action}
  GET/POST /api/video-labeler/custom-labels  ·  PATCH /custom-labels/{slug}
       ·  POST /custom-labels/{slug}/hide|/merge|/promote
  POST /api/video-labeler/prelabel   {video_ids?|all_pending}
  GET  /api/video-labeler/calibration?axis=
  GET  /api/video-labeler/videos/{id}/windows/{window_id}/keyframes[/{name}]

ENV (defaults): DATA_DIR=/data PORT=8099 MIN_FREE_VRAM_GB=6 (unused in M0)
  INBOX_DIR={DATA_DIR}/inbox
  VLM_BASE_URL=http://127.0.0.1:11434/v1 VLM_MODEL=qwen2.5vl:32b VLM_API_KEY=
  VLM_TIMEOUT_S=180 VLM_KEEP_ALIVE=10m
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
from videolabeler import gpuexclusive
from videolabeler.api import custom_labels as custom_labels_api
from videolabeler.api import jobs as jobs_api
from videolabeler.api import labels as labels_api
from videolabeler.api import media as media_api
from videolabeler.api import prelabel as prelabel_api
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

# GPU eviction + deadman (videolabeler/gpuexclusive.py). The supervisor
# lives HERE in the always-up API process — never in job subprocesses —
# so a SIGKILLed batch can't strand the voice stack or an evicted model.
gx = gpuexclusive.GpuExclusive(
    db_path=str(cfg.db_path),
    ollama=gpuexclusive.OllamaTransport(
        os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")),
    docker=(gpuexclusive.DockerTransport()
            if os.path.exists("/var/run/docker.sock") else None),
)


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


async def _gpu_supervisor_loop() -> None:
    """Deadman: restore evicted GPU units when their ledger deadline passes
    or the holder job finishes (gpuexclusive.supervisor_tick)."""
    while True:
        await asyncio.sleep(gpuexclusive.SUPERVISOR_PERIOD_S)
        try:
            restored = await asyncio.to_thread(gx.supervisor_tick)
            if restored:
                log.warning("gpu deadman restored ledgers: %s", restored)
        except Exception:
            log.exception("gpu supervisor tick failed")


async def _startup() -> None:
    cfg.ensure_dirs()
    with vldb.open_db(cfg.db_path) as conn:
        applied = vldb.apply_migrations(conn)
        if applied:
            log.info("applied migrations: %s", applied)
        recovered = jobq.recover_stale(conn)
        if recovered["requeued"] or recovered["failed"]:
            log.warning("startup job recovery: %s", recovered)
    try:
        orphans = await asyncio.to_thread(gx.startup_recover)
        if orphans:
            log.warning("gpu-exclusive orphans restored at startup: %s", orphans)
    except Exception:
        log.exception("gpu-exclusive startup recovery failed")
    runner.start()
    _bg_tasks.append(asyncio.create_task(_wal_checkpoint_loop(), name="wal-checkpoint"))
    _bg_tasks.append(asyncio.create_task(_gpu_supervisor_loop(), name="gpu-deadman"))
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
app.include_router(labels_api.build_router(cfg))
app.include_router(custom_labels_api.build_router(cfg))
app.include_router(prelabel_api.build_router(cfg))


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
    try:
        gpu_excl = gx.status()
    except Exception:
        gpu_excl = {"active": False}
    return {
        "ok": db_ok,
        "db": db_ok,
        "jobs_running": jobs_running,
        "gpu_free_gb": _gpu_free_gb(),
        "disk_free_gb": disk_free_gb,
        "wal_size": vldb.wal_size(cfg.db_path),
        "gpu_exclusive": gpu_excl,
        "uptime_s": round(time.time() - started, 1),
    }


@app.get("/api/video-labeler/gpu/survey")
async def gpu_survey():
    return await asyncio.to_thread(gx.survey)


@app.post("/api/video-labeler/gpu/exclusive")
async def gpu_exclusive_acquire(body: dict):
    """Manually open a GPU-eviction window (batch ops). Body:
    {min_free_gb, ttl_s, holder_job_id?, evict_containers?}. The deadman
    force-restores at the TTL regardless of what happens to the caller."""
    try:
        ledger = await asyncio.to_thread(
            gx.acquire,
            float(body.get("min_free_gb", 22)),
            float(body.get("ttl_s", 3600)),
            body.get("holder_job_id"),
            bool(body.get("evict_containers", False)),
        )
    except gpuexclusive.InsufficientVRAM as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "ledger_id": ledger, "status": gx.status()}


@app.post("/api/video-labeler/gpu/restore")
async def gpu_restore(body: dict | None = None):
    ledger_id = (body or {}).get("ledger_id")
    if ledger_id is None:
        st = gx.status()
        if not st.get("active"):
            return {"ok": True, "restored": []}
        ledger_id = st["ledger_id"]
    restored = await asyncio.to_thread(gx.restore, int(ledger_id))
    return {"ok": True, "restored": [ledger_id] if restored else []}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=cfg.port)
