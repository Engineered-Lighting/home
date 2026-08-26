#!/usr/bin/env python3
"""Bound one private authority invocation to a two-minute database login."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import sys
from typing import Literal

import phase3_migration_executor as activation


CONTRACT = "phase3-identity-authority-ceremony-e5v-v1"
MAX_REQUEST_BYTES = 5_593_088


@dataclass(frozen=True, slots=True)
class AuthorityCommand:
    name: Literal["finalize", "cutover", "register"]
    service: str
    # The role ceremony's target vocabulary, which is not the command name.
    # "cutover" happens to match; "finalize" does not, and passing the command
    # name would be refused as an invalid target.
    role: Literal["finalizer", "cutover", "migration"]
    expected_contract: str


COMMANDS = {
    command.name: command
    for command in (
        AuthorityCommand(
            "finalize",
            "identity-finalizer",
            "finalizer",
            "identity-finalizer-result-e5n-v1",
        ),
        AuthorityCommand(
            "cutover",
            "identity-cutover",
            "cutover",
            "identity-cutover-result-e5n-v1",
        ),
        AuthorityCommand(
            "register",
            "identity-registrar",
            "migration",
            "identity-migration-registration-result-e5ak-v1",
        ),
    )
}


class AuthorityCeremonyError(RuntimeError):
    """The disposable authority ceremony failed closed."""


def _request() -> bytes:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise AuthorityCeremonyError("identity authority request is invalid")
    return raw


def _role_ceremony(mode: str, authority: str) -> None:
    try:
        result = subprocess.run(
            activation._compose(
                "run",
                "--rm",
                "--no-deps",
                "identity-role-activation",
                mode,
                authority,
            ),
            cwd=activation.SOURCE_ROOT,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityCeremonyError(
            "identity authority role ceremony failed"
        ) from error
    if result.returncode != 0:
        raise AuthorityCeremonyError("identity authority role ceremony failed")


def _invoke(command: AuthorityCommand, request: bytes) -> dict[str, str]:
    try:
        result = subprocess.run(
            activation._compose(
                "run",
                "--rm",
                "--no-deps",
                command.service,
            ),
            cwd=activation.SOURCE_ROOT,
            check=False,
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=180,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityCeremonyError("identity authority invocation failed") from error
    if (
        result.returncode != 0
        or not result.stdout
        or len(result.stdout) > activation.MAX_OUTPUT_BYTES
        or b"\0" in result.stdout
    ):
        raise AuthorityCeremonyError("identity authority invocation failed")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityCeremonyError("identity authority result is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("contract") != command.expected_contract
        or value.get("status") != "committed"
        or not isinstance(value.get("result_id"), str)
    ):
        raise AuthorityCeremonyError("identity authority result is invalid")
    return value


def execute(command: AuthorityCommand) -> dict[str, str]:
    activation._require_root_linux()
    activation._require_trusted_source()
    activation._require_fresh_permit(activation.datetime.now(activation.UTC))
    descriptor = activation._activation_lock()
    activated = False
    result: dict[str, str] | None = None
    primary_error: BaseException | None = None
    try:
        if activation._running_protected_services():
            raise AuthorityCeremonyError(
                "application-facing services must be stopped"
            )
        activation._guard_revision("0015_current_authority_e5a")
        private_request = _request()
        _role_ceremony("activate", command.role)
        activated = True
        result = _invoke(command, private_request)
    except BaseException as error:
        primary_error = error
    finally:
        try:
            if activated:
                _role_ceremony("deactivate", command.role)
            else:
                # Prove an unsuccessful activation attempt did not leave a
                # callable login. This is safe and idempotent.
                _role_ceremony("deactivate", command.role)
            activation._guard_revision("0015_current_authority_e5a")
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
        activation._unlock_activation(descriptor)
    if primary_error is not None:
        if isinstance(
            primary_error,
            (AuthorityCeremonyError, activation.MigrationExecutionError),
        ):
            raise primary_error
        raise AuthorityCeremonyError("identity authority ceremony failed") from (
            primary_error
        )
    if result is None:
        raise AuthorityCeremonyError("identity authority ceremony failed")
    return {
        "contract": CONTRACT,
        "operation": command.name,
        "result_id": result["result_id"],
        "status": "committed_and_reexpired",
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print("identity authority ceremony requires one fixed operation", file=sys.stderr)
        return 64
    try:
        result = execute(COMMANDS[sys.argv[1]])
    except (AuthorityCeremonyError, activation.MigrationExecutionError):
        print("identity authority ceremony failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
