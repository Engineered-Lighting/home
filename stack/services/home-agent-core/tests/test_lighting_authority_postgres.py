"""Real lighting-role grant transaction on the guarded hosted 0047 clone.

Everything runs in one transaction that is rolled back (roles, policies,
registrations and grants included), so the clone is unchanged afterwards.
"""
import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import personal_memory_registration
from app.lighting_authority import ROLE, LightingConsentCommitment, LightingConsentConfirmation, LightingGrantStorage
from app.lighting_contract import CAPABILITY, SOURCE
from app.lighting_permissions import install_dormant_role, register_sources
from .e1_postgres_harness import assert_guarded_database_url


def test_lighting_role_writes_only_reviewed_lighting_grants():
    url = os.getenv("TEST_PERSONAL_PREFERENCE_AUTHORITY_ADMIN_DATABASE_URL")
    if not url: pytest.skip("guarded hosted preference clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url, isolation_level="SERIALIZABLE", pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("SELECT current_database()")).scalar_one() == "personal_preference_authority_0047"
                connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_owner"))
                personal_memory_registration.register_sources(connection)
                assert register_sources(connection) == 2
                assert register_sources(connection) == 0
                install_dormant_role(connection)
                with pytest.raises(ValueError):
                    install_dormant_role(connection)  # an existing role is never silently adopted
                connection.execute(text("RESET SESSION AUTHORIZATION"))
                assert connection.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname=:role"),
                                          {"role": ROLE}).scalar_one() is False
                link = connection.execute(text("SELECT * FROM identity.shared_owner_links WHERE revoked_at IS NULL")).mappings().one()
                subject = connection.execute(text("SELECT subject FROM identity.shared_subject_bindings WHERE link_id=:link "
                    "AND issuer_id='home-assistant:echo' AND revoked_at IS NULL"), {"link": link["link_id"]}).scalar_one()

                class Adapter:
                    async def execute(self, statement, parameters=None):
                        return connection.execute(statement, parameters or {})

                storage = LightingGrantStorage(LightingConsentCommitment(b"l" * 32))
                adapter = Adapter()
                connection.execute(text(f"SET LOCAL SESSION AUTHORIZATION {ROLE}"))
                # Row security confines the role to the lighting source.
                visible = connection.execute(text("SELECT DISTINCT source_id,capability FROM identity.shared_sources")).all()
                assert [tuple(row) for row in visible] == [(SOURCE, CAPABILITY)]
                authority = asyncio.run(storage.resolve(adapter, subject=subject, session_commitment="a" * 64))
                assert not authority.granted("echo") and not authority.granted("victoria")
                stamp = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                review = storage.commitment.prepare(uuid4(), authority, now=stamp, grants_expire_at=stamp + timedelta(days=30))
                confirmation = LightingConsentConfirmation(operation_id=review.operation_id,
                                                           reviewed_digest=review.reviewed_digest)
                assert asyncio.run(storage.consent_outcome(adapter, subject=subject, session_commitment="a" * 64,
                    authority=authority, review=review)) == "unknown"
                asyncio.run(storage.confirm(adapter, authority=authority, review=review, confirmation=confirmation))
                assert asyncio.run(storage.consent_outcome(adapter, subject=subject, session_commitment="a" * 64,
                    authority=authority, review=review)) == "committed"
                granted = asyncio.run(storage.resolve(adapter, subject=subject, session_commitment="a" * 64))
                assert granted.granted("echo") and granted.granted("victoria")
                rows = connection.execute(text("SELECT site_id,source_id,capability,revision FROM identity.shared_source_grants "
                                               "WHERE revoked_at IS NULL ORDER BY site_id")).all()
                assert [tuple(row) for row in rows] == [("echo", SOURCE, CAPABILITY, 1), ("victoria", SOURCE, CAPABILITY, 1)]
                # A subject without a current link cannot resolve authority.
                with pytest.raises(Exception):
                    asyncio.run(storage.resolve(adapter, subject="someone-else", session_commitment="a" * 64))
                # Direct SQL is constrained too, not only the fixed statements.
                for sql in (
                    "UPDATE identity.shared_source_grants SET revision=revision+1",
                    "DELETE FROM identity.shared_source_grants",
                    "INSERT INTO identity.shared_source_grants (grant_id,link_id,site_id,source_id,capability,source_revision,"
                    "authorization_generation,revision,approval_commitment,created_at,expires_at) SELECT gen_random_uuid(),"
                    "link_id,site_id,'core.personal-preferences.v1','memory.read',source_revision,authorization_generation,"
                    "revision+1,approval_commitment,created_at,expires_at FROM identity.shared_source_grants LIMIT 1",
                    "UPDATE identity.shared_owner_links SET revision=revision+1",
                    "UPDATE identity.shared_sources SET state='revoked'",
                ):
                    with connection.begin_nested() as savepoint:
                        with pytest.raises(DBAPIError) as denied:
                            connection.execute(text(sql))
                        # Permission or row-security denial, not some unrelated failure.
                        assert denied.value.orig.sqlstate == "42501", sql
                        savepoint.rollback()
            finally:
                transaction.rollback()
        with engine.connect() as connection:
            assert not connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"),
                                          {"role": ROLE}).scalar_one()
    finally:
        engine.dispose()
