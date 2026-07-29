from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
SEQUENCER = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_activation_sequencer.py"
)
GRANTS = ROOT / "stack/home-agent-deploy/apply-grants.sh"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_activation_sequencer_e5m",
        SEQUENCER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trusted_plan() -> dict[str, object]:
    return {
        "source_pack_matches_hosted_acceptance": True,
        "fixed_migration_entrypoints_installed": True,
        "current_commit": "a" * 40,
        "accepted_postgres_run_id": "30396371133",
        "accepted_web_run_id": "30473087421",
    }


def _passing_preflight() -> dict[str, object]:
    return {
        "contract": "phase3-activation-preflight-e5j-v1",
        "preflight_passed": True,
        "blockers": [],
        "authoritative": False,
        "enables_writes": False,
        "runs_migrations": False,
        "changes_rollout_mode": False,
        "source_acceptance": "valid",
    }


def test_source_receipt_binds_exact_hosted_and_web_acceptance(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "grant_contract_installed", lambda: True)

    receipt = module.build_source_receipt(_trusted_plan())

    assert receipt == {
        "contract": "phase3-source-acceptance-e5j-v1",
        "target_revision": "0021_parent_status_e5h",
        "source_commit": "a" * 40,
        "postgres_authority_run_id": "30396371133",
        "postgres_authority_status": "success",
        "web_boundary_run_id": "30473087421",
        "web_boundary_status": "success",
        "activation_contract_status": "installed",
    }


@pytest.mark.parametrize(
    "change",
    (
        {"source_pack_matches_hosted_acceptance": False},
        {"fixed_migration_entrypoints_installed": False},
        {"current_commit": "not-a-commit"},
        {"accepted_postgres_run_id": "queued"},
        {"accepted_web_run_id": "queued"},
    ),
)
def test_source_admission_fails_closed(change, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "grant_contract_installed", lambda: True)
    plan = _trusted_plan()
    plan.update(change)

    with pytest.raises(module.SequencerError):
        module.build_source_receipt(plan)


def test_grant_arming_requires_the_complete_non_authoritative_preflight(
    monkeypatch,
) -> None:
    module = _module()
    writes: list[tuple[Path, bytes]] = []
    monkeypatch.setattr(module, "_atomic_write", lambda path, raw: writes.append((path, raw)))

    monkeypatch.setattr(module, "preflight", _passing_preflight)
    module.arm_grants()
    assert writes == [
        (
            module.GRANT_PERMIT_PATH,
            (module.GRANT_PERMIT_VALUE + "\n").encode("ascii"),
        )
    ]

    for changed in (
        {"preflight_passed": False},
        {"blockers": ["record_only_evidence_not_ready"]},
        {"authoritative": True},
        {"enables_writes": True},
        {"runs_migrations": True},
        {"changes_rollout_mode": True},
    ):
        report = _passing_preflight()
        report.update(changed)
        monkeypatch.setattr(module, "preflight", lambda report=report: report)
        with pytest.raises(module.SequencerError):
            module.arm_grants()


def test_status_is_content_minimized_and_never_claims_execution(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "source_plan", _trusted_plan)
    monkeypatch.setattr(module, "preflight", _passing_preflight)
    monkeypatch.setattr(module, "grant_contract_installed", lambda: True)
    monkeypatch.setattr(module, "_permit_is_armed", lambda: False)

    report = module.status()

    assert report["contract"] == "phase3-activation-sequencer-e5m-v1"
    assert report["source_admitted"] is True
    assert report["grant_activation_contract_installed"] is True
    assert report["grant_permit_armed"] is False
    assert report["migration_executor_enabled"] is False
    assert report["authoritative"] is False
    assert report["enables_writes"] is False
    assert report["runs_migrations"] is False
    assert report["changes_rollout_mode"] is False
    assert len(report["activation_sequence"]) == 21


def test_grant_replay_requires_a_root_owned_single_link_permit_before_sql() -> None:
    source = GRANTS.read_text(encoding="utf-8")
    permit = source.index("phase3 grant activation permit is not mounted")
    first_sql = source.index("psql -v ON_ERROR_STOP=1 <<'SQL'")

    assert permit < first_sql
    assert '"/run/phase3-activation/permit"' in source
    assert "0:0:600:1" in source
    assert "0017_authenticated_binding_e5c" in source
    assert "0021_parent_status_e5h" in source
    assert "phase3-grant-permit-e5m-v1" in source


def test_compose_activation_grant_surface_is_operator_only_and_isolated() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    service = compose.split("  grant-phase3-activation:\n", 1)[1].split(
        "\n  core-api:", 1
    )[0]

    assert "profiles: [operator]" in service
    assert 'restart: "no"' in service
    assert "read_only: true" in service
    assert "cap_drop: [ALL]" in service
    assert "no-new-privileges:true" in service
    assert "phase3_grant_permit_phase3_activation" in service
    assert "postgres_owner_password_phase3_activation" in service
    assert "networks: [postgres-net]" in service
    assert "logging:\n      driver: none" in service
    assert "ports:" not in service
    assert "api-net" not in service


def test_sequencer_has_no_migration_or_compose_execution_path() -> None:
    source = SEQUENCER.read_text(encoding="utf-8")
    live = source.split("def source_plan()", 1)[1]

    for forbidden in (
        '["docker"',
        '"compose"',
        '"alembic"',
        '"psql"',
        "subprocess.Popen",
        "os.system",
        "shell=True",
    ):
        assert forbidden not in live
    assert "migration_executor_enabled" in source
    assert '"runs_migrations": False' in source


def test_e5m_is_carried_by_the_filtered_hosted_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert "phase3_activation_sequencer.py" in workflow
    assert "test_phase3_activation_sequencer_e5m.py" in workflow
    assert "phase3_activation_sequencer.py" in runner
    assert "test_phase3_activation_sequencer_e5m.py" in runner
    assert "E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v PostgreSQL gate" in workflow
    assert "E5k/E5l/E5m/E5n/E5o/E5p/E5q/E5r/E5s/E5t/E5u/E5v authority gate" in workflow
