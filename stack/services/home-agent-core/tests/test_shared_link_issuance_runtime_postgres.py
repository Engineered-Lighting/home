"""Actual issuance path using real preparation, only in the guarded hosted DB."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.shared_link_commitments import SharedLinkBegin, SharedLinkCommitments
from .test_shared_link_challenge_runtime_postgres import guarded_connection, challenge_rows
from .test_shared_link_issuance_migration import migration
from .test_shared_link_proof_runtime_postgres import CALL
from .test_shared_link_confirmation_runtime_postgres import CONFIRM
from .test_shared_link_session_runtime_postgres import REVOKE

BEGIN = text("""SELECT * FROM identity.begin_shared_link_v1(
 :ceremony_id,:principal_id,:person_id,:legacy_binding_id,:echo_subject,:owner_commitment,:request_commitment,
 :echo_session_commitment,:victoria_session_commitment,:echo_challenge_id,:victoria_challenge_id,
 :echo_challenge_commitment,:victoria_challenge_commitment,:key_id,:key_fingerprint)""")


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


@pytest.fixture
def prepared(connection, challenge_rows):
    child = challenge_rows[0][0]
    anchor = dict(connection.execute(text("SELECT principal_id,person_id,legacy_binding_id FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                                     {"id": child["ceremony_id"]}).mappings().one())
    # Retain the reviewed legacy anchor, remove only this transaction's synthetic
    # shared rows so issuance must create its own parent/children and generation.
    connection.execute(text("DELETE FROM identity.shared_link_challenges WHERE ceremony_id=:id"), {"id": child["ceremony_id"]})
    connection.execute(text("DELETE FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"), {"id": child["ceremony_id"]})
    connection.execute(text("DELETE FROM privacy.shared_owner_generations WHERE owner_commitment=:owner"), {"owner": challenge_rows[2]})
    factory = SharedLinkCommitments(b"x"*32, key_id="fixture-v1")
    submission = SharedLinkBegin(**anchor, ceremony_id=uuid.uuid4(), echo_subject=challenge_rows[1],
        echo_session_commitment=uuid.uuid4().hex*2, victoria_session_commitment=uuid.uuid4().hex*2,
        echo_challenge_id=uuid.uuid4(), victoria_challenge_id=uuid.uuid4(),
        echo_challenge_commitment=uuid.uuid4().hex*2, victoria_challenge_commitment=uuid.uuid4().hex*2)
    value = factory.prepare(submission)
    return {**value.parameters(), "key_id": value.key_id, "key_fingerprint": value.key_fingerprint}


@pytest.fixture
def admitted(connection, prepared):
    connection.execute(text("""INSERT INTO privacy.shared_link_key_admission
        (scope,key_id,key_fingerprint,revision,state) VALUES ('shared-link-v1',:key_id,:key_fingerprint,1,'active')"""), prepared)
    return prepared


def reject(connection, values, code):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(BEGIN, values)
        assert error.value.orig.sqlstate == code
        savepoint.rollback()


def test_shared_link_issuance_exact_replay_preserves_two_children_and_deadline(connection, admitted):
    first = dict(connection.execute(BEGIN, admitted).mappings().one())
    assert dict(connection.execute(BEGIN, admitted).mappings().one()) == first
    assert first["authorization_generation"] == 1 and first["revision"] == 1
    assert (first["expires_at"]-first["created_at"]).total_seconds() == 300
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_challenges WHERE ceremony_id=:ceremony_id"), admitted).scalar_one() == 2


def test_shared_link_issuance_fails_without_admitted_key(connection, prepared):
    reject(connection, prepared, "42501")
    assert connection.execute(text("SELECT count(*) FROM privacy.shared_owner_generations WHERE owner_commitment=:owner_commitment"), prepared).scalar_one() == 0


@pytest.mark.parametrize("field,value", [("key_id", "another-key"), ("key_fingerprint", "f"*64),
    ("echo_subject", "wrong-owner"), ("principal_id", uuid.UUID(int=99))])
def test_shared_link_issuance_rejects_wrong_key_or_owner(connection, admitted, field, value):
    reject(connection, {**admitted, field: value}, "42501")


def test_shared_link_issuance_preserves_blocked_generation(connection, admitted):
    connection.execute(text("""INSERT INTO privacy.shared_owner_generations
        (owner_commitment,authorization_generation,revision,state,updated_at)
        VALUES (:owner_commitment,9,9,'blocked',clock_timestamp())"""), admitted)
    reject(connection, admitted, "42501")
    assert connection.execute(text("SELECT authorization_generation FROM privacy.shared_owner_generations WHERE owner_commitment=:owner_commitment"), admitted).scalar_one() == 9


def test_shared_link_issuance_reuses_existing_active_generation(connection, admitted):
    connection.execute(text("""INSERT INTO privacy.shared_owner_generations
        (owner_commitment,authorization_generation,revision,state,updated_at)
        VALUES (:owner_commitment,9,9,'active',clock_timestamp())"""), admitted)
    assert connection.execute(BEGIN, admitted).mappings().one()["authorization_generation"] == 9


def test_shared_link_issuance_live_queue_is_bounded_but_exact_replay_still_works(connection, admitted):
    first = dict(connection.execute(BEGIN, admitted).mappings().one())
    for _ in range(31):
        extra = {**admitted, "ceremony_id": uuid.uuid4(), "request_commitment": uuid.uuid4().hex*2,
            "echo_challenge_id": uuid.uuid4(), "victoria_challenge_id": uuid.uuid4(),
            "echo_challenge_commitment": uuid.uuid4().hex*2, "victoria_challenge_commitment": uuid.uuid4().hex*2}
        connection.execute(BEGIN, extra).one()
    reject(connection, {**admitted, "ceremony_id": uuid.uuid4(), "request_commitment": uuid.uuid4().hex*2}, "54000")
    assert dict(connection.execute(BEGIN, admitted).mappings().one()) == first


def test_shared_link_issuance_expired_replay_does_not_renew_deadline(connection, admitted):
    connection.execute(BEGIN, admitted).one()
    # Keep parent and child snapshots consistent so expiry, not tuple mismatch,
    # is the reason that replay is refused.
    connection.execute(text("UPDATE identity.shared_link_ceremonies SET expires_at=created_at+interval '1 microsecond' WHERE ceremony_id=:ceremony_id"), admitted)
    connection.execute(text("UPDATE identity.shared_link_challenges SET expires_at=created_at+interval '1 microsecond' WHERE ceremony_id=:ceremony_id"), admitted)
    reject(connection, admitted, "22023")


@pytest.mark.parametrize("field", ["request_commitment", "victoria_session_commitment", "victoria_challenge_commitment", "echo_challenge_id"])
def test_shared_link_issuance_changed_replay_conflicts(connection, admitted, field):
    connection.execute(BEGIN, admitted).one()
    reject(connection, {**admitted, field: uuid.uuid4() if field.endswith("_id") else "f"*64}, "23505")


def test_shared_link_issuance_revoked_session_never_creates_parent(connection, admitted):
    connection.execute(REVOKE, dict(issuer="home-assistant:victoria", session=admitted["victoria_session_commitment"], id=uuid.uuid4())).one()
    reject(connection, admitted, "42501")
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), admitted).scalar_one() == 0


def test_shared_link_issuance_child_failure_rolls_back_parent_and_generation(connection, admitted):
    connection.execute(text("""CREATE FUNCTION pg_temp.reject_shared_child_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure' USING ERRCODE='23514'; END $$"""))
    connection.execute(text("""CREATE TRIGGER shared_child_insert_failure BEFORE INSERT ON identity.shared_link_challenges
        FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_shared_child_insert()"""))
    reject(connection, admitted, "23514")
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony_id"), admitted).scalar_one() == 0
    assert connection.execute(text("SELECT count(*) FROM privacy.shared_owner_generations WHERE owner_commitment=:owner_commitment"), admitted).scalar_one() == 0


def test_shared_link_issuance_through_both_proofs_and_confirmation(connection, admitted):
    issued = dict(connection.execute(BEGIN, admitted).mappings().one())
    for site in ("echo", "victoria"):
        connection.execute(CALL, dict(issuer=f"home-assistant:{site}", id=uuid.uuid4(), subject=admitted["echo_subject"],
            session=admitted[f"{site}_session_commitment"], challenge=admitted[f"{site}_challenge_commitment"],
            auth=issued["created_at"], revision=issued[f"{site}_registration_revision"])).one()
    final = dict(ceremony=admitted["ceremony_id"], subject=admitted["echo_subject"], session=admitted["echo_session_commitment"],
        revision=1, confirmation=uuid.uuid4().hex*2, digest=uuid.uuid4().hex*2,
        **{name: uuid.uuid4() for name in ("proposal", "receipt", "link", "echo_binding", "victoria_binding")})
    receipt = connection.execute(CONFIRM, final).mappings().one()
    assert receipt["authorization_generation"] == 2 and receipt["link_id"] == final["link"]
    assert connection.execute(text("SELECT count(*) FROM identity.shared_source_grants WHERE link_id=:link"), final).scalar_one() == 0


def test_shared_link_issuance_downgrade_preserves_admission(connection, admitted, monkeypatch):
    module = migration()
    monkeypatch.setattr(module.op, "execute", lambda statement: connection.execute(text(statement)))
    with connection.begin_nested() as savepoint:
        connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
        with pytest.raises(DBAPIError) as error:
            module.downgrade()
        assert error.value.orig.sqlstate in ("42501", "55000")
        assert ("row-level security" if error.value.orig.sqlstate == "42501" else
                "shared_issuance_downgrade_requires_empty_key_admission") in str(error.value.orig)
        savepoint.rollback()


def test_shared_link_issuance_storage_and_function_are_closed(connection):
    row = connection.execute(text("""SELECT c.relrowsecurity,c.relforcerowsecurity,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid),
        (SELECT count(*) FROM aclexplode(c.relacl) a WHERE a.grantee<>c.relowner)
        FROM pg_class c WHERE c.oid='privacy.shared_link_key_admission'::regclass""")).one()
    assert tuple(row) == (True, True, 0, 0)
    row = connection.execute(text("""SELECT p.prosecdef,(SELECT count(*) FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner)
        FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)"""),
        {"signature": f"{migration().FUNCTION}({migration().SIGNATURE})"}).one()
    assert tuple(row) == (False, 0)
