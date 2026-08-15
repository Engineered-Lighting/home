from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
RUNNER = OPERATOR / "phase3_activation_runner.py"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
MATERIALIZE = ROOT / "stack/home-agent-deploy/materialize-secrets.sh"
PREFLIGHT = ROOT / "stack/home-agent-deploy/preflight.sh"


def _module() -> ModuleType:
    sys.path.insert(0, str(OPERATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_phase3_activation_runner_e5ad",
            RUNNER,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(OPERATOR))


class MemoryStore:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.value: dict[str, object] | None = None

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, state):
        self.value = copy.deepcopy(self.module.validate_state(state))

    def record_source_transition(self, state, new_commit):
        self.transition = {
            "from": state["source_commit"],
            "to": new_commit,
        }


class FakeBackend:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.binding_confirmed = False
        self.parents_confirmed = False
        self.calls: list[str] = []
        self.contain_calls = 0
        self.fail_once_at: str | None = None

    def perform(self, step, state):
        self.calls.append(step)
        if self.fail_once_at == step:
            self.fail_once_at = None
            raise self.module.ActivationRunnerError("injected failure")
        if step == "await_authenticated_binding" and not self.binding_confirmed:
            raise self.module.ActivationPause("awaiting_authenticated_binding")
        if step == "await_parent_confirmation" and not self.parents_confirmed:
            raise self.module.ActivationPause("awaiting_parent_confirmation")

    def contain(self):
        self.contain_calls += 1


def _trusted_source(module: ModuleType, monkeypatch) -> None:
    monkeypatch.setattr(
        module.source_plan,
        "live_report",
        lambda: {
            "current_commit": "a" * 40,
            "source_acceptance_receipt_issuable": True,
            "blockers": [],
        },
    )


def test_runner_advances_through_both_private_pauses_and_completes(
    monkeypatch,
) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    runner = module.Runner(backend, store)

    first = runner.advance()
    assert first["status"] == "paused"
    assert first["next_step"] == "await_authenticated_binding"
    assert first["pause_code"] == "awaiting_authenticated_binding"

    backend.binding_confirmed = True
    second = runner.advance()
    assert second["status"] == "paused"
    assert second["next_step"] == "await_parent_confirmation"
    assert second["pause_code"] == "awaiting_parent_confirmation"

    backend.parents_confirmed = True
    final = runner.advance()
    assert final == {
        "contract": module.CONTRACT,
        "status": "complete",
        "next_step": "none",
        "completed_step_count": len(module.STEPS),
        "pause_code": "none",
        "last_error_code": "none",
    }
    assert backend.contain_calls == 0
    assert backend.calls.count("await_authenticated_binding") == 2
    assert backend.calls.count("await_parent_confirmation") == 2


def test_private_people_review_pauses_before_backup_or_service_change(
    monkeypatch,
) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)

    class ReviewPauseBackend(FakeBackend):
        def perform(self, step, state):
            self.calls.append(step)
            if step == "await_reviewed_people_packet":
                raise self.module.ActivationPause("awaiting_private_people_review")

    backend = ReviewPauseBackend(module)
    report = module.Runner(backend, store).advance()

    assert report["status"] == "paused"
    assert report["next_step"] == "await_reviewed_people_packet"
    assert report["pause_code"] == "awaiting_private_people_review"
    assert backend.calls == [
        "admit_source",
        "validate_pre_authorization_prerequisites",
        "authorize_shadow",
        "provision_signing_credentials",
        "await_reviewed_people_packet",
    ]
    assert "local_backup" not in backend.calls


def test_missing_ha_ssh_pauses_before_backup_or_service_change(monkeypatch) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)

    class SshPauseBackend(FakeBackend):
        def perform(self, step, state):
            self.calls.append(step)
            if step == "validate_pre_authorization_prerequisites":
                raise self.module.ActivationPause("awaiting_ha_ssh_prerequisite")

    backend = SshPauseBackend(module)
    report = module.Runner(backend, store).advance()

    assert report["status"] == "paused"
    assert report["next_step"] == "validate_pre_authorization_prerequisites"
    assert report["pause_code"] == "awaiting_ha_ssh_prerequisite"
    assert backend.calls == [
        "admit_source",
        "validate_pre_authorization_prerequisites",
    ]
    assert "local_backup" not in backend.calls


def test_source_refresh_replays_only_precredential_checks(monkeypatch) -> None:
    module = _module()
    new_commit = "c" * 40
    monkeypatch.setattr(
        module.source_plan,
        "live_report",
        lambda: {
            "current_commit": new_commit,
            "source_acceptance_receipt_issuable": True,
            "blockers": [],
        },
    )
    monkeypatch.setattr(module, "SOURCE_REFRESH_FORBIDDEN_PATHS", ())
    store = MemoryStore(module)
    state = module.new_state("b" * 40)
    state["completed_steps"] = list(module.STEPS[:3])
    state["next_step"] = "provision_signing_credentials"
    state["status"] = "paused"
    state["pause_code"] = "operator_recovery_required"
    state["last_error_code"] = "activationrunnererror"
    state["operation_ids"]["authorize_shadow"] = (
        "00000000-0000-7000-8000-000000000321"
    )
    store.save(state)
    backend = FakeBackend(module)

    result = module.Runner(backend, store).refresh_source()

    assert backend.calls == list(module.STEPS[:3])
    assert store.transition == {"from": "b" * 40, "to": new_commit}
    assert store.value["source_commit"] == new_commit
    assert store.value["completed_steps"] == list(module.STEPS[:3])
    assert store.value["next_step"] == "provision_signing_credentials"
    assert result["status"] == "active"
    assert result["pause_code"] == "none"
    assert result["last_error_code"] == "none"


def test_source_refresh_rejects_late_or_dirty_boundary(monkeypatch, tmp_path) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    state = module.new_state("b" * 40)
    state["completed_steps"] = list(module.STEPS[:4])
    state["next_step"] = "await_reviewed_people_packet"
    store.save(state)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).refresh_source()

    artifact = tmp_path / "credential.json"
    artifact.write_text("present", encoding="utf-8")
    monkeypatch.setattr(module, "SOURCE_REFRESH_FORBIDDEN_PATHS", (artifact,))
    state["completed_steps"] = list(module.STEPS[:3])
    state["next_step"] = "provision_signing_credentials"
    store.save(state)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).refresh_source()


def test_finalizer_operation_id_survives_failure_and_resume(monkeypatch) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    backend.fail_once_at = "commit_finalizer"
    runner = module.Runner(backend, store)

    with pytest.raises(module.ActivationRunnerError):
        runner.advance()
    assert store.value is not None
    first_id = store.value["operation_ids"]["commit_finalizer"]
    assert store.value["status"] == "paused"
    assert store.value["pause_code"] == "operator_recovery_required"
    assert backend.contain_calls == 0

    resumed = runner.advance()
    assert resumed["next_step"] == "await_authenticated_binding"
    assert store.value["operation_ids"]["commit_finalizer"] == first_id


def test_shadow_authorization_uses_exact_reviewed_evidence_and_restart_id(
    monkeypatch, tmp_path
) -> None:
    module = _module()
    backend = module.Backend()
    receipt_path = tmp_path / "shadow.json"
    monkeypatch.setattr(module, "SHADOW_AUTHORIZATION_RECEIPT", receipt_path)
    operation_id = "00000000-0000-7000-8000-000000000321"
    phase2 = {
        "contract": module.activation_preflight.PHASE2_CONTRACT,
        "rule_version": module.activation_preflight.PHASE2_RULE,
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": "a" * 64,
        "input_digest": "b" * 64,
        "ready_to_advance": True,
        "blockers": [],
    }
    receipt = {
        "contract": "rollout-authorization-receipt-v2",
        "authorization_id": "00000000-0000-7000-8000-000000000322",
        "operator_request_id": operation_id,
        "from_mode": "record_only",
        "to_mode": "shadow",
        "rule_version": phase2["rule_version"],
        "policy_version": phase2["policy_version"],
        "policy_digest": phase2["policy_digest"],
        "input_digest": phase2["input_digest"],
        "worker_kernel_version": "worker-maintenance-cycle-v1",
        "worker_success_sequence": 42,
        "worker_proof_digest": "c" * 64,
        "readiness_evaluated_at": "2026-08-13T12:00:00Z",
        "authorized_at": "2026-08-13T12:00:01Z",
    }
    calls = []

    def fake_json(command, **kwargs):
        calls.append((command, kwargs))
        if command[:2] == ["docker", "exec"]:
            return {"phase2": phase2, "phase3": {}}
        assert json.loads(kwargs["input_bytes"]) == {
            "operator_request_id": operation_id,
            "expected_rule_version": phase2["rule_version"],
            "expected_policy_version": phase2["policy_version"],
            "expected_policy_digest": phase2["policy_digest"],
            "expected_input_digest": phase2["input_digest"],
        }
        return receipt

    monkeypatch.setattr(backend, "_json", fake_json)
    monkeypatch.setattr(
        backend,
        "_atomic_private",
        lambda path, raw: path.write_bytes(raw + b"\n"),
    )
    monkeypatch.setattr(
        backend,
        "_private_document",
        lambda path: path.read_bytes().rstrip(b"\n"),
    )

    state = module.new_state("d" * 40)
    state["operation_ids"]["authorize_shadow"] = operation_id
    backend._authorize_shadow(state)

    assert json.loads(receipt_path.read_bytes()) == receipt
    assert len(calls) == 2
    assert "rollout-authorize" in calls[1][0]

    calls.clear()
    backend._authorize_shadow(state)
    assert len(calls) == 1


def test_failure_after_ha_stop_is_contained(monkeypatch) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    backend.fail_once_at = "freeze_legacy_writer"
    runner = module.Runner(backend, store)

    with pytest.raises(module.ActivationRunnerError):
        runner.advance()

    assert backend.contain_calls == 1
    assert store.value is not None
    assert store.value["status"] == "contained"
    assert store.value["next_step"] == "freeze_legacy_writer"


def test_journal_rejects_reordering_and_content_bearing_codes() -> None:
    module = _module()
    state = module.new_state("b" * 40)
    state["completed_steps"] = ["local_backup"]
    state["next_step"] = "offhost_backup"
    with pytest.raises(module.ActivationRunnerError):
        module.validate_state(state)

    receipt = module.completion_receipt(module.new_state("b" * 40))
    assert set(receipt) == {
        "contract",
        "runner_id",
        "source_commit",
        "completed_step_count",
        "step_set_sha256",
        "status",
    }
    assert receipt["completed_step_count"] == len(module.STEPS)

    state = module.new_state("b" * 40)
    state["pause_code"] = "Amelia is Marcelo's parent"
    with pytest.raises(module.ActivationRunnerError):
        module.validate_state(state)


def test_runner_contract_is_fixed_restart_safe_and_action_free() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "phase3-runner.lock" in source
    assert "phase3-activation.lock" not in source
    assert '"--no-deps"' in source
    assert "_wait_agents_ready" in source
    assert "awaiting_ha_ssh_prerequisite" in source
    assert "_require_remote_stopped_database" in source
    assert "existing, normalized" in source
    assert "identity-signing-receipt-e5y.json" in source
    assert "phase3-activation-completion-receipt-e5ad-v1" in source
    for forbidden in (
        "execute_services",
        "confirmed:true",
        "script.turn_on",
        "light.turn_on",
    ):
        assert forbidden not in source
    assert '"authorize_shadow"' in source
    assert '"rollout-authorize"' in source
    assert "phase3-shadow-authorization-e5ae.json" in source
    assert '"refresh-source"' in source
    assert "phase3-activation-source-transition-e5af-v1" in source


def test_kernel_provisioners_have_distinct_isolated_compose_surfaces() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    materialize = MATERIALIZE.read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    services = (
        (
            "provision-identity-binding-kernel-role",
            "provision-identity-binding-kernel-role.sh",
            "postgres_owner_password_identity_binding_kernel_provision",
        ),
        (
            "provision-parent-relationship-kernel-role",
            "provision-parent-relationship-kernel-role.sh",
            "postgres_owner_password_parent_relationship_kernel_provision",
        ),
    )
    for index, (name, entrypoint, secret) in enumerate(services):
        header = f"  {name}:\n"
        start = compose.index(header)
        following_header = (
            f"\n  {services[index + 1][0]}:"
            if index + 1 < len(services)
            else "\n  migrate:"
        )
        following = compose.index(following_header, start + len(header))
        section = compose[start : following if following >= 0 else None]
        assert "profiles: [operator]" in section
        assert 'restart: "no"' in section
        assert f'"/deploy/{entrypoint}"' in section
        assert "read_only: true" in section
        assert "no-new-privileges:true" in section
        assert "cap_drop: [ALL]" in section
        assert "networks: [postgres-net]" in section
        assert "ports:" not in section
        assert secret in section
        assert services[1 - index][2] not in section
        assert f"install_secret {name}" in materialize
        assert f"runtime/{name}/postgres_owner_password" in preflight


def test_status_never_discloses_operation_ids_or_private_content(monkeypatch) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    report = module.Runner(backend, store).advance()

    serialized = json.dumps(report, sort_keys=True)
    assert "operation_id" not in serialized
    assert "runner_id" not in serialized
    assert "Amelia" not in serialized
    assert "Marcelo" not in serialized
