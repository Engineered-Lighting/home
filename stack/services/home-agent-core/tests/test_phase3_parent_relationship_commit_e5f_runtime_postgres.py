from __future__ import annotations

import asyncio
import os
import secrets
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.ids import uuid7


OWNER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5F_OWNER_DATABASE_URL"
)
COMMITTER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5F_COMMITTER_DATABASE_URL"
)
OPERATOR_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5F_OPERATOR_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
STAGE_FUNCTION = (
    "identity.stage_authenticated_parent_relationship_e5e("
    "character varying,uuid,uuid,uuid,uuid,uuid,"
    "character varying,character varying)"
)
COMMIT_FUNCTION = (
    "identity.commit_authenticated_parent_relationship_e5f("
    "character varying,uuid,character varying,uuid,"
    "uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,"
    "uuid,uuid,uuid,uuid,uuid)"
)
REVIEW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _configured() -> bool:
    return all(
        os.getenv(name)
        for name in (
            OWNER_DATABASE_ENV,
            COMMITTER_DATABASE_ENV,
            OPERATOR_DATABASE_ENV,
        )
    )


def _engine(
    environment_name: str, *, serializable: bool = False
) -> AsyncEngine:
    url = make_url(os.environ[environment_name]).set(
        drivername="postgresql+psycopg"
    )
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "hide_parameters": True,
    }
    if serializable:
        options["isolation_level"] = "SERIALIZABLE"
    return create_async_engine(url, **options)


def _sqlstate(error: BaseException) -> str | None:
    original = error.orig if isinstance(error, DBAPIError) else error
    return getattr(original, "sqlstate", None)


def _review_code() -> str:
    return "".join(secrets.choice(REVIEW_ALPHABET) for _ in range(16))


def _new_commit_values(
    ha_user_id: str,
    proposal_id: uuid.UUID,
    proposal_digest: str,
) -> dict[str, object]:
    return {
        "ha_user_id": ha_user_id,
        "proposal_id": proposal_id,
        "proposal_digest": proposal_digest,
        "nonce": uuid.uuid4(),
        "artifact_id": uuid7(),
        "memory_id": uuid7(),
        "receipt_id": uuid7(),
        "fact_id_0": uuid7(),
        "fact_version_id_0": uuid7(),
        "confirmation_support_id_0": uuid7(),
        "legacy_support_id_0": uuid7(),
        "receipt_edge_id_0": uuid7(),
        "fact_id_1": uuid7(),
        "fact_version_id_1": uuid7(),
        "confirmation_support_id_1": uuid7(),
        "legacy_support_id_1": uuid7(),
        "receipt_edge_id_1": uuid7(),
    }


async def _stage(
    owner_engine: AsyncEngine,
    committer_engine: AsyncEngine,
) -> tuple[str, uuid.UUID, str]:
    async with owner_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT binding.ha_user_id "
                    "FROM identity.ha_user_bindings AS binding "
                    "WHERE binding.revoked_at IS NULL "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM identity.legacy_role_labels AS label "
                    "WHERE label.person_id=binding.person_id "
                    "AND label.role_label='parent' "
                    "AND label.perspective='unknown')"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    values = {
        "ha_user_id": rows[0],
        "request_id": uuid7(),
        "proposal_id": uuid7(),
        "operator_request_id": uuid7(),
        "edge_id_0": uuid7(),
        "edge_id_1": uuid7(),
        "review_code_0": _review_code(),
        "review_code_1": _review_code(),
    }
    async with committer_engine.begin() as connection:
        staged = (
            await connection.execute(
                text(
                    "SELECT * FROM identity."
                    "stage_authenticated_parent_relationship_e5e("
                    "CAST(:ha_user_id AS varchar),:request_id,"
                    ":proposal_id,:operator_request_id,:edge_id_0,"
                    ":edge_id_1,CAST(:review_code_0 AS varchar),"
                    "CAST(:review_code_1 AS varchar))"
                ),
                values,
            )
        ).mappings().one()
    return rows[0], staged["proposal_id"], staged["proposal_digest"]


async def _commit(
    engine: AsyncEngine,
    values: dict[str, object],
) -> tuple[uuid.UUID, object]:
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT * FROM identity."
                    "commit_authenticated_parent_relationship_e5f("
                    "CAST(:ha_user_id AS varchar),:proposal_id,"
                    "CAST(:proposal_digest AS varchar),:nonce,"
                    ":artifact_id,:memory_id,:receipt_id,"
                    ":fact_id_0,:fact_version_id_0,"
                    ":confirmation_support_id_0,:legacy_support_id_0,"
                    ":receipt_edge_id_0,:fact_id_1,:fact_version_id_1,"
                    ":confirmation_support_id_1,:legacy_support_id_1,"
                    ":receipt_edge_id_1)"
                ),
                values,
            )
        ).one()
    return row.receipt_id, row.committed_at


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5f_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    assert _configured()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5f URLs are not configured")
async def test_e5f_catalog_is_split_credential_and_table_blind() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    try:
        async with owner.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one() == "0020_parent_commit_e5f"
            function = (
                await connection.execute(
                    text(
                        "SELECT owner.rolname, function.prosecdef, "
                        "function.provolatile, function.proparallel, "
                        "function.proconfig "
                        "FROM pg_catalog.pg_proc AS function "
                        "JOIN pg_catalog.pg_roles AS owner "
                        "ON owner.oid=function.proowner "
                        "WHERE function.oid="
                        "pg_catalog.to_regprocedure(:function)"
                    ),
                    {"function": COMMIT_FUNCTION},
                )
            ).one()
            assert function[0:4] == (
                "home_agent_parent_relationship_kernel",
                True,
                "v",
                "u",
            )
            assert set(function.proconfig) == {
                "search_path=pg_catalog",
                "row_security=on",
            }
            privileges = (
                await connection.execute(
                    text(
                        "SELECT "
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_committer',"
                        "pg_catalog.to_regprocedure(:commit),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_committer',"
                        "pg_catalog.to_regprocedure(:stage),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_operator',"
                        "pg_catalog.to_regprocedure(:commit),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_api',"
                        "pg_catalog.to_regprocedure(:commit),'EXECUTE')"
                    ),
                    {"commit": COMMIT_FUNCTION, "stage": STAGE_FUNCTION},
                )
            ).one()
            assert privileges == (True, True, False, False)
    finally:
        await owner.dispose()

    for environment_name in (
        COMMITTER_DATABASE_ENV,
        OPERATOR_DATABASE_ENV,
    ):
        engine = _engine(environment_name)
        try:
            with pytest.raises(DBAPIError) as denied:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM knowledge.fact_versions"
                        )
                    )
            assert _sqlstate(denied.value) == "42501"
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _configured(), reason="E5f URLs are not configured")
async def test_e5f_two_edge_commit_is_atomic_race_safe_and_replayable() -> None:
    owner = _engine(OWNER_DATABASE_ENV)
    staging = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    first = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    second = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        ha_user_id, proposal_id, proposal_digest = await _stage(
            owner, staging
        )
        first_values = _new_commit_values(
            ha_user_id, proposal_id, proposal_digest
        )
        second_values = _new_commit_values(
            ha_user_id, proposal_id, proposal_digest
        )
        results = await asyncio.gather(
            _commit(first, first_values),
            _commit(second, second_values),
            return_exceptions=True,
        )
        winners = [
            (index, result)
            for index, result in enumerate(results)
            if isinstance(result, tuple)
        ]
        failures = [
            result for result in results if isinstance(result, BaseException)
        ]
        assert len(winners) == 1
        assert len(failures) == 1
        assert _sqlstate(failures[0]) in {"23514", "40001"}
        winner_values = first_values if winners[0][0] == 0 else second_values
        winner_receipt, winner_time = winners[0][1]
        assert winner_receipt == winner_values["receipt_id"]

        replay_receipt, replay_time = await _commit(first, winner_values)
        assert replay_receipt == winner_receipt
        assert replay_time == winner_time

        drift = dict(winner_values)
        drift["receipt_edge_id_1"] = uuid7()
        with pytest.raises(DBAPIError) as replay_drift:
            await _commit(first, drift)
        assert _sqlstate(replay_drift.value) == "23514"
        assert "parent_relationship_e5f_replay_drift" in str(
            replay_drift.value.orig
        )

        async with owner.connect() as connection:
            graph = (
                await connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE memory_transaction_id=:memory_id),"
                        "(SELECT count(*) FROM knowledge.fact_support "
                        "WHERE fact_version_id IN ("
                        "SELECT fact_version_id "
                        "FROM knowledge.fact_versions "
                        "WHERE memory_transaction_id=:memory_id)),"
                        "(SELECT count(*) FROM operations."
                        "parent_relationship_authority_receipt_edges "
                        "WHERE receipt_id=:receipt_id),"
                        "(SELECT count(*) FROM identity."
                        "parent_relationship_proposals "
                        "WHERE proposal_id=:proposal_id "
                        "AND state='consumed'),"
                        "(SELECT count(*) FROM knowledge.fact_versions "
                        "WHERE memory_transaction_id=:memory_id "
                        "AND predicate='parent_of' "
                        "AND authority='explicit_related_party' "
                        "AND support='explicit_authority' "
                        "AND resolution='accepted' "
                        "AND privacy_scope='private')"
                    ),
                    {
                        "memory_id": winner_values["memory_id"],
                        "receipt_id": winner_values["receipt_id"],
                        "proposal_id": proposal_id,
                    },
                )
            ).one()
            assert graph == (2, 4, 2, 1, 2)
    finally:
        await second.dispose()
        await first.dispose()
        await staging.dispose()
        await owner.dispose()

