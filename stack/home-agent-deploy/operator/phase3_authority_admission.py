#!/usr/bin/env python3
"""Root-only bridge to the two content-minimized identity admission writers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import sys
from typing import Literal

import phase3_migration_executor as activation


CONTRACT = "phase3-authority-admission-e5u-v1"
MAX_REQUEST_BYTES = 5_593_088


@dataclass(frozen=True, slots=True)
class AdmissionCommand:
    name: Literal["admit-finalizer", "admit-cutover"]
    entrypoint: str
    result_contract: str


COMMANDS = {
    command.name: command
    for command in (
        AdmissionCommand(
            "admit-finalizer",
            "identity-admit-finalizer",
            "identity-finalizer-admission-result-e5u-v1",
        ),
        AdmissionCommand(
            "admit-cutover",
            "identity-admit-cutover",
            "identity-cutover-admission-result-e5u-v1",
        ),
    )
}


class AuthorityAdmissionError(RuntimeError):
    """The root admission bridge could not prove a safe invocation."""


def _private_request() -> bytes:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES or b"\0" in raw:
        raise AuthorityAdmissionError("authority admission request is invalid")
    return raw


def _invoke(command: AdmissionCommand, request: bytes) -> dict[str, str]:
    arguments = activation._compose(
        "run",
        "--rm",
        "--no-deps",
        "migrate",
        command.entrypoint,
    )
    try:
        result = subprocess.run(
            arguments,
            cwd=activation.SOURCE_ROOT,
            check=False,
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=180,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityAdmissionError("authority admission command failed") from error
    if (
        result.returncode != 0
        or not result.stdout
        or len(result.stdout) > activation.MAX_OUTPUT_BYTES
        or b"\0" in result.stdout
    ):
        raise AuthorityAdmissionError("authority admission command failed")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityAdmissionError("authority admission result is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("contract") != command.result_contract
        or value.get("status") != "admitted"
        or not isinstance(value.get("admission_id"), str)
    ):
        raise AuthorityAdmissionError("authority admission result is invalid")
    return value


def execute(command: AdmissionCommand) -> dict[str, str]:
    activation._require_root_linux()
    activation._require_trusted_source()
    activation._require_fresh_permit(activation.datetime.now(activation.UTC))
    descriptor = activation._activation_lock()
    try:
        if activation._running_protected_services():
            raise AuthorityAdmissionError(
                "application-facing services must be stopped"
            )
        activation._guard_revision("0015_current_authority_e5a")
        result = _invoke(command, _private_request())
        activation._guard_revision("0015_current_authority_e5a")
    finally:
        activation._unlock_activation(descriptor)
    return {
        "admission_id": result["admission_id"],
        "contract": CONTRACT,
        "operation": command.name,
        "status": "admitted",
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print("phase3 authority admission requires one fixed operation", file=sys.stderr)
        return 64
    try:
        result = execute(COMMANDS[sys.argv[1]])
    except (AuthorityAdmissionError, activation.MigrationExecutionError):
        print("phase3 authority admission failed closed", file=sys.stderr)
        return 78
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
