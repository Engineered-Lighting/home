"""Offline migration/containment checks; PostgreSQL execution remains gated."""
import importlib.util
from pathlib import Path
from typing import get_args

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import ReadinessMigration
from app.identity_capabilities import supports_identity_capability
from app import schema
from app import shared_identity_schema as shared


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "alembic/versions/0032_shared_identity.py"


def migration():
    specification = importlib.util.spec_from_file_location("shared_identity_migration", PATH)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_migration_has_frozen_ddl_matching_isolated_schema():
    module = migration()
    assert module.revision == "0032_shared_identity_v1"
    assert module.down_revision == "0031_relationship_uniqueness_e5r"
    dialect = postgresql.dialect()
    declarations = []
    for table in shared.SHARED_IDENTITY_TABLES:
        declarations.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        declarations.extend(str(CreateIndex(index).compile(dialect=dialect)).strip()
                            for index in sorted(table.indexes, key=lambda item: item.name))
    # SQLAlchemy emits trailing spaces after commas; frozen source omits them.
    normalized = tuple("\n".join(line.rstrip() for line in sql.splitlines())
                       for sql in declarations)
    assert normalized == module.CREATE_STATEMENTS
    assert "from app" not in PATH.read_text(encoding="utf-8")
    assert not ({table.fullname for table in shared.SHARED_IDENTITY_TABLES}
                & set(schema.metadata.tables))


def test_upgrade_closes_every_new_table_without_grant_or_policy(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    combined = "\n".join(statements)
    for table in shared.SHARED_IDENTITY_TABLES:
        assert f"ALTER TABLE {table.fullname} ENABLE ROW LEVEL SECURITY" in combined
        assert f"ALTER TABLE {table.fullname} FORCE ROW LEVEL SECURITY" in combined
        assert f"REVOKE ALL ON TABLE {table.fullname} FROM PUBLIC" in combined
    assert "CREATE POLICY" not in combined
    assert "GRANT " not in combined
    assert "aclexplode" in combined  # Also removes default ACL recipients.
    assert "INSERT INTO" not in combined
    assert "UPDATE identity.ha_user_bindings" not in combined


def test_downgrade_locks_and_refuses_nonempty_authority_history(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    combined = "\n".join(statements)
    assert statements[0] == "SET LOCAL row_security = off;"
    assert "IN ACCESS EXCLUSIVE MODE" in statements[1]
    assert "shared_identity_downgrade_requires_empty_storage" in statements[2]
    for table in shared.SHARED_IDENTITY_TABLES:
        assert f"EXISTS (SELECT 1 FROM {table.fullname})" in statements[2]
        assert f"DROP TABLE {table.fullname}" in combined
    assert "CASCADE" not in combined
    assert "DELETE FROM" not in combined


def test_foundation_cannot_be_selected_as_runtime_schema():
    revision = migration().revision
    assert revision not in get_args(ReadinessMigration)
    for capability in ("principal_binding_confirmation", "parent_relationship_confirmation",
                       "owner_person_creation", "owner_relationship_attestation"):
        assert supports_identity_capability(revision, capability) is False
