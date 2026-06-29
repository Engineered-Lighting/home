"""GPU eviction + deadman restore -- full state machine on fakes (no GPU).

The dangerous properties under test:
  * the ledger row exists BEFORE any eviction side effect (crash-safety);
  * restore-to-recorded-baseline: pinned models re-pin, transient models
    are left alone, protected containers are never evicted;
  * the deadman restores on deadline or holder-job completion;
  * startup recovery restores orphaned ledgers;
  * acquire failure restores immediately and surfaces InsufficientVRAM.
"""
from datetime import datetime, timedelta, timezone

import pytest

from videolabeler import db, gpuexclusive
from videolabeler.gpuexclusive import GpuExclusive, InsufficientVRAM
from videolabeler.jobqueue import queue as jq


class FakeOllama:
    def __init__(self, models):
        self.models = list(models)          # [{name, size_vram_gb, pinned}]
        self.unloaded = []
        self.repinned = []
        self.fail_unload = False

    def loaded_models(self):
        return list(self.models)

    def unload(self, model):
        if self.fail_unload:
            raise RuntimeError("ollama exploded")
        self.unloaded.append(model)
        self.models = [m for m in self.models if m["name"] != model]

    def repin(self, model):
        self.repinned.append(model)


class FakeDocker:
    def __init__(self, gpu_containers):
        self.gpu = list(gpu_containers)
        self.stopped = []
        self.started = []

    def running_gpu_containers(self):
        return list(self.gpu)

    def stop(self, name):
        self.stopped.append(name)
        self.gpu.remove(name)

    def start(self, name):
        self.started.append(name)


class FreeSeq:
    """free_gb() returning a scripted sequence (last value repeats)."""

    def __init__(self, *vals):
        self.vals = list(vals)

    def __call__(self):
        return self.vals.pop(0) if len(self.vals) > 1 else self.vals[0]


def make_gx(cfg, ollama, docker=None, free=FreeSeq(9.0, 90.0), now=None):
    return GpuExclusive(
        db_path=cfg.db_path, ollama=ollama, docker=docker, free_gb=free,
        sleep=lambda s: None,
        clock=(lambda: now) if now else (lambda: datetime.now(timezone.utc)),
    )


def active_rows(cfg):
    with db.open_db(cfg.db_path) as conn:
        return conn.execute("SELECT * FROM exclusive_state"
                            " WHERE state = 'active'").fetchall()


def test_acquire_noop_when_vram_already_free(cfg, conn):
    gx = make_gx(cfg, FakeOllama([]), free=FreeSeq(50.0))
    assert gx.acquire(min_free_gb=22, ttl_s=600) is None
    assert active_rows(cfg) == []


def test_acquire_evicts_and_ledger_precedes_side_effects(cfg, conn):
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    ol.fail_unload = True
    gx = make_gx(cfg, ol, free=FreeSeq(9.0))
    with pytest.raises(RuntimeError):
        gx.acquire(min_free_gb=22, ttl_s=600)
    # the crash happened AFTER the ledger write; acquire's error path then
    # restored it -> a 'restored' row with the unit recorded
    with db.open_db(cfg.db_path) as conn:
        rows = conn.execute("SELECT * FROM exclusive_state").fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "restored"
    assert "big:90b" in rows[0]["units_json"]


def test_acquire_success_records_pin_and_restore_repins(cfg, conn):
    ol = FakeOllama([
        {"name": "big:90b", "size_vram_gb": 90.3, "pinned": True},
        {"name": "small:7b", "size_vram_gb": 5.0, "pinned": False},
    ])
    gx = make_gx(cfg, ol, free=FreeSeq(9.0, 9.0, 95.0))
    ledger = gx.acquire(min_free_gb=22, ttl_s=600)
    assert ledger is not None
    assert ol.unloaded == ["big:90b", "small:7b"]
    assert len(active_rows(cfg)) == 1

    assert gx.restore(ledger) is True
    # baseline semantics: only the PINNED model is re-pinned
    assert ol.repinned == ["big:90b"]
    assert active_rows(cfg) == []
    assert gx.restore(ledger) is False  # idempotent


def test_protected_containers_never_evicted(cfg, conn):
    ol = FakeOllama([{"name": "m:1b", "size_vram_gb": 1.0, "pinned": False}])
    dk = FakeDocker(["hav-spatial-tracker", "hav-vllm"])
    gx = make_gx(cfg, ol, docker=dk, free=FreeSeq(9.0, 95.0))
    ledger = gx.acquire(min_free_gb=22, ttl_s=600, evict_containers=True)
    assert dk.stopped == ["hav-vllm"]
    gx.restore(ledger)
    assert dk.started == ["hav-vllm"]


def test_acquire_fails_honestly_when_nothing_evictable(cfg, conn):
    gx = make_gx(cfg, FakeOllama([]), free=FreeSeq(9.0))
    with pytest.raises(InsufficientVRAM):
        gx.acquire(min_free_gb=22, ttl_s=600)
    assert active_rows(cfg) == []


def test_acquire_restores_when_target_unreachable(cfg, conn):
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    gx = make_gx(cfg, ol, free=FreeSeq(9.0))  # never improves
    with pytest.raises(InsufficientVRAM):
        gx.acquire(min_free_gb=22, ttl_s=600)
    with db.open_db(cfg.db_path) as conn:
        rows = conn.execute("SELECT state FROM exclusive_state").fetchall()
    assert [r["state"] for r in rows] == ["restored"]
    assert ol.repinned == ["big:90b"]


def test_deadman_restores_past_deadline(cfg, conn):
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    t0 = datetime.now(timezone.utc)
    gx = make_gx(cfg, ol, free=FreeSeq(9.0, 95.0), now=t0)
    gx.acquire(min_free_gb=22, ttl_s=60)
    assert gx.supervisor_tick() == []            # before the deadline
    gx.clock = lambda: t0 + timedelta(seconds=61)
    restored = gx.supervisor_tick()
    assert len(restored) == 1
    assert ol.repinned == ["big:90b"]
    assert active_rows(cfg) == []


def test_deadman_restores_when_holder_job_finishes(cfg, conn):
    job = jq.enqueue(conn, "prelabel", {"video_id": "vid_x"}, "gpu",
                     "prelabel:test:deadman")
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    gx = make_gx(cfg, ol, free=FreeSeq(9.0, 95.0))
    gx.acquire(min_free_gb=22, ttl_s=3600, holder_job_id=job["id"])
    assert gx.supervisor_tick() == []            # job still queued
    with db.open_db(cfg.db_path) as conn:
        with db.tx(conn):
            conn.execute("UPDATE jobs SET state = 'succeeded' WHERE id = ?",
                         (job["id"],))
    assert len(gx.supervisor_tick()) == 1
    assert active_rows(cfg) == []


def test_startup_recovery_restores_orphans(cfg, conn):
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    gx = make_gx(cfg, ol, free=FreeSeq(9.0, 95.0))
    gx.acquire(min_free_gb=22, ttl_s=3600)
    # simulate a crash: new instance over the same db
    gx2 = make_gx(cfg, ol, free=FreeSeq(95.0))
    assert len(gx2.startup_recover()) == 1
    assert ol.repinned == ["big:90b"]
    assert active_rows(cfg) == []


def test_status_shape(cfg, conn):
    ol = FakeOllama([{"name": "big:90b", "size_vram_gb": 90.3, "pinned": True}])
    gx = make_gx(cfg, ol, free=FreeSeq(9.0, 95.0))
    assert gx.status() == {"active": False}
    ledger = gx.acquire(min_free_gb=22, ttl_s=600)
    st = gx.status()
    assert st["active"] is True and st["ledger_id"] == ledger
    assert st["units"] == ["big:90b"]

