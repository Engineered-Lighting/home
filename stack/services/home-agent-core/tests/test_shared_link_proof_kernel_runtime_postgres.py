"""Issuer-role acceptance in the runner-owned disposable 0041 clone only.

Owner graph setup uses the guarded administrator; every proof call is a fresh
transaction under an ingress session role. No role is made LOGIN-capable.
"""
import importlib.util
import os
from pathlib import Path
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from app.shared_link_challenge_schema import shared_link_ceremonies, shared_link_challenges
from app import schema
from .e1_postgres_harness import assert_guarded_database_url
from .test_shared_link_challenge_runtime_postgres import challenge_rows
from .test_shared_link_session_runtime_postgres import REVOKE


def migration():
    path = Path(__file__).parents[1] / "alembic/versions/0041_shared_link_proof_kernel.py"
    spec = importlib.util.spec_from_file_location("proof_kernel_0041_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def database():
    url = os.getenv("TEST_SHARED_LINK_PROOF_KERNEL_ADMIN_DATABASE_URL")
    if not url:
        pytest.skip("dedicated guarded 0041 proof-kernel clone required")
    assert_guarded_database_url(url)
    module = migration()
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=2, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=8000"})
    installed = False
    roles = (module.ECHO_ROLE, module.VICTORIA_ROLE)
    signature = f"{module.FUNCTION}({module.SIGNATURE})"
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == "shared_link_proof_kernel_0041"
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == module.revision
            graph = challenge_rows.__wrapped__(conn)
            parent = dict(conn.execute(text("SELECT * FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                {"id": graph[0][0]["ceremony_id"]}).mappings().one())
            for role in roles:
                assert not conn.execute(text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                    {"role": role, "signature": signature}).scalar_one()
                conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {role}"))
                conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}"))
        installed = True
        yield engine, module, graph, parent
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    for role in roles:
                        conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {role}"))
                        conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {role}"))
        finally:
            engine.dispose()


@pytest.fixture
def case(database):
    engine, _, graph, original = database
    parent = {**original, "ceremony_id": uuid.uuid4(), "request_commitment": uuid.uuid4().hex*2,
              "initiating_session_commitment": uuid.uuid4().hex*2}
    children = []
    with engine.begin() as conn:
        now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        parent.update(created_at=now, expires_at=now+timedelta(minutes=5))
        conn.execute(insert(shared_link_ceremonies).values(**{k: v for k, v in parent.items()
            if k in shared_link_ceremonies.c}))
        for index, old in enumerate(graph[0]):
            child = {**old, "challenge_id": uuid.uuid4(), "ceremony_id": parent["ceremony_id"],
                     "session_commitment": parent["initiating_session_commitment"] if index == 0 else uuid.uuid4().hex*2,
                     "challenge_commitment": uuid.uuid4().hex*2, "created_at": now,
                     "expires_at": now+timedelta(minutes=5)}
            conn.execute(insert(shared_link_challenges).values(**child))
            children.append(child)
    return parent, children, graph[1]


def values(case, index=0):
    _, children, subject = case
    child = children[index]
    return dict(id=uuid.uuid4(), subject=subject, session=child["session_commitment"],
        challenge=child["challenge_commitment"], auth=child["created_at"], revision=1)


def issue(database, params, index=0):
    engine, module, *_ = database
    role = (module.ECHO_ROLE, module.VICTORIA_ROLE)[index]
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
        return dict(conn.execute(text(f"SELECT * FROM {module.FUNCTION}(:id,:subject,:session,:challenge,:auth,:revision)"),
                                 params).mappings().one())


@pytest.mark.parametrize("index", [0, 1])
def test_shared_link_proof_kernel_positive_and_exact_replay(database, case, index):
    params = values(case, index)
    receipt = issue(database, params, index)
    assert issue(database, params, index) == receipt
    assert receipt["issuer_id"] == case[1][index]["issuer_id"]
    assert receipt["subject"] == case[2]
    assert receipt["expires_at"] == case[1][index]["expires_at"]
    with database[0].connect() as conn:
        assert conn.execute(text("SELECT consumed_proof_id FROM identity.shared_link_challenges WHERE challenge_commitment=:challenge"),
                            params).scalar_one() == params["id"]
        assert conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE person_id=:person_id"), case[0]).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == 0


@pytest.mark.parametrize("change", ["issuer", "session", "subject", "revision"])
def test_shared_link_proof_kernel_rejects_wrong_scope(database, case, change):
    params = values(case)
    if change == "session": params["session"] = uuid.uuid4().hex*2
    if change == "subject": params["subject"] = "different-owner"
    if change == "revision": params["revision"] = 2
    with pytest.raises(DBAPIError) as error:
        issue(database, params, 1 if change == "issuer" else 0)
    assert error.value.orig.sqlstate == "42501"
    with database[0].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs WHERE proof_id=:id"), params).scalar_one() == 0


@pytest.mark.parametrize("revoked_index", [0, 1])
def test_shared_link_proof_kernel_rechecks_both_sessions_on_replay(database, case, revoked_index):
    params = values(case)
    issue(database, params)
    child = case[1][revoked_index]
    with database[0].begin() as conn:
        conn.execute(REVOKE, {"issuer": child["issuer_id"], "session": child["session_commitment"], "id": uuid.uuid4()}).one()
    with pytest.raises(DBAPIError) as error:
        issue(database, params)
    assert error.value.orig.sqlstate == "42501"


def test_shared_link_proof_kernel_has_no_direct_table_access(database, case):
    with pytest.raises(DBAPIError) as error:
        with database[0].begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {database[1].ECHO_ROLE}"))
            conn.execute(text("SELECT * FROM identity.shared_auth_proofs"))
    assert error.value.orig.sqlstate == "42501"


def test_shared_link_proof_kernel_rechecks_other_account_privacy_block(database, case):
    params = values(case)
    issue(database, params)
    block_id = uuid.uuid4()
    try:
        with database[0].begin() as conn:
            conn.execute(insert(schema.edge_privacy_user_blocks).values(block_id=block_id,
                ha_user_id=uuid.uuid4().hex, person_id=case[0]["person_id"], reason_code="ignored"))
        with pytest.raises(DBAPIError) as error:
            issue(database, params)
        assert error.value.orig.sqlstate == "42501"
    finally:
        with database[0].begin() as conn:
            conn.execute(schema.edge_privacy_user_blocks.delete().where(schema.edge_privacy_user_blocks.c.block_id == block_id))


def test_shared_link_proof_kernel_rejects_changed_replay(database, case):
    params = values(case)
    original = issue(database, params)
    with pytest.raises(DBAPIError) as error:
        issue(database, {**params, "id": uuid.uuid4()})
    assert error.value.orig.sqlstate == "23505"
    assert issue(database, params) == original


def test_shared_link_proof_kernel_refuses_prior_write_transaction(database, case):
    params = values(case)
    module = database[1]
    with pytest.raises(DBAPIError) as error:
        with database[0].begin() as conn:
            conn.execute(text("SELECT pg_current_xact_id()"))
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.ECHO_ROLE}"))
            conn.execute(text(f"SELECT * FROM {module.FUNCTION}(:id,:subject,:session,:challenge,:auth,:revision)"), params)
    assert error.value.orig.sqlstate == "25001"
