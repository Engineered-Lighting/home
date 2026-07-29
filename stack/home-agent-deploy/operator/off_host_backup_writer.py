#!/usr/bin/env python3
"""Copy the encrypted local pgBackRest repository to reviewed OneDrive storage.

The writer is deliberately root-only and accepts no arguments. It snapshots
only the encrypted pgBackRest repository while holding the deployment's
repository lock, uploads the archive through one fixed rclone remote, downloads
the remote bytes again for an exact SHA-256 comparison, and only then writes
the content-minimized E5j off-host receipt.

It never reads PostgreSQL data, runtime.sqlite, the GPS spool, the erasure
ledger, pgBackRest's cipher passphrase, or any Home Assistant credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


DESTINATION_CONTRACT = "phase3-off-host-backup-destination-e5o-v1"
RECEIPT_CONTRACT = "phase3-off-host-backup-receipt-e5j-v1"
DESTINATION_CLASS = "operator_controlled_off_host"
FIXED_REMOTE = "home-agent-offhost"
FIXED_PREFIX = "HomeAgent/pgBackRest"
STANZA = "home-agent"
CONFIG_ROOT = Path("/srv/home-agent/config")
DURABLE_ROOT = Path("/srv/home-agent/durable")
REPOSITORY = DURABLE_ROOT / "pgbackrest-repository"
STAGING = DURABLE_ROOT / "off-host-export-e5o"
REPOSITORY_LOCK = Path("/srv/home-agent/locks/pgbackrest-repository.lock")
DESTINATION_PATH = CONFIG_ROOT / "off-host-backup-destination-e5o.json"
RECEIPT_PATH = CONFIG_ROOT / "phase3-off-host-backup-e5j.json"
RCLONE_CONFIG = Path("/srv/home-agent/secrets/offhost-rclone/rclone.conf")
PREFLIGHT = Path(__file__).with_name("phase3_activation_preflight.py")
REPOSITORY_GUARD = Path(__file__).with_name("pgbackrest_repository_guard.py")
BACKUP_LABEL = re.compile(r"^[0-9]{8}-[0-9]{6}F$")
MAX_JSON_BYTES = 64 * 1024
COPY_TIMEOUT_SECONDS = 3600
LOCK_TIMEOUT_SECONDS = 300


class OffHostBackupError(RuntimeError):
    """The off-host copy could not be proven without exposing private data."""


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _protected_json(path: Path) -> Any:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise OffHostBackupError("off-host destination is not configured") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise OffHostBackupError("off-host destination configuration is unsafe")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise OffHostBackupError("off-host destination configuration is invalid")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OffHostBackupError(
            "off-host destination configuration is invalid"
        ) from error


def validate_destination(value: Any) -> dict[str, str]:
    expected = {
        "contract": DESTINATION_CONTRACT,
        "destination_class": DESTINATION_CLASS,
        "remote": FIXED_REMOTE,
        "prefix": FIXED_PREFIX,
    }
    if not isinstance(value, Mapping) or dict(value) != expected:
        raise OffHostBackupError("off-host destination configuration is invalid")
    return expected


def build_receipt(*, backup_label: str, copied_at: datetime) -> dict[str, str]:
    if BACKUP_LABEL.fullmatch(backup_label) is None:
        raise OffHostBackupError("off-host backup label is invalid")
    if copied_at.tzinfo is None or copied_at.utcoffset() is None:
        raise OffHostBackupError("off-host receipt time is invalid")
    return {
        "contract": RECEIPT_CONTRACT,
        "backup_label": backup_label,
        "copied_at": copied_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "destination_class": DESTINATION_CLASS,
        "verification_status": "checksum_verified",
    }


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise OffHostBackupError("off-host writer requires root on Linux")


def _require_regular(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise OffHostBackupError("required off-host writer input is missing") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != uid
        or details.st_gid != gid
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_nlink != 1
    ):
        raise OffHostBackupError("required off-host writer input is unsafe")
    return details


def _require_directory(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int | None,
) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise OffHostBackupError("required off-host writer directory is missing") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != uid
        or details.st_gid != gid
        or (
            stat.S_IMODE(details.st_mode) != mode
            if mode is not None
            else bool(stat.S_IMODE(details.st_mode) & 0o022)
        )
    ):
        raise OffHostBackupError("required off-host writer directory is unsafe")


def _run(
    command: Sequence[str],
    *,
    timeout: int,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OffHostBackupError("off-host writer command failed") from error
    if result.returncode not in accepted_codes:
        raise OffHostBackupError("off-host writer command failed")
    return result.stdout


def latest_backup_label() -> str:
    raw = _run(
        [sys.executable, str(PREFLIGHT)],
        timeout=120,
        accepted_codes=frozenset({0, 3}),
    )
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise OffHostBackupError("backup readiness evidence is invalid")
    try:
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OffHostBackupError("backup readiness evidence is invalid") from error
    backup = report.get("backup") if isinstance(report, Mapping) else None
    label = backup.get("latest_full_backup_label") if isinstance(backup, Mapping) else None
    if (
        not isinstance(backup, Mapping)
        or backup.get("repository_healthy") is not True
        or not isinstance(label, str)
        or BACKUP_LABEL.fullmatch(label) is None
    ):
        raise OffHostBackupError("encrypted backup repository is not ready")
    return label


def _acquire_repository_lock() -> int:
    import fcntl

    _require_regular(REPOSITORY_LOCK, uid=999, gid=999, mode=0o600)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(REPOSITORY_LOCK, flags)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OffHostBackupError(
                        "timed out waiting for the backup repository"
                    )
                time.sleep(0.25)
    except BaseException:
        os.close(descriptor)
        raise


def _archive_name(label: str) -> str:
    nonce = secrets.token_hex(8)
    return f"pgbackrest-{label}-{nonce}.tar"


def create_encrypted_repository_archive(label: str) -> Path:
    import fcntl

    _require_directory(REPOSITORY, uid=999, gid=999, mode=0o700)
    _require_directory(STAGING, uid=0, gid=0, mode=0o700)
    archive = STAGING / _archive_name(label)
    if archive.exists() or archive.is_symlink():
        raise OffHostBackupError("off-host staging target already exists")
    lock_descriptor = _acquire_repository_lock()
    try:
        _run(
            [
                sys.executable,
                str(REPOSITORY_GUARD),
                str(REPOSITORY),
                STANZA,
            ],
            timeout=120,
        )
        try:
            _run(
                [
                    "tar",
                    "--create",
                    "--format=pax",
                    "--sort=name",
                    "--mtime=@0",
                    "--owner=0",
                    "--group=0",
                    "--numeric-owner",
                    "--pax-option=delete=atime,delete=ctime",
                    "--one-file-system",
                    f"--file={archive}",
                    f"--directory={REPOSITORY}",
                    ".",
                ],
                timeout=COPY_TIMEOUT_SECONDS,
            )
        except BaseException:
            try:
                archive.unlink()
            except FileNotFoundError:
                pass
            raise
        os.chown(archive, 0, 0)
        os.chmod(archive, 0o600)
        with archive.open("rb") as stream:
            os.fsync(stream.fileno())
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)
    _require_regular(archive, uid=0, gid=0, mode=0o600)
    if archive.stat().st_size <= 0:
        raise OffHostBackupError("off-host archive is empty")
    return archive


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _remote_sha256(remote_path: str, *, maximum_bytes: int) -> tuple[str, int]:
    command = [
        "rclone",
        "cat",
        "--config",
        str(RCLONE_CONFIG),
        "--contimeout",
        "15s",
        "--timeout",
        "5m",
        remote_path,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as error:
        raise OffHostBackupError("off-host verification failed") from error
    digest = hashlib.sha256()
    size = 0
    assert process.stdout is not None
    try:
        while block := process.stdout.read(1024 * 1024):
            size += len(block)
            if size > maximum_bytes:
                process.kill()
                raise OffHostBackupError("off-host verification size mismatch")
            digest.update(block)
        if process.wait(timeout=30) != 0:
            raise OffHostBackupError("off-host verification failed")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    return digest.hexdigest(), size


def upload_and_verify(
    archive: Path,
    destination: Mapping[str, str],
    *,
    backup_label: str,
) -> None:
    _require_regular(RCLONE_CONFIG, uid=0, gid=0, mode=0o600)
    local_digest, local_size = _sha256(archive)
    remote_path = (
        f"{destination['remote']}:{destination['prefix']}/"
        f"pgbackrest-{backup_label}.tar"
    )
    _run(
        [
            "rclone",
            "copyto",
            "--config",
            str(RCLONE_CONFIG),
            "--immutable",
            "--transfers",
            "1",
            "--checkers",
            "1",
            "--retries",
            "2",
            "--low-level-retries",
            "2",
            "--contimeout",
            "15s",
            "--timeout",
            "5m",
            str(archive),
            remote_path,
        ],
        timeout=COPY_TIMEOUT_SECONDS,
    )
    remote_digest, remote_size = _remote_sha256(
        remote_path,
        maximum_bytes=local_size,
    )
    if remote_size != local_size or remote_digest != local_digest:
        raise OffHostBackupError("off-host checksum verification failed")


def _atomic_write_receipt(value: Mapping[str, Any]) -> None:
    _require_directory(CONFIG_ROOT, uid=0, gid=0, mode=None)
    try:
        existing = RECEIPT_PATH.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        not stat.S_ISREG(existing.st_mode)
        or existing.st_uid != 0
        or existing.st_gid != 0
        or stat.S_IMODE(existing.st_mode) != 0o600
        or existing.st_nlink != 1
    ):
        raise OffHostBackupError("existing off-host receipt is unsafe")
    raw = (
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )
    temporary = RECEIPT_PATH.with_name(
        f".{RECEIPT_PATH.name}.new.{secrets.token_hex(12)}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, RECEIPT_PATH)
        directory_descriptor = os.open(CONFIG_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def run() -> dict[str, str]:
    _require_root_linux()
    destination = validate_destination(_protected_json(DESTINATION_PATH))
    label = latest_backup_label()
    archive = create_encrypted_repository_archive(label)
    try:
        upload_and_verify(archive, destination, backup_label=label)
        receipt = build_receipt(backup_label=label, copied_at=datetime.now(UTC))
        _atomic_write_receipt(receipt)
        return {
            "contract": "phase3-off-host-backup-writer-result-e5o-v1",
            "backup_label": label,
            "status": "checksum_verified",
        }
    finally:
        try:
            archive.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    if len(sys.argv) != 1:
        print("off-host backup writer accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = run()
    except OffHostBackupError:
        print("off-host backup writer failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
