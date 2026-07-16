"""Isolated SQLAlchemy declarations for the identity-person erasure boundary.

These tables intentionally do not participate in :mod:`app.schema`'s global
metadata.  Revision 0001 calls ``app.schema.metadata.create_all()``; adding the
E1/E2 names there would make a fresh database pre-create objects that later
Alembic revisions must admit and install in a reviewed order.

Alembic revision 0012 is the DDL authority.  This module exists only for
explicit E2 replay/query code and schema-contract tests; callers must never
call ``identity_erasure_metadata.create_all()``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID


identity_erasure_metadata = MetaData()
UUID_TYPE = UUID(as_uuid=True)
TRANSACTION_TIME = text("transaction_timestamp()")


subject_retrieval_blocks = Table(
    "subject_retrieval_blocks",
    identity_erasure_metadata,
    Column("block_id", UUID_TYPE, primary_key=True),
    Column("operation_id", UUID_TYPE),
    Column("person_id", UUID_TYPE, nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("erasure_request_id", UUID_TYPE),
    Column("block_code", String(32), nullable=False),
    Column("outcome_code", String(32), nullable=False),
    Column("block_commitment", String(64), nullable=False),
    Column("ledger_epoch", BigInteger),
    Column("ledger_outbox_id", UUID_TYPE),
    Column("ledger_record_hash", String(64)),
    Column("ledger_record_digest", String(64)),
    Column("ledger_attached_at", DateTime(timezone=True)),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=TRANSACTION_TIME,
    ),
    ForeignKeyConstraint(
        ["operation_id"],
        ["operations.reviewed_identity_migration_erasure_operations.operation_id"],
        name="fk_subject_retrieval_block_operation",
    ),
    ForeignKeyConstraint(
        ["operation_id", "erasure_request_id"],
        [
            "operations.reviewed_identity_migration_erasure_operations.operation_id",
            "operations.reviewed_identity_migration_erasure_operations.erasure_request_id",
        ],
        name="fk_subject_retrieval_block_request_operation",
    ),
    ForeignKeyConstraint(
        ["erasure_request_id", "person_id"],
        [
            "privacy.person_erasure_scopes.erasure_request_id",
            "privacy.person_erasure_scopes.person_id",
        ],
        name="fk_subject_retrieval_block_request_person",
    ),
    UniqueConstraint("operation_id", name="identity_erasure_block_operation"),
    UniqueConstraint("person_id", name="identity_erasure_block_person"),
    UniqueConstraint("block_id", "person_id", name="identity_erasure_block_identity"),
    UniqueConstraint("block_commitment", name="identity_erasure_block_commitment"),
    UniqueConstraint("ledger_epoch", name="identity_erasure_block_ledger_epoch"),
    UniqueConstraint("ledger_outbox_id", name="identity_erasure_block_ledger_outbox"),
    CheckConstraint(
        "(source_kind = 'erasure_request' "
        "AND operation_id IS NOT NULL AND erasure_request_id IS NOT NULL "
        "AND ((ledger_epoch IS NULL AND ledger_outbox_id IS NULL "
        "AND ledger_record_hash IS NULL AND ledger_record_digest IS NULL "
        "AND ledger_attached_at IS NULL) OR "
        "(ledger_epoch IS NOT NULL AND ledger_outbox_id IS NOT NULL "
        "AND ledger_record_hash IS NOT NULL AND ledger_record_digest IS NOT NULL "
        "AND ledger_attached_at IS NOT NULL))) OR "
        "(source_kind = 'ledger_replay' "
        "AND operation_id IS NULL AND erasure_request_id IS NULL "
        "AND ledger_epoch IS NOT NULL AND ledger_outbox_id IS NOT NULL "
        "AND ledger_record_hash IS NOT NULL AND ledger_record_digest IS NOT NULL "
        "AND ledger_attached_at IS NOT NULL)",
        name="identity_erasure_block_exact_source",
    ),
    CheckConstraint(
        "block_code = 'identity_person_erasure' "
        "AND outcome_code = 'retrieval_block_active'",
        name="identity_erasure_block_outcome",
    ),
    CheckConstraint(
        "block_commitment ~ '^[0-9a-f]{64}$'",
        name="identity_erasure_block_commitment_shape",
    ),
    CheckConstraint(
        "substring(block_id::text from 15 for 1) = '7' "
        "AND substring(block_id::text from 20 for 1) IN ('8','9','a','b') "
        "AND (operation_id IS NULL OR ("
        "substring(operation_id::text from 15 for 1) = '7' "
        "AND substring(operation_id::text from 20 for 1) IN ('8','9','a','b'))) ",
        name="identity_erasure_block_uuidv7",
    ),
    CheckConstraint(
        "ledger_epoch IS NULL OR (ledger_epoch > 0 "
        "AND ledger_record_hash ~ '^[0-9a-f]{64}$' "
        "AND ledger_record_digest ~ '^[0-9a-f]{64}$')",
        name="identity_erasure_block_ledger_shape",
    ),
    schema="privacy",
)


identity_person_erasure_residuals = Table(
    "identity_person_erasure_residuals",
    identity_erasure_metadata,
    Column("block_id", UUID_TYPE, primary_key=True),
    Column("residual_code", String(64), primary_key=True),
    Column("person_id", UUID_TYPE, nullable=False),
    Column("residual_class", String(32), nullable=False),
    Column("state", String(16), nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=TRANSACTION_TIME,
    ),
    Column("resolved_at", DateTime(timezone=True)),
    Column("residual_commitment", String(64), nullable=False),
    ForeignKeyConstraint(
        ["block_id", "person_id"],
        [
            "privacy.subject_retrieval_blocks.block_id",
            "privacy.subject_retrieval_blocks.person_id",
        ],
        name="fk_identity_person_erasure_residual_block",
    ),
    UniqueConstraint(
        "residual_commitment",
        name="identity_person_erasure_residual_commitment",
    ),
    CheckConstraint(
        "(residual_code, residual_class) IN ("
        "('live_identity_rows_retained','live'),"
        "('semantic_dependencies_not_evaluated','semantic'),"
        "('generic_artifact_lineage_unresolved','lineage'),"
        "('external_cleanup_not_evaluated','external'),"
        "('legacy_cleanup_not_evaluated','legacy'),"
        "('backup_expiry_unverified','backup'),"
        "('preexisting_snapshot_visibility','transaction'),"
        "('closure_limit_exceeded','closure'))",
        name="identity_person_erasure_residual_code_class",
    ),
    CheckConstraint(
        "(state = 'open' AND resolved_at IS NULL) OR "
        "(state = 'resolved' AND resolved_at IS NOT NULL "
        "AND resolved_at >= recorded_at)",
        name="identity_person_erasure_residual_state",
    ),
    CheckConstraint(
        "residual_commitment ~ '^[0-9a-f]{64}$'",
        name="identity_person_erasure_residual_commitment_shape",
    ),
    schema="privacy",
)


__all__ = (
    "identity_erasure_metadata",
    "identity_person_erasure_residuals",
    "subject_retrieval_blocks",
)
