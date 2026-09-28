"""Atomic association cases, only on the guarded disposable hosted database.

Administrator fixtures exercise transaction behavior, not issuer authentication
or production caller permissions. Every mutation rolls back with the fixture.
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_shared_link_challenge_runtime_postgres import guarded_connection, challenge_rows
from .test_shared_link_proof_transaction import migration

CALL = text("""SELECT * FROM identity.associate_shared_auth_proof_v1(
    :issuer,:id,:subject,:session,:challenge,:auth,:revision)""")


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


def params(challenge_rows, index=0):
    children, subject, _ = challenge_rows
    child = children[index]
    return dict(issuer=child["issuer_id"], id=uuid.uuid4(), subject=subject,
        session=child["session_commitment"], challenge=child["challenge_commitment"],
        auth=child["created_at"], revision=1)


def rejected(connection, values, code):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(CALL, values)
        assert error.value.orig.sqlstate == code
        savepoint.rollback()


@pytest.mark.parametrize("index", [0, 1])
def test_shared_link_proof_association_replay_is_exact_and_bounded(connection, challenge_rows, index):
    values = params(challenge_rows, index)
    first = dict(connection.execute(CALL, values).mappings().one())
    assert dict(connection.execute(CALL, values).mappings().one()) == first
    assert first["issuer_id"] == values["issuer"] and first["subject"] == values["subject"]
    assert first["expires_at"] == challenge_rows[0][index]["expires_at"]
    assert connection.execute(text("SELECT consumed_proof_id FROM identity.shared_link_challenges WHERE challenge_commitment=:challenge"),
                              values).scalar_one() == values["id"]
    rejected(connection, {**values, "id": uuid.uuid4()}, "23505")


@pytest.mark.parametrize("change", [
    {"session": "a"*64}, {"challenge": "b"*64}, {"revision": 2},
    {"issuer": "home-assistant:victoria"}, {"subject": "different-echo-owner"},
])
def test_shared_link_proof_rejects_wrong_scope(connection, challenge_rows, change):
    values = params(challenge_rows)
    rejected(connection, {**values, **change}, "42501")
    assert connection.execute(text("SELECT count(*) FROM identity.shared_auth_proofs WHERE proof_id=:id"), values).scalar_one() == 0


@pytest.mark.parametrize("mutation", ["generation", "cancel", "issuer", "expiry"])
def test_shared_link_proof_rechecks_authority_even_on_replay(connection, challenge_rows, mutation):
    values = params(challenge_rows)
    connection.execute(CALL, values).one()
    child = challenge_rows[0][0]
    if mutation == "generation":
        connection.execute(text("UPDATE privacy.shared_owner_generations SET authorization_generation=2,revision=2 WHERE owner_commitment=:owner"),
                           {"owner": challenge_rows[2]})
    elif mutation == "cancel":
        connection.execute(text("UPDATE identity.shared_link_ceremonies SET state='cancelled',ended_at=clock_timestamp() WHERE ceremony_id=:id"),
                           {"id": child["ceremony_id"]})
    elif mutation == "issuer":
        connection.execute(text("UPDATE identity.shared_issuers SET state='revoked',registration_revision=2 WHERE issuer_id=:issuer"), values)
    else:
        # Expire the parent (child consumption constraints remain intact).
        connection.execute(text("UPDATE identity.shared_link_ceremonies SET expires_at=created_at+interval '1 microsecond' WHERE ceremony_id=:id"),
                           {"id": child["ceremony_id"]})
    rejected(connection, values, "22023" if mutation == "expiry" else "42501")


def test_shared_link_proof_authentication_must_follow_challenge_creation(connection, challenge_rows):
    values = params(challenge_rows)
    rejected(connection, {**values, "auth": values["auth"]-timedelta(microseconds=1)}, "22023")


def test_shared_link_proof_both_homes_do_not_implicitly_link_or_grant_sources(connection, challenge_rows):
    count = text("SELECT (SELECT count(*) FROM identity.shared_owner_links), (SELECT count(*) FROM identity.shared_source_grants)")
    before = tuple(connection.execute(count).one())
    receipts = [connection.execute(CALL, params(challenge_rows, index)).mappings().one() for index in (0, 1)]
    assert receipts[0]["subject"] == receipts[1]["subject"]
    assert receipts[0]["issuer_id"] != receipts[1]["issuer_id"]
    assert receipts[0]["proof_id"] != receipts[1]["proof_id"]
    assert tuple(connection.execute(count).one()) == before
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_challenges WHERE ceremony_id=:id AND consumed_proof_id IS NOT NULL"),
        {"id": challenge_rows[0][0]["ceremony_id"]}).scalar_one() == 2


def test_shared_link_proof_child_failure_rolls_back_insert(connection, challenge_rows):
    values = params(challenge_rows)
    connection.execute(text("""CREATE FUNCTION pg_temp.reject_shared_child_update() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure' USING ERRCODE='23514'; END $$"""))
    connection.execute(text("""CREATE TRIGGER shared_child_failure BEFORE UPDATE ON identity.shared_link_challenges
        FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_shared_child_update()"""))
    rejected(connection, values, "23514")
    assert connection.execute(text("SELECT count(*) FROM identity.shared_auth_proofs WHERE proof_id=:id"), values).scalar_one() == 0
    assert connection.execute(text("SELECT consumed_proof_id FROM identity.shared_link_challenges WHERE challenge_commitment=:challenge"),
                              values).scalar_one() is None


def test_shared_link_proof_function_has_no_runtime_access(connection):
    result = connection.execute(text("""SELECT p.prosecdef,
        (SELECT count(*) FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner) other_acl
        FROM pg_proc p WHERE p.oid='identity.associate_shared_auth_proof_v1(text,uuid,text,text,text,timestamptz,bigint)'::regprocedure
    """)).mappings().one()
    assert not result["prosecdef"] and result["other_acl"] == 0
