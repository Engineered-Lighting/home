from __future__ import annotations

import ast
import copy
import dataclasses
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest


OPERATOR_DIR = Path(__file__).parents[1]
VERIFIER_PATH = OPERATOR_DIR / "reviewed_identity_payload.py"
VERIFIER_SPEC = importlib.util.spec_from_file_location(
    "reviewed_identity_payload", VERIFIER_PATH
)
assert VERIFIER_SPEC is not None and VERIFIER_SPEC.loader is not None
verifier = importlib.util.module_from_spec(VERIFIER_SPEC)
sys.modules[VERIFIER_SPEC.name] = verifier
VERIFIER_SPEC.loader.exec_module(verifier)

MODULE_PATH = OPERATOR_DIR / "parent_confirmation_staging.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "parent_confirmation_staging", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
staging = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = staging
MODULE_SPEC.loader.exec_module(staging)


NOW = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)
RUN_ID = "00000000-0000-7000-8000-00000000a001"
REQUEST_ID = "00000000-0000-7000-8000-00000000a002"
SHADOW_ID = "00000000-0000-7000-8000-00000000a003"
CHILD_ID = "11111111-1111-4111-8111-111111111111"
PARENT_A_ID = "22222222-2222-4222-8222-222222222222"
PARENT_B_ID = "33333333-3333-4333-8333-333333333333"
PARENT_C_ID = "44444444-4444-4444-8444-444444444444"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def u7(value: int) -> str:
    return f"00000000-0000-7000-8000-{value:012x}"


def person(person_id: str, label: str) -> dict[str, Any]:
    return {
        "kind": "person",
        "record": {
            "person_id": person_id,
            "display_name": label,
            "pronouns": None,
            "privacy_scope": "private",
            "legacy_source_ref": f"legacy-identity-store:v1:identity:{person_id[:8]}",
            "legacy_source_version": 3,
            "legacy_source_sha256": digest(f"person-{person_id}"),
        },
    }


def parent_role(person_id: str, *, role_label: str = "parent") -> dict[str, Any]:
    return {
        "kind": "legacy_role_candidate",
        "record": {
            "label_id": None,
            "person_id": person_id,
            "role_label": role_label,
            "perspective": "unknown",
            "source_ref": f"legacy-identity-store:v1:role:{person_id[:8]}",
            "source_version": 3,
            "source_snapshot_sha256": digest(f"role-{person_id}"),
        },
    }


def directive(person_id: str, value: str) -> dict[str, Any]:
    return {
        "kind": "privacy_directive",
        "record": {
            "directive_id": None,
            "person_id": person_id,
            "directive": value,
            "enabled": True,
            "expires_at": (
                (NOW + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                if value == "auto_expire"
                else None
            ),
            "source_ref": f"legacy-identity-store:v1:privacy:{person_id[:8]}",
            "source_version": 3,
            "source_snapshot_sha256": digest(f"privacy-{person_id}-{value}"),
        },
    }


def archived(person_id: str) -> dict[str, Any]:
    return {
        "kind": "person_status",
        "record": {
            "person_id": person_id,
            "status": "archived",
            "source_ref": f"legacy-identity-store:v1:status:{person_id[:8]}",
            "source_version": 3,
            "source_snapshot_sha256": digest(f"status-{person_id}"),
        },
    }


def happy_specs() -> list[dict[str, Any]]:
    return [
        person(CHILD_ID, "Marcelo"),
        person(PARENT_A_ID, "Amelia"),
        person(PARENT_B_ID, "Marcelo Sr."),
        parent_role(PARENT_A_ID),
        parent_role(PARENT_B_ID),
    ]


class SignedBundleFixture:
    def __init__(self) -> None:
        self.review_private = Ed25519PrivateKey.generate()
        self.finalization_private = Ed25519PrivateKey.generate()
        review_public = self.review_private.public_key()
        finalization_public = self.finalization_private.public_key()
        review_bytes = review_public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        finalization_bytes = finalization_public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.commitment_key = bytes(range(32))
        self.policy = verifier.VerificationPolicy(
            review_public_key=review_public,
            review_key_fingerprint=hashlib.sha256(review_bytes).hexdigest(),
            finalization_public_key=finalization_public,
            finalization_key_fingerprint=hashlib.sha256(finalization_bytes).hexdigest(),
            commitment_key=self.commitment_key,
            commitment_key_epoch=9,
            commitment_key_fingerprint=hashlib.sha256(self.commitment_key).hexdigest(),
            policy_version="home-agent-mvp-v1",
            policy_digest=digest("policy"),
            source_projection_contract_digest=digest("projection-contract"),
            release_manifest_digest=digest("release"),
            migration_tool_bundle_digest=digest("tool"),
            core_oci_manifest_digest=digest("oci"),
            core_schema_digest=digest("schema"),
            core_capability_digest=digest("capability"),
            shadow_authorization_id=verifier.uuid.UUID(SHADOW_ID),
            now=lambda: NOW,
        )

    @staticmethod
    def _record_identity(kind: str, record: dict[str, Any]) -> dict[str, Any]:
        fields = {
            "person": "person_id",
            "privacy_directive": "directive_id",
            "person_status": "person_id",
            "legacy_role_candidate": "label_id",
        }
        field = fields[kind]
        return {field: record[field]}

    def build(
        self, specs: list[dict[str, Any]]
    ) -> tuple[bytes, list[dict[str, Any]], bytes]:
        tables = {
            "person": "identity.people",
            "privacy_directive": "identity.privacy_directives",
            "person_status": "identity.people",
            "legacy_role_candidate": "identity.legacy_role_labels",
        }
        id_fields = {
            "privacy_directive": "directive_id",
            "legacy_role_candidate": "label_id",
        }
        sources: list[dict[str, Any]] = []
        private_sources: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        projections: list[dict[str, Any]] = []
        for ordinal, value in enumerate(copy.deepcopy(specs)):
            kind = value["kind"]
            record = value["record"]
            source_id = u7(0x100 + ordinal)
            decision_id = u7(0x200 + ordinal)
            receipt_id = value.get("receipt_id_override", u7(0x300 + ordinal))
            if kind in id_fields:
                record[id_fields[kind]] = decision_id
            allowed_projection = {
                "decisions": [
                    {
                        "decision_kind": kind,
                        "disposition": "apply",
                        "record": record,
                    }
                ]
            }
            private_source = {
                "source_item_id": source_id,
                "source_table_kind": "identities",
                "row_key": {"fixture_ordinal": ordinal},
                "allowed_projection": allowed_projection,
            }
            source = {
                "source_item_id": source_id,
                "ordinal": ordinal,
                "source_table_kind": "identities",
                "row_key_commitment": verifier.keyed_commitment(
                    self.commitment_key,
                    "identity-source-row-key-v1",
                    {
                        "run_id": RUN_ID,
                        "source_item_id": source_id,
                        "source_table_kind": "identities",
                        "row_key": private_source["row_key"],
                    },
                ),
                "allowed_projection_commitment": verifier.keyed_commitment(
                    self.commitment_key,
                    "identity-allowed-projection-v1",
                    {
                        "run_id": RUN_ID,
                        "source_item_id": source_id,
                        "source_table_kind": "identities",
                        "allowed_projection": allowed_projection,
                    },
                ),
            }
            candidate = verifier.keyed_commitment(
                self.commitment_key,
                "identity-candidate-v1",
                {
                    "run_id": RUN_ID,
                    "decision_id": decision_id,
                    "decision_kind": kind,
                    "record": record,
                },
            )
            decision = {
                "decision_id": decision_id,
                "source_item_id": source_id,
                "ordinal": ordinal,
                "decision_kind": kind,
                "disposition": "apply",
                "candidate_commitment": candidate,
                "canonical_apply_decision_id": None,
                "canonical_apply_disposition": None,
                "decision_commitment": "",
            }
            decision["decision_commitment"] = verifier.keyed_commitment(
                self.commitment_key,
                "identity-decision-v1",
                {
                    key: item
                    for key, item in decision.items()
                    if key != "decision_commitment"
                },
            )
            table = tables[kind]
            projection_ref = verifier.keyed_commitment(
                self.commitment_key,
                "identity-projection-reference-v1",
                {
                    "run_id": RUN_ID,
                    "decision_id": decision_id,
                    "projection_table_kind": table,
                    "record_identity": self._record_identity(kind, record),
                },
            )
            projection_commitment = verifier.keyed_commitment(
                self.commitment_key,
                "identity-semantic-projection-v1",
                {
                    "run_id": RUN_ID,
                    "decision_id": decision_id,
                    "decision_kind": kind,
                    "projection_table_kind": table,
                    "record": record,
                },
            )
            receipt = verifier.keyed_commitment(
                self.commitment_key,
                "identity-projection-receipt-v1",
                {
                    "run_id": RUN_ID,
                    "decision_id": decision_id,
                    "receipt_id": receipt_id,
                    "candidate_commitment": candidate,
                    "projection_ref_commitment": projection_ref,
                    "projection_commitment": projection_commitment,
                },
            )
            projection = {
                "decision_id": decision_id,
                "decision_kind": kind,
                "candidate_commitment": candidate,
                "projection_table_kind": table,
                "projection_ref_commitment": projection_ref,
                "projection_commitment": projection_commitment,
                "receipt_id": receipt_id,
                "receipt_commitment": receipt,
                "record": record,
            }
            sources.append(source)
            private_sources.append(private_source)
            decisions.append(decision)
            projections.append(projection)

        run = {
            "run_id": RUN_ID,
            "operator_request_id": REQUEST_ID,
            "contract_version": "reviewed-identity-migration-run-v1",
            "source_schema_version": 1,
            "source_projection_contract_version": (
                "legacy-identity-source-projection-v1"
            ),
            "importer_version": "legacy-identity-importer-v1",
            "canonicalization_version": "identity-canonicalization-v1",
            "projection_version": "semantic-people-projection-v1",
            "shadow_rule_version": "record-only-envelope-worker-gate-v3",
            "commitment_algorithm": "hmac-sha256-v1",
            "commitment_key_fingerprint": self.policy.commitment_key_fingerprint,
            "commitment_key_epoch": self.policy.commitment_key_epoch,
            "source_item_count": len(sources),
            "decision_count": len(decisions),
            "logical_source_manifest_commitment": verifier.keyed_commitment(
                self.commitment_key, "identity-source-manifest-v1", sources
            ),
            "projection_manifest_commitment": verifier.keyed_commitment(
                self.commitment_key, "identity-projection-manifest-v1", projections
            ),
            "source_projection_contract_digest": (
                self.policy.source_projection_contract_digest
            ),
            "review_receipt_commitment": "",
            "policy_version": self.policy.policy_version,
            "policy_digest": self.policy.policy_digest,
            "shadow_authorization_id": SHADOW_ID,
            "release_manifest_digest": self.policy.release_manifest_digest,
            "migration_tool_bundle_digest": (self.policy.migration_tool_bundle_digest),
            "core_oci_manifest_digest": self.policy.core_oci_manifest_digest,
            "core_schema_digest": self.policy.core_schema_digest,
            "core_capability_digest": self.policy.core_capability_digest,
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": self.policy.review_key_fingerprint,
            "review_signature": "",
            "expires_at": (NOW + timedelta(minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
        }
        receipt_run = {
            key: item
            for key, item in run.items()
            if key not in {"review_signature", "review_receipt_commitment"}
        }
        run["review_receipt_commitment"] = verifier.keyed_commitment(
            self.commitment_key,
            "identity-review-receipt-v1",
            {
                "run": receipt_run,
                "source_items": sources,
                "decisions": decisions,
                "projections": projections,
            },
        )
        unsigned_run = {
            key: item for key, item in run.items() if key != "review_signature"
        }
        signed = verifier.canonical_bytes(
            {
                "domain": "reviewed-identity-migration-review-v1",
                "run": unsigned_run,
                "source_items": sources,
                "decisions": decisions,
                "projections": projections,
            }
        )
        run["review_signature"] = self.review_private.sign(signed).hex()
        raw_bundle = verifier.canonical_bytes(
            {
                "contract_version": "reviewed-identity-projection-bundle-v1",
                "manifest": {
                    "run": run,
                    "source_items": sources,
                    "decisions": decisions,
                },
                "projections": projections,
            }
        )
        verified_bundle = verifier.verify_projection_bundle(
            raw_bundle,
            source_records=private_sources,
            policy=self.policy,
        )
        proposal = verified_bundle.finalization_proposal
        raw_envelope = verifier.canonical_bytes(
            {
                "contract_version": "reviewed-identity-finalization-envelope-v1",
                "finalization_id": RUN_ID,
                "finalization_commitment": proposal["finalization_commitment"],
                "signature_algorithm": "ed25519",
                "signing_key_fingerprint": self.policy.finalization_key_fingerprint,
                "signature_scope": "reviewed-identity-migration-finalization-v1",
                "finalization_signature": self.finalization_private.sign(
                    verified_bundle.finalization_signing_payload(now=NOW)
                ).hex(),
            }
        )
        return raw_bundle, private_sources, raw_envelope


def compile_specs(specs: list[dict[str, Any]]):
    fixture = SignedBundleFixture()
    raw_bundle, sources, raw_envelope = fixture.build(specs)
    result = staging.compile_signed_parent_candidate_set(
        raw_bundle,
        raw_envelope,
        source_records=sources,
        policy=fixture.policy,
    )
    return fixture, raw_bundle, sources, raw_envelope, result


def test_signed_finalizer_derives_exactly_two_stable_dormant_candidates() -> None:
    _fixture, _raw, _sources, _envelope, result = compile_specs(happy_specs())

    assert [str(item.parent_person_id) for item in result.candidates] == [
        PARENT_A_ID,
        PARENT_B_ID,
    ]
    assert [item.display_label for item in result.candidates] == [
        "Amelia",
        "Marcelo Sr.",
    ]
    assert all(item.legacy_role_label == "parent" for item in result.candidates)
    assert all(item.legacy_perspective == "unknown" for item in result.candidates)
    assert all(item.legacy_authoritative is False for item in result.candidates)
    assert result.source_signatures_verified_during_compilation is True
    assert result.portable_proof is False
    assert result.downstream_consumption_allowed is False
    assert result.requires_raw_artifact_reverification is True
    assert result.signed_snapshot_has_no_archived_parent is True
    assert result.current_person_status_verified is False
    assert result.compatibility_status == "coverage_unproven"
    assert result.deployment_status == "non_deployable"
    assert result.capability_status == "capability_disabled"
    assert result.candidate_count == 2
    assert result.verified_at == NOW
    assert result.review_expires_at == NOW + timedelta(minutes=5)


def test_candidate_order_is_independent_of_signed_projection_order() -> None:
    specs = happy_specs()
    reordered = [specs[0], specs[2], specs[1], specs[4], specs[3]]
    first = compile_specs(specs)[-1]
    second = compile_specs(reordered)[-1]
    assert [item.parent_person_id for item in first.candidates] == [
        item.parent_person_id for item in second.candidates
    ]


@pytest.mark.parametrize("count", [0, 1, 3])
def test_every_parent_label_participates_and_exactly_two_are_required(
    count: int,
) -> None:
    parent_ids = [PARENT_A_ID, PARENT_B_ID, PARENT_C_ID][:count]
    specs = [person(CHILD_ID, "Marcelo")]
    specs += [
        person(value, f"Parent {index}") for index, value in enumerate(parent_ids)
    ]
    specs += [parent_role(value) for value in parent_ids]
    fixture = SignedBundleFixture()
    raw, sources, envelope = fixture.build(specs)
    with pytest.raises(
        staging.ParentCandidateStagingError, match="parent_candidate_count_invalid"
    ):
        staging.compile_signed_parent_candidate_set(
            raw, envelope, source_records=sources, policy=fixture.policy
        )


def test_duplicate_parent_identity_and_ambiguous_display_labels_fail_closed() -> None:
    duplicate = happy_specs() + [parent_role(PARENT_A_ID)]
    duplicate.remove(parent_role(PARENT_B_ID))
    with pytest.raises(
        staging.ParentCandidateStagingError, match="duplicate_parent_candidate"
    ):
        compile_specs(duplicate)

    ambiguous = happy_specs()
    ambiguous[2]["record"]["display_name"] = "amelia"
    with pytest.raises(
        staging.ParentCandidateStagingError, match="ambiguous_parent_display_label"
    ):
        compile_specs(ambiguous)

    compatibility_ambiguous = happy_specs()
    compatibility_ambiguous[2]["record"]["display_name"] = "Ａｍｅｌｉａ"
    with pytest.raises(
        staging.ParentCandidateStagingError,
        match="ambiguous_parent_display_label",
    ):
        compile_specs(compatibility_ambiguous)

    whitespace_ambiguous = happy_specs()
    whitespace_ambiguous[1]["record"]["display_name"] = "Marcelo  Sr."
    with pytest.raises(
        staging.ParentCandidateStagingError,
        match="ambiguous_parent_display_label",
    ):
        compile_specs(whitespace_ambiguous)


def test_signed_bundle_cannot_reuse_a_projection_receipt_identity() -> None:
    specs = happy_specs()
    specs[1]["receipt_id_override"] = u7(0x399)
    specs[3]["receipt_id_override"] = u7(0x399)
    fixture = SignedBundleFixture()
    with pytest.raises(verifier.VerificationError, match="receipt ID is duplicated"):
        fixture.build(specs)


@pytest.mark.parametrize(
    "privacy_directive",
    ["do_not_track", "ignored", "silent", "private", "auto_expire"],
)
def test_every_known_parent_privacy_directive_blocks_staging(
    privacy_directive: str,
) -> None:
    specs = happy_specs() + [directive(PARENT_A_ID, privacy_directive)]
    with pytest.raises(
        (staging.ParentCandidateStagingError, verifier.VerificationError)
    ):
        compile_specs(specs)


def test_archived_parent_and_non_parent_role_fail_closed() -> None:
    with pytest.raises(
        staging.ParentCandidateStagingError, match="parent_status_blocked"
    ):
        compile_specs(happy_specs() + [archived(PARENT_A_ID)])

    specs = happy_specs()
    specs[-1] = parent_role(PARENT_B_ID, role_label="relative")
    with pytest.raises(
        staging.ParentCandidateStagingError, match="parent_candidate_count_invalid"
    ):
        compile_specs(specs)


def test_bundle_source_receipt_and_finalization_tampering_are_rejected() -> None:
    fixture = SignedBundleFixture()
    raw, sources, envelope = fixture.build(happy_specs())

    bundle = json.loads(raw)
    bundle["projections"][1]["record"]["display_name"] = "Tampered"
    with pytest.raises(verifier.VerificationError):
        staging.compile_signed_parent_candidate_set(
            verifier.canonical_bytes(bundle),
            envelope,
            source_records=sources,
            policy=fixture.policy,
        )

    for mutable_bundle, mutable_envelope in (
        (bytearray(raw), envelope),
        (raw, bytearray(envelope)),
    ):
        with pytest.raises(
            staging.ParentCandidateStagingError,
            match="signed_artifact_type_invalid",
        ):
            staging.compile_signed_parent_candidate_set(
                mutable_bundle,
                mutable_envelope,
                source_records=sources,
                policy=fixture.policy,
            )

    changed_sources = copy.deepcopy(sources)
    changed_sources[0]["row_key"]["fixture_ordinal"] = 99
    with pytest.raises(verifier.VerificationError):
        staging.compile_signed_parent_candidate_set(
            raw,
            envelope,
            source_records=changed_sources,
            policy=fixture.policy,
        )

    receipt = json.loads(raw)
    receipt["projections"][3]["receipt_commitment"] = digest("substitute")
    with pytest.raises(verifier.VerificationError):
        staging.compile_signed_parent_candidate_set(
            verifier.canonical_bytes(receipt),
            envelope,
            source_records=sources,
            policy=fixture.policy,
        )

    changed_envelope = json.loads(envelope)
    changed_envelope["finalization_signature"] = "00" * 64
    with pytest.raises(verifier.VerificationError):
        staging.compile_signed_parent_candidate_set(
            raw,
            verifier.canonical_bytes(changed_envelope),
            source_records=sources,
            policy=fixture.policy,
        )


def test_output_is_immutable_redacted_and_permanently_write_disabled() -> None:
    _fixture, _raw, sources, _envelope, result = compile_specs(happy_specs())
    rendered = repr(result)
    audit = json.dumps(result.content_free_audit_payload(), sort_keys=True)
    for private in (
        "Amelia",
        "Marcelo Sr.",
        PARENT_A_ID,
        PARENT_B_ID,
        result.candidate_set_commitment,
    ):
        assert private not in rendered
        assert private not in audit
    for field_name in (
        "authoritative",
        "commit_ready",
        "enables_writes",
        "atomic_commit_enforced",
        "explicit_parent_facts_created",
        "portable_proof",
        "downstream_consumption_allowed",
        "current_person_status_verified",
    ):
        assert getattr(result, field_name) is False
        with pytest.raises(ValueError):
            dataclasses.replace(result, **{field_name: True})

    sources[1]["allowed_projection"]["decisions"][0]["record"]["display_name"] = (
        "Mutated after verification"
    )
    assert result.candidates[0].display_label == "Amelia"


def test_candidate_set_must_be_reverified_against_original_signed_artifacts() -> None:
    fixture, raw, sources, envelope, result = compile_specs(happy_specs())
    reverified = staging.reverify_signed_parent_candidate_set(
        result,
        raw,
        envelope,
        source_records=sources,
        policy=fixture.policy,
    )
    assert reverified == result
    assert reverified is not result

    forged_candidate = dataclasses.replace(
        result.candidates[0], display_label="Attacker-selected parent"
    )
    forged = dataclasses.replace(
        result,
        candidates=(forged_candidate, result.candidates[1]),
    )
    with pytest.raises(
        staging.ParentCandidateStagingError,
        match="candidate_set_reverification_failed",
    ):
        staging.reverify_signed_parent_candidate_set(
            forged,
            raw,
            envelope,
            source_records=sources,
            policy=fixture.policy,
        )


def test_candidate_set_reverification_uses_a_fresh_advancing_clock() -> None:
    clock = [NOW]
    fixture = SignedBundleFixture()
    fixture.policy = dataclasses.replace(fixture.policy, now=lambda: clock[0])
    raw, sources, envelope = fixture.build(happy_specs())
    result = staging.compile_signed_parent_candidate_set(
        raw,
        envelope,
        source_records=sources,
        policy=fixture.policy,
    )
    clock[0] = NOW + timedelta(seconds=1)
    reverified = staging.reverify_signed_parent_candidate_set(
        result,
        raw,
        envelope,
        source_records=sources,
        policy=fixture.policy,
    )
    assert reverified.verified_at == NOW + timedelta(seconds=1)
    assert reverified.candidate_set_commitment == result.candidate_set_commitment


def test_compiler_has_no_parent_selector_or_trusted_verified_object_input() -> None:
    parameters = inspect.signature(
        staging.compile_signed_parent_candidate_set
    ).parameters
    assert tuple(parameters) == (
        "raw_bundle",
        "raw_finalization_envelope",
        "source_records",
        "policy",
    )
    assert not {
        "parent_id",
        "parent_ids",
        "candidate_ids",
        "child_id",
        "verified_document",
    } & set(parameters)


def test_module_has_no_runtime_database_network_or_fact_authority() -> None:
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
        "parent_of",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "authoritative=True",
        "commit_ready=True",
        "enables_writes=True",
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
            assert "parent_confirmation_staging" not in path.read_text(
                encoding="utf-8"
            ), path
