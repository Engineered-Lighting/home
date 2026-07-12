from __future__ import annotations

import asyncio
import base64
import os
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update

from app import schema
from app.config import Settings
from app.db import Database
from app.errors import ConflictError, NotFoundError
from app.models import (
    OperatorPrincipalBindingProposalStage,
    PersonCreate,
    PrincipalBindingConfirmation,
    ReviewedPrivacyDirectiveImport,
)
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration",
)


async def _reset(database: Database) -> None:
    tables = [
        table
        for table in schema.metadata.sorted_tables
        if table.schema
        in {"ingest", "identity", "knowledge", "engagement", "privacy", "operations"}
    ]
    qualified = ", ".join(
        f'"{table.schema}"."{table.name}"' for table in tables
    )
    async with database.transaction() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {qualified} CASCADE"))


def _settings() -> Settings:
    knowledge_key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    return Settings(
        database_url=SecretStr(os.environ["TEST_DATABASE_URL"]),
        knowledge_encryption_key=SecretStr(knowledge_key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="canary",
    )


async def _request_for(store: CoreStore, ha_user_id: str):
    subject = await store.request_principal_binding(ha_user_id)
    assert subject.state == "awaiting_operator_review"
    assert subject.review_code is not None
    pending = await store.list_pending_principal_binding_requests()
    matches = [
        request
        for request in pending.requests
        if request.review_code == subject.review_code
    ]
    assert len(matches) == 1
    return matches[0]


async def _stage(store: CoreStore, request, person_id: uuid.UUID):
    return await store.stage_principal_binding_proposal(
        OperatorPrincipalBindingProposalStage(
            request_id=request.request_id,
            review_code=request.review_code,
            person_id=person_id,
            operator_request_id=uuid.uuid4(),
        )
    )


@pytest.mark.asyncio
async def test_staged_binding_happy_path_privacy_drift_and_race() -> None:
    settings = _settings()
    database = Database(settings.async_database_url())
    store = CoreStore(database, DisabledRuntimeSpool(), settings)
    await _reset(database)
    try:
        person = await store.create_person(PersonCreate(display_name="Marcelo"))
        request = await _request_for(store, "ha-user-marcelo")
        await _stage(store, request, person.person_id)
        proposal = await store.principal_binding_proposal_status("ha-user-marcelo")
        assert proposal.state == "ready_for_confirmation"
        assert proposal.confirmation_statement == (
            "Bind this authenticated Home Assistant account to Marcelo."
        )
        assert proposal.proposal_digest is not None

        with pytest.raises(NotFoundError):
            await store.confirm_principal_binding_proposal(
                "ha-user-marcelo",
                PrincipalBindingConfirmation(
                    proposal_digest="0" * 64,
                    confirmation_nonce=uuid.uuid4(),
                ),
            )

        confirmation = await store.confirm_principal_binding_proposal(
            "ha-user-marcelo",
            PrincipalBindingConfirmation(
                proposal_digest=proposal.proposal_digest,
                confirmation_nonce=uuid.uuid4(),
            ),
        )
        assert confirmation.location_memory_enabled is False
        assert confirmation.travel_greetings_enabled is False
        replay = await store.confirm_principal_binding_proposal(
            "ha-user-marcelo",
            PrincipalBindingConfirmation(
                proposal_digest=proposal.proposal_digest,
                confirmation_nonce=uuid.uuid4(),
            ),
        )
        assert replay.confirmed_at == confirmation.confirmed_at
        principal = await store.resolve_principal("ha-user-marcelo")
        async with database.transaction() as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.preferences)
                    .where(
                        schema.preferences.c.principal_id
                        == principal["principal_id"]
                    )
                )
            ).scalar_one() == 0

        privacy_person = await store.create_person(
            PersonCreate(display_name="Privacy transition")
        )
        privacy_request = await _request_for(store, "ha-user-privacy")
        await _stage(store, privacy_request, privacy_person.person_id)
        await store.import_reviewed_privacy_directive(
            privacy_person.person_id,
            ReviewedPrivacyDirectiveImport(
                directive="silent",
                source_ref="reviewed:privacy-transition",
                source_version=1,
                source_snapshot_sha256="b" * 64,
            ),
        )
        async with database.transaction() as connection:
            states = (
                (
                    await connection.execute(
                        select(
                            schema.principal_binding_requests.c.state.label(
                                "request_state"
                            ),
                            schema.principal_binding_proposals.c.state.label(
                                "proposal_state"
                            ),
                        )
                        .select_from(
                            schema.principal_binding_requests.join(
                                schema.principal_binding_proposals,
                                schema.principal_binding_requests.c.request_id
                                == schema.principal_binding_proposals.c.request_id,
                            )
                        )
                        .where(
                            schema.principal_binding_requests.c.request_id
                            == privacy_request.request_id
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert states == {
            "request_state": "cancelled",
            "proposal_state": "cancelled",
        }

        drift_person = await store.create_person(
            PersonCreate(display_name="Reviewed before drift")
        )
        drift_request = await _request_for(store, "ha-user-drift")
        await _stage(store, drift_request, drift_person.person_id)
        drift_proposal = await store.principal_binding_proposal_status("ha-user-drift")
        assert drift_proposal.proposal_digest is not None
        async with database.transaction() as connection:
            await connection.execute(
                update(schema.people)
                .where(schema.people.c.person_id == drift_person.person_id)
                .values(display_name="Changed after review", updated_at=datetime.now(UTC))
            )
        assert (
            await store.principal_binding_proposal_status("ha-user-drift")
        ).state == "unavailable"
        with pytest.raises(ConflictError, match="changed after operator staging"):
            await store.confirm_principal_binding_proposal(
                "ha-user-drift",
                PrincipalBindingConfirmation(
                    proposal_digest=drift_proposal.proposal_digest,
                    confirmation_nonce=uuid.uuid4(),
                ),
            )

        race_person = await store.create_person(PersonCreate(display_name="Race target"))
        race_a = await _request_for(store, "ha-user-race-a")
        race_b = await _request_for(store, "ha-user-race-b")

        async def stage_with_retry(request):
            return await database.run_serializable(
                lambda: _stage(store, request, race_person.person_id)
            )

        outcomes = await asyncio.gather(
            stage_with_retry(race_a),
            stage_with_retry(race_b),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, Exception) for value in outcomes) == 1
        assert sum(isinstance(value, ConflictError) for value in outcomes) == 1
    finally:
        await _reset(database)
        await database.close()
