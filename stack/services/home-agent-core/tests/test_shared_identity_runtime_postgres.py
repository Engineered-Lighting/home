"""Execute 0032 storage invariants only on the guarded, disposable E1 cluster.

All changes, including role/ownership changes and successful downgrade DDL,
are rolled back. This is not authentication/kernel or live deployment evidence.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .e1_postgres_harness import assert_guarded_database_url


OWNER_DATABASE_ENV = "TEST_SHARED_IDENTITY_OWNER_DATABASE_URL"
REVISION = "0032_shared_identity_v1"
MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0032_shared_identity.py"


def _migration():
    spec = importlib.util.spec_from_file_location("shared_identity_pg_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connection():
    url = os.getenv(OWNER_DATABASE_ENV)
    if not url:
        pytest.skip(f"{OWNER_DATABASE_ENV} is required for guarded PostgreSQL execution")
    assert_guarded_database_url(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
                yield conn
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _reject(connection, statement, params, sqlstate):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(text(statement), params)
        assert error.value.orig.sqlstate == sqlstate
        savepoint.rollback()


def _issuers(connection):
    connection.execute(text("""
        INSERT INTO identity.shared_issuers
          (issuer_id, site_id, registration_revision, state)
        VALUES ('home-assistant:echo','echo',1,'active'),
               ('home-assistant:victoria','victoria',1,'active')
    """))


def _history(connection):
    record_id = uuid.uuid4()
    connection.execute(text("""
        INSERT INTO privacy.shared_identity_revocations
          (revocation_id, owner_commitment, scope_commitment, kind,
           authorization_generation, revoked_at)
        VALUES (:id,:owner,:scope,'owner_unlink',1,now())
    """), {"id": record_id, "owner": "a" * 64, "scope": "b" * 64})
    return record_id


def _bind_downgrade(monkeypatch, connection):
    module = _migration()
    monkeypatch.setattr(module.op, "execute", lambda sql: connection.execute(text(sql)))
    return module


def test_all_nine_tables_are_forced_rls_without_policy_or_nonowner_acl(connection):
    tables = _migration().TABLE_NAMES
    assert len(tables) == 9
    for table in tables:
        row = connection.execute(text("""
            SELECT c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS policies,
                   (SELECT count(*) FROM aclexplode(c.relacl) a
                     WHERE a.grantee <> c.relowner) AS other_acl
              FROM pg_class c WHERE c.oid=to_regclass(:name)
        """), {"name": table}).mappings().one()
        assert row["relrowsecurity"] and row["relforcerowsecurity"]
        assert row["policies"] == 0 and row["other_acl"] == 0


def test_same_subject_proofs_are_distinct_by_registered_issuer(connection):
    _issuers(connection)
    for index, issuer in enumerate(("echo", "victoria")):
        connection.execute(text("""
            INSERT INTO identity.shared_auth_proofs
              (proof_id,issuer_id,subject,session_commitment,challenge_commitment,
               authenticated_at,issued_at,expires_at)
            VALUES (:id,:issuer,'same-ha-subject',:session,:challenge,
                    now(),now(),now()+interval '4 minutes')
        """), {"id": uuid.uuid4(), "issuer": f"home-assistant:{issuer}",
                "session": "c" * 64, "challenge": str(index) * 64})
    assert connection.execute(text("SELECT count(*) FROM identity.shared_auth_proofs")).scalar_one() == 2


@pytest.mark.parametrize("issuer,site", [
    ("home-assistant:echo", "victoria"), ("home-assistant:unknown", "unknown")])
def test_invalid_issuer_registration_is_rejected(connection, issuer, site):
    _reject(connection, """INSERT INTO identity.shared_issuers
        (issuer_id,site_id,registration_revision,state) VALUES (:issuer,:site,1,'active')""",
        {"issuer": issuer, "site": site}, "23514")


@pytest.mark.parametrize("source,capability", [("*", "memory.read"), ("camera", "*")])
def test_wildcard_source_or_capability_is_rejected(connection, source, capability):
    _issuers(connection)
    _reject(connection, """INSERT INTO identity.shared_sources
        (site_id,source_id,capability,issuer_id,registration_revision,state)
        VALUES ('echo',:source,:capability,'home-assistant:echo',1,'active')""",
        {"source": source, "capability": capability}, "23514")


def test_proof_window_cannot_exceed_five_minutes(connection):
    _issuers(connection)
    _reject(connection, """INSERT INTO identity.shared_auth_proofs
        (proof_id,issuer_id,subject,session_commitment,challenge_commitment,
         authenticated_at,issued_at,expires_at)
        VALUES (:id,'home-assistant:echo','subject',:session,:challenge,
                now(),now(),now()+interval '5 minutes 1 second')""",
        {"id": uuid.uuid4(), "session": "c" * 64, "challenge": "d" * 64}, "23514")


def test_populated_history_refuses_downgrade_and_preserves_tables(connection, monkeypatch):
    record_id = _history(connection)
    migration = _bind_downgrade(monkeypatch, connection)
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            migration.downgrade()
        assert error.value.orig.sqlstate == "55000"
        assert "shared_identity_downgrade_requires_empty_storage" in str(error.value.orig)
        savepoint.rollback()
    assert connection.execute(text("SELECT revocation_id FROM privacy.shared_identity_revocations")).scalar_one() == record_id
    for table in migration.TABLE_NAMES:
        assert connection.execute(text("SELECT to_regclass(:table)"), {"table": table}).scalar_one()


def test_empty_downgrade_really_drops_tables_but_savepoint_restores_them(connection, monkeypatch):
    migration = _bind_downgrade(monkeypatch, connection)
    for table in migration.TABLE_NAMES:
        assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with connection.begin_nested() as savepoint:
        migration.downgrade()
        for table in migration.TABLE_NAMES:
            assert connection.execute(text("SELECT to_regclass(:table)"), {"table": table}).scalar_one() is None
        savepoint.rollback()
    for table in migration.TABLE_NAMES:
        assert connection.execute(text("SELECT to_regclass(:table)"), {"table": table}).scalar_one()


def test_forced_rls_owner_cannot_mistake_hidden_history_for_empty_storage(connection, monkeypatch):
    record_id = _history(connection)
    migration = _bind_downgrade(monkeypatch, connection)
    role = connection.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname='home_agent_api'")).one()
    assert not role.rolsuper and not role.rolbypassrls
    # Fixture-only ownership eligibility; outer transaction rolls this back.
    connection.execute(text("GRANT USAGE, CREATE ON SCHEMA identity, privacy TO home_agent_api"))
    for table in migration.TABLE_NAMES:
        connection.execute(text(f"ALTER TABLE {table} OWNER TO home_agent_api"))
    connection.execute(text("SET LOCAL ROLE home_agent_api"))
    # Demonstrate the actual false-empty precondition, not just an arbitrary error.
    assert connection.execute(text("SELECT count(*) FROM privacy.shared_identity_revocations")).scalar_one() == 0
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            migration.downgrade()
        assert error.value.orig.sqlstate == "42501"
        assert "row-level security" in str(error.value.orig)
        savepoint.rollback()
    connection.execute(text("RESET ROLE"))
    assert connection.execute(text("SELECT revocation_id FROM privacy.shared_identity_revocations")).scalar_one() == record_id
    for table in migration.TABLE_NAMES:
        assert connection.execute(text("SELECT to_regclass(:table)"), {"table": table}).scalar_one()
