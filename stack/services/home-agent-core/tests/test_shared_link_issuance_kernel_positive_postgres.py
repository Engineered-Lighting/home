"""Committed owner-graph acceptance in a dedicated, disposable 0040 clone.

The runner destroys this entire database. Roles are never altered; temporary
database-local grants are removed. No test is runnable without the host harness.
"""
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from app import schema
from app.shared_link_commitments import SharedLinkBegin, SharedLinkCommitments
from app.shared_link_issuance import BEGIN, LOOKUP, INSPECT
from .e1_postgres_harness import assert_guarded_database_url
from .test_shared_link_challenge_runtime_postgres import challenge_rows
from .test_shared_link_issuance_runtime_postgres import prepared, admitted
from .test_shared_link_issuance_kernel_migration import migration
from .test_shared_link_session_runtime_postgres import REVOKE


@pytest.fixture(scope="module")
def positive_database():
    url = os.getenv("TEST_SHARED_LINK_KERNEL_ADMIN_DATABASE_URL")
    if not url:
        pytest.skip("dedicated guarded shared-link kernel clone required")
    assert_guarded_database_url(url)
    module = migration()
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=2, max_overflow=0,
        connect_args={"connect_timeout": 5,
                      "options": "-c statement_timeout=10000 -c lock_timeout=8000"})
    signature = f"{module.FUNCTION}({module.SIGNATURE})"
    installed = False
    try:
        with engine.begin() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == "shared_link_kernel_0040"
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar_one()
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == module.revision
            assert not conn.execute(text("SELECT has_function_privilege(:role,:fn,'EXECUTE')"),
                {"role": module.COORDINATOR_ROLE, "fn": signature}).scalar_one()
            graph = challenge_rows.__wrapped__(conn)
            values = prepared.__wrapped__(conn, graph)
            admitted.__wrapped__(conn, values)
            conn.execute(text(f"GRANT USAGE ON SCHEMA identity TO {module.COORDINATOR_ROLE}"))
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {signature} TO {module.COORDINATOR_ROLE}"))
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {module.LOOKUP_FUNCTION}({module.LOOKUP_SIGNATURE}) TO {module.COORDINATOR_ROLE}"))
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {module.RECONCILE_FUNCTION}({module.RECONCILE_SIGNATURE}) TO {module.COORDINATOR_ROLE}"))
        installed = True
        yield engine, module, values
    finally:
        try:
            if installed:
                with engine.begin() as conn:
                    conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {module.COORDINATOR_ROLE}"))
                    conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {module.LOOKUP_FUNCTION}({module.LOOKUP_SIGNATURE}) FROM {module.COORDINATOR_ROLE}"))
                    conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {module.RECONCILE_FUNCTION}({module.RECONCILE_SIGNATURE}) FROM {module.COORDINATOR_ROLE}"))
                    conn.execute(text(f"REVOKE USAGE ON SCHEMA identity FROM {module.COORDINATOR_ROLE}"))
        finally:
            engine.dispose()


@pytest.fixture
def values(positive_database):
    base = positive_database[2]
    submission = SharedLinkBegin(
        **{key: base[key] for key in ("principal_id", "person_id", "legacy_binding_id", "echo_subject")},
        ceremony_id=uuid.uuid4(), echo_challenge_id=uuid.uuid4(), victoria_challenge_id=uuid.uuid4(),
        **{key: uuid.uuid4().hex*2 for key in ("echo_session_commitment", "victoria_session_commitment",
                                             "echo_challenge_commitment", "victoria_challenge_commitment")})
    value = SharedLinkCommitments(b"x"*32, key_id="fixture-v1").prepare(submission)
    return {**value.parameters(), "key_id": value.key_id, "key_fingerprint": value.key_fingerprint}


def issue(database, values):
    engine, module, _ = database
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
        # No graph setup or prior XID allocation occurs in this transaction.
        return dict(conn.execute(BEGIN, values).mappings().one())


def reject(database, values, code, message):
    with pytest.raises(DBAPIError) as error:
        issue(database, values)
    assert error.value.orig.sqlstate == code
    assert message in str(error.value.orig)
    with database[0].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), values).scalar_one() == 0


def test_shared_link_kernel_positive_commit_and_fresh_transaction_replay(positive_database, values):
    engine, module, _ = positive_database
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
        anchor = dict(conn.execute(LOOKUP, {"subject": values["echo_subject"]}).mappings().one())
    assert anchor == {key: values[key] for key in ("principal_id", "person_id", "legacy_binding_id")}
    first = issue(positive_database, values)
    assert issue(positive_database, values) == first
    assert first["authorization_generation"] == first["revision"] == 1
    assert (first["expires_at"]-first["created_at"]).total_seconds() == 300
    with positive_database[0].connect() as conn:
        rows = conn.execute(text("""SELECT issuer_id,registration_revision,session_commitment,
            challenge_commitment,created_at,expires_at FROM identity.shared_link_challenges
            WHERE ceremony_id=:ceremony_id ORDER BY issuer_id"""), values).mappings().all()
        assert len(rows) == 2
        for row, site in zip(rows, ("echo", "victoria")):
            assert row["issuer_id"] == f"home-assistant:{site}"
            assert row["registration_revision"] == first[f"{site}_registration_revision"]
            assert row["session_commitment"] == values[f"{site}_session_commitment"]
            assert row["challenge_commitment"] == values[f"{site}_challenge_commitment"]
            assert row["created_at"] == first["created_at"] and row["expires_at"] == first["expires_at"]
        assert conn.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE person_id=:person_id"), values).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == 0


@pytest.mark.parametrize("field", ["echo_subject", "principal_id", "person_id", "legacy_binding_id"])
def test_shared_link_kernel_positive_graph_rejects_wrong_owner(positive_database, values, field):
    changed = {**values, field: "different-subject" if field == "echo_subject" else uuid.uuid4()}
    reject(positive_database, changed, "42501", "shared_link_owner_unavailable")


@pytest.mark.parametrize("site", ["echo", "victoria"])
def test_shared_link_kernel_committed_session_revocation(positive_database, values, site):
    with positive_database[0].begin() as conn:
        conn.execute(REVOKE, {"issuer": f"home-assistant:{site}",
            "session": values[f"{site}_session_commitment"], "id": uuid.uuid4()}).one()
    reject(positive_database, values, "42501", "shared_link_session_revoked")


def test_shared_link_kernel_other_subject_person_block(positive_database, values):
    engine = positive_database[0]
    block_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(insert(schema.edge_privacy_user_blocks).values(block_id=block_id,
                ha_user_id=uuid.uuid4().hex, person_id=values["person_id"], reason_code="ignored"))
        reject(positive_database, values, "42501", "shared_link_owner_unavailable")
        with pytest.raises(DBAPIError) as error:
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {positive_database[1].COORDINATOR_ROLE}"))
                conn.execute(LOOKUP, {"subject": values["echo_subject"]}).one()
        assert error.value.orig.sqlstate == "42501"
    finally:
        with engine.begin() as conn:
            conn.execute(schema.edge_privacy_user_blocks.delete().where(schema.edge_privacy_user_blocks.c.block_id == block_id))
    # Removing only this fixture block restores the original admitted graph.
    assert issue(positive_database, values)["ceremony_id"] == values["ceremony_id"]


def test_shared_link_kernel_concurrent_revocation_observed_lock(positive_database, values):
    engine, module, _ = positive_database
    pids = Queue()

    def pending_issue():
        try:
            with engine.begin() as conn:
                pids.put(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
                conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
                conn.execute(BEGIN, values).one()
            return "committed"
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=1) as worker:
        with engine.connect() as blocker:
            transaction = blocker.begin()
            try:
                blocker.execute(REVOKE, {"issuer": "home-assistant:victoria",
                    "session": values["victoria_session_commitment"], "id": uuid.uuid4()}).one()
                future = worker.submit(pending_issue)
                pid = pids.get(timeout=5)
                deadline = time.monotonic()+5
                observed = False
                while time.monotonic() < deadline:
                    blocker.execute(text("SELECT pg_stat_clear_snapshot()"))
                    observed = blocker.execute(text("""SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                        WHERE pid=:pid AND wait_event_type='Lock')"""), {"pid": pid}).scalar_one()
                    if observed:
                        break
                    time.sleep(0.025)
                assert observed, "issuance never demonstrably waited on the revocation transaction"
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
        # The existing global write fence forces a stale SERIALIZABLE snapshot
        # to abort; this is not evidence that the request can be auto-retried.
        assert future.result(timeout=12) == "40001"
    reject(positive_database, values, "42501", "shared_link_session_revoked")


def test_shared_link_issuance_reconciliation_missing_never_creates_and_exact_preserves_receipt(positive_database, values):
    engine, module, _ = positive_database
    def inspect(candidate):
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {module.COORDINATOR_ROLE}"))
            row = conn.execute(INSPECT, candidate).mappings().one_or_none()
            return dict(row) if row is not None else None
    assert inspect(values) is None
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), values).scalar_one() == 0
    receipt = issue(positive_database, values)
    assert inspect(values) == receipt
    with pytest.raises(DBAPIError) as error:
        inspect({**values, "request_commitment": "0"*64})
    assert error.value.orig.sqlstate in ("42501", "23505")
    assert inspect(values) == receipt
