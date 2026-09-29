"""Verifiable SQLite boundary for the legacy semantic Identity Store.

The fence is installed only while Home Assistant is stopped.  This module is
standard-library-only so both the offline installer and the HA integration use
the exact same marker, schema, trigger, integrity, and process-lock contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SEMANTIC_WRITE_FENCE_KEY = "semantic_write_fence"
SEMANTIC_WRITE_FENCE_CONTRACT = "legacy_identity_semantics_v2"
SEMANTIC_WRITE_FENCE_GENERATION = 2
SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX = "e4_identity_fence_"
SEMANTIC_AUTHORITY_KEY = "semantic_authority"
LEGACY_APPLICATION_FROZEN_UNPROVEN = "legacy_application_frozen_unproven"
LEGACY_FROZEN_PENDING_CUTOVER = "legacy_frozen_pending_cutover"
OBSOLETE_PRE_E4_AUTHORITY_VALUE = "home_agent_core"
LEGACY_SCHEMA_VERSION_KEY = "schema_version"
LEGACY_SCHEMA_VERSION_VALUE = "1"
FENCE_ABORT_MESSAGE = "legacy_identity_semantics_frozen"
FENCE_MARKER_ABORT_MESSAGE = "legacy_identity_fence_metadata_immutable"
SCRUBBED_AUDIT_ENVELOPE = '{"operation_code":"legacy_scrubbed"}'
SCRUBBED_AUDIT_TIMESTAMP = "1970-01-01T00:00:00Z"
SCRUBBED_AUDIT_KIND = "identity_mutation"
SCRUBBED_AUDIT_ACTOR = "legacy_scrubbed"
CUTOVER_WITNESS_CONTRACT = "legacy_identity_cutover_witness_v1"
CUTOVER_WITNESS_SUFFIX = ".e4-cutover-witness"

_LOCK_SUFFIX = ".e4-semantic-writer.lock"
_SEMANTIC_TABLES = (
    "identities",
    "identity_aliases",
    "enrollments",
    "relationships",
    "preferences",
    "face_crops_meta",
    "change_log",
    "pending_writes",
)


class LegacyIdentityFenceError(RuntimeError):
    """The legacy semantic-write boundary is absent or inconsistent."""


class LegacyIdentityProcessLock:
    """Cooperative lifetime OS lock shared by HA and the offline installer."""

    def __init__(self, database_path: str | Path):
        path = Path(database_path).expanduser().resolve()
        self.path = Path(f"{path}{_LOCK_SUFFIX}")
        self._file = None

    def acquire(self) -> "LegacyIdentityProcessLock":
        if self._file is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise LegacyIdentityFenceError(
                "legacy_identity_database_active"
            ) from exc
        self._file = handle
        return self

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._file = None

    def __enter__(self) -> "LegacyIdentityProcessLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def _trigger(name: str, table: str, event: str, message: str) -> str:
    return f"""CREATE TRIGGER {name}
BEFORE {event} ON {table}
BEGIN
    SELECT RAISE(ABORT, '{message}');
END"""


TRIGGER_SQL: Mapping[str, str] = {
    **{
        f"{SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX}{table}_{event.lower()}": _trigger(
            f"{SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX}{table}_{event.lower()}",
            table,
            event,
            FENCE_ABORT_MESSAGE,
        )
        for table in _SEMANTIC_TABLES
        for event in ("INSERT", "UPDATE", "DELETE")
    },
    **{
        f"{SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX}schema_meta_{event.lower()}": _trigger(
            f"{SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX}schema_meta_{event.lower()}",
            "schema_meta",
            event,
            FENCE_MARKER_ABORT_MESSAGE,
        )
        for event in ("INSERT", "UPDATE", "DELETE")
    },
}


def _stable_sql(value: str) -> str:
    """Normalize transport differences without changing SQL token bytes."""

    normalized = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    if normalized.endswith(";"):
        normalized = normalized[:-1]
    return normalized


def _object_digest(objects: Mapping[str, str]) -> str:
    canonical = "\n".join(
        f"{name}\0{_stable_sql(sql)}" for name, sql in sorted(objects.items())
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def trigger_digest(trigger_sql: Mapping[str, str] = TRIGGER_SQL) -> str:
    return _object_digest(trigger_sql)


EXPECTED_TRIGGER_DIGEST = trigger_digest()


def _schema_objects(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view') "
        "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
        "ORDER BY type, name"
    ).fetchall()
    return {
        f"{row[0]}:{row[1]}": str(row[2] or "")
        for row in rows
    }


def expected_schema_objects(schema_ddl: str) -> dict[str, str]:
    scratch = sqlite3.connect(":memory:", isolation_level=None)
    try:
        scratch.executescript(schema_ddl)
        return _schema_objects(scratch)
    finally:
        scratch.close()


def schema_digest(schema_ddl: str) -> str:
    return _object_digest(expected_schema_objects(schema_ddl))


def _marker_payload(*, expected_schema_digest: str) -> dict[str, Any]:
    return {
        "contract": SEMANTIC_WRITE_FENCE_CONTRACT,
        "generation": SEMANTIC_WRITE_FENCE_GENERATION,
        "schema_digest": expected_schema_digest,
        "trigger_digest": EXPECTED_TRIGGER_DIGEST,
    }


def marker_json(*, expected_schema_digest: str) -> str:
    return json.dumps(
        _marker_payload(expected_schema_digest=expected_schema_digest),
        sort_keys=True,
        separators=(",", ":"),
    )


def cutover_witness_path(database_path: str | Path) -> Path:
    """Return the non-SQLite monotonic witness path for one semantic store."""

    database = Path(database_path).expanduser()
    if not database.is_absolute():
        database = Path(os.path.abspath(database))
    return database.with_name(f"{database.name}{CUTOVER_WITNESS_SUFFIX}")


def _witness_payload(*, expected_schema_digest: str) -> dict[str, Any]:
    return {
        "contract": CUTOVER_WITNESS_CONTRACT,
        "generation": SEMANTIC_WRITE_FENCE_GENERATION,
        "schema_digest": expected_schema_digest,
        "trigger_digest": EXPECTED_TRIGGER_DIGEST,
    }


def _witness_bytes(*, expected_schema_digest: str) -> bytes:
    return json.dumps(
        _witness_payload(expected_schema_digest=expected_schema_digest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # The file itself is flushed below. Python/Win32 cannot portably open
        # a directory for FlushFileBuffers; os.replace still supplies the
        # atomic namespace transition on the supported local filesystems.
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_regular_private_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LegacyIdentityFenceError("legacy_cutover_witness_not_regular")
    if info.st_nlink != 1:
        raise LegacyIdentityFenceError(
            "legacy_cutover_witness_link_count_invalid"
        )
    if os.name != "nt" and info.st_mode & 0o077:
        raise LegacyIdentityFenceError("legacy_cutover_witness_permissions")
    return info


def verify_cutover_witness(
    database_path: str | Path,
    *,
    schema_ddl: str,
) -> bool:
    """Verify the exact external witness, returning false only when absent."""

    path = cutover_witness_path(database_path)
    try:
        _require_regular_private_file(path)
    except FileNotFoundError:
        return False
    expected = _witness_bytes(
        expected_schema_digest=schema_digest(schema_ddl)
    )
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise LegacyIdentityFenceError(
            "legacy_cutover_witness_unreadable"
        ) from exc
    if actual != expected:
        raise LegacyIdentityFenceError(
            "legacy_cutover_witness_noncanonical_or_mismatch"
        )
    return True


def ensure_cutover_witness(
    database_path: str | Path,
    *,
    schema_ddl: str,
) -> Path:
    """Atomically persist the irreversible external cutover witness."""

    path = cutover_witness_path(database_path)
    if verify_cutover_witness(database_path, schema_ddl=schema_ddl):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = _witness_bytes(
        expected_schema_digest=schema_digest(schema_ddl)
    )
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{os.urandom(16).hex()}"
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        offset = 0
        while offset < len(expected):
            written = os.write(descriptor, expected[offset:])
            if written <= 0:
                raise OSError("short witness write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    if not verify_cutover_witness(database_path, schema_ddl=schema_ddl):
        raise LegacyIdentityFenceError(
            "legacy_cutover_witness_persistence_failed"
        )
    return path


def _strict_json_object(raw: str) -> dict[str, Any]:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LegacyIdentityFenceError(
                    "semantic_write_fence_marker_duplicate_key"
                )
            result[key] = value
        return result

    try:
        parsed = json.loads(raw, object_pairs_hook=reject_duplicates)
    except LegacyIdentityFenceError:
        raise
    except (TypeError, ValueError) as exc:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_marker_invalid_json"
        ) from exc
    if type(parsed) is not dict:
        raise LegacyIdentityFenceError("semantic_write_fence_marker_not_object")
    return parsed


def _read_meta_raw(
    connection: sqlite3.Connection,
    key: str,
) -> str | None:
    row = connection.execute(
        "SELECT value FROM schema_meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str:
        raise LegacyIdentityFenceError(f"{key}_marker_non_text")
    return row[0]


def fence_artifacts_present(connection: sqlite3.Connection) -> bool:
    schema_meta = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'schema_meta'"
    ).fetchone()
    marker_present = False
    if schema_meta is not None:
        marker_present = _read_meta_raw(
            connection, SEMANTIC_WRITE_FENCE_KEY
        ) is not None
        authority = _read_meta_raw(connection, SEMANTIC_AUTHORITY_KEY)
        marker_present = (
            marker_present
            or authority == LEGACY_FROZEN_PENDING_CUTOVER
        )
    trigger_present = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'trigger' AND name LIKE ? LIMIT 1",
        (f"{SEMANTIC_WRITE_FENCE_TRIGGER_PREFIX}%",),
    ).fetchone() is not None
    return marker_present or trigger_present


def _read_fence_triggers(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' ORDER BY name"
    ).fetchall()
    return {str(row[0]): str(row[1] or "") for row in rows}


def _require_integrity(connection: sqlite3.Connection) -> None:
    integrity_rows = [
        str(row[0])
        for row in connection.execute("PRAGMA integrity_check").fetchall()
    ]
    if integrity_rows != ["ok"]:
        raise LegacyIdentityFenceError("legacy_identity_integrity_check_failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise LegacyIdentityFenceError("legacy_identity_foreign_key_check_failed")


def _require_pinned_schema(
    connection: sqlite3.Connection,
    schema_ddl: str,
) -> str:
    expected = expected_schema_objects(schema_ddl)
    actual = _schema_objects(connection)
    if set(actual) != set(expected):
        raise LegacyIdentityFenceError("legacy_identity_schema_object_set_mismatch")
    for name, expected_sql in expected.items():
        if _stable_sql(actual[name]) != _stable_sql(expected_sql):
            raise LegacyIdentityFenceError(
                f"legacy_identity_schema_definition_mismatch:{name}"
            )
    actual_digest = _object_digest(actual)
    expected_digest = _object_digest(expected)
    if actual_digest != expected_digest:
        raise LegacyIdentityFenceError("legacy_identity_schema_digest_mismatch")
    return expected_digest


def _require_clean_outbox(connection: sqlite3.Connection) -> None:
    count = int(
        connection.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0]
    )
    if count != 0:
        raise LegacyIdentityFenceError("legacy_identity_pending_writes_not_empty")


def _read_schema_meta_contract(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    rows = connection.execute(
        "SELECT key, value FROM schema_meta ORDER BY key"
    ).fetchall()
    result: dict[str, str] = {}
    for key, value in rows:
        if type(key) is not str or type(value) is not str:
            raise LegacyIdentityFenceError(
                "legacy_identity_schema_meta_non_text"
            )
        result[key] = value
    return result


def require_unfenced_schema_meta_contract(
    connection: sqlite3.Connection,
) -> None:
    """Require the complete, reviewed pre-fence metadata key/value set."""

    actual = _read_schema_meta_contract(connection)
    if set(actual) == {LEGACY_SCHEMA_VERSION_KEY}:
        if (
            actual[LEGACY_SCHEMA_VERSION_KEY]
            != LEGACY_SCHEMA_VERSION_VALUE
        ):
            raise LegacyIdentityFenceError(
                "legacy_identity_schema_version_mismatch"
            )
        return
    if set(actual) != {
        LEGACY_SCHEMA_VERSION_KEY,
        SEMANTIC_AUTHORITY_KEY,
    }:
        raise LegacyIdentityFenceError(
            "legacy_identity_schema_meta_key_set_mismatch"
        )
    if actual[LEGACY_SCHEMA_VERSION_KEY] != LEGACY_SCHEMA_VERSION_VALUE:
        raise LegacyIdentityFenceError(
            "legacy_identity_schema_version_mismatch"
        )
    if actual[SEMANTIC_AUTHORITY_KEY] not in {
        LEGACY_APPLICATION_FROZEN_UNPROVEN,
        OBSOLETE_PRE_E4_AUTHORITY_VALUE,
    }:
        raise LegacyIdentityFenceError(
            "semantic_authority_marker_conflict"
        )


def _require_fenced_schema_meta_contract(
    connection: sqlite3.Connection,
    *,
    expected_marker: str,
) -> None:
    actual = _read_schema_meta_contract(connection)
    expected = {
        LEGACY_SCHEMA_VERSION_KEY: LEGACY_SCHEMA_VERSION_VALUE,
        SEMANTIC_AUTHORITY_KEY: LEGACY_FROZEN_PENDING_CUTOVER,
        SEMANTIC_WRITE_FENCE_KEY: expected_marker,
    }
    if set(actual) != set(expected):
        raise LegacyIdentityFenceError(
            "legacy_identity_schema_meta_key_set_mismatch"
        )
    if actual != expected:
        raise LegacyIdentityFenceError(
            "legacy_identity_schema_meta_value_mismatch"
        )


def scrub_legacy_audit_content(connection: sqlite3.Connection) -> int:
    """Remove contentful legacy snapshots before the immutable fence."""

    result = connection.execute(
        "UPDATE change_log SET ts = ?, kind = ?, actor = ?, "
        "target_uuid = NULL, before_json = NULL, after_json = ?, "
        "link_conv_id = NULL, link_turn_id = NULL, link_tool_idx = NULL, "
        "pinned = 0 "
        "WHERE typeof(ts) <> 'text' OR ts <> ? "
        "OR typeof(kind) <> 'text' OR kind <> ? "
        "OR typeof(actor) <> 'text' OR actor <> ? "
        "OR target_uuid IS NOT NULL OR before_json IS NOT NULL "
        "OR typeof(after_json) <> 'text' OR after_json <> ? "
        "OR link_conv_id IS NOT NULL OR link_turn_id IS NOT NULL "
        "OR link_tool_idx IS NOT NULL OR pinned <> 0",
        (
            SCRUBBED_AUDIT_TIMESTAMP,
            SCRUBBED_AUDIT_KIND,
            SCRUBBED_AUDIT_ACTOR,
            SCRUBBED_AUDIT_ENVELOPE,
            SCRUBBED_AUDIT_TIMESTAMP,
            SCRUBBED_AUDIT_KIND,
            SCRUBBED_AUDIT_ACTOR,
            SCRUBBED_AUDIT_ENVELOPE,
        ),
    )
    return int(result.rowcount)


def _require_scrubbed_audit_content(
    connection: sqlite3.Connection,
) -> None:
    contentful = connection.execute(
        "SELECT 1 FROM change_log "
        "WHERE typeof(ts) <> 'text' OR ts <> ? "
        "OR typeof(kind) <> 'text' OR kind <> ? "
        "OR typeof(actor) <> 'text' OR actor <> ? "
        "OR target_uuid IS NOT NULL OR before_json IS NOT NULL "
        "OR typeof(after_json) <> 'text' OR after_json <> ? "
        "OR link_conv_id IS NOT NULL OR link_turn_id IS NOT NULL "
        "OR link_tool_idx IS NOT NULL OR pinned <> 0 LIMIT 1",
        (
            SCRUBBED_AUDIT_TIMESTAMP,
            SCRUBBED_AUDIT_KIND,
            SCRUBBED_AUDIT_ACTOR,
            SCRUBBED_AUDIT_ENVELOPE,
        ),
    ).fetchone()
    if contentful is not None:
        raise LegacyIdentityFenceError(
            "legacy_identity_audit_content_not_scrubbed"
        )


def _require_consistent_legacy_recognition_links(
    connection: sqlite3.Connection,
) -> None:
    mismatch = connection.execute(
        "SELECT 1 FROM enrollments AS enrollment "
        "LEFT JOIN identity_aliases AS alias "
        "ON alias.identity_uuid = enrollment.identity_uuid "
        "AND alias.alias_kind = 'frigate_name' "
        "AND alias.alias = enrollment.frigate_person_name "
        "WHERE enrollment.retired_at IS NULL AND alias.id IS NULL LIMIT 1"
    ).fetchone()
    if mismatch is None:
        mismatch = connection.execute(
            "SELECT 1 FROM identity_aliases AS alias "
            "LEFT JOIN enrollments AS enrollment "
            "ON enrollment.identity_uuid = alias.identity_uuid "
            "AND enrollment.frigate_person_name = alias.alias "
            "AND enrollment.retired_at IS NULL "
            "WHERE alias.alias_kind = 'frigate_name' "
            "AND enrollment.id IS NULL LIMIT 1"
        ).fetchone()
    if mismatch is not None:
        raise LegacyIdentityFenceError(
            "legacy_identity_recognition_link_mismatch"
        )
    recognition_rows = connection.execute(
        "SELECT identity_uuid, alias AS recognition_name "
        "FROM identity_aliases WHERE alias_kind = 'frigate_name' "
        "UNION ALL "
        "SELECT identity_uuid, frigate_person_name AS recognition_name "
        "FROM enrollments WHERE retired_at IS NULL"
    ).fetchall()
    casefold_groups: dict[str, set[tuple[str, str]]] = {}
    for identity_uuid, recognition_name in recognition_rows:
        if type(identity_uuid) is not str or type(recognition_name) is not str:
            raise LegacyIdentityFenceError(
                "legacy_identity_recognition_link_non_text"
            )
        casefold_groups.setdefault(
            recognition_name.casefold(),
            set(),
        ).add((identity_uuid, recognition_name))
    if any(
        len({identity for identity, _name in entries}) > 1
        or len({name for _identity, name in entries}) > 1
        for entries in casefold_groups.values()
    ):
        raise LegacyIdentityFenceError(
            "legacy_identity_recognition_casefold_collision"
        )


def verify_unfenced_preconditions(
    connection: sqlite3.Connection,
    *,
    schema_ddl: str,
) -> str:
    _require_integrity(connection)
    expected_digest = _require_pinned_schema(connection, schema_ddl)
    _require_clean_outbox(connection)
    _require_scrubbed_audit_content(connection)
    _require_consistent_legacy_recognition_links(connection)
    require_unfenced_schema_meta_contract(connection)
    return expected_digest


def _verify_common_preconditions(
    connection: sqlite3.Connection,
    *,
    schema_ddl: str,
) -> str:
    _require_integrity(connection)
    expected_digest = _require_pinned_schema(connection, schema_ddl)
    _require_clean_outbox(connection)
    _require_scrubbed_audit_content(connection)
    _require_consistent_legacy_recognition_links(connection)
    return expected_digest


def verify_semantic_write_fence(
    connection: sqlite3.Connection,
    *,
    schema_ddl: str,
) -> bool:
    """Verify exact schema, markers, triggers, content gates, and integrity."""

    if not fence_artifacts_present(connection):
        return False

    expected_schema_digest = _verify_common_preconditions(
        connection,
        schema_ddl=schema_ddl,
    )
    raw_marker = _read_meta_raw(connection, SEMANTIC_WRITE_FENCE_KEY)
    if raw_marker is None:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_triggers_without_marker"
        )

    parsed = _strict_json_object(raw_marker)
    expected_keys = {
        "contract",
        "generation",
        "schema_digest",
        "trigger_digest",
    }
    if set(parsed) != expected_keys:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_marker_key_set_mismatch"
        )
    if type(parsed["contract"]) is not str:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_contract_type_invalid"
        )
    if type(parsed["generation"]) is not int:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_generation_type_invalid"
        )
    if type(parsed["schema_digest"]) is not str:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_schema_digest_type_invalid"
        )
    if type(parsed["trigger_digest"]) is not str:
        raise LegacyIdentityFenceError(
            "semantic_write_fence_trigger_digest_type_invalid"
        )
    canonical_marker = marker_json(
        expected_schema_digest=expected_schema_digest
    )
    if raw_marker != canonical_marker:
        if parsed["generation"] > SEMANTIC_WRITE_FENCE_GENERATION:
            raise LegacyIdentityFenceError(
                "semantic_write_fence_installer_outdated"
            )
        raise LegacyIdentityFenceError(
            "semantic_write_fence_marker_noncanonical_or_mismatch"
        )

    authority = _read_meta_raw(connection, SEMANTIC_AUTHORITY_KEY)
    if authority != LEGACY_FROZEN_PENDING_CUTOVER:
        raise LegacyIdentityFenceError(
            "semantic_authority_marker_missing_or_mismatch"
        )
    _require_fenced_schema_meta_contract(
        connection,
        expected_marker=canonical_marker,
    )

    actual = _read_fence_triggers(connection)
    if set(actual) != set(TRIGGER_SQL):
        raise LegacyIdentityFenceError("semantic_write_fence_trigger_set_mismatch")
    for name, expected_sql in TRIGGER_SQL.items():
        if _stable_sql(actual[name]) != _stable_sql(expected_sql):
            raise LegacyIdentityFenceError(
                f"semantic_write_fence_trigger_definition_mismatch:{name}"
            )
    if trigger_digest(actual) != parsed["trigger_digest"]:
        raise LegacyIdentityFenceError("semantic_write_fence_trigger_digest_mismatch")
    return True


def probe_semantic_write_fence(connection: sqlite3.Connection) -> None:
    probes = (
        (
            "INSERT INTO relationships("
            "from_uuid, to_uuid, rel_type, status, edge_data, created_at"
            ") VALUES ('__e4_probe_from__', '__e4_probe_to__', 'probe', "
            "'active', '{}', '1970-01-01T00:00:00Z')",
            FENCE_ABORT_MESSAGE,
        ),
        (
            "UPDATE schema_meta SET value = value "
            f"WHERE key = '{SEMANTIC_AUTHORITY_KEY}'",
            FENCE_MARKER_ABORT_MESSAGE,
        ),
        (
            "INSERT OR REPLACE INTO schema_meta(key, value) "
            f"VALUES ('{SEMANTIC_WRITE_FENCE_KEY}', 'replaced')",
            FENCE_MARKER_ABORT_MESSAGE,
        ),
    )
    for sql, expected_error in probes:
        connection.execute("SAVEPOINT e4_fence_probe")
        try:
            connection.execute(sql)
        except sqlite3.IntegrityError as exc:
            if expected_error not in str(exc):
                raise LegacyIdentityFenceError(
                    "semantic_write_fence_probe_wrong_failure"
                ) from exc
        else:
            raise LegacyIdentityFenceError("semantic_write_fence_probe_bypassed")
        finally:
            connection.execute("ROLLBACK TO e4_fence_probe")
            connection.execute("RELEASE e4_fence_probe")


def install_semantic_write_fence(
    connection: sqlite3.Connection,
    *,
    schema_ddl: str,
) -> int:
    """Install the current generation inside the caller's exclusive txn."""

    if fence_artifacts_present(connection):
        if not verify_semantic_write_fence(
            connection,
            schema_ddl=schema_ddl,
        ):
            raise LegacyIdentityFenceError("semantic_write_fence_partial")
        probe_semantic_write_fence(connection)
        return SEMANTIC_WRITE_FENCE_GENERATION

    expected_schema_digest = verify_unfenced_preconditions(
        connection,
        schema_ddl=schema_ddl,
    )
    authority = _read_meta_raw(connection, SEMANTIC_AUTHORITY_KEY)
    if authority not in (
        None,
        LEGACY_APPLICATION_FROZEN_UNPROVEN,
        OBSOLETE_PRE_E4_AUTHORITY_VALUE,
    ):
        raise LegacyIdentityFenceError("semantic_authority_marker_conflict")
    if authority != LEGACY_FROZEN_PENDING_CUTOVER:
        if authority is not None:
            connection.execute(
                "DELETE FROM schema_meta WHERE key = ?",
                (SEMANTIC_AUTHORITY_KEY,),
            )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
            (
                SEMANTIC_AUTHORITY_KEY,
                LEGACY_FROZEN_PENDING_CUTOVER,
            ),
        )
    connection.execute(
        "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
        (
            SEMANTIC_WRITE_FENCE_KEY,
            marker_json(expected_schema_digest=expected_schema_digest),
        ),
    )
    for sql in TRIGGER_SQL.values():
        connection.execute(sql)

    if not verify_semantic_write_fence(
        connection,
        schema_ddl=schema_ddl,
    ):
        raise LegacyIdentityFenceError("semantic_write_fence_install_incomplete")
    probe_semantic_write_fence(connection)
    return SEMANTIC_WRITE_FENCE_GENERATION
