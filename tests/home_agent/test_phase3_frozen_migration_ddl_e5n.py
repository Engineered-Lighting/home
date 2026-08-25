"""Migrations must not render DDL from the mutable ``app.schema`` module.

``app.schema`` describes the CURRENT shape of each table and is rewritten
whenever a later revision alters one. A revision that renders its own DDL from
it silently changes what it emits as the schema moves on. Revision 0010 rewrites
the erasure-impacts table, which made revision 0007 emit a foreign key to a table
0010 does not create until three revisions later, so the dormant 0007-0013 chain
could not apply at all.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "stack/services/home-agent-core/alembic/versions"
FOUNDATION = VERSIONS / "0007_phase3_identity_authority_foundation.py"

# Tables revision 0007 installs, in creation order.
FROZEN_TABLES = (
    "reviewed_identity_migration_runs",
    "reviewed_identity_migration_source_items",
    "reviewed_identity_migration_decisions",
    "reviewed_identity_migration_item_receipts",
    "reviewed_identity_migration_finalizations",
    "legacy_identity_writer_evidence",
    "privacy_cutover_check_receipts",
    "semantic_authority_cutovers",
    "reviewed_identity_migration_erasure_impacts",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_e5n_foundation_does_not_render_ddl_from_app_schema() -> None:
    source = _read(FOUNDATION)

    assert "from app import schema" not in source
    assert "CreateTable(table" not in source
    assert "CreateTable(schema." not in source


def test_e5n_foundation_freezes_every_table_it_installs() -> None:
    source = _read(FOUNDATION)

    assert "FROZEN_TABLE_DDL = {" in source
    for table in FROZEN_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS operations.{table} (" in source or (
            f"CREATE TABLE IF NOT EXISTS privacy.{table} (" in source
        )
    assert source.count("CREATE TABLE IF NOT EXISTS ") == len(FROZEN_TABLES)


def test_e5n_erasure_impacts_is_frozen_at_its_pre_0010_shape() -> None:
    """0010 rewrites this table; 0007 must install the shape 0010 expects."""

    source = _read(FOUNDATION)

    # The pre-0010 shape carries erasure_request_id and its request-scoped
    # constraints, and must NOT reference the operation table 0010 creates.
    assert "erasure_request_id UUID NOT NULL" in source
    assert "fk_identity_migration_erasure_request" in source
    assert "identity_erasure_impact_request UNIQUE (run_id, erasure_request_id)" in source
    assert "reviewed_identity_migration_erasure_operations" not in source
    assert "fk_identity_migration_erasure_operation" not in source
    assert "operation_id" not in source


def test_e5n_frozen_shape_matches_what_0010_declares_as_its_predecessor() -> None:
    """0010 encodes the predecessor shape it will migrate; 0007 must match it."""

    foundation = _read(FOUNDATION)
    source = _read(VERSIONS / "0010_identity_erasure_operation_foundation.py")

    start = source.index("OLD_IMPACT_COLUMNS = (")
    end = source.index(")", start)
    for entry in source[start:end].splitlines():
        entry = entry.strip().strip('",')
        if "|" not in entry:
            continue
        column = entry.split("|", 1)[0]
        assert f"\t{column} " in foundation, column
