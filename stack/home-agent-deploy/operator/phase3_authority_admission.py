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
    name: Literal[
        "admit-finalizer",
        "admit-cutover",
        "record-freeze-evidence",
        "record-privacy-evidence",
        "record-cutover-candidate",
    ]
    entrypoint: str
    result_contract: str
    # The evidence writer records rows rather than admitting one document, so
    # it reports a different status and returns the identifiers it wrote.
    result_status: str = "admitted"
    result_key: str = "admission_id"
    result_is_list: bool = False


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
        AdmissionCommand(
            "record-freeze-evidence",
            "identity-evidence-freeze",
            "identity-writer-freeze-evidence-result-e5an-v1",
            result_status="recorded",
            result_key="written",
            result_is_list=True,
        ),
        AdmissionCommand(
            "record-privacy-evidence",
            "identity-evidence-privacy",
            "identity-privacy-cutover-evidence-result-e5an-v1",
            result_status="recorded",
            result_key="written",
            result_is_list=True,
        ),
        AdmissionCommand(
            "record-cutover-candidate",
            "identity-evidence-cutover",
            "identity-semantic-cutover-candidate-result-e5an-v1",
            result_status="recorded",
            result_key="written",
            result_is_list=True,
        ),
    )
}


class AuthorityAdmissionError(RuntimeError):
    """The root admission bridge could not prove a safe invocation."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code if code in activation.DIAGNOSTIC_CODES else None


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
            stderr=subprocess.PIPE,
            timeout=180,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AuthorityAdmissionError(
            "authority admission command failed", code="timeout"
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        raise AuthorityAdmissionError(
            "authority admission command failed", code="spawn_failed"
        ) from error
    if result.returncode != 0:
        code = "exit_nonzero"
    elif not result.stdout:
        code = "stdout_empty"
    elif len(result.stdout) > activation.MAX_OUTPUT_BYTES:
        code = "stdout_oversize"
    elif b"\0" in result.stdout:
        code = "stdout_nul"
    else:
        code = None
    if code is not None:
        # The admission container reads a private People document, so its
        # stderr is never echoed. Only governed kernel identifiers are named:
        # they are a closed snake_case vocabulary and the writers already
        # disable parameter echo.
        refusals = activation.governed_error_codes(result.stderr)
        if refusals:
            print(
                "authority admission refused: " + " ".join(refusals),
                file=sys.stderr,
            )
        raise AuthorityAdmissionError("authority admission command failed", code=code)
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthorityAdmissionError("authority admission result is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("contract") != command.result_contract
        or value.get("status") != command.result_status
        or not isinstance(
            value.get(command.result_key), list if command.result_is_list else str
        )
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
        command.result_key: result[command.result_key],
        "contract": CONTRACT,
        "operation": command.name,
        "status": command.result_status,
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
