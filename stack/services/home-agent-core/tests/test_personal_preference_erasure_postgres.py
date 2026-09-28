"""Preference erasure in the pinned disposable 0046 clone, never production."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.types.range import Range
from sqlalchemy import create_engine, insert, select, text

from app import schema
from app.erasure import apply_personal_preference_erasure
from .e1_postgres_harness import assert_guarded_database_url


def test_preference_forgetting_scrubs_all_versions_and_replay_is_idempotent():
    url = os.getenv("TEST_SHARED_LINK_SESSION_KERNEL_ADMIN_DATABASE_URL")
    if not url:
        pytest.skip("pinned disposable session-kernel clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url, pool_size=1, max_overflow=0,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("SELECT current_database()")).scalar_one() == "shared_link_session_kernel_0046"
                assert connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == "0046_shared_link_session_krnl_v1"
                principal, person = connection.execute(text("SELECT principal_id,person_id FROM identity.shared_owner_links")).one()
                fact, first_tx, second_tx, pending_tx, erasure_id = (uuid4() for _ in range(5))
                now = datetime.now(UTC)
                for tx, state in ((first_tx,"committed"),(second_tx,"committed"),(pending_tx,"needs_confirmation")):
                    connection.execute(insert(schema.memory_transactions).values(
                        transaction_id=tx, principal_id=principal, kind="personal_preference.v1",
                        state=state, candidate={"preference_fact_id":str(fact),"value":"warm"},
                        preview={"value":"warm"}, policy_version="fixture",policy_digest="a"*64))
                for revision, tx in ((1,first_tx),(2,second_tx)):
                    connection.execute(insert(schema.fact_versions).values(
                        fact_version_id=uuid4(), fact_id=fact, version=revision,
                        subject_type="person", subject_id=person,
                        predicate="personal_preference.evening_lighting", object={"value":"warm"},
                        perspective_principal_id=principal,
                        valid_range=Range(now-timedelta(days=2),None),
                        system_range=Range(now-timedelta(days=2),now-timedelta(days=1)) if revision==1 else Range(now-timedelta(days=1),None),
                        authority="explicit_subject",support="explicit_authority",contradiction="none",
                        freshness="not_applicable",coverage="not_applicable",resolution="accepted",
                        privacy_scope="private",memory_transaction_id=tx))

                class AsyncTransaction:
                    async def execute(self, statement, parameters=None):
                        return connection.execute(statement, parameters or {})

                async def erase(require_existing):
                    return await apply_personal_preference_erasure(
                        AsyncTransaction(),principal_id=principal,fact_id=fact,
                        erasure_request_id=erasure_id,now=now,require_existing=require_existing)

                first = asyncio.run(erase(True))
                replay = asyncio.run(erase(False))
                assert first.fact_version_ids == replay.fact_version_ids
                assert connection.execute(select(schema.retrieval_blocks.c.artifact_id).where(schema.retrieval_blocks.c.artifact_id==fact)).scalar_one()==fact
                rows = connection.execute(select(schema.fact_versions).where(schema.fact_versions.c.fact_id==fact)).mappings().all()
                assert len(rows)==2 and all(r["object"]=={"erased":True} and r["resolution"]=="suppressed" for r in rows)
                for tx in (first_tx,second_tx,pending_tx):
                    row = connection.execute(select(schema.memory_transactions).where(schema.memory_transactions.c.transaction_id==tx)).mappings().one()
                    assert row["candidate"] == row["preview"] == {"erased":True}
                    if tx==pending_tx: assert row["state"]=="cancelled"
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
