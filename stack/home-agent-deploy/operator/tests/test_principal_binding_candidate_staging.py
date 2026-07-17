from __future__ import annotations

import ast
import copy
import dataclasses
from datetime import timedelta
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import uuid

import pytest


OPERATOR_DIR = Path(__file__).parents[1]
FIXTURE_PATH = Path(__file__).with_name("test_parent_confirmation_staging.py")
FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_principal_nominee_signed_bundle_fixture", FIXTURE_PATH
)
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
fixture_helpers = importlib.util.module_from_spec(FIXTURE_SPEC)
sys.modules[FIXTURE_SPEC.name] = fixture_helpers
FIXTURE_SPEC.loader.exec_module(fixture_helpers)
verifier = fixture_helpers.verifier

MODULE_PATH = OPERATOR_DIR / "principal_binding_candidate_staging.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "principal_binding_candidate_staging", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
staging = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = staging
MODULE_SPEC.loader.exec_module(staging)


NOW = fixture_helpers.NOW
CHILD_ID = fixture_helpers.CHILD_ID
PARENT_A_ID = fixture_helpers.PARENT_A_ID
PARENT_B_ID = fixture_helpers.PARENT_B_ID


def receipt(ordinal: int) -> uuid.UUID:
    return uuid.UUID(fixture_helpers.u7(0x300 + ordinal))


def compile_specs(
    specs: list[dict[str, object]] | None = None,
    *,
    selected_ordinal: int = 0,
):
    fixture = fixture_helpers.SignedBundleFixture()
    raw_bundle, sources, raw_envelope = fixture.build(
        copy.deepcopy(specs if specs is not None else fixture_helpers.happy_specs())
    )
    result = staging.compile_reviewed_principal_nominee(
        raw_bundle,
        raw_envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(selected_ordinal),
        policy=fixture.policy,
    )
    return fixture, raw_bundle, sources, raw_envelope, result


def test_exact_person_receipt_compiles_one_permanently_dormant_nominee() -> None:
    _fixture, _raw, _sources, _envelope, result = compile_specs()

    assert str(result.nominee.person_id) == CHILD_ID
    assert result.nominee.display_label == "Marcelo"
    assert result.nominee.receipt_id == receipt(0)
    assert result.nominee.nomination_role == "operator_review_candidate"
    assert result.nominee.nomination_authority_status == "unverified"
    assert result.nominee.me_identity_established is False
    assert result.nominee_count == 1
    assert result.nomination_role == "operator_review_candidate"
    assert result.nomination_authority_status == "unverified"
    assert result.source_signatures_verified_during_compilation is True
    assert result.signed_person_projection_verified is True
    assert result.exact_receipt_selector_resolved is True
    assert result.portable_proof is False
    assert result.requires_raw_artifact_reverification is True
    assert result.compatibility_status == "coverage_unproven"
    assert result.deployment_status == "non_deployable"
    assert result.capability_status == "capability_disabled"
    assert result.verified_at == NOW
    assert result.review_expires_at == NOW + timedelta(minutes=5)


def test_exact_selector_changes_nominee_but_never_proves_nomination_authority() -> None:
    fixture = fixture_helpers.SignedBundleFixture()
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())

    child = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    parent = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(1),
        policy=fixture.policy,
    )

    assert child.nominee.person_id != parent.nominee.person_id
    assert child.nomination_commitment != parent.nomination_commitment
    for result in (child, parent):
        assert result.operator_identity_verified is False
        assert result.operator_nomination_authority_verified is False
        assert result.selector_authority_verified is False
        assert result.ha_identity_verified is False
        assert result.me_identity_established is False
        assert result.binding_created is False
        assert result.authoritative is False


def test_selector_must_be_an_exact_uuid7_person_projection_receipt() -> None:
    fixture = fixture_helpers.SignedBundleFixture()
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())

    for value in (str(receipt(0)), uuid.uuid4(), uuid.UUID(int=0), None):
        with pytest.raises(
            staging.PrincipalNomineeStagingError,
            match="person_receipt_selector_invalid",
        ):
            staging.compile_reviewed_principal_nominee(
                raw,
                envelope,
                source_records=sources,
                selected_person_projection_receipt_id=value,
                policy=fixture.policy,
            )

    for value in (receipt(3), uuid.UUID(fixture_helpers.u7(0x999))):
        with pytest.raises(
            staging.PrincipalNomineeStagingError,
            match="person_projection_receipt_not_found",
        ):
            staging.compile_reviewed_principal_nominee(
                raw,
                envelope,
                source_records=sources,
                selected_person_projection_receipt_id=value,
                policy=fixture.policy,
            )


@pytest.mark.parametrize(
    "other_label",
    ["MARCELO", "Ｍａｒｃｅｌｏ"],
)
def test_casefold_and_nfkc_display_ambiguity_fail_closed(other_label: str) -> None:
    specs = fixture_helpers.happy_specs()
    specs[1]["record"]["display_name"] = other_label
    with pytest.raises(
        staging.PrincipalNomineeStagingError,
        match="ambiguous_nominee_display_label",
    ):
        compile_specs(specs)


def test_whitespace_normalized_display_ambiguity_fails_closed() -> None:
    specs = fixture_helpers.happy_specs()
    specs[0]["record"]["display_name"] = "Marcelo Lima"
    specs[1]["record"]["display_name"] = "Marcelo   Lima"
    with pytest.raises(
        staging.PrincipalNomineeStagingError,
        match="ambiguous_nominee_display_label",
    ):
        compile_specs(specs)


@pytest.mark.parametrize(
    "privacy_directive",
    ["do_not_track", "ignored", "silent", "private", "auto_expire"],
)
def test_every_known_nominee_privacy_directive_blocks_staging(
    privacy_directive: str,
) -> None:
    specs = fixture_helpers.happy_specs() + [
        fixture_helpers.directive(CHILD_ID, privacy_directive)
    ]
    with pytest.raises(
        (staging.PrincipalNomineeStagingError, verifier.VerificationError)
    ):
        compile_specs(specs)


def test_archived_nominee_fails_closed() -> None:
    specs = fixture_helpers.happy_specs() + [fixture_helpers.archived(CHILD_ID)]
    with pytest.raises(
        staging.PrincipalNomineeStagingError, match="nominee_status_blocked"
    ):
        compile_specs(specs)


def test_exact_artifact_bytes_source_snapshot_and_both_signatures_are_rechecked() -> (
    None
):
    fixture = fixture_helpers.SignedBundleFixture()
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())

    changed_bundle = json.loads(raw)
    changed_bundle["projections"][0]["record"]["display_name"] = "Tampered"
    with pytest.raises(verifier.VerificationError):
        staging.compile_reviewed_principal_nominee(
            verifier.canonical_bytes(changed_bundle),
            envelope,
            source_records=sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )

    changed_sources = copy.deepcopy(sources)
    changed_sources[0]["row_key"]["fixture_ordinal"] = 99
    with pytest.raises(verifier.VerificationError):
        staging.compile_reviewed_principal_nominee(
            raw,
            envelope,
            source_records=changed_sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )

    changed_envelope = json.loads(envelope)
    changed_envelope["finalization_signature"] = "00" * 64
    with pytest.raises(verifier.VerificationError):
        staging.compile_reviewed_principal_nominee(
            raw,
            verifier.canonical_bytes(changed_envelope),
            source_records=sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )

    for mutable_bundle, mutable_envelope in (
        (bytearray(raw), envelope),
        (raw, bytearray(envelope)),
    ):
        with pytest.raises(
            staging.PrincipalNomineeStagingError,
            match="signed_artifact_type_invalid",
        ):
            staging.compile_reviewed_principal_nominee(
                mutable_bundle,
                mutable_envelope,
                source_records=sources,
                selected_person_projection_receipt_id=receipt(0),
                policy=fixture.policy,
            )


def test_source_snapshot_and_nomination_commitments_are_order_stable() -> None:
    fixture = fixture_helpers.SignedBundleFixture()
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())
    first = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    second = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=list(reversed(sources)),
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    assert second.source_record_snapshot_commitment == (
        first.source_record_snapshot_commitment
    )
    assert second.nomination_commitment == first.nomination_commitment


def test_stateful_source_sequence_is_frozen_once_before_verification() -> None:
    fixture = fixture_helpers.SignedBundleFixture()
    raw, valid_sources, envelope = fixture.build(fixture_helpers.happy_specs())
    tampered_sources = copy.deepcopy(valid_sources)
    tampered_sources[0]["row_key"]["fixture_ordinal"] = 99

    class FlipSequence:
        def __init__(self) -> None:
            self.iterations = 0

        def __len__(self) -> int:
            return len(valid_sources)

        def __iter__(self):
            self.iterations += 1
            if self.iterations == 1:
                return iter(tampered_sources)
            return iter(valid_sources)

    source_records = FlipSequence()
    with pytest.raises(verifier.VerificationError):
        staging.compile_reviewed_principal_nominee(
            raw,
            envelope,
            source_records=source_records,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )
    assert source_records.iterations == 1


def test_result_is_immutable_redacted_and_every_authority_switch_is_off() -> None:
    _fixture, _raw, sources, _envelope, result = compile_specs()
    rendered = repr(result)
    audit = json.dumps(result.content_free_audit_payload(), sort_keys=True)
    for private in (
        "Marcelo",
        CHILD_ID,
        str(result.nominee.receipt_id),
        result.nomination_commitment,
        result.source_record_snapshot_commitment,
    ):
        assert private not in rendered
        assert private not in audit

    false_fields = (
        "portable_proof",
        "downstream_consumption_allowed",
        "operator_identity_verified",
        "operator_nomination_authority_verified",
        "selector_authority_verified",
        "ha_identity_verified",
        "binding_graph_verified",
        "current_person_status_verified",
        "current_privacy_state_verified",
        "current_retrieval_state_verified",
        "erasure_coverage_complete",
        "fresh_confirmation_verified",
        "single_use_artifact_consumed",
        "serializable_commit_enforced",
        "me_identity_established",
        "binding_created",
        "authoritative",
        "commit_ready",
        "enables_writes",
        "location_memory_enabled",
        "travel_greetings_enabled",
    )
    for field_name in false_fields:
        assert getattr(result, field_name) is False
        with pytest.raises(ValueError):
            dataclasses.replace(result, **{field_name: True})

    sources[0]["allowed_projection"]["decisions"][0]["record"]["display_name"] = (
        "Mutated after verification"
    )
    assert result.nominee.display_label == "Marcelo"


def test_reverification_requires_raw_artifacts_and_returns_a_fresh_object() -> None:
    fixture, raw, sources, envelope, result = compile_specs()
    reverified = staging.reverify_reviewed_principal_nominee(
        result,
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    assert reverified == result
    assert reverified is not result

    forged_projection = dataclasses.replace(
        result.nominee, display_label="Attacker-selected person"
    )
    forged = dataclasses.replace(result, nominee=forged_projection)
    with pytest.raises(
        staging.PrincipalNomineeStagingError,
        match="principal_nominee_reverification_failed",
    ):
        staging.reverify_reviewed_principal_nominee(
            forged,
            raw,
            envelope,
            source_records=sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )

    with pytest.raises(
        staging.PrincipalNomineeStagingError,
        match="principal_nominee_reverification_failed",
    ):
        staging.reverify_reviewed_principal_nominee(
            result.private_payload(),
            raw,
            envelope,
            source_records=sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )


def test_reverification_uses_a_fresh_advancing_clock_without_digest_drift() -> None:
    clock = [NOW]
    fixture = fixture_helpers.SignedBundleFixture()
    fixture.policy = dataclasses.replace(fixture.policy, now=lambda: clock[0])
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())
    result = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    clock[0] = NOW + timedelta(seconds=1)
    reverified = staging.reverify_reviewed_principal_nominee(
        result,
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    assert reverified.verified_at == NOW + timedelta(seconds=1)
    assert reverified.nomination_commitment == result.nomination_commitment


def test_expired_review_cannot_be_nominated_or_reverified() -> None:
    clock = [NOW]
    fixture = fixture_helpers.SignedBundleFixture()
    fixture.policy = dataclasses.replace(fixture.policy, now=lambda: clock[0])
    raw, sources, envelope = fixture.build(fixture_helpers.happy_specs())
    result = staging.compile_reviewed_principal_nominee(
        raw,
        envelope,
        source_records=sources,
        selected_person_projection_receipt_id=receipt(0),
        policy=fixture.policy,
    )
    clock[0] = NOW + timedelta(minutes=5)
    with pytest.raises(verifier.VerificationError):
        staging.reverify_reviewed_principal_nominee(
            result,
            raw,
            envelope,
            source_records=sources,
            selected_person_projection_receipt_id=receipt(0),
            policy=fixture.policy,
        )


def test_compiler_has_only_one_receipt_selector_and_no_identity_authority_input() -> (
    None
):
    parameters = inspect.signature(
        staging.compile_reviewed_principal_nominee
    ).parameters
    assert tuple(parameters) == (
        "raw_review_bundle",
        "raw_finalization_envelope",
        "source_records",
        "selected_person_projection_receipt_id",
        "policy",
    )
    assert not {
        "person_id",
        "person_name",
        "display_label",
        "alias",
        "ha_user_id",
        "principal_id",
        "actor_id",
        "me_role",
        "verified_document",
        "operator_authorized",
    } & set(parameters)


def test_module_has_no_runtime_database_network_binding_or_memory_authority() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        value.startswith(
            ("psycopg", "sqlalchemy", "requests", "socket", "urllib", "subprocess")
        )
        for value in imports
    )
    for prohibited in (
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "authoritative=True",
        "commit_ready=True",
        "enables_writes=True",
        "binding_created=True",
        "location_memory_enabled=True",
        "travel_greetings_enabled=True",
    ):
        assert prohibited not in source

    repository = MODULE_PATH.parents[3]
    runtime_roots = (
        repository / "stack/services/home-agent-core/app",
        repository / "stack/services/home-agent-bff",
        repository / "app/src",
        repository / "app/src-tauri/src",
        repository / "web-gateway",
        repository / "ha-config/home_agent_edge",
    )
    for root in runtime_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {
                ".py",
                ".js",
                ".mjs",
                ".ts",
                ".tsx",
                ".jsx",
                ".rs",
            }:
                continue
            assert "principal_binding_candidate_staging" not in path.read_text(
                encoding="utf-8"
            ), path
