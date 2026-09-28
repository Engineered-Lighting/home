"""Storage lifecycle under synthetic authority in the guarded hosted clone.

These administrator fixtures do not prove BFF authentication or production role
permissions. They exercise real Core tables, revisions and deletion behavior.
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text

from app import schema
from app.errors import ConflictError, ForbiddenError
from app.personal_memory_contract import (
    SOURCE, EveningLightingPreference, PreferenceAuthority, PreferenceConfirmation,
    PreferenceProposalRequest, PreferenceReviewCommitment,
)
from app.personal_memory_storage import KIND, PersonalMemoryStorage
from .e1_postgres_harness import assert_guarded_database_url


def test_preference_storage_correction_forgetting_and_relearning():
    url = os.getenv("TEST_SHARED_LINK_SESSION_KERNEL_ADMIN_DATABASE_URL")
    if not url: pytest.skip("guarded hosted preference storage clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url,isolation_level="SERIALIZABLE",pool_size=1,max_overflow=0,
        connect_args={"connect_timeout":5,"options":"-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("SELECT current_database()")).scalar_one()=="shared_link_session_kernel_0046"
                link = connection.execute(text("SELECT * FROM identity.shared_owner_links")).mappings().one()
                now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                for site in ("echo","victoria"):
                    for capability in ("memory.read","personal_memory.write"):
                        params = dict(site=site,source=SOURCE,capability=capability,issuer=f"home-assistant:{site}",
                            id=uuid4(),link=link["link_id"],generation=link["authorization_generation"],
                            approval=uuid4().hex*2,expires=now+timedelta(minutes=5))
                        connection.execute(text("INSERT INTO identity.shared_sources VALUES(:site,:source,:capability,:issuer,1,'active')"),params)
                        connection.execute(text("""INSERT INTO identity.shared_source_grants
                            (grant_id,link_id,site_id,source_id,capability,source_revision,authorization_generation,
                             revision,approval_commitment,created_at,expires_at)
                            VALUES(:id,:link,:site,:source,:capability,1,:generation,1,:approval,clock_timestamp(),:expires)"""),params)
                authority = PreferenceAuthority(principal_id=link["principal_id"],person_id=link["person_id"],
                    link_id=link["link_id"],authorization_generation=link["authorization_generation"],
                    issuer_id="home-assistant:echo",site_id="echo",session_commitment=uuid4().hex*2,
                    echo_grant_revision=1,victoria_grant_revision=1,valid_until=now+timedelta(minutes=5))
                storage = PersonalMemoryStorage(PreferenceReviewCommitment(b"k"*32),policy_digest="a"*64,policy_version="fixture")

                class AsyncTransaction:
                    async def execute(self, statement, parameters=None):
                        return connection.execute(statement,parameters or {})

                async def exercise():
                    conn = AsyncTransaction()

                    async def propose(operation,revision,tone="warm"):
                        current = await storage.read(conn,authority)
                        request = PreferenceProposalRequest(operation_id=uuid4(),operation=operation,
                            expected_revision=revision,expected_fact_id=current["fact_id"] if revision else None,
                            preference=None if operation=="forget" else EveningLightingPreference(value=tone))
                        return request,await storage.propose(conn,authority,request)

                    async def confirm(pair):
                        request,review = pair
                        artifact = uuid4()
                        stamp = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                        # Synthetic governed gesture fixture, not a live owner approval.
                        connection.execute(insert(schema.confirmation_artifacts).values(artifact_id=artifact,
                            principal_id=authority.principal_id,purpose=f"{KIND}.{request.operation}.confirm",
                            proposal_digest=review.reviewed_digest,client_nonce_sha256=uuid4().hex*2,
                            issued_at=stamp,consumed_at=stamp,expires_at=stamp+timedelta(seconds=60)))
                        confirmation = PreferenceConfirmation(operation_id=request.operation_id,reviewed_digest=review.reviewed_digest)
                        assert await storage.outcome(conn,authority,confirmation) is None
                        result = await storage.confirm(conn,authority,confirmation,confirmation_artifact_id=artifact)
                        recovered = await storage.outcome(conn,authority,confirmation)
                        assert recovered["revision"]==result["revision"] and recovered["historical"]
                        return result

                    first = await propose("remember",0)
                    assert await storage.propose(conn,authority,first[0]) == first[1]
                    assert (await confirm(first))["revision"]==1
                    first_snapshot = await storage.read(conn,authority)
                    assert first_snapshot["preference"].value=="warm"
                    victoria = PreferenceAuthority.model_validate({**authority.model_dump(),
                        "issuer_id":"home-assistant:victoria","site_id":"victoria",
                        "session_commitment":uuid4().hex*2})
                    assert (await storage.read(conn,victoria))["preference"].value=="warm"
                    wrong_owner = PreferenceAuthority.model_validate({**authority.model_dump(),"principal_id":uuid4()})
                    with pytest.raises(ForbiddenError): await storage.read(conn,wrong_owner)
                    correction = await propose("correct",1,"cool")
                    competing = await propose("correct",1,"neutral")
                    assert (await confirm(correction))["revision"]==2
                    with pytest.raises(ConflictError): await confirm(competing)
                    assert (await storage.read(conn,authority))["preference"].value=="cool"
                    forgotten = await confirm(await propose("forget",2))
                    assert forgotten["status"]=="ledger_pending" and forgotten["revision"]==3
                    snapshot = await storage.read(conn,authority)
                    assert snapshot["preference"] is None and snapshot["revision"]==3
                    with pytest.raises(ConflictError): await propose("remember",0)
                    assert (await confirm(await propose("remember",3)))["revision"]==4
                    latest = await storage.read(conn,authority)
                    assert latest["preference"].value=="warm" and latest["fact_id"]!=first_snapshot["fact_id"]
                    with pytest.raises(ConflictError):
                        await storage.propose(conn,authority,PreferenceProposalRequest(
                            operation_id=uuid4(),operation="correct",expected_revision=4,
                            expected_fact_id=first_snapshot["fact_id"],preference=EveningLightingPreference(value="cool")))
                    connection.execute(text("UPDATE identity.shared_source_grants SET revoked_at=clock_timestamp() WHERE source_id=:source AND site_id='victoria' AND capability='memory.read'"),{"source":SOURCE})
                    with pytest.raises(ForbiddenError): await storage.read(conn,authority)
                    with pytest.raises(ForbiddenError): await storage.read(conn,victoria)

                asyncio.run(exercise())
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
