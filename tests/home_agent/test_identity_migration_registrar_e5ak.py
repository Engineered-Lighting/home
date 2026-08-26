"""The reviewed manifest reaches the 0008 kernel, and nothing else does."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
REGISTRAR = (
    ROOT / "stack/services/home-agent-core/app/identity_migration_registrar.py"
)
WRITER = ROOT / "stack/services/home-agent-core/app/identity_admission_writer.py"
CEREMONY = (
    ROOT / "stack/home-agent-deploy/operator/phase3_identity_authority_ceremony.py"
)
COMPILER = (
    ROOT / "stack/home-agent-deploy/operator/reviewed_identity_packet_compiler.py"
)
RUNNER = ROOT / "stack/home-agent-deploy/operator/phase3_activation_runner.py"
ENTRYPOINT = ROOT / "stack/services/home-agent-core/docker-entrypoint.sh"
COMPOSE = ROOT / "stack/home-agent-compose.yml"
KERNEL = (
    ROOT
    / "stack/services/home-agent-core/alembic/versions/0008_identity_migration_kernel.py"
)

RUN_ID = "018f3f7a-8b4d-7abc-8def-0123456789ab"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_identity_migration_registrar_e5ak",
        REGISTRAR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(run_id: str = RUN_ID) -> dict[str, object]:
    return {
        "run": {"run_id": run_id, "review_signature": "a" * 128},
        "source_items": [{"ordinal": 1}],
        "decisions": [{"ordinal": 1}],
    }


def _request(manifest: dict[str, object], run_id: str = RUN_ID) -> bytes:
    raw = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return json.dumps(
        {
            "contract": "identity-migration-registration-e5ak-v1",
            "run_id": run_id,
            "manifest_b64": base64.b64encode(raw).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def test_registrar_accepts_only_the_reviewed_manifest_shape() -> None:
    module = _module()
    parsed = module.parse_request(_request(_manifest()))
    assert str(parsed.run_id) == RUN_ID
    assert json.loads(parsed.manifest) == _manifest()


@pytest.mark.parametrize(
    "manifest",
    [
        # The kernel accepts exactly these three keys.
        {"run": {"run_id": RUN_ID}, "source_items": [{}]},
        {
            "run": {"run_id": RUN_ID},
            "source_items": [{}],
            "decisions": [{}],
            "projections": [{}],
        },
        # Arrays and object types are fixed.
        {"run": [], "source_items": [{}], "decisions": [{}]},
        {"run": {"run_id": RUN_ID}, "source_items": {}, "decisions": [{}]},
        # The manifest must describe the run the operator asked to register.
        {
            "run": {"run_id": "018f3f7a-8b4d-7abc-8def-ffffffffffff"},
            "source_items": [{}],
            "decisions": [{}],
        },
    ],
)
def test_registrar_refuses_a_manifest_the_kernel_would_reject(manifest) -> None:
    module = _module()
    with pytest.raises(module.MigrationRegistrarError):
        module.parse_request(_request(manifest))


def test_registrar_refuses_a_malformed_envelope() -> None:
    module = _module()
    for raw in (
        b"",
        b"{}",
        b"not json",
        b'{"contract":"wrong","run_id":"' + RUN_ID.encode() + b'","manifest_b64":"e30="}',
        # A version-4 identifier is not a reviewed run identifier.
        _request(_manifest(), run_id="8f14e45f-ceea-4e1a-9b1e-6c3a8f2b0d11"),
    ):
        with pytest.raises(module.MigrationRegistrarError):
            module.parse_request(raw)


def test_registrar_connects_only_as_the_migration_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kernel refuses every caller but the migration login.

    The admission writer pins home_agent_owner, so the registrar deliberately
    keeps its own copy of this check rather than sharing one.
    """

    module = _module()
    password = "b" * 64
    good = f"postgresql+psycopg://home_agent_identity_migration:{password}@postgres:5432/home_agent"
    monkeypatch.setenv("HOME_AGENT_DATABASE_URL", good)
    assert module.database_url() == good

    for bad in (
        "",
        f"postgresql+psycopg://home_agent_owner:{password}@postgres:5432/home_agent",
        f"postgresql+psycopg://home_agent_identity_finalizer:{password}@postgres:5432/home_agent",
        f"postgresql://home_agent_identity_migration:{password}@postgres:5432/home_agent",
        f"postgresql+psycopg://home_agent_identity_migration:{password}@localhost:5432/home_agent",
        f"postgresql+psycopg://home_agent_identity_migration:{password}@postgres:5432/other",
        f"postgresql+psycopg://home_agent_identity_migration:{password}@postgres:5432/home_agent?sslmode=disable",
    ):
        monkeypatch.setenv("HOME_AGENT_DATABASE_URL", bad)
        with pytest.raises(module.MigrationRegistrarError):
            module.database_url()


def test_registrar_calls_the_frozen_kernel_once_under_serializable() -> None:
    source = REGISTRAR.read_text(encoding="utf-8")
    assert "operations.register_reviewed_identity_migration(" in source
    assert source.count("SELECT operations.register_reviewed_identity_migration") == 1
    assert 'isolation_level="SERIALIZABLE"' in source
    assert "hide_parameters=True" in source
    assert "log_parameter_max_length_on_error=0" in source
    assert "poolclass=NullPool" in source
    # The kernel is SECURITY DEFINER and its body is digest-frozen; the caller
    # must never try to steer it with anything but the manifest.
    assert "CAST(:manifest AS jsonb)" in source
    # Retry the whole transaction or nothing.
    assert 'RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03"})' in source
    assert "print(error" not in source
    assert "str(error)" not in source


def test_registrar_retries_the_same_states_as_the_admission_writer() -> None:
    registrar = _module()
    spec = importlib.util.spec_from_file_location(
        "home_agent_identity_admission_writer_for_e5ak", WRITER
    )
    assert spec is not None and spec.loader is not None
    writer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = writer
    spec.loader.exec_module(writer)
    assert registrar.RETRYABLE_SQLSTATES == writer.RETRYABLE_SQLSTATES
    assert registrar.MAX_ATTEMPTS == writer.MAX_ATTEMPTS


def test_registration_is_wired_from_the_image_to_the_ceremony() -> None:
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    ceremony = CEREMONY.read_text(encoding="utf-8")

    assert "identity-register-run)" in entrypoint
    assert "exec python -m app.identity_migration_registrar" in entrypoint

    assert "identity-registrar:" in compose
    assert "command: [identity-register-run]" in compose
    # The registrar uses the migration login's own URL, which until now was
    # declared and consumed by nothing.
    assert compose.count("database_url_identity_migration_identity_migration") == 2
    registrar_block = compose.split("identity-registrar:", 1)[1].split(
        "\n  identity-cutover:", 1
    )[0]
    assert "database_url_identity_migration_identity_migration" in registrar_block
    assert "networks: [postgres-net]" in registrar_block
    assert 'user: "10001:10001"' in registrar_block
    assert "read_only: true" in registrar_block
    assert "cap_drop: [ALL]" in registrar_block
    assert "no-new-privileges:true" in registrar_block

    assert '"register",' in ceremony
    assert '"identity-registrar",' in ceremony


def test_the_retired_sequential_import_service_stays_networkless() -> None:
    """Registration must not reawaken the retired sequential importer.

    The new registrar is a separate service. The obsolete `identity-migration`
    service keeps `network_mode: none` and must never gain the migration URL.
    """

    compose = COMPOSE.read_text(encoding="utf-8")
    block = compose.split("\n  identity-migration:\n", 1)[1].split("\n\n  ", 1)[0]
    assert "network_mode: none" in block
    assert "database_url_identity_migration_identity_migration" not in block


def test_runner_registers_from_ceremony_state_without_adding_a_step() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    # Folded into the existing handler: the journal requires next_step to equal
    # STEPS[len(completed_steps)], so a new step would brick the live journal.
    assert "_register_migration_run()" in source
    commit = source.split("def _commit_finalizer", 1)[1].split("def ", 1)[0]
    assert commit.index("_register_migration_run()") < commit.index(
        "identity-finalizer-admission-e5u-v1"
    )
    assert '"register"' in source
    assert "REGISTRATION_CONTRACT" in source


def test_runner_manifest_matches_the_sealed_compiler_split() -> None:
    """The runner rebuilds the manifest the sealed compiler would emit.

    reviewed_identity_packet_compiler.assemble_reviewed_bundle is inside the
    signing tool bundle whose digest is sealed into the credential policy, so it
    cannot be changed or reused from a networked process. The runner rebuilds
    the manifest half from the same private state; this pins the two together.
    """

    compiler = COMPILER.read_text(encoding="utf-8")
    bundle = compiler.split("def assemble_reviewed_bundle", 1)[1].split("\n\n\ndef ", 1)[0]
    assert 'run["review_signature"] = signature_hex' in bundle
    assert '"run": run,' in bundle
    assert '"source_items": sources,' in bundle
    assert '"decisions": decisions,' in bundle

    runner = RUNNER.read_text(encoding="utf-8")
    manifest = runner.split("def _registration_manifest", 1)[1].split(
        "    def _register_migration_run", 1
    )[0]
    assert 'signed_run["review_signature"] = signature' in manifest
    assert '"run": signed_run,' in manifest
    assert '"source_items": sources,' in manifest
    assert '"decisions": decisions,' in manifest
    # Projections are part of the signed bundle but not of the manifest;
    # including them would fail the kernel's exact key check.
    registered = manifest.split("return run_id, canonical_bytes(", 1)[1]
    assert "projections" not in registered

    kernel = KERNEL.read_text(encoding="utf-8")
    assert "ARRAY['run','source_items','decisions']" in kernel
