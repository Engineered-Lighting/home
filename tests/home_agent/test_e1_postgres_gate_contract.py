from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
TEST_IMAGE = ROOT / "stack/services/home-agent-core/Dockerfile.postgres-test"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"
ROLE_DOC = ROOT / "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md"
HARNESS = ROOT / "stack/services/home-agent-core/tests/e1_postgres_harness.py"
PINNED_DIGEST = "17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a"
CHECKOUT_SHA = "34e114876b0b11c390a56381ad16ebd13914f8d5"


def _load_runner():
    spec = importlib.util.spec_from_file_location("home_agent_e1_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_refuses_ambient_endpoint_overrides_before_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    monkeypatch.setenv("DOCKER_HOST", "tcp://unreviewed.example:2376")

    with pytest.raises(runner.GateFailure, match="DOCKER_HOST"):
        runner._validate_local_docker()


def test_generated_build_context_is_an_exact_filtered_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    pathspecs = runner.BUILD_CONTEXT_FILES + runner.BUILD_CONTEXT_TREES
    if (ROOT / ".git").is_dir():
        indexed = runner._git_index_entries(pathspecs)
        observed_untracked = runner._git_untracked_entries(pathspecs)
    else:
        indexed = {}
        explicit = set(runner.BUILD_CONTEXT_FILES)
        for tree in runner.BUILD_CONTEXT_TREES:
            for source in (ROOT / tree).rglob("*"):
                relative = source.relative_to(ROOT).as_posix()
                if (
                    source.is_file()
                    and relative not in explicit
                    and not any(
                        part in runner.IGNORED_CONTEXT_NAMES
                        for part in Path(relative).parts
                    )
                ):
                    indexed[relative] = "100644"
        observed_untracked = set(runner.BUILD_CONTEXT_FILES) - set(indexed)
    allowed_untracked = observed_untracked & set(runner.BUILD_CONTEXT_FILES)
    assert observed_untracked == allowed_untracked
    monkeypatch.setattr(
        runner,
        "_git_index_entries",
        lambda _pathspecs: indexed,
    )
    canary_relative = (
        "stack/services/home-agent-core/app/e1_untracked_context_canary.py"
    )
    canary = ROOT / canary_relative
    assert not canary.exists()
    canary.write_text("raise RuntimeError('must not enter Docker context')\n")
    try:
        monkeypatch.setattr(
            runner,
            "_git_untracked_entries",
            lambda _pathspecs: allowed_untracked | {canary_relative},
        )
        with pytest.raises(runner.GateFailure, match="unexpected untracked"):
            runner._prepare_build_context(tmp_path)
        assert not (tmp_path / canary_relative).exists()

        canary.unlink()
        monkeypatch.setattr(
            runner,
            "_git_untracked_entries",
            lambda _pathspecs: allowed_untracked,
        )
        runner._prepare_build_context(tmp_path)

        copied = {
            path.relative_to(tmp_path).as_posix()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        exact = set(runner.BUILD_CONTEXT_FILES)
        for tree in runner.BUILD_CONTEXT_TREES:
            prefix = tree + "/"
            exact.update(path for path in indexed if path.startswith(prefix))
        assert copied == exact
        assert not any(
            part in runner.IGNORED_CONTEXT_NAMES
            for path in copied
            for part in Path(path).parts
        )
        assert not any(
            "AGENT-BOOTSTRAP" in path or path.startswith("changes/") for path in copied
        )
    finally:
        canary.unlink(missing_ok=True)


def test_context_manifest_explicitly_carries_untracked_e1_test_sources() -> None:
    runner = _load_runner()

    assert "stack/services/home-agent-core/tests/e1_postgres_harness.py" in (
        runner.BUILD_CONTEXT_FILES
    )
    assert (
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_erasure_admission_postgres.py"
        in runner.BUILD_CONTEXT_FILES
    )


def test_context_policy_rejects_sensitive_binary_and_git_symlink_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()

    with pytest.raises(runner.GateFailure, match="sensitive"):
        runner._validate_context_path_policy(
            "stack/services/home-agent-core/tests/secrets/canary.py"
        )
    with pytest.raises(runner.GateFailure, match="binary"):
        runner._validate_context_path_policy(
            "stack/services/home-agent-core/tests/canary.png"
        )

    git_symlink = runner.subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            "120000 0000000000000000000000000000000000000000 0\t"
            "stack/services/home-agent-core/app/link.py\0"
        ),
        stderr="",
    )

    def fake_git(command, **_kwargs):
        if "rev-parse" in command:
            return runner.subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=str(ROOT),
                stderr="",
            )
        return git_symlink

    monkeypatch.setattr(runner, "_run", fake_git)
    with pytest.raises(runner.GateFailure, match="symlink"):
        runner._git_index_entries(runner.BUILD_CONTEXT_TREES)


def test_runner_uses_three_fresh_clusters_and_revision_0007_case_clones() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    assert 'REVISION_0007 = "0007_phase3_identity_authority"' in source
    assert 'ADMISSION_TEMPLATE = "e1_template_0007"' in source
    assert 'CASE_DATABASE = "home_agent"' in harness
    assert "alembic_upgrade(database_url(database), REVISION_0010)" in harness
    assert "run_provision_roles(database_url(database))" in harness
    assert "assert_identity_kernel_ownership(database)" in harness
    assert "_set_identity_kernel_function_owner" not in harness
    for phase in ("behavior", "lifecycle", "admission"):
        assert f'"{phase}"' in source
    assert "fail_fast: bool = True" in source
    assert "fail_fast=False" in source


def test_runner_labels_clients_and_cleanup_residue_fails_the_gate() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    for value in (
        "MANAGED_LABEL",
        "RUN_LABEL",
        "PHASE_LABEL",
        "next_client_name",
        '"--name"',
        "_inspect_resource",
        "_cleanup_labeled",
        "Docker cleanup residue remains",
        "for attempt in range(1, 4)",
        "cleanup_failure",
    ):
        assert value in source
    assert "if len(sys.argv) != 1:" in source
    assert "/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=1g" in source
    assert "170000 <= int(version) < 180000" in source
    assert '"--publish"' not in source
    assert '"--network=host"' not in source
    assert "HOME_AGENT_DATABASE_URL = os.getenv" not in source


def test_runner_pins_local_endpoint_sentinel_inventory_and_minimal_context() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    assert PINNED_DIGEST in source
    assert '("unix://", "npipe://")' in source
    for variable in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "DOCKER_CONFIG",
        "BUILDKIT_HOST",
        "BUILDX_BUILDER",
    ):
        assert variable in source
    assert "system_identifier::text" in source
    assert "home_agent_e1.run_id" in source
    assert "_verify_cluster_guard" in source
    assert "str(build_context)" in source
    assert "str(ROOT)" not in source[source.index("def _build_test_image") :]
    assert "assert_guarded_cluster" in harness
    assert "SYSTEM_DATABASES" in harness
    assert "REVOKE ALL ON PARAMETER %%I FROM %%s" in harness
    assert "parameter_acl_item_count" in harness
    assert "pg_catalog.aclexplode(paracl) AS acl" in harness
    assert 'ALTER ROLE "home_agent_owner" RESET ALL' in harness
    assert "owner_role_config is None" in harness


def test_test_image_and_ci_pin_the_reviewed_top_level_inputs() -> None:
    dockerfile = TEST_IMAGE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert PINNED_DIGEST in dockerfile
    assert "requirements.txt" in dockerfile
    assert "requirements-dev.txt" in dockerfile
    assert "python3 -m venv" in dockerfile
    assert "COPY stack/home-agent-deploy/" in dockerfile
    assert "COPY tests/home_agent/" in dockerfile
    assert "COPY tools/run-home-agent-e1-postgres-gate.py" in dockerfile
    assert "COPY .github/workflows/home-agent-e1-postgres.yml" in dockerfile
    assert f"actions/checkout@{CHECKOUT_SHA}" in workflow
    assert "actions/checkout v4.3.1" in workflow


def test_operator_documentation_states_precise_cleanup_limits() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    role_doc = ROLE_DOC.read_text(encoding="utf-8")
    command = "python3 tools/run-home-agent-e1-postgres-gate.py"

    assert command in workflow
    assert command in role_doc
    assert "timeout-minutes: 30" in workflow
    assert "contents: read" in workflow
    assert "SIGKILL" in role_doc
    assert "filtered build context" in role_doc
