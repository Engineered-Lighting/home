"""Durable metadata for a future trusted, in-process engine cleanup hook.

This is NOT a vLLM lifecycle attestor. The caller must register every internal
request ID before dispatch and attest cleanup only after independently reviewed
engine/worker cleanup. HTTP completion, cancellation, timeouts and missing IDs
are not evidence. The authority object is an in-process wiring guard, not a
security boundary against code executing in the engine process.

Use a protected local filesystem and one engine epoch per database. Reopening
never invents receipts or changes outstanding registrations. Missing databases
require explicit provisioning; old epochs must remain available for lookup.
There is no pruning: exhaustion closes this instance until reviewed maintenance.
The configured byte limit covers database pages; provision additional space for
one rollback journal up to the database size and filesystem overhead. SQLite's
durability guarantee requires a local filesystem honoring its sync operations.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading


class ReceiptUnavailable(RuntimeError):
    """No reliable receipt; keep the gateway reservation charged."""


class ReceiptConflict(ReceiptUnavailable):
    pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}", value):
        raise ValueError("invalid metadata identifier")
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("sha256 required")
    return value


def _ids(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 64:
        raise ValueError("one to 64 complete internal IDs required")
    result = tuple(sorted(_id(value) for value in values))
    if len(set(result)) != len(result):
        raise ValueError("duplicate internal ID")
    return result


@dataclass(frozen=True)
class EngineReceipt:
    manifest_digest: str
    engine_id: str
    engine_epoch: str
    model_revision: str
    ledger_id: str
    request_digest: str
    upstream_body_sha256: str
    internal_ids: tuple[str, ...]
    registration_digest: str
    cleanup_evidence_sha256: str | None = None


@dataclass(frozen=True)
class DispatchRegistration:
    receipt: EngineReceipt
    dispatch_permitted: bool


class EngineReceiptStore:
    def __init__(self, path, *, manifest_digest, engine_id, engine_epoch, model_revision,
                 cleanup_authority, create=False, max_records=10000,
                 max_database_bytes=64 * 1024 * 1024, _read_only=False):
        if cleanup_authority is None:
            raise ValueError("explicit trusted cleanup caller required")
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError("bounded record limit required")
        if type(max_database_bytes) is not int or not 65536 <= max_database_bytes <= 1024**3:
            raise ValueError("bounded database size required")
        self.binding = (_digest(manifest_digest), _id(engine_id), _id(engine_epoch), _id(model_revision))
        self._read_only = _read_only
        self._authority = cleanup_authority
        self._limit = max_records
        self._lock = threading.RLock()
        self._failed = False
        self._db = None
        path = Path(path).absolute()
        # Exclusive creation prevents accidentally adopting or overwriting state.
        if create:
            with path.open("xb"):
                pass
        try:
            self._db = sqlite3.connect(path.as_uri() + ("?mode=ro" if _read_only else "?mode=rw"), uri=True,
                                      isolation_level=None, check_same_thread=False, timeout=1)
            if not _read_only:
                self._db.execute("PRAGMA journal_mode=DELETE")
                self._db.execute("PRAGMA synchronous=EXTRA")
            self._db.execute("PRAGMA foreign_keys=ON")
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            if not _read_only:
                self._db.execute(f"PRAGMA max_page_count={max_database_bytes // page_size}")
            if self._db.execute("PRAGMA page_count").fetchone()[0] * page_size > max_database_bytes:
                raise ReceiptUnavailable("database exceeds configured limit")
            with self._transaction():
                if create:
                    self._db.execute("CREATE TABLE binding (value TEXT NOT NULL)")
                    self._db.execute("INSERT INTO binding VALUES (?)", (json.dumps(self.binding),))
                    self._db.execute("CREATE TABLE requests (ledger_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL)")
                    self._db.execute("CREATE TABLE internal_ids (internal_id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL REFERENCES requests(ledger_id))")
                    self._db.execute("PRAGMA user_version=1")
                if self._db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ReceiptUnavailable("database integrity failure")
                if self._db.execute("PRAGMA user_version").fetchone() != (1,):
                    raise ReceiptUnavailable("unsupported receipt schema")
                if self._db.execute("SELECT value FROM binding").fetchall() != [(json.dumps(self.binding),)]:
                    raise ReceiptConflict("engine binding mismatch")
                rows = self._db.execute("SELECT ledger_id,payload,checksum FROM requests LIMIT ?", (max_records + 1,)).fetchall()
                if len(rows) > max_records:
                    raise ReceiptUnavailable("record limit exceeded")
                expected = set()
                for row in rows:
                    record = self._decode(row)
                    expected.update((internal, record.ledger_id) for internal in record.internal_ids)
                actual = self._db.execute("SELECT internal_id,ledger_id FROM internal_ids LIMIT ?", (max_records * 64 + 1,)).fetchall()
                if len(actual) != len(expected) or set(actual) != expected:
                    raise ReceiptUnavailable("internal ID index inconsistent")
        except Exception as exc:
            self.close()
            raise ReceiptUnavailable("receipt store cannot open") from exc

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._failed or self._db is None:
                raise ReceiptUnavailable("receipt store closed")
            try:
                self._db.execute("BEGIN" if self._read_only else "BEGIN IMMEDIATE")
                yield
                self._db.execute("COMMIT")
            except Exception as exc:
                self._failed = True
                try:
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if isinstance(exc, ReceiptUnavailable):
                    raise
                raise ReceiptUnavailable("receipt transaction failed") from exc

    def _encode(self, receipt):
        payload = json.dumps([receipt.ledger_id, receipt.request_digest, receipt.upstream_body_sha256,
                              receipt.internal_ids, receipt.registration_digest, receipt.cleanup_evidence_sha256], separators=(",", ":"))
        return payload, hashlib.sha256(payload.encode()).hexdigest()

    def _decode(self, row):
        ledger_id, payload, checksum = row
        if len(payload) > 16384 or hashlib.sha256(payload.encode()).hexdigest() != checksum:
            raise ReceiptUnavailable("receipt checksum failure")
        values = json.loads(payload)
        if not isinstance(values, list) or len(values) != 6 or values[0] != ledger_id:
            raise ReceiptUnavailable("invalid receipt encoding")
        receipt = self._receipt(values[0], values[1], values[2], values[3], values[5])
        if receipt.registration_digest != values[4]:
            raise ReceiptUnavailable("registration digest mismatch")
        if self._encode(receipt) != (payload, checksum):
            raise ReceiptUnavailable("noncanonical receipt")
        return receipt

    def _receipt(self, ledger_id, request_digest, upstream_body_sha256, internal_ids, evidence=None):
        values = (*self.binding, _id(ledger_id), _digest(request_digest),
                  _digest(upstream_body_sha256), _ids(internal_ids))
        digest = hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()
        return EngineReceipt(*values, digest, None if evidence is None else _digest(evidence))

    def _lookup(self, ledger_id):
        row = self._db.execute("SELECT ledger_id,payload,checksum FROM requests WHERE ledger_id=?", (ledger_id,)).fetchone()
        return None if row is None else self._decode(row)

    def lookup(self, ledger_id):
        ledger_id = _id(ledger_id)
        with self._transaction():
            return self._lookup(ledger_id)

    def register(self, ledger_id, request_digest, upstream_body_sha256, internal_ids):
        """Persist/retrieve metadata; this return value NEVER authorizes dispatch.

        Engine dispatch integration must use register_for_dispatch instead.
        """
        return self.register_for_dispatch(ledger_id, request_digest, upstream_body_sha256, internal_ids).receipt

    def register_for_dispatch(self, ledger_id, request_digest, upstream_body_sha256, internal_ids):
        """Return an atomic one-shot dispatch decision after durable commit.

        The caller supplies the COMPLETE immutable internal-ID set. It cannot
        dispatch first and append IDs later. Repeated registration returns the
        recorded state with dispatch_permitted=False, even for pending work
        after a crash. A newly inserted decision can be consumed only once by
        the trusted engine hook; no automatic retry is permitted.
        """
        receipt = self._receipt(ledger_id, request_digest, upstream_body_sha256, internal_ids)
        with self._transaction():
            existing = self._lookup(receipt.ledger_id)
            if existing:
                if existing.registration_digest != receipt.registration_digest:
                    raise ReceiptConflict("changed registration")
                return DispatchRegistration(existing, False)
            if self._db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= self._limit:
                raise ReceiptUnavailable("receipt capacity exhausted")
            for internal in receipt.internal_ids:
                if self._db.execute("SELECT 1 FROM internal_ids WHERE internal_id=?", (internal,)).fetchone():
                    raise ReceiptConflict("internal ID already registered")
            self._db.execute("INSERT INTO requests VALUES (?,?,?)", (receipt.ledger_id, *self._encode(receipt)))
            self._db.executemany("INSERT INTO internal_ids VALUES (?,?)", [(internal, receipt.ledger_id) for internal in receipt.internal_ids])
        return DispatchRegistration(receipt, True)

    def record_cleanup(self, ledger_id, request_digest, upstream_body_sha256, internal_ids, *, authority, evidence_sha256):
        """Trusted engine hook only: evidence digest references reviewed cleanup.

        This method does not verify the evidence itself. No HTTP-facing adapter
        may hold the authority object or map transport results to this call.
        """
        if authority is not self._authority:
            raise ReceiptUnavailable("untrusted cleanup caller")
        receipt = self._receipt(ledger_id, request_digest, upstream_body_sha256, internal_ids, evidence_sha256)
        with self._transaction():
            existing = self._lookup(receipt.ledger_id)
            if existing is None or existing.registration_digest != receipt.registration_digest:
                raise ReceiptConflict("cleanup registration mismatch")
            if existing.cleanup_evidence_sha256 is not None and existing != receipt:
                raise ReceiptConflict("immutable cleanup receipt conflict")
            self._db.execute("UPDATE requests SET payload=?,checksum=? WHERE ledger_id=?", (*self._encode(receipt), receipt.ledger_id))
        return receipt

    def close(self):
        with self._lock:
            self._failed = True
            if self._db is not None:
                self._db.close()
                self._db = None


class EngineReceiptReader(EngineReceiptStore):
    """Read-only view on an operator-provisioned, protected local database.

    Path and expected engine bindings come from trusted deployment configuration,
    never from a request. The reader cannot authenticate malicious filesystem
    edits: checksums detect damage, not forgery. Do not use immutable SQLite mode
    because engine commits must remain visible to subsequent lookups.
    """
    def __init__(self, path, *, manifest_digest, engine_id, engine_epoch, model_revision,
                 max_records=10000, max_database_bytes=64 * 1024 * 1024):
        super().__init__(path, manifest_digest=manifest_digest, engine_id=engine_id,
                         engine_epoch=engine_epoch, model_revision=model_revision,
                         cleanup_authority=object(), max_records=max_records,
                         max_database_bytes=max_database_bytes, _read_only=True)

    def register(self, *args, **kwargs):
        raise ReceiptUnavailable("reader cannot register")

    def register_for_dispatch(self, *args, **kwargs):
        raise ReceiptUnavailable("reader cannot authorize dispatch")

    def record_cleanup(self, *args, **kwargs):
        raise ReceiptUnavailable("reader cannot attest cleanup")
