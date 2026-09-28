"""Real SQL cases for the guarded disposable hosted cluster only.

Fixtures briefly grant execution to NOLOGIN roles and impersonate them from the
guarded administrator connection. This is not runtime credential provisioning.
Fixture data/grants are removed; production tables must never be used here.
"""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
from threading import Barrier
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import DBAPIError

from app.shared_auth_proof import SharedAuthProofDatabase
from app import shared_auth_proof as proof_module

from .e1_postgres_harness import assert_guarded_database_url

ENV = "TEST_SHARED_AUTH_PROOF_ADMIN_DATABASE_URL"
ROLES = {site: f"home_agent_shared_{site}_proof_ingress" for site in ("echo", "victoria")}
SIGNATURE = "identity.issue_shared_auth_proof_v1(uuid,text,text,text,timestamptz,bigint)"
CALL = text("SELECT * FROM identity.issue_shared_auth_proof_v1(:id,:subject,:session,:challenge,:auth,:revision)")


@pytest.mark.asyncio
async def test_production_adapter_installs_server_deadlines_and_statement_timeout(monkeypatch):
    url = os.getenv(ENV)
    if not url:
        pytest.skip(f"{ENV} is required for guarded PostgreSQL execution")
    assert_guarded_database_url(url)
    # This verifies the actual adapter's connection startup options against the
    # disposable cluster. Only this fixture substitutes the guarded administrator
    # transport after production role validation; no proofs are issued here.
    # Production code has no administrator fallback or credential override.
    def fixture_transport(validated_url, **options):
        assert validated_url.username == ROLES["echo"]
        return create_async_engine(make_url(url).set(drivername="postgresql+psycopg"), **options)
    monkeypatch.setattr(proof_module, "create_async_engine", fixture_transport)
    database = SharedAuthProofDatabase("postgresql://home_agent_shared_echo_proof_ingress@fixture/unused",
                                       issuer_id="home-assistant:echo")
    try:
        async with database.engine.connect() as connection:
            for name, expected in (("statement_timeout", "7s"), ("lock_timeout", "5s"),
                                   ("idle_in_transaction_session_timeout", "10s")):
                assert (await connection.execute(text(f"SHOW {name}"))).scalar_one() == expected
            await connection.rollback()
            with pytest.raises(DBAPIError) as error:
                await connection.execute(text("SELECT pg_sleep(8)"))
            assert error.value.orig.sqlstate == "57014"
            await connection.rollback()
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await database.close()


@pytest.fixture
def proof_database():
    url = os.getenv(ENV)
    if not url:
        pytest.skip(f"{ENV} is required for guarded PostgreSQL execution")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=2, max_overflow=0,
        connect_args={"connect_timeout": 5,
                      "options": "-c statement_timeout=10000 -c lock_timeout=8000"})
    ids = set()
    installed = False
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0033_shared_auth_proof_v1"
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM identity.shared_issuers")).scalar_one() == 0
            for site, role in ROLES.items():
                state = conn.execute(text("SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role"), {"role": role}).one()
                assert tuple(state) == (False, False, False)
                assert not conn.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"), {"role": role, "fn": SIGNATURE}).scalar_one()
                conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {role}"))
                conn.execute(text(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {role}"))
                conn.execute(text("INSERT INTO identity.shared_issuers VALUES (:issuer,:site,1,'active')"),
                             {"issuer": f"home-assistant:{site}", "site": site})
        installed = True
        def issue(site="echo", params=None, *, before=None):
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {ROLES[site]}"))
                if before:
                    conn.execute(text(before))
                ids.add(params["id"])
                return dict(conn.execute(CALL, params).mappings().one())
        with engine.connect() as conn:
            now = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
        params = {"id": uuid.uuid4(), "subject": "same-ha-id", "session": "a" * 64,
                  "challenge": uuid.uuid4().hex * 2, "auth": now, "revision": 1}
        yield engine, issue, params
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    for proof_id in ids:
                        conn.execute(text("DELETE FROM identity.shared_auth_proofs WHERE proof_id=:id"), {"id": proof_id})
                    for site, role in ROLES.items():
                        conn.execute(text("DELETE FROM identity.shared_issuers WHERE issuer_id=:issuer"), {"issuer": f"home-assistant:{site}"})
                        conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM {role}"))
                        conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {role}"))
        finally:
            engine.dispose()


def rejected(operation, state):
    with pytest.raises(DBAPIError) as error:
        operation()
    assert error.value.orig.sqlstate == state


def test_exact_replay_retains_original_receipt_and_each_issuer_namespaces_subject(proof_database):
    _, issue, params = proof_database
    first = issue(params=params)
    assert issue(params=params) == first
    assert first["issuer_id"] == "home-assistant:echo"
    assert first["registration_revision"] == 1
    assert first["expires_at"] == params["auth"] + timedelta(minutes=5)
    other = issue("victoria", {**params, "id": uuid.uuid4(), "challenge": uuid.uuid4().hex * 2})
    assert other["subject"] == first["subject"]
    assert other["issuer_id"] == "home-assistant:victoria"


@pytest.mark.parametrize("change", [
    {"subject": "changed"}, {"session": "b" * 64}, {"challenge": "c" * 64},
])
def test_changed_replay_fails(proof_database, change):
    _, issue, params = proof_database
    issue(params=params)
    rejected(lambda: issue(params={**params, **change}), "23505")


def test_consumed_proof_and_cross_issuer_collision_cannot_replay(proof_database):
    engine, issue, params = proof_database
    issue(params=params)
    rejected(lambda: issue("victoria", params), "23505")
    with engine.begin() as conn:
        conn.execute(text("UPDATE identity.shared_auth_proofs SET consumed_at=clock_timestamp() WHERE proof_id=:id"), params)
    rejected(lambda: issue(params=params), "23505")


def test_revoked_registration_invalidates_existing_receipt_lookup(proof_database):
    engine, issue, params = proof_database
    issue(params=params)
    with engine.begin() as conn:
        conn.execute(text("UPDATE identity.shared_issuers SET state='revoked',registration_revision=2 WHERE site_id='echo'"))
    rejected(lambda: issue(params=params), "42501")


@pytest.mark.parametrize("change,state", [
    ({"revision": 2}, "42501"), ({"subject": " padded "}, "22023"),
    ({"session": "bad"}, "22023"), ({"revision": 0}, "22023"),
])
def test_invalid_submission_fails(proof_database, change, state):
    _, issue, params = proof_database
    rejected(lambda: issue(params={**params, **change}), state)


def test_expired_time_and_prior_write_fail(proof_database):
    _, issue, params = proof_database
    rejected(lambda: issue(params={**params, "auth": params["auth"] - timedelta(minutes=6)}), "22023")
    rejected(lambda: issue(params=params, before="SELECT pg_current_xact_id()"), "25001")


def test_caller_cannot_read_storage_or_assume_kernel_owner(proof_database):
    _, issue, params = proof_database
    rejected(lambda: issue(params=params, before="SELECT * FROM identity.shared_auth_proofs"), "42501")
    rejected(lambda: issue(params=params, before="SET LOCAL ROLE home_agent_shared_proof_kernel"), "42501")


def test_simultaneous_challenge_claims_create_at_most_one_proof(proof_database):
    engine, issue, params = proof_database
    barrier = Barrier(2)
    def compete(proof_id):
        barrier.wait(timeout=5)
        try:
            return issue(params={**params, "id": proof_id})
        except DBAPIError as error:
            assert error.orig.sqlstate in ("23505", "40001")
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(compete, uuid.uuid4()) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(result is not None for result in results) == 1
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs WHERE challenge_commitment=:challenge"), params).scalar_one() == 1


def wait_for_row_lock(connection, tag):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        connection.execute(text("SELECT pg_stat_clear_snapshot()"))
        if connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name=:tag AND wait_event_type='Lock')"), {"tag": tag}).scalar_one():
            return
        time.sleep(0.02)
    pytest.fail("proof kernel was not observed waiting for the held issuer lock")


def test_revocation_committed_while_issuance_waits_cannot_issue_stale_proof(proof_database):
    engine, issue, params = proof_database
    tag = "sharedproof_" + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=1) as worker:
        with engine.begin() as locker:
            locker.execute(text("UPDATE identity.shared_issuers SET state='revoked',registration_revision=2 WHERE site_id='echo'"))
            pending = worker.submit(issue, params=params, before=f"SET LOCAL application_name='{tag}'")
            wait_for_row_lock(locker, tag)
        with pytest.raises(DBAPIError) as error:
            pending.result(timeout=10)
        assert error.value.orig.sqlstate in ("40001", "42501")
    rejected(lambda: issue(params=params), "42501")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs")).scalar_one() == 0


def test_proof_expiring_during_observed_lock_wait_is_rejected(proof_database):
    engine, issue, params = proof_database
    tag = "sharedproof_" + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=1) as worker:
        with engine.begin() as locker:
            locker.execute(text("SELECT issuer_id FROM identity.shared_issuers WHERE site_id='echo' FOR UPDATE"))
            # Leave three seconds of validity: enough to observe the lock, while
            # keeping this deterministic hosted case below the eight-second limit.
            params = {**params, "auth": locker.execute(text("SELECT clock_timestamp()-interval '4 minutes 57 seconds'")).scalar_one()}
            pending = worker.submit(issue, params=params, before=f"SET LOCAL application_name='{tag}'")
            wait_for_row_lock(locker, tag)
            deadline = time.monotonic() + 4
            while locker.execute(text("SELECT clock_timestamp() > :expires"),
                {"expires": params["auth"] + timedelta(minutes=5)}).scalar_one() is False:
                if time.monotonic() >= deadline:
                    pytest.fail("database clock did not advance through the bounded expiry window")
                time.sleep(0.02)
        rejected(lambda: pending.result(timeout=10), "22023")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_auth_proofs")).scalar_one() == 0


def test_populated_proof_prevents_actual_downgrade(proof_database, monkeypatch):
    engine, issue, params = proof_database
    receipt = issue(params=params)
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0033_shared_auth_proof_ingress.py"
    spec = importlib.util.spec_from_file_location("shared_proof_downgrade_pg", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            # Match the migration's actual owner login boundary. The hosted
            # owner is expected to have maintenance visibility through RLS.
            conn.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
            monkeypatch.setattr(migration.op, "execute", lambda sql: conn.execute(text(sql)))
            with pytest.raises(DBAPIError) as error:
                migration.downgrade()
            assert error.value.orig.sqlstate in ("55000", "42501")
            # 42501 is acceptable only when forced RLS blocks visibility;
            # an unrelated ownership/function error must not mask a bad test.
            if error.value.orig.sqlstate == "42501":
                assert "row-level security" in str(error.value.orig)
            else:
                assert "shared_proof_downgrade_requires_empty_storage" in str(error.value.orig)
        finally:
            transaction.rollback()
        assert conn.execute(text("SELECT registration_revision FROM identity.shared_auth_proofs WHERE proof_id=:id"), params).scalar_one() == receipt["registration_revision"]
        assert conn.execute(text("SELECT to_regprocedure(:name) IS NOT NULL"), {"name": SIGNATURE}).scalar_one()
