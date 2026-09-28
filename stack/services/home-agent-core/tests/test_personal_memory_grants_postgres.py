"""Real consent-role grant transaction on the guarded hosted 0047 clone."""
import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.personal_memory_consent import SharingConfirmation, SharingReviewCommitment
from app.personal_memory_grants import PreferenceGrantStorage, ROLE
from app.personal_memory_grant_permissions import install_dormant_role
from app.personal_memory_registration import register_sources
from .e1_postgres_harness import assert_guarded_database_url


def test_consent_role_writes_only_reviewed_preference_grants():
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
                install_dormant_role(connection)
                register_sources(connection)
                register_sources(connection)
                connection.execute(text("RESET SESSION AUTHORIZATION"))
                assert connection.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname=:role"), {"role": ROLE}).scalar_one() is False
                link = connection.execute(text("SELECT * FROM identity.shared_owner_links WHERE revoked_at IS NULL")).mappings().one()
                subject = connection.execute(text("SELECT subject FROM identity.shared_subject_bindings WHERE link_id=:link AND issuer_id='home-assistant:echo' AND revoked_at IS NULL"), {"link": link["link_id"]}).scalar_one()

                class Adapter:
                    async def execute(self, statement, parameters=None):
                        return connection.execute(statement, parameters or {})

                storage = PreferenceGrantStorage(SharingReviewCommitment(b"s"*32))
                adapter = Adapter()
                connection.execute(text(f"SET LOCAL SESSION AUTHORIZATION {ROLE}"))
                authority = asyncio.run(storage.resolve(adapter, subject=subject, session_commitment="a"*64))
                stamp = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                review = storage.commitment.prepare(uuid4(), authority, now=stamp, grants_expire_at=stamp+timedelta(days=30))
                confirmation = SharingConfirmation(operation_id=review.operation_id, reviewed_digest=review.reviewed_digest)
                assert asyncio.run(storage.outcome(adapter, subject=subject, session_commitment="a"*64,
                    authority=authority, review=review)) == "unknown"
                asyncio.run(storage.confirm(adapter, authority=authority, review=review, confirmation=confirmation))
                assert asyncio.run(storage.outcome(adapter, subject=subject, session_commitment="a"*64,
                    authority=authority, review=review)) == "committed"
                rows = connection.execute(text("SELECT site_id,capability,revision FROM identity.shared_source_grants WHERE revoked_at IS NULL")).all()
                assert len(rows) == 4 and all(row.revision == 1 for row in rows)
                # RLS/column privileges must also constrain direct SQL, not only
                # the application's fixed statement strings.
                for sql in (
                    "UPDATE identity.shared_source_grants SET revision=revision+1",
                    "DELETE FROM identity.shared_source_grants",
                    "UPDATE identity.shared_owner_links SET link_id=link_id",
                    "UPDATE identity.shared_sources SET state='revoked'",
                    "INSERT INTO identity.shared_source_grants (grant_id,link_id,site_id,source_id,capability,source_revision,authorization_generation,revision,approval_commitment,created_at,expires_at) SELECT gen_random_uuid(),link_id,site_id,source_id,'lighting.execute',source_revision,authorization_generation,revision,repeat('c',64),created_at,expires_at FROM identity.shared_source_grants LIMIT 1",
                ):
                    with pytest.raises(DBAPIError) as denied:
                        with connection.begin_nested(): connection.execute(text(sql))
                    assert denied.value.orig.sqlstate == "42501"
                # A new explicit review renews grants with new IDs/revisions and
                # preserves old rows rather than rewriting approval history.
                renewed = asyncio.run(storage.resolve(adapter, subject=subject, session_commitment="a"*64))
                second = storage.commitment.prepare(uuid4(), renewed, now=stamp, grants_expire_at=stamp+timedelta(days=31))
                asyncio.run(storage.confirm(adapter, authority=renewed, review=second,
                    confirmation=SharingConfirmation(operation_id=second.operation_id, reviewed_digest=second.reviewed_digest)))
                assert connection.execute(text("SELECT count(*) FROM identity.shared_source_grants")).scalar_one() == 8
                assert connection.execute(text("SELECT min(revision) FROM identity.shared_source_grants WHERE revoked_at IS NULL")).scalar_one() == 2
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
