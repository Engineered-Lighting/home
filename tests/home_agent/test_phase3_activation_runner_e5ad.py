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
    assert "stderr=subprocess.DEVNULL" not in source
    assert "stderr=subprocess.PIPE" in source
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
