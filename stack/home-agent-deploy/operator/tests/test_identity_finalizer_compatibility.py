from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


OPERATOR_DIR = Path(__file__).parents[1]
FIXTURE_PATH = Path(__file__).with_name("test_reviewed_identity_payload.py")
FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "identity_finalizer_reviewed_payload_fixture", FIXTURE_PATH
)
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
fixture_module = importlib.util.module_from_spec(FIXTURE_SPEC)
sys.modules[FIXTURE_SPEC.name] = fixture_module
FIXTURE_SPEC.loader.exec_module(fixture_module)

MODULE_PATH = OPERATOR_DIR / "identity_finalizer_compatibility.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "identity_finalizer_compatibility", MODULE_PATH
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
compatibility = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = compatibility
MODULE_SPEC.loader.exec_module(compatibility)

verifier = fixture_module.verifier
NOW = fixture_module.NOW
RUN_ID = fixture_module.RUN_ID
PERSON_ID = fixture_module.PERSON_ID
OTHER_PERSON_ID = "22222222-2222-4222-8222-222222222222"
SNAPSHOT_ID = "00000000-0000-7000-8000-000000000a01"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def u7(value: int) -> str:
    return f"00000000-0000-7000-8000-{value:012x}"


def finalization_inputs():
    fixture = fixture_module.BundleFixture()
    raw_bundle, source_records = fixture.build()
    bundle = verifier.verify_projection_bundle(
        raw_bundle, source_records=source_records, policy=fixture.policy
    )
    proposal = bundle.finalization_proposal
    raw_envelope = verifier.canonical_bytes(
        {
            "contract_version": "reviewed-identity-finalization-envelope-v1",
            "finalization_id": RUN_ID,
            "finalization_commitment": proposal["finalization_commitment"],
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": (fixture.policy.finalization_key_fingerprint),
            "signature_scope": "reviewed-identity-migration-finalization-v1",
            "finalization_signature": fixture.finalization_private.sign(
                bundle.finalization_signing_payload(now=NOW)
            ).hex(),
        }
    )
    return fixture, raw_bundle, source_records, raw_envelope


def tombstone_row(
    person_id: str = OTHER_PERSON_ID, *, ordinal: int = 1, ledger_epoch: int = 41
) -> dict:
    return {
        "person_id": person_id,
        "block_id": u7(0xB00 + ordinal),
        "block_commitment": digest(f"block-{ordinal}"),
        "ledger_epoch": ledger_epoch,
        "ledger_outbox_id": u7(0xC00 + ordinal),
        "ledger_record_hash": digest(f"ledger-hash-{ordinal}"),
        "ledger_record_digest": digest(f"ledger-digest-{ordinal}"),
    }


def tombstone_snapshot(
    policy,
    rows: list[dict] | None = None,
    *,
    observed_at=NOW - timedelta(seconds=1),
    expires_at=NOW + timedelta(minutes=2),
    snapshot_id: str = SNAPSHOT_ID,
) -> bytes:
    rows = copy.deepcopy(rows or [])
    core = {
        "contract_version": compatibility.SNAPSHOT_CONTRACT_VERSION,
        "e2_contract_revision": compatibility.E2_CONTRACT_REVISION,
        "policy_digest": policy.policy_digest,
        "snapshot_id": snapshot_id,
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "ledger_epoch_high_watermark": max(
            (row["ledger_epoch"] for row in rows), default=0
        ),
        "rows": rows,
    }
    return verifier.canonical_bytes(
        {
            **core,
            "snapshot_commitment": verifier.keyed_commitment(
                policy.commitment_key,
                "identity-erasure-e2-tombstone-snapshot-v1",
                core,
            ),
        }
    )


def compile_with_snapshot(rows: list[dict] | None = None):
    fixture, raw_bundle, sources, envelope = finalization_inputs()
    result = compatibility.compile_identity_finalizer_compatibility(
        raw_bundle,
        envelope,
        tombstone_snapshot(fixture.policy, rows),
        source_records=sources,
        policy=fixture.policy,
    )
    return fixture, result


def projection(kind: str, record: dict, *, ordinal: int) -> dict:
    decision_id = u7(0xD00 + ordinal)
    receipt_id = u7(0xE00 + ordinal)
    table = {
        "person": "identity.people",
        "privacy_directive": "identity.privacy_directives",
        "person_status": "identity.people",
        "alias": "identity.aliases",
        "recognition_binding": "identity.external_recognition_bindings",
        "legacy_role_candidate": "identity.legacy_role_labels",
        "legacy_relationship_candidate": "identity.legacy_relationship_candidates",
    }[kind]
    projection_id_field = {
        "person": "person_id",
        "privacy_directive": "directive_id",
        "person_status": "person_id",
        "alias": "alias_id",
        "recognition_binding": "binding_id",
        "legacy_role_candidate": "label_id",
        "legacy_relationship_candidate": "candidate_id",
    }[kind]
    if kind == "legacy_relationship_candidate":
        subjects = [
            {"person_id": record["from_person_id"], "subject_role": "from"},
            {"person_id": record["to_person_id"], "subject_role": "to"},
        ]
    else:
        subjects = [{"person_id": record["person_id"], "subject_role": "primary"}]
    projection_ref = digest(f"projection-ref-{ordinal}")
    return {
        "decision_id": decision_id,
        "decision_kind": kind,
        "projection_table_kind": table,
        "projection_ref_commitment": projection_ref,
        "receipt_id": receipt_id,
        "record": record,
        "lineage": {
            "lineage_id": receipt_id,
            "run_id": RUN_ID,
            "receipt_id": receipt_id,
            "decision_id": decision_id,
            "decision_kind": kind,
            "projection_table_kind": table,
            "projection_id": record[projection_id_field],
            "projection_ref_commitment": projection_ref,
            "subjects": subjects,
            "lineage_commitment": digest(f"lineage-{ordinal}"),
        },
    }


def all_kind_document() -> dict:
    return {
        "run_id": RUN_ID,
        "projections": [
            projection("person", {"person_id": PERSON_ID}, ordinal=1),
            projection(
                "privacy_directive",
                {"directive_id": u7(0x101), "person_id": PERSON_ID},
                ordinal=2,
            ),
            projection("person_status", {"person_id": PERSON_ID}, ordinal=3),
            projection(
                "alias",
                {"alias_id": u7(0x102), "person_id": PERSON_ID},
                ordinal=4,
            ),
            projection(
                "recognition_binding",
                {"binding_id": u7(0x103), "person_id": PERSON_ID},
                ordinal=5,
            ),
            projection(
                "legacy_role_candidate",
                {"label_id": u7(0x104), "person_id": PERSON_ID},
                ordinal=6,
            ),
            projection(
                "legacy_relationship_candidate",
                {
                    "candidate_id": u7(0x105),
                    "from_person_id": PERSON_ID,
                    "to_person_id": OTHER_PERSON_ID,
                },
                ordinal=7,
            ),
        ],
    }


def test_valid_signed_inputs_compile_only_coverage_unproven_intent() -> None:
    fixture, raw_bundle, sources, envelope = finalization_inputs()
    snapshot = tombstone_snapshot(fixture.policy)
    first = compatibility.compile_identity_finalizer_compatibility(
        raw_bundle,
        envelope,
        snapshot,
        source_records=sources,
        policy=fixture.policy,
    )
    second = compatibility.compile_identity_finalizer_compatibility(
        raw_bundle,
        envelope,
        snapshot,
        source_records=sources,
        policy=fixture.policy,
    )

    assert (
        first.compatibility_intent_commitment == second.compatibility_intent_commitment
    )
    assert first.affected_person_count == 1
    assert first.tombstone_count == 0
    assert first.ledger_epoch_high_watermark == 0
    assert first.compatibility_status == "coverage_unproven"
    assert first.observed_snapshot_disjoint is True
    assert first.deployment_status == "non_deployable"
    assert first.capability_status == "capability_disabled"
    for safety_field in (
        "authoritative",
        "commit_ready",
        "enables_writes",
        "atomic_commit_enforced",
        "snapshot_completeness_proven",
        "database_state_current",
        "transaction_lock_enforced",
    ):
        assert getattr(first, safety_field) is False
    rendered = first.canonical_audit_payload().decode("utf-8")
    assert "Marcelo" not in rendered
    assert "legacy-identity-store" not in rendered
    assert PERSON_ID not in rendered
    assert repr(fixture.commitment_key) not in repr(first)


def test_nonempty_exact_snapshot_is_bound_but_never_claims_coverage() -> None:
    _, result = compile_with_snapshot([tombstone_row()])
    assert result.tombstone_count == 1
    assert result.ledger_epoch_high_watermark == 41
    assert result.snapshot_commitment != result.tombstone_set_digest
    assert result.compatibility_status == "coverage_unproven"
    assert result.snapshot_completeness_proven is False


def test_blocked_projection_subject_rejects_without_private_content() -> None:
    fixture, raw_bundle, sources, envelope = finalization_inputs()
    with pytest.raises(
        verifier.VerificationError, match="^identity_erasure_blocked$"
    ) as caught:
        compatibility.compile_identity_finalizer_compatibility(
            raw_bundle,
            envelope,
            tombstone_snapshot(fixture.policy, [tombstone_row(PERSON_ID)]),
            source_records=sources,
            policy=fixture.policy,
        )
    rendered = str(caught.value)
    assert PERSON_ID not in rendered
    assert "Marcelo" not in rendered


def test_every_projection_kind_contributes_exact_lineage_subjects() -> None:
    document = all_kind_document()
    affected = compatibility._affected_person_ids(document)
    assert affected == tuple(sorted((PERSON_ID, OTHER_PERSON_ID)))

    reversed_document = {
        "run_id": RUN_ID,
        "projections": list(reversed(document["projections"])),
    }
    assert compatibility._affected_person_ids(reversed_document) == affected


def test_either_relationship_endpoint_blocks_the_whole_subject_set() -> None:
    relationship = all_kind_document()["projections"][-1]
    affected = compatibility._affected_person_ids(
        {"run_id": RUN_ID, "projections": [relationship]}
    )
    for endpoint in (PERSON_ID, OTHER_PERSON_ID):
        with pytest.raises(
            verifier.VerificationError, match="identity_erasure_blocked"
        ):
            compatibility._reject_erasure_overlap(affected, frozenset({endpoint}))


def test_lineage_must_be_same_run_and_match_projection_tuple() -> None:
    original = all_kind_document()
    mutations = []
    for field_name, replacement in (
        ("run_id", u7(0x999)),
        ("lineage_id", u7(0x998)),
        ("decision_id", u7(0x997)),
        ("decision_kind", "alias"),
        ("projection_id", u7(0x996)),
        ("projection_ref_commitment", digest("wrong-ref")),
    ):
        changed = copy.deepcopy(original)
        changed["projections"][0]["lineage"][field_name] = replacement
        mutations.append(changed)
    for changed in mutations:
        with pytest.raises(verifier.VerificationError, match="lineage"):
            compatibility._affected_person_ids(changed)


def test_snapshot_rows_are_an_exact_canonical_current_set() -> None:
    fixture, _, _, _ = finalization_inputs()
    first = tombstone_row(PERSON_ID, ordinal=1, ledger_epoch=41)
    second = tombstone_row(OTHER_PERSON_ID, ordinal=2, ledger_epoch=42)
    valid_rows = sorted((first, second), key=lambda row: row["person_id"])
    compatibility._parse_tombstone_snapshot(
        tombstone_snapshot(fixture.policy, valid_rows), policy=fixture.policy
    )

    malformed_snapshots = []
    malformed_snapshots.append(
        tombstone_snapshot(fixture.policy, list(reversed(valid_rows)))
    )
    malformed_snapshots.append(tombstone_snapshot(fixture.policy, [first, first]))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["e2_contract_revision"] = "0011_identity_erasure_schema"
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["rows"][0]["block_id"] = malformed["rows"][0]["block_id"].upper()
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["rows"][0]["block_commitment"] = "not-a-commitment"
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["ledger_epoch_high_watermark"] = 999
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["snapshot_commitment"] = digest("tampered-snapshot")
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed = json.loads(tombstone_snapshot(fixture.policy, [first]))
    malformed["unexpected"] = True
    malformed_snapshots.append(verifier.canonical_bytes(malformed))
    malformed_snapshots.append(
        tombstone_snapshot(
            fixture.policy,
            [first],
            observed_at=NOW - timedelta(minutes=10),
            expires_at=NOW - timedelta(minutes=5),
        )
    )

    for raw in malformed_snapshots:
        with pytest.raises(verifier.VerificationError):
            compatibility._parse_tombstone_snapshot(raw, policy=fixture.policy)


def test_tampered_review_or_finalization_signature_fails_existing_verifier() -> None:
    fixture, raw_bundle, sources, envelope = finalization_inputs()
    snapshot = tombstone_snapshot(fixture.policy)

    tampered_bundle = json.loads(raw_bundle)
    tampered_bundle["projections"][0]["record"]["display_name"] = "CANARY NAME"
    with pytest.raises(verifier.VerificationError):
        compatibility.compile_identity_finalizer_compatibility(
            verifier.canonical_bytes(tampered_bundle),
            envelope,
            snapshot,
            source_records=sources,
            policy=fixture.policy,
        )

    tampered_envelope = json.loads(envelope)
    tampered_envelope["finalization_signature"] = "0" * 128
    with pytest.raises(verifier.VerificationError, match="signature"):
        compatibility.compile_identity_finalizer_compatibility(
            raw_bundle,
            verifier.canonical_bytes(tampered_envelope),
            snapshot,
            source_records=sources,
            policy=fixture.policy,
        )


def test_expired_first_admission_is_rejected() -> None:
    fixture, raw_bundle, sources, envelope = finalization_inputs()
    expired_policy = replace(fixture.policy, now=lambda: NOW + timedelta(minutes=6))
    with pytest.raises(verifier.VerificationError, match="expired"):
        compatibility.compile_identity_finalizer_compatibility(
            raw_bundle,
            envelope,
            tombstone_snapshot(
                expired_policy,
                observed_at=NOW + timedelta(minutes=5),
                expires_at=NOW + timedelta(minutes=7),
            ),
            source_records=sources,
            policy=expired_policy,
        )


def test_locked_safety_fields_cannot_be_enabled_with_dataclass_replace() -> None:
    _, result = compile_with_snapshot([])
    locked = {
        "contract_version": "unsafe",
        "e2_contract_revision": "unsafe",
        "compatibility_status": "compatible",
        "deployment_status": "deployable",
        "capability_status": "enabled",
        "observed_snapshot_disjoint": False,
        "authoritative": True,
        "commit_ready": True,
        "enables_writes": True,
        "atomic_commit_enforced": True,
        "snapshot_completeness_proven": True,
        "database_state_current": True,
        "transaction_lock_enforced": True,
    }
    init_fields = {item.name for item in fields(result) if item.init}
    assert set(locked).isdisjoint(init_fields)
    for name, value in locked.items():
        with pytest.raises(ValueError):
            replace(result, **{name: value})


def test_module_has_no_production_import_or_route() -> None:
    repository = Path(__file__).resolve().parents[4]
    compose = (repository / "stack/home-agent-compose.yml").read_text(encoding="utf-8")
    assert "./home-agent-deploy/operator:" not in compose
    assert "/operator:ro" not in compose
    roots = (
        repository / "stack/services/home-agent-core/app",
        repository / "stack/services/home-agent-bff",
        repository / "app/src",
        repository / "app/src-tauri/src",
        repository / "web-gateway",
        repository / "ha-config/home_agent_edge",
    )
    inspected = 0
    for root in roots:
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
            if any(
                part.casefold() in {"test", "tests", "__tests__"} for part in path.parts
            ):
                continue
            source = path.read_text(encoding="utf-8")
            inspected += 1
            assert "identity_finalizer_compatibility" not in source, path
            assert "identity-finalizer-erasure-compatibility" not in source, path
            if path.suffix == ".py":
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        assert all(
                            not alias.name.endswith("identity_finalizer_compatibility")
                            for alias in node.names
                        ), path
                    if isinstance(node, ast.ImportFrom):
                        assert not (
                            (node.module or "").endswith(
                                "identity_finalizer_compatibility"
                            )
                            or any(
                                alias.name == "identity_finalizer_compatibility"
                                for alias in node.names
                            )
                        ), path
    assert inspected


def test_module_source_has_no_runtime_io_or_mutation_client() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    imported_roots = set()
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)

    assert imported_roots.isdisjoint(
        {
            "asyncpg",
            "docker",
            "httpx",
            "pathlib",
            "psycopg",
            "requests",
            "socket",
            "sqlalchemy",
            "subprocess",
            "urllib",
        }
    )
    assert called_names.isdisjoint(
        {
            "connect",
            "execute",
            "open",
            "Popen",
            "request",
            "run",
            "system",
            "write_bytes",
            "write_text",
        }
    )
