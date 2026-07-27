from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app import identity_erasure_schema, schema
from app.ids import uuid7


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0012_identity_person_erasure_tombstone.py"
OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E2_OWNER_DATABASE_URL"
ERASURE_DATABASE_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E2_ERASURE_DATABASE_URL"

MANDATORY_RESIDUALS = (
    "live_identity_rows_retained",
    "semantic_dependencies_not_evaluated",
    "generic_artifact_lineage_unresolved",
    "external_cleanup_not_evaluated",
    "legacy_cleanup_not_evaluated",
    "backup_expiry_unverified",
    "preexisting_snapshot_visibility",
)


def _residual_commitment(record_digest: str, code: str) -> str:
    preimage = f"identity-person-erasure-residual-v2:{record_digest}:{code}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _payload(*, person_id: uuid.UUID, block_id: uuid.UUID) -> dict[str, object]:
    completed_at = datetime.now(UTC) - timedelta(seconds=1)
    source_created_at = completed_at - timedelta(seconds=1)
    record_digest = hashlib.sha256(f"record:{block_id}".encode()).hexdigest()
    return {
        "version": 2,
        "subject_kind": "identity_person",
        "outbox_id": str(uuid.uuid4()),
        "erasure_request_id": str(uuid7()),
        "person_id": str(person_id),
        "operation_id": str(uuid7()),
        "block_id": str(block_id),
        "block_commitment": hashlib.sha256(f"block:{block_id}".encode()).hexdigest(),
        "outcome_code": "retrieval_block_active",
        "operation_codes": ["activate_identity_person_retrieval_block"],
        "policy_digest": hashlib.sha256(b"e2-policy").hexdigest(),
        "completed_at": completed_at.isoformat(),
        "source_created_at": source_created_at.isoformat(),
        "external_pending_codes": [],
        "legacy_untracked_codes": [],
        "checkpoint_affected": True,
        "backup_expiry_at": None,
        "exact_residual_codes": list(MANDATORY_RESIDUALS),
        "ledger_epoch": 1,
        "ledger_record_hash": hashlib.sha256(b"ledger-record").hexdigest(),
        "ledger_record_digest": record_digest,
        "residual_commitments": {
            code: _residual_commitment(record_digest, code)
            for code in MANDATORY_RESIDUALS
        },
    }


def test_e2_schema_is_isolated_and_deletion_free() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0012_identity_erasure_e2"' in source
    assert 'down_revision: str | None = "0011_identity_erasure_e1"' in source
    assert "privacy.identity_person_erasure_residuals" in source
    assert "identity_erasure_metadata" in identity_erasure_schema.__dict__
    assert (
        "privacy.subject_retrieval_blocks"
        in identity_erasure_schema.identity_erasure_metadata.tables
    )
    assert (
        "privacy.identity_person_erasure_residuals"
        in identity_erasure_schema.identity_erasure_metadata.tables
    )
    assert "privacy.subject_retrieval_blocks" not in schema.metadata.tables
    assert "privacy.identity_person_erasure_residuals" not in schema.metadata.tables

    assert "DELETE FROM" not in source
    assert "apply_identity_person_erasure" not in source
    assert "CREATE PROCEDURE" not in source
    assert "replay_identity_person_retrieval_block_v2" in source
    assert "retrieval_block_active" in source
    assert "live_erased" not in source
    assert "never a claim of physical whole-person erasure" in source


def test_e2_contract_has_exact_residuals_and_restore_fields() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    block = identity_erasure_schema.subject_retrieval_blocks
    residual = identity_erasure_schema.identity_person_erasure_residuals

    assert set(block.c.keys()) == {
        "block_id",
        "operation_id",
        "person_id",
        "source_kind",
        "erasure_request_id",
        "block_code",
        "outcome_code",
        "block_commitment",
        "ledger_epoch",
        "ledger_outbox_id",
        "ledger_record_hash",
        "ledger_record_digest",
        "ledger_attached_at",
        "created_at",
    }
    assert set(residual.c.keys()) == {
        "block_id",
        "residual_code",
        "person_id",
        "residual_class",
        "state",
        "recorded_at",
        "resolved_at",
        "residual_commitment",
    }
    assert tuple(column.name for column in residual.primary_key.columns) == (
        "block_id",
        "residual_code",
    )
    for code in (*MANDATORY_RESIDUALS, "closure_limit_exceeded"):
        assert code in source
    assert "DEFERRABLE INITIALLY DEFERRED" in source
    assert "identity_person_erasure_residual_set_incomplete" in source
    assert "identity-person-erasure-residual-v2:" in source
    assert "ledger_epoch IS NULL" in source
    assert "source_kind = 'ledger_replay'" in source
    assert "identity_erasure_block_person UNIQUE (person_id)" in source


def test_e2_suppression_excludes_control_and_confirmation_evidence() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    target_section = source.split("SUPPRESSION_TARGETS = (", 1)[1].split(
        "SUPPRESSION_FUNCTIONS =", 1
    )[0]

    for included in (
        "identity.people",
        "identity.principals",
        "identity.principal_binding_proposals",
        "identity.aliases",
        "knowledge.fact_versions",
        "knowledge.fact_support",
        "engagement.initiatives",
        "privacy.artifact_registry",
    ):
        assert included in target_section
    for excluded in (
        "identity.confirmation_artifacts",
        "identity.privacy_directives",
        "identity.edge_privacy_blocks",
        "identity.edge_privacy_user_blocks",
        "privacy.erasure_requests",
        "privacy.auto_expiry_schedules",
        "operations.outbox",
        "operations.erasure_replay_receipts",
    ):
        assert excluded not in target_section

    assert "AS RESTRICTIVE" in source
    assert "reject_tombstoned_identity_write" in source
    assert "target_predicate <> 'parent_of'" in source
    assert "identity_fact_is_blocked(text,uuid,text,jsonb,uuid)" in source
    assert (
        "privacy.identity_fact_version_is_visible(source_fact_version_id)"
        in target_section
    )
    assert (
        "e2_fact.fact_version_id = initiatives.source_fact_version_id"
        not in target_section
    )


def test_e2_kernel_stays_dml_free_and_replay_is_owner_owned() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    visibility_helper = source.split(
        "CREATE FUNCTION privacy.identity_fact_version_is_visible(", 1
    )[1].split(
        "CREATE FUNCTION privacy.reject_tombstoned_identity_write()", 1
    )[0]

    assert "GRANT SELECT (person_id) ON TABLE {BLOCK_TABLE}" in source
    assert "GRANT SELECT (principal_id, person_id)" in source
    assert (
        "GRANT USAGE ON SCHEMA identity, knowledge, privacy TO {KERNEL_ROLE};"
        in source
    )
    assert "GRANT USAGE ON SCHEMA privacy TO {initiative_fact_roles};" in source
    assert (
        "runtime.role_name, 'privacy', 'USAGE'"
        in source
    )
    assert (
        "required_schema.schema_name,\n"
        "                        'CREATE'"
        in source
    )
    assert (
        "GRANT SELECT (\n"
        "          fact_version_id, subject_type, subject_id, predicate, object,\n"
        "          perspective_principal_id\n"
        "        ) ON TABLE knowledge.fact_versions TO {KERNEL_ROLE};"
        in source
    )
    assert (
        "GRANT SELECT ON TABLE knowledge.fact_versions TO {KERNEL_ROLE}"
        not in source
    )
    assert (
        "GRANT INSERT ON TABLE knowledge.fact_versions TO {KERNEL_ROLE}"
        not in source
    )
    assert "TO {KERNEL_ROLE};" in source
    assert "GRANT INSERT ON TABLE {BLOCK_TABLE} TO {KERNEL_ROLE}" not in source
    assert "GRANT INSERT ON TABLE {RESIDUAL_TABLE} TO {KERNEL_ROLE}" not in source
    assert "LANGUAGE sql" in visibility_helper
    assert "STABLE" in visibility_helper
    assert "SECURITY DEFINER" in visibility_helper
    assert "SET search_path = pg_catalog" in visibility_helper
    assert "SET row_security = on" in visibility_helper
    assert "fact.perspective_principal_id =" in visibility_helper
    assert "pg_catalog.current_setting(" in visibility_helper
    assert "'app.principal_id', true" in visibility_helper
    assert "privacy.identity_fact_is_blocked(" in visibility_helper
    assert "INITIATIVE_FACT_VISIBILITY_ROLES = (" in source
    assert (
        'INITIATIVE_FACT_VISIBILITY_FUNCTION} "\n'
        '        f"TO {initiative_fact_roles}"'
        in source
    )
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "{INITIATIVE_FACT_VISIBILITY_FUNCTION} TO {KERNEL_ROLE}"
        not in source
    )
    assert (
        'op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {KERNEL_ROLE}")'
        in source
    )
    assert (
        "NOT pg_catalog.has_function_privilege(\n"
        "                  '{KERNEL_ROLE}', function_row.function_oid, 'EXECUTE'"
        in source
    )
    assert source.index("RESET ROLE;") < source.index(
        "CREATE FUNCTION privacy.replay_identity_person_retrieval_block_v2"
    )
    assert "GRANT EXECUTE ON FUNCTION {REPLAY_FUNCTION} TO home_agent_erasure" in source
    assert "SECURITY INVOKER" in source
    assert "current_user = 'home_agent_owner'" in source
    assert "session_user = 'home_agent_erasure'" in source
    assert (
        "DROP FUNCTION IF EXISTS\n"
        "          privacy.identity_fact_version_is_visible(uuid);"
        in source
    )


@pytest.mark.skipif(
    not os.getenv(OWNER_DATABASE_ENV) or not os.getenv(ERASURE_DATABASE_ENV),
    reason="dedicated E2 owner and erasure PostgreSQL URLs are required",
)
@pytest.mark.asyncio
async def test_postgresql_e2_replay_is_idempotent_and_residual_bounded() -> None:
    owner = create_async_engine(os.environ[OWNER_DATABASE_ENV], pool_size=1)
    erasure = create_async_engine(os.environ[ERASURE_DATABASE_ENV], pool_size=1)
    person_id = uuid7()
    block_id = uuid7()
    payload = _payload(person_id=person_id, block_id=block_id)
    try:
        async with owner.begin() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one()
            assert revision == "0012_identity_erasure_e2"

            kernel_acl = (
                (
                    await connection.execute(
                        text(
                            "SELECT has_column_privilege("
                            "'home_agent_identity_erasure_kernel', "
                            "'privacy.subject_retrieval_blocks', 'person_id', "
                            "'SELECT') AS block_person, "
                            "has_table_privilege("
                            "'home_agent_identity_erasure_kernel', "
                            "'privacy.subject_retrieval_blocks', 'INSERT,UPDATE,DELETE') "
                            "AS block_dml, has_table_privilege("
                            "'home_agent_identity_erasure_kernel', "
                            "'privacy.identity_person_erasure_residuals', 'SELECT,INSERT') "
                            "AS residual_access"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert kernel_acl == {
                "block_person": True,
                "block_dml": False,
                "residual_access": False,
            }

        for _ in range(2):
            async with erasure.begin() as connection:
                returned = (
                    await connection.execute(
                        text(
                            "SELECT privacy."
                            "replay_identity_person_retrieval_block_v2("
                            "CAST(:payload AS jsonb))"
                        ),
                        {"payload": json.dumps(payload, separators=(",", ":"))},
                    )
                ).scalar_one()
                assert returned == block_id

        async with owner.begin() as connection:
            block = (
                (
                    await connection.execute(
                        text(
                            "SELECT source_kind, operation_id, erasure_request_id, "
                            "outcome_code, ledger_epoch FROM "
                            "privacy.subject_retrieval_blocks WHERE person_id=:person"
                        ),
                        {"person": person_id},
                    )
                )
                .mappings()
                .one()
            )
            assert block == {
                "source_kind": "ledger_replay",
                "operation_id": None,
                "erasure_request_id": None,
                "outcome_code": "retrieval_block_active",
                "ledger_epoch": 1,
            }
            residuals = (
                (
                    await connection.execute(
                        text(
                            "SELECT residual_code, residual_class, state, resolved_at "
                            "FROM privacy.identity_person_erasure_residuals "
                            "WHERE block_id=:block ORDER BY residual_code"
                        ),
                        {"block": block_id},
                    )
                )
                .mappings()
                .all()
            )
            assert {row["residual_code"] for row in residuals} == set(
                MANDATORY_RESIDUALS
            )
            assert all(row["state"] == "open" for row in residuals)
            assert all(row["resolved_at"] is None for row in residuals)

        missing_residual_block = uuid7()
        with pytest.raises(DBAPIError) as missing_residual_error:
            async with owner.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO privacy.subject_retrieval_blocks ("
                        "block_id, operation_id, person_id, source_kind, "
                        "erasure_request_id, block_code, outcome_code, "
                        "block_commitment, ledger_epoch, ledger_outbox_id, "
                        "ledger_record_hash, ledger_record_digest, "
                        "ledger_attached_at, created_at) VALUES ("
                        ":block, NULL, :person, 'ledger_replay', NULL, "
                        "'identity_person_erasure', 'retrieval_block_active', "
                        ":commitment, 2, :outbox, :record_hash, "
                        ":record_digest, transaction_timestamp(), "
                        "transaction_timestamp())"
                    ),
                    {
                        "block": missing_residual_block,
                        "person": uuid7(),
                        "commitment": hashlib.sha256(
                            b"missing-residual-block"
                        ).hexdigest(),
                        "outbox": uuid.uuid4(),
                        "record_hash": hashlib.sha256(
                            b"missing-residual-hash"
                        ).hexdigest(),
                        "record_digest": hashlib.sha256(
                            b"missing-residual-digest"
                        ).hexdigest(),
                    },
                )
        assert getattr(missing_residual_error.value.orig, "sqlstate", None) == "23514"

        conflicting = dict(payload)
        conflicting["block_commitment"] = hashlib.sha256(b"conflict").hexdigest()
        async with erasure.connect() as connection:
            with pytest.raises(DBAPIError) as error:
                await connection.execute(
                    text(
                        "SELECT privacy."
                        "replay_identity_person_retrieval_block_v2("
                        "CAST(:payload AS jsonb))"
                    ),
                    {"payload": json.dumps(conflicting, separators=(",", ":"))},
                )
            assert getattr(error.value.orig, "sqlstate", None) == "23505"
    finally:
        await erasure.dispose()
        await owner.dispose()
