#!/usr/bin/env python3
"""Execute one exact Phase 3 migration stop under a bounded root ceremony.

The executor cannot arm itself, stop application services, choose an arbitrary
revision, pull/build an image, or start the normal deployment. It accepts only
the five reviewed stage commands, requires the separately armed E5m permit and
the exact hosted-tested source pack, proves all application-facing services
are stopped, verifies the expected current revision, runs one fixed image
entrypoint, and verifies the resulting revision.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence


CONTRACT = "phase3-migration-executor-e5t-v1"
PERMIT_VALUE = (
    "phase3-grant-permit-e5m-v1:"
    "0017_authenticated_binding_e5c:0021_parent_status_e5h\n"
)
PERMIT_MAX_AGE = timedelta(hours=4)
SOURCE_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = SOURCE_ROOT / "stack/home-agent-compose.yml"
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
PERMIT_PATH = Path("/srv/home-agent/config/phase3-grant-permit-e5m.txt")
LOCK_PATH = Path("/srv/home-agent/locks/phase3-activation.lock")
SOURCE_PLAN = Path(__file__).with_name("phase3_activation_source_plan.py")
PROTECTED_SERVICES = frozenset(
    {"core-api", "core-ingest", "core-worker", "bff", "edge-ingress"}
)
MAX_OUTPUT_BYTES = 64 * 1024
DATABASE_URL_SECRET = "/run/secrets/database_url"
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


@dataclass(frozen=True, slots=True)
class MigrationStage:
    command: str
    entrypoint: str
    source_revision: str
    target_revision: str


STAGES = {
    stage.command: stage
    for stage in (
        MigrationStage(
            "migrate-finalizer",
            "phase3-migrate-finalizer",
            "0006a_worker_lease_arbitration",
            "0013_identity_finalizer_e3",
        ),
        MigrationStage(
            "migrate-current-authority",
            "phase3-migrate-current-authority",
            "0013_identity_finalizer_e3",
            "0015_current_authority_e5a",
        ),
        MigrationStage(
            "migrate-authenticated-binding",
            "phase3-migrate-authenticated-binding",
            "0015_current_authority_e5a",
            "0017_authenticated_binding_e5c",
        ),
        MigrationStage(
            "migrate-parent-authority",
            "phase3-migrate-parent-authority",
            "0017_authenticated_binding_e5c",
            "0018_parent_relationship_e5d",
        ),
        MigrationStage(
            "migrate-parent-status",
            "phase3-migrate-parent-status",
            "0018_parent_relationship_e5d",
            "0021_parent_status_e5h",
        ),
    )
}


DIAGNOSTIC_TAIL_BYTES = 4 * 1024
DIAGNOSTIC_CODES = frozenset(
    {
        "spawn_failed",
        "timeout",
        "exit_nonzero",
        "stdout_empty",
        "stdout_oversize",
        "stdout_nul",
    }
)
_CREDENTIAL_USERINFO = re.compile(rb"://[^@\s]*@")
_GOVERNED_ERROR_CODE = re.compile(
    rb"\b((?:identity|rollout|phase3)_[a-z0-9_]{3,80})\b"
)
_NEVER_DIAGNOSE = (
    "backup",
    "restore",
    "signing",
    "privacy",
    "snapshot",
    "identity-admit",
    "identity-register",
    "credential",
)


class MigrationExecutionError(RuntimeError):
    """The fixed migration ceremony could not establish trusted state."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code if code in DIAGNOSTIC_CODES else None


def _require_root_linux() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise MigrationExecutionError(
            "phase3 migration execution requires root on Linux"
        )


def redact_diagnostic(raw: bytes | None) -> bytes:
    """Remove credential userinfo from a diagnostic tail."""

    return _CREDENTIAL_USERINFO.sub(b"://<redacted>@", raw or b"")


def governed_error_codes(raw: bytes | None) -> tuple[str, ...]:
    """Return only governed kernel error identifiers found in a diagnostic.

    Kernel RAISE identifiers are a closed snake_case vocabulary carrying no
    household content, and the admission writers already disable parameter
    echo, so the identifier alone names the actual refusal without quoting any
    surrounding text.
    """

    found: list[str] = []
    for match in _GOVERNED_ERROR_CODE.findall(raw or b""):
        code = match.decode("ascii", "ignore")
        if code and code not in found:
            found.append(code)
    return tuple(found[:8])


def diagnosable(command: Sequence[str]) -> bool:
    """Refuse to echo any subprocess that can emit household People data.

    Defence in depth behind the per-call-site opt-in: even a mistaken
    ``diagnostic=True`` cannot print the restore drill, either backup writer,
    a signing phase, the privacy observer, the legacy snapshot, or an identity
    admission/registration container.
    """

    joined = " ".join(str(item) for item in command).lower()
    return not any(marker in joined for marker in _NEVER_DIAGNOSE)


def report_diagnostic(
    command: Sequence[str], raw: bytes | None, *, enabled: bool
) -> None:
    """Print a redacted stderr tail to the operator terminal only.

    This never reaches the activation journal. The journal is restricted to
    categorical codes by contract, and these subprocesses emit arbitrary text.
    """

    if not enabled or not raw or not diagnosable(command):
        return
    tail = redact_diagnostic(raw)[-DIAGNOSTIC_TAIL_BYTES:]
    text = tail.decode("utf-8", "replace").strip()
    if not text:
        return
    name = Path(str(command[0])).name
    print(f"--- {name} stderr (tail) ---", file=sys.stderr)
    print(text, file=sys.stderr)
    print("--- end stderr ---", file=sys.stderr)


def _run(
    command: Sequence[str],
    *,
    accepted_codes: frozenset[int] = frozenset({0}),
    timeout: int = 300,
    diagnostic: bool = False,
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            cwd=SOURCE_ROOT,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise MigrationExecutionError(
            "phase3 migration command failed", code="timeout"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise MigrationExecutionError(
            "phase3 migration command failed", code="spawn_failed"
        ) from error
    if result.returncode not in accepted_codes:
        code = "exit_nonzero"
    elif len(result.stdout) > MAX_OUTPUT_BYTES:
        code = "stdout_oversize"
    elif b"\0" in result.stdout:
        code = "stdout_nul"
    else:
        return result.stdout
    report_diagnostic(command, result.stderr, enabled=diagnostic)
    raise MigrationExecutionError("phase3 migration command failed", code=code)


def _compose(*arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(ENVIRONMENT_PATH),
        "-f",
        str(COMPOSE),
        "--profile",
        "operator",
        *arguments,
    ]


def _require_trusted_source() -> Mapping[str, Any]:
    raw = _run(
        [sys.executable, str(SOURCE_PLAN)],
        accepted_codes=frozenset({3}),
        timeout=30,
    )
    try:
        report = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationExecutionError("hosted source evidence is invalid") from error
    if (
        not isinstance(report, Mapping)
        or report.get("source_pack_matches_hosted_acceptance") is not True
        or report.get("fixed_migration_entrypoints_installed") is not True
        or report.get("activation_grant_contract_installed") is not True
    ):
        raise MigrationExecutionError("hosted source evidence is invalid")
    return report


def _require_fresh_permit(now: datetime) -> None:
    try:
        details = PERMIT_PATH.lstat()
    except FileNotFoundError as error:
        raise MigrationExecutionError("phase3 migration permit is not armed") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
        or PERMIT_PATH.read_text(encoding="ascii") != PERMIT_VALUE
    ):
        raise MigrationExecutionError("phase3 migration permit is invalid")
    modified = datetime.fromtimestamp(details.st_mtime, tz=UTC)
    current = now.astimezone(UTC)
    if modified > current + timedelta(minutes=1) or current - modified > PERMIT_MAX_AGE:
        raise MigrationExecutionError("phase3 migration permit is stale")


def _running_protected_services() -> frozenset[str]:
    raw = _run(
        _compose("ps", "--status", "running", "--services"),
        timeout=30,
        diagnostic=True,
    )
    try:
        services = {
            line.strip()
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        }
    except UnicodeDecodeError as error:
        raise MigrationExecutionError("deployment state is invalid") from error
    return frozenset(services & PROTECTED_SERVICES)


def revision_guard_arguments(revision: str) -> tuple[str, ...]:
    """Verify one exact revision with the database secret already loaded.

    The image entrypoint is the only component that materialises
    HOME_AGENT_DATABASE_URL from HOME_AGENT_DATABASE_URL_FILE, and it dispatches
    on a fixed role rather than offering a verify-only one, so overriding it
    with ``python`` leaves app.migration_guard without a database URL and the
    guard fails closed for every well-formed deployment. Load the one secret it
    needs under the entrypoint's own rules (never both forms, present, readable,
    non-empty, no whitespace) instead of bypassing the loader. The secret value
    stays inside the container; only its path is ever passed as an argument.
    """

    return (
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "sh",
        "migrate",
        "-c",
        REVISION_GUARD_SCRIPT,
        "sh",
        DATABASE_URL_SECRET,
        revision,
    )


def _guard_revision(revision: str) -> None:
    _run(_compose(*revision_guard_arguments(revision)), timeout=180, diagnostic=True)


def _migrate(stage: MigrationStage) -> None:
    _run(
        _compose(
            "run",
            "--rm",
            "--no-deps",
            "migrate",
            stage.entrypoint,
        ),
        timeout=900,
        diagnostic=True,
    )


def _activation_lock() -> int:
    import fcntl

    try:
        parent = LOCK_PATH.parent.lstat()
    except FileNotFoundError as error:
        raise MigrationExecutionError("activation lock directory is missing") from error
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise MigrationExecutionError("activation lock directory is unsafe")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(LOCK_PATH, flags, 0o600)
    except OSError as error:
        raise MigrationExecutionError("activation lock is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise MigrationExecutionError("activation lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError, MigrationExecutionError) as error:
        os.close(descriptor)
        if isinstance(error, MigrationExecutionError):
            raise
        raise MigrationExecutionError("another activation operation is active") from error
    return descriptor


def _unlock_activation(descriptor: int) -> None:
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def execute(stage: MigrationStage, *, now: datetime | None = None) -> dict[str, str]:
    _require_root_linux()
    _require_trusted_source()
    _require_fresh_permit(now or datetime.now(UTC))
    descriptor = _activation_lock()
    try:
        if _running_protected_services():
            raise MigrationExecutionError(
                "application-facing services must be stopped"
            )
        _guard_revision(stage.source_revision)
        _migrate(stage)
        _guard_revision(stage.target_revision)
    finally:
        _unlock_activation(descriptor)
    return {
        "contract": CONTRACT,
        "source_revision": stage.source_revision,
        "status": "verified",
        "target_revision": stage.target_revision,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="phase3-migration-executor")
    parser.add_argument("command", choices=tuple(STAGES))
    arguments = parser.parse_args()
    try:
        result = execute(STAGES[arguments.command])
    except MigrationExecutionError:
        print("phase3 migration executor failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
