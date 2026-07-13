#!/usr/bin/env python3
"""Run the E1 gate only against locally hosted, disposable PostgreSQL 17."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "stack/services/home-agent-core"
TEST_DOCKERFILE = CORE / "Dockerfile.postgres-test"
POSTGRES_IMAGE = (
    "postgres:17.10-bookworm@sha256:"
    "17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a"
)
OWNER = "home_agent_owner"
BASE_DATABASE = "home_agent"
ADMIN_DATABASE = "postgres"
ADMISSION_TEMPLATE = "e1_template_0007"
REVISION_0007 = "0007_phase3_identity_authority"
REVISION_0010 = "0010_identity_erasure_source"
REVISION_0011 = "0011_identity_erasure_e1"
RUN_LABEL = "com.engineeredlighting.home-agent-e1.run"
MANAGED_LABEL = "com.engineeredlighting.home-agent-e1.managed"
PHASE_LABEL = "com.engineeredlighting.home-agent-e1.phase"
SENTINEL_SETTING = "home_agent_e1.run_id"
SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
SYSTEM_ID_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_SYSTEM_IDENTIFIER"
ALLOWLIST_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_DATABASE_ALLOWLIST"
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
    "postgres_identity_migration_password",
    "postgres_identity_finalizer_password",
    "postgres_ingest_password",
    "postgres_worker_password",
    "postgres_erasure_password",
    "postgres_rollout_password",
    "postgres_backup_password",
)
BUILD_CONTEXT_FILES = (
    "stack/services/home-agent-core/Dockerfile.postgres-test",
    "stack/services/home-agent-core/alembic.ini",
    "stack/services/home-agent-core/pytest.ini",
    "stack/services/home-agent-core/requirements.txt",
    "stack/services/home-agent-core/requirements-dev.txt",
    "stack/home-agent-deploy/provision-roles.sh",
    "stack/home-agent-deploy/apply-grants.sh",
    "stack/home-agent-deploy/identity-api-acl.sql",
    "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md",
    "tests/home_agent/test_identity_erasure_kernel_foundation_deployment_contract.py",
    "tests/home_agent/test_e1_postgres_gate_contract.py",
    "stack/services/home-agent-core/tests/e1_postgres_harness.py",
    "stack/services/home-agent-core/tests/"
    "test_phase3_identity_erasure_admission_postgres.py",
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
REVIEWED_CONTEXT_FILENAMES = {"Dockerfile.postgres-test"}
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
    return endpoint, environment


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
) -> subprocess.CompletedProcess[str]:
    name = state.next_client_name(phase)
    arguments = state.docker(
        "run",
        "--rm",
        "--name",
        name,
        *_labels(state, phase),
        "--network",
        network,
        "--mount",
        f"type=bind,source={secrets_directory},target=/run/secrets,readonly",
        "--workdir",
        "/workspace/stack/services/home-agent-core",
    )
    for key, value in environment.items():
        arguments.extend(("--env", f"{key}={value}"))
    arguments.append(image)
    arguments.extend(command)
    return _run(
        arguments,
        label=label,
        timeout=timeout,
        environment=state.docker_environment,
    )


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


def _database_url_shell_export(name: str, database: str) -> str:
    return (
        f'export {name}="postgresql+psycopg://{OWNER}:'
        f'$password@postgres:5432/{database}"; '
    )


def _pytest(
    state: GateState,
    phase: Phase,
    secrets_directory: Path,
    *,
    nodes: list[str],
    url_environment: dict[str, str],
    environment: dict[str, str] | None = None,
    fail_fast: bool = True,
) -> None:
    shell = "password=\"$(tr -d '\\r\\n' < " '"$POSTGRES_OWNER_PASSWORD_FILE")"; '
    for name, database in url_environment.items():
        shell += _database_url_shell_export(name, database)
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
    if len(sys.argv) != 1:
        print("usage: python tools/run-home-agent-e1-postgres-gate.py", file=sys.stderr)
        return 64
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
        ):
            build_context = Path(context_temp)
            secrets_directory = Path(secrets_temp)
            print("[1/5] Generating the minimal filtered build context")
            _prepare_build_context(build_context)
            _write_secrets(secrets_directory)
            print("[2/5] Building the labeled pinned PostgreSQL 17 test image")
            _build_test_image(state, build_context)
            print("[3/5] Running the production-shaped behavioral cluster")
            _run_phase(
                state,
                "behavior",
                secrets_directory,
                lambda phase: _run_behavior_phase(state, secrets_directory, phase),
            )
            print("[4/5] Running the production-shaped lifecycle cluster")
            _run_phase(
                state,
                "lifecycle",
                secrets_directory,
                lambda phase: _run_lifecycle_phase(state, secrets_directory, phase),
            )
            print("[5/5] Running isolated revision-0007 admission cases")
            _run_phase(
                state,
                "admission",
                secrets_directory,
                lambda phase: _run_admission_phase(state, secrets_directory, phase),
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
        print("E1 PostgreSQL 17 gate passed; labeled cleanup verified")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
