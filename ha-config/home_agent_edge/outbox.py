"""Bounded, encrypted, at-least-once delivery spool."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import random
import sqlite3
import stat
import threading
import time
from typing import Any, Protocol
from uuid import uuid4
from uuid import NAMESPACE_URL, uuid5

from .const import (
    EVENT_CONVERSATION_FINISHED,
    EVENT_GAP,
    SCHEMA_VERSION,
    SOURCE_NAME,
)


class EnvelopeCodec(Protocol):
    def encode(self, envelope: Mapping[str, Any]) -> bytes: ...
    def decode(self, payload: bytes) -> dict[str, Any]: ...
    def subject_tag(self, subject_kind: str, value: str) -> str: ...


MAX_ENVELOPE_BYTES = 1024 * 1024
SQLITE_AUTO_VACUUM_FULL = 1
PHYSICAL_PRESSURE_BATCH = 256


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back on context exit, then release the OS handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True, slots=True)
class OutboxItem:
    sequence_start: int
    sequence_end: int
    kind: str
    event_type: str
    payload: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class OutboxBatch:
    edge_instance_id: str
    epoch: str
    items: tuple[OutboxItem, ...]

    @property
    def first_sequence(self) -> int:
        return self.items[0].sequence_start

    @property
    def last_sequence(self) -> int:
        return self.items[-1].sequence_end

    def as_request(self) -> dict[str, Any]:
        return {"envelopes": [self._core_envelope(item) for item in self.items]}

    def acknowledgement_key(self) -> str:
        return f"{self.edge_instance_id}:{SOURCE_NAME}"

    def _core_envelope(self, item: OutboxItem) -> dict[str, Any]:
        source = item.payload
        context = source.get("context") if isinstance(source.get("context"), Mapping) else {}
        source_type = str(source.get("source_event_type") or "unknown")
        observation_kind = source.get("observation_kind")
        if source_type == EVENT_GAP:
            event_type = "coverage_gap"
            coverage = "gap"
        elif source_type == EVENT_CONVERSATION_FINISHED:
            event_type = "conversation_turn"
            coverage = "continuous"
        elif observation_kind == "snapshot":
            event_type = "snapshot"
            coverage = "snapshot_only"
        elif str(source.get("entity_id") or "").startswith(
            ("person.", "device_tracker.", "zone.")
        ):
            event_type = "location_fix"
            coverage = "continuous"
        else:
            event_type = "state_changed"
            coverage = "continuous"

        excluded = {
            "edge_instance_id", "epoch", "sequence_start", "sequence_end",
            "source_event_type", "occurred_at", "received_at", "context",
        }
        payload = {key: value for key, value in source.items() if key not in excluded}
        if source_type == EVENT_GAP:
            payload.update(
                {
                    "sequence_start": item.sequence_start,
                    "sequence_end": item.sequence_end,
                }
            )

        root_id, family_id, dependency_domain = _lineage_for(source)
        new_attributes = ((source.get("new_state") or {}).get("attributes") or {})
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "source_platform": "home_assistant",
            "projection_type": _projection_type(source),
        }
        if isinstance(new_attributes, Mapping) and new_attributes.get("tracking_type"):
            metadata["tracking_type"] = new_attributes["tracking_type"]
        if str(source.get("entity_id") or "").startswith("person.") and isinstance(new_attributes, Mapping):
            if new_attributes.get("source"):
                metadata["derived_from_entity"] = new_attributes["source"]

        return {
            "edge_instance_id": self.edge_instance_id,
            "source_name": SOURCE_NAME,
            "epoch": self.epoch,
            "sequence": item.sequence_start,
            "event_type": event_type,
            "entity_id": source.get("entity_id"),
            "source_event_id": context.get("id"),
            "source_observed_at": _timestamp(source.get("occurred_at")),
            "edge_received_at": _timestamp(source.get("received_at")),
            "payload": payload,
            "root_observation_id": root_id,
            "evidence_family_id": family_id,
            "dependency_domain": dependency_domain,
            "coverage": coverage,
            "clock_state": "unknown",
            "ha_context": {
                key: context.get(key) for key in ("id", "user_id", "parent_id")
                if context.get(key) is not None
            },
            "metadata": metadata,
        }


class EdgeOutbox:
    """SQLite outbox whose sensitive payload column is always encrypted."""

    def __init__(
        self,
        path: str | Path,
        codec: EnvelopeCodec,
        *,
        max_bytes: int,
        max_age_seconds: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        if max_bytes < 64 * 1024:
            raise ValueError("spool max_bytes must be at least 64 KiB")
        if max_age_seconds < 60:
            raise ValueError("spool max_age_seconds must be at least 60")
        self.path = Path(path)
        self.codec = codec
        self.max_bytes = int(max_bytes)
        self.max_age_seconds = int(max_age_seconds)
        self._now = now
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            parent_mode = stat.S_IMODE(self.path.parent.stat().st_mode)
            if parent_mode & 0o077:
                raise PermissionError(
                    "Home Agent Edge spool directory must be mode 0700 or stricter"
                )
            if self.path.exists():
                if stat.S_IMODE(self.path.stat().st_mode) & 0o077:
                    raise PermissionError(
                        "Home Agent Edge spool file must be mode 0600 or stricter"
                    )
            else:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                os.close(descriptor)
        # auto_vacuum cannot be changed safely on an existing database without
        # a VACUUM rebuild. Do that once, before opening the normal WAL-backed
        # connection, and fail closed if a second process prevents the
        # migration. FULL mode makes every committed deletion release trailing
        # pages instead of retaining expired ciphertext on the freelist.
        self._migrate_storage_pragmas()
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    sequence_start INTEGER PRIMARY KEY,
                    sequence_end INTEGER NOT NULL CHECK(sequence_end >= sequence_start),
                    kind TEXT NOT NULL CHECK(kind IN ('event', 'gap')),
                    event_type TEXT NOT NULL,
                    entity_id TEXT,
                    user_scope_tag TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    ciphertext BLOB NOT NULL,
                    payload_bytes INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_delivery
                    ON outbox(sequence_start, next_attempt_at);
                CREATE TABLE IF NOT EXISTS quarantine (
                    quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    metadata_sha256 TEXT NOT NULL
                );
                """
            )
            outbox_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(outbox)")
            }
            if "user_scope_tag" not in outbox_columns:
                conn.execute("ALTER TABLE outbox ADD COLUMN user_scope_tag TEXT")
            # A short-lived development revision wrote HA UUIDs to a plaintext
            # column. Backfill only keyed tags from encrypted envelopes, then
            # rebuild the table so no UUID or dropped-column pages remain.
            if "user_id" in outbox_columns:
                for sequence, ciphertext in conn.execute(
                    "SELECT sequence_start, ciphertext FROM outbox"
                ):
                    envelope = self.codec.decode(bytes(ciphertext))
                    context = envelope.get("context")
                    user_id = context.get("user_id") if isinstance(context, Mapping) else None
                    if user_id:
                        conn.execute(
                            "UPDATE outbox SET user_scope_tag=? WHERE sequence_start=?",
                            (self.codec.subject_tag("ha_user", str(user_id)), sequence),
                        )
                conn.execute("DROP INDEX IF EXISTS idx_outbox_privacy_subject")
                conn.executescript(
                    """
                    CREATE TABLE outbox_privacy_migration (
                        sequence_start INTEGER PRIMARY KEY,
                        sequence_end INTEGER NOT NULL CHECK(sequence_end >= sequence_start),
                        kind TEXT NOT NULL CHECK(kind IN ('event', 'gap')),
                        event_type TEXT NOT NULL,
                        entity_id TEXT,
                        user_scope_tag TEXT,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        ciphertext BLOB NOT NULL,
                        payload_bytes INTEGER NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at REAL NOT NULL DEFAULT 0,
                        last_error_code TEXT
                    );
                    INSERT INTO outbox_privacy_migration(
                        sequence_start, sequence_end, kind, event_type, entity_id,
                        user_scope_tag, created_at, expires_at, ciphertext,
                        payload_bytes, attempt_count, next_attempt_at, last_error_code
                    ) SELECT sequence_start, sequence_end, kind, event_type, entity_id,
                        user_scope_tag, created_at, expires_at, ciphertext,
                        payload_bytes, attempt_count, next_attempt_at, last_error_code
                      FROM outbox;
                    DROP TABLE outbox;
                    ALTER TABLE outbox_privacy_migration RENAME TO outbox;
                    """
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_delivery "
                "ON outbox(sequence_start, next_attempt_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_privacy_subject "
                "ON outbox(entity_id, user_scope_tag)"
            )
            self._ensure_metadata(conn, "edge_instance_id", str(uuid4()))
            self._ensure_metadata(conn, "epoch", str(uuid4()))
            self._ensure_metadata(conn, "next_sequence", "1")
        self._compact_storage()
        self._enforce_physical_limit()
        self._harden_permissions()

    @property
    def edge_instance_id(self) -> str:
        return self._metadata("edge_instance_id")

    @property
    def epoch(self) -> str:
        return self._metadata("epoch")

    def enqueue(self, envelope: Mapping[str, Any]) -> int:
        """Assign a sequence and atomically append one encrypted envelope."""
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sequence = self._enqueue_locked(conn, envelope, now)
            conn.commit()
        self.maintain()
        return sequence

    def suppress_privacy_subjects(
        self, entity_ids: set[str], user_ids: set[str]
    ) -> int:
        """Replace already-spooled blocked entity/user events with gaps."""
        blocked = {str(value).strip().lower() for value in entity_ids if value}
        blocked_users = {str(value).strip().lower() for value in user_ids if value}
        blocked_user_tags = {
            self.codec.subject_tag("ha_user", value) for value in blocked_users
        }
        if not blocked and not blocked_user_tags:
            return 0
        replaced = 0
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            entity_placeholders = ",".join("?" for _ in blocked) or "NULL"
            user_placeholders = ",".join("?" for _ in blocked_user_tags) or "NULL"
            rows = conn.execute(
                f"SELECT sequence_start, sequence_end FROM outbox "
                f"WHERE entity_id IN ({entity_placeholders}) "
                f"OR user_scope_tag IN ({user_placeholders}) ORDER BY sequence_start",
                [*sorted(blocked), *sorted(blocked_user_tags)],
            ).fetchall()
            for row in rows:
                self._replace_span_with_gap(
                    conn,
                    int(row[0]),
                    int(row[1]),
                    "privacy_directive",
                    "core_edge_privacy_block",
                )
                replaced += 1
            conn.commit()
        self._compact_storage()
        return replaced

    def begin_runtime(self) -> int | None:
        """Open a runtime session and preserve downtime as an explicit gap."""
        now = self._now()
        sequence: int | None = None
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior_seen = self._metadata_optional(conn, "runtime_last_seen_at")
            prior_clean = self._metadata_optional(conn, "runtime_clean_shutdown") == "1"
            if prior_seen is not None:
                sequence = self._enqueue_locked(
                    conn,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "source_event_type": EVENT_GAP,
                        "occurred_at": _timestamp(float(prior_seen)),
                        "received_at": _timestamp(now),
                        "reason_code": (
                            "edge_restart_after_clean_shutdown"
                            if prior_clean
                            else "edge_unclean_restart"
                        ),
                    },
                    now,
                )
            self._set_metadata(conn, "runtime_last_seen_at", str(now))
            self._set_metadata(conn, "runtime_clean_shutdown", "0")
            conn.commit()
        self.maintain()
        return sequence

    def heartbeat(self) -> None:
        with self._lock, self._connect() as conn:
            self._set_metadata(conn, "runtime_last_seen_at", str(self._now()))
        self._compact_storage()
        self._enforce_physical_limit()

    def end_runtime(self) -> None:
        with self._lock, self._connect() as conn:
            self._set_metadata(conn, "runtime_last_seen_at", str(self._now()))
            self._set_metadata(conn, "runtime_clean_shutdown", "1")
        self._compact_storage()
        self._enforce_physical_limit()

    def quarantine(self, event_type: str, reason_code: str, metadata: Any) -> None:
        """Record a content-free parsing failure without retaining raw input."""
        # A plain digest of malformed metadata is still an offline oracle for
        # short utterances or nearby coordinate grids. The receipt needs only
        # uniqueness, not content equality, so bind it to fresh random material
        # and deliberately ignore attacker-controlled metadata.
        del metadata
        receipt_id = os.urandom(32).hex()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO quarantine(created_at, event_type, reason_code, metadata_sha256)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self._now(),
                    str(event_type)[:128],
                    str(reason_code)[:128],
                    receipt_id,
                ),
            )
        self.maintain()

    def read_batch(self, limit: int = 100) -> OutboxBatch | None:
        """Return the oldest contiguous ready batch without removing it."""
        if limit < 1:
            raise ValueError("batch limit must be positive")
        with self._lock, self._connect() as conn:
            now = self._now()
            rows = conn.execute(
                """
                SELECT sequence_start, sequence_end, kind, event_type,
                       ciphertext, attempt_count, next_attempt_at
                FROM outbox ORDER BY sequence_start LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if not rows or float(rows[0][6]) > now:
                return None

            items: list[OutboxItem] = []
            expected: int | None = None
            for row in rows:
                start, end = int(row[0]), int(row[1])
                if expected is not None and start != expected:
                    raise RuntimeError(
                        f"edge outbox sequence discontinuity: expected {expected}, got {start}"
                    )
                if float(row[6]) > now:
                    break
                try:
                    payload = self.codec.decode(bytes(row[4]))
                except Exception as exc:
                    self._quarantine_locked(
                        conn,
                        str(row[3]),
                        "ciphertext_unreadable",
                        {"sequence_start": start, "exception": type(exc).__name__},
                    )
                    self._replace_span_with_gap(
                        conn, start, end, "ciphertext_unreadable", str(type(exc).__name__)
                    )
                    conn.commit()
                    return self.read_batch(limit)
                if (
                    int(payload.get("sequence_start", -1)) != start
                    or int(payload.get("sequence_end", -1)) != end
                ):
                    self._quarantine_locked(
                        conn,
                        str(row[3]),
                        "sequence_mismatch",
                        {"sequence_start": start, "sequence_end": end},
                    )
                    self._replace_span_with_gap(
                        conn, start, end, "sequence_mismatch", "payload_header"
                    )
                    conn.commit()
                    return self.read_batch(limit)
                items.append(
                    OutboxItem(start, end, str(row[2]), str(row[3]), payload, int(row[5]))
                )
                expected = end + 1

            if not items:
                return None
            return OutboxBatch(
                self._metadata_from(conn, "edge_instance_id"),
                self._metadata_from(conn, "epoch"),
                tuple(items),
            )

    def acknowledge(self, through_sequence: int, batch: OutboxBatch) -> int:
        """Delete only a response acknowledgement that ends on a batch boundary."""
        valid = {item.sequence_end for item in batch.items}
        if through_sequence not in valid:
            raise ValueError("acknowledgement is not an envelope boundary in this batch")
        with self._lock, self._connect() as conn:
            if not self._batch_matches_current(conn, batch):
                raise ValueError("acknowledgement belongs to a retired edge epoch")
            cursor = conn.execute(
                "DELETE FROM outbox WHERE sequence_end <= ?",
                (int(through_sequence),),
            )
            removed = int(cursor.rowcount)
        self._compact_storage()
        self._enforce_physical_limit()
        return removed

    def mark_failure(self, batch: OutboxBatch, error_code: str) -> float:
        """Apply bounded exponential backoff to every row in a failed batch."""
        attempts = max(item.attempt_count for item in batch.items) + 1
        delay = min(300.0, float(2 ** min(attempts, 8)))
        delay += random.uniform(0, min(5.0, delay * 0.2))
        next_attempt = self._now() + delay
        with self._lock, self._connect() as conn:
            if not self._batch_matches_current(conn, batch):
                # Capacity pressure can retire an epoch while a network request
                # is in flight. Never apply that stale batch's backoff to the
                # new epoch's compact coverage-gap receipt.
                return self._now()
            conn.execute(
                """
                UPDATE outbox
                   SET attempt_count = attempt_count + 1,
                       next_attempt_at = ?,
                       last_error_code = ?
                 WHERE sequence_start >= ? AND sequence_end <= ?
                """,
                (
                    next_attempt,
                    str(error_code)[:128],
                    batch.first_sequence,
                    batch.last_sequence,
                ),
            )
        self._compact_storage()
        self._enforce_physical_limit()
        return next_attempt

    def maintain(self) -> dict[str, int]:
        """Replace expired/overflowed payloads with compact sequence gap markers."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = self._now()
            expired = conn.execute(
                """
                SELECT sequence_start, sequence_end FROM outbox
                 WHERE expires_at <= ? ORDER BY sequence_start
                """,
                (now,),
            ).fetchall()
            # The current Core contract accepts one sequence per envelope and
            # returns a per-stream integer acknowledgement. Preserve that
            # one-to-one boundary even when raw payloads expire.
            expired_ranges = [(int(row[0]), int(row[1])) for row in expired]
            for start, end in expired_ranges:
                self._replace_span_with_gap(conn, start, end, "retention_expired", None)

            # Quarantine receipts contain no event content, but they still
            # consume the same physical SQLite budget and have no reason to
            # outlive the raw-observation retention window.
            conn.execute(
                "DELETE FROM quarantine WHERE created_at <= ?",
                (now - self.max_age_seconds,),
            )

            overflow_ranges = 0
            while self._logical_bytes(conn) > self.max_bytes:
                rows = conn.execute(
                    """
                    SELECT sequence_start, sequence_end, payload_bytes, kind
                      FROM outbox WHERE kind = 'event' ORDER BY sequence_start
                    """
                ).fetchall()
                if not rows:
                    break
                start, end, _prior_size, _kind = rows[0]
                self._replace_span_with_gap(
                    conn,
                    int(start),
                    int(end),
                    "spool_overflow",
                    None,
                )
                overflow_ranges += 1
                if overflow_ranges > 32:
                    break
            conn.commit()
            logical_bytes = self._logical_bytes(conn)
            pending_rows = int(
                conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            )
        self._compact_storage()
        physical_rollovers = self._enforce_physical_limit()
        physical_bytes = self._physical_bytes()
        with self._lock, self._connect() as conn:
            logical_bytes = self._logical_bytes(conn)
            pending_rows = int(
                conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            )
        return {
            "expired_ranges": len(expired_ranges),
            "overflow_ranges": overflow_ranges,
            "physical_rollovers": physical_rollovers,
            "logical_bytes": logical_bytes,
            "physical_bytes": physical_bytes,
            "over_capacity": int(physical_bytes > self.max_bytes),
            "pending_rows": pending_rows,
        }

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0),
                       MIN(sequence_start), MAX(sequence_end),
                       COALESCE(MAX(attempt_count), 0)
                  FROM outbox
                """
            ).fetchone()
            result = {
                "pending_rows": int(row[0]),
                "logical_bytes": int(row[1]),
                "first_sequence": row[2],
                "last_sequence": row[3],
                "max_attempt_count": int(row[4]),
                "quarantine_rows": int(
                    conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
                ),
                "edge_instance_id": self._metadata_from(conn, "edge_instance_id"),
                "epoch": self._metadata_from(conn, "epoch"),
            }
        result["physical_bytes"] = self._physical_bytes()
        result["max_bytes"] = self.max_bytes
        return result

    def close(self) -> None:
        """Compatibility hook; connections are intentionally short-lived."""

    def _replace_span_with_gap(
        self,
        conn: sqlite3.Connection,
        start: int,
        end: int,
        reason_code: str,
        detail_code: str | None,
    ) -> None:
        instance_id = self._metadata_from(conn, "edge_instance_id")
        epoch = self._metadata_from(conn, "epoch")
        prior_created = conn.execute(
            """
            SELECT MIN(created_at) FROM outbox
             WHERE sequence_start >= ? AND sequence_end <= ?
            """,
            (int(start), int(end)),
        ).fetchone()[0]
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "source_event_type": EVENT_GAP,
            "edge_instance_id": instance_id,
            "epoch": epoch,
            "sequence_start": int(start),
            "sequence_end": int(end),
            "occurred_at": _timestamp(prior_created or self._now()),
            "received_at": _timestamp(self._now()),
            "reason_code": reason_code,
        }
        if detail_code:
            envelope["detail_code"] = detail_code[:64]
        ciphertext = self.codec.encode(envelope)
        conn.execute(
            "DELETE FROM outbox WHERE sequence_start >= ? AND sequence_end <= ?",
            (int(start), int(end)),
        )
        conn.execute(
            """
            INSERT INTO outbox(
                sequence_start, sequence_end, kind, event_type, entity_id,
                created_at, expires_at, ciphertext, payload_bytes,
                attempt_count, next_attempt_at, last_error_code
            ) VALUES (?, ?, 'gap', ?, NULL, ?, ?, ?, ?, 0, 0, NULL)
            """,
            (
                int(start),
                int(end),
                EVENT_GAP,
                self._now(),
                self._now() + self.max_age_seconds,
                ciphertext,
                len(ciphertext),
            ),
        )

    def _enqueue_locked(
        self,
        conn: sqlite3.Connection,
        envelope: Mapping[str, Any],
        now: float,
    ) -> int:
        sequence = int(self._metadata_from(conn, "next_sequence"))
        materialized = dict(envelope)
        materialized.update(
            {
                "edge_instance_id": self._metadata_from(conn, "edge_instance_id"),
                "epoch": self._metadata_from(conn, "epoch"),
                "sequence_start": sequence,
                "sequence_end": sequence,
            }
        )
        ciphertext = self.codec.encode(materialized)
        if len(ciphertext) > MAX_ENVELOPE_BYTES:
            raise ValueError("normalized edge envelope exceeds 1 MiB")
        kind = (
            "gap"
            if materialized.get("source_event_type") == EVENT_GAP
            else "event"
        )
        conn.execute(
            """
            INSERT INTO outbox(
                sequence_start, sequence_end, kind, event_type, entity_id, user_scope_tag,
                created_at, expires_at, ciphertext, payload_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                sequence,
                kind,
                str(materialized.get("source_event_type") or "unknown"),
                materialized.get("entity_id"),
                self.codec.subject_tag(
                    "ha_user", str((materialized.get("context") or {}).get("user_id"))
                )
                if isinstance(materialized.get("context"), Mapping)
                and (materialized.get("context") or {}).get("user_id")
                else None,
                now,
                now + self.max_age_seconds,
                ciphertext,
                len(ciphertext),
            ),
        )
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'next_sequence'",
            (str(sequence + 1),),
        )
        return sequence

    def _quarantine_locked(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        reason_code: str,
        metadata: Any,
    ) -> None:
        del metadata
        receipt_id = os.urandom(32).hex()
        conn.execute(
            """
            INSERT INTO quarantine(created_at, event_type, reason_code, metadata_sha256)
            VALUES (?, ?, ?, ?)
            """,
            (self._now(), event_type[:128], reason_code[:128], receipt_id),
        )

    @staticmethod
    def _coalesce_rows(rows: list[sqlite3.Row] | list[tuple[Any, ...]]) -> list[tuple[int, int]]:
        result: list[tuple[int, int]] = []
        for row in rows:
            start, end = int(row[0]), int(row[1])
            if result and start <= result[-1][1] + 1:
                result[-1] = (result[-1][0], max(result[-1][1], end))
            else:
                result.append((start, end))
        return result

    @staticmethod
    def _logical_bytes(conn: sqlite3.Connection) -> int:
        return int(
            conn.execute(
                "SELECT COALESCE(SUM(payload_bytes), 0) FROM outbox"
            ).fetchone()[0]
        )

    @staticmethod
    def _ensure_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            (key, value),
        )

    @staticmethod
    def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    @staticmethod
    def _metadata_optional(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    def _metadata(self, key: str) -> str:
        with self._lock, self._connect() as conn:
            return self._metadata_from(conn, key)

    @staticmethod
    def _batch_matches_current(conn: sqlite3.Connection, batch: OutboxBatch) -> bool:
        return (
            EdgeOutbox._metadata_from(conn, "edge_instance_id")
            == batch.edge_instance_id
            and EdgeOutbox._metadata_from(conn, "epoch") == batch.epoch
        )

    @staticmethod
    def _metadata_from(conn: sqlite3.Connection, key: str) -> str:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise RuntimeError(f"missing edge outbox metadata: {key}")
        return str(row[0])

    def _migrate_storage_pragmas(self) -> None:
        """Make secure deletion and page reclamation durable on old spools.

        Merely assigning ``PRAGMA auto_vacuum`` does not convert a database
        that already has tables. A one-time DELETE-journal VACUUM is required;
        switching away from WAL first also checkpoints every committed frame.
        """

        with self._lock:
            conn = sqlite3.connect(
                self.path,
                timeout=5.0,
                isolation_level=None,
                factory=_ClosingConnection,
            )
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA secure_delete=ON")
                journal_mode = str(
                    conn.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower()
                if journal_mode == "wal":
                    busy, _log, _checkpointed = conn.execute(
                        "PRAGMA wal_checkpoint(TRUNCATE)"
                    ).fetchone()
                    if int(busy) != 0:
                        raise RuntimeError(
                            "edge spool WAL is busy during secure migration"
                        )
                actual_mode = str(
                    conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                ).lower()
                if actual_mode != "delete":
                    raise RuntimeError(
                        "edge spool could not enter migration journal mode"
                    )
                if int(conn.execute("PRAGMA auto_vacuum").fetchone()[0]) != (
                    SQLITE_AUTO_VACUUM_FULL
                ):
                    conn.execute("PRAGMA auto_vacuum=FULL")
                    conn.execute("VACUUM")
                if int(conn.execute("PRAGMA auto_vacuum").fetchone()[0]) != (
                    SQLITE_AUTO_VACUUM_FULL
                ):
                    raise RuntimeError("edge spool auto_vacuum migration failed")
                actual_mode = str(
                    conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                ).lower()
                if actual_mode != "wal":
                    raise RuntimeError("edge spool could not enable WAL")
                conn.execute("PRAGMA synchronous=FULL")
            finally:
                conn.close()

    def _compact_storage(self) -> None:
        """Checkpoint all committed frames and remove WAL-resident ciphertext."""

        with self._lock, self._connect() as conn:
            busy, _log, _checkpointed = conn.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if int(busy) != 0:
                raise RuntimeError("edge spool WAL checkpoint is busy")
        self._harden_permissions()

    def _enforce_physical_limit(self) -> int:
        """Keep the complete SQLite artifact set within ``max_bytes``.

        Row ciphertext is replaced with sequence-preserving gap receipts first.
        If the receipts themselves cannot fit, a new delivery epoch is opened
        with one explicit capacity-gap receipt. The old epoch is never silently
        treated as complete.
        """

        rollovers = 0
        prior_size: int | None = None
        for _attempt in range(64):
            physical = self._physical_bytes()
            if physical <= self.max_bytes:
                return rollovers

            with self._lock, self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                event_rows = conn.execute(
                    """
                    SELECT sequence_start, sequence_end
                      FROM outbox WHERE kind = 'event'
                     ORDER BY sequence_start LIMIT ?
                    """,
                    (PHYSICAL_PRESSURE_BATCH,),
                ).fetchall()
                if event_rows:
                    for row in event_rows:
                        self._replace_span_with_gap(
                            conn,
                            int(row[0]),
                            int(row[1]),
                            "spool_overflow",
                            "physical_limit",
                        )
                else:
                    quarantine_ids = conn.execute(
                        """
                        SELECT quarantine_id FROM quarantine
                         ORDER BY quarantine_id LIMIT ?
                        """,
                        (PHYSICAL_PRESSURE_BATCH,),
                    ).fetchall()
                    if quarantine_ids:
                        conn.executemany(
                            "DELETE FROM quarantine WHERE quarantine_id = ?",
                            quarantine_ids,
                        )
                    else:
                        span = conn.execute(
                            "SELECT MIN(sequence_start), MAX(sequence_end), COUNT(*) FROM outbox"
                        ).fetchone()
                        if not span or int(span[2]) == 0:
                            conn.rollback()
                            raise RuntimeError(
                                "edge spool schema exceeds the hard physical byte limit"
                            )
                        previous_epoch = self._metadata_from(conn, "epoch")
                        conn.execute("DELETE FROM outbox")
                        self._set_metadata(conn, "epoch", str(uuid4()))
                        self._set_metadata(conn, "next_sequence", "1")
                        now = self._now()
                        self._enqueue_locked(
                            conn,
                            {
                                "schema_version": SCHEMA_VERSION,
                                "source_event_type": EVENT_GAP,
                                "occurred_at": _timestamp(now),
                                "received_at": _timestamp(now),
                                "reason_code": "spool_capacity_epoch_rollover",
                                "previous_epoch": previous_epoch,
                                "lost_sequence_start": int(span[0]),
                                "lost_sequence_end": int(span[1]),
                            },
                            now,
                        )
                        rollovers += 1
                conn.commit()

            self._compact_storage()
            current_size = self._physical_bytes()
            if prior_size is not None and current_size >= prior_size and not event_rows:
                # The next pass will use the epoch-rollover branch. Keeping the
                # loop bounded makes a corrupt/hostile SQLite file fail closed
                # instead of consuming unbounded CPU during HA startup.
                pass
            prior_size = current_size

        raise RuntimeError("edge spool cannot satisfy the hard physical byte limit")

    def _harden_permissions(self) -> None:
        if os.name != "posix":
            return
        os.chmod(self.path.parent, 0o700)
        for candidate in self._physical_paths():
            if candidate.exists():
                os.chmod(candidate, 0o600)
        if stat.S_IMODE(self.path.parent.stat().st_mode) & 0o077:
            raise PermissionError("Home Agent Edge spool directory must be mode 0700")
        for candidate in self._physical_paths():
            if candidate.exists() and stat.S_IMODE(candidate.stat().st_mode) & 0o077:
                raise PermissionError("Home Agent Edge spool files must be mode 0600")

    def _physical_paths(self) -> tuple[Path, Path, Path]:
        return (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        )

    def _physical_bytes(self) -> int:
        return sum(
            candidate.stat().st_size if candidate.exists() else 0
            for candidate in self._physical_paths()
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            factory=_ClosingConnection,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA secure_delete=ON")
        if int(conn.execute("PRAGMA auto_vacuum").fetchone()[0]) != (
            SQLITE_AUTO_VACUUM_FULL
        ):
            conn.close()
            raise RuntimeError("edge spool auto_vacuum is not fully enabled")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_size_limit=8388608")
        return conn


def _timestamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _projection_type(source: Mapping[str, Any]) -> str:
    entity_id = str(source.get("entity_id") or "")
    if entity_id.startswith("person."):
        return "person_projection"
    if entity_id.startswith("device_tracker."):
        return "device_tracker"
    if entity_id.startswith("zone."):
        return "zone"
    if source.get("source_event_type") == EVENT_CONVERSATION_FINISHED:
        return "conversation_turn"
    if source.get("source_event_type") == EVENT_GAP:
        return "coverage_gap"
    return "entity_state"


def _lineage_for(source: Mapping[str, Any]) -> tuple[str | None, str | None, str]:
    entity_id = str(source.get("entity_id") or "")
    if not entity_id.startswith(("person.", "device_tracker.", "zone.")):
        return None, None, f"home_assistant:{entity_id or 'event'}"[:128]
    new_state = source.get("new_state") or {}
    attributes = new_state.get("attributes") if isinstance(new_state, Mapping) else {}
    attributes = attributes if isinstance(attributes, Mapping) else {}
    root_source = str(attributes.get("source") or entity_id)
    observed = str(new_state.get("last_updated") or source.get("occurred_at") or "unknown")
    # Durable lineage IDs must not be an offline oracle for raw GPS. The
    # prior UUIDv5 material included latitude/longitude, which allowed anyone
    # holding a PostgreSQL backup to enumerate nearby coordinate grids using
    # the durable source/timestamp. Source + sensor observation time identifies
    # the underlying observation family without encoding its measured value.
    material = "/".join((root_source, observed))
    root = str(uuid5(NAMESPACE_URL, f"home-agent-edge/location/{material}"))
    return root, root, f"home_assistant.location:{root_source}"[:128]
