#!/usr/bin/env python3
"""Offline, network-free installer for the E4 legacy Identity Store fence."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

try:
    from .identity_store import SCHEMA_DDL
    from .legacy_identity_fence import (
        EXPECTED_TRIGGER_DIGEST,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        ensure_cutover_witness,
        fence_artifacts_present,
        install_semantic_write_fence,
        scrub_legacy_audit_content,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )
except ImportError:
    from identity_store import SCHEMA_DDL  # type: ignore[no-redef]
    from legacy_identity_fence import (  # type: ignore[no-redef]
        EXPECTED_TRIGGER_DIGEST,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        ensure_cutover_witness,
        fence_artifacts_present,
        install_semantic_write_fence,
        scrub_legacy_audit_content,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )


def _require_clean_wal_checkpoint(
    connection: sqlite3.Connection,
    *,
    stage: str,
) -> None:
    result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result is None or len(result) != 3:
        raise LegacyIdentityFenceError(
            f"legacy_identity_{stage}_wal_checkpoint_unavailable"
        )
    busy, remaining, checkpointed = (int(value) for value in result)
    if busy != 0 or remaining != 0 or checkpointed != 0:
        raise LegacyIdentityFenceError(
            f"legacy_identity_{stage}_wal_checkpoint_not_clean"
        )


def _require_integrity_and_foreign_keys(
    connection: sqlite3.Connection,
    *,
    stage: str,
) -> None:
    rows = [
        str(row[0])
        for row in connection.execute("PRAGMA integrity_check").fetchall()
    ]
    if rows != ["ok"]:
        raise LegacyIdentityFenceError(
            f"legacy_identity_{stage}_integrity_check_failed"
        )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise LegacyIdentityFenceError(
            f"legacy_identity_{stage}_foreign_key_check_failed"
        )


def _fsync_main_database(path: Path) -> None:
    # Windows' CRT rejects fsync on a read-only descriptor, so use a
    # write-capable handle without modifying any bytes.
    descriptor = os.open(path, os.O_RDWR)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_empty_journal_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        if not sidecar.exists():
            continue
        if sidecar.stat().st_size != 0:
            raise LegacyIdentityFenceError(
                "legacy_identity_residual_journal_content"
            )
        sidecar.unlink()
    # The shared-memory file can remain non-empty after a clean checkpoint.
    # It is safe to remove only now: all installer connections are closed and
    # the WAL above has been proven empty/absent.
    shm = Path(f"{path}-shm")
    if Path(f"{path}-wal").exists():
        raise LegacyIdentityFenceError(
            "legacy_identity_wal_still_present_before_shm_cleanup"
        )
    if shm.exists():
        shm.unlink()


def _verify_main_file_read_only(path: Path) -> None:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
        timeout=0,
        isolation_level=None,
    )
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise LegacyIdentityFenceError(
                "legacy_identity_read_only_reopen_failed"
            )
        if not verify_semantic_write_fence(
            connection,
            schema_ddl=SCHEMA_DDL,
        ):
            raise LegacyIdentityFenceError(
                "semantic_write_fence_not_in_main_database"
            )
    finally:
        connection.close()


def _require_single_link_database(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LegacyIdentityFenceError(
            "legacy_identity_database_not_found"
        ) from exc
    if path.is_symlink() or not path.is_file():
        raise LegacyIdentityFenceError(
            "legacy_identity_database_not_regular"
        )
    if info.st_nlink != 1:
        raise LegacyIdentityFenceError(
            "legacy_identity_database_link_count_invalid"
        )


def freeze_database(path: Path) -> int:
    """Durably install and verify the fence while HA is fully stopped."""

    expanded = path.expanduser()
    if expanded.is_symlink():
        raise LegacyIdentityFenceError(
            "legacy_identity_database_symlink_rejected"
        )
    resolved = expanded.resolve()
    _require_single_link_database(resolved)

    with LegacyIdentityProcessLock(resolved):
        _require_single_link_database(resolved)
        # Persist the monotonic non-SQLite witness before opening SQLite. Even
        # hot-journal recovery or a checkpoint must not precede this boundary.
        # A failed ceremony is therefore intentionally fail-closed and can be
        # completed only by rerunning this stopped-host installer.
        ensure_cutover_witness(resolved, schema_ddl=SCHEMA_DDL)
        connection = sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=rw",
            uri=True,
            timeout=0.25,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 250")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA secure_delete = ON")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise LegacyIdentityFenceError(
                    "legacy_identity_journal_mode_not_wal"
                )
            already_fenced = fence_artifacts_present(connection)
            if already_fenced:
                if not verify_semantic_write_fence(
                    connection,
                    schema_ddl=SCHEMA_DDL,
                ):
                    raise LegacyIdentityFenceError(
                        "semantic_write_fence_partial"
                    )
            _require_clean_wal_checkpoint(connection, stage="pre")
            _require_integrity_and_foreign_keys(connection, stage="pre")
            if not already_fenced:
                # Scrubbing rows alone leaves prior snapshot bytes in free
                # pages. Commit the scrub, rebuild the database with
                # secure_delete enabled, then checkpoint before installing the
                # immutable trigger boundary.
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    scrub_legacy_audit_content(connection)
                    connection.execute("COMMIT")
                except Exception:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                connection.execute("VACUUM")
                _require_clean_wal_checkpoint(
                    connection,
                    stage="post_scrub_compaction",
                )
                _require_integrity_and_foreign_keys(
                    connection,
                    stage="post_scrub_compaction",
                )

            connection.execute("BEGIN EXCLUSIVE")
            try:
                generation = install_semantic_write_fence(
                    connection,
                    schema_ddl=SCHEMA_DDL,
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

            if not verify_semantic_write_fence(
                connection,
                schema_ddl=SCHEMA_DDL,
            ):
                raise LegacyIdentityFenceError(
                    "semantic_write_fence_post_commit_missing"
                )
            _require_clean_wal_checkpoint(connection, stage="post")
            _require_integrity_and_foreign_keys(connection, stage="post")
        finally:
            connection.close()

        _require_single_link_database(resolved)
        _fsync_main_database(resolved)
        _remove_empty_journal_sidecars(resolved)
        _fsync_directory(resolved.parent)
        _require_single_link_database(resolved)
        _verify_main_file_read_only(resolved)
        _remove_empty_journal_sidecars(resolved)
        _fsync_directory(resolved.parent)
        if not verify_cutover_witness(resolved, schema_ddl=SCHEMA_DDL):
            raise LegacyIdentityFenceError(
                "legacy_cutover_witness_missing_after_freeze"
            )
        return generation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install the irreversible E4 semantic-write fence in a stopped "
            "Home Assistant legacy identity.db."
        )
    )
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="Existing legacy identity.db; Home Assistant must be stopped.",
    )
    args = parser.parse_args(argv)
    try:
        generation = freeze_database(args.database)
    except (LegacyIdentityFenceError, sqlite3.Error, OSError) as exc:
        print(f"legacy identity freeze failed: {exc}", file=sys.stderr)
        return 1
    print(
        "legacy identity semantics frozen pending reviewed cutover "
        f"(generation={generation}, trigger_digest={EXPECTED_TRIGGER_DIGEST})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
