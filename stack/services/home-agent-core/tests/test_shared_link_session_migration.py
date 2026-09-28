"""Offline contracts for durable linking-session revocation."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0037_shared_link_session_revocation.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_session_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_link_session_migration_closes_storage_and_functions(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0036_shared_link_proof_v1"
    assert "FORCE ROW LEVEL SECURITY" in sql and "ENABLE ROW LEVEL SECURITY" in sql
    assert "FOREIGN KEY" not in module.CREATE_TABLE
    assert "PRIMARY KEY (issuer_id,session_commitment)" in module.CREATE_TABLE
    assert "aclexplode(relation.relacl)" in sql and "aclexplode(proc.proacl)" in sql
    assert sql.count("SECURITY INVOKER") == 3 and "SECURITY DEFINER" not in sql
    assert "GRANT " not in sql and "CREATE POLICY" not in sql


def test_shared_link_session_cancellation_and_write_guards_share_fence():
    module = migration()
    assert "privacy.lock_identity_semantic_write_fence()" in module.REVOKE_BODY
    assert "privacy.lock_identity_semantic_write_fence()" in module.REQUIRE_BODY
    assert "parent.state='pending'" in module.REVOKE_BODY
    assert "parent.revision+1" in module.REVOKE_BODY
    assert "revoked.issuer_id=child.issuer_id" in module.TRIGGER_BODY
    assert len(module.TRIGGERS) == 3


def test_shared_link_session_downgrade_refuses_history_before_removing_guards(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    sql = "\n".join(statements)
    assert module.revision in statements[0]
    assert "SET LOCAL row_security=off" in sql
    assert sql.index("IN ACCESS EXCLUSIVE MODE") < sql.index("shared_session_downgrade_requires_empty_history")
    assert sql.index("shared_session_downgrade_requires_empty_history") < sql.index("DROP TRIGGER")
    assert "CASCADE" not in sql and "DELETE " not in sql
