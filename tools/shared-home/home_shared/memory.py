"""Versioned personal-memory contract harness and authorized source federation.

The SQLite implementation below is a staging contract store, not the governed
Core database or a production privacy/deletion implementation. Mutation is closed
unless a trusted governance adapter supplies an operation-bound receipt. A real
Core adapter, schema migration, protected transaction, retention/erasure handling
and pinned integration acceptance remain required before production use.

All returned content is untrusted evidence. Nothing in this module can produce
identity grants, action envelopes, location authority or executable policies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Callable, Protocol
from uuid import uuid4

from .authority import Conflict, Denied, IdentityRegistry, Session, SITES, _digest, _time


@dataclass(frozen=True)
class SourcePermission:
    site_id: str
    source: str

    def __post_init__(self) -> None:
        if self.site_id not in SITES or not self.source or self.source == "*":
            raise ValueError("source permission requires an exact site and source")


@dataclass(frozen=True)
class EvidenceReference:
    site_id: str
    source: str
    original_record_id: str
    revision: int

    def __post_init__(self) -> None:
        SourcePermission(self.site_id, self.source)
        if not self.original_record_id or type(self.revision) is not int or self.revision < 1:
            raise ValueError("immutable evidence record and revision are required")


@dataclass(frozen=True)
class NormalizedMemory:
    version: int
    record_id: str
    original_record_id: str
    source_instance: str
    source_schema: str
    owner_id: str
    site_id: str | None
    kind: str
    content: str
    trust: str
    revision: int
    evidence: tuple[EvidenceReference, ...]
    required_sources: tuple[SourcePermission, ...]
    created_at: float
    updated_at: float
    invalidated: bool = False
    deleted: bool = False
    # Protected location/relationship records stay in their specialized systems.
    authority: str = "untrusted_evidence"
    camera_id: str | None = None
    room_id: str | None = None
    observed_from: float | None = None
    observed_until: float | None = None
    ingested_at: float | None = None
    retention_expires_at: float | None = None


@dataclass(frozen=True)
class MutationRequest:
    version: int
    operation_id: str
    operation: str
    owner_id: str
    record_id: str
    expected_revision: int
    payload_digest: str
    upstream_session_id: str


@dataclass(frozen=True)
class GovernanceReceipt:
    receipt_id: str
    operation_digest: str
    owner_id: str
    expires_at: float
    policy_revision: str


class GovernedMemoryBackend(Protocol):
    """Trusted dependency; no request can choose the implementation.

    Validate the live governed owner/privacy gates and the entire proposed record,
    including sensitive-place/relationship content and provenance. Confirmation
    requires an authenticated owner gesture bound to the exact candidate revision.
    An adapter must not replace those checks with a caller 'confirmed' boolean.
    """

    def authorize_mutation(self, request: MutationRequest,
                           proposed: NormalizedMemory, credential: str) -> GovernanceReceipt: ...


class StagingMemoryStore:
    """Staging mirror for exercising the personal-memory v1 contract."""

    def __init__(self, path: str | Path, authority: IdentityRegistry, *,
                 backend: GovernedMemoryBackend | None = None,
                 storage_site: str = "echo"):
        if storage_site not in SITES:
            raise ValueError("unknown storage site")
        self.authority = authority
        self.backend = backend
        self.storage_site = storage_site
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS personal_memory_contract(
            record_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            revision INTEGER NOT NULL, payload TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS memory_mutation_receipt(
            receipt_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
            digest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS memory_derivation(
            parent_id TEXT NOT NULL, child_id TEXT NOT NULL,
            PRIMARY KEY(parent_id,child_id));
          CREATE TABLE IF NOT EXISTS memory_tombstone(
            record_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            revision INTEGER NOT NULL, deleted_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS memory_learning_event(
            event_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
            record_id TEXT NOT NULL, kind TEXT NOT NULL,
            evidence_digest TEXT NOT NULL, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS memory_maintenance(
            key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # Any restored mirror must first reapply an authoritative tombstone set.
        self._restoration_pending = self.db.execute(
            "SELECT 1 FROM memory_maintenance WHERE key='restore_pending'").fetchone() is not None

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.db.close()

    def _restore_pending(self) -> bool:
        # A different maintenance handle may have closed this database after
        # this instance opened it. Never rely on an instance-local cached flag.
        durable = self.db.execute(
            "SELECT 1 FROM memory_maintenance WHERE key='restore_pending'").fetchone() is not None
        self._restoration_pending = self._restoration_pending or durable
        return self._restoration_pending

    def _ensure_available(self) -> None:
        if self._restore_pending():
            raise Denied("restored data awaits authoritative deletion replay")

    def _write_lease(self, session: Session):
        self._ensure_available()
        if self.backend is None:
            raise Denied("governed personal-memory adapter is not installed")
        return self.authority.authorize(session, self.storage_site, "personal-memory", "personal_memory.write")

    def _source_leases(self, session: Session, record: NormalizedMemory):
        permissions = _record_permissions(record)
        return tuple(self.authority.authorize(session, source.site_id, source.source, "memory.read")
                     for source in sorted(permissions, key=lambda value: (value.site_id, value.source)))

    def _receipt(self, session: Session, operation: str, record: NormalizedMemory,
                 expected_revision: int, credential: str, operation_id: str):
        lease = self._write_lease(session)
        if record.owner_id != lease.owner_id:
            raise Denied("memory belongs to another governed owner")
        request = MutationRequest(1, operation_id, operation, lease.owner_id,
                                  record.record_id, expected_revision,
                                  _digest(asdict(record)), session.evidence.upstream_session_id)
        if not operation_id:
            raise ValueError("operation ID is required")
        receipt = self.backend.authorize_mutation(request, record, credential)
        if (receipt.operation_digest != _digest(asdict(request))
                or receipt.owner_id != lease.owner_id or not receipt.receipt_id
                or not receipt.policy_revision or _time(receipt.expires_at) <= self.authority.clock()):
            raise Denied("governance receipt does not match the exact mutation")
        self.authority.check_lease(lease)
        return lease, request, receipt

    def _record_receipt(self, request: MutationRequest, receipt: GovernanceReceipt) -> None:
        try:
            self.db.execute("INSERT INTO memory_mutation_receipt VALUES (?,?,?)",
                            (receipt.receipt_id, request.operation_id, _digest(asdict(request))))
        except sqlite3.IntegrityError as exc:
            raise Conflict("governed mutation receipt already consumed") from exc

    def _raw(self, record_id: str) -> NormalizedMemory:
        row = self.db.execute("SELECT payload FROM personal_memory_contract WHERE record_id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return _decode(row[0])

    def _save(self, record: NormalizedMemory, expected_revision: int) -> None:
        if expected_revision == 0:
            try:
                self.db.execute("INSERT INTO personal_memory_contract VALUES (?,?,?,?,?)",
                                (record.record_id, record.owner_id, record.revision, _encode(record), int(record.deleted)))
            except sqlite3.IntegrityError as exc:
                raise Conflict("record ID already exists") from exc
        else:
            result = self.db.execute("UPDATE personal_memory_contract SET revision=?,payload=?,deleted=? WHERE record_id=? AND revision=? AND deleted=0",
                (record.revision, _encode(record), int(record.deleted), record.record_id, expected_revision))
            if result.rowcount != 1:
                raise Conflict("memory revision changed or record was forgotten")

    def propose(self, session: Session, *, kind: str, content: str,
                evidence: tuple[EvidenceReference, ...], credential: str,
                operation_id: str, site_id: str | None = None,
                derives_from: tuple[str, ...] = ()) -> NormalizedMemory:
        self._write_lease(session)
        if kind not in {"personal_preference", "home_fact"}:
            raise Denied("unsupported generic memory kind")
        if (kind == "home_fact" and site_id not in SITES) or (kind == "personal_preference" and site_id is not None):
            raise Denied("memory scope does not match its kind")
        _content(content)
        owner = self.authority.owner(session)
        now = self.authority.clock()
        original_id = str(uuid4())
        requirements = {SourcePermission(self.storage_site, "personal-memory")}
        requirements.update(SourcePermission(item.site_id, item.source) for item in evidence)
        if site_id:
            requirements.add(SourcePermission(site_id, "personal-memory"))
        parents: list[NormalizedMemory] = []
        bound_evidence = list(evidence)
        for parent_id in derives_from:
            parent = self.read(session, parent_id)
            if parent.deleted or parent.invalidated:
                raise Denied("cannot derive from unavailable evidence")
            requirements.update(parent.required_sources)
            parents.append(parent)
            bound_evidence.append(EvidenceReference(self.storage_site, "personal-memory",
                                                   parent.original_record_id, parent.revision))
        record = NormalizedMemory(1, f"{self.storage_site}/personal-memory/{original_id}",
            original_id, self.storage_site + "/personal-memory", "personal-memory-v1",
            owner, site_id, kind, content, "candidate", 1, tuple(bound_evidence),
            tuple(sorted(requirements, key=lambda x: (x.site_id, x.source))), now, now)
        record = replace(record, ingested_at=now)
        source_leases = self._source_leases(session, record)
        lease, request, receipt = self._receipt(session, "propose", record, 0, credential, operation_id)
        with self._transaction():
            self._ensure_available()
            self.authority.check_lease(lease)
            for source_lease in source_leases:
                self.authority.check_lease(source_lease)
            for parent in parents:
                current = self._raw(parent.record_id)
                if current.revision != parent.revision or current.deleted or current.invalidated:
                    raise Conflict("derived evidence changed before mutation committed")
            self._record_receipt(request, receipt)
            self._save(record, 0)
            for parent_id in derives_from:
                self.db.execute("INSERT INTO memory_derivation VALUES (?,?)", (parent_id, record.record_id))
            self._learning(record, "interpretation", _digest([asdict(item) for item in bound_evidence]))
        return record

    def read(self, session: Session, record_id: str) -> NormalizedMemory:
        self._ensure_available()
        owner = self.authority.owner(session)
        record = self._raw(record_id)
        if record.owner_id != owner:
            raise Denied("memory belongs to another owner")
        if record.deleted or self.db.execute("SELECT 1 FROM memory_tombstone WHERE record_id=?", (record_id,)).fetchone():
            raise Denied("memory was forgotten")
        if record.invalidated:
            raise Denied("derived memory requires recomputation")
        leases = self._source_leases(session, record)
        for lease in leases:
            self.authority.check_lease(lease)
        self._ensure_available()
        return record

    def confirm(self, session: Session, record_id: str, *, expected_revision: int,
                credential: str, operation_id: str) -> NormalizedMemory:
        old = self.read(session, record_id)
        if old.trust != "candidate":
            raise Conflict("only a candidate can be confirmed")
        record = replace(old, trust="confirmed", revision=old.revision+1,
                         updated_at=self.authority.clock())
        return self._mutate(session, old, record, "confirm", expected_revision, credential, operation_id)

    def correct(self, session: Session, record_id: str, *, content: str,
                expected_revision: int, credential: str, operation_id: str) -> NormalizedMemory:
        _content(content)
        old = self.read(session, record_id)
        record = replace(old, content=content, revision=old.revision+1,
                         updated_at=self.authority.clock())
        return self._mutate(session, old, record, "correct", expected_revision, credential, operation_id)

    def _mutate(self, session: Session, old: NormalizedMemory, record: NormalizedMemory,
                operation: str, expected_revision: int, credential: str,
                operation_id: str) -> NormalizedMemory:
        if type(expected_revision) is not int or old.revision != expected_revision:
            raise Conflict("expected revision does not match")
        source_leases = self._source_leases(session, old)
        lease, request, receipt = self._receipt(session, operation, record, expected_revision, credential, operation_id)
        with self._transaction():
            self._ensure_available()
            self.authority.check_lease(lease)
            for source_lease in source_leases:
                self.authority.check_lease(source_lease)
            self._record_receipt(request, receipt)
            self._save(record, expected_revision)
            self._invalidate_descendants(record.record_id)
            self._learning(record, "owner_confirmation" if operation == "confirm" else "owner_correction", request.payload_digest)
        return record

    def forget(self, session: Session, record_id: str, *, expected_revision: int,
               credential: str, operation_id: str) -> None:
        # Owners retain the ability to delete their record after source unlinking.
        old = self._raw(record_id)
        if old.owner_id != self.authority.owner(session):
            raise Denied("memory belongs to another owner")
        if type(expected_revision) is not int or old.revision != expected_revision or old.deleted:
            raise Conflict("expected revision does not match")
        record = replace(old, content="", evidence=(), trust="deleted", deleted=True,
                         invalidated=True, revision=old.revision+1,
                         updated_at=self.authority.clock())
        lease, request, receipt = self._receipt(session, "forget", record, expected_revision, credential, operation_id)
        with self._transaction():
            self._ensure_available()
            self.authority.check_lease(lease)
            self._record_receipt(request, receipt)
            self._save(record, expected_revision)
            self.db.execute("INSERT INTO memory_tombstone VALUES (?,?,?,?)",
                (record.record_id, record.owner_id, record.revision, record.updated_at))
            self._invalidate_descendants(record.record_id)
            self.db.execute("DELETE FROM memory_learning_event WHERE record_id=?", (record.record_id,))

    def _invalidate_descendants(self, record_id: str) -> None:
        descendants = self.db.execute("""WITH RECURSIVE affected(record_id) AS (
          SELECT child_id FROM memory_derivation WHERE parent_id=?
          UNION SELECT d.child_id FROM memory_derivation d JOIN affected a ON d.parent_id=a.record_id)
          SELECT record_id FROM affected""", (record_id,)).fetchall()
        for row in descendants:
            old = self._raw(row[0])
            record = replace(old, invalidated=True, content="", evidence=(),
                             revision=old.revision+1,
                             updated_at=self.authority.clock())
            self.db.execute("UPDATE personal_memory_contract SET payload=?,revision=? WHERE record_id=?", (_encode(record), record.revision, row[0]))
            self.db.execute("DELETE FROM memory_learning_event WHERE record_id=?", (row[0],))

    def _learning(self, record: NormalizedMemory, kind: str, digest: str) -> None:
        self.db.execute("INSERT INTO memory_learning_event VALUES (?,?,?,?,?,?)",
            (str(uuid4()), record.owner_id, record.record_id, kind, digest, self.authority.clock()))

    def record_outcome(self, session: Session, record_id: str, *, kind: str,
                       evidence_digest: str, credential: str, operation_id: str) -> None:
        if kind not in {"proposed_action", "actual_outcome"}:
            raise ValueError("unsupported learning event")
        if len(evidence_digest) != 64 or any(c not in "0123456789abcdef" for c in evidence_digest):
            raise ValueError("learning event needs an immutable evidence digest")
        record = self.read(session, record_id)
        source_leases = self._source_leases(session, record)
        # Bind outcome content into the exact governance receipt too.
        receipt_record = replace(record, content=json.dumps({"event": kind, "digest": evidence_digest}))
        lease, request, receipt = self._receipt(session, kind, receipt_record, record.revision, credential, operation_id)
        with self._transaction():
            self._ensure_available()
            self.authority.check_lease(lease)
            for source_lease in source_leases:
                self.authority.check_lease(source_lease)
            current = self._raw(record_id)
            if current.revision != record.revision or current.deleted or current.invalidated:
                raise Conflict("learning record changed before outcome commit")
            self._record_receipt(request, receipt)
            self._learning(record, kind, evidence_digest)

    def begin_restore(self) -> None:
        """Call before attaching/importing a restored snapshot, never after reads."""
        self._restoration_pending = True
        with self._transaction():
            self.db.execute("INSERT OR REPLACE INTO memory_maintenance VALUES ('restore_pending','1')")

    def replay_tombstones(self, tombstones: tuple[tuple[str, str, int, float], ...]) -> None:
        """Trusted maintenance boundary; input must come from the governed ledger.

        This test-store helper does not certify backup completeness or replace the
        existing production erasure-ledger restore gate.
        """
        if not self._restore_pending():
            raise Denied("restore replay requires admission-closed maintenance")
        with self._transaction():
            for record_id, owner_id, revision, deleted_at in tombstones:
                self.db.execute("INSERT INTO memory_tombstone VALUES (?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET revision=max(revision,excluded.revision)", (record_id, owner_id, revision, deleted_at))
                row = self.db.execute("SELECT payload FROM personal_memory_contract WHERE record_id=?", (record_id,)).fetchone()
                if row is not None:
                    old = _decode(row[0])
                    if old.owner_id != owner_id:
                        raise Denied("restored tombstone owner mismatch")
                    record = replace(old, content="", evidence=(), deleted=True, invalidated=True, trust="deleted", revision=max(old.revision, revision))
                    self.db.execute("UPDATE personal_memory_contract SET payload=?,deleted=1,revision=? WHERE record_id=?", (_encode(record), record.revision, record_id))
                    self._invalidate_descendants(record_id)
                    self.db.execute("DELETE FROM memory_learning_event WHERE record_id=?", (record_id,))
        # Remains closed. Only a trusted restore-verification adapter may reopen it.


@dataclass(frozen=True)
class SourceRegistration:
    site_id: str
    source_instance: str
    source_schema: str
    permission_source: str


class MemorySource(Protocol):
    def retrieve(self, query: str) -> tuple[NormalizedMemory, ...]: ...


class AuthorizedRetriever:
    """Federation over explicitly registered adapters, with delivery revalidation."""

    def __init__(self, authority: IdentityRegistry):
        self.authority = authority
        self._sources: dict[tuple[str, str], tuple[SourceRegistration, MemorySource]] = {}

    def register(self, registration: SourceRegistration, source: MemorySource) -> None:
        SourcePermission(registration.site_id, registration.permission_source)
        key = (registration.site_id, registration.source_instance)
        if key in self._sources or not registration.source_schema or not registration.source_instance:
            raise ValueError("source registration is missing or duplicated")
        self._sources[key] = (registration, source)

    def retrieve(self, session: Session, site_id: str, source_instance: str,
                 query: str) -> tuple[NormalizedMemory, ...]:
        if not isinstance(query, str) or not 0 < len(query) <= 4096:
            raise ValueError("invalid bounded retrieval query")
        try:
            registration, source = self._sources[(site_id, source_instance)]
        except KeyError as exc:
            raise Denied("source connector has not been registered") from exc
        lease = self.authority.authorize(session, site_id, registration.permission_source, "memory.read")
        results = source.retrieve(query)
        if not isinstance(results, tuple) or len(results) > 100:
            raise Denied("adapter result is not bounded")
        delivery_leases = [lease]
        for record in results:
            if (not isinstance(record, NormalizedMemory)
                    or record.version != 1 or record.owner_id != lease.owner_id
                    or record.site_id != site_id or record.source_instance != source_instance
                    or record.source_schema != registration.source_schema
                    or record.record_id != f"{site_id}/{source_instance}/{record.original_record_id}"
                    or record.deleted or record.invalidated or record.authority != "untrusted_evidence"):
                raise Denied("source result has invalid provenance or ownership")
            _content(record.content)
            if record.retention_expires_at is not None and _time(record.retention_expires_at) <= self.authority.clock():
                raise Denied("source record passed its retention boundary")
            for required in _record_permissions(record):
                delivery_leases.append(self.authority.authorize(session, required.site_id, required.source, "memory.read"))
        for pending in delivery_leases:
            self.authority.check_lease(pending)
        return results


def _record_permissions(record: NormalizedMemory) -> set[SourcePermission]:
    if (not isinstance(record.evidence, tuple) or len(record.evidence) > 100
            or not isinstance(record.required_sources, tuple) or len(record.required_sources) > 100):
        raise Denied("source provenance must be bounded immutable typed tuples")
    permissions = set()
    for required in record.required_sources:
        if not isinstance(required, SourcePermission):
            raise Denied("invalid source permission")
        permissions.add(SourcePermission(required.site_id, required.source))
    for evidence in record.evidence:
        if not isinstance(evidence, EvidenceReference):
            raise Denied("invalid evidence reference")
        # Reconstruct to validate revisions even if an adapter bypassed dataclass
        # initialization while decoding an external record.
        EvidenceReference(evidence.site_id, evidence.source, evidence.original_record_id, evidence.revision)
        permissions.add(SourcePermission(evidence.site_id, evidence.source))
    return permissions


def _content(content: str) -> None:
    if not isinstance(content, str) or not 0 < len(content) <= 4096:
        raise ValueError("memory content must be nonempty and bounded")


def _encode(record: NormalizedMemory) -> str:
    return json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))


def _decode(payload: str) -> NormalizedMemory:
    data = json.loads(payload)
    data["evidence"] = tuple(EvidenceReference(**item) for item in data["evidence"])
    data["required_sources"] = tuple(SourcePermission(**item) for item in data["required_sources"])
    return NormalizedMemory(**data)
