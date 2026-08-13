from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
CORE = ROOT / "stack/services/home-agent-core"
PROVISIONER = OPERATOR / "phase3_identity_credential_provisioner.py"
PROVISIONER_LAUNCHER = OPERATOR / "phase3_identity_credential_provisioner.sh"
INSTALLER = ROOT / "stack/home-agent-deploy/install-phase3-identity-signing.sh"
ENTRYPOINT = CORE / "docker-entrypoint.sh"
RUNNER = OPERATOR / "phase3_activation_runner.py"
SOURCE_PLAN = OPERATOR / "phase3_activation_source_plan.py"


def _load(name: str, path: Path, search: Path) -> ModuleType:
    sys.path.insert(0, str(search))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(search))


def _provisioner() -> ModuleType:
    return _load("phase3_credential_provisioner_test", PROVISIONER, OPERATOR)


def _core_material() -> ModuleType:
    return _load("app.phase3_signing_material", CORE / "app/phase3_signing_material.py", CORE)


def _digest(label: str) -> str:
    import hashlib

    return hashlib.sha256(label.encode()).hexdigest()


def _material(module: ModuleType) -> dict[str, object]:
    return {
        "contract": module.MATERIAL_CONTRACT,
        "schema_revision": "0006a_worker_lease_arbitration",
        "shadow_authorization_id": "00000000-0000-7000-8000-000000000999",
        "shadow_rule_version": "record-only-envelope-worker-gate-v3",
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": _digest("policy"),
        "source_projection_contract_digest": _digest("projection"),
        "core_schema_digest": _digest("schema"),
        "core_capability_digest": _digest("capability"),
        "authorization_status": "authorized",
        "authoritative": False,
        "read_only": True,
    }


def test_operator_and_core_contracts_and_schema_commitments_match() -> None:
    provisioner = _provisioner()
    core = _core_material()

    assert core.EXPECTED_CAPABILITY_CONTRACT == provisioner.migration.EXPECTED_CAPABILITY_CONTRACT
    assert core.SOURCE_PROJECTION_CONTRACT == provisioner.migration.SOURCE_PROJECTION_CONTRACT
    assert core.core_schema_digest(CORE) == provisioner.core_schema_digest(CORE)
    assert core.contract_digest(core.EXPECTED_CAPABILITY_CONTRACT) == provisioner._canonical_digest(
        provisioner.migration.EXPECTED_CAPABILITY_CONTRACT
    )


def test_bundle_compiles_ten_distinct_purpose_credentials_without_secret_receipt() -> None:
    module = _provisioner()
    keys = iter(
        Ed25519PrivateKey.from_private_bytes(bytes([value]) * 32)
        for value in range(1, 6)
    )
    bundle = module.compile_bundle(
        material=_material(module),
        source_commit="a" * 40,
        source_pack_digest=_digest("release"),
        migration_tool_bundle_digest=_digest("tools"),
        core_oci_manifest_digest=_digest("image"),
        expected_policy_version="home-agent-mvp-v1",
        expected_policy_digest=_digest("policy"),
        expected_source_projection_digest=_digest("projection"),
        expected_core_schema_digest=_digest("schema"),
        expected_core_capability_digest=_digest("capability"),
        operation_id=uuid.UUID("00000000-0000-7000-8000-000000000111"),
        key_factory=lambda: next(keys),
        random_bytes=lambda size: b"c" * size,
    )

    assert set(bundle.plaintext) == {name for name, _ in module.CREDENTIALS}
    assert len(bundle.plaintext) == 10
    assert all(len(value) == 64 for value in bundle.plaintext_sha256.values())
    assert bundle.receipt["credential_count"] == 10
    assert bundle.receipt["status"] == "provisioned"
    assert "plaintext" not in json.dumps(bundle.receipt)
    assert "private" not in json.dumps(bundle.receipt)
    fingerprints = {
        value
        for key, value in bundle.receipt.items()
        if key.endswith("key_fingerprint")
    }
    assert len(fingerprints) == 6

    policy = json.loads(bundle.plaintext["policy.json"])
    assert policy["review_key_fingerprint"] == bundle.receipt["review_key_fingerprint"]
    assert policy["finalization_key_fingerprint"] == bundle.receipt[
        "finalization_key_fingerprint"
    ]
    assert policy["shadow_authorization_id"] == _material(module)[
        "shadow_authorization_id"
    ]


def test_bundle_rejects_material_drift_and_key_reuse() -> None:
    module = _provisioner()
    material = _material(module)
    material["core_schema_digest"] = _digest("other")
    arguments = {
        "material": material,
        "source_commit": "a" * 40,
        "source_pack_digest": _digest("release"),
        "migration_tool_bundle_digest": _digest("tools"),
        "core_oci_manifest_digest": _digest("image"),
        "expected_policy_version": "home-agent-mvp-v1",
        "expected_policy_digest": _digest("policy"),
        "expected_source_projection_digest": _digest("projection"),
        "expected_core_schema_digest": _digest("schema"),
        "expected_core_capability_digest": _digest("capability"),
        "operation_id": uuid.UUID("00000000-0000-7000-8000-000000000111"),
    }
    with pytest.raises(module.CredentialProvisioningError):
        module.compile_bundle(**arguments)

    arguments["material"] = _material(module)
    reused = Ed25519PrivateKey.from_private_bytes(b"r" * 32)
    with pytest.raises(module.CredentialProvisioningError):
        module.compile_bundle(**arguments, key_factory=lambda: reused)


def test_receipt_validation_rejects_secret_fields_and_fingerprint_reuse() -> None:
    module = _provisioner()
    keys = iter(
        Ed25519PrivateKey.from_private_bytes(bytes([value]) * 32)
        for value in range(1, 6)
    )
    bundle = module.compile_bundle(
        material=_material(module),
        source_commit="a" * 40,
        source_pack_digest=_digest("release"),
        migration_tool_bundle_digest=_digest("tools"),
        core_oci_manifest_digest=_digest("image"),
        expected_policy_version="home-agent-mvp-v1",
        expected_policy_digest=_digest("policy"),
        expected_source_projection_digest=_digest("projection"),
        expected_core_schema_digest=_digest("schema"),
        expected_core_capability_digest=_digest("capability"),
        operation_id=uuid.UUID("00000000-0000-7000-8000-000000000111"),
        key_factory=lambda: next(keys),
        random_bytes=lambda size: b"c" * size,
    )
    receipt = {**bundle.receipt, "key_source": "tpm2"}
    module._validate_receipt(receipt)

    receipt["private_key"] = "do-not-store"
    with pytest.raises(module.CredentialProvisioningError):
        module._validate_receipt(receipt)
    receipt.pop("private_key")
    receipt["privacy_probe_key_fingerprint"] = receipt["review_key_fingerprint"]
    with pytest.raises(module.CredentialProvisioningError):
        module._validate_receipt(receipt)


def test_core_material_requires_current_authorization_and_remains_read_only(
    monkeypatch,
) -> None:
    core = _core_material()
    monkeypatch.setattr(core, "IMAGE_ROOT", CORE)
    authorization_id = uuid.UUID("00000000-0000-7000-8000-000000000999")
    monkeypatch.setattr(
        core,
        "evaluate_rollout_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(
            authorized=True,
            code="authorized",
            authorization_id=authorization_id,
        ),
    )
    settings = core.Settings.model_construct(
        role="api",
        rollout_mode="record_only",
        readiness_migration="0006a_worker_lease_arbitration",
        policy_version="home-agent-mvp-v1",
        policy_digest=_digest("policy"),
    )
    readiness = SimpleNamespace(
        ready_to_advance=True, blockers=[], input_digest=_digest("evidence")
    )
    result = core.evaluate_material(
        settings=settings,
        revision="0006a_worker_lease_arbitration",
        readiness=readiness,
        receipt={},
    )

    assert result["shadow_authorization_id"] == str(authorization_id)
    assert result["authoritative"] is False
    assert result["read_only"] is True
    assert set(result).isdisjoint({"name", "coordinate", "utterance", "ha_user_id"})

    settings.rollout_mode = "shadow"
    with pytest.raises(core.SigningMaterialError):
        core.evaluate_material(
            settings=settings,
            revision="0006a_worker_lease_arbitration",
            readiness=readiness,
            receipt={},
        )


def test_installed_core_image_requires_one_id_and_exact_source_revision(
    monkeypatch,
) -> None:
    module = _provisioner()
    image = "sha256:" + ("1" * 64)
    commit = "a" * 40

    def run(command, **_kwargs):
        rendered = " ".join(command)
        if "org.opencontainers.image.revision" in rendered:
            return (commit + "\n").encode("ascii")
        if "--format={{.Id}}" in command:
            return (image + "\n").encode("ascii")
        if "--format={{.Image}}" in command:
            return (image + "\n").encode("ascii")
        raise AssertionError(command)

    monkeypatch.setattr(module, "_run", run)
    assert module._image_digest(commit) == "1" * 64
    with pytest.raises(
        module.CredentialProvisioningError,
        match="source revision differs",
    ):
        module._image_digest("b" * 40)


def test_production_boundary_is_fixed_host_bound_restart_safe_and_admitted() -> None:
    module = _provisioner()
    provisioner = PROVISIONER.read_text(encoding="utf-8")
    launcher = PROVISIONER_LAUNCHER.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    source_plan = SOURCE_PLAN.read_text(encoding="utf-8")

    assert "systemd-creds" in provisioner
    assert '"-",\n            "-"' in provisioner
    assert "--with-key=" in provisioner
    assert '"has-tpm2"' in provisioner
    assert '"setup"' in provisioner
    assert "os.link(staged, published)" in provisioner
    assert 'state["phase"] = "publishing"' in provisioner
    assert "untracked signing credentials exist" in provisioner
    assert "stderr=subprocess.DEVNULL" in provisioner
    assert "HOME_AGENT_" not in provisioner.split("def main", 1)[1]
    assert "curl" not in provisioner
    assert "execute_services" not in provisioner
    assert "org.opencontainers.image.revision" in provisioner
    assert "installed Core source revision differs" in provisioner

    assert "[[ $# -eq 0 ]]" in launcher
    assert "sha256sum --check --status" in launcher
    assert "phase3_identity_credential_provisioner.py" in installer
    assert "home-agent-provision-identity-signing-credentials" in installer
    assert "phase3-signing-material" in entrypoint
    assert "SET TRANSACTION READ ONLY" in (CORE / "app/phase3_signing_material.py").read_text(
        encoding="utf-8"
    )
    material_source = (CORE / "app/phase3_signing_material.py").read_text(
        encoding="utf-8"
    )
    assert "schema.rollout_authorizations" in material_source
    assert "identity." not in material_source
    assert "knowledge." not in material_source
    assert "identity_signing_credential_provisioner_installed" in source_plan
    assert "phase3_identity_credential_provisioner.py" in source_plan
    assert "phase3-identity-signing-credentials-e5ae.json" in runner
    assert "CREDENTIAL_TARGETS" in runner
    gate = (ROOT / "tools/run-home-agent-e1-postgres-gate.py").read_text(
        encoding="utf-8"
    )
    source_plan_module = _load(
        "phase3_source_plan_credential_coverage_test", SOURCE_PLAN, OPERATOR
    )
    for name in module.TOOL_FILES:
        path = f"stack/home-agent-deploy/operator/{name}"
        assert path in source_plan_module.ACTIVATION_PATHS
        assert path in gate
    assert '"ha-config/home_agent_edge"' in gate
    assert "def _run_edge_source_contracts" in gate
    assert "test_edge.py &&" in gate
    assert "test_transport.py" in gate
    postgres_test_image = (
        ROOT / "stack/services/home-agent-core/Dockerfile.postgres-test"
    ).read_text(encoding="utf-8")
    assert "COPY ha-config/home_agent_edge/" in postgres_test_image
