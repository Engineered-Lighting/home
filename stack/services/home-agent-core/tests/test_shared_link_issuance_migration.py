"""Offline issuance contracts, not PostgreSQL execution."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0039_shared_link_issuance.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_issuance_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_link_issuance_closes_key_admission_and_caller(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0038_shared_link_confirm_v1"
    assert "FORCE ROW LEVEL SECURITY" in sql and "SECURITY INVOKER" in sql
    assert "aclexplode(relation.relacl)" in sql and "aclexplode(proc.proacl)" in sql
    assert "GRANT " not in sql and "CREATE POLICY" not in sql
    assert f"INSERT INTO {module.TABLE}" not in sql
    assert "key_fingerprint=p_key_fingerprint FOR SHARE" in module.BODY


def test_shared_link_issuance_never_resets_existing_generation_or_replay_expiry():
    sql = migration().BODY
    assert "ON CONFLICT (owner_commitment) DO NOTHING" in sql
    assert "IF NOT FOUND OR generation.state<>'active'" in sql
    assert "UPDATE privacy.shared_owner_generations" not in sql
    assert "prior.created_at,prior.expires_at" in sql
    assert ">=32 THEN" in sql


def test_shared_link_issuance_downgrade_preserves_key_admission(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    sql = "\n".join(statements)
    assert module.revision in statements[0]
    assert "SET LOCAL row_security=off" in sql
    assert sql.index("IN ACCESS EXCLUSIVE MODE") < sql.index("shared_issuance_downgrade_requires_empty_key_admission")
    assert sql.index("shared_issuance_downgrade_requires_empty_key_admission") < sql.index("DROP FUNCTION")
    assert "CASCADE" not in sql and "DELETE " not in sql
