from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "alembic/versions/0010_identity_erasure_operation_foundation.py"
)
OPERATION = "reviewed_identity_migration_erasure_operations"
IMPACT = "reviewed_identity_migration_erasure_impacts"


def _table(name: str):
    return schema.metadata.tables[f"operations.{name}"]


def _checks(name: str) -> str:
    return " ".join(
        str(item.sqltext)
        for item in _table(name).constraints
        if isinstance(item, CheckConstraint)
    )


def _foreign_keys(name: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in item.columns),
            tuple(element.target_fullname for element in item.elements),
        )
        for item in _table(name).constraints
        if isinstance(item, ForeignKeyConstraint)
    }


def _unique_columns(name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in item.columns)
        for item in _table(name).constraints
        if isinstance(item, UniqueConstraint)
    }


def test_operation_source_is_exact_normalized_and_content_minimized() -> None:
    operation = _table(OPERATION)
    assert set(operation.c.keys()) == {
        "operation_id",
        "source_kind",
        "erasure_request_id",
        "auto_expiry_schedule_id",
        "operation_commitment",
        "recorded_at",
    }
    ddl = str(CreateTable(operation).compile(dialect=postgresql.dialect()))
    assert "JSON" not in ddl
    assert "BYTEA" not in ddl
    assert not {
        "person_id",
        "principal_id",
        "display_name",
        "alias",
        "external_id",
        "payload",
        "content",
        "decision_id",
        "receipt_id",
    } & set(operation.c.keys())

    checks = _checks(OPERATION)
    assert "source_kind = 'erasure_request'" in checks
    assert "source_kind = 'auto_expiry_schedule'" in checks
    assert "erasure_request_id IS NOT NULL" in checks
    assert "auto_expiry_schedule_id IS NOT NULL" in checks
    assert "operation_commitment ~ '^[0-9a-f]{64}$'" in checks
    assert ("erasure_request_id",) in _unique_columns(OPERATION)
    assert ("auto_expiry_schedule_id",) in _unique_columns(OPERATION)
    assert ("operation_commitment",) in _unique_columns(OPERATION)

    foreign_keys = _foreign_keys(OPERATION)
    assert (
        ("erasure_request_id",),
        ("privacy.erasure_requests.erasure_request_id",),
    ) in foreign_keys
    assert (
        ("auto_expiry_schedule_id",),
        ("privacy.auto_expiry_schedules.schedule_id",),
    ) in foreign_keys


def test_impacts_reference_operation_without_retaining_leaf_authority() -> None:
    impact = _table(IMPACT)
    assert "operation_id" in impact.c
    assert "erasure_request_id" not in impact.c
    assert (
        ("operation_id",),
        (
            "operations."
            "reviewed_identity_migration_erasure_operations.operation_id",
        ),
    ) in _foreign_keys(IMPACT)
    assert ("run_id", "operation_id") in _unique_columns(IMPACT)
    for prohibited in ("decision_id", "receipt_id", "person_id", "principal_id"):
        assert prohibited not in impact.c


def test_0010_is_dormant_owner_only_and_handles_both_predecessor_shapes() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0010_identity_erasure_source"' in source
    assert 'down_revision: str | None = "0009_identity_finalizer_base"' in source
    assert "OLD_IMPACT_COLUMNS" in source
    assert "NEW_IMPACT_COLUMNS" in source
    assert "OPERATION_COLUMNS" in source
    assert "identity_erasure_operation_bootstrap_collision" in source
    assert "identity_erasure_operation_predecessor_invalid" in source
    assert "identity_erasure_operation_preexisting_evidence" in source
    assert "pg_catalog.pg_get_constraintdef" in source
    assert "actual_definitions IS DISTINCT FROM expected_definitions" in source
    assert "actual_policies IS DISTINCT FROM expected_policies" in source
    assert "operation_table.relowner = owner_oid" in source
    assert "impact_table.relowner = owner_oid" in source
    assert "reviewed_identity_migration_erasure_replay_guard" in source
    assert "guard_function.proowner = kernel_oid" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "TO home_agent_owner" in source
    assert "home_agent_identity_finalizer" in source
    assert "REVOKE ALL PRIVILEGES ON TABLE" in source
    assert "finalize_reviewed_identity_migration" not in source
    assert "CREATE FUNCTION" not in source
    assert "CREATE PROCEDURE" not in source
    assert "identity_erasure_operation_downgrade_blocked" in source
    assert "USING ERRCODE = '2BP01'" in source
    for evidence_table in (
        "reviewed_identity_migration_item_receipts",
        "reviewed_identity_migration_finalizations",
        "reviewed_identity_migration_projection_lineage",
        "reviewed_identity_migration_projection_subjects",
        "legacy_identity_writer_evidence",
        "privacy_cutover_check_receipts",
        "semantic_authority_cutovers",
    ):
        assert evidence_table in source
