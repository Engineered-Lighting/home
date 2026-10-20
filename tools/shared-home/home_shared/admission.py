"""Fail-closed resource admission and durable dispatch accounting.

This module does not attest host health, authorize a quarantined workload, or
prove that upstream bypasses have been removed. Those are required, independent
inputs from a trusted deployment verifier. There is deliberately no JSON flag
or command-line option that substitutes for that verifier.

Only hashes, routing metadata and reservations enter the ledger: never prompts,
images, credentials or model output. SQLite is local to the single dispatcher.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.parse import urlsplit

from .rejections import REJECTION_REASONS as REJECTION_OUTCOMES


class AdmissionError(ValueError):
    """Invalid admission input or transition."""


class AdmissionClosed(AdmissionError):
    """Admission is unavailable; the caller must not send upstream bytes."""


class CapacityExceeded(AdmissionError):
    pass


class RequestConflict(AdmissionError):
    pass


class LedgerLocked(AdmissionError):
    pass


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value: Any, name: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdmissionError(f"{name} must be a finite number")
    try:
        valid = math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    if not valid:
        raise AdmissionError(f"{name} is outside its permitted range")
    return float(value)


def _integer(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AdmissionError(f"{name} must be an integer >= {minimum}")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}", value):
        raise AdmissionError(f"invalid {name}")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise AdmissionError(f"{name} must be a resolved sha256 digest")
    return value


RESOURCES = ("ram_bytes", "vram_bytes", "cpu_millicores", "io_bytes_per_second")
RECEIPTS = frozenset({"policy", "health", "resource_validation", "route_isolation", "engine_identity"})


def _resources(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != set(RESOURCES):
        raise AdmissionError(f"{name} must specify every resource dimension")
    return {key: _number(value[key], f"{name}.{key}") for key in RESOURCES}


@dataclass(frozen=True)
class ResourceManifest:
    """Immutable, schema-checked evidence *subject*, not self-authenticating proof.

    Resource profiles include the validated worst-case overlapping resident
    startup/steady peaks. Request peaks are additional to residency. An exact
    manifest hash must be bound into every externally verified receipt.
    """

    digest: str
    engine_id: str
    model_alias: str
    model_revision: str
    upstream: str
    max_active: int
    max_queue: int
    max_ledger_records: int
    max_input_tokens: int
    max_output_tokens: int
    max_image_bytes: int
    max_deadline_seconds: float
    request_peak: Mapping[str, float]
    consumer_routes: Mapping[str, str]
    free_storage_floor_bytes: int
    health_max_age_seconds: float
    receipt_max_age_seconds: float
    consumer_deadline_seconds: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}))
    consumer_policy_sha256: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResourceManifest":
        required = {"version", "engine_id", "model_alias", "model_revision", "upstream",
                    "services", "capacity", "os_recovery_reserve", "resident_overlap_peak",
                    "request_peak", "max_active", "max_queue", "max_ledger_records", "max_input_tokens",
                    "max_output_tokens", "max_image_bytes", "max_deadline_seconds",
                    "consumer_routes", "free_storage_floor_bytes", "health_max_age_seconds",
                    "receipt_max_age_seconds", "measurement_artifact_sha256"}
        if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 3:
            required = required | {"consumer_deadline_seconds", "consumer_policy_sha256"}
        if (not isinstance(value, dict) or set(value) != required or
                type(value["version"]) is not int or value["version"] not in (2, 3)):
            raise AdmissionError("resource manifest must exactly match schema version 2 or 3")
        _digest(value["measurement_artifact_sha256"], "measurement_artifact_sha256")
        engine = _identifier(value["engine_id"], "engine_id")
        alias = _identifier(value["model_alias"], "model_alias")
        revision = _digest(value["model_revision"], "model_revision")
        upstream = value["upstream"]
        if not isinstance(upstream, str):
            raise AdmissionError("upstream must be a fixed URL")
        parsed = urlsplit(upstream)
        # A real network deployment uses TLS. Cleartext is allowed only for a
        # backend in the same loopback trust boundary, never LAN hosts.
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"})):
            raise AdmissionError("upstream requires fixed HTTPS or loopback HTTP without credentials")
        capacity = _resources(value["capacity"], "capacity")
        reserve = _resources(value["os_recovery_reserve"], "os_recovery_reserve")
        resident = _resources(value["resident_overlap_peak"], "resident_overlap_peak")
        request = _resources(value["request_peak"], "request_peak")
        active = _integer(value["max_active"], "max_active")
        for resource in RESOURCES:
            if reserve[resource] + resident[resource] + active * request[resource] > capacity[resource]:
                raise AdmissionError(f"validated overlap exceeds {resource} capacity")
        services = value["services"]
        if not isinstance(services, list) or not services:
            raise AdmissionError("all resident services require immutable identities and enforced caps")
        service_ids: set[str] = set()
        service_profiles: list[tuple[dict[str, float], dict[str, float]]] = []
        for service in services:
            if not isinstance(service, dict) or set(service) != {
                    "id", "image_sha256", "model_sha256", "startup_peak", "steady_peak", "caps"}:
                raise AdmissionError("incomplete service resource manifest")
            service_id = _identifier(service["id"], "service id")
            if service_id in service_ids:
                raise AdmissionError("duplicate service")
            service_ids.add(service_id)
            _digest(service["image_sha256"], "image_sha256")
            if service["model_sha256"] is not None:
                _digest(service["model_sha256"], "model_sha256")
            startup = _resources(service["startup_peak"], "startup_peak")
            steady = _resources(service["steady_peak"], "steady_peak")
            service_profiles.append((startup, steady))
            caps = service["caps"]
            if not isinstance(caps, dict) or set(caps) != {
                    "ram_bytes", "swap_bytes", "cpu_millicores", "pids", "io_bytes_per_second"}:
                raise AdmissionError("service caps must bound RAM, swap, CPU, PIDs and I/O")
            for name, limit in caps.items():
                _integer(limit, f"caps.{name}", minimum=0 if name == "swap_bytes" else 1)
            for name in ("ram_bytes", "cpu_millicores", "io_bytes_per_second"):
                if max(startup[name], steady[name]) > caps[name]:
                    raise AdmissionError(f"service {service_id} peak exceeds {name} cap")
            for name in RESOURCES:
                if max(startup[name], steady[name]) > resident[name]:
                    raise AdmissionError(f"resident overlap omits {service_id} {name}")
        if engine not in service_ids:
            raise AdmissionError("engine must appear in resident services")
        for name in RESOURCES:
            # Every listed service can be resident simultaneously. Until an
            # externally enforced phased-start policy is implemented, conservatively
            # include every service's worse startup/steady peak together.
            overlapping_peaks = sum(max(startup[name], steady[name])
                                    for startup, steady in service_profiles)
            if overlapping_peaks > resident[name]:
                raise AdmissionError(f"resident overlap undercounts {name} process peaks")
        engine_caps = next(service["caps"] for service in services if service["id"] == engine)
        engine_service = next(service for service in services if service["id"] == engine)
        for name in ("ram_bytes", "cpu_millicores", "io_bytes_per_second"):
            engine_peak = max(engine_service["startup_peak"][name], engine_service["steady_peak"][name])
            if engine_peak + active * request[name] > engine_caps[name]:
                raise AdmissionError(f"engine cap does not admit resident plus request {name}")
        routes = value["consumer_routes"]
        if not isinstance(routes, dict) or not routes:
            raise AdmissionError("registered consumers and their bound sites are required")
        for consumer, site in routes.items():
            _identifier(consumer, "consumer")
            if site not in {"echo", "victoria"}:
                raise AdmissionError("consumer route has unknown site")
        image_limit = _integer(value["max_image_bytes"], "max_image_bytes")
        if image_limit > 256 * 1024:
            raise AdmissionError("snapshot byte limit exceeds pilot maximum")
        deadline = _number(value["max_deadline_seconds"], "max_deadline_seconds", minimum=0.001)
        if deadline > (15 if value["version"] == 2 else 300):
            raise AdmissionError("admission deadline exceeds manifest schema limit")
        deadlines = {consumer: deadline for consumer in routes}
        policy_digests = {}
        if value["version"] == 3:
            policies = value["consumer_policy_sha256"]
            if not isinstance(policies, dict) or set(policies) != set(routes):
                raise AdmissionError("every registered consumer requires a bound request policy")
            policy_digests = {consumer: _digest(digest, "consumer policy digest")
                              for consumer, digest in policies.items()}
            supplied = value["consumer_deadline_seconds"]
            if not isinstance(supplied, dict) or set(supplied) != set(routes):
                raise AdmissionError("every registered consumer requires an explicit deadline")
            deadlines = {}
            for consumer, limit in supplied.items():
                limit = _number(limit, "consumer deadline", minimum=0.001)
                if limit > deadline or (routes[consumer] == "victoria" and limit > 15):
                    raise AdmissionError("consumer deadline exceeds admitted site or global limit")
                deadlines[consumer] = limit
        ledger_records = _integer(value["max_ledger_records"], "max_ledger_records")
        if ledger_records > 1_000_000:
            raise AdmissionError("metadata ledger record cap exceeds supported maximum")
        return cls(canonical_digest(value), engine, alias, revision, upstream, active,
                   _integer(value["max_queue"], "max_queue"), ledger_records,
                   _integer(value["max_input_tokens"], "max_input_tokens"),
                   _integer(value["max_output_tokens"], "max_output_tokens"), image_limit,
                   deadline, MappingProxyType(dict(request)), MappingProxyType(dict(routes)),
                   _integer(value["free_storage_floor_bytes"], "free_storage_floor_bytes"),
                   _number(value["health_max_age_seconds"], "health_max_age_seconds", minimum=0.001),
                   _number(value["receipt_max_age_seconds"], "receipt_max_age_seconds", minimum=0.001),
                   MappingProxyType(deadlines), MappingProxyType(policy_digests))


@dataclass(frozen=True)
class VerifiedReceipt:
    kind: str
    manifest_digest: str
    engine_epoch: str
    verified_at: float
    expires_at: float
    artifact_digest: str


class EvidenceVerifier(Protocol):
    """Trusted deployment adapter; raw caller-supplied receipts cannot implement it."""

    def verify_admission(self, manifest: ResourceManifest, engine_epoch: str,
                         now: float) -> tuple[VerifiedReceipt, ...]: ...

    def verify_terminal(self, manifest: ResourceManifest, engine_epoch: str,
                        request_id: str, evidence: Any) -> bool: ...


class _LifetimeLock:
    def __init__(self, path: Path):
        self.file = path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.file.close()
            raise LedgerLocked("another dispatch process owns this ledger") from exc

    def close(self) -> None:
        if self.file.closed:
            return
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    site_id: str
    consumer_id: str
    digest: str
    state: str
    task_class: str
    submitted_at: float
    deadline: float
    engine_epoch: str | None
    reservation: Mapping[str, float]
    outcome: str | None


class AdmissionLedger:
    """Single-process durable dispatch ledger. Constructing it fences restarts."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _LifetimeLock(Path(str(self.path) + ".lock"))
        self._mutex = threading.RLock()
        self._engine_locks: dict[str, _LifetimeLock] = {}
        self.broken = False
        self._closed = False
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise AdmissionError("unsupported ledger version")
            self.db.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, site_id TEXT NOT NULL, consumer_id TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, task_class TEXT NOT NULL,
                submitted_at REAL NOT NULL, deadline REAL NOT NULL, engine_epoch TEXT,
                reservation TEXT NOT NULL DEFAULT '{}', outcome TEXT)""")
            self.db.execute("PRAGMA user_version=1")
            with self.transaction():
                self.db.execute("UPDATE requests SET state='indeterminate', outcome='dispatcher_restart' "
                                "WHERE state IN ('dispatching','running')")
                # Queued payloads were never sent and intentionally are not durable.
                self.db.execute("UPDATE requests SET state='terminal', outcome='restart_before_dispatch' "
                                "WHERE state='queued'")
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._mutex:
            if self.broken or self._closed:
                raise AdmissionClosed("ledger unavailable")
            try:
                self.db.execute("BEGIN IMMEDIATE")
                yield
                self.db.execute("COMMIT")
            except sqlite3.Error as exc:
                self.broken = True
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise AdmissionClosed("ledger write failed; admission closed") from exc
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def get(self, request_id: str) -> RequestRecord | None:
        with self._mutex:
            if self.broken or self._closed:
                raise AdmissionClosed("ledger unavailable")
            try:
                row = self.db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            except sqlite3.Error as exc:
                self.broken = True
                raise AdmissionClosed("ledger read failed") from exc
            if row is None:
                return None
            values = dict(row)
            values["reservation"] = json.loads(values["reservation"])
            return RequestRecord(**values)

    def close(self) -> None:
        with self._mutex:
            if not self._closed:
                self.db.close()
                self._closed = True
                self._lock.close()
                for lock in self._engine_locks.values():
                    lock.close()
                self._engine_locks.clear()

    def fence_engine(self, engine_id: str) -> None:
        """Also fence a second ledger accidentally configured for this engine.

        The deployment verifier must establish a single dispatcher host/user and
        stable OS temporary directory. This is not a distributed lease or HA lock.
        """
        with self._mutex:
            if self._closed or self.broken:
                raise AdmissionClosed("ledger unavailable")
            if engine_id not in self._engine_locks:
                directory = Path(tempfile.gettempdir()) / "home-shared-admission-locks"
                directory.mkdir(mode=0o700, exist_ok=True)
                filename = hashlib.sha256(engine_id.encode()).hexdigest() + ".lock"
                self._engine_locks[engine_id] = _LifetimeLock(directory / filename)

    def __enter__(self) -> "AdmissionLedger":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AdmissionController:
    def __init__(self, ledger: AdmissionLedger, manifest: ResourceManifest | None = None,
                 verifier: EvidenceVerifier | None = None, *, clock: Callable[[], float] = time.time):
        self.ledger = ledger
        self.manifest = manifest
        self.verifier = verifier
        self.clock = clock
        self.engine_epoch: str | None = None
        self._enabled = False
        self._reason = "not commissioned"

    def close_admission(self, reason: str) -> None:
        self._enabled = False
        self._reason = reason

    def _evidence(self, epoch: str) -> None:
        if self.manifest is None or self.verifier is None:
            raise AdmissionClosed("validated manifest and trusted evidence verifier required")
        try:
            now = _number(self.clock(), "admission clock")
        except AdmissionError as exc:
            raise AdmissionClosed("admission clock unavailable") from exc
        try:
            receipts = self.verifier.verify_admission(self.manifest, epoch, now)
            if len(receipts) != len(RECEIPTS) or {r.kind for r in receipts} != RECEIPTS:
                raise AdmissionClosed("all independent admission receipts are required")
            for receipt in receipts:
                max_age = (self.manifest.health_max_age_seconds if receipt.kind == "health"
                           else self.manifest.receipt_max_age_seconds)
                _digest(receipt.artifact_digest, "receipt artifact")
                if (receipt.manifest_digest != self.manifest.digest or receipt.engine_epoch != epoch or
                        not math.isfinite(receipt.verified_at) or not math.isfinite(receipt.expires_at) or
                        receipt.verified_at > now or now - receipt.verified_at > max_age or
                        receipt.expires_at <= now):
                    raise AdmissionClosed("stale or mismatched admission receipt")
            if shutil.disk_usage(self.ledger.path.parent).free < self.manifest.free_storage_floor_bytes:
                raise AdmissionClosed("ledger storage below validated free-space floor")
        except AdmissionClosed:
            raise
        except Exception as exc:
            raise AdmissionClosed("trusted evidence verification failed") from exc

    def enable(self, engine_epoch: str) -> None:
        self.close_admission("commissioning")
        _identifier(engine_epoch, "engine_epoch")
        self._evidence(engine_epoch)
        assert self.manifest is not None
        self.ledger.fence_engine(self.manifest.engine_id)
        with self.ledger.transaction():
            if self.ledger.db.execute("SELECT 1 FROM requests WHERE state IN "
                                      "('dispatching','running','indeterminate') LIMIT 1").fetchone():
                raise AdmissionClosed("outstanding execution must be affirmatively reconciled")
        self.engine_epoch = engine_epoch
        self._enabled = True
        self._reason = "admitted"

    def readiness(self) -> dict[str, Any]:
        try:
            self._require_open()
            return {"ready": True, "reason": "admitted", "engine_epoch": self.engine_epoch}
        except AdmissionClosed as exc:
            return {"ready": False, "reason": str(exc), "engine_epoch": self.engine_epoch}

    def _require_open(self) -> None:
        if not self._enabled or self.engine_epoch is None:
            raise AdmissionClosed(self._reason)
        if self.ledger.broken or self.ledger._closed:
            self.close_admission("ledger unavailable")
            raise AdmissionClosed(self._reason)
        try:
            self._evidence(self.engine_epoch)
        except AdmissionClosed as exc:
            self.close_admission(str(exc))
            raise

    def deadline_limit(self, consumer_id: str) -> float:
        if self.manifest is None or consumer_id not in self.manifest.consumer_routes:
            raise AdmissionClosed("consumer has no admitted deadline")
        return self.manifest.consumer_deadline_seconds.get(consumer_id, self.manifest.max_deadline_seconds)

    def submit(self, request_id: str, site_id: str, consumer_id: str, body_digest: str,
               task_class: str, deadline: float) -> RequestRecord:
        _identifier(request_id, "request_id")
        _digest(body_digest, "request digest")
        if task_class not in {"interactive", "background"}:
            raise AdmissionError("unknown task class")
        deadline = _number(deadline, "deadline")
        with self.ledger.transaction():
            existing = self.ledger.get(request_id)
            if existing is not None:
                if (existing.digest, existing.site_id, existing.consumer_id) != (body_digest, site_id, consumer_id):
                    raise RequestConflict("request ID already belongs to different content or identity")
                return existing
            self._require_open()
            assert self.manifest is not None
            if self.manifest.consumer_routes.get(consumer_id) != site_id:
                raise AdmissionClosed("consumer has no admitted route for this site")
            now = _number(self.clock(), "admission clock")
            if not now < deadline <= now + self.deadline_limit(consumer_id):
                raise AdmissionError("request is expired or exceeds its deadline bound")
            self.ledger.db.execute("UPDATE requests SET state='terminal', outcome='queue_expired' "
                                   "WHERE state='queued' AND deadline<=?", (now,))
            queued = self.ledger.db.execute("SELECT COUNT(*) FROM requests WHERE state='queued'").fetchone()[0]
            if queued >= self.manifest.max_queue:
                raise CapacityExceeded("bounded dispatch queue is full")
            self._require_metadata_capacity()
            self.ledger.db.execute("INSERT INTO requests(request_id,site_id,consumer_id,digest,state,"
                                   "task_class,submitted_at,deadline) VALUES(?,?,?,?,'queued',?,?,?)",
                                   (request_id, site_id, consumer_id, body_digest, task_class, now, deadline))
        result = self.ledger.get(request_id)
        assert result is not None
        return result

    def _require_metadata_capacity(self) -> None:
        """Bound new rows, including negative receipts, within their transaction.

        Existing status, idempotent retrieval and terminal reconciliation do not
        call this guard. No deletion of tombstones manufactures new headroom.
        """
        if not self.ledger.db.in_transaction:
            raise AdmissionClosed("metadata capacity check requires a transaction")
        if self.manifest is None:
            raise AdmissionClosed("explicit metadata storage manifest required")
        if self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= self.manifest.max_ledger_records:
            self.close_admission("metadata ledger record capacity exhausted")
            raise AdmissionClosed("metadata ledger record capacity exhausted")
        try:
            free = shutil.disk_usage(self.ledger.path.parent).free
        except OSError:
            self.close_admission("metadata storage headroom unavailable")
            raise AdmissionClosed("metadata storage headroom unavailable") from None
        if free < self.manifest.free_storage_floor_bytes:
            self.close_admission("metadata storage below validated free-space floor")
            raise AdmissionClosed("metadata storage below validated free-space floor")

    def reject_if_absent(self, request_id: str, site_id: str, consumer_id: str,
                         body_digest: str, task_class: str, deadline: float,
                         outcome: str) -> RequestRecord:
        """Durably reject only an operation this ledger has never accepted.

        New rejection rows require the same explicit storage budget as accepted
        rows. Existing records always win; a late rejection cannot clear an accepted reservation.
        Rejections are permanent idempotency tombstones, not expiring queue
        entries. Disk/commit/readback failure supplies no affirmative receipt.
        """
        _identifier(request_id, "request_id")
        _identifier(consumer_id, "consumer_id")
        _digest(body_digest, "request digest")
        if site_id not in {"echo", "victoria"} or task_class not in {"interactive", "background"}:
            raise AdmissionError("invalid rejection routing")
        if outcome not in REJECTION_OUTCOMES:
            raise AdmissionError("unknown rejection outcome")
        deadline = _number(deadline, "deadline")
        with self.ledger.transaction():
            existing = self.ledger.get(request_id)
            if existing is not None:
                if (existing.digest, existing.site_id, existing.consumer_id) != (body_digest, site_id, consumer_id):
                    raise RequestConflict("request ID already belongs to different content or identity")
                return existing
            now = _number(self.clock(), "admission clock")
            self._require_metadata_capacity()
            self.ledger.db.execute("INSERT INTO requests(request_id,site_id,consumer_id,digest,state,"
                                   "task_class,submitted_at,deadline,outcome) VALUES(?,?,?,?,'terminal',?,?,?,?)",
                                   (request_id, site_id, consumer_id, body_digest, task_class, now, deadline, outcome))
        result = self.ledger.get(request_id)
        if result is None:
            self.ledger.broken = True
            self.close_admission("rejection readback unavailable")
            raise AdmissionClosed("rejection readback unavailable")
        return result

    def claim_next(self) -> RequestRecord | None:
        self._require_open()
        assert self.manifest is not None
        with self.ledger.transaction():
            charged = self.ledger.db.execute("SELECT COUNT(*) FROM requests WHERE state IN "
                                             "('dispatching','running','indeterminate')").fetchone()[0]
            if charged >= self.manifest.max_active:
                return None
            self.ledger.db.execute("UPDATE requests SET state='terminal', outcome='queue_expired' "
                                   "WHERE state='queued' AND deadline<=?", (self.clock(),))
            row = self.ledger.db.execute("SELECT request_id FROM requests WHERE state='queued' "
                                         "ORDER BY CASE task_class WHEN 'interactive' THEN 0 ELSE 1 END,"
                                         "submitted_at, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            self.ledger.db.execute("UPDATE requests SET state='dispatching',engine_epoch=?,reservation=? "
                                   "WHERE request_id=?", (self.engine_epoch, json.dumps(dict(self.manifest.request_peak)), row[0]))
        # FULL synchronous commit has completed BEFORE the caller may send bytes.
        return self.ledger.get(row[0])

    def mark_running(self, request_id: str) -> None:
        with self.ledger.transaction():
            changed = self.ledger.db.execute("UPDATE requests SET state='running' WHERE request_id=? "
                                             "AND state='dispatching' AND engine_epoch=?",
                                             (request_id, self.engine_epoch)).rowcount
            if changed != 1:
                raise AdmissionError("request was not reserved in this engine epoch")

    def mark_indeterminate(self, request_id: str, reason: str = "upstream_outcome_unknown") -> None:
        self.close_admission("upstream outcome unknown; reconciliation required")
        with self.ledger.transaction():
            changed = self.ledger.db.execute("UPDATE requests SET state='indeterminate',outcome=? "
                                             "WHERE request_id=? AND state IN ('dispatching','running')",
                                             (_identifier(reason, "outcome"), request_id)).rowcount
            if changed != 1:
                raise AdmissionError("request is not outstanding")

    def finish(self, request_id: str, evidence: Any, outcome: str = "completed") -> None:
        """Release only upon trusted affirmative proof, never elapsed time."""
        record = self.ledger.get(request_id)
        if record is None or record.state not in {"dispatching", "running", "indeterminate"}:
            raise AdmissionError("request is not outstanding")
        if self.manifest is None or self.verifier is None or record.engine_epoch is None:
            raise AdmissionClosed("terminal verifier unavailable")
        try:
            valid = self.verifier.verify_terminal(self.manifest, record.engine_epoch, request_id, evidence)
        except Exception as exc:
            self.close_admission("terminal verification failed")
            raise AdmissionClosed("terminal verification failed") from exc
        if valid is not True:
            self.close_admission("terminal proof rejected")
            raise AdmissionClosed("terminal proof rejected; reservation retained")
        with self.ledger.transaction():
            self.ledger.db.execute("UPDATE requests SET state='terminal',outcome=?,reservation='{}' "
                                   "WHERE request_id=?", (_identifier(outcome, "outcome"), request_id))

