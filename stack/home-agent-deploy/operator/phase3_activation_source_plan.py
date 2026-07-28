#!/usr/bin/env python3
"""Verify the E5k hosted-tested Phase 3 source pack without activating it.

This is a source-plan verifier, not a migration or receipt writer. It proves
that every activation-relevant tracked path still has the exact tree accepted
by the E1-E5j hosted gate, then reports the deliberately missing executable
boundaries. No successful result from this command can authorize a rollout.
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
ACCEPTED_COMMIT = "74c751628f0559452215897d1fd251e7277f0f51"
ACCEPTED_POSTGRES_RUN_ID = "30395870684"
SOURCE_REVISION = "0006a_worker_lease_arbitration"
TARGET_REVISION = "0021_parent_status_e5h"
SOURCE_ROOT = Path(__file__).resolve().parents[3]
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TREE_ENTRY = re.compile(
    rb"^(100644|100755) blob ([0-9a-f]{40})\t([A-Za-z0-9._/-]+)$"
)
ACTIVATION_PATHS = (
    "app/src/home-agent",
    "stack/home-agent-compose.yml",
    "stack/home-agent.env.example",
    "stack/home-agent-deploy/add-binding-committer-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-cutover-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-finalizer-role-secrets.sh",
    "stack/home-agent-deploy/add-identity-migration-role-secrets.sh",
    "stack/home-agent-deploy/apply-grants.sh",
    "stack/home-agent-deploy/identity-api-acl.sql",
    "stack/home-agent-deploy/materialize-secrets.sh",
    "stack/home-agent-deploy/policy/home-agent-mvp-v1.json",
    "stack/home-agent-deploy/postgres-pg_hba.conf",
    "stack/home-agent-deploy/preflight-identity-cutover-roles.sh",
    "stack/home-agent-deploy/preflight.sh",
    "stack/home-agent-deploy/provision-identity-binding-kernel-role.sh",
    "stack/home-agent-deploy/provision-identity-cutover-roles.sh",
    "stack/home-agent-deploy/provision-parent-relationship-kernel-role.sh",
    "stack/home-agent-deploy/provision-roles.sh",
    "stack/home-agent-deploy/operator/identity_finalizer_compatibility.py",
    "stack/home-agent-deploy/operator/parent_confirmation_staging.py",
    "stack/home-agent-deploy/operator/phase3_activation_preflight.py",
    "stack/home-agent-deploy/operator/phase3_evidence_receipts.py",
    "stack/home-agent-deploy/operator/principal_binding_candidate_staging.py",
    "stack/home-agent-deploy/operator/reviewed_identity_payload.py",
    "stack/services/home-agent-bff/src",
    "stack/services/home-agent-core/Dockerfile",
    "stack/services/home-agent-core/alembic",
    "stack/services/home-agent-core/app",
    "stack/services/home-agent-core/docker-entrypoint.sh",
)
MISSING_EXECUTABLE_BOUNDARIES = (
    "activation_sequencer_not_installed",
    "activation_grant_contract_not_installed",
    "identity_finalizer_executor_not_installed",
    "identity_cutover_executor_not_installed",
    "off_host_backup_writer_not_installed",
)


class SourcePlanError(RuntimeError):
    """The accepted source boundary could not be established."""


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
        "current_commit": head_commit if COMMIT_SHA.fullmatch(head_commit) else None,
        "source_pack_digest": source_pack_digest if trusted else None,
        "source_pack_entries": tree_entries if trusted else None,
        "source_pack_matches_hosted_acceptance": trusted,
        "fixed_migration_entrypoints_installed": trusted,
        "activation_executor_installed": False,
        "source_acceptance_receipt_issuable": False,
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
    clean = str(
        _run(["git", "status", "--porcelain", "--untracked-files=all"])
    ).strip() == ""
    accepted_exists = _succeeds(
        ["git", "cat-file", "-e", f"{ACCEPTED_COMMIT}^{{commit}}"]
    )
    if not accepted_exists:
        raise SourcePlanError("hosted accepted commit is unavailable")
    accepted_is_ancestor = _succeeds(
        ["git", "merge-base", "--is-ancestor", ACCEPTED_COMMIT, "HEAD"]
    )
    source_diff_clean = _succeeds(
        ["git", "diff", "--quiet", ACCEPTED_COMMIT, "--", *ACTIVATION_PATHS]
    )
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
