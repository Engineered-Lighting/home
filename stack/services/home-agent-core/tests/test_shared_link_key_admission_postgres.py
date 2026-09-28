"""Owner admission of the coordinator commitment key in the guarded 0047 clone.

Everything runs in one owner transaction that is rolled back, so whatever an
earlier stage left in the clone's admission table is restored unchanged.
"""
import os

import pytest
from sqlalchemy import create_engine, text

from app import shared_link_key_admission as admission
from app.shared_link_commitments import SharedLinkCommitments
from .e1_postgres_harness import assert_guarded_database_url


def test_owner_admits_the_coordinator_key_once_and_refuses_a_different_key():
    url = os.getenv("TEST_PERSONAL_PREFERENCE_AUTHORITY_ADMIN_DATABASE_URL")
    if not url: pytest.skip("guarded hosted preference clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    key = SharedLinkCommitments(b"k" * 32, key_id="shared-link-fixture")
    other = SharedLinkCommitments(b"o" * 32, key_id="shared-link-fixture")
    snapshot = text("SELECT scope,key_id,key_fingerprint,revision,state FROM privacy.shared_link_key_admission ORDER BY scope")
    try:
        with engine.connect() as connection:
            before = [tuple(r) for r in connection.execute(snapshot).all()]
        with engine.connect() as connection:
            connection.execute(text("SET SESSION AUTHORIZATION home_agent_owner"))
            connection.commit()
            transaction = connection.begin()
            try:
                # Start from an empty table; the rollback below restores any prior row.
                connection.execute(text("DELETE FROM privacy.shared_link_key_admission"))
                assert admission.status(connection, key) == {"admitted": False}
                assert admission.admit(connection, key) == "admitted"
                row = connection.execute(text("SELECT scope,key_id,key_fingerprint,revision,state "
                                              "FROM privacy.shared_link_key_admission")).all()
                # Exactly the row shape the issuance kernel tests admit.
                assert [tuple(r) for r in row] == [("shared-link-v1", key.key_id, key.fingerprint, 1, "active")]
                assert admission.admit(connection, key) == "unchanged"
                assert admission.status(connection, key) == {
                    "admitted": True, "key_id": key.key_id, "revision": 1, "state": "active", "matches": True}
                assert admission.status(connection, other)["matches"] is False
                with pytest.raises(ValueError):
                    admission.admit(connection, other)
                connection.execute(text("UPDATE privacy.shared_link_key_admission SET state='blocked'"))
                with pytest.raises(ValueError):
                    admission.admit(connection, key)
            finally:
                transaction.rollback()
                connection.execute(text("RESET SESSION AUTHORIZATION"))
                connection.commit()
        with engine.connect() as connection:
            assert [tuple(r) for r in connection.execute(snapshot).all()] == before
    finally:
        engine.dispose()
