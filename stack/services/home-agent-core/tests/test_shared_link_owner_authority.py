"""Offline migration checks; actual authority acceptance is hosted-only."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0035_shared_link_owner_authority.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_owner_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_link_owner_migration_removes_default_function_access(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0034_shared_link_challenges_v1"
    assert "SECURITY INVOKER" in module.BODY and "SECURITY DEFINER" not in module.BODY
    assert "SET search_path = pg_catalog" in module.BODY and "SET row_security = on" in module.BODY
    assert "FROM PUBLIC" in sql and "aclexplode(proc.proacl)" in sql
    assert "acl.grantee <> proc.proowner" in sql
    assert "GRANT " not in sql and "CREATE POLICY" not in sql
    assert "from app" not in PATH.read_text(encoding="utf-8")


def test_shared_link_owner_check_fences_before_reading_and_locks_lineage():
    sql = migration().BODY
    assert sql.index("privacy.lock_identity_semantic_write_fence()") < sql.index("FROM identity.ha_user_bindings")
    assert "FOR SHARE OF b, p, person, proposal, request, artifact" in sql
    assert "privacy.identity_person_is_blocked(p_person_id)" in sql
    assert "'auto_expire','do_not_track','ignored','silent'" in sql
    assert "directive.expires_at" not in sql


def test_shared_link_owner_downgrade_has_no_data_mutation(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    assert module.revision in statements[0]
    assert "current_user <> 'home_agent_owner'" in statements[0]
    assert "session_user <> 'home_agent_owner'" in statements[0]
    assert statements[1] == f"DROP FUNCTION {module.FUNCTION}({module.SIGNATURE});"
    assert len(statements) == 2
