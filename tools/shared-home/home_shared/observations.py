"""Separate Victoria observation contract store; no HA/model/owner credentials.

Mutation methods are a trusted local source-adapter interface, not a remotely
accessible memory writer. Register ReadOnlyObservationSource explicitly with the
authorized retrieval facade before using these records. This module never reads
legacy production databases or deletes media files.

Retention and tombstones run independently of inference. SQLite backups include
WAL contents through SQLite's backup API. Restored stores are persistently closed
to reads and writes until a trusted verifier attests a complete, current deletion
ledger for the exact snapshot, binding and restore challenge. No verifier ships
here, and a caller-supplied 'complete' flag cannot reopen restoration.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
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
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping, Protocol
from uuid import uuid4

from .authority import Conflict, Denied
from .memory import EvidenceReference, NormalizedMemory, SourcePermission

RETENTION_SECONDS = 30 * 24 * 60 * 60
SCHEMA = "victoria-observations-v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _time(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid {name}")
    return float(value)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceBinding:
    owner_id: str
    source_instance: str
    cameras: Mapping[str, str]
    site_id: str = "victoria"

    def __post_init__(self) -> None:
        if self.site_id != "victoria":
            raise ValueError("this store is exclusively Victoria-owned")
        _id(self.owner_id, "owner")
        _id(self.source_instance, "source")
        if not isinstance(self.cameras, Mapping) or not 1 <= len(self.cameras) <= 64:
            raise ValueError("source needs a bounded explicit camera/room registry")
        for camera, room in self.cameras.items():
            _id(camera, "camera")
            _id(room, "room")
        object.__setattr__(self, "cameras", MappingProxyType(dict(self.cameras)))

    @property
    def digest(self) -> str:
        return _digest({"site_id": self.site_id, "owner_id": self.owner_id,
                        "source_instance": self.source_instance, "cameras": dict(self.cameras)})


@dataclass(frozen=True)
class ObservationProvenance:
    """Additive provenance v1; absent fields mean unknown, never inferred.

    This version belongs to the provenance document, not the SQLite schema or
    source namespace. A trusted sink supplies clock mapping/uncertainty from the
    selected camera frame and schedule/deadline from the original adapter work.
    Model and configuration revisions come from the authenticated gateway result.
    """

    version: int = 1
    model_revision: str | None = None
    configuration_revision: str | None = None
    clock_mapping_revision: str | None = None
    uncertainty_seconds: float | None = None
    scheduled_at: float | None = None
    request_deadline: float | None = None
    valid_until: float | None = None

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("unsupported observation provenance version")
        for name in ("model_revision", "configuration_revision", "clock_mapping_revision"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not 0 < len(value) <= 512
                                      or value != value.strip() or not value.isprintable()):
                raise ValueError(f"invalid {name}")
        if self.uncertainty_seconds is not None and not 0 <= _time(self.uncertainty_seconds, "clock uncertainty") <= 1:
            raise ValueError("clock uncertainty exceeds admitted bound")
        for name in ("scheduled_at", "request_deadline", "valid_until"):
            value = getattr(self, name)
            if value is not None:
                _time(value, name)
        if (self.scheduled_at is not None and self.request_deadline is not None
                and self.request_deadline != self.scheduled_at + 15):
            raise ValueError("request provenance cannot extend the original deadline")


@dataclass(frozen=True)
class ObservationInput:
    site_id: str
    source_instance: str
    camera_id: str
    room_id: str
    producer_session: str
    producer_sequence: int
    occupancy_generation: int
    capture_session: str
    frame_id: str
    request_id: str
    observed_at: float
    received_at: float
    description: str
    observations: tuple[Mapping[str, Any], ...] = ()
    provenance: ObservationProvenance | None = None


@dataclass(frozen=True)
class StoredObservation:
    record_id: str
    original_record_id: str
    revision: int
    observation: ObservationInput
    ingested_at: float
    expires_at: float
    evidence: tuple[EvidenceReference, ...]
    invalidated: bool = False
    deleted: bool = False


@dataclass(frozen=True)
class Tombstone:
    sequence: int
    record_id: str
    revision: int
    deleted_at: float
    reason: str


@dataclass(frozen=True)
class DeletionLedger:
    binding_digest: str
    checkpoint: int
    entries: tuple[Tombstone, ...]

    @property
    def digest(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class BackupReceipt:
    path: str
    sha256: str
    binding_digest: str
    deletion_checkpoint: int
    record_count: int


@dataclass(frozen=True)
class RestoreRequest:
    version: int
    restore_id: str
    backup_sha256: str
    binding_digest: str
    ledger_sha256: str
    deletion_checkpoint: int


@dataclass(frozen=True)
class VerifiedRestoreReceipt:
    receipt_id: str
    request_digest: str
    verified_at: float
    expires_at: float


class DeletionReplayVerifier(Protocol):
    """Trusted maintenance dependency, not supplied by an end-user request.

    Verify authenticity, completeness AND currentness against the authoritative
    deletion source. Merely recomputing the supplied ledger hash is insufficient.
    Also validate restore authorization and bind the receipt to request.digest.
    """

    def verify_restore(self, request: RestoreRequest, evidence: Any) -> VerifiedRestoreReceipt: ...


class VictoriaObservationStore:
    def __init__(self, path: str | Path, binding: SourceBinding, *,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path).resolve()
        if self.path.name.startswith(".observation-restore-"):
            raise Denied("unpublished restoration artifacts cannot be opened")
        self.binding = binding
        self.clock = clock
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported Victoria observation schema")
            # Never silently attach to an unrelated existing SQLite database.
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "observation_metadata" not in tables:
                raise ValueError("database is not a Victoria observation store")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS observation_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS observation_record(
                record_id TEXT PRIMARY KEY, original_record_id TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL, input_digest TEXT NOT NULL, payload TEXT NOT NULL,
                ingested_at REAL NOT NULL, expires_at REAL NOT NULL,
                invalidated INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
              CREATE INDEX IF NOT EXISTS observation_expiry ON observation_record(expires_at,deleted);
              CREATE TABLE IF NOT EXISTS observation_revision(
                record_id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT,
                digest TEXT NOT NULL,PRIMARY KEY(record_id,revision));
              CREATE TABLE IF NOT EXISTS observation_dependency(
                parent_id TEXT NOT NULL,child_id TEXT NOT NULL,parent_revision INTEGER NOT NULL,
                PRIMARY KEY(parent_id,child_id));
              CREATE TABLE IF NOT EXISTS observation_tombstone(
                sequence INTEGER PRIMARY KEY,record_id TEXT UNIQUE NOT NULL,
                revision INTEGER NOT NULL,deleted_at REAL NOT NULL,reason TEXT NOT NULL);
            """)
            with self._transaction():
                row = self.db.execute("SELECT value FROM observation_metadata WHERE key='binding'").fetchone()
                if row is not None and row[0] != binding.digest:
                    raise Denied("store owner/site/source binding differs")
                self.db.execute("INSERT OR IGNORE INTO observation_metadata VALUES ('binding',?)", (binding.digest,))
                self.db.execute("PRAGMA user_version=1")
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def _ready(self) -> None:
        if self.db.execute("SELECT 1 FROM observation_metadata WHERE key='restore_pending'").fetchone():
            raise Denied("restored observations await verified deletion-ledger replay")

    def _now(self) -> float:
        # Keep retention expiry irreversible across wall-clock rollback and
        # process restart, even if the independent janitor has not run yet.
        now = _time(self.clock(), "clock")
        self.db.execute("""INSERT INTO observation_metadata VALUES ('clock_floor',?)
            ON CONFLICT(key) DO UPDATE SET value=max(CAST(value AS REAL),CAST(excluded.value AS REAL))""", (str(now),))
        return float(self.db.execute("SELECT value FROM observation_metadata WHERE key='clock_floor'").fetchone()[0])

    def _validate_input(self, value: ObservationInput) -> None:
        if not isinstance(value, ObservationInput):
            raise ValueError("typed observation input required")
        if (value.site_id, value.source_instance) != ("victoria", self.binding.source_instance):
            raise Denied("source or site does not match the provisioned binding")
        if self.binding.cameras.get(value.camera_id) != value.room_id:
            raise Denied("camera/room pair is not registered")
        for field in ("camera_id", "room_id", "producer_session", "capture_session", "frame_id", "request_id"):
            _id(getattr(value, field), field)
        for field in ("producer_sequence", "occupancy_generation"):
            number = getattr(value, field)
            if type(number) is not int or not 0 <= number < 2**63:
                raise ValueError(f"invalid {field}")
        observed = _time(value.observed_at, "observation time")
        received = _time(value.received_at, "receive time")
        now = self._now()
        if observed > now+1 or received > now+1 or received < observed-1:
            raise ValueError("inconsistent observation times")
        if now >= observed + RETENTION_SECONDS:
            raise Denied("observation is already beyond retention")
        _validate_provenance(value)
        _validate_content(value.description, value.observations)

    def _identity(self, value: ObservationInput) -> tuple[str, str]:
        # Hash the tuple: delimiters inside producer IDs cannot cause collisions.
        original = _digest([value.camera_id, value.capture_session, value.frame_id])
        return f"victoria/{self.binding.source_instance}/{original}", original

    def _check_record_id(self, record_id: str) -> None:
        prefix = f"victoria/{self.binding.source_instance}/"
        if not isinstance(record_id, str) or not record_id.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", record_id[len(prefix):]):
            raise Denied("record is outside the provisioned source namespace")

    def ingest(self, value: ObservationInput, *, expected_revision: int = 0,
               derives_from: tuple[str, ...] = ()) -> StoredObservation:
        """Create immutable frame identity or replay its identical original input.

        Source revisions use revise(); caller data cannot change the identity,
        retention anchor, owner, capture metadata or source registration.
        """
        self._validate_input(value)
        if type(expected_revision) is not int or expected_revision != 0:
            raise Conflict("creation requires expected revision zero")
        if not isinstance(derives_from, tuple) or len(derives_from) > 32 or len(set(derives_from)) != len(derives_from):
            raise ValueError("bounded unique parent records required")
        record_id, original = self._identity(value)
        # Older v1 records predate provenance. Keep their original input digest
        # stable when replayed with the new optional field still unknown.
        digest = _digest({"input": _input_document(value), "parents": derives_from})
        with self._transaction():
            self._ready()
            if self.db.execute("SELECT 1 FROM observation_tombstone WHERE record_id=?", (record_id,)).fetchone():
                raise Denied("forgotten observation cannot be recreated")
            old = self.db.execute("SELECT * FROM observation_record WHERE record_id=?", (record_id,)).fetchone()
            if old is not None:
                if old["input_digest"] != digest:
                    raise Conflict("frame identity already has different input")
                return self._available(old)
            expiry = value.observed_at + RETENTION_SECONDS
            parents = [self._read(parent) for parent in derives_from]
            evidence = tuple(EvidenceReference("victoria", self.binding.source_instance,
                                               parent.original_record_id, parent.revision) for parent in parents)
            for parent in parents:
                expiry = min(expiry, parent.expires_at)
            record = StoredObservation(record_id, original, 1, value, self._now(), expiry, evidence)
            payload = _json(asdict(record))
            self.db.execute("INSERT INTO observation_record VALUES (?,?,?,?,?,?,?,0,0)",
                (record_id, original, 1, digest, payload, record.ingested_at, expiry))
            self.db.execute("INSERT INTO observation_revision VALUES (?,?,?,?)", (record_id, 1, payload, _digest(asdict(record))))
            for parent in parents:
                self.db.execute("INSERT INTO observation_dependency VALUES (?,?,?)", (parent.record_id, record_id, parent.revision))
            return record

    def _available(self, row: sqlite3.Row) -> StoredObservation:
        if row["deleted"] or row["invalidated"] or row["expires_at"] <= self._now():
            raise Denied("observation is deleted, invalidated or expired")
        return _decode(row["payload"])

    def _read(self, record_id: str) -> StoredObservation:
        self._check_record_id(record_id)
        row = self.db.execute("SELECT * FROM observation_record WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return self._available(row)

    def read(self, record_id: str) -> StoredObservation:
        with self._lock:
            self._ready()
            return self._read(record_id)

    def revise(self, record_id: str, *, expected_revision: int,
               description: str, observations: tuple[Mapping[str, Any], ...] = ()) -> StoredObservation:
        """Trusted source correction; this never asserts owner-confirmed truth."""
        _validate_content(description, observations)
        with self._transaction():
            self._ready()
            old = self._read(record_id)
            if type(expected_revision) is not int or old.revision != expected_revision:
                raise Conflict("observation revision changed")
            updated = StoredObservation(old.record_id, old.original_record_id, old.revision+1,
                replace(old.observation, description=description, observations=observations),
                old.ingested_at, old.expires_at, old.evidence)
            payload = _json(asdict(updated))
            self.db.execute("UPDATE observation_record SET revision=?,payload=? WHERE record_id=? AND revision=?",
                            (updated.revision, payload, record_id, expected_revision))
            self.db.execute("INSERT INTO observation_revision VALUES (?,?,?,?)",
                            (record_id, updated.revision, payload, _digest(asdict(updated))))
            self._invalidate_descendants(record_id)
            return updated

    def _invalidate_descendants(self, record_id: str) -> None:
        rows = self.db.execute("""WITH RECURSIVE affected(id) AS (
            SELECT child_id FROM observation_dependency WHERE parent_id=? UNION
            SELECT d.child_id FROM observation_dependency d JOIN affected a ON d.parent_id=a.id)
            SELECT id FROM affected""", (record_id,)).fetchall()
        for row in rows:
            self.db.execute("UPDATE observation_record SET invalidated=1,payload='{}',revision=revision+1 WHERE record_id=? AND invalidated=0", (row[0],))
            self.db.execute("UPDATE observation_revision SET payload=NULL WHERE record_id=?", (row[0],))

    def _delete(self, record_id: str, revision: int, reason: str, deleted_at: float) -> None:
        self.db.execute("UPDATE observation_record SET deleted=1,invalidated=1,payload='{}',revision=? WHERE record_id=?", (revision, record_id))
        self.db.execute("UPDATE observation_revision SET payload=NULL WHERE record_id=?", (record_id,))
        self._invalidate_descendants(record_id)

    def _tombstone(self, record_id: str, revision: int, reason: str) -> None:
        now = self._now()
        self.db.execute("INSERT INTO observation_tombstone(record_id,revision,deleted_at,reason) VALUES (?,?,?,?)", (record_id, revision, now, reason))
        self._delete(record_id, revision, reason, now)

    def forget(self, record_id: str, *, expected_revision: int) -> None:
        with self._transaction():
            self._ready()
            self._check_record_id(record_id)
            row = self.db.execute("SELECT revision,deleted FROM observation_record WHERE record_id=?", (record_id,)).fetchone()
            if row is None:
                raise KeyError(record_id)
            if type(expected_revision) is not int or row[0] != expected_revision or row[1]:
                raise Conflict("observation revision changed or already deleted")
            self._tombstone(record_id, expected_revision+1, "forgotten")

    def prune(self, *, batch_size: int = 1000) -> int:
        """Independent janitor; read expiry is enforced even if this never runs."""
        if type(batch_size) is not int or not 1 <= batch_size <= 10000:
            raise ValueError("bounded pruning batch required")
        with self._transaction():
            self._ready()
            rows = self.db.execute("SELECT record_id,revision FROM observation_record WHERE deleted=0 AND expires_at<=? ORDER BY expires_at LIMIT ?", (self._now(), batch_size)).fetchall()
            for row in rows:
                # Earlier parent deletion may have incremented a child's revision.
                revision = self.db.execute("SELECT revision FROM observation_record WHERE record_id=?", (row[0],)).fetchone()[0]
                self._tombstone(row[0], revision+1, "retention_expired")
            return len(rows)

    def retrieve(self, query: str) -> tuple[NormalizedMemory, ...]:
        if not isinstance(query, str) or not 0 < len(query) <= 4096:
            raise ValueError("bounded literal query required")
        with self._lock:
            self._ready()
            # Search is a literal substring, never SQL or a model instruction.
            rows = self.db.execute("SELECT * FROM observation_record WHERE deleted=0 AND invalidated=0 AND expires_at>? AND instr(lower(payload),lower(?))>0 ORDER BY ingested_at DESC,record_id LIMIT 100", (self._now(), query)).fetchall()
            return tuple(self._normalize(self._available(row)) for row in rows)

    def _normalize(self, record: StoredObservation) -> NormalizedMemory:
        value = record.observation
        return NormalizedMemory(1, record.record_id, record.original_record_id,
            self.binding.source_instance, SCHEMA, self.binding.owner_id, "victoria",
            "observation", value.description, "model_caption" if record.revision == 1 else "source_revision",
            record.revision, record.evidence,
            (SourcePermission("victoria", self.binding.source_instance),),
            record.ingested_at, record.ingested_at, camera_id=value.camera_id, room_id=value.room_id,
            observed_from=value.observed_at, observed_until=value.observed_at,
            ingested_at=record.ingested_at, retention_expires_at=record.expires_at)

    def deletion_ledger(self) -> DeletionLedger:
        with self._lock:
            self._ready()
            rows = self.db.execute("SELECT * FROM observation_tombstone ORDER BY sequence").fetchall()
            entries = tuple(Tombstone(**dict(row)) for row in rows)
            return DeletionLedger(self.binding.digest, entries[-1].sequence if entries else 0, entries)

    def backup(self, destination: str | Path) -> BackupReceipt:
        target = Path(destination).resolve()
        with self._lock:
            self._ready()
            # Exclusive create prevents overwriting an existing artifact or store.
            with target.open("xb"):
                pass
            copied = sqlite3.connect(target)
            try:
                self.db.backup(copied)
                if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("backup consistency check failed")
                checkpoint = copied.execute("SELECT coalesce(max(sequence),0) FROM observation_tombstone").fetchone()[0]
                count = copied.execute("SELECT count(*) FROM observation_record WHERE deleted=0").fetchone()[0]
                # Make the returned digest describe a self-contained closed file.
                copied.execute("PRAGMA journal_mode=DELETE")
            finally:
                copied.close()
            return BackupReceipt(str(target), _file_digest(target), self.binding.digest, checkpoint, count)

    @classmethod
    def restore_from_backup(cls, backup: str | Path, destination: str | Path,
                            binding: SourceBinding, *, clock: Callable[[], float] = time.time) -> "VictoriaObservationStore":
        source_path = Path(backup).resolve()
        target = Path(destination).resolve()
        # backup() produces a self-contained, closed SQLite file. Copy its exact
        # bytes before mutation so the verifier can match the backup manifest's
        # hash. A different/corrupt/stale image cannot pass an exact digest proof.
        staging = target.with_name(".observation-restore-"+str(uuid4())+".sqlite")
        try:
            with source_path.open("rb") as source, staging.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024*1024)
            digest = _file_digest(staging)
            copied = sqlite3.connect(staging)
            try:
                if copied.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("unsupported observation backup")
                if copied.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("corrupt observation backup")
                copied.execute("PRAGMA synchronous=FULL")
                # Write the durable closed gate before publishing the destination.
                with copied:
                    copied.execute("INSERT OR REPLACE INTO observation_metadata VALUES ('restore_pending',?)", (str(uuid4()),))
                    copied.execute("INSERT OR REPLACE INTO observation_metadata VALUES ('restore_backup_sha256',?)", (digest,))
                    checkpoint = copied.execute("SELECT coalesce(max(sequence),0) FROM observation_tombstone").fetchone()[0]
                    copied.execute("INSERT OR REPLACE INTO observation_metadata VALUES ('restore_checkpoint',?)", (str(checkpoint),))
                copied.execute("PRAGMA journal_mode=DELETE")
            finally:
                copied.close()
            with staging.open("r+b") as durable:
                os.fsync(durable.fileno())
            # Same-directory hard-link publication is atomic and refuses an
            # existing destination on both Windows and POSIX. No overwrite
            # rename fallback is allowed. A crash leaves absent or gated data.
            os.link(staging, target)
        finally:
            # Only the staging SQLite file created by this call is removed.
            # Leftovers from a process crash are never accepted by __init__.
            if staging.exists():
                staging.unlink()
        return cls(target, binding, clock=clock)

    def replay_deletion_ledger(self, ledger: DeletionLedger, *, evidence: Any = None,
                              verifier: DeletionReplayVerifier | None = None) -> None:
        with self._lock:
            metadata = dict(self.db.execute("SELECT key,value FROM observation_metadata"))
            if "restore_pending" not in metadata:
                raise Denied("deletion replay requires a closed restore")
            if verifier is None:
                raise Denied("trusted deletion replay verifier is not installed")
            self._validate_ledger(ledger, int(metadata["restore_checkpoint"]))
            request = RestoreRequest(1, metadata["restore_pending"], metadata["restore_backup_sha256"],
                                     self.binding.digest, ledger.digest, ledger.checkpoint)
            receipt = verifier.verify_restore(request, evidence)
            now = self._now()
            if (not isinstance(receipt, VerifiedRestoreReceipt) or not receipt.receipt_id
                    or receipt.request_digest != _digest(asdict(request))
                    or _time(receipt.verified_at, "receipt time") > now
                    or now - receipt.verified_at > 60
                    or _time(receipt.expires_at, "receipt expiry") <= now):
                raise Denied("restore receipt does not attest this exact replay")
            with self._transaction():
                for entry in ledger.entries:
                    old = self.db.execute("SELECT * FROM observation_tombstone WHERE record_id=?", (entry.record_id,)).fetchone()
                    if old is not None and tuple(old) != (entry.sequence, entry.record_id, entry.revision, entry.deleted_at, entry.reason):
                        raise Denied("deletion ledger contradicts snapshot history")
                    if old is None:
                        self.db.execute("INSERT INTO observation_tombstone VALUES (?,?,?,?,?)", (entry.sequence, entry.record_id, entry.revision, entry.deleted_at, entry.reason))
                    self._delete(entry.record_id, entry.revision, entry.reason, entry.deleted_at)
                self.db.execute("INSERT OR REPLACE INTO observation_metadata VALUES ('restore_receipt',?)", (receipt.receipt_id,))
                self.db.execute("DELETE FROM observation_metadata WHERE key IN ('restore_pending','restore_backup_sha256','restore_checkpoint')")

    def _validate_ledger(self, ledger: DeletionLedger, minimum_checkpoint: int) -> None:
        if not isinstance(ledger, DeletionLedger) or ledger.binding_digest != self.binding.digest:
            raise Denied("deletion ledger belongs to another source")
        if type(ledger.checkpoint) is not int or ledger.checkpoint < minimum_checkpoint:
            raise Denied("deletion ledger precedes the snapshot")
        if not isinstance(ledger.entries, tuple) or len(ledger.entries) != ledger.checkpoint:
            raise Denied("complete contiguous deletion history is required")
        seen = set()
        for sequence, entry in enumerate(ledger.entries, 1):
            if not isinstance(entry, Tombstone) or entry.sequence != sequence:
                raise Denied("deletion ledger sequence gap")
            self._check_record_id(entry.record_id)
            if entry.record_id in seen or type(entry.revision) is not int or entry.revision < 2:
                raise Denied("duplicate or invalid tombstone")
            seen.add(entry.record_id)
            _time(entry.deleted_at, "deletion time")
            if entry.deleted_at > self._now()+1:
                raise Denied("tombstone is from the future")
            if entry.reason not in {"forgotten", "retention_expired"}:
                raise Denied("unknown deletion reason")
            record = self.db.execute("SELECT revision,deleted FROM observation_record WHERE record_id=?", (entry.record_id,)).fetchone()
            if record is not None and entry.revision <= record[0] and not (record[1] and entry.revision == record[0]):
                raise Denied("tombstone cannot precede the restored record revision")
        existing = {row[0] for row in self.db.execute("SELECT record_id FROM observation_tombstone")}
        if not existing <= seen:
            raise Denied("deletion replay omits existing tombstones")


class ReadOnlyObservationSource:
    """Read adapter only. Registration/owner/source grants remain external gates."""

    def __init__(self, store: VictoriaObservationStore):
        self._store = store

    def retrieve(self, query: str) -> tuple[NormalizedMemory, ...]:
        return self._store.retrieve(query)


def _validate_content(description: str, observations: tuple[Mapping[str, Any], ...]) -> None:
    if not isinstance(description, str) or not 0 < len(description) <= 4096:
        raise ValueError("bounded nonempty observation description required")
    if not isinstance(observations, tuple) or len(observations) > 100 or any(not isinstance(item, dict) for item in observations):
        raise ValueError("bounded structured observations required")
    if len(_json(observations).encode()) > 64 * 1024:
        raise ValueError("structured observations exceed size limit")


def _validate_provenance(value: ObservationInput) -> None:
    provenance = value.provenance
    if provenance is None:
        return
    if not isinstance(provenance, ObservationProvenance):
        raise ValueError("typed versioned provenance required")
    if provenance.valid_until is not None and provenance.valid_until != value.observed_at + 180:
        raise ValueError("observation provenance cannot extend validity")
    if provenance.request_deadline is not None and value.received_at >= provenance.request_deadline:
        raise ValueError("accepted observation arrived after its request deadline")


def _input_document(value: ObservationInput) -> dict:
    document = asdict(value)
    if value.provenance is None:
        document.pop("provenance")
    return document


def _decode(payload: str) -> StoredObservation:
    value = json.loads(payload)
    observation = value["observation"]
    observation["observations"] = tuple(observation["observations"])
    if observation.get("provenance") is not None:
        if not isinstance(observation["provenance"], dict):
            raise ValueError("invalid stored provenance document")
        observation["provenance"] = ObservationProvenance(**observation["provenance"])
    value["observation"] = ObservationInput(**observation)
    _validate_provenance(value["observation"])
    value["evidence"] = tuple(EvidenceReference(**item) for item in value["evidence"])
    return StoredObservation(**value)
