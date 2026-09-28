"""Guarded hosted revocation behavior; no workstation database execution."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_shared_link_challenge_runtime_postgres import guarded_connection, challenge_rows
from .test_shared_link_session_migration import migration
from .test_shared_link_proof_runtime_postgres import (
    CALL, params, rejected,
    test_shared_link_proof_association_replay_is_exact_and_bounded,
    test_shared_link_proof_both_homes_do_not_implicitly_link_or_grant_sources,
    test_shared_link_proof_child_failure_rolls_back_insert,
)

REVOKE = text("SELECT * FROM identity.revoke_shared_link_session_v1(:issuer,:session,:id)")
REQUIRE = text("SELECT identity.require_shared_link_session_active_v1(:issuer,:session)")


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


def denied(connection, statement, values, code="42501"):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(statement, values)
        assert error.value.orig.sqlstate == code
        savepoint.rollback()


@pytest.mark.parametrize("target", ["initiator", "echo", "victoria"])
def test_shared_link_session_revocation_cancels_pending_and_rejects_proof_replay(connection, challenge_rows, target):
    child = challenge_rows[0][0]
    values = params(challenge_rows)
    connection.execute(CALL, values).one()
    if target == "initiator":
        session = connection.execute(text("SELECT initiating_session_commitment FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                                     {"id": child["ceremony_id"]}).scalar_one()
        revoke = dict(issuer="home-assistant:echo", session=session, id=uuid.uuid4())
    else:
        revoke = params(challenge_rows, 0 if target == "echo" else 1)
    receipt = dict(connection.execute(REVOKE, revoke).mappings().one())
    assert dict(connection.execute(REVOKE, revoke).mappings().one()) == receipt
    row = connection.execute(text("SELECT state,revision,ended_at FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                             {"id": child["ceremony_id"]}).one()
    assert tuple(row) == ("cancelled", 2, receipt["revoked_at"])
    rejected(connection, values, "42501")
    denied(connection, text("UPDATE identity.shared_link_ceremonies SET state='pending',ended_at=NULL WHERE ceremony_id=:id"),
           {"id": child["ceremony_id"]})
    denied(connection, REVOKE, {**revoke, "id": uuid.uuid4()}, "23505")


def test_shared_link_session_tombstone_precedes_any_matching_ceremony(connection, challenge_rows):
    values = dict(issuer="home-assistant:echo", session="f"*64, id=uuid.uuid4())
    connection.execute(REVOKE, values).one()
    child = challenge_rows[0][0]
    denied(connection, text("UPDATE identity.shared_link_ceremonies SET initiating_session_commitment=:session WHERE ceremony_id=:parent"),
           dict(session=values["session"], parent=child["ceremony_id"]))
    denied(connection, text("UPDATE identity.shared_link_challenges SET session_commitment=:session WHERE challenge_id=:child"),
           dict(session=values["session"], child=child["challenge_id"]))
    denied(connection, REQUIRE, values)
    # The same digest under another issuer is a separate session identity.
    connection.execute(REQUIRE, {**values, "issuer": "home-assistant:victoria"}).one()


def test_shared_link_session_cancellation_failure_rolls_back_tombstone(connection, challenge_rows):
    values = params(challenge_rows)
    connection.execute(text("""CREATE FUNCTION pg_temp.reject_shared_cancellation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure' USING ERRCODE='23514'; END $$"""))
    connection.execute(text("""CREATE TRIGGER shared_cancel_failure BEFORE UPDATE ON identity.shared_link_ceremonies
        FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_shared_cancellation()"""))
    denied(connection, REVOKE, values, "23514")
    assert connection.execute(text("SELECT count(*) FROM privacy.shared_link_session_revocations WHERE revocation_id=:id"), values).scalar_one() == 0
    connection.execute(REQUIRE, values).one()


def test_shared_link_session_revoked_child_cannot_move_to_another_pending_parent(connection, challenge_rows):
    values = params(challenge_rows, 1)
    connection.execute(CALL, values).one()
    connection.execute(REVOKE, values).one()
    replacement = uuid.uuid4()
    child = challenge_rows[0][1]
    connection.execute(text("""INSERT INTO identity.shared_link_ceremonies
        (ceremony_id,owner_commitment,principal_id,person_id,legacy_binding_id,
         initiating_session_commitment,request_commitment,purpose,authorization_generation,
         revision,state,created_at,expires_at)
        SELECT :replacement,owner_commitment,principal_id,person_id,legacy_binding_id,
         initiating_session_commitment,:request,purpose,authorization_generation,
         1,'pending',created_at,expires_at FROM identity.shared_link_ceremonies WHERE ceremony_id=:original"""),
        dict(replacement=replacement, request=uuid.uuid4().hex*2, original=child["ceremony_id"]))
    denied(connection, text("UPDATE identity.shared_link_challenges SET ceremony_id=:parent WHERE challenge_id=:child"),
           dict(parent=replacement, child=child["challenge_id"]))


def test_shared_link_session_downgrade_preserves_tombstones(connection, monkeypatch):
    values = dict(issuer="home-assistant:echo", session="e"*64, id=uuid.uuid4())
    connection.execute(REVOKE, values).one()
    module = migration()
    monkeypatch.setattr(module.op, "execute", lambda statement: connection.execute(text(statement)))
    with connection.begin_nested() as savepoint:
        connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
        with pytest.raises(DBAPIError) as error:
            module.downgrade()
        assert error.value.orig.sqlstate in ("42501", "55000")
        assert ("row-level security" if error.value.orig.sqlstate == "42501" else
                "shared_session_downgrade_requires_empty_history") in str(error.value.orig)
        savepoint.rollback()
    assert connection.execute(text("SELECT count(*) FROM privacy.shared_link_session_revocations WHERE revocation_id=:id"), values).scalar_one() == 1


def test_shared_link_session_storage_and_functions_have_no_runtime_acl(connection):
    row = connection.execute(text("""SELECT c.relrowsecurity,c.relforcerowsecurity,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid),
        (SELECT count(*) FROM aclexplode(c.relacl) a WHERE a.grantee<>c.relowner)
        FROM pg_class c WHERE c.oid='privacy.shared_link_session_revocations'::regclass""")).one()
    assert tuple(row) == (True, True, 0, 0)
    for signature in migration().FUNCTIONS:
        row = connection.execute(text("""SELECT p.prosecdef,
            (SELECT count(*) FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner)
            FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)"""), {"signature": signature}).one()
        assert tuple(row) == (False, 0)
