#!/usr/bin/env python3
"""Run the E1-E5c scaffold gate against disposable PostgreSQL 17."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
import uuid


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "stack/services/home-agent-core"
CORE_CONTAINER_ROOT = "/workspace/stack/services/home-agent-core"
TEST_DOCKERFILE = CORE / "Dockerfile.postgres-test"
POSTGRES_IMAGE = (
    "postgres:17.10-bookworm@sha256:"
    "17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a"
)
OWNER = "home_agent_owner"
BASE_DATABASE = "home_agent"
ADMIN_DATABASE = "postgres"
ADMISSION_TEMPLATE = "e1_template_0007"
REVISION_0006A = "0006a_worker_lease_arbitration"
REVISION_0007 = "0007_phase3_identity_authority"
REVISION_0010 = "0010_identity_erasure_source"
REVISION_0011 = "0011_identity_erasure_e1"
REVISION_0012 = "0012_identity_erasure_e2"
REVISION_0013 = "0013_identity_finalizer_e3"
REVISION_0014 = "0014_identity_cutover_e4"
REVISION_0015 = "0015_current_authority_e5a"
REVISION_0016 = "0016_principal_binding_e5b"
REVISION_0017 = "0017_authenticated_binding_e5c"
E4_SUCCESS_DOCUMENT_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DOCUMENT_B64"
E4_SUCCESS_ADMISSION_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_ADMISSION_ID"
E4_SCAFFOLD_OWNER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL"
)
E4_SCAFFOLD_CUTOVER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL"
)
E4_LEDGER_WORKER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CUTOVER_E4_LEDGER_WORKER_DATABASE_URL"
)
E4_FIXTURE_MOUNT = "/run/e4-fixture"
E4_FIXTURE_DOCUMENT_FILE = "document.b64"
E4_FIXTURE_ADMISSION_FILE = "admission_id"
E5_OWNER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_OWNER_DATABASE_URL"
)
E5_AUTHORITY_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_DATABASE_URL"
)
E5B_OWNER_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_OWNER_DATABASE_URL"
)
E5B_COMMITTER_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_COMMITTER_DATABASE_URL"
)
E5C_OPERATOR_DATABASE_ENV = (
    "TEST_PHASE3_PRINCIPAL_BINDING_ADAPTER_E5C_OPERATOR_DATABASE_URL"
)
CATALOG_DIGEST_CONTRACTS = (
    (
        "e3",
        "123326a4620d3dd123773819d95255e40813a5a949f406570252ff1f7031f29a",
        "identity finalizer E3 catalog manifest mismatch",
    ),
    (
        "e4",
        "a96aeb68c7c5656988088ae74539760c6a811320849f01c122e02141f87eff27",
        "identity cutover E4 catalog admission is pending reviewed digest",
    ),
    (
        "e5a",
        "adfd664ccb07372107649fab7275d1d3040ebe498813fc69af27a4e7336cd084",
        "identity current-authority E5 catalog admission digest mismatch",
    ),
    (
        "e5b",
        "01c11885e7ae4f5c336ee7e43a15a481396819e40a359124e0330fd113d189fb",
        "identity principal-binding E5b catalog admission digest mismatch",
    ),
)
RUN_LABEL = "com.engineeredlighting.home-agent-e1.run"
MANAGED_LABEL = "com.engineeredlighting.home-agent-e1.managed"
PHASE_LABEL = "com.engineeredlighting.home-agent-e1.phase"
SENTINEL_SETTING = "home_agent_e1.run_id"
SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
SYSTEM_ID_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_SYSTEM_IDENTIFIER"
ALLOWLIST_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_DATABASE_ALLOWLIST"
QUARANTINED_HOSTNAMES = frozenset(
    {
        "engineeredlightingserver1",
        "home-app",
    }
)
GITHUB_HOSTED_LINUX_FLAG = "--github-hosted-linux"
GITHUB_HOSTED_LINUX_ENVIRONMENT = "HOME_AGENT_E1_RUNNER_ENVIRONMENT"
GITHUB_HOSTED_LINUX_CONTEXT = {
    "CI": "true",
    "GITHUB_ACTIONS": "true",
    "RUNNER_OS": "Linux",
    GITHUB_HOSTED_LINUX_ENVIRONMENT: "github-hosted",
}
CLIENT_CONTAINER_LIMITS = (
    "--cpus",
    "2",
    "--memory",
    "1536m",
    "--memory-swap",
    "1536m",
    "--pids-limit",
    "256",
    "--ulimit",
    "nofile=4096:4096",
    "--security-opt",
    "no-new-privileges=true",
)
POSTGRES_CONTAINER_LIMITS = (
    "--cpus",
    "2",
    "--memory",
    "2g",
    "--memory-swap",
    "2g",
    "--pids-limit",
    "512",
    "--ulimit",
    "nofile=8192:8192",
    "--security-opt",
    "no-new-privileges=true",
)
CLIENT_CHURN_COOLDOWN_SECONDS = 0.5
REMOTE_DOCKER_ENV = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "BUILDKIT_HOST",
    "BUILDX_BUILDER",
)
SECRET_NAMES = (
    "postgres_owner_password",
    "postgres_api_password",
    "postgres_binding_operator_password",
    "postgres_binding_committer_password",
    "postgres_identity_migration_password",
    "postgres_identity_finalizer_password",
    "postgres_identity_cutover_password",
    "postgres_ingest_password",
    "postgres_worker_password",
    "postgres_erasure_password",
    "postgres_rollout_password",
    "postgres_backup_password",
)
E2_RUNTIME_ROLE_URLS = (
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_API_DATABASE_URL",
        "home_agent_api",
        "postgres_api_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_BINDING_OPERATOR_DATABASE_URL",
        "home_agent_binding_operator",
        "postgres_binding_operator_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_BINDING_COMMITTER_DATABASE_URL",
        "home_agent_binding_committer",
        "postgres_binding_committer_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_INGEST_DATABASE_URL",
        "home_agent_ingest",
        "postgres_ingest_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_WORKER_DATABASE_URL",
        "home_agent_worker",
        "postgres_worker_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_ERASURE_DATABASE_URL",
        "home_agent_erasure",
        "postgres_erasure_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_ROLLOUT_DATABASE_URL",
        "home_agent_rollout",
        "postgres_rollout_password",
    ),
    (
        "TEST_PHASE3_IDENTITY_ERASURE_E2_BACKUP_DATABASE_URL",
        "home_agent_backup",
        "postgres_backup_password",
    ),
)
BUILD_CONTEXT_FILES = (
    "stack/home-agent-compose.yml",
    "stack/home-agent.env.example",
    "stack/services/home-agent-core/Dockerfile.postgres-test",
    "stack/services/home-agent-core/alembic.ini",
    "stack/services/home-agent-core/pytest.ini",
    "stack/services/home-agent-core/requirements.txt",
    "stack/services/home-agent-core/requirements-dev.txt",
    "stack/services/home-agent-core/docker-entrypoint.sh",
    "stack/services/home-agent-bff/src/bff.mjs",
    "stack/home-agent-deploy/provision-roles.sh",
    "stack/home-agent-deploy/apply-grants.sh",
    "stack/home-agent-deploy/add-binding-committer-role-secrets.sh",
    "stack/home-agent-deploy/identity-api-acl.sql",
    "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md",
    "stack/home-agent-deploy/IDENTITY-CUTOVER-ROLE.md",
    "stack/home-agent-deploy/bootstrap-secrets.sh",
    "stack/home-agent-deploy/materialize-secrets.sh",
    "stack/home-agent-deploy/preflight.sh",
    "stack/home-agent-deploy/add-identity-cutover-role-secrets.sh",
    "stack/home-agent-deploy/add-binding-committer-role-secrets.sh",
    "stack/home-agent-deploy/preflight-identity-cutover-roles.sh",
    "stack/home-agent-deploy/provision-identity-cutover-roles.sh",
    "stack/home-agent-deploy/provision-identity-binding-kernel-role.sh",
    "stack/home-agent-deploy/postgres-pg_hba.conf",
    "stack/home-agent-deploy/test-identity-cutover-secret-lifecycle.sh",
    "tests/home_agent/test_identity_erasure_kernel_foundation_deployment_contract.py",
    "tests/home_agent/test_apply_grants_revision_0006a_contract.py",
    "tests/home_agent/test_e1_postgres_gate_contract.py",
    "stack/services/home-agent-core/tests/e1_postgres_harness.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0006a_worker_lease_arbitration.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_erasure_admission_postgres.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0012_identity_person_erasure_tombstone.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0013_identity_finalizer_kernel.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0014_identity_semantic_cutover_e4.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0015_identity_current_authority_e5.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0016_principal_binding_kernel_e5b.py",
    "stack/services/home-agent-core/alembic/versions/"
    "0017_authenticated_binding_e5c.py",
    "stack/services/home-agent-core/app/identity_erasure_schema.py",
    "stack/home-agent-deploy/operator/reviewed_identity_payload.py",
    "stack/home-agent-deploy/operator/principal_binding_candidate_staging.py",
    "stack/home-agent-deploy/operator/REVIEWED-IDENTITY-PAYLOAD.md",
    "stack/services/home-agent-core/tests/test_identity_person_restore_replay.py",
    "stack/services/home-agent-core/tests/test_ledger_versions.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_erasure_e2_runtime_postgres.py",
    "stack/services/home-agent-core/tests/" "test_phase3_identity_erasure_e2_schema.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_finalizer_e3_runtime_postgres.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_finalizer_e3_schema.py",
    "tests/home_agent/test_identity_erasure_e2_deployment_contract.py",
    "tests/home_agent/test_identity_finalizer_e3_deployment_contract.py",
    "tests/home_agent/test_identity_cutover_e4_deployment_contract.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_cutover_e4_scaffold_postgres.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_semantic_cutover_e4_runtime_postgres.py",
    "stack/services/home-agent-core/tests/"
    "seed_phase3_identity_semantic_cutover_e4_success.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_semantic_cutover_e4_schema.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_current_authority_e5_schema.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_current_authority_e5_runtime_postgres.py",
    "tests/home_agent/test_identity_current_authority_e5_deployment_contract.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_principal_binding_kernel_e5b_schema.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_principal_binding_kernel_e5b_runtime_postgres.py",
    "tests/home_agent/test_principal_binding_kernel_e5b_deployment_contract.py",
    "tests/home_agent/test_identity_binding_kernel_role_ceremony_contract.py",
    "tests/home_agent/test_principal_binding_authority_boundary_contract.py",
    "tests/home_agent/"
    "test_principal_binding_adapter_e5c_deployment_contract.py",
    "tools/run-home-agent-e1-postgres-gate.py",
    ".github/workflows/home-agent-e1-postgres.yml",
)
BUILD_CONTEXT_TREES = (
    "stack/services/home-agent-core/app",
    "stack/services/home-agent-core/alembic",
    "stack/services/home-agent-core/tests",
)
IGNORED_CONTEXT_NAMES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
REVIEWED_CONTEXT_SUFFIXES = {
    ".ini",
    ".mako",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".txt",
    ".yml",
}
REVIEWED_CONTEXT_FILENAMES = {
    "Dockerfile.postgres-test",
    "bff.mjs",
    "home-agent.env.example",
    "postgres-pg_hba.conf",
}
SENSITIVE_CONTEXT_COMPONENTS = {
    ".gnupg",
    ".ssh",
    "credentials",
    "private",
    "runtime",
    "secrets",
}
SENSITIVE_CONTEXT_FILENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
}
SENSITIVE_CONTEXT_SUFFIXES = {
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}
REVIEWED_GIT_FILE_MODES = {"100644", "100755"}
MAX_CONTEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_TOTAL_BYTES = 16 * 1024 * 1024


class GateFailure(RuntimeError):
    pass


@dataclass
class GateState:
    sentinel: str
    suffix: str
    endpoint: str
    docker_environment: dict[str, str]
    test_image: str
    client_sequence: int = 0
    interrupted: bool = False
    phases: set[str] = field(default_factory=set)

    @property
    def name_prefix(self) -> str:
        return f"home-agent-e1-{self.suffix}-"

    def docker(self, *arguments: str) -> list[str]:
        return ["docker", "--host", self.endpoint, *arguments]

    def next_client_name(self, phase: str) -> str:
        self.client_sequence += 1
        return f"{self.name_prefix}{phase}-client-{self.client_sequence:03d}"


@dataclass(frozen=True)
class Phase:
    name: str
    network: str
    postgres_container: str
    system_identifier: str


def _run(
    command: list[str],
    *,
    label: str,
    timeout: int = 300,
    check: bool = True,
    environment: dict[str, str] | None = None,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GateFailure(f"{label} timed out after {timeout} seconds") from error
    if check and result.returncode != 0:
        output = result.stdout.rstrip()
        if output:
            print(output, file=sys.stderr)
        raise GateFailure(f"{label} failed with exit code {result.returncode}")
    return result


def _sanitized_docker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in REMOTE_DOCKER_ENV:
        environment.pop(name, None)
    return environment


def _validate_local_docker() -> tuple[str, dict[str, str]]:
    contaminated = [name for name in REMOTE_DOCKER_ENV if os.environ.get(name)]
    if contaminated:
        raise GateFailure(
            "Docker endpoint override environment is forbidden: "
            + ", ".join(contaminated)
        )
    environment = _sanitized_docker_environment()
    context = _run(
        ["docker", "context", "show"],
        label="Docker context discovery",
        environment=environment,
    ).stdout.strip()
    if not context:
        raise GateFailure("Docker returned an empty current context")
    endpoint = _run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        label="Docker endpoint inspection",
        environment=environment,
    ).stdout.strip()
    if not endpoint.startswith(("unix://", "npipe://")):
        raise GateFailure(
            "E1 gate requires a local unix:// or npipe:// Docker endpoint; "
            f"received {endpoint!r}"
        )
    daemon_name = _run(
        [
            "docker",
            "--host",
            endpoint,
            "info",
            "--format",
            "{{.Name}}",
        ],
        label="Docker daemon identity inspection",
        environment=environment,
    ).stdout.strip()
    if not daemon_name:
        raise GateFailure("Docker returned an empty daemon name")
    _assert_name_not_quarantined(daemon_name, source="Docker daemon")
    return endpoint, environment


def _assert_name_not_quarantined(hostname: str, *, source: str) -> None:
    observed = hostname.strip()
    normalized = observed.split(".", 1)[0].casefold()
    if normalized in QUARANTINED_HOSTNAMES:
        raise GateFailure(
            f"the E1/E2 Docker gate is quarantined for {source} "
            f"{observed!r} after the 2026-07-12 unclean host halt; "
            "run this gate in CI or on a disposable test host"
        )


def _assert_host_not_quarantined(hostname: str | None = None) -> None:
    _assert_name_not_quarantined(
        hostname or socket.gethostname(),
        source="host",
    )


def _assert_execution_admitted(
    *,
    hostname: str | None = None,
    platform: str | None = None,
    arguments: tuple[str, ...] | None = None,
    environment: dict[str, str] | None = None,
) -> None:
    _assert_host_not_quarantined(hostname)
    observed_platform = platform or sys.platform
    observed_arguments = tuple(sys.argv[1:] if arguments is None else arguments)
    observed_environment = os.environ if environment is None else environment

    if observed_platform.startswith("linux"):
        if observed_arguments != (GITHUB_HOSTED_LINUX_FLAG,):
            raise GateFailure(
                "Linux execution is disabled outside the explicitly admitted "
                "GitHub-hosted gate"
            )
        mismatches = [
            name
            for name, expected in GITHUB_HOSTED_LINUX_CONTEXT.items()
            if observed_environment.get(name) != expected
        ]
        if mismatches:
            raise GateFailure(
                "GitHub-hosted Linux admission context is missing or invalid: "
                + ", ".join(sorted(mismatches))
            )
        return

    if observed_arguments:
        raise GateFailure(
            f"{GITHUB_HOSTED_LINUX_FLAG} is valid only in the pinned "
            "GitHub-hosted Linux workflow"
        )


def _canonical_context_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or path.is_absolute()
        or path.as_posix() != relative_path
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise GateFailure(f"non-canonical build-context path: {relative_path!r}")
    return path


def _validate_context_path_policy(relative_path: str) -> None:
    path = _canonical_context_path(relative_path)
    lowered_parts = {part.casefold() for part in path.parts}
    lowered_name = path.name.casefold()
    suffix = path.suffix.casefold()
    if lowered_parts & IGNORED_CONTEXT_NAMES:
        raise GateFailure(f"generated/cache path is forbidden: {relative_path}")
    if (
        lowered_parts & SENSITIVE_CONTEXT_COMPONENTS
        or lowered_name in SENSITIVE_CONTEXT_FILENAMES
        or lowered_name.startswith(".env.")
        or suffix in SENSITIVE_CONTEXT_SUFFIXES
    ):
        raise GateFailure(f"sensitive build-context path is forbidden: {relative_path}")
    if (
        path.name not in REVIEWED_CONTEXT_FILENAMES
        and suffix not in REVIEWED_CONTEXT_SUFFIXES
    ):
        raise GateFailure(f"unreviewed or binary build-context path: {relative_path}")


def _git_index_entries(pathspecs: tuple[str, ...]) -> dict[str, str]:
    repository_root = _run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        label="Git worktree root verification",
        timeout=30,
    ).stdout.strip()
    if not repository_root or Path(repository_root).resolve() != ROOT.resolve():
        raise GateFailure("E1 gate must run from the reviewed Git worktree root")
    result = _run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--stage",
            "-z",
            "--",
            *pathspecs,
        ],
        label="Git-index build-context enumeration",
        timeout=30,
    )
    entries: dict[str, str] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        try:
            metadata, relative_path = record.split("\t", 1)
            mode, _object_id, stage = metadata.split(" ", 2)
        except ValueError as error:
            raise GateFailure("Git returned a malformed build-context entry") from error
        _canonical_context_path(relative_path)
        if stage != "0" or relative_path in entries:
            raise GateFailure(f"unmerged or duplicate Git-index entry: {relative_path}")
        if mode not in REVIEWED_GIT_FILE_MODES:
            raise GateFailure(
                f"symlink or special Git-index mode is forbidden: {relative_path}"
            )
        entries[relative_path] = mode
    return entries


def _git_untracked_entries(pathspecs: tuple[str, ...]) -> set[str]:
    repository_root = _run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        label="Git worktree root verification",
        timeout=30,
    ).stdout.strip()
    if not repository_root or Path(repository_root).resolve() != ROOT.resolve():
        raise GateFailure("E1 gate must run from the reviewed Git worktree root")
    result = _run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *pathspecs,
        ],
        label="Git untracked build-context audit",
        timeout=30,
    )
    entries: set[str] = set()
    for relative_path in result.stdout.split("\0"):
        if not relative_path:
            continue
        _canonical_context_path(relative_path)
        if relative_path in entries:
            raise GateFailure(f"duplicate untracked Git path: {relative_path}")
        entries.add(relative_path)
    return entries


def _copy_context_file(
    relative_path: str,
    destination_root: Path,
    *,
    index_mode: str | None,
) -> int:
    _validate_context_path_policy(relative_path)
    source = ROOT / relative_path
    current = ROOT
    for part in PurePosixPath(relative_path).parts:
        current /= part
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise GateFailure(f"symlink or junction is forbidden: {relative_path}")
    if not source.is_file() or source.is_symlink():
        raise GateFailure(f"unsafe or missing build-context file: {relative_path}")
    try:
        source.resolve(strict=True).relative_to(ROOT.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise GateFailure(
            f"build-context file escapes the reviewed worktree: {relative_path}"
        ) from error
    content = source.read_bytes()
    if len(content) > MAX_CONTEXT_FILE_BYTES:
        raise GateFailure(f"oversized build-context file: {relative_path}")
    if b"\0" in content:
        raise GateFailure(f"binary build-context content is forbidden: {relative_path}")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GateFailure(
            f"non-UTF-8 build-context content is forbidden: {relative_path}"
        ) from error
    destination = destination_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o755 if index_mode == "100755" else 0o644)
    return len(content)


def _tracked_context_tree_files(
    relative_path: str, index_entries: dict[str, str]
) -> set[str]:
    _canonical_context_path(relative_path)
    source_root = ROOT / relative_path
    if not source_root.is_dir() or source_root.is_symlink():
        raise GateFailure(f"unsafe or missing build-context tree: {relative_path}")
    prefix = relative_path.rstrip("/") + "/"
    tracked = {path for path in index_entries if path.startswith(prefix)}
    if not tracked:
        raise GateFailure(f"Git-indexed build-context tree is empty: {relative_path}")
    return tracked


def _prepare_build_context(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    pathspecs = BUILD_CONTEXT_FILES + BUILD_CONTEXT_TREES
    index_entries = _git_index_entries(pathspecs)
    unexpected_untracked = _git_untracked_entries(pathspecs) - set(BUILD_CONTEXT_FILES)
    if unexpected_untracked:
        raise GateFailure(
            "unexpected untracked build-context source is forbidden: "
            + ", ".join(sorted(unexpected_untracked))
        )
    manifest = set(BUILD_CONTEXT_FILES)
    for relative_path in BUILD_CONTEXT_TREES:
        manifest.update(_tracked_context_tree_files(relative_path, index_entries))
    casefold_manifest: dict[str, str] = {}
    for relative_path in manifest:
        previous = casefold_manifest.setdefault(relative_path.casefold(), relative_path)
        if previous != relative_path:
            raise GateFailure(
                "case-colliding build-context paths are forbidden: "
                f"{previous!r}, {relative_path!r}"
            )
    total_bytes = 0
    for relative_path in sorted(manifest):
        total_bytes += _copy_context_file(
            relative_path,
            directory,
            index_mode=index_entries.get(relative_path),
        )
        if total_bytes > MAX_CONTEXT_TOTAL_BYTES:
            raise GateFailure("generated Docker build context exceeds the reviewed cap")
    copied = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    if (
        not copied
        or copied != manifest
        or any(path.is_symlink() for path in directory.rglob("*"))
    ):
        raise GateFailure("generated Docker build context is empty or unsafe")


def _write_secrets(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    for name in SECRET_NAMES:
        path = directory / name
        path.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        try:
            path.chmod(0o400)
        except OSError:
            pass


def _labels(state: GateState, phase: str) -> list[str]:
    return [
        "--label",
        f"{MANAGED_LABEL}=true",
        "--label",
        f"{RUN_LABEL}={state.sentinel}",
        "--label",
        f"{PHASE_LABEL}={phase}",
    ]


def _docker_run(
    state: GateState,
    image: str,
    *,
    phase: str,
    network: str,
    secrets_directory: Path,
    environment: dict[str, str],
    command: list[str],
    label: str,
    timeout: int = 300,
    check: bool = True,
    fixture_directory: Path | None = None,
    fixture_read_only: bool = True,
    run_as_host_user: bool = False,
) -> subprocess.CompletedProcess[str]:
    name = state.next_client_name(phase)
    arguments = state.docker(
        "run",
        "--rm",
        "--name",
        name,
        *_labels(state, phase),
        *CLIENT_CONTAINER_LIMITS,
        "--network",
        network,
    )
    if run_as_host_user and os.name == "posix":
        arguments.extend(("--user", f"{os.getuid()}:{os.getgid()}"))
    arguments.extend(
        (
            "--mount",
            f"type=bind,source={secrets_directory},"
            "target=/run/secrets,readonly",
        )
    )
    if fixture_directory is not None:
        if fixture_directory.is_symlink() or not fixture_directory.is_dir():
            raise GateFailure("E4 fixture mount must be a real directory")
        fixture_source = fixture_directory.resolve(strict=True)
        fixture_mount = (
            f"type=bind,source={fixture_source},target={E4_FIXTURE_MOUNT}"
        )
        if fixture_read_only:
            fixture_mount += ",readonly"
        arguments.extend(("--mount", fixture_mount))
    arguments.extend(
        ("--workdir", CORE_CONTAINER_ROOT)
    )
    for key, value in environment.items():
        arguments.extend(("--env", f"{key}={value}"))
    arguments.append(image)
    arguments.extend(command)
    result = _run(
        arguments,
        label=label,
        timeout=timeout,
        check=check,
        environment=state.docker_environment,
    )
    time.sleep(CLIENT_CHURN_COOLDOWN_SECONDS)
    return result


def _client_environment(database: str) -> dict[str, str]:
    return {
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGDATABASE": database,
        "PGUSER": OWNER,
        "POSTGRES_OWNER_PASSWORD_FILE": "/run/secrets/postgres_owner_password",
    }


def _psql(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    *,
    database: str,
    sql: str,
    label: str,
) -> subprocess.CompletedProcess[str]:
    return _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=[
            "sh",
            "-eu",
            "-c",
            "export PGPASSWORD=\"$(tr -d '\\r\\n' < "
            '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
            'psql -At -v ON_ERROR_STOP=1 --command "$1"',
            "e1-psql",
            sql,
        ],
        label=label,
    )


def _alembic(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
    revision: str,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=[
            "sh",
            "-eu",
            "-c",
            "password=\"$(tr -d '\\r\\n' < "
            '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
            'export HOME_AGENT_DATABASE_URL="postgresql+psycopg://'
            f'{OWNER}:$password@postgres:5432/{database}"; '
            'exec python -m alembic upgrade "$1"',
            "e1-alembic",
            revision,
        ],
        label=f"migration of {database} to {revision}",
        timeout=600,
    )


def _alembic_downgrade(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
    revision: str,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=[
            "sh",
            "-eu",
            "-c",
            "password=\"$(tr -d '\\r\\n' < "
            '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
            'export HOME_AGENT_DATABASE_URL="postgresql+psycopg://'
            f'{OWNER}:$password@postgres:5432/{database}"; '
            'exec python -m alembic downgrade "$1"',
            "e1-alembic-downgrade",
            revision,
        ],
        label=f"downgrade of {database} to {revision}",
        timeout=600,
    )


def _alembic_expect_failure(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
    *,
    action: str,
    revision: str,
    expected_output: str,
    failure_label: str,
) -> None:
    if action not in {"upgrade", "downgrade"}:
        raise GateFailure("invalid rejected Alembic action")
    result = _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=[
            "sh",
            "-eu",
            "-c",
            "password=\"$(tr -d '\\r\\n' < "
            '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
            'export HOME_AGENT_DATABASE_URL="postgresql+psycopg://'
            f'{OWNER}:$password@postgres:5432/{database}"; '
            'exec python -m alembic "$1" "$2"',
            "e1-alembic-rejected",
            action,
            revision,
        ],
        label=failure_label,
        timeout=600,
        check=False,
    )
    if result.returncode == 0:
        raise GateFailure(f"{failure_label} unexpectedly succeeded")
    if expected_output not in result.stdout:
        raise GateFailure(
            f"{failure_label} failed without the reviewed contract marker"
        )


def _apply_grants(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=["sh", "/workspace/stack/home-agent-deploy/apply-grants.sh"],
        label=f"grant application for {database}",
    )


def _apply_grants_expect_failure(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
    *,
    expected_output: str,
    failure_label: str = "tampered E2 helper",
    redact_output: bool = False,
) -> None:
    result = _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(database),
        command=["sh", "/workspace/stack/home-agent-deploy/apply-grants.sh"],
        label=f"rejected grant application for {database}",
        check=False,
    )
    if result.returncode == 0:
        raise GateFailure(f"{failure_label} unexpectedly passed grant replay")
    if expected_output not in result.stdout:
        output = result.stdout.rstrip()
        if output and not redact_output:
            print(output, file=sys.stderr)
        raise GateFailure(
            f"{failure_label} failed without the reviewed contract marker"
        )


def _discover_changed_catalog_digests(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
) -> None:
    """Emit only changed catalog digests, then require a reviewed source pin."""
    script = "/workspace/stack/home-agent-deploy/apply-grants.sh"
    temporary_script = "/tmp/apply-grants-catalog-discovery.sh"
    replacements: dict[str, str] = {}
    discovered: dict[str, str] = {}
    activation_stop = "identity cutover E4 activation contract is not installed"

    for _attempt in range(len(CATALOG_DIGEST_CONTRACTS) + 1):
        commands = [f'cp "{script}" "{temporary_script}"']
        for expected, actual in replacements.items():
            commands.append(
                f"sed -i 's/{expected}/{actual}/g' " f'"{temporary_script}"'
            )
        commands.append(f'exec sh "{temporary_script}"')
        result = _docker_run(
            state,
            state.test_image,
            phase=phase.name,
            network=phase.network,
            secrets_directory=secrets_directory,
            environment=_client_environment(database),
            command=["sh", "-eu", "-c", "; ".join(commands)],
            label=f"catalog digest discovery for {database}",
            check=False,
        )
        if result.returncode == 0:
            raise GateFailure("catalog digest discovery unexpectedly activated E5b")
        if activation_stop in result.stdout:
            break

        matches: list[tuple[str, str, str]] = []
        for layer, expected, message in CATALOG_DIGEST_CONTRACTS:
            pattern = (
                rf"ERROR:\s+{re.escape(message)}\r?\n"
                rf"DETAIL:\s+expected=([0-9a-f]{{64}}) "
                rf"actual=([0-9a-f]{{64}})(?=\s|$)"
            )
            for match in re.finditer(pattern, result.stdout):
                matches.append((layer, match.group(1), match.group(2)))
            if layer in discovered and expected not in replacements:
                raise GateFailure("catalog discovery replacement state is invalid")
        if len(matches) != 1:
            raise GateFailure(
                "catalog digest discovery failed without one exact redacted digest"
            )
        layer, observed_expected, actual = matches[0]
        contract = next(
            item for item in CATALOG_DIGEST_CONTRACTS if item[0] == layer
        )
        source_expected = contract[1]
        if (
            layer in discovered
            or observed_expected != replacements.get(source_expected, source_expected)
            or actual == observed_expected
        ):
            raise GateFailure("catalog digest discovery returned an invalid transition")
        replacements[source_expected] = actual
        discovered[layer] = actual
    else:
        raise GateFailure("catalog digest discovery exceeded the reviewed layer bound")

    if discovered:
        for layer in (item[0] for item in CATALOG_DIGEST_CONTRACTS):
            if layer in discovered:
                print(f"CATALOG_DIGEST layer={layer} sha256={discovered[layer]}")
        raise GateFailure(
            "catalog digests changed; review and pin the emitted fingerprints"
        )


def _provision_roles(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(BASE_DATABASE),
        command=["sh", "/workspace/stack/home-agent-deploy/provision-roles.sh"],
        label=f"role provisioning for {phase.name}",
    )


def _provision_identity_cutover_roles(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment={
            **_client_environment(BASE_DATABASE),
            "POSTGRES_OWNER_PASSWORD_FILE": (
                "/run/secrets/postgres_owner_password"
            ),
        },
        command=[
            "sh",
            "/workspace/stack/home-agent-deploy/"
            "provision-identity-cutover-roles.sh",
        ],
        label=f"additive E4 role ceremony for {phase.name}",
    )


def _provision_identity_binding_kernel_role(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment={
            **_client_environment(BASE_DATABASE),
            "POSTGRES_OWNER_PASSWORD_FILE": (
                "/run/secrets/postgres_owner_password"
            ),
        },
        command=[
            "sh",
            "/workspace/stack/home-agent-deploy/"
            "provision-identity-binding-kernel-role.sh",
        ],
        label=f"additive E5b kernel-role ceremony for {phase.name}",
    )


def _database_url_shell_export(
    name: str,
    database: str,
    role: str = OWNER,
    password_secret: str = "postgres_owner_password",
) -> str:
    return (
        f'export {name}="postgresql+psycopg://{role}:'
        f"$(tr -d '\\r\\n' < /run/secrets/{password_secret})"
        f'@postgres:5432/{database}"; '
    )


def _direct_psycopg_database_url_shell_export(
    name: str,
    database: str,
    role: str = OWNER,
    password_secret: str = "postgres_owner_password",
) -> str:
    reviewed_contracts = {
        E4_SCAFFOLD_OWNER_DATABASE_ENV: (
            BASE_DATABASE,
            OWNER,
            "postgres_owner_password",
        ),
        E4_SCAFFOLD_CUTOVER_DATABASE_ENV: (
            BASE_DATABASE,
            "home_agent_identity_cutover",
            "postgres_identity_cutover_password",
        ),
    }
    if reviewed_contracts.get(name) != (database, role, password_secret):
        raise GateFailure("unreviewed direct psycopg database URL export")
    return (
        f'export {name}="postgresql://{role}:'
        f"$(tr -d '\\r\\n' < /run/secrets/{password_secret})"
        f'@postgres:5432/{database}"; '
    )


def _validate_e4_fixture_material(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise GateFailure("E4 fixture output is not a real directory")
    expected_names = {
        E4_FIXTURE_DOCUMENT_FILE,
        E4_FIXTURE_ADMISSION_FILE,
    }
    entries = list(directory.iterdir())
    if {entry.name for entry in entries} != expected_names:
        raise GateFailure("E4 fixture output has missing or unexpected files")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise GateFailure("E4 fixture output contains a non-regular file")
        if entry.resolve(strict=True).parent != directory.resolve(strict=True):
            raise GateFailure("E4 fixture output escapes its directory")
        if os.name == "posix" and stat.S_IMODE(entry.stat().st_mode) & 0o077:
            raise GateFailure("E4 fixture output permissions are too broad")
        if os.name == "posix" and entry.stat().st_uid != os.getuid():
            raise GateFailure("E4 fixture output is not owned by the gate user")

    try:
        document_lines = (
            directory.joinpath(E4_FIXTURE_DOCUMENT_FILE)
            .read_text(encoding="ascii")
            .splitlines()
        )
        admission_lines = (
            directory.joinpath(E4_FIXTURE_ADMISSION_FILE)
            .read_text(encoding="ascii")
            .splitlines()
        )
        if len(document_lines) != 1 or len(admission_lines) != 1:
            raise ValueError("fixture files must each contain exactly one line")
        document = base64.b64decode(document_lines[0], validate=True)
        admission_id = uuid.UUID(admission_lines[0])
    except (OSError, UnicodeError, ValueError) as error:
        raise GateFailure("E4 fixture output is malformed") from error
    if not 2 <= len(document) <= 1048576:
        raise GateFailure("E4 fixture document is outside the admitted bound")
    if (
        admission_lines[0] != str(admission_id)
        or admission_id.version != 7
        or admission_id.variant != uuid.RFC_4122
    ):
        raise GateFailure("E4 fixture admission identity is not UUIDv7")


def _fixture_file_shell_export(name: str, filename: str) -> str:
    allowed = {
        E4_SUCCESS_DOCUMENT_ENV: E4_FIXTURE_DOCUMENT_FILE,
        E4_SUCCESS_ADMISSION_ENV: E4_FIXTURE_ADMISSION_FILE,
    }
    if allowed.get(name) != filename:
        raise GateFailure("unreviewed E4 fixture environment export")
    return (
        f'test -s "{E4_FIXTURE_MOUNT}/{filename}"; '
        f'export {name}="$(tr -d \'\\r\\n\' < '
        f'"{E4_FIXTURE_MOUNT}/{filename}")"; '
    )


def _seed_e4_success_fixture(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    fixture_directory: Path,
) -> None:
    if fixture_directory.is_symlink() or not fixture_directory.is_dir():
        raise GateFailure("E4 fixture directory is not a real directory")
    if any(fixture_directory.iterdir()):
        raise GateFailure("E4 fixture directory is not empty before seeding")
    shell = _database_url_shell_export(
        "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL",
        BASE_DATABASE,
    )
    shell += _database_url_shell_export(
        "TEST_PHASE3_IDENTITY_CUTOVER_E4_FINALIZER_DATABASE_URL",
        BASE_DATABASE,
        "home_agent_identity_finalizer",
        "postgres_identity_finalizer_password",
    )
    shell += _database_url_shell_export(
        E4_LEDGER_WORKER_DATABASE_ENV,
        BASE_DATABASE,
        "home_agent_worker",
        "postgres_worker_password",
    )
    shell += (
        f'cd "{CORE_CONTAINER_ROOT}"; '
        f'export PYTHONPATH="{CORE_CONTAINER_ROOT}"; '
        "exec python -m "
        "tests.seed_phase3_identity_semantic_cutover_e4_success"
    )
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=_client_environment(ADMIN_DATABASE),
        command=["sh", "-eu", "-c", shell],
        label="seed disposable synthetic E4 admitted-success fixture",
        timeout=600,
        fixture_directory=fixture_directory,
        fixture_read_only=False,
        run_as_host_user=True,
    )
    _validate_e4_fixture_material(fixture_directory)


def _set_disposable_e4_role_login(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    *,
    role: str,
    enabled: bool,
) -> None:
    allowed_roles = {
        "home_agent_identity_finalizer",
        "home_agent_identity_cutover",
    }
    if role not in allowed_roles:
        raise GateFailure("unreviewed disposable E4 login role")
    action = "open" if enabled else "re-expire"
    if enabled:
        role_change_sql = (
            "DO $e4_bounded_login$ BEGIN "
            f"EXECUTE pg_catalog.format('ALTER ROLE {role} VALID UNTIL %L', "
            "pg_catalog.clock_timestamp() + interval '5 minutes'); "
            "END $e4_bounded_login$"
        )
    else:
        role_change_sql = (
            f"ALTER ROLE {role} VALID UNTIL '1970-01-01 00:00:00+00'"
        )
    _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=role_change_sql,
        label=f"{action} disposable E4 {role} login",
    )
    if enabled:
        bounded_window = _psql(
            state,
            phase,
            secrets_directory,
            database=BASE_DATABASE,
            sql=(
                "SELECT count(*) FROM pg_catalog.pg_roles "
                f"WHERE rolname='{role}' AND rolcanlogin "
                "AND rolvaliduntil > pg_catalog.clock_timestamp() "
                "AND rolvaliduntil <= "
                "pg_catalog.clock_timestamp() + interval '5 minutes'"
            ),
            label=f"verify bounded disposable E4 {role} login window",
        )
        if bounded_window.stdout.strip() != "1":
            raise GateFailure(
                f"disposable E4 {role} login window was not bounded"
            )
        return
    verification = _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "SELECT pg_catalog.pg_terminate_backend(activity.pid, 5000) "
            "FROM pg_catalog.pg_stat_activity AS activity "
            f"WHERE activity.usename='{role}' "
            "AND activity.pid <> pg_catalog.pg_backend_pid(); "
            "SELECT "
            "(SELECT count(*) FROM pg_catalog.pg_roles "
            f"WHERE rolname='{role}' AND rolcanlogin "
            "AND rolvaliduntil <= "
            "'1970-01-01 00:00:00+00'::timestamptz)::text "
            "|| '|' || "
            "(SELECT count(*) FROM pg_catalog.pg_stat_activity "
            f"WHERE usename='{role}')::text"
        ),
        label=f"terminate and verify disposable E4 {role} login is expired",
    )
    final_row = verification.stdout.strip().splitlines()[-1:]
    if final_row != ["1|0"]:
        raise GateFailure(
            f"disposable E4 {role} login or session remained usable"
        )


def _pytest(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    *,
    nodes: list[str],
    url_environment: dict[str, str],
    credential_url_environment: dict[str, tuple[str, str, str]] | None = None,
    direct_psycopg_url_environment: dict[str, str] | None = None,
    direct_psycopg_credential_url_environment: (
        dict[str, tuple[str, str, str]] | None
    ) = None,
    environment: dict[str, str] | None = None,
    fixture_file_environment: dict[str, str] | None = None,
    fixture_directory: Path | None = None,
    fail_fast: bool = True,
) -> None:
    shell = "password=\"$(tr -d '\\r\\n' < " '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
    for name, database in url_environment.items():
        shell += _database_url_shell_export(name, database)
    for name, (database, role, password_secret) in (
        credential_url_environment or {}
    ).items():
        shell += _database_url_shell_export(name, database, role, password_secret)
    for name, database in (direct_psycopg_url_environment or {}).items():
        shell += _direct_psycopg_database_url_shell_export(name, database)
    for name, (database, role, password_secret) in (
        direct_psycopg_credential_url_environment or {}
    ).items():
        shell += _direct_psycopg_database_url_shell_export(
            name,
            database,
            role,
            password_secret,
        )
    if fixture_file_environment:
        if fixture_directory is None:
            raise GateFailure("E4 fixture exports require a fixture directory")
        _validate_e4_fixture_material(fixture_directory)
        for name, filename in fixture_file_environment.items():
            shell += _fixture_file_shell_export(name, filename)
    fail_fast_argument = " -x" if fail_fast else ""
    shell += f'exec python -m pytest{fail_fast_argument} -q "$@"'
    client_environment = _client_environment(ADMIN_DATABASE)
    if environment:
        client_environment.update(environment)
    _docker_run(
        state,
        state.test_image,
        phase=phase.name,
        network=phase.network,
        secrets_directory=secrets_directory,
        environment=client_environment,
        command=["sh", "-eu", "-c", shell, "e1-pytest", *nodes],
        label=f"{phase.name} PostgreSQL test contracts",
        timeout=1200,
        fixture_directory=fixture_directory,
    )


def _resource_ids(
    state: GateState,
    resource: str,
    *,
    phase: str | None,
) -> list[str]:
    filters = ["--filter", f"label={RUN_LABEL}={state.sentinel}"]
    if phase is not None:
        filters.extend(("--filter", f"label={PHASE_LABEL}={phase}"))
    if resource == "container":
        command = state.docker("container", "ls", "--all", "--quiet", *filters)
    elif resource == "network":
        command = state.docker("network", "ls", "--quiet", *filters)
    elif resource == "image":
        command = state.docker("image", "ls", "--quiet", *filters)
    else:
        raise ValueError(f"unknown Docker resource kind: {resource}")
    output = _run(
        command,
        label=f"{resource} residue discovery",
        timeout=30,
        environment=state.docker_environment,
    ).stdout
    return sorted(set(output.split()))


def _inspect_resource(state: GateState, resource: str, resource_id: str) -> None:
    if resource == "container":
        command = state.docker("container", "inspect", resource_id)
    elif resource == "network":
        command = state.docker("network", "inspect", resource_id)
    elif resource == "image":
        command = state.docker("image", "inspect", resource_id)
    else:
        raise ValueError(f"unknown Docker resource kind: {resource}")
    payload = json.loads(
        _run(
            command,
            label=f"{resource} cleanup authorization inspection",
            timeout=30,
            environment=state.docker_environment,
        ).stdout
    )[0]
    if resource == "network":
        name = str(payload.get("Name", ""))
        labels = payload.get("Labels") or {}
    elif resource == "container":
        name = str(payload.get("Name", "")).lstrip("/")
        labels = (payload.get("Config") or {}).get("Labels") or {}
    else:
        tags = payload.get("RepoTags") or []
        name = state.test_image if state.test_image in tags else ""
        labels = (payload.get("Config") or {}).get("Labels") or {}
    if (
        not name.startswith(state.name_prefix)
        or labels.get(MANAGED_LABEL) != "true"
        or labels.get(RUN_LABEL) != state.sentinel
    ):
        raise GateFailure(
            f"refusing cleanup of unowned {resource} {resource_id}: {name!r}"
        )


def _cleanup_labeled(
    state: GateState,
    *,
    phase: str | None,
    include_image: bool,
) -> None:
    kinds = (
        ("container", "network", "image")
        if include_image
        else (
            "container",
            "network",
        )
    )
    last_residue: dict[str, list[str]] = {}
    for attempt in range(1, 4):
        for resource in kinds:
            resource_ids = _resource_ids(state, resource, phase=phase)
            for resource_id in resource_ids:
                _inspect_resource(state, resource, resource_id)
            if not resource_ids:
                continue
            if resource == "container":
                command = state.docker(
                    "container", "rm", "--force", "--volumes", *resource_ids
                )
            elif resource == "network":
                command = state.docker("network", "rm", *resource_ids)
            else:
                command = state.docker("image", "rm", "--force", *resource_ids)
            _run(
                command,
                label=f"labeled {resource} cleanup attempt {attempt}",
                timeout=60,
                check=False,
                environment=state.docker_environment,
            )
        last_residue = {
            resource: _resource_ids(state, resource, phase=phase) for resource in kinds
        }
        if not any(last_residue.values()):
            return
        time.sleep(attempt)
    raise GateFailure(f"Docker cleanup residue remains: {last_residue}")


def _wait_for_postgres(state: GateState, container: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = _run(
            state.docker(
                "exec",
                container,
                "pg_isready",
                "--username",
                OWNER,
                "--dbname",
                BASE_DATABASE,
            ),
            label="PostgreSQL readiness probe",
            timeout=10,
            check=False,
            environment=state.docker_environment,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise GateFailure("disposable PostgreSQL did not become ready")


def _start_phase(
    state: GateState,
    phase_name: str,
    secrets_directory: Path,
) -> Phase:
    network = f"{state.name_prefix}{phase_name}-network"
    postgres_container = f"{state.name_prefix}{phase_name}-postgres"
    state.phases.add(phase_name)
    _run(
        state.docker(
            "network",
            "create",
            "--internal",
            *_labels(state, phase_name),
            network,
        ),
        label=f"{phase_name} internal network creation",
        environment=state.docker_environment,
    )
    _run(
        state.docker(
            "run",
            "--detach",
            "--name",
            postgres_container,
            *_labels(state, phase_name),
            *POSTGRES_CONTAINER_LIMITS,
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=1g",
            "--mount",
            f"type=bind,source={secrets_directory},target=/run/secrets,readonly",
            "--env",
            f"POSTGRES_USER={OWNER}",
            "--env",
            f"POSTGRES_DB={BASE_DATABASE}",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_owner_password",
            "--env",
            "POSTGRES_INITDB_ARGS=--data-checksums",
            POSTGRES_IMAGE,
        ),
        label=f"{phase_name} PostgreSQL startup",
        environment=state.docker_environment,
    )
    _wait_for_postgres(state, postgres_container)
    provisional = Phase(phase_name, network, postgres_container, "")
    version_and_id = _psql(
        state,
        provisional,
        secrets_directory,
        database=BASE_DATABASE,
        sql="SELECT current_setting('server_version_num') || '|' || "
        "system_identifier::text FROM pg_control_system()",
        label=f"{phase_name} PostgreSQL version and system-ID check",
    ).stdout.strip()
    try:
        version, system_identifier = version_and_id.split("|", 1)
    except ValueError as error:
        raise GateFailure("invalid PostgreSQL version/system-ID response") from error
    if not version.isdigit() or not 170000 <= int(version) < 180000:
        raise GateFailure(f"expected PostgreSQL 17, received {version!r}")
    if not system_identifier.isdigit():
        raise GateFailure("PostgreSQL returned an invalid system identifier")
    phase = Phase(phase_name, network, postgres_container, system_identifier)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=(
            f'ALTER DATABASE {ADMIN_DATABASE} SET "{SENTINEL_SETTING}" = '
            f"'{state.sentinel}'"
        ),
        label=f"{phase_name} sentinel installation",
    )
    _verify_cluster_guard(
        state,
        phase,
        secrets_directory,
        {ADMIN_DATABASE, "template0", "template1", BASE_DATABASE},
    )
    _provision_roles(state, phase, secrets_directory)
    return phase


def _verify_cluster_guard(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    expected_databases: set[str],
) -> None:
    inventory = ",".join(sorted(expected_databases))
    sql = (
        "SELECT current_setting('home_agent_e1.run_id', true) || '|' || "
        "system_identifier::text || '|' || "
        "(SELECT string_agg(datname, ',' ORDER BY datname) FROM pg_database) "
        "FROM pg_control_system()"
    )
    observed = _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=sql,
        label=f"{phase.name} destructive-operation guard",
    ).stdout.strip()
    expected = f"{state.sentinel}|{phase.system_identifier}|{inventory}"
    if observed != expected:
        raise GateFailure(
            f"{phase.name} cluster sentinel/inventory mismatch: {observed!r}"
        )


def _create_database_clone(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    source: str,
    target: str,
) -> None:
    sql = f'CREATE DATABASE "{target}" WITH TEMPLATE "{source}" OWNER "{OWNER}"'
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=sql,
        label=f"clone {target} from {source}",
    )


def _assert_database_revision(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    database: str,
    expected_revision: str,
) -> None:
    revision = _psql(
        state,
        phase,
        secrets_directory,
        database=database,
        sql="SELECT version_num FROM public.alembic_version",
        label=f"{database} revision assertion",
    ).stdout.strip()
    if revision != expected_revision:
        raise GateFailure(
            f"expected {database} at {expected_revision}, received {revision!r}"
        )


def _assert_zero_identity_kernel_ownership(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    count = _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=(
            "SELECT count(*) FROM pg_shdepend AS dependency "
            "JOIN pg_roles AS role_row ON role_row.oid = dependency.refobjid "
            "WHERE role_row.rolname IN "
            "('home_agent_identity_kernel','home_agent_identity_migration') "
            "AND dependency.deptype = 'o'"
        ),
        label=f"{phase.name} zero identity-kernel ownership assertion",
    ).stdout.strip()
    if count != "0":
        raise GateFailure(
            f"{phase.name} revision-0007 cluster has owned kernel objects"
        )


def _run_phase(
    state: GateState,
    phase_name: str,
    secrets_directory: Path,
    callback: Callable[[Phase], None],
) -> None:
    failure: BaseException | None = None
    try:
        phase = _start_phase(state, phase_name, secrets_directory)
        callback(phase)
    except BaseException as error:
        failure = error
    cleanup_failure: BaseException | None = None
    try:
        _cleanup_labeled(state, phase=phase_name, include_image=False)
    except BaseException as error:
        cleanup_failure = error
    if cleanup_failure is not None:
        if failure is not None:
            raise GateFailure(
                f"{phase_name} failed and cleanup also failed: {cleanup_failure}"
            ) from failure
        raise cleanup_failure
    if failure is not None:
        raise failure


def _run_behavior_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
) -> None:
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0006A)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0010)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0011)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    guard_environment = {
        SENTINEL_ENV: state.sentinel,
        SYSTEM_ID_ENV: phase.system_identifier,
        ALLOWLIST_ENV: BASE_DATABASE,
    }
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_erasure_schema_foundation.py::"
            "test_0011_is_dormant_content_free_and_exactly_source_linked",
            "tests/test_phase3_identity_erasure_schema_foundation.py::"
            "test_0011_admission_acl_and_downgrade_fail_closed",
            "tests/test_phase3_identity_erasure_schema_foundation.py::"
            "test_postgresql_e1_owner_rls_and_exact_manual_scope_boundary",
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E1_OWNER_DATABASE_URL": BASE_DATABASE,
            "TEST_PHASE3_IDENTITY_ERASURE_E1_ADMIN_DATABASE_URL": ADMIN_DATABASE,
        },
        environment=guard_environment,
    )


def _run_lifecycle_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
) -> None:
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0006A)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0010)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_erasure_schema_foundation.py::"
            "test_postgresql_e1_upgrade_round_trip_and_evidence_downgrade_guard"
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E1_LIFECYCLE_DATABASE_URL": BASE_DATABASE,
            "TEST_PHASE3_IDENTITY_ERASURE_E1_ADMIN_DATABASE_URL": ADMIN_DATABASE,
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
        },
    )


def _run_admission_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
) -> None:
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0006A)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0007)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _assert_database_revision(
        state, phase, secrets_directory, BASE_DATABASE, REVISION_0007
    )
    _assert_zero_identity_kernel_ownership(state, phase, secrets_directory)
    _create_database_clone(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        ADMISSION_TEMPLATE,
    )
    expected_with_template = {
        ADMIN_DATABASE,
        "template0",
        "template1",
        BASE_DATABASE,
        ADMISSION_TEMPLATE,
    }
    _verify_cluster_guard(state, phase, secrets_directory, expected_with_template)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=(
            f"COMMENT ON DATABASE {ADMISSION_TEMPLATE} IS "
            f"'home-agent-e1:{state.sentinel}:{REVISION_0007}'; "
            f"UPDATE pg_database SET datistemplate = true, datallowconn = false "
            f"WHERE datname = '{ADMISSION_TEMPLATE}'"
        ),
        label="lock revision-0007 admission template",
    )
    _verify_cluster_guard(state, phase, secrets_directory, expected_with_template)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
            f"datname = '{BASE_DATABASE}' AND pid <> pg_backend_pid()"
        ),
        label="guarded bootstrap connection termination",
    )
    _verify_cluster_guard(state, phase, secrets_directory, expected_with_template)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=f"DROP DATABASE {BASE_DATABASE}",
        label="guarded bootstrap database removal",
    )
    _verify_cluster_guard(
        state,
        phase,
        secrets_directory,
        {ADMIN_DATABASE, "template0", "template1", ADMISSION_TEMPLATE},
    )
    _assert_zero_identity_kernel_ownership(state, phase, secrets_directory)
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_erasure_admission_postgres.py",
            "/workspace/tests/home_agent/"
            "test_identity_erasure_kernel_foundation_deployment_contract.py",
            "/workspace/tests/home_agent/"
            "test_apply_grants_revision_0006a_contract.py",
            "/workspace/tests/home_agent/test_e1_postgres_gate_contract.py",
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E1_ADMIN_DATABASE_URL": ADMIN_DATABASE
        },
        environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E1_TEMPLATE_DATABASE": (ADMISSION_TEMPLATE),
            "TEST_PHASE3_IDENTITY_ERASURE_E1_OWNER_PASSWORD_FILE": (
                "/run/secrets/postgres_owner_password"
            ),
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: f"{ADMISSION_TEMPLATE},{BASE_DATABASE}",
        },
        fail_fast=False,
    )


def _upgrade_e2_database(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0006A)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    for revision in (REVISION_0010, REVISION_0011, REVISION_0012):
        _alembic(state, phase, secrets_directory, BASE_DATABASE, revision)
        _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0012,
    )


def _upgrade_e3_database(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    _upgrade_e2_database(state, phase, secrets_directory)
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0013)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0013,
    )


def _guarded_recreate_base_database(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
) -> None:
    with_base = {ADMIN_DATABASE, "template0", "template1", BASE_DATABASE}
    without_base = {ADMIN_DATABASE, "template0", "template1"}
    _verify_cluster_guard(state, phase, secrets_directory, with_base)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE "
            f"datname = '{BASE_DATABASE}' AND pid <> pg_backend_pid()"
        ),
        label="guarded E2 lifecycle connection termination",
    )
    _verify_cluster_guard(state, phase, secrets_directory, with_base)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=f'DROP DATABASE "{BASE_DATABASE}"',
        label="guarded E2 lifecycle database removal",
    )
    _verify_cluster_guard(state, phase, secrets_directory, without_base)
    _psql(
        state,
        phase,
        secrets_directory,
        database=ADMIN_DATABASE,
        sql=f'CREATE DATABASE "{BASE_DATABASE}" OWNER "{OWNER}"',
        label="guarded E2 runtime database creation",
    )
    _verify_cluster_guard(state, phase, secrets_directory, with_base)
    _provision_roles(state, phase, secrets_directory)


def _run_e2_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
) -> None:
    guard_environment = {
        SENTINEL_ENV: state.sentinel,
        SYSTEM_ID_ENV: phase.system_identifier,
        ALLOWLIST_ENV: BASE_DATABASE,
    }
    _upgrade_e2_database(state, phase, secrets_directory)
    _verify_cluster_guard(
        state,
        phase,
        secrets_directory,
        {ADMIN_DATABASE, "template0", "template1", BASE_DATABASE},
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_erasure_e2_runtime_postgres.py::"
            "test_postgresql_e2_clean_roundtrip_and_data_bearing_downgrade_refusal"
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E2_LIFECYCLE_DATABASE_URL": BASE_DATABASE
        },
        credential_url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E2_LIFECYCLE_ERASURE_DATABASE_URL": (
                BASE_DATABASE,
                "home_agent_erasure",
                "postgres_erasure_password",
            )
        },
        environment=guard_environment,
    )

    # E1 permits its erasure-kernel objects in only one database per cluster.
    # Recreate that sole database instead of cloning a second 0012 database.
    _guarded_recreate_base_database(state, phase, secrets_directory)
    _upgrade_e2_database(state, phase, secrets_directory)
    runtime_urls = {
        environment_name: (BASE_DATABASE, role, password_secret)
        for environment_name, role, password_secret in E2_RUNTIME_ROLE_URLS
    }
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_identity_person_restore_replay.py",
            "tests/test_ledger_versions.py",
            "tests/test_phase3_identity_erasure_e2_schema.py",
            "tests/test_phase3_identity_erasure_e2_runtime_postgres.py::"
            "test_postgresql_e2_all_target_rls_and_control_evidence_matrix",
            "tests/test_phase3_identity_erasure_e2_runtime_postgres.py::"
            "test_postgresql_e2_restore_before_person_and_replay_mismatches",
            "/workspace/tests/home_agent/"
            "test_identity_erasure_e2_deployment_contract.py",
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_ERASURE_E2_OWNER_DATABASE_URL": BASE_DATABASE
        },
        credential_url_environment=runtime_urls,
        environment=guard_environment,
    )

    # A same-owner SECURITY DEFINER replacement must fail admission, and the
    # separately committed caller quarantine must leave it non-callable.
    _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "GRANT EXECUTE ON FUNCTION "
            "privacy.identity_fact_version_is_visible(uuid) TO "
            "home_agent_identity_erasure_kernel; "
            "GRANT CREATE ON SCHEMA privacy TO "
            "home_agent_identity_erasure_kernel; "
            "SET ROLE home_agent_identity_erasure_kernel; "
            "CREATE OR REPLACE FUNCTION "
            "privacy.identity_fact_version_is_visible("
            "target_fact_version_id uuid) "
            "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER "
            "SET search_path=pg_catalog SET row_security=on "
            "AS $tampered$ SELECT true $tampered$; "
            "RESET ROLE; "
            "REVOKE EXECUTE ON FUNCTION "
            "privacy.identity_fact_version_is_visible(uuid) FROM "
            "home_agent_identity_erasure_kernel; "
            "REVOKE CREATE ON SCHEMA privacy FROM "
            "home_agent_identity_erasure_kernel"
        ),
        label="tamper E2 fact visibility helper in disposable database",
    )
    _apply_grants_expect_failure(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        expected_output="identity erasure E2 function ownership invalid",
    )
    quarantined_acl = _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "SELECT count(*) FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
            "function_row.proacl, pg_catalog.acldefault("
            "'f', function_row.proowner))) AS function_acl "
            "WHERE function_row.oid='privacy."
            "identity_fact_version_is_visible(uuid)'::regprocedure "
            "AND function_acl.privilege_type='EXECUTE'"
        ),
        label="verify rejected E2 helper remains quarantined",
    )
    if quarantined_acl.stdout.strip() != "0":
        raise GateFailure(
            "rejected E2 helper retained an EXECUTE privilege after quarantine"
        )


def _run_e3_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
) -> None:
    guard_environment = {
        SENTINEL_ENV: state.sentinel,
        SYSTEM_ID_ENV: phase.system_identifier,
        ALLOWLIST_ENV: BASE_DATABASE,
        "TEST_PHASE3_IDENTITY_FINALIZER_E3_OWNER_PASSWORD_FILE": (
            "/run/secrets/postgres_owner_password"
        ),
    }
    _upgrade_e3_database(state, phase, secrets_directory)
    _verify_cluster_guard(
        state,
        phase,
        secrets_directory,
        {ADMIN_DATABASE, "template0", "template1", BASE_DATABASE},
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_finalizer_e3_schema.py",
            "tests/test_phase3_identity_finalizer_e3_runtime_postgres.py",
            "/workspace/tests/home_agent/"
            "test_identity_finalizer_e3_deployment_contract.py",
        ],
        url_environment={
            "TEST_PHASE3_IDENTITY_FINALIZER_E3_OWNER_DATABASE_URL": BASE_DATABASE,
            "TEST_PHASE3_IDENTITY_FINALIZER_E3_ADMIN_DATABASE_URL": ADMIN_DATABASE,
        },
        credential_url_environment={
            "TEST_PHASE3_IDENTITY_FINALIZER_E3_FINALIZER_DATABASE_URL": (
                BASE_DATABASE,
                "home_agent_identity_finalizer",
                "postgres_identity_finalizer_password",
            ),
            **{
                environment_name: (BASE_DATABASE, role, password_secret)
                for environment_name, role, password_secret in E2_RUNTIME_ROLE_URLS
            },
        },
        environment=guard_environment,
        fail_fast=False,
    )


def _run_e4_scaffold_phase(
    state: GateState,
    secrets_directory: Path,
    phase: Phase,
    fixture_directory: Path,
) -> None:
    """Run dormant E4/E5a/E5b and the separate E5c activation gate."""
    _upgrade_e3_database(state, phase, secrets_directory)
    _provision_identity_cutover_roles(state, phase, secrets_directory)
    _apply_grants(state, phase, secrets_directory, BASE_DATABASE)
    _verify_cluster_guard(
        state,
        phase,
        secrets_directory,
        {ADMIN_DATABASE, "template0", "template1", BASE_DATABASE},
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_cutover_e4_scaffold_postgres.py",
            "/workspace/tests/home_agent/"
            "test_identity_cutover_e4_deployment_contract.py",
        ],
        url_environment={},
        direct_psycopg_url_environment={
            E4_SCAFFOLD_OWNER_DATABASE_ENV: BASE_DATABASE,
        },
        direct_psycopg_credential_url_environment={
            E4_SCAFFOLD_CUTOVER_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_identity_cutover",
                "postgres_identity_cutover_password",
            ),
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
        },
        fail_fast=False,
    )
    _alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0014)
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0014,
    )

    # Exercise the quarantined-role downgrade while all E4 relations are
    # empty. Once an admitted fixture exists, downgrade must fail by design.
    _apply_grants_expect_failure(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        expected_output=(
            "identity cutover E4 activation contract is not installed"
        ),
        failure_label=(
            "pinned dormant E4 catalog; "
            "empty E4 quarantine before downgrade"
        ),
        redact_output=True,
    )
    _alembic_downgrade(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0013,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0013,
    )
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0014,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0014,
    )
    try:
        _set_disposable_e4_role_login(
            state,
            phase,
            secrets_directory,
            role="home_agent_identity_finalizer",
            enabled=True,
        )
        _seed_e4_success_fixture(
            state,
            phase,
            secrets_directory,
            fixture_directory,
        )
    finally:
        _set_disposable_e4_role_login(
            state,
            phase,
            secrets_directory,
            role="home_agent_identity_finalizer",
            enabled=False,
        )

    # The isolated disposable contract test opens the otherwise expired login.
    # Re-expire it before testing grant replay and destroy the whole labeled
    # cluster on every success/failure path.
    try:
        _set_disposable_e4_role_login(
            state,
            phase,
            secrets_directory,
            role="home_agent_identity_cutover",
            enabled=True,
        )
        _pytest(
            state,
            phase,
            secrets_directory,
            nodes=[
                "tests/test_phase3_identity_semantic_cutover_e4_schema.py",
                "tests/"
                "test_phase3_identity_semantic_cutover_e4_runtime_postgres.py",
                "/workspace/tests/home_agent/"
                "test_identity_cutover_e4_deployment_contract.py",
            ],
            url_environment={
                "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL": (
                    BASE_DATABASE
                ),
            },
            credential_url_environment={
                "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL": (
                    BASE_DATABASE,
                    "home_agent_identity_cutover",
                    "postgres_identity_cutover_password",
                ),
            },
            environment={
                SENTINEL_ENV: state.sentinel,
                SYSTEM_ID_ENV: phase.system_identifier,
                ALLOWLIST_ENV: BASE_DATABASE,
            },
            fixture_file_environment={
                E4_SUCCESS_DOCUMENT_ENV: E4_FIXTURE_DOCUMENT_FILE,
                E4_SUCCESS_ADMISSION_ENV: E4_FIXTURE_ADMISSION_FILE,
            },
            fixture_directory=fixture_directory,
            fail_fast=False,
        )
    finally:
        _set_disposable_e4_role_login(
            state,
            phase,
            secrets_directory,
            role="home_agent_identity_cutover",
            enabled=False,
        )

    # E5a is a function/policy overlay over the admitted synthetic E4
    # promotion. Exercise it while the migration-owned ACL is callable only by
    # the reviewed binding-operator role, then prove its stateless downgrade
    # and re-upgrade before grant replay quarantines every callable path.
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_identity_current_authority_e5_schema.py",
            "tests/"
            "test_phase3_identity_current_authority_e5_runtime_postgres.py",
            "/workspace/tests/home_agent/"
            "test_identity_current_authority_e5_deployment_contract.py",
        ],
        url_environment={
            E5_OWNER_DATABASE_ENV: BASE_DATABASE,
        },
        credential_url_environment={
            E5_AUTHORITY_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_binding_operator",
                "postgres_binding_operator_password",
            ),
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
        },
        fail_fast=False,
    )
    _alembic_downgrade(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0014,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0014,
    )
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )

    # E5b adds a separately owned, database-only principal-binding commit
    # kernel. Its cluster-wide NOLOGIN role is admitted only after the pinned
    # E5a catalog exists; no password, service, or runtime surface is created.
    _provision_identity_binding_kernel_role(
        state,
        phase,
        secrets_directory,
    )
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0016,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0016,
    )
    # Prove the evidence-free downgrade restores the exact E5a schema before
    # exercising any commit path.
    _alembic_downgrade(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0015,
    )
    _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "ALTER ROLE home_agent_binding_committer "
            "SET application_name='e5b-role-config-tamper'"
        ),
        label="add one unreviewed E5b caller role setting",
    )
    _alembic_expect_failure(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        action="upgrade",
        revision=REVISION_0016,
        expected_output="principal_binding_e5b_caller_role_invalid",
        failure_label="E5b upgrade with extra caller role setting",
    )
    _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql="ALTER ROLE home_agent_binding_committer RESET application_name",
        label="remove the unreviewed E5b caller role setting",
    )
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0016,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0016,
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_principal_binding_kernel_e5b_schema.py",
            "tests/"
            "test_phase3_principal_binding_kernel_e5b_runtime_postgres.py",
            "tests/test_principal_binding_adapter_e5c.py",
            "/workspace/tests/home_agent/"
            "test_principal_binding_kernel_e5b_deployment_contract.py",
            "/workspace/tests/home_agent/"
            "test_identity_binding_kernel_role_ceremony_contract.py",
            "/workspace/tests/home_agent/"
            "test_principal_binding_authority_boundary_contract.py",
            "/workspace/tests/home_agent/"
            "test_principal_binding_adapter_e5c_deployment_contract.py",
        ],
        url_environment={
            E5B_OWNER_DATABASE_ENV: BASE_DATABASE,
        },
        credential_url_environment={
            E5B_COMMITTER_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_binding_committer",
                "postgres_binding_committer_password",
            ),
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
        },
        fail_fast=False,
    )
    # Leave one synthetic graph only in this disposable hosted database, then
    # prove E5b refuses to erase its normalized authority through downgrade.
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/"
            "test_phase3_principal_binding_kernel_e5b_runtime_postgres.py"
            "::test_e5b_retains_one_graph_for_hosted_downgrade_refusal",
        ],
        url_environment={
            E5B_OWNER_DATABASE_ENV: BASE_DATABASE,
        },
        credential_url_environment={
            E5B_COMMITTER_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_binding_committer",
                "postgres_binding_committer_password",
            ),
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
            (
                "TEST_PHASE3_PRINCIPAL_BINDING_KERNEL_E5B_"
                "RETAIN_DOWNGRADE_EVIDENCE"
            ): "1",
        },
        fail_fast=True,
    )
    _alembic_expect_failure(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        action="downgrade",
        revision=REVISION_0015,
        expected_output=(
            "refusing to remove populated E5b principal-binding authority"
        ),
        failure_label="populated E5b downgrade refusal",
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0016,
    )
    _discover_changed_catalog_digests(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
    )
    _apply_grants_expect_failure(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        expected_output=(
            "identity cutover E4 activation contract is not installed"
        ),
        failure_label="pinned dormant E5b catalog",
        redact_output=True,
    )
    quarantined_acl = _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "SELECT count(*) FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
            "function_row.proacl, pg_catalog.acldefault("
            "'f', function_row.proowner))) AS function_acl "
            "WHERE function_row.oid='operations."
            "commit_reviewed_identity_cutover(bytea,uuid)'::regprocedure "
            "AND function_acl.grantee=("
            "SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='home_agent_identity_cutover') "
            "AND function_acl.privilege_type='EXECUTE'"
        ),
        label="verify rejected E4 kernel remains quarantined",
    )
    if quarantined_acl.stdout.strip() != "0":
        raise GateFailure(
            "rejected E4 kernel retained an EXECUTE privilege after quarantine"
        )
    e5_quarantined_acl = _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "WITH authority_kernel AS ("
            "SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='home_agent_identity_authority_kernel'), "
            "binding_operator AS ("
            "SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='home_agent_binding_operator'), "
            "direct_kernel_acl AS ("
            "SELECT database_acl.grantee "
            "FROM pg_catalog.pg_database AS database_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) "
            "AS database_acl WHERE database_acl.grantee=kernel.oid "
            "UNION ALL SELECT namespace_acl.grantee "
            "FROM pg_catalog.pg_namespace AS namespace_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(namespace_row.nspacl) "
            "AS namespace_acl WHERE namespace_acl.grantee=kernel.oid "
            "UNION ALL SELECT relation_acl.grantee "
            "FROM pg_catalog.pg_class AS relation_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(relation_row.relacl) "
            "AS relation_acl WHERE relation_acl.grantee=kernel.oid "
            "UNION ALL SELECT attribute_acl.grantee "
            "FROM pg_catalog.pg_attribute AS attribute_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) "
            "AS attribute_acl WHERE attribute_acl.grantee=kernel.oid "
            "UNION ALL SELECT function_acl.grantee "
            "FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) "
            "AS function_acl WHERE function_acl.grantee=kernel.oid "
            "UNION ALL SELECT type_acl.grantee "
            "FROM pg_catalog.pg_type AS type_row "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(type_row.typacl) "
            "AS type_acl WHERE type_acl.grantee=kernel.oid "
            "UNION ALL SELECT default_acl_item.grantee "
            "FROM pg_catalog.pg_default_acl AS default_acl "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) "
            "AS default_acl_item "
            "WHERE default_acl_item.grantee=kernel.oid "
            "UNION ALL SELECT parameter_acl_item.grantee "
            "FROM pg_catalog.pg_parameter_acl AS parameter_acl "
            "CROSS JOIN authority_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(parameter_acl.paracl) "
            "AS parameter_acl_item "
            "WHERE parameter_acl_item.grantee=kernel.oid), "
            "caller_acl AS ("
            "SELECT function_acl.grantee "
            "FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN binding_operator AS caller "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
            "function_row.proacl, pg_catalog.acldefault("
            "'f', function_row.proowner))) AS function_acl "
            "WHERE function_row.oid='operations."
            "evaluate_current_identity_semantic_authority(uuid)'::regprocedure "
            "AND function_acl.grantee=caller.oid "
            "AND function_acl.privilege_type='EXECUTE' "
            "UNION ALL SELECT namespace_acl.grantee "
            "FROM pg_catalog.pg_namespace AS namespace_row "
            "CROSS JOIN binding_operator AS caller "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(namespace_row.nspacl) "
            "AS namespace_acl "
            "WHERE namespace_row.nspname IN ('operations','privacy') "
            "AND namespace_acl.grantee=caller.oid) "
            "SELECT (SELECT count(*) FROM direct_kernel_acl) + "
            "(SELECT count(*) FROM caller_acl)"
        ),
        label="verify rejected E5 catalog remains broadly quarantined",
    )
    if e5_quarantined_acl.stdout.strip() != "0":
        raise GateFailure(
            "rejected E5 catalog retained a callable or direct privilege"
        )
    e5b_quarantined_acl = _psql(
        state,
        phase,
        secrets_directory,
        database=BASE_DATABASE,
        sql=(
            "WITH binding_kernel AS ("
            "SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='home_agent_identity_binding_kernel'), "
            "owner_role AS ("
            "SELECT oid FROM pg_catalog.pg_roles "
            "WHERE rolname='home_agent_owner'), "
            "direct_kernel_acl AS ("
            "SELECT database_acl.grantee "
            "FROM pg_catalog.pg_database AS database_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(database_row.datacl) "
            "AS database_acl WHERE database_acl.grantee=kernel.oid "
            "UNION ALL SELECT namespace_acl.grantee "
            "FROM pg_catalog.pg_namespace AS namespace_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(namespace_row.nspacl) "
            "AS namespace_acl WHERE namespace_acl.grantee=kernel.oid "
            "UNION ALL SELECT relation_acl.grantee "
            "FROM pg_catalog.pg_class AS relation_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(relation_row.relacl) "
            "AS relation_acl WHERE relation_acl.grantee=kernel.oid "
            "UNION ALL SELECT attribute_acl.grantee "
            "FROM pg_catalog.pg_attribute AS attribute_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) "
            "AS attribute_acl WHERE attribute_acl.grantee=kernel.oid "
            "UNION ALL SELECT function_acl.grantee "
            "FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(function_row.proacl) "
            "AS function_acl WHERE function_acl.grantee=kernel.oid "
            "UNION ALL SELECT type_acl.grantee "
            "FROM pg_catalog.pg_type AS type_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(type_row.typacl) "
            "AS type_acl WHERE type_acl.grantee=kernel.oid "
            "UNION ALL SELECT default_acl_item.grantee "
            "FROM pg_catalog.pg_default_acl AS default_acl "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl) "
            "AS default_acl_item "
            "WHERE default_acl_item.grantee=kernel.oid "
            "UNION ALL SELECT parameter_acl_item.grantee "
            "FROM pg_catalog.pg_parameter_acl AS parameter_acl "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(parameter_acl.paracl) "
            "AS parameter_acl_item "
            "WHERE parameter_acl_item.grantee=kernel.oid), "
            "callable_acl AS ("
            "SELECT function_acl.grantee "
            "FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
            "function_row.proacl, pg_catalog.acldefault("
            "'f', function_row.proowner))) AS function_acl "
            "WHERE function_row.oid='identity."
            "commit_authenticated_principal_binding_e5b("
            "uuid,character varying,character varying,"
            "uuid,uuid,uuid,uuid,uuid)'::regprocedure "
            "AND function_acl.privilege_type='EXECUTE' "
            "AND function_acl.grantee<>kernel.oid "
            "UNION ALL SELECT function_acl.grantee "
            "FROM pg_catalog.pg_proc AS function_row "
            "CROSS JOIN binding_kernel AS kernel "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE("
            "function_row.proacl, pg_catalog.acldefault("
            "'f', function_row.proowner))) AS function_acl "
            "WHERE function_row.oid='operations."
            "evaluate_current_identity_semantic_authority(uuid)'"
            "::regprocedure "
            "AND function_acl.grantee=kernel.oid "
            "AND function_acl.privilege_type='EXECUTE'), "
            "invalid_receipt_acl AS ("
            "SELECT relation_acl.grantee "
            "FROM pg_catalog.pg_class AS relation_row "
            "CROSS JOIN owner_role AS owner "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(relation_row.relacl) "
            "AS relation_acl "
            "WHERE relation_row.oid='operations."
            "principal_binding_authority_receipts'::regclass "
            "AND NOT ("
            "relation_acl.grantee=owner.oid "
            "AND relation_acl.privilege_type='SELECT' "
            "AND NOT relation_acl.is_grantable) "
            "UNION ALL SELECT attribute_acl.grantee "
            "FROM pg_catalog.pg_attribute AS attribute_row "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) "
            "AS attribute_acl "
            "WHERE attribute_row.attrelid='operations."
            "principal_binding_authority_receipts'::regclass), "
            "owner_receipt_select AS ("
            "SELECT relation_acl.grantee "
            "FROM pg_catalog.pg_class AS relation_row "
            "CROSS JOIN owner_role AS owner "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(relation_row.relacl) "
            "AS relation_acl "
            "WHERE relation_row.oid='operations."
            "principal_binding_authority_receipts'::regclass "
            "AND relation_acl.grantee=owner.oid "
            "AND relation_acl.privilege_type='SELECT' "
            "AND NOT relation_acl.is_grantable) "
            "SELECT (SELECT count(*) FROM direct_kernel_acl) + "
            "(SELECT count(*) FROM callable_acl) + "
            "(SELECT count(*) FROM invalid_receipt_acl) + "
            "CASE WHEN (SELECT count(*) FROM owner_receipt_select)=1 "
            "THEN 0 ELSE 1 END + "
            "CASE WHEN pg_catalog.pg_has_role("
            "'home_agent_binding_operator',"
            "'home_agent_identity_binding_kernel','SET') "
            "THEN 1 ELSE 0 END"
        ),
        label="verify rejected E5b catalog remains broadly quarantined",
    )
    if e5b_quarantined_acl.stdout.strip() != "0":
        raise GateFailure(
            "rejected E5b catalog retained a callable or direct privilege"
        )

    # E5c is a separate reviewed activation boundary. Its migration remains
    # denial-only; the pinned grant replay restores only the two internal
    # kernels and the commit-only outer function.
    _alembic(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0017,
    )
    _assert_database_revision(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
        REVISION_0017,
    )
    _apply_grants(
        state,
        phase,
        secrets_directory,
        BASE_DATABASE,
    )
    _pytest(
        state,
        phase,
        secrets_directory,
        nodes=[
            "tests/test_phase3_authenticated_binding_e5c_schema.py",
            "tests/test_phase3_principal_binding_kernel_e5b_runtime_postgres.py"
            "::test_e5c_split_adapter_commits_after_pinned_grant_replay",
            "/workspace/tests/home_agent/"
            "test_principal_binding_adapter_e5c_deployment_contract.py",
        ],
        url_environment={
            E5B_OWNER_DATABASE_ENV: BASE_DATABASE,
        },
        credential_url_environment={
            E5B_COMMITTER_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_binding_committer",
                "postgres_binding_committer_password",
            ),
            E5C_OPERATOR_DATABASE_ENV: (
                BASE_DATABASE,
                "home_agent_binding_operator",
                "postgres_binding_operator_password",
            ),
        },
        environment={
            SENTINEL_ENV: state.sentinel,
            SYSTEM_ID_ENV: phase.system_identifier,
            ALLOWLIST_ENV: BASE_DATABASE,
        },
        fail_fast=True,
    )


def _build_test_image(state: GateState, build_context: Path) -> None:
    dockerfile = (
        build_context / "stack/services/home-agent-core/Dockerfile.postgres-test"
    )
    _run(
        state.docker(
            "build",
            "--file",
            str(dockerfile),
            "--tag",
            state.test_image,
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"{RUN_LABEL}={state.sentinel}",
            "--label",
            f"{PHASE_LABEL}=shared",
            str(build_context),
        ),
        label="E1 filtered test-image build",
        timeout=900,
        environment=state.docker_environment,
    )


def main() -> int:
    try:
        _assert_execution_admitted()
    except GateFailure as error:
        print(
            "E1/E2/E3/E4 gate execution quarantine "
            f"(E5a/E5b/E5c included): {error}",
            file=sys.stderr,
        )
        return 77
    if shutil.which("docker") is None:
        print("Docker is required for the E1 PostgreSQL gate", file=sys.stderr)
        return 69
    if not TEST_DOCKERFILE.is_file():
        print(f"missing test image contract: {TEST_DOCKERFILE}", file=sys.stderr)
        return 66

    try:
        endpoint, docker_environment = _validate_local_docker()
    except GateFailure as error:
        print(f"E1 gate refused Docker endpoint: {error}", file=sys.stderr)
        return 78

    sentinel = secrets.token_hex(32)
    suffix = sentinel[:12]
    state = GateState(
        sentinel=sentinel,
        suffix=suffix,
        endpoint=endpoint,
        docker_environment=docker_environment,
        test_image=f"home-agent-e1-{suffix}-test:gate",
    )

    def mark_interrupted(_signum: int, _frame: object) -> None:
        state.interrupted = True
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, mark_interrupted)
    exit_code = 1
    cleanup_failure: BaseException | None = None
    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="home-agent-e1-context-"
            ) as context_temp,
            tempfile.TemporaryDirectory(
                prefix="home-agent-e1-secrets-"
            ) as secrets_temp,
            tempfile.TemporaryDirectory(
                prefix="home-agent-e4-fixture-"
            ) as e4_fixture_temp,
        ):
            build_context = Path(context_temp)
            secrets_directory = Path(secrets_temp)
            e4_fixture_directory = Path(e4_fixture_temp)
            try:
                e4_fixture_directory.chmod(0o700)
            except OSError:
                pass
            print("[1/8] Generating the minimal filtered build context")
            _prepare_build_context(build_context)
            _write_secrets(secrets_directory)
            print("[2/8] Building the labeled pinned PostgreSQL 17 test image")
            _build_test_image(state, build_context)
            print("[3/8] Running the production-shaped behavioral cluster")
            _run_phase(
                state,
                "behavior",
                secrets_directory,
                lambda phase: _run_behavior_phase(state, secrets_directory, phase),
            )
            print("[4/8] Running the production-shaped lifecycle cluster")
            _run_phase(
                state,
                "lifecycle",
                secrets_directory,
                lambda phase: _run_lifecycle_phase(state, secrets_directory, phase),
            )
            print("[5/8] Running isolated revision-0007 admission cases")
            _run_phase(
                state,
                "admission",
                secrets_directory,
                lambda phase: _run_admission_phase(state, secrets_directory, phase),
            )
            print("[6/8] Running isolated revision-0012 E2 contracts")
            _run_phase(
                state,
                "e2",
                secrets_directory,
                lambda phase: _run_e2_phase(state, secrets_directory, phase),
            )
            print("[7/8] Running isolated dormant revision-0013 E3 contracts")
            _run_phase(
                state,
                "e3",
                secrets_directory,
                lambda phase: _run_e3_phase(state, secrets_directory, phase),
            )
            print(
                "[8/8] Running isolated dormant E4 deployment scaffold "
                "with E5a/E5b and the E5c activation boundary"
            )
            _run_phase(
                state,
                "e4-scaffold",
                secrets_directory,
                lambda phase: _run_e4_scaffold_phase(
                    state,
                    secrets_directory,
                    phase,
                    e4_fixture_directory,
                ),
            )
            exit_code = 0
    except KeyboardInterrupt:
        message = "terminated" if state.interrupted else "interrupted"
        print(f"E1 gate {message}; cleaning labeled disposable state", file=sys.stderr)
        exit_code = 130
    except GateFailure as error:
        print(f"E1 gate failed: {error}", file=sys.stderr)
        exit_code = 1
    except BaseException as error:
        print(f"E1 gate failed unexpectedly: {error}", file=sys.stderr)
        exit_code = 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        try:
            _cleanup_labeled(state, phase=None, include_image=True)
        except BaseException as error:
            cleanup_failure = error
    if cleanup_failure is not None:
        print(f"E1 gate cleanup failed: {cleanup_failure}", file=sys.stderr)
        return 1
    if exit_code == 0:
        print(
            "E1/E2/E3/E4 PostgreSQL 17 gate passed; "
            "E5a/E5b catalog gates and E5c split adapter passed; "
            "labeled cleanup verified"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
