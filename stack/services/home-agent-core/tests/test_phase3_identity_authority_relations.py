from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app import schema
from app.db import Database
from app.ids import uuid7


REVISION = "0007_phase3_identity_authority"
DATABASE_ENV = "TEST_PHASE3_AUTHORITY_DATABASE_URL"
POLICY_DIGEST = "a" * 64
PRIVACY_CATEGORIES = (
    "ingress",
    "retrieval",
    "prompt",
    "initiative",
    "export",
    "edge_block",
)


def _commitment(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _signature(label: str) -> str:
    return hashlib.sha512(label.encode("utf-8")).hexdigest()


def _sqlstate(exc: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(exc.value.orig, "sqlstate", None)


async def _reject(
    connection: AsyncConnection,
    table,
    values: dict[str, Any],
    expected_sqlstate: str,
) -> None:
    with pytest.raises(DBAPIError) as exc:
        async with connection.begin_nested():
            await connection.execute(insert(table).values(**values))
    assert _sqlstate(exc) == expected_sqlstate


def _run(
    *,
    run_id: uuid.UUID,
    authorization_id: uuid.UUID,
    label: str,
    source_count: int,
    decision_count: int,
    now: datetime,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "operator_request_id": uuid7(),
        "contract_version": "reviewed-identity-migration-run-v1",
        "source_schema_version": 1,
        "source_projection_contract_version": "legacy-identity-source-projection-v1",
        "importer_version": "legacy-identity-importer-v1",
        "canonicalization_version": "identity-canonicalization-v1",
        "projection_version": "semantic-people-projection-v1",
        "shadow_rule_version": "record-only-envelope-worker-gate-v3",
        "commitment_algorithm": "hmac-sha256-v1",
        "commitment_key_fingerprint": _commitment(f"{label}:commitment-key"),
        "commitment_key_epoch": 1,
        "source_item_count": source_count,
        "decision_count": decision_count,
        "logical_source_manifest_commitment": _commitment(f"{label}:source"),
        "projection_manifest_commitment": _commitment(f"{label}:projection"),
        "source_projection_contract_digest": _commitment(f"{label}:contract"),
        "review_receipt_commitment": _commitment(f"{label}:review"),
        "policy_version": "home-agent-mvp-v1",
        "policy_digest": POLICY_DIGEST,
        "shadow_authorization_id": authorization_id,
        "release_manifest_digest": _commitment(f"{label}:release"),
        "migration_tool_bundle_digest": _commitment(f"{label}:tool"),
        "core_oci_manifest_digest": _commitment(f"{label}:oci"),
        "core_schema_digest": _commitment(f"{label}:schema"),
        "core_capability_digest": _commitment(f"{label}:capability"),
        "signature_algorithm": "ed25519",
        "signing_key_fingerprint": _commitment(f"{label}:review-key"),
        "review_signature": _signature(f"{label}:review-signature"),
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
    }


def _source(
    *,
    source_item_id: uuid.UUID,
    run_id: uuid.UUID,
    ordinal: int,
    row_label: str,
    projection_commitment: str,
) -> dict[str, Any]:
    return {
        "source_item_id": source_item_id,
        "run_id": run_id,
        "ordinal": ordinal,
        "source_table_kind": "identity_aliases",
        "row_key_commitment": _commitment(f"{row_label}:row-key"),
        "allowed_projection_commitment": projection_commitment,
    }


def _apply_decision(
    *,
    decision_id: uuid.UUID,
    run_id: uuid.UUID,
    source_item_id: uuid.UUID,
    ordinal: int,
    candidate: str,
    label: str,
) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "run_id": run_id,
        "source_item_id": source_item_id,
        "ordinal": ordinal,
        "decision_kind": "alias",
        "disposition": "apply",
        "candidate_commitment": candidate,
        "canonical_apply_decision_id": None,
        "canonical_apply_disposition": None,
        "decision_commitment": _commitment(f"{label}:decision"),
    }


def _receipt(
    *,
    receipt_id: uuid.UUID,
    run_id: uuid.UUID,
    decision_id: uuid.UUID,
    candidate: str,
    label: str,
    projection_table: str = "identity.aliases",
) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "run_id": run_id,
        "decision_id": decision_id,
        "decision_kind": "alias",
        "decision_disposition": "apply",
        "candidate_commitment": candidate,
        "outcome": "inserted_exact",
        "projection_table_kind": projection_table,
        "projection_ref_commitment": _commitment(f"{label}:projection-ref"),
        "projection_commitment": _commitment(f"{label}:projection"),
        "receipt_commitment": _commitment(f"{label}:receipt"),
        "database_transaction_id": 1,
    }


@pytest.mark.skipif(
    not os.getenv(DATABASE_ENV),
    reason=(
        "TEST_PHASE3_AUTHORITY_DATABASE_URL is required for the isolated "
        "Phase 3 authority invariant test"
    ),
)
@pytest.mark.asyncio
async def test_phase3_identity_authority_relational_invariants() -> None:
    """Exercise 0007 in one rollback-only transaction on a dedicated database."""

    database = Database(os.environ[DATABASE_ENV])
    connection = await database.engine.connect()
    outer = await connection.begin()
    now = datetime.now(UTC)
    run_id, other_run_id = uuid7(), uuid7()
    source_ids = (uuid7(), uuid7(), uuid7())
    other_source_id = uuid7()
    apply_id, omission_id, coalesced_id = uuid7(), uuid7(), uuid7()
    finalization_id, evidence_id, cutover_id = uuid7(), uuid7(), uuid7()
    candidate = _commitment(f"candidate:{run_id}")
    shared_projection = _commitment(f"shared-projection:{run_id}")

    try:
        revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        if revision != REVISION:
            pytest.skip(f"isolated authority database is not migrated to {REVISION}")
        session_user = (
            await connection.execute(text("SELECT session_user"))
        ).scalar_one()
        if session_user != "home_agent_owner":
            pytest.skip("isolated authority database must authenticate as its owner")

        authorization = (
            (
                await connection.execute(
                    select(schema.rollout_authorizations).where(
                        schema.rollout_authorizations.c.from_mode == "record_only",
                        schema.rollout_authorizations.c.to_mode == "shadow",
                    )
                )
            )
            .mappings()
            .first()
        )
        if authorization is None:
            authorization_id = uuid7()
            await connection.execute(
                insert(schema.rollout_authorizations).values(
                    authorization_id=authorization_id,
                    operator_request_id=uuid7(),
                    from_mode="record_only",
                    to_mode="shadow",
                    rule_version="record-only-envelope-worker-gate-v3",
                    policy_version="home-agent-mvp-v1",
                    policy_digest=POLICY_DIGEST,
                    input_digest=_commitment(f"rollout:{run_id}"),
                    worker_instance_id=uuid7(),
                    worker_success_sequence=1,
                    worker_kernel_version="worker-maintenance-cycle-v1",
                    worker_maintenance_succeeded_at=now - timedelta(seconds=2),
                    worker_proof_digest=_commitment(f"worker:{run_id}"),
                    readiness_evaluated_at=now - timedelta(seconds=1),
                    authorized_at=now,
                )
            )
        else:
            authorization_id = authorization["authorization_id"]

        await connection.execute(
            insert(schema.reviewed_identity_migration_runs),
            [
                _run(
                    run_id=run_id,
                    authorization_id=authorization_id,
                    label=f"run:{run_id}",
                    source_count=3,
                    decision_count=3,
                    now=now,
                ),
                _run(
                    run_id=other_run_id,
                    authorization_id=authorization_id,
                    label=f"run:{other_run_id}",
                    source_count=1,
                    decision_count=1,
                    now=now,
                ),
            ],
        )
        await connection.execute(
            insert(schema.reviewed_identity_migration_source_items),
            [
                _source(
                    source_item_id=source_ids[index],
                    run_id=run_id,
                    ordinal=index,
                    row_label=f"source:{source_ids[index]}",
                    projection_commitment=(
                        shared_projection
                        if index < 2
                        else _commitment(f"projection:{source_ids[index]}")
                    ),
                )
                for index in range(3)
            ]
            + [
                _source(
                    source_item_id=other_source_id,
                    run_id=other_run_id,
                    ordinal=0,
                    row_label=f"source:{other_source_id}",
                    projection_commitment=shared_projection,
                )
            ],
        )
        duplicate_count = (
            await connection.execute(
                select(func.count())
                .select_from(schema.reviewed_identity_migration_source_items)
                .where(
                    schema.reviewed_identity_migration_source_items.c.run_id == run_id,
                    schema.reviewed_identity_migration_source_items.c.allowed_projection_commitment
                    == shared_projection,
                )
            )
        ).scalar_one()
        assert duplicate_count == 2

        duplicate_row = _source(
            source_item_id=uuid7(),
            run_id=run_id,
            ordinal=3,
            row_label="unused",
            projection_commitment=_commitment("unused-projection"),
        )
        duplicate_row["row_key_commitment"] = _commitment(
            f"source:{source_ids[0]}:row-key"
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_source_items,
            duplicate_row,
            "23505",
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_source_items,
            _source(
                source_item_id=uuid7(),
                run_id=run_id,
                ordinal=0,
                row_label="duplicate-ordinal",
                projection_commitment=_commitment("duplicate-ordinal"),
            ),
            "23505",
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_decisions,
            _apply_decision(
                decision_id=uuid7(),
                run_id=other_run_id,
                source_item_id=source_ids[0],
                ordinal=0,
                candidate=candidate,
                label="cross-run-source",
            ),
            "23503",
        )

        invalid_decision = {
            "decision_id": uuid7(),
            "run_id": run_id,
            "source_item_id": source_ids[2],
            "ordinal": 7,
            "decision_kind": "explicit_omission",
            "disposition": "privacy_suppressed",
            "candidate_commitment": candidate,
            "canonical_apply_decision_id": None,
            "canonical_apply_disposition": None,
            "decision_commitment": _commitment("invalid-omission"),
        }
        await _reject(
            connection,
            schema.reviewed_identity_migration_decisions,
            invalid_decision,
            "23514",
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_decisions,
            {
                **invalid_decision,
                "decision_id": uuid7(),
                "decision_kind": "explicit_omission",
                "disposition": "apply",
                "decision_commitment": _commitment("invalid-apply"),
            },
            "23514",
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_decisions,
            {
                **invalid_decision,
                "decision_id": uuid7(),
                "decision_kind": "alias",
                "disposition": "coalesced_duplicate",
                "canonical_apply_disposition": "apply",
                "decision_commitment": _commitment("invalid-coalesced"),
            },
            "23514",
        )

        await connection.execute(
            insert(schema.reviewed_identity_migration_decisions).values(
                **_apply_decision(
                    decision_id=apply_id,
                    run_id=run_id,
                    source_item_id=source_ids[0],
                    ordinal=0,
                    candidate=candidate,
                    label=f"apply:{run_id}",
                )
            )
        )
        await connection.execute(
            insert(schema.reviewed_identity_migration_decisions),
            [
                {
                    "decision_id": omission_id,
                    "run_id": run_id,
                    "source_item_id": source_ids[1],
                    "ordinal": 1,
                    "decision_kind": "explicit_omission",
                    "disposition": "privacy_suppressed",
                    "candidate_commitment": None,
                    "canonical_apply_decision_id": None,
                    "canonical_apply_disposition": None,
                    "decision_commitment": _commitment(f"omit:{run_id}"),
                },
                {
                    "decision_id": coalesced_id,
                    "run_id": run_id,
                    "source_item_id": source_ids[2],
                    "ordinal": 2,
                    "decision_kind": "alias",
                    "disposition": "coalesced_duplicate",
                    "candidate_commitment": candidate,
                    "canonical_apply_decision_id": apply_id,
                    "canonical_apply_disposition": "apply",
                    "decision_commitment": _commitment(f"coalesced:{run_id}"),
                },
            ],
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_decisions,
            {
                "decision_id": uuid7(),
                "run_id": other_run_id,
                "source_item_id": other_source_id,
                "ordinal": 0,
                "decision_kind": "alias",
                "disposition": "coalesced_duplicate",
                "candidate_commitment": candidate,
                "canonical_apply_decision_id": apply_id,
                "canonical_apply_disposition": "apply",
                "decision_commitment": _commitment("cross-run-canonical"),
            },
            "23503",
        )

        await _reject(
            connection,
            schema.reviewed_identity_migration_item_receipts,
            _receipt(
                receipt_id=uuid7(),
                run_id=run_id,
                decision_id=apply_id,
                candidate=candidate,
                label="wrong-projection",
                projection_table="identity.people",
            ),
            "23514",
        )
        await _reject(
            connection,
            schema.reviewed_identity_migration_item_receipts,
            _receipt(
                receipt_id=uuid7(),
                run_id=run_id,
                decision_id=omission_id,
                candidate=candidate,
                label="omission-receipt",
            ),
            "23503",
        )
        await connection.execute(
            insert(schema.reviewed_identity_migration_item_receipts).values(
                **_receipt(
                    receipt_id=uuid7(),
                    run_id=run_id,
                    decision_id=apply_id,
                    candidate=candidate,
                    label=f"receipt:{run_id}",
                )
            )
        )
        duplicate_projection_decision_id = uuid7()
        duplicate_projection_decision = _apply_decision(
            decision_id=duplicate_projection_decision_id,
            run_id=run_id,
            source_item_id=source_ids[1],
            ordinal=3,
            candidate=_commitment(f"duplicate-target:{run_id}"),
            label=f"duplicate-target:{run_id}",
        )
        duplicate_projection_receipt = _receipt(
            receipt_id=uuid7(),
            run_id=run_id,
            decision_id=duplicate_projection_decision_id,
            candidate=_commitment(f"duplicate-target:{run_id}"),
            label=f"duplicate-target:{run_id}",
        )
        duplicate_projection_receipt["projection_ref_commitment"] = _commitment(
            f"receipt:{run_id}:projection-ref"
        )
        with pytest.raises(DBAPIError) as duplicate_projection_error:
            async with connection.begin_nested():
                await connection.execute(
                    insert(schema.reviewed_identity_migration_decisions).values(
                        **duplicate_projection_decision
                    )
                )
                await connection.execute(
                    insert(schema.reviewed_identity_migration_item_receipts).values(
                        **duplicate_projection_receipt
                    )
                )
        assert _sqlstate(duplicate_projection_error) == "23505"

        finalization = {
            "finalization_id": finalization_id,
            "run_id": run_id,
            "contract_version": "reviewed-identity-migration-finalization-v1",
            "verification_status": "candidate_unverified",
            "authoritative": False,
            "source_item_count": 3,
            "decision_count": 3,
            "apply_decision_count": 1,
            "receipt_count": 1,
            "source_manifest_commitment": _commitment(f"final:{run_id}:source"),
            "projection_manifest_commitment": _commitment(f"final:{run_id}:projection"),
            "decision_manifest_commitment": _commitment(f"final:{run_id}:decision"),
            "receipt_set_commitment": _commitment(f"final:{run_id}:receipt"),
            "finalization_commitment": _commitment(f"final:{run_id}"),
            "database_transaction_id": 2,
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": _commitment(f"final:{run_id}:key"),
            "finalization_signature": _signature(f"final:{run_id}:signature"),
        }
        await _reject(
            connection,
            schema.reviewed_identity_migration_finalizations,
            {
                **finalization,
                "finalization_id": uuid7(),
                "authoritative": True,
                "finalization_commitment": _commitment("invalid-finalization"),
            },
            "23514",
        )
        await connection.execute(
            insert(schema.reviewed_identity_migration_finalizations).values(
                **finalization
            )
        )
        await connection.execute(
            insert(schema.legacy_identity_writer_evidence).values(
                evidence_id=evidence_id,
                run_id=run_id,
                source_installation_id=uuid7(),
                semantic_generation=1,
                source_projection_commitment=_commitment(f"writer:{run_id}:projection"),
                evidence_strength="observed_stopped",
                integrity_result="passed",
                checkpoint_result="complete",
                journal_result="clean",
                legacy_context_cutoff_status="observed_cutoff",
                release_manifest_digest=_commitment(f"writer:{run_id}:release"),
                freeze_kernel_build_digest=_commitment(f"writer:{run_id}:build"),
                evidence_commitment=_commitment(f"writer:{run_id}:evidence"),
                signature_algorithm="ed25519",
                signing_key_fingerprint=_commitment(f"writer:{run_id}:key"),
                evidence_signature=_signature(f"writer:{run_id}:signature"),
                observed_at=now - timedelta(minutes=1),
            )
        )

        privacy_ids = {category: uuid7() for category in PRIVACY_CATEGORIES}
        await connection.execute(
            insert(schema.privacy_cutover_check_receipts),
            [
                {
                    "check_id": privacy_ids[category],
                    "run_id": run_id,
                    "finalization_id": finalization_id,
                    "check_category": category,
                    "check_result": "passed",
                    "residual_code": "none",
                    "check_commitment": _commitment(
                        f"privacy:{run_id}:{category}:check"
                    ),
                    "receipt_commitment": _commitment(
                        f"privacy:{run_id}:{category}:receipt"
                    ),
                    "policy_digest": POLICY_DIGEST,
                }
                for category in PRIVACY_CATEGORIES
            ],
        )
        cutover = {
            "cutover_id": cutover_id,
            "run_id": run_id,
            "finalization_id": finalization_id,
            "writer_evidence_id": evidence_id,
            "contract_version": "semantic-authority-cutover-candidate-v1",
            "authority_status": "candidate_unenforced",
            "authoritative": False,
            **{
                f"{category}_check_id": privacy_ids[category]
                for category in PRIVACY_CATEGORIES
            },
            **{
                f"{category}_check_category": category
                for category in PRIVACY_CATEGORIES
            },
            "required_privacy_check_result": "passed",
            "required_privacy_residual_code": "none",
            "privacy_check_set_commitment": _commitment(f"cutover:{run_id}:privacy"),
            "cutover_commitment": _commitment(f"cutover:{run_id}"),
            "policy_digest": POLICY_DIGEST,
            "signature_algorithm": "ed25519",
            "signing_key_fingerprint": _commitment(f"cutover:{run_id}:key"),
            "cutover_signature": _signature(f"cutover:{run_id}:signature"),
        }
        await _reject(
            connection,
            schema.semantic_authority_cutovers,
            {
                **cutover,
                "cutover_id": uuid7(),
                "authoritative": True,
                "cutover_commitment": _commitment("invalid-cutover"),
            },
            "23514",
        )
        await connection.execute(
            insert(schema.semantic_authority_cutovers).values(**cutover)
        )

        final_state = (
            await connection.execute(
                select(
                    schema.reviewed_identity_migration_finalizations.c.verification_status,
                    schema.reviewed_identity_migration_finalizations.c.authoritative,
                ).where(
                    schema.reviewed_identity_migration_finalizations.c.finalization_id
                    == finalization_id
                )
            )
        ).one()
        cutover_state = (
            await connection.execute(
                select(
                    schema.semantic_authority_cutovers.c.authority_status,
                    schema.semantic_authority_cutovers.c.authoritative,
                ).where(schema.semantic_authority_cutovers.c.cutover_id == cutover_id)
            )
        ).one()
        assert tuple(final_state) == ("candidate_unverified", False)
        assert tuple(cutover_state) == ("candidate_unenforced", False)
    finally:
        # FORCE RLS deliberately provides no leaf DELETE path. The dedicated
        # test authority is cleaned by rolling back this one outer transaction.
        if outer.is_active:
            await outer.rollback()
        await connection.close()
        await database.close()
