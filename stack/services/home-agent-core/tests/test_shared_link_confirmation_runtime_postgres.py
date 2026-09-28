"""Atomic final linking in the guarded disposable hosted fixture only."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_shared_link_challenge_runtime_postgres import guarded_connection, challenge_rows
from .test_shared_link_confirmation_migration import migration
from .test_shared_link_proof_runtime_postgres import CALL, params
from .test_shared_link_session_runtime_postgres import REVOKE

CONFIRM = text("""SELECT * FROM identity.confirm_shared_link_v1(:ceremony,:subject,:session,:revision,
    :confirmation,:digest,:proposal,:receipt,:link,:echo_binding,:victoria_binding)""")


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


@pytest.fixture
def confirmation(connection, challenge_rows):
    for index in (0, 1):
        connection.execute(CALL, params(challenge_rows, index)).one()
    ceremony = challenge_rows[0][0]["ceremony_id"]
    session = connection.execute(text("SELECT initiating_session_commitment FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"),
                                 {"id": ceremony}).scalar_one()
    return dict(ceremony=ceremony, subject=challenge_rows[1], session=session, revision=1,
        confirmation=uuid.uuid4().hex*2, digest=uuid.uuid4().hex*2,
        **{name: uuid.uuid4() for name in ("proposal", "receipt", "link", "echo_binding", "victoria_binding")})


def reject(connection, values, code):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(CONFIRM, values)
        assert error.value.orig.sqlstate == code
        savepoint.rollback()


def test_shared_link_confirmation_creates_one_link_two_bindings_no_grants_and_exact_replay(connection, challenge_rows, confirmation):
    grants = connection.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one()
    result = dict(connection.execute(CONFIRM, confirmation).mappings().one())
    assert result["link_id"] == confirmation["link"] and result["authorization_generation"] == 2 and result["revision"] == 1
    assert dict(connection.execute(CONFIRM, confirmation).mappings().one()) == result
    assert connection.execute(text("SELECT count(*) FROM identity.shared_subject_bindings WHERE link_id=:link"), confirmation).scalar_one() == 2
    assert connection.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == grants
    row = connection.execute(text("SELECT state,revision,confirmation_receipt_id FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony"), confirmation).one()
    assert tuple(row) == ("consumed", 2, confirmation["receipt"])
    assert connection.execute(text("""SELECT count(*) FROM identity.shared_auth_proofs proof
        JOIN identity.shared_link_challenges child ON child.consumed_proof_id=proof.proof_id
        WHERE child.ceremony_id=:ceremony AND proof.consumed_at=:stamp"""),
        {**confirmation, "stamp": result["confirmed_at"]}).scalar_one() == 2
    assert connection.execute(text("SELECT authorization_generation FROM privacy.shared_owner_generations WHERE owner_commitment=:owner"),
                              {"owner": challenge_rows[2]}).scalar_one() == 2


@pytest.mark.parametrize("field", ["confirmation", "digest", "proposal", "receipt", "link", "echo_binding", "victoria_binding", "revision"])
def test_shared_link_confirmation_changed_replay_conflicts(connection, confirmation, field):
    connection.execute(CONFIRM, confirmation).one()
    replacement = (uuid.uuid4().hex*2 if field in ("confirmation", "digest") else
                   2 if field == "revision" else uuid.uuid4())
    reject(connection, {**confirmation, field: replacement}, "23505")


@pytest.mark.parametrize("mutation", ["owner", "session", "generation", "issuer", "revoked_session", "expiry"])
def test_shared_link_confirmation_rejects_changed_authority(connection, challenge_rows, confirmation, mutation):
    values = dict(confirmation)
    code = "42501"
    if mutation == "owner":
        values["subject"] = "other-echo-owner"
    elif mutation == "session":
        values["session"] = "a"*64
    elif mutation == "generation":
        connection.execute(text("UPDATE privacy.shared_owner_generations SET authorization_generation=2 WHERE owner_commitment=:owner"),
                           {"owner": challenge_rows[2]})
        code = "23505"
    elif mutation == "issuer":
        connection.execute(text("UPDATE identity.shared_issuers SET registration_revision=2 WHERE issuer_id='home-assistant:victoria'"))
    elif mutation == "revoked_session":
        connection.execute(REVOKE, params(challenge_rows, 1)).one()
    else:
        connection.execute(text("UPDATE identity.shared_link_ceremonies SET expires_at=created_at+interval '1 microsecond' WHERE ceremony_id=:ceremony"), confirmation)
        code = "22023"
    reject(connection, values, code)
    assert connection.execute(text("SELECT count(*) FROM identity.shared_owner_links WHERE link_id=:link"), confirmation).scalar_one() == 0


def test_shared_link_confirmation_failed_generation_update_rolls_back_entire_link(connection, challenge_rows, confirmation):
    connection.execute(text("""CREATE FUNCTION pg_temp.reject_shared_generation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture failure' USING ERRCODE='23514'; END $$"""))
    connection.execute(text("""CREATE TRIGGER shared_generation_failure BEFORE UPDATE ON privacy.shared_owner_generations
        FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_shared_generation()"""))
    reject(connection, confirmation, "23514")
    for table, key in (("shared_owner_links", "link_id"), ("shared_subject_bindings", "link_id")):
        assert connection.execute(text(f"SELECT count(*) FROM identity.{table} WHERE {key}=:link"), confirmation).scalar_one() == 0
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_receipts WHERE receipt_id=:receipt"), confirmation).scalar_one() == 0
    assert connection.execute(text("SELECT count(*) FROM identity.shared_link_proposals WHERE proposal_id=:proposal"), confirmation).scalar_one() == 0
    assert connection.execute(text("""SELECT count(*) FROM identity.shared_auth_proofs proof
        JOIN identity.shared_link_challenges child ON child.consumed_proof_id=proof.proof_id
        WHERE child.ceremony_id=:ceremony AND proof.consumed_at IS NULL"""), confirmation).scalar_one() == 2


def test_shared_link_confirmation_downgrade_cannot_discard_receipt_association(connection, confirmation, monkeypatch):
    connection.execute(CONFIRM, confirmation).one()
    module = migration()
    monkeypatch.setattr(module.op, "execute", lambda statement: connection.execute(text(statement)))
    with connection.begin_nested() as savepoint:
        connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
        with pytest.raises(DBAPIError) as error:
            module.downgrade()
        assert error.value.orig.sqlstate in ("42501", "55000")
        assert ("row-level security" if error.value.orig.sqlstate == "42501" else
                "shared_confirmation_downgrade_requires_empty_history") in str(error.value.orig)
        savepoint.rollback()
    assert connection.execute(text("SELECT confirmation_receipt_id FROM identity.shared_link_ceremonies WHERE ceremony_id=:ceremony"), confirmation).scalar_one() == confirmation["receipt"]


def test_shared_link_confirmation_function_has_no_runtime_acl(connection):
    row = connection.execute(text("""SELECT p.prosecdef,
        (SELECT count(*) FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner)
        FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)"""),
        {"signature": f"{migration().FUNCTION}({migration().SIGNATURE})"}).one()
    assert tuple(row) == (False, 0)


def test_shared_link_confirmation_requires_both_associated_proofs(connection, confirmation):
    connection.execute(text("""UPDATE identity.shared_link_challenges
        SET consumed_proof_id=NULL,consumed_subject=NULL,consumed_at=NULL
        WHERE ceremony_id=:ceremony AND issuer_id='home-assistant:victoria'"""), confirmation)
    reject(connection, confirmation, "42501")


def test_shared_link_confirmation_exact_receipt_lookup_does_not_renew_expired_ceremony(connection, confirmation):
    receipt = dict(connection.execute(CONFIRM, confirmation).mappings().one())
    connection.execute(text("UPDATE identity.shared_link_ceremonies SET expires_at=ended_at+interval '1 microsecond' WHERE ceremony_id=:ceremony"), confirmation)
    assert dict(connection.execute(CONFIRM, confirmation).mappings().one()) == receipt


def test_shared_link_confirmation_revoked_session_cannot_lookup_but_does_not_unlink(connection, confirmation):
    connection.execute(CONFIRM, confirmation).one()
    connection.execute(REVOKE, dict(issuer="home-assistant:echo", session=confirmation["session"], id=uuid.uuid4())).one()
    reject(connection, confirmation, "42501")
    assert connection.execute(text("SELECT revoked_at FROM identity.shared_owner_links WHERE link_id=:link"), confirmation).scalar_one() is None
