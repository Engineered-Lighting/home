from __future__ import annotations

import base64
import copy
import hashlib
from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Mapping, Sequence

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

    def record_source_rebind(self, state, new_commit, **details):
        self.rebind = {
            "from": state["source_commit"],
            "to": new_commit,
            **details,
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
    assert '"rebind-source"' in source
    assert "phase3-activation-source-rebind-e5ak-v1" in source
    assert 'report.get("latest_full_backup_label")' not in source
    assert source.count("preflight_backup_label(report)") == 2
    assert "activation source rebind chain is ambiguous" in source
    assert 'value.get("runner_id") != runner_id' in source
    assert "for _ in range(REBIND_MAX_HOPS):" in source
    assert "for _ in range(REBIND_MAX_HOPS + 1):" not in source


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


def _ceremony_module() -> ModuleType:
    sys.path.insert(0, str(OPERATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_phase3_identity_signing_ceremony_for_e5ad",
            OPERATOR / "phase3_identity_signing_ceremony.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(OPERATOR))


def _credential_receipt(module: ModuleType, source_commit: str) -> dict[str, object]:
    return {
        "contract": "phase3-identity-credential-receipt-e5ae-v1",
        "operation_id": "00000000-0000-7000-8000-000000000700",
        "source_commit": source_commit,
        "release_manifest_digest": "1" * 64,
        "migration_tool_bundle_digest": "2" * 64,
        "core_oci_manifest_digest": "3" * 64,
        "core_schema_digest": "4" * 64,
        "core_capability_digest": "5" * 64,
        "source_projection_contract_digest": "6" * 64,
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": "7" * 64,
        "shadow_authorization_id": "00000000-0000-7000-8000-000000000999",
        "review_key_fingerprint": "a" * 64,
        "finalization_key_fingerprint": "b" * 64,
        "writer_freeze_key_fingerprint": "c" * 64,
        "privacy_probe_key_fingerprint": "d" * 64,
        "semantic_cutover_key_fingerprint": "e" * 64,
        "commitment_key_fingerprint": "f" * 64,
        "commitment_key_epoch": 1,
        "credential_count": 10,
        "status": "provisioned",
        "key_source": "host+tpm2",
    }


def _rebind_boundary_state(module: ModuleType) -> dict[str, object]:
    state = module.new_state("b" * 40)
    state["completed_steps"] = list(module.STEPS[:4])
    state["next_step"] = "await_reviewed_people_packet"
    state["status"] = "paused"
    state["pause_code"] = "awaiting_private_people_packet"
    state["operation_ids"]["authorize_shadow"] = (
        "00000000-0000-7000-8000-000000000321"
    )
    return state


def _rebind_fixture(module: ModuleType, monkeypatch, *, new_commit: str):
    credential = _credential_receipt(module, "b" * 40)
    credential_raw = module.canonical_bytes(credential)
    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(lambda path: credential_raw),
    )
    monkeypatch.setattr(module, "validate_shadow_receipt_on_disk", lambda state: None)
    monkeypatch.setattr(
        module, "read_rebind_receipts", lambda runner_id, digest: {}
    )
    monkeypatch.setattr(module, "REBIND_FORBIDDEN_PATHS", ())
    monkeypatch.setattr(
        module.source_plan,
        "live_report",
        lambda: {
            "current_commit": new_commit,
            "source_pack_digest": "9" * 64,
            "source_acceptance_receipt_issuable": True,
            "blockers": [],
        },
    )
    return credential, credential_raw


def test_rebind_source_replays_prechecks_and_records_chain(monkeypatch) -> None:
    module = _module()
    new_commit = "c" * 40
    credential, credential_raw = _rebind_fixture(
        module, monkeypatch, new_commit=new_commit
    )
    store = MemoryStore(module)
    store.save(_rebind_boundary_state(module))
    backend = FakeBackend(module)

    result = module.Runner(backend, store).rebind_source()

    assert backend.calls == list(module.STEPS[:2])
    assert store.rebind == {
        "from": "b" * 40,
        "to": new_commit,
        "from_source_pack_digest": credential["release_manifest_digest"],
        "to_source_pack_digest": "9" * 64,
        "credential_receipt_sha256": (
            __import__("hashlib").sha256(credential_raw).hexdigest()
        ),
        "credential_source_commit": "b" * 40,
    }
    assert store.value["source_commit"] == new_commit
    assert store.value["completed_steps"] == list(module.STEPS[:4])
    assert store.value["next_step"] == "await_reviewed_people_packet"
    assert result["status"] == "active"
    assert result["pause_code"] == "none"


def test_rebind_source_rejects_wrong_boundary_or_present_artifacts(
    monkeypatch, tmp_path
) -> None:
    module = _module()
    credential, _credential_raw = _rebind_fixture(
        module, monkeypatch, new_commit="c" * 40
    )
    backend = FakeBackend(module)

    store = MemoryStore(module)
    early = module.new_state("b" * 40)
    early["completed_steps"] = list(module.STEPS[:3])
    early["next_step"] = "provision_signing_credentials"
    store.save(early)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).rebind_source()

    store = MemoryStore(module)
    stranded = _rebind_boundary_state(module)
    stranded["pause_code"] = "operator_recovery_required"
    store.save(stranded)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).rebind_source()

    artifact = tmp_path / "identity-signing-state-e5y.json"
    artifact.write_text("present", encoding="utf-8")
    monkeypatch.setattr(module, "REBIND_FORBIDDEN_PATHS", (artifact,))
    store = MemoryStore(module)
    store.save(_rebind_boundary_state(module))
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).rebind_source()
    monkeypatch.setattr(module, "REBIND_FORBIDDEN_PATHS", ())

    broken = dict(_credential_receipt(module, "b" * 40))
    broken.pop("key_source")
    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(lambda path: module.canonical_bytes(broken)),
    )
    store = MemoryStore(module)
    store.save(_rebind_boundary_state(module))
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).rebind_source()

    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(
            lambda path: module.canonical_bytes(_credential_receipt(module, "b" * 40))
        ),
    )

    class PausingBackend(FakeBackend):
        def perform(self, step, state):
            self.calls.append(step)
            if step == "validate_pre_authorization_prerequisites":
                raise self.module.ActivationPause("awaiting_ha_ssh_prerequisite")

    store = MemoryStore(module)
    store.save(_rebind_boundary_state(module))
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(PausingBackend(module), store).rebind_source()

    def drifted(state):
        raise module.ActivationRunnerError("shadow authorization receipt is invalid")

    monkeypatch.setattr(module, "validate_shadow_receipt_on_disk", drifted)
    store = MemoryStore(module)
    store.save(_rebind_boundary_state(module))
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(FakeBackend(module), store).rebind_source()
    monkeypatch.setattr(module, "validate_shadow_receipt_on_disk", lambda state: None)

    saturated = {}
    previous = "b" * 40
    previous_digest = "1" * 64
    for index in range(module.REBIND_MAX_HOPS):
        commit = f"{index + 1:040x}"
        digest = f"{index + 32:064x}"
        saturated[previous] = _hop(
            "b" * 40, previous, commit, previous_digest, digest
        )
        previous = commit
        previous_digest = digest
    monkeypatch.setattr(
        module, "read_rebind_receipts", lambda runner_id, digest: saturated
    )
    store = MemoryStore(module)
    exhausted = _rebind_boundary_state(module)
    exhausted["source_commit"] = previous
    store.save(exhausted)
    saturated_backend = FakeBackend(module)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(saturated_backend, store).rebind_source()
    assert saturated_backend.calls == []


def test_refresh_source_still_rejects_the_await_boundary(monkeypatch) -> None:
    module = _module()
    _trusted_source(module, monkeypatch)
    store = MemoryStore(module)
    backend = FakeBackend(module)
    state = _rebind_boundary_state(module)
    store.save(state)
    with pytest.raises(module.ActivationRunnerError):
        module.Runner(backend, store).refresh_source()


def _hop(
    origin: str,
    from_commit: str,
    to_commit: str,
    from_digest: str,
    to_digest: str,
) -> dict[str, str]:
    return {
        "credential_source_commit": origin,
        "from_source_commit": from_commit,
        "to_source_commit": to_commit,
        "from_source_pack_digest": from_digest,
        "to_source_pack_digest": to_digest,
    }


def test_live_prerequisites_accept_single_and_multi_hop_rebind_chain() -> None:
    module = _module()
    credential = {"source_commit": "a" * 40, "release_manifest_digest": "1" * 64}

    fast_state = {"source_commit": "a" * 40}
    fast_report = {"current_commit": "a" * 40, "source_pack_digest": "1" * 64}
    assert module.credential_source_binding_valid(
        credential, fast_state, fast_report, {}
    )

    one_state = {"source_commit": "b" * 40}
    one_report = {"current_commit": "b" * 40, "source_pack_digest": "2" * 64}
    one_chain = {
        "a" * 40: _hop("a" * 40, "a" * 40, "b" * 40, "1" * 64, "2" * 64),
    }
    assert module.credential_source_binding_valid(
        credential, one_state, one_report, one_chain
    )

    two_state = {"source_commit": "c" * 40}
    two_report = {"current_commit": "c" * 40, "source_pack_digest": "3" * 64}
    two_chain = {
        "a" * 40: _hop("a" * 40, "a" * 40, "b" * 40, "1" * 64, "2" * 64),
        "b" * 40: _hop("a" * 40, "b" * 40, "c" * 40, "2" * 64, "3" * 64),
    }
    assert module.credential_source_binding_valid(
        credential, two_state, two_report, two_chain
    )

    full_chain = {}
    previous = "a" * 40
    previous_digest = "1" * 64
    for index in range(module.REBIND_MAX_HOPS):
        commit = f"{index + 1:040x}"
        digest = f"{index + 32:064x}"
        full_chain[previous] = _hop(
            "a" * 40, previous, commit, previous_digest, digest
        )
        previous = commit
        previous_digest = digest
    full_state = {"source_commit": previous}
    full_report = {"current_commit": previous, "source_pack_digest": previous_digest}
    assert module.credential_source_binding_valid(
        credential, full_state, full_report, full_chain
    )


def test_live_prerequisites_reject_broken_rebind_chain() -> None:
    module = _module()
    credential = {"source_commit": "a" * 40, "release_manifest_digest": "1" * 64}
    state = {"source_commit": "c" * 40}
    report = {"current_commit": "c" * 40, "source_pack_digest": "3" * 64}
    good = {
        "a" * 40: _hop("a" * 40, "a" * 40, "b" * 40, "1" * 64, "2" * 64),
        "b" * 40: _hop("a" * 40, "b" * 40, "c" * 40, "2" * 64, "3" * 64),
    }

    assert not module.credential_source_binding_valid(credential, state, report, {})

    digest_break = {
        key: dict(value) for key, value in good.items()
    }
    digest_break["b" * 40]["from_source_pack_digest"] = "f" * 64
    assert not module.credential_source_binding_valid(
        credential, state, report, digest_break
    )

    unterminated = {"a" * 40: good["a" * 40]}
    assert not module.credential_source_binding_valid(
        credential, state, report, unterminated
    )

    cycle = {
        "a" * 40: _hop("a" * 40, "a" * 40, "b" * 40, "1" * 64, "2" * 64),
        "b" * 40: _hop("a" * 40, "b" * 40, "a" * 40, "2" * 64, "1" * 64),
    }
    assert not module.credential_source_binding_valid(
        credential, state, report, cycle
    )

    foreign_origin = {
        key: dict(value) for key, value in good.items()
    }
    foreign_origin["b" * 40]["credential_source_commit"] = "9" * 40
    assert not module.credential_source_binding_valid(
        credential, state, report, foreign_origin
    )

    untrusted_report = {"current_commit": "c" * 40, "source_pack_digest": None}
    assert not module.credential_source_binding_valid(
        credential, state, untrusted_report, good
    )

    moved_checkout = {"current_commit": "d" * 40, "source_pack_digest": "3" * 64}
    assert not module.credential_source_binding_valid(
        credential, state, moved_checkout, good
    )

    tail_digest_mismatch = {"current_commit": "c" * 40, "source_pack_digest": "e" * 64}
    assert not module.credential_source_binding_valid(
        credential, state, tail_digest_mismatch, good
    )

    long_chain = {}
    previous = "a" * 40
    previous_digest = "1" * 64
    for index in range(module.REBIND_MAX_HOPS + 1):
        commit = f"{index:040x}"
        digest = f"{index + 16:064x}"
        long_chain[previous] = _hop(
            "a" * 40, previous, commit, previous_digest, digest
        )
        previous = commit
        previous_digest = digest
    deep_state = {"source_commit": previous}
    deep_report = {"current_commit": previous, "source_pack_digest": previous_digest}
    assert not module.credential_source_binding_valid(
        credential, deep_state, deep_report, long_chain
    )

    fast_digest_mismatch = {"current_commit": "a" * 40, "source_pack_digest": "2" * 64}
    assert not module.credential_source_binding_valid(
        credential,
        {"source_commit": "a" * 40},
        fast_digest_mismatch,
        {},
    )


def test_rebind_receipt_validation_rejects_foreign_or_transition_receipts() -> None:
    module = _module()
    runner_id = "00000000-0000-7000-8000-000000000123"
    digest = "5" * 64
    valid = {
        "contract": module.REBIND_CONTRACT,
        "runner_id": runner_id,
        "from_source_commit": "a" * 40,
        "to_source_commit": "b" * 40,
        "from_source_pack_digest": "1" * 64,
        "to_source_pack_digest": "2" * 64,
        "credential_receipt_sha256": digest,
        "credential_source_commit": "a" * 40,
        "completed_step_count": 4,
        "next_step": "await_reviewed_people_packet",
        "recorded_at": "2026-08-16T00:00:00.000000Z",
    }
    name = f"{'a' * 40}-{'b' * 40}.json"
    module.validate_rebind_receipt(
        valid, name=name, runner_id=runner_id, credential_receipt_sha256=digest
    )

    transition_shaped = dict(valid)
    transition_shaped["contract"] = "phase3-activation-source-transition-e5af-v1"
    with pytest.raises(module.ActivationRunnerError):
        module.validate_rebind_receipt(
            transition_shaped,
            name=name,
            runner_id=runner_id,
            credential_receipt_sha256=digest,
        )

    for key, value in (
        ("runner_id", "00000000-0000-7000-8000-000000000124"),
        ("completed_step_count", 3),
        ("next_step", "provision_signing_credentials"),
        ("credential_receipt_sha256", "6" * 64),
        ("from_source_pack_digest", "zz"),
        ("to_source_commit", "a" * 40),
    ):
        broken = dict(valid)
        broken[key] = value
        with pytest.raises(module.ActivationRunnerError):
            module.validate_rebind_receipt(
                broken,
                name=name,
                runner_id=runner_id,
                credential_receipt_sha256=digest,
            )

    with pytest.raises(module.ActivationRunnerError):
        module.validate_rebind_receipt(
            valid,
            name=f"{'a' * 40}-{'c' * 40}.json",
            runner_id=runner_id,
            credential_receipt_sha256=digest,
        )


def test_ceremony_supersession_literals_match_runner_contract() -> None:
    module = _module()
    ceremony = _ceremony_module()

    assert ceremony.RUNNER_JOURNAL_CONTRACT == module.CONTRACT
    assert list(ceremony.RUNNER_COMPLETED_PREFIX) == list(module.STEPS[:4])
    assert ceremony.RUNNER_AWAIT_STEP == module.STEPS[4]
    assert ceremony.RUNNER_JOURNAL_PATH == module.STATE_PATH
    assert ceremony.RUNNER_COMPLETION_PATH == module.COMPLETION_RECEIPT
    assert ceremony.RUNNER_LOCK_PATH == module.LOCK_PATH
    assert ceremony.CREDENTIAL_RECEIPT_PATH == module.CREDENTIAL_RECEIPT
    assert ceremony.ENVIRONMENT_PATH == module.ENVIRONMENT_PATH
    assert ceremony.EXPECTED_DB_REVISION == module.source_plan.SOURCE_REVISION
    assert ceremony.EXPECTED_ROLLOUT_MODE == "record_only"
    assert ceremony.CREDENTIAL_RECEIPT_COUNT == len(module.CREDENTIAL_TARGETS)
    assert ceremony.RUNNER_PAUSE_CODES == frozenset(
        {"awaiting_private_people_review", "awaiting_private_people_packet", "none"}
    )
    assert set(ceremony.SUPERSESSION_ABSENT_PATHS) == {
        module.FINALIZER_DOCUMENT,
        module.FINALIZER_RECEIPT,
        module.EDGE_RECEIPT,
        module.WRITER_OBSERVATION,
        module.PRIVACY_OBSERVATION,
        module.CUTOVER_PACKET,
        module.CUTOVER_RECEIPT,
        module.COMPLETION_RECEIPT,
    }
    assert ceremony.STATE_PATH == module.IDENTITY_SIGNING_STATE
    assert set(module.REBIND_FORBIDDEN_PATHS) == {
        module.IDENTITY_SIGNING_STATE,
        *ceremony.SUPERSESSION_ABSENT_PATHS,
    }


def _preflight_report(label):
    return {
        "authoritative": False,
        "backup": {
            "active_topology": "local",
            "current_erasure_gate_receipt": "valid",
            "current_restore_receipt": "valid",
            "latest_full_backup_label": label,
            "off_host_receipt": "valid",
            "repository_healthy": True,
        },
        "blockers": [],
        "contract": "phase3-activation-preflight-e5j-v1",
        "preflight_passed": True,
    }


def test_preflight_backup_label_reads_the_nested_backup_mapping() -> None:
    module = _module()
    label = "20260825-024318F"

    assert module.preflight_backup_label(_preflight_report(label)) == label
    # The preflight never publishes the label at the report root; reading it
    # there silently yields None for every well-formed report.
    assert module.preflight_backup_label({"latest_full_backup_label": label}) is None
    assert module.preflight_backup_label({}) is None
    assert module.preflight_backup_label({"backup": None}) is None
    assert module.preflight_backup_label({"backup": {}}) is None
    assert module.preflight_backup_label(_preflight_report(None)) is None


def test_backup_steps_consume_the_nested_preflight_label(monkeypatch) -> None:
    module = _module()
    label = "20260825-024318F"
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append([str(item) for item in command])
        return b""

    def fake_json(cls, command, **kwargs):
        commands.append([str(item) for item in command])
        return _preflight_report(label)

    monkeypatch.setattr(module.Backend, "_run", staticmethod(fake_run))
    monkeypatch.setattr(module.Backend, "_json", classmethod(fake_json))

    backend = module.Backend()
    backend._local_backup({})
    assert backend.backup_label == label
    assert [str(module.LOCAL_BACKUP), str(module.ENVIRONMENT_PATH)] in commands

    drill = module.Backend()
    drill._restore_drill({})
    assert drill.backup_label == label
    assert [
        str(module.RESTORE_DRILL),
        str(module.ENVIRONMENT_PATH),
        label,
    ] in commands


def test_backup_steps_fail_closed_when_the_label_is_absent(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module.Backend, "_run", staticmethod(lambda command, **kwargs: b"")
    )
    monkeypatch.setattr(
        module.Backend,
        "_json",
        classmethod(lambda cls, command, **kwargs: _preflight_report(None)),
    )

    with pytest.raises(module.ActivationRunnerError, match="fresh backup label"):
        module.Backend()._local_backup({})
    with pytest.raises(module.ActivationRunnerError, match="restore backup label"):
        module.Backend()._restore_drill({})


def test_migrate_fast_path_guard_loads_the_database_secret() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    # The fast-path idempotency guard used to override the image entrypoint with
    # `python`, which skips the only component that materialises
    # HOME_AGENT_DATABASE_URL from its _FILE secret. It therefore failed for
    # every well-formed deployment and the runner always fell through to the
    # executor, which fail-closed on the same defect.
    assert "migration_executor.revision_guard_arguments(target)" in source
    assert '"app.migration_guard",' not in source
    assert '"--entrypoint",\n                    "python",' not in source


def test_runner_and_executor_share_one_revision_guard_definition() -> None:
    module = _module()

    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location(
        "home_agent_phase3_migration_executor_shared",
        OPERATOR / "phase3_migration_executor.py",
    )
    assert spec is not None and spec.loader is not None
    executor = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = executor
    spec.loader.exec_module(executor)

    # One definition, so the two call sites cannot drift apart again.
    assert module.migration_executor.revision_guard_arguments(
        "0013_identity_finalizer_e3"
    ) == executor.revision_guard_arguments("0013_identity_finalizer_e3")


def test_journal_error_codes_stay_categorical() -> None:
    module = _module()
    allowed = set("abcdefghijklmnopqrstuvwxyz_0123456789")
    coded = module.ActivationRunnerError("failed", code="exit_nonzero")
    plain = module.ActivationRunnerError("failed")
    smuggled = module.ActivationRunnerError(
        "failed", code="marcelo lives at 12 main street"
    )
    assert module._error_code(coded) == "exit_nonzero"
    assert module._error_code(plain) == "activationrunnererror"
    assert module._error_code(smuggled) == "activationrunnererror"
    for error in (coded, plain, smuggled):
        code = module._error_code(error)
        assert code and len(code) <= 96
        assert set(code) <= allowed


def test_runner_subprocess_refusals_carry_categorical_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    class Result:
        def __init__(self, returncode: int, stdout: bytes) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = b""

    backend = module.Backend()
    for expected, result in {
        "exit_nonzero": Result(1, b"{}"),
        "stdout_nul": Result(0, b"ok\0"),
    }.items():
        monkeypatch.setattr(
            module.subprocess, "run", lambda *a, _r=result, **k: _r
        )
        with pytest.raises(module.ActivationRunnerError) as caught:
            backend._run(["docker", "compose", "ps"])
        assert caught.value.code == expected


def test_runner_no_longer_discards_subprocess_diagnostics() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    # Captured only where it can be shown, so a People-bearing subprocess's
    # output is never read into this process at all.
    assert "stderr=subprocess.PIPE if diagnostic else subprocess.DEVNULL" in source
    assert "stderr=subprocess.DEVNULL," not in source
    assert "diagnostic=True" in source


def _environment(text: str, revision: str) -> dict[str, str]:
    module = _module()
    body = module.Backend._environment_body(text, revision)
    return dict(
        line.split("=", 1) for line in body.splitlines() if "=" in line
    )


def test_environment_rewrite_introduces_the_readiness_pin() -> None:
    """Core reads HOME_AGENT_READINESS_MIGRATION, so the runner must write it.

    An environment deployed before this key existed must still be rewritable:
    every rewrite at or after stop_home_assistant is contained forward-only, so
    refusing here would strand the ceremony with the Agent services stopped.
    """

    written = _environment(
        "HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration\n"
        "HOME_AGENT_ROLLOUT_MODE=record_only\n"
        "HOME_AGENT_PORT=8104\n",
        "0017_authenticated_binding_e5c",
    )
    assert written["HOME_AGENT_EXPECTED_DB_REVISION"] == "0017_authenticated_binding_e5c"
    assert written["HOME_AGENT_READINESS_MIGRATION"] == "0017_authenticated_binding_e5c"
    assert written["HOME_AGENT_ROLLOUT_MODE"] == "shadow"
    assert written["HOME_AGENT_PORT"] == "8104"


def test_environment_rewrite_replaces_an_existing_readiness_pin() -> None:
    module = _module()
    body = module.Backend._environment_body(
        "HOME_AGENT_EXPECTED_DB_REVISION=0017_authenticated_binding_e5c\n"
        "HOME_AGENT_READINESS_MIGRATION=0017_authenticated_binding_e5c\n"
        "HOME_AGENT_ROLLOUT_MODE=shadow\n",
        "0021_parent_status_e5h",
    )
    assert body.count("HOME_AGENT_READINESS_MIGRATION=") == 1
    assert "HOME_AGENT_READINESS_MIGRATION=0021_parent_status_e5h" in body


def test_environment_rewrite_still_refuses_a_missing_required_key() -> None:
    module = _module()
    with pytest.raises(module.ActivationRunnerError):
        module.Backend._environment_body(
            "HOME_AGENT_ROLLOUT_MODE=record_only\n",
            "0017_authenticated_binding_e5c",
        )


def test_environment_rewrite_refuses_a_duplicated_key() -> None:
    module = _module()
    with pytest.raises(module.ActivationRunnerError):
        module.Backend._environment_body(
            "HOME_AGENT_EXPECTED_DB_REVISION=0006a_worker_lease_arbitration\n"
            "HOME_AGENT_EXPECTED_DB_REVISION=0013_identity_finalizer_e3\n"
            "HOME_AGENT_ROLLOUT_MODE=record_only\n",
            "0017_authenticated_binding_e5c",
        )


def _signing_state(**overrides: object) -> bytes:
    module = _module()
    state = {
        "contract": "phase3-identity-signing-state-e5y-v1",
        "phase": "finalized",
        "review_signature": "a" * 128,
        "unsigned_packet": {
            "contract": "reviewed-identity-packet-compiler-e5x-v1",
            "run_id": "018f3f7a-8b4d-7abc-8def-0123456789ab",
            "unsigned_run": {
                "run_id": "018f3f7a-8b4d-7abc-8def-0123456789ab",
                "decision_count": 2,
                "source_item_count": 1,
            },
            "source_items": [{"ordinal": 1}],
            "decisions": [{"ordinal": 1}, {"ordinal": 2}],
            "projections": [{"ordinal": 1}],
            "source_records": [{"ordinal": 1}],
        },
    }
    state.update(overrides)
    return module.canonical_bytes(state)


def test_registration_manifest_is_the_signed_manifest() -> None:
    """The runner registers exactly the manifest the review signature covers.

    The sealed compiler builds {run, source_items, decisions} and inserts the
    review signature into the run. The runner rebuilds that half from the same
    private state because the compiler lives in the networkless signing bundle.
    """

    module = _module()
    run_id, manifest = module.Backend._registration_manifest(_signing_state())
    assert run_id == "018f3f7a-8b4d-7abc-8def-0123456789ab"
    value = json.loads(manifest)
    assert set(value) == {"run", "source_items", "decisions"}
    assert value["run"]["review_signature"] == "a" * 128
    assert value["run"]["run_id"] == run_id
    assert value["source_items"] == [{"ordinal": 1}]
    assert value["decisions"] == [{"ordinal": 1}, {"ordinal": 2}]
    # The projections and the raw source records never leave the operator host.
    assert "projections" not in value
    assert "source_records" not in value
    assert b"source_records" not in manifest
    # Canonical bytes, so the kernel sees a stable manifest.
    assert manifest == module.canonical_bytes(value)


def test_registration_manifest_refuses_unusable_ceremony_state() -> None:
    module = _module()
    packet = json.loads(_signing_state())["unsigned_packet"]

    def state(**overrides: object) -> bytes:
        return _signing_state(**overrides)

    broken = [
        # No review signature: nothing attests the manifest.
        state(review_signature="not-a-signature"),
        state(review_signature="a" * 127),
        # A run that already carries a signature is not the unsigned half.
        state(
            unsigned_packet={
                **packet,
                "unsigned_run": {**packet["unsigned_run"], "review_signature": "a" * 128},
            }
        ),
        # Empty source items or decisions cannot describe a migration.
        state(unsigned_packet={**packet, "source_items": []}),
        state(unsigned_packet={**packet, "decisions": []}),
        # Wrong shapes.
        state(unsigned_packet={**packet, "unsigned_run": []}),
        state(unsigned_packet={**packet, "source_items": {}}),
        module.canonical_bytes({"contract": "x"}),
        module.canonical_bytes([]),
    ]
    for raw in broken:
        with pytest.raises(module.ActivationRunnerError):
            module.Backend._registration_manifest(raw)


RETIRABLE = {
    "contract": "phase3-activation-probe-e5ac-v1",
    "probe": "migration",
    "reviewed_run_count": 0,
    "finalizer_admission_count": 0,
    "consumed_admission_count": 0,
    "finalization_count": 0,
    "expired_finalization_retirable": True,
}


def _finalized_state(module, *, expires_at: str = "2020-01-01T00:00:00.000000Z"):
    run_id = "018f3f7a-8b4d-7abc-8def-0123456789ab"
    return module.canonical_bytes(
        {
            "contract": "phase3-identity-signing-state-e5y-v1",
            "phase": "finalized",
            "ceremony_policy_sha256": "c" * 64,
            "review_signature": "a" * 128,
            "unsigned_packet": {
                "run_id": run_id,
                "review_signing_payload_sha256": "d" * 64,
                "private_review_sha256": "e" * 64,
                "unsigned_run": {"run_id": run_id, "expires_at": expires_at},
                "source_items": [{"ordinal": 1}],
                "decisions": [{"ordinal": 1}],
            },
        }
    )


def _retirement_backend(module, monkeypatch, *, state_bytes, probe):
    backend = module.Backend()
    monkeypatch.setattr(
        module.Backend, "_private_document", staticmethod(lambda path: state_bytes)
    )
    monkeypatch.setattr(module.Backend, "_probe", lambda self, name: probe)
    return backend


def test_retirement_refuses_outside_the_finalizer_boundary() -> None:
    """Retirement is only meaningful where the ceremony is actually stuck."""

    module = _module()
    store = MemoryStore(module)
    runner = module.Runner(module.Backend(), store)
    with pytest.raises(module.ActivationRunnerError):
        runner.retire_expired_finalization()

    # A fresh journal sits at the first step, not the finalizer boundary.
    store.value = module.new_state("b" * 40)
    with pytest.raises(module.ActivationRunnerError):
        runner.retire_expired_finalization()


def test_retirement_refuses_while_anything_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registration is one-shot for the life of the database.

    Retiring a packet whose run was already registered would leave a successor
    that could never be registered at all, and no role can delete the row.
    """

    module = _module()
    for probe in (
        {**RETIRABLE, "reviewed_run_count": 1, "expired_finalization_retirable": False},
        {
            **RETIRABLE,
            "finalizer_admission_count": 1,
            "expired_finalization_retirable": False,
        },
        {**RETIRABLE, "finalization_count": 1, "expired_finalization_retirable": False},
        # A probe that omits the verdict entirely must not be read as consent.
        {"contract": "phase3-activation-probe-e5ac-v1", "probe": "migration"},
    ):
        backend = _retirement_backend(
            module,
            monkeypatch,
            state_bytes=_finalized_state(module),
            probe=probe,
        )
        with pytest.raises(module.ActivationRunnerError):
            backend.retire_expired_finalization({})


def test_retirement_refuses_a_run_that_has_not_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live run must be finalized, never retired."""

    module = _module()
    future = "2999-01-01T00:00:00.000000Z"
    backend = _retirement_backend(
        module,
        monkeypatch,
        state_bytes=_finalized_state(module, expires_at=future),
        probe=RETIRABLE,
    )
    with pytest.raises(module.ActivationRunnerError):
        backend.retire_expired_finalization({})


def test_retirement_refuses_an_unfinalized_or_malformed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    good = json.loads(_finalized_state(module))
    for broken in (
        {**good, "phase": "superseded"},
        {**good, "phase": ""},
        {**good, "contract": "other"},
        {**good, "unsigned_packet": {}},
        # The packet identifier must agree with the run it describes.
        {
            **good,
            "unsigned_packet": {
                **good["unsigned_packet"],
                "run_id": "018f3f7a-8b4d-7abc-8def-ffffffffffff",
            },
        },
    ):
        backend = _retirement_backend(
            module,
            monkeypatch,
            state_bytes=module.canonical_bytes(broken),
            probe=RETIRABLE,
        )
        with pytest.raises(module.ActivationRunnerError):
            backend.retire_expired_finalization({})


def test_retirement_accepts_every_phase_a_packet_can_strand_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ten-minute window can lapse before `finalize`, not only after.

    It holds two interactive signatures and two container round-trips. A packet
    stranded at `staged` or `review_signed` had no recovery verb at all: this
    command required `finalized`, and the ceremony's own `supersede-expired`
    requires `staged` *and* a four-step journal, which is unreachable here.
    """

    module = _module()
    good = json.loads(_finalized_state(module))
    assert module.RETIRABLE_PHASES == {"staged", "review_signed", "finalized"}
    monkeypatch.setattr(module, "PRIVATE_IDENTITY_ROOT", tmp_path)
    monkeypatch.setattr(module, "IDENTITY_SIGNING_STATE", tmp_path / "absent-state.json")
    monkeypatch.setattr(module, "FINALIZER_DOCUMENT", tmp_path / "absent-document.json")
    monkeypatch.setattr(module, "FINALIZER_RECEIPT", tmp_path / "absent-receipt.json")
    for phase in sorted(module.RETIRABLE_PHASES):
        backend = _retirement_backend(
            module,
            monkeypatch,
            state_bytes=module.canonical_bytes({**good, "phase": phase}),
            probe=RETIRABLE,
        )
        # Reaches the database check rather than refusing on the phase.
        recorded: list[str] = []
        monkeypatch.setattr(
            module.Backend,
            "_probe",
            lambda self, name: recorded.append(name) or RETIRABLE,
        )
        monkeypatch.setattr(
            module.Backend, "_atomic_private", staticmethod(lambda path, raw: None)
        )
        result = backend.retire_expired_finalization({})
        assert result["status"] == "retired", phase
        assert recorded == ["migration"], phase
        # A packet that never reached `finalize` simply has fewer artifacts.
        assert result["archived_count"] == 0, phase


def test_retirement_still_refuses_a_live_run_in_every_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    good = json.loads(_finalized_state(module))
    good["unsigned_packet"]["unsigned_run"]["expires_at"] = "2999-01-01T00:00:00.000000Z"
    for phase in sorted(module.RETIRABLE_PHASES):
        backend = _retirement_backend(
            module,
            monkeypatch,
            state_bytes=module.canonical_bytes({**good, "phase": phase}),
            probe=RETIRABLE,
        )
        with pytest.raises(module.ActivationRunnerError):
            backend.retire_expired_finalization({})


def test_retirement_archives_the_three_artifacts_and_receipts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    state_bytes = _finalized_state(module)
    run_id = "018f3f7a-8b4d-7abc-8def-0123456789ab"

    signing = tmp_path / "identity-signing-state-e5y.json"
    document = tmp_path / "identity-finalizer-document-e5y.json"
    receipt = tmp_path / "identity-signing-receipt-e5y.json"
    for path in (signing, document, receipt):
        path.write_bytes(state_bytes)

    monkeypatch.setattr(module, "PRIVATE_IDENTITY_ROOT", tmp_path)
    monkeypatch.setattr(module, "IDENTITY_SIGNING_STATE", signing)
    monkeypatch.setattr(module, "FINALIZER_DOCUMENT", document)
    monkeypatch.setattr(module, "FINALIZER_RECEIPT", receipt)
    backend = _retirement_backend(
        module, monkeypatch, state_bytes=state_bytes, probe=RETIRABLE
    )
    receipts: list[tuple[Path, bytes]] = []
    monkeypatch.setattr(
        module.Backend,
        "_atomic_private",
        staticmethod(lambda path, raw: receipts.append((path, raw))),
    )

    result = backend.retire_expired_finalization({})
    assert result["status"] == "retired"
    assert result["archived_count"] == 3

    # The live names are gone, so the unchanged ceremony can stage again.
    for path in (signing, document, receipt):
        assert not path.exists()
        archived = path.with_name(f"{path.stem}.retired-{run_id}{path.suffix}")
        assert archived.read_bytes() == state_bytes

    assert len(receipts) == 1
    receipt_path, receipt_raw = receipts[0]
    assert receipt_path == (
        tmp_path / f"identity-finalization-retirement-{run_id}-e5am.json"
    )
    written = json.loads(receipt_raw)
    assert written["contract"] == "phase3-identity-finalization-retirement-e5am-v1"
    assert written["reason_code"] == "finalized_run_expired_before_registration"
    assert written["run_id"] == run_id
    assert len(written["archived_names"]) == 3
    # Content-free: identifiers and digests only.
    assert "source_items" not in written
    assert "decisions" not in written
    assert "review_signature" not in written


def test_retirement_never_overwrites_an_existing_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The private record is append-only."""

    module = _module()
    state_bytes = _finalized_state(module)
    run_id = "018f3f7a-8b4d-7abc-8def-0123456789ab"
    signing = tmp_path / "identity-signing-state-e5y.json"
    signing.write_bytes(state_bytes)
    (tmp_path / f"identity-signing-state-e5y.retired-{run_id}.json").write_bytes(
        b"{}\n"
    )

    monkeypatch.setattr(module, "PRIVATE_IDENTITY_ROOT", tmp_path)
    monkeypatch.setattr(module, "IDENTITY_SIGNING_STATE", signing)
    monkeypatch.setattr(module, "FINALIZER_DOCUMENT", tmp_path / "absent-document.json")
    monkeypatch.setattr(module, "FINALIZER_RECEIPT", tmp_path / "absent-receipt.json")
    backend = _retirement_backend(
        module, monkeypatch, state_bytes=state_bytes, probe=RETIRABLE
    )

    with pytest.raises(module.ActivationRunnerError):
        backend.retire_expired_finalization({})
    assert signing.read_bytes() == state_bytes


def test_retirement_touches_neither_the_journal_nor_the_database() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def retire_expired_finalization", 1)[1].split(
        "    def _capture_edge_receipt", 1
    )[0]
    # Read-only towards PostgreSQL: one probe, no writer, no kernel call.
    assert 'self._probe("migration")' in body
    for forbidden in (
        "AUTHORITY_ADMISSION",
        "AUTHORITY_CEREMONY",
        "_register_migration_run",
        "completed_steps",
        "next_step",
        "store.save",
    ):
        assert forbidden not in body, forbidden


def _parked(module: ModuleType, step: str, status: str = "paused") -> dict[str, object]:
    """Return a journal parked exactly at one step.

    ``validate_state`` admits only a prefix of ``STEPS``, so setting the cursor
    is enough to establish everything that ran before it.
    """

    state = module.new_state("b" * 40)
    state["completed_steps"] = list(module.STEPS[: module.STEPS.index(step)])
    state["next_step"] = step
    state["status"] = status
    return state


class RecoveryBackend:
    """Records what a permit recovery attempted without touching the host."""

    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.states: list[Mapping[str, object]] = []

    def recover_permit(self, state):
        self.states.append(copy.deepcopy(state))


@pytest.mark.parametrize(
    ("step", "revision"),
    (
        ("stop_agent_services", "0006a_worker_lease_arbitration"),
        ("provision_cutover_roles", "0013_identity_finalizer_e3"),
        ("commit_finalizer", "0015_current_authority_e5a"),
        ("grant_and_start_binding_stage", "0017_authenticated_binding_e5c"),
        ("provision_parent_kernel", "0018_parent_relationship_e5d"),
        ("seal_completion", "0021_parent_status_e5h"),
    ),
)
def test_expected_revision_tracks_the_last_completed_migration(
    step: str, revision: str
) -> None:
    """The journal alone determines where the database must be."""

    module = _module()
    completed = module.STEPS[: module.STEPS.index(step)]

    assert module.expected_revision(completed) == revision


def test_expected_revision_covers_every_migrating_step() -> None:
    """A migrating step with no recorded revision would guard the wrong one."""

    module = _module()
    source = RUNNER.read_text(encoding="utf-8")
    mapped = {step for step, _ in module.STEP_REVISIONS}
    migrating = {
        step
        for step in module.STEPS
        if f'"{step}": lambda _state: self._migrate(' in source
    }

    assert migrating == mapped


def test_permit_recovery_refuses_before_the_first_permit_window() -> None:
    """Nothing upstream of the initial arm has a permit to refresh."""

    module = _module()
    store = MemoryStore(module)
    runner = module.Runner(RecoveryBackend(module), store)

    with pytest.raises(module.ActivationRunnerError):
        runner.recover_permit()

    for step in module.STEPS[: module.STEPS.index("stop_agent_services")]:
        store.value = _parked(module, step)
        with pytest.raises(module.ActivationRunnerError):
            runner.recover_permit()


def test_permit_recovery_refuses_a_run_that_is_not_parked() -> None:
    """A contained or finished run is not waiting on a permit."""

    module = _module()
    store = MemoryStore(module)
    backend = RecoveryBackend(module)
    runner = module.Runner(backend, store)

    for status in ("contained", "complete", "failed"):
        state = _parked(module, "commit_finalizer")
        state["status"] = status
        store.value = state
        with pytest.raises(module.ActivationRunnerError):
            runner.recover_permit()
    assert backend.states == []


def test_permit_recovery_accepts_every_step_inside_a_permit_window() -> None:
    """Both windows span an unbounded human pause, so both can go stale."""

    module = _module()
    store = MemoryStore(module)
    backend = RecoveryBackend(module)
    runner = module.Runner(backend, store)

    windowed = module.STEPS[module.STEPS.index("stop_agent_services") :]
    for step in windowed:
        store.value = _parked(module, step)
        runner.recover_permit()

    assert [state["next_step"] for state in backend.states] == list(windowed)


def test_permit_recovery_never_moves_a_run_past_a_private_confirmation() -> None:
    """Refreshing a permit must not stand in for a human gate.

    The prefix invariant carries this: a run parked in the parent window has
    provably completed both confirmations, and one parked in the binding window
    still has to reach them.
    """

    module = _module()
    store = MemoryStore(module)
    backend = RecoveryBackend(module)
    runner = module.Runner(backend, store)

    store.value = _parked(module, "migrate_parent_authority")
    runner.recover_permit()
    completed = backend.states[-1]["completed_steps"]

    assert "commit_finalizer" in completed
    assert "await_authenticated_binding" in completed
    assert "await_parent_confirmation" not in completed


def _recovery_backend(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    report: Mapping[str, object] | None = None,
    contract_installed: bool = True,
    guard_fails: bool = False,
):
    """A Backend whose only reachable effects are recorded, never performed."""

    backend = module.Backend()
    written: list[tuple[object, bytes]] = []
    guarded: list[Sequence[str]] = []

    monkeypatch.setattr(
        module.source_plan,
        "live_report",
        lambda: dict(
            {"source_acceptance_receipt_issuable": True, "blockers": []}
            if report is None
            else report
        ),
    )
    monkeypatch.setattr(
        module.sequencer, "grant_contract_installed", lambda: contract_installed
    )
    monkeypatch.setattr(
        module.Backend, "_compose", lambda self, *args: ["compose", *args]
    )

    def _run(command, **kwargs):
        guarded.append(list(command))
        if guard_fails:
            raise module.ActivationRunnerError("revision guard failed")
        return b""

    monkeypatch.setattr(module.Backend, "_run", staticmethod(_run))
    monkeypatch.setattr(
        module.sequencer,
        "_atomic_write",
        lambda path, raw: written.append((path, raw)),
    )
    return backend, written, guarded


def test_permit_recovery_pins_the_revision_the_journal_implies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The permit is only ever refreshed onto an undrifted database."""

    module = _module()
    backend, written, guarded = _recovery_backend(module, monkeypatch)

    backend.recover_permit(_parked(module, "commit_finalizer"))

    assert "0015_current_authority_e5a" in guarded[-1]
    assert written == [
        (
            module.sequencer.GRANT_PERMIT_PATH,
            (module.sequencer.GRANT_PERMIT_VALUE + "\n").encode("ascii"),
        )
    ]


def test_permit_recovery_writes_nothing_when_the_database_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database that moved underneath the run invalidates the evidence."""

    module = _module()
    backend, written, _ = _recovery_backend(module, monkeypatch, guard_fails=True)

    with pytest.raises(module.ActivationRunnerError):
        backend.recover_permit(_parked(module, "commit_finalizer"))
    assert written == []


@pytest.mark.parametrize(
    "report",
    (
        {"source_acceptance_receipt_issuable": False, "blockers": []},
        {"source_acceptance_receipt_issuable": True, "blockers": ["drift"]},
        {"blockers": []},
        {"source_acceptance_receipt_issuable": True},
    ),
)
def test_permit_recovery_refuses_an_unadmitted_source(
    monkeypatch: pytest.MonkeyPatch, report: Mapping[str, object]
) -> None:
    """Recovery re-establishes the same source evidence the initial arm did."""

    module = _module()
    backend, written, guarded = _recovery_backend(module, monkeypatch, report=report)

    with pytest.raises(module.ActivationRunnerError):
        backend.recover_permit(_parked(module, "commit_finalizer"))
    assert written == []
    assert guarded == []


def test_permit_recovery_refuses_without_the_grant_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The permit means nothing if the grant service no longer mounts it."""

    module = _module()
    backend, written, _ = _recovery_backend(
        module, monkeypatch, contract_installed=False
    )

    with pytest.raises(module.ActivationRunnerError):
        backend.recover_permit(_parked(module, "commit_finalizer"))
    assert written == []


def test_permit_recovery_runs_no_migration_and_never_advances_the_journal() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def recover_permit", 1)[1].split("    def _await_parents", 1)[0]

    # The one guard call verifies a revision; it is not a migration entrypoint.
    assert "revision_guard_arguments" in body
    for forbidden in (
        "MIGRATION_EXECUTOR",
        "AUTHORITY_ADMISSION",
        "AUTHORITY_CEREMONY",
        "_apply_grants",
        "_start_agents",
        "_register_migration_run",
        "completed_steps.append",
        "store.save",
        '"next_step"',
    ):
        assert forbidden not in body, forbidden


SPENT_ADMISSION = {
    "contract": "phase3-activation-probe-e5ac-v1",
    "probe": "migration",
    "reviewed_run_count": 1,
    "finalizer_admission_count": 1,
    "consumed_admission_count": 0,
    "finalization_count": 0,
    "expired_finalization_retirable": False,
}


def _admission_backend(module, monkeypatch, *, state_bytes=None, probe=None):
    backend = module.Backend()
    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(lambda path: state_bytes or _finalized_state(module)),
    )
    monkeypatch.setattr(
        module.Backend, "_probe", lambda self, name: probe or SPENT_ADMISSION
    )
    monkeypatch.setattr(module.Backend, "_atomic_private", staticmethod(lambda p, r: None))
    monkeypatch.setattr(module.os, "rename", lambda a, b: None)
    monkeypatch.setattr(module.Path, "exists", lambda self: False)
    monkeypatch.setattr(module.Path, "is_symlink", lambda self: False)
    return backend


def test_admission_recovery_refuses_outside_the_finalizer_boundary() -> None:
    """The spent admission only matters where the ceremony actually strands."""

    module = _module()
    store = MemoryStore(module)
    runner = module.Runner(module.Backend(), store)

    with pytest.raises(module.ActivationRunnerError):
        runner.recover_finalizer_admission()

    store.value = module.new_state("b" * 40)
    with pytest.raises(module.ActivationRunnerError):
        runner.recover_finalizer_admission()


@pytest.mark.parametrize(
    "probe",
    (
        {**SPENT_ADMISSION, "finalization_count": 1},
        {**SPENT_ADMISSION, "consumed_admission_count": 1},
        {**SPENT_ADMISSION, "finalizer_admission_count": 0},
    ),
)
def test_admission_recovery_refuses_once_the_database_acted_on_it(
    monkeypatch: pytest.MonkeyPatch, probe
) -> None:
    """A finalized or consumed admission has projections behind it.

    Re-minting past that would let a second run become a second semantic
    authority, which no recovery may create.
    """

    module = _module()
    backend = _admission_backend(module, monkeypatch, probe=probe)

    with pytest.raises(module.ActivationRunnerError):
        backend.recover_finalizer_admission(_parked(module, "commit_finalizer"))


def test_admission_recovery_refuses_a_live_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run still inside its window must be finalized, not recovered around."""

    module = _module()
    future = (datetime.now(UTC) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    ) + "Z"
    backend = _admission_backend(
        module, monkeypatch, state_bytes=_finalized_state(module, expires_at=future)
    )

    with pytest.raises(module.ActivationRunnerError):
        backend.recover_finalizer_admission(_parked(module, "commit_finalizer"))


def test_admission_recovery_rewinds_nothing_and_touches_one_field() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def recover_finalizer_admission", 2)[2].split(
        "    def contain", 1
    )[0]

    assert 'state["operation_ids"]["commit_finalizer"] = outcome["replacement"]' in body
    # Reading the cursor to gate on it is fine; writing it is not.
    for forbidden in (
        'state["next_step"] =',
        'state["completed_steps"]',
        '"completed_steps"]',
        "_register_migration_run",
    ):
        assert forbidden not in body, forbidden


def test_admission_recovery_writes_nothing_to_the_database() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def recover_finalizer_admission", 1)[1].split(
        "    def retire_expired_finalization", 1
    )[0]

    # One probe, read-only. No writer, no kernel call, no migration.
    assert 'self._probe("migration")' in body
    for forbidden in (
        "AUTHORITY_ADMISSION",
        "AUTHORITY_CEREMONY",
        "MIGRATION_EXECUTOR",
        "_apply_grants",
        "store.save",
    ):
        assert forbidden not in body, forbidden


def _stop_ha_harness(module, monkeypatch, *, stop_raises=False, quiet_after):
    """Drive _stop_ha with a probe that goes quiet after N attempts."""

    calls = {"stop": 0, "probe": 0, "slept": 0.0}

    def _remote(*args, **kwargs):
        calls["stop"] += 1
        if stop_raises:
            raise RuntimeError("ssh response lost")
        return b""

    def _probe():
        calls["probe"] += 1
        if calls["probe"] <= quiet_after:
            raise RuntimeError("legacy_identity_wal_still_present")
        return (1, "a" * 64)

    monkeypatch.setattr(module.ha_transport, "_remote", _remote)
    monkeypatch.setattr(module.ha_transport, "_require_remote_stopped_database", _probe)
    monkeypatch.setattr(module.time, "sleep", lambda s: calls.__setitem__("slept", calls["slept"] + s))
    return calls


def test_stop_ha_waits_for_the_database_to_go_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ha core stop` returning does not mean the writer has finished.

    The step 20 fence refuses a database that still carries a `-wal` sidecar and
    reports it as an opaque transport failure, so the wait has to happen here.
    """

    module = _module()
    calls = _stop_ha_harness(module, monkeypatch, quiet_after=3)

    module.Backend._stop_ha()

    assert calls["stop"] == 1
    assert calls["probe"] == 4          # three refusals, then quiet
    assert calls["slept"] > 0           # it actually waited


def test_stop_ha_still_accepts_a_lost_ssh_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost response is ambiguous; the database is what settles it."""

    module = _module()
    calls = _stop_ha_harness(module, monkeypatch, stop_raises=True, quiet_after=0)

    module.Backend._stop_ha()

    assert calls["probe"] == 1


def test_stop_ha_fails_closed_when_the_database_never_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer that never stops must not be treated as stopped."""

    module = _module()
    ticks = iter([0.0] + [float(i) for i in range(1, 400)])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    _stop_ha_harness(module, monkeypatch, quiet_after=10_000)

    with pytest.raises(module.ActivationRunnerError):
        module.Backend._stop_ha()


def test_stop_ha_bounds_its_wait() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def _stop_ha", 1)[1].split("    def _freeze_legacy_writer", 1)[0]

    assert "HA_QUIESCE_TIMEOUT_SECONDS" in body
    assert "time.monotonic()" in body
    assert "while True" in body
    # The probe must run on the success path, not only after an SSH failure.
    assert body.index("_require_remote_stopped_database") > body.index('"ha", "core", "stop"')


def test_every_ha_host_import_is_verified_and_deployed() -> None:
    """The two step-20 scripts import modules that must travel with them.

    Both do a package-relative import and then a bare one; neither resolves
    unless the file sits beside them on the Home Assistant host. A missing
    dependency passed the readiness check and then failed at the writer fence,
    with Home Assistant already stopped and the remote ImportError discarded.
    """

    module = _module()
    eoc = ROOT / "ha-config/extended_openai_conversation"
    imported: set[str] = set()
    for script in (
        "freeze_legacy_identity_semantics.py",
        "collect_legacy_identity_freeze_observation.py",
    ):
        for line in (eoc / script).read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            for prefix in ("from .", "from "):
                if stripped.startswith(prefix):
                    name = stripped[len(prefix):].split(" ", 1)[0].lstrip(".")
                    if (eoc / f"{name}.py").exists():
                        imported.add(name)

    assert imported, "expected the step 20 scripts to import local modules"

    verified = {Path(remote).name for _, remote in module.REMOTE_HA_MODULES}
    for name in imported:
        assert f"{name}.py" in verified, f"{name} is imported but never verified"


def test_the_installer_deploys_everything_the_runner_verifies() -> None:
    """Two lists, one truth. A file verified but never copied strands a run."""

    module = _module()
    installer = (
        ROOT / "stack/home-agent-deploy/install-ha-operator-module.sh"
    ).read_text(encoding="utf-8")

    for _, remote in module.REMOTE_HA_MODULES:
        name = Path(remote).name
        assert name in installer, f"{name} is verified but never installed"


def _writer_evidence_harness(module, monkeypatch, tmp_path, *, permit_fresh=True):
    """Drive step 21 with every side effect recorded instead of performed."""

    calls: list[str] = []
    monkeypatch.setattr(
        module, "WRITER_OBSERVATION", tmp_path / "writer-freeze-observation-e5z.json"
    )
    monkeypatch.setattr(
        module, "WRITER_FREEZE_EVIDENCE", tmp_path / "writer-freeze-evidence-e5z.json"
    )
    monkeypatch.setattr(
        module,
        "WRITER_FREEZE_RECEIPT",
        tmp_path / "writer-freeze-evidence-receipt-e5z.json",
    )

    def _permit(now):
        calls.append("permit")
        assert isinstance(now, datetime), "the permit check requires a datetime"
        if not permit_fresh:
            raise module.migration_executor.MigrationExecutionError(
                "phase3 migration permit is stale"
            )

    monkeypatch.setattr(
        module.migration_executor, "_require_fresh_permit", _permit
    )
    monkeypatch.setattr(
        module.Backend, "_stop_ha", staticmethod(lambda: calls.append("stop"))
    )
    monkeypatch.setattr(
        module.Backend,
        "_freeze_legacy_writer",
        lambda self, state: calls.append("freeze"),
    )
    monkeypatch.setattr(
        module.Backend,
        "_json",
        lambda self, command, **kwargs: calls.append(f"json:{command[-1]}") or b"{}",
    )
    monkeypatch.setattr(
        module.Backend,
        "_record_evidence",
        lambda self, command, path: calls.append(f"record:{command}"),
    )
    # The real reader enforces root ownership, which a tmp_path file cannot
    # satisfy; the freshness logic is what these tests are about.
    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(lambda path: path.read_bytes().rstrip(b"\n")),
    )
    return calls


def test_step_21_refreshes_the_observation_it_is_about_to_sign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every downstream window is measured from `observed_at`.

    The privacy observer refuses a freeze older than five minutes and the
    cutover kernel refuses evidence older than the finalization, but
    `_freeze_legacy_writer` reuses an observation that already exists. A run
    resumed hours later could never satisfy them.
    """

    module = _module()
    calls = _writer_evidence_harness(module, monkeypatch, tmp_path)
    module.WRITER_OBSERVATION.write_text("{}", encoding="utf-8")

    module.Backend()._sign_writer_evidence({})

    assert calls == [
        "permit",
        "stop",
        "freeze",
        "json:freeze-evidence",
        "record:record-freeze-evidence",
    ]
    assert not module.WRITER_OBSERVATION.exists()
    archived = list(tmp_path.glob("writer-freeze-observation-e5z.stale-*.json"))
    assert len(archived) == 1, "the stale measurement must be kept, not dropped"
    assert archived[0].read_text(encoding="utf-8") == "{}"


def test_step_21_observes_when_no_measurement_is_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _module()
    calls = _writer_evidence_harness(module, monkeypatch, tmp_path)

    module.Backend()._sign_writer_evidence({})

    assert calls[:3] == ["permit", "stop", "freeze"]
    assert not list(tmp_path.glob("*.stale-*.json"))


def _canonical(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _measure(module, age_seconds: float) -> None:
    """Write an observation stamped `age_seconds` in the past."""

    observed = datetime.now(UTC) - timedelta(seconds=age_seconds)
    module.WRITER_OBSERVATION.write_text(
        _canonical({"observed_at": observed.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"}),
        encoding="utf-8",
    )


def _sign(module, age_seconds: float) -> None:
    """Write signed evidence whose freeze time is `age_seconds` in the past."""

    verified = datetime.now(UTC) - timedelta(seconds=age_seconds)
    stamp = verified.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    module.WRITER_FREEZE_EVIDENCE.write_text(
        _canonical(
            {"enforced_writer_freeze": {"enforced_at": stamp, "verified_at": stamp}}
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("existing", ["evidence", "receipt"])
def test_step_21_pauses_rather_than_refresh_under_aged_signed_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, existing: str
) -> None:
    """Signed evidence and a new measurement must never be combined.

    The ceremony writes evidence before the row is recorded and resumes from
    that file, so refreshing the observation underneath would re-emit evidence
    bound to the old time. Re-emitting it once the measurement has aged out
    spends the one-shot freeze rows just as surely, so the run parks instead.
    """

    module = _module()
    calls = _writer_evidence_harness(module, monkeypatch, tmp_path)
    _measure(module, 4000)
    _sign(module, 4000)
    if existing == "receipt":
        # Only the receipt survived; the freeze time cannot be read at all.
        module.WRITER_FREEZE_EVIDENCE.unlink()
        module.WRITER_FREEZE_RECEIPT.write_text("{}", encoding="utf-8")

    with pytest.raises(module.ActivationPause) as caught:
        module.Backend()._sign_writer_evidence({})

    assert str(caught.value) == "awaiting_writer_evidence_review"
    assert calls == ["permit"]
    assert module.WRITER_OBSERVATION.exists(), "the measurement must be left in place"
    assert not list(tmp_path.glob("*.stale-*.json"))


def test_step_21_resumes_signed_evidence_that_is_still_inside_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lost journal write must not strand a run whose row may already exist.

    Before the measurement ages out the ceremony's resume path is correct and
    the evidence writer replays an identical document harmlessly, so the step
    re-emits rather than parks -- and must not disturb the measurement the
    evidence is bound to.
    """

    module = _module()
    calls = _writer_evidence_harness(module, monkeypatch, tmp_path)
    _measure(module, 5)
    _sign(module, 5)

    module.Backend()._sign_writer_evidence({})

    assert calls == [
        "permit",
        "json:freeze-evidence",
        "record:record-freeze-evidence",
    ]
    assert "observed_at" in module.WRITER_OBSERVATION.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.stale-*.json"))


def test_step_21_reads_the_freeze_time_out_of_the_evidence_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh observation beside stale evidence must not read as resumable.

    The ceremony resumes from the evidence and never re-reads the observation,
    so the only freeze time that matters is the one inside the signed document.
    """

    module = _module()
    _writer_evidence_harness(module, monkeypatch, tmp_path)
    _measure(module, 1)
    _sign(module, 4000)

    with pytest.raises(module.ActivationPause) as caught:
        module.Backend()._sign_writer_evidence({})
    assert str(caught.value) == "awaiting_writer_evidence_review"


@pytest.mark.parametrize(
    "body",
    ["{}", '{"enforced_writer_freeze":{}}', '{"enforced_writer_freeze":"x"}'],
)
def test_step_21_will_not_resume_evidence_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    """Fail closed: an unreadable freeze time is not a fresh one."""

    module = _module()
    _writer_evidence_harness(module, monkeypatch, tmp_path)
    _measure(module, 1)
    module.WRITER_FREEZE_EVIDENCE.write_text(body, encoding="utf-8")

    with pytest.raises(module.ActivationPause):
        module.Backend()._sign_writer_evidence({})


def test_step_21_pauses_on_a_stale_permit_before_stopping_home_assistant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pause is recoverable at this step; a containment is not cheap.

    The permit is checked again inside the admission, but by then Home
    Assistant is stopped and a fresh measurement has been spent.
    """

    module = _module()
    calls = _writer_evidence_harness(module, monkeypatch, tmp_path, permit_fresh=False)
    module.WRITER_OBSERVATION.write_text("{}", encoding="utf-8")

    with pytest.raises(module.ActivationPause) as caught:
        module.Backend()._sign_writer_evidence({})

    assert str(caught.value) == "awaiting_permit_recovery"
    assert calls == ["permit"]
    assert module.WRITER_OBSERVATION.exists()
    assert not list(tmp_path.glob("*.stale-*.json"))
    assert "sign_writer_evidence" in module.RECOVERABLE_PERMIT_STEPS


def test_step_23_refreshes_the_erasure_receipt_before_compiling_the_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packet refuses an erasure receipt older than five minutes.

    Nothing between step 12 and the cutover refreshes one, so a run that took
    longer than five minutes to get here fails after the privacy rows are
    already committed.
    """

    module = _module()
    calls: list[str] = []

    class _Stop(RuntimeError):
        pass

    monkeypatch.setattr(
        module.Backend,
        "_probe",
        lambda self, name: calls.append(f"probe:{name}")
        or {"semantic_authority_current": False},
    )
    monkeypatch.setattr(
        module.Backend,
        "_erasure_current",
        lambda self, state: calls.append("erasure-current"),
    )

    def _json(self, command, **kwargs):
        calls.append(f"json:{command[-1]}")
        raise _Stop("stop after the launcher")

    monkeypatch.setattr(module.Backend, "_json", _json)

    with pytest.raises(_Stop):
        module.Backend()._commit_semantic_cutover({})

    assert calls == ["probe:authority", "erasure-current", "json:cutover-packet"]


def test_step_23_skips_the_refresh_once_authority_is_current() -> None:
    """The early return is the resume path; it must stay side-effect free."""

    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def _commit_semantic_cutover", 1)[1]
    early = body.split("return", 1)[0]

    assert "_erasure_current" not in early
    assert body.index("_erasure_current") < body.index('"cutover-packet"')


def test_step_21_archives_the_stale_observation_and_never_deletes_it() -> None:
    """A measurement that aged out is still the evidence of what was frozen."""

    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("    def _refresh_writer_observation", 1)[1].split(
        "    def _sign_writer_evidence", 1
    )[0]

    assert "os.rename" in body
    assert "unlink" not in body
    # stop -> fence -> observe: the observer refuses a `-shm` that only the
    # fence run removes. Anchor on the calls, not the prose above them.
    assert body.index("self._stop_ha()") < body.index("self._freeze_legacy_writer(")


def test_the_runner_and_the_ceremony_name_the_same_writer_freeze_artifacts() -> None:
    """The step-21 guard is only real if it watches the files the ceremony writes."""

    module = _module()
    sys.path.insert(0, str(OPERATOR))
    try:
        spec = importlib.util.spec_from_file_location(
            "home_agent_writer_freeze_ceremony_e5ad",
            OPERATOR / "phase3_writer_freeze_ceremony.py",
        )
        assert spec is not None and spec.loader is not None
        ceremony = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ceremony)
    finally:
        sys.path.remove(str(OPERATOR))

    assert module.WRITER_FREEZE_EVIDENCE == ceremony.EVIDENCE_PATH
    assert module.WRITER_FREEZE_RECEIPT == ceremony.RECEIPT_PATH
    assert module.WRITER_OBSERVATION == ceremony.OBSERVATION_PATH


def test_jsonb_text_bytes_matches_postgres_object_rendering() -> None:
    """The E4 kernel compares against PostgreSQL's own rendering, not RFC JSON.

    `jsonb` orders object keys by length and then by bytes, and separates with
    ", " and ": ". Both differ from `canonical_bytes`, which is why the wire
    form has its own renderer.
    """

    module = _module()
    rendered = module.jsonb_text_bytes(
        {"admission_id": "b", "run_id": "a", "zz": "c", "az": "d"}
    )

    # length first, then bytewise: az, zz (2) < run_id (6) < admission_id (12)
    assert rendered == (
        b'{"az": "d", "zz": "c", "run_id": "a", "admission_id": "b"}'
    )
    assert b'", "' in rendered and b'": "' in rendered


def test_jsonb_text_bytes_differs_from_canonical_bytes() -> None:
    """Documents why both encodings exist -- they can never coincide."""

    module = _module()
    value = {"run_id": "a", "admission_id": "b"}

    assert module.jsonb_text_bytes(value) != module.canonical_bytes(value)
    # canonical_bytes sorts lexicographically and omits whitespace
    assert module.canonical_bytes(value) == b'{"admission_id":"b","run_id":"a"}'


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1},                      # non-string value
        {"a": {"b": "c"}},             # nested object
        {"a": None},
        ["not", "a", "mapping"],
    ],
)
def test_jsonb_text_bytes_refuses_anything_but_a_string_only_object(value) -> None:
    """The ordering is only well-defined for the shape the kernel requires."""

    module = _module()
    with pytest.raises(module.ActivationRunnerError):
        module.jsonb_text_bytes(value)


def test_step_23_submits_the_kernel_wire_form_to_both_hops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admission and the execution must carry identical bytes.

    The kernel compares `sha256(submitted)` against the digest the admission
    recorded, so the two hops agree only if both are handed the same
    rendering -- and it must be the one PostgreSQL produces.
    """

    module = _module()
    document = {"run_id": "a" * 4, "admission_id": "01a0415b-9356-7170-80a1-6c305a3c62e5"}
    packet = {"cutover_document": document}
    packet_raw = module.canonical_bytes(packet)
    canonical_document = module.canonical_bytes(document)
    receipt = {
        "admission_id": document["admission_id"],
        "cutover_packet_sha256": hashlib.sha256(packet_raw).hexdigest(),
        "cutover_document_sha256": hashlib.sha256(canonical_document).hexdigest(),
    }
    submitted: list[bytes] = []

    monkeypatch.setattr(
        module.Backend, "_probe", lambda self, name: {"semantic_authority_current": False}
    )
    monkeypatch.setattr(module.Backend, "_erasure_current", lambda self, state: None)
    monkeypatch.setattr(module.Backend, "_record_evidence", lambda self, c, p: None)
    monkeypatch.setattr(
        module.Backend,
        "_private_document",
        staticmethod(
            lambda path: packet_raw
            if path == module.CUTOVER_PACKET
            else module.canonical_bytes(receipt)
        ),
    )

    class _Stop(RuntimeError):
        pass

    def _json(self, command, input_bytes=None, **kwargs):
        if input_bytes is not None:
            submitted.append(input_bytes)
            if len(submitted) == 2:
                raise _Stop("both hops captured")
        return b"{}"

    monkeypatch.setattr(module.Backend, "_json", _json)

    with pytest.raises(_Stop):
        module.Backend()._commit_semantic_cutover({})

    assert len(submitted) == 2, "admission and execution must both be submitted"
    payloads = [
        module.parse_canonical_json(item, maximum=module.MAX_OUTPUT)
        for item in submitted
    ]
    documents = {item["document_b64"] for item in payloads}
    assert len(documents) == 1, "the two hops must carry identical document bytes"

    wire = base64.b64decode(documents.pop())
    assert wire == module.jsonb_text_bytes(document)
    assert wire != canonical_document, "the wire form must not be RFC-canonical"
