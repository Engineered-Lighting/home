"""Committed confirmation acceptance in the dedicated runner-owned 0042 clone."""
import importlib.util
import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from .e1_postgres_harness import assert_guarded_database_url
from .test_shared_link_challenge_runtime_postgres import challenge_rows
from .test_shared_link_confirmation_runtime_postgres import confirmation
from .test_shared_link_session_runtime_postgres import REVOKE


def migration():
    path = Path(__file__).parents[1] / "alembic/versions/0042_shared_link_confirmation_kernel.py"
    spec = importlib.util.spec_from_file_location("confirmation_kernel_0042_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def database():
    url = os.getenv("TEST_SHARED_LINK_CONFIRM_KERNEL_ADMIN_DATABASE_URL")
    if not url: pytest.skip("dedicated guarded 0042 confirmation clone required")
    assert_guarded_database_url(url)
    module = migration()
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=2, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=8000"})
    signature = f"{module.FUNCTION}({module.SIGNATURE})"
    installed = False
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == "shared_link_confirm_kernel_0042"
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == module.revision
            graph = challenge_rows.__wrapped__(conn)
            params = confirmation.__wrapped__(conn, graph)
            assert not conn.execute(text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                {"role": module.COORDINATOR_ROLE, "signature": signature}).scalar_one()
            conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {module.COORDINATOR_ROLE}"))
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {module.COORDINATOR_ROLE}"))
        installed = True
        yield engine, module, graph, params
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {module.COORDINATOR_ROLE}"))
                    conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {module.COORDINATOR_ROLE}"))
        finally: engine.dispose()


def call(database, params):
    with database[0].begin() as conn:
        conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {database[1].COORDINATOR_ROLE}"))
        return dict(conn.execute(text(f"SELECT * FROM {database[1].FUNCTION}(:ceremony,:subject,:session,:revision,"
            ":confirmation,:digest,:proposal,:receipt,:link,:echo_binding,:victoria_binding)"), params).mappings().one())


def test_shared_link_confirmation_kernel_commit_replay_and_revocation(database):
    engine, module, graph, params = database
    with pytest.raises(DBAPIError) as error:
        call(database, {**params, "subject": "wrong-owner"})
    assert error.value.orig.sqlstate == "42501"
    receipt = call(database, params)
    assert receipt["link_id"] == params["link"] and receipt["authorization_generation"] == 2
    assert receipt["revision"] == 1 and call(database, params) == receipt
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_subject_bindings WHERE link_id=:link"), params).scalar_one() == 2
        assert conn.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == 0
        assert conn.execute(text("SELECT state FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony"), params).scalar_one() == "consumed"
    with pytest.raises(DBAPIError) as error:
        call(database, {**params, "confirmation": uuid.uuid4().hex*2})
    assert error.value.orig.sqlstate == "23505"
    child = graph[0][1]
    with engine.begin() as conn:
        conn.execute(REVOKE, {"issuer": child["issuer_id"], "session": child["session_commitment"], "id": uuid.uuid4()}).one()
    with pytest.raises(DBAPIError) as error:
        call(database, params)
    assert error.value.orig.sqlstate == "42501"
    # Session revocation blocks replay; it does not pretend to unlink a completed link.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT revoked_at FROM identity.shared_owner_links WHERE link_id=:link"), params).scalar_one() is None


def test_shared_link_confirmation_kernel_denies_direct_table_reads(database):
    with pytest.raises(DBAPIError) as error:
        with database[0].begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {database[1].COORDINATOR_ROLE}"))
            conn.execute(text("SELECT * FROM identity.shared_subject_bindings"))
    assert error.value.orig.sqlstate == "42501"
