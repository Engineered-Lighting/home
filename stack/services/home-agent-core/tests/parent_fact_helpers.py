from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from psycopg.types.range import Range
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app import schema
from app.crypto import sha256_json
from app.ids import uuid7
from app.models import MemoryTransactionView
from app.store import CoreStore


async def seed_parent_fact_fixture(
    store: CoreStore,
    principal: dict[str, Any],
    *,
    parent_person_id: uuid.UUID,
    child_person_id: uuid.UUID,
    valid_from: datetime | None = None,
) -> MemoryTransactionView:
    """Seed one parent fact for downstream tests only.

    This deliberately lives outside ``app`` and is not mounted by FastAPI.
    Production parent authority remains disabled until the reviewed, staged,
    atomic two-parent proposal protocol is implemented.
    """

    assert principal["person_id"] == child_person_id
    transaction_id = uuid7()
    fact_id = uuid7()
    fact_version_id = uuid7()
    now = datetime.now(UTC)
    candidate = {
        "subject_person_id": str(parent_person_id),
        "predicate": "parent_of",
        "object_person_id": str(child_person_id),
    }
    async with store.database.transaction(
        principal_id=principal["principal_id"], serializable=True
    ) as connection:
        people_count = int(
            (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.people)
                    .where(
                        schema.people.c.person_id.in_(
                            [parent_person_id, child_person_id]
                        ),
                        schema.people.c.status == "active",
                    )
                )
            ).scalar_one()
        )
        assert people_count == 2
        existing = (
            await connection.execute(
                select(schema.fact_versions.c.fact_id).where(
                    schema.fact_versions.c.subject_id == parent_person_id,
                    schema.fact_versions.c.predicate == "parent_of",
                    schema.fact_versions.c.object.contains(
                        {"person_id": str(child_person_id)}
                    ),
                    func.upper_inf(schema.fact_versions.c.system_range),
                    schema.fact_versions.c.resolution == "accepted",
                )
            )
        ).scalar_one_or_none()
        assert existing is None
        confirmation_artifact_id = await store._mint_authenticated_confirmation(
            connection,
            principal_id=principal["principal_id"],
            purpose="test_fixture.parent_relationship",
            proposal_digest=sha256_json(candidate),
            client_nonce=uuid.uuid4(),
        )
        legacy_labels = list(
            (
                await connection.execute(
                    select(schema.legacy_role_labels.c.label_id).where(
                        schema.legacy_role_labels.c.person_id == parent_person_id,
                        schema.legacy_role_labels.c.role_label == "parent",
                        schema.legacy_role_labels.c.perspective == "unknown",
                    )
                )
            ).scalars()
        )
        await connection.execute(
            insert(schema.memory_transactions).values(
                transaction_id=transaction_id,
                principal_id=principal["principal_id"],
                kind="test_fixture_parent_confirmation",
                state="committed",
                candidate=candidate,
                preview={"fixture": candidate},
                verifier_results=[
                    {
                        "rule": "test_fixture_only",
                        "outcome": "pass",
                        "reason_code": "not_product_authority",
                    }
                ],
                policy_version=store.settings.policy_version,
                policy_digest=store.settings.policy_digest,
                confirmation_digest=sha256_json(candidate),
                confirmed_at=now,
            )
        )
        await connection.execute(
            insert(schema.fact_versions).values(
                fact_version_id=fact_version_id,
                fact_id=fact_id,
                version=1,
                subject_type="person",
                subject_id=parent_person_id,
                predicate="parent_of",
                object={"person_id": str(child_person_id)},
                perspective_principal_id=principal["principal_id"],
                valid_range=Range(valid_from or now, None, bounds="[)"),
                system_range=Range(now, None, bounds="[)"),
                authority="explicit_related_party",
                support="explicit_authority",
                contradiction="none",
                freshness="not_applicable",
                coverage="not_applicable",
                resolution="accepted",
                privacy_scope="private",
                memory_transaction_id=transaction_id,
            )
        )
        await connection.execute(
            insert(schema.fact_support).values(
                support_id=uuid7(),
                fact_version_id=fact_version_id,
                artifact_id=confirmation_artifact_id,
                support_role="confirmation",
            )
        )
        if len(legacy_labels) == 1:
            await connection.execute(
                insert(schema.fact_support).values(
                    support_id=uuid7(),
                    fact_version_id=fact_version_id,
                    artifact_id=legacy_labels[0],
                    dependency_domain="legacy_identity_store",
                    support_role="legacy_context",
                )
            )
    return MemoryTransactionView(
        transaction_id=transaction_id,
        state="committed",
        fact_id=fact_id,
    )
