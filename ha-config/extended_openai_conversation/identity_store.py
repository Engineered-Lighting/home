"""SQLite-backed identity + relationship + preference store.

Companion to world_state.py. Where world_state holds VOLATILE state
(who's in which room right now), identity_store holds PERSISTENT
context (who Sarah IS, that she's my partner, that she prefers warm
light). The two are linked: world_state's `_people[<frigate_name>]`
rows are projected through identity_store to resolve display names,
relationships, and privacy flags before the LLM (or the Home app
UI) ever sees them.

Why a separate file
-------------------
Different lifecycle. world_state rebuilds from HA's state bus on
boot. identity_store is the durable record — survives restarts +
backups + restore. Mixing the two would force one or the other to
compromise (volatile data churning the durable store, or durable
schema gating volatile reads).

What this module owns
---------------------
Tables (see schema.sql):
- identities         — one row per known person; typed privacy flags
- identity_aliases   — many aliases per identity (incl. Frigate names)
- enrollments        — one row per Frigate face cluster; many-to-one
                       to identities (per AR-3)
- relationships      — directed edges with type + status (incl. "ended"
                       per AR-6); from_id / to_id reference identities
- preferences        — per-identity key/value with scope + source +
                       confidence + provenance
- face_crops_meta    — references to Frigate-stored crops (we don't
                       own bytes; per AR-7)
- change_log         — append-only audit + Frigate writeback queue
                       (per AR-10, AR2-7)
- pending_writes     — outbox for cross-system mutations (Frigate
                       rename + delete; per AR-8)

Public API (mirror world_state's surface):
- IdentityStore(hass).async_setup() — opens db, applies schema
- get_identity(uuid_or_alias)       — single lookup
- list_identities(filter)           — for the people-tab list
- create_identity(display_name, ...) — insert; returns uuid
- update_identity(uuid, patch)       — partial update + audit row
- delete_identity(uuid, options)     — cascade delete + queued writeback
- list_relationships(uuid)           — edges originating from this id
- add_relationship(from, to, type)   — insert edge
- list_preferences(uuid, scope?)     — get prefs for context injection
- set_preference(uuid, key, value, ...) — upsert + audit row
- enqueue_frigate_write(op, payload) — outbox for the reconciler
- resolve_frigate_name(name)         — Frigate name → identity row
                                        (used by world_state projection)

Disable
-------
Set env var `EXTENDED_OPENAI_IDENTITY_STORE=off` to disable. All
methods become no-ops returning empty results. Single rollback knob.

Threat model + privacy
----------------------
- Trusted-LAN + trusted-HAOS-admin model (per AR-4).
- All data local. Face crops are referenced in face_crops_meta but
  the bytes live in Frigate's volume (per AR-7).
- Names + relationships are extracted from the prompt before any
  external routing happens (per US-5.08 / AR-2).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import threading
import time
import uuid as _uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TYPE_CHECKING

try:
    from .legacy_identity_fence import (
        EXPECTED_TRIGGER_DIGEST,
        LEGACY_APPLICATION_FROZEN_UNPROVEN,
        LEGACY_FROZEN_PENDING_CUTOVER,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        OBSOLETE_PRE_E4_AUTHORITY_VALUE,
        SEMANTIC_AUTHORITY_KEY,
        SEMANTIC_WRITE_FENCE_GENERATION,
        cutover_witness_path,
        ensure_cutover_witness,
        fence_artifacts_present,
        require_unfenced_schema_meta_contract,
        scrub_legacy_audit_content,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )
except ImportError:
    from legacy_identity_fence import (  # type: ignore[no-redef]
        EXPECTED_TRIGGER_DIGEST,
        LEGACY_APPLICATION_FROZEN_UNPROVEN,
        LEGACY_FROZEN_PENDING_CUTOVER,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        OBSOLETE_PRE_E4_AUTHORITY_VALUE,
        SEMANTIC_AUTHORITY_KEY,
        SEMANTIC_WRITE_FENCE_GENERATION,
        cutover_witness_path,
        ensure_cutover_witness,
        fence_artifacts_present,
        require_unfenced_schema_meta_contract,
        scrub_legacy_audit_content,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

IDENTITY_STORE_DISABLED_ENV = "EXTENDED_OPENAI_IDENTITY_STORE"
SEMANTIC_WRITES_MODE_ENV = "EXTENDED_OPENAI_IDENTITY_SEMANTIC_WRITES"
LEGACY_MIGRATION_MODE = "legacy_migration_only"
# Temporary, non-authoritative compatibility exception for the production
# People profile fix. This updates only the legacy self-profile projection and
# must never be accepted as Core evidence or authority. It is deliberately
# narrower than the legacy PATCH surface: relationships, names, notes, privacy
# directives, and HA bindings remain Core authority.
LEGACY_SELF_PROFILE_EDIT_FIELDS = {"pronouns"}
RECOGNITION_PRIVACY_FIELDS = {
    "do_not_track",
    "is_ignored",
    "is_private",
    "is_silent",
    "relationship_type",
    "auto_expire_at",
}

# Schema version. Bump when columns change; migrations run on open.
SCHEMA_VERSION = 1

# Relationship types — explicit allow-list. Schema enforces via CHECK.
# Mirrors US-1.13 through US-1.22.
RELATIONSHIP_TYPES = (
    "me",                  # only one identity may have this
    "partner",
    "family_immediate",    # parent/sibling/child
    "family_extended",     # in-law/cousin/etc.
    "roommate",            # co-resident, not family
    "friend",
    "neighbor",
    "service",             # delivery / contractor / cleaner
    "guest",               # transient
    "unknown",             # detected, not yet enrolled
    "do_not_identify",     # explicitly excluded from enrollment
)

# Relationship status — supports US-2.16 (ended relationships preserved).
RELATIONSHIP_STATUSES = ("active", "ended", "paused")

# Preference sources — manual takes precedence over inferred in conflict.
PREFERENCE_SOURCES = ("manual", "inferred")

# change_log "kind" values — per AR-10 retention table is keyed on this.
CHANGE_LOG_KINDS = (
    "detection",            # 7d rolling
    "identity_mutation",    # 90d
    "assistant_action",     # 30d + user-pinnable
    "frigate_writeback",    # 90d
    "pending_write_state",  # 30d (state changes on pending_writes rows)
)

# Retention in seconds — per AR2-7 + locked-decision audit log = 30d.
CHANGE_LOG_RETENTION = {
    "detection":           7 * 24 * 3600,
    "identity_mutation":   90 * 24 * 3600,
    "assistant_action":    30 * 24 * 3600,
    "frigate_writeback":   90 * 24 * 3600,
    "pending_write_state": 30 * 24 * 3600,
}


# ── Schema DDL ──────────────────────────────────────────────────────

SCHEMA_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- identities: one row per known person. Privacy flags are typed columns
-- per AR-5 (not a junk-drawer flag_name/flag_value table).
CREATE TABLE IF NOT EXISTS identities (
    uuid TEXT PRIMARY KEY,                       -- internal stable id
    display_name TEXT NOT NULL,
    pronouns TEXT DEFAULT NULL,                  -- freeform (defaults to they/them in app)
    relationship_type TEXT DEFAULT 'unknown'
        CHECK (relationship_type IN (
            'me','partner','family_immediate','family_extended',
            'roommate','friend','neighbor','service','guest',
            'unknown','do_not_identify'
        )),
    relationship_subrole TEXT DEFAULT NULL,      -- e.g. 'sibling','parent','contractor'
    notes TEXT DEFAULT NULL,                     -- freeform LLM-readable
    is_private INTEGER NOT NULL DEFAULT 0,       -- no proactive announcements
    is_silent  INTEGER NOT NULL DEFAULT 0,       -- detected but never spoken/written
    is_ignored INTEGER NOT NULL DEFAULT 0,       -- silently dropped at aggregator (AR-7)
    is_archived INTEGER NOT NULL DEFAULT 0,      -- soft-delete; restorable
    do_not_track INTEGER NOT NULL DEFAULT 0,     -- per-person opt-out (US-5.17)
    auto_expire_at TEXT DEFAULT NULL,            -- ISO ts; guest auto-purge
    ha_person_entity_id TEXT DEFAULT NULL,       -- e.g. 'person.marcelo' (AR2-16)
    ha_device_tracker_entity_id TEXT DEFAULT NULL,
    flags_extra TEXT NOT NULL DEFAULT '{}',      -- JSON; future flags before they earn a column
    version INTEGER NOT NULL DEFAULT 1,          -- optimistic concurrency (AR-9)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_identities_relationship_type ON identities(relationship_type);
CREATE INDEX IF NOT EXISTS idx_identities_is_archived ON identities(is_archived);
CREATE INDEX IF NOT EXISTS idx_identities_ha_person ON identities(ha_person_entity_id);

-- Aliases: every name + Frigate-cluster-name maps here.
CREATE TABLE IF NOT EXISTS identity_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_uuid TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL DEFAULT 'name'
        CHECK (alias_kind IN ('name','frigate_name','nickname')),
    created_at TEXT NOT NULL,
    UNIQUE(alias, alias_kind)
);
CREATE INDEX IF NOT EXISTS idx_aliases_uuid ON identity_aliases(identity_uuid);

-- Enrollments: one row per Frigate face cluster (AR-3). A real person
-- can have multiple enrollments (glasses on/off, different cameras).
CREATE TABLE IF NOT EXISTS enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_uuid TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    frigate_person_name TEXT NOT NULL,           -- as stored in Frigate
    quality REAL DEFAULT NULL,                   -- cluster purity 0..1 (AR2-17)
    sample_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    retired_at TEXT DEFAULT NULL,
    UNIQUE(frigate_person_name)
);
CREATE INDEX IF NOT EXISTS idx_enrollments_uuid ON enrollments(identity_uuid);

-- Relationships: directed edges. Bidirectional logical relations
-- (e.g. partner) are stored as TWO rows for symmetric lookup.
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uuid TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    to_uuid   TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    rel_type TEXT NOT NULL,                      -- 'partner','sibling','parent','friend','colleague','roommate'...
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','ended','paused')),
    edge_data TEXT NOT NULL DEFAULT '{}',        -- JSON: 'since', 'notes', etc.
    created_at TEXT NOT NULL,
    ended_at TEXT DEFAULT NULL,
    UNIQUE(from_uuid, to_uuid, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_uuid);
CREATE INDEX IF NOT EXISTS idx_rel_to   ON relationships(to_uuid);
CREATE INDEX IF NOT EXISTS idx_rel_active ON relationships(status) WHERE status = 'active';

-- Preferences: per-identity key/value with scope + source.
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_uuid TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    key TEXT NOT NULL,                           -- 'light_temp_preference', 'music_genre', etc.
    value TEXT NOT NULL,                         -- JSON-encoded
    scope TEXT NOT NULL DEFAULT '{}',            -- JSON: {'time_of_day':'morning','room':'kitchen'}
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual','inferred')),
    confidence REAL DEFAULT NULL,                -- 0..1 for inferred
    provenance TEXT NOT NULL DEFAULT '{}',       -- JSON: what observations led to this (US-4.14)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(identity_uuid, key, scope)
);
CREATE INDEX IF NOT EXISTS idx_pref_uuid ON preferences(identity_uuid);
CREATE INDEX IF NOT EXISTS idx_pref_source ON preferences(source);

-- face_crops_meta: references to Frigate-stored crops. Bytes live in
-- Frigate's volume (AR-7). retention_until supports guest auto-expire.
CREATE TABLE IF NOT EXISTS face_crops_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_uuid TEXT NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    frigate_event_id TEXT NOT NULL,              -- key into Frigate's events
    source_camera TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    score REAL DEFAULT NULL,                     -- recognition confidence
    retention_until TEXT DEFAULT NULL,           -- ISO ts; auto-purge after
    UNIQUE(frigate_event_id)
);
CREATE INDEX IF NOT EXISTS idx_crops_uuid ON face_crops_meta(identity_uuid);
CREATE INDEX IF NOT EXISTS idx_crops_retention ON face_crops_meta(retention_until);

-- change_log: append-only audit + writeback queue. Retention by kind
-- per AR2-7.
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN (
            'detection','identity_mutation','assistant_action',
            'frigate_writeback','pending_write_state'
        )),
    actor TEXT NOT NULL DEFAULT 'system',        -- 'system','user','llm','frigate'
    target_uuid TEXT DEFAULT NULL,               -- identity touched
    before_json TEXT DEFAULT NULL,               -- JSON snapshot
    after_json TEXT DEFAULT NULL,                -- JSON snapshot
    link_conv_id TEXT DEFAULT NULL,              -- ties to routing-log conv_id
    link_turn_id TEXT DEFAULT NULL,
    link_tool_idx INTEGER DEFAULT NULL,
    pinned INTEGER NOT NULL DEFAULT 0            -- US-5.21: user can pin rows
);
CREATE INDEX IF NOT EXISTS idx_change_log_ts ON change_log(ts);
CREATE INDEX IF NOT EXISTS idx_change_log_kind ON change_log(kind);
CREATE INDEX IF NOT EXISTS idx_change_log_target ON change_log(target_uuid);
CREATE INDEX IF NOT EXISTS idx_change_log_conv ON change_log(link_conv_id);

-- pending_writes: outbox for cross-system mutations (Frigate rename +
-- delete; per AR-8). Reconciler drains this.
CREATE TABLE IF NOT EXISTS pending_writes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_system TEXT NOT NULL DEFAULT 'frigate'
        CHECK (target_system IN ('frigate')),
    op TEXT NOT NULL                              -- 'rename','delete'
        CHECK (op IN ('rename','delete')),
    payload TEXT NOT NULL,                        -- JSON
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','in_flight','done','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_state ON pending_writes(state);
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_writes(created_at);
"""

OPERATIONAL_RECOGNITION_TABLE_SQL = """
CREATE TABLE operational_recognition_enrollments (
    frigate_person_name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE
        CHECK (
            typeof(frigate_person_name) = 'text'
            AND length(frigate_person_name) BETWEEN 1 AND 255
        ),
    source TEXT NOT NULL DEFAULT 'frigate'
        CHECK (source = 'frigate'),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

OPERATIONAL_STORE_META_TABLE_SQL = """
CREATE TABLE operational_store_meta (
    key TEXT PRIMARY KEY NOT NULL
        CHECK (
            typeof(key) = 'text'
            AND key IN ('contract', 'generation', 'instance_id')
        ),
    value TEXT NOT NULL CHECK (typeof(value) = 'text')
)
"""

OPERATIONAL_STORE_CONTRACT = "home_agent_operational_recognition_v1"
OPERATIONAL_STORE_GENERATION = "1"

# The only pre-marker schema accepted for the one-time prerelease cleanup.
OPERATIONAL_RECOGNITION_PRERELEASE_TABLE_SQL = """
CREATE TABLE operational_recognition_enrollments (
    frigate_person_name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
    display_label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'frigate',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

OPERATIONAL_RECOGNITION_DDL = (
    "PRAGMA journal_mode = WAL;\n"
    f"{OPERATIONAL_RECOGNITION_TABLE_SQL};\n"
    f"{OPERATIONAL_STORE_META_TABLE_SQL};"
)


# ── Utility ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with seconds precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_uuid() -> str:
    """Stable internal identifier — UUIDv4, hex."""
    return _uuid.uuid4().hex


def is_disabled() -> bool:
    """Single-knob rollback per the module docstring."""
    return os.environ.get(IDENTITY_STORE_DISABLED_ENV, "").lower() == "off"


# ── Dataclasses for typed reads (optional; callers can also use rows) ─


@dataclass
class IdentityRow:
    uuid: str
    display_name: str
    pronouns: Optional[str] = None
    relationship_type: str = "unknown"
    relationship_subrole: Optional[str] = None
    notes: Optional[str] = None
    is_private: bool = False
    is_silent: bool = False
    is_ignored: bool = False
    is_archived: bool = False
    do_not_track: bool = False
    auto_expire_at: Optional[str] = None
    ha_person_entity_id: Optional[str] = None
    ha_device_tracker_entity_id: Optional[str] = None
    flags_extra: dict = field(default_factory=dict)
    aliases: list = field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass
class OperationalRecognitionRow:
    frigate_person_name: str
    source: str = "frigate"
    created_at: str = ""
    updated_at: str = ""


# ── IdentityStore (the API surface) ─────────────────────────────────


class IdentityStore:
    """Thread-safe SQLite-backed identity store.

    HA's integration loop is single-process; multiple writers (aggregator,
    UI edits, reconciler, daily purge) all go through this one object.
    Internal lock guards multi-step transactions (AR-9).
    """

    def __init__(
        self,
        hass: "HomeAssistant | None" = None,
        db_path: Optional[str] = None,
        operational_db_path: Optional[str] = None,
        core_recognition_policy: Optional[Callable[[str], str]] = None,
    ):
        self.hass = hass
        # In HA: /config/extended_openai_conversation/identity.db
        # In pytest: caller passes db_path=":memory:" or a tmp file
        if db_path is None:
            if hass is not None:
                db_path = str(Path(hass.config.config_dir) / "extended_openai_conversation" / "identity.db")
            else:
                db_path = ":memory:"
        self.db_path = db_path
        if operational_db_path is None:
            if db_path == ":memory:":
                operational_db_path = ":memory:"
            else:
                semantic_path = Path(db_path)
                operational_db_path = str(
                    semantic_path.with_name(
                        f"{semantic_path.stem}-recognition{semantic_path.suffix}"
                    )
                )
        self.operational_db_path = operational_db_path
        self._core_recognition_policy = core_recognition_policy
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._operational_conn: Optional[sqlite3.Connection] = None
        self._operational_path_identity: Optional[tuple[int, int]] = None
        self._operational_instance_id: Optional[str] = None
        self._process_lock: Optional[LegacyIdentityProcessLock] = None
        self._disabled = is_disabled()
        # Fail closed. Legacy semantic writes are permitted only during an
        # explicitly configured pre-cutover migration window, and never after
        # the explicit legacy freeze marker has been written.
        self._semantic_writes_frozen = True
        # The E4 fence is stronger than the earlier application-mode marker:
        # it is an explicit offline ceremony backed by an exact SQLite trigger
        # contract. Before this marker only, HA administrators retain the
        # narrow unique-self pronoun compatibility edit.
        self._semantic_write_fence_installed = False
        self._fence_verification_fingerprint: Optional[tuple] = None
        # Addendum 16 F-4a: set by the integration when setup() raises so
        # the IdentityListView can report `ready: false` + the Tauri
        # overlay can render a clear banner instead of "empty store".
        self.setup_error: Optional[str] = None
        # Addendum 16 F-5a: hass handle for firing identity_mutation
        # bus events from _audit(). Set lazily via set_hass_for_events
        # so unit tests with hass=None don't trigger an event fire.
        self._event_hass = None

    def set_hass_for_events(self, hass) -> None:
        """Wire hass for identity_mutation bus events. Idempotent."""
        self._event_hass = hass

    # ─── Setup / teardown ────────────────────────────────────────────

    def setup(self) -> None:
        """Open DB + apply schema. Safe to call repeatedly."""
        if self._disabled:
            LOGGER.info("identity_store disabled via env var")
            return
        with self._lock:
            if self._conn is not None:
                return
            try:
                self._validate_database_paths()
                if self.db_path != ":memory:":
                    Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
                    self._process_lock = LegacyIdentityProcessLock(
                        self.db_path
                    ).acquire()
                    self._validate_database_paths()
                existing_file = (
                    self.db_path != ":memory:"
                    and Path(self.db_path).is_file()
                )
                witness_required = (
                    self.db_path != ":memory:"
                    and verify_cutover_witness(
                        self.db_path,
                        schema_ddl=SCHEMA_DDL,
                    )
                )
                if witness_required and not existing_file:
                    raise LegacyIdentityFenceError(
                        "legacy_cutover_witness_requires_fenced_database"
                    )
                self._conn = self._open_semantic_connection(
                    read_only=existing_file
                )
                self._validate_database_paths()

                # Probe an existing file through a query-only connection.
                # A fenced database is therefore never opened writable, even
                # briefly, before its exact contract is verified.
                artifacts_present = fence_artifacts_present(self._conn)
                if witness_required and not artifacts_present:
                    raise LegacyIdentityFenceError(
                        "legacy_cutover_witness_requires_fenced_database"
                    )
                if artifacts_present:
                    self._semantic_write_fence_installed = (
                        verify_semantic_write_fence(
                            self._conn,
                            schema_ddl=SCHEMA_DDL,
                        )
                    )
                    if not self._semantic_write_fence_installed:
                        raise LegacyIdentityFenceError(
                            "semantic_write_fence_partial"
                        )
                    self._semantic_writes_frozen = True
                    if self.db_path != ":memory:" and not witness_required:
                        ensure_cutover_witness(
                            self.db_path,
                            schema_ddl=SCHEMA_DDL,
                        )
                else:
                    if existing_file:
                        self._conn.close()
                        self._conn = self._open_semantic_connection(
                            read_only=False
                        )
                    self._setup_mutable_semantic_store(
                        new_database=not existing_file
                    )

                self._setup_operational_recognition_store()
                self._validate_database_paths()
                if self._semantic_write_fence_installed:
                    # Operational setup is a separate SQLite lifecycle. Prove
                    # path separation and the semantic fence again after it.
                    self.assert_legacy_read_current()
                self.setup_error = None
                LOGGER.info(
                    "identity_store opened at %s (schema v%d, fenced=%s)",
                    self.db_path,
                    SCHEMA_VERSION,
                    self._semantic_write_fence_installed,
                )
            except Exception:
                self._semantic_writes_frozen = True
                if self._operational_conn is not None:
                    self._operational_conn.close()
                    self._operational_conn = None
                self._operational_path_identity = None
                self._operational_instance_id = None
                if self._conn is not None:
                    self._conn.close()
                self._conn = None
                self._fence_verification_fingerprint = None
                if self._process_lock is not None:
                    self._process_lock.release()
                    self._process_lock = None
                raise

    def _open_semantic_connection(
        self,
        *,
        read_only: bool,
    ) -> sqlite3.Connection:
        if read_only and self.db_path != ":memory:":
            resolved = Path(self.db_path).expanduser().resolve().as_posix()
            connection = sqlite3.connect(
                f"file:{resolved}?mode=ro",
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
        else:
            connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        else:
            connection.execute("PRAGMA secure_delete = ON")
        return connection

    @staticmethod
    def _absolute_database_path(value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(os.path.abspath(path))
        return path

    def _validate_database_paths(self) -> None:
        """Reject aliases that could merge semantic and operational stores."""

        semantic = (
            None
            if self.db_path == ":memory:"
            else self._absolute_database_path(self.db_path)
        )
        operational = (
            None
            if self.operational_db_path == ":memory:"
            else self._absolute_database_path(self.operational_db_path)
        )
        for label, path in (
            ("semantic", semantic),
            ("operational", operational),
        ):
            if path is None:
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise LegacyIdentityFenceError(
                    "legacy_identity_database_symlink_rejected"
                )
            if info.st_nlink != 1:
                raise LegacyIdentityFenceError(
                    f"legacy_identity_{label}_database_link_count_invalid"
                )
        if semantic is None or operational is None:
            return
        semantic_resolved = semantic.resolve(strict=False)
        operational_resolved = operational.resolve(strict=False)
        if os.path.normcase(str(semantic_resolved)) == os.path.normcase(
            str(operational_resolved)
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_database_path_collision"
            )
        if semantic.exists() and operational.exists():
            try:
                same_file = os.path.samefile(semantic, operational)
            except OSError as exc:
                raise LegacyIdentityFenceError(
                    "legacy_identity_database_path_unverifiable"
                ) from exc
            if same_file:
                raise LegacyIdentityFenceError(
                    "legacy_identity_database_inode_collision"
                )
        witness = cutover_witness_path(semantic)
        if os.path.normcase(str(operational_resolved)) == os.path.normcase(
            str(witness.resolve(strict=False))
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_witness_path_collision"
            )
        if operational.exists() and witness.exists():
            try:
                if os.path.samefile(operational, witness):
                    raise LegacyIdentityFenceError(
                        "legacy_identity_operational_witness_inode_collision"
                    )
            except LegacyIdentityFenceError:
                raise
            except OSError as exc:
                raise LegacyIdentityFenceError(
                    "legacy_identity_database_path_unverifiable"
                ) from exc

    def _setup_mutable_semantic_store(
        self,
        *,
        new_database: bool,
    ) -> None:
        assert self._conn is not None
        self._conn.executescript(SCHEMA_DDL)
        metadata = {
            str(row["key"]): str(row["value"])
            for row in self._conn.execute(
                "SELECT key, value FROM schema_meta"
            ).fetchall()
        }
        if new_database:
            if metadata:
                raise LegacyIdentityFenceError(
                    "legacy_identity_new_database_metadata_present"
                )
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) "
                "VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif metadata.get("schema_version") != str(SCHEMA_VERSION):
            raise LegacyIdentityFenceError(
                "legacy_identity_schema_version_mismatch"
            )
        if set(metadata) - {"schema_version", SEMANTIC_AUTHORITY_KEY}:
            raise LegacyIdentityFenceError(
                "legacy_identity_schema_meta_key_set_mismatch"
            )
        authority_row = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (SEMANTIC_AUTHORITY_KEY,),
        ).fetchone()
        requested_mode = os.environ.get(SEMANTIC_WRITES_MODE_ENV, "").strip()
        if authority_row is not None:
            self._semantic_writes_frozen = True
            if authority_row["value"] == OBSOLETE_PRE_E4_AUTHORITY_VALUE:
                self._conn.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = ?",
                    (
                        LEGACY_APPLICATION_FROZEN_UNPROVEN,
                        SEMANTIC_AUTHORITY_KEY,
                    ),
                )
            elif (
                authority_row["value"]
                != LEGACY_APPLICATION_FROZEN_UNPROVEN
            ):
                raise LegacyIdentityFenceError(
                    "semantic_authority_marker_conflict"
                )
        elif requested_mode == LEGACY_MIGRATION_MODE:
            self._semantic_writes_frozen = False
        else:
            self._semantic_writes_frozen = True
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
                (
                    SEMANTIC_AUTHORITY_KEY,
                    LEGACY_APPLICATION_FROZEN_UNPROVEN,
                ),
            )
        # Historical audit rows could contain full identity snapshots,
        # notes, relationship data, and deleted names. Preserve only the
        # content-free audit envelope; never read or copy the old JSON.
        scrubbed = scrub_legacy_audit_content(self._conn)
        if scrubbed > 0 and self.db_path != ":memory:":
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
        require_unfenced_schema_meta_contract(self._conn)

    def _setup_operational_recognition_store(self) -> None:
        self._validate_database_paths()
        prior_identity = self._operational_database_identity()
        if self.operational_db_path != ":memory:":
            Path(self.operational_db_path).parent.mkdir(
                parents=True,
                exist_ok=True,
            )
        self._operational_conn = sqlite3.connect(
            self.operational_db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._operational_conn.row_factory = sqlite3.Row
        # This sqlite_master read is deliberately the first statement issued
        # through the opened handle. A redirected connect must be rejected
        # before any PRAGMA, DDL, or row mutation reaches the wrong database.
        initial_objects = self._inspect_operational_objects(
            self._operational_conn
        )
        initial_shape = self._classify_operational_shape(initial_objects)
        self._require_operational_connection_targets_visible_path(
            self._operational_conn
        )
        self._validate_database_paths()
        opened_identity = self._operational_database_identity()
        if (
            prior_identity is not None
            and opened_identity != prior_identity
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_database_substituted"
            )
        self._operational_path_identity = opened_identity
        if initial_shape == "current":
            instance_id = self._read_operational_contract(
                self._operational_conn,
                objects=initial_objects,
            )
            self._verify_visible_operational_store(
                expected_instance_id=instance_id,
            )
        else:
            # Bind the unmarked handle to the visible path before allowing its
            # one-time schema upgrade. The fresh reader must see the exact
            # reviewed empty/current/prerelease shape seen by this handle.
            self._verify_visible_operational_store(
                expected_unmarked_objects=initial_objects,
            )
            self._operational_conn.execute("PRAGMA secure_delete = ON")
            journal_mode = self._operational_conn.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()
            expected_journal_mode = (
                "memory"
                if self.operational_db_path == ":memory:"
                else "wal"
            )
            if (
                journal_mode is None
                or str(journal_mode[0]).lower()
                != expected_journal_mode
            ):
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_journal_mode_not_wal"
                )
            if initial_shape == "prerelease":
                # E4 prerelease schema cleanup: remove the duplicated semantic
                # display label while retaining only the opaque identifier.
                self._operational_conn.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE operational_recognition_enrollments_v2 (
                        frigate_person_name TEXT PRIMARY KEY NOT NULL
                            COLLATE NOCASE
                            CHECK (
                                typeof(frigate_person_name) = 'text'
                                AND length(frigate_person_name)
                                    BETWEEN 1 AND 255
                            ),
                        source TEXT NOT NULL DEFAULT 'frigate'
                            CHECK (source = 'frigate'),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT OR IGNORE INTO
                        operational_recognition_enrollments_v2(
                            frigate_person_name, source, created_at, updated_at
                        )
                    SELECT frigate_person_name, source, created_at, updated_at
                    FROM operational_recognition_enrollments;
                    DROP TABLE operational_recognition_enrollments;
                    ALTER TABLE operational_recognition_enrollments_v2
                        RENAME TO operational_recognition_enrollments;
                    COMMIT;
                    """
                )
                self._operational_conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                )
                self._operational_conn.execute("VACUUM")
                self._operational_conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                )
            self._validate_database_paths()
            if (
                self._operational_database_identity()
                != self._operational_path_identity
            ):
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_database_substituted"
                )
            self._operational_conn.execute("BEGIN IMMEDIATE")
            try:
                if initial_shape == "empty":
                    self._operational_conn.execute(
                        OPERATIONAL_RECOGNITION_TABLE_SQL
                    )
                self._operational_conn.execute(
                    OPERATIONAL_STORE_META_TABLE_SQL
                )
                instance_id = _uuid.uuid4().hex
                self._operational_conn.executemany(
                    "INSERT INTO operational_store_meta(key, value) "
                    "VALUES(?, ?)",
                    (
                        ("contract", OPERATIONAL_STORE_CONTRACT),
                        ("generation", OPERATIONAL_STORE_GENERATION),
                        ("instance_id", instance_id),
                    ),
                )
                self._operational_conn.execute("COMMIT")
            except Exception:
                self._operational_conn.execute("ROLLBACK")
                raise
            current_objects = self._inspect_operational_objects(
                self._operational_conn
            )
            self._classify_operational_shape(current_objects)
            if (
                self._read_operational_contract(
                    self._operational_conn,
                    objects=current_objects,
                )
                != instance_id
            ):
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_marker_commit_failed"
                )
            self._verify_visible_operational_store(
                expected_instance_id=instance_id,
            )

        self._operational_instance_id = instance_id
        self._operational_conn.execute("PRAGMA secure_delete = ON")
        self._require_operational_database_current()
        self.purge_blocked_operational_recognition()

    @staticmethod
    def _normalize_operational_sql(value: str) -> str:
        normalized = " ".join((value or "").strip().rstrip(";").split())
        for name in (
            "operational_recognition_enrollments",
            "operational_store_meta",
        ):
            normalized = normalized.replace(
                f'CREATE TABLE "{name}"',
                f"CREATE TABLE {name}",
                1,
            )
        return normalized

    def _inspect_operational_objects(
        self,
        connection: sqlite3.Connection,
    ) -> dict[str, tuple[str, str]]:
        rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        objects: dict[str, tuple[str, str]] = {}
        for row in rows:
            object_type = str(row["type"])
            name = str(row["name"])
            sql = row["sql"]
            if object_type != "table" or type(sql) is not str:
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_unknown_object"
                )
            if name not in {
                "operational_recognition_enrollments",
                "operational_store_meta",
            }:
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_unknown_object"
                )
            objects[name] = (
                object_type,
                self._normalize_operational_sql(sql),
            )
        return objects

    def _classify_operational_shape(
        self,
        objects: dict[str, tuple[str, str]],
    ) -> str:
        names = set(objects)
        if not names:
            return "empty"
        expected_current = self._normalize_operational_sql(
            OPERATIONAL_RECOGNITION_TABLE_SQL
        )
        recognition = objects.get(
            "operational_recognition_enrollments"
        )
        if recognition is None:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_schema_mismatch"
            )
        recognition_sql = recognition[1]
        if names == {"operational_recognition_enrollments"}:
            if recognition_sql == expected_current:
                return "unmarked_current"
            if recognition_sql == self._normalize_operational_sql(
                OPERATIONAL_RECOGNITION_PRERELEASE_TABLE_SQL
            ):
                return "prerelease"
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_schema_mismatch"
            )
        if names != {
            "operational_recognition_enrollments",
            "operational_store_meta",
        }:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_schema_mismatch"
            )
        if recognition_sql != expected_current:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_schema_mismatch"
            )
        if objects["operational_store_meta"][1] != (
            self._normalize_operational_sql(
                OPERATIONAL_STORE_META_TABLE_SQL
            )
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_meta_schema_mismatch"
            )
        return "current"

    def _read_operational_contract(
        self,
        connection: sqlite3.Connection,
        *,
        objects: Optional[dict[str, tuple[str, str]]] = None,
    ) -> str:
        current_objects = (
            objects
            if objects is not None
            else self._inspect_operational_objects(connection)
        )
        if self._classify_operational_shape(current_objects) != "current":
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_marker_missing"
            )
        rows = connection.execute(
            "SELECT key, value FROM operational_store_meta ORDER BY key"
        ).fetchall()
        marker: dict[str, str] = {}
        for row in rows:
            if type(row["key"]) is not str or type(row["value"]) is not str:
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_marker_non_text"
                )
            marker[row["key"]] = row["value"]
        if set(marker) != {"contract", "generation", "instance_id"}:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_marker_key_set_mismatch"
            )
        if marker["contract"] != OPERATIONAL_STORE_CONTRACT:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_marker_contract_mismatch"
            )
        if marker["generation"] != OPERATIONAL_STORE_GENERATION:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_marker_generation_mismatch"
            )
        instance_id = marker["instance_id"]
        try:
            parsed = _uuid.UUID(hex=instance_id)
        except (AttributeError, ValueError) as exc:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_instance_id_invalid"
            ) from exc
        if (
            parsed.version != 4
            or parsed.hex != instance_id
            or len(instance_id) != 32
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_instance_id_invalid"
            )
        return instance_id

    def _require_operational_connection_targets_visible_path(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute("PRAGMA database_list").fetchall()
        main_rows = [row for row in rows if str(row["name"]) == "main"]
        if len(main_rows) != 1:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_database_binding_invalid"
            )
        actual = str(main_rows[0]["file"] or "")
        if self.operational_db_path == ":memory:":
            if actual:
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_database_binding_mismatch"
                )
            return
        if not actual:
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_database_binding_mismatch"
            )
        visible = self._absolute_database_path(
            self.operational_db_path
        ).resolve(strict=False)
        actual_path = Path(actual).expanduser().resolve(strict=False)
        if os.path.normcase(str(visible)) != os.path.normcase(
            str(actual_path)
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_database_binding_mismatch"
            )

    def _verify_visible_operational_store(
        self,
        *,
        expected_instance_id: Optional[str] = None,
        expected_unmarked_objects: Optional[
            dict[str, tuple[str, str]]
        ] = None,
    ) -> None:
        if self.operational_db_path == ":memory:":
            if expected_instance_id is not None:
                if (
                    self._read_operational_contract(
                        self._operational_conn
                    )
                    != expected_instance_id
                ):
                    raise LegacyIdentityFenceError(
                        "legacy_identity_operational_marker_mismatch"
                    )
            return
        visible = self._absolute_database_path(
            self.operational_db_path
        ).resolve(strict=False)
        fresh = sqlite3.connect(
            f"file:{visible.as_posix()}?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
        fresh.row_factory = sqlite3.Row
        try:
            # As for the primary handle, sqlite_master is the first read.
            objects = self._inspect_operational_objects(fresh)
            self._require_operational_connection_targets_visible_path(fresh)
            if expected_instance_id is not None:
                if (
                    self._read_operational_contract(
                        fresh,
                        objects=objects,
                    )
                    != expected_instance_id
                ):
                    raise LegacyIdentityFenceError(
                        "legacy_identity_operational_marker_mismatch"
                    )
            elif (
                expected_unmarked_objects is None
                or objects != expected_unmarked_objects
            ):
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_unmarked_shape_mismatch"
                )
            else:
                shape = self._classify_operational_shape(objects)
                if shape not in {
                    "empty",
                    "unmarked_current",
                    "prerelease",
                }:
                    raise LegacyIdentityFenceError(
                        "legacy_identity_operational_unmarked_shape_mismatch"
                    )
        finally:
            fresh.close()

    def _operational_database_identity(self) -> Optional[tuple[int, int]]:
        if self.operational_db_path == ":memory:":
            return None
        path = self._absolute_database_path(self.operational_db_path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino)

    def _require_operational_database_current(self) -> None:
        if (
            self._operational_conn is None
            or self._operational_instance_id is None
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_store_unbound"
            )
        if self.operational_db_path != ":memory:":
            self._validate_database_paths()
            current = self._operational_database_identity()
            if (
                self._operational_path_identity is not None
                and current != self._operational_path_identity
            ):
                raise LegacyIdentityFenceError(
                    "legacy_identity_operational_database_substituted"
                )
        objects = self._inspect_operational_objects(
            self._operational_conn
        )
        if (
            self._read_operational_contract(
                self._operational_conn,
                objects=objects,
            )
            != self._operational_instance_id
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_operational_marker_mismatch"
            )
        self._require_operational_connection_targets_visible_path(
            self._operational_conn
        )
        if self.operational_db_path == ":memory:":
            return
        self._verify_visible_operational_store(
            expected_instance_id=self._operational_instance_id,
        )

    @property
    def semantic_writes_frozen(self) -> bool:
        return self._semantic_writes_frozen

    @property
    def semantic_write_fence_installed(self) -> bool:
        return self._semantic_write_fence_installed

    @property
    def recognition_metadata_available(self) -> bool:
        """Whether legacy recognition metadata may cross the private API."""

        if self._disabled or self._operational_conn is None:
            return False
        if (
            self._semantic_write_fence_installed
            and self._core_recognition_policy is None
        ):
            return False
        return True

    @property
    def semantic_write_fence_generation(self) -> Optional[int]:
        return (
            SEMANTIC_WRITE_FENCE_GENERATION
            if self._semantic_write_fence_installed
            else None
        )

    @property
    def semantic_write_fence_trigger_digest(self) -> Optional[str]:
        return (
            EXPECTED_TRIGGER_DIGEST
            if self._semantic_write_fence_installed
            else None
        )

    @property
    def legacy_authority_state(self) -> str:
        if self._semantic_write_fence_installed:
            return LEGACY_FROZEN_PENDING_CUTOVER
        if self._semantic_writes_frozen:
            return LEGACY_APPLICATION_FROZEN_UNPROVEN
        return "legacy_migration_only"

    def _current_fence_file_fingerprint(self) -> tuple:
        resolved = self._absolute_database_path(self.db_path)
        paths = (
            resolved,
            Path(f"{resolved}-wal"),
            Path(f"{resolved}-shm"),
            Path(f"{resolved}-journal"),
            cutover_witness_path(resolved),
        )
        fingerprint = []
        for path in paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                fingerprint.append((str(path), None))
                continue
            fingerprint.append(
                (
                    str(path),
                    info.st_mode,
                    info.st_dev,
                    info.st_ino,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
        return tuple(fingerprint)

    def assert_legacy_read_current(self) -> None:
        """Verify a changed fence image before returning a legacy read."""

        if not self._semantic_write_fence_installed:
            if self.db_path != ":memory:":
                self._validate_database_paths()
            return
        try:
            if self.db_path == ":memory:":
                if self._conn is None or not verify_semantic_write_fence(
                    self._conn,
                    schema_ddl=SCHEMA_DDL,
                ):
                    raise LegacyIdentityFenceError(
                        "semantic_write_fence_not_current"
                    )
                return
            self._validate_database_paths()
            before = self._current_fence_file_fingerprint()
            if before == self._fence_verification_fingerprint:
                return
            if not verify_cutover_witness(
                self.db_path,
                schema_ddl=SCHEMA_DDL,
            ):
                raise LegacyIdentityFenceError(
                    "legacy_cutover_witness_missing"
                )
            resolved = Path(self.db_path).expanduser().resolve().as_posix()
            fresh = sqlite3.connect(
                f"file:{resolved}?mode=ro",
                uri=True,
                isolation_level=None,
            )
            try:
                fresh.execute("PRAGMA foreign_keys = ON")
                fresh.execute("PRAGMA query_only = ON")
                if not verify_semantic_write_fence(
                    fresh,
                    schema_ddl=SCHEMA_DDL,
                ):
                    raise LegacyIdentityFenceError(
                        "semantic_write_fence_not_current"
                    )
            finally:
                fresh.close()
            after = self._current_fence_file_fingerprint()
            if after != before:
                raise LegacyIdentityFenceError(
                    "legacy_identity_fence_changed_during_verification"
                )
            self._fence_verification_fingerprint = after
        except Exception as exc:
            self._fence_verification_fingerprint = None
            self.setup_error = f"legacy_frozen_view_degraded:{exc}"
            raise LegacyIdentityFenceError(
                "legacy_frozen_view_degraded"
            ) from exc

    def _require_semantic_writes(
        self,
        *,
        patch: Optional[dict] = None,
        target: Optional[IdentityRow] = None,
        allow_legacy_self_profile_edit: bool = False,
    ) -> None:
        if self._semantic_writes_frozen:
            fields = set(patch or {})
            if (
                allow_legacy_self_profile_edit
                and not self._semantic_write_fence_installed
                and fields
                and fields <= LEGACY_SELF_PROFILE_EDIT_FIELDS
                and target is not None
                and target.relationship_type == "me"
                and self._conn is not None
            ):
                me_rows = self._conn.execute(
                    "SELECT uuid FROM identities "
                    "WHERE relationship_type = 'me' LIMIT 2"
                ).fetchall()
                if (
                    len(me_rows) == 1
                    and me_rows[0]["uuid"] == target.uuid
                ):
                    return
            raise PermissionError(
                "legacy semantics are frozen pending reviewed cutover"
            )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._operational_conn is not None:
                self._operational_conn.close()
                self._operational_conn = None
            self._operational_path_identity = None
            self._operational_instance_id = None
            if self._process_lock is not None:
                self._process_lock.release()
                self._process_lock = None
            self._fence_verification_fingerprint = None

    @contextmanager
    def _txn(self):
        """All multi-step writes go through a BEGIN IMMEDIATE transaction
        (AR-9). Reader-only callers use direct cursor without txn wrap."""
        if self._disabled or self._conn is None:
            yield None
            return
        with self._lock:
            assert self._conn is not None
            self._validate_database_paths()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._validate_database_paths()
                yield self._conn
                self._validate_database_paths()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ─── Read API ────────────────────────────────────────────────────

    def get_identity(self, uuid_or_alias: str) -> Optional[IdentityRow]:
        """Look up by uuid; if not found, try alias resolution."""
        if self._disabled or self._conn is None:
            return None
        self.assert_legacy_read_current()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM identities WHERE uuid = ?", (uuid_or_alias,)
            ).fetchone()
            if row is None:
                # Alias fallback (case-insensitive)
                alias_row = self._conn.execute(
                    "SELECT identity_uuid FROM identity_aliases "
                    "WHERE LOWER(alias) = LOWER(?) LIMIT 1",
                    (uuid_or_alias,),
                ).fetchone()
                if alias_row is None:
                    return None
                row = self._conn.execute(
                    "SELECT * FROM identities WHERE uuid = ?",
                    (alias_row["identity_uuid"],),
                ).fetchone()
                if row is None:
                    return None
            return self._row_to_identity(row)

    def list_identities(
        self,
        *,
        include_archived: bool = False,
        include_ignored: bool = False,
        relationship_type: Optional[str] = None,
    ) -> list[IdentityRow]:
        if self._disabled or self._conn is None:
            return []
        self.assert_legacy_read_current()
        clauses = []
        args: list[Any] = []
        if not include_archived:
            clauses.append("is_archived = 0")
        if not include_ignored:
            clauses.append("is_ignored = 0")
        if relationship_type is not None:
            clauses.append("relationship_type = ?")
            args.append(relationship_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM identities {where} ORDER BY relationship_type, display_name",
                args,
            ).fetchall()
            return [self._row_to_identity(r) for r in rows]

    def resolve_frigate_name(self, frigate_name: str) -> Optional[IdentityRow]:
        """Map a Frigate person name → identity row. Used by world_state's
        projection layer to substitute display_name + privacy flags."""
        if self._disabled or self._conn is None or not frigate_name:
            return None
        if self._semantic_writes_frozen:
            # A frozen legacy identity is historical evidence only. This
            # cutoff applies before and after the physical fence; only the
            # explicit legacy migration mode can project it into WorldState.
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT i.* FROM identities i "
                "JOIN identity_aliases a ON a.identity_uuid = i.uuid "
                "WHERE LOWER(a.alias) = LOWER(?) AND a.alias_kind = 'frigate_name' "
                "LIMIT 1",
                (frigate_name,),
            ).fetchone()
            if row is None:
                # Also try enrollments
                row = self._conn.execute(
                    "SELECT i.* FROM identities i "
                    "JOIN enrollments e ON e.identity_uuid = i.uuid "
                    "WHERE LOWER(e.frigate_person_name) = LOWER(?) "
                    "AND e.retired_at IS NULL LIMIT 1",
                    (frigate_name,),
                ).fetchone()
            return self._row_to_identity(row) if row else None

    def list_relationships(
        self, identity_uuid: str, *, include_ended: bool = False
    ) -> list[dict]:
        if self._disabled or self._conn is None:
            return []
        self.assert_legacy_read_current()
        clauses = ["from_uuid = ?"]
        args: list[Any] = [identity_uuid]
        if not include_ended:
            clauses.append("status = 'active'")
        where = "WHERE " + " AND ".join(clauses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT r.*, i.display_name AS to_display_name "
                f"FROM relationships r "
                f"JOIN identities i ON i.uuid = r.to_uuid "
                f"{where} ORDER BY r.rel_type, i.display_name",
                args,
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["edge_data"] = json.loads(d.get("edge_data") or "{}")
                except (ValueError, TypeError):
                    d["edge_data"] = {}
                out.append(d)
            return out

    def list_preferences(
        self, identity_uuid: str, *, scope_filter: Optional[dict] = None
    ) -> list[dict]:
        if self._disabled or self._conn is None:
            return []
        self.assert_legacy_read_current()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM preferences WHERE identity_uuid = ? ORDER BY key",
                (identity_uuid,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                for jf in ("value", "scope", "provenance"):
                    try:
                        d[jf] = json.loads(d.get(jf) or "{}")
                    except (ValueError, TypeError):
                        d[jf] = {} if jf != "value" else d.get(jf)
                # Optional scope filter: prefs whose scope is a subset of filter
                if scope_filter and not self._scope_matches(d.get("scope") or {}, scope_filter):
                    continue
                out.append(d)
            return out

    # ─── Write API ───────────────────────────────────────────────────

    def create_identity(
        self,
        display_name: str,
        *,
        relationship_type: str = "unknown",
        relationship_subrole: Optional[str] = None,
        pronouns: Optional[str] = None,
        notes: Optional[str] = None,
        aliases: Optional[Iterable[str]] = None,
        frigate_person_name: Optional[str] = None,
        ha_person_entity_id: Optional[str] = None,
        actor: str = "system",
    ) -> str:
        """Insert a new identity, optionally with seed aliases + an
        enrollment. Returns the generated uuid. Atomically writes an
        audit row."""
        if self._disabled or self._conn is None:
            return ""
        self._require_semantic_writes()
        if relationship_type not in RELATIONSHIP_TYPES:
            raise ValueError(f"invalid relationship_type: {relationship_type!r}")
        new_uuid = _new_uuid()
        now = _now_iso()
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO identities("
                "uuid, display_name, pronouns, relationship_type, "
                "relationship_subrole, notes, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (new_uuid, display_name, pronouns, relationship_type,
                 relationship_subrole, notes, now, now),
            )
            # Seed primary display name as a 'name' alias for resolution
            conn.execute(
                "INSERT OR IGNORE INTO identity_aliases("
                "identity_uuid, alias, alias_kind, created_at) "
                "VALUES (?,?,?,?)",
                (new_uuid, display_name, "name", now),
            )
            for a in (aliases or []):
                conn.execute(
                    "INSERT OR IGNORE INTO identity_aliases("
                    "identity_uuid, alias, alias_kind, created_at) "
                    "VALUES (?,?,?,?)",
                    (new_uuid, a, "nickname", now),
                )
            if frigate_person_name:
                conn.execute(
                    "INSERT OR IGNORE INTO identity_aliases("
                    "identity_uuid, alias, alias_kind, created_at) "
                    "VALUES (?,?,?,?)",
                    (new_uuid, frigate_person_name, "frigate_name", now),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO enrollments("
                    "identity_uuid, frigate_person_name, created_at) "
                    "VALUES (?,?,?)",
                    (new_uuid, frigate_person_name, now),
                )
            if ha_person_entity_id:
                conn.execute(
                    "UPDATE identities SET ha_person_entity_id = ? WHERE uuid = ?",
                    (ha_person_entity_id, new_uuid),
                )
            self._audit(conn, "identity_mutation", actor, new_uuid, None, {
                "op": "create",
                "display_name": display_name,
                "relationship_type": relationship_type,
                "frigate_person_name": frigate_person_name,
            })
        return new_uuid

    def ensure_recognition_enrollment(
        self,
        frigate_person_name: str,
        *,
        display_label: Optional[str] = None,
        actor: str = "frigate_sync",
    ) -> str:
        """Maintain the isolated operational Frigate mapping after cutover.

        The enrollment lives in a separate operational database with no
        identity UUID, alias, privacy, relationship, preference, or HA-binding
        columns. It can never become semantic context by table linkage.
        """
        # Compatibility arguments intentionally have no durable representation.
        # A display label would duplicate a semantic name in the operational
        # database and create another privacy/erasure surface.
        del actor, display_label
        if self._disabled or self._operational_conn is None:
            return ""
        self._require_operational_database_current()
        name = frigate_person_name.strip()
        if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
            raise ValueError("invalid Frigate recognition identifier")
        if self.legacy_recognition_persistence_policy(name) == "blocked":
            self._purge_operational_recognition_name(name)
            return ""
        now = _now_iso()
        with self._lock:
            conn = self._operational_conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                matches = self._operational_casefold_matches(name)
                if len(matches) > 1:
                    conn.executemany(
                        "DELETE FROM operational_recognition_enrollments "
                        "WHERE frigate_person_name = ?",
                        ((match,) for match in matches),
                    )
                    conn.execute("COMMIT")
                    return ""
                if not matches:
                    conn.execute(
                        "INSERT INTO operational_recognition_enrollments("
                        "frigate_person_name, source, created_at, updated_at"
                        ") VALUES(?, 'frigate', ?, ?)",
                        (name, now, now),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return name

    def resolve_operational_recognition_name(
        self,
        frigate_person_name: str,
    ) -> Optional[OperationalRecognitionRow]:
        if (
            self._disabled
            or self._operational_conn is None
            or not frigate_person_name
        ):
            return None
        self._require_operational_database_current()
        if (
            self.legacy_recognition_persistence_policy(
                frigate_person_name
            )
            == "blocked"
        ):
            self._purge_operational_recognition_name(
                frigate_person_name
            )
            return None
        with self._lock:
            matches = self._operational_casefold_matches(
                frigate_person_name
            )
            if len(matches) != 1:
                if matches:
                    self._operational_conn.execute("BEGIN IMMEDIATE")
                    try:
                        self._operational_conn.executemany(
                            "DELETE FROM operational_recognition_enrollments "
                            "WHERE frigate_person_name = ?",
                            ((match,) for match in matches),
                        )
                        self._operational_conn.execute("COMMIT")
                    except Exception:
                        self._operational_conn.execute("ROLLBACK")
                        raise
                return None
            row = self._operational_conn.execute(
                "SELECT * FROM operational_recognition_enrollments "
                "WHERE frigate_person_name = ?",
                (matches[0],),
            ).fetchone()
        if row is None:
            return None
        return OperationalRecognitionRow(
            frigate_person_name=row["frigate_person_name"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def legacy_recognition_persistence_policy(
        self,
        frigate_person_name: str,
    ) -> str:
        """Return a non-model-facing ingress policy for a Frigate bucket.

        `blocked` identities are never copied to the operational database.
        `restricted` covers private/silent legacy records: the opaque Frigate
        identifier may remain operational, but no semantic label is copied and
        the fenced store is never wired to WorldState. `unmatched` is a new
        Frigate bucket with no legacy semantic record.
        """

        if self._disabled or self._conn is None or not frigate_person_name:
            return "blocked"
        if self._semantic_write_fence_installed:
            self.assert_legacy_read_current()
        with self._lock:
            rows = self._conn.execute(
                "SELECT i.uuid, i.do_not_track, i.is_ignored, "
                "i.is_silent, i.is_private, i.relationship_type, "
                "a.alias AS recognition_name "
                "FROM identities AS i "
                "JOIN identity_aliases AS a "
                "ON a.identity_uuid = i.uuid "
                "WHERE a.alias_kind = 'frigate_name' "
                "UNION ALL "
                "SELECT i.uuid, i.do_not_track, i.is_ignored, "
                "i.is_silent, i.is_private, i.relationship_type, "
                "e.frigate_person_name AS recognition_name "
                "FROM identities AS i "
                "JOIN enrollments AS e ON e.identity_uuid = i.uuid "
                "WHERE e.retired_at IS NULL"
            ).fetchall()
        target = frigate_person_name.casefold()
        matches = {
            row["uuid"]: row
            for row in rows
            if str(row["recognition_name"]).casefold() == target
        }
        legacy_policy = "unmatched"
        if matches:
            if any(
                bool(row["do_not_track"])
                or bool(row["is_ignored"])
                or row["relationship_type"] == "do_not_identify"
                for row in matches.values()
            ):
                legacy_policy = "blocked"
            elif len(matches) != 1:
                legacy_policy = "blocked"
            elif any(
                bool(row["is_silent"]) or bool(row["is_private"])
                for row in matches.values()
            ):
                legacy_policy = "restricted"
            else:
                legacy_policy = "allowed"

        # Once the physical fence is installed, Core is semantic/privacy
        # authority. Legacy privacy can veto, but it can no longer authorize
        # persistence by itself. Until a caller wires a current deterministic
        # Core policy provider, operational recognition fails closed.
        if self._semantic_write_fence_installed:
            if self._core_recognition_policy is None:
                return "blocked"
            try:
                core_policy = self._core_recognition_policy(
                    frigate_person_name
                )
            except Exception:  # noqa: BLE001
                return "blocked"
            if core_policy not in {"allowed", "restricted", "blocked"}:
                return "blocked"
            if "blocked" in {legacy_policy, core_policy}:
                return "blocked"
            if "restricted" in {legacy_policy, core_policy}:
                return "restricted"
            return core_policy
        return legacy_policy

    def _operational_casefold_matches(self, name: str) -> list[str]:
        if self._operational_conn is None:
            return []
        self._require_operational_database_current()
        target = name.casefold()
        return [
            str(row["frigate_person_name"])
            for row in self._operational_conn.execute(
                "SELECT frigate_person_name "
                "FROM operational_recognition_enrollments"
            ).fetchall()
            if str(row["frigate_person_name"]).casefold() == target
        ]

    def _purge_operational_recognition_name(self, name: str) -> int:
        if self._operational_conn is None:
            return 0
        self._require_operational_database_current()
        with self._lock:
            matches = self._operational_casefold_matches(name)
            if not matches:
                return 0
            self._operational_conn.execute("BEGIN IMMEDIATE")
            try:
                self._operational_conn.executemany(
                    "DELETE FROM operational_recognition_enrollments "
                    "WHERE frigate_person_name = ?",
                    ((match,) for match in matches),
                )
                self._operational_conn.execute("COMMIT")
            except Exception:
                self._operational_conn.execute("ROLLBACK")
                raise
            return len(matches)

    def _purge_blocked_operational_recognition_rows(self) -> int:
        if self._operational_conn is None:
            return 0
        self._require_operational_database_current()
        with self._lock:
            names = [
                str(row["frigate_person_name"])
                for row in self._operational_conn.execute(
                    "SELECT frigate_person_name "
                    "FROM operational_recognition_enrollments"
                ).fetchall()
            ]
        groups: dict[str, list[str]] = {}
        for name in names:
            groups.setdefault(name.casefold(), []).append(name)
        purge = {
            name
            for group in groups.values()
            for name in group
            if len(group) > 1
        }
        for name in names:
            if self.legacy_recognition_persistence_policy(name) == "blocked":
                purge.add(name)
        if not purge:
            return 0
        with self._lock:
            self._operational_conn.execute("BEGIN IMMEDIATE")
            try:
                self._operational_conn.executemany(
                    "DELETE FROM operational_recognition_enrollments "
                    "WHERE frigate_person_name = ?",
                    ((name,) for name in sorted(purge)),
                )
                self._operational_conn.execute("COMMIT")
            except Exception:
                self._operational_conn.execute("ROLLBACK")
                raise
        return len(purge)

    def purge_blocked_operational_recognition(self) -> int:
        """Reapply current privacy policy to every durable opaque mapping.

        The method validates the operational database identity before every
        mutation. Core-policy errors resolve to ``blocked`` in the policy
        adapter, so periodic callers safely delete rather than retain rows
        whose current privacy authorization cannot be established.
        """

        if self._disabled or self._operational_conn is None:
            return 0
        return self._purge_blocked_operational_recognition_rows()

    def update_identity(
        self,
        uuid_or_alias: str,
        patch: dict,
        *,
        actor: str = "user",
        expected_version: Optional[int] = None,
        allow_legacy_self_profile_edit: bool = False,
    ) -> bool:
        """Partial update. Allowed keys map to schema columns. Bumps
        version (optimistic concurrency per AR-9). Returns True if a
        row was updated."""
        if self._disabled or self._conn is None:
            return False
        cur = self.get_identity(uuid_or_alias)
        if cur is None:
            return False
        self._require_semantic_writes(
            patch=patch,
            target=cur,
            allow_legacy_self_profile_edit=allow_legacy_self_profile_edit,
        )
        allowed = {
            "display_name", "pronouns", "relationship_type",
            "relationship_subrole", "notes",
            "is_private", "is_silent", "is_ignored", "is_archived",
            "do_not_track", "auto_expire_at",
            "ha_person_entity_id", "ha_device_tracker_entity_id",
        }
        sets = []
        args: list[Any] = []
        for k, v in patch.items():
            if k not in allowed:
                raise ValueError(f"field {k!r} not allowed in update_identity")
            if k == "relationship_type" and v not in RELATIONSHIP_TYPES:
                raise ValueError(f"invalid relationship_type: {v!r}")
            sets.append(f"{k} = ?")
            args.append(int(v) if isinstance(v, bool) else v)
        if not sets:
            return False
        privacy_changed = bool(
            set(patch) & RECOGNITION_PRIVACY_FIELDS
        )
        privacy_recognition_names = {
            str(alias["alias"])
            for alias in cur.aliases
            if alias.get("kind") == "frigate_name"
        } if privacy_changed else set()
        now = _now_iso()
        sets.append("updated_at = ?")
        args.append(now)
        sets.append("version = version + 1")
        args.append(cur.uuid)
        where = "uuid = ?"
        if expected_version is not None:
            where += " AND version = ?"
            args.append(expected_version)
        with self._txn() as conn:
            updated = conn.execute(
                f"UPDATE identities SET {', '.join(sets)} WHERE {where}",
                args,
            )
            if updated.rowcount != 1:
                current_version = conn.execute(
                    "SELECT version FROM identities WHERE uuid = ?", (cur.uuid,)
                ).fetchone()
                actual = current_version["version"] if current_version else "missing"
                raise ValueError(
                    f"version mismatch: expected {expected_version}, got {actual}"
                )
            # Also rename the primary 'name' alias if display_name changed
            if "display_name" in patch:
                conn.execute(
                    "UPDATE identity_aliases SET alias = ? "
                    "WHERE identity_uuid = ? AND alias_kind = 'name'",
                    (patch["display_name"], cur.uuid),
                )
                # Slice 10: our store is authoritative — queue Frigate
                # rename(s) for every frigate_name alias this identity
                # carries. The drainer (frigate_sync.drain_pending_writes)
                # will POST /api/faces/<old>/rename to Frigate. We push
                # for EACH frigate alias; in practice usually just one.
                new_display = patch["display_name"]
                if new_display and new_display != cur.display_name:
                    frigate_aliases = [
                        r["alias"] for r in conn.execute(
                            "SELECT alias FROM identity_aliases "
                            "WHERE identity_uuid = ? AND alias_kind = 'frigate_name'",
                            (cur.uuid,),
                        ).fetchall()
                    ]
                    for old_frigate_name in frigate_aliases:
                        # Frigate's name is typically lowercased + underscored.
                        # Mirror that convention for the rename target so
                        # the post-rename name stays Frigate-friendly.
                        new_frigate_name = (
                            new_display.strip().lower().replace(" ", "_")
                        )
                        if new_frigate_name == old_frigate_name:
                            continue
                        payload = json.dumps({
                            "old_name": old_frigate_name,
                            "new_name": new_frigate_name,
                        })
                        conn.execute(
                            "INSERT INTO pending_writes("
                            "target_system, op, payload, state, created_at, updated_at) "
                            "VALUES ('frigate','rename',?, 'pending',?,?)",
                            (payload, now, now),
                        )
                        # Also update the Frigate alias optimistically so
                        # our store stays in sync with what Frigate will
                        # become. The drainer reconciles if Frigate rejects.
                        conn.execute(
                            "UPDATE identity_aliases SET alias = ? "
                            "WHERE identity_uuid = ? AND alias = ? "
                            "AND alias_kind = 'frigate_name'",
                            (new_frigate_name, cur.uuid, old_frigate_name),
                        )
                        # Mirror to enrollments table
                        conn.execute(
                            "UPDATE enrollments SET frigate_person_name = ? "
                            "WHERE identity_uuid = ? AND frigate_person_name = ?",
                            (new_frigate_name, cur.uuid, old_frigate_name),
                        )
            self._audit(conn, "identity_mutation", actor, cur.uuid,
                        asdict(cur), patch)
            if privacy_changed:
                privacy_recognition_names.update(
                    str(row["recognition_name"])
                    for row in conn.execute(
                        "SELECT alias AS recognition_name "
                        "FROM identity_aliases "
                        "WHERE identity_uuid = ? "
                        "AND alias_kind = 'frigate_name' "
                        "UNION "
                        "SELECT frigate_person_name AS recognition_name "
                        "FROM enrollments "
                        "WHERE identity_uuid = ? AND retired_at IS NULL",
                        (cur.uuid, cur.uuid),
                    ).fetchall()
                )
        if privacy_changed:
            for recognition_name in privacy_recognition_names:
                self._purge_operational_recognition_name(
                    recognition_name
                )
            self.purge_blocked_operational_recognition()
        return True

    def add_alias(
        self, uuid_or_alias: str, alias: str,
        *, kind: str = "nickname", actor: str = "user",
    ) -> bool:
        if self._disabled or self._conn is None:
            return False
        self._require_semantic_writes()
        cur = self.get_identity(uuid_or_alias)
        if cur is None:
            return False
        if kind not in ("name", "frigate_name", "nickname"):
            raise ValueError(f"invalid alias kind: {kind!r}")
        now = _now_iso()
        with self._txn() as conn:
            try:
                conn.execute(
                    "INSERT INTO identity_aliases("
                    "identity_uuid, alias, alias_kind, created_at) "
                    "VALUES (?,?,?,?)",
                    (cur.uuid, alias, kind, now),
                )
            except sqlite3.IntegrityError:
                return False  # already exists; idempotent return
            self._audit(conn, "identity_mutation", actor, cur.uuid,
                        None, {"op": "add_alias", "alias": alias, "kind": kind})
        return True

    def delete_identity(
        self, uuid_or_alias: str,
        *, actor: str = "user", queue_frigate_delete: bool = True,
    ) -> bool:
        """Hard-delete locally + queue Frigate-side delete via outbox
        (AR-8). All cascading rows go via ON DELETE CASCADE."""
        if self._disabled or self._conn is None:
            return False
        self._require_semantic_writes()
        cur = self.get_identity(uuid_or_alias)
        if cur is None:
            return False
        with self._txn() as conn:
            # Collect Frigate names to queue for delete BEFORE cascade
            frigate_names = [
                r["alias"] for r in conn.execute(
                    "SELECT alias FROM identity_aliases "
                    "WHERE identity_uuid = ? AND alias_kind = 'frigate_name'",
                    (cur.uuid,),
                ).fetchall()
            ]
            enrollment_names = [
                r["frigate_person_name"] for r in conn.execute(
                    "SELECT frigate_person_name FROM enrollments "
                    "WHERE identity_uuid = ?",
                    (cur.uuid,),
                ).fetchall()
            ]
            unique_names = sorted(set(frigate_names) | set(enrollment_names))
            conn.execute("DELETE FROM identities WHERE uuid = ?", (cur.uuid,))
            self._audit(conn, "identity_mutation", actor, cur.uuid,
                        asdict(cur), {"op": "delete", "frigate_names": unique_names})
            if queue_frigate_delete and unique_names:
                now = _now_iso()
                for name in unique_names:
                    conn.execute(
                        "INSERT INTO pending_writes("
                        "target_system, op, payload, state, created_at, updated_at) "
                        "VALUES ('frigate','delete',?, 'pending',?,?)",
                        (json.dumps({"person_name": name}), now, now),
                    )
        for name in unique_names:
            self._purge_operational_recognition_name(name)
        return True

    def add_relationship(
        self, from_uuid: str, to_uuid: str, rel_type: str,
        *, edge_data: Optional[dict] = None, actor: str = "user",
    ) -> int:
        """Insert a directed edge. For bidirectional logical relations
        (e.g. partner) the caller should call twice (A→B, B→A)."""
        if self._disabled or self._conn is None:
            return 0
        self._require_semantic_writes()
        if from_uuid == to_uuid:
            raise ValueError("cannot relate an identity to itself")
        if not self.get_identity(from_uuid) or not self.get_identity(to_uuid):
            raise ValueError("both identities must exist")
        now = _now_iso()
        ed = json.dumps(edge_data or {})
        with self._txn() as conn:
            cur = conn.execute(
                "INSERT INTO relationships("
                "from_uuid, to_uuid, rel_type, status, edge_data, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (from_uuid, to_uuid, rel_type, "active", ed, now),
            )
            edge_id = cur.lastrowid
            self._audit(conn, "identity_mutation", actor, from_uuid, None, {
                "op": "add_relationship",
                "to_uuid": to_uuid, "rel_type": rel_type,
                "edge_data": edge_data or {},
            })
            return edge_id

    def end_relationship(self, edge_id: int, *, actor: str = "user") -> bool:
        if self._disabled or self._conn is None:
            return False
        self._require_semantic_writes()
        now = _now_iso()
        with self._txn() as conn:
            cur = conn.execute(
                "UPDATE relationships SET status = 'ended', ended_at = ? "
                "WHERE id = ? AND status = 'active'",
                (now, edge_id),
            )
            if cur.rowcount == 0:
                return False
            self._audit(conn, "identity_mutation", actor, None, None,
                        {"op": "end_relationship", "edge_id": edge_id})
        return True

    def set_preference(
        self,
        identity_uuid: str,
        key: str,
        value: Any,
        *,
        scope: Optional[dict] = None,
        source: str = "manual",
        confidence: Optional[float] = None,
        provenance: Optional[dict] = None,
        actor: str = "user",
    ) -> int:
        if self._disabled or self._conn is None:
            return 0
        self._require_semantic_writes()
        if source not in PREFERENCE_SOURCES:
            raise ValueError(f"invalid source: {source!r}")
        scope_json = json.dumps(scope or {}, sort_keys=True)
        prov_json = json.dumps(provenance or {})
        val_json = json.dumps(value)
        now = _now_iso()
        with self._txn() as conn:
            # Upsert
            existing = conn.execute(
                "SELECT id FROM preferences WHERE identity_uuid = ? AND key = ? AND scope = ?",
                (identity_uuid, key, scope_json),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE preferences SET value = ?, source = ?, confidence = ?, "
                    "provenance = ?, updated_at = ? WHERE id = ?",
                    (val_json, source, confidence, prov_json, now, existing["id"]),
                )
                pref_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO preferences("
                    "identity_uuid, key, value, scope, source, confidence, "
                    "provenance, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (identity_uuid, key, val_json, scope_json, source,
                     confidence, prov_json, now, now),
                )
                pref_id = cur.lastrowid
            self._audit(conn, "identity_mutation", actor, identity_uuid, None,
                        {"op": "set_preference", "key": key, "scope": scope or {},
                         "source": source})
            return pref_id

    # ─── Audit helper ────────────────────────────────────────────────

    def _audit(
        self,
        conn: sqlite3.Connection,
        kind: str,
        actor: str,
        target_uuid: Optional[str],
        before: Optional[dict],
        after: Optional[dict],
        *,
        conv_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        tool_idx: Optional[int] = None,
    ) -> None:
        raw_operation = (
            (after or {}).get("op") if isinstance(after, dict) else None
        ) or kind
        operation_code = re.sub(
            r"[^a-z0-9_]+", "_", str(raw_operation).strip().lower()
        ).strip("_")[:64] or "audit_event"
        conn.execute(
            "INSERT INTO change_log("
            "ts, kind, actor, target_uuid, before_json, after_json, "
            "link_conv_id, link_turn_id, link_tool_idx) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                _now_iso(), kind, actor, target_uuid,
                None,
                json.dumps({"operation_code": operation_code}),
                None, None, None,
            ),
        )
        # Addendum 16 F-5a: fire an HA bus event for identity mutations
        # so the Tauri people overlay (and other subscribers like the
        # world_state aggregator's reconciler) can refresh on writes
        # they didn't originate. Best-effort: never raise out of audit.
        # Only fire for mutation kinds (not detection / frigate_writeback
        # / pending_write_state — those would spam the bus).
        #
        # BUG (S4 found in scenario testing): _audit runs in an executor
        # thread (REST views dispatch writes via async_add_executor_job),
        # but `bus.async_fire` is event-loop-only. From a thread, the
        # event silently doesn't fire. Use `hass.add_job` which schedules
        # the call onto the event loop thread-safely.
        if kind == "identity_mutation" and self._event_hass is not None:
            event_data = {
                "actor": actor,
                "target_uuid": target_uuid,
                "op": operation_code,
                "ts": _now_iso(),
            }
            try:
                self._event_hass.add_job(
                    self._event_hass.bus.async_fire,
                    "extended_openai_conversation.identity_mutation",
                    event_data,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("identity_mutation bus fire failed: %s", exc)

    # ─── Outbox + retention ──────────────────────────────────────────

    def pending_writes(self, *, state: str = "pending", limit: int = 100) -> list[dict]:
        if self._disabled or self._conn is None:
            return []
        if self._semantic_writes_frozen:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_writes WHERE state = ? "
                "ORDER BY created_at LIMIT ?",
                (state, limit),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d.get("payload") or "{}")
                except (ValueError, TypeError):
                    d["payload"] = {}
                out.append(d)
            return out

    def mark_pending_write(self, pw_id: int, *, state: str, error: Optional[str] = None) -> bool:
        if self._disabled or self._conn is None:
            return False
        self._require_semantic_writes()
        if state not in ("pending", "in_flight", "done", "failed"):
            raise ValueError(f"invalid pending_write state: {state!r}")
        now = _now_iso()
        with self._txn() as conn:
            cur = conn.execute(
                "UPDATE pending_writes SET state = ?, last_error = ?, "
                "attempt_count = attempt_count + ?, updated_at = ? WHERE id = ?",
                (state, error, 1 if state in ("in_flight", "failed") else 0, now, pw_id),
            )
            if cur.rowcount == 0:
                return False
        return True

    def purge_change_log(self, *, now_ts: Optional[float] = None) -> int:
        """Retention sweep per AR2-7. Returns rows deleted. Skips pinned
        rows (US-5.21)."""
        if self._disabled or self._conn is None:
            return 0
        self._require_semantic_writes()
        now_ts = now_ts if now_ts is not None else time.time()
        total = 0
        with self._txn() as conn:
            for kind, ttl_s in CHANGE_LOG_RETENTION.items():
                cutoff_iso = datetime.fromtimestamp(now_ts - ttl_s, timezone.utc) \
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                cur = conn.execute(
                    "DELETE FROM change_log WHERE kind = ? AND ts < ? AND pinned = 0",
                    (kind, cutoff_iso),
                )
                total += cur.rowcount
        return total

    # ─── Internal helpers ────────────────────────────────────────────

    def _row_to_identity(self, row: sqlite3.Row) -> IdentityRow:
        if row is None:
            return None  # type: ignore[return-value]
        # Pull aliases for the identity
        aliases = []
        if self._conn is not None:
            aliases = [
                {"alias": r["alias"], "kind": r["alias_kind"]}
                for r in self._conn.execute(
                    "SELECT alias, alias_kind FROM identity_aliases "
                    "WHERE identity_uuid = ? ORDER BY alias_kind, alias",
                    (row["uuid"],),
                ).fetchall()
            ]
        try:
            flags_extra = json.loads(row["flags_extra"]) if row["flags_extra"] else {}
        except (ValueError, TypeError):
            flags_extra = {}
        return IdentityRow(
            uuid=row["uuid"],
            display_name=row["display_name"],
            pronouns=row["pronouns"],
            relationship_type=row["relationship_type"],
            relationship_subrole=row["relationship_subrole"],
            notes=row["notes"],
            is_private=bool(row["is_private"]),
            is_silent=bool(row["is_silent"]),
            is_ignored=bool(row["is_ignored"]),
            is_archived=bool(row["is_archived"]),
            do_not_track=bool(row["do_not_track"]),
            auto_expire_at=row["auto_expire_at"],
            ha_person_entity_id=row["ha_person_entity_id"],
            ha_device_tracker_entity_id=row["ha_device_tracker_entity_id"],
            flags_extra=flags_extra,
            aliases=aliases,
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _scope_matches(pref_scope: dict, filter_scope: dict) -> bool:
        """Preference scope is a 'subset of context' match: a pref with
        scope {time_of_day:'morning'} matches a filter {time_of_day:'morning', room:'kitchen'}
        but NOT a filter {room:'kitchen'} (missing time_of_day key). Empty
        pref scope always matches (= unscoped)."""
        if not pref_scope:
            return True
        for k, v in pref_scope.items():
            if filter_scope.get(k) != v:
                return False
        return True
