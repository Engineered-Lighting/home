"""Victoria Lab live-scene consumer composed into the LA camera-pilot process.

Disabled by default: camera_pilot_service composes this only when
private/live-scene.json exists and says "enabled": true. Without that file the
camera pilot is composed exactly as before and this module is never imported.

Why this exists. lab_live_scene.compose_live_scene_gateway needs the common
AdmissionController/Gateway: a v3 resource manifest, an evidence verifier with
all five receipts, a dispatch-token authority, an input-token authority and an
engine attestor, all bound to a vLLM started by the guarded launcher. None of
that is deployed on the LA host. The running service is the owner-approved
limited camera pilot (camera_pilot.CameraPilot: its own SQLite ledger, one active
request, a 30 s cooldown, a stock `vllm serve` reached through `docker exec`).
This module adds the live consumer to that process instead:

- the envelope and body policy are exactly lab_live_scene's (validate_envelope,
  POLICY_SHA256 bound by the config), as are the typed refusals, the HTTP routes
  (LiveSceneRoutes) and the proof format (LabSavedActivity._proof), so the
  deployed Victoria pump talks to this consumer unchanged;
- rows live in a separate live_requests table in the pilot's own SQLite file,
  written through the pilot's connection under the pilot's lock. Camera accounting
  (one active camera request, 30 s cooldown) is untouched and never reads live rows;
- a live request is refused (typed, no ledger write) while any camera request is
  unresolved, while another live request is outstanding, while either consumer is
  closed or when the live ledger is full;
- execution uses the pilot's fixed docker-exec model path and preflight, and sends
  the exact bytes Victoria hashed; with the S1.1 warm path that is a long-lived
  `docker exec` worker of its own (model_worker.py), separate from camera chat's,
  opening a fresh loopback connection per request;
- a live timeout or unknown outcome closes the live consumer only; camera chat
  keeps its own accounting and failure semantics;
- the submit reply waits (outside the pilot lock) for its own worker, up to the
  pilot's reply wait, and carries the terminal proof when it is ready; polling
  by lookup stays the recovery path;
- the engine identity is private/model-pin.json (model_pin.py): live-scene.json
  must quote its engine_epoch and artifact_sha256, and after an accepted vLLM
  re-pin this consumer stays closed (engine_changed), across restarts too,
  until live-scene.json names the new epoch; camera chat keeps running;
- speed plan S1.4, before any ledger write (typed, nothing written): the
  thermal ladder from the cached health verdict (503 thermal_shed at GPU >=
  live_shed_gpu_c or Tctl >= live_shed_tctl_c, and at GPU >= live_stop_gpu_c),
  then the yield to vLLM's own queue (429 queue_busy while a request waits, at
  least yield_running_threshold run, or the counts are older than 1 s and
  cannot be refreshed on a healthy engine; 503 admission_closed when the
  health verdict failed or the engine check refused the live worker's start,
  as nothing could be sent); both read outside pilot.lock. Camera chat keeps
  only its > 80 C preflight guard;
- expired terminal live rows are deleted in bounded batches (PRUNE_BATCH_ROWS
  per admission), so pilot.lock is never held through one long delete;
  lab_live_prune.py prunes a large backlog offline before a restart.

Deviations from the common-admission design that need explicit owner sign-off:
- completion is application-level (a validated HTTP completion), not engine
  attested; no dispatch token or input-token authority binds the upstream bytes;
- a camera request admitted while a live request runs executes concurrently
  (vLLM --max-num-seqs 4): at most two model requests from this service at once;
- the S1.1 warm path (approved with the speed plan, 2026-10-08) keeps one
  long-lived interpreter per consumer inside the pinned vLLM container and a
  health verdict up to 2 s old; private/pilot-runtime.json restores the
  per-request exec and per-request checks;
- the S1.8 reconcile (approved 2026-10-08): when the long-lived live worker
  proves that an unanswered exchange ended (a reply frame: its model call
  returned; a worker exit is no proof), the indeterminate live row becomes terminal
  unknown_resolved with no answer, and the live consumer reopens when the
  health verdict is clean, at most live_reopens_per_hour (3) times per rolling
  hour. A failed verdict defers the reopen to a later submission, which
  retries it; the hourly limit or any other close reason keeps it closed with
  an alarm line. Heat never blocks the reopen: every live submission meets
  the thermal ladder first. vLLM's running count is never that evidence.
  Camera rows keep their own semantics.
"""
from __future__ import annotations

from collections import OrderedDict, deque, namedtuple
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable

from . import backend_timing
from . import lab_live_scene as scene_protocol
from .admission import AdmissionClosed, AdmissionError, RequestConflict, canonical_digest
from .camera_pilot import REPLY_MARGIN_SECONDS, BeforeDispatchRejected, CameraPilot, bounded_reply_wait
from .gateway import CredentialBinding
from .ingress import PrivateTLSIngress, _TLSHandler
from .lab_live_scene import (CONSUMER_ID, ENVELOPE_FIELDS, LIVE_CAMERA, LIVE_ROOM, MAX_DEADLINE_SECONDS,
                             MAX_OUTPUT_TOKENS, POLICY_SHA256, LiveSceneRefusal, LiveSceneRoutes, validate_envelope)
from .lab_saved_activity import _HEX64, _ID, LabSavedActivity, _identity, _lab_completion
from .model_pin import EngineIdentityMismatch, check_live_identity
from .model_worker import ModelWorker, WorkerExited, WorkerFailed, WorkerNotSent
from .openai_backend import BackendUnknown, _constant, _object
from .pilot_runtime import RuntimeOptions
from .service_config import protected_file

CONFIG_NAME = "live-scene.json"
LEDGER_NAME = "admission.sqlite"   # the camera pilot's ledger beside live-scene.json
CONFIG_FIELDS = frozenset({"version", "enabled", "client_certificate", "device_id", "configuration_revision",
                           "artifact_sha256", "engine_epoch", "policy_sha256", "deadline_seconds",
                           "min_execution_seconds", "max_input_tokens", "max_image_bytes", "max_output_tokens",
                           "max_records", "retention_days"})
MODEL_ALIAS = "qwen3-vl-30b"
MAX_IMAGE_BYTES = 256 * 1024
MAX_COMPLETION_BYTES = 128 * 1024
EXECUTION_GRACE_SECONDS = 3
MAX_CONSECUTIVE_FAILURES = 3
PRUNE_BATCH_ROWS = 500          # expired live rows deleted per admission, under pilot.lock
YIELD_METRICS_SECONDS = 1.0     # vLLM counts older than this are refreshed before an admission
HEALTH_LOG_SECONDS = 60.0       # a lasting failed-health refusal is logged again this often
LIVE_LINGER_SECONDS = 60.0      # how long the live worker is kept to learn whether an exchange ended
REOPEN_WINDOW_SECONDS = 3600.0  # the rolling hour of live_reopens_per_hour
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_Record = namedtuple("_Record", "request_id state outcome engine_epoch")
_GUARDS_PENDING = object()      # _submit: the S1.4 guards have not run for this request yet


class UpstreamFinished(RuntimeError):
    """The model transport process exited: the request is no longer running."""


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool
    client_certificate: str
    device_id: str
    configuration_revision: str
    artifact_sha256: str
    engine_epoch: str
    deadline_seconds: float
    min_execution_seconds: float
    max_input_tokens: int
    max_image_bytes: int
    max_output_tokens: int
    max_records: int
    retention_days: int
    protocol: str = "lab-live-scene-1"


def live_protocol(name: str):
    """Fixed opt-in protocols; a config never names importable Python code."""
    if name == "lab-live-scene-1":
        return scene_protocol
    if name == "lab-live-person-scene-1":
        from . import lab_live_person_scene
        return lab_live_person_scene
    raise ValueError("unsupported live-scene protocol")


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def load_live_config(path: str | Path, *, enforce_linux: bool = False) -> LiveConfig | None:
    """Exact provisioning; None when the file is absent or "enabled" is false."""
    config = read_live_config(path, enforce_linux=enforce_linux)
    return config if config is not None and config.enabled else None


def read_live_config(path: str | Path, *, enforce_linux: bool = False) -> LiveConfig | None:
    """Exact provisioning, enabled or not (the offline prune reads its
    retention either way); None only when the file is absent."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    path = protected_file(path, enforce_linux=enforce_linux)
    with path.open("rb") as source:
        raw = source.read(16385)
    if len(raw) > 16384:
        raise ValueError("live-scene provisioning exceeds limit")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or set(value) not in (CONFIG_FIELDS, CONFIG_FIELDS | {"protocol"}):
        raise ValueError("live-scene provisioning fields do not match the versioned schema")
    protocol_name = value.get("protocol", "lab-live-scene-1")
    protocol = live_protocol(protocol_name)
    if type(value["version"]) is not int or value["version"] != 1 or type(value["enabled"]) is not bool:
        raise ValueError("unsupported live-scene provisioning")
    if not isinstance(value["client_certificate"], str) or not _NAME.fullmatch(value["client_certificate"]):
        raise ValueError("live client certificate must be a file name beside the provisioning")
    for key in ("device_id", "configuration_revision", "engine_epoch"):
        if not isinstance(value[key], str) or not _ID.fullmatch(value[key]):
            raise ValueError("invalid live-scene identifier")
    if not isinstance(value["artifact_sha256"], str) or not _HEX64.fullmatch(value["artifact_sha256"]):
        raise ValueError("pinned 64-hex artifact digest required")
    if value["policy_sha256"] != protocol.POLICY_SHA256:
        raise ValueError("provisioning must bind the reviewed live-scene policy digest")
    deadline, minimum = value["deadline_seconds"], value["min_execution_seconds"]
    # Victoria's pump requires a 5-8 s transport deadline; the headroom is measured.
    if not _finite(deadline) or not 5 <= deadline <= MAX_DEADLINE_SECONDS:
        raise ValueError("live deadline must be 5-8 seconds")
    if not _finite(minimum) or not 1 <= minimum < deadline:
        raise ValueError("measured live execution headroom below the deadline required")
    if (not _integer(value["max_input_tokens"], 1024, 32768)
            or not _integer(value["max_image_bytes"], 1, MAX_IMAGE_BYTES)
            or not _integer(value["max_output_tokens"], protocol.MAX_OUTPUT_TOKENS,
                            512 if protocol_name == "lab-live-person-scene-1" else 256)
            or not _integer(value["max_records"], 1000, 1_000_000)
            or not _integer(value["retention_days"], 1, 30)):
        raise ValueError("live-scene bounds outside their limits")
    # max_records 1000-1,000,000 and retention_days 1-30 admit the S1.4 sizing
    # (200,000 rows, 2 days) as well as the previous 7 days.
    return LiveConfig(value["enabled"], value["client_certificate"], value["device_id"],
                      value["configuration_revision"], value["artifact_sha256"], value["engine_epoch"],
                      float(deadline), float(minimum), value["max_input_tokens"], value["max_image_bytes"],
                      value["max_output_tokens"], value["max_records"], value["retention_days"], protocol_name)


def prune_live_rows(db: sqlite3.Connection, cutoff: float, limit: int) -> int:
    """Delete at most limit terminal live rows accepted before cutoff, oldest
    first, in one statement (one short transaction). Unresolved rows and camera
    rows are never touched. Returns the number deleted."""
    return db.execute("DELETE FROM live_requests WHERE rowid IN (SELECT rowid FROM live_requests "
                      "WHERE state='terminal' AND accepted<? ORDER BY accepted LIMIT ?)", (cutoff, limit)).rowcount


class PilotLiveScene:
    """Admission, idempotent submission and terminal lookup on the pilot ledger.

    backend(body: bytes, deadline: float) -> completion bytes; it raises
    BeforeDispatchRejected when nothing was sent and UpstreamFinished when the
    transport process exited without a usable completion. Anything else is an
    unknown outcome.

    reply_wait_seconds 0 answers a submission with its reservation only;
    otherwise the reply waits up to that long for the answer. engine_epoch,
    when given, is the pinned engine's current epoch: once it differs from the
    provisioned one this consumer closes (engine_changed), and it is composed
    closed again until live-scene.json names the current epoch.

    The speed-plan guards, each off when its source is None (runtime is the
    pilot_runtime.RuntimeOptions holding their thresholds):
    - thermal() -> the cached health verdict (pilot_health.Verdict: gpu_c,
      tctl_c), for the S1.4 thermal ladder;
    - metrics(timeout=, seconds=) -> {"running", "waiting"} from vLLM through
      the live worker, for the S1.4 yield (off unless runtime.yield_enabled);
      a failure with a true engine_check attribute (model_worker.WorkerNotSent
      from a refused worker start) is a failed engine check, not a busy vLLM;
    - health() raises unless the live preflight passes: the "clean verdict"
      an S1.8 reopen needs (retried by later submissions while it fails).
      A backend exception with an "ended" attribute
      (model_worker.Linger: wait() -> bool, abandon()) is the live worker
      keeping an unanswered exchange; see _reconcile.
    monotonic is the clock of the metrics age and the rolling reopen hour.
    """

    # The proof format is exactly the saved/live consumers', so the Victoria
    # transport parses one contract.
    _proof = LabSavedActivity._proof

    def __init__(self, pilot: CameraPilot, config: LiveConfig, *, backend: Callable[[bytes, float], bytes],
                 clock: Callable[[], float] = time.time, retention_seconds: float = 900, max_retained: int = 64,
                 admission_margin_seconds: float = 2, log: Callable[[str], None] | None = None,
                 reply_wait_seconds: float = 0, engine_epoch: Callable[[], str] | None = None,
                 metrics: Callable[..., dict] | None = None, thermal: Callable[[], Any] | None = None,
                 health: Callable[[], None] | None = None, runtime: RuntimeOptions | None = None,
                 monotonic: Callable[[], float] = time.monotonic):
        if not isinstance(pilot, CameraPilot) or not isinstance(config, LiveConfig) or not config.enabled:
            raise ValueError("camera pilot and enabled live-scene provisioning required")
        if not callable(backend) or not _finite(admission_margin_seconds) or not 1 <= admission_margin_seconds <= 60:
            raise ValueError("live backend and bounded clock margin required")
        if engine_epoch is not None and not callable(engine_epoch):
            raise ValueError("engine epoch source must be callable")
        if any(source is not None and not callable(source) for source in (metrics, thermal, health)):
            raise ValueError("live guard sources must be callable")
        runtime = RuntimeOptions() if runtime is None else runtime
        if not isinstance(runtime, RuntimeOptions):
            raise ValueError("pilot runtime options required")
        self._reply_wait, self._engine_epoch = bounded_reply_wait(reply_wait_seconds), engine_epoch
        self._runtime, self._thermal, self._health, self._monotonic = runtime, thermal, health, monotonic
        self._metrics = metrics if runtime.yield_enabled else None
        self._guarded = self._thermal is not None or self._metrics is not None
        self._queue: tuple[float, dict] | None = None   # (monotonic, vLLM counts) of the last refresh
        self._metrics_failure: str | None = None        # logged once per change
        self._health_logged: tuple[str, float] | None = None   # (cause, monotonic) of a health refusal line
        self._thermal_logged: str | None = None         # the ladder rung last logged
        # Why live admission is closed; only "unknown_outcome" alone may reopen.
        self._closed_for: set[str] = set()
        self._reopened: deque = deque()                 # monotonic times of S1.8 reopens
        # Monotonic time of the last "reopen deferred" line while a reopen
        # waits for a clean health verdict (submit() retries it), else None.
        self._reopen_pending: float | None = None
        self._lingering, self._closing = None, False
        self.pilot, self.config, self._backend, self.clock = pilot, config, backend, clock
        self.protocol = live_protocol(config.protocol)
        consumer_id = self.protocol.CONSUMER_ID
        self.binding = CredentialBinding("victoria", config.device_id, consumer_id, {LIVE_CAMERA: LIVE_ROOM},
                                         config.configuration_revision)
        # The attributes validate_envelope and _proof read from a composed gateway.
        manifest = SimpleNamespace(model_alias=MODEL_ALIAS, model_revision=config.artifact_sha256,
                                   max_input_tokens=config.max_input_tokens, max_output_tokens=config.max_output_tokens,
                                   max_image_bytes=config.max_image_bytes,
                                   consumer_routes=MappingProxyType({consumer_id: "victoria"}),
                                   consumer_policy_sha256=MappingProxyType({consumer_id: self.protocol.POLICY_SHA256}))
        self.manifest = manifest
        self.controller = SimpleNamespace(manifest=manifest, engine_epoch=config.engine_epoch)
        self.gateway = self.backend = self
        self.margin = float(admission_margin_seconds)
        self._retention, self._max_retained = float(retention_seconds), max_retained
        self._outputs: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self.closed, self.worker, self.failures = False, None, 0
        with pilot.lock:
            pilot.db.executescript("""
              CREATE TABLE IF NOT EXISTS live_requests(
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                accepted REAL NOT NULL, deadline REAL NOT NULL, state TEXT NOT NULL,
                outcome TEXT, engine_epoch TEXT NOT NULL, finished REAL);
              CREATE UNIQUE INDEX IF NOT EXISTS live_one_active ON live_requests((1)) WHERE state!='terminal';
              CREATE INDEX IF NOT EXISTS live_accepted ON live_requests(accepted);
            """)
            # Same rule as the camera rows: an interrupted dispatch stays unresolved.
            pilot.db.execute("UPDATE live_requests SET state='indeterminate',outcome='dispatcher_restart' "
                             "WHERE state='dispatching'")
            self._close_if_engine_changed()

    # --- lifecycle -----------------------------------------------------
    def close_admission(self, reason: str | None = None) -> None:
        with self.pilot.lock:
            self._close("stopping")
            self._reopen_pending = None

    def _close(self, reason: str) -> None:
        """Close live admission (under pilot.lock where a thread may race)."""
        self.closed = True
        self._closed_for.add(reason)

    def _close_if_engine_changed(self) -> None:
        """Under pilot.lock. After an accepted vLLM re-pin camera chat continues
        on the new incarnation, but live facts carry this consumer's epoch, so
        it stays closed until restarted with live-scene.json naming the new one."""
        # Recorded even when already closed, so an S1.8 reopen never undoes it.
        if (self._engine_epoch is None or "engine_changed" in self._closed_for
                or self._engine_epoch() == self.config.engine_epoch):
            return
        self._close("engine_changed")
        self._log("live scene admission closed: engine_changed")

    def close(self, timeout: float = 12) -> bool:
        self.close_admission()
        # A lingering S1.8 exchange is abandoned: its worker is killed and the
        # row stays indeterminate (owner reconcile, as before).
        self._closing = True
        lingering = self._lingering
        if lingering is not None:
            _abandon(lingering)
        worker = self.worker
        if worker is not None and worker.ident is not None:
            worker.join(timeout=timeout)
            return not worker.is_alive()
        return True

    # --- retained answers ----------------------------------------------
    def output(self, ledger_id: str) -> dict | None:
        item = self._outputs.get(ledger_id)
        return dict(item[1]) if item and item[0] > self.clock() else None

    def _expire_outputs(self) -> None:
        now = self.clock()
        for key in [key for key, (expiry, _) in self._outputs.items() if expiry <= now]:
            self._outputs.pop(key, None)

    def _record(self, ledger_id: str) -> _Record | None:
        row = self.pilot.db.execute("SELECT id,state,outcome,engine_epoch,digest FROM live_requests WHERE id=?",
                                    (ledger_id,)).fetchone()
        return None if row is None else (_Record(row[0], row[1], row[2], row[3]), row[4])

    # --- consumer API --------------------------------------------------
    def submit(self, payload: Any, binding: CredentialBinding) -> dict:
        if binding != self.binding:
            raise LiveSceneRefusal("consumer_not_registered", 403)
        request = self.protocol.validate_envelope(payload, binding, self)
        if self._reopen_pending is not None:
            # S1.8: a reopen deferred for a failed health verdict is retried
            # here, outside pilot.lock; it never raises.
            self._reopen()
        try:
            proof, worker, request = self._submit(request)
            if proof is None:
                # Admissible on the ledger. The S1.4 guards run outside
                # pilot.lock (a health refresh or a vLLM metrics exchange may
                # take a while); then every check runs again under the lock.
                proof, worker, request = self._submit(request, self._guard(request))
        except sqlite3.Error:
            self._close("ledger_unavailable")
            raise AdmissionClosed("live ledger unavailable") from None
        return proof if worker is None else self._reply(worker, request, proof)

    def _reply(self, worker: threading.Thread, request: Any, pending: dict) -> dict:
        """Synchronous reply: wait (outside pilot.lock) for this request's own
        worker for at most min(deadline - now - margin, reply wait), then re-read
        its row. Never raises: an exception here would reach the ingress worker
        and close admission (ingress.py:196-203). On a timeout or any error the
        reservation proof is returned and lookup polling recovers."""
        try:
            wait = min(request.deadline - self.clock() - REPLY_MARGIN_SECONDS, self._reply_wait)
            if not wait > 0:
                return pending
            worker.join(wait)
            if worker.is_alive():
                return pending
            with self.pilot.lock:
                self._expire_outputs()
                existing = self._record(request.ledger_id)
                return pending if existing is None else self._proof(request.identity, existing[0])
        except Exception:
            return pending

    def _submit(self, request: Any, guard: Any = _GUARDS_PENDING) -> tuple[dict | None, threading.Thread | None, Any]:
        """(proof, the worker to await or None, the admitted request), or
        (None, None, request) when the S1.4 guards must run first. guard is
        their result: None (passed) or the LiveSceneRefusal to raise."""
        pilot, db = self.pilot, self.pilot.db
        with pilot.lock:
            self._expire_outputs()
            existing = self._record(request.ledger_id)
            if existing is not None:
                if existing[1] != request.digest:
                    raise RequestConflict("request ID already belongs to different content or identity")
                return self._proof(request.identity, existing[0]), None, request
            # Every refusal below writes nothing; a continuous consumer never grows
            # the ledger by ticking.
            now = self.clock()
            if not _finite(now):
                raise LiveSceneRefusal("admission_closed", 503)
            deadline = min(request.deadline, now + self.config.deadline_seconds)
            if deadline - now < self.config.min_execution_seconds:
                raise LiveSceneRefusal("deadline_insufficient", 409)
            self._close_if_engine_changed()
            if self.closed or pilot.closed:
                raise LiveSceneRefusal("admission_closed", 503)
            live_state = db.execute("SELECT state FROM live_requests WHERE state!='terminal'").fetchone()
            if live_state is not None:
                raise LiveSceneRefusal("perceiver_busy" if live_state[0] == "dispatching" else "admission_closed",
                                       429 if live_state[0] == "dispatching" else 503)
            camera = db.execute("SELECT state FROM pilot_requests WHERE state!='terminal'").fetchone()
            if camera is not None:
                # An unresolved camera outcome leaves engine state unknown.
                if camera[0] == "dispatching":
                    raise LiveSceneRefusal("interactive_queued", 429)
                raise LiveSceneRefusal("admission_closed", 503)
            if shutil.disk_usage(pilot.path.parent).free < pilot.free_space_floor:
                raise LiveSceneRefusal("admission_closed", 503)
            if guard is _GUARDS_PENDING and self._guarded:
                return None, None, request
            if isinstance(guard, LiveSceneRefusal):
                raise guard
            # Bounded: a large backlog drains over many admissions (or offline,
            # lab_live_prune.py) instead of one long delete under pilot.lock.
            prune_live_rows(db, now - self.config.retention_days * 86400, PRUNE_BATCH_ROWS)
            if db.execute("SELECT COUNT(*) FROM live_requests").fetchone()[0] >= self.config.max_records:
                raise LiveSceneRefusal("admission_closed", 503)
            request = replace(request, deadline=deadline)
            # The durable reservation precedes the worker and every upstream byte.
            db.execute("INSERT INTO live_requests VALUES(?,?,?,?,?,'dispatching',NULL,?,NULL)",
                       (request.ledger_id, request.request_id, request.digest, now, deadline, self.config.engine_epoch))
            worker = threading.Thread(target=self._execute, args=(request,), name="lab-live-scene", daemon=True)
            try:
                worker.start()
            except Exception:
                # Proven never sent: no thread ran.
                self._close("dispatch_unavailable")
                db.execute("UPDATE live_requests SET state='terminal',outcome='dispatch_unavailable',finished=? WHERE id=?",
                           (now, request.ledger_id))
                return self._proof(request.identity, self._record(request.ledger_id)[0]), None, request
            self.worker = worker
            return (self._proof(request.identity, _Record(request.ledger_id, "dispatching", None, self.config.engine_epoch)),
                    worker, request)

    # --- S1.4 guards (outside pilot.lock, before any ledger write) ------
    def _guard(self, request: Any) -> LiveSceneRefusal | None:
        """The thermal ladder, then the yield to vLLM's queue. Never raises."""
        verdict = self._reading()
        if self._thermal_level(verdict) is not None:
            return LiveSceneRefusal("thermal_shed", 503)
        if self._metrics is not None:
            return self._yield(request, verdict)
        return None

    def _reading(self) -> Any:
        """The cached health verdict (thermal()), or None."""
        if self._thermal is None:
            return None
        try:
            return self._thermal()
        except Exception:
            return None

    def _thermal_level(self, verdict: Any) -> str | None:
        """"stop", "shed" or None from the cached health verdict's readings.
        No reading is no decision: the backend's own fail-closed preflight,
        with its unchanged > 80 C guard, still runs before any upstream byte.
        Each change of rung is logged once."""
        gpu, tctl = (value if _finite(value) else None
                     for value in (getattr(verdict, "gpu_c", None), getattr(verdict, "tctl_c", None)))
        if gpu is None and tctl is None:
            return None
        runtime = self._runtime
        if gpu is not None and gpu >= runtime.live_stop_gpu_c:
            level = "stop"
        elif (gpu is not None and gpu >= runtime.live_shed_gpu_c) or (tctl is not None and tctl >= runtime.live_shed_tctl_c):
            level = "shed"
        else:
            level = None
        if level != self._thermal_logged:
            self._thermal_logged = level
            self._log("live scene thermal_shed %s gpu_c=%s tctl_c=%s" % (
                "level=" + level if level else "cleared", gpu, tctl))
        return level

    def _yield(self, request: Any, verdict: Any) -> LiveSceneRefusal | None:
        """(429, queue_busy) while vLLM has a waiting request or at least
        yield_running_threshold running (voice, camera chat and batch work
        count too), or when its counts are older than YIELD_METRICS_SECONDS
        and cannot be refreshed within this request's execution headroom.
        A failed engine or health check is no load signal: nothing could be
        sent, so it is (503, admission_closed), Victoria's backoff refusal."""
        if verdict is not None and getattr(verdict, "ok", True) is False:
            # The backend's preflight would reject on this same cached verdict.
            return self._health_refused(getattr(verdict, "error", None) or "HealthVerdictFailed")
        queue_busy = LiveSceneRefusal("queue_busy", 429)
        cached, now = self._queue, self._monotonic()
        if cached is None or not 0 <= now - cached[0] <= YIELD_METRICS_SECONDS:
            wall = self.clock()
            budget = min(request.deadline, wall + self.config.deadline_seconds) - wall - self.config.min_execution_seconds
            if not _finite(budget) or budget <= 0:
                return queue_busy   # the re-check under the lock refuses deadline_insufficient first
            failure = None
            try:
                value = self._metrics(timeout=min(YIELD_METRICS_SECONDS, budget), seconds=budget)
                if not isinstance(value, dict) or not all(_finite(value.get(key)) and value[key] >= 0
                                                          for key in ("running", "waiting")):
                    raise ValueError("vLLM counts unavailable")
                cached = self._queue = (now, {"running": value["running"], "waiting": value["waiting"]})
            except Exception as error:
                if getattr(error, "engine_check", False) is True:
                    # The live worker is not running and its fresh,
                    # epoch-bound engine check refused to start one.
                    cause = error.__cause__
                    return self._health_refused(type(cause if cause is not None else error).__name__)
                failure = type(error).__name__
            if failure != self._metrics_failure:
                self._metrics_failure = failure
                self._log("live scene yield metrics " + ("available" if failure is None else "unavailable: " + failure))
            if failure is not None:
                return queue_busy
        self._health_logged = None
        counts = cached[1]
        if counts["waiting"] > 0 or counts["running"] >= self._runtime.yield_running_threshold:
            return queue_busy
        return None

    def _health_refused(self, cause: str) -> LiveSceneRefusal:
        """(503, admission_closed) for a failed health or engine check, with
        nothing written. Logged on each change of cause and at most every
        HEALTH_LOG_SECONDS while it lasts, so the outage stays visible."""
        now = self._monotonic()
        logged = self._health_logged
        if logged is None or logged[0] != cause or not 0 <= now - logged[1] < HEALTH_LOG_SECONDS:
            self._health_logged = (cause, now)
            self._log("live scene preflight rejected: %s before admission" % cause)
        return LiveSceneRefusal("admission_closed", 503)

    def _execute(self, request: Any) -> None:
        backend_timing.start()
        started = time.monotonic()
        state, outcome, detail, close, ended, recorded = "terminal", None, None, False, None, False
        try:
            raw = self._backend(request.body, request.deadline)
            if not isinstance(raw, bytes) or len(raw) > MAX_COMPLETION_BYTES:
                raise UpstreamFinished("completion unavailable")
            text, completion_id, finish, prompt_tokens, completion_tokens = _lab_completion(
                self.manifest, raw, SimpleNamespace(max_output_tokens=request.max_tokens))
            detail = {"content": text, "completion_id": completion_id, "finish_reason": finish,
                      "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}
        except BeforeDispatchRejected:
            outcome = "preflight_rejected"
        except UpstreamFinished:
            outcome = "upstream_failed"
        except (BackendUnknown, ValueError, UnicodeError, RecursionError):
            # The transport process returned: the exchange ended, the answer is unusable.
            outcome = "completion_invalid"
        except BaseException as error:
            state, outcome, close = "indeterminate", "upstream_outcome_unknown", True
            # model_worker.Linger when the live worker kept the exchange (S1.8).
            ended = getattr(error, "ended", None)
            self._log("live scene execution unresolved: " + type(error).__name__)
        seconds = time.monotonic() - started
        try:
            with self.pilot.lock:
                now = self.clock()
                if detail is not None:
                    outcome = "completed_after_deadline" if now > request.deadline else "completed"
                    self._outputs[request.ledger_id] = (now + self._retention, detail)
                    while len(self._outputs) > self._max_retained:
                        self._outputs.popitem(last=False)
                    self.failures = 0
                elif outcome in {"upstream_failed", "completion_invalid"}:
                    self.failures += 1
                    close = close or self.failures >= MAX_CONSECUTIVE_FAILURES
                if close:
                    self._close("unknown_outcome" if state == "indeterminate" else "consecutive_failures")
                self.pilot.db.execute("UPDATE live_requests SET state=?,outcome=?,finished=? WHERE id=?",
                                      (state, outcome, now, request.ledger_id))
                recorded = True
        except Exception as error:
            self._close("ledger_unavailable")
            self._log("live scene ledger unavailable: " + type(error).__name__)
        self._log("live scene backend seconds=%.3f outcome=%s" % (seconds, outcome) + backend_timing.suffix(seconds))
        if ended is not None:
            self._reconcile(request, ended, recorded)

    # --- S1.8 reconcile and bounded reopen ----------------------------
    def _reconcile(self, request: Any, ended: Any, recorded: bool) -> None:
        """The live worker kept this unanswered exchange (model_worker.Linger).
        Once it proves the exchange ended (a reply frame: its model call
        returned; a worker exit proves nothing) the indeterminate row becomes terminal
        unknown_resolved, with no answer even if one arrived, and the consumer
        may reopen. vLLM's num_requests_running is never this evidence: it
        counts voice and batch work and cannot show that this request ended.
        Without that proof the row stays indeterminate and live stays closed
        (owner reconcile, as before). Runs in this request's worker thread,
        outside pilot.lock; camera rows are never touched."""
        if not recorded or not self._runtime.live_reopens_per_hour:
            _abandon(ended)
            return
        self._lingering = ended
        if self._closing:
            _abandon(ended)
        try:
            proven = ended.wait() is True
        except Exception:
            proven = False
        finally:
            self._lingering = None
        if not proven:
            self._log("live scene unknown outcome unresolved: " + ("stopping" if self._closing else "exchange_not_ended"))
            return
        try:
            with self.pilot.lock:
                changed = self.pilot.db.execute(
                    "UPDATE live_requests SET state='terminal',outcome='unknown_resolved',finished=? "
                    "WHERE id=? AND state='indeterminate'", (self.clock(), request.ledger_id)).rowcount
        except Exception as error:
            self._close("ledger_unavailable")
            self._log("live scene ledger unavailable: " + type(error).__name__)
            return
        if changed != 1:
            self._log("live scene unknown outcome unresolved: row_changed")
            return
        self._log("live scene unknown outcome resolved: unknown_resolved")
        self._reopen()

    def _reopen(self) -> None:
        """Reopen when the unknown outcome alone closed this consumer, no live
        row is unresolved and the live health verdict is clean, at most
        live_reopens_per_hour times per rolling hour (plan S1.8). Heat is no
        reason to stay closed: every live submission meets the S1.4 thermal
        ladder (503 thermal_shed) before any ledger write. A failed verdict is
        transient, so the reopen is deferred and submit() retries it until it
        passes (one line, repeated at most every HEALTH_LOG_SECONDS); a blip
        at reconcile time never needs a restart. Another close reason or the
        hourly limit keeps live closed until a restart, with one alarm line.
        Never raises."""
        healthy = self._health is not None
        if healthy:
            try:
                self._health()
            except Exception:
                healthy = False
        blocked, count, deferred = None, 0, False
        try:
            with self.pilot.lock:
                # Recorded first, so a pending reopen never outlives a re-pin.
                self._close_if_engine_changed()
                now = self._monotonic()
                if not self.closed:
                    self._reopen_pending = None
                    return
                while self._reopened and now - self._reopened[0] >= REOPEN_WINDOW_SECONDS:
                    self._reopened.popleft()
                other = sorted(self._closed_for - {"unknown_outcome"})
                if other or "unknown_outcome" not in self._closed_for:
                    blocked = "+".join(other) or "closed"
                elif self._health is None:
                    blocked = "no_health_check"
                elif self.pilot.db.execute("SELECT 1 FROM live_requests WHERE state!='terminal'").fetchone() is not None:
                    blocked = "unresolved"
                elif len(self._reopened) >= self._runtime.live_reopens_per_hour:
                    blocked = "reopen_limit"
                elif not healthy:
                    logged = self._reopen_pending
                    deferred = logged is None or not 0 <= now - logged < HEALTH_LOG_SECONDS
                    if deferred:
                        self._reopen_pending = now
                    else:
                        return
                else:
                    self.closed = False
                    self._closed_for.clear()
                    self._reopened.append(now)
                    count = len(self._reopened)
                if not deferred:
                    self._reopen_pending = None
        except Exception:
            self._reopen_pending = None
            self._close("ledger_unavailable")
            blocked = "ledger_unavailable"
        try:
            if deferred:
                self._log("live scene admission reopen deferred: health")
            elif blocked is not None:
                self._log("live scene admission stays closed: " + blocked)
            else:
                self._log("live scene admission reopened: unknown_resolved reopens=%d" % count)
        except Exception:
            pass

    def lookup(self, payload: Any, binding: CredentialBinding) -> dict:
        """Terminal lookup by identity and digests; never accepts a body."""
        if binding != self.binding:
            raise LiveSceneRefusal("consumer_not_registered", 403)
        if (not isinstance(payload, dict) or set(payload) != ENVELOPE_FIELDS - {"body"}
                or not isinstance(payload["request_id"], str) or not _HEX64.fullmatch(payload["request_id"])
                or payload["site_id"] != "victoria" or not isinstance(payload["body_sha256"], str)
                or not _HEX64.fullmatch(payload["body_sha256"]) or not _finite(payload["deadline"])):
            raise LiveSceneRefusal("invalid_request")
        identity = _identity(payload)
        ledger_id = canonical_digest([binding.site_id, binding.device_id, payload["request_id"]])
        try:
            with self.pilot.lock:
                self._expire_outputs()
                existing = self._record(ledger_id)
                if existing is not None:
                    if existing[1] != canonical_digest(identity):
                        raise RequestConflict("request identity differs from the recorded request")
                    return self._proof(identity, existing[0])
                now = self.clock()
        except sqlite3.Error:
            self._close("ledger_unavailable")
            raise AdmissionClosed("live ledger unavailable") from None
        value = {"version": 1, "request_id": payload["request_id"], "body_sha256": payload["body_sha256"],
                 "terminal": False, "state": "not_found", "engine_epoch": self.config.engine_epoch,
                 "artifact_sha256": self.config.artifact_sha256, "reason": None}
        # Admission refuses a deadline shorter than the headroom on its own clock,
        # so a missing record after the deadline plus a clock margin never executes.
        if _finite(now) and now > payload["deadline"] + self.margin:
            value.update(terminal=True, state="not_admitted", reason="deadline_passed_without_admission")
        return value


def _abandon(ended: Any) -> None:
    try:
        ended.abandon()
    except Exception:
        pass


class _PilotLiveHandler(LiveSceneRoutes, _TLSHandler):
    pass


def _person_live_handler():
    from .lab_live_person_scene import LivePersonSceneRoutes
    class PersonPilotLiveHandler(LivePersonSceneRoutes, _TLSHandler):
        pass
    return PersonPilotLiveHandler


class PilotLiveIngress(PrivateTLSIngress):
    """The camera pilot's private TLS ingress plus the live-scene routes."""

    def __init__(self, address: tuple[str, int], pilot: CameraPilot, live: PilotLiveScene, **kwargs: Any):
        if not isinstance(live, PilotLiveScene) or live.pilot is not pilot:
            raise AdmissionError("live consumer must share this camera pilot")
        bindings = list((kwargs.get("identities") or {}).values())
        if bindings.count(live.binding) != 1 or bindings.count(pilot.binding) != 1 or len(bindings) != 2:
            raise AdmissionError("exactly the camera and live identities must be registered")
        super().__init__(address, pilot, **kwargs)
        self.live, self.lab = live, None
        self.RequestHandlerClass = (_person_live_handler() if live.config.protocol == "lab-live-person-scene-1"
                                    else _PilotLiveHandler)

    def server_close(self):
        self.live.close_admission("private TLS ingress stopping")
        super().server_close()


LIVE_INFER = '''
import sys,json,http.client
value=json.load(sys.stdin)
connection=http.client.HTTPConnection('127.0.0.1',8000,timeout=value['timeout'])
try:
 connection.request('POST','/v1/chat/completions',body=value['body'].encode(),headers={'Content-Type':'application/json'})
 response=connection.getresponse()
 raw=response.read(131073)
 if response.status!=200 or len(raw)>131072:raise ValueError('model response unavailable')
 sys.stdout.write(raw.decode())
finally:connection.close()
'''


def docker_backend(container: str, model_health: Callable[[], None], *, clock: Callable[[], float] = time.time,
                   run: Callable[..., Any] = subprocess.run, worker: ModelWorker | None = None,
                   fresh_health: Callable[[], None] | None = None,
                   linger: float = 0) -> Callable[[bytes, float], bytes]:
    """The camera pilot's fixed model path for exact live-scene bytes; never retried.

    With worker (model_worker.ModelWorker, speed plan S1.1) the bytes go
    through this consumer's long-lived worker instead of a fresh `docker exec`.
    The mapping is the per-request one: a frame proven never written is a
    rejection before dispatch, an error frame or a worker exit is a finished
    transport (as a non-zero exit was), and no reply by the deadline is an
    unknown outcome (as a timeout was). fresh_health (default model_health)
    must pass before the worker process is (re)started. The returned callable
    then also has metrics(timeout=1.0, seconds=None): vLLM's running and
    waiting counts through the same worker, for the S1.4 yield (seconds, by
    default timeout + 1, also bounds a worker start); it raises
    model_worker.ModelWorkerError when unavailable. linger > 0 (S1.8) keeps
    the worker of an unanswered exchange that long, so the unknown outcome
    carries a model_worker.Linger; per-request exec never lingers.
    """
    before_start = fresh_health or model_health
    if not isinstance(container, str) or not re.fullmatch(r"[a-f0-9]{64}", container):
        raise ValueError("pinned container id required")
    if not _finite(linger) or not 0 <= linger <= LIVE_LINGER_SECONDS:
        raise ValueError("bounded live linger required")

    def execute(body: bytes, deadline: float) -> bytes:
        # This exception boundary ends BEFORE starting the Docker/model transport.
        try:
            with backend_timing.stage("health"):
                model_health()
            remaining = deadline - clock()
            if not _finite(remaining) or remaining <= 1:
                raise ValueError("live deadline exhausted")
            text = body.decode("ascii")
        except Exception as error:
            print("live scene preflight rejected: " + type(error).__name__, file=sys.stderr, flush=True)
            raise BeforeDispatchRejected("preflight_rejected") from error
        # Same fixed container path as camera chat: no published model port,
        # shell, dynamic command, retry or model lifecycle action.
        with backend_timing.stage("exec"):
            if worker is not None:
                return _worker_exchange(worker, container, text, remaining, before_start, linger)
            result = run(["docker", "exec", "-i", container, "python3", "-I", "-S", "-c", LIVE_INFER],
                         input=json.dumps({"body": text, "timeout": remaining + 1}), capture_output=True, text=True,
                         timeout=remaining + EXECUTION_GRACE_SECONDS, check=False)
        if result.returncode != 0:
            raise UpstreamFinished("model transport exited")
        return result.stdout.encode()

    if worker is not None:
        def metrics(timeout: float = 1.0, seconds: float | None = None) -> dict:
            return worker.metrics(container, timeout=timeout, seconds=timeout + 1 if seconds is None else seconds,
                                  before_start=before_start)
        execute.metrics = metrics
    return execute


def _worker_exchange(worker: ModelWorker, container: str, text: str, remaining: float,
                     before_start: Callable[[], None], linger: float = 0) -> bytes:
    # linger is passed only when set, so the camera-identical call is unchanged.
    extra = {"linger": linger} if linger else {}
    try:
        reply = worker.chat(container, text, timeout=remaining + 1, limit=MAX_COMPLETION_BYTES,
                            seconds=remaining + EXECUTION_GRACE_SECONDS, before_start=before_start, **extra)
    except WorkerNotSent as error:
        print("live scene preflight rejected: " + type(error).__name__, file=sys.stderr, flush=True)
        raise BeforeDispatchRejected("preflight_rejected") from error
    except (WorkerFailed, WorkerExited):
        raise UpstreamFinished("model transport exited") from None
    return reply.encode()


def timed_backend(backend: Callable[[Any], Any], label: str) -> Callable[[Any], Any]:
    """Camera backend wrapper used only when the live consumer is composed: one
    stderr line per camera call (duration, health/exec split and result type,
    no content)."""
    def call(request: Any) -> Any:
        backend_timing.start()
        started = time.monotonic()
        result = "ok"
        try:
            return backend(request)
        except BaseException as error:
            result = type(error).__name__
            raise
        finally:
            seconds = time.monotonic() - started
            print("%s backend seconds=%.3f result=%s" % (label, seconds, result) + backend_timing.suffix(seconds),
                  file=sys.stderr, flush=True)
    return call


def _epoch_bound(model_health: Callable[[], None], engine: Any, epoch: str) -> Callable[[], None]:
    """The live preflight: model_health, then the pinned engine still has this
    consumer's epoch. The health check may just have accepted a re-pin; the live
    bytes never reach an engine incarnation their epoch does not name."""
    def health() -> None:
        model_health()
        if engine.engine_epoch != epoch:
            raise ValueError("engine_changed")
    return health


def compose_pilot_live(private: str | Path, pilot: CameraPilot, identities: dict, *, container: str,
                       model_health: Callable[[], None], enforce_linux: bool = True,
                       engine: Any = None, worker: ModelWorker | None = None,
                       fresh_health: Callable[[], None] | None = None, runtime: RuntimeOptions | None = None,
                       verdict: Callable[[], Any] | None = None) -> PilotLiveScene | None:
    """Live consumer from private/live-scene.json, or None (camera-only).

    Absent or disabled provisioning returns None. Invalid provisioning also
    returns None after one stderr line: a live-scene mistake never takes camera
    chat down. On success the live certificate identity is added to identities.

    engine (model_pin.PinnedEngine): enabled provisioning must quote the pin's
    artifact_sha256 and its engine_epoch as provisioned, or an epoch an
    accepted re-pin replaced (the pin's repinned_from, or any epoch once that
    bounded lineage is full: the live-scene.json bump is still pending), or
    EngineIdentityMismatch is raised and the service refuses to start. The consumer then follows the engine's current
    epoch: composed or later closed (engine_changed, one alarm line) whenever
    that differs from live-scene.json's, while camera chat runs. It replies
    synchronously exactly when the camera pilot does.

    worker: this consumer's own long-lived model worker (never camera chat's),
    or None for a fresh `docker exec` per request. fresh_health: the uncached
    check a worker start needs (default model_health), bound to the epoch too.

    runtime (pilot_runtime.RuntimeOptions, default the defaults) holds the
    S1.4/S1.8 thresholds. verdict() returns the cached health verdict for the
    thermal ladder (none: no ladder). The yield and the reconcile need the
    long-lived worker; without one they are off and the yield says so once.
    """
    private = Path(private)
    runtime = RuntimeOptions() if runtime is None else runtime
    try:
        config = load_live_config(private / CONFIG_NAME, enforce_linux=enforce_linux)
        if config is None:
            return None
        health, fresh, current = model_health, fresh_health or model_health, None
        if engine is not None:
            check_live_identity(engine.provisioned, config)
            health, current = _epoch_bound(model_health, engine, config.engine_epoch), (lambda: engine.engine_epoch)
            fresh = _epoch_bound(fresh, engine, config.engine_epoch)
        certificate = protected_file(private / config.client_certificate, enforce_linux=enforce_linux).read_text()
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        if fingerprint in identities or len(identities) != 1:
            raise ValueError("live client certificate must be a distinct identity")
        linger = LIVE_LINGER_SECONDS if worker is not None and runtime.live_reopens_per_hour else 0
        backend = docker_backend(container, health, worker=worker, fresh_health=fresh, linger=linger)
        metrics = getattr(backend, "metrics", None)
        live = PilotLiveScene(pilot, config, backend=backend,
                              reply_wait_seconds=getattr(pilot, "reply_wait_seconds", 0), engine_epoch=current,
                              metrics=metrics, thermal=verdict, health=health, runtime=runtime)
    except EngineIdentityMismatch:
        raise
    except Exception as error:
        print("live scene consumer disabled: " + type(error).__name__, file=sys.stderr, flush=True)
        return None
    if runtime.yield_enabled and metrics is None:
        print("live scene yield unavailable: no live model worker", file=sys.stderr, flush=True)
    identities[fingerprint] = live.binding
    print("live scene consumer enabled: device=%s revision=%s epoch=%s" % (
        config.device_id, config.configuration_revision, config.engine_epoch), file=sys.stderr, flush=True)
    return live
