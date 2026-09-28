"""Storage lifecycle as the API role in the guarded hosted clone.

Administrator access seeds synthetic linked accounts and grants only. Storage
and service SQL use the existing API role. This does not prove live BFF login
or independent transaction races.
"""
import asyncio
import os
from contextlib import asynccontextmanager
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
from app.personal_memory_service import PersonalMemoryService
from app.store import CoreStore
from .e1_postgres_harness import assert_guarded_database_url


def test_preference_storage_correction_forgetting_and_relearning():
    url = os.getenv("TEST_PERSONAL_PREFERENCE_AUTHORITY_ADMIN_DATABASE_URL")
    if not url: pytest.skip("guarded hosted preference storage clone required")
    assert_guarded_database_url(url)
    engine = create_engine(url,isolation_level="SERIALIZABLE",pool_size=1,max_overflow=0,
        connect_args={"connect_timeout":5,"options":"-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(text("SELECT current_database()")).scalar_one()=="personal_preference_authority_0047"
                connection.execute(text("GRANT EXECUTE ON FUNCTION identity.resolve_personal_preference_authority_v1(text,text,text,boolean) TO home_agent_api"))
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
                subjects = dict(connection.execute(text("SELECT issuer_id,subject FROM identity.shared_subject_bindings WHERE link_id=:link AND revoked_at IS NULL"),
                    {"link":link["link_id"]}).all())
                authority = PreferenceAuthority(principal_id=link["principal_id"],person_id=link["person_id"],
                    link_id=link["link_id"],authorization_generation=link["authorization_generation"],
                    issuer_id="home-assistant:echo",site_id="echo",subject=subjects["home-assistant:echo"],access="write",session_commitment=uuid4().hex*2,
                    echo_grant_revision=1,victoria_grant_revision=1,valid_until=now+timedelta(minutes=5))
                storage = PersonalMemoryStorage(PreferenceReviewCommitment(b"k"*32),policy_digest="a"*64,policy_version="fixture")

                class AsyncTransaction:
                    async def execute(self, statement, parameters=None):
                        with connection.begin_nested():
                            connection.execute(text("SET LOCAL SESSION AUTHORIZATION home_agent_api"))
                            result=connection.execute(statement,parameters or {})
                            connection.execute(text("RESET SESSION AUTHORIZATION"))
                            return result

                async def exercise():
                    nonlocal authority
                    conn = AsyncTransaction()
                    subjects = dict(connection.execute(text("SELECT issuer_id,subject FROM identity.shared_subject_bindings WHERE link_id=:link AND revoked_at IS NULL"),
                        {"link":link["link_id"]}).all())
                    authority = await storage.resolve_authority(conn,issuer_id="home-assistant:echo",
                        subject=subjects["home-assistant:echo"],session_commitment=authority.session_commitment,write=True)
                    assert authority.principal_id==link["principal_id"]
                    await conn.execute(text("SELECT set_config('app.principal_id',:principal,true)"),
                        {"principal":str(authority.principal_id)})
                    assert authority.valid_until <= now+timedelta(seconds=65)
                    retained = await storage.resolve_authority(conn,issuer_id=authority.issuer_id,
                        subject=subjects[authority.issuer_id],session_commitment=authority.session_commitment,
                        write=True,retained=authority)
                    assert retained==authority  # Confirmation cannot extend the review lease.
                    with pytest.raises(ForbiddenError):
                        await storage.resolve_authority(conn,issuer_id=authority.issuer_id,
                            subject="unlinked-"+uuid4().hex,session_commitment=authority.session_commitment,write=True)
                    with pytest.raises(ForbiddenError):
                        await storage.resolve_authority(conn,issuer_id=authority.issuer_id,
                            subject=subjects[authority.issuer_id],session_commitment=uuid4().hex*2,
                            write=True,retained=authority)

                    async def propose(operation,revision,tone="warm"):
                        current = await storage.read(conn,authority)
                        request = PreferenceProposalRequest(operation_id=uuid4(),operation=operation,
                            expected_revision=revision,expected_fact_id=current["fact_id"] if revision else None,
                            preference=None if operation=="forget" else EveningLightingPreference(value=tone))
                        return request,await storage.propose(conn,authority,request)

                    async def confirm(pair):
                        request,review = pair
                        # Synthetic gesture, real Core minting under API-role
                        # permissions for every operation, including forgetting.
                        artifact = await CoreStore._mint_authenticated_confirmation(
                            object.__new__(CoreStore),conn,principal_id=authority.principal_id,
                            purpose=f"{KIND}.{request.operation}.confirm",
                            proposal_digest=review.reviewed_digest,client_nonce=uuid4())
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
                    victoria = await storage.resolve_authority(conn,issuer_id="home-assistant:victoria",
                        subject=subjects["home-assistant:victoria"],session_commitment=uuid4().hex*2,write=False)
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
                    # Exercise the authenticated transaction service and real
                    # Core confirmation minting in this rollback-only fixture.
                    # API-role execution proves permissions here, but this
                    # rollback-only fixture does not prove live login or races.
                    class DatabaseFixture:
                        @asynccontextmanager
                        async def transaction(self, *, serializable):
                            assert serializable
                            yield conn
                    core = object.__new__(CoreStore)
                    core.database = DatabaseFixture()
                    admissions = []
                    async def admit(): admissions.append(True)
                    service = PersonalMemoryService(store=core,storage=storage,admission=admit)
                    session = dict(issuer_id="home-assistant:echo",subject=subjects["home-assistant:echo"],
                        session_commitment=authority.session_commitment)
                    request = PreferenceProposalRequest(operation_id=uuid4(),operation="correct",
                        expected_revision=4,expected_fact_id=latest["fact_id"],
                        preference=EveningLightingPreference(value="neutral"))
                    review = await service.propose(session,request)
                    assert await service.propose(session,request)==review
                    confirmation = PreferenceConfirmation(operation_id=request.operation_id,reviewed_digest=review.reviewed_digest)
                    assert (await service.confirm(session,confirmation,gesture_id=uuid4()))["revision"]==5
                    assert (await service.read(session))["preference"].value=="neutral"
                    assert (await service.outcome(session,confirmation))["revision"]==5
                    assert len(admissions)>=10
                    connection.execute(text("UPDATE identity.shared_source_grants SET revoked_at=clock_timestamp() WHERE source_id=:source AND site_id='victoria' AND capability='memory.read'"),{"source":SOURCE})
                    with pytest.raises(ForbiddenError): await storage.read(conn,authority)
                    with pytest.raises(ForbiddenError): await storage.read(conn,victoria)

                asyncio.run(exercise())
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
