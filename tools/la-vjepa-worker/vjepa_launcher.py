"""Fixed LA systemd launcher and loopback TLS transport for the V-JEPA worker.

The host adapter for an unchanged lifecycle.Lifecycle in its own supervisor
(vjepa_lifecycle_app, unit lab-vjepa-lifecycle.service on 192.168.0.100:9447).
It is deliberately separate from the detector's supervisor: Lifecycle admits
one live lease per ledger, and the V-JEPA lease must run beside the detector's,
so sharing one would either serialize the two workers or require changing the
reviewed single-worker lifecycle. Here the reviewed classes run unchanged with
a second ledger, a second fixed worker unit and a second loopback port.

Nothing here starts a model at import. start() runs the host preflight, then
exactly one systemd-run of the worker (probe venv interpreter, caps below);
the worker re-checks GPU admission itself immediately before loading weights.
The full kernel-journal fault scan runs once at supervisor start (warm_start),
so a lease's preflight runs only the incremental scan. Clips go to the worker
over one pooled keep-alive loopback connection, serialised behind a lock, and
are refused with the typed reason thermal_shed while the GPU is at or above
the V-JEPA rung of the thermal ladder.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shutil
import socket
import ssl
import subprocess
import threading
import time

import vjepa_pins as pins
from lifecycle import PreflightRejected

BASE = Path("/home/marcelo-lima/home-lab-vjepa")
TLS = BASE / "tls"
PRIVATE = BASE / "private"
UNIT = "lab-vjepa-worker.service"
PORT = 9448
READY = {"error": "request_unavailable_or_indeterminate", "profile_admitted": True}
# Mirrors start_live.DOCUMENTED_FAILED_UNITS and its fault baseline (tests pin the parity).
DOCUMENTED_FAILED_UNITS = frozenset({"observer-health.service", "observer-recover.service",
                                     "home-agent-preference-offhost-20260928.service",
                                     "home-agent-preference-offhost-20260928-r2.service"})
FAULT_BASELINE = "2026-09-26 23:07:00 UTC"
_FAULT_CLEAN = {"until": None}
_FAULT_LOCK = threading.Lock()  # the start-up scan and a lease's preflight never scan at once
EXECUTE = "/v1/specialist/execute"
FORWARD_TIMEOUT = 10
# Under the worker's 5 s keep-alive timeout (vjepa_worker Handler.timeout), so
# the pooled connection is replaced before the worker can close it.
IDLE_RECONNECT_SECONDS = 4.0
# The V-JEPA rung of the LA thermal ladder (plan S1.4): new clips are refused
# at or above this GPU temperature. Config key vjepa_refuse_gpu_c, set through
# the environment below; the launch preflight's > GPU_MAX_TEMPERATURE_C gate is unchanged.
REFUSE_GPU_C_ENV = "PERCEPTION_LAB_VJEPA_REFUSE_GPU_C"
REFUSE_GPU_C_DEFAULT = 74
THERMAL_CACHE_SECONDS = 5.0
FAULT_PATTERN = "invoked oom-killer|Out of memory: Killed|kernel panic|soft lockup|hard LOCKUP|Hardware Error|I/O error|segfault"
SESSION_STAGES = {"60": (115, 120), "120": (175, 180), "300": (355, 360)}  # seconds -> (lease, runtime)
GATES = {"host memory headroom failed": "preflight_memory", "gpu memory headroom failed": "preflight_vram",
         "gpu thermal headroom failed": "preflight_thermal", "new failed service": "preflight_failed_unit",
         "new fault or unavailable fault check": "preflight_kernel_fault", "staged source changed": "preflight_source"}


def run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout).stdout


class StaleConnection(Exception):
    """A connection failure that proves the worker did not take the request."""


def readable(sock):
    """True when a connection that should be idle has something to read: the
    worker closed it, or sent bytes nobody asked for. Either way it is not reused."""
    try:
        if isinstance(sock, ssl.SSLSocket) and sock.pending():
            return True
        return bool(select.select([sock], [], [], 0)[0])
    except (OSError, ValueError):
        return True


def stage(environ=os.environ):
    """(lease_seconds, runtime_seconds) for the operator commissioning stage."""
    value = environ.get("PERCEPTION_LAB_SESSION_SECONDS", "60")
    if value not in SESSION_STAGES:
        raise ValueError("Invalid operator commissioning stage")
    return SESSION_STAGES[value]


def refuse_gpu_c(environ=os.environ):
    """The configured V-JEPA thermal rung in whole degrees C (default 74)."""
    value = environ.get(REFUSE_GPU_C_ENV, str(REFUSE_GPU_C_DEFAULT))
    if not re.fullmatch(r"[0-9]{2}", value) or not 40 <= int(value) <= pins.GPU_MAX_TEMPERATURE_C:
        raise ValueError("Invalid V-JEPA thermal threshold")
    return int(value)


def gpu_reading(*, run=run, timeout=15):
    """(free MiB, temperature C) of the one GPU, from nvidia-smi."""
    free, temperature = (int(v) for v in run(["nvidia-smi", "--query-gpu=memory.free,temperature.gpu",
                                              "--format=csv,noheader,nounits"], timeout=timeout).strip().split(","))
    return free, temperature


def staged_source(base=BASE, digest=None):
    """The staged worker tree named by vjepa-source.json, verified; (directory, runtime_revision)."""
    digest = digest or (lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
    configured = json.loads((base / "vjepa-source.json").read_text())
    directory = (base / configured["directory"]).resolve()
    if not directory.is_relative_to(base.resolve()) or set(configured["files"]) != {"vjepa_pins.py", "vjepa_worker.py"}:
        raise ValueError("staged source changed")
    for name, expected in configured["files"].items():
        if digest(directory / name) != expected:
            raise ValueError("staged source changed")
    revision = hashlib.sha256(pins.canonical(configured["files"]).encode()).hexdigest()
    if configured.get("runtime_revision") != revision:
        raise ValueError("staged source changed")
    return directory, revision


def preflight(*, run=run, meminfo=None, hostname=socket.gethostname):
    """Host gates before any launch. Thresholds live in vjepa_pins."""
    if hostname() != "EngineeredLightingServer1":
        raise ValueError("LA host required")
    for line in run(["systemctl", "list-units", "--failed", "--plain", "--no-legend"]).splitlines():
        if line.split() and line.split()[0] not in DOCUMENTED_FAILED_UNITS:
            raise ValueError("new failed service")
    free, temperature = gpu_reading(run=run)
    if free < pins.GPU_MIN_FREE_MIB:
        raise ValueError("gpu memory headroom failed")
    if temperature > pins.GPU_MAX_TEMPERATURE_C:
        raise ValueError("gpu thermal headroom failed")
    text = meminfo() if meminfo else Path("/proc/meminfo").read_text()
    memory = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
    if int(memory["MemAvailable"].split()[0]) * 1024 < pins.HOST_MIN_AVAILABLE_BYTES:
        raise ValueError("host memory headroom failed")
    fault_scan(run=run)
    return free, temperature


def fault_scan(*, run=run):
    """The kernel-journal fault gate; raises ValueError unless proven clean."""
    with _FAULT_LOCK:
        try:
            # No sudo: the supervisor unit runs with NoNewPrivileges, under which
            # sudo refuses with exit 1 and empty stdout, indistinguishable from "no
            # match". The service user reads the kernel journal through group adm.
            # Incremental: the full window from the fixed baseline once per
            # supervisor process (slow at a 50% CPU quota: ~15 s live 2026-09-29;
            # run by warm_start, not inside the first lease), then only since
            # the last clean scan with a 5 s overlap.
            started = time.time()
            since = FAULT_BASELINE if _FAULT_CLEAN["until"] is None else "@%d" % int(_FAULT_CLEAN["until"] - 5)
            run(["journalctl", "-k", "--since", since, "--no-pager", "--grep", FAULT_PATTERN], timeout=45)
        except subprocess.CalledProcessError as error:
            # journalctl --grep exits 1 when nothing matches; anything on stderr
            # (no journal access, a refused elevation) is no proof of that.
            if (error.returncode != 1 or (error.stdout or "").strip() not in {"", "-- No entries --"}
                    or (error.stderr or "").strip()):
                raise ValueError("new fault or unavailable fault check") from None
            _FAULT_CLEAN["until"] = started
            return
        except Exception:
            raise ValueError("new fault or unavailable fault check") from None
        raise ValueError("new fault or unavailable fault check")


def warm_fault_scan(*, run=run):
    """The full baseline scan once at supervisor start. A fault or an unavailable
    check records nothing clean, so the first lease's preflight rescans from the
    baseline and refuses exactly as before."""
    try:
        fault_scan(run=run)
    except ValueError:
        return False
    return True


def worker_command(directory, config_path, runtime_seconds):
    """The only systemd-run this supervisor issues."""
    return ["systemd-run", "--user", "--unit=" + UNIT, "--collect",
            "--property=MemoryMax=" + pins.WORKER_MEMORY_MAX, "--property=MemorySwapMax=0", "--property=CPUQuota=100%",
            "--property=TasksMax=64", "--property=RuntimeMaxSec=" + str(runtime_seconds), "--property=Restart=no",
            "--property=TimeoutStopSec=5", "--property=KillMode=control-group", "--property=UMask=0077",
            "--property=NoNewPrivileges=yes", "--working-directory=" + str(directory),
            "--setenv=PERCEPTION_LAB_VJEPA_WORKER_CONFIG=" + str(config_path), "--setenv=CUDA_VISIBLE_DEVICES=0",
            "--setenv=OMP_NUM_THREADS=1", "--setenv=OPENBLAS_NUM_THREADS=1", "--setenv=MKL_NUM_THREADS=1",
            "--setenv=HF_HUB_OFFLINE=1", "--setenv=TRANSFORMERS_OFFLINE=1", "--setenv=PYTHONDONTWRITEBYTECODE=1",
            pins.PROBE_PYTHON, "-I", "-B", str(PurePosixPath(directory) / "vjepa_worker.py")]


class Launcher:
    def __init__(self, *, base=BASE, environ=os.environ):
        if socket.gethostname() != "EngineeredLightingServer1":
            raise ValueError("LA host required")
        self.base = base
        self.lease_seconds, self.runtime_seconds = stage(environ)
        self.refuse_gpu_c = refuse_gpu_c(environ)
        self.context = ssl.create_default_context(cafile=str(TLS / "ca.pem"))
        self.reset_pool()

    def reset_pool(self):
        # LifecycleHTTP allows 2 concurrent forwards; the worker serves one
        # connection at a time, so both share one pooled connection in turn.
        self.pool_lock, self.pool, self.pool_used = threading.Lock(), None, 0.0
        self.thermal_lock, self.thermal = threading.Lock(), None
        self.monotonic = time.monotonic

    def warm_start(self, *, run=run):
        """Supervisor start: the full baseline fault scan in the background, so
        the first lease's preflight runs the incremental scan (or waits for this
        one on the scan lock instead of starting its own)."""
        thread = threading.Thread(target=warm_fault_scan, kwargs={"run": run}, name="fault-scan", daemon=True)
        thread.start()
        return thread

    def unit(self):
        raw = run(["systemctl", "--user", "show", UNIT, "-p", "InvocationID", "-p", "ActiveState", "-p", "MainPID",
                   "-p", "ControlGroup"], timeout=5)
        return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

    @staticmethod
    def ended(value):
        if value.get("ActiveState") not in ("inactive", "failed") or value.get("MainPID") != "0":
            return False
        group = value.get("ControlGroup")
        if not group:
            return True
        events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
        return not events.exists() or "populated 0" in events.read_text().splitlines()

    def token(self):
        data = (PRIVATE / "execution.token").read_text().strip()
        if not 32 <= len(data) <= 512:
            raise ValueError("execution credential invalid")
        return data

    def start(self, epoch, expires, variant="detector"):
        # Lifecycle names its default variant 'detector'; this supervisor's only
        # worker is the V-JEPA clip worker, so any other variant is refused.
        if variant != "detector":
            raise PreflightRejected("variant not provisioned")
        if not self.ended(self.unit()):
            raise PreflightRejected("worker already active", reason="preflight_worker_active")
        try:
            preflight()
            directory, revision = staged_source(self.base)
        except Exception as error:
            gate = GATES.get(str(error)) if type(error) is ValueError else None
            raise PreflightRejected("health preflight rejected", reason=gate) from None
        if not re.fullmatch(r"la-live-[a-f0-9]{32}", epoch) or not time.time() < expires <= time.time() + self.runtime_seconds:
            raise ValueError("Bounded epoch required")
        profile = pins.profile(epoch, revision)
        config = {"schema_version": 1, "engine_epoch": epoch, "expires_at": expires, "runtime_seconds": self.runtime_seconds,
                  "maximum_requests": min(400, self.runtime_seconds), "profile": profile,
                  "token_sha256": hashlib.sha256(self.token().encode()).hexdigest()}
        configs = PRIVATE / "worker-configs"
        configs.mkdir(mode=0o700, exist_ok=True)
        path = configs / ("config-" + epoch + ".json")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(config, out)
            out.flush()
            os.fsync(out.fileno())
        run(worker_command(directory, path, self.runtime_seconds), timeout=45)
        value = self.unit()
        if value.get("ActiveState") not in ("active", "activating") or not value.get("InvocationID"):
            raise RuntimeError("launch not proven")
        return value["InvocationID"], profile

    def connect(self):
        client = http.client.HTTPSConnection("192.168.0.100", PORT, context=self.context, timeout=FORWARD_TIMEOUT)
        # Loopback only, while verifying the provisioned certificate's IP SAN.
        client._create_connection = lambda address, timeout, source_address=None: socket.create_connection(
            ("127.0.0.1", PORT), timeout)
        return client

    def thermal_refusal(self):
        """None, or the typed reason a new clip is refused: thermal_shed at or
        above the V-JEPA rung, thermal_unavailable when the GPU cannot be read.
        One nvidia-smi read per THERMAL_CACHE_SECONDS."""
        with self.thermal_lock:
            now = self.monotonic()
            if self.thermal is None or now - self.thermal[0] >= THERMAL_CACHE_SECONDS:
                try:
                    temperature = gpu_reading(run=run, timeout=3)[1]
                except Exception:
                    temperature = None
                self.thermal = (now, temperature)
            temperature = self.thermal[1]
        if temperature is None:
            return "thermal_unavailable"
        return "thermal_shed" if temperature >= self.refuse_gpu_c else None

    def forward(self, path, body):
        if path not in (EXECUTE, "/v1/specialist/lookup") or len(body) > 6 * 1024**2:
            raise ValueError("fixed worker routes only")
        if path == EXECUTE:
            # Refused here, before the worker sees the clip, in the worker's own
            # refusal shape: Victoria counts a typed reason as a skip.
            reason = self.thermal_refusal()
            if reason is not None:
                return 503, json.dumps({"reason": reason}).encode()
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.token()}
        if not self.pool_lock.acquire(timeout=FORWARD_TIMEOUT):
            # Provably never sent: a typed refusal, not an exception. A raised
            # TimeoutError is asyncio.TimeoutError on 3.11+, which LifecycleHTTP
            # would answer as a 408 body_timeout.
            return 429, json.dumps({"reason": "worker_busy"}).encode()
        try:
            client, self.pool = self.pool, None
            if client is not None and (client.sock is None or self.monotonic() - self.pool_used > IDLE_RECONNECT_SECONDS
                                       or readable(client.sock)):
                client.close()  # idle too long, or closed or written to by the worker: never sent on
                client = None
            if client is not None:
                try:
                    return self.exchange(client, path, body, headers)
                except StaleConnection:
                    pass  # the worker cannot have this request: send it once on a fresh connection
            try:
                return self.exchange(self.connect(), path, body, headers)
            except StaleConnection as error:
                raise error.__cause__ from None
        finally:
            self.pool_lock.release()

    def exchange(self, client, path, body, headers):
        """One request on client, pooled again only after a complete keep-alive
        response. StaleConnection means the worker provably did not get the whole
        request (the send failed), or, for the read-only lookup, that a reused
        connection failed before any response; neither is a worker fault."""
        reused = client.sock is not None
        try:
            try:
                client.request("POST", path, body, headers)
            except (OSError, http.client.HTTPException) as error:
                raise StaleConnection() from error
            try:
                response = client.getresponse()
            except (OSError, http.client.HTTPException) as error:
                if reused and path != EXECUTE:
                    raise StaleConnection() from error
                raise
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("worker response too large")
            if response.getheader("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("worker response type invalid")
        except BaseException:
            client.close()
            raise
        if response.isclosed() and not response.will_close and client.sock is not None:
            self.pool, self.pool_used = client, self.monotonic()
        else:
            client.close()  # the worker said Connection: close, or the body was not fully read
        return response.status, data

    def identity(self, invocation):
        value = self.unit()
        if self.ended(value):
            return "ended"
        if value.get("InvocationID") != invocation:
            return "unrelated"
        return "running" if value.get("ActiveState") == "active" else "starting"

    def absence(self, epoch):
        value = self.unit()
        return dict(unit=UNIT, active_state=value.get("ActiveState"), main_pid=value.get("MainPID"),
                    invocation_id=value.get("InvocationID") or None, worker_absent=self.ended(value),
                    config_present=(PRIVATE / "worker-configs" / ("config-" + epoch + ".json")).exists())

    def inspect(self, invocation, epoch):
        status = self.identity(invocation)
        if status in ("ended", "unrelated"):
            return status
        try:
            config = json.loads((PRIVATE / "worker-configs" / ("config-" + epoch + ".json")).read_text())
            reference = dict(site_id="victoria", camera_id="den", session_id="readiness", request_id="readiness",
                             body_sha256="0" * 64, profile=config["profile"])
            code, data = self.forward("/v1/specialist/lookup", json.dumps(reference).encode())
            value = json.loads(data)
            return "ready" if code == 409 and value == READY and value["profile_admitted"] is True else "starting"
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return "starting"

    def stop(self, invocation, epoch):
        value = self.unit()
        if self.ended(value):
            return
        if value.get("InvocationID") != invocation:
            raise RuntimeError("refusing unrelated worker stop")
        run(["systemctl", "--user", "stop", UNIT], timeout=15)
