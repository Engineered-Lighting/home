from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "stack/services/home-agent-core/app/identity_admission_writer.py"
BRIDGE = ROOT / "stack/home-agent-deploy/operator/phase3_authority_admission.py"
ENTRYPOINT = ROOT / "stack/services/home-agent-core/docker-entrypoint.sh"
SOURCE_PLAN = ROOT / "stack/home-agent-deploy/operator/phase3_activation_source_plan.py"
RUNNER = ROOT / "tools/run-home-agent-e1-postgres-gate.py"
WORKFLOW = ROOT / ".github/workflows/home-agent-e1-postgres.yml"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "home_agent_identity_admission_writer_e5u",
        WRITER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _uuid7(suffix: str) -> str:
    return f"018f3f7a-8b4d-7abc-8def-{suffix}"


def _cutover_document(admission_id: str) -> bytes:
    ids = {
        "admission_id": admission_id,
        "promotion_id": _uuid7("0123456789ac"),
        "run_id": _uuid7("0123456789ad"),
        "finalization_id": _uuid7("0123456789ad"),
        "candidate_cutover_id": _uuid7("0123456789ae"),
        "writer_freeze_id": _uuid7("0123456789af"),
        "source_installation_id": _uuid7("0123456789b0"),
    }
    value = {
        **ids,
        "authority_scope": "identity_semantics",
        "contract_version": "reviewed-identity-semantic-cutover-v1",
        "core_capability_digest": "a" * 64,
        "core_schema_digest": "b" * 64,
        "cutover_signature": "c" * 128,
        "e3_commitment_key_epoch": "1",
        "e3_projection_manifest_commitment": "d" * 64,
        "e3_source_manifest_commitment": "e" * 64,
        "erasure_ledger_epoch": "0",
        "erasure_ledger_head_hash": "f" * 64,
        "erasure_ledger_verification": "head_matched",
        "freeze_commitment": "1" * 64,
        "policy_digest": "2" * 64,
        "privacy_check_set_commitment": "3" * 64,
        "promotion_commitment": "4" * 64,
        "release_manifest_digest": "5" * 64,
        "semantic_generation": "1",
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": "6" * 64,
        "verifier_bundle_digest": (
            "eae0fd391a19a50a89fab9ee7665760499d7f154bfaf518abe56b8eed2090aa0"
        ),
    }
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _request(contract: str, admission_id: str, document: bytes) -> bytes:
    return json.dumps(
        {
            "admission_id": admission_id,
            "contract": contract,
            "document_b64": base64.b64encode(document).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_cutover_request_derives_only_content_minimized_parameters() -> None:
    module = _module()
    admission_id = _uuid7("0123456789ab")
    request = module.parse_request(
        _request(
            "identity-cutover-admission-e5u-v1",
            admission_id,
            _cutover_document(admission_id),
        ),
        module.OPERATIONS["cutover"],
    )

    assert str(request.admission_id) == admission_id
    assert request.parameters["admission_id"] == admission_id
    assert request.parameters["semantic_generation"] == 1
    assert request.parameters["erasure_ledger_epoch"] == 0
    assert len(request.parameters["document_sha256"]) == 64
    assert "cutover_signature" not in request.parameters


@pytest.mark.parametrize(
    "mutator",
    (
        lambda raw: raw.replace(
            b'"semantic_generation":"1"', b'"semantic_generation":"01"'
        ),
        lambda raw: raw.replace(
            b'"authority_scope":"identity_semantics"', b'"authority_scope":"other"'
        ),
        lambda raw: raw.replace(
            b'"verifier_bundle_digest":"eae0', b'"verifier_bundle_digest":"0ae0'
        ),
        lambda raw: raw[:-1] + b',"admission_id":"duplicate"}',
    ),
)
def test_cutover_request_fails_closed_on_drift(mutator) -> None:
    module = _module()
    admission_id = _uuid7("0123456789ab")
    document = mutator(_cutover_document(admission_id))
    with pytest.raises(module.AdmissionWriterError):
        module.parse_request(
            _request(
                "identity-cutover-admission-e5u-v1",
                admission_id,
                document,
            ),
            module.OPERATIONS["cutover"],
        )


def test_writer_database_url_is_pinned_to_owner(monkeypatch) -> None:
    module = _module()
    expected = (
        f"postgresql+psycopg://home_agent_owner:{'a' * 64}" "@postgres:5432/home_agent"
    )
    monkeypatch.setenv("HOME_AGENT_DATABASE_URL", expected)
    assert module.database_url() == expected
    for rejected in (
        expected.replace("home_agent_owner", "home_agent_api"),
        expected.replace("@postgres:", "@127.0.0.1:"),
        expected + "?sslmode=disable",
        expected.replace("a" * 64, "password"),
    ):
        monkeypatch.setenv("HOME_AGENT_DATABASE_URL", rejected)
        with pytest.raises(module.AdmissionWriterError):
            module.database_url()


def test_writers_are_single_statement_serializable_and_exact_replay_safe() -> None:
    source = WRITER.read_text(encoding="utf-8")
    assert 'isolation_level="SERIALIZABLE"' in source
    assert "hide_parameters=True" in source
    assert "poolclass=NullPool" in source
    assert "log_parameter_max_length_on_error=0" in source
    assert "WITH migration_run AS (" in source
    assert "WITH attempted AS (" in source
    assert source.count("ON CONFLICT DO NOTHING") == 2
    assert source.count("exact_existing AS (") == 2
    assert "transaction_timestamp() + interval '15 minutes'" in source
    assert "print(error" not in source
    assert "str(error)" not in source


def test_finalizer_admission_never_outlives_its_reviewed_run() -> None:
    """The finalizer kernel refuses an admission that expires after its run.

    0013 rejects `admission.expires_at > migration_run.expires_at`, and does not
    relax it for an exact replay. An unconditional fifteen-minute admission
    always outlived the ten-minute reviewed run, so no admission this writer
    produced could ever be finalized, for any operator timing.
    """

    source = WRITER.read_text(encoding="utf-8")
    finalizer = source.split("FINALIZER_SQL", 1)[1].split("CUTOVER_SQL", 1)[0]

    # The admission expires with the run, whichever comes first.
    assert "LEAST(" in finalizer
    assert "migration_run.expires_at," in finalizer
    assert "transaction_timestamp() + interval '15 minutes'" in finalizer

    # A new admission is only inserted while the run is still live, because the
    # admissions table requires expires_at > admitted_at.
    assert "WHERE migration_run.expires_at > transaction_timestamp()" in finalizer

    # The replay lookup stays unfiltered so an exact replay against an expired
    # run still returns the admission that already exists.
    exact_existing = finalizer.split("exact_existing AS (", 1)[1]
    assert "expires_at > transaction_timestamp()" not in exact_existing

    # The cutover kernel compares only against its own admission expiry, so the
    # cutover statement is deliberately left alone.
    cutover = source.split("CUTOVER_SQL", 1)[1]
    assert "transaction_timestamp() + interval '15 minutes'" in cutover
    assert "LEAST(" not in cutover


def test_root_bridge_passes_private_stdin_to_only_fixed_image_entrypoints() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    assert "identity-admit-finalizer" in source
    assert "identity-admit-cutover" in source
    assert '"--no-deps"' in source
    assert "input=request" in source
    # The bridge feeds a private People document to the container, so its
    # stderr is captured but never echoed: only governed kernel identifiers
    # are named, and the tail printer is not reachable from this program.
    assert "stderr=subprocess.PIPE" in source
    assert "activation.governed_error_codes(result.stderr)" in source
    assert "report_diagnostic" not in source
    assert "shell=False" in source
    assert 'activation._guard_revision("0015_current_authority_e5a")' in source
    assert "identity-admit-finalizer)" in entrypoint
    assert "identity-admit-cutover)" in entrypoint
    assert "python -m app.identity_admission_writer finalizer" in entrypoint
    assert "python -m app.identity_admission_writer cutover" in entrypoint


def test_source_plan_tracks_admission_writer_and_retains_role_boundary() -> None:
    source = SOURCE_PLAN.read_text(encoding="utf-8")
    assert "phase3_authority_admission.py" in source
    assert "identity_authority_admission_writer_not_installed" not in source
    assert "identity_disposable_role_activation_not_installed" not in source
    assert '"identity_authority_admission_writer_installed": trusted' in source


def test_e5u_is_carried_by_the_filtered_hosted_gate() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "identity_admission_writer.py" in runner
    assert "phase3_authority_admission.py" in runner
    assert "test_identity_admission_writer_e5u.py" in runner
    assert "test_identity_admission_writer_e5u.py" in workflow
    assert "E5t/E5u/E5v/E5w/E5x PostgreSQL gate" in workflow
    assert "E5t/E5u/E5v/E5w/E5x authority gate" in workflow
