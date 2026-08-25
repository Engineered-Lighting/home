from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = ROOT / "stack/home-agent-deploy/operator/phase3_migration_executor.py"
SOURCE_PLAN = ROOT / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_migration_executor_e5t",
        EXECUTOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_executor_exposes_only_the_five_reviewed_revision_edges() -> None:
    module = _module()

    assert {
        name: (stage.entrypoint, stage.source_revision, stage.target_revision)
        for name, stage in module.STAGES.items()
    } == {
        "migrate-finalizer": (
            "phase3-migrate-finalizer",
            "0006a_worker_lease_arbitration",
            "0013_identity_finalizer_e3",
        ),
        "migrate-current-authority": (
            "phase3-migrate-current-authority",
            "0013_identity_finalizer_e3",
            "0015_current_authority_e5a",
        ),
        "migrate-authenticated-binding": (
            "phase3-migrate-authenticated-binding",
            "0015_current_authority_e5a",
            "0017_authenticated_binding_e5c",
        ),
        "migrate-parent-authority": (
            "phase3-migrate-parent-authority",
            "0017_authenticated_binding_e5c",
            "0018_parent_relationship_e5d",
        ),
        "migrate-parent-status": (
            "phase3-migrate-parent-status",
            "0018_parent_relationship_e5d",
            "0021_parent_status_e5h",
        ),
    }


def test_execution_orders_source_permit_stop_and_revision_guards(
    monkeypatch,
) -> None:
    module = _module()
    calls: list[str] = []

    monkeypatch.setattr(module, "_require_root_linux", lambda: calls.append("root"))
    monkeypatch.setattr(
        module, "_require_trusted_source", lambda: calls.append("source")
    )
    monkeypatch.setattr(
        module,
        "_require_fresh_permit",
        lambda now: calls.append(f"permit:{now.isoformat()}"),
    )
    monkeypatch.setattr(module, "_activation_lock", lambda: 123)
    monkeypatch.setattr(module, "_unlock_activation", lambda descriptor: None)
    monkeypatch.setattr(module, "_running_protected_services", lambda: frozenset())
    monkeypatch.setattr(
        module, "_guard_revision", lambda revision: calls.append(f"guard:{revision}")
    )
    monkeypatch.setattr(
        module, "_migrate", lambda stage: calls.append(f"migrate:{stage.entrypoint}")
    )
    now = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)

    result = module.execute(module.STAGES["migrate-finalizer"], now=now)

    assert calls == [
        "root",
        "source",
        f"permit:{now.isoformat()}",
        "guard:0006a_worker_lease_arbitration",
        "migrate:phase3-migrate-finalizer",
        "guard:0013_identity_finalizer_e3",
    ]
    assert result["status"] == "verified"


def test_execution_refuses_to_migrate_while_any_private_surface_runs(
    monkeypatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_require_root_linux", lambda: None)
    monkeypatch.setattr(module, "_require_trusted_source", lambda: {})
    monkeypatch.setattr(module, "_require_fresh_permit", lambda now: None)
    monkeypatch.setattr(module, "_activation_lock", lambda: 123)
    monkeypatch.setattr(module, "_unlock_activation", lambda descriptor: None)
    monkeypatch.setattr(
        module, "_running_protected_services", lambda: frozenset({"core-api"})
    )
    monkeypatch.setattr(
        module,
        "_migrate",
        lambda stage: pytest.fail("migration must not run"),
    )

    with pytest.raises(module.MigrationExecutionError):
        module.execute(module.STAGES["migrate-finalizer"])


def test_executor_never_builds_pulls_starts_or_uses_a_shell() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "os.geteuid() != 0" in source
    assert "shell=False" in source
    assert '"--no-deps"' in source
    assert "app.migration_guard" in source
    assert "_guard_revision(stage.source_revision)" in source
    assert "_guard_revision(stage.target_revision)" in source
    assert "PERMIT_MAX_AGE = timedelta(hours=4)" in source
    for forbidden in (
        "shell=True",
        "os.system",
        '"build"',
        '"pull"',
        '"up"',
        '"start"',
        "alembic upgrade head",
    ):
        assert forbidden not in source


def test_source_plan_tracks_executor_and_retains_later_boundaries() -> None:
    source = SOURCE_PLAN.read_text(encoding="utf-8")
    assert "phase3_migration_executor.py" in source
    assert "activation_migration_executor_not_installed" not in source
    assert "identity_authority_admission_writer_not_installed" not in source
    assert "identity_disposable_role_activation_not_installed" not in source
    assert '"activation_executor_installed": trusted' in source


def test_e5t_is_carried_by_the_filtered_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "phase3_migration_executor.py" in runner
    assert "test_phase3_migration_executor_e5t.py" in runner
    assert "test_phase3_migration_executor_e5t.py" in workflow
    assert "E5s/E5t/E5u/E5v/E5w/E5x PostgreSQL gate" in workflow
    assert "E5s/E5t/E5u/E5v/E5w/E5x authority gate" in workflow


def test_revision_guard_loads_the_database_secret_instead_of_bypassing_it() -> None:
    module = _module()

    arguments = module.revision_guard_arguments("0013_identity_finalizer_e3")

    # The image entrypoint is the only component that turns
    # HOME_AGENT_DATABASE_URL_FILE into HOME_AGENT_DATABASE_URL. Overriding it
    # with `python` left app.migration_guard without a database URL, so the
    # guard failed closed for every well-formed deployment.
    assert "--entrypoint" in arguments
    assert arguments[arguments.index("--entrypoint") + 1] == "sh"
    assert "python" not in arguments
    assert "-m" not in arguments
    assert arguments[-1] == "0013_identity_finalizer_e3"
    assert arguments[-2] == module.DATABASE_URL_SECRET
    assert "--no-deps" in arguments
    assert "migrate" in arguments
    for forbidden in ("build", "pull", "up", "start"):
        assert forbidden not in arguments


def test_revision_guard_passes_only_the_secret_path_never_its_value() -> None:
    module = _module()

    arguments = module.revision_guard_arguments("0015_current_authority_e5a")

    assert module.DATABASE_URL_SECRET == "/run/secrets/database_url"
    # Only the path may cross the command line; the value is read inside the
    # container so it never reaches host process arguments or logs.
    assert all("postgresql" not in argument for argument in arguments)
    assert sum(argument == module.DATABASE_URL_SECRET for argument in arguments) == 1


@pytest.mark.parametrize(
    ("content", "environment", "expected"),
    (
        ("postgresql+psycopg://home_agent_owner:a@postgres:5432/home_agent", {}, 0),
        (None, {}, 78),
        ("", {}, 78),
        ("has whitespace", {}, 78),
        ("tab\there", {}, 78),
        (
            "postgresql+psycopg://home_agent_owner:a@postgres:5432/home_agent",
            {"HOME_AGENT_DATABASE_URL": "already-set"},
            78,
        ),
    ),
)
def test_revision_guard_script_mirrors_entrypoint_secret_rules(
    tmp_path: Path,
    content: str | None,
    environment: dict[str, str],
    expected: int,
) -> None:
    import os
    import subprocess

    module = _module()

    secret = tmp_path / "database_url"
    if content is not None:
        secret.write_text(content, encoding="utf-8")

    # Stub `python` so the script's final exec is observable without a database.
    stub_directory = tmp_path / "bin"
    stub_directory.mkdir()
    stub = stub_directory / "python"
    stub.write_text('#!/bin/sh\necho "$HOME_AGENT_DATABASE_URL"\n', encoding="utf-8")
    stub.chmod(0o755)

    result = subprocess.run(
        ["sh", "-c", module.REVISION_GUARD_SCRIPT, "sh", str(secret), "0013_x"],
        capture_output=True,
        env={"PATH": f"{stub_directory}:{os.environ['PATH']}", **environment},
        check=False,
    )

    assert result.returncode == expected
    if expected == 0:
        assert result.stdout.decode().strip() == content


def test_subprocess_refusals_carry_distinct_categorical_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    class Result:
        def __init__(self, returncode: int, stdout: bytes) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = b""

    cases = {
        "exit_nonzero": Result(1, b"{}"),
        "stdout_oversize": Result(0, b"x" * (module.MAX_OUTPUT_BYTES + 1)),
        "stdout_nul": Result(0, b"ok\0"),
    }
    for expected, result in cases.items():
        monkeypatch.setattr(
            module.subprocess, "run", lambda *a, _r=result, **k: _r
        )
        with pytest.raises(module.MigrationExecutionError) as caught:
            module._run(["docker", "compose", "ps"])
        assert caught.value.code == expected
        assert str(caught.value) == "phase3 migration command failed"

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise module.subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(module.subprocess, "run", raise_timeout)
    with pytest.raises(module.MigrationExecutionError) as caught:
        module._run(["docker", "compose", "ps"])
    assert caught.value.code == "timeout"

    def raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("no such executable")

    monkeypatch.setattr(module.subprocess, "run", raise_oserror)
    with pytest.raises(module.MigrationExecutionError) as caught:
        module._run(["docker", "compose", "ps"])
    assert caught.value.code == "spawn_failed"


def test_diagnostic_codes_satisfy_the_journal_character_contract() -> None:
    module = _module()
    allowed = set("abcdefghijklmnopqrstuvwxyz_0123456789")
    assert module.DIAGNOSTIC_CODES
    for code in module.DIAGNOSTIC_CODES:
        assert code and len(code) <= 96
        assert set(code) <= allowed
    assert module.MigrationExecutionError("x", code="Marcelo at 12 Main").code is None


def test_people_bearing_subprocesses_are_never_echoed() -> None:
    module = _module()
    for command in (
        ["/usr/local/libexec/home-agent/local-backup.sh", "/srv/home-agent/config/home-agent.env"],
        ["/srv/operator/isolated_restore_drill.sh", "label"],
        ["/usr/local/sbin/home-agent-identity-signing", "finalize"],
        ["docker", "compose", "run", "--rm", "migrate", "identity-admit-finalizer"],
        ["docker", "compose", "run", "--rm", "migrate", "identity-admit-cutover"],
        ["docker", "compose", "run", "--rm", "migrate", "identity-register-run"],
        ["python3", "off_host_backup_writer.py"],
        ["python3", "phase3_privacy_cutover_observer.py"],
        ["python3", "phase3_capture_legacy_identity_snapshot.py"],
        ["python3", "phase3_identity_credential_provisioner.py"],
    ):
        assert module.diagnosable(command) is False, command
    for command in (
        ["docker", "compose", "ps", "--status", "running", "--services"],
        ["docker", "inspect", "home-agent-core"],
        ["docker", "compose", "stop", "core-api"],
        ["docker", "compose", "config", "--quiet"],
        ["python3", "phase3_migration_executor.py", "migrate-current-authority"],
    ):
        assert module.diagnosable(command) is True, command


def test_every_reviewed_migration_stage_stays_diagnosable() -> None:
    module = _module()
    for stage in module.STAGES.values():
        command = module._compose("run", "--rm", "--no-deps", "migrate", stage.entrypoint)
        assert module.diagnosable(command) is True, stage.entrypoint
    guard = module._compose(*module.revision_guard_arguments("0015_current_authority_e5a"))
    assert module.diagnosable(guard) is True


def test_diagnostic_tail_redacts_credential_userinfo() -> None:
    module = _module()
    raw = b"could not connect: postgresql://owner:hunter2@postgres:5432/home_agent"
    redacted = module.redact_diagnostic(raw)
    assert b"hunter2" not in redacted
    assert b"://<redacted>@" in redacted
    assert module.redact_diagnostic(None) == b""


def test_governed_error_codes_name_only_kernel_identifiers() -> None:
    module = _module()
    raw = (
        b"psycopg.errors.RaiseException: identity_finalizer_live_run_mismatch\n"
        b"CONTEXT:  PL/pgSQL function operations.finalize_reviewed_identity_migration"
    )
    assert module.governed_error_codes(raw) == (
        "identity_finalizer_live_run_mismatch",
    )
    assert module.governed_error_codes(b"connection refused") == ()
    assert module.governed_error_codes(None) == ()


def test_report_diagnostic_is_silent_unless_explicitly_enabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    module.report_diagnostic(["docker", "compose", "ps"], b"boom", enabled=False)
    assert capsys.readouterr().err == ""
    module.report_diagnostic(["docker", "compose", "ps"], b"boom", enabled=True)
    assert "boom" in capsys.readouterr().err
    module.report_diagnostic(
        ["/usr/local/libexec/home-agent/local-backup.sh"], b"boom", enabled=True
    )
    assert capsys.readouterr().err == ""


def test_executor_no_longer_discards_subprocess_diagnostics() -> None:
    source = EXECUTOR.read_text(encoding="utf-8")
    assert "stderr=subprocess.DEVNULL" not in source
    assert "stderr=subprocess.PIPE" in source
