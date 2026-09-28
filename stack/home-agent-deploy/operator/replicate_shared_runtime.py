#!/usr/bin/python3 -Is
"""Replicate the shared-preference runtime material off-host.

The job is root-only, accepts no arguments and reads only its own root-owned
environment file. It snapshots every durable SQLite journal of the
shared-preference services with the SQLite online-backup API, checks each copy
with ``PRAGMA integrity_check``, adds the services' config/ and secrets/ trees
and the internal authority/ tree, and builds a deterministic tar whose first
member is a SHA-256 manifest. The tar is encrypted to a pinned recipient
certificate (``openssl cms``, AES-256-GCM); the decryption key never exists on
this host. The ciphertext is published as an immutable epoch, downloaded again
for an exact SHA-256 comparison, and committed by a ``complete.sha256`` marker
written last, mirroring the erasure-ledger replicator. Only then are older
epochs created by this tool pruned and the content-free receipt written.

Session stores, owner-lock sidecars and live -wal/-shm/-journal sidecars are
excluded by name; see SHARED-RUNTIME-BACKUP.md. Output and errors never contain
secret values or file contents.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


RESULT_CONTRACT = "home-agent-shared-runtime-backup-result-v1"
RECEIPT_CONTRACT = "home-agent-shared-runtime-backup-receipt-v1"
INSTALLED_PATH = Path("/usr/local/libexec/home-agent/shared-runtime-backup.py")
ENV_PATH = Path("/srv/home-agent/config/shared-runtime-backup.env")
OPERATOR_MANIFEST = Path("/srv/home-agent/config/shared-runtime-backup-operator.sha256")
WORK_ROOT = Path("/srv/home-agent/shared-runtime-backup")
RECEIPT_NAME = "receipt.json"
LOCK_PATH = Path("/run/lock/home-agent-shared-runtime-backup.lock")
SOURCE_PARENT = PurePosixPath("/srv/home-agent/shared-preferences")
RCLONE_PARENT = PurePosixPath("/srv/home-agent/secrets/shared-runtime-rclone")
RECIPIENT_PARENT = PurePosixPath("/srv/home-agent/config")
TRUSTED_PARENTS = (
    Path("/usr"),
    Path("/usr/local"),
    Path("/usr/local/libexec"),
    Path("/usr/local/libexec/home-agent"),
    Path("/srv/home-agent"),
    Path("/srv/home-agent/config"),
)
TRUSTED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
CHILD_ENV: Mapping[str, str] | None = {"PATH": TRUSTED_PATH, "LC_ALL": "C", "HOME": "/root"}

SERVICE_DIRS = (
    "echo-preferences",
    "victoria-preferences",
    "echo-identity",
    "victoria-identity",
    "link-coordinator",
    "echo-bff",
    "victoria-bff",
)
SERVICE_SUBDIRS = ("config", "secrets", "journals")
AUTHORITY_DIR = "authority"

DEFAULT_PREFIX = "HomeAgent/SharedRuntime"
RESERVED_PREFIXES = ("HomeAgent/pgBackRest",)
ARCHIVE_NAME = "shared-runtime.tar.cms"
MARKER_NAME = "complete.sha256"
MANIFEST_MEMBER = "SHARED-RUNTIME-MANIFEST.sha256"
EPOCH = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAPPER = re.compile(r"^/dev/mapper/[A-Za-z0-9._-]+$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  ([^\0\n]+)$")

# Journals are discovered by content, not configuration. The session store is
# deliberately excluded like HOME_AGENT_SESSION_ROOT; these exact names are the
# only ones treated as sessions, so a renamed journal is never skipped.
SESSION_STORE_NAMES = frozenset({"sessions.sqlite", "session.sqlite", "sessions.db", "session.db"})
OWNER_LOCK_SUFFIX = ".owner.sqlite"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
DATABASE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SQLITE_HEADER = b"SQLite format 3\x00"

KEEP_DEFAULT = 30
KEEP_RANGE = (7, 366)
MAX_ENV_BYTES = 16 * 1024
MAX_SMALL_BYTES = 64 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
SNAPSHOT_ATTEMPTS = 5
SQLITE_TIMEOUT_MS = 2000
COMMAND_TIMEOUT_SECONDS = 300
UPLOAD_TIMEOUT_SECONDS = 1800
ENV_KEYS = frozenset(
    {
        "HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT",
        "HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG",
        "HOME_AGENT_SHARED_RUNTIME_REMOTE",
        "HOME_AGENT_SHARED_RUNTIME_PREFIX",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256",
        "HOME_AGENT_SHARED_RUNTIME_KEEP",
        "HOME_AGENT_EXPECTED_MAPPER",
    }
)


class BackupError(RuntimeError):
    """The backup could not be proven; the message never carries secrets."""


class BackupBusy(BackupError):
    """Another run holds the process lock."""


@dataclass(frozen=True)
class Config:
    source_root: Path
    rclone_config: Path
    remote: str
    prefix: str
    recipient_cert: Path
    recipient_sha256: str
    keep: int
    expected_mapper: str


@dataclass(frozen=True)
class Tools:
    openssl: str
    rclone: str
    findmnt: str


@dataclass(frozen=True)
class Member:
    name: str
    source: Path
    kind: str  # "dir", "file" or "database"
    mode: int
    uid: int
    gid: int


@dataclass(frozen=True)
class Plan:
    members: tuple[Member, ...]
    excluded: tuple[tuple[str, str], ...]
    ignored_top_level: int

    @property
    def databases(self) -> tuple[Member, ...]:
        return tuple(member for member in self.members if member.kind == "database")


# --------------------------------------------------------------------------
# Process helpers


def _run(
    command: Sequence[str],
    *,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
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
            env=None if CHILD_ENV is None else dict(CHILD_ENV),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise BackupError(f"{Path(command[0]).name} failed") from error
    if result.returncode not in accepted_codes:
        raise BackupError(f"{Path(command[0]).name} failed")
    return result.stdout


def _download_digest(tools: Tools, config: Config, remote_path: str, *, maximum: int) -> tuple[str, int, bytes]:
    """Stream a remote object back; return its SHA-256, size and (if small) bytes."""
    command = [
        tools.rclone, "cat", "--config", str(config.rclone_config),
        "--contimeout", "15s", "--timeout", "5m", remote_path,
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env=None if CHILD_ENV is None else dict(CHILD_ENV),
        )
    except OSError as error:
        raise BackupError("remote verification failed") from error
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    assert process.stdout is not None
    try:
        while block := process.stdout.read(1024 * 1024):
            size += len(block)
            if size > maximum:
                raise BackupError("remote verification size mismatch")
            digest.update(block)
            if len(head) < MAX_SMALL_BYTES:
                head.extend(block[: MAX_SMALL_BYTES - len(head)])
        if process.wait(timeout=60) != 0:
            raise BackupError("remote verification failed")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    return digest.hexdigest(), size, bytes(head)


def resolve_tools() -> Tools:
    found: dict[str, str] = {}
    for name in ("openssl", "rclone", "findmnt"):
        located = shutil.which(name, path=TRUSTED_PATH)
        if located is None:
            raise BackupError(f"missing command: {name}")
        resolved = Path(os.path.realpath(located))
        details = resolved.stat()
        if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
            raise BackupError(f"untrusted command: {name}")
        found[name] = str(resolved)
    return Tools(**found)


# --------------------------------------------------------------------------
# Trusted installation and configuration


def _nofollow() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _read_small(path: Path, *, maximum: int = MAX_SMALL_BYTES) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | _nofollow())
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise BackupError(f"{path.name} is too large")
    return raw


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | _nofollow())
    with os.fdopen(descriptor, "rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _require_file(path: Path, *, mode: int | None, what: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise BackupError(f"{what} is missing") from error
    unsafe_mode = (
        stat.S_IMODE(details.st_mode) != mode
        if mode is not None
        else bool(stat.S_IMODE(details.st_mode) & 0o022)
    )
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or details.st_nlink != 1
        or unsafe_mode
    ):
        raise BackupError(f"{what} has an unsafe owner, mode or link count")


def _require_directory(path: Path, *, mode: int | None, what: str) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise BackupError(f"{what} is missing") from error
    unsafe_mode = (
        stat.S_IMODE(details.st_mode) != mode
        if mode is not None
        else bool(stat.S_IMODE(details.st_mode) & 0o022)
    )
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or unsafe_mode:
        raise BackupError(f"{what} has an unsafe owner or mode")


def require_trusted_install() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BackupError("requires root on Linux")
    for parent in TRUSTED_PARENTS:
        _require_directory(parent, mode=None, what=f"trusted parent {parent}")
    if Path(os.path.realpath(__file__)) != INSTALLED_PATH:
        raise BackupError("run the root-installed operator copy")
    _require_file(INSTALLED_PATH, mode=0o555, what="installed operator")


def _absolute(value: str, *, under: PurePosixPath, what: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value.startswith("/")
        or str(path) != value
        or ".." in path.parts
        or not path.is_relative_to(under)
        or path == under
    ):
        raise BackupError(f"{what} is not an approved path")
    return path


def validate_prefix(value: str) -> str:
    parts = value.split("/")
    if not parts or not all(SEGMENT.fullmatch(part) for part in parts):
        raise BackupError("remote prefix is invalid")
    if "erasure-ledger" in (part.lower() for part in parts):
        raise BackupError("remote prefix overlaps the erasure-ledger replica")
    for reserved in RESERVED_PREFIXES:
        folded, other = value.lower() + "/", reserved.lower() + "/"
        if folded.startswith(other) or other.startswith(folded):
            raise BackupError("remote prefix overlaps the pgBackRest off-host copy")
    return value


def parse_env(raw: bytes) -> Config:
    if not raw or len(raw) > MAX_ENV_BYTES or b"\0" in raw:
        raise BackupError("environment file is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BackupError("environment file is invalid") from error
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or key not in ENV_KEYS or key in values:
            raise BackupError("environment file has an unknown or duplicate key")
        if not value or any(character in value for character in "\"'`$\\ \t"):
            raise BackupError(f"environment value is invalid: {key}")
        values[key] = value
    for required in (
        "HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT",
        "HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG",
        "HOME_AGENT_SHARED_RUNTIME_REMOTE",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256",
    ):
        if required not in values:
            raise BackupError(f"environment is missing {required}")

    source = _absolute(values["HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT"], under=SOURCE_PARENT, what="source root")
    if source.parent != SOURCE_PARENT:
        raise BackupError("source root must be a direct child of the shared-preferences root")
    rclone_config = _absolute(values["HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG"], under=RCLONE_PARENT, what="rclone configuration")
    recipient = _absolute(values["HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT"], under=RECIPIENT_PARENT, what="recipient certificate")
    remote = values["HOME_AGENT_SHARED_RUNTIME_REMOTE"]
    if not REMOTE_NAME.fullmatch(remote):
        raise BackupError("remote name is invalid")
    fingerprint = values["HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256"]
    if not HEX64.fullmatch(fingerprint):
        raise BackupError("recipient fingerprint is invalid")
    keep_raw = values.get("HOME_AGENT_SHARED_RUNTIME_KEEP", str(KEEP_DEFAULT))
    if not keep_raw.isdigit() or not KEEP_RANGE[0] <= int(keep_raw) <= KEEP_RANGE[1]:
        raise BackupError("retention count is out of range")
    mapper = values.get("HOME_AGENT_EXPECTED_MAPPER", "/dev/mapper/home-agent")
    if not MAPPER.fullmatch(mapper):
        raise BackupError("expected mapper is invalid")
    return Config(
        source_root=Path(str(source)),
        rclone_config=Path(str(rclone_config)),
        remote=remote,
        prefix=validate_prefix(values.get("HOME_AGENT_SHARED_RUNTIME_PREFIX", DEFAULT_PREFIX)),
        recipient_cert=Path(str(recipient)),
        recipient_sha256=fingerprint,
        keep=int(keep_raw),
        expected_mapper=mapper,
    )


def load_recipient(path: Path, fingerprint: str) -> None:
    """Accept exactly one public certificate whose DER SHA-256 is pinned."""
    raw = _read_small(path)
    if b"PRIVATE KEY" in raw:
        raise BackupError("recipient file must not contain a private key")
    blocks = re.findall(
        rb"-----BEGIN CERTIFICATE-----\r?\n([A-Za-z0-9+/=\r\n]+?)-----END CERTIFICATE-----",
        raw,
    )
    if len(blocks) != 1 or raw.count(b"-----BEGIN ") != 1:
        raise BackupError("recipient file must hold exactly one certificate")
    try:
        der = base64.b64decode(re.sub(rb"\s+", b"", blocks[0]), validate=True)
    except ValueError as error:
        raise BackupError("recipient certificate is invalid") from error
    if hashlib.sha256(der).hexdigest() != fingerprint:
        raise BackupError("recipient certificate does not match its pinned fingerprint")


def verify_operator_manifest(manifest: Path, required: Iterable[Path], *, root: Path = Path("/")) -> None:
    raw = _read_small(manifest)
    entries: dict[str, str] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise BackupError("operator manifest is invalid") from error
    for line in lines:
        match = MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise BackupError("operator manifest is invalid")
        relative = PurePosixPath(match.group(2))
        if relative.is_absolute() or ".." in relative.parts or str(relative) in entries:
            raise BackupError("operator manifest is invalid")
        entries[str(relative)] = match.group(1)
    for path in required:
        if PurePosixPath(path.as_posix()).relative_to("/").as_posix() not in entries:
            raise BackupError(f"operator manifest does not cover {path.name}")
    for relative, expected in entries.items():
        try:
            actual, _ = _sha256_file(root / relative)
        except OSError as error:
            raise BackupError("operator manifest entry is unreadable") from error
        if actual != expected:
            raise BackupError("installed operator digest mismatch")


def require_mapper(tools: Tools, path: Path, expected: str) -> None:
    source = _run([tools.findmnt, "-n", "-o", "SOURCE", "-T", str(path)], timeout=30)
    value = source.decode("utf-8", "replace").strip()
    if value != expected and not value.startswith(expected + "["):
        raise BackupError(f"{path} is not on the expected encrypted mapper")


# --------------------------------------------------------------------------
# Coverage


def classify_journal_file(name: str, head: bytes) -> str:
    """Return database, session-store, owner-lock or sidecar; fail otherwise."""
    base = name
    sidecar = False
    for suffix in SIDECAR_SUFFIXES:
        if name.endswith(suffix):
            base, sidecar = name[: -len(suffix)], True
            break
    owner = base.endswith(OWNER_LOCK_SUFFIX)
    database_name = base[: -len(OWNER_LOCK_SUFFIX)] if owner else base
    if database_name in SESSION_STORE_NAMES:
        return "session-store"
    if owner:
        return "owner-lock"
    if sidecar:
        return "sidecar"
    if name.endswith(DATABASE_SUFFIXES) or head.startswith(SQLITE_HEADER):
        return "database"
    raise BackupError(f"unclassified journal file: {name}")


def _head(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | _nofollow())
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(len(SQLITE_HEADER))


def _member(name: str, source: Path, kind: str, details: os.stat_result) -> Member:
    return Member(name, source, kind, stat.S_IMODE(details.st_mode), details.st_uid, details.st_gid)


def _walk(
    root: Path,
    name: str,
    *,
    journals: bool,
    members: list[Member],
    excluded: list[tuple[str, str]],
) -> None:
    details = root.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise BackupError(f"covered directory is not a real directory: {name}")
    members.append(_member(name, root, "dir", details))
    with os.scandir(root) as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name)
    for entry in entries:
        child = f"{name}/{entry.name}"
        path = Path(entry.path)
        child_details = path.lstat()
        if stat.S_ISLNK(child_details.st_mode):
            raise BackupError(f"symbolic link in covered tree: {child}")
        if stat.S_ISDIR(child_details.st_mode):
            _walk(path, child, journals=journals, members=members, excluded=excluded)
            continue
        if not stat.S_ISREG(child_details.st_mode):
            raise BackupError(f"special file in covered tree: {child}")
        if child_details.st_size > MAX_MEMBER_BYTES:
            raise BackupError(f"covered file is too large: {child}")
        head = _head(path)
        if journals:
            kind = classify_journal_file(entry.name, head)
            if kind == "database":
                members.append(_member(child, path, "database", child_details))
            else:
                excluded.append((child, kind))
            continue
        if entry.name.endswith(DATABASE_SUFFIXES) or head.startswith(SQLITE_HEADER):
            raise BackupError(f"live database outside a journals directory: {child}")
        members.append(_member(child, path, "file", child_details))


def collect(source_root: Path) -> Plan:
    members: list[Member] = []
    excluded: list[tuple[str, str]] = []
    for service in SERVICE_DIRS:
        service_root = source_root / service
        try:
            details = service_root.lstat()
        except FileNotFoundError as error:
            raise BackupError(f"service directory is missing: {service}") from error
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise BackupError(f"service directory is unsafe: {service}")
        unexpected = sorted(set(os.listdir(service_root)) - set(SERVICE_SUBDIRS))
        if unexpected:
            raise BackupError(f"unexpected entry in service directory: {service}/{unexpected[0]}")
        members.append(_member(service, service_root, "dir", details))
        for subdir in SERVICE_SUBDIRS:
            path = service_root / subdir
            if not os.path.lexists(path):
                raise BackupError(f"service directory is missing: {service}/{subdir}")
            _walk(path, f"{service}/{subdir}", journals=subdir == "journals", members=members, excluded=excluded)
    if not os.path.lexists(source_root / AUTHORITY_DIR):
        raise BackupError("authority directory is missing")
    _walk(source_root / AUTHORITY_DIR, AUTHORITY_DIR, journals=False, members=members, excluded=excluded)
    covered = set(SERVICE_DIRS) | {AUTHORITY_DIR}
    ignored = len(set(os.listdir(source_root)) - covered)
    return Plan(tuple(sorted(members, key=lambda member: member.name)), tuple(excluded), ignored)


# --------------------------------------------------------------------------
# Snapshot and archive


def integrity_check(path: Path) -> list[tuple]:
    checker = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return checker.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        return []
    finally:
        checker.close()


def snapshot_database(
    tools: Tools,
    source: Path,
    destination: Path,
    *,
    label: str,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Copy one live journal with the online-backup API and verify the copy.

    Uses the standard-library SQLite binding (the LA host has no sqlite3 CLI).
    The source is opened read-only through a URI; a live file is never copied
    raw. When root must create a -wal/-shm sidecar, SQLite assigns it the
    database owner.
    """
    del tools  # snapshotting needs no external command
    for attempt in range(SNAPSHOT_ATTEMPTS):
        for stale in (destination, *(Path(f"{destination}{suffix}") for suffix in SIDECAR_SUFFIXES)):
            if os.path.lexists(stale):
                stale.unlink()
        reader = sqlite3.connect(
            f"{source.as_uri()}?mode=ro", uri=True, timeout=SQLITE_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            # Take the shared read lock first, bounded by the busy timeout.
            # Connection.backup() alone would retry a busy source forever; with
            # our own read transaction held it copies one consistent state.
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM sqlite_master").fetchone()
            writer = sqlite3.connect(str(destination))
            try:
                reader.backup(writer)
                # A WAL-mode source yields a WAL-mode copy; make the archived
                # snapshot a self-contained single file.
                writer.execute("PRAGMA journal_mode=DELETE").fetchone()
            finally:
                writer.close()
            reader.execute("ROLLBACK")
            break
        except sqlite3.Error:
            if attempt + 1 == SNAPSHOT_ATTEMPTS:
                raise BackupError(f"journal snapshot failed: {label}") from None
            sleep(1.0 + attempt)
        finally:
            reader.close()
    if integrity_check(destination) != [("ok",)]:
        raise BackupError(f"journal snapshot failed integrity_check: {label}")
    if any(os.path.lexists(f"{destination}{suffix}") for suffix in SIDECAR_SUFFIXES):
        raise BackupError(f"journal snapshot left a sidecar: {label}")


def require_sidecar_ownership(databases: Iterable[Member]) -> None:
    """Fail if any sidecar next to a live journal is not owned like the journal."""
    for member in databases:
        details = member.source.lstat()
        for suffix in SIDECAR_SUFFIXES:
            sidecar = Path(f"{member.source}{suffix}")
            try:
                sidecar_details = sidecar.lstat()
            except FileNotFoundError:
                continue
            if (sidecar_details.st_uid, sidecar_details.st_gid) != (details.st_uid, details.st_gid):
                raise BackupError(f"journal sidecar ownership differs: {member.name}{suffix}")


def _tarinfo(name: str, *, kind: str, size: int, mode: int, uid: int, gid: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if kind == "dir" else tarfile.REGTYPE
    info.size = 0 if kind == "dir" else size
    info.mode = mode
    info.uid, info.gid = uid, gid
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def build_archive(plan: Plan, snapshots: Mapping[str, Path], output: Path) -> tuple[str, int]:
    """Write a deterministic tar; return its SHA-256 and manifest entry count.

    Members are sorted, times are zero and names carry no owner strings; modes
    and numeric owners are kept so a restore reinstates service permissions.
    """
    contents: dict[str, tuple[Path, str, int]] = {}
    for member in plan.members:
        if member.kind == "dir":
            continue
        path = snapshots[member.name] if member.kind == "database" else member.source
        digest, size = _sha256_file(path)
        contents[member.name] = (path, digest, size)
    manifest = "".join(f"{digest}  {name}\n" for name, (_, digest, _) in sorted(contents.items())).encode("utf-8")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow(), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
            archive.addfile(
                _tarinfo(MANIFEST_MEMBER, kind="file", size=len(manifest), mode=0o400, uid=0, gid=0),
                io.BytesIO(manifest),
            )
            for member in plan.members:
                if member.kind == "dir":
                    archive.addfile(_tarinfo(member.name, kind="dir", size=0, mode=member.mode, uid=member.uid, gid=member.gid))
                    continue
                path, digest, size = contents[member.name]
                with path.open("rb") as source:
                    data = source.read(size + 1)
                if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                    raise BackupError(f"covered file changed during the snapshot: {member.name}")
                archive.addfile(
                    _tarinfo(member.name, kind="file", size=size, mode=member.mode, uid=member.uid, gid=member.gid),
                    io.BytesIO(data),
                )
        stream.flush()
        os.fsync(stream.fileno())
    digest, _ = _sha256_file(output)
    return digest, len(contents)


def encrypt_archive(tools: Tools, archive: Path, recipient: Path, output: Path) -> None:
    _run(
        [
            tools.openssl, "cms", "-encrypt", "-binary", "-aes-256-gcm",
            "-outform", "DER", "-in", str(archive), "-out", str(output), str(recipient),
        ],
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    _run(
        [tools.openssl, "cms", "-cmsout", "-inform", "DER", "-in", str(output), "-noout"],
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if output.stat().st_size <= archive.stat().st_size:
        raise BackupError("encrypted archive is implausibly small")


# --------------------------------------------------------------------------
# Remote publication


def _rclone(tools: Tools, config: Config, *arguments: str, timeout: int = COMMAND_TIMEOUT_SECONDS,
            accepted_codes: frozenset[int] = frozenset({0})) -> bytes:
    return _run(
        [
            tools.rclone, *arguments[:1], "--config", str(config.rclone_config),
            "--contimeout", "15s", "--timeout", "5m", "--retries", "2", "--low-level-retries", "2",
            *arguments[1:],
        ],
        timeout=timeout,
        accepted_codes=accepted_codes,
    )


def epochs_root(config: Config) -> str:
    return f"{config.remote}:{config.prefix}/epochs"


def list_epochs(tools: Tools, config: Config) -> list[str]:
    # Exit 3 is rclone's "directory not found": the first run has no epochs.
    raw = _rclone(tools, config, "lsf", "--dirs-only", epochs_root(config), accepted_codes=frozenset({0, 3}))
    return sorted(line.rstrip("/") for line in raw.decode("utf-8", "replace").splitlines() if line.strip())


def publish_epoch(tools: Tools, config: Config, epoch: str, encrypted: Path, stage: Path) -> tuple[str, int]:
    if not EPOCH.fullmatch(epoch):
        raise BackupError("epoch id is invalid")
    existing = [name for name in list_epochs(tools, config) if EPOCH.fullmatch(name)]
    if epoch in existing:
        raise BackupError("epoch already exists; immutable epochs are never overwritten")
    if existing and max(existing) > epoch:
        raise BackupError("a newer epoch already exists; check the host clock")
    local_digest, local_size = _sha256_file(encrypted)
    remote = f"{epochs_root(config)}/{epoch}"
    _rclone(tools, config, "copyto", "--immutable", str(encrypted), f"{remote}/{ARCHIVE_NAME}",
            timeout=UPLOAD_TIMEOUT_SECONDS)
    remote_digest, remote_size, _ = _download_digest(tools, config, f"{remote}/{ARCHIVE_NAME}", maximum=local_size)
    if (remote_digest, remote_size) != (local_digest, local_size):
        raise BackupError("remote archive verification failed")
    marker = stage / MARKER_NAME
    marker_bytes = f"{local_digest}  {ARCHIVE_NAME}\n".encode("ascii")
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow(), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(marker_bytes)
    # The commit marker is written last; an epoch without it is incomplete.
    _rclone(tools, config, "copyto", "--immutable", str(marker), f"{remote}/{MARKER_NAME}")
    _, _, downloaded = _download_digest(tools, config, f"{remote}/{MARKER_NAME}", maximum=len(marker_bytes))
    if downloaded != marker_bytes:
        raise BackupError("remote commit-marker verification failed")
    return local_digest, local_size


def expired_epochs(names: Iterable[str], *, keep: int, current: str) -> list[str]:
    ours = sorted(name for name in names if EPOCH.fullmatch(name))
    if current not in ours or ours[-1] != current:
        raise BackupError("current epoch is not the newest remote epoch")
    return ours[:-keep] if len(ours) > keep else []


def apply_retention(tools: Tools, config: Config, current: str) -> tuple[int, int]:
    names = list_epochs(tools, config)
    expired = expired_epochs(names, keep=config.keep, current=current)
    for name in expired:
        _rclone(tools, config, "purge", f"{epochs_root(config)}/{name}")
    return len([name for name in names if EPOCH.fullmatch(name)]) - len(expired), len(expired)


# --------------------------------------------------------------------------
# Receipt and orchestration


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_receipt(
    *,
    epoch: str,
    created: datetime,
    config: Config,
    plan: Plan,
    manifest_entries: int,
    archive_sha256: str,
    encrypted_sha256: str,
    encrypted_bytes: int,
    retained: int,
    deleted: int,
) -> dict[str, Any]:
    return {
        "contract": RECEIPT_CONTRACT,
        "epoch": epoch,
        "created_at": utc_text(created),
        "member_count": manifest_entries,
        "database_count": len(plan.databases),
        "integrity_check": "ok",
        "excluded_count": len(plan.excluded),
        "ignored_top_level_count": plan.ignored_top_level,
        "archive_sha256": archive_sha256,
        "encrypted_sha256": encrypted_sha256,
        "encrypted_bytes": encrypted_bytes,
        "encryption": "cms-aes-256-gcm",
        "recipient_sha256": config.recipient_sha256,
        "remote_object": f"{config.prefix}/epochs/{epoch}/{ARCHIVE_NAME}",
        "remote_verification": "downloaded_sha256_match",
        "retained_epochs": retained,
        "deleted_epochs": deleted,
    }


def write_receipt(directory: Path, value: Mapping[str, Any]) -> Path:
    target = directory / RECEIPT_NAME
    if os.path.lexists(target):
        _require_file(target, mode=0o600, what="existing receipt")
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    temporary = directory / f".{RECEIPT_NAME}.new.{secrets.token_hex(12)}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _nofollow(), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
    if hasattr(os, "O_DIRECTORY"):
        directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    return target


def _acquire_lock() -> int:
    import fcntl

    descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | _nofollow(), 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise BackupBusy("another shared-runtime backup is running") from None
    return descriptor


def remove_stale_stages(work_root: Path) -> None:
    # A killed run can leave plaintext copies; they are only ever on the
    # encrypted mapper and are removed under the process lock.
    for entry in os.scandir(work_root):
        if entry.name.startswith(".stage-") and entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)


def backup_once(tools: Tools, config: Config, *, work_root: Path, now: datetime) -> dict[str, Any]:
    load_recipient(config.recipient_cert, config.recipient_sha256)
    epoch = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = collect(config.source_root)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=work_root))
    try:
        os.chmod(stage, 0o700)
        snapshots: dict[str, Path] = {}
        for index, member in enumerate(plan.databases, start=1):
            destination = stage / f"journal-{index:04d}.sqlite"
            snapshot_database(tools, member.source, destination, label=member.name)
            snapshots[member.name] = destination
        require_sidecar_ownership(plan.databases)
        archive = stage / "shared-runtime.tar"
        archive_sha256, entries = build_archive(plan, snapshots, archive)
        for snapshot in snapshots.values():
            snapshot.unlink()
        encrypted = stage / ARCHIVE_NAME
        try:
            encrypt_archive(tools, archive, config.recipient_cert, encrypted)
        finally:
            archive.unlink()
        encrypted_sha256, encrypted_bytes = publish_epoch(tools, config, epoch, encrypted, stage)
        retained, deleted = apply_retention(tools, config, epoch)
        receipt = build_receipt(
            epoch=epoch,
            created=now,
            config=config,
            plan=plan,
            manifest_entries=entries,
            archive_sha256=archive_sha256,
            encrypted_sha256=encrypted_sha256,
            encrypted_bytes=encrypted_bytes,
            retained=retained,
            deleted=deleted,
        )
        write_receipt(work_root, receipt)
        return receipt
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run() -> dict[str, Any]:
    require_trusted_install()
    _require_file(ENV_PATH, mode=0o600, what="environment file")
    config = parse_env(_read_small(ENV_PATH, maximum=MAX_ENV_BYTES))
    _require_file(OPERATOR_MANIFEST, mode=0o600, what="operator manifest")
    _require_file(config.recipient_cert, mode=None, what="recipient certificate")
    verify_operator_manifest(OPERATOR_MANIFEST, (INSTALLED_PATH, ENV_PATH, config.recipient_cert))
    tools = resolve_tools()
    _require_file(config.rclone_config, mode=0o600, what="rclone configuration")
    _require_directory(config.source_root, mode=0o700, what="source root")
    _require_directory(WORK_ROOT, mode=0o700, what="work root")
    for path in (ENV_PATH, config.source_root, config.rclone_config, WORK_ROOT):
        require_mapper(tools, path, config.expected_mapper)
    lock = _acquire_lock()
    try:
        remove_stale_stages(WORK_ROOT)
        receipt = backup_once(tools, config, work_root=WORK_ROOT, now=datetime.now(timezone.utc))
    finally:
        os.close(lock)
    return {
        "contract": RESULT_CONTRACT,
        "epoch": receipt["epoch"],
        "member_count": receipt["member_count"],
        "status": "verified",
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments:
        print("shared-runtime backup accepts no arguments", file=sys.stderr)
        return 64
    try:
        result = run()
    except BackupBusy as error:
        print(f"shared-runtime backup: {error}", file=sys.stderr)
        return 75
    except BackupError as error:
        print(f"shared-runtime backup failed: {error}", file=sys.stderr)
        return 78
    except OSError as error:
        print(f"shared-runtime backup failed: {type(error).__name__}", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
