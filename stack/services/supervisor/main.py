"""HAV stack-supervisor — HTTP control plane for the AI stack.

Wraps the existing ``scripts/stack.sh`` (up/down/restart) and the per-service
health probes so the Home app can show status, stream logs, and start/stop
the stack from the UI without SSHing.

Current API surface: read-only ``/healthz``, ``/api/stack/status``,
``/api/stack/tasks``, and ``/api/stack/logs/stream``; mutating stack routes
``/api/stack/start``, ``/api/stack/restart``, ``/api/stack/stop``, and
``/api/stack/free-gpu``; and per-service ``logs``/``restart``/``stop`` routes.
The auth / rate-limit / audit-log / reconcile-on-startup plumbing applies to
the mutating verbs as well as the status endpoints.

Trust model
-----------
The trust boundary is the HTTP API surface, NOT the Unix UID. ``docker``-
group membership is root-equivalent on Linux (any socket-writer can spawn
``--privileged --pid=host -v /:/host`` and escape). Protections:

  1. ``BIND_ADDR`` pins to Tailscale or LAN, never ``0.0.0.0``.
  2. ``STACK_TOKEN`` bearer on every non-``/healthz`` endpoint.
  3. Auth-failure rate limit (5 / min / IP → 60-s lockout).
  4. Verb allowlist (enum-only; no user-supplied strings ever reach a shell).

The supervisor REFUSES TO START if ``STACK_TOKEN`` is unset/empty or
``BIND_ADDR`` is missing / ``0.0.0.0``. See :func:`validate_config`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# ─────────────────────────────────────────────────────────────────────
# Configuration (env, validated at startup)
# ─────────────────────────────────────────────────────────────────────
STACK_TOKEN = os.environ.get("STACK_TOKEN", "").strip()
BIND_ADDR = os.environ.get("BIND_ADDR", "").strip()
EXPECTED_VLLM_MODEL = os.environ.get("EXPECTED_VLLM_MODEL", "qwen3-vl-30b")
STACK_SH = os.environ.get("STACK_SH", "/opt/home/stack/scripts/stack.sh")
AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "/var/log/hav-supervisor.log")

# Per-service probe map. Ports are host-published by docker-compose (see
# stack/docker-compose.yml), so the supervisor — running on the host, not
# in a container — can hit localhost directly.
#
# vLLM probe: /v1/models (NOT /health). Recent vllm-openai:latest builds
# return 404 for /health; /v1/models returns 200 once a model is loaded,
# which is a stronger readiness signal anyway (and matches what
# `_vllm_model_loaded` already uses for the warming→ready gate, so the
# two checks stay consistent). stack.sh:smoke_test() still uses /health —
# worth fixing it too on the next pass, but the supervisor doesn't need
# to wait.
SERVICES: list[dict] = [
    {"name": "vllm",             "container": "hav-vllm",             "probe_kind": "http", "probe": "http://localhost:8000/v1/models"},
    {"name": "wyoming-parakeet", "container": "hav-wyoming-parakeet", "probe_kind": "tcp",  "probe": ("localhost", 10300)},
    {"name": "kokoro",           "container": "hav-kokoro-tts",       "probe_kind": "http", "probe": "http://localhost:8880/v1/models"},
    {"name": "chatterbox-tts",    "container": "hav-chatterbox-tts",   "probe_kind": "http", "probe": "http://localhost:8881/v1/audio/voices"},
    {"name": "wyoming-kokoro",   "container": "hav-wyoming-kokoro",   "probe_kind": "tcp",  "probe": ("localhost", 10301)},
    {"name": "vision-sidecar",   "container": "hav-vision-sidecar",   "probe_kind": "http", "probe": "http://localhost:8091/healthz"},
    {"name": "metrics-sidecar",  "container": "hav-metrics-sidecar",  "probe_kind": "http", "probe": "http://localhost:8092/healthz"},
    {"name": "intelligence",     "container": "hav-intelligence",     "probe_kind": "http", "probe": "http://localhost:8095/healthz"},
]

SERVICE_ACTIONS: dict[str, dict] = {
    "vllm": {
        "compose": "vllm",
        "container": "hav-vllm",
        "confirm": "vllm",
        "gpu_heavy": True,
    },
    "wyoming-parakeet": {
        "compose": "wyoming-parakeet",
        "container": "hav-wyoming-parakeet",
        "confirm": "wyoming-parakeet",
    },
    "kokoro": {
        "compose": "kokoro-tts",
        "container": "hav-kokoro-tts",
        "confirm": "kokoro",
    },
    "chatterbox-tts": {
        "compose": "chatterbox-tts",
        "container": "hav-chatterbox-tts",
        "confirm": "chatterbox-tts",
    },
    "wyoming-kokoro": {
        "compose": "wyoming-kokoro",
        "container": "hav-wyoming-kokoro",
        "confirm": "wyoming-kokoro",
    },
    "vision-sidecar": {
        "compose": "vision-sidecar",
        "container": "hav-vision-sidecar",
        "confirm": "vision-sidecar",
    },
    "metrics-sidecar": {
        "compose": "metrics-sidecar",
        "container": "hav-metrics-sidecar",
        "confirm": "metrics-sidecar",
    },
    "intelligence": {
        "compose": "intelligence",
        "container": "hav-intelligence",
        "confirm": "intelligence",
    },
}

SERVICE_ALIASES = {
    "chatterbox": "chatterbox-tts",
    "kokoro-tts": "kokoro",
    "home-intelligence": "intelligence",
    "parakeet": "wyoming-parakeet",
    "vision": "vision-sidecar",
    "metrics": "metrics-sidecar",
}


# ─────────────────────────────────────────────────────────────────────
# Logging — app log to stderr (journald-captured); audit log to file.
# ─────────────────────────────────────────────────────────────────────
log = logging.getLogger("hav-supervisor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

audit = logging.getLogger("hav-supervisor.audit")
audit.propagate = False  # never double-log audit lines to stderr


def _setup_audit_log() -> None:
    """Configure the rotating audit-log file.

    Failures here are non-fatal — the rest of the supervisor still works,
    just without audit persistence. install.sh should chown/chmod the path
    so the supervisor's user can write it.
    """
    try:
        handler = logging.handlers.TimedRotatingFileHandler(
            AUDIT_LOG_PATH, when="W0", backupCount=8, utc=True
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)
        log.info("audit log: %s", AUDIT_LOG_PATH)
    except (PermissionError, FileNotFoundError, OSError) as e:
        log.warning(
            "audit log unavailable at %s (%s); state changes will not be persisted",
            AUDIT_LOG_PATH, e,
        )


def write_audit(verb: str, caller_ip: str, task_id: str, exit_code: Optional[int]) -> None:
    """Append one structured audit line. Phase 2/3 calls this on every mutation."""
    audit.info(
        "%s verb=%s caller_ip=%s task_id=%s exit_code=%s",
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        verb, caller_ip, task_id,
        exit_code if exit_code is not None else "-",
    )


def _stack_dir() -> str:
    """Directory where docker-compose commands should run."""
    return str(Path(STACK_SH).expanduser().resolve().parent.parent)


def _resolve_service(service: str) -> dict:
    key = SERVICE_ALIASES.get(service, service)
    cfg = SERVICE_ACTIONS.get(key)
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown service",
                "service": service,
                "allowed": sorted(SERVICE_ACTIONS.keys()),
            },
        )
    return {"name": key, **cfg}


async def _run_compose(args: list[str], timeout_s: float = 30.0) -> subprocess.CompletedProcess:
    """Run a docker compose command with fixed args from our allowlist."""
    return await asyncio.to_thread(
        subprocess.run,
        ["docker", "compose", *args],
        cwd=_stack_dir(),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _http_error_from_completed(proc: subprocess.CompletedProcess, action: str) -> None:
    if proc.returncode == 0:
        return
    detail = (proc.stderr or proc.stdout or "").strip()
    raise HTTPException(
        status_code=500,
        detail={
            "error": f"{action} failed",
            "exit_code": proc.returncode,
            "detail": detail[-1000:],
        },
    )


def _tail_file(path: str, n: int) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except FileNotFoundError:
        return []
    except OSError as exc:
        return [f"[supervisor] unable to read {path}: {exc}"]


# ─────────────────────────────────────────────────────────────────────
# Startup config validation — refuse to run if mis-configured.
# ─────────────────────────────────────────────────────────────────────
def validate_config() -> None:
    if not STACK_TOKEN:
        log.error("STACK_TOKEN unset; refusing to expose un-authed control plane")
        raise SystemExit(2)
    if len(STACK_TOKEN) < 16:
        log.error("STACK_TOKEN too short (<16 chars); refusing for brute-force safety")
        raise SystemExit(2)
    if not BIND_ADDR:
        log.error("BIND_ADDR unset; refuse to default to 0.0.0.0 — set to your Tailscale or LAN IP")
        raise SystemExit(2)
    if BIND_ADDR in ("0.0.0.0", "::", "::0"):
        log.error("BIND_ADDR=%s exposes supervisor to all interfaces; refusing", BIND_ADDR)
        raise SystemExit(2)
    if not os.path.exists(STACK_SH):
        log.warning(
            "STACK_SH=%s not found; /api/stack/status will still work, mutations (Phase 2+) will fail",
            STACK_SH,
        )
    log.info(
        "config OK — BIND_ADDR=%s STACK_SH=%s EXPECTED_VLLM_MODEL=%s",
        BIND_ADDR, STACK_SH, EXPECTED_VLLM_MODEL,
    )


# ─────────────────────────────────────────────────────────────────────
# Auth + rate-limit
# ─────────────────────────────────────────────────────────────────────
# Per-IP failure window. Each IP gets a deque of failure timestamps;
# evict entries older than _FAIL_WINDOW_S on each check. ≥ _FAIL_THRESHOLD
# in the window → lock that IP for _LOCKOUT_S.
_auth_failures: dict[str, deque] = defaultdict(deque)
_locked_until: dict[str, float] = {}
_FAIL_WINDOW_S = 60.0
_FAIL_THRESHOLD = 5
_LOCKOUT_S = 60.0


def _client_ip(request: Request) -> str:
    # No X-Forwarded-For trust: supervisor is only reached directly from
    # the LAN / Tailscale interface. Anything else is suspect.
    return request.client.host if request.client else "?"


def _register_fail(ip: str, now: float) -> None:
    q = _auth_failures[ip]
    q.append(now)
    while q and now - q[0] > _FAIL_WINDOW_S:
        q.popleft()
    if len(q) >= _FAIL_THRESHOLD:
        _locked_until[ip] = now + _LOCKOUT_S
        log.warning(
            "auth lockout: ip=%s for %.0fs after %d failures in %.0fs window",
            ip, _LOCKOUT_S, len(q), _FAIL_WINDOW_S,
        )


def check_auth(request: Request) -> None:
    """Raise an appropriate HTTPException on auth fail / lockout."""
    ip = _client_ip(request)
    now = time.monotonic()

    locked_at = _locked_until.get(ip)
    if locked_at is not None:
        if now < locked_at:
            raise HTTPException(status_code=429, detail="rate-limited; try again later")
        # lockout expired — clear it
        _locked_until.pop(ip, None)
        _auth_failures.pop(ip, None)

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        _register_fail(ip, now)
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header[len("Bearer "):].strip()
    if token != STACK_TOKEN:
        _register_fail(ip, now)
        raise HTTPException(status_code=401, detail="bad token")


# ─────────────────────────────────────────────────────────────────────
# Container state + per-service probes (status aggregator)
# ─────────────────────────────────────────────────────────────────────
async def _docker_inspect(container: str) -> tuple[str, str]:
    """Return (container_state, health_state) for the given container.

    container_state ∈ {"running", "starting", "exited", "missing"}
    health_state    ∈ {"healthy", "starting", "unhealthy", "none"}
    """
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "inspect", "--format", "{{json .State}}", container],
            capture_output=True, text=True, timeout=4,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.debug("docker inspect %s failed: %s", container, e)
        return "missing", "none"
    if result.returncode != 0:
        # Container does not exist (docker daemon would have returned the
        # state for a stopped-but-existing one).
        return "missing", "none"
    try:
        st = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "missing", "none"

    raw = st.get("Status", "missing")  # running, exited, created, paused, dead, restarting
    cont = {
        "running": "running",
        "restarting": "starting",
        "created": "starting",
        "exited": "exited",
        "dead": "exited",
        "paused": "exited",
        "removing": "exited",
    }.get(raw, "missing")

    health = (st.get("Health") or {}).get("Status", "none")
    if health not in ("healthy", "starting", "unhealthy"):
        health = "none"
    return cont, health


async def _probe_http(url: str) -> tuple[str, Optional[str]]:
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get(url)
        if 200 <= r.status_code < 300:
            return "ok", None
        return "fail", f"http {r.status_code}"
    except Exception as e:
        return "fail", type(e).__name__


async def _probe_tcp(host: str, port: int) -> tuple[str, Optional[str]]:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=2.0
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "ok", None
    except Exception as e:
        return "fail", type(e).__name__


async def _vllm_model_loaded() -> Optional[str]:
    """The model id reported by vLLM's /v1/models, or None if not loaded.

    This is the "warm" gate: until vLLM reports a model, the engine is
    still cold-loading weights into VRAM. Used by the aggregator to
    distinguish ``warming`` vs ``ready``.
    """
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get("http://localhost:8000/v1/models")
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        if not data:
            return None
        return data[0].get("id")
    except Exception:
        return None


async def aggregate_status() -> dict:
    """Snapshot of overall + per-service state. Idempotent, cheap (~2s)."""
    container_results = await asyncio.gather(
        *(_docker_inspect(s["container"]) for s in SERVICES),
    )
    probe_results = await asyncio.gather(
        *(
            _probe_http(s["probe"]) if s["probe_kind"] == "http"
            else _probe_tcp(*s["probe"])  # type: ignore[arg-type]
            for s in SERVICES
        ),
    )
    vllm_model = await _vllm_model_loaded()

    out_services: list[dict] = []
    any_running = False
    all_running = True
    all_probes_ok = True
    for s, (cont, hlth), (probe, detail) in zip(SERVICES, container_results, probe_results):
        out_services.append({
            "name": s["name"],
            "container": cont,
            "health": hlth,
            "probe": probe,
            "probe_kind": s["probe_kind"],
            "detail": detail,
        })
        if cont == "running":
            any_running = True
        else:
            all_running = False
        if probe != "ok":
            all_probes_ok = False

    inflight = _current_inflight()
    if inflight:
        overall = "starting" if inflight["verb"] in ("start", "restart") else "stopping"
    elif not any_running:
        overall = "offline"
    elif all_running and all_probes_ok and vllm_model == EXPECTED_VLLM_MODEL:
        overall = "ready"
    elif all_running and all_probes_ok:
        overall = "warming"
    else:
        overall = "partial"

    return {
        "overall": overall,
        "services": out_services,
        "in_flight": inflight,
        "last_action": _last_action,
        "vllm_model_loaded": vllm_model,
        "expected_vllm_model": EXPECTED_VLLM_MODEL,
    }


# ─────────────────────────────────────────────────────────────────────
# Log redaction — mask token-shaped strings before they reach the SSE
# log stream. stack.sh itself does NOT echo .env values, but if vLLM /
# the bridge / a future verb ever does, we want defense-in-depth.
# ─────────────────────────────────────────────────────────────────────
_REDACT_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{30,}"),                # Hugging Face tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT (HA tokens)
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),               # any 40+ char hex (incl. STACK_TOKEN if echoed)
)


def _redact(s: str) -> str:
    for p in _REDACT_PATTERNS:
        s = p.sub("***REDACTED***", s)
    return s


# ─────────────────────────────────────────────────────────────────────
# Task tracking (in-memory; recovered-on-startup via pgrep).
#
# Each Task carries the subprocess handle + a bounded log buffer + a
# set of SSE subscriber queues. Multiple browsers can tail the same
# task's logs concurrently. Buffers are bounded so memory stays flat
# even on a long stack.sh run.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class Task:
    task_id: str
    verb: str  # "start" | "stop" | "restart"
    started_at: str
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    recovered: bool = False
    # Runtime state (not part of the JSON summary):
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=200))
    subscribers: set = field(default_factory=set)  # set of asyncio.Queue
    process: Optional[asyncio.subprocess.Process] = None


_tasks: deque = deque(maxlen=20)  # type: deque[Task]
_last_action: Optional[dict] = None


def _task_summary(t: Task) -> dict:
    """Subset of Task that's JSON-serializable (no asyncio.Queue / Process)."""
    return {
        "task_id": t.task_id,
        "verb": t.verb,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
        "exit_code": t.exit_code,
        "recovered": t.recovered,
    }


def _current_inflight() -> Optional[dict]:
    for t in reversed(_tasks):
        if t.ended_at is None:
            entry = {"verb": t.verb, "started_at": t.started_at, "task_id": t.task_id}
            if t.recovered:
                entry["recovered"] = True
            return entry
    return None


def _broadcast(t: Task, payload: dict) -> None:
    """Push to every live subscriber, dropping any whose queue overflows."""
    dead = []
    for q in list(t.subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        t.subscribers.discard(q)


async def _run_stack_verb(verb: str, caller_ip: str) -> Task:
    """Spawn ``stack.sh <verb>`` and stream its output into a new Task.

    Returns immediately after registering the task and starting the
    background runner. The HTTP layer returns 202 + ``task_id`` so the
    client can attach to ``/api/stack/logs/stream`` for live output.
    """
    sh_verb = {"start": "up", "stop": "down", "restart": "restart"}[verb]
    task_id = f"t-{uuid.uuid4().hex[:8]}"
    t = Task(
        task_id=task_id,
        verb=verb,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    _tasks.append(t)
    # Audit the start immediately (exit_code unknown yet).
    write_audit(verb, caller_ip, task_id, None)
    log.info("task %s verb=%s caller_ip=%s start", task_id, verb, caller_ip)

    async def runner() -> None:
        global _last_action
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash", STACK_SH, sh_verb,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            t.process = proc
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = _redact(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                t.log_buffer.append(line)
                _broadcast(t, {"line": line})
            t.exit_code = await proc.wait()
        except FileNotFoundError as e:
            line = f"[supervisor] failed to spawn stack.sh: {e}"
            t.log_buffer.append(line)
            _broadcast(t, {"line": line})
            t.exit_code = 255
        except Exception as e:
            line = f"[supervisor] runner error: {type(e).__name__}: {e}"
            t.log_buffer.append(line)
            _broadcast(t, {"line": line})
            t.exit_code = 254
        finally:
            t.ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            # Final audit row, this time with the exit_code.
            write_audit(verb, caller_ip, task_id, t.exit_code)
            _last_action = {
                "verb": verb,
                "ended_at": t.ended_at,
                "exit_code": t.exit_code,
            }
            log.info("task %s done exit_code=%s", task_id, t.exit_code)
            # Tell live subscribers the stream is closing.
            _broadcast(t, {"done": True, "exit_code": t.exit_code})

    asyncio.create_task(runner())
    return t


async def reconcile_on_startup() -> None:
    """If a previous supervisor instance had a ``stack.sh`` subprocess in
    flight when it died, the docker-compose work still finishes (the
    subprocess detaches), but the in-memory task buffer is gone.

    On startup we ``pgrep`` for any in-flight ``stack.sh`` and register a
    placeholder "recovered" task. The UI then sees ``starting`` (with no
    log history) instead of a misleading ``ready`` until containers
    finish coming up. The /api/stack/logs/stream endpoint for a recovered
    task just backfills the empty buffer and waits for completion-or-
    timeout — there's no subprocess handle to reattach to.
    """
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["pgrep", "-af", r"stack\.sh\s+(up|down|restart)"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.info("reconcile: pgrep unavailable; skipping")
        return
    if result.returncode != 0 or not result.stdout.strip():
        log.info("reconcile: no in-flight stack.sh found")
        return
    line = result.stdout.strip().splitlines()[0]
    m = re.search(r"stack\.sh\s+(up|down|restart)", line)
    raw_verb = m.group(1) if m else "up"
    verb = {"up": "start", "down": "stop", "restart": "restart"}.get(raw_verb, "start")
    t = Task(
        task_id=f"recov-{uuid.uuid4().hex[:8]}",
        verb=verb,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        recovered=True,
    )
    _tasks.append(t)
    log.info(
        "reconcile: recovered in-flight task task_id=%s verb=%s line=%r",
        t.task_id, t.verb, line,
    )


# ─────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="hav-stack-supervisor", version="0.1.0-phase1")

# CORS: the Tauri webview's effective origin is "tauri://localhost" (or
# similar) which is not a typical http origin. Keep it permissive — the
# real protection is the bearer token. The browser still won't send the
# bearer cross-origin without our own code asking it to.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-confirm"],
)


@app.on_event("startup")
async def _startup() -> None:
    validate_config()
    _setup_audit_log()
    await reconcile_on_startup()
    log.info("hav-stack-supervisor ready, listening on %s:%s",
             BIND_ADDR, os.environ.get("PORT", "8093"))


@app.get("/healthz")
def healthz() -> dict:
    """Unauthenticated liveness probe — for the Home app's 15-s poller.

    Intentionally returns no internal state. Anyone on the bound interface
    can hit this; the bound interface itself is the trust boundary.
    """
    return {"ok": True}


@app.get("/api/stack/status")
async def get_status(request: Request) -> dict:
    check_auth(request)
    return await aggregate_status()


@app.get("/api/stack/tasks")
async def get_tasks(request: Request) -> dict:
    check_auth(request)
    return {"tasks": [_task_summary(t) for t in reversed(_tasks)]}


# ─────────────────────────────────────────────────────────────────────
# Phase 2 — mutating verbs (start, restart) + SSE log streaming.
# ``stop`` lands in Phase 3 (needs an extra X-Confirm header + UI modal).
# ─────────────────────────────────────────────────────────────────────
def _conflict_or_none() -> Optional[dict]:
    """If a task is in flight, return the 409 body; else None.

    The 409 body carries the existing task_id so the client can attach
    to its log stream gracefully instead of failing.
    """
    inflight = _current_inflight()
    if inflight:
        return {"error": "in_flight", "task_id": inflight["task_id"], "verb": inflight["verb"]}
    return None


@app.post("/api/stack/start", status_code=202)
async def post_start(request: Request) -> dict:
    check_auth(request)
    conflict = _conflict_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    t = await _run_stack_verb("start", _client_ip(request))
    return {"task_id": t.task_id, "verb": "start"}


@app.post("/api/stack/restart", status_code=202)
async def post_restart(request: Request) -> dict:
    check_auth(request)
    conflict = _conflict_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)
    t = await _run_stack_verb("restart", _client_ip(request))
    return {"task_id": t.task_id, "verb": "restart"}


@app.get("/api/stack/logs/stream")
async def get_logs_stream(request: Request, task_id: Optional[str] = None) -> StreamingResponse:
    """SSE stream for a task's stack.sh output.

    Frames look like:
        data: {"line": "==> Bringing up the stack..."}\\n\\n
    Terminal frame on completion:
        event: done\\ndata: {"exit_code": 0}\\n\\n
    Heartbeats every 20 s as `: ping\\n\\n` so middleboxes don't drop
    idle connections during long boots.

    EventSource in the browser can't send custom headers, so the Home
    app uses fetch() + ReadableStream — see home-sse-fetch.js.
    """
    check_auth(request)

    # Resolve target task. If task_id omitted, default to the current
    # in-flight task. If neither exists, 404 (no point streaming nothing).
    target: Optional[Task] = None
    if task_id:
        for t in _tasks:
            if t.task_id == task_id:
                target = t
                break
        if target is None:
            raise HTTPException(status_code=404, detail="task not found")
    else:
        for t in reversed(_tasks):
            if t.ended_at is None:
                target = t
                break
        if target is None:
            raise HTTPException(status_code=404, detail="no in-flight task")

    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    target.subscribers.add(q)
    captured_target = target  # close over a non-None reference for the generator

    async def gen():
        try:
            yield b": connected\n\n"
            # Backfill the bounded buffer first so a late-attaching
            # client doesn't miss earlier lines.
            for past in list(captured_target.log_buffer):
                yield f"data: {json.dumps({'line': past})}\n\n".encode()
            # If the task already finished by the time we got here,
            # send the terminal frame and exit cleanly.
            if captured_target.ended_at is not None:
                yield f"event: done\ndata: {json.dumps({'exit_code': captured_target.exit_code})}\n\n".encode()
                return
            # Live stream until terminal frame or client disconnects.
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(q.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if item.get("done"):
                    yield f"event: done\ndata: {json.dumps({'exit_code': item.get('exit_code')})}\n\n".encode()
                    return
                yield f"data: {json.dumps(item)}\n\n".encode()
        finally:
            captured_target.subscribers.discard(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 3 — POST /api/stack/stop. Two extra guards on top of the usual
# bearer-token + 409-conflict semantics:
#   1. Require an explicit `X-Confirm: stop-ai-stack` header. The UI
#      sets this only after the user clears a confirm modal, so a
#      stray POST (e.g. a misconfigured automation) can't accidentally
#      kill the stack.
#   2. Rate-limit: at most one stop attempt per _STOP_COOLDOWN_S window.
#      Both rapid double-clicks AND repeated-bug retries are blocked.
# ─────────────────────────────────────────────────────────────────────
_STOP_COOLDOWN_S = 10.0
_last_stop_ts: float = 0.0  # monotonic clock; 0 = never attempted


@app.post("/api/stack/stop", status_code=202)
async def post_stop(request: Request) -> dict:
    global _last_stop_ts
    check_auth(request)

    # Confirmation header — UI must include this, set only by the
    # AiStackCard's "Stop" modal confirm-click.
    if request.headers.get("x-confirm", "") != "stop-ai-stack":
        raise HTTPException(
            status_code=400,
            detail={"error": "missing or wrong X-Confirm header — expected 'stop-ai-stack'"},
        )

    # Rate limit: 1 stop per _STOP_COOLDOWN_S. Returns 429 with a
    # retry_after_s hint so the client can show a cooldown badge.
    now = time.monotonic()
    if _last_stop_ts and (now - _last_stop_ts) < _STOP_COOLDOWN_S:
        retry = int(_STOP_COOLDOWN_S - (now - _last_stop_ts) + 0.5)
        raise HTTPException(
            status_code=429,
            detail={"error": "stop rate-limited", "retry_after_s": retry},
        )

    # Conflict check — if a start/restart is already running, return 409
    # with that task_id so the client can attach to the existing stream
    # rather than queue a stop behind it.
    conflict = _conflict_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail=conflict)

    _last_stop_ts = now
    t = await _run_stack_verb("stop", _client_ip(request))
    return {"task_id": t.task_id, "verb": "stop"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — allowlisted service operations for the Lab diag pane.
# These back the Home app's per-service logs / stop / restart buttons and the
# global "free gpu" action. All service names resolve through SERVICE_ACTIONS;
# no caller-provided strings ever become shell input.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/services/{service}/logs")
async def get_service_logs(request: Request, service: str, n: int = 50) -> dict:
    check_auth(request)
    n = max(1, min(int(n or 50), 500))
    if service == "supervisor":
        return {
            "service": "supervisor",
            "source": "audit_log",
            "lines": _tail_file(AUDIT_LOG_PATH, n),
        }

    cfg = _resolve_service(service)
    proc = await _run_compose(
        ["logs", "--tail", str(n), cfg["compose"]],
        timeout_s=10.0,
    )
    _http_error_from_completed(proc, "logs")
    return {
        "service": cfg["name"],
        "compose_service": cfg["compose"],
        "lines": (proc.stdout or "").splitlines()[-n:],
    }


@app.post("/api/services/{service}/restart", status_code=202)
async def post_service_restart(request: Request, service: str) -> dict:
    check_auth(request)
    cfg = _resolve_service(service)
    proc = await _run_compose(["restart", cfg["compose"]], timeout_s=45.0)
    write_audit(f"restart:{cfg['name']}", _client_ip(request), f"svc-{uuid.uuid4().hex[:8]}", proc.returncode)
    _http_error_from_completed(proc, "restart")
    return {
        "ok": True,
        "service": cfg["name"],
        "compose_service": cfg["compose"],
        "verb": "restart",
        "exit_code": proc.returncode,
    }


@app.post("/api/services/{service}/stop", status_code=202)
async def post_service_stop(request: Request, service: str) -> dict:
    check_auth(request)
    cfg = _resolve_service(service)
    expected = f"stop-{cfg['confirm']}"
    if request.headers.get("x-confirm", "") != expected:
        raise HTTPException(
            status_code=400,
            detail={"error": f"missing or wrong X-Confirm header — expected '{expected}'"},
        )
    proc = await _run_compose(["stop", cfg["compose"]], timeout_s=45.0)
    write_audit(f"stop:{cfg['name']}", _client_ip(request), f"svc-{uuid.uuid4().hex[:8]}", proc.returncode)
    _http_error_from_completed(proc, "stop")
    return {
        "ok": True,
        "service": cfg["name"],
        "compose_service": cfg["compose"],
        "verb": "stop",
        "exit_code": proc.returncode,
    }


@app.post("/api/stack/free-gpu", status_code=202)
@app.post("/api/stack/free_gpu", status_code=202)
async def post_free_gpu(request: Request) -> dict:
    check_auth(request)
    if request.headers.get("x-confirm", "") != "free-gpu":
        raise HTTPException(
            status_code=400,
            detail={"error": "missing or wrong X-Confirm header — expected 'free-gpu'"},
        )
    gpu_services = [svc for svc in SERVICE_ACTIONS.values() if svc.get("gpu_heavy")]
    compose_services = [svc["compose"] for svc in gpu_services]
    proc = await _run_compose(["stop", *compose_services], timeout_s=45.0)
    write_audit("free-gpu", _client_ip(request), f"svc-{uuid.uuid4().hex[:8]}", proc.returncode)
    _http_error_from_completed(proc, "free-gpu")
    return {
        "ok": True,
        "verb": "free-gpu",
        "services": ["vllm"],
        "compose_services": compose_services,
        "exit_code": proc.returncode,
    }
