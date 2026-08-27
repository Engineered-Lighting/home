#!/usr/bin/env python3
"""Restart-safe, split-phase Phase 3 identity activation runner.

The runner executes only fixed, separately reviewed boundaries. It advances
until it reaches a private human confirmation, completes, or contains a
failure. Its journal contains only random IDs, step codes, source revision,
and categorical outcomes. It never records people, HA users, relationships,
coordinates, utterances, or credentials.

There is deliberately no rollback to legacy semantic authority. Once the HA
writer fence is installed, failure handling stops Agent-facing services and
restores ordinary HA device control with the legacy store still fenced.
"""

from __future__ import annotations

import base64
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
from typing import Any, Callable, Mapping, Sequence
import uuid

import phase3_activation_sequencer as sequencer
import phase3_activation_source_plan as source_plan
import phase3_activation_preflight as activation_preflight
import phase3_migration_executor as migration_executor
import phase3_capture_legacy_identity_snapshot as ha_transport
from reviewed_identity_payload import (
    VerificationError,
    canonical_bytes,
    parse_canonical_json,
)


CONTRACT = "phase3-authoritative-split-activation-runner-e5ad-v1"
STATE_ROOT = Path("/srv/home-agent/private/phase3-activation")
STATE_PATH = STATE_ROOT / "runner-state-e5ad.json"
SOURCE_TRANSITION_ROOT = STATE_ROOT / "source-transitions"
LOCK_PATH = Path("/srv/home-agent/locks/phase3-runner.lock")
PRIVATE_IDENTITY_ROOT = Path("/srv/home-agent/private/phase3-identity")
ENVIRONMENT_PATH = Path("/srv/home-agent/config/home-agent.env")
SOURCE_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = SOURCE_ROOT / "stack/home-agent-compose.yml"
OPERATOR_ROOT = Path(__file__).resolve().parent
SIGNING_LAUNCHER = Path("/usr/local/sbin/home-agent-identity-signing")
CREDENTIAL_PROVISIONER = Path(
    "/usr/local/sbin/home-agent-provision-identity-signing-credentials"
)
LOCAL_BACKUP = Path("/usr/local/libexec/home-agent/local-backup.sh")
OFFHOST_BACKUP = OPERATOR_ROOT / "off_host_backup_writer.py"
RESTORE_DRILL = OPERATOR_ROOT / "isolated_restore_drill.sh"
ERASURE_RECEIPTS = OPERATOR_ROOT / "phase3_evidence_receipts.py"
MIGRATION_EXECUTOR = OPERATOR_ROOT / "phase3_migration_executor.py"
AUTHORITY_ADMISSION = OPERATOR_ROOT / "phase3_authority_admission.py"
AUTHORITY_CEREMONY = OPERATOR_ROOT / "phase3_identity_authority_ceremony.py"
PRIVACY_OBSERVER = OPERATOR_ROOT / "phase3_privacy_cutover_observer.py"
FINALIZER_DOCUMENT = PRIVATE_IDENTITY_ROOT / "identity-finalizer-document-e5y.json"
FINALIZER_RECEIPT = PRIVATE_IDENTITY_ROOT / "identity-signing-receipt-e5y.json"
IDENTITY_SIGNING_STATE = PRIVATE_IDENTITY_ROOT / "identity-signing-state-e5y.json"
REGISTRATION_CONTRACT = "identity-migration-registration-e5ak-v1"
EVIDENCE_CONTRACTS = {
    "record-freeze-evidence": "identity-writer-freeze-evidence-e5an-v1",
    "record-privacy-evidence": "identity-privacy-cutover-evidence-e5an-v1",
    "record-cutover-candidate": "identity-semantic-cutover-candidate-e5an-v1",
}
RETIREMENT_CONTRACT = "phase3-identity-finalization-retirement-e5am-v1"
RETIREMENT_REASON = "finalized_run_expired_before_registration"
RETIREMENT_RECEIPT_PREFIX = "identity-finalization-retirement-"
RETIREMENT_RECEIPT_SUFFIX = "-e5am.json"
# Every phase a reviewed packet can be sitting in when its run expires. The
# signing ceremony can only supersede an unsigned packet staged at the
# four-step await boundary, so at this boundary all three are equally stranded
# and equally safe to retire: the run is expired either way, and the database
# check below is what actually establishes that nothing was consumed.
RETIRABLE_PHASES = frozenset({"staged", "review_signed", "finalized"})
# `ha core stop` returns as soon as the supervisor accepts the request, not when
# Home Assistant has finished flushing. The legacy identity database can still
# carry a `-wal` sidecar for seconds afterwards, and the step 20 writer fence
# refuses exactly that. Wait for the database itself rather than trusting the
# stop call to mean quiescence.
HA_QUIESCE_TIMEOUT_SECONDS = 120
HA_QUIESCE_POLL_SECONDS = 3
# How fresh the measurement under already-signed evidence has to be for a
# resumed step 21 to re-emit it instead of parking for review. The privacy
# observer at step 22 refuses a freeze older than five minutes and the launcher
# and the record still have to run inside what is left.
WRITER_EVIDENCE_RESUME_SECONDS = 120
ADMISSION_RECOVERY_CONTRACT = "phase3-finalizer-admission-recovery-e5an-v1"
ADMISSION_RECOVERY_REASON = "admission_spent_on_unfinalizable_packet"
ADMISSION_RECOVERY_PREFIX = "identity-finalizer-admission-recovery-"
ADMISSION_RECOVERY_SUFFIX = "-e5an.json"
EDGE_RECEIPT = PRIVATE_IDENTITY_ROOT / "edge-privacy-policy-receipt-e5ac.json"
WRITER_OBSERVATION = PRIVATE_IDENTITY_ROOT / "writer-freeze-observation-e5z.json"
PRIVACY_OBSERVATION = PRIVATE_IDENTITY_ROOT / "privacy-cutover-observation-e5aa.json"
WRITER_FREEZE_EVIDENCE = (
    PRIVATE_IDENTITY_ROOT / "writer-freeze-evidence-e5z.json"
)
WRITER_FREEZE_RECEIPT = (
    PRIVATE_IDENTITY_ROOT / "writer-freeze-evidence-receipt-e5z.json"
)
PRIVACY_CUTOVER_EVIDENCE = (
    PRIVATE_IDENTITY_ROOT / "privacy-cutover-evidence-e5aa.json"
)
CUTOVER_PACKET = PRIVATE_IDENTITY_ROOT / "semantic-cutover-packet-e5ab.json"
CUTOVER_RECEIPT = PRIVATE_IDENTITY_ROOT / "semantic-cutover-packet-receipt-e5ab.json"
COMPLETION_RECEIPT = STATE_ROOT / "runner-completion-e5ad.json"
CREDENTIAL_RECEIPT = Path(
    "/srv/home-agent/config/phase3-identity-signing-credentials-e5ae.json"
)
SHADOW_AUTHORIZATION_RECEIPT = Path(
    "/srv/home-agent/config/phase3-shadow-authorization-e5ae.json"
)
KEY_SOURCE_RECEIPT = Path(
    "/srv/home-agent/config/phase3-signing-key-source-e5ae.json"
)
CREDENTIAL_TARGETS = tuple(
    Path("/etc/credstore.encrypted") / name
    for name in (
        "home-agent-identity-policy.cred",
        "home-agent-identity-commitment.cred",
        "home-agent-identity-review.cred",
        "home-agent-identity-finalization.cred",
        "home-agent-identity-writer-freeze-policy.cred",
        "home-agent-identity-writer-freeze.cred",
        "home-agent-identity-privacy-probe-policy.cred",
        "home-agent-identity-privacy-probe.cred",
        "home-agent-identity-semantic-cutover-policy.cred",
        "home-agent-identity-semantic-cutover.cred",
    )
)
SOURCE_REFRESH_FORBIDDEN_PATHS = (
    CREDENTIAL_RECEIPT,
    *CREDENTIAL_TARGETS,
    IDENTITY_SIGNING_STATE,
    FINALIZER_DOCUMENT,
    FINALIZER_RECEIPT,
    EDGE_RECEIPT,
    WRITER_OBSERVATION,
    PRIVACY_OBSERVATION,
    CUTOVER_PACKET,
    CUTOVER_RECEIPT,
    COMPLETION_RECEIPT,
)
SOURCE_REBIND_ROOT = STATE_ROOT / "source-rebinds"
REBIND_CONTRACT = "phase3-activation-source-rebind-e5ak-v1"
REBIND_RECEIPT_NAME = re.compile(r"^[0-9a-f]{40}-[0-9a-f]{40}\.json$")
REBIND_MAX_HOPS = 8
REBIND_FORBIDDEN_PATHS = (
    IDENTITY_SIGNING_STATE,
    FINALIZER_DOCUMENT,
    FINALIZER_RECEIPT,
    EDGE_RECEIPT,
    WRITER_OBSERVATION,
    PRIVACY_OBSERVATION,
    CUTOVER_PACKET,
    CUTOVER_RECEIPT,
    COMPLETION_RECEIPT,
)
REMOTE_EDGE_RECEIPT = "/config/.storage/home_agent_edge_privacy_policy_receipt.json"
REMOTE_IDENTITY_DB = "/config/extended_openai_conversation/identity.db"
REMOTE_FREEZE = (
    "/config/extended_openai_conversation/freeze_legacy_identity_semantics.py"
)
REMOTE_OBSERVER = (
    "/config/extended_openai_conversation/collect_legacy_identity_freeze_observation.py"
)
# The freeze observer imports the source-projection loader from here and hashes
# the module's bytes into its observation, so a missing or stale copy either
# fails the step outright or records a digest that does not describe the code
# that ran. Nothing deployed this directory, and the step runs with Home
# Assistant already stopped.
REMOTE_OPERATOR_ROOT = "/config/home-agent-operator"
REMOTE_OPERATOR_MODULE = f"{REMOTE_OPERATOR_ROOT}/migrate_legacy_identity.py"
OPERATOR_MODULE_SOURCE = OPERATOR_ROOT / "migrate_legacy_identity.py"
EOC_SOURCE_ROOT = SOURCE_ROOT / "ha-config" / "extended_openai_conversation"
# Every module step 20 executes on the Home Assistant host, paired with the
# pinned source it must equal. Presence was the only thing ever checked here,
# and presence is what a stale copy also satisfies: the freeze observer on the
# host was three revisions behind its pinned source -- still shelling out to
# `ha core info` for a run-state key this deployment does not return -- while a
# readiness audit recorded it as "present". Both halves of step 20 run with
# Home Assistant already stopped, so a mismatch has to fail before that.
REMOTE_IDENTITY_STORE = "/config/extended_openai_conversation/identity_store.py"
REMOTE_LEGACY_FENCE = (
    "/config/extended_openai_conversation/legacy_identity_fence.py"
)
# Both step 20 scripts import these two, first as a package-relative import and
# then as a bare module. Neither form resolves unless the file sits beside them
# on the Home Assistant host. Verifying only the scripts let the activation pass
# its own readiness check and then fail at the writer fence -- with Home
# Assistant already stopped, and the remote ImportError discarded by the
# transport.
REMOTE_HA_MODULES = (
    (OPERATOR_MODULE_SOURCE, REMOTE_OPERATOR_MODULE),
    (
        EOC_SOURCE_ROOT / "freeze_legacy_identity_semantics.py",
        REMOTE_FREEZE,
    ),
    (
        EOC_SOURCE_ROOT / "collect_legacy_identity_freeze_observation.py",
        REMOTE_OBSERVER,
    ),
    (
        EOC_SOURCE_ROOT / "identity_store.py",
        REMOTE_IDENTITY_STORE,
    ),
    (
        EOC_SOURCE_ROOT / "legacy_identity_fence.py",
        REMOTE_LEGACY_FENCE,
    ),
)
MAX_OUTPUT = 6 * 1024 * 1024
APPLICATION_SERVICES = (
    "core-api",
    "core-ingest",
    "core-worker",
    "bff",
    "edge-ingress",
)
REQUIRED_CONTAINER_STATES = {
    "home-agent-core-api-1": "healthy",
    "home-agent-core-ingest-1": "healthy",
    "home-agent-core-worker-1": "healthy",
    "home-agent-bff-1": "healthy",
    "home-agent-edge-ingress-1": "running",
}
STEPS = (
    "admit_source",
    "validate_pre_authorization_prerequisites",
    "authorize_shadow",
    "provision_signing_credentials",
    "await_reviewed_people_packet",
    "validate_live_prerequisites",
    "local_backup",
    "offhost_backup",
    "restore_drill",
    "erasure_current",
    "arm_initial_permit",
    "stop_agent_services",
    "migrate_finalizer",
    "provision_cutover_roles",
    "migrate_current_authority",
    "provision_binding_kernel",
    "commit_finalizer",
    "capture_edge_privacy_receipt",
    "stop_home_assistant",
    "freeze_legacy_writer",
    "sign_writer_evidence",
    "sign_privacy_evidence",
    "commit_semantic_cutover",
    "restart_home_assistant",
    "migrate_authenticated_binding",
    "grant_and_start_binding_stage",
    "await_authenticated_binding",
    "stop_binding_stage",
    "rearm_parent_permit",
    "migrate_parent_authority",
    "provision_parent_kernel",
    "migrate_parent_status",
    "grant_and_start_parent_stage",
    "await_parent_confirmation",
    "seal_completion",
)
PAUSE_STEPS = frozenset(
    {
        "await_reviewed_people_packet",
        "validate_live_prerequisites",
        "commit_finalizer",
        "await_authenticated_binding",
        "await_parent_confirmation",
    }
)
OPERATION_ID_STEPS = frozenset({"authorize_shadow", "commit_finalizer"})

# Every step downstream of ``arm_initial_permit`` runs inside a grant-permit
# window. The permit carries a four-hour freshness bound, but the windows hold
# two private human confirmations (``commit_finalizer``,
# ``await_authenticated_binding``) that have no bounded duration, so a run can
# legitimately outlive its permit at any of these steps.
RECOVERABLE_PERMIT_STEPS = frozenset(STEPS[STEPS.index("stop_agent_services") :])

# The revision each migrating step lands on. Ordered as the steps run, so the
# last completed entry is the revision the database must currently be at.
STEP_REVISIONS = (
    ("migrate_finalizer", "0013_identity_finalizer_e3"),
    ("migrate_current_authority", "0015_current_authority_e5a"),
    ("migrate_authenticated_binding", "0017_authenticated_binding_e5c"),
    ("migrate_parent_authority", "0018_parent_relationship_e5d"),
    ("migrate_parent_status", "0021_parent_status_e5h"),
)
PRE_ACTIVATION_REVISION = "0006a_worker_lease_arbitration"


def expected_revision(completed_steps: Sequence[str]) -> str:
    """Return the exact revision the journal implies the database is at.

    ``validate_state`` guarantees ``completed_steps`` is a prefix of ``STEPS``,
    so scanning in step order leaves the most recent completed migration.
    """

    revision = PRE_ACTIVATION_REVISION
    completed = frozenset(completed_steps)
    for step, target in STEP_REVISIONS:
        if step in completed:
            revision = target
    return revision


class ActivationRunnerError(RuntimeError):
    """The split activation runner failed closed."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code if code in migration_executor.DIAGNOSTIC_CODES else None


class ActivationPause(RuntimeError):
    """The runner is safely waiting for a private human confirmation."""


def _error_code(error: BaseException) -> str:
    """Return the categorical journal code for a failed step.

    Subprocess failures carry a code from a closed vocabulary so the journal
    can distinguish a non-zero exit from oversized or NUL-bearing output. Every
    other failure keeps the previous exception-name behaviour. Free text never
    enters the journal.
    """

    code = getattr(error, "code", None)
    if isinstance(code, str) and code in migration_executor.DIAGNOSTIC_CODES:
        return code
    return type(error).__name__.lower()[:96]


def uuid7() -> uuid.UUID:
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_state(commit: str) -> dict[str, Any]:
    if source_plan.COMMIT_SHA.fullmatch(commit) is None:
        raise ActivationRunnerError("activation source commit is invalid")
    return {
        "contract": CONTRACT,
        "runner_id": str(uuid7()),
        "source_commit": commit,
        "status": "active",
        "next_step": STEPS[0],
        "completed_steps": [],
        "attempt_counts": {},
        "operation_ids": {},
        "pause_code": "none",
        "last_error_code": "none",
        "updated_at": _utc(),
    }


def validate_state(value: Any) -> dict[str, Any]:
    keys = {
        "contract",
        "runner_id",
        "source_commit",
        "status",
        "next_step",
        "completed_steps",
        "attempt_counts",
        "operation_ids",
        "pause_code",
        "last_error_code",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ActivationRunnerError("activation journal shape is invalid")
    state = dict(value)
    try:
        runner_id = uuid.UUID(str(state["runner_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ActivationRunnerError(
            "activation runner identifier is invalid"
        ) from error
    if runner_id.version != 7 or str(runner_id) != state["runner_id"]:
        raise ActivationRunnerError("activation runner identifier is invalid")
    if source_plan.COMMIT_SHA.fullmatch(str(state["source_commit"])) is None:
        raise ActivationRunnerError("activation journal source is invalid")
    completed = state["completed_steps"]
    if (
        not isinstance(completed, list)
        or completed != list(STEPS[: len(completed)])
        or len(completed) > len(STEPS)
    ):
        raise ActivationRunnerError("activation journal sequence is invalid")
    expected_next = "none" if len(completed) == len(STEPS) else STEPS[len(completed)]
    if state["next_step"] != expected_next:
        raise ActivationRunnerError("activation journal cursor is invalid")
    if state["status"] not in {
        "active",
        "running",
        "paused",
        "contained",
        "complete",
    }:
        raise ActivationRunnerError("activation journal status is invalid")
    attempts = state["attempt_counts"]
    if not isinstance(attempts, Mapping) or any(
        key not in STEPS or type(count) is not int or count < 1
        for key, count in attempts.items()
    ):
        raise ActivationRunnerError("activation attempt journal is invalid")
    operations = state["operation_ids"]
    if not isinstance(operations, Mapping) or set(operations) - OPERATION_ID_STEPS:
        raise ActivationRunnerError("activation operation journal is invalid")
    for key, raw_id in operations.items():
        try:
            parsed = uuid.UUID(str(raw_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ActivationRunnerError("activation operation ID is invalid") from error
        if (
            parsed.version != 7
            or str(parsed) != raw_id
            or key not in completed + [expected_next]
        ):
            raise ActivationRunnerError("activation operation ID is invalid")
    for key in ("pause_code", "last_error_code"):
        value_text = state[key]
        if (
            not isinstance(value_text, str)
            or not value_text
            or len(value_text) > 96
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz_0123456789"
                for character in value_text
            )
        ):
            raise ActivationRunnerError("activation journal code is invalid")
    try:
        parsed_time = datetime.fromisoformat(str(state["updated_at"])[:-1] + "+00:00")
    except ValueError as error:
        raise ActivationRunnerError(
            "activation journal timestamp is invalid"
        ) from error
    if (
        not str(state["updated_at"]).endswith("Z")
        or parsed_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != state["updated_at"]
    ):
        raise ActivationRunnerError("activation journal timestamp is invalid")
    if state["status"] == "complete" and expected_next != "none":
        raise ActivationRunnerError("activation completion journal is invalid")
    return state


def completion_receipt(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract": "phase3-activation-completion-receipt-e5ad-v1",
        "runner_id": state["runner_id"],
        "source_commit": state["source_commit"],
        "completed_step_count": len(STEPS),
        "step_set_sha256": hashlib.sha256(canonical_bytes(list(STEPS))).hexdigest(),
        "status": "complete",
    }


def preflight_backup_label(report: Mapping[str, Any]) -> str | None:
    """Return the newest completed full backup label from a preflight report.

    The label lives inside the report's ``backup`` mapping, matching the
    off-host writer's reader and the preflight's own contract. Reading it
    from the report root silently yields ``None`` for every well-formed
    report, so the lookup is shared rather than repeated per call site.
    """

    backup = report.get("backup") if isinstance(report, Mapping) else None
    label = (
        backup.get("latest_full_backup_label") if isinstance(backup, Mapping) else None
    )
    return label if isinstance(label, str) else None


def validate_credential_receipt_shape(credential: Any) -> None:
    """Validate the immutable credential receipt without any live-source pin."""

    expected_keys = {
        "contract",
        "operation_id",
        "source_commit",
        "release_manifest_digest",
        "migration_tool_bundle_digest",
        "core_oci_manifest_digest",
        "core_schema_digest",
        "core_capability_digest",
        "source_projection_contract_digest",
        "policy_version",
        "policy_digest",
        "shadow_authorization_id",
        "review_key_fingerprint",
        "finalization_key_fingerprint",
        "writer_freeze_key_fingerprint",
        "privacy_probe_key_fingerprint",
        "semantic_cutover_key_fingerprint",
        "commitment_key_fingerprint",
        "commitment_key_epoch",
        "credential_count",
        "status",
        "key_source",
    }
    digest_keys = {
        key
        for key in expected_keys
        if key.endswith("_digest") or key.endswith("_fingerprint")
    }
    if (
        not isinstance(credential, Mapping)
        or set(credential) != expected_keys
        or credential.get("contract") != "phase3-identity-credential-receipt-e5ae-v1"
        or source_plan.COMMIT_SHA.fullmatch(str(credential.get("source_commit", "")))
        is None
        or credential.get("credential_count") != len(CREDENTIAL_TARGETS)
        or credential.get("commitment_key_epoch") != 1
        or credential.get("status") != "provisioned"
        or credential.get("key_source") not in {"host", "tpm2", "host+tpm2"}
        or any(
            not isinstance(credential.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", credential[key]) is None
            for key in digest_keys
        )
    ):
        raise ActivationRunnerError("identity credential receipt is invalid")
    try:
        operation_id = uuid.UUID(str(credential["operation_id"]))
        shadow_id = uuid.UUID(str(credential["shadow_authorization_id"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ActivationRunnerError("identity credential receipt is invalid") from error
    fingerprints = {
        credential[key] for key in digest_keys if key.endswith("_fingerprint")
    }
    if (
        operation_id.version != 7
        or shadow_id.version not in {4, 7}
        or len(fingerprints) != 6
    ):
        raise ActivationRunnerError("identity credential receipt is invalid")


def validate_rebind_receipt(
    receipt: Any,
    *,
    name: str,
    runner_id: str,
    credential_receipt_sha256: str,
) -> dict[str, Any]:
    """Validate one immutable rebind receipt against its filename and owner."""

    expected_keys = {
        "contract",
        "runner_id",
        "from_source_commit",
        "to_source_commit",
        "from_source_pack_digest",
        "to_source_pack_digest",
        "credential_receipt_sha256",
        "credential_source_commit",
        "completed_step_count",
        "next_step",
        "recorded_at",
    }
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != expected_keys
        or receipt.get("contract") != REBIND_CONTRACT
        or receipt.get("runner_id") != runner_id
        or receipt.get("completed_step_count") != 4
        or receipt.get("next_step") != "await_reviewed_people_packet"
        or receipt.get("credential_receipt_sha256") != credential_receipt_sha256
        or not isinstance(receipt.get("recorded_at"), str)
    ):
        raise ActivationRunnerError("activation source rebind receipt is unsafe")
    from_commit = str(receipt.get("from_source_commit", ""))
    to_commit = str(receipt.get("to_source_commit", ""))
    if (
        source_plan.COMMIT_SHA.fullmatch(from_commit) is None
        or source_plan.COMMIT_SHA.fullmatch(to_commit) is None
        or from_commit == to_commit
        or name != f"{from_commit}-{to_commit}.json"
        or source_plan.COMMIT_SHA.fullmatch(
            str(receipt.get("credential_source_commit", ""))
        )
        is None
        or any(
            not isinstance(receipt.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt[key]) is None
            for key in ("from_source_pack_digest", "to_source_pack_digest")
        )
    ):
        raise ActivationRunnerError("activation source rebind receipt is unsafe")
    return dict(receipt)


def credential_source_binding_valid(
    credential: Mapping[str, Any],
    state: Mapping[str, Any],
    report: Mapping[str, Any],
    rebind_receipts: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Accept the credential receipt directly or through the rebind chain."""

    origin = credential.get("source_commit")
    tail = state.get("source_commit")
    live_commit = report.get("current_commit")
    live_digest = report.get("source_pack_digest")
    if not isinstance(live_digest, str) or live_commit != tail:
        return False
    if origin == tail:
        return credential.get("release_manifest_digest") == live_digest
    commit = origin
    digest = credential.get("release_manifest_digest")
    visited = {commit}
    for _ in range(REBIND_MAX_HOPS):
        receipt = rebind_receipts.get(commit)
        if (
            receipt is None
            or receipt.get("credential_source_commit") != origin
            or receipt.get("from_source_pack_digest") != digest
        ):
            return False
        commit = receipt.get("to_source_commit")
        digest = receipt.get("to_source_pack_digest")
        if commit in visited:
            return False
        visited.add(commit)
        if commit == tail:
            return digest == live_digest
    return False


def read_rebind_receipts(
    runner_id: str, credential_receipt_sha256: str
) -> dict[str, dict[str, Any]]:
    """Load this activation's append-only rebind chain.

    Receipts recorded by a different runner identifier are earlier-activation
    lineage: they stay on disk untouched and are excluded from the chain, so
    a fresh activation is never poisoned by an abandoned one. Ambiguity and
    tampering within this activation's own chain remain fail-closed.
    """

    if not SOURCE_REBIND_ROOT.exists() and not SOURCE_REBIND_ROOT.is_symlink():
        return {}
    details = SOURCE_REBIND_ROOT.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ActivationRunnerError("activation source rebind directory is unsafe")
    receipts: dict[str, dict[str, Any]] = {}
    for item in sorted(SOURCE_REBIND_ROOT.iterdir()):
        name = item.name
        if name.startswith("."):
            continue
        if REBIND_RECEIPT_NAME.fullmatch(name) is None:
            raise ActivationRunnerError("activation source rebind receipt is unsafe")
        try:
            entry_details = item.lstat()
            value = parse_canonical_json(item.read_bytes(), maximum=4096)
        except (OSError, VerificationError) as error:
            raise ActivationRunnerError(
                "activation source rebind receipt is unsafe"
            ) from error
        if (
            not stat.S_ISREG(entry_details.st_mode)
            or entry_details.st_uid != 0
            or entry_details.st_gid != 0
            or stat.S_IMODE(entry_details.st_mode) != 0o600
            or entry_details.st_nlink != 1
        ):
            raise ActivationRunnerError("activation source rebind receipt is unsafe")
        if isinstance(value, Mapping) and value.get("runner_id") != runner_id:
            continue
        receipt = validate_rebind_receipt(
            value,
            name=name,
            runner_id=runner_id,
            credential_receipt_sha256=credential_receipt_sha256,
        )
        from_commit = receipt["from_source_commit"]
        if from_commit in receipts:
            raise ActivationRunnerError(
                "activation source rebind chain is ambiguous"
            )
        receipts[from_commit] = receipt
    return receipts


class StateStore:
    def prepare(self) -> None:
        STATE_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
        details = STATE_ROOT.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise ActivationRunnerError("activation journal directory is unsafe")

    def load(self) -> dict[str, Any] | None:
        self.prepare()
        if not STATE_PATH.exists() and not STATE_PATH.is_symlink():
            if COMPLETION_RECEIPT.exists() or COMPLETION_RECEIPT.is_symlink():
                raise ActivationRunnerError("activation completion state is unsafe")
            return None
        try:
            details = STATE_PATH.lstat()
            raw = STATE_PATH.read_bytes()
            value = parse_canonical_json(raw, maximum=256 * 1024)
        except (OSError, VerificationError) as error:
            raise ActivationRunnerError("activation journal is unreadable") from error
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise ActivationRunnerError("activation journal is unsafe")
        state = validate_state(value)
        completion_exists = (
            COMPLETION_RECEIPT.exists() or COMPLETION_RECEIPT.is_symlink()
        )
        if completion_exists:
            try:
                completion_details = COMPLETION_RECEIPT.lstat()
                completion_raw = COMPLETION_RECEIPT.read_bytes()
            except OSError as error:
                raise ActivationRunnerError(
                    "activation completion state is unsafe"
                ) from error
            expected = canonical_bytes(completion_receipt(state)) + b"\n"
            if (
                not stat.S_ISREG(completion_details.st_mode)
                or completion_details.st_uid != 0
                or completion_details.st_gid != 0
                or stat.S_IMODE(completion_details.st_mode) != 0o600
                or completion_details.st_nlink != 1
                or not secrets.compare_digest(completion_raw, expected)
                or state["next_step"] not in {"seal_completion", "none"}
            ):
                raise ActivationRunnerError("activation completion state is unsafe")
        elif state["status"] == "complete":
            raise ActivationRunnerError("activation completion receipt is missing")
        return state

    def save(self, state: Mapping[str, Any]) -> None:
        self.prepare()
        raw = canonical_bytes(validate_state(state))
        temporary = STATE_PATH.with_name(
            f".{STATE_PATH.name}.new.{secrets.token_hex(12)}"
        )
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
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o600)
            os.replace(temporary, STATE_PATH)
            directory = os.open(STATE_ROOT, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def record_source_transition(
        self,
        state: Mapping[str, Any],
        new_commit: str,
    ) -> None:
        old_commit = str(state["source_commit"])
        if (
            source_plan.COMMIT_SHA.fullmatch(old_commit) is None
            or source_plan.COMMIT_SHA.fullmatch(new_commit) is None
            or old_commit == new_commit
        ):
            raise ActivationRunnerError("activation source transition is invalid")
        SOURCE_TRANSITION_ROOT.mkdir(parents=False, mode=0o700, exist_ok=True)
        details = SOURCE_TRANSITION_ROOT.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise ActivationRunnerError("activation source transition directory is unsafe")
        path = SOURCE_TRANSITION_ROOT / f"{old_commit}-{new_commit}.json"
        expected_static = {
            "contract": "phase3-activation-source-transition-e5af-v1",
            "runner_id": state["runner_id"],
            "from_source_commit": old_commit,
            "to_source_commit": new_commit,
            "completed_step_count": 3,
            "next_step": "provision_signing_credentials",
        }
        if path.exists() or path.is_symlink():
            try:
                existing_details = path.lstat()
                existing = parse_canonical_json(path.read_bytes(), maximum=4096)
            except (OSError, VerificationError) as error:
                raise ActivationRunnerError(
                    "activation source transition receipt is unsafe"
                ) from error
            if (
                not stat.S_ISREG(existing_details.st_mode)
                or existing_details.st_uid != 0
                or existing_details.st_gid != 0
                or stat.S_IMODE(existing_details.st_mode) != 0o600
                or existing_details.st_nlink != 1
                or not isinstance(existing, Mapping)
                or set(existing) != set(expected_static) | {"recorded_at"}
                or any(existing.get(key) != value for key, value in expected_static.items())
                or not isinstance(existing.get("recorded_at"), str)
            ):
                raise ActivationRunnerError(
                    "activation source transition receipt is unsafe"
                )
            return
        receipt = {**expected_static, "recorded_at": _utc()}
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
                stream.write(canonical_bytes(receipt))
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory = os.open(SOURCE_TRANSITION_ROOT, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def record_source_rebind(
        self,
        state: Mapping[str, Any],
        new_commit: str,
        *,
        from_source_pack_digest: str,
        to_source_pack_digest: str,
        credential_receipt_sha256: str,
        credential_source_commit: str,
    ) -> None:
        old_commit = str(state["source_commit"])
        if (
            source_plan.COMMIT_SHA.fullmatch(old_commit) is None
            or source_plan.COMMIT_SHA.fullmatch(new_commit) is None
            or old_commit == new_commit
        ):
            raise ActivationRunnerError("activation source rebind is invalid")
        SOURCE_REBIND_ROOT.mkdir(parents=False, mode=0o700, exist_ok=True)
        details = SOURCE_REBIND_ROOT.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise ActivationRunnerError(
                "activation source rebind directory is unsafe"
            )
        path = SOURCE_REBIND_ROOT / f"{old_commit}-{new_commit}.json"
        for item in SOURCE_REBIND_ROOT.iterdir():
            if (
                not item.name.startswith(".")
                and item.name.startswith(f"{old_commit}-")
                and item.name != path.name
            ):
                raise ActivationRunnerError(
                    "activation source rebind chain is ambiguous"
                )
        expected_static = {
            "contract": REBIND_CONTRACT,
            "runner_id": state["runner_id"],
            "from_source_commit": old_commit,
            "to_source_commit": new_commit,
            "from_source_pack_digest": from_source_pack_digest,
            "to_source_pack_digest": to_source_pack_digest,
            "credential_receipt_sha256": credential_receipt_sha256,
            "credential_source_commit": credential_source_commit,
            "completed_step_count": 4,
            "next_step": "await_reviewed_people_packet",
        }
        if path.exists() or path.is_symlink():
            try:
                existing_details = path.lstat()
                existing = parse_canonical_json(path.read_bytes(), maximum=4096)
            except (OSError, VerificationError) as error:
                raise ActivationRunnerError(
                    "activation source rebind receipt is unsafe"
                ) from error
            if (
                not stat.S_ISREG(existing_details.st_mode)
                or existing_details.st_uid != 0
                or existing_details.st_gid != 0
                or stat.S_IMODE(existing_details.st_mode) != 0o600
                or existing_details.st_nlink != 1
                or not isinstance(existing, Mapping)
                or set(existing) != set(expected_static) | {"recorded_at"}
                or any(
                    existing.get(key) != value
                    for key, value in expected_static.items()
                )
                or not isinstance(existing.get("recorded_at"), str)
            ):
                raise ActivationRunnerError(
                    "activation source rebind receipt is unsafe"
                )
            return
        receipt = {**expected_static, "recorded_at": _utc()}
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
                stream.write(canonical_bytes(receipt))
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory = os.open(SOURCE_REBIND_ROOT, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class Backend:
    """Fixed production actions; every method is independently fail-closed."""

    def __init__(self) -> None:
        self.backup_label: str | None = None

    @staticmethod
    def _run(
        command: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int = 900,
        accepted: frozenset[int] = frozenset({0}),
        diagnostic: bool = False,
    ) -> bytes:
        try:
            result = subprocess.run(
                list(command),
                cwd=SOURCE_ROOT,
                check=False,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if diagnostic else subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ActivationRunnerError(
                "activation subprocess failed", code="timeout"
            ) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise ActivationRunnerError(
                "activation subprocess failed", code="spawn_failed"
            ) from error
        if result.returncode not in accepted:
            code = "exit_nonzero"
        elif len(result.stdout) > MAX_OUTPUT:
            code = "stdout_oversize"
        elif b"\0" in result.stdout:
            code = "stdout_nul"
        else:
            return result.stdout
        migration_executor.report_diagnostic(
            command, result.stderr, enabled=diagnostic
        )
        raise ActivationRunnerError("activation subprocess failed", code=code)

    @classmethod
    def _json(cls, command: Sequence[str], **kwargs: Any) -> Mapping[str, Any]:
        raw = cls._run(command, **kwargs)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ActivationRunnerError(
                "activation subprocess result is invalid"
            ) from error
        if not isinstance(value, Mapping):
            raise ActivationRunnerError("activation subprocess result is invalid")
        return value

    @staticmethod
    def _run_interactive(command: Sequence[str], *, timeout: int) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ActivationPause("awaiting_private_finalization_tty")
        try:
            result = subprocess.run(
                list(command),
                cwd=SOURCE_ROOT,
                check=False,
                timeout=timeout,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ActivationRunnerError(
                "private finalization command failed"
            ) from error
        if result.returncode != 0:
            raise ActivationRunnerError("private finalization command failed")

    @staticmethod
    def _compose(*arguments: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(ENVIRONMENT_PATH),
            "-f",
            str(COMPOSE_PATH),
            "--profile",
            "operator",
            *arguments,
        ]

    @staticmethod
    def _atomic_private(path: Path, raw: bytes) -> None:
        if not raw or len(raw) > MAX_OUTPUT or b"\0" in raw:
            raise ActivationRunnerError("activation private artifact is unsafe")
        normalized = raw.rstrip(b"\n") + b"\n"
        if path.exists() or path.is_symlink():
            try:
                details = path.lstat()
                existing = path.read_bytes()
            except OSError as error:
                raise ActivationRunnerError(
                    "activation private artifact is unsafe"
                ) from error
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != 0
                or details.st_gid != 0
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
                or not secrets.compare_digest(existing, normalized)
            ):
                raise ActivationRunnerError(
                    "activation private artifact already differs"
                )
            return
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
                stream.write(normalized)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temporary, 0, 0)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _private_document(path: Path) -> bytes:
        try:
            details = path.lstat()
            raw = path.read_bytes().rstrip(b"\n")
            parse_canonical_json(raw, maximum=MAX_OUTPUT)
        except (OSError, VerificationError) as error:
            raise ActivationRunnerError(
                "activation private document is invalid"
            ) from error
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) not in {0o400, 0o440, 0o600}
            or details.st_nlink != 1
        ):
            raise ActivationRunnerError("activation private document is unsafe")
        return raw

    @staticmethod
    def _require_root_executable(path: Path) -> None:
        try:
            details = path.lstat()
        except OSError as error:
            raise ActivationRunnerError(
                "activation executable is unavailable"
            ) from error
        mode = stat.S_IMODE(details.st_mode)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or details.st_nlink != 1
            or mode & 0o022
            or not mode & stat.S_IXUSR
        ):
            raise ActivationRunnerError("activation executable is unsafe")

    @staticmethod
    def _request(contract: str, admission_id: str, document: bytes) -> bytes:
        return canonical_bytes(
            {
                "contract": contract,
                "admission_id": admission_id,
                "document_b64": base64.b64encode(document).decode("ascii"),
            }
        )

    @staticmethod
    def _registration_manifest(raw_state: bytes) -> tuple[str, bytes]:
        """Return the run id and the manifest the 0008 kernel registers.

        The reviewed bundle is assembled inside the signing sandbox by
        reviewed_identity_packet_compiler.assemble_reviewed_bundle, whose bytes
        are sealed into the credential policy and cannot be changed. This
        rebuilds the manifest half of that bundle from the same private state,
        so the manifest that is registered is the one the review signature
        covers. The projections half is deliberately not sent: the registration
        kernel accepts exactly {run, source_items, decisions}.
        """

        state = parse_canonical_json(raw_state, maximum=MAX_OUTPUT)
        if not isinstance(state, Mapping):
            raise ActivationRunnerError("identity signing state is invalid")
        packet = state.get("unsigned_packet")
        signature = state.get("review_signature")
        if (
            not isinstance(packet, Mapping)
            or not isinstance(signature, str)
            or len(signature) != 128
            or any(character not in "0123456789abcdef" for character in signature)
        ):
            raise ActivationRunnerError("identity signing state is invalid")
        run = packet.get("unsigned_run")
        sources = packet.get("source_items")
        decisions = packet.get("decisions")
        if (
            not isinstance(run, Mapping)
            or not isinstance(sources, list)
            or not isinstance(decisions, list)
            or not sources
            or not decisions
            or "review_signature" in run
        ):
            raise ActivationRunnerError("identity signing state is invalid")
        run_id = run.get("run_id")
        if not isinstance(run_id, str):
            raise ActivationRunnerError("identity signing state is invalid")
        signed_run = dict(run)
        signed_run["review_signature"] = signature
        return run_id, canonical_bytes(
            {
                "run": signed_run,
                "source_items": sources,
                "decisions": decisions,
            }
        )

    def _register_migration_run(self) -> str:
        """Register the reviewed manifest immediately before it is admitted.

        commit_finalizer copies its provenance out of the reviewed run row, so
        the row has to exist before the admission is written. This is folded
        into the same handler rather than added as a step: the journal requires
        next_step to equal STEPS[len(completed_steps)], so inserting a step
        would make the live journal unloadable with no repair path.

        Registration is one-shot for the life of the database. There is exactly
        one record_only -> shadow authorization, exactly one run per
        authorization, and no role holds DELETE on the runs table. Registering
        and then failing to finalize in the same window cannot be undone, which
        is why the two happen back to back here with no human step between
        them. Re-running is safe: an identical manifest replays and the kernel
        returns the same run id.
        """

        run_id, manifest = self._registration_manifest(
            self._private_document(IDENTITY_SIGNING_STATE)
        )
        request = canonical_bytes(
            {
                "contract": REGISTRATION_CONTRACT,
                "run_id": run_id,
                "manifest_b64": base64.b64encode(manifest).decode("ascii"),
            }
        )
        result = self._json(
            [sys.executable, str(AUTHORITY_CEREMONY), "register"],
            input_bytes=request,
            timeout=300,
        )
        if result.get("result_id") != run_id:
            raise ActivationRunnerError("reviewed migration run was not registered")
        return run_id

    def _probe(self, name: str) -> Mapping[str, Any]:
        return self._json(
            self._compose(
                "run",
                "--rm",
                "--no-deps",
                "migrate",
                "phase3-activation-probe",
                name,
            ),
            timeout=180,
        )

    def _migrate(self, command: str, target: str) -> None:
        try:
            self._run(
                self._compose(
                    *migration_executor.revision_guard_arguments(target)
                ),
                timeout=180,
            )
            return
        except ActivationRunnerError:
            pass
        self._json(
            [sys.executable, str(MIGRATION_EXECUTOR), command],
            timeout=1200,
            diagnostic=True,
        )

    @staticmethod
    def _environment_body(text: str, revision: str) -> str:
        """Return the rewritten deployment environment for one revision.

        Kept separate from the file handling so the substitution rules are
        testable without a root-owned file.
        """

        replacements = {
            # The image entrypoint reads this one when it runs migrations.
            "HOME_AGENT_EXPECTED_DB_REVISION": revision,
            "HOME_AGENT_ROLLOUT_MODE": "shadow",
            # Core itself reads this one. Without it the agent services keep the
            # previous pin and fail closed against the migrated database.
            "HOME_AGENT_READINESS_MIGRATION": revision,
        }
        # Keys this release introduced are written whether or not the deployed
        # environment already declares them. Requiring them to pre-exist would
        # abort the first rewrite after the upgrade, and every rewrite at or
        # after stop_home_assistant is contained forward-only, which would
        # strand the ceremony with the Agent services stopped.
        introduced = frozenset({"HOME_AGENT_READINESS_MIGRATION"})
        found: set[str] = set()
        lines: list[str] = []
        for line in text.splitlines():
            name = line.split("=", 1)[0] if "=" in line else ""
            if name in replacements:
                if name in found:
                    raise ActivationRunnerError("activation environment is ambiguous")
                found.add(name)
                lines.append(f"{name}={replacements[name]}")
            else:
                lines.append(line)
        if not (set(replacements) - introduced) <= found:
            raise ActivationRunnerError("activation environment is incomplete")
        for name in sorted(introduced - found):
            lines.append(f"{name}={replacements[name]}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _rewrite_environment(revision: str) -> None:
        try:
            details = ENVIRONMENT_PATH.lstat()
            text = ENVIRONMENT_PATH.read_text(encoding="utf-8")
        except OSError as error:
            raise ActivationRunnerError(
                "activation environment is unavailable"
            ) from error
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise ActivationRunnerError("activation environment is unsafe")
        raw = Backend._environment_body(text, revision).encode("utf-8")
        temporary = ENVIRONMENT_PATH.with_name(
            f".{ENVIRONMENT_PATH.name}.new.{secrets.token_hex(12)}"
        )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temporary, 0, 0)
            os.chmod(temporary, stat.S_IMODE(details.st_mode))
            os.replace(temporary, ENVIRONMENT_PATH)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _stop_agents(self) -> None:
        self._run(
            self._compose("stop", *APPLICATION_SERVICES),
            timeout=180,
            diagnostic=True,
        )

    def _start_agents(self, revision: str) -> None:
        self._rewrite_environment(revision)
        self._run(
            self._compose("up", "-d", "--no-deps", *APPLICATION_SERVICES),
            timeout=300,
            diagnostic=True,
        )
        self._wait_agents_ready()

    def _wait_agents_ready(self) -> None:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            ready = True
            for name, required in REQUIRED_CONTAINER_STATES.items():
                try:
                    state = self._json(
                        ["docker", "inspect", "--format={{json .State}}", name],
                        timeout=15,
                    )
                except ActivationRunnerError:
                    ready = False
                    break
                if required == "healthy":
                    health = state.get("Health")
                    actual = (
                        health.get("Status") if isinstance(health, Mapping) else None
                    )
                else:
                    actual = state.get("Status")
                if actual != required:
                    ready = False
                    break
            if ready:
                return
            time.sleep(3)
        raise ActivationRunnerError("activation services did not become ready")

    def _apply_grants(self) -> None:
        self._run(
            self._compose("run", "--rm", "--no-deps", "grant-phase3-activation"),
            timeout=300,
            diagnostic=True,
        )

    def perform(self, step: str, state: Mapping[str, Any]) -> None:
        actions: dict[str, Callable[[Mapping[str, Any]], None]] = {
            "admit_source": self._admit_source,
            "validate_pre_authorization_prerequisites": (
                self._validate_pre_authorization_prerequisites
            ),
            "authorize_shadow": self._authorize_shadow,
            "provision_signing_credentials": self._provision_signing_credentials,
            "await_reviewed_people_packet": self._await_reviewed_packet,
            "validate_live_prerequisites": self._validate_live_prerequisites,
            "local_backup": self._local_backup,
            "offhost_backup": self._offhost_backup,
            "restore_drill": self._restore_drill,
            "erasure_current": self._erasure_current,
            "arm_initial_permit": self._arm_initial_permit,
            "stop_agent_services": lambda _state: self._stop_agents(),
            "migrate_finalizer": lambda _state: self._migrate(
                "migrate-finalizer", "0013_identity_finalizer_e3"
            ),
            "provision_cutover_roles": lambda _state: self._run(
                self._compose(
                    "run", "--rm", "--no-deps", "provision-identity-cutover-roles"
                ),
                timeout=300,
            ),
            "migrate_current_authority": lambda _state: self._migrate(
                "migrate-current-authority", "0015_current_authority_e5a"
            ),
            "provision_binding_kernel": lambda _state: self._run(
                self._compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "provision-identity-binding-kernel-role",
                ),
                timeout=300,
            ),
            "commit_finalizer": self._commit_finalizer,
            "capture_edge_privacy_receipt": self._capture_edge_receipt,
            "stop_home_assistant": lambda _state: self._stop_ha(),
            "freeze_legacy_writer": self._freeze_legacy_writer,
            "sign_writer_evidence": self._sign_writer_evidence,
            "sign_privacy_evidence": self._sign_privacy_evidence,
            "commit_semantic_cutover": self._commit_semantic_cutover,
            "restart_home_assistant": lambda _state: self._restart_ha(),
            "migrate_authenticated_binding": lambda _state: self._migrate(
                "migrate-authenticated-binding", "0017_authenticated_binding_e5c"
            ),
            "grant_and_start_binding_stage": lambda _state: self._grant_start(
                "0017_authenticated_binding_e5c"
            ),
            "await_authenticated_binding": self._await_binding,
            "stop_binding_stage": lambda _state: self._stop_agents(),
            "rearm_parent_permit": self._rearm_parent_permit,
            "migrate_parent_authority": lambda _state: self._migrate(
                "migrate-parent-authority", "0018_parent_relationship_e5d"
            ),
            "provision_parent_kernel": lambda _state: self._run(
                self._compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "provision-parent-relationship-kernel-role",
                ),
                timeout=300,
            ),
            "migrate_parent_status": lambda _state: self._migrate(
                "migrate-parent-status", "0021_parent_status_e5h"
            ),
            "grant_and_start_parent_stage": lambda _state: self._grant_start(
                "0021_parent_status_e5h"
            ),
            "await_parent_confirmation": self._await_parents,
            "seal_completion": self._seal_completion,
        }
        try:
            action = actions[step]
        except KeyError as error:
            raise ActivationRunnerError("activation step is unsupported") from error
        action(state)

    def _admit_source(self, _state: Mapping[str, Any]) -> None:
        self._json(
            [
                sys.executable,
                str(OPERATOR_ROOT / "phase3_activation_sequencer.py"),
                "admit-source",
            ],
            timeout=120,
        )

    @staticmethod
    def _require_remote_operator_module() -> None:
        """Every module step 20 runs on the HA host must be byte-identical.

        Existence alone is not enough, and never was. The observation embeds a
        digest of the loader, so a stale copy describes code that did not run;
        and a stale observer is worse, because it fails outright at a point
        where Home Assistant is already stopped.

        These files are pinned activation paths, so the host *checkout* is
        already guaranteed to match. Nothing copies them onward to the Home
        Assistant host, which is the gap this closes.
        """

        for source, remote in REMOTE_HA_MODULES:
            try:
                expected = hashlib.sha256(source.read_bytes()).hexdigest()
            except OSError as error:
                raise ActivationRunnerError(
                    "activation operator module is unavailable"
                ) from error
            raw = ha_transport._remote("sha256sum", remote, timeout=30)
            digest = raw.decode("ascii", errors="replace").split(" ", 1)[0].strip()
            if digest != expected:
                raise ActivationPause("awaiting_ha_operator_module")

    def _validate_pre_authorization_prerequisites(
        self, _state: Mapping[str, Any]
    ) -> None:
        self._require_root_executable(SIGNING_LAUNCHER)
        self._require_root_executable(CREDENTIAL_PROVISIONER)
        self._require_root_executable(LOCAL_BACKUP)
        try:
            key_source = parse_canonical_json(
                self._private_document(KEY_SOURCE_RECEIPT), maximum=4096
            )
        except (ActivationRunnerError, VerificationError) as error:
            raise ActivationPause(
                "awaiting_signing_key_source_confirmation"
            ) from error
        if (
            not isinstance(key_source, Mapping)
            or set(key_source) != {"contract", "with_key"}
            or key_source.get("contract")
            != "phase3-signing-key-source-e5ae-v1"
            or key_source.get("with_key") not in {"host", "tpm2", "host+tpm2"}
        ):
            raise ActivationPause("awaiting_signing_key_source_confirmation")
        self._run(self._compose("config", "--quiet"), timeout=120, diagnostic=True)
        try:
            ha_transport._remote("true", timeout=15)
        except Exception as error:
            raise ActivationPause("awaiting_ha_ssh_prerequisite") from error
        ha_transport._remote("python3", "--version", timeout=15)
        ha_transport._remote("test", "-f", REMOTE_EDGE_RECEIPT, timeout=15)
        ha_transport._remote("test", "-f", REMOTE_FREEZE, timeout=15)
        ha_transport._remote("test", "-f", REMOTE_OBSERVER, timeout=15)
        ha_transport._remote("test", "-f", REMOTE_IDENTITY_STORE, timeout=15)
        ha_transport._remote("test", "-f", REMOTE_LEGACY_FENCE, timeout=15)
        self._require_remote_operator_module()
        report = self._json(
            [sys.executable, str(OPERATOR_ROOT / "phase3_activation_preflight.py")],
            timeout=180,
            accepted=frozenset({0, 3}),
        )
        if (
            report.get("preflight_passed") is not True
            or report.get("blockers") != []
            or report.get("authoritative") is not False
            or report.get("enables_writes") is not False
            or report.get("runs_migrations") is not False
            or report.get("changes_rollout_mode") is not False
        ):
            raise ActivationPause("awaiting_current_activation_preflight")

    def _authorize_shadow(self, state: Mapping[str, Any]) -> None:
        core_probe = self._json(
            [
                "docker",
                "exec",
                activation_preflight.CORE_CONTAINER,
                "python",
                "-c",
                activation_preflight.CORE_PROBE,
            ],
            timeout=180,
        )
        phase2 = core_probe.get("phase2")
        if (
            not isinstance(phase2, Mapping)
            or phase2.get("contract") != activation_preflight.PHASE2_CONTRACT
            or phase2.get("rule_version") != activation_preflight.PHASE2_RULE
            or phase2.get("ready_to_advance") is not True
            or phase2.get("blockers") != []
            or not isinstance(phase2.get("policy_version"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(phase2.get("policy_digest", "")))
            is None
            or re.fullmatch(r"[0-9a-f]{64}", str(phase2.get("input_digest", "")))
            is None
        ):
            raise ActivationRunnerError("shadow authorization evidence is invalid")
        request = {
            "operator_request_id": state["operation_ids"]["authorize_shadow"],
            "expected_rule_version": phase2["rule_version"],
            "expected_policy_version": phase2["policy_version"],
            "expected_policy_digest": phase2["policy_digest"],
            "expected_input_digest": phase2["input_digest"],
        }
        if (
            SHADOW_AUTHORIZATION_RECEIPT.exists()
            or SHADOW_AUTHORIZATION_RECEIPT.is_symlink()
        ):
            receipt = parse_canonical_json(
                self._private_document(SHADOW_AUTHORIZATION_RECEIPT),
                maximum=MAX_OUTPUT,
            )
        else:
            receipt = self._json(
                self._compose("run", "--rm", "-T", "rollout-authorize"),
                input_bytes=canonical_bytes(request),
                timeout=300,
            )
            self._atomic_private(
                SHADOW_AUTHORIZATION_RECEIPT, canonical_bytes(receipt)
            )
        expected_receipt_keys = {
            "contract",
            "authorization_id",
            "operator_request_id",
            "from_mode",
            "to_mode",
            "rule_version",
            "policy_version",
            "policy_digest",
            "input_digest",
            "worker_kernel_version",
            "worker_success_sequence",
            "worker_proof_digest",
            "readiness_evaluated_at",
            "authorized_at",
        }
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != expected_receipt_keys
            or receipt.get("contract") != "rollout-authorization-receipt-v2"
            or receipt.get("operator_request_id") != request["operator_request_id"]
            or receipt.get("from_mode") != "record_only"
            or receipt.get("to_mode") != "shadow"
            or receipt.get("rule_version") != request["expected_rule_version"]
            or receipt.get("policy_version") != request["expected_policy_version"]
            or receipt.get("policy_digest") != request["expected_policy_digest"]
            or receipt.get("input_digest") != request["expected_input_digest"]
            or receipt.get("worker_kernel_version")
            != "worker-maintenance-cycle-v1"
            or not isinstance(receipt.get("worker_success_sequence"), int)
            or receipt["worker_success_sequence"] < 1
            or re.fullmatch(
                r"[0-9a-f]{64}", str(receipt.get("worker_proof_digest", ""))
            )
            is None
        ):
            raise ActivationRunnerError("shadow authorization receipt is invalid")
        for key in ("authorization_id", "operator_request_id"):
            try:
                value = uuid.UUID(str(receipt[key]))
            except (TypeError, ValueError, AttributeError) as error:
                raise ActivationRunnerError(
                    "shadow authorization receipt is invalid"
                ) from error
            if value.version not in {4, 7}:
                raise ActivationRunnerError("shadow authorization receipt is invalid")

    def _provision_signing_credentials(self, _state: Mapping[str, Any]) -> None:
        result = self._json([str(CREDENTIAL_PROVISIONER)], timeout=900)
        if (
            result.get("contract")
            != "phase3-identity-credential-receipt-e5ae-v1"
            or result.get("status") != "provisioned"
            or result.get("credential_count") != len(CREDENTIAL_TARGETS)
            or result.get("provisioning_status") not in {"completed", "resumed"}
        ):
            raise ActivationRunnerError("identity credential provisioning failed")

    def _await_reviewed_packet(self, _state: Mapping[str, Any]) -> None:
        if (
            not IDENTITY_SIGNING_STATE.exists()
            and not IDENTITY_SIGNING_STATE.is_symlink()
        ):
            raise ActivationPause("awaiting_private_people_packet")
        raw = self._private_document(IDENTITY_SIGNING_STATE)
        try:
            state = parse_canonical_json(raw, maximum=MAX_OUTPUT)
        except VerificationError as error:
            raise ActivationRunnerError(
                "private People signing state is invalid"
            ) from error
        if (
            not isinstance(state, Mapping)
            or state.get("contract") != "phase3-identity-signing-state-e5y-v1"
        ):
            raise ActivationRunnerError("private People signing state is invalid")
        phase = state.get("phase")
        if phase == "staged":
            raise ActivationPause("awaiting_private_people_review")
        if phase not in {"review_signed", "finalized"}:
            raise ActivationRunnerError("private People signing state is invalid")
        if phase == "review_signed" and (
            not sys.stdin.isatty() or not sys.stdout.isatty()
        ):
            raise ActivationPause("awaiting_private_finalization_tty")

    def _validate_live_prerequisites(self, state: Mapping[str, Any]) -> None:
        self._require_root_executable(SIGNING_LAUNCHER)
        self._require_root_executable(CREDENTIAL_PROVISIONER)
        self._require_root_executable(LOCAL_BACKUP)
        credential_raw = self._private_document(CREDENTIAL_RECEIPT)
        try:
            credential = parse_canonical_json(credential_raw, maximum=MAX_OUTPUT)
        except VerificationError as error:
            raise ActivationRunnerError(
                "identity credential receipt is invalid"
            ) from error
        validate_credential_receipt_shape(credential)
        report = source_plan.live_report()
        rebind_receipts = read_rebind_receipts(
            str(state["runner_id"]),
            hashlib.sha256(credential_raw).hexdigest(),
        )
        if not credential_source_binding_valid(
            credential, state, report, rebind_receipts
        ):
            raise ActivationRunnerError("identity credential receipt is invalid")
        for target in CREDENTIAL_TARGETS:
            try:
                details = target.lstat()
            except OSError as error:
                raise ActivationRunnerError(
                    "identity signing credential is unavailable"
                ) from error
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != 0
                or details.st_gid != 0
                or stat.S_IMODE(details.st_mode) != 0o600
                or details.st_nlink != 1
            ):
                raise ActivationRunnerError(
                    "identity signing credential is unsafe"
                )
        self._validate_pre_authorization_prerequisites(state)

    def _local_backup(self, _state: Mapping[str, Any]) -> None:
        self._run([str(LOCAL_BACKUP), str(ENVIRONMENT_PATH)], timeout=1800)
        report = self._json(
            [sys.executable, str(OPERATOR_ROOT / "phase3_activation_preflight.py")],
            timeout=180,
            accepted=frozenset({0, 3}),
        )
        label = preflight_backup_label(report)
        if label is None:
            raise ActivationRunnerError("fresh backup label is unavailable")
        self.backup_label = label

    def _offhost_backup(self, _state: Mapping[str, Any]) -> None:
        self._json([sys.executable, str(OFFHOST_BACKUP)], timeout=3600)

    def _restore_drill(self, _state: Mapping[str, Any]) -> None:
        if self.backup_label is None:
            report = self._json(
                [sys.executable, str(OPERATOR_ROOT / "phase3_activation_preflight.py")],
                timeout=180,
                accepted=frozenset({0, 3}),
            )
            value = preflight_backup_label(report)
            if value is None:
                raise ActivationRunnerError("restore backup label is unavailable")
            self.backup_label = value
        self._run(
            [str(RESTORE_DRILL), str(ENVIRONMENT_PATH), self.backup_label],
            timeout=3600,
        )

    def _erasure_current(self, _state: Mapping[str, Any]) -> None:
        self._json(
            [sys.executable, str(ERASURE_RECEIPTS), "erasure-current"], timeout=600
        )

    def _arm_initial_permit(self, _state: Mapping[str, Any]) -> None:
        self._json(
            [
                sys.executable,
                str(OPERATOR_ROOT / "phase3_activation_sequencer.py"),
                "arm-grants",
            ],
            timeout=180,
        )

    def _commit_finalizer(self, state: Mapping[str, Any]) -> None:
        if not (
            FINALIZER_DOCUMENT.exists()
            and not FINALIZER_DOCUMENT.is_symlink()
            and FINALIZER_RECEIPT.exists()
            and not FINALIZER_RECEIPT.is_symlink()
        ):
            self._run_interactive([str(SIGNING_LAUNCHER), "finalize"], timeout=900)
        document = self._private_document(FINALIZER_DOCUMENT)
        receipt = parse_canonical_json(
            self._private_document(FINALIZER_RECEIPT), maximum=MAX_OUTPUT
        )
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("finalizer_document_sha256")
            != hashlib.sha256(document).hexdigest()
        ):
            raise ActivationRunnerError("finalizer receipt does not match")
        self._register_migration_run()
        admission_id = str(state["operation_ids"]["commit_finalizer"])
        admission = self._request(
            "identity-finalizer-admission-e5u-v1", admission_id, document
        )
        self._json(
            [sys.executable, str(AUTHORITY_ADMISSION), "admit-finalizer"],
            input_bytes=admission,
            timeout=300,
        )
        execution = self._request(
            "identity-finalizer-execution-e5n-v1", admission_id, document
        )
        self._json(
            [sys.executable, str(AUTHORITY_CEREMONY), "finalize"],
            input_bytes=execution,
            timeout=300,
        )

    @staticmethod
    def _retirement_name(path: Path, run_id: str) -> Path:
        """Archive name for one retired artifact.

        Deliberately not the signing ceremony's `.superseded-` name. That one
        means an unsigned packet whose staged review window lapsed; this means a
        review-signed, finalized packet whose reviewed run expired before any
        registration. Conflating the two would misdescribe the private record.
        """

        return path.with_name(f"{path.stem}.retired-{run_id}{path.suffix}")

    @staticmethod
    def _admission_recovery_name(path: Path, run_id: str) -> Path:
        """Archive name for an artifact whose admission was spent.

        Distinct from both `.retired-` and the ceremony's `.superseded-`: those
        mean a packet that was never registered. This one means a packet that
        was registered and then refused by the projection kernel.
        """

        return path.with_name(f"{path.stem}.unfinalizable-{run_id}{path.suffix}")

    def recover_finalizer_admission(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Re-mint the finalizer admission id after an unfinalizable packet.

        `commit_finalizer` writes its admission under a fixed operation id held
        in the journal, and `operations.reviewed_identity_finalizer_admissions`
        keys on that id. So one activation gets exactly one admission. If the
        packet it was spent on can never finalize -- because the finalizer
        kernel refuses its projections -- the run is stranded with no verb that
        can move it: `retire-expired-finalization` requires that nothing was
        registered, and the ceremony's `supersede-expired` requires an unsigned
        packet still at `staged`. Neither describes a packet that was signed,
        registered, and then rejected.

        This is that gap. It re-mints the admission operation id and archives
        the stale signing artifacts so `stage` compiles a fresh packet. It
        writes nothing to the database, advances no step, and rewinds nothing:
        `completed_steps` and `next_step` are untouched, and the superseded id
        is recorded in an append-only receipt beside its successor.

        It refuses unless the database proves the spent admission was never
        acted on -- nothing finalized, nothing consumed -- because a finalized
        run has projections behind it and a second run would be a second
        semantic authority, which no recovery may create.
        """

        raw_state = self._private_document(IDENTITY_SIGNING_STATE)
        signing = parse_canonical_json(raw_state, maximum=MAX_OUTPUT)
        if (
            not isinstance(signing, Mapping)
            or signing.get("contract") != "phase3-identity-signing-state-e5y-v1"
            or signing.get("phase") not in RETIRABLE_PHASES
        ):
            raise ActivationRunnerError("identity signing state refuses recovery")
        packet = signing.get("unsigned_packet")
        if not isinstance(packet, Mapping):
            raise ActivationRunnerError("identity signing state refuses recovery")
        run = packet.get("unsigned_run")
        run_id = packet.get("run_id")
        if (
            not isinstance(run, Mapping)
            or not isinstance(run_id, str)
            or run_id != run.get("run_id")
        ):
            raise ActivationRunnerError("identity signing state refuses recovery")
        try:
            parsed = uuid.UUID(run_id)
        except ValueError as error:
            raise ActivationRunnerError(
                "identity signing state refuses recovery"
            ) from error
        if parsed.version != 7 or str(parsed) != run_id:
            raise ActivationRunnerError("identity signing state refuses recovery")

        expires_text = run.get("expires_at")
        if not isinstance(expires_text, str):
            raise ActivationRunnerError("identity signing state refuses recovery")
        try:
            expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ActivationRunnerError(
                "identity signing state refuses recovery"
            ) from error
        if expires_at.tzinfo is None or expires_at > datetime.now(UTC):
            # A live run must be finalized, never recovered around.
            raise ActivationRunnerError("reviewed migration run has not expired")

        probe = self._probe("migration")
        if (
            probe.get("finalization_count") != 0
            or probe.get("consumed_admission_count") != 0
            or not isinstance(probe.get("finalizer_admission_count"), int)
            or probe["finalizer_admission_count"] < 1
        ):
            raise ActivationRunnerError("reviewed migration state refuses recovery")

        superseded = str(state["operation_ids"]["commit_finalizer"])
        replacement = str(uuid7())
        receipt_path = PRIVATE_IDENTITY_ROOT / (
            f"{ADMISSION_RECOVERY_PREFIX}{run_id}{ADMISSION_RECOVERY_SUFFIX}"
        )
        archived: list[str] = []
        for path in (IDENTITY_SIGNING_STATE, FINALIZER_DOCUMENT, FINALIZER_RECEIPT):
            target = self._admission_recovery_name(path, run_id)
            if target.exists() or target.is_symlink():
                if path.exists() or path.is_symlink():
                    raise ActivationRunnerError(
                        "identity recovery archive is ambiguous"
                    )
                archived.append(target.name)
                continue
            if not (path.exists() and not path.is_symlink()):
                continue
            os.rename(path, target)
            archived.append(target.name)

        if not (receipt_path.exists() or receipt_path.is_symlink()):
            self._atomic_private(
                receipt_path,
                canonical_bytes(
                    {
                        "contract": ADMISSION_RECOVERY_CONTRACT,
                        "status": "recovered",
                        "reason_code": ADMISSION_RECOVERY_REASON,
                        "run_id": run_id,
                        "expires_at": expires_text,
                        "superseded_admission_id": superseded,
                        "replacement_admission_id": replacement,
                        "ceremony_policy_sha256": signing["ceremony_policy_sha256"],
                        "recovered_state_sha256": hashlib.sha256(raw_state).hexdigest(),
                        "archived_names": sorted(archived),
                        "created_at": _utc(),
                    }
                ),
            )
        return {"replacement": replacement, "archived_count": len(archived)}

    def retire_expired_finalization(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Retire a reviewed packet whose run expired before registration.

        The signing ceremony can supersede only an unsigned packet staged at the
        four-step await boundary. A packet whose run expired at this boundary is
        out of scope there: the runbook records that such a state "fails closed
        for separate owner review". This command is that review's outcome.

        It accepts any phase the packet can be stranded in. The ten-minute
        window holds two interactive signatures and two container round-trips,
        so lapsing at `staged` or `review_signed` is at least as likely as
        lapsing at `finalized` — and those two had no recovery verb in either
        tool. Which phase it died in changes nothing that matters here: the run
        is expired, the database proves nothing was consumed, and a fresh packet
        re-stages from the same private review and SQLite snapshot.

        It archives the three private artifacts and writes one content-free
        receipt. It never rewinds the journal, never writes to the database, and
        refuses outright unless the database proves nothing was registered,
        admitted, or finalized. Registration is one-shot for the life of the
        database, so retiring a packet whose run was already registered would
        leave a successor that could never be registered at all.
        """

        raw_state = self._private_document(IDENTITY_SIGNING_STATE)
        signing = parse_canonical_json(raw_state, maximum=MAX_OUTPUT)
        if (
            not isinstance(signing, Mapping)
            or signing.get("contract") != "phase3-identity-signing-state-e5y-v1"
            or signing.get("phase") not in RETIRABLE_PHASES
        ):
            raise ActivationRunnerError("identity signing state refuses retirement")
        packet = signing.get("unsigned_packet")
        if not isinstance(packet, Mapping):
            raise ActivationRunnerError("identity signing state refuses retirement")
        run = packet.get("unsigned_run")
        run_id = packet.get("run_id")
        if (
            not isinstance(run, Mapping)
            or not isinstance(run_id, str)
            or run_id != run.get("run_id")
        ):
            raise ActivationRunnerError("identity signing state refuses retirement")
        try:
            parsed = uuid.UUID(run_id)
        except ValueError as error:
            raise ActivationRunnerError(
                "identity signing state refuses retirement"
            ) from error
        if parsed.version != 7 or str(parsed) != run_id:
            raise ActivationRunnerError("identity signing state refuses retirement")

        expires_text = run.get("expires_at")
        if not isinstance(expires_text, str):
            raise ActivationRunnerError("identity signing state refuses retirement")
        try:
            expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ActivationRunnerError(
                "identity signing state refuses retirement"
            ) from error
        if expires_at.tzinfo is None:
            raise ActivationRunnerError("identity signing state refuses retirement")
        if expires_at > datetime.now(UTC):
            # A live run must be finalized, never retired.
            raise ActivationRunnerError("reviewed migration run has not expired")

        probe = self._probe("migration")
        if probe.get("expired_finalization_retirable") is not True:
            raise ActivationRunnerError("reviewed migration state refuses retirement")

        receipt_path = PRIVATE_IDENTITY_ROOT / (
            f"{RETIREMENT_RECEIPT_PREFIX}{run_id}{RETIREMENT_RECEIPT_SUFFIX}"
        )
        archived: list[str] = []
        for path in (IDENTITY_SIGNING_STATE, FINALIZER_DOCUMENT, FINALIZER_RECEIPT):
            target = self._retirement_name(path, run_id)
            if target.exists() or target.is_symlink():
                # A previous attempt already archived this one. Never overwrite
                # an archive: the private record is append-only.
                if path.exists() or path.is_symlink():
                    raise ActivationRunnerError(
                        "identity retirement archive is ambiguous"
                    )
                archived.append(target.name)
                continue
            if not (path.exists() and not path.is_symlink()):
                continue
            os.rename(path, target)
            archived.append(target.name)

        if not (receipt_path.exists() or receipt_path.is_symlink()):
            self._atomic_private(
                receipt_path,
                canonical_bytes(
                    {
                        "contract": RETIREMENT_CONTRACT,
                        "status": "retired",
                        "reason_code": RETIREMENT_REASON,
                        "run_id": run_id,
                        "expires_at": expires_text,
                        "ceremony_policy_sha256": signing["ceremony_policy_sha256"],
                        "retired_state_sha256": hashlib.sha256(
                            raw_state
                        ).hexdigest(),
                        "archived_names": sorted(archived),
                        "created_at": _utc(),
                    }
                ),
            )
        return {
            "contract": CONTRACT,
            "operation": "retire_expired_finalization",
            "status": "retired",
            "archived_count": len(archived),
        }

    def _capture_edge_receipt(self, _state: Mapping[str, Any]) -> None:
        if EDGE_RECEIPT.exists() or EDGE_RECEIPT.is_symlink():
            self._private_document(EDGE_RECEIPT)
            return
        raw = ha_transport._remote("cat", "--", REMOTE_EDGE_RECEIPT, timeout=30)
        self._atomic_private(EDGE_RECEIPT, raw)

    @staticmethod
    def _stop_ha() -> None:
        """Stop Home Assistant and wait for the legacy database to go quiet.

        The stopped-database probe is the authoritative condition, on both
        paths. A lost SSH response is ambiguous -- HA Core may already be
        stopped -- but so is a successful one: `ha core stop` returns when the
        supervisor accepts the request, not when the writer has finished. The
        step 20 fence then refuses a database that still carries a `-wal`
        sidecar, and reports it as an opaque transport failure with the remote
        error discarded. Polling the probe removes that race in the one place
        that can observe it.
        """

        try:
            ha_transport._remote("ha", "core", "stop", timeout=90)
        except Exception:
            pass
        deadline = time.monotonic() + HA_QUIESCE_TIMEOUT_SECONDS
        while True:
            try:
                ha_transport._require_remote_stopped_database()
                return
            except Exception as error:
                if time.monotonic() >= deadline:
                    raise ActivationRunnerError(
                        "Home Assistant could not be stopped"
                    ) from error
                time.sleep(HA_QUIESCE_POLL_SECONDS)

    def _freeze_legacy_writer(self, _state: Mapping[str, Any]) -> None:
        if WRITER_OBSERVATION.exists() or WRITER_OBSERVATION.is_symlink():
            self._private_document(WRITER_OBSERVATION)
            return
        ha_transport._remote(
            "python3",
            REMOTE_FREEZE,
            "--database",
            REMOTE_IDENTITY_DB,
            timeout=600,
        )
        raw = ha_transport._remote("python3", REMOTE_OBSERVER, timeout=300)
        self._atomic_private(WRITER_OBSERVATION, raw)

    def _record_evidence(self, command: str, path: Path) -> None:
        """Carry one signed evidence packet into the database.

        The ceremony signs these to private files and, until the evidence
        writer existed, nothing carried them any further — the semantic cutover
        kernel reads four tables that no production code wrote. Recording
        happens in the same step that signs, so a signed packet and its rows
        cannot drift apart.
        """

        document = self._private_document(path)
        request = canonical_bytes(
            {
                "contract": EVIDENCE_CONTRACTS[command],
                "document_b64": base64.b64encode(document).decode("ascii"),
            }
        )
        self._json(
            [sys.executable, str(AUTHORITY_ADMISSION), command],
            input_bytes=request,
            timeout=300,
        )

    @staticmethod
    def _stale_observation_name(path: Path) -> Path:
        """Archive name for an observation whose freeze time expired.

        Distinct from `.unfinalizable-`: nothing was spent, the measurement
        simply aged out of the windows the privacy observer and the cutover
        kernel enforce.
        """

        return path.with_name(f"{path.stem}.stale-{uuid7()}{path.suffix}")

    def _refresh_writer_observation(self, state: Mapping[str, Any]) -> None:
        """Re-measure the frozen writer so the evidence carries a fresh time.

        Every downstream window is measured from this file's `observed_at`:
        the privacy observer refuses a freeze older than five minutes and the
        cutover kernel refuses evidence older than the finalization. Because
        `_freeze_legacy_writer` reuses an observation that already exists, a
        run resumed hours later could otherwise never satisfy them. Archive
        the old measurement instead of reusing it -- a rename, so the bytes
        that were on disk when the run stalled stay auditable.
        """

        if WRITER_OBSERVATION.exists() or WRITER_OBSERVATION.is_symlink():
            target = self._stale_observation_name(WRITER_OBSERVATION)
            if target.exists() or target.is_symlink():
                raise ActivationRunnerError("writer freeze archive is ambiguous")
            os.rename(WRITER_OBSERVATION, target)
        # stop -> fence -> observe, in that order. `_stop_ha` waits only for
        # `-wal` and `-journal` to disappear, while the observer additionally
        # refuses a `-shm`; it is the fence run's sidecar sweep in between that
        # removes it. Skipping the fence on an already-fenced database would
        # leave the observer refusing a database that is genuinely stopped.
        self._stop_ha()
        self._freeze_legacy_writer(state)

    @staticmethod
    def _writer_evidence_resumable() -> bool:
        """Is the freeze time inside the signed evidence still usable?

        Read the time out of the evidence rather than off the observation on
        disk. `verified_at` is the value the step 22 observer measures and the
        cutover kernel compares against the finalization, and taking it from
        the signed document means no separate file has to still agree with it
        for the answer to be right.
        """

        try:
            raw = Backend._private_document(WRITER_FREEZE_EVIDENCE)
            document = parse_canonical_json(raw, maximum=MAX_OUTPUT)
            if not isinstance(document, Mapping):
                return False
            freeze = document["enforced_writer_freeze"]
            if not isinstance(freeze, Mapping):
                return False
            verified_at = datetime.fromisoformat(
                str(freeze["verified_at"]).replace("Z", "+00:00")
            )
        except (
            ActivationRunnerError,
            VerificationError,
            KeyError,
            ValueError,
            OSError,
        ):
            return False
        if verified_at.tzinfo is None:
            return False
        age = (datetime.now(UTC) - verified_at).total_seconds()
        # A freeze time slightly in the future is ordinary cross-host skew.
        return -60 <= age <= WRITER_EVIDENCE_RESUME_SECONDS

    def _sign_writer_evidence(self, state: Mapping[str, Any]) -> None:
        """Sign the physical freeze observation and record it as evidence.

        The ceremony writes signed evidence before the row is recorded and
        resumes from it verbatim, so on a re-entry the two halves have to be
        handled together. Refreshing the measurement under existing evidence
        would re-emit the old evidence against a new observation and spend the
        one-shot freeze rows on a time step 22 rejects; re-emitting evidence
        whose measurement has already aged out spends them just as surely.

        So resume only while the measurement is still inside the window --
        which also covers the narrow case where the row was committed and only
        the journal write was lost, because the evidence writer replays an
        identical document harmlessly. Otherwise park for review: by then the
        run needs a decision about what the database already holds, not another
        attempt.
        """

        try:
            # Checked here as well as inside the admission below: a permit that
            # expired mid-run should pause before Home Assistant is stopped and
            # a fresh measurement is spent, not after.
            migration_executor._require_fresh_permit(datetime.now(UTC))
        except migration_executor.MigrationExecutionError as error:
            raise ActivationPause("awaiting_permit_recovery") from error
        if any(
            path.exists() or path.is_symlink()
            for path in (WRITER_FREEZE_EVIDENCE, WRITER_FREEZE_RECEIPT)
        ):
            if not self._writer_evidence_resumable():
                raise ActivationPause("awaiting_writer_evidence_review")
        else:
            self._refresh_writer_observation(state)
        self._json([str(SIGNING_LAUNCHER), "freeze-evidence"], timeout=900)
        self._record_evidence("record-freeze-evidence", WRITER_FREEZE_EVIDENCE)

    def _sign_privacy_evidence(self, _state: Mapping[str, Any]) -> None:
        if PRIVACY_OBSERVATION.exists() or PRIVACY_OBSERVATION.is_symlink():
            self._private_document(PRIVACY_OBSERVATION)
        else:
            self._json([sys.executable, str(PRIVACY_OBSERVER)], timeout=180)
        self._json([str(SIGNING_LAUNCHER), "privacy-evidence"], timeout=900)
        self._record_evidence("record-privacy-evidence", PRIVACY_CUTOVER_EVIDENCE)

    def _commit_semantic_cutover(self, state: Mapping[str, Any]) -> None:
        probe = self._probe("authority")
        if probe.get("semantic_authority_current") is True:
            return
        # The packet refuses an erasure receipt older than five minutes, and
        # nothing between step 12 and here refreshes one. Re-verify immediately
        # before the packet is compiled so only the launcher start separates the
        # receipt's `verified_at` from the packet's own clock.
        self._erasure_current(state)
        self._json([str(SIGNING_LAUNCHER), "cutover-packet"], timeout=900)
        # The authority candidate carries the writer evidence id and all six
        # privacy check ids, so it can only be recorded after those rows exist,
        # and the admission below carries foreign keys to it.
        self._record_evidence("record-cutover-candidate", CUTOVER_PACKET)
        packet_raw = self._private_document(CUTOVER_PACKET)
        receipt = parse_canonical_json(
            self._private_document(CUTOVER_RECEIPT), maximum=MAX_OUTPUT
        )
        packet = parse_canonical_json(packet_raw, maximum=MAX_OUTPUT)
        if not isinstance(packet, Mapping) or not isinstance(receipt, Mapping):
            raise ActivationRunnerError("semantic cutover packet is invalid")
        document_value = packet.get("cutover_document")
        if not isinstance(document_value, Mapping):
            raise ActivationRunnerError("semantic cutover document is invalid")
        document = canonical_bytes(document_value)
        admission_id = document_value.get("admission_id")
        if (
            not isinstance(admission_id, str)
            or receipt.get("admission_id") != admission_id
            or receipt.get("cutover_packet_sha256")
            != hashlib.sha256(packet_raw).hexdigest()
            or receipt.get("cutover_document_sha256")
            != hashlib.sha256(document).hexdigest()
        ):
            raise ActivationRunnerError("semantic cutover receipt does not match")
        admission = self._request(
            "identity-cutover-admission-e5u-v1", admission_id, document
        )
        self._json(
            [sys.executable, str(AUTHORITY_ADMISSION), "admit-cutover"],
            input_bytes=admission,
            timeout=300,
        )
        execution = self._request(
            "identity-cutover-execution-e5n-v1", admission_id, document
        )
        self._json(
            [sys.executable, str(AUTHORITY_CEREMONY), "cutover"],
            input_bytes=execution,
            timeout=300,
        )
        if self._probe("authority").get("semantic_authority_current") is not True:
            raise ActivationRunnerError("semantic authority did not become current")

    @staticmethod
    def _restart_ha() -> None:
        ha_transport._start_home_assistant()
        ha_transport._wait_home_assistant_ready()

    def _grant_start(self, revision: str) -> None:
        self._apply_grants()
        self._start_agents(revision)

    def _await_binding(self, _state: Mapping[str, Any]) -> None:
        if self._probe("binding").get("authenticated_binding_confirmed") is not True:
            raise ActivationPause("awaiting_authenticated_binding")

    def _rearm_parent_permit(self, _state: Mapping[str, Any]) -> None:
        report = source_plan.live_report()
        if (
            report.get("source_acceptance_receipt_issuable") is not True
            or report.get("blockers") != []
            or self._probe("binding").get("authenticated_binding_confirmed") is not True
        ):
            raise ActivationRunnerError("parent migration permit cannot be refreshed")
        sequencer._atomic_write(
            sequencer.GRANT_PERMIT_PATH,
            (sequencer.GRANT_PERMIT_VALUE + "\n").encode("ascii"),
        )

    def recover_permit(self, state: Mapping[str, Any]) -> None:
        """Re-arm the grant permit for a run stranded past the permit TTL.

        The permit is armed once at ``arm_initial_permit`` and once more at
        ``rearm_parent_permit``. Both windows span a private human confirmation
        with no bounded duration, so an operator who takes longer than the
        four-hour freshness bound is left with a run that cannot advance and no
        verb that can refresh it: ``arm-grants`` re-runs the E5j preflight,
        which probes a diagnostic the core disables for good once the database
        moves past the pre-Phase-3 revision. This command is the recovery that
        absence leaves missing.

        It re-establishes the same evidence the initial arm required -- the
        hosted source still admitted with no blockers, the grant contract still
        installed -- and additionally pins the database to the exact revision
        the journal says it reached. That last check is what makes refreshing
        safe: a permit is only ever re-armed onto a database that is precisely
        where the run left it, never one that drifted underneath.

        It writes one file. It runs no migration, starts no service, confirms
        no binding, and never advances the journal. Every human gate stays
        where it was: ``validate_state`` admits only a prefix of ``STEPS``, so
        a run parked in the parent window has provably already passed both
        private confirmations, and one parked in the binding window still has
        to reach them.
        """

        report = source_plan.live_report()
        if (
            report.get("source_acceptance_receipt_issuable") is not True
            or report.get("blockers") != []
            or not sequencer.grant_contract_installed()
        ):
            raise ActivationRunnerError("activation source refuses permit recovery")
        self._run(
            self._compose(
                *migration_executor.revision_guard_arguments(
                    expected_revision(state["completed_steps"])
                )
            ),
            timeout=180,
        )
        sequencer._atomic_write(
            sequencer.GRANT_PERMIT_PATH,
            (sequencer.GRANT_PERMIT_VALUE + "\n").encode("ascii"),
        )

    def _await_parents(self, _state: Mapping[str, Any]) -> None:
        if (
            self._probe("parents").get("exact_parent_relationship_confirmed")
            is not True
        ):
            raise ActivationPause("awaiting_parent_confirmation")

    def _seal_completion(self, state: Mapping[str, Any]) -> None:
        receipt = canonical_bytes(completion_receipt(state))
        self._atomic_private(COMPLETION_RECEIPT, receipt)

    def contain(self) -> None:
        try:
            self._stop_agents()
        finally:
            try:
                self._restart_ha()
            except Exception:
                pass


def validate_shadow_receipt_on_disk(state: Mapping[str, Any]) -> None:
    """Validate the durable shadow authorization without a live core probe."""

    raw = Backend._private_document(SHADOW_AUTHORIZATION_RECEIPT)
    try:
        receipt = parse_canonical_json(raw, maximum=MAX_OUTPUT)
    except VerificationError as error:
        raise ActivationRunnerError(
            "shadow authorization receipt is invalid"
        ) from error
    expected_receipt_keys = {
        "contract",
        "authorization_id",
        "operator_request_id",
        "from_mode",
        "to_mode",
        "rule_version",
        "policy_version",
        "policy_digest",
        "input_digest",
        "worker_kernel_version",
        "worker_success_sequence",
        "worker_proof_digest",
        "readiness_evaluated_at",
        "authorized_at",
    }
    operations = state.get("operation_ids")
    expected_request = (
        operations.get("authorize_shadow") if isinstance(operations, Mapping) else None
    )
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != expected_receipt_keys
        or receipt.get("contract") != "rollout-authorization-receipt-v2"
        or receipt.get("operator_request_id") != expected_request
        or receipt.get("from_mode") != "record_only"
        or receipt.get("to_mode") != "shadow"
        or not isinstance(receipt.get("rule_version"), str)
        or not isinstance(receipt.get("policy_version"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("policy_digest", "")))
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("input_digest", "")))
        is None
        or receipt.get("worker_kernel_version") != "worker-maintenance-cycle-v1"
        or not isinstance(receipt.get("worker_success_sequence"), int)
        or receipt["worker_success_sequence"] < 1
        or re.fullmatch(
            r"[0-9a-f]{64}", str(receipt.get("worker_proof_digest", ""))
        )
        is None
    ):
        raise ActivationRunnerError("shadow authorization receipt is invalid")
    for key in ("authorization_id", "operator_request_id"):
        try:
            value = uuid.UUID(str(receipt[key]))
        except (TypeError, ValueError, AttributeError) as error:
            raise ActivationRunnerError(
                "shadow authorization receipt is invalid"
            ) from error
        if value.version not in {4, 7}:
            raise ActivationRunnerError("shadow authorization receipt is invalid")


class Runner:
    def __init__(self, backend: Backend, store: StateStore) -> None:
        self.backend = backend
        self.store = store

    def _initial_state(self) -> dict[str, Any]:
        try:
            report = source_plan.live_report()
        except source_plan.SourcePlanError as error:
            raise ActivationRunnerError(
                "activation source is not hosted-accepted"
            ) from error
        commit = report.get("current_commit")
        if (
            report.get("source_acceptance_receipt_issuable") is not True
            or report.get("blockers") != []
            or not isinstance(commit, str)
        ):
            raise ActivationRunnerError("activation source is not hosted-accepted")
        return new_state(commit)

    def status(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            return {
                "contract": CONTRACT,
                "status": "not_started",
                "next_step": STEPS[0],
                "completed_step_count": 0,
                "pause_code": "none",
                "last_error_code": "none",
            }
        return {
            "contract": CONTRACT,
            "status": state["status"],
            "next_step": state["next_step"],
            "completed_step_count": len(state["completed_steps"]),
            "pause_code": state["pause_code"],
            "last_error_code": state["last_error_code"],
        }

    def refresh_source(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        if (
            state["completed_steps"] != list(STEPS[:3])
            or state["next_step"] != "provision_signing_credentials"
            or state["status"] not in {"active", "paused"}
            or any(path.exists() or path.is_symlink() for path in SOURCE_REFRESH_FORBIDDEN_PATHS)
        ):
            raise ActivationRunnerError(
                "activation source cannot be refreshed at this boundary"
            )
        try:
            report = source_plan.live_report()
        except source_plan.SourcePlanError as error:
            raise ActivationRunnerError(
                "activation source is not hosted-accepted"
            ) from error
        commit = report.get("current_commit")
        if (
            report.get("source_acceptance_receipt_issuable") is not True
            or report.get("blockers") != []
            or not isinstance(commit, str)
            or source_plan.COMMIT_SHA.fullmatch(commit) is None
        ):
            raise ActivationRunnerError("activation source is not hosted-accepted")
        if commit == state["source_commit"]:
            return self.status()
        for step in STEPS[:3]:
            self.backend.perform(step, state)
        self.store.record_source_transition(state, commit)
        state["source_commit"] = commit
        state["status"] = "active"
        state["pause_code"] = "none"
        state["last_error_code"] = "none"
        state["updated_at"] = _utc()
        self.store.save(state)
        return self.status()

    def rebind_source(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        if (
            state["completed_steps"] != list(STEPS[:4])
            or state["next_step"] != "await_reviewed_people_packet"
            or state["status"] not in {"active", "paused"}
            or state["pause_code"]
            not in {
                "awaiting_private_people_review",
                "awaiting_private_people_packet",
                "none",
            }
            or any(
                path.exists() or path.is_symlink()
                for path in REBIND_FORBIDDEN_PATHS
            )
        ):
            raise ActivationRunnerError(
                "activation source cannot be rebound at this boundary"
            )
        credential_raw = Backend._private_document(CREDENTIAL_RECEIPT)
        try:
            credential = parse_canonical_json(credential_raw, maximum=MAX_OUTPUT)
        except VerificationError as error:
            raise ActivationRunnerError(
                "identity credential receipt is invalid"
            ) from error
        validate_credential_receipt_shape(credential)
        credential_sha256 = hashlib.sha256(credential_raw).hexdigest()
        receipts = read_rebind_receipts(str(state["runner_id"]), credential_sha256)
        origin = str(credential["source_commit"])
        commit = origin
        digest = str(credential["release_manifest_digest"])
        visited = {commit}
        for _ in range(REBIND_MAX_HOPS):
            if commit == state["source_commit"]:
                break
            receipt = receipts.get(commit)
            if (
                receipt is None
                or receipt.get("credential_source_commit") != origin
                or receipt.get("from_source_pack_digest") != digest
            ):
                raise ActivationRunnerError(
                    "activation source rebind chain is broken"
                )
            commit = receipt["to_source_commit"]
            digest = receipt["to_source_pack_digest"]
            if commit in visited:
                raise ActivationRunnerError(
                    "activation source rebind chain is broken"
                )
            visited.add(commit)
        else:
            raise ActivationRunnerError("activation source rebind chain is broken")
        validate_shadow_receipt_on_disk(state)
        try:
            report = source_plan.live_report()
        except source_plan.SourcePlanError as error:
            raise ActivationRunnerError(
                "activation source is not hosted-accepted"
            ) from error
        new_commit = report.get("current_commit")
        new_digest = report.get("source_pack_digest")
        if (
            report.get("source_acceptance_receipt_issuable") is not True
            or report.get("blockers") != []
            or not isinstance(new_commit, str)
            or source_plan.COMMIT_SHA.fullmatch(new_commit) is None
            or not isinstance(new_digest, str)
        ):
            raise ActivationRunnerError("activation source is not hosted-accepted")
        if new_commit == state["source_commit"]:
            return self.status()
        for step in STEPS[:2]:
            try:
                self.backend.perform(step, state)
            except ActivationPause as pause:
                raise ActivationRunnerError(
                    "activation source cannot be rebound at this boundary"
                ) from pause
        self.store.record_source_rebind(
            state,
            new_commit,
            from_source_pack_digest=digest,
            to_source_pack_digest=new_digest,
            credential_receipt_sha256=credential_sha256,
            credential_source_commit=origin,
        )
        state["source_commit"] = new_commit
        state["status"] = "active"
        state["pause_code"] = "none"
        state["last_error_code"] = "none"
        state["updated_at"] = _utc()
        self.store.save(state)
        return self.status()

    def advance(self) -> dict[str, Any]:
        state = self.store.load() or self._initial_state()
        if state["status"] == "complete":
            return self.status()
        state["status"] = "active"
        state["pause_code"] = "none"
        state["last_error_code"] = "none"
        self.store.save(state)
        while state["next_step"] != "none":
            step = state["next_step"]
            state["status"] = "running"
            state["attempt_counts"][step] = state["attempt_counts"].get(step, 0) + 1
            if step in OPERATION_ID_STEPS and step not in state["operation_ids"]:
                state["operation_ids"][step] = str(uuid7())
            state["updated_at"] = _utc()
            self.store.save(state)
            try:
                self.backend.perform(step, state)
            except ActivationPause as pause:
                state["status"] = "paused"
                state["pause_code"] = str(pause)
                state["updated_at"] = _utc()
                self.store.save(state)
                return self.status()
            except Exception as error:
                state["last_error_code"] = _error_code(error)
                if STEPS.index(step) >= STEPS.index("stop_home_assistant"):
                    try:
                        self.backend.contain()
                    finally:
                        state["status"] = "contained"
                else:
                    state["status"] = "paused"
                    state["pause_code"] = "operator_recovery_required"
                state["updated_at"] = _utc()
                self.store.save(state)
                raise ActivationRunnerError(
                    "activation advance failed closed"
                ) from error
            state["completed_steps"].append(step)
            next_index = len(state["completed_steps"])
            state["next_step"] = (
                "none" if next_index == len(STEPS) else STEPS[next_index]
            )
            state["status"] = "complete" if state["next_step"] == "none" else "active"
            state["pause_code"] = "none"
            state["last_error_code"] = "none"
            state["updated_at"] = _utc()
            self.store.save(state)
        return self.status()

    def retire_expired_finalization(self) -> dict[str, Any]:
        """Refuse unless the ceremony is parked exactly at the finalizer step."""

        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        if (
            state["next_step"] != "commit_finalizer"
            or state["status"] not in {"paused", "active"}
        ):
            raise ActivationRunnerError("activation boundary refuses retirement")
        return self.backend.retire_expired_finalization(state)

    def recover_permit(self) -> dict[str, Any]:
        """Refuse unless a started run is parked inside a grant-permit window."""

        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        if (
            state["next_step"] not in RECOVERABLE_PERMIT_STEPS
            or state["status"] not in {"paused", "active"}
        ):
            raise ActivationRunnerError("activation boundary refuses permit recovery")
        self.backend.recover_permit(state)
        return self.status()

    def recover_finalizer_admission(self) -> dict[str, Any]:
        """Refuse unless the ceremony is parked exactly at the finalizer step."""

        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        if (
            state["next_step"] != "commit_finalizer"
            or state["status"] not in {"paused", "active"}
        ):
            raise ActivationRunnerError("activation boundary refuses recovery")
        outcome = self.backend.recover_finalizer_admission(state)
        # The only journal field this touches. The cursor, the completed steps,
        # and every other operation id are left exactly as they were.
        state["operation_ids"]["commit_finalizer"] = outcome["replacement"]
        state["updated_at"] = _utc()
        self.store.save(state)
        return self.status()

    def contain(self) -> dict[str, Any]:
        state = self.store.load()
        if state is None:
            raise ActivationRunnerError("activation has not started")
        self.backend.contain()
        state["status"] = "contained"
        state["pause_code"] = "operator_recovery_required"
        state["updated_at"] = _utc()
        self.store.save(state)
        return self.status()


def _lock() -> int:
    import fcntl

    parent = LOCK_PATH.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_gid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ActivationRunnerError("activation runner lock directory is unsafe")
    descriptor = os.open(
        LOCK_PATH,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != 0
            or details.st_gid != 0
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_nlink != 1
        ):
            raise ActivationRunnerError("activation runner lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "status",
        "advance",
        "contain",
        "refresh-source",
        "rebind-source",
        "retire-expired-finalization",
        "recover-permit",
        "recover-finalizer-admission",
    }:
        print(
            "phase3 activation runner requires status, advance, contain, "
            "refresh-source, rebind-source, retire-expired-finalization, "
            "recover-permit, or recover-finalizer-admission",
            file=sys.stderr,
        )
        return 64
    if sys.platform != "linux" or os.geteuid() != 0:
        print("phase3 activation runner requires root on Linux", file=sys.stderr)
        return 77
    descriptor = -1
    try:
        descriptor = _lock()
        runner = Runner(Backend(), StateStore())
        action = sys.argv[1].replace("-", "_")
        result = getattr(runner, action)()
    except (ActivationRunnerError, ActivationPause, OSError):
        print("phase3 activation runner failed closed", file=sys.stderr)
        return 78
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return (
        0
        if sys.argv[1]
        in {
            "refresh-source",
            "rebind-source",
            "retire-expired-finalization",
            "recover-permit",
            "recover-finalizer-admission",
        }
        or result["status"] in {"not_started", "complete"}
        else 3
    )


if __name__ == "__main__":
    raise SystemExit(main())
