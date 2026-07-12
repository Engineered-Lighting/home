from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from app import schema
from app.db import Database


REQUIRED_URLS = (
    "TEST_DATABASE_URL",
    "TEST_API_DATABASE_URL",
    "TEST_BINDING_OPERATOR_DATABASE_URL",
    "TEST_INGEST_DATABASE_URL",
    "TEST_WORKER_DATABASE_URL",
    "TEST_ERASURE_DATABASE_URL",
    "TEST_ROLLOUT_DATABASE_URL",
)

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
            await connection.execute(
                insert(schema.principal_binding_requests).values(
                    **request_values(
                        request_id=request_id,
                        ha_user_id=ha_user_id,
                        review_code="23456789ABCDEFGH",
                        requested_at=now,
                        expires_at=expires_at,
                    )
                )
            )

        with pytest.raises(DBAPIError) as active_duplicate:
            async with api.transaction(ha_user_id=ha_user_id) as connection:
                await connection.execute(
                    insert(schema.principal_binding_requests).values(
                        **request_values(
                            request_id=uuid.uuid4(),
                            ha_user_id=ha_user_id,
                            review_code="23456789ABCDEFGJ",
                            requested_at=now,
                            expires_at=expires_at,
                        )
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
        assert sqlstate(invalid_shape) == "23514"

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
        async with api.transaction() as connection:
            cancellation_receipt = (
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
            ).scalar_one()
        assert cancellation_receipt == {
            "proposals_cancelled": 1,
            "requests_cancelled": 1,
        }
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
                assert grants[f"home_agent_api_{table}_insert"] is (
                    table == "principal_binding_requests"
                )
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
                assert grants[f"home_agent_erasure_{table}_select"] is True
                assert grants[f"home_agent_erasure_{table}_insert"] is False
                assert grants[f"home_agent_erasure_{table}_update"] is True
                assert grants[f"home_agent_erasure_{table}_delete"] is True
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
                    "can_select": True,
                    "can_insert": False,
                    "can_update": False,
                    "can_delete": False,
                }

            allowed_update_columns = {
                "home_agent_api": {
                    "principal_binding_requests": {"state", "closed_at"},
                    "principal_binding_proposals": {
                        "state",
                        "consumed_at",
                        "result_principal_id",
                        "confirmation_artifact_id",
                    },
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
                "api_artifact_insert": True,
                "api_artifact_update": False,
                "api_artifact_delete": False,
                "api_binding_insert": True,
                "api_binding_update": True,
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
                "api_cancel": True,
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
