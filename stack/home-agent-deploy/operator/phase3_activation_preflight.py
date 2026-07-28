#!/usr/bin/env python3
"""Read-only E5j admission preflight for the dormant Phase 3 deployment.

This command is deliberately not an activation command. It reads the existing
operator-only Core diagnostics and local deployment metadata, verifies
root-owned receipts, and emits a content-minimized blocker report. It never
opens a database connection, changes rollout mode, runs Compose, or invokes an
Alembic migration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence


CONTRACT = "phase3-activation-preflight-e5j-v1"
SOURCE_REVISION = "0006a_worker_lease_arbitration"
TARGET_REVISION = "0021_parent_status_e5h"
PHASE2_CONTRACT = "phase2-record-only-gate-v3"
PHASE2_RULE = "record-only-envelope-worker-gate-v3"
PHASE3_CONTRACT = "phase3-readiness-diagnostic-v0"
RESTORE_RECEIPT_CONTRACT = "phase3-restore-drill-receipt-e5j-v1"
ERASURE_RECEIPT_CONTRACT = "phase3-erasure-current-receipt-e5j-v1"
OFF_HOST_RECEIPT_CONTRACT = "phase3-off-host-backup-receipt-e5j-v1"
SOURCE_ACCEPTANCE_CONTRACT = "phase3-source-acceptance-e5j-v1"
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
RESTORE_RECEIPT_PATH = Path("/srv/home-agent/config/phase3-restore-drill-e5j.json")
ERASURE_RECEIPT_PATH = Path(
    "/srv/home-agent/config/phase3-erasure-current-e5j.json"
)
SOURCE_ACCEPTANCE_PATH = Path(
    "/srv/home-agent/config/phase3-source-acceptance-e5j.json"
)
OFF_HOST_RECEIPT_PATH = Path("/srv/home-agent/config/phase3-off-host-backup-e5j.json")
LEDGER_HEAD_PATH = Path("/srv/home-agent/erasure-ledger/ledger.head.json")
SOURCE_ROOT = Path(__file__).resolve().parents[3]
CORE_CONTAINER = "home-agent-core-api-1"
POSTGRES_CONTAINER = "home-agent-postgres-1"
REQUIRED_CONTAINER_STATES = {
    "home-agent-postgres-1": "healthy",
    "home-agent-core-api-1": "healthy",
    "home-agent-core-ingest-1": "healthy",
    "home-agent-core-worker-1": "healthy",
    "home-agent-bff-1": "healthy",
    # nginx edge-ingress intentionally has no Docker healthcheck.
    "home-agent-edge-ingress-1": "running",
}
MAX_RECEIPT_AGE = timedelta(days=35)
BACKUP_LABEL = re.compile(r"^[0-9]{8}-[0-9]{6}F$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{5,19}$")
SYSTEM_IDENTIFIER = re.compile(r"^[1-9][0-9]{9,19}$")
HEAD_HASH = re.compile(r"^[0-9a-f]{64}$")

CORE_PROBE = r"""
import json
from pathlib import Path
from urllib.request import Request, urlopen

operator = Path("/run/secrets/operator_token").read_text(encoding="utf-8").strip()
bootstrap = Path("/run/secrets/bootstrap_token").read_text(encoding="utf-8").strip()
result = {}
for key, path in (
    ("phase2", "/v1/operator-rollout/phase2-readiness"),
    ("phase3", "/v1/operator-rollout/phase3-readiness"),
):
    request = Request(
        "http://127.0.0.1:8104" + path,
        headers={
            "Authorization": "Bearer " + operator,
            "X-Home-Agent-Bootstrap": bootstrap,
        },
    )
    with urlopen(request, timeout=5) as response:
        result[key] = json.load(response)
print(json.dumps(result, separators=(",", ":"), sort_keys=True))
""".strip()


class PreflightError(RuntimeError):
    """The diagnostic could not establish a trustworthy input boundary."""


def _aware_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PreflightError("receipt timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PreflightError("receipt timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PreflightError("receipt timestamp is invalid")
    return parsed.astimezone(UTC)


def selected_environment(text: str) -> dict[str, str]:
    """Read only the three non-secret settings used by this diagnostic."""

    selected: dict[str, str] = {}
    expected = {
        "HOME_AGENT_EXPECTED_DB_REVISION",
        "HOME_AGENT_ROLLOUT_MODE",
        "HOME_AGENT_BACKUP_TOPOLOGY",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name not in expected:
            continue
        if (
            name in selected
            or not value
            or any(character.isspace() for character in value)
        ):
            raise PreflightError("deployment environment is invalid")
        selected[name] = value
    if set(selected) != expected:
        raise PreflightError("deployment environment is incomplete")
    return selected


def _latest_full_backup(info: Any) -> tuple[str | None, bool]:
    if not isinstance(info, list) or len(info) != 1:
        return None, False
    stanza = info[0]
    if not isinstance(stanza, Mapping):
        return None, False
    status = stanza.get("status")
    if (
        not isinstance(status, Mapping)
        or status.get("code") != 0
        or stanza.get("cipher") in {None, "none"}
    ):
        return None, False
    completed: list[tuple[int, str]] = []
    for value in stanza.get("backup", []):
        if not isinstance(value, Mapping):
            continue
        label = value.get("label")
        timestamp = value.get("timestamp")
        if (
            value.get("type") != "full"
            or value.get("error") is not False
            or not isinstance(label, str)
            or BACKUP_LABEL.fullmatch(label) is None
            or not isinstance(timestamp, Mapping)
            or not isinstance(timestamp.get("stop"), int)
        ):
            continue
        completed.append((timestamp["stop"], label))
    if not completed:
        return None, False
    completed.sort()
    return completed[-1][1], True


def _valid_restore_receipt(
    receipt: Any,
    *,
    latest_backup_label: str | None,
    now: datetime,
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    try:
        completed_at = _aware_utc(receipt.get("completed_at"))
    except PreflightError:
        return False
    return (
        set(receipt)
        == {
            "contract",
            "backup_label",
            "schema_revision",
            "database_system_identifier",
            "completed_at",
            "restore_status",
        }
        and receipt.get("contract") == RESTORE_RECEIPT_CONTRACT
        and latest_backup_label is not None
        and receipt.get("backup_label") == latest_backup_label
        and receipt.get("schema_revision") == SOURCE_REVISION
        and isinstance(receipt.get("database_system_identifier"), str)
        and SYSTEM_IDENTIFIER.fullmatch(receipt["database_system_identifier"])
        is not None
        and receipt.get("restore_status") == "passed"
        and completed_at <= now
        and now - completed_at <= MAX_RECEIPT_AGE
    )


def _valid_ledger_head(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"version", "epoch", "head_hash"}
        and value.get("version") == 1
        and not isinstance(value.get("epoch"), bool)
        and isinstance(value.get("epoch"), int)
        and value["epoch"] >= 0
        and isinstance(value.get("head_hash"), str)
        and HEAD_HASH.fullmatch(value["head_hash"]) is not None
        and (value["epoch"] != 0 or value["head_hash"] == "0" * 64)
    )


def _valid_erasure_receipt(
    receipt: Any,
    *,
    restore_receipt: Any,
    ledger_head: Any,
    latest_backup_label: str | None,
    now: datetime,
) -> bool:
    if (
        not isinstance(receipt, Mapping)
        or not isinstance(restore_receipt, Mapping)
        or not _valid_ledger_head(ledger_head)
        or not _valid_restore_receipt(
            restore_receipt,
            latest_backup_label=latest_backup_label,
            now=now,
        )
    ):
        return False
    try:
        verified_at = _aware_utc(receipt.get("verified_at"))
    except PreflightError:
        return False
    return (
        set(receipt)
        == {
            "contract",
            "backup_label",
            "schema_revision",
            "ledger_epoch",
            "ledger_head_hash",
            "verified_at",
            "gate_status",
        }
        and receipt.get("contract") == ERASURE_RECEIPT_CONTRACT
        and receipt.get("backup_label") == restore_receipt.get("backup_label")
        and receipt.get("schema_revision") == SOURCE_REVISION
        and receipt.get("ledger_epoch") == ledger_head.get("epoch")
        and receipt.get("ledger_head_hash") == ledger_head.get("head_hash")
        and receipt.get("gate_status") == "current"
        and verified_at <= now
        and now - verified_at <= MAX_RECEIPT_AGE
    )


def _valid_source_acceptance(receipt: Any) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    postgres_run = receipt.get("postgres_authority_run_id")
    web_run = receipt.get("web_boundary_run_id")
    return (
        set(receipt)
        == {
            "contract",
            "target_revision",
            "source_commit",
            "postgres_authority_run_id",
            "postgres_authority_status",
            "web_boundary_run_id",
            "web_boundary_status",
            "activation_contract_status",
        }
        and receipt.get("contract") == SOURCE_ACCEPTANCE_CONTRACT
        and receipt.get("target_revision") == TARGET_REVISION
        and isinstance(receipt.get("source_commit"), str)
        and COMMIT_SHA.fullmatch(receipt["source_commit"]) is not None
        and isinstance(postgres_run, str)
        and RUN_ID.fullmatch(postgres_run) is not None
        and receipt.get("postgres_authority_status") == "success"
        and isinstance(web_run, str)
        and RUN_ID.fullmatch(web_run) is not None
        and receipt.get("web_boundary_status") == "success"
        and receipt.get("activation_contract_status") == "installed"
    )


def _valid_off_host_receipt(
    receipt: Any,
    *,
    latest_backup_label: str | None,
    now: datetime,
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    try:
        copied_at = _aware_utc(receipt.get("copied_at"))
    except PreflightError:
        return False
    return (
        set(receipt)
        == {
            "contract",
            "backup_label",
            "copied_at",
            "destination_class",
            "verification_status",
        }
        and receipt.get("contract") == OFF_HOST_RECEIPT_CONTRACT
        and latest_backup_label is not None
        and receipt.get("backup_label") == latest_backup_label
        and receipt.get("destination_class") == "operator_controlled_off_host"
        and receipt.get("verification_status") == "checksum_verified"
        and copied_at <= now
        and now - copied_at <= MAX_RECEIPT_AGE
    )


def evaluate(
    *,
    environment: Mapping[str, str],
    core_probe: Any,
    backup_info: Any,
    container_health: Mapping[str, str],
    restore_receipt: Any,
    erasure_receipt: Any,
    ledger_head: Any,
    off_host_receipt: Any,
    source_acceptance: Any,
    source_commit: str,
    source_tree_clean: bool,
    now: datetime,
) -> dict[str, Any]:
    """Return the canonical, non-authoritative E5j admission report."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise PreflightError("evaluation time must be timezone-aware")
    evaluated_at = now.astimezone(UTC)
    phase2 = core_probe.get("phase2") if isinstance(core_probe, Mapping) else None
    phase3 = core_probe.get("phase3") if isinstance(core_probe, Mapping) else None

    phase2_shape_valid = (
        isinstance(phase2, Mapping)
        and phase2.get("contract") == PHASE2_CONTRACT
        and phase2.get("rule_version") == PHASE2_RULE
        and isinstance(phase2.get("ready_to_advance"), bool)
        and isinstance(phase2.get("relevant_envelopes"), int)
        and phase2.get("relevant_envelopes_required") == 500
        and isinstance(phase2.get("relevant_envelopes_remaining"), int)
        and isinstance(phase2.get("blockers"), list)
        and all(isinstance(value, str) for value in phase2.get("blockers", []))
    )
    phase3_shape_valid = (
        isinstance(phase3, Mapping)
        and phase3.get("contract") == PHASE3_CONTRACT
        and phase3.get("schema_revision") == SOURCE_REVISION
        and phase3.get("authoritative") is False
        and phase3.get("enables_writes") is False
        and phase3.get("ready_to_advance") is False
    )
    latest_backup_label, backup_healthy = _latest_full_backup(backup_info)
    unready_containers = sorted(
        name
        for name, required_state in REQUIRED_CONTAINER_STATES.items()
        if container_health.get(name) != required_state
    )

    blockers: list[str] = []
    if environment.get("HOME_AGENT_EXPECTED_DB_REVISION") != SOURCE_REVISION:
        blockers.append("source_revision_not_pinned")
    if environment.get("HOME_AGENT_ROLLOUT_MODE") != "record_only":
        blockers.append("rollout_mode_not_record_only")
    if not phase2_shape_valid or not phase3_shape_valid:
        blockers.append("core_admission_diagnostic_invalid")
    elif phase2.get("ready_to_advance") is not True:
        blockers.append("record_only_evidence_not_ready")
    if unready_containers:
        blockers.append("required_container_not_ready")
    if not backup_healthy:
        blockers.append("encrypted_backup_repository_not_healthy")
    if not _valid_off_host_receipt(
        off_host_receipt,
        latest_backup_label=latest_backup_label,
        now=evaluated_at,
    ):
        blockers.append("off_host_backup_receipt_missing")
    if not _valid_restore_receipt(
        restore_receipt,
        latest_backup_label=latest_backup_label,
        now=evaluated_at,
    ):
        blockers.append("current_backup_restore_receipt_missing")
    if not _valid_erasure_receipt(
        erasure_receipt,
        restore_receipt=restore_receipt,
        ledger_head=ledger_head,
        latest_backup_label=latest_backup_label,
        now=evaluated_at,
    ):
        blockers.append("current_erasure_gate_receipt_missing")
    source_accepted = (
        source_tree_clean
        and COMMIT_SHA.fullmatch(source_commit) is not None
        and _valid_source_acceptance(source_acceptance)
        and source_acceptance.get("source_commit") == source_commit
    )
    if not source_accepted:
        blockers.append("reviewed_activation_source_not_admitted")

    phase2_summary: dict[str, Any] = {
        "valid": phase2_shape_valid,
        "ready": False,
        "qualifying_events": None,
        "required_events": 500,
        "remaining_events": None,
        "blockers": [],
    }
    if phase2_shape_valid:
        phase2_summary.update(
            {
                "ready": phase2["ready_to_advance"],
                "qualifying_events": phase2["relevant_envelopes"],
                "remaining_events": phase2["relevant_envelopes_remaining"],
                "blockers": list(phase2["blockers"]),
            }
        )

    return {
        "contract": CONTRACT,
        "evaluated_at": evaluated_at.isoformat().replace("+00:00", "Z"),
        "source_revision": SOURCE_REVISION,
        "target_revision": TARGET_REVISION,
        "phase2": phase2_summary,
        "deployment": {
            "rollout_mode": environment.get("HOME_AGENT_ROLLOUT_MODE", "unknown"),
            "required_containers_ready": not unready_containers,
            "unready_containers": unready_containers,
        },
        "backup": {
            "repository_healthy": backup_healthy,
            "latest_full_backup_label": latest_backup_label,
            "active_topology": environment.get("HOME_AGENT_BACKUP_TOPOLOGY", "unknown"),
            "off_host_receipt": (
                "valid"
                if _valid_off_host_receipt(
                    off_host_receipt,
                    latest_backup_label=latest_backup_label,
                    now=evaluated_at,
                )
                else "missing_or_invalid"
            ),
            "current_restore_receipt": (
                "valid"
                if _valid_restore_receipt(
                    restore_receipt,
                    latest_backup_label=latest_backup_label,
                    now=evaluated_at,
                )
                else "missing_or_invalid"
            ),
            "current_erasure_gate_receipt": (
                "valid"
                if _valid_erasure_receipt(
                    erasure_receipt,
                    restore_receipt=restore_receipt,
                    ledger_head=ledger_head,
                    latest_backup_label=latest_backup_label,
                    now=evaluated_at,
                )
                else "missing_or_invalid"
            ),
        },
        "source_acceptance": "valid" if source_accepted else "missing_or_invalid",
        "source_tree_clean": source_tree_clean,
        "preflight_passed": not blockers,
        "authoritative": False,
        "enables_writes": False,
        "changes_rollout_mode": False,
        "runs_migrations": False,
        "blockers": blockers,
    }


def _run(command: Sequence[str], *, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PreflightError("read-only deployment probe failed") from error
    return result.stdout


def _protected_file(
    path: Path,
    *,
    required: bool,
    expected_uid: int,
    expected_gid: int,
) -> Any:
    try:
        details = path.lstat()
    except FileNotFoundError:
        if required:
            raise PreflightError("required operator input is missing")
        return None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != expected_uid
        or details.st_gid != expected_gid
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_nlink != 1
    ):
        raise PreflightError("operator input has unsafe ownership or mode")
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > 64 * 1024 or b"\0" in raw:
            raise PreflightError("operator input has invalid size")
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreflightError("operator input is invalid JSON") from error


def _environment() -> dict[str, str]:
    try:
        details = ENVIRONMENT_PATH.lstat()
    except FileNotFoundError as error:
        raise PreflightError("deployment environment is missing") from error
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) & 0o022
    ):
        raise PreflightError("deployment environment is unsafe")
    return selected_environment(ENVIRONMENT_PATH.read_text(encoding="utf-8"))


def live_report(*, now: datetime | None = None) -> dict[str, Any]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PreflightError("live preflight requires the root operator on Linux")
    environment = _environment()
    core_probe = json.loads(
        _run(["docker", "exec", CORE_CONTAINER, "python", "-c", CORE_PROBE])
    )
    backup_info = json.loads(
        _run(
            [
                "docker",
                "exec",
                POSTGRES_CONTAINER,
                "pgbackrest",
                "--stanza=home-agent",
                "info",
                "--output=json",
            ],
            timeout=30,
        )
    )
    health: dict[str, str] = {}
    for name, required_state in REQUIRED_CONTAINER_STATES.items():
        state = json.loads(
            _run(["docker", "inspect", "--format={{json .State}}", name])
        )
        if not isinstance(state, Mapping):
            raise PreflightError("container state is invalid")
        if required_state == "healthy":
            health_state = state.get("Health")
            health[name] = (
                str(health_state.get("Status"))
                if isinstance(health_state, Mapping)
                else "missing"
            )
        else:
            health[name] = str(state.get("Status", "missing"))
    source_commit = _run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"]).strip()
    source_tree_clean = (
        _run(["git", "-C", str(SOURCE_ROOT), "status", "--porcelain"]).strip() == ""
    )
    return evaluate(
        environment=environment,
        core_probe=core_probe,
        backup_info=backup_info,
        container_health=health,
        restore_receipt=_protected_file(
            RESTORE_RECEIPT_PATH,
            required=False,
            expected_uid=0,
            expected_gid=0,
        ),
        erasure_receipt=_protected_file(
            ERASURE_RECEIPT_PATH,
            required=False,
            expected_uid=0,
            expected_gid=0,
        ),
        ledger_head=_protected_file(
            LEDGER_HEAD_PATH,
            required=True,
            expected_uid=10001,
            expected_gid=10001,
        ),
        off_host_receipt=_protected_file(
            OFF_HOST_RECEIPT_PATH,
            required=False,
            expected_uid=0,
            expected_gid=0,
        ),
        source_acceptance=_protected_file(
            SOURCE_ACCEPTANCE_PATH,
            required=False,
            expected_uid=0,
            expected_gid=0,
        ),
        source_commit=source_commit,
        source_tree_clean=source_tree_clean,
        now=now or datetime.now(UTC),
    )


def main() -> int:
    if len(sys.argv) != 1:
        print("phase3 activation preflight accepts no arguments", file=sys.stderr)
        return 64
    try:
        report = live_report()
    except (PreflightError, json.JSONDecodeError):
        print(
            "phase3 activation preflight could not establish trusted state",
            file=sys.stderr,
        )
        return 78
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if report["preflight_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
