from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
TEST_IMAGE = ROOT / "stack/services/home-agent-core/Dockerfile.postgres-test"
PRODUCTION_IMAGE = ROOT / "stack/services/home-agent-core/Dockerfile"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"
SOURCE_PLAN = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
)
ROLE_DOC = ROOT / "stack/home-agent-deploy/IDENTITY-ERASURE-KERNEL-ROLE.md"
HARNESS = ROOT / "stack/services/home-agent-core/tests/e1_postgres_harness.py"
PINNED_DIGEST = "17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a"
CHECKOUT_SHA = "34e114876b0b11c390a56381ad16ebd13914f8d5"
ATTEST_SHA = "f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"
UPLOAD_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
PYTHON_INDEX_DIGEST = (
    "229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36"
)
PYTHON_AMD64_DIGEST = (
    "d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("home_agent_e1_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_source_plan():
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_source_plan_gate_contract", SOURCE_PLAN
    )
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


def test_runner_hard_quarantines_the_ai_host() -> None:
    runner = _load_runner()

    for hostname in (
        "EngineeredLightingServer1",
        "engineeredlightingserver1.example.test",
        "home-app",
    ):
        with pytest.raises(runner.GateFailure, match="2026-07-12 unclean host halt"):
            runner._assert_host_not_quarantined(hostname)

    runner._assert_host_not_quarantined("github-actions-runner")


def test_runner_admits_only_explicit_github_hosted_linux_context() -> None:
    runner = _load_runner()
    admitted = dict(runner.GITHUB_HOSTED_LINUX_CONTEXT)

    runner._assert_execution_admitted(
        hostname="github-actions-runner",
        platform="linux",
        arguments=(runner.GITHUB_HOSTED_LINUX_FLAG,),
        environment=admitted,
    )
    runner._assert_execution_admitted(
        hostname="windows-disposable",
        platform="win32",
        arguments=(),
        environment={},
    )

    with pytest.raises(runner.GateFailure, match="Linux execution is disabled"):
        runner._assert_execution_admitted(
            hostname="renamed-ai-host",
            platform="linux",
            arguments=(),
            environment={},
        )
    with pytest.raises(runner.GateFailure, match="RUNNER_ENVIRONMENT"):
        runner._assert_execution_admitted(
            hostname="self-hosted-runner",
            platform="linux",
            arguments=(runner.GITHUB_HOSTED_LINUX_FLAG,),
            environment={
                **admitted,
                runner.GITHUB_HOSTED_LINUX_ENVIRONMENT: "self-hosted",
            },
        )
    with pytest.raises(runner.GateFailure, match="valid only"):
        runner._assert_execution_admitted(
            hostname="windows-disposable",
            platform="win32",
            arguments=(runner.GITHUB_HOSTED_LINUX_FLAG,),
            environment=admitted,
        )
    with pytest.raises(runner.GateFailure, match="2026-07-12 unclean host halt"):
        runner._assert_execution_admitted(
            hostname="EngineeredLightingServer1",
            platform="linux",
            arguments=(runner.GITHUB_HOSTED_LINUX_FLAG,),
            environment=admitted,
        )


def test_e4_catalog_failure_redacts_unexpected_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    private_canary = "PRIVATE-E4-FAILURE-CONTEXT-MUST-NOT-BE-EMITTED"
    activation_stop = "identity cutover E4 activation contract is not installed"
    state = SimpleNamespace(test_image="test-image")
    phase = SimpleNamespace(name="e4-scaffold", network="e4-network")

    monkeypatch.setattr(
        runner,
        "_docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=f"{private_canary}\nERROR: {activation_stop}\n",
        ),
    )
    runner._apply_grants_expect_failure(
        state,
        phase,
        Path("."),
        "home_agent",
        expected_output=activation_stop,
        failure_label="pinned dormant E4 catalog",
        redact_output=True,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    monkeypatch.setattr(
        runner,
        "_docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=private_canary,
        ),
    )
    with pytest.raises(runner.GateFailure, match="reviewed contract marker"):
        runner._apply_grants_expect_failure(
            state,
            phase,
            Path("."),
            "home_agent",
            expected_output=activation_stop,
            failure_label="pinned dormant E4 catalog",
            redact_output=True,
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_e5_pinned_catalog_failure_redacts_unexpected_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    private_canary = "PRIVATE-E5-FAILURE-CONTEXT-MUST-NOT-BE-EMITTED"
    activation_stop = "identity cutover E4 activation contract is not installed"
    state = SimpleNamespace(test_image="test-image")
    phase = SimpleNamespace(name="e4-scaffold", network="e4-network")

    monkeypatch.setattr(
        runner,
        "_docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=f"{private_canary}\nERROR: {activation_stop}\n",
        ),
    )
    runner._apply_grants_expect_failure(
        state,
        phase,
        Path("."),
        "home_agent",
        expected_output=activation_stop,
        failure_label="pinned dormant E5 catalog",
        redact_output=True,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    monkeypatch.setattr(
        runner,
        "_docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=private_canary,
        ),
    )
    with pytest.raises(runner.GateFailure, match="reviewed contract marker"):
        runner._apply_grants_expect_failure(
            state,
            phase,
            Path("."),
            "home_agent",
            expected_output=activation_stop,
            failure_label="pinned dormant E5 catalog",
            redact_output=True,
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_catalog_discovery_emits_only_exact_changed_fingerprints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    state = SimpleNamespace(test_image="test-image")
    phase = SimpleNamespace(name="e4-scaffold", network="e4-network")
    private_canary = "PRIVATE-CATALOG-CONTEXT-MUST-NOT-BE-EMITTED"
    e3 = runner.CATALOG_DIGEST_CONTRACTS[0]
    e4 = runner.CATALOG_DIGEST_CONTRACTS[1]
    actual_e3 = "1" * 64
    actual_e4 = "2" * 64
    commands: list[list[str]] = []
    results = iter(
        (
            SimpleNamespace(
                returncode=1,
                stdout=(
                    f"{private_canary}\npsql: ERROR:  {e3[2]}\n"
                    f"psql: DETAIL:  expected={e3[1]} actual={actual_e3}\n"
                ),
            ),
            SimpleNamespace(
                returncode=1,
                stdout=(
                    f"{private_canary}\nERROR:  {e4[2]}\n"
                    f"DETAIL:  expected={e4[1]} actual={actual_e4}\n"
                ),
            ),
            SimpleNamespace(
                returncode=1,
                stdout=(
                    "ERROR:  identity cutover E4 activation contract "
                    "is not installed\n"
                ),
            ),
        )
    )

    def fake_docker_run(*_args, **kwargs):
        commands.append(kwargs["command"])
        return next(results)

    monkeypatch.setattr(runner, "_docker_run", fake_docker_run)
    with pytest.raises(runner.GateFailure, match="review and pin"):
        runner._discover_changed_catalog_digests(
            state,
            phase,
            Path("."),
            "home_agent",
        )

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"CATALOG_DIGEST layer=e3 sha256={actual_e3}",
        f"CATALOG_DIGEST layer=e4 sha256={actual_e4}",
    ]
    assert private_canary not in captured.out
    assert captured.err == ""
    assert commands
    assert all("identity-api-acl.sql" in " ".join(command) for command in commands)


def test_catalog_discovery_reports_only_allowlisted_contract_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    private_canary = "PRIVATE-CATALOG-CONTEXT-MUST-NOT-BE-EMITTED"
    safe_failure = "current-authority E5 policy contract mismatch"
    monkeypatch.setattr(
        runner,
        "_docker_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=(
                f"{private_canary}\npsql: ERROR:  {safe_failure}\n"
                "DETAIL: private context remains redacted\n"
            ),
        ),
    )

    with pytest.raises(
        runner.GateFailure,
        match=f"reviewed contract: {safe_failure}",
    ):
        runner._discover_changed_catalog_digests(
            SimpleNamespace(test_image="test-image"),
            SimpleNamespace(name="e4-scaffold", network="e4-network"),
            Path("."),
            "home_agent",
        )

    captured = capsys.readouterr()
    assert private_canary not in captured.out
    assert private_canary not in captured.err


def test_runner_refuses_quarantined_docker_daemon_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    for name in runner.REMOTE_DOCKER_ENV:
        monkeypatch.delenv(name, raising=False)

    def fake_run(command, **_kwargs):
        if command[:3] == ["docker", "context", "show"]:
            output = "default\n"
        elif command[:3] == ["docker", "context", "inspect"]:
            output = "unix:///var/run/docker.sock\n"
        elif "info" in command:
            output = "EngineeredLightingServer1\n"
        else:  # pragma: no cover - exact calls are part of this contract
            raise AssertionError(command)
        return runner.subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
            stderr="",
        )

    monkeypatch.setattr(runner, "_run", fake_run)
    with pytest.raises(runner.GateFailure, match="Docker daemon"):
        runner._validate_local_docker()


def test_every_executed_host_test_node_is_in_the_build_context() -> None:
    """Each `tests/home_agent/...` node a phase runs must be copied in.

    The gate builds a minimal filtered build context, so a node added to a
    phase's list without a matching `BUILD_CONTEXT_FILES` entry does not fail
    at review time. It fails inside the container with "file or directory not
    found", after several minutes of cluster setup. The two lists are
    maintained independently and nothing cross-checked them.
    """

    runner = _load_runner()
    source = RUNNER.read_text(encoding="utf-8")
    # Nodes are written as adjacent string literals:
    #     "/workspace/tests/home_agent/" "test_something.py"
    referenced = set(
        re.findall(
            r'"/workspace/tests/home_agent/"\s*"([^"]+\.py)"',
            source,
        )
    )
    assert referenced, "no host test nodes found; the node syntax changed"
    packaged = set(runner.BUILD_CONTEXT_FILES)
    missing = sorted(
        name for name in referenced if f"tests/home_agent/{name}" not in packaged
    )
    assert not missing, (
        "these host test nodes are executed by a gate phase but are not copied "
        f"into the build context: {missing}"
    )


def test_every_executed_core_test_node_is_in_the_build_context() -> None:
    """The same cross-check for the core image's own `tests/...` nodes.

    These are carried by a context *tree* rather than by individual entries,
    which is why the sibling guard above only had to enumerate the host half.
    That difference is invisible at the call site — both halves are written as
    plain node strings — so assert the covering mechanism explicitly. Drop the
    tree and this names every node that silently stopped being copied.
    """

    runner = _load_runner()
    source = RUNNER.read_text(encoding="utf-8")
    # Core nodes are plain relative literals inside a `nodes=[...]` list:
    #     "tests/test_something.py"
    referenced = {
        name
        for name in re.findall(r'"tests/(test_[^"/]+\.py)"', source)
        # `/workspace/tests/home_agent/...` is the host half, guarded above.
        if not name.startswith("home_agent")
    }
    assert referenced, "no core test nodes found; the node syntax changed"
    prefix = "stack/services/home-agent-core/tests/"
    packaged = set(runner.BUILD_CONTEXT_FILES)
    trees = set(runner.BUILD_CONTEXT_TREES)
    missing = sorted(
        name
        for name in referenced
        if f"{prefix}{name}" not in packaged and prefix.rstrip("/") not in trees
    )
    assert not missing, (
        "these core test nodes are executed by a gate phase but are not copied "
        f"into the build context: {missing}"
    )


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


def test_context_manifest_explicitly_carries_untracked_erasure_test_sources() -> None:
    runner = _load_runner()

    assert "stack/services/home-agent-core/tests/e1_postgres_harness.py" in (
        runner.BUILD_CONTEXT_FILES
    )
    assert (
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_erasure_admission_postgres.py"
        in runner.BUILD_CONTEXT_FILES
    )
    for relative_path in (
        "stack/services/home-agent-core/alembic/versions/"
        "0012_identity_person_erasure_tombstone.py",
        "stack/services/home-agent-core/alembic/versions/"
        "0013_identity_finalizer_kernel.py",
        "stack/services/home-agent-core/alembic/versions/"
        "0014_identity_semantic_cutover_e4.py",
        "stack/services/home-agent-core/alembic/versions/"
        "0015_identity_current_authority_e5.py",
        "stack/services/home-agent-core/app/identity_erasure_schema.py",
        "stack/services/home-agent-bff/src/bff.mjs",
        "app/src/home-agent/api.js",
        "app/src/home-agent/panel.jsx",
        "stack/home-agent-deploy/postgres-pg_hba.conf",
        "stack/home-agent-deploy/test-identity-cutover-secret-lifecycle.sh",
        "stack/home-agent-deploy/operator/reviewed_identity_payload.py",
        "stack/home-agent-deploy/operator/phase3_activation_preflight.py",
        "stack/home-agent-deploy/operator/phase3_activation_source_plan.py",
        "stack/home-agent-deploy/operator/phase3_evidence_receipts.py",
        "stack/home-agent-deploy/operator/isolated_restore_drill.sh",
        "stack/home-agent-deploy/operator/REVIEWED-IDENTITY-PAYLOAD.md",
        "stack/services/home-agent-core/tests/test_identity_person_restore_replay.py",
        "stack/services/home-agent-core/tests/test_ledger_versions.py",
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_erasure_e2_runtime_postgres.py",
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_erasure_e2_schema.py",
        "tests/home_agent/test_identity_erasure_e2_deployment_contract.py",
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_finalizer_e3_runtime_postgres.py",
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_finalizer_e3_schema.py",
        "tests/home_agent/test_identity_finalizer_e3_deployment_contract.py",
        "stack/services/home-agent-core/tests/"
        "test_phase3_identity_cutover_e4_scaffold_postgres.py",
        "tests/home_agent/test_identity_cutover_e4_deployment_contract.py",
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
        "tests/home_agent/" "test_identity_current_authority_e5_deployment_contract.py",
        "tests/home_agent/test_phase3_activation_preflight_e5j.py",
        "tests/home_agent/test_phase3_activation_source_plan_e5k.py",
        "tests/home_agent/test_phase3_evidence_receipts_e5j.py",
    ):
        assert relative_path in runner.BUILD_CONTEXT_FILES


def test_postgres_test_image_copies_exact_e5h_browser_contract_sources() -> None:
    dockerfile = (
        ROOT / "stack/services/home-agent-core/Dockerfile.postgres-test"
    ).read_text(encoding="utf-8")

    assert (
        "COPY app/src/home-agent/api.js /workspace/app/src/home-agent/api.js"
        in dockerfile
    )
    assert (
        "COPY app/src/home-agent/panel.jsx /workspace/app/src/home-agent/panel.jsx"
        in dockerfile
    )
    assert "COPY app/ /workspace/app/" not in dockerfile


def test_context_policy_rejects_sensitive_binary_and_git_symlink_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()

    runner._validate_context_path_policy("stack/home-agent-deploy/postgres-pg_hba.conf")
    with pytest.raises(runner.GateFailure, match="unreviewed"):
        runner._validate_context_path_policy("stack/home-agent-deploy/unreviewed.conf")
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


def test_runner_uses_six_fresh_clusters_and_revision_0007_case_clones() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    assert 'REVISION_0006A = "0006a_worker_lease_arbitration"' in source
    assert 'REVISION_0007 = "0007_phase3_identity_authority"' in source
    assert 'REVISION_0012 = "0012_identity_erasure_e2"' in source
    assert 'REVISION_0013 = "0013_identity_finalizer_e3"' in source
    assert 'REVISION_0014 = "0014_identity_cutover_e4"' in source
    assert 'REVISION_0015 = "0015_current_authority_e5a"' in source
    assert 'ADMISSION_TEMPLATE = "e1_template_0007"' in source
    assert 'CASE_DATABASE = "home_agent"' in harness
    assert "alembic_upgrade(database_url(database), REVISION_0010)" in harness
    assert "run_provision_roles(database_url(database))" in harness
    assert "assert_identity_kernel_ownership(database)" in harness
    assert "_set_identity_kernel_function_owner" not in harness
    for phase in (
        "behavior",
        "lifecycle",
        "admission",
        "e2",
        "e3",
        "e4-scaffold",
    ):
        assert f'"{phase}"' in source
    assert "fail_fast: bool = True" in source
    assert "fail_fast=False" in source


def test_each_fresh_upgrade_path_replays_grants_at_the_live_0006a_pin() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    boundaries = (
        ("def _run_behavior_phase(", "def _run_lifecycle_phase(", "REVISION_0010"),
        ("def _run_lifecycle_phase(", "def _run_admission_phase(", "REVISION_0010"),
        ("def _run_admission_phase(", "def _upgrade_e2_database(", "REVISION_0007"),
        ("def _upgrade_e2_database(", "def _upgrade_e3_database(", "REVISION_0010"),
    )

    for start, end, next_revision in boundaries:
        section = source.split(start, 1)[1].split(end, 1)[0]
        live_pin = section.index("REVISION_0006A")
        grant_replay = section.index("_apply_grants", live_pin)
        next_upgrade = section.index(next_revision, live_pin)
        assert live_pin < grant_replay < next_upgrade


def test_e2_phase_uses_secret_file_role_urls_and_guarded_database_recreation() -> None:
    runner = _load_runner()
    source = RUNNER.read_text(encoding="utf-8")

    expected = {
        "home_agent_api": "postgres_api_password",
        "home_agent_binding_operator": "postgres_binding_operator_password",
        "home_agent_binding_committer": "postgres_binding_committer_password",
        "home_agent_ingest": "postgres_ingest_password",
        "home_agent_worker": "postgres_worker_password",
        "home_agent_erasure": "postgres_erasure_password",
        "home_agent_rollout": "postgres_rollout_password",
        "home_agent_backup": "postgres_backup_password",
    }
    observed = {
        role: password_secret
        for _environment, role, password_secret in runner.E2_RUNTIME_ROLE_URLS
    }
    assert observed == expected
    assert "/run/secrets/{password_secret}" in source
    assert "_guarded_recreate_base_database" in source
    assert "guarded E2 lifecycle database removal" in source
    assert "_verify_cluster_guard" in source
    assert "test_postgresql_e2_clean_roundtrip" in source
    assert "test_postgresql_e2_all_target_rls" in source
    assert "test_postgresql_e2_restore_before_person" in source
    assert "def _apply_grants_expect_failure(" in source
    assert "tamper E2 fact visibility helper in disposable database" in source
    assert "identity erasure E2 function ownership invalid" in source
    assert "verify rejected E2 helper remains quarantined" in source
    assert "rejected E2 helper retained an EXECUTE privilege" in source


def test_e3_phase_is_guarded_dormant_and_uses_secret_file_role_urls() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "def _upgrade_e3_database(" in source
    assert "_upgrade_e2_database(state, phase, secrets_directory)" in source
    assert (
        "_alembic(state, phase, secrets_directory, BASE_DATABASE, REVISION_0013)"
        in (source)
    )
    assert "_apply_grants(state, phase, secrets_directory, BASE_DATABASE)" in source
    assert "def _run_e3_phase(" in source
    assert "TEST_PHASE3_IDENTITY_FINALIZER_E3_OWNER_DATABASE_URL" in source
    assert "TEST_PHASE3_IDENTITY_FINALIZER_E3_FINALIZER_DATABASE_URL" in source
    assert "postgres_identity_finalizer_password" in source
    assert "test_phase3_identity_finalizer_e3_schema.py" in source
    assert "test_phase3_identity_finalizer_e3_runtime_postgres.py" in source
    assert "test_identity_finalizer_e3_deployment_contract.py" in source
    assert "test_apply_grants_revision_0006a_contract.py" in source
    assert "Running isolated dormant revision-0013 E3 contracts" in source


def test_e4_e5_scaffold_phase_is_fresh_dormant_and_secret_file_only() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "def _run_e4_scaffold_phase(" in source
    assert "_upgrade_e3_database(state, phase, secrets_directory)" in source
    assert "def _provision_identity_cutover_roles(" in source
    assert "provision-identity-cutover-roles.sh" in source
    section = source.split("def _run_e4_scaffold_phase(", 1)[1].split(
        "def _build_test_image(", 1
    )[0]
    historical_upgrade = section.index("_upgrade_e3_database(")
    ceremony = section.index("_provision_identity_cutover_roles(")
    quarantine = section.index("_apply_grants(", ceremony)
    e4_upgrade = section.index("REVISION_0014", quarantine)
    assert historical_upgrade < ceremony < quarantine < e4_upgrade
    empty_quarantine = section.index("_apply_grants_expect_failure(", e4_upgrade)
    empty_downgrade = section.index("_alembic_downgrade(", e4_upgrade)
    second_upgrade = section.index("_alembic(", empty_downgrade)
    fixture_seed = section.index("_seed_e4_success_fixture(", second_upgrade)
    login_open = section.index('role="home_agent_identity_cutover"', fixture_seed)
    assert (
        e4_upgrade
        < empty_quarantine
        < empty_downgrade
        < second_upgrade
        < fixture_seed
        < login_open
    )
    assert "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL" in source
    assert "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL" in source
    assert "TEST_PHASE3_IDENTITY_CUTOVER_E4_FINALIZER_DATABASE_URL" in source
    assert "TEST_PHASE3_IDENTITY_CUTOVER_E4_DOCUMENT_B64" in source
    assert "TEST_PHASE3_IDENTITY_CUTOVER_E4_ADMISSION_ID" in source
    assert "home_agent_identity_cutover" in source
    assert "postgres_identity_cutover_password" in source
    assert "seed_phase3_identity_semantic_cutover_e4_success.py" in source
    assert "fixture_file_environment={" in section
    assert "fixture_read_only=False" in source
    assert "run_as_host_user=True" in source
    assert '("--user", f"{os.getuid()}:{os.getgid()}")' in source
    assert "fixture_directory=fixture_directory" in section
    assert "test_phase3_identity_cutover_e4_scaffold_postgres.py" in source
    assert "test_phase3_identity_semantic_cutover_e4_schema.py" in source
    assert "test_phase3_identity_semantic_cutover_e4_runtime_postgres.py" in source
    assert "test_identity_cutover_e4_deployment_contract.py" in source
    assert "test_phase3_identity_current_authority_e5_schema.py" in source
    assert "test_phase3_identity_current_authority_e5_runtime_postgres.py" in (source)
    assert "test_identity_current_authority_e5_deployment_contract.py" in (source)
    assert "def _alembic_expect_failure(" in source
    assert "SET application_name='e5b-role-config-tamper'" in section
    assert "principal_binding_e5b_caller_role_invalid" in section
    assert "RESET application_name" in section
    assert "test_e5b_retains_one_graph_for_hosted_downgrade_refusal" in section
    assert "refusing to remove populated E5b principal-binding authority" in section
    assert "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_OWNER_DATABASE_URL" in (source)
    assert "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_DATABASE_URL" in source
    assert "def _set_disposable_e4_role_login(" in source
    assert 'role="home_agent_identity_finalizer"' in section
    assert 'role="home_agent_identity_cutover"' in section
    assert section.count("finally:") >= 2
    assert "VALID UNTIL 'infinity'" not in source
    # The window is a parameter now, because the registration kernel needs a
    # longer one than the E4 executors. Pin the default and the ceiling rather
    # than a literal interval, which is what "bounded" actually means here.
    assert "minutes: int = 5," in source
    assert "if not 1 <= minutes <= 14:" in source
    assert "interval '{minutes} minutes'" in source
    assert "verify bounded disposable E4 {role} login window" in source
    assert "terminate and verify disposable E4 {role} login is expired" in source
    assert "pg_terminate_backend(activity.pid, 5000)" in source
    assert "empty E4 quarantine before downgrade" in source
    assert "pinned dormant E4 catalog" in source
    assert (
        section.count("identity cutover E4 activation contract is not installed") == 2
    )
    assert "identity principal-binding E5b catalog admission is pending " not in section
    e5_upgrade = section.index("REVISION_0015", login_open)
    e5_downgrade = section.index("_alembic_downgrade(", e5_upgrade)
    e5_reupgrade = section.index("_alembic(", e5_downgrade)
    e5_replay = section.index("_apply_grants_expect_failure(", e5_reupgrade)
    assert e5_upgrade < e5_downgrade < e5_reupgrade < e5_replay
    assert section.count("redact_output=True") == 2
    assert "pending_e4_catalog_digest" not in source
    assert "_extract_e4_catalog_digest" not in source
    assert "capture_e4_catalog_digest" not in source
    assert "E4_CATALOG_SHA256=" not in source
    assert "pending_e5_catalog_digest" not in source
    assert "_extract_e5_catalog_digest" not in source
    assert "_classify_e5_catalog_failure" not in source
    assert "E5_CATALOG_FAILURE_CODES" not in source
    assert "capture_e5_catalog_digest" not in source
    assert "E5_CATALOG_SHA256=" not in source
    assert "pending_e5b_catalog_digest" not in source
    assert "_extract_e5b_catalog_digest" not in source
    assert "_classify_e5b_catalog_failure" not in source
    assert "E5B_CATALOG_FAILURE_CODES" not in source
    assert "capture_e5b_catalog_digest" not in source
    assert "E5B_CATALOG_SHA256=" not in source
    assert "pinned dormant E5b catalog" in section
    assert "unpinned dormant E5b catalog" not in section
    assert "if cleanup_failure is not None:" in source
    assert source.index("if cleanup_failure is not None:") < source.index(
        '"E1/E2/E3/E4 PostgreSQL 17 gate passed; "'
    )
    assert "verify rejected E4 kernel remains quarantined" in source
    assert "Running isolated dormant E4 deployment scaffold" in source
    assert "verify rejected E5 catalog remains broadly quarantined" in source


def test_direct_psycopg_url_exports_are_narrow_and_scheme_distinct() -> None:
    runner = _load_runner()

    sqlalchemy_export = runner._database_url_shell_export(
        "TEST_SQLALCHEMY_DATABASE_URL",
        runner.BASE_DATABASE,
    )
    direct_owner_export = runner._direct_psycopg_database_url_shell_export(
        runner.E4_SCAFFOLD_OWNER_DATABASE_ENV,
        runner.BASE_DATABASE,
    )
    direct_cutover_export = runner._direct_psycopg_database_url_shell_export(
        runner.E4_SCAFFOLD_CUTOVER_DATABASE_ENV,
        runner.BASE_DATABASE,
        "home_agent_identity_cutover",
        "postgres_identity_cutover_password",
    )

    assert '"postgresql+psycopg://' in sqlalchemy_export
    assert '"postgresql://' in direct_owner_export
    assert '"postgresql://' in direct_cutover_export
    assert "+psycopg" not in direct_owner_export
    assert "+psycopg" not in direct_cutover_export
    with pytest.raises(runner.GateFailure, match="unreviewed direct psycopg"):
        runner._direct_psycopg_database_url_shell_export(
            "TEST_UNREVIEWED_DATABASE_URL",
            runner.BASE_DATABASE,
        )
    with pytest.raises(runner.GateFailure, match="unreviewed direct psycopg"):
        runner._direct_psycopg_database_url_shell_export(
            runner.E4_SCAFFOLD_CUTOVER_DATABASE_ENV,
            runner.BASE_DATABASE,
        )


@pytest.mark.parametrize(
    ("failing_role", "expected_roles"),
    [
        (
            "home_agent_identity_finalizer",
            [
                ("home_agent_identity_finalizer", True),
                ("home_agent_identity_finalizer", False),
            ],
        ),
        (
            "home_agent_identity_cutover",
            [
                ("home_agent_identity_finalizer", True),
                ("home_agent_identity_finalizer", False),
                ("home_agent_identity_cutover", True),
                ("home_agent_identity_cutover", False),
            ],
        ),
    ],
)
def test_e4_scaffold_reexpires_role_when_opening_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_role: str,
    expected_roles: list[tuple[str, bool]],
) -> None:
    runner = _load_runner()
    calls: list[tuple[str, bool]] = []

    for name in (
        "_upgrade_e3_database",
        "_provision_identity_cutover_roles",
        "_apply_grants",
        "_verify_cluster_guard",
        "_pytest",
        "_alembic",
        "_assert_database_revision",
        "_apply_grants_expect_failure",
        "_alembic_downgrade",
        "_seed_e4_success_fixture",
    ):
        monkeypatch.setattr(runner, name, lambda *args, **kwargs: None)

    def fail_after_opening(
        *args: object,
        role: str,
        enabled: bool,
        **kwargs: object,
    ) -> None:
        calls.append((role, enabled))
        if role == failing_role and enabled:
            raise runner.GateFailure("simulated failure after ALTER ROLE committed")

    monkeypatch.setattr(runner, "_set_disposable_e4_role_login", fail_after_opening)

    with pytest.raises(runner.GateFailure, match="after ALTER ROLE committed"):
        runner._run_e4_scaffold_phase(
            SimpleNamespace(sentinel="test-sentinel"),
            tmp_path,
            SimpleNamespace(system_identifier="test-system"),
            tmp_path,
        )

    assert calls == expected_roles


def test_e4_fixture_files_are_bounded_private_and_exact(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    document = b'{"authority_scope": "synthetic-test-only"}'
    admission_id = "0197f6f0-0000-7000-8000-000000000001"
    document_path = tmp_path / runner.E4_FIXTURE_DOCUMENT_FILE
    admission_path = tmp_path / runner.E4_FIXTURE_ADMISSION_FILE
    document_path.write_text(
        runner.base64.b64encode(document).decode("ascii") + "\n",
        encoding="ascii",
    )
    admission_path.write_text(admission_id + "\n", encoding="ascii")
    document_path.chmod(0o400)
    admission_path.chmod(0o400)

    runner._validate_e4_fixture_material(tmp_path)
    export = runner._fixture_file_shell_export(
        runner.E4_SUCCESS_DOCUMENT_ENV,
        runner.E4_FIXTURE_DOCUMENT_FILE,
    )
    assert runner.E4_SUCCESS_DOCUMENT_ENV in export
    assert runner.E4_FIXTURE_DOCUMENT_FILE in export
    assert document.decode("ascii") not in export

    (tmp_path / "unexpected").write_text("must fail\n", encoding="ascii")
    with pytest.raises(runner.GateFailure, match="unexpected"):
        runner._validate_e4_fixture_material(tmp_path)


def test_e4_fixture_seed_uses_fixed_core_module_import_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    fixture_directory = tmp_path / "fixture"
    secrets_directory = tmp_path / "secrets"
    fixture_directory.mkdir()
    secrets_directory.mkdir()
    observed: dict[str, object] = {}

    def fake_docker_run(state, image, **kwargs):
        observed.update(kwargs)
        observed["image"] = image
        document_path = fixture_directory / runner.E4_FIXTURE_DOCUMENT_FILE
        admission_path = fixture_directory / runner.E4_FIXTURE_ADMISSION_FILE
        document_path.write_text(
            runner.base64.b64encode(b'{"synthetic":true}').decode("ascii") + "\n",
            encoding="ascii",
        )
        admission_path.write_text(
            "0197f6f0-0000-7000-8000-000000000001\n",
            encoding="ascii",
        )
        document_path.chmod(0o400)
        admission_path.chmod(0o400)
        return runner.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(runner, "_docker_run", fake_docker_run)
    runner._seed_e4_success_fixture(
        SimpleNamespace(test_image="reviewed-test-image"),
        SimpleNamespace(name="e4-scaffold", network="isolated-network"),
        secrets_directory,
        fixture_directory,
    )

    command = observed["command"]
    assert isinstance(command, list)
    assert command[:3] == ["sh", "-eu", "-c"]
    shell = command[3]
    assert isinstance(shell, str)
    assert f'cd "{runner.CORE_CONTAINER_ROOT}";' in shell
    assert f'export PYTHONPATH="{runner.CORE_CONTAINER_ROOT}";' in shell
    assert (
        "exec python -m "
        "tests.seed_phase3_identity_semantic_cutover_e4_success" in shell
    )
    assert "python tests/" not in shell
    assert ".py" not in shell
    assert "$PYTHONPATH" not in shell
    assert runner.E4_LEDGER_WORKER_DATABASE_ENV in shell
    assert "postgresql+psycopg://home_agent_worker:" in shell
    assert "/run/secrets/postgres_worker_password" in shell
    assert observed["fixture_directory"] == fixture_directory
    assert observed["fixture_read_only"] is False
    assert observed["run_as_host_user"] is True
    assert observed["secrets_directory"] == secrets_directory


def test_hosted_e4_success_fixture_has_a_non_skippable_contract() -> None:
    runner_source = RUNNER.read_text(encoding="utf-8")
    seeder_source = (
        ROOT / "stack/services/home-agent-core/tests/"
        "seed_phase3_identity_semantic_cutover_e4_success.py"
    ).read_text(encoding="utf-8")
    runtime_source = (
        ROOT / "stack/services/home-agent-core/tests/"
        "test_phase3_identity_semantic_cutover_e4_runtime_postgres.py"
    ).read_text(encoding="utf-8")

    assert "test_e4_hosted_gate_cannot_silently_skip_admitted_success" in (
        runtime_source
    )
    assert "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL" in runtime_source
    assert "base64.b64decode(encoded_document, validate=True)" in runtime_source
    assert "admission_id.version == 7" in runtime_source
    assert "tests.seed_phase3_identity_semantic_cutover_e4_success" in runner_source
    assert "CORE_CONTAINER_ROOT" in runner_source
    assert "export PYTHONPATH=" in runner_source
    assert "E4_SUCCESS_DOCUMENT_ENV: E4_FIXTURE_DOCUMENT_FILE" in runner_source
    assert "E4_SUCCESS_ADMISSION_ENV: E4_FIXTURE_ADMISSION_FILE" in runner_source
    assert "_seed_fixture" in seeder_source
    assert "_finalize" in seeder_source
    assert "LEDGER_WORKER_DATABASE_ENV" in seeder_source
    assert "DurableWorker._advance_ledger_state(" in seeder_source
    assert "LedgerHead(epoch=0, head_hash=ZERO_HASH)" in seeder_source
    assert seeder_source.index(
        "DurableWorker._advance_ledger_state("
    ) < seeder_source.index("fixture = await _seed_fixture(")
    assert 'evidence["recorded_epoch"] != 0' in seeder_source
    assert 'evidence["recorded_head_hash"] != ZERO_HASH' in seeder_source
    assert "INSERT INTO operations.erasure_ledger_state" not in seeder_source
    assert "insert(schema.erasure_ledger_state)" not in seeder_source
    assert "(CAST(:document AS jsonb))::text" in seeder_source
    assert "len(values) != 27" in seeder_source
    assert seeder_source.count("hide_parameters=True") == 3
    assert "erasure_ledger_verification" in seeder_source
    assert "e3_source_manifest_commitment" in seeder_source
    assert "e3_projection_manifest_commitment" in seeder_source
    assert "e3_commitment_key_epoch" in seeder_source
    assert "print(" not in seeder_source


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
    assert "_assert_execution_admitted()" in source
    assert "/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=1g" in source
    assert "170000 <= int(version) < 180000" in source
    assert '"--publish"' not in source
    assert '"--network=host"' not in source
    assert "HOME_AGENT_DATABASE_URL = os.getenv" not in source
    assert "CLIENT_CONTAINER_LIMITS" in source
    assert "POSTGRES_CONTAINER_LIMITS" in source
    assert '"--cpus"' in source
    assert '"--memory"' in source
    assert '"--memory-swap"' in source
    assert '"--pids-limit"' in source
    assert '"no-new-privileges=true"' in source
    assert "CLIENT_CHURN_COOLDOWN_SECONDS = 0.5" in source
    assert "GITHUB_HOSTED_LINUX_FLAG" in source
    assert "GITHUB_HOSTED_LINUX_CONTEXT" in source
    assert "Docker daemon identity inspection" in source
    assert "E1/E2/E3/E4 gate execution quarantine" in source


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
    assert "requirements-dev.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "python3 -m venv" in dockerfile
    assert "COPY stack/home-agent-deploy/" in dockerfile
    assert (
        "COPY stack/home-agent-compose.yml stack/home-agent.env.example "
        "/workspace/stack/"
    ) in dockerfile
    assert "COPY tests/home_agent/" in dockerfile
    assert "COPY tools/run-home-agent-e1-postgres-gate.py" in dockerfile
    assert "COPY .github/workflows/home-agent-e1-postgres.yml" in dockerfile
    assert f"actions/checkout@{CHECKOUT_SHA}" in workflow
    assert "actions/checkout v4.3.1" in workflow
    assert workflow.count('- "stack/home-agent.env.example"') == 2
    assert (
        workflow.count(
            '- "tests/home_agent/test_apply_grants_revision_0006a_contract.py"'
        )
        == 2
    )
    assert (
        workflow.count(
            '- "tests/home_agent/test_identity_erasure_e2_deployment_contract.py"'
        )
        == 2
    )


def test_operator_documentation_states_precise_cleanup_limits() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    role_doc = ROLE_DOC.read_text(encoding="utf-8")
    command = "python3 tools/run-home-agent-e1-postgres-gate.py"

    assert command in workflow
    assert command in role_doc
    assert "--github-hosted-linux" in workflow
    assert "${{ runner.environment }}" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "contents: read" in workflow
    assert "SIGKILL" in role_doc
    assert "filtered build context" in role_doc
    assert "EngineeredLightingServer1" in role_doc
    assert "no environment-variable bypass" in role_doc


def test_production_core_image_is_hosted_built_attested_and_main_only() -> None:
    dockerfile = PRODUCTION_IMAGE.read_text(encoding="utf-8")
    lock = (
        ROOT / "stack/services/home-agent-core/requirements.lock"
    ).read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    package = workflow.split("  deployable-core-image:", 1)[1]

    assert (
        "ARG PYTHON_BASE_IMAGE=python:3.12-slim@sha256:"
        + PYTHON_INDEX_DIGEST
    ) in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE} AS runtime" in dockerfile
    assert "COPY requirements.txt requirements.lock ./" in dockerfile
    assert (
        "RUN pip install --no-cache-dir --require-hashes -r requirements.lock"
        in dockerfile
    )
    assert lock.count("--hash=sha256:") >= 50
    for requirement in (
        "alembic==1.14.0",
        "cryptography==44.0.0",
        "fastapi==0.115.4",
        "psycopg==3.2.3",
        "pydantic==2.10.2",
        "pydantic-settings==2.6.1",
        "sqlalchemy==2.0.36",
        "uvicorn==0.32.0",
    ):
        assert requirement in lock
    assert " @ " not in lock
    assert "--editable" not in lock
    for token in (
        "runs-on: ubuntu-24.04",
        "pinned Python Linux AMD64 manifest mismatch",
        "github.ref == 'refs/heads/main'",
        "github.event_name == 'push'",
        "github.event_name == 'workflow_dispatch'",
        "id-token: write",
        "attestations: write",
        "artifact-metadata: write",
        f"actions/attest@{ATTEST_SHA}",
        f"actions/upload-artifact@{UPLOAD_SHA}",
        "home-agent-core-linux-amd64.tar.gz",
        "home-agent-core-image-${{ github.sha }}",
        "manifest.json",
        "SHA256SUMS",
        "provenance.sigstore.json",
        "workflow_run_id",
        "source_commit",
        "revision_label",
        'docker save "${CORE_IMAGE}"',
        "tarfile.open(archive, mode=\"r:gz\")",
        "retention-days: 14",
        "compression-level: 0",
    ):
        assert token in package
    assert f"python:3.12-slim@sha256:{PYTHON_INDEX_DIGEST}" in workflow
    assert f"sha256:{PYTHON_AMD64_DIGEST}" in workflow
    assert package.count("docker buildx build") == 1
    assert '--build-arg "PYTHON_BASE_IMAGE=${PYTHON_IMAGE}"' in package
    assert '--label "org.opencontainers.image.revision=${GITHUB_SHA}"' in package
    assert "--network none" in package
    assert "--read-only" in package
    assert "--cap-drop ALL" in package
    assert "--security-opt no-new-privileges" in package
    assert "--memory 256m" in package
    assert "--pids-limit 64" in package
    assert "packages: write" not in package
    assert "${{ secrets." not in package
    assert "docker tag " not in package
    assert "${CORE_DEPLOYMENT_IMAGE}" not in package
    assert package.count('os.environ["CORE_DEPLOYMENT_IMAGE"]') == 1


def test_every_nonweb_activation_source_is_in_the_hosted_gate_context() -> None:
    runner = _load_runner()
    source_plan = _load_source_plan()
    explicit = set(runner.BUILD_CONTEXT_FILES)
    trees = tuple(path.rstrip("/") + "/" for path in runner.BUILD_CONTEXT_TREES)
    web_roots = (
        "app/src/home-agent",
        "stack/services/home-agent-bff/src",
    )

    def covered(path: str) -> bool:
        return path in explicit or any(path.startswith(prefix) for prefix in trees)

    uncovered: list[str] = []
    for relative in source_plan.ACTIVATION_PATHS:
        if relative in web_roots:
            continue
        source = ROOT / relative
        if source.is_file():
            candidates = (relative,)
        else:
            candidates = tuple(
                path.relative_to(ROOT).as_posix()
                for path in source.rglob("*")
                if path.is_file()
                and not any(
                    part in runner.IGNORED_CONTEXT_NAMES
                    for part in path.relative_to(ROOT).parts
                )
            )
        uncovered.extend(path for path in candidates if not covered(path))

    assert uncovered == []
    web_workflow = (
        ROOT / ".github/workflows/home-agent-web-boundary.yml"
    ).read_text(encoding="utf-8")
    assert web_workflow.count('- "app/src/home-agent/**"') == 2
    assert web_workflow.count('- "stack/services/home-agent-bff/**"') == 2


KERNEL_TEST = (
    ROOT
    / "stack/services/home-agent-core/tests"
    / "test_phase3_identity_migration_kernel_postgres.py"
)


def _kernel_phase_section() -> str:
    gate = RUNNER.read_text(encoding="utf-8")
    return gate.split("def _run_migration_kernel_contracts(", 1)[1].split(
        "\ndef ", 1
    )[0]


def test_migration_kernel_phase_seeds_the_predecessor_the_test_expects() -> None:
    """The seeded predecessor must match the kernel test's own constants.

    `register_reviewed_identity_migration` matches the predecessor on four
    values at once -- authorization id, shadow rule version, policy version and
    policy digest -- and the caller has no API that can discover any of them.
    The gate writes that row and the test module declares what it must be, so a
    drift in either surfaces only as `identity_migration_predecessor_invalid`
    from inside a container, minutes into a run. Cross-check them here.
    """

    runner = _load_runner()
    kernel_test = KERNEL_TEST.read_text(encoding="utf-8")
    # Read the statement the gate actually issues, not the source that builds
    # it -- the values reach the SQL through interpolation and never appear as
    # literals in the file.
    seed = runner._migration_kernel_predecessor_sql()

    declared = dict(
        re.findall(
            r"^(SHADOW_AUTHORIZATION_ID|POLICY_VERSION|POLICY_DIGEST) = (.+)$",
            kernel_test,
            re.MULTILINE,
        )
    )
    assert set(declared) == {
        "SHADOW_AUTHORIZATION_ID",
        "POLICY_VERSION",
        "POLICY_DIGEST",
    }, declared

    authorization = re.search(
        r'uuid\.UUID\("([0-9a-f-]+)"\)', declared["SHADOW_AUTHORIZATION_ID"]
    )
    assert authorization, declared["SHADOW_AUTHORIZATION_ID"]
    assert runner.MIGRATION_KERNEL_PREDECESSOR == authorization.group(1)
    assert f"'{authorization.group(1)}'" in seed

    assert f"'{declared['POLICY_VERSION'].strip(chr(34))}'" in seed
    # POLICY_DIGEST is written as a repeated character, not a literal digest.
    character, _, count = declared["POLICY_DIGEST"].partition("*")
    digest = character.strip().strip('"') * int(count)
    assert runner.MIGRATION_KERNEL_POLICY_DIGEST == digest
    assert f"'{digest}'" in seed

    rule_version = re.search(r'"shadow_rule_version": "([^"]+)"', kernel_test)
    assert rule_version, "the kernel test stopped declaring a shadow rule version"
    assert runner.MIGRATION_KERNEL_RULE_VERSION == rule_version.group(1)
    assert f"'{rule_version.group(1)}'" in seed

    # The row must also satisfy `worker_proof_time`, which orders maintenance
    # <= readiness <= authorization. All three are stamped in the past.
    assert seed.index("interval '2 minutes'") < seed.index("interval '1 minute'")
    assert seed.index("interval '1 minute'") < seed.index("interval '30 seconds'")
    # The E3 fixture's own authorization is what has to go, and the schema
    # resolves the order rather than the gate hand-picking one.
    assert seed.startswith("TRUNCATE TABLE operations.rollout_authorizations, ")
    assert "CASCADE" in seed


def test_migration_kernel_phase_is_isolated_and_reverts_itself() -> None:
    """It gets its own database, and both the login and the database unwind.

    The kernel node cannot join the E3 node list: `rollout_transition_once`
    admits exactly one `record_only -> shadow` authorization per database, the
    E3 fixture holds it, and the migration caller has no DELETE to reclaim it.
    """

    gate = RUNNER.read_text(encoding="utf-8")
    kernel_node = "tests/test_phase3_identity_migration_kernel_postgres.py"

    e3 = gate.split("def _run_e3_phase(", 1)[1].split("\ndef ", 1)[0]
    assert kernel_node not in e3.split("_run_migration_kernel_contracts", 1)[0], (
        "the kernel node joined the E3 node list; it would collide with that "
        "phase's own one-shot authorization"
    )
    assert "_run_migration_kernel_contracts(state, secrets_directory, phase)" in e3

    section = _kernel_phase_section()
    assert f'"{kernel_node}"' in section
    # The phase names the base database exactly twice -- as the clone source
    # and in the guard inventory -- and never runs the test against it.
    assert section.count("BASE_DATABASE") == 2
    assert 'role="home_agent_identity_migration"' in section
    # The registration kernel refuses a window wider than fifteen minutes.
    assert "minutes=14" in section
    assert "DROP DATABASE IF EXISTS" in section
    # The login re-expires and the database drops even when the test fails.
    assert section.count("finally:") == 2
    assert "enabled=False" in section
    assert section.index("enabled=True") < section.index("finally:")
