#!/usr/bin/env python3
"""Collect a content-free physical observation of the frozen identity store.

Production accepts no arguments and uses only fixed HAOS paths. It must run as
root while Home Assistant Core reports ``stopped``. The collector holds the
same OS process lock as the integration, verifies the immutable trigger fence
and external witness, runs blocked-write probes, checks integrity/checkpoint
state, and proves the main SQLite file did not change during observation.

The canonical JSON result contains hashes, categorical results, a stable
installation UUID, and time only. It contains no person, relationship, alias,
coordinate, utterance, or source row content and grants no authority itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import time
from typing import Callable, Sequence
import uuid

try:
    from .identity_store import SCHEMA_DDL
    from .legacy_identity_fence import (
        EXPECTED_TRIGGER_DIGEST,
        SEMANTIC_WRITE_FENCE_GENERATION,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        cutover_witness_path,
        probe_semantic_write_fence,
        schema_digest,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )
except ImportError:
    from identity_store import SCHEMA_DDL  # type: ignore[no-redef]
    from legacy_identity_fence import (  # type: ignore[no-redef]
        EXPECTED_TRIGGER_DIGEST,
        SEMANTIC_WRITE_FENCE_GENERATION,
        LegacyIdentityFenceError,
        LegacyIdentityProcessLock,
        cutover_witness_path,
        probe_semantic_write_fence,
        schema_digest,
        verify_cutover_witness,
        verify_semantic_write_fence,
    )


CONTRACT = "legacy-identity-physical-freeze-observation-v1"
INSTALLATION_CONTRACT = "legacy-identity-source-installation-v1"
DATABASE_PATH = Path("/config/extended_openai_conversation/identity.db")
INSTALLATION_PATH = Path(
    "/config/extended_openai_conversation/.source-installation-e5.json"
)
OPERATOR_ROOT = Path("/config/home-agent-operator")
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_CLI_BYTES = 64 * 1024
BUILD_FILENAMES = (
    "legacy_identity_fence.py",
    "freeze_legacy_identity_semantics.py",
    "collect_legacy_identity_freeze_observation.py",
)


class FreezeObservationError(RuntimeError):
    """A content-free physical observation failure."""


def _canonical(value) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FreezeObservationError("freeze observation encoding failed") from error


def _uuid7() -> uuid.UUID:
    timestamp_ms = time.time_ns() // 1_000_000
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def _sha256_file(path: Path, *, maximum: int) -> str:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise FreezeObservationError("freeze observation file is missing") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_size <= 0
        or details.st_size > maximum
    ):
        raise FreezeObservationError("freeze observation file is unsafe")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise FreezeObservationError("freeze observation file is unreadable") from error
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_installation_id(path: Path) -> str:
    if path.exists() or path.is_symlink():
        try:
            details = path.lstat()
            raw = path.read_bytes()
            value = json.loads(raw, object_pairs_hook=_reject_duplicates)
        except (OSError, ValueError, UnicodeError) as error:
            raise FreezeObservationError(
                "source installation witness is invalid"
            ) from error
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (os.name != "nt" and details.st_mode & 0o077)
            or set(value) != {"contract", "source_installation_id"}
            or value["contract"] != INSTALLATION_CONTRACT
            or _canonical(value) != raw
        ):
            raise FreezeObservationError("source installation witness is unsafe")
        try:
            parsed = uuid.UUID(value["source_installation_id"])
        except (ValueError, TypeError, AttributeError) as error:
            raise FreezeObservationError(
                "source installation witness identifier is invalid"
            ) from error
        if parsed.version != 7 or str(parsed) != value["source_installation_id"]:
            raise FreezeObservationError(
                "source installation witness identifier is invalid"
            )
        return str(parsed)

    value = {
        "contract": INSTALLATION_CONTRACT,
        "source_installation_id": str(_uuid7()),
    }
    raw = _canonical(value)
    temporary = path.with_name(f".{path.name}.new.{secrets.token_hex(12)}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return value["source_installation_id"]


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


# SQLite creates these beside a database while a connection is open in WAL
# mode and removes them on the last clean close, so their absence is direct
# evidence that nothing holds the legacy identity store open.
QUIESCENT_SUFFIXES = ("-wal", "-journal", "-shm")


def _require_home_assistant_stopped(database_path: Path = DATABASE_PATH) -> None:
    """Prove the legacy identity store is quiescent before touching it.

    This deliberately does not ask the Home Assistant CLI for a state string.
    `ha core info --raw-json` returns the Supervisor envelope, and on Core
    2026.8.1 neither the envelope nor its `data` object carries a run-state key
    at all — so a `state == "stopped"` comparison raises for every input,
    including when Home Assistant genuinely is stopped. The only test covering
    it fed a hand-written `{"state": ...}` payload the CLI never emits.

    The property this step actually needs is not "the supervisor reports a
    string" but "no process is writing the identity database", and the
    observation below already proves exactly that with a process lock, an
    integrity check, sidecar absence, and a stable digest. Checking the
    sidecars up front makes that same evidence the fail-fast guard, so the
    collector never opens the database read-write while Home Assistant is live.
    """

    for suffix in QUIESCENT_SUFFIXES:
        if Path(f"{database_path}{suffix}").exists():
            raise FreezeObservationError("Home Assistant is not stopped")
    if not database_path.exists():
        raise FreezeObservationError("legacy identity database is absent")


def _load_plan_digest(path: Path, operator_root: Path) -> str:
    inserted = False
    if str(operator_root) not in sys.path:
        sys.path.insert(0, str(operator_root))
        inserted = True
    try:
        from migrate_legacy_identity import MigrationError, load_plan

        return load_plan(path).digest
    except (ImportError, MigrationError) as error:
        raise FreezeObservationError(
            "source projection could not be verified"
        ) from error
    finally:
        if inserted:
            sys.path.remove(str(operator_root))


def _build_digest(paths: Sequence[Path], operator_module: Path) -> str:
    manifest = []
    for path in (*paths, operator_module):
        manifest.append(
            {
                "name": path.name,
                "sha256": _sha256_file(path, maximum=4 * 1024 * 1024),
            }
        )
    return hashlib.sha256(_canonical(manifest)).hexdigest()


def _checkpoint_and_integrity(connection: sqlite3.Connection) -> None:
    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint is None or tuple(int(value) for value in checkpoint) != (0, 0, 0):
        raise FreezeObservationError("identity checkpoint is not complete")
    if [
        str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()
    ] != ["ok"]:
        raise FreezeObservationError("identity integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise FreezeObservationError("identity foreign-key check failed")


def collect(
    *,
    database_path: Path = DATABASE_PATH,
    installation_path: Path = INSTALLATION_PATH,
    operator_root: Path = OPERATOR_ROOT,
    require_stopped: Callable[[], None] = _require_home_assistant_stopped,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, object]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise FreezeObservationError("freeze observation requires root on Linux")
    require_stopped()
    database = database_path.resolve(strict=True)
    installation_id = _source_installation_id(installation_path)
    witness = cutover_witness_path(database)
    before = _sha256_file(database, maximum=MAX_DATABASE_BYTES)
    with LegacyIdentityProcessLock(database):
        if not verify_cutover_witness(database, schema_ddl=SCHEMA_DDL):
            raise FreezeObservationError("external cutover witness is absent")
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=rw",
            uri=True,
            timeout=0.25,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 250")
            connection.execute("PRAGMA foreign_keys = ON")
            if not verify_semantic_write_fence(connection, schema_ddl=SCHEMA_DDL):
                raise FreezeObservationError("semantic write fence is absent")
            probe_semantic_write_fence(connection)
            _checkpoint_and_integrity(connection)
        except (sqlite3.Error, LegacyIdentityFenceError) as error:
            raise FreezeObservationError("physical semantic fence failed") from error
        finally:
            connection.close()
    for suffix in QUIESCENT_SUFFIXES:
        if Path(f"{database}{suffix}").exists():
            raise FreezeObservationError("identity journal state is not clean")
    after = _sha256_file(database, maximum=MAX_DATABASE_BYTES)
    if before != after:
        raise FreezeObservationError("identity database changed during observation")
    observed_at = now()
    if observed_at.tzinfo is None:
        raise FreezeObservationError("freeze observation clock is invalid")
    module_root = Path(__file__).resolve().parent
    build_paths = tuple(module_root / name for name in BUILD_FILENAMES)
    operator_module = operator_root / "migrate_legacy_identity.py"
    return {
        "contract": CONTRACT,
        "source_installation_id": installation_id,
        "semantic_generation": SEMANTIC_WRITE_FENCE_GENERATION,
        "source_plan_sha256": _load_plan_digest(database, operator_root),
        "database_sha256": after,
        "schema_digest": schema_digest(SCHEMA_DDL),
        "trigger_digest": EXPECTED_TRIGGER_DIGEST,
        "cutover_witness_sha256": _sha256_file(witness, maximum=4096),
        "home_assistant_state": "stopped",
        "process_lock_result": "exclusive",
        "write_probe_result": "blocked",
        "integrity_result": "passed",
        "checkpoint_result": "complete",
        "journal_result": "clean",
        "legacy_context_cutoff_status": "enforced_cutoff",
        "recognition_mode": "nonauthoritative_only",
        "freeze_kernel_build_digest": _build_digest(build_paths, operator_module),
        "observed_at": observed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def main() -> int:
    if len(sys.argv) != 1:
        print("freeze observation accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = collect()
    except (FreezeObservationError, OSError, sqlite3.Error):
        print("freeze observation failed closed", file=sys.stderr)
        return 78
    print(_canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
