#!/usr/bin/env python3
"""Capture one stopped-HA legacy Identity Store snapshot directly on Ubuntu.

This E5x prerequisite has no configurable paths.  It runs as root on the
dedicated Ubuntu deployment, uses the deployment user's purpose-specific SSH
key to reach the fixed Home Assistant host, stops HA Core, proves the SQLite
journals are absent, copies ``identity.db`` straight into the encrypted private
volume, and starts HA Core in a ``finally`` boundary.  Private bytes never pass
through stdout, argv, OneDrive, or an off-host backup.

The command is intentionally create-once.  A later capture requires deliberate
operator removal of the old private E5x directory after reviewing its lineage.
It does not freeze the legacy writer or authorize semantic cutover.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import subprocess
import sys
import time
from typing import Sequence


CONTRACT = "phase3-stopped-ha-identity-snapshot-e5x-v1"
HA_HOST = "192.168.0.125"
HA_PORT = "22222"
HA_USER = "root"
HA_DATABASE = "/config/extended_openai_conversation/identity.db"
DEPLOYMENT_USER_HOME = Path("/home/marcelo-lima")
SSH_IDENTITY = DEPLOYMENT_USER_HOME / ".ssh/id_ed25519"
SSH_KNOWN_HOSTS = DEPLOYMENT_USER_HOME / ".ssh/known_hosts"
PRIVATE_ROOT = Path("/srv/home-agent/private/phase3-identity")
SNAPSHOT_PATH = PRIVATE_ROOT / "identity.db"
MAX_DATABASE_BYTES = 256 * 1024 * 1024
HTTP_READY_TIMEOUT_SECONDS = 180


class SnapshotCaptureError(RuntimeError):
    """A content-free stopped-HA snapshot failure."""


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SnapshotCaptureError("snapshot capture requires root on Linux")


def _safe_regular(path: Path, *, mode: int, owner: int) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise SnapshotCaptureError(
            "snapshot transport credential is missing"
        ) from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != owner
        or stat.S_IMODE(details.st_mode) != mode
        or details.st_nlink != 1
    ):
        raise SnapshotCaptureError("snapshot transport credential is unsafe")


def _prepare_private_root() -> None:
    PRIVATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    details = PRIVATE_ROOT.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise SnapshotCaptureError("private snapshot directory is unsafe")
    if SNAPSHOT_PATH.exists() or SNAPSHOT_PATH.is_symlink():
        raise SnapshotCaptureError("private identity snapshot already exists")


def _ssh_base() -> list[str]:
    return [
        "ssh",
        "-p",
        HA_PORT,
        "-i",
        str(SSH_IDENTITY),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={SSH_KNOWN_HOSTS}",
        f"{HA_USER}@{HA_HOST}",
    ]


def _run(
    command: Sequence[str],
    *,
    timeout: int,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SnapshotCaptureError("snapshot transport command failed") from error
    if check and result.returncode != 0:
        raise SnapshotCaptureError("snapshot transport command failed")
    return result


def _remote(*arguments: str, timeout: int = 30) -> bytes:
    result = _run([*_ssh_base(), *arguments], timeout=timeout)
    return result.stdout


def _remote_sha256() -> str:
    raw = _remote(
        "sha256sum",
        "--",
        HA_DATABASE,
        timeout=30,
    )
    fields = raw.decode("ascii", errors="strict").split()
    if len(fields) != 2 or len(fields[0]) != 64:
        raise SnapshotCaptureError("remote snapshot digest is invalid")
    try:
        int(fields[0], 16)
    except ValueError as error:
        raise SnapshotCaptureError("remote snapshot digest is invalid") from error
    return fields[0]


def _require_remote_stopped_database() -> tuple[int, str]:
    raw = _remote(
        "sh",
        "-ceu",
        (
            f'test -f "{HA_DATABASE}"; '
            f'test ! -e "{HA_DATABASE}-wal"; '
            f'test ! -e "{HA_DATABASE}-journal"; '
            f'stat -c "%s" -- "{HA_DATABASE}"'
        ),
        timeout=30,
    )
    try:
        size = int(raw.decode("ascii", errors="strict").strip())
    except (UnicodeDecodeError, ValueError) as error:
        raise SnapshotCaptureError("remote snapshot size is invalid") from error
    if size <= 0 or size > MAX_DATABASE_BYTES:
        raise SnapshotCaptureError("remote snapshot size is invalid")
    first = _remote_sha256()
    time.sleep(1)
    second = _remote_sha256()
    if first != second:
        raise SnapshotCaptureError("stopped identity database changed during capture")
    return size, first


def _copy_to_temporary(temporary: Path) -> None:
    command = [
        "scp",
        "-P",
        HA_PORT,
        "-i",
        str(SSH_IDENTITY),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={SSH_KNOWN_HOSTS}",
        f"{HA_USER}@{HA_HOST}:{HA_DATABASE}",
        str(temporary),
    ]
    _run(command, timeout=120, capture=True)


def _local_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_sqlite(path: Path) -> None:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
        isolation_level=None,
        timeout=0,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise SnapshotCaptureError("private snapshot did not open query-only")
        integrity = [
            str(row[0])
            for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity != ["ok"]:
            raise SnapshotCaptureError("private snapshot integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotCaptureError("private snapshot foreign-key check failed")
        schema = connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if schema != ("1",):
            raise SnapshotCaptureError("private snapshot schema is unsupported")
    except sqlite3.Error as error:
        raise SnapshotCaptureError(
            "private snapshot SQLite verification failed"
        ) from error
    finally:
        connection.close()


def _start_home_assistant() -> None:
    _remote("ha", "core", "start", timeout=60)


def _wait_home_assistant_ready() -> None:
    deadline = time.monotonic() + HTTP_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(HA_HOST, 8123, timeout=4)
        try:
            connection.request("GET", "/api/")
            response = connection.getresponse()
            response.read(4096)
            if response.status == 401:
                return
        except OSError:
            pass
        finally:
            connection.close()
        time.sleep(3)
    raise SnapshotCaptureError("Home Assistant did not return after snapshot capture")


def capture() -> dict[str, object]:
    _require_root_linux()
    deployment_uid = DEPLOYMENT_USER_HOME.stat().st_uid
    _safe_regular(SSH_IDENTITY, mode=0o600, owner=deployment_uid)
    _safe_regular(SSH_KNOWN_HOSTS, mode=0o644, owner=deployment_uid)
    _prepare_private_root()
    _remote("true", timeout=15)

    temporary = PRIVATE_ROOT / f".identity.db.new.{secrets.token_hex(16)}"
    stopped = False
    capture_error: BaseException | None = None
    try:
        # Once the stop command is attempted, always issue a matching start in
        # ``finally``.  SSH can lose the response after HA has already stopped.
        stopped = True
        _remote("ha", "core", "stop", timeout=90)
        size, remote_digest = _require_remote_stopped_database()
        _copy_to_temporary(temporary)
        if _remote_sha256() != remote_digest:
            raise SnapshotCaptureError(
                "stopped identity database changed during transfer"
            )
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        details = temporary.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
            or details.st_size != size
        ):
            raise SnapshotCaptureError("private snapshot file is unsafe")
        if _local_sha256(temporary) != remote_digest:
            raise SnapshotCaptureError("private snapshot digest mismatch")
        _verify_sqlite(temporary)
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, SNAPSHOT_PATH)
        directory = os.open(PRIVATE_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException as error:
        capture_error = error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if stopped:
            try:
                _start_home_assistant()
                _wait_home_assistant_ready()
            except BaseException as start_error:
                if capture_error is None:
                    capture_error = start_error
                else:
                    capture_error = SnapshotCaptureError(
                        "snapshot failed and Home Assistant restart was not verified"
                    )
    if capture_error is not None:
        if isinstance(capture_error, SnapshotCaptureError):
            raise capture_error
        raise SnapshotCaptureError("private snapshot capture failed") from capture_error

    return {
        "contract": CONTRACT,
        "home_assistant_restarted": True,
        "private_snapshot_sha256": _local_sha256(SNAPSHOT_PATH),
        "snapshot_bytes": SNAPSHOT_PATH.stat().st_size,
        "status": "captured",
        "writer_freeze_installed": False,
    }


def main() -> int:
    if len(sys.argv) != 1:
        print("legacy identity snapshot capture accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = capture()
    except SnapshotCaptureError:
        print("legacy identity snapshot capture failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
