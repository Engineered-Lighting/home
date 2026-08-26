from __future__ import annotations

import asyncio
import ast
import base64
from collections.abc import Callable
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import identity_admission_writer, schema
from app.ids import uuid7


ROOT = Path(__file__).resolve().parents[1]
OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_FINALIZER_E3_OWNER_DATABASE_URL"
ADMIN_DATABASE_ENV = "TEST_PHASE3_IDENTITY_FINALIZER_E3_ADMIN_DATABASE_URL"
FINALIZER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_FINALIZER_E3_FINALIZER_DATABASE_URL"
ERASURE_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E2_ERASURE_DATABASE_URL"
FINALIZER_ROLE = "home_agent_identity_finalizer"
KERNEL_ROLE = "home_agent_identity_finalizer_kernel"
REVISION_0012 = "0012_identity_erasure_e2"
REVISION_0013 = "0013_identity_finalizer_e3"
MANDATORY_RESIDUALS = (
    "live_identity_rows_retained",
    "semantic_dependencies_not_evaluated",
    "generic_artifact_lineage_unresolved",
    "external_cleanup_not_evaluated",
    "legacy_cleanup_not_evaluated",
    "backup_expiry_unverified",
    "preexisting_snapshot_visibility",
)


@dataclass(frozen=True, slots=True)
class Projection:
    kind: str
    decision_id: uuid.UUID
    receipt_id: uuid.UUID
    candidate_commitment: str
    projection_ref_commitment: str
    projection_commitment: str
    receipt_commitment: str
    table: str
    record: dict[str, Any]
    lineage: dict[str, Any]

    def document_value(self) -> dict[str, Any]:
        return {
            "candidate_commitment": self.candidate_commitment,
            "decision_id": str(self.decision_id),
            "decision_kind": self.kind,
            "lineage": self.lineage,
            "projection_commitment": self.projection_commitment,
            "projection_ref_commitment": self.projection_ref_commitment,
            "projection_table_kind": self.table,
            "receipt_commitment": self.receipt_commitment,
            "receipt_id": str(self.receipt_id),
            "record": self.record,
        }


@dataclass(frozen=True, slots=True)
class FinalizerFixture:
    run_id: uuid.UUID
    admission_id: uuid.UUID
    authorization_id: uuid.UUID
    document: bytes
    projections: tuple[Projection, ...]
    person_ids: tuple[uuid.UUID, ...]
    alias_id: uuid.UUID | None
    auto_expiry_directive_id: uuid.UUID | None
    auto_expiry_outbox_id: uuid.UUID | None


def _required_environment() -> bool:
    return all(
        os.getenv(name)
        for name in (
            OWNER_DATABASE_ENV,
            ADMIN_DATABASE_ENV,
            FINALIZER_DATABASE_ENV,
            ERASURE_DATABASE_ENV,
        )
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signature(value: str) -> str:
    return hashlib.sha512(value.encode("utf-8")).hexdigest()


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def _safe_identity_error_code(error: DBAPIError) -> str:
    migration_source = (
        ROOT / "alembic/versions/0013_identity_finalizer_kernel.py"
    ).read_text(encoding="utf-8")
    known_codes = set(re.findall(r"\bidentity_[a-z0-9_]+\b", migration_source))
    error_codes = re.findall(r"\bidentity_[a-z0-9_]+\b", str(error.orig))
    return next((code for code in error_codes if code in known_codes), "unavailable")


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _migration_literal(name: str) -> str:
    source = (ROOT / "alembic/versions/0013_identity_finalizer_kernel.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            continue
        assert isinstance(statement.value, ast.Constant)
        assert isinstance(statement.value.value, str)
        return statement.value.value
    raise AssertionError(f"0013 {name} is not a string literal")


def _run_alembic(
    database_url: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["HOME_AGENT_DATABASE_URL"] = database_url
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def _table_for_kind(kind: str) -> str:
    return {
        "person": "identity.people",
        "person_status": "identity.people",
        "alias": "identity.aliases",
        "recognition_binding": "identity.external_recognition_bindings",
        "privacy_directive": "identity.privacy_directives",
        "legacy_role_candidate": "identity.legacy_role_labels",
        "legacy_relationship_candidate": ("identity.legacy_relationship_candidates"),
    }[kind]


def _source_table_for_kind(kind: str) -> str:
    return {
        "person": "identities",
        "person_status": "identities",
        "alias": "identity_aliases",
        "recognition_binding": "enrollments",
        "privacy_directive": "identities",
        "legacy_role_candidate": "relationships",
        "legacy_relationship_candidate": "relationships",
    }[kind]


def _projection(
    *,
    label: str,
    run_id: uuid.UUID,
    kind: str,
    record: dict[str, Any],
    person_id: uuid.UUID | None = None,
    from_person_id: uuid.UUID | None = None,
    to_person_id: uuid.UUID | None = None,
    decision_id: uuid.UUID | None = None,
) -> Projection:
    target_decision_id = decision_id or uuid7()
    receipt_id = uuid7()
    if kind in {"person", "person_status"}:
        assert person_id is not None
        projection_id = person_id
    elif kind == "alias":
        projection_id = uuid.UUID(str(record["alias_id"]))
    elif kind == "recognition_binding":
        projection_id = uuid.UUID(str(record["binding_id"]))
    elif kind == "privacy_directive":
        projection_id = uuid.UUID(str(record["directive_id"]))
    elif kind == "legacy_role_candidate":
        projection_id = uuid.UUID(str(record["label_id"]))
    else:
        projection_id = uuid.UUID(str(record["candidate_id"]))

    if kind == "legacy_relationship_candidate":
        assert from_person_id is not None
        assert to_person_id is not None
        subjects = [
            {"person_id": str(from_person_id), "subject_role": "from"},
            {"person_id": str(to_person_id), "subject_role": "to"},
        ]
    else:
        assert person_id is not None
        subjects = [{"person_id": str(person_id), "subject_role": "primary"}]

    projection_ref = _digest(f"{label}:projection-ref")
    lineage = {
        "decision_id": str(target_decision_id),
        "decision_kind": kind,
        "lineage_commitment": _digest(f"{label}:lineage"),
        "lineage_id": str(receipt_id),
        "projection_id": str(projection_id),
        "projection_ref_commitment": projection_ref,
        "projection_table_kind": _table_for_kind(kind),
        "receipt_id": str(receipt_id),
        "run_id": str(run_id),
        "subjects": subjects,
    }
    return Projection(
        kind=kind,
        decision_id=target_decision_id,
        receipt_id=receipt_id,
        candidate_commitment=_digest(f"{label}:candidate"),
        projection_ref_commitment=projection_ref,
        projection_commitment=_digest(f"{label}:projection"),
        receipt_commitment=_digest(f"{label}:receipt"),
        table=_table_for_kind(kind),
        record=record,
        lineage=lineage,
    )


def _minimal_projections(
    *, label: str, run_id: uuid.UUID
) -> tuple[list[Projection], list[uuid.UUID]]:
    person_id = uuid7()
    person = _projection(
        label=f"{label}:person",
        run_id=run_id,
        kind="person",
        person_id=person_id,
        record={
            "display_name": f"E3 candidate {label}",
            "legacy_source_ref": f"legacy://identity/{label}",
            "legacy_source_sha256": _digest(f"{label}:person-source"),
            "legacy_source_version": 1,
            "person_id": str(person_id),
            "privacy_scope": "private",
            "pronouns": None,
        },
    )
    return [person], [person_id]


def _parent_authority_projections(
    *, label: str, run_id: uuid.UUID
) -> tuple[list[Projection], list[uuid.UUID]]:
    child_id, parent_0_id, parent_1_id = uuid7(), uuid7(), uuid7()
    projections: list[Projection] = []
    for role, person_id, display_name in (
        ("child", child_id, f"E3 child {label}"),
        ("parent-0", parent_0_id, f"E3 parent A {label}"),
        ("parent-1", parent_1_id, f"E3 parent B {label}"),
    ):
        projections.append(
            _projection(
                label=f"{label}:person:{role}",
                run_id=run_id,
                kind="person",
                person_id=person_id,
                record={
                    "display_name": display_name,
                    "legacy_source_ref": (
                        f"legacy://identity/{label}/{role}"
                    ),
                    "legacy_source_sha256": _digest(
                        f"{label}:person-source:{role}"
                    ),
                    "legacy_source_version": 1,
                    "person_id": str(person_id),
                    "privacy_scope": "private",
                    "pronouns": None,
                },
            )
        )

    for ordinal, parent_id in enumerate((parent_0_id, parent_1_id)):
        label_id = uuid7()
        projections.append(
            _projection(
                label=f"{label}:legacy-role:parent-{ordinal}",
                run_id=run_id,
                kind="legacy_role_candidate",
                person_id=parent_id,
                decision_id=label_id,
                record={
                    "label_id": str(label_id),
                    "perspective": "unknown",
                    "person_id": str(parent_id),
                    "role_label": "parent",
                    "source_ref": (
                        f"legacy://role/{label}/parent-{ordinal}"
                    ),
                    "source_snapshot_sha256": _digest(
                        f"{label}:role-source:parent-{ordinal}"
                    ),
                    "source_version": None,
                },
            )
        )

    return projections, [child_id, parent_0_id, parent_1_id]


def _comprehensive_projections(
    *,
    label: str,
    run_id: uuid.UUID,
    auto_expiry_at: datetime,
) -> tuple[list[Projection], list[uuid.UUID]]:
    primary_id, ignored_id, relation_id = uuid7(), uuid7(), uuid7()
    projections: list[Projection] = []
    for name, person_id, display_name in (
        ("primary", primary_id, f"E3 primary {label}"),
        ("ignored", ignored_id, "Suppressed legacy identity"),
        ("relation", relation_id, f"E3 relation {label}"),
    ):
        projections.append(
            _projection(
                label=f"{label}:person:{name}",
                run_id=run_id,
                kind="person",
                person_id=person_id,
                record={
                    "display_name": display_name,
                    "legacy_source_ref": f"legacy://identity/{label}/{name}",
                    "legacy_source_sha256": _digest(f"{label}:person-source:{name}"),
                    "legacy_source_version": 1,
                    "person_id": str(person_id),
                    "privacy_scope": "private",
                    "pronouns": None,
                },
            )
        )

    projections.append(
        _projection(
            label=f"{label}:status",
            run_id=run_id,
            kind="person_status",
            person_id=primary_id,
            record={
                "person_id": str(primary_id),
                "source_ref": f"legacy://identity/{label}/status",
                "source_snapshot_sha256": _digest(f"{label}:status-source"),
                "source_version": 2,
                "status": "archived",
            },
        )
    )

    alias_id = uuid7()
    projections.append(
        _projection(
            label=f"{label}:alias",
            run_id=run_id,
            kind="alias",
            person_id=primary_id,
            decision_id=alias_id,
            record={
                "alias": f"E3 nickname {label}",
                "alias_id": str(alias_id),
                "alias_kind": "nickname",
                "normalized_alias": f"e3-{label}-nickname",
                "person_id": str(primary_id),
                "source_ref": f"legacy://alias/{label}",
                "source_snapshot_sha256": _digest(f"{label}:alias-source"),
                "source_version": 1,
            },
        )
    )

    binding_id = uuid7()
    projections.append(
        _projection(
            label=f"{label}:recognition",
            run_id=run_id,
            kind="recognition_binding",
            person_id=primary_id,
            decision_id=binding_id,
            record={
                "binding_id": str(binding_id),
                "external_id": f"e3-{label}",
                "external_system": "frigate",
                "person_id": str(primary_id),
                "source_ref": f"legacy://enrollment/{label}",
                "source_snapshot_sha256": _digest(f"{label}:recognition-source"),
                "source_version": 1,
                "status": "active",
            },
        )
    )

    private_id, auto_expiry_id, ignored_directive_id = uuid7(), uuid7(), uuid7()
    for directive_id, person_id, directive, expires_at in (
        (private_id, primary_id, "private", None),
        (
            auto_expiry_id,
            primary_id,
            "auto_expire",
            auto_expiry_at.isoformat(),
        ),
        (ignored_directive_id, ignored_id, "ignored", None),
    ):
        projections.append(
            _projection(
                label=f"{label}:directive:{directive}",
                run_id=run_id,
                kind="privacy_directive",
                person_id=person_id,
                decision_id=directive_id,
                record={
                    "directive": directive,
                    "directive_id": str(directive_id),
                    "enabled": True,
                    "expires_at": expires_at,
                    "person_id": str(person_id),
                    "source_ref": f"legacy://privacy/{label}/{directive}",
                    "source_snapshot_sha256": _digest(
                        f"{label}:privacy-source:{directive}"
                    ),
                    "source_version": 1,
                },
            )
        )

    label_id = uuid7()
    projections.append(
        _projection(
            label=f"{label}:legacy-role",
            run_id=run_id,
            kind="legacy_role_candidate",
            person_id=primary_id,
            decision_id=label_id,
            record={
                "label_id": str(label_id),
                "perspective": "unknown",
                "person_id": str(primary_id),
                "role_label": "parent",
                "source_ref": f"legacy://role/{label}",
                "source_snapshot_sha256": _digest(f"{label}:role-source"),
                "source_version": None,
            },
        )
    )

    candidate_id = uuid7()
    projections.append(
        _projection(
            label=f"{label}:legacy-relationship",
            run_id=run_id,
            kind="legacy_relationship_candidate",
            decision_id=candidate_id,
            from_person_id=primary_id,
            to_person_id=relation_id,
            record={
                "authoritative": False,
                "candidate_id": str(candidate_id),
                "from_person_id": str(primary_id),
                "perspective": "unknown",
                "relationship_label": "parent",
                "relationship_status": "active",
                "source_ref": f"legacy://relationship/{label}",
                "source_snapshot_sha256": _digest(f"{label}:relationship-source"),
                "to_person_id": str(relation_id),
            },
        )
    )
    return projections, [primary_id, ignored_id, relation_id]


async def _seed_fixture(
    connection,
    *,
    label: str,
    comprehensive: bool = False,
    parent_authority: bool = False,
    review_signature_override: str | None = None,
    raw_document_override: bytes | None = None,
    document_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> FinalizerFixture:
    now = datetime.now(UTC)
    run_id = uuid7()
    admission_id = uuid7()
    authorization_id = uuid7()
    review_expires_at = now + timedelta(minutes=10)
    auto_expiry_at = now + timedelta(hours=1)

    if comprehensive and parent_authority:
        raise ValueError(
            "comprehensive and parent_authority fixtures are exclusive"
        )
    if comprehensive:
        projections, person_ids = _comprehensive_projections(
            label=label,
            run_id=run_id,
            auto_expiry_at=auto_expiry_at,
        )
    elif parent_authority:
        projections, person_ids = _parent_authority_projections(
            label=label,
            run_id=run_id,
        )
    else:
        projections, person_ids = _minimal_projections(
            label=label,
            run_id=run_id,
        )

    source_manifest = _digest(f"{label}:source-manifest")
    projection_manifest = _digest(f"{label}:projection-manifest")
    decision_manifest = _digest(f"{label}:decision-manifest")
    receipt_set = _digest(f"{label}:receipt-set")
    lineage_set = _digest(f"{label}:lineage-set")
    privacy_set = _digest(f"{label}:privacy-set")
    auto_expiry_set = _digest(f"{label}:auto-expiry-set")
    finalization_commitment = _digest(f"{label}:finalization")
    review_receipt = _digest(f"{label}:review-receipt")
    release_digest = _digest(f"{label}:release")
    tool_digest = _digest(f"{label}:tool")
    oci_digest = _digest(f"{label}:oci")
    schema_digest = _digest(f"{label}:schema")
    capability_digest = _digest(f"{label}:capability")
    policy_digest = _digest(f"{label}:policy")
    review_key = _digest(f"{label}:review-key")
    finalization_key = _digest(f"{label}:finalization-key")
    run_review_signature = _signature(f"{label}:review-signature")

    await connection.execute(
        insert(schema.rollout_authorizations).values(
            authorization_id=authorization_id,
            operator_request_id=uuid7(),
            from_mode="record_only",
            to_mode="shadow",
            rule_version="record-only-envelope-worker-gate-v3",
            policy_version="home-agent-mvp-v1",
            policy_digest=policy_digest,
            input_digest=_digest(f"{label}:rollout-input"),
            worker_instance_id=uuid.uuid4(),
            worker_success_sequence=1,
            worker_kernel_version="worker-maintenance-cycle-v1",
            worker_maintenance_succeeded_at=now - timedelta(minutes=2),
            worker_proof_digest=_digest(f"{label}:worker-proof"),
            readiness_evaluated_at=now - timedelta(minutes=1),
            authorized_at=now,
        )
    )
    await connection.execute(
        insert(schema.reviewed_identity_migration_runs).values(
            run_id=run_id,
            operator_request_id=uuid7(),
            contract_version="reviewed-identity-migration-run-v1",
            source_schema_version=1,
            source_projection_contract_version=("legacy-identity-source-projection-v1"),
            importer_version="legacy-identity-importer-v1",
            canonicalization_version="identity-canonicalization-v1",
            projection_version="semantic-people-projection-v1",
            shadow_rule_version="record-only-envelope-worker-gate-v3",
            commitment_algorithm="hmac-sha256-v1",
            commitment_key_fingerprint=_digest(f"{label}:commitment-key"),
            commitment_key_epoch=1,
            source_item_count=len(projections),
            decision_count=len(projections),
            logical_source_manifest_commitment=source_manifest,
            projection_manifest_commitment=projection_manifest,
            source_projection_contract_digest=_digest(f"{label}:contract"),
            review_receipt_commitment=review_receipt,
            policy_version="home-agent-mvp-v1",
            policy_digest=policy_digest,
            shadow_authorization_id=authorization_id,
            release_manifest_digest=release_digest,
            migration_tool_bundle_digest=tool_digest,
            core_oci_manifest_digest=oci_digest,
            core_schema_digest=schema_digest,
            core_capability_digest=capability_digest,
            signature_algorithm="ed25519",
            signing_key_fingerprint=review_key,
            review_signature=run_review_signature,
            expires_at=review_expires_at,
        )
    )

    source_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for ordinal, projection in enumerate(projections):
        source_item_id = uuid7()
        source_rows.append(
            {
                "source_item_id": source_item_id,
                "run_id": run_id,
                "ordinal": ordinal,
                "source_table_kind": _source_table_for_kind(projection.kind),
                "row_key_commitment": _digest(f"{label}:row:{ordinal}"),
                "allowed_projection_commitment": (projection.candidate_commitment),
            }
        )
        decision_rows.append(
            {
                "decision_id": projection.decision_id,
                "run_id": run_id,
                "source_item_id": source_item_id,
                "ordinal": ordinal,
                "decision_kind": projection.kind,
                "disposition": "apply",
                "candidate_commitment": projection.candidate_commitment,
                "canonical_apply_decision_id": None,
                "canonical_apply_disposition": None,
                "decision_commitment": _digest(f"{label}:decision:{ordinal}"),
            }
        )
    await connection.execute(
        insert(schema.reviewed_identity_migration_source_items),
        source_rows,
    )
    await connection.execute(
        insert(schema.reviewed_identity_migration_decisions),
        decision_rows,
    )

    closures: list[dict[str, Any]] = []
    for person_id in person_ids:
        person_projection = next(
            projection
            for projection in projections
            if projection.kind == "person"
            and projection.record["person_id"] == str(person_id)
        )
        directives = [
            projection
            for projection in projections
            if projection.kind == "privacy_directive"
            and projection.record["person_id"] == str(person_id)
        ]
        closures.append(
            {
                "directives": [
                    {
                        "decision_id": str(projection.decision_id),
                        "directive": projection.record["directive"],
                        "directive_id": projection.record["directive_id"],
                        "enabled": True,
                        "expires_at": projection.record["expires_at"],
                        "projection_ref_commitment": (
                            projection.projection_ref_commitment
                        ),
                        "receipt_id": str(projection.receipt_id),
                    }
                    for projection in directives
                ],
                "do_not_track": any(
                    projection.record["directive"] == "do_not_track"
                    for projection in directives
                ),
                "ignored": any(
                    projection.record["directive"] == "ignored"
                    for projection in directives
                ),
                "person_decision_id": str(person_projection.decision_id),
                "person_id": str(person_id),
                "person_receipt_id": str(person_projection.receipt_id),
                "privacy_closure_commitment": _digest(
                    f"{label}:privacy-closure:{person_id}"
                ),
            }
        )

    effects: list[dict[str, Any]] = []
    for projection in projections:
        if (
            projection.kind != "privacy_directive"
            or projection.record["directive"] != "auto_expire"
        ):
            continue
        effects.append(
            {
                "directive_id": projection.record["directive_id"],
                "due_at": projection.record["expires_at"],
                "effect_commitment": _digest(f"{label}:auto-expiry-effect"),
                "outbox_id": str(projection.receipt_id),
                "person_id": projection.record["person_id"],
                "schedule_id": projection.record["directive_id"],
                "source_receipt_commitment": projection.receipt_commitment,
                "topic": "privacy.person.auto_expire",
            }
        )

    document = {
        "apply_decision_count": len(projections),
        "authoritative": False,
        "auto_expiry_effect_set_commitment": auto_expiry_set,
        "auto_expiry_effects": effects,
        "contract_version": "reviewed-identity-migration-finalization-v1",
        "decision_count": len(projections),
        "decision_manifest_commitment": decision_manifest,
        "finalization_attestation": {
            "finalization_signature": _signature(f"{label}:finalization-signature"),
            "signature_algorithm": "ed25519",
            "signature_scope": "reviewed-identity-migration-finalization-v1",
            "signing_key_fingerprint": finalization_key,
        },
        "finalization_commitment": finalization_commitment,
        "finalization_id": str(run_id),
        "finalization_signing": {
            "signature_algorithm": "ed25519",
            "signature_scope": "reviewed-identity-migration-finalization-v1",
            "signing_key_fingerprint": finalization_key,
        },
        "lineage_set_commitment": lineage_set,
        "privacy_closure_set_commitment": privacy_set,
        "privacy_closures": closures,
        "projections": [projection.document_value() for projection in projections],
        "projection_manifest_commitment": projection_manifest,
        "receipt_count": len(projections),
        "receipt_set_commitment": receipt_set,
        "review_attestation": {
            "review_signature": (review_signature_override or run_review_signature),
            "signature_algorithm": "ed25519",
            "signature_scope": "reviewed-identity-migration-review-v1",
            "signing_key_fingerprint": review_key,
        },
        "review_expires_at": review_expires_at.isoformat(),
        "review_receipt_commitment": review_receipt,
        "run_id": str(run_id),
        "source_item_count": len(projections),
        "source_manifest_commitment": source_manifest,
        "verification_status": "candidate_unverified",
    }
    if document_mutator is not None:
        document_mutator(document)
    document_bytes = raw_document_override or _canonical_bytes(document)

    await connection.execute(
        insert(schema.reviewed_identity_finalizer_admissions).values(
            admission_id=admission_id,
            run_id=run_id,
            finalization_id=run_id,
            contract_version="reviewed-identity-finalizer-admission-v1",
            verification_status="offline_verified",
            document_sha256=hashlib.sha256(document_bytes).hexdigest(),
            document_octets=len(document_bytes),
            finalization_commitment=finalization_commitment,
            decision_manifest_commitment=decision_manifest,
            receipt_set_commitment=receipt_set,
            lineage_set_commitment=lineage_set,
            privacy_closure_set_commitment=privacy_set,
            auto_expiry_effect_set_commitment=auto_expiry_set,
            review_receipt_commitment=review_receipt,
            release_manifest_digest=release_digest,
            migration_tool_bundle_digest=tool_digest,
            core_oci_manifest_digest=oci_digest,
            core_schema_digest=schema_digest,
            core_capability_digest=capability_digest,
            policy_digest=policy_digest,
            review_signing_key_fingerprint=review_key,
            finalization_signing_key_fingerprint=finalization_key,
            verifier_bundle_digest=_migration_literal("VERIFIER_BUNDLE_DIGEST"),
            expires_at=now + timedelta(minutes=5),
        )
    )

    alias_id = next(
        (
            uuid.UUID(str(projection.record["alias_id"]))
            for projection in projections
            if projection.kind == "alias"
        ),
        None,
    )
    auto_projection = next(
        (
            projection
            for projection in projections
            if projection.kind == "privacy_directive"
            and projection.record["directive"] == "auto_expire"
        ),
        None,
    )
    return FinalizerFixture(
        run_id=run_id,
        admission_id=admission_id,
        authorization_id=authorization_id,
        document=document_bytes,
        projections=tuple(projections),
        person_ids=tuple(person_ids),
        alias_id=alias_id,
        auto_expiry_directive_id=(
            uuid.UUID(str(auto_projection.record["directive_id"]))
            if auto_projection
            else None
        ),
        auto_expiry_outbox_id=(auto_projection.receipt_id if auto_projection else None),
    )


def _erasure_payload(person_id: uuid.UUID, label: str) -> dict[str, Any]:
    block_id = uuid7()
    record_digest = _digest(f"{label}:ledger-record")
    completed_at = datetime.now(UTC) - timedelta(seconds=1)
    return {
        "version": 2,
        "subject_kind": "identity_person",
        "outbox_id": str(uuid7()),
        "erasure_request_id": str(uuid7()),
        "person_id": str(person_id),
        "operation_id": str(uuid7()),
        "block_id": str(block_id),
        "block_commitment": _digest(f"{label}:block"),
        "outcome_code": "retrieval_block_active",
        "operation_codes": ["activate_identity_person_retrieval_block"],
        "policy_digest": _digest(f"{label}:erasure-policy"),
        "completed_at": completed_at.isoformat(),
        "source_created_at": (completed_at - timedelta(seconds=1)).isoformat(),
        "external_pending_codes": [],
        "legacy_untracked_codes": [],
        "checkpoint_affected": True,
        "backup_expiry_at": None,
        "exact_residual_codes": list(MANDATORY_RESIDUALS),
        "ledger_epoch": int.from_bytes(
            hashlib.sha256(label.encode()).digest()[:7], "big"
        )
        + 1,
        "ledger_record_hash": _digest(f"{label}:ledger-hash"),
        "ledger_record_digest": record_digest,
        "residual_commitments": {
            code: _digest(f"identity-person-erasure-residual-v2:{record_digest}:{code}")
            for code in MANDATORY_RESIDUALS
        },
    }


async def _finalize(
    engine: AsyncEngine,
    fixture: FinalizerFixture,
    document: bytes | None = None,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT operations.finalize_reviewed_identity_migration("
                    "CAST(:document AS bytea), CAST(:admission AS uuid))"
                ),
                {
                    "document": document or fixture.document,
                    "admission": fixture.admission_id,
                },
            )
        ).scalar_one()


async def _probe_finalize(
    engine: AsyncEngine,
    fixture: FinalizerFixture,
    document: bytes | None = None,
) -> tuple[uuid.UUID | None, DBAPIError | None]:
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        try:
            value = (
                await connection.execute(
                    text(
                        "SELECT operations."
                        "finalize_reviewed_identity_migration("
                        "CAST(:document AS bytea), CAST(:admission AS uuid))"
                    ),
                    {
                        "document": document or fixture.document,
                        "admission": fixture.admission_id,
                    },
                )
            ).scalar_one()
        except DBAPIError as error:
            await transaction.rollback()
            return None, error
        await transaction.rollback()
        return value, None
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


async def _delete_rejected_fixture(
    connection,
    fixture: FinalizerFixture,
    rejection: DBAPIError | None,
) -> None:
    if rejection is None:
        raise AssertionError("refusing to delete a fixture without a failed probe")
    finalization_count = (
        await connection.execute(
            text(
                "SELECT count(*) FROM "
                "operations.reviewed_identity_migration_finalizations "
                "WHERE run_id=:run"
            ),
            {"run": fixture.run_id},
        )
    ).scalar_one()
    assert finalization_count == 0

    admission_ids = (
        (
            await connection.execute(
                delete(schema.reviewed_identity_finalizer_admissions)
                .where(
                    schema.reviewed_identity_finalizer_admissions.c.admission_id
                    == fixture.admission_id
                )
                .returning(
                    schema.reviewed_identity_finalizer_admissions.c.admission_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert admission_ids == [fixture.admission_id]

    decision_ids = (
        (
            await connection.execute(
                delete(schema.reviewed_identity_migration_decisions)
                .where(
                    schema.reviewed_identity_migration_decisions.c.run_id
                    == fixture.run_id
                )
                .returning(
                    schema.reviewed_identity_migration_decisions.c.decision_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(decision_ids) == {
        projection.decision_id for projection in fixture.projections
    }
    assert len(decision_ids) == len(fixture.projections)

    source_item_ids = (
        (
            await connection.execute(
                delete(schema.reviewed_identity_migration_source_items)
                .where(
                    schema.reviewed_identity_migration_source_items.c.run_id
                    == fixture.run_id
                )
                .returning(
                    schema.reviewed_identity_migration_source_items.c.source_item_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(source_item_ids) == len(fixture.projections)
    assert len(set(source_item_ids)) == len(source_item_ids)

    run_ids = (
        (
            await connection.execute(
                delete(schema.reviewed_identity_migration_runs)
                .where(
                    schema.reviewed_identity_migration_runs.c.run_id == fixture.run_id
                )
                .returning(schema.reviewed_identity_migration_runs.c.run_id)
            )
        )
        .scalars()
        .all()
    )
    assert run_ids == [fixture.run_id]

    authorization_ids = (
        (
            await connection.execute(
                delete(schema.rollout_authorizations)
                .where(
                    schema.rollout_authorizations.c.authorization_id
                    == fixture.authorization_id
                )
                .returning(schema.rollout_authorizations.c.authorization_id)
            )
        )
        .scalars()
        .all()
    )
    assert authorization_ids == [fixture.authorization_id]


async def _replay_erasure(
    connection,
    payload: dict[str, Any],
) -> uuid.UUID:
    return (
        await connection.execute(
            text(
                "SELECT privacy.replay_identity_person_retrieval_block_v2("
                "CAST(:payload AS jsonb))"
            ),
            {"payload": json.dumps(payload, separators=(",", ":"))},
        )
    ).scalar_one()


async def _set_finalizer_valid_until(
    admin: AsyncEngine,
    valid_until: datetime,
) -> None:
    value = valid_until.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S+00")
    async with admin.begin() as connection:
        await connection.execute(
            text(f"ALTER ROLE {FINALIZER_ROLE} VALID UNTIL '{value}'")
        )


async def _downgraded_e3_boundary(database_url: str) -> dict[str, bool]:
    engine = create_async_engine(
        database_url,
        pool_size=1,
        hide_parameters=True,
    )
    try:
        async with engine.begin() as connection:
            return dict(
                (
                    await connection.execute(
                        text(
                            "SELECT version_num=:revision AS exact_revision, "
                            "to_regclass('operations."
                            "reviewed_identity_finalizer_admissions') IS NULL "
                            "AS admission_absent, "
                            "to_regclass('privacy.identity_semantic_write_fence') "
                            "IS NULL AS fence_absent, "
                            "to_regprocedure('operations."
                            "finalize_reviewed_identity_migration(bytea,uuid)') "
                            "IS NULL AS finalizer_absent, "
                            "to_regprocedure('privacy."
                            "lock_identity_semantic_write_fence()') IS NULL "
                            "AS fence_function_absent, "
                            "NOT EXISTS (SELECT 1 FROM pg_policy "
                            "WHERE polname LIKE '%_e3_%') AS policies_absent, "
                            "NOT EXISTS (SELECT 1 FROM pg_trigger "
                            "WHERE tgname="
                            "'subject_retrieval_blocks_identity_write_fence' "
                            "AND NOT tgisinternal) AS trigger_absent, "
                            "NOT has_schema_privilege(:kernel, 'operations', "
                            "'USAGE') AND NOT has_schema_privilege("
                            ":kernel, 'identity', 'USAGE') AND "
                            "NOT has_schema_privilege(:kernel, 'privacy', "
                            "'USAGE') AS kernel_schema_acl_absent, "
                            "NOT has_table_privilege(:kernel, "
                            "'identity.people', "
                            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,"
                            "REFERENCES,TRIGGER') AND "
                            "NOT has_table_privilege(:kernel, "
                            "'operations.reviewed_identity_migration_runs', "
                            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,"
                            "REFERENCES,TRIGGER') AND "
                            "NOT has_column_privilege(:kernel, "
                            "'operations.reviewed_identity_migration_runs', "
                            "'expires_at', 'UPDATE') "
                            "AS kernel_table_acl_absent, "
                            "NOT has_schema_privilege(:finalizer, "
                            "'operations', 'USAGE') AS "
                            "finalizer_schema_acl_absent "
                            "FROM alembic_version"
                        ),
                        {
                            "revision": REVISION_0012,
                            "kernel": KERNEL_ROLE,
                            "finalizer": FINALIZER_ROLE,
                        },
                    )
                )
                .mappings()
                .one()
            )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not _required_environment(),
    reason=(
        "dedicated E3 owner, admin, finalizer, and E2 erasure PostgreSQL "
        "URLs are required"
    ),
)
@pytest.mark.asyncio
async def test_postgresql_e3_lifecycle_boundary_and_atomic_finalizer() -> None:
    owner_url = os.environ[OWNER_DATABASE_ENV]

    clean_downgrade = _run_alembic(
        owner_url,
        "downgrade",
        REVISION_0012,
    )
    assert clean_downgrade.returncode == 0, (
        clean_downgrade.stdout + clean_downgrade.stderr
    )
    downgraded_boundary = await _downgraded_e3_boundary(owner_url)
    clean_reupgrade = _run_alembic(owner_url, "upgrade", REVISION_0013)
    assert clean_reupgrade.returncode == 0, (
        clean_reupgrade.stdout + clean_reupgrade.stderr
    )

    owner = create_async_engine(
        owner_url,
        pool_size=2,
        max_overflow=0,
        hide_parameters=True,
    )
    admin = create_async_engine(
        os.environ[ADMIN_DATABASE_ENV],
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
    )
    admin_target_url = make_url(os.environ[ADMIN_DATABASE_ENV]).set(
        database=make_url(owner_url).database
    )
    admin_target = create_async_engine(
        admin_target_url,
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
    )
    erasure = create_async_engine(
        os.environ[ERASURE_DATABASE_ENV],
        pool_size=2,
        max_overflow=0,
        hide_parameters=True,
    )
    finalizer: AsyncEngine | None = None
    review_signature_rejected = False
    fence_serialized = False
    try:
        async with admin.begin() as connection:
            dormant = (
                (
                    await connection.execute(
                        text(
                            "SELECT rolcanlogin, NOT rolinherit AS noinherit, "
                            "NOT rolsuper AS nosuper, "
                            "NOT rolbypassrls AS nobypassrls, "
                            "rolconnlimit, rolvaliduntil IS NOT NULL "
                            "AND rolvaliduntil <= clock_timestamp() AS expired, "
                            "NOT EXISTS (SELECT 1 FROM pg_auth_members member "
                            "WHERE member.member=role.oid "
                            "OR member.roleid=role.oid) AS no_membership "
                            "FROM pg_roles role WHERE rolname=:role"
                        ),
                        {"role": FINALIZER_ROLE},
                    )
                )
                .mappings()
                .one()
            )
            assert dormant == {
                "rolcanlogin": True,
                "noinherit": True,
                "nosuper": True,
                "nobypassrls": True,
                "rolconnlimit": 1,
                "expired": True,
                "no_membership": True,
            }

        async with owner.begin() as connection:
            catalog = (
                (
                    await connection.execute(
                        text(
                            "SELECT pg_get_userbyid(proowner)=:kernel "
                            "AS kernel_owned, prosecdef, "
                            "proconfig=ARRAY['search_path=pg_catalog, pg_temp'] "
                            "AS fixed_path, "
                            "encode(sha256(convert_to(prosrc, 'UTF8')), 'hex') "
                            "AS body_sha256, "
                            "has_function_privilege(:finalizer, function.oid, "
                            "'EXECUTE') AS finalizer_execute, "
                            "NOT EXISTS (SELECT 1 FROM aclexplode("
                            "coalesce(function.proacl, '{}'::aclitem[])) acl "
                            "JOIN pg_roles role ON role.oid=acl.grantee "
                            "WHERE role.rolname=:kernel "
                            "AND acl.privilege_type='EXECUTE') "
                            "AS kernel_no_explicit_execute, "
                            "has_column_privilege(:kernel, 'operations."
                            "reviewed_identity_migration_runs', 'expires_at', "
                            "'UPDATE') AS migration_run_lock_column, "
                            "NOT has_table_privilege(:kernel, 'operations."
                            "reviewed_identity_migration_runs', 'UPDATE') "
                            "AS migration_run_no_table_update, "
                            "EXISTS (SELECT 1 FROM pg_policy lock_policy "
                            "JOIN pg_policy select_policy ON "
                            "select_policy.polrelid=lock_policy.polrelid "
                            "AND select_policy.polname="
                            "'identity_finalizer_e3_select' "
                            "JOIN pg_roles policy_role ON "
                            "lock_policy.polroles=ARRAY[policy_role.oid]::oid[] "
                            "WHERE policy_role.rolname=:kernel "
                            "AND lock_policy.polrelid='operations."
                            "reviewed_identity_migration_runs'::regclass "
                            "AND lock_policy.polname='identity_finalizer_e3_"
                            "migration_run_lock' "
                            "AND lock_policy.polpermissive "
                            "AND lock_policy.polcmd='w' "
                            "AND lock_policy.polqual::text="
                            "select_policy.polqual::text "
                            "AND pg_get_expr(lock_policy.polwithcheck, "
                            "lock_policy.polrelid, true)='false') "
                            "AS migration_run_lock_policy "
                            "FROM pg_proc function "
                            "WHERE function.oid='operations."
                            "finalize_reviewed_identity_migration"
                            "(bytea,uuid)'::regprocedure"
                        ),
                        {
                            "kernel": KERNEL_ROLE,
                            "finalizer": FINALIZER_ROLE,
                        },
                    )
                )
                .mappings()
                .one()
            )
            expected_body_sha256 = hashlib.sha256(
                _migration_literal("FUNCTION_BODY").encode("utf-8")
            ).hexdigest()
            assert catalog == {
                "kernel_owned": True,
                "prosecdef": True,
                "fixed_path": True,
                "body_sha256": expected_body_sha256,
                "finalizer_execute": True,
                "kernel_no_explicit_execute": True,
                "migration_run_lock_column": True,
                "migration_run_no_table_update": True,
                "migration_run_lock_policy": True,
            }

        await _set_finalizer_valid_until(
            admin,
            datetime.now(UTC) + timedelta(minutes=15),
        )
        finalizer = create_async_engine(
            os.environ[FINALIZER_DATABASE_ENV],
            isolation_level="SERIALIZABLE",
            pool_size=2,
            max_overflow=0,
            hide_parameters=True,
        )

        direct_connection = await finalizer.connect()
        direct_transaction = await direct_connection.begin()
        try:
            assert (
                await direct_connection.execute(text("SELECT session_user"))
            ).scalar_one() == FINALIZER_ROLE
            with pytest.raises(DBAPIError) as direct_read:
                await direct_connection.execute(text("SELECT 1 FROM identity.people"))
            assert _sqlstate(direct_read.value) == "42501"
        finally:
            await direct_transaction.rollback()
            await direct_connection.close()

        def null_finalization_commitment(document: dict[str, Any]) -> None:
            document["finalization_commitment"] = None

        def null_projection_field(
            kind: str,
            field: str,
        ) -> Callable[[dict[str, Any]], None]:
            def mutate(document: dict[str, Any]) -> None:
                target = next(
                    projection
                    for projection in document["projections"]
                    if projection["decision_kind"] == kind
                )
                target["record"][field] = None

            return mutate

        hostile_null_mutators: list[
            tuple[str, Callable[[dict[str, Any]], None]]
        ] = [
            ("top_finalization_commitment", null_finalization_commitment),
        ]
        for kind, field in (
            ("person", "legacy_source_ref"),
            ("person_status", "source_snapshot_sha256"),
            ("alias", "source_ref"),
            ("privacy_directive", "source_snapshot_sha256"),
        ):
            hostile_null_mutators.append(
                (f"{kind}.{field}", null_projection_field(kind, field))
            )

        for label, mutator in hostile_null_mutators:
            async with owner.begin() as connection:
                hostile_fixture = await _seed_fixture(
                    connection,
                    label=f"hostile-null-{label}-{uuid.uuid4().hex}",
                    comprehensive=True,
                    document_mutator=mutator,
                )
            _value, hostile_error = await _probe_finalize(
                finalizer,
                hostile_fixture,
            )
            assert hostile_error is not None, f"{label} JSON null was accepted"
            assert _sqlstate(hostile_error) == "22023", (
                f"{label}: {hostile_error.orig}"
            )
            async with owner.begin() as connection:
                await _delete_rejected_fixture(
                    connection,
                    hostile_fixture,
                    hostile_error,
                )

        private_canary = f"PRIVATE_FINALIZER_CANARY_{uuid.uuid4().hex}"

        def replace_review_expiry(document: dict[str, Any]) -> None:
            document["review_expires_at"] = private_canary

        async with owner.begin() as connection:
            private_fixture = await _seed_fixture(
                connection,
                label=f"private-canary-{uuid.uuid4().hex}",
                document_mutator=replace_review_expiry,
            )
        _value, private_error = await _probe_finalize(
            finalizer,
            private_fixture,
        )
        assert private_error is not None
        private_sqlstate = _sqlstate(private_error)
        assert private_sqlstate == "22023", (
            "private document bypassed the sanitized invalid-input boundary; "
            f"sqlstate={private_sqlstate}"
        )
        assert "identity_finalizer_private_document_rejected" in str(
            private_error.orig
        )
        assert private_canary not in str(private_error)
        async with owner.begin() as connection:
            await _delete_rejected_fixture(
                connection,
                private_fixture,
                private_error,
            )

        def replace_auto_expiry_with_infinity(document: dict[str, Any]) -> None:
            for projection in document["projections"]:
                if (
                    projection["decision_kind"] == "privacy_directive"
                    and projection["record"]["directive"] == "auto_expire"
                ):
                    projection["record"]["expires_at"] = "infinity"
            for closure in document["privacy_closures"]:
                for directive in closure["directives"]:
                    if directive["directive"] == "auto_expire":
                        directive["expires_at"] = "infinity"
            for effect in document["auto_expiry_effects"]:
                effect["due_at"] = "infinity"

        async with owner.begin() as connection:
            infinite_fixture = await _seed_fixture(
                connection,
                label=f"infinite-expiry-{uuid.uuid4().hex}",
                comprehensive=True,
                document_mutator=replace_auto_expiry_with_infinity,
            )
        _value, infinite_error = await _probe_finalize(
            finalizer,
            infinite_fixture,
        )
        assert infinite_error is not None
        assert _sqlstate(infinite_error) == "22023", str(infinite_error.orig)
        assert "identity_finalizer_privacy_record_invalid" in str(
            infinite_error.orig
        )
        async with owner.begin() as connection:
            await _delete_rejected_fixture(
                connection,
                infinite_fixture,
                infinite_error,
            )

        malformed_bytes = b"\xff\xfe"
        async with owner.begin() as connection:
            malformed_fixture = await _seed_fixture(
                connection,
                label=f"malformed-{uuid.uuid4().hex}",
                raw_document_override=malformed_bytes,
            )
        _value, malformed_error = await _probe_finalize(
            finalizer,
            malformed_fixture,
        )
        assert malformed_error is not None
        assert _sqlstate(malformed_error) == "22023"
        async with owner.begin() as connection:
            await _delete_rejected_fixture(
                connection,
                malformed_fixture,
                malformed_error,
            )

        async with owner.begin() as connection:
            comprehensive = await _seed_fixture(
                connection,
                label=f"complete-{uuid.uuid4().hex}",
                comprehensive=True,
            )

        _value, tampered = await _probe_finalize(
            finalizer,
            comprehensive,
            comprehensive.document + b" ",
        )
        assert tampered is not None
        assert _sqlstate(tampered) == "42501"

        signed_document = json.loads(comprehensive.document)
        live_review_signature = signed_document["review_attestation"][
            "review_signature"
        ]
        async with admin_target.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE operations.reviewed_identity_migration_runs "
                    "SET review_signature=:signature WHERE run_id=:run"
                ),
                {
                    "signature": _signature("unrelated-live-review"),
                    "run": comprehensive.run_id,
                },
            )
        _value, signature_error = await _probe_finalize(
            finalizer,
            comprehensive,
        )
        if signature_error is not None:
            assert _sqlstate(signature_error) in {"42501", "55000"}
            assert (
                "attestation" in str(signature_error).lower()
                or "run" in str(signature_error).lower()
            )
            review_signature_rejected = True
        async with admin_target.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE operations.reviewed_identity_migration_runs "
                    "SET review_signature=:signature WHERE run_id=:run"
                ),
                {
                    "signature": live_review_signature,
                    "run": comprehensive.run_id,
                },
            )

        async with owner.begin() as connection:
            await connection.execute(
                insert(schema.people).values(
                    person_id=comprehensive.person_ids[0],
                    display_name="preexisting collision",
                    status="active",
                    privacy_scope="private",
                )
            )
        _value, collision_error = await _probe_finalize(
            finalizer,
            comprehensive,
        )
        assert collision_error is not None
        collision_sqlstate = _sqlstate(collision_error)
        assert collision_sqlstate == "23505", (
            "preexisting person collision returned an unexpected error class; "
            f"sqlstate={collision_sqlstate}; "
            f"error_code={_safe_identity_error_code(collision_error)}"
        )
        async with owner.begin() as connection:
            await connection.execute(
                text("DELETE FROM identity.people WHERE person_id=:person"),
                {"person": comprehensive.person_ids[0]},
            )

        auto_expiry_person_id = uuid.UUID(
            next(
                projection.record["person_id"]
                for projection in comprehensive.projections
                if projection.kind == "privacy_directive"
                and projection.record["directive"] == "auto_expire"
            )
        )
        semantic_collision_id = uuid7()
        async with owner.begin() as connection:
            await connection.execute(
                insert(schema.outbox).values(
                    outbox_id=semantic_collision_id,
                    topic="privacy.person.auto_expire",
                    aggregate_id=auto_expiry_person_id,
                    payload={"version": 1, "test_only": True},
                    available_at=datetime.now(UTC),
                )
            )
        _value, outbox_collision_error = await _probe_finalize(
            finalizer,
            comprehensive,
        )
        assert outbox_collision_error is not None
        assert _sqlstate(outbox_collision_error) == "23505"
        assert "identity_finalizer_auto_expiry_invalid" in str(
            outbox_collision_error.orig
        )
        async with owner.begin() as connection:
            await connection.execute(
                text("DELETE FROM operations.outbox WHERE outbox_id=:outbox"),
                {"outbox": semantic_collision_id},
            )

        blocked_payload = _erasure_payload(
            comprehensive.person_ids[0],
            f"blocked-{uuid.uuid4().hex}",
        )
        async with erasure.begin() as connection:
            await _replay_erasure(connection, blocked_payload)
        _value, blocked_error = await _probe_finalize(
            finalizer,
            comprehensive,
        )
        assert blocked_error is not None
        assert _sqlstate(blocked_error) == "42501"
        assert "identity_erasure_blocked" in str(blocked_error.orig)
        async with admin_target.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM privacy.identity_person_erasure_residuals "
                    "WHERE block_id=:block"
                ),
                {"block": uuid.UUID(str(blocked_payload["block_id"]))},
            )
            await connection.execute(
                text(
                    "DELETE FROM privacy.subject_retrieval_blocks "
                    "WHERE block_id=:block"
                ),
                {"block": uuid.UUID(str(blocked_payload["block_id"]))},
            )

        racing_payload = _erasure_payload(
            comprehensive.person_ids[0],
            f"race-{uuid.uuid4().hex}",
        )
        erasure_connection = await erasure.connect()
        erasure_transaction = await erasure_connection.begin()
        try:
            await _replay_erasure(erasure_connection, racing_payload)
            waiting_finalizer = asyncio.create_task(
                _probe_finalize(finalizer, comprehensive)
            )
            await asyncio.sleep(0.2)
            assert not waiting_finalizer.done()
            await erasure_transaction.commit()
            _value, racing_error = await waiting_finalizer
            if racing_error is not None:
                assert _sqlstate(racing_error) == "40001"
                fence_serialized = True
        finally:
            if erasure_transaction.is_active:
                await erasure_transaction.rollback()
            await erasure_connection.close()
        async with admin_target.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM privacy.identity_person_erasure_residuals "
                    "WHERE block_id=:block"
                ),
                {"block": uuid.UUID(str(racing_payload["block_id"]))},
            )
            await connection.execute(
                text(
                    "DELETE FROM privacy.subject_retrieval_blocks "
                    "WHERE block_id=:block"
                ),
                {"block": uuid.UUID(str(racing_payload["block_id"]))},
            )

        assert await _finalize(finalizer, comprehensive) == comprehensive.run_id
        assert await _finalize(finalizer, comprehensive) == comprehensive.run_id

        async with owner.begin() as connection:
            atomic = (
                (
                    await connection.execute(
                        text(
                            "SELECT finalization.verification_status, "
                            "finalization.authoritative, "
                            "finalization.database_transaction_id, "
                            "finalization.finalized_at, admission.consumed_at, "
                            "(SELECT count(DISTINCT "
                            "receipt.database_transaction_id) FROM operations."
                            "reviewed_identity_migration_item_receipts receipt "
                            "WHERE receipt.run_id=finalization.run_id) "
                            "AS receipt_txids, "
                            "(SELECT bool_and(receipt.applied_at="
                            "finalization.finalized_at) FROM operations."
                            "reviewed_identity_migration_item_receipts receipt "
                            "WHERE receipt.run_id=finalization.run_id) "
                            "AS receipt_times_match, "
                            "(SELECT bool_and(lineage.linked_at="
                            "finalization.finalized_at) FROM operations."
                            "reviewed_identity_migration_projection_lineage "
                            "lineage WHERE lineage.run_id=finalization.run_id) "
                            "AS lineage_times_match "
                            "FROM operations."
                            "reviewed_identity_migration_finalizations "
                            "finalization JOIN operations."
                            "reviewed_identity_finalizer_admissions admission "
                            "USING (run_id) WHERE finalization.run_id=:run"
                        ),
                        {"run": comprehensive.run_id},
                    )
                )
                .mappings()
                .one()
            )
            assert atomic["verification_status"] == "candidate_unverified"
            assert atomic["authoritative"] is False
            assert atomic["database_transaction_id"] > 0
            assert atomic["consumed_at"] == atomic["finalized_at"]
            assert atomic["receipt_txids"] == 1
            assert atomic["receipt_times_match"] is True
            assert atomic["lineage_times_match"] is True

            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM identity.principals "
                        "WHERE person_id=ANY(CAST(:people AS uuid[]))"
                    ),
                    {"people": list(comprehensive.person_ids)},
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE subject_id=ANY(CAST(:people AS uuid[]))"
                    ),
                    {"people": list(comprehensive.person_ids)},
                )
            ).scalar_one() == 0
            assert comprehensive.auto_expiry_directive_id is not None
            assert comprehensive.auto_expiry_outbox_id is not None
            effect = (
                (
                    await connection.execute(
                        text(
                            "SELECT schedule.schedule_id, "
                            "schedule.directive_id, schedule.outbox_id, "
                            "schedule.state, effect.topic, effect.state "
                            "AS outbox_state, effect.aggregate_id "
                            "FROM privacy.auto_expiry_schedules schedule "
                            "JOIN operations.outbox effect "
                            "ON effect.outbox_id=schedule.outbox_id "
                            "WHERE schedule.directive_id=:directive"
                        ),
                        {"directive": (comprehensive.auto_expiry_directive_id)},
                    )
                )
                .mappings()
                .one()
            )
            assert effect["schedule_id"] == (comprehensive.auto_expiry_directive_id)
            assert effect["outbox_id"] == comprehensive.auto_expiry_outbox_id
            assert effect["state"] == "scheduled"
            assert effect["topic"] == "privacy.person.auto_expire"
            assert effect["outbox_state"] == "pending"

            assert comprehensive.alias_id is not None
            await connection.execute(
                text("DELETE FROM identity.aliases WHERE alias_id=:alias"),
                {"alias": comprehensive.alias_id},
            )

        with pytest.raises(DBAPIError) as no_repair:
            await _finalize(finalizer, comprehensive)
        assert _sqlstate(no_repair.value) == "55000"
        async with owner.begin() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM identity.aliases " "WHERE alias_id=:alias"
                    ),
                    {"alias": comprehensive.alias_id},
                )
            ).scalar_one() == 0

        drift_payload = _erasure_payload(
            comprehensive.person_ids[0],
            f"erasure-drift-{uuid.uuid4().hex}",
        )
        async with erasure.begin() as connection:
            await _replay_erasure(connection, drift_payload)
        with pytest.raises(DBAPIError) as erasure_drift:
            await _finalize(finalizer, comprehensive)
        assert _sqlstate(erasure_drift.value) == "42501"

        refused_downgrade = _run_alembic(
            owner_url,
            "downgrade",
            REVISION_0012,
        )
        assert refused_downgrade.returncode != 0
        assert "refusing to remove used E3 identity finalizer" in (
            refused_downgrade.stdout + refused_downgrade.stderr
        )
    finally:
        if finalizer is not None:
            await finalizer.dispose()
        await _set_finalizer_valid_until(
            admin,
            datetime(1970, 1, 1, tzinfo=UTC),
        )
        await erasure.dispose()
        await owner.dispose()
        await admin_target.dispose()
        await admin.dispose()

    assert all(downgraded_boundary.values()), downgraded_boundary
    assert review_signature_rejected, (
        "an owner-admitted document with a review signature different from "
        "the immutable live reviewed run was accepted"
    )
    assert (
        fence_serialized
    ), "a concurrent E2 tombstone did not force the E3 finalizer to retry"


async def _clear_reviewed_migration_state(connection) -> None:
    """Free the single record_only -> shadow authorization for a fresh seed.

    `rollout_transition_once UNIQUE (from_mode, to_mode)` permits exactly one
    such row per database, so a second `_seed_fixture` in the same phase
    collides with whatever the lifecycle test left behind.
    `_delete_rejected_fixture` cannot be reused: it refuses any fixture that
    reached a finalization, and the lifecycle test ends with one that did.

    `TRUNCATE ... CASCADE` rather than an ordered delete, deliberately. The run
    row is referenced transitively by receipts, projection lineage, erasure
    impacts, admissions, and finalizations; hand-ordering that graph is a
    guessing game that a future migration would silently invalidate. This is a
    disposable gate database, and the cascade is the schema's own answer.

    The one-shot rule this works around is worth naming: it is the same
    constraint that makes registration irreversible in production. Here it
    costs a gate run.
    """

    await connection.execute(
        text("TRUNCATE TABLE operations.reviewed_identity_migration_runs CASCADE")
    )
    await connection.execute(
        text(
            "DELETE FROM operations.rollout_authorizations "
            "WHERE from_mode = 'record_only' AND to_mode = 'shadow'"
        )
    )


# The writer's own lookup CTE joins the run row on exactly these, so a single
# disagreement makes the admission silently unwritable.
_ADMISSION_LOOKUP_COLUMNS = (
    ("logical_source_manifest_commitment", "source_manifest_commitment"),
    ("projection_manifest_commitment", "projection_manifest_commitment"),
    ("review_receipt_commitment", "review_receipt_commitment"),
    ("signing_key_fingerprint", "review_signing_key_fingerprint"),
    ("source_item_count", "source_item_count"),
    ("decision_count", "decision_count"),
)


async def _assert_admission_lookup_matches(connection, request) -> None:
    """Name the mismatching column instead of reporting a bare rejection.

    The writer raises one generic error whether the run is absent, a
    commitment disagrees, or a count is off. Comparing here turns that into a
    specific failure. Every value is an identifier, digest, or count.
    """

    columns = ", ".join(name for name, _ in _ADMISSION_LOOKUP_COLUMNS)
    row = (
        await connection.execute(
            text(
                f"SELECT {columns}, expires_at FROM "
                "operations.reviewed_identity_migration_runs "
                "WHERE run_id = CAST(:run_id AS uuid)"
            ),
            {"run_id": request.parameters["run_id"]},
        )
    ).mappings().one_or_none()
    assert row is not None, (
        f"no reviewed run {request.parameters['run_id']}; the seed did not land"
    )
    mismatched = {
        column: (row[column], request.parameters[parameter])
        for column, parameter in _ADMISSION_LOOKUP_COLUMNS
        if row[column] != request.parameters[parameter]
    }
    assert not mismatched, f"run row disagrees with the document on {mismatched}"
    # reviewed_identity_finalizer_admissions.run_id is UNIQUE: one admission per
    # run, ever. A pre-existing row makes the writer's insert a no-op that
    # ON CONFLICT DO NOTHING swallows, and the caller sees a bare rejection.
    existing = (
        await connection.execute(
            text(
                "SELECT admission_id FROM "
                "operations.reviewed_identity_finalizer_admissions "
                "WHERE run_id = CAST(:run_id AS uuid)"
            ),
            {"run_id": request.parameters["run_id"]},
        )
    ).scalar_one_or_none()
    assert existing is None, (
        f"run already holds admission {existing}; the writer cannot add a second"
    )
    now = (await connection.execute(text("SELECT clock_timestamp()"))).scalar_one()
    assert row["expires_at"] > now, (
        f"run already expired: {row['expires_at']} <= {now}"
    )


async def _admit_through_the_production_writer(
    engine: AsyncEngine,
    url: str,
    fixture: FinalizerFixture,
) -> tuple[uuid.UUID, FinalizerFixture]:
    """Write one admission with app.identity_admission_writer itself."""

    admission_id = uuid7()
    operation = identity_admission_writer.OPERATIONS["finalizer"]
    request = identity_admission_writer.parse_request(
        _canonical_bytes(
            {
                "contract": operation.contract,
                "admission_id": str(admission_id),
                "document_b64": base64.b64encode(fixture.document).decode("ascii"),
            }
        ),
        operation,
    )
    async with engine.connect() as connection:
        await _assert_admission_lookup_matches(connection, request)
    written = await identity_admission_writer.execute(url, operation, request)
    assert written == admission_id
    return admission_id, replace(fixture, admission_id=admission_id)


@pytest.mark.skipif(
    not _required_environment(),
    reason=(
        "dedicated E3 owner, admin, finalizer, and E2 erasure PostgreSQL "
        "URLs are required"
    ),
)
@pytest.mark.asyncio
async def test_production_admission_writer_can_actually_be_finalized() -> None:
    """The real writer and the real kernel must agree about expiry.

    0013 refuses an admission that outlives its run, and does not relax that for
    an exact replay. The writer stamped every admission at now + 15 minutes
    while the compiler stamps the run at staged + 10 minutes, so no admission
    the writer produced could ever be finalized, for any operator timing.

    Every other test in this file hand-inserts its admission row, so the seam
    between the two was exercised nowhere. This drives the production writer.
    """

    owner_url = os.environ[OWNER_DATABASE_ENV]
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as connection:
            await _clear_reviewed_migration_state(connection)
            fixture = await _seed_fixture(connection, label="writer-seam")
            # _seed_fixture hand-inserts an admission, and run_id is UNIQUE on
            # that table. Drop it so the production writer creates the only one
            # — which is the whole point of this test.
            await connection.execute(
                delete(schema.reviewed_identity_finalizer_admissions).where(
                    schema.reviewed_identity_finalizer_admissions.c.run_id
                    == fixture.run_id
                )
            )

        admission_id, written = await _admit_through_the_production_writer(
            engine, owner_url, fixture
        )

        async with engine.connect() as connection:
            admission_expiry, run_expiry = (
                await connection.execute(
                    text(
                        "SELECT admission.expires_at, run.expires_at "
                        "FROM operations.reviewed_identity_finalizer_admissions "
                        "AS admission "
                        "JOIN operations.reviewed_identity_migration_runs AS run "
                        "ON run.run_id = admission.run_id "
                        "WHERE admission.admission_id = CAST(:admission AS uuid)"
                    ),
                    {"admission": admission_id},
                )
            ).one()

        # This is exactly the predicate 0013 rejects on.
        assert admission_expiry <= run_expiry

        # And the kernel accepts it, which is the part that never held before.
        finalization_id = await _finalize(engine, written)
        assert isinstance(finalization_id, uuid.UUID)
    finally:
        await engine.dispose()
