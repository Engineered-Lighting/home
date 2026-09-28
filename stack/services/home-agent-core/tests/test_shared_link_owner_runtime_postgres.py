"""Real owner authority checks on the guarded disposable hosted cluster."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError

from app import schema
from .test_shared_link_challenge_runtime_postgres import guarded_connection, challenge_rows
from .test_shared_link_owner_authority import migration


@pytest.fixture
def connection():
    yield from guarded_connection(migration().revision)


def arguments(connection, challenge_rows):
    children, subject, _ = challenge_rows
    row = connection.execute(text("""SELECT principal_id,person_id,legacy_binding_id
        FROM identity.shared_link_ceremonies WHERE ceremony_id=:id"""),
        {"id": children[0]["ceremony_id"]}).mappings().one()
    return dict(subject=subject, principal=row["principal_id"], person=row["person_id"], binding=row["legacy_binding_id"])


CALL = text("SELECT identity.require_shared_link_owner_v1(:subject,:principal,:person,:binding)")


def reject(connection, params):
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            connection.execute(CALL, params)
        assert error.value.orig.sqlstate == "42501"
        assert "shared_link_owner_unavailable" in str(error.value.orig)
        savepoint.rollback()


def test_shared_link_owner_valid_complete_lineage(connection, challenge_rows):
    connection.execute(CALL, arguments(connection, challenge_rows)).one()


@pytest.mark.parametrize("field", ["subject", "principal", "person", "binding"])
def test_shared_link_owner_wrong_identity_rejected(connection, challenge_rows, field):
    params = arguments(connection, challenge_rows)
    params[field] = "different-ha-subject" if field == "subject" else uuid.uuid4()
    reject(connection, params)


@pytest.mark.parametrize("directive", ["auto_expire", "do_not_track", "ignored", "silent"])
def test_shared_link_owner_privacy_directives_block_immediately(connection, challenge_rows, directive):
    params = arguments(connection, challenge_rows)
    connection.execute(insert(schema.privacy_directives).values(directive_id=uuid.uuid4(),
        person_id=params["person"], directive=directive, enabled=True,
        expires_at=challenge_rows[0][0]["created_at"]+timedelta(days=1) if directive == "auto_expire" else None))
    reject(connection, params)


def test_shared_link_owner_edge_block_and_revoked_binding(connection, challenge_rows):
    params = arguments(connection, challenge_rows)
    connection.execute(insert(schema.edge_privacy_user_blocks).values(block_id=uuid.uuid4(),
        ha_user_id=params["subject"], person_id=params["person"], reason_code="ignored"))
    reject(connection, params)
    connection.execute(schema.edge_privacy_user_blocks.delete().where(
        schema.edge_privacy_user_blocks.c.ha_user_id == params["subject"]))
    connection.execute(schema.ha_user_bindings.update().where(
        schema.ha_user_bindings.c.binding_id == params["binding"]).values(revoked_at=challenge_rows[0][0]["created_at"]))
    reject(connection, params)


def test_shared_link_owner_function_has_no_runtime_acl(connection):
    row = connection.execute(text("""SELECT p.prosecdef,
        (SELECT count(*) FROM aclexplode(p.proacl) a WHERE a.grantee<>p.proowner) other_acl
        FROM pg_proc p WHERE p.oid='identity.require_shared_link_owner_v1(text,uuid,uuid,uuid)'::regprocedure
    """)).mappings().one()
    assert not row["prosecdef"] and row["other_acl"] == 0


def test_shared_link_owner_other_subject_block_still_blocks_same_person(connection, challenge_rows):
    params = arguments(connection, challenge_rows)
    connection.execute(insert(schema.edge_privacy_user_blocks).values(block_id=uuid.uuid4(),
        ha_user_id="historical-ha-subject", person_id=params["person"], reason_code="ignored"))
    reject(connection, params)
