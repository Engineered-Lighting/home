from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXECUTOR = (
    ROOT / "stack/home-agent-deploy/operator/phase3_migration_executor.py"
)
SOURCE_PLAN = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
)
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
    monkeypatch.setattr(
        module, "_unlock_activation", lambda descriptor: None
    )
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
    monkeypatch.setattr(
        module, "_unlock_activation", lambda descriptor: None
    )
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
    assert '"app.migration_guard"' in source
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
    assert "identity_authority_admission_writer_not_installed" in source
    assert "identity_disposable_role_activation_not_installed" in source
    assert '"activation_executor_installed": trusted' in source


def test_e5t_is_carried_by_the_filtered_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "phase3_migration_executor.py" in runner
    assert "test_phase3_migration_executor_e5t.py" in runner
    assert "test_phase3_migration_executor_e5t.py" in workflow
    assert "E5s/E5t PostgreSQL gate" in workflow
    assert "E5s/E5t authority gate" in workflow
