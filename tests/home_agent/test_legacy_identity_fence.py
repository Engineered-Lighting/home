"""Deterministic adversarial tests for the E4 legacy Identity Store fence."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_DIR = (
    Path(__file__).resolve().parents[2]
    / "ha-config"
    / "extended_openai_conversation"
)
sys.path.insert(0, str(MODULE_DIR))

from freeze_legacy_identity_semantics import freeze_database  # noqa: E402
from frigate_sync import auto_seed_identities_from_frigate  # noqa: E402
from identity_store import (  # noqa: E402
    IdentityStore,
    LEGACY_MIGRATION_MODE,
    OPERATIONAL_RECOGNITION_TABLE_SQL,
    SCHEMA_DDL,
    SEMANTIC_WRITES_MODE_ENV,
)
from legacy_identity_fence import (  # noqa: E402
    CUTOVER_WITNESS_SUFFIX,
    EXPECTED_TRIGGER_DIGEST,
    FENCE_ABORT_MESSAGE,
    FENCE_MARKER_ABORT_MESSAGE,
    LEGACY_APPLICATION_FROZEN_UNPROVEN,
    LEGACY_FROZEN_PENDING_CUTOVER,
    LegacyIdentityFenceError,
    SCRUBBED_AUDIT_ACTOR,
    SCRUBBED_AUDIT_ENVELOPE,
    SCRUBBED_AUDIT_KIND,
    SCRUBBED_AUDIT_TIMESTAMP,
    SEMANTIC_AUTHORITY_KEY,
    SEMANTIC_WRITE_FENCE_GENERATION,
    SEMANTIC_WRITE_FENCE_KEY,
    TRIGGER_SQL,
    _stable_sql,
    cutover_witness_path,
    ensure_cutover_witness,
    verify_cutover_witness,
    verify_semantic_write_fence,
)
from world_state import WorldStateAggregator  # noqa: E402


@pytest.fixture(autouse=True)
def _migration_environment(monkeypatch):
    monkeypatch.delenv("EXTENDED_OPENAI_IDENTITY_STORE", raising=False)
    monkeypatch.setenv(
        SEMANTIC_WRITES_MODE_ENV,
        LEGACY_MIGRATION_MODE,
    )
    monkeypatch.setenv("EXTENDED_OPENAI_FRIGATE_SYNC", "on")


def _mutable_store(path: Path) -> IdentityStore:
    store = IdentityStore(db_path=str(path))
    store.setup()
    return store


def _populated_store(path: Path) -> tuple[IdentityStore, str, str]:
    store = _mutable_store(path)
    first = store.create_identity(
        "Primary",
        relationship_type="me",
        frigate_person_name="primary_face",
    )
    second = store.create_identity("Parent", relationship_type="family_immediate")
    store.add_relationship(first, second, "parent")
    store.set_preference(first, "lighting", "warm")
    store._conn.execute(
        "INSERT INTO face_crops_meta("
        "identity_uuid, frigate_event_id, source_camera, captured_at"
        ") VALUES(?, 'event-1', 'camera-1', '2026-01-01T00:00:00Z')",
        (first,),
    )
    return store, first, second


def _freeze_populated(path: Path) -> tuple[str, str]:
    store, first, second = _populated_store(path)
    store.close()
    assert freeze_database(path) == SEMANTIC_WRITE_FENCE_GENERATION
    return first, second


def _raw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def test_offline_fence_is_idempotent_pending_cutover_and_query_only(tmp_path):
    path = tmp_path / "identity.db"
    first, _second = _freeze_populated(path)
    assert freeze_database(path) == SEMANTIC_WRITE_FENCE_GENERATION

    raw = _raw(path)
    try:
        assert verify_semantic_write_fence(raw, schema_ddl=SCHEMA_DDL)
        assert raw.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (SEMANTIC_AUTHORITY_KEY,),
        ).fetchone() == (LEGACY_FROZEN_PENDING_CUTOVER,)
        assert len(TRIGGER_SQL) == 27
        assert set(TRIGGER_SQL) == {
            row[0]
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        audit_rows = raw.execute(
            "SELECT ts, kind, actor, target_uuid, before_json, after_json, "
            "link_conv_id, link_turn_id, link_tool_idx, pinned "
            "FROM change_log"
        ).fetchall()
        assert audit_rows
        assert all(
            ts == SCRUBBED_AUDIT_TIMESTAMP
            and kind == SCRUBBED_AUDIT_KIND
            and actor == SCRUBBED_AUDIT_ACTOR
            and target is None
            and before is None
            and after == SCRUBBED_AUDIT_ENVELOPE
            and conv is None
            and turn is None
            and tool is None
            and pinned == 0
            for (
                ts,
                kind,
                actor,
                target,
                before,
                after,
                conv,
                turn,
                tool,
                pinned,
            ) in audit_rows
        )
        assert verify_cutover_witness(path, schema_ddl=SCHEMA_DDL)
    finally:
        raw.close()

    reopened = IdentityStore(db_path=str(path))
    reopened.setup()
    try:
        assert reopened.semantic_write_fence_installed is True
        assert reopened.semantic_write_fence_generation == 2
        assert reopened.semantic_write_fence_trigger_digest == EXPECTED_TRIGGER_DIGEST
        assert reopened.legacy_authority_state == LEGACY_FROZEN_PENDING_CUTOVER
        assert reopened._conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert reopened.get_identity(first).display_name == "Primary"
        with pytest.raises(PermissionError, match="frozen pending"):
            reopened.update_identity(
                first,
                {"pronouns": "he/him"},
                allow_legacy_self_profile_edit=True,
            )
        assert reopened.pending_writes() == []
        with pytest.raises(PermissionError, match="frozen pending"):
            reopened.mark_pending_write(1, state="done")
        with pytest.raises(PermissionError, match="frozen pending"):
            reopened.purge_change_log()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "table",
    (
        "identities",
        "identity_aliases",
        "enrollments",
        "relationships",
        "preferences",
        "face_crops_meta",
        "change_log",
        "pending_writes",
    ),
)
def test_every_semantic_table_rejects_insert_without_nullable_bypass(
    tmp_path,
    table,
):
    path = tmp_path / f"{table}.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=FENCE_ABORT_MESSAGE):
            raw.execute(f'INSERT INTO "{table}" DEFAULT VALUES')
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("table", "nullable_probe"),
    (
        ("identities", "pronouns = NULL"),
        ("identity_aliases", "alias = NULL"),
        ("enrollments", "retired_at = NULL"),
        ("relationships", "ended_at = NULL"),
        ("preferences", "confidence = NULL"),
        ("face_crops_meta", "score = NULL"),
        ("change_log", "before_json = NULL"),
    ),
)
def test_existing_semantic_rows_reject_null_update_and_delete(
    tmp_path,
    table,
    nullable_probe,
):
    path = tmp_path / f"{table}.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=FENCE_ABORT_MESSAGE):
            raw.execute(f'UPDATE "{table}" SET {nullable_probe}')
        with pytest.raises(sqlite3.IntegrityError, match=FENCE_ABORT_MESSAGE):
            raw.execute(f'DELETE FROM "{table}"')
    finally:
        raw.close()


def test_fence_and_pending_authority_markers_reject_all_mutation_forms(tmp_path):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        for key in (SEMANTIC_WRITE_FENCE_KEY, SEMANTIC_AUTHORITY_KEY):
            with pytest.raises(
                sqlite3.IntegrityError,
                match=FENCE_MARKER_ABORT_MESSAGE,
            ):
                raw.execute(
                    "UPDATE schema_meta SET value = value WHERE key = ?",
                    (key,),
                )
            with pytest.raises(
                sqlite3.IntegrityError,
                match=FENCE_MARKER_ABORT_MESSAGE,
            ):
                raw.execute("DELETE FROM schema_meta WHERE key = ?", (key,))
            with pytest.raises(
                sqlite3.IntegrityError,
                match=FENCE_MARKER_ABORT_MESSAGE,
            ):
                raw.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) "
                    "VALUES(?, 'replacement')",
                    (key,),
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=FENCE_MARKER_ABORT_MESSAGE,
        ):
            raw.execute(
                "INSERT INTO schema_meta(key, value) VALUES('new', 'value')"
            )
    finally:
        raw.close()


def test_sql_contract_is_byte_faithful_inside_json_literals():
    left = "SELECT '{\"a\":  1}'"
    right = "SELECT '{\"a\": 1}'"
    assert _stable_sql(left + ";") == left
    assert _stable_sql(left + "\r\n") == left + "\n"
    assert _stable_sql(left) != _stable_sql(right)


def test_pinned_schema_rejects_json_literal_whitespace_mutation(tmp_path):
    path = tmp_path / "identity.db"
    mutated_ddl = SCHEMA_DDL.replace(
        "flags_extra TEXT NOT NULL DEFAULT '{}'",
        "flags_extra TEXT NOT NULL DEFAULT '{ }'",
        1,
    )
    assert mutated_ddl != SCHEMA_DDL
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript(mutated_ddl)
    raw.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_schema_definition_mismatch",
    ):
        freeze_database(path)


@pytest.mark.parametrize(
    ("mutated", "expected_error"),
    (
        (
            lambda raw: raw.replace('"generation":2', '"generation":true'),
            "semantic_write_fence_generation_type_invalid",
        ),
        (
            lambda raw: raw.replace('"generation":2', '"generation":2.0'),
            "semantic_write_fence_generation_type_invalid",
        ),
        (
            lambda raw: raw[:-1] + ',"generation":2}',
            "semantic_write_fence_marker_duplicate_key",
        ),
        (
            lambda raw: raw.replace('":', '": ', 1),
            "semantic_write_fence_marker_noncanonical_or_mismatch",
        ),
    ),
)
def test_marker_rejects_noncanonical_types_and_duplicate_keys(
    tmp_path,
    mutated,
    expected_error,
):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        for event in ("insert", "update", "delete"):
            raw.execute(
                f"DROP TRIGGER e4_identity_fence_schema_meta_{event}"
            )
        current = raw.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (SEMANTIC_WRITE_FENCE_KEY,),
        ).fetchone()[0]
        raw.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?",
            (mutated(current), SEMANTIC_WRITE_FENCE_KEY),
        )
        with pytest.raises(LegacyIdentityFenceError, match=expected_error):
            verify_semantic_write_fence(raw, schema_ddl=SCHEMA_DDL)
    finally:
        raw.close()


def test_fenced_schema_meta_contract_rejects_extra_key(tmp_path):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        for event in ("insert", "update", "delete"):
            raw.execute(
                f"DROP TRIGGER e4_identity_fence_schema_meta_{event}"
            )
        raw.execute(
            "INSERT INTO schema_meta(key, value) "
            "VALUES('unexpected', 'metadata')"
        )
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_schema_meta_key_set_mismatch",
        ):
            verify_semantic_write_fence(raw, schema_ddl=SCHEMA_DDL)
    finally:
        raw.close()


def test_trigger_definition_mutation_is_detected_exactly(tmp_path):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    name = sorted(TRIGGER_SQL)[0]
    raw = _raw(path)
    try:
        raw.execute(f'DROP TRIGGER "{name}"')
        raw.execute(TRIGGER_SQL[name].replace(FENCE_ABORT_MESSAGE, f"{FENCE_ABORT_MESSAGE} "))
        with pytest.raises(
            LegacyIdentityFenceError,
            match="semantic_write_fence_trigger_definition_mismatch",
        ):
            verify_semantic_write_fence(raw, schema_ddl=SCHEMA_DDL)
    finally:
        raw.close()


def test_installer_rejects_extra_schema_object(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    store.close()
    raw = _raw(path)
    raw.execute("CREATE TABLE unreviewed_extra(value TEXT)")
    raw.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_schema_object_set_mismatch",
    ):
        freeze_database(path)


def test_installer_rejects_foreign_key_orphan(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    store.close()
    raw = _raw(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.execute(
        "INSERT INTO identity_aliases("
        "identity_uuid, alias, alias_kind, created_at"
        ") VALUES('missing', 'orphan', 'name', '2026-01-01T00:00:00Z')"
    )
    raw.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_pre_foreign_key_check_failed",
    ):
        freeze_database(path)


def test_installer_rejects_recognition_link_mismatch_and_outbox(tmp_path):
    mismatch = tmp_path / "mismatch.db"
    store = _mutable_store(mismatch)
    store.create_identity("One", frigate_person_name="face_one")
    store._conn.execute(
        "DELETE FROM identity_aliases "
        "WHERE alias = 'face_one' AND alias_kind = 'frigate_name'"
    )
    store.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_recognition_link_mismatch",
    ):
        freeze_database(mismatch)

    outbox = tmp_path / "outbox.db"
    store = _mutable_store(outbox)
    store._conn.execute(
        "INSERT INTO pending_writes("
        "target_system, op, payload, state, created_at, updated_at"
        ") VALUES('frigate', 'delete', '{}', 'pending', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    store.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_pending_writes_not_empty",
    ):
        freeze_database(outbox)


def test_installer_rejects_casefolded_recognition_collision(tmp_path):
    path = tmp_path / "collision.db"
    store = _mutable_store(path)
    store.create_identity("First", frigate_person_name="CaseFace")
    store.create_identity("Second", frigate_person_name="caseface")
    store.close()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_recognition_casefold_collision",
    ):
        freeze_database(path)


def test_startup_detects_tamper_before_any_schema_mutation(tmp_path):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    missing = sorted(TRIGGER_SQL)[0]
    raw = _raw(path)
    raw.execute(f'DROP TRIGGER "{missing}"')
    raw.close()

    reopened = IdentityStore(db_path=str(path))
    with pytest.raises(
        LegacyIdentityFenceError,
        match="semantic_write_fence_trigger_set_mismatch",
    ):
        reopened.setup()
    assert reopened._conn is None
    check = _raw(path)
    try:
        assert check.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (missing,),
        ).fetchone() is None
    finally:
        check.close()


def test_external_witness_blocks_old_restore_and_allows_offline_recovery(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "identity.db"
    store, _first, _second = _populated_store(path)
    store.close()
    old_backup = tmp_path / "pre-cutover.db"
    shutil.copyfile(path, old_backup)

    freeze_database(path)
    witness = cutover_witness_path(path)
    assert witness.name.endswith(CUTOVER_WITNESS_SUFFIX)
    assert verify_cutover_witness(path, schema_ddl=SCHEMA_DDL)
    if os.name != "nt":
        assert witness.stat().st_mode & 0o077 == 0

    # Restore only the old SQLite image. The external witness is deliberately
    # not part of that restore and permanently disables migration mode.
    shutil.copyfile(old_backup, path)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    monkeypatch.setenv(SEMANTIC_WRITES_MODE_ENV, LEGACY_MIGRATION_MODE)
    restored = IdentityStore(db_path=str(path))
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_cutover_witness_requires_fenced_database",
    ):
        restored.setup()

    # The stopped-host ceremony can safely finish a witness-first crash or an
    # old-DB restore, but the normal application can never reopen migration.
    assert freeze_database(path) == SEMANTIC_WRITE_FENCE_GENERATION
    reopened = IdentityStore(db_path=str(path))
    reopened.setup()
    try:
        assert reopened.semantic_write_fence_installed is True
        with pytest.raises(PermissionError, match="frozen pending"):
            reopened.create_identity("migration must remain closed")
    finally:
        reopened.close()


def test_witness_first_crash_state_fails_closed_until_installer_resumes(
    tmp_path,
):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    store.close()
    ensure_cutover_witness(path, schema_ddl=SCHEMA_DDL)

    blocked = IdentityStore(db_path=str(path))
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_cutover_witness_requires_fenced_database",
    ):
        blocked.setup()
    assert freeze_database(path) == SEMANTIC_WRITE_FENCE_GENERATION


def test_witness_catches_complete_in_database_fence_artifact_removal(
    tmp_path,
):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    raw = _raw(path)
    try:
        for name in sorted(TRIGGER_SQL):
            raw.execute(f'DROP TRIGGER "{name}"')
        raw.execute("DELETE FROM schema_meta")
    finally:
        raw.close()

    reopened = IdentityStore(db_path=str(path))
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_cutover_witness_requires_fenced_database",
    ):
        reopened.setup()


def test_default_application_freeze_is_not_physical_fence_authority(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(SEMANTIC_WRITES_MODE_ENV, raising=False)
    path = tmp_path / "identity.db"
    store = IdentityStore(db_path=str(path))
    store.setup()
    try:
        assert store.semantic_write_fence_installed is False
        assert store.legacy_authority_state == LEGACY_APPLICATION_FROZEN_UNPROVEN
        assert store._conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            (SEMANTIC_AUTHORITY_KEY,),
        ).fetchone()[0] == LEGACY_APPLICATION_FROZEN_UNPROVEN
    finally:
        store.close()
    assert not cutover_witness_path(path).exists()
    assert freeze_database(path) == SEMANTIC_WRITE_FENCE_GENERATION


@pytest.mark.parametrize(
    ("key", "value", "expected_error"),
    (
        ("unexpected", "value", "legacy_identity_schema_meta_key_set_mismatch"),
        ("schema_version", "999", "legacy_identity_schema_version_mismatch"),
    ),
)
def test_unfenced_schema_meta_contract_is_exact(
    tmp_path,
    key,
    value,
    expected_error,
):
    path = tmp_path / f"{key}.db"
    store = _mutable_store(path)
    store.close()
    raw = _raw(path)
    if key == "schema_version":
        raw.execute(
            "UPDATE schema_meta SET value = ? WHERE key = ?",
            (value, key),
        )
    else:
        raw.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
            (key, value),
        )
    raw.close()
    with pytest.raises(LegacyIdentityFenceError, match=expected_error):
        freeze_database(path)


def test_fresh_admin_read_degrades_after_post_startup_tamper(tmp_path):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    store = IdentityStore(db_path=str(path))
    store.setup()
    try:
        raw = _raw(path)
        raw.execute(f'DROP TRIGGER "{sorted(TRIGGER_SQL)[0]}"')
        raw.close()
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_frozen_view_degraded",
        ):
            store.list_identities()
        assert store.setup_error.startswith("legacy_frozen_view_degraded:")
    finally:
        store.close()


def test_identity_store_lifetime_lock_blocks_offline_installer(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    try:
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_database_active",
        ):
            freeze_database(path)
    finally:
        store.close()


def test_hardlink_alias_cannot_bypass_ceremony_or_lifetime_boundary(
    tmp_path,
):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    alias = tmp_path / "identity-alias.db"
    os.link(path, alias)
    try:
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_database_link_count_invalid",
        ):
            freeze_database(alias)
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_semantic_database_link_count_invalid",
        ):
            store.create_identity("hardlink write must fail")
        second = IdentityStore(
            db_path=str(alias),
            operational_db_path=str(tmp_path / "second-recognition.db"),
        )
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_semantic_database_link_count_invalid",
        ):
            second.setup()
    finally:
        store.close()


def test_witness_and_operational_database_reject_hardlinks(tmp_path):
    semantic = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    store = IdentityStore(
        db_path=str(semantic),
        operational_db_path=str(operational),
    )
    store.setup()
    store.close()
    operational_alias = tmp_path / "recognition-alias.db"
    os.link(operational, operational_alias)
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_operational_database_link_count_invalid",
    ):
        IdentityStore(
            db_path=str(semantic),
            operational_db_path=str(operational),
        ).setup()
    operational_alias.unlink()

    freeze_database(semantic)
    witness = cutover_witness_path(semantic)
    witness_alias = tmp_path / "witness-alias"
    os.link(witness, witness_alias)
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_cutover_witness_link_count_invalid",
    ):
        IdentityStore(db_path=str(semantic)).setup()


def test_open_raw_writer_and_wal_reader_block_freeze(tmp_path):
    writer_path = tmp_path / "writer.db"
    store = _mutable_store(writer_path)
    store.close()
    writer = _raw(writer_path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(
            (sqlite3.OperationalError, LegacyIdentityFenceError)
        ):
            freeze_database(writer_path)
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    reader_path = tmp_path / "reader.db"
    store = _mutable_store(reader_path)
    store.close()
    reader = _raw(reader_path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM identities").fetchone()
    writer = _raw(reader_path)
    writer.execute(
        "INSERT INTO change_log(ts, kind, actor) "
        "VALUES('2026-01-01T00:00:00Z', 'detection', 'system')"
    )
    writer.close()
    try:
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_pre_wal_checkpoint_not_clean",
        ):
            freeze_database(reader_path)
    finally:
        reader.execute("ROLLBACK")
        reader.close()
    assert freeze_database(reader_path) == SEMANTIC_WRITE_FENCE_GENERATION


def test_successful_freeze_has_no_wal_or_journal_content(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    canary = "E4_AUDIT_SNAPSHOT_CANARY_6f24c5f3"
    store._conn.execute(
        "INSERT INTO change_log("
        "ts, kind, actor, target_uuid, before_json, after_json, "
        "link_conv_id, link_turn_id, link_tool_idx, pinned"
        ") VALUES(?, 'identity_mutation', ?, ?, ?, ?, ?, ?, 7, 1)",
        (
            canary,
            canary,
            canary,
            json.dumps({"secret": canary}),
            json.dumps({"secret": canary}),
            canary,
            canary,
        ),
    )
    store.close()
    freeze_database(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        assert not sidecar.exists()
    assert canary.encode("utf-8") not in path.read_bytes()
    immutable = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
        isolation_level=None,
    )
    try:
        assert verify_semantic_write_fence(
            immutable,
            schema_ddl=SCHEMA_DDL,
        )
    finally:
        immutable.close()
    assert not path.with_name(
        f".{path.name}.e4-main-file-verification"
    ).exists()


def test_mutable_startup_scrub_clears_every_content_bearing_audit_field(
    tmp_path,
):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    canary = "E4_STARTUP_AUDIT_CANARY_0c5223"
    store._conn.execute(
        "INSERT INTO change_log("
        "ts, kind, actor, target_uuid, before_json, after_json, "
        "link_conv_id, link_turn_id, link_tool_idx, pinned"
        ") VALUES(?, 'identity_mutation', ?, ?, ?, ?, ?, ?, 11, 1)",
        (
            canary,
            canary,
            canary,
            json.dumps({"canary": canary}),
            json.dumps({"canary": canary}),
            canary,
            canary,
        ),
    )
    store.close()
    reopened = _mutable_store(path)
    reopened.close()
    raw = sqlite3.connect(path)
    try:
        rows = raw.execute(
            "SELECT ts, kind, actor, target_uuid, before_json, after_json, "
            "link_conv_id, link_turn_id, link_tool_idx, pinned "
            "FROM change_log"
        ).fetchall()
        assert rows
        assert all(
            row
            == (
                SCRUBBED_AUDIT_TIMESTAMP,
                SCRUBBED_AUDIT_KIND,
                SCRUBBED_AUDIT_ACTOR,
                None,
                None,
                SCRUBBED_AUDIT_ENVELOPE,
                None,
                None,
                None,
                0,
            )
            for row in rows
        )
    finally:
        raw.close()
    assert canary.encode() not in path.read_bytes()


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload):
        self.payload = payload

    async def get(self, _url):
        return _Response(self.payload)


def test_fenced_privacy_policy_blocks_dnt_ignored_and_world_state(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    dnt = store.create_identity("DNT", frigate_person_name="dnt_face")
    ignored = store.create_identity("Ignored", frigate_person_name="ignored_face")
    private = store.create_identity("Private", frigate_person_name="private_face")
    store.update_identity(dnt, {"do_not_track": True})
    store.update_identity(ignored, {"is_ignored": True})
    store.update_identity(private, {"is_private": True, "is_silent": True})
    store.close()
    freeze_database(path)

    fenced = IdentityStore(
        db_path=str(path),
        core_recognition_policy=lambda name: (
            "restricted" if name == "private_face" else "allowed"
        ),
    )
    fenced.setup()
    try:
        assert fenced.ensure_recognition_enrollment("dnt_face") == ""
        assert fenced.ensure_recognition_enrollment("ignored_face") == ""
        assert fenced.resolve_operational_recognition_name("dnt_face") is None
        assert fenced.resolve_operational_recognition_name("ignored_face") is None
        assert fenced.ensure_recognition_enrollment("private_face") == "private_face"
        restricted = fenced.resolve_operational_recognition_name("private_face")
        assert restricted is not None
        assert not hasattr(restricted, "display_label")

        report = asyncio.run(
            auto_seed_identities_from_frigate(
                fenced,
                http_client=_Client(
                    {
                        "dnt_face": ["crop"],
                        "ignored_face": ["crop"],
                        "private_face": ["crop"],
                        "new_face": ["crop"],
                    }
                ),
            )
        )
        assert report.identities_skipped_policy == 2
        assert fenced.resolve_operational_recognition_name("new_face") is not None

        aggregator = WorldStateAggregator.__new__(WorldStateAggregator)
        aggregator._identity_store = object()
        aggregator.set_identity_store(fenced)
        assert aggregator._identity_store is None
        assert fenced.resolve_frigate_name("private_face") is None
    finally:
        fenced.close()


def test_fenced_recognition_requires_current_core_privacy_provider(tmp_path):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    store.create_identity("Allowed", frigate_person_name="allowed_face")
    assert store.ensure_recognition_enrollment("allowed_face") == "allowed_face"
    store.close()
    freeze_database(path)

    no_core = IdentityStore(db_path=str(path))
    no_core.setup()
    try:
        assert no_core.ensure_recognition_enrollment("allowed_face") == ""
        assert (
            no_core.resolve_operational_recognition_name("allowed_face")
            is None
        )
    finally:
        no_core.close()

    current_core = IdentityStore(
        db_path=str(path),
        core_recognition_policy=lambda _name: "allowed",
    )
    current_core.setup()
    try:
        assert (
            current_core.ensure_recognition_enrollment("allowed_face")
            == "allowed_face"
        )
        assert (
            current_core.resolve_operational_recognition_name(
                "allowed_face"
            )
            is not None
        )
    finally:
        current_core.close()


def test_casefold_collision_dnt_wins_and_purges_stale_operational_row(
    tmp_path,
):
    path = tmp_path / "identity.db"
    store = _mutable_store(path)
    store.create_identity("Allowed", frigate_person_name="Straße")
    assert store.ensure_recognition_enrollment("Straße") == "Straße"
    blocked = store.create_identity("Blocked")
    assert store.add_alias(blocked, "STRASSE", kind="frigate_name")
    store.update_identity(blocked, {"do_not_track": True})

    assert store.legacy_recognition_persistence_policy("strasse") == "blocked"
    assert store.resolve_operational_recognition_name("Straße") is None
    store.close()

    # Setup also performs the purge, so a stale row cannot survive a restart.
    raw = sqlite3.connect(
        path.with_name("identity-recognition.db"),
        isolation_level=None,
    )
    raw.execute(
        "INSERT INTO operational_recognition_enrollments("
        "frigate_person_name, source, created_at, updated_at"
        ") VALUES('Straße', 'frigate', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    raw.close()
    reopened = _mutable_store(path)
    try:
        assert reopened.resolve_operational_recognition_name("strasse") is None
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "privacy_patch",
    ({"do_not_track": True}, {"is_private": True}),
)
def test_privacy_update_immediately_purges_durable_operational_row(
    tmp_path,
    privacy_patch,
):
    path = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    store = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
    )
    store.setup()
    identity_uuid = store.create_identity(
        "Privacy subject",
        frigate_person_name="privacy_face",
    )
    assert (
        store.ensure_recognition_enrollment("privacy_face")
        == "privacy_face"
    )
    assert store.update_identity(identity_uuid, privacy_patch)
    assert store._operational_conn.execute(
        "SELECT COUNT(*) FROM operational_recognition_enrollments"
    ).fetchone()[0] == 0
    store.close()


def test_periodic_seed_reapplies_changed_core_privacy_before_network(
    tmp_path,
):
    path = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    seed = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
    )
    seed.setup()
    seed.create_identity("Allowed", frigate_person_name="allowed_face")
    seed.close()
    freeze_database(path)

    policy = {"value": "allowed"}
    fenced = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
        core_recognition_policy=lambda _name: policy["value"],
    )
    fenced.setup()
    try:
        assert (
            fenced.ensure_recognition_enrollment("allowed_face")
            == "allowed_face"
        )
        policy["value"] = "blocked"
        report = asyncio.run(
            auto_seed_identities_from_frigate(
                fenced,
                http_client=_Client({"allowed_face": ["crop"]}),
            )
        )
        assert report.operational_rows_purged == 1
        assert fenced._operational_conn.execute(
            "SELECT COUNT(*) FROM operational_recognition_enrollments"
        ).fetchone()[0] == 0
    finally:
        fenced.close()


def test_operational_casefold_ambiguity_is_deleted_fail_closed(tmp_path):
    path = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    store = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
    )
    store.setup()
    store.close()
    raw = sqlite3.connect(operational, isolation_level=None)
    raw.execute(
        "INSERT INTO operational_recognition_enrollments VALUES("
        "'Straße', 'frigate', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')"
    )
    raw.execute(
        "INSERT INTO operational_recognition_enrollments VALUES("
        "'STRASSE', 'frigate', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')"
    )
    raw.close()
    reopened = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
    )
    reopened.setup()
    try:
        assert reopened.resolve_operational_recognition_name("strasse") is None
        assert reopened._operational_conn.execute(
            "SELECT COUNT(*) FROM operational_recognition_enrollments"
        ).fetchone()[0] == 0
    finally:
        reopened.close()


def test_operational_path_substitution_is_detected_before_mutation(tmp_path):
    path = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    store = IdentityStore(
        db_path=str(path),
        operational_db_path=str(operational),
    )
    store.setup()
    replacement = tmp_path / "replacement.db"
    raw = sqlite3.connect(replacement)
    raw.execute("CREATE TABLE unrelated(value TEXT)")
    raw.close()
    try:
        os.replace(replacement, operational)
    except PermissionError:
        # Windows may deny namespace replacement while SQLite holds the file.
        # Keep the store's recorded inode and connection object, close only
        # the OS handle, then prove the identity guard fires before SQL use.
        store._operational_conn.close()
        os.replace(replacement, operational)
    try:
        with pytest.raises(
            LegacyIdentityFenceError,
            match="legacy_identity_operational_database_substituted",
        ):
            store.ensure_recognition_enrollment("must_not_write")
    finally:
        store.close()


def test_operational_connect_redirect_to_semantic_fails_before_mutation(
    tmp_path,
    monkeypatch,
):
    semantic = tmp_path / "identity.db"
    visible_operational = tmp_path / "recognition.db"
    seed = IdentityStore(
        db_path=str(semantic),
        operational_db_path=":memory:",
    )
    seed.setup()
    seed.create_identity("Semantic canary")
    seed.close()
    visible = sqlite3.connect(visible_operational, isolation_level=None)
    visible.execute(OPERATIONAL_RECOGNITION_TABLE_SQL)
    visible.close()

    import identity_store as identity_store_module

    real_connect = identity_store_module.sqlite3.connect

    def redirected_connect(database, *args, **kwargs):
        if str(database) == str(visible_operational):
            return real_connect(str(semantic), *args, **kwargs)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(
        identity_store_module.sqlite3,
        "connect",
        redirected_connect,
    )
    redirected = IdentityStore(
        db_path=str(semantic),
        operational_db_path=str(visible_operational),
    )
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_operational_unknown_object",
    ):
        redirected.setup()

    semantic_check = real_connect(semantic)
    try:
        semantic_tables = {
            row[0]
            for row in semantic_check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "operational_recognition_enrollments" not in semantic_tables
        assert "operational_store_meta" not in semantic_tables
    finally:
        semantic_check.close()
    visible_check = real_connect(visible_operational)
    try:
        assert visible_check.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'operational_store_meta'"
        ).fetchone() is None
        assert visible_check.execute(
            "SELECT COUNT(*) FROM operational_recognition_enrollments"
        ).fetchone()[0] == 0
    finally:
        visible_check.close()


def test_operational_prerelease_display_labels_are_removed_and_compacted(
    tmp_path,
):
    semantic = tmp_path / "identity.db"
    operational = tmp_path / "recognition.db"
    canary = "E4_OPERATIONAL_DISPLAY_LABEL_CANARY"
    raw = sqlite3.connect(operational, isolation_level=None)
    raw.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE operational_recognition_enrollments (
            frigate_person_name TEXT PRIMARY KEY NOT NULL COLLATE NOCASE,
            display_label TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'frigate',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO operational_recognition_enrollments VALUES("
        "'opaque-face-id', ?, 'frigate', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (canary,),
    )
    raw.close()

    store = IdentityStore(
        db_path=str(semantic),
        operational_db_path=str(operational),
    )
    store.setup()
    store.close()
    check = sqlite3.connect(operational)
    try:
        columns = {
            row[1]
            for row in check.execute(
                "PRAGMA table_info(operational_recognition_enrollments)"
            )
        }
        assert "display_label" not in columns
        assert check.execute(
            "SELECT frigate_person_name "
            "FROM operational_recognition_enrollments"
        ).fetchone() == ("opaque-face-id",)
    finally:
        check.close()
    assert canary.encode("utf-8") not in operational.read_bytes()


def test_semantic_and_operational_database_path_collisions_are_rejected(
    tmp_path,
):
    same = tmp_path / "same.db"
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_database_path_collision",
    ):
        IdentityStore(
            db_path=str(same),
            operational_db_path=str(same),
        ).setup()

    semantic = tmp_path / "semantic.db"
    seed = IdentityStore(
        db_path=str(semantic),
        operational_db_path=":memory:",
    )
    seed.setup()
    seed.close()
    hardlink = tmp_path / "hardlink.db"
    os.link(semantic, hardlink)
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_semantic_database_link_count_invalid",
    ):
        IdentityStore(
            db_path=str(semantic),
            operational_db_path=str(hardlink),
        ).setup()


def test_symlink_database_paths_are_rejected(tmp_path):
    target = tmp_path / "target.db"
    seed = IdentityStore(
        db_path=str(target),
        operational_db_path=":memory:",
    )
    seed.setup()
    seed.close()
    link = tmp_path / "link.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_database_symlink_rejected",
    ):
        IdentityStore(
            db_path=str(link),
            operational_db_path=str(tmp_path / "operational.db"),
        ).setup()
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_database_symlink_rejected",
    ):
        freeze_database(link)


def test_unchanged_fence_reads_reuse_verified_file_fingerprint(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "identity.db"
    _freeze_populated(path)
    import identity_store as identity_store_module

    original = identity_store_module.verify_semantic_write_fence
    calls = 0

    def counted(connection, *, schema_ddl):
        nonlocal calls
        calls += 1
        return original(connection, schema_ddl=schema_ddl)

    monkeypatch.setattr(
        identity_store_module,
        "verify_semantic_write_fence",
        counted,
    )
    store = IdentityStore(db_path=str(path))
    store.setup()
    try:
        setup_calls = calls
        assert setup_calls >= 2
        store.list_identities()
        store.list_identities()
        assert calls == setup_calls
    finally:
        store.close()


def test_default_prefence_application_freeze_is_also_context_cutoff(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv(SEMANTIC_WRITES_MODE_ENV, raising=False)
    store = IdentityStore(db_path=str(tmp_path / "identity.db"))
    store.setup()
    try:
        assert store.semantic_writes_frozen is True
        assert store.semantic_write_fence_installed is False
        aggregator = WorldStateAggregator.__new__(WorldStateAggregator)
        aggregator._identity_store = object()
        aggregator.set_identity_store(store)
        assert aggregator._identity_store is None
        assert store.resolve_frigate_name("anything") is None
    finally:
        store.close()


def test_offline_installer_refuses_to_create_database(tmp_path):
    with pytest.raises(
        LegacyIdentityFenceError,
        match="legacy_identity_database_not_found",
    ):
        freeze_database(tmp_path / "missing.db")
