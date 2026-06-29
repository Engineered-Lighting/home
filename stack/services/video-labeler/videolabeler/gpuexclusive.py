"""GPU eviction + deadman restore (the plan's "GPU-exclusive mode").

The RTX 6000 is shared: Ollama (native, loopback API), occasional GPU
containers, and unknown native holders (e.g. stagec's pose feeder, which
must NEVER be touched). Batch jobs (VLM prelabel, embed, train) need tens
of GB that may be pinned by an idle model, so this module:

  1. SURVEYS holders at runtime — Ollama via its API, containers via
     docker.sock. NVML pids cannot be named from inside the container
     (different pid namespace), so anything NVML sees that we cannot
     attribute is left strictly alone and merely subtracted from "free".
  2. Writes an ``exclusive_state`` ledger row BEFORE evicting anything —
     a crash between ledger and eviction restores a no-op; a crash after
     eviction restores the recorded units. finally-blocks are NOT the
     mechanism (SIGKILL skips them); the always-up API process runs the
     deadman: restore when the deadline passes or the holder job ends,
     and restore anything 'active' at startup.
  3. RESTORES to the recorded baseline only: models that were pinned
     (keep_alive forever) are re-pinned; transient models are left for
     on-demand reload; only containers WE stopped are started. Nothing
     the user had down is ever resurrected.

Transports (Ollama HTTP, docker.sock, NVML free-memory) are injected so
the whole state machine is unit-testable on a GPU-less box.
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import db

log = logging.getLogger("videolabeler.gpuexclusive")

# A model whose expiry sits absurdly far out is "pinned" (Ollama renders
# keep_alive=-1 as a year-2318 expires_at). 10 years is a safe threshold.
PIN_HORIZON_DAYS = 3650
EVICT_POLL_S = 3.0
EVICT_TIMEOUT_S = 180.0
SUPERVISOR_PERIOD_S = 30.0


class InsufficientVRAM(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---- default transports -----------------------------------------------------

def nvml_free_gb() -> Optional[float]:
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(h)
            return info.free / 1e9
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None


class OllamaTransport:
    """Minimal Ollama API client (native daemon on the host; the service
    runs with --network host so 127.0.0.1:11434 is reachable)."""

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def _req(self, method: str, path: str, payload: Optional[dict] = None,
             timeout: float = 30.0):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        return json.loads(body) if body else {}

    def loaded_models(self) -> list[dict]:
        try:
            doc = self._req("GET", "/api/ps")
        except (urllib.error.URLError, OSError):
            return []  # Ollama not running -> nothing to evict
        out = []
        for m in doc.get("models") or []:
            pinned = False
            exp = m.get("expires_at")
            if exp:
                try:
                    horizon = datetime.now(timezone.utc) + timedelta(days=PIN_HORIZON_DAYS)
                    pinned = _parse_iso(exp) > horizon
                except ValueError:
                    pinned = False
            out.append({"name": m["name"],
                        "size_vram_gb": round((m.get("size_vram") or 0) / 1e9, 1),
                        "pinned": pinned})
        return out

    def unload(self, model: str) -> None:
        # keep_alive 0 asks the daemon to drop the model immediately.
        self._req("POST", "/api/generate",
                  {"model": model, "keep_alive": 0}, timeout=120.0)

    def repin(self, model: str) -> None:
        # keep_alive -1 reloads AND pins — restoring the user's baseline.
        # The reload of a huge model takes minutes; fire with a long timeout
        # in the supervisor thread, tolerate timeouts (load continues server-side).
        try:
            self._req("POST", "/api/generate",
                      {"model": model, "keep_alive": -1}, timeout=600.0)
        except (urllib.error.URLError, OSError, TimeoutError):
            log.warning("repin of %s timed out client-side (load likely "
                        "continuing server-side)", model)


class DockerTransport:
    """Tiny HTTP-over-unix-socket client for the two verbs we need."""

    def __init__(self, sock_path: str = "/var/run/docker.sock"):
        self.sock_path = sock_path

    def _req(self, method: str, path: str) -> tuple[int, bytes]:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(30.0)
        try:
            s.connect(self.sock_path)
            s.sendall(f"{method} {path} HTTP/1.0\r\nHost: docker\r\n"
                      f"Content-Length: 0\r\n\r\n".encode())
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        finally:
            s.close()
        head, _, body = buf.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        return status, body

    def running_gpu_containers(self) -> list[str]:
        status, body = self._req("GET", "/containers/json")
        if status != 200:
            return []
        names = []
        for c in json.loads(body):
            name = (c.get("Names") or ["?"])[0].lstrip("/")
            status_, detail = self._req("GET", f"/containers/{name}/json")
            if status_ != 200:
                continue
            info = json.loads(detail)
            reqs = (((info.get("HostConfig") or {}).get("DeviceRequests")) or [])
            if any("gpu" in (cap or []) for r in reqs
                   for cap in (r.get("Capabilities") or [[]])):
                names.append(name)
        return names

    def stop(self, name: str) -> None:
        status, _ = self._req("POST", f"/containers/{name}/stop?t=30")
        if status not in (204, 304):
            raise RuntimeError(f"docker stop {name} -> HTTP {status}")

    def start(self, name: str) -> None:
        status, _ = self._req("POST", f"/containers/{name}/start")
        if status not in (204, 304):
            raise RuntimeError(f"docker start {name} -> HTTP {status}")


# ---- the state machine -------------------------------------------------------

@dataclass
class GpuExclusive:
    db_path: str
    ollama: OllamaTransport
    docker: Optional[DockerTransport] = None
    free_gb: Callable[[], Optional[float]] = nvml_free_gb
    protected_containers: tuple = ("hav-video-labeler", "hav-spatial-tracker")
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def survey(self) -> dict:
        return {
            "gpu_free_gb": self.free_gb(),
            "ollama_models": self.ollama.loaded_models(),
            "gpu_containers": (self.docker.running_gpu_containers()
                               if self.docker else []),
        }

    # -- acquire ---------------------------------------------------------------
    def acquire(self, min_free_gb: float, ttl_s: float,
                holder_job_id: Optional[str] = None,
                evict_containers: bool = False) -> Optional[int]:
        """Free at least ``min_free_gb`` by evicting recorded units.

        Returns the ledger id (None when nothing had to be evicted).
        Raises InsufficientVRAM — after restoring — when eviction cannot
        reach the target (unknown native holders are never touched).
        """
        free = self.free_gb()
        if free is not None and free >= min_free_gb:
            return None

        units: list[dict] = []
        for m in self.ollama.loaded_models():
            units.append({"kind": "ollama_model", "name": m["name"],
                          "repin": bool(m["pinned"])})
        if evict_containers and self.docker:
            for name in self.docker.running_gpu_containers():
                if name not in self.protected_containers:
                    units.append({"kind": "container", "name": name})
        if not units:
            raise InsufficientVRAM(
                f"free={free} gb < {min_free_gb} gb and no evictable units "
                f"(unknown native holders are never touched)")

        deadline = (self.clock() + timedelta(seconds=ttl_s)).isoformat(
            timespec="milliseconds")
        with db.open_db(self.db_path) as conn:
            with db.tx(conn):
                cur = conn.execute(
                    "INSERT INTO exclusive_state (holder_job_id, units_json,"
                    " deadline_at, state, created_at) VALUES (?, ?, ?, 'active', ?)",
                    (holder_job_id, json.dumps(units), deadline, _iso_now()))
                ledger_id = cur.lastrowid
        log.info("gpu-exclusive %d: evicting %s (deadline %s)",
                 ledger_id, [u["name"] for u in units], deadline)

        try:
            for u in units:
                if u["kind"] == "ollama_model":
                    self.ollama.unload(u["name"])
                elif u["kind"] == "container":
                    self.docker.stop(u["name"])
            waited = 0.0
            while waited <= EVICT_TIMEOUT_S:
                free = self.free_gb()
                if free is None or free >= min_free_gb:
                    log.info("gpu-exclusive %d: free=%.1f gb", ledger_id, free or -1)
                    return ledger_id
                self.sleep(EVICT_POLL_S)
                waited += EVICT_POLL_S
            raise InsufficientVRAM(
                f"evicted {[u['name'] for u in units]} but free={free} gb "
                f"< {min_free_gb} gb after {EVICT_TIMEOUT_S:.0f}s")
        except Exception:
            self.restore(ledger_id)
            raise

    # -- restore ---------------------------------------------------------------
    def restore(self, ledger_id: int) -> bool:
        with db.open_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM exclusive_state WHERE id = ?",
                               (ledger_id,)).fetchone()
            if row is None or row["state"] != "active":
                return False
        units = json.loads(row["units_json"])
        for u in units:
            try:
                if u["kind"] == "ollama_model" and u.get("repin"):
                    log.info("gpu-exclusive %d: re-pinning %s", ledger_id, u["name"])
                    self.ollama.repin(u["name"])
                elif u["kind"] == "container":
                    log.info("gpu-exclusive %d: starting %s", ledger_id, u["name"])
                    self.docker.start(u["name"])
                # transient ollama models reload on demand: nothing to do
            except Exception:
                log.exception("gpu-exclusive %d: restore of %s failed "
                              "(continuing with the rest)", ledger_id, u.get("name"))
        with db.open_db(self.db_path) as conn:
            with db.tx(conn):
                conn.execute(
                    "UPDATE exclusive_state SET state = 'restored',"
                    " restored_at = ? WHERE id = ?", (_iso_now(), ledger_id))
        return True

    # -- deadman ----------------------------------------------------------------
    def supervisor_tick(self) -> list[int]:
        """Restore every active ledger whose deadline passed or whose holder
        job reached a terminal state. Returns restored ledger ids."""
        restored = []
        with db.open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, holder_job_id, deadline_at FROM exclusive_state"
                " WHERE state = 'active'").fetchall()
            now = self.clock()
            due = []
            for r in rows:
                past_deadline = _parse_iso(r["deadline_at"]) <= now
                holder_done = False
                if r["holder_job_id"]:
                    j = conn.execute("SELECT state FROM jobs WHERE id = ?",
                                     (r["holder_job_id"],)).fetchone()
                    holder_done = bool(
                        j and j["state"] in ("succeeded", "failed", "cancelled"))
                if past_deadline or holder_done:
                    due.append((r["id"], past_deadline))
        for ledger_id, was_deadline in due:
            if was_deadline:
                log.warning("gpu-exclusive %d: DEADLINE passed — force-restoring",
                            ledger_id)
            if self.restore(ledger_id):
                restored.append(ledger_id)
        return restored

    def startup_recover(self) -> list[int]:
        """Service (re)start: anything still 'active' was orphaned by a
        crash — restore it before accepting new work."""
        with db.open_db(self.db_path) as conn:
            rows = conn.execute("SELECT id FROM exclusive_state"
                                " WHERE state = 'active'").fetchall()
        out = []
        for r in rows:
            log.warning("gpu-exclusive %d: orphaned at startup — restoring", r["id"])
            if self.restore(r["id"]):
                out.append(r["id"])
        return out

    def status(self) -> dict:
        with db.open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, deadline_at, units_json FROM exclusive_state"
                " WHERE state = 'active' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return {"active": False}
        return {"active": True, "ledger_id": row["id"],
                "deadline_at": row["deadline_at"],
                "units": [u["name"] for u in json.loads(row["units_json"])]}
