from __future__ import annotations

import base64
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from app import schema
from app.config import Settings
from app.db import Database
from app.models import (
    OperatorPrincipalBindingProposalStage,
    PersonCreate,
    PrincipalBindingConfirmation,
)
from app.spool import DisabledRuntimeSpool
from app.store import CoreStore


REQUIRED_URLS = (
    "TEST_DATABASE_URL",
    "TEST_API_DATABASE_URL",
    "TEST_BINDING_OPERATOR_DATABASE_URL",
    "TEST_INGEST_DATABASE_URL",
    "TEST_WORKER_DATABASE_URL",
    "TEST_ERASURE_DATABASE_URL",
    "TEST_ROLLOUT_DATABASE_URL",
)
IDENTITY_API_ACL = (
    Path(__file__).resolve().parents[3]
    / "home-agent-deploy"
    / "identity-api-acl.sql"
).read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(
    not all(os.getenv(name) for name in REQUIRED_URLS),
    reason="owner and runtime-role database URLs are required",
)


def request_values(
    *,
    request_id: uuid.UUID,
    ha_user_id: str,
    review_code: str,
    requested_at: datetime,
    expires_at: datetime,
    state: str = "pending",
    staged_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "ha_user_id": ha_user_id,
        "review_code": review_code,
        "state": state,
        "requested_at": requested_at,
        "expires_at": expires_at,
        "staged_at": staged_at,
        "closed_at": closed_at,
    }


def proposal_values(
    *,
    proposal_id: uuid.UUID,
    request_id: uuid.UUID,
    ha_user_id: str,
    person_id: uuid.UUID,
    staged_at: datetime,
    expires_at: datetime,
    digest: str,
) -> dict:
    return {
        "proposal_id": proposal_id,
        "operator_request_id": uuid.uuid4(),
        "request_id": request_id,
        "ha_user_id": ha_user_id,
        "person_id": person_id,
        "reviewed_display_label": "Reviewed person",
        "person_snapshot_digest": "c" * 64,
        "proposal_digest": digest,
        "state": "ready",
        "stage_receipt_digest": "d" * 64,
        "staged_at": staged_at,
        "expires_at": expires_at,
    }


def sqlstate(exc: pytest.ExceptionInfo[DBAPIError]) -> str | None:
    return getattr(exc.value.orig, "sqlstate", None)


def api_settings() -> Settings:
    key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    return Settings(
        database_url=SecretStr(os.environ["TEST_API_DATABASE_URL"]),
        knowledge_encryption_key=SecretStr(key),
        service_token=SecretStr("service-token-with-at-least-32-chars"),
        policy_digest="a" * 64,
        role="api",
        rollout_mode="canary",
    )


async def replay_identity_api_acl(connection) -> None:
    executable_sql = "\n".join(
        line
        for line in IDENTITY_API_ACL.splitlines()
        if not line.lstrip().startswith("--")
    )
    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_quote: str | None = None
    while index < len(executable_sql):
        if dollar_quote is not None:
            if executable_sql.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue
        character = executable_sql[index]
        if quote is not None:
            if character == quote:
                if (
                    index + 1 < len(executable_sql)
                    and executable_sql[index + 1] == quote
                ):
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character in ("'", '"'):
            quote = character
            index += 1
            continue
        if character == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", executable_sql[index:])
            if match:
                dollar_quote = match.group(0)
                index += len(dollar_quote)
                continue
        if character == ";":
            statements.append(executable_sql[start:index])
            start = index + 1
        index += 1
    statements.append(executable_sql[start:])
    for statement in statements:
        if statement.strip():
            await connection.execute(text(statement))


@pytest.mark.asyncio
async def test_binding_constraints_and_deferred_graph_reject_direct_writes() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    suffix = uuid.uuid4().hex
    ha_user_id = f"binding-schema-{suffix}"
    request_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    person_id = uuid.uuid4()
    other_person_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    other_principal_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=15)
    try:
        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                insert(schema.people),
                [
                    {
                        "person_id": person_id,
                        "display_name": "Binding person",
                        "status": "active",
                        "privacy_scope": "private",
                    },
                    {
                        "person_id": other_person_id,
                        "display_name": "Other binding person",
                        "status": "active",
                        "privacy_scope": "private",
                    },
                ],
            )
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **request_values(
                        request_id=request_id,
                        ha_user_id=ha_user_id,
                        review_code="ABCDEFGHJKLMNPQ2",
                        requested_at=now,
                        expires_at=expires_at,
                        state="staged",
                        staged_at=now,
                    )
                )
            )
            await connection.execute(
                insert(schema.principal_binding_proposals).values(
                    **proposal_values(
                        proposal_id=proposal_id,
                        request_id=request_id,
                        ha_user_id=ha_user_id,
                        person_id=person_id,
                        staged_at=now,
                        expires_at=expires_at,
                        digest="a" * 64,
                    )
                )
            )
            await connection.execute(
                insert(schema.principals),
                [
                    {
                        "principal_id": principal_id,
                        "person_id": person_id,
                        "kind": "ha_user",
                        "display_label": "Binding principal",
                        "status": "active",
                    },
                    {
                        "principal_id": other_principal_id,
                        "person_id": other_person_id,
                        "kind": "ha_user",
                        "display_label": "Other principal",
                        "status": "active",
                    },
                ],
            )
            await connection.execute(
                insert(schema.confirmation_artifacts).values(
                    artifact_id=artifact_id,
                    principal_id=principal_id,
                    purpose="ha_user_person_binding.confirm",
                    proposal_digest="a" * 64,
                    client_nonce_sha256="b" * 64,
                    issued_at=now,
                    expires_at=expires_at,
                    consumed_at=now,
                )
            )

        with pytest.raises(DBAPIError) as direct_insert:
            async with owner.transaction(binding_operator=True) as connection:
                await connection.execute(
                    insert(schema.ha_user_bindings).values(
                        binding_id=uuid.uuid4(),
                        ha_user_id=ha_user_id,
                        principal_id=principal_id,
                        person_id=person_id,
                        confirmed_by_principal_id=principal_id,
                        confirmed_at=now,
                        source_artifact_id=artifact_id,
                    )
                )
        assert sqlstate(direct_insert) == "23502"

        with pytest.raises(DBAPIError) as ready_proposal:
            async with owner.transaction(binding_operator=True) as connection:
                await connection.execute(
                    insert(schema.ha_user_bindings).values(
                        binding_id=binding_id,
                        proposal_id=proposal_id,
                        ha_user_id=ha_user_id,
                        principal_id=principal_id,
                        person_id=person_id,
                        confirmed_by_principal_id=principal_id,
                        confirmed_at=now,
                        source_artifact_id=artifact_id,
                    )
                )
        assert sqlstate(ready_proposal) == "23514"

        with pytest.raises(DBAPIError) as self_confirmation:
            async with owner.transaction(binding_operator=True) as connection:
                await connection.execute(
                    insert(schema.ha_user_bindings).values(
                        binding_id=uuid.uuid4(),
                        proposal_id=proposal_id,
                        ha_user_id=ha_user_id,
                        principal_id=principal_id,
                        person_id=person_id,
                        confirmed_by_principal_id=other_principal_id,
                        confirmed_at=now,
                        source_artifact_id=artifact_id,
                    )
                )
        assert sqlstate(self_confirmation) == "23514"

        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                update(schema.principal_binding_requests)
                .where(schema.principal_binding_requests.c.request_id == request_id)
                .values(state="consumed", closed_at=now)
            )
            await connection.execute(
                update(schema.principal_binding_proposals)
                .where(schema.principal_binding_proposals.c.proposal_id == proposal_id)
                .values(
                    state="consumed",
                    consumed_at=now,
                    result_principal_id=principal_id,
                    confirmation_artifact_id=artifact_id,
                )
            )
            await connection.execute(
                insert(schema.ha_user_bindings).values(
                    binding_id=binding_id,
                    proposal_id=proposal_id,
                    ha_user_id=ha_user_id,
                    principal_id=principal_id,
                    person_id=person_id,
                    confirmed_by_principal_id=principal_id,
                    confirmed_at=now,
                    source_artifact_id=artifact_id,
                )
            )

        async with owner.transaction() as connection:
            await connection.execute(
                update(schema.ha_user_bindings)
                .where(schema.ha_user_bindings.c.binding_id == binding_id)
                .values(revoked_at=now + timedelta(seconds=1))
            )
    finally:
        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.request_id == request_id
                )
            )
            await connection.execute(
                delete(schema.confirmation_artifacts).where(
                    schema.confirmation_artifacts.c.artifact_id == artifact_id
                )
            )
            await connection.execute(
                delete(schema.principals).where(
                    schema.principals.c.principal_id.in_(
                        (principal_id, other_principal_id)
                    )
                )
            )
            await connection.execute(
                delete(schema.people).where(
                    schema.people.c.person_id.in_((person_id, other_person_id))
                )
            )
        await owner.close()


@pytest.mark.asyncio
async def test_identity_api_acl_replay_removes_broad_and_future_privileges() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    probe = f"api_acl_probe_{uuid.uuid4().hex}"
    try:
        async with owner.transaction() as connection:
            # Model both stale schema-wide ACLs and unsafe future defaults,
            # then replay the exact production contract in the same tx.
            await connection.execute(
                text(
                    "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA identity "
                    "TO home_agent_api"
                )
            )
            await connection.execute(
                text(
                    "GRANT SELECT (alias), UPDATE (alias) "
                    "ON TABLE identity.aliases TO home_agent_api"
                )
            )
            await connection.execute(
                text(
                    "GRANT SELECT (alias) ON TABLE identity.aliases TO PUBLIC"
                )
            )
            await connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner "
                    "IN SCHEMA identity GRANT ALL PRIVILEGES ON TABLES "
                    "TO home_agent_api"
                )
            )
            await connection.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE home_agent_owner "
                    "IN SCHEMA identity GRANT ALL PRIVILEGES ON TABLES "
                    "TO PUBLIC"
                )
            )
            await replay_identity_api_acl(connection)
            await connection.execute(
                text(f'CREATE TABLE identity."{probe}" (id uuid PRIMARY KEY)')
            )

            readable = {
                "people",
                "principals",
                "confirmation_artifacts",
                "principal_binding_requests",
                "principal_binding_proposals",
                "edge_privacy_user_blocks",
                "privacy_directives",
            }
            inaccessible = {
                "aliases",
                "external_recognition_bindings",
                "legacy_role_labels",
                "legacy_relationship_candidates",
                "source_entity_bindings",
                "edge_privacy_blocks",
                "ha_user_bindings",
                probe,
            }
            for table in readable | inaccessible:
                acl = (
                    (
                        await connection.execute(
                            text(
                                "SELECT "
                                "has_table_privilege('home_agent_api', :table, "
                                "'SELECT') AS can_select, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'INSERT') AS can_insert, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'UPDATE') AS can_update, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'DELETE') AS can_delete, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'TRUNCATE') AS can_truncate, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'REFERENCES') AS can_reference, "
                                "has_table_privilege('home_agent_api', :table, "
                                "'TRIGGER') AS can_trigger"
                            ),
                            {"table": f"identity.{table}"},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert dict(acl) == {
                    "can_select": table in readable,
                    "can_insert": False,
                    "can_update": False,
                    "can_delete": False,
                    "can_truncate": False,
                    "can_reference": False,
                    "can_trigger": False,
                }

            for column in (
                "binding_id",
                "proposal_id",
                "ha_user_id",
                "principal_id",
                "person_id",
                "confirmed_by_principal_id",
                "confirmed_at",
                "revoked_at",
                "source_artifact_id",
            ):
                assert (
                    await connection.execute(
                        text(
                            "SELECT has_column_privilege("
                            "'home_agent_api', 'identity.ha_user_bindings', "
                            ":column, 'SELECT')"
                        ),
                        {"column": column},
                    )
                ).scalar_one() is True

            assert (
                await connection.execute(
                    text(
                        "SELECT has_column_privilege('home_agent_api', "
                        "'identity.aliases', 'alias', 'SELECT,UPDATE') "
                        "OR EXISTS (SELECT 1 FROM pg_attribute a "
                        "CROSS JOIN LATERAL aclexplode(a.attacl) acl "
                        "WHERE a.attrelid = 'identity.aliases'::regclass "
                        "AND a.attname = 'alias' AND acl.grantee = 0) "
                        "OR EXISTS (SELECT 1 FROM pg_class c "
                        "CROSS JOIN LATERAL aclexplode(c.relacl) acl "
                        "WHERE c.oid = CAST(:probe AS regclass) "
                        "AND acl.grantee = 0)"
                    ),
                    {"probe": f"identity.{probe}"},
                )
            ).scalar_one() is False

            assert (
                await connection.execute(
                    text(
                        "SELECT has_function_privilege("
                        "'home_agent_api', "
                        "'privacy.cancel_principal_binding_work_for_person("
                        "uuid,timestamptz)', 'EXECUTE')"
                    )
                )
            ).scalar_one() is False
    finally:
        async with owner.transaction() as connection:
            await connection.execute(text(f'DROP TABLE IF EXISTS identity."{probe}"'))
        await owner.close()


@pytest.mark.asyncio
async def test_api_role_stages_binding_but_cannot_create_authority_graph() -> (
    None
):
    owner = Database(os.environ["TEST_DATABASE_URL"])
    api = Database(os.environ["TEST_API_DATABASE_URL"])
    operator = Database(os.environ["TEST_BINDING_OPERATOR_DATABASE_URL"])
    settings = api_settings()
    api_store = CoreStore(api, DisabledRuntimeSpool(), settings)
    operator_store = CoreStore(operator, DisabledRuntimeSpool(), settings)
    person_id = uuid.uuid4()
    ha_user_id = f"binding-e2e-{uuid.uuid4().hex}"
    entity_id = f"device_tracker.binding_e2e_{uuid.uuid4().hex}"
    try:
        async with owner.transaction() as connection:
            await connection.execute(
                insert(schema.people).values(
                    person_id=person_id,
                    display_name="Reviewed binding subject",
                    privacy_scope="private",
                    status="active",
                    legacy_source_sha256="a" * 64,
                )
            )

        requested = await api_store.request_principal_binding(ha_user_id)
        assert requested.state == "awaiting_operator_review"
        pending = await operator_store.list_pending_principal_binding_requests()
        request = next(
            item for item in pending.requests if item.review_code == requested.review_code
        )
        staged = await operator_store.stage_principal_binding_proposal(
            OperatorPrincipalBindingProposalStage(
                request_id=request.request_id,
                review_code=request.review_code,
                person_id=person_id,
                operator_request_id=uuid.uuid4(),
            )
        )
        proposal = await api_store.principal_binding_proposal_status(ha_user_id)
        assert proposal.state == "ready_for_confirmation"
        assert proposal.proposal_digest is not None
        with pytest.raises(DBAPIError) as contained_confirmation:
            await api_store.confirm_principal_binding_proposal(
                ha_user_id,
                PrincipalBindingConfirmation(
                    proposal_digest=proposal.proposal_digest,
                    confirmation_nonce=uuid.uuid4(),
                ),
            )
        assert sqlstate(contained_confirmation) == "42501"

        for kind in ("ha_user", "service"):
            with pytest.raises(DBAPIError) as raw_principal:
                async with api.transaction(
                    principal_id=uuid.uuid4(), ha_user_id=ha_user_id
                ) as connection:
                    await connection.execute(
                        insert(schema.principals).values(
                            principal_id=uuid.uuid4(),
                            person_id=person_id,
                            kind=kind,
                            display_label="must not create authority",
                            status="active",
                        )
                    )
            assert sqlstate(raw_principal) == "42501"

        forged_principal_id = uuid.uuid4()
        with pytest.raises(DBAPIError) as raw_artifact:
            async with api.transaction(
                principal_id=forged_principal_id, ha_user_id=ha_user_id
            ) as connection:
                await connection.execute(
                    insert(schema.confirmation_artifacts).values(
                        artifact_id=uuid.uuid4(),
                        principal_id=forged_principal_id,
                        purpose="ha_user_person_binding.confirm",
                        proposal_digest=proposal.proposal_digest,
                        client_nonce_sha256="b" * 64,
                        issued_at=datetime.now(UTC),
                        expires_at=datetime.now(UTC) + timedelta(minutes=5),
                        consumed_at=datetime.now(UTC),
                    )
                )
        # The immutable receipt INSERT is shared by still-live privacy opt-out
        # and descriptor workflows. RLS accepts only the session principal;
        # the foreign key then prevents an orphan authority artifact.
        assert sqlstate(raw_artifact) == "23503"

        with pytest.raises(DBAPIError) as raw_binding:
            async with api.transaction(
                principal_id=forged_principal_id, ha_user_id=ha_user_id
            ) as connection:
                await connection.execute(
                    insert(schema.ha_user_bindings).values(
                        binding_id=uuid.uuid4(),
                        proposal_id=staged.proposal_id,
                        ha_user_id=ha_user_id,
                        principal_id=forged_principal_id,
                        person_id=person_id,
                        confirmed_by_principal_id=forged_principal_id,
                        confirmed_at=datetime.now(UTC),
                        source_artifact_id=uuid.uuid4(),
                    )
                )
        assert sqlstate(raw_binding) == "42501"

        with pytest.raises(DBAPIError) as raw_source_binding:
            async with api.transaction(
                principal_id=forged_principal_id, ha_user_id=ha_user_id
            ) as connection:
                await connection.execute(
                    insert(schema.source_entity_bindings).values(
                        binding_id=uuid.uuid4(),
                        source_system="home_assistant",
                        entity_id=entity_id,
                        principal_id=forged_principal_id,
                        person_id=person_id,
                        confirmed_by_principal_id=forged_principal_id,
                        confirmation_artifact_id=uuid.uuid4(),
                    )
                )
        assert sqlstate(raw_source_binding) == "42501"

        async with owner.transaction() as connection:
            graph_counts = (
                await connection.execute(
                    select(
                        func.count(schema.principals.c.principal_id).label(
                            "principals"
                        ),
                        func.count(schema.confirmation_artifacts.c.artifact_id).label(
                            "artifacts"
                        ),
                        func.count(schema.ha_user_bindings.c.binding_id).label(
                            "bindings"
                        ),
                    ).select_from(
                        schema.people
                        .outerjoin(
                            schema.principals,
                            schema.people.c.person_id == schema.principals.c.person_id,
                        )
                        .outerjoin(
                            schema.confirmation_artifacts,
                            schema.principals.c.principal_id
                            == schema.confirmation_artifacts.c.principal_id,
                        )
                        .outerjoin(
                            schema.ha_user_bindings,
                            schema.principals.c.principal_id
                            == schema.ha_user_bindings.c.principal_id,
                        )
                    ).where(schema.people.c.person_id == person_id)
                )
            )
            assert dict(graph_counts.mappings().one()) == {
                "principals": 0,
                "artifacts": 0,
                "bindings": 0,
            }

        # The former import methods remain unusable even in canary: the API
        # role has no semantic People DML despite the in-process method body.
        with pytest.raises(DBAPIError) as retired_person_write:
            await api_store.create_person(
                PersonCreate(
                    person_id=uuid.uuid4(),
                    display_name="must not write",
                    privacy_scope="private",
                )
            )
        assert sqlstate(retired_person_write) == "42501"
        assert staged.person_id == person_id
    finally:
        async with owner.transaction() as connection:
            principal_ids = list(
                (
                    await connection.execute(
                        select(schema.principals.c.principal_id).where(
                            schema.principals.c.person_id == person_id
                        )
                    )
                ).scalars()
            )
            if principal_ids:
                artifact_ids = list(
                    (
                        await connection.execute(
                            select(schema.confirmation_artifacts.c.artifact_id).where(
                                schema.confirmation_artifacts.c.principal_id.in_(
                                    principal_ids
                                )
                            )
                        )
                    ).scalars()
                )
                if artifact_ids:
                    await connection.execute(
                        delete(schema.artifact_registry).where(
                            schema.artifact_registry.c.artifact_id.in_(artifact_ids)
                        )
                    )
                await connection.execute(
                    delete(schema.source_entity_bindings).where(
                        schema.source_entity_bindings.c.principal_id.in_(principal_ids)
                    )
                )
                await connection.execute(
                    delete(schema.ha_user_bindings).where(
                        schema.ha_user_bindings.c.principal_id.in_(principal_ids)
                    )
                )
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.ha_user_id == ha_user_id
                )
            )
            if principal_ids:
                await connection.execute(
                    delete(schema.confirmation_artifacts).where(
                        schema.confirmation_artifacts.c.principal_id.in_(principal_ids)
                    )
                )
                await connection.execute(
                    delete(schema.principals).where(
                        schema.principals.c.principal_id.in_(principal_ids)
                    )
                )
            await connection.execute(
                delete(schema.people).where(schema.people.c.person_id == person_id)
            )
        await operator.close()
        await api.close()
        await owner.close()


@pytest.mark.asyncio
async def test_partial_uniqueness_state_shapes_rls_and_runtime_grants() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    api = Database(os.environ["TEST_API_DATABASE_URL"])
    operator = Database(os.environ["TEST_BINDING_OPERATOR_DATABASE_URL"])
    worker = Database(os.environ["TEST_WORKER_DATABASE_URL"])
    databases = (owner, api, operator, worker)
    suffix = uuid.uuid4().hex
    ha_user_id = f"binding-rls-{suffix}"
    other_ha_user_id = f"binding-rls-other-{suffix}"
    request_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    person_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=15)
    try:
        async with api.transaction(ha_user_id=ha_user_id) as connection:
            values = request_values(
                request_id=request_id,
                ha_user_id=ha_user_id,
                review_code="23456789ABCDEFGH",
                requested_at=now,
                expires_at=expires_at,
            )
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **{key: value for key, value in values.items() if value is not None}
                )
            )

        with pytest.raises(DBAPIError) as active_duplicate:
            async with api.transaction(ha_user_id=ha_user_id) as connection:
                values = request_values(
                    request_id=uuid.uuid4(),
                    ha_user_id=ha_user_id,
                    review_code="23456789ABCDEFGJ",
                    requested_at=now,
                    expires_at=expires_at,
                )
                await connection.execute(
                    insert(schema.principal_binding_requests).values(
                        **{
                            key: value
                            for key, value in values.items()
                            if value is not None
                        }
                    )
                )
        assert sqlstate(active_duplicate) == "23505"

        with pytest.raises(DBAPIError) as invalid_shape:
            async with api.transaction(ha_user_id=other_ha_user_id) as connection:
                await connection.execute(
                    insert(schema.principal_binding_requests).values(
                        **request_values(
                            request_id=uuid.uuid4(),
                            ha_user_id=other_ha_user_id,
                            review_code="23456789ABCDEFGK",
                            requested_at=now,
                            expires_at=expires_at,
                            state="expired",
                            closed_at=now,
                        )
                    )
                )
        # The online API cannot set lifecycle-only columns during creation;
        # the column ACL rejects this before a malformed row reaches a check.
        assert sqlstate(invalid_shape) == "42501"

        async with api.transaction(ha_user_id=other_ha_user_id) as connection:
            visible = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principal_binding_requests)
                    .where(schema.principal_binding_requests.c.request_id == request_id)
                )
            ).scalar_one()
            assert visible == 0

        # Any login may assign a custom GUC. It is therefore never authority.
        async with api.transaction() as connection:
            await connection.execute(
                text("SELECT set_config('app.binding_operator', 'true', true)")
            )
            visible = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principal_binding_requests)
                    .where(schema.principal_binding_requests.c.request_id == request_id)
                )
            ).scalar_one()
            assert visible == 0

        with pytest.raises(PermissionError):
            async with api.transaction(binding_operator=True):
                pass

        async with operator.transaction(binding_operator=True) as connection:
            visible = (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principal_binding_requests)
                    .where(schema.principal_binding_requests.c.request_id == request_id)
                )
            ).scalar_one()
            assert visible == 1

        # SET LOCAL scopes must not survive a pooled connection checkout.
        async with api.transaction(ha_user_id=ha_user_id) as connection:
            assert (
                await connection.execute(
                    text("SELECT current_setting('app.ha_user_id', true)")
                )
            ).scalar_one() == ha_user_id
        async with api.transaction() as connection:
            residual_scope = (
                await connection.execute(
                    text("SELECT current_setting('app.ha_user_id', true)")
                )
            ).scalar_one_or_none()
            assert residual_scope in (None, "")
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principal_binding_requests)
                    .where(schema.principal_binding_requests.c.request_id == request_id)
                )
            ).scalar_one() == 0

        with pytest.raises(DBAPIError) as api_delete:
            async with api.transaction(ha_user_id=ha_user_id) as connection:
                await connection.execute(
                    delete(schema.principal_binding_requests).where(
                        schema.principal_binding_requests.c.request_id == request_id
                    )
                )
        assert sqlstate(api_delete) == "42501"

        with pytest.raises(DBAPIError) as api_proposal_insert:
            async with api.transaction(ha_user_id=ha_user_id) as connection:
                await connection.execute(
                    insert(schema.principal_binding_proposals).values(
                        **proposal_values(
                            proposal_id=uuid.uuid4(),
                            request_id=request_id,
                            ha_user_id=ha_user_id,
                            person_id=uuid.uuid4(),
                            staged_at=now,
                            expires_at=expires_at,
                            digest="8" * 64,
                        )
                    )
                )
        assert sqlstate(api_proposal_insert) == "42501"

        with pytest.raises(DBAPIError) as operator_request_insert:
            async with operator.transaction(binding_operator=True) as connection:
                await connection.execute(
                    insert(schema.principal_binding_requests).values(
                        **request_values(
                            request_id=uuid.uuid4(),
                            ha_user_id=other_ha_user_id,
                            review_code="23456789ABCDEFGM",
                            requested_at=now,
                            expires_at=expires_at,
                        )
                    )
                )
        assert sqlstate(operator_request_insert) == "42501"

        with pytest.raises(DBAPIError) as operator_principal_insert:
            async with operator.transaction(binding_operator=True) as connection:
                await connection.execute(
                    insert(schema.principals).values(
                        principal_id=uuid.uuid4(),
                        person_id=uuid.uuid4(),
                        kind="ha_user",
                        display_label="must not write",
                        status="active",
                    )
                )
        assert sqlstate(operator_principal_insert) == "42501"

        async with owner.transaction() as connection:
            await connection.execute(
                insert(schema.people).values(
                    person_id=person_id,
                    display_name="Operator-reviewed person",
                    status="active",
                    privacy_scope="private",
                )
            )
        async with operator.transaction(binding_operator=True) as connection:
            await connection.execute(
                insert(schema.principal_binding_proposals).values(
                    **proposal_values(
                        proposal_id=proposal_id,
                        request_id=request_id,
                        ha_user_id=ha_user_id,
                        person_id=person_id,
                        staged_at=now,
                        expires_at=expires_at,
                        digest="7" * 64,
                    )
                )
            )
            await connection.execute(
                update(schema.principal_binding_requests)
                .where(schema.principal_binding_requests.c.request_id == request_id)
                .values(state="staged", staged_at=now)
            )

        cancellation_time = datetime.now(UTC)
        with pytest.raises(DBAPIError) as arbitrary_person_cancel:
            async with api.transaction(ha_user_id=ha_user_id) as connection:
                await connection.execute(
                    text(
                        "SELECT "
                        "privacy.cancel_principal_binding_work_for_person("
                        ":person_id, :operation_time)"
                    ),
                    {
                        "person_id": person_id,
                        "operation_time": cancellation_time,
                    },
                )
        assert sqlstate(arbitrary_person_cancel) == "42501"

        # The authenticated subject can still cancel its own staged graph
        # through exact column grants and HA-user RLS.
        async with api.transaction(ha_user_id=ha_user_id) as connection:
            await connection.execute(
                update(schema.principal_binding_proposals)
                .where(
                    schema.principal_binding_proposals.c.proposal_id == proposal_id,
                    schema.principal_binding_proposals.c.state == "ready",
                )
                .values(state="cancelled")
            )
            await connection.execute(
                update(schema.principal_binding_requests)
                .where(
                    schema.principal_binding_requests.c.request_id == request_id,
                    schema.principal_binding_requests.c.state == "staged",
                )
                .values(state="cancelled", closed_at=cancellation_time)
            )
        async with operator.transaction(binding_operator=True) as connection:
            cancelled = (
                (
                    await connection.execute(
                        select(
                            schema.principal_binding_requests.c.state.label(
                                "request_state"
                            ),
                            schema.principal_binding_proposals.c.state.label(
                                "proposal_state"
                            ),
                            schema.principal_binding_requests.c.ha_user_id,
                            schema.principal_binding_requests.c.review_code,
                            schema.principal_binding_proposals.c.person_id,
                            schema.principal_binding_proposals.c.proposal_digest,
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
                            == request_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert dict(cancelled) == {
                "request_state": "cancelled",
                "proposal_state": "cancelled",
                "ha_user_id": ha_user_id,
                "review_code": "23456789ABCDEFGH",
                "person_id": person_id,
                "proposal_digest": "7" * 64,
            }

        async with owner.transaction() as connection:
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principals)
                    .where(schema.principals.c.person_id == person_id)
                )
            ).scalar_one() == 0
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.ha_user_bindings)
                    .where(schema.ha_user_bindings.c.person_id == person_id)
                )
            ).scalar_one() == 0
            grants = (
                (
                    await connection.execute(
                        select(
                            *[
                                func.has_table_privilege(
                                    role,
                                    f"identity.{table}",
                                    privilege,
                                ).label(f"{role}_{table}_{privilege}".lower())
                                for role in (
                                    "home_agent_api",
                                    "home_agent_binding_operator",
                                    "home_agent_ingest",
                                    "home_agent_worker",
                                    "home_agent_erasure",
                                    "home_agent_rollout",
                                )
                                for table in (
                                    "principal_binding_requests",
                                    "principal_binding_proposals",
                                )
                                for privilege in (
                                    "SELECT",
                                    "INSERT",
                                    "UPDATE",
                                    "DELETE",
                                )
                            ]
                        )
                    )
                )
                .mappings()
                .one()
            )
            for table in ("principal_binding_requests", "principal_binding_proposals"):
                assert grants[f"home_agent_api_{table}_select"] is True
                assert grants[f"home_agent_api_{table}_insert"] is False
                assert grants[f"home_agent_api_{table}_update"] is False
                assert grants[f"home_agent_api_{table}_delete"] is False
                assert grants[f"home_agent_binding_operator_{table}_select"] is True
                assert grants[f"home_agent_binding_operator_{table}_insert"] is (
                    table == "principal_binding_proposals"
                )
                assert (
                    grants[f"home_agent_binding_operator_{table}_update"] is False
                )
                assert grants[f"home_agent_binding_operator_{table}_delete"] is False
                # Restore/erasure cancellation is a fenced function call, not
                # a direct binding-table capability for the login role.
                assert grants[f"home_agent_erasure_{table}_select"] is False
                assert grants[f"home_agent_erasure_{table}_insert"] is False
                assert grants[f"home_agent_erasure_{table}_update"] is False
                assert grants[f"home_agent_erasure_{table}_delete"] is False
                for role in (
                    "home_agent_ingest",
                    "home_agent_worker",
                    "home_agent_rollout",
                ):
                    for privilege in ("select", "insert", "update", "delete"):
                        assert grants[f"{role}_{table}_{privilege}"] is False

            support_tables = (
                "people",
                "principals",
                "ha_user_bindings",
                "edge_privacy_user_blocks",
                "privacy_directives",
            )
            for table in support_tables:
                operator_acl = (
                    (
                        await connection.execute(
                            text(
                                "SELECT "
                                "has_table_privilege('home_agent_binding_operator', "
                                ":table, 'SELECT') AS can_select, "
                                "has_table_privilege('home_agent_binding_operator', "
                                ":table, 'INSERT') AS can_insert, "
                                "has_table_privilege('home_agent_binding_operator', "
                                ":table, 'UPDATE') AS can_update, "
                                "has_table_privilege('home_agent_binding_operator', "
                                ":table, 'DELETE') AS can_delete"
                            ),
                            {"table": f"identity.{table}"},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert dict(operator_acl) == {
                    "can_select": table != "ha_user_bindings",
                    "can_insert": False,
                    "can_update": False,
                    "can_delete": False,
                }
                if table == "ha_user_bindings":
                    readable_binding_columns = (
                        await connection.execute(
                            text(
                                "SELECT coalesce(array_agg(attname::text "
                                "ORDER BY attnum) FILTER (WHERE "
                                "has_column_privilege("
                                "'home_agent_binding_operator', "
                                "'identity.ha_user_bindings', attname, "
                                "'SELECT')), ARRAY[]::text[]) "
                                "FROM pg_attribute WHERE attrelid = "
                                "'identity.ha_user_bindings'::regclass "
                                "AND attnum > 0 AND NOT attisdropped"
                            )
                        )
                    ).scalar_one()
                    assert readable_binding_columns == [
                        "binding_id",
                        "proposal_id",
                        "ha_user_id",
                        "principal_id",
                        "person_id",
                        "confirmed_by_principal_id",
                        "confirmed_at",
                        "revoked_at",
                        "source_artifact_id",
                    ]

            allowed_update_columns = {
                "home_agent_api": {
                    "principal_binding_requests": {"state", "closed_at"},
                    "principal_binding_proposals": {"state"},
                },
                "home_agent_binding_operator": {
                    "principal_binding_requests": {
                        "state",
                        "staged_at",
                        "expires_at",
                        "closed_at",
                    },
                    "principal_binding_proposals": {"state"},
                },
            }
            protected_columns = {
                "principal_binding_requests": {
                    "request_id",
                    "ha_user_id",
                    "review_code",
                    "requested_at",
                },
                "principal_binding_proposals": {
                    "proposal_id",
                    "request_id",
                    "ha_user_id",
                    "person_id",
                    "reviewed_display_label",
                    "person_snapshot_digest",
                    "proposal_digest",
                    "stage_receipt_digest",
                    "operator_request_id",
                    "staged_at",
                    "expires_at",
                },
            }

            allowed_insert_columns = {
                "principal_binding_requests": {
                    "request_id",
                    "ha_user_id",
                    "review_code",
                    "state",
                    "requested_at",
                    "expires_at",
                },
                "confirmation_artifacts": {
                    "artifact_id",
                    "principal_id",
                    "purpose",
                    "proposal_digest",
                    "client_nonce_sha256",
                    "issued_at",
                    "expires_at",
                    "consumed_at",
                },
            }
            for table, allowed_columns in allowed_insert_columns.items():
                columns = set(schema.metadata.tables[f"identity.{table}"].c.keys())
                for column in columns:
                    can_insert = (
                        await connection.execute(
                            text(
                                "SELECT has_column_privilege("
                                "'home_agent_api', :table, :column, 'INSERT')"
                            ),
                            {"table": f"identity.{table}", "column": column},
                        )
                    ).scalar_one()
                    assert can_insert is (column in allowed_columns)

            for table in (
                "people",
                "aliases",
                "external_recognition_bindings",
                "privacy_directives",
                "legacy_role_labels",
                "legacy_relationship_candidates",
                "source_entity_bindings",
                "principals",
                "ha_user_bindings",
            ):
                for column in schema.metadata.tables[f"identity.{table}"].c.keys():
                    assert (
                        await connection.execute(
                            text(
                                "SELECT has_column_privilege("
                                "'home_agent_api', :table, :column, 'INSERT')"
                            ),
                            {"table": f"identity.{table}", "column": column},
                        )
                    ).scalar_one() is False
            for role, table_columns in allowed_update_columns.items():
                for table, allowed_columns in table_columns.items():
                    for column in allowed_columns:
                        assert (
                            await connection.execute(
                                text(
                                    "SELECT has_column_privilege("
                                    ":role, :table, :column, 'UPDATE')"
                                ),
                                {
                                    "role": role,
                                    "table": f"identity.{table}",
                                    "column": column,
                                },
                            )
                        ).scalar_one() is True
                    for column in protected_columns[table] - allowed_columns:
                        assert (
                            await connection.execute(
                                text(
                                    "SELECT has_column_privilege("
                                    ":role, :table, :column, 'UPDATE')"
                                ),
                                {
                                    "role": role,
                                    "table": f"identity.{table}",
                                    "column": column,
                                },
                            )
                        ).scalar_one() is False

            authority_acls = (
                (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', 'SELECT') "
                            "AS api_artifact_select, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', 'INSERT') "
                            "AS api_artifact_insert, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', 'UPDATE') "
                            "AS api_artifact_update, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.confirmation_artifacts', 'DELETE') "
                            "AS api_artifact_delete, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.ha_user_bindings', 'INSERT') "
                            "AS api_binding_insert, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.ha_user_bindings', 'UPDATE') "
                            "AS api_binding_update, "
                            "has_table_privilege('home_agent_api', "
                            "'identity.ha_user_bindings', 'DELETE') "
                            "AS api_binding_delete, "
                            "has_table_privilege('home_agent_binding_operator', "
                            "'identity.confirmation_artifacts', 'SELECT') "
                            "AS operator_artifact_select, "
                            "has_table_privilege('home_agent_binding_operator', "
                            "'identity.edge_privacy_blocks', 'SELECT') "
                            "AS operator_entity_block_select"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert dict(authority_acls) == {
                "api_artifact_select": True,
                "api_artifact_insert": False,
                "api_artifact_update": False,
                "api_artifact_delete": False,
                "api_binding_insert": False,
                "api_binding_update": False,
                "api_binding_delete": False,
                "operator_artifact_select": False,
                "operator_entity_block_select": False,
            }

            rls_rows = (
                (
                    await connection.execute(
                        text(
                            "SELECT relname, relrowsecurity, relforcerowsecurity "
                            "FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'identity' "
                            "AND relname = ANY(:tables)"
                        ),
                        {
                            "tables": [
                                "principal_binding_requests",
                                "principal_binding_proposals",
                                "ha_user_bindings",
                                "confirmation_artifacts",
                            ]
                        },
                    )
                )
                .mappings()
                .all()
            )
            assert {row["relname"] for row in rls_rows} == {
                "principal_binding_requests",
                "principal_binding_proposals",
                "ha_user_bindings",
                "confirmation_artifacts",
            }
            assert all(
                row["relrowsecurity"] and row["relforcerowsecurity"]
                for row in rls_rows
            )
            binding_policy_sql = " ".join(
                str(value)
                for row in (
                    (
                        await connection.execute(
                            text(
                                "SELECT qual, with_check FROM pg_policies "
                                "WHERE schemaname = 'identity' "
                                "AND tablename IN "
                                "('principal_binding_requests', "
                                "'principal_binding_proposals')"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                for value in row.values()
            )
            assert "app.binding_operator" not in binding_policy_sql
            assert "session_user" in binding_policy_sql.lower()
            function_grants = (
                (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_function_privilege('home_agent_worker', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS worker_execute, "
                            "has_function_privilege('home_agent_erasure', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS erasure_execute, "
                            "has_function_privilege('home_agent_api', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS api_execute, "
                            "has_function_privilege('home_agent_binding_operator', "
                            "'privacy.expire_principal_binding_work(timestamptz)', "
                            "'EXECUTE') AS operator_execute, "
                            "has_function_privilege('home_agent_api', "
                            "'privacy.cancel_principal_binding_work_for_person("
                            "uuid,timestamptz)', 'EXECUTE') AS api_cancel, "
                            "has_function_privilege('home_agent_erasure', "
                            "'privacy.cancel_principal_binding_work_for_person("
                            "uuid,timestamptz)', 'EXECUTE') AS erasure_cancel, "
                            "has_function_privilege('home_agent_binding_operator', "
                            "'privacy.cancel_principal_binding_work_for_person("
                            "uuid,timestamptz)', 'EXECUTE') AS operator_cancel"
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert dict(function_grants) == {
                "worker_execute": False,
                "erasure_execute": True,
                "api_execute": False,
                "operator_execute": False,
                "api_cancel": False,
                "erasure_cancel": True,
                "operator_cancel": False,
            }
    finally:
        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.request_id == request_id
                )
            )
            await connection.execute(
                delete(schema.people).where(schema.people.c.person_id == person_id)
            )
        for database in databases:
            await database.close()


@pytest.mark.asyncio
async def test_worker_expiry_is_content_free_and_preserves_graph_invariants() -> None:
    owner = Database(os.environ["TEST_DATABASE_URL"])
    worker = Database(os.environ["TEST_WORKER_DATABASE_URL"])
    request_id = uuid.uuid4()
    purge_request_id = uuid.uuid4()
    staged_request_id = uuid.uuid4()
    staged_proposal_id = uuid.uuid4()
    staged_person_id = uuid.uuid4()
    ha_user_id = f"binding-expiry-{uuid.uuid4().hex}"
    cutoff = datetime.now(UTC) - timedelta(seconds=1)
    requested_at = cutoff - timedelta(minutes=16)
    expires_at = cutoff - timedelta(minutes=1)
    worker_instance_id = uuid.uuid4()
    try:
        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                insert(schema.people).values(
                    person_id=staged_person_id,
                    display_name="Expiring staged person",
                    status="active",
                    privacy_scope="private",
                )
            )
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **request_values(
                        request_id=request_id,
                        ha_user_id=ha_user_id,
                        review_code="23456789ABCDEFGM",
                        requested_at=requested_at,
                        expires_at=expires_at,
                    )
                )
            )
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **request_values(
                        request_id=staged_request_id,
                        ha_user_id=f"binding-staged-expiry-{uuid.uuid4().hex}",
                        review_code="23456789ABCDEFGP",
                        requested_at=requested_at,
                        expires_at=expires_at,
                        state="staged",
                        staged_at=requested_at + timedelta(minutes=1),
                    )
                )
            )
            staged_ha_user_id = (
                await connection.execute(
                    select(schema.principal_binding_requests.c.ha_user_id).where(
                        schema.principal_binding_requests.c.request_id
                        == staged_request_id
                    )
                )
            ).scalar_one()
            await connection.execute(
                insert(schema.principal_binding_proposals).values(
                    **proposal_values(
                        proposal_id=staged_proposal_id,
                        request_id=staged_request_id,
                        ha_user_id=staged_ha_user_id,
                        person_id=staged_person_id,
                        staged_at=requested_at + timedelta(minutes=1),
                        expires_at=expires_at,
                        digest="9" * 64,
                    )
                )
            )
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **request_values(
                        request_id=purge_request_id,
                        ha_user_id=f"binding-purge-{uuid.uuid4().hex}",
                        review_code="23456789ABCDEFGN",
                        requested_at=cutoff - timedelta(days=10),
                        expires_at=cutoff - timedelta(days=9),
                        state="cancelled",
                        closed_at=cutoff - timedelta(days=8),
                    )
                )
            )
        async with worker.transaction() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT operations.register_worker_maintenance(:instance)"
                    ),
                    {"instance": worker_instance_id},
                )
            ).scalar_one() is True
            receipt = (
                await connection.execute(
                    text(
                        "SELECT operations.run_worker_maintenance_cycle("
                        ":instance, 0)"
                    ),
                    {"instance": worker_instance_id},
                )
            ).scalar_one()
            maintenance_attempted_at = (
                await connection.execute(
                    select(
                        schema.worker_maintenance_state.c.maintenance_attempted_at
                    )
                )
            ).scalar_one()
        assert receipt == {
            "proposals_expired": 1,
            "staged_requests_expired": 1,
            "pending_requests_expired": 1,
            "requests_expired": 2,
            "proposals_purged": 0,
            "requests_purged": 1,
        }
        assert not any(ha_user_id in str(value) for value in receipt.values())
        async with owner.transaction(binding_operator=True) as connection:
            row = (
                await connection.execute(
                    select(
                        schema.principal_binding_requests.c.state,
                        schema.principal_binding_requests.c.closed_at,
                    ).where(
                        schema.principal_binding_requests.c.request_id == request_id
                    )
                )
            ).one()
            assert row.state == "expired"
            assert row.closed_at == maintenance_attempted_at
            staged_states = (
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
                        == staged_request_id
                    )
                )
            ).one()
            assert staged_states.request_state == "expired"
            assert staged_states.proposal_state == "expired"
            assert (
                await connection.execute(
                    select(func.count())
                    .select_from(schema.principal_binding_requests)
                    .where(
                        schema.principal_binding_requests.c.request_id
                        == purge_request_id
                    )
                )
            ).scalar_one() == 0
    finally:
        async with owner.transaction(binding_operator=True) as connection:
            await connection.execute(
                delete(schema.principal_binding_requests).where(
                    schema.principal_binding_requests.c.request_id.in_(
                        (request_id, purge_request_id, staged_request_id)
                    )
                )
            )
            await connection.execute(
                delete(schema.people).where(
                    schema.people.c.person_id == staged_person_id
                )
            )
            await connection.execute(delete(schema.worker_maintenance_state))
        await worker.close()
        await owner.close()
