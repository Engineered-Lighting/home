from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack/home-agent-deploy/activate-identity-authority-role.sh"
CEREMONY = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_identity_authority_ceremony.py"
)
COMPOSE = ROOT / "stack/home-agent-compose.yml"
MATERIALIZE = ROOT / "stack/home-agent-deploy/materialize-secrets.sh"
SOURCE_PLAN = (
    ROOT
    / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
)
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"


def _module() -> ModuleType:
    operator_directory = CEREMONY.parent
    sys.path.insert(0, str(operator_directory))
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_identity_authority_role_ceremony_e5v",
            CEREMONY,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(operator_directory))


def test_role_script_is_fixed_bounded_and_force_reexpires_sessions() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "activate|deactivate|status" in source
    assert 'finalizer) role_name="home_agent_identity_finalizer"' in source
    assert 'cutover) role_name="home_agent_identity_cutover"' in source
    assert (
        "phase3-grant-permit-e5m-v1:"
        "0017_authenticated_binding_e5c:0021_parent_status_e5h"
    ) in source
    assert "0015_current_authority_e5a" in source
    assert "interval '2 minutes'" in source
    assert "interval '2 minutes 5 seconds'" in source
    assert "1970-01-01 00:00:00+00" in source
    assert "pg_catalog.pg_terminate_backend" in source
    assert "rolconnlimit <> 1" in source
    assert "role_row.rolsuper" in source
    assert "role_row.rolbypassrls" in source
    assert "active_sessions <> 0" in source
    assert "eval " not in source


def test_compose_role_surface_is_operator_only_and_owner_isolated() -> None:
    source = COMPOSE.read_text(encoding="utf-8")
    block = source.split("  identity-role-activation:", 1)[1].split(
        "\n  core-api:", 1
    )[0]
    assert "profiles: [operator]" in block
    assert 'restart: "no"' in block
    assert "read_only: true" in block
    assert "cap_drop: [ALL]" in block
    assert "no-new-privileges:true" in block
    assert "activate-identity-authority-role.sh" in block
    assert "postgres_owner_password_identity_role_activation" in block
    assert "phase3_grant_permit_identity_role_activation" in block
    assert "networks: [postgres-net]" in block
    assert "ports:" not in block
    assert "driver: none" in block
    assert "128m" in block
    assert "pids_limit: 64" in block

    materialize = MATERIALIZE.read_text(encoding="utf-8")
    assert (
        "install_secret identity-role-activation \\\n"
        "  postgres_owner_password postgres_owner_password 0 0"
    ) in materialize


def test_root_ceremony_binds_private_request_to_activation_and_cleanup() -> None:
    source = CEREMONY.read_text(encoding="utf-8")
    assert "sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)" in source
    assert '"identity-role-activation"' in source
    assert '"identity-finalizer"' in source
    assert '"identity-cutover"' in source
    assert '"--no-deps"' in source
    assert "_role_ceremony(\"activate\", command.name)" in source
    assert "_role_ceremony(\"deactivate\", command.name)" in source
    assert "finally:" in source
    assert 'activation._guard_revision("0015_current_authority_e5a")' in source
    assert "activation._require_trusted_source()" in source
    assert "activation._require_fresh_permit" in source
    assert "activation._activation_lock()" in source
    assert "activation._running_protected_services()" in source
    assert "stderr=subprocess.DEVNULL" in source
    assert "shell=False" in source
    assert "private_request" not in source.split("print(", 1)[-1]


def test_root_ceremony_orders_guards_activation_invocation_and_reexpiry(
    monkeypatch,
) -> None:
    module = _module()
    calls: list[str] = []
    activation = module.activation
    monkeypatch.setattr(
        activation, "_require_root_linux", lambda: calls.append("root")
    )
    monkeypatch.setattr(
        activation, "_require_trusted_source", lambda: calls.append("source")
    )
    monkeypatch.setattr(
        activation,
        "_require_fresh_permit",
        lambda now: calls.append("permit"),
    )
    monkeypatch.setattr(activation, "_activation_lock", lambda: 123)
    monkeypatch.setattr(
        activation,
        "_unlock_activation",
        lambda descriptor: calls.append(f"unlock:{descriptor}"),
    )
    monkeypatch.setattr(
        activation, "_running_protected_services", lambda: frozenset()
    )
    monkeypatch.setattr(
        activation,
        "_guard_revision",
        lambda revision: calls.append(f"guard:{revision}"),
    )
    monkeypatch.setattr(module, "_request", lambda: b"private")
    monkeypatch.setattr(
        module,
        "_role_ceremony",
        lambda mode, authority: calls.append(f"role:{mode}:{authority}"),
    )
    monkeypatch.setattr(
        module,
        "_invoke",
        lambda command, request: {
            "contract": command.expected_contract,
            "result_id": "018f3f7a-8b4d-7abc-8def-0123456789ab",
            "status": "committed",
        },
    )

    result = module.execute(module.COMMANDS["finalize"])

    assert calls == [
        "root",
        "source",
        "permit",
        "guard:0015_current_authority_e5a",
        "role:activate:finalize",
        "role:deactivate:finalize",
        "guard:0015_current_authority_e5a",
        "unlock:123",
    ]
    assert result["status"] == "committed_and_reexpired"


def test_root_ceremony_reexpires_after_invocation_failure(monkeypatch) -> None:
    module = _module()
    activation = module.activation
    calls: list[str] = []
    monkeypatch.setattr(activation, "_require_root_linux", lambda: None)
    monkeypatch.setattr(activation, "_require_trusted_source", lambda: None)
    monkeypatch.setattr(activation, "_require_fresh_permit", lambda now: None)
    monkeypatch.setattr(activation, "_activation_lock", lambda: 123)
    monkeypatch.setattr(activation, "_unlock_activation", lambda descriptor: None)
    monkeypatch.setattr(
        activation, "_running_protected_services", lambda: frozenset()
    )
    monkeypatch.setattr(activation, "_guard_revision", lambda revision: None)
    monkeypatch.setattr(module, "_request", lambda: b"private")
    monkeypatch.setattr(
        module,
        "_role_ceremony",
        lambda mode, authority: calls.append(f"{mode}:{authority}"),
    )
    monkeypatch.setattr(
        module,
        "_invoke",
        lambda command, request: (_ for _ in ()).throw(
            module.AuthorityCeremonyError("failed")
        ),
    )

    with pytest.raises(module.AuthorityCeremonyError):
        module.execute(module.COMMANDS["cutover"])

    assert calls == ["activate:cutover", "deactivate:cutover"]


def test_source_plan_closes_disposable_role_boundary() -> None:
    source = SOURCE_PLAN.read_text(encoding="utf-8")
    assert "activate-identity-authority-role.sh" in source
    assert "phase3_identity_authority_ceremony.py" in source
    assert "identity_disposable_role_activation_not_installed" not in source
    assert '"identity_disposable_role_activation_installed": trusted' in source


def test_e5v_is_exercised_by_the_filtered_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "activate-identity-authority-role.sh" in runner
    assert "phase3_identity_authority_ceremony.py" in runner
    assert "_exercise_identity_authority_role_ceremony" in runner
    assert "test_identity_authority_role_ceremony_e5v.py" in runner
    assert workflow.count("test_identity_authority_role_ceremony_e5v.py") == 2
    assert workflow.count("activate-identity-authority-role.sh") == 2
    assert workflow.count("phase3_identity_authority_ceremony.py") == 2
    assert "E5u/E5v PostgreSQL gate" in workflow
    assert "E5u/E5v authority gate" in workflow
