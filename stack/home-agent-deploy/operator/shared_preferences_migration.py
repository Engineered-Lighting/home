#!/usr/bin/env python3
"""Migrate live Core exactly from schema 0031 to 0047 for shared preferences.

`check` only evaluates the admission gates. `run` evaluates the same gates,
stops the three Core roles (worker first), runs the image's fixed
`phase3-migrate-personal-preferences` entrypoint once through the bounded
`migrate` service, and starts only those three roles on the reviewed 0047
runtime. A migration that exits non-zero rolled back its single Alembic
transaction; the source revision is then verified before the reviewed 0031
runtime is restarted. After a committed migration the 0031 runtime is never
started again by this tool.

The tool never builds or pulls an image, never starts dependencies, never runs
grant or provisioning services, and never restores a database. Output and the
root-only receipt carry categorical codes only: no secrets and no environment.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


CONTRACT = "shared-preferences-migration-0031-0047-v1"
SOURCE_REVISION = "0031_relationship_uniqueness_e5r"
TARGET_REVISION = "0047_personal_pref_authority_v1"
ENTRYPOINT = "phase3-migrate-personal-preferences"
TRUSTED_INSTALL = Path(
    "/usr/local/libexec/home-agent/shared-preferences/shared_preferences_migration.py"
)
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
PROJECT = "home-agent"
CONFIG_ROOT = Path("/srv/home-agent/config")
OFF_HOST_RECEIPT_PATH = CONFIG_ROOT / "phase3-off-host-backup-e5j.json"
OFF_HOST_CONTRACT = "phase3-off-host-backup-receipt-e5j-v1"
MAINTENANCE_RESTORE_PATH = CONFIG_ROOT / "maintenance-restore-drill.json"
MAINTENANCE_CONTRACT = "home-agent-maintenance-restore-receipt-v1"
LOCK_PATH = Path("/srv/home-agent/locks/phase3-activation.lock")
CORE_SERVICES = ("core-worker", "core-ingest", "core-api")
CORE_CONTAINERS = tuple(f"{PROJECT}-{service}-1" for service in CORE_SERVICES)
API_CONTAINER = f"{PROJECT}-core-api-1"
POSTGRES_CONTAINER = f"{PROJECT}-postgres-1"
REQUIRED_HEALTHY = (*CORE_CONTAINERS, POSTGRES_CONTAINER)
OFF_HOST_MAX_AGE = timedelta(hours=2)
CLOCK_SKEW = timedelta(minutes=1)
READY_TIMEOUT_SECONDS = 180
READY_POLL_SECONDS = 5
MIGRATE_TIMEOUT_SECONDS = 1800
MAX_JSON_BYTES = 64 * 1024
DIAGNOSTIC_TAIL_BYTES = 4 * 1024
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
BACKUP_LABEL = re.compile(r"^[0-9]{8}-[0-9]{6}F$")
SYSTEM_IDENTIFIER = re.compile(r"^[1-9][0-9]{9,19}$")
SUMMARY_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_CREDENTIAL_USERINFO = re.compile(rb"://[^@\s]*@")
READYZ_FIELDS = (
    "ready",
    "migration",
    "expected_migration",
    "restore_gate",
    "rollout_authorization",
    "worker_maintenance",
    "outbox",
)
DATABASE_URL_SECRET = "/run/secrets/database_url"
# Same fixed loader as phase3_migration_executor: the image entrypoint has no
# verify-only role, so load only the one secret under its rules and verify.
REVISION_GUARD_SCRIPT = (
    "set -eu\n"
    "secret_file=$1\n"
    "revision=$2\n"
    '[ -z "${HOME_AGENT_DATABASE_URL:-}" ] || exit 78\n'
    '[ -f "$secret_file" ] && [ -r "$secret_file" ] || exit 78\n'
    'secret_value=$(cat -- "$secret_file")\n'
    '[ -n "$secret_value" ] || exit 78\n'
    "case $secret_value in *[[:space:]]*) exit 78 ;; esac\n"
    "HOME_AGENT_DATABASE_URL=$secret_value\n"
    "export HOME_AGENT_DATABASE_URL\n"
    'exec python -m app.migration_guard "$revision"\n'
)
READYZ_PROBE = r"""
import json
from urllib.error import HTTPError
from urllib.request import urlopen

try:
    with urlopen("http://127.0.0.1:8104/readyz", timeout=5) as response:
        body = json.load(response)
except HTTPError as error:
    if error.code != 503:
        raise
    body = json.load(error)
maintenance = body.get("worker_maintenance")
print(json.dumps({
    "ready": body.get("ready"),
    "migration": body.get("migration"),
    "expected_migration": body.get("expected_migration"),
    "restore_gate": body.get("restore_gate"),
    "rollout_authorization": body.get("rollout_authorization"),
    "worker_maintenance": (
        maintenance.get("status") if isinstance(maintenance, dict) else None
    ),
    "outbox": body.get("outbox"),
}, sort_keys=True))
""".strip()
UNREADY_GUIDANCE = (
    "Schema 0047 is committed but Core did not become ready. Do not start the "
    "0031 image and do not restore the database. Fix forward with a compatible "
    "0047 Core, or apply the owner-approved contingency: a reviewed "
    "`alembic downgrade 0031_relationship_uniqueness_e5r`, only before source "
    "registration or CONNECT grants."
)
UNVERIFIED_GUIDANCE = (
    "The migration result could not be verified. Core roles remain stopped. "
    "Do not start either runtime or restore the database; verify the live "
    "revision under the reviewed maintenance procedure first."
)


class MigrationError(RuntimeError):
    """A trusted input or boundary could not be established."""


class StepTimeout(MigrationError):
    """A mutation step outlived its bound; its effect is unknown."""


@dataclass(frozen=True, slots=True)
class Inputs:
    env: Path
    compose_file: Path
    project_directory: Path
    pool_limits: Path
    runtime_override: Path
    rollback_override: Path
    core_image: str
    rollback_image: str
    receipt_dir: Path
    run_migrations: str | None


def _is_root() -> bool:
    return sys.platform == "linux" and getattr(os, "geteuid", lambda: -1)() == 0


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_trusted_execution() -> None:
    """Reject root execution from a mutable checkout or injected Python."""

    if sys.flags.isolated != 1:
        raise MigrationError("invoke the installed runner with python3 -I")
    source = Path(__file__)
    if source.is_symlink():
        raise MigrationError("installed runner may not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("installed runner is unavailable") from exc
    if resolved != TRUSTED_INSTALL:
        raise MigrationError("runner must run from its trusted installed path")
    _require_root_chain(resolved)


def sanitize_process_environment() -> None:
    """Children inherit only a fixed environment; nothing overrides the env file."""

    os.environ.clear()
    os.environ.update({"PATH": TRUSTED_PATH, "LANG": "C", "LC_ALL": "C", "HOME": "/root"})


def _require_root_chain(path: Path) -> None:
    current = path
    while True:
        metadata = current.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise MigrationError("operator path is not root-owned and immutable")
        if current == current.parent:
            return
        current = current.parent


def validate_root_path(path: str | Path, *, directory: bool = False) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise MigrationError("operator input must be an absolute non-symlink path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("operator input is unavailable") from exc
    if resolved != candidate:
        raise MigrationError("operator input path may not traverse symlinks")
    _require_root_chain(resolved)
    metadata = resolved.stat()
    if directory:
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise MigrationError("receipt directory must be root-only 0700")
    elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise MigrationError("operator input must be a regular file")
    return resolved


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MigrationError("deployment environment is unreadable") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise MigrationError("deployment environment is invalid")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in values:
            raise MigrationError("deployment environment is invalid")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def _exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MigrationError("operator JSON contains a duplicate key")
        value[key] = item
    return value


def decode_json(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_JSON_BYTES or b"\0" in raw:
        raise MigrationError("operator JSON has an invalid size")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_exact_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("operator JSON is invalid") from exc


def _protected_receipt(path: Path) -> Any:
    """Read a root:root 0600 receipt; absence is a gate result, not an error."""

    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise MigrationError("operator receipt has unsafe ownership or mode")
    return decode_json(path.read_bytes())


def _environment_value(service: Mapping[str, Any], name: str) -> Any:
    environment = service.get("environment")
    if isinstance(environment, Mapping):
        return environment.get(name)
    if isinstance(environment, list):
        matches = [
            item.split("=", 1)[1]
            for item in environment
            if isinstance(item, str) and item.startswith(f"{name}=")
        ]
        return matches[0] if len(matches) == 1 else None
    return None


def validate_runtime_override(value: Any, *, revision: str) -> str:
    """Return the one immutable image the reviewed Core runtime override pins."""

    services = value.get("services") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"services"}
        or not isinstance(services, Mapping)
        or set(services) != set(CORE_SERVICES)
    ):
        raise MigrationError("Core runtime override shape is not reviewed")
    images = set()
    for name in CORE_SERVICES:
        service = services[name]
        if (
            not isinstance(service, Mapping)
            or not isinstance(service.get("image"), str)
            or IMAGE_ID.fullmatch(service["image"]) is None
            or _environment_value(service, "HOME_AGENT_READINESS_MIGRATION") != revision
        ):
            raise MigrationError("Core runtime override does not pin the reviewed runtime")
        images.add(service["image"])
    if len(images) != 1:
        raise MigrationError("Core runtime override mixes images")
    return images.pop()


def validate_pool_limits(value: Any) -> None:
    services = value.get("services") if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"services"}
        or not isinstance(services, Mapping)
        or not services
        or not set(services) <= set(CORE_SERVICES)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"environment"}
            or not isinstance(item["environment"], Mapping)
            for item in services.values()
        )
    ):
        raise MigrationError("Core pool limits are not the reviewed shape")


def pin_compose_file(
    source: str | Path, expected_sha256: str, receipt_dir: Path
) -> tuple[Path, Path]:
    """Run Compose from a root-owned copy of the exact reviewed live file.

    The live deployment checkout is user-owned, so its path cannot be trusted
    for root execution. Verify the file's digest against the reviewed value
    and use a private copy; relative paths still resolve in the original
    project directory, and the Compose project name is unchanged.
    """

    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or "") is None:
        raise MigrationError("reviewed Compose digest must be a sha256 hex value")
    candidate = Path(source)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise MigrationError("Compose file must be an absolute non-symlink path")
    try:
        resolved = candidate.resolve(strict=True)
        raw = resolved.read_bytes()
    except OSError as exc:
        raise MigrationError("Compose file is unavailable") from exc
    if resolved != candidate or not stat.S_ISREG(resolved.stat().st_mode):
        raise MigrationError("Compose file must be a regular file without symlinks")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise MigrationError("Compose file differs from the reviewed digest")
    copy = receipt_dir / f"home-agent-compose.{expected_sha256[:16]}.yml"
    if copy.exists():
        if copy.is_symlink() or copy.read_bytes() != raw:
            raise MigrationError("existing pinned Compose copy differs")
    else:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0))
        descriptor = os.open(copy, flags, 0o400)
        try:
            os.fchown(descriptor, 0, 0)
            os.write(descriptor, raw)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return copy, resolved.parent


def load_inputs(args: argparse.Namespace) -> Inputs:
    if IMAGE_ID.fullmatch(args.core_image or "") is None:
        raise MigrationError("Core image must be an immutable sha256 image ID")
    env = validate_root_path(args.env)
    pool_limits = validate_root_path(args.pool_limits)
    runtime_override = validate_root_path(args.runtime_override)
    rollback_override = validate_root_path(args.rollback_override)
    receipt_dir = validate_root_path(args.receipt_dir, directory=True)
    compose_file, project_directory = pin_compose_file(
        args.compose_file, args.compose_sha256, receipt_dir
    )
    validate_pool_limits(decode_json(pool_limits.read_bytes()))
    target_image = validate_runtime_override(
        decode_json(runtime_override.read_bytes()), revision=TARGET_REVISION
    )
    if target_image != args.core_image:
        raise MigrationError("Core runtime override does not use the verified image")
    rollback_image = validate_runtime_override(
        decode_json(rollback_override.read_bytes()), revision=SOURCE_REVISION
    )
    return Inputs(
        env=env,
        compose_file=compose_file,
        project_directory=project_directory,
        pool_limits=pool_limits,
        runtime_override=runtime_override,
        rollback_override=rollback_override,
        core_image=args.core_image,
        rollback_image=rollback_image,
        receipt_dir=receipt_dir,
        run_migrations=read_env(env).get("HOME_AGENT_RUN_MIGRATIONS"),
    )


def _aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def latest_full_backup(info: Any) -> str | None:
    """Newest completed full backup of the one encrypted healthy stanza."""

    if not isinstance(info, list) or len(info) != 1 or not isinstance(info[0], Mapping):
        return None
    stanza = info[0]
    status = stanza.get("status")
    if (
        not isinstance(status, Mapping)
        or status.get("code") != 0
        or stanza.get("cipher") in {None, "none"}
    ):
        return None
    completed: list[tuple[int, str]] = []
    for backup in stanza.get("backup", []):
        if not isinstance(backup, Mapping):
            continue
        label = backup.get("label")
        timestamp = backup.get("timestamp")
        if (
            backup.get("type") == "full"
            and backup.get("error") is False
            and isinstance(label, str)
            and BACKUP_LABEL.fullmatch(label) is not None
            and isinstance(timestamp, Mapping)
            and isinstance(timestamp.get("stop"), int)
        ):
            completed.append((timestamp["stop"], label))
    return max(completed)[1] if completed else None


def valid_off_host_receipt(receipt: Any, *, latest_label: str | None, now: datetime) -> bool:
    if not isinstance(receipt, Mapping) or latest_label is None:
        return False
    copied_at = _aware_utc(receipt.get("copied_at"))
    return (
        copied_at is not None
        and set(receipt)
        == {"contract", "backup_label", "copied_at", "destination_class", "verification_status"}
        and receipt.get("contract") == OFF_HOST_CONTRACT
        and receipt.get("backup_label") == latest_label
        and receipt.get("destination_class") == "operator_controlled_off_host"
        and receipt.get("verification_status") == "checksum_verified"
        and copied_at <= now + CLOCK_SKEW
        and now - copied_at <= OFF_HOST_MAX_AGE
    )


def valid_maintenance_restore(receipt: Any) -> bool:
    return (
        isinstance(receipt, Mapping)
        and set(receipt)
        == {
            "contract",
            "backup_label",
            "schema_revision",
            "database_system_identifier",
            "completed_at",
            "restore_status",
        }
        and receipt.get("contract") == MAINTENANCE_CONTRACT
        and isinstance(receipt.get("backup_label"), str)
        and BACKUP_LABEL.fullmatch(receipt["backup_label"]) is not None
        and receipt.get("schema_revision") == SOURCE_REVISION
        and isinstance(receipt.get("database_system_identifier"), str)
        and SYSTEM_IDENTIFIER.fullmatch(receipt["database_system_identifier"]) is not None
        and _aware_utc(receipt.get("completed_at")) is not None
        and receipt.get("restore_status") == "passed"
    )


def readyz_current(summary: Mapping[str, Any] | None, revision: str) -> bool:
    return (
        summary is not None
        and summary.get("ready") is True
        and summary.get("migration") == revision
        and summary.get("expected_migration") == revision
        and summary.get("rollout_authorization") == "authorized"
        and summary.get("restore_gate") == "current"
        and summary.get("worker_maintenance") == "current"
    )


def _run(
    command: Sequence[str],
    *,
    timeout: int = 60,
    extra_env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed argv without a shell; a spawn failure reads as exit 127."""

    environment = dict(os.environ)
    environment.update(extra_env or {})
    try:
        return subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StepTimeout("operator step exceeded its bound") from exc
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(list(command), 127, b"", b"")


def _json_probe(command: Sequence[str], *, timeout: int = 30) -> Any:
    try:
        result = _run(command, timeout=timeout, capture=True)
    except StepTimeout:
        return None
    if result.returncode != 0 or len(result.stdout) > MAX_JSON_BYTES:
        return None
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def local_image(image: str) -> Mapping[str, Any] | None:
    """The exact local image entry; --pull never makes absence final."""

    inspected = _json_probe(["docker", "image", "inspect", image])
    if (
        not isinstance(inspected, list)
        or len(inspected) != 1
        or not isinstance(inspected[0], Mapping)
        or inspected[0].get("Id") != image
    ):
        return None
    return inspected[0]


def image_revision(image: str) -> str | None:
    """The exact local image's source revision label, if well formed."""

    entry = local_image(image)
    config = entry.get("Config") if entry is not None else None
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    revision = (
        labels.get("org.opencontainers.image.revision")
        if isinstance(labels, Mapping)
        else None
    )
    return revision if isinstance(revision, str) and COMMIT_SHA.fullmatch(revision) else None


def container_health(names: Sequence[str]) -> dict[str, str]:
    health: dict[str, str] = {}
    for name in names:
        state = _json_probe(["docker", "inspect", "--format={{json .State}}", name])
        detail = state.get("Health") if isinstance(state, Mapping) else None
        status = detail.get("Status") if isinstance(detail, Mapping) else None
        health[name] = status if isinstance(status, str) else "missing"
    return health


def readyz() -> dict[str, Any] | None:
    """Content-free readiness summary from inside the Core API container."""

    value = _json_probe(["docker", "exec", API_CONTAINER, "python", "-c", READYZ_PROBE])
    if not isinstance(value, Mapping) or set(value) != set(READYZ_FIELDS):
        return None
    summary: dict[str, Any] = {}
    for key in READYZ_FIELDS:
        item = value[key]
        if key == "ready":
            summary[key] = item is True
        elif isinstance(item, str) and SUMMARY_TOKEN.fullmatch(item):
            summary[key] = item
        else:
            summary[key] = None
    return summary


def backup_info() -> Any:
    return _json_probe(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "pgbackrest",
            "--stanza=home-agent",
            "info",
            "--output=json",
        ]
    )


def preflight(inputs: Inputs, *, now: datetime) -> dict[str, Any]:
    """Evaluate every admission gate without changing host state."""

    blockers: list[str] = []
    if inputs.run_migrations == "1":
        blockers.append("automatic_startup_migration_enabled")
    revision = image_revision(inputs.core_image)
    if revision is None:
        blockers.append("core_image_unverified")
    if local_image(inputs.rollback_image) is None:
        blockers.append("rollback_image_unavailable")
    if not readyz_current(readyz(), SOURCE_REVISION):
        blockers.append("core_not_ready_at_source_revision")
    if any(state != "healthy" for state in container_health(REQUIRED_HEALTHY).values()):
        blockers.append("required_container_not_healthy")
    latest_label = latest_full_backup(backup_info())
    if latest_label is None:
        blockers.append("backup_repository_not_healthy")
    if not valid_off_host_receipt(
        _protected_receipt(OFF_HOST_RECEIPT_PATH), latest_label=latest_label, now=now
    ):
        blockers.append("off_host_backup_not_current")
    if not valid_maintenance_restore(_protected_receipt(MAINTENANCE_RESTORE_PATH)):
        blockers.append("maintenance_restore_receipt_missing")
    return {
        "blockers": blockers,
        "core_image_revision": revision,
        "backup_label": latest_label,
    }


def generated_override(core_image: str) -> dict[str, Any]:
    return {
        "services": {
            "migrate": {
                "image": core_image,
                "cpus": "0.5",
                "pids_limit": 64,
                "mem_limit": "512m",
                "environment": {"HOME_AGENT_EXPECTED_DB_REVISION": TARGET_REVISION},
            }
        }
    }


def compose(inputs: Inputs, *overrides: Path, operator: bool = False) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--project-directory",
        str(inputs.project_directory),
        "--env-file",
        str(inputs.env),
        "-f",
        str(inputs.compose_file),
    ]
    for override in overrides:
        command += ["-f", str(override)]
    if operator:
        command += ["--profile", "operator"]
    return command


def _write_private(path: Path, value: Mapping[str, Any], mode: int) -> None:
    raw = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, raw)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _report_diagnostic(raw: bytes | None) -> None:
    """Print a credential-redacted stderr tail to the operator terminal only."""

    tail = _CREDENTIAL_USERINFO.sub(b"://<redacted>@", raw or b"")[-DIAGNOSTIC_TAIL_BYTES:]
    text = tail.decode("utf-8", "replace").strip()
    if text:
        print(f"--- migrate stderr (tail) ---\n{text}\n--- end stderr ---", file=sys.stderr)


class Runner:
    """One bounded 0031 -> 0047 ceremony; every step lands in the receipt."""

    def __init__(self, inputs: Inputs, report: Mapping[str, Any], started: datetime) -> None:
        self.inputs = inputs
        self.report = report
        self.started = started
        self.stamp = started.strftime("%Y%m%dT%H%M%SZ")
        self.generated = inputs.receipt_dir / f"shared-preferences-migrate-{self.stamp}.override.json"
        self.steps: list[dict[str, Any]] = []
        self.summary: dict[str, Any] | None = None
        self.migrate_env = {"HOME_AGENT_EXPECTED_DB_REVISION": TARGET_REVISION}

    def step(
        self,
        name: str,
        command: Sequence[str],
        *,
        timeout: int = 300,
        extra_env: Mapping[str, str] | None = None,
        diagnostic: bool = False,
    ) -> subprocess.CompletedProcess[bytes] | None:
        try:
            result = _run(command, timeout=timeout, extra_env=extra_env, capture=True)
        except StepTimeout:
            self.steps.append({"step": name, "exit_code": None})
            return None
        self.steps.append({"step": name, "exit_code": result.returncode})
        if diagnostic and result.returncode != 0:
            _report_diagnostic(result.stderr)
        return result

    def ok(self, name: str, command: Sequence[str], **options: Any) -> bool:
        result = self.step(name, command, **options)
        return result is not None and result.returncode == 0

    def _stop_core(self) -> bool:
        base = compose(self.inputs)
        for service in CORE_SERVICES:
            if not self.ok(f"stop-{service}", [*base, "stop", service]):
                return False
        running = self.step("verify-core-stopped", [*base, "ps", "--status", "running", "--services"])
        if running is None or running.returncode != 0:
            return False
        names = {line.strip() for line in running.stdout.decode("utf-8", "replace").splitlines()}
        return not names & set(CORE_SERVICES)

    def _migrate(self) -> subprocess.CompletedProcess[bytes] | None:
        files = (self.inputs.pool_limits, self.inputs.runtime_override, self.generated)
        return self.step(
            "migrate",
            [
                *compose(self.inputs, *files, operator=True),
                "run", "--rm", "--no-deps", "--pull", "never",
                "migrate", ENTRYPOINT,
            ],
            timeout=MIGRATE_TIMEOUT_SECONDS,
            extra_env=self.migrate_env,
            diagnostic=True,
        )

    def _source_revision_intact(self) -> bool:
        files = (self.inputs.pool_limits, self.inputs.runtime_override, self.generated)
        return self.ok(
            "verify-source-revision",
            [
                *compose(self.inputs, *files, operator=True),
                "run", "--rm", "--no-deps", "--pull", "never",
                "--entrypoint", "sh", "migrate",
                "-c", REVISION_GUARD_SCRIPT, "sh", DATABASE_URL_SECRET, SOURCE_REVISION,
            ],
            timeout=180,
            extra_env=self.migrate_env,
        )

    def _start_core(self, name: str, runtime: Path, revision: str) -> bool:
        started = self.ok(
            name,
            [
                *compose(self.inputs, self.inputs.pool_limits, runtime),
                "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                *CORE_SERVICES,
            ],
        )
        return started and self._wait_ready(revision)

    def _wait_ready(self, revision: str) -> bool:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while True:
            healthy = all(
                state == "healthy" for state in container_health(CORE_CONTAINERS).values()
            )
            self.summary = readyz()
            if healthy and readyz_current(self.summary, revision):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(READY_POLL_SECONDS)

    def _restore_source(self) -> str:
        restored = self._start_core(
            "start-core-0031", self.inputs.rollback_override, SOURCE_REVISION
        )
        return "failed_rolled_back" if restored else "failed_source_unready"

    def execute(self) -> str:
        _write_private(self.generated, generated_override(self.inputs.core_image), 0o600)
        if not self._stop_core():
            return self._restore_source()
        migrated = self._migrate()
        if migrated is None:
            return "migration_state_unverified"
        if migrated.returncode != 0:
            if not self._source_revision_intact():
                return "migration_state_unverified"
            return self._restore_source()
        if self._start_core("start-core-0047", self.inputs.runtime_override, TARGET_REVISION):
            return "migrated"
        return "migrated_core_unready"

    def receipt(self, outcome: str, finished: datetime) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "outcome": outcome,
            "started_at": _utc_text(self.started),
            "finished_at": _utc_text(finished),
            "core_image": self.inputs.core_image,
            "core_image_revision": self.report.get("core_image_revision"),
            "rollback_image": self.inputs.rollback_image,
            "from_revision": SOURCE_REVISION,
            "to_revision": TARGET_REVISION,
            "backup_label": self.report.get("backup_label"),
            "generated_override": self.generated.name,
            "steps": self.steps,
            "readyz": self.summary,
        }


OUTCOME_EXIT = {
    "migrated": 0,
    "failed_rolled_back": 4,
    "failed_source_unready": 5,
    "migrated_core_unready": 5,
    "migration_state_unverified": 5,
    "interrupted": 5,
}


def run(inputs: Inputs, report: Mapping[str, Any]) -> int:
    runner = Runner(inputs, report, _now())
    outcome = "interrupted"
    try:
        outcome = runner.execute()
    except (MigrationError, OSError):
        outcome = "interrupted"
    finally:
        path = inputs.receipt_dir / f"shared-preferences-migration-{runner.stamp}.json"
        _write_private(path, runner.receipt(outcome, _now()), 0o400)
        print(json.dumps({"contract": CONTRACT, "outcome": outcome, "receipt": str(path)}))
    if outcome == "migrated_core_unready":
        print(UNREADY_GUIDANCE, file=sys.stderr)
    elif outcome in {"migration_state_unverified", "interrupted"}:
        print(UNVERIFIED_GUIDANCE, file=sys.stderr)
    return OUTCOME_EXIT[outcome]


def activation_lock() -> int:
    import fcntl

    try:
        parent = LOCK_PATH.parent.lstat()
    except FileNotFoundError as error:
        raise MigrationError("activation lock directory is missing") from error
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise MigrationError("activation lock directory is unsafe")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(LOCK_PATH, flags, 0o600)
    except OSError as error:
        raise MigrationError("activation lock is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise MigrationError("activation lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, MigrationError) as error:
        os.close(descriptor)
        if isinstance(error, MigrationError):
            raise
        raise MigrationError("another activation operation is active") from error
    return descriptor


def release_lock(descriptor: int) -> None:
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shared-preferences-migration")
    parser.add_argument("action", choices=("check", "run"))
    for name in (
        "env",
        "compose-file",
        "compose-sha256",
        "pool-limits",
        "runtime-override",
        "rollback-override",
        "core-image",
        "receipt-dir",
    ):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    if not _is_root():
        print("shared preferences migration requires root on Linux", file=sys.stderr)
        return 77
    try:
        validate_trusted_execution()
        sanitize_process_environment()
        inputs = load_inputs(args)
        descriptor = activation_lock()
    except (MigrationError, OSError) as error:
        message = error if isinstance(error, MigrationError) else "operator input is unreadable"
        print(f"shared preferences migration failed closed: {message}", file=sys.stderr)
        return 78
    try:
        report = preflight(inputs, now=_now())
        print(
            json.dumps(
                {
                    "contract": CONTRACT,
                    "action": args.action,
                    "preflight_passed": not report["blockers"],
                    "blockers": report["blockers"],
                    "backup_label": report["backup_label"],
                    "core_image_revision": report["core_image_revision"],
                },
                sort_keys=True,
            )
        )
        if report["blockers"]:
            return 3
        if args.action == "check":
            return 0
        return run(inputs, report)
    except (MigrationError, OSError) as error:
        message = error if isinstance(error, MigrationError) else "operator I/O failed"
        print(f"shared preferences migration failed closed: {message}", file=sys.stderr)
        return 78
    finally:
        release_lock(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
