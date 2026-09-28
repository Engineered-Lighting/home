"""Offline migration contract; SQL execution belongs to the pinned gate."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0036_shared_link_proof_transaction.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_link_proof_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_link_proof_transaction_is_internal_and_removes_default_acl(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0035_shared_link_owner_v1"
    assert "SECURITY INVOKER" in sql and "SECURITY DEFINER" not in sql
    assert "SET search_path = pg_catalog" in sql and "SET row_security = on" in sql
    assert "FROM PUBLIC" in sql and "aclexplode(proc.proacl)" in sql
    assert "GRANT " not in sql and "CREATE POLICY" not in sql
    assert "from app" not in PATH.read_text(encoding="utf-8")


def test_shared_link_proof_transaction_checks_time_after_authority_locks():
    sql = migration().BODY
    assert sql.index("privacy.lock_identity_semantic_write_fence()") < sql.index("FOR UPDATE OF generation, parent, child")
    assert sql.index("require_shared_link_owner_v1") < sql.index("checked_at := pg_catalog.clock_timestamp()")
    assert sql.index("checked_at :=") < sql.index("INSERT INTO identity.shared_auth_proofs")
    assert "context.expires_at, context.parent_expires_at" in sql
    assert "existing.expires_at <> expiry" in sql


def test_shared_link_proof_transaction_downgrade_preserves_data(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    assert module.revision in statements[0]
    assert "current_user <> 'home_agent_owner'" in statements[0]
    assert "session_user <> 'home_agent_owner'" in statements[0]
    assert statements[1] == f"DROP FUNCTION {module.FUNCTION}({module.SIGNATURE});"
    assert len(statements) == 2
