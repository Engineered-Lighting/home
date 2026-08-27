#!/usr/bin/env python3
"""Verify the hosted-tested Phase 3 source pack without activating it.

This is a source-plan verifier, not a migration or receipt writer. It proves
that every activation-relevant tracked path still has the exact tree accepted
by the hosted gates. A later pin-only commit may change exactly the accepted
commit, PostgreSQL-run, and web-run literals in this file; all executable
content must remain byte-identical after those three literals are normalized.
No successful result from this command can authorize a rollout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


CONTRACT = "phase3-activation-source-plan-e5k-v1"
SOURCE_PIN_BOOTSTRAP_CONTRACT = "phase3-source-pin-bootstrap-e5q-v1"
ACCEPTED_COMMIT = "3167a7a080d10f19bb2914117da796003c82d63d"
ACCEPTED_POSTGRES_RUN_ID = "33098210676"
ACCEPTED_WEB_RUN_ID = "31906584262"
SOURCE_REVISION = "0006a_worker_lease_arbitration"
TARGET_REVISION = "0021_parent_status_e5h"
SOURCE_ROOT = Path(__file__).resolve().parents[3]
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SOURCE_PLAN_RELATIVE_PATH = (
    "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
)
ACCEPTED_COMMIT_LITERAL = re.compile(rb'(?m)^ACCEPTED_COMMIT = "[0-9a-f]{40}"$')
ACCEPTED_RUN_LITERAL = re.compile(
    rb'(?m)^ACCEPTED_POSTGRES_RUN_ID = "[1-9][0-9]{5,19}"$'
)
ACCEPTED_WEB_RUN_LITERAL = re.compile(
    rb'(?m)^ACCEPTED_WEB_RUN_ID = "[1-9][0-9]{5,19}"$'
)
TREE_ENTRY = re.compile(rb"^(100644|100755) blob ([0-9a-f]{40})\t([A-Za-z0-9._/-]+)$")
ACTIVATION_PATHS = (
    ".github/workflows/home-agent-e1-postgres.yml",
    "app/src/home-agent",
    "ha-config/home_agent_edge",
    "ha-config/extended_openai_conversation/collect_legacy_identity_freeze_observation.py",
    "ha-config/extended_openai_conversation/freeze_legacy_identity_semantics.py",
    "ha-config/extended_openai_conversation/identity_store.py",
    "ha-config/extended_openai_conversation/legacy_identity_fence.py",
    "stack/home-agent-compose.yml",
    "stack/home-agent.env.example",
    "stack/home-agent-deploy/add-binding-committer-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-cutover-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-finalizer-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-migration-role-secrets.sh",
    "stack/home-agent-deploy/install-phase3-identity-signing.sh",
    "stack/home-agent-deploy/apply-grants.sh",
    "stack/home-agent-deploy/activate-identity-authority-role.sh",
    "stack/home-agent-deploy/identity-api-acl.sql",
    "stack/home-agent-deploy/materialize-secrets.sh",
    "stack/home-agent-deploy/off-host-backup-destination.e5o.example.json",
    "stack/home-agent-deploy/policy/home-agent-mvp-v1.json",
    "stack/home-agent-deploy/postgres-pg_hba.conf",
    "stack/home-agent-deploy/preflight-identity-cutover-roles.sh",
    "stack/home-agent-deploy/preflight.sh",
    "stack/home-agent-deploy/provision-identity-binding-kernel-role.sh",
    "stack/home-agent-deploy/provision-identity-cutover-roles.sh",
    "stack/home-agent-deploy/provision-parent-relationship-kernel-role.sh",
    "stack/home-agent-deploy/provision-roles.sh",
    "stack/home-agent-deploy/operator/identity_finalizer_compatibility.py",
    "stack/home-agent-deploy/operator/imported_image_identity.py",
    "stack/home-agent-deploy/operator/migrate_legacy_identity.py",
    "stack/home-agent-deploy/operator/isolated_restore_drill.sh",
    "stack/home-agent-deploy/operator/off_host_backup_writer.py",
    "stack/home-agent-deploy/operator/parent_confirmation_staging.py",
    "stack/home-agent-deploy/operator/phase3_capture_legacy_identity_snapshot.py",
    "stack/home-agent-deploy/operator/phase3_authority_admission.py",
    "stack/home-agent-deploy/operator/phase3_identity_authority_ceremony.py",
    "stack/home-agent-deploy/operator/phase3_identity_signing.sh",
    "stack/home-agent-deploy/operator/phase3_identity_signing_ceremony.py",
    "stack/home-agent-deploy/operator/phase3_identity_credential_provisioner.py",
    "stack/home-agent-deploy/operator/phase3_identity_credential_provisioner.sh",
    "stack/home-agent-deploy/operator/phase3_writer_freeze_ceremony.py",
    "stack/home-agent-deploy/operator/phase3_writer_freeze_evidence.py",
    "stack/home-agent-deploy/operator/phase3_privacy_cutover_ceremony.py",
    "stack/home-agent-deploy/operator/phase3_privacy_cutover_evidence.py",
    "stack/home-agent-deploy/operator/phase3_privacy_cutover_observer.py",
    "stack/home-agent-deploy/operator/phase3_semantic_cutover_ceremony.py",
    "stack/home-agent-deploy/operator/phase3_semantic_cutover_packet.py",
    "stack/home-agent-deploy/operator/phase3_reviewed_people_packet.py",
    "stack/home-agent-deploy/operator/reviewed_identity_packet_compiler.py",
    "stack/home-agent-deploy/operator/phase3_migration_executor.py",
    "stack/home-agent-deploy/operator/phase3_activation_preflight.py",
    "stack/home-agent-deploy/operator/phase3_activation_runner.py",
    "stack/home-agent-deploy/operator/phase3_activation_sequencer.py",
    "stack/home-agent-deploy/operator/phase3_evidence_receipts.py",
    "stack/home-agent-deploy/operator/principal_binding_candidate_staging.py",
    "stack/home-agent-deploy/operator/reviewed_identity_payload.py",
    "stack/services/home-agent-bff/src",
    "stack/services/home-agent-core/.dockerignore",
    "stack/services/home-agent-core/Dockerfile",
    "stack/services/home-agent-core/alembic.ini",
    "stack/services/home-agent-core/alembic",
    "stack/services/home-agent-core/app",
    "stack/services/home-agent-core/docker-entrypoint.sh",
    "stack/services/home-agent-core/requirements.lock",
    "stack/services/home-agent-core/requirements-dev.lock",
    "stack/services/home-agent-core/requirements.txt",
    "tools/run-home-agent-e1-postgres-gate.py",
)
MISSING_EXECUTABLE_BOUNDARIES: tuple[str, ...] = ()


class SourcePlanError(RuntimeError):
    """The accepted source boundary could not be established."""


def normalize_source_plan_pins(raw: bytes) -> bytes:
    if not raw or len(raw) > 256 * 1024 or b"\0" in raw:
        raise SourcePlanError("source-plan pin file is invalid")
    commit_matches = ACCEPTED_COMMIT_LITERAL.findall(raw)
    run_matches = ACCEPTED_RUN_LITERAL.findall(raw)
    web_run_matches = ACCEPTED_WEB_RUN_LITERAL.findall(raw)
    if len(commit_matches) != 1 or len(run_matches) != 1 or len(web_run_matches) != 1:
        raise SourcePlanError("source-plan pin file is invalid")
    normalized = ACCEPTED_COMMIT_LITERAL.sub(
        b'ACCEPTED_COMMIT = "' + (b"0" * 40) + b'"',
        raw,
        count=1,
    )
    normalized = ACCEPTED_RUN_LITERAL.sub(
        b'ACCEPTED_POSTGRES_RUN_ID = "0"',
        normalized,
        count=1,
    )
    normalized = ACCEPTED_WEB_RUN_LITERAL.sub(
        b'ACCEPTED_WEB_RUN_ID = "0"',
        normalized,
        count=1,
    )
    return normalized


def source_plan_matches_accepted_pin_only(
    accepted_raw: bytes, current_raw: bytes
) -> bool:
    try:
        return normalize_source_plan_pins(accepted_raw) == normalize_source_plan_pins(
            current_raw
        )
    except SourcePlanError:
        return False


def parse_tree(raw: bytes) -> tuple[int, str]:
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    if not records:
        raise SourcePlanError("accepted source tree is empty")
    canonical: list[bytes] = []
    previous_path = ""
    for record in records:
        match = TREE_ENTRY.fullmatch(record)
        if match is None:
            raise SourcePlanError("accepted source tree contains an unsafe entry")
        path = match.group(3).decode("ascii")
        if (
            path.startswith("/")
            or path.startswith("../")
            or "/../" in path
            or path <= previous_path
        ):
            raise SourcePlanError("accepted source tree ordering is invalid")
        previous_path = path
        canonical.append(record + b"\0")
    return len(records), hashlib.sha256(b"".join(canonical)).hexdigest()


def evaluate(
    *,
    head_commit: str,
    clean: bool,
    accepted_is_ancestor: bool,
    source_diff_clean: bool,
    tree_entries: int,
    source_pack_digest: str,
) -> dict[str, Any]:
    trusted = (
        COMMIT_SHA.fullmatch(head_commit) is not None
        and clean
        and accepted_is_ancestor
        and source_diff_clean
        and tree_entries > 0
        and re.fullmatch(r"[0-9a-f]{64}", source_pack_digest) is not None
    )
    blockers = list(MISSING_EXECUTABLE_BOUNDARIES)
    if not trusted:
        blockers.insert(0, "hosted_accepted_source_pack_not_established")
    return {
        "contract": CONTRACT,
        "source_revision": SOURCE_REVISION,
        "target_revision": TARGET_REVISION,
        "accepted_commit": ACCEPTED_COMMIT,
        "accepted_postgres_run_id": ACCEPTED_POSTGRES_RUN_ID,
        "accepted_web_run_id": ACCEPTED_WEB_RUN_ID,
        "current_commit": head_commit if COMMIT_SHA.fullmatch(head_commit) else None,
        "source_pack_digest": source_pack_digest if trusted else None,
        "source_pack_entries": tree_entries if trusted else None,
        "source_pack_matches_hosted_acceptance": trusted,
        "source_pin_bootstrap_contract": SOURCE_PIN_BOOTSTRAP_CONTRACT,
        "source_pin_bootstrap_installed": trusted,
        "fixed_migration_entrypoints_installed": trusted,
        "activation_grant_contract_installed": trusted,
        "identity_finalizer_executor_installed": trusted,
        "identity_cutover_executor_installed": trusted,
        "identity_authority_admission_writer_installed": trusted,
        "identity_disposable_role_activation_installed": trusted,
        "reviewed_identity_packet_compiler_installed": trusted,
        "reviewed_identity_distinct_purpose_signing_ceremony_installed": trusted,
        "identity_signing_credential_provisioner_installed": trusted,
        "identity_writer_freeze_evidence_writer_installed": trusted,
        "identity_privacy_cutover_evidence_writer_installed": trusted,
        "identity_privacy_cutover_observer_installed": trusted,
        "identity_semantic_cutover_packet_compiler_installed": trusted,
        "off_host_backup_writer_installed": trusted,
        "activation_executor_installed": trusted,
        "authoritative_split_phase_activation_runner_installed": trusted,
        "source_acceptance_receipt_issuable": trusted and not blockers,
        "authoritative": False,
        "enables_writes": False,
        "runs_migrations": False,
        "changes_rollout_mode": False,
        "blockers": blockers,
    }


def _run(
    command: Sequence[str],
    *,
    timeout: int = 20,
    text: bool = True,
    check: bool = True,
) -> str | bytes:
    try:
        result = subprocess.run(
            list(command),
            cwd=SOURCE_ROOT,
            check=False,
            capture_output=True,
            text=text,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SourcePlanError("source-plan git probe failed") from error
    if check and result.returncode != 0:
        raise SourcePlanError("source-plan git probe failed")
    return result.stdout


def _succeeds(command: Sequence[str], *, timeout: int = 20) -> bool:
    try:
        result = subprocess.run(
            list(command),
            cwd=SOURCE_ROOT,
            check=False,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SourcePlanError("source-plan git probe failed") from error
    return result.returncode == 0


def live_report() -> Mapping[str, Any]:
    head = str(_run(["git", "rev-parse", "HEAD"])).strip()
    clean = (
        str(_run(["git", "status", "--porcelain", "--untracked-files=all"])).strip()
        == ""
    )
    accepted_exists = _succeeds(
        ["git", "cat-file", "-e", f"{ACCEPTED_COMMIT}^{{commit}}"]
    )
    if not accepted_exists:
        raise SourcePlanError("hosted accepted commit is unavailable")
    accepted_is_ancestor = _succeeds(
        ["git", "merge-base", "--is-ancestor", ACCEPTED_COMMIT, "HEAD"]
    )
    source_diff_clean = _succeeds(
        [
            "git",
            "diff",
            "--quiet",
            ACCEPTED_COMMIT,
            "--",
            *(path for path in ACTIVATION_PATHS if path != SOURCE_PLAN_RELATIVE_PATH),
        ]
    )
    accepted_plan = _run(
        ["git", "show", f"{ACCEPTED_COMMIT}:{SOURCE_PLAN_RELATIVE_PATH}"],
        text=False,
    )
    try:
        current_plan = (SOURCE_ROOT / SOURCE_PLAN_RELATIVE_PATH).read_bytes()
    except OSError as error:
        raise SourcePlanError("source-plan pin file is unavailable") from error
    if not isinstance(
        accepted_plan, bytes
    ) or not source_plan_matches_accepted_pin_only(accepted_plan, current_plan):
        source_diff_clean = False
    tree = _run(
        [
            "git",
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            ACCEPTED_COMMIT,
            "--",
            *ACTIVATION_PATHS,
        ],
        text=False,
    )
    if not isinstance(tree, bytes):
        raise SourcePlanError("accepted source tree probe is invalid")
    entries, digest = parse_tree(tree)
    return evaluate(
        head_commit=head,
        clean=clean,
        accepted_is_ancestor=accepted_is_ancestor,
        source_diff_clean=source_diff_clean,
        tree_entries=entries,
        source_pack_digest=digest,
    )


def main() -> int:
    if len(sys.argv) != 1:
        print("phase3 activation source plan accepts no arguments", file=sys.stderr)
        return 64
    try:
        report = live_report()
    except SourcePlanError:
        print(
            "phase3 activation source plan could not establish trusted state",
            file=sys.stderr,
        )
        return 78
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
