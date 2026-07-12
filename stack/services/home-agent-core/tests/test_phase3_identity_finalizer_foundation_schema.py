from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import schema


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0009_identity_finalizer_foundation.py"
LINEAGE_TABLE = "reviewed_identity_migration_projection_lineage"
SUBJECT_TABLE = "reviewed_identity_migration_projection_subjects"


def _table(name: str):
    return schema.metadata.tables[f"operations.{name}"]


def _checks(name: str) -> str:
    return " ".join(
        str(item.sqltext)
        for item in _table(name).constraints
        if isinstance(item, CheckConstraint)
    )


def _unique_columns(name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in item.columns)
        for item in _table(name).constraints
        if isinstance(item, UniqueConstraint)
    }


def _foreign_keys(name: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in item.columns),
            tuple(element.target_fullname for element in item.elements),
        )
        for item in _table(name).constraints
        if isinstance(item, ForeignKeyConstraint)
    }


def test_projection_lineage_is_normalized_content_minimized_and_offline_compilable() -> (
    None
):
    lineage = _table(LINEAGE_TABLE)
    subjects = _table(SUBJECT_TABLE)

    assert set(lineage.c.keys()) == {
        "lineage_id",
        "run_id",
        "receipt_id",
        "decision_id",
        "decision_kind",
        "projection_table_kind",
        "projection_id",
        "projection_ref_commitment",
        "lineage_commitment",
        "linked_at",
    }
    assert set(subjects.c.keys()) == {"lineage_id", "person_id", "subject_role"}

    for table in (lineage, subjects):
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "JSON" not in ddl
        assert "BYTEA" not in ddl
        assert not {
            "display_name",
            "alias",
            "source_ref",
            "source_path",
            "external_id",
            "payload",
            "content",
            "before_json",
            "after_json",
            "ha_user_id",
            "principal_id",
        } & set(table.c.keys())


def test_each_lineage_row_is_bound_to_one_exact_apply_receipt() -> None:
    uniques = _unique_columns(LINEAGE_TABLE)
    assert ("run_id", "receipt_id") in uniques or ("receipt_id",) in uniques

    exact_receipt_columns = (
        "run_id",
        "receipt_id",
        "decision_id",
        "decision_kind",
        "projection_table_kind",
        "projection_ref_commitment",
    )
    exact_receipt_targets = tuple(
        f"operations.reviewed_identity_migration_item_receipts.{column}"
        for column in exact_receipt_columns
    )
    assert (exact_receipt_columns, exact_receipt_targets) in _foreign_keys(
        LINEAGE_TABLE
    )

    checks = _checks(LINEAGE_TABLE)
    assert "lineage_commitment ~ '^[0-9a-f]{64}$'" in checks
    assert "projection_ref_commitment ~ '^[0-9a-f]{64}$'" in checks
    for decision_kind, projection_kind in (
        ("person", "identity.people"),
        ("person_status", "identity.people"),
        ("alias", "identity.aliases"),
        ("recognition_binding", "identity.external_recognition_bindings"),
        ("privacy_directive", "identity.privacy_directives"),
        ("legacy_role_candidate", "identity.legacy_role_labels"),
        (
            "legacy_relationship_candidate",
            "identity.legacy_relationship_candidates",
        ),
    ):
        assert decision_kind in checks
        assert projection_kind in checks
    assert "parent_of" not in checks


def test_projection_subjects_are_person_fk_roles_without_parent_authority() -> None:
    subjects = _table(SUBJECT_TABLE)
    assert list(subjects.primary_key.columns.keys()) == [
        "lineage_id",
        "person_id",
        "subject_role",
    ]
    assert ("lineage_id", "subject_role") in _unique_columns(SUBJECT_TABLE)

    foreign_keys = _foreign_keys(SUBJECT_TABLE)
    assert (
        ("lineage_id",),
        (f"operations.{LINEAGE_TABLE}.lineage_id",),
    ) in foreign_keys
    assert (
        ("person_id",),
        ("identity.people.person_id",),
    ) in foreign_keys

    checks = _checks(SUBJECT_TABLE)
    for role in ("primary", "from", "to"):
        assert role in checks
    for prohibited in ("parent", "parent_of", "child", "authoritative"):
        assert prohibited not in checks


def test_0009_is_schema_only_force_rls_and_downgrade_refuses_lineage_loss() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "0009_identity_finalizer_base"' in source
    assert 'down_revision: str | None = "0008_identity_migration_kernel"' in source
    for table in (LINEAGE_TABLE, SUBJECT_TABLE):
        assert table in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "home_agent_identity_finalizer_kernel" in source
    assert "home_agent_identity_finalizer" in source
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES" in source
    assert "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS" in source
    assert "REVOKE USAGE ON TYPE %I.%I" in source
    assert "REVOKE USAGE, CREATE ON SCHEMA" in source
    assert "identity_finalizer_foundation_name_occupied" in source
    assert "identity_finalizer_receipt_constraint_occupied" in source
    assert "bootstrap_rows <> 0" in source
    assert "pg_catalog.pg_get_constraintdef" in source
    assert (
        "DROP TABLE operations.reviewed_identity_migration_projection_subjects"
        in source
    )
    assert "finalization_enabled" in source
    assert "false" in source.lower()
    assert "finalize_reviewed_identity_migration" not in source
    assert "parent_of" not in source
    assert "parent_confirmation" not in source
    assert "EXISTS (SELECT 1 FROM" in source
    assert "refusing" in source.lower()
    assert "USING ERRCODE = '2BP01'" in source
