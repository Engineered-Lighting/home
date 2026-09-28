"""Offline confirmation migration checks; not PostgreSQL acceptance."""
import importlib.util
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0038_shared_link_confirmation.py"


def migration():
    spec = importlib.util.spec_from_file_location("shared_confirmation_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_link_confirmation_migration_closes_function_without_source_grants(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.down_revision == "0037_shared_link_session_v1"
    assert "SECURITY INVOKER" in sql and "SECURITY DEFINER" not in sql
    assert "aclexplode(proc.proacl)" in sql and "FROM PUBLIC" in sql
    assert "GRANT " not in sql and "shared_source_grants" not in sql
    assert "((state='consumed') = (confirmation_receipt_id IS NOT NULL))" in sql


def test_shared_link_confirmation_checks_deadline_after_writes():
    sql = migration().BODY
    assert sql.index("require_shared_link_owner_v1") < sql.index("stamp := pg_catalog.clock_timestamp()")
    assert sql.index("UPDATE identity.shared_link_ceremonies") < sql.index("IF pg_catalog.clock_timestamp()>=expiry")
    assert "echo_proof.authenticated_at<GREATEST(context.created_at,echo_proof.child_created_at)" in sql
    assert "victoria_proof.authenticated_at<GREATEST(context.created_at,victoria_proof.child_created_at)" in sql


def test_shared_link_confirmation_downgrade_refuses_receipt_history(monkeypatch):
    module = migration()
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.downgrade()
    sql = "\n".join(statements)
    assert module.revision in statements[0]
    assert "SET LOCAL row_security=off" in sql
    assert sql.index("IN ACCESS EXCLUSIVE MODE") < sql.index("shared_confirmation_downgrade_requires_empty_history")
    assert sql.index("shared_confirmation_downgrade_requires_empty_history") < sql.index("DROP FUNCTION")
    assert "CASCADE" not in sql and "DELETE " not in sql
