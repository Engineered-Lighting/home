from __future__ import annotations

import json
import hashlib
import hmac
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .crypto import FieldCipher, SealedValue, canonical_json


@dataclass(frozen=True, slots=True)
class SpoolRecord:
    stream_key: str
    epoch: str
    sequence: int
    observed_at: datetime
    received_at: datetime
    expires_at: datetime
    metadata: dict[str, Any]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SpoolAppendResult:
    inserted: bool
    evicted: tuple[tuple[str, str, int, str], ...]


class EncryptedRuntimeSpool:
    """Short-retention, row-encrypted raw observation spool.

    SQLite is a delivery/runtime cache, never semantic authority. Only encrypted
    payloads and non-sensitive routing metadata are written to disk.
    """

    def __init__(
        self,
        path: Path,
        key: bytes,
        *,
        ttl_seconds: int = 86_400,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.path = path
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_bytes = max_bytes
        self.cipher = FieldCipher(key)
        self._subject_tag_key = hmac.new(
            key,
            b"home-agent:v1:runtime-spool-subject-tag",
            hashlib.sha256,
        ).digest()
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._harden_permissions()
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA secure_delete=ON")
        if int(self._conn.execute("PRAGMA auto_vacuum").fetchone()[0]) != 1:
            self._conn.execute("PRAGMA auto_vacuum=FULL")
            self._conn.execute("VACUUM")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._migrate()
        self._harden_permissions()

    def _migrate(self) -> None:
        with self._transaction(immediate=True):
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO schema_version(version, applied_at)
                VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_observations (
                    spool_id TEXT PRIMARY KEY,
                    stream_key TEXT NOT NULL,
                    epoch TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    observed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    UNIQUE(stream_key, epoch, sequence)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_spool_expiry
                    ON raw_observations(expires_at)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gap_markers (
                    marker_id TEXT PRIMARY KEY,
                    stream_key TEXT NOT NULL,
                    epoch TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            gap_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(gap_markers)")
            }
            if "expires_at" not in gap_columns:
                self._conn.execute("ALTER TABLE gap_markers ADD COLUMN expires_at TEXT")
                self._conn.execute(
                    "UPDATE gap_markers SET expires_at=created_at WHERE expires_at IS NULL"
                )

    def append(
        self,
        *,
        stream_key: str,
        epoch: str,
        sequence: int,
        observed_at: datetime,
        received_at: datetime,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        user_scope_id: str | None = None,
        now: datetime | None = None,
    ) -> SpoolAppendResult:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        expires_at = now + self.ttl
        spool_id = str(uuid.uuid4())
        routing_metadata = dict(metadata)
        if user_scope_id:
            routing_metadata["user_scope_tag"] = self._subject_tag(
                "ha_user", user_scope_id
            )
        sealed = self.cipher.seal(
            canonical_json(payload),
            purpose="raw-observation",
            artifact_id=spool_id,
        )
        with self._transaction(immediate=True):
            self._prune_locked(now)
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO raw_observations(
                    spool_id, stream_key, epoch, sequence, observed_at,
                    received_at, expires_at, metadata_json, nonce, ciphertext,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spool_id,
                    stream_key,
                    epoch,
                    sequence,
                    self._iso(observed_at),
                    self._iso(received_at),
                    self._iso(expires_at),
                    canonical_json(routing_metadata).decode(),
                    sealed.nonce,
                    sealed.ciphertext,
                    sealed.sha256,
                ),
            )
            if cursor.rowcount == 0:
                result = SpoolAppendResult(inserted=False, evicted=())
            else:
                evicted = self._enforce_size_locked(now)
                result = SpoolAppendResult(inserted=True, evicted=tuple(evicted))
        self._checkpoint_truncate()
        if self._physical_bytes() > self.max_bytes:
            raise RuntimeError("runtime spool exceeded its hard physical byte limit")
        return result

    def get(
        self, stream_key: str, epoch: str, sequence: int, *, now: datetime | None = None
    ) -> SpoolRecord | None:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        with self._transaction(immediate=True):
            self._prune_locked(now)
            row = self._conn.execute(
                "SELECT * FROM raw_observations WHERE stream_key=? AND epoch=? AND sequence=?",
                (stream_key, epoch, sequence),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def recent_for_entity(
        self,
        entity_id: str,
        *,
        since: datetime,
        now: datetime | None = None,
        limit: int = 200,
    ) -> list[SpoolRecord]:
        """Return decrypted recent observations for deterministic projection.

        Entity IDs are routing metadata already retained in the durable header;
        coordinates and state payloads remain encrypted.
        """

        now = (now or datetime.now(UTC)).astimezone(UTC)
        if limit < 1 or limit > 1000:
            raise ValueError("recent observation limit must be between 1 and 1000")
        with self._transaction(immediate=True):
            self._prune_locked(now)
            rows = self._conn.execute(
                """
                SELECT * FROM raw_observations
                 WHERE observed_at >= ? AND expires_at > ?
                 ORDER BY observed_at DESC, sequence DESC
                 LIMIT ?
                """,
                (self._iso(since), self._iso(now), limit),
            ).fetchall()
        records = [self._record_from_row(row) for row in rows]
        return [
            record
            for record in reversed(records)
            if record.metadata.get("entity_id") == entity_id
        ]

    def _record_from_row(self, row: sqlite3.Row) -> SpoolRecord:
        sealed = SealedValue(
            nonce=bytes(row["nonce"]),
            ciphertext=bytes(row["ciphertext"]),
            sha256=row["payload_sha256"],
        )
        payload = json.loads(
            self.cipher.open(
                sealed,
                purpose="raw-observation",
                artifact_id=row["spool_id"],
            )
        )
        return SpoolRecord(
            stream_key=row["stream_key"],
            epoch=row["epoch"],
            sequence=row["sequence"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            received_at=datetime.fromisoformat(row["received_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            metadata=json.loads(row["metadata_json"]),
            payload=payload,
        )

    def prune(self, *, now: datetime | None = None) -> int:
        with self._transaction(immediate=True):
            removed = self._prune_locked((now or datetime.now(UTC)).astimezone(UTC))
        self._checkpoint_truncate()
        return removed

    def delete_for_subjects(
        self, entity_ids: list[str], user_ids: list[str] | None = None
    ) -> int:
        """Immediately remove entity- or HA-user-linked rows during erasure."""
        exact = {value for value in entity_ids if isinstance(value, str) and value}
        user_tags = {
            self._subject_tag("ha_user", value)
            for value in (user_ids or [])
            if isinstance(value, str) and value
        }
        if not exact and not user_tags:
            return 0
        removed = 0
        with self._transaction(immediate=True):
            rows = self._conn.execute(
                "SELECT spool_id, stream_key, epoch, sequence, metadata_json "
                "FROM raw_observations"
            ).fetchall()
            targets = []
            linkage_uncertain = False
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"])
                except (TypeError, ValueError):
                    linkage_uncertain = True
                    break
                if (
                    metadata.get("entity_id") in exact
                    or metadata.get("derived_from_entity") in exact
                    or metadata.get("user_scope_tag") in user_tags
                ):
                    targets.append(row)
            if linkage_uncertain:
                # Raw runtime data is rebuildable and unbacked-up. When exact
                # row ownership cannot be proven, privacy wins over retention.
                targets = list(rows)
            if targets:
                now = datetime.now(UTC)
                for row in targets:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO gap_markers("
                        "marker_id, stream_key, epoch, sequence, reason_code, "
                        "created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            row["stream_key"],
                            row["epoch"],
                            row["sequence"],
                            "privacy_auto_expiry",
                            self._iso(now),
                            self._iso(now + self.ttl),
                        ),
                    )
                cursor = self._conn.executemany(
                    "DELETE FROM raw_observations WHERE spool_id=?",
                    [(row["spool_id"],) for row in targets],
                )
                removed = max(int(cursor.rowcount or 0), 0)
        self._checkpoint_truncate()
        return removed

    def delete_for_entities(self, entity_ids: list[str]) -> int:
        """Compatibility wrapper for pre-user-scope erasure callers."""

        return self.delete_for_subjects(entity_ids)

    def acknowledge_gap_markers(self, marker_ids: list[str]) -> None:
        if not marker_ids:
            return
        with self._transaction(immediate=True):
            self._conn.executemany(
                "DELETE FROM gap_markers WHERE marker_id=?",
                [(marker_id,) for marker_id in marker_ids],
            )
        self._checkpoint_truncate()

    def stats(self) -> dict[str, Any]:
        with self._transaction(immediate=False):
            row = self._conn.execute(
                "SELECT COUNT(*) AS records, COALESCE(SUM(length(ciphertext)),0) AS encrypted_bytes, MIN(expires_at) AS next_expiry FROM raw_observations"
            ).fetchone()
            gaps = self._conn.execute("SELECT COUNT(*) FROM gap_markers").fetchone()[0]
            physical_bytes = self._physical_bytes()
        return {
            "records": int(row["records"]),
            "encrypted_bytes": int(row["encrypted_bytes"]),
            "next_expiry": row["next_expiry"],
            "gap_markers": int(gaps),
            "physical_bytes": physical_bytes,
            "max_bytes": self.max_bytes,
            "ttl_seconds": int(self.ttl.total_seconds()),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _transaction(self, *, immediate: bool) -> Iterator[None]:
        """Serialize a complete spool operation across processes.

        The connection remains in SQLite autocommit mode so transaction
        boundaries are never implicit. ``BEGIN IMMEDIATE`` obtains the single
        writer reservation before prune/insert/overflow work starts; rollback
        therefore removes both an observation and any associated gap markers.
        WAL still permits concurrent readers, and ``busy_timeout`` bounds
        cross-process writer contention.
        """

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                self._harden_permissions()

    def _harden_permissions(self) -> None:
        if os.name != "posix":
            return
        try:
            os.chmod(self.path.parent, 0o700)
            for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            ):
                if candidate.exists():
                    os.chmod(candidate, 0o600)
        except OSError as exc:
            raise PermissionError(
                "runtime spool permissions cannot be hardened"
            ) from exc
        if self.path.parent.stat().st_mode & 0o077:
            raise PermissionError("runtime spool directory must be mode 0700")
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if candidate.exists() and candidate.stat().st_mode & 0o777 != 0o600:
                raise PermissionError("runtime spool files must be mode 0600")

    def _prune_locked(self, now: datetime) -> int:
        cursor = self._conn.execute(
            "DELETE FROM raw_observations WHERE expires_at <= ?",
            (self._iso(now),),
        )
        removed = max(cursor.rowcount, 0)
        gap_cursor = self._conn.execute(
            "DELETE FROM gap_markers WHERE expires_at <= ?",
            (self._iso(now),),
        )
        return removed + max(gap_cursor.rowcount, 0)

    def _enforce_size_locked(self, now: datetime) -> list[tuple[str, str, int, str]]:
        evicted: list[tuple[str, str, int, str]] = []
        while self._estimated_used_bytes_locked() > self.max_bytes:
            row = self._conn.execute(
                "SELECT spool_id, stream_key, epoch, sequence FROM raw_observations ORDER BY received_at, sequence LIMIT 1"
            ).fetchone()
            if row is None:
                break
            marker_id = str(uuid.uuid4())
            self._conn.execute(
                "INSERT INTO gap_markers(marker_id, stream_key, epoch, sequence, reason_code, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    marker_id,
                    row["stream_key"],
                    row["epoch"],
                    row["sequence"],
                    "runtime_spool_overflow",
                    self._iso(now),
                    self._iso(now + self.ttl),
                ),
            )
            self._conn.execute(
                "DELETE FROM raw_observations WHERE spool_id=?", (row["spool_id"],)
            )
            evicted.append(
                (row["stream_key"], row["epoch"], row["sequence"], marker_id)
            )
        return evicted

    def _estimated_used_bytes_locked(self) -> int:
        page_size = int(self._conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(self._conn.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(self._conn.execute("PRAGMA freelist_count").fetchone()[0])
        # SQLite keeps a shared-memory sidecar while WAL connections are open.
        # Reserve its normal 32 KiB footprint even before it appears.
        return max(0, page_count - free_pages) * page_size + 32 * 1024

    def _checkpoint_truncate(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._harden_permissions()

    def _physical_bytes(self) -> int:
        return sum(
            candidate.stat().st_size if candidate.exists() else 0
            for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            )
        )

    @staticmethod
    def _iso(value: datetime) -> str:
        if value.tzinfo is None:
            raise ValueError("spool timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()

    def _subject_tag(self, subject_kind: str, value: str) -> str:
        if subject_kind != "ha_user" or not value:
            raise ValueError("invalid runtime spool subject tag input")
        material = f"{subject_kind}:{value.strip().lower()}".encode("utf-8")
        return hmac.new(self._subject_tag_key, material, hashlib.sha256).hexdigest()


class DisabledRuntimeSpool:
    """API-role placeholder with no raw-observation key or filesystem access."""

    def stats(self) -> dict[str, Any]:
        return {"status": "disabled_for_role"}

    def close(self) -> None:
        return None

    def prune(self) -> int:
        return 0

    def delete_for_entities(self, _entity_ids: list[str]) -> int:
        return 0

    def delete_for_subjects(
        self, _entity_ids: list[str], _user_ids: list[str] | None = None
    ) -> int:
        return 0

    def append(self, **_kwargs):
        raise RuntimeError("runtime spool is disabled for this role")

    def recent_for_entity(self, *_args, **_kwargs):
        raise RuntimeError("runtime spool is disabled for this role")

    def acknowledge_gap_markers(self, _marker_ids: list[str]) -> None:
        return None
