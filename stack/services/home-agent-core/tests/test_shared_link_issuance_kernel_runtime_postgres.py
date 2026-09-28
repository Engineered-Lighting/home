"""Dormant entry-point boundaries on the guarded disposable hosted database.

Only temporary function/schema grants are committed, then removed. These tests
do not constitute positive owner-graph or concurrent-revocation acceptance.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .e1_postgres_harness import assert_guarded_database_url
from .test_shared_link_issuance_kernel_migration import migration
from app.shared_link_issuance import BEGIN


@pytest.fixture
def kernel_database():
    url = os.getenv("TEST_SHARED_LINK_CHALLENGE_ADMIN_DATABASE_URL")
    if not url:
        pytest.skip("guarded shared-link PostgreSQL URL required")
    assert_guarded_database_url(url)
    module = migration()
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    installed = False
    signature = f"{module.FUNCTION}({module.SIGNATURE})"
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == module.revision
            assert not conn.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"),
                {"role": module.COORDINATOR_ROLE, "fn": signature}).scalar_one()
            conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {module.COORDINATOR_ROLE}"))
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {module.COORDINATOR_ROLE}"))
        installed = True
        yield engine, module
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {module.COORDINATOR_ROLE}"))
                    conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {module.COORDINATOR_ROLE}"))
        finally:
            engine.dispose()


def params():
    return dict(ceremony_id=uuid.uuid4(), principal_id=uuid.uuid4(), person_id=uuid.uuid4(),
        legacy_binding_id=uuid.uuid4(), echo_subject="unbound-fixture",
        owner_commitment="a"*64, request_commitment="b"*64,
        echo_session_commitment="c"*64, victoria_session_commitment="d"*64,
        echo_challenge_id=uuid.uuid4(), victoria_challenge_id=uuid.uuid4(),
        echo_challenge_commitment="e"*64, victoria_challenge_commitment="f"*64,
        key_id="fixture-v1", key_fingerprint="0"*64)


@pytest.mark.parametrize("operation", [
    "SELECT * FROM identity.shared_link_ceremonies",
    "SELECT * FROM privacy.shared_owner_generations",
    "SET LOCAL ROLE home_agent_shared_link_issue_kernel",
])
def test_shared_link_issuance_kernel_caller_has_no_direct_authority(kernel_database, operation):
    engine, module = kernel_database
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            conn.execute(text(operation))
    assert error.value.orig.sqlstate == "42501"


def test_shared_link_issuance_kernel_rejects_administrator_caller(kernel_database):
    engine, _ = kernel_database
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(BEGIN, params())
    assert error.value.orig.sqlstate == "42501"


def test_shared_link_issuance_kernel_rejects_prior_write(kernel_database):
    engine, module = kernel_database
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            conn.execute(text("SELECT pg_current_xact_id()"))
            conn.execute(BEGIN, params())
    assert error.value.orig.sqlstate == "25001"


@pytest.mark.parametrize("setting", ["SET TRANSACTION READ ONLY", "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"])
def test_shared_link_issuance_kernel_requires_writable_serializable_transaction(kernel_database, setting):
    engine, module = kernel_database
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(text(setting))
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            conn.execute(BEGIN, params())
    assert error.value.orig.sqlstate == "25001"


def test_shared_link_issuance_kernel_roles_remain_dormant(kernel_database):
    engine, module = kernel_database
    with engine.connect() as conn:
        for role in (module.COORDINATOR_ROLE, module.KERNEL_ROLE):
            row = conn.execute(text("""SELECT rolcanlogin,rolsuper,rolbypassrls,rolinherit,
                rolcreaterole,rolcreatedb,rolreplication,rolconnlimit FROM pg_roles WHERE rolname=:role"""),
                {"role": role}).one()
            assert tuple(row) == (False, False, False, False, False, False, False, 0)
        assert not conn.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"),
            {"role": module.COORDINATOR_ROLE,
             "fn": f"identity.begin_shared_link_v1({module.SIGNATURE})"}).scalar_one()


def test_shared_link_issuance_kernel_unbound_owner_creates_nothing(kernel_database):
    engine, module = kernel_database
    values = params()
    with pytest.raises(DBAPIError) as error:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            conn.execute(BEGIN, values)
    assert error.value.orig.sqlstate == "42501"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM privacy.shared_owner_generations WHERE owner_commitment=:owner_commitment"), values).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), values).scalar_one() == 0
