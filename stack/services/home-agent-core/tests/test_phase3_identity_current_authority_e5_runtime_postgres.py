from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


OWNER_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_OWNER_DATABASE_URL"
)
AUTHORITY_DATABASE_ENV = (
    "TEST_PHASE3_IDENTITY_CURRENT_AUTHORITY_E5_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
FUNCTION = "operations.evaluate_current_identity_semantic_authority"


def _runtime_configured() -> bool:
    return bool(
        os.getenv(OWNER_DATABASE_ENV) and os.getenv(AUTHORITY_DATABASE_ENV)
    )


def _async_url(value: str):
    return make_url(value).set(drivername="postgresql+psycopg")


def _engine(env_name: str, *, serializable: bool = False) -> AsyncEngine:
    options: dict[str, object] = {
        "pool_pre_ping": True,
        "hide_parameters": True,
    }
    if serializable:
        options["isolation_level"] = "SERIALIZABLE"
    return create_async_engine(_async_url(os.environ[env_name]), **options)


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


async def _promotion(connection):
    return (
        await connection.execute(
            text(
                "SELECT promotion_id, run_id, erasure_ledger_epoch, "
                "erasure_ledger_head_hash "
                "FROM operations.semantic_authority_promotions "
                "WHERE authority_scope='identity_semantics'"
            )
        )
    ).one()


async def _evaluate(
    engine: AsyncEngine, promotion_id: uuid.UUID | str | None
) -> str:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(f"SELECT {FUNCTION}(:promotion_id)"),
                {"promotion_id": promotion_id},
            )
        ).scalar_one()


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5a_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    assert _runtime_configured()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_catalog_and_positive_database_authority_contract() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    try:
        async with owner_engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one()
            assert revision == "0015_current_authority_e5a"
            promotion = await _promotion(connection)
            role_contract = (
                await connection.execute(
                    text(
                        "SELECT "
                        "NOT rolcanlogin, NOT rolinherit, NOT rolsuper, "
                        "NOT rolcreatedb, NOT rolcreaterole, "
                        "NOT rolreplication, NOT rolbypassrls, "
                        "rolconnlimit=0 "
                        "FROM pg_catalog.pg_roles "
                        "WHERE rolname='home_agent_identity_authority_kernel'"
                    )
                )
            ).one()
            assert all(role_contract)
            function_contract = (
                await connection.execute(
                    text(
                        "SELECT p.prosecdef, p.provolatile='v', "
                        "p.prorettype='varchar'::regtype, "
                        "p.proconfig=ARRAY["
                        "'search_path=pg_catalog, pg_temp']::text[], "
                        "r.rolname='home_agent_identity_authority_kernel' "
                        "FROM pg_catalog.pg_proc p "
                        "JOIN pg_catalog.pg_roles r ON r.oid=p.proowner "
                        "WHERE p.oid="
                        "'operations."
                        "evaluate_current_identity_semantic_authority(uuid)'"
                        "::regprocedure"
                    )
                )
            ).one()
            assert all(function_contract)
            policy_contract = (
                await connection.execute(
                    text(
                        "WITH reference AS ("
                        "SELECT polqual FROM pg_catalog.pg_policy "
                        "WHERE polrelid="
                        "'operations.semantic_authority_promotions'::regclass "
                        "AND polname='identity_authority_e5_select') "
                        "SELECT count(*)=12, bool_and(p.polpermissive), "
                        "bool_and(p.polcmd='r'), "
                        "bool_and(p.polwithcheck IS NULL), "
                        "bool_and(p.polqual::text=reference.polqual::text) "
                        "FROM pg_catalog.pg_policy p CROSS JOIN reference "
                        "WHERE p.polname='identity_authority_e5_select'"
                    )
                )
            ).one()
            assert all(policy_contract)

        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "current_database_authority"
    finally:
        await owner_engine.dispose()
        await authority_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_rejects_wrong_transaction_and_invalid_input() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    read_committed_engine = _engine(AUTHORITY_DATABASE_ENV)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    try:
        async with owner_engine.connect() as connection:
            promotion = await _promotion(connection)

        with pytest.raises(DBAPIError) as transaction_error:
            await _evaluate(read_committed_engine, promotion.promotion_id)
        assert _sqlstate(transaction_error.value) == "25000"

        for invalid in (
            None,
            uuid.UUID("0197f6f0-0000-4000-8000-000000000001"),
        ):
            with pytest.raises(DBAPIError) as input_error:
                await _evaluate(authority_engine, invalid)
            assert _sqlstate(input_error.value) == "22023"

        missing = uuid.UUID("0197f6f0-0000-7000-8000-000000000001")
        assert await _evaluate(
            authority_engine, missing
        ) == "promotion_missing"
    finally:
        await owner_engine.dispose()
        await read_committed_engine.dispose()
        await authority_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_rejects_wrong_session_and_direct_evidence_reads() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV, serializable=True)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    try:
        async with owner_engine.connect() as connection:
            promotion = await _promotion(connection)

        with pytest.raises(DBAPIError) as role_error:
            await _evaluate(owner_engine, promotion.promotion_id)
        assert _sqlstate(role_error.value) == "42501"

        async with authority_engine.connect() as connection:
            with pytest.raises(DBAPIError) as direct_read_error:
                await connection.execute(
                    text(
                        "SELECT promotion_id "
                        "FROM operations.semantic_authority_promotions"
                    )
                )
            assert _sqlstate(direct_read_error.value) == "42501"
            await connection.rollback()
    finally:
        await owner_engine.dispose()
        await authority_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_rejects_a_structurally_changed_promotion_graph() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    original_policy_digest: str | None = None
    run_id: uuid.UUID | None = None

    async def set_policy_digest(value: str) -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE operations.reviewed_identity_migration_runs "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            await connection.execute(
                text(
                    "UPDATE operations.reviewed_identity_migration_runs "
                    "SET policy_digest=:policy_digest WHERE run_id=:run_id"
                ),
                {"policy_digest": value, "run_id": run_id},
            )
            await connection.execute(
                text(
                    "ALTER TABLE operations.reviewed_identity_migration_runs "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE operations.reviewed_identity_migration_runs "
                    "FORCE ROW LEVEL SECURITY"
                )
            )

    try:
        async with owner_engine.connect() as connection:
            promotion = await _promotion(connection)
            run_id = promotion.run_id
            original_policy_digest = (
                await connection.execute(
                    text(
                        "SELECT policy_digest "
                        "FROM operations.reviewed_identity_migration_runs "
                        "WHERE run_id=:run_id"
                    ),
                    {"run_id": run_id},
                )
            ).scalar_one()

        changed_policy_digest = (
            "a" * 64 if original_policy_digest != "a" * 64 else "b" * 64
        )
        await set_policy_digest(changed_policy_digest)
        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "promotion_invalid"
    finally:
        if original_policy_digest is not None and run_id is not None:
            await set_policy_digest(original_policy_digest)
        await owner_engine.dispose()
        await authority_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_holds_all_three_currentness_locks_until_commit() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    try:
        async with owner_engine.connect() as connection:
            promotion = await _promotion(connection)

        async with authority_engine.connect() as authority_connection:
            transaction = await authority_connection.begin()
            result = (
                await authority_connection.execute(
                    text(f"SELECT {FUNCTION}(:promotion_id)"),
                    {"promotion_id": promotion.promotion_id},
                )
            ).scalar_one()
            assert result == "current_database_authority"
            bounded_transaction = (
                await authority_connection.execute(
                    text(
                        "SELECT current_setting('statement_timeout'), "
                        "current_setting('lock_timeout'), "
                        "current_setting("
                        "'idle_in_transaction_session_timeout'), "
                        "current_setting('transaction_timeout')"
                    )
                )
            ).one()
            assert tuple(bounded_transaction) == (
                "15s",
                "5s",
                "15s",
                "30s",
            )

            statements = (
                "UPDATE privacy.identity_semantic_write_fence "
                "SET fence_generation=fence_generation "
                "WHERE fence_key='global'",
                "UPDATE operations.reviewed_identity_migration_runs "
                "SET expires_at=expires_at WHERE run_id=:run_id",
                "UPDATE operations.erasure_ledger_state "
                "SET updated_at=updated_at WHERE state_key='current'",
            )
            for statement in statements:
                async with owner_engine.connect() as owner_connection:
                    await owner_connection.execute(
                        text("SET LOCAL lock_timeout='200ms'")
                    )
                    with pytest.raises(DBAPIError) as lock_error:
                        await owner_connection.execute(
                            text(statement), {"run_id": promotion.run_id}
                        )
                    assert _sqlstate(lock_error.value) == "55P03"
                    await owner_connection.rollback()
            await transaction.commit()
    finally:
        await owner_engine.dispose()
        await authority_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5a PostgreSQL owner/binding-operator URLs are not configured",
)
async def test_e5a_ledger_categories_and_contiguous_advance() -> None:
    owner_engine = _engine(OWNER_DATABASE_ENV)
    authority_engine = _engine(AUTHORITY_DATABASE_ENV, serializable=True)
    original_state: tuple[int, str, datetime] | None = None
    inserted_epoch: int | None = None
    try:
        async with owner_engine.connect() as connection:
            promotion = await _promotion(connection)
            state = (
                await connection.execute(
                    text(
                        "SELECT recorded_epoch, recorded_head_hash, updated_at "
                        "FROM operations.erasure_ledger_state "
                        "WHERE state_key='current'"
                    )
                )
            ).one()
            original_state = tuple(state)

        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM operations.erasure_ledger_state "
                    "WHERE state_key='current'"
                )
            )
        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "ledger_state_missing"
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO operations.erasure_ledger_state("
                    "state_key,recorded_epoch,recorded_head_hash,updated_at) "
                    "VALUES('current',:epoch,:head,:updated_at)"
                ),
                {
                    "epoch": original_state[0],
                    "head": original_state[1],
                    "updated_at": original_state[2],
                },
            )

        divergent_hash = (
            "1" * 64
            if promotion.erasure_ledger_head_hash != "1" * 64
            else "2" * 64
        )
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE operations.erasure_ledger_state "
                    "SET recorded_epoch=:epoch, recorded_head_hash=:head "
                    "WHERE state_key='current'"
                ),
                {
                    "epoch": promotion.erasure_ledger_epoch,
                    "head": divergent_hash,
                },
            )
        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "ledger_diverged"

        async with owner_engine.begin() as connection:
            maximum_receipt = (
                await connection.execute(
                    text(
                        "SELECT coalesce(max(ledger_epoch),0) "
                        "FROM operations.erasure_replay_receipts"
                    )
                )
            ).scalar_one()
            invalid_epoch = max(
                original_state[0], maximum_receipt
            ) + 2
            await connection.execute(
                text(
                    "UPDATE operations.erasure_ledger_state "
                    "SET recorded_epoch=:epoch, recorded_head_hash=:head "
                    "WHERE state_key='current'"
                ),
                {"epoch": invalid_epoch, "head": "3" * 64},
            )
        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "ledger_continuity_invalid"

        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE operations.erasure_ledger_state "
                    "SET recorded_epoch=:epoch, recorded_head_hash=:head, "
                    "updated_at=:updated_at WHERE state_key='current'"
                ),
                {
                    "epoch": original_state[0],
                    "head": original_state[1],
                    "updated_at": original_state[2],
                },
            )
            inserted_epoch = original_state[0] + 1
            next_hash = (
                "4" * 64
                if original_state[1] != "4" * 64
                else "5" * 64
            )
            await connection.execute(
                text(
                    "INSERT INTO operations.erasure_replay_receipts("
                    "ledger_epoch,outbox_id,erasure_request_id,"
                    "record_hash,record_digest) "
                    "VALUES(:epoch,:outbox,:request,:head,:digest)"
                ),
                {
                    "epoch": inserted_epoch,
                    "outbox": uuid.uuid4(),
                    "request": uuid.uuid4(),
                    "head": next_hash,
                    "digest": "6" * 64,
                },
            )
            await connection.execute(
                text(
                    "UPDATE operations.erasure_ledger_state "
                    "SET recorded_epoch=:epoch, recorded_head_hash=:head "
                    "WHERE state_key='current'"
                ),
                {"epoch": inserted_epoch, "head": next_hash},
            )
        assert await _evaluate(
            authority_engine, promotion.promotion_id
        ) == "current_database_authority"

        if promotion.erasure_ledger_epoch > 0:
            async with owner_engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE operations.erasure_ledger_state "
                        "SET recorded_epoch=:epoch, recorded_head_hash=:head "
                        "WHERE state_key='current'"
                    ),
                    {
                        "epoch": promotion.erasure_ledger_epoch - 1,
                        "head": "7" * 64,
                    },
                )
            assert await _evaluate(
                authority_engine, promotion.promotion_id
            ) == "ledger_regressed"
    finally:
        if original_state is not None:
            async with owner_engine.begin() as connection:
                if inserted_epoch is not None:
                    await connection.execute(
                        text(
                            "DELETE FROM operations.erasure_replay_receipts "
                            "WHERE ledger_epoch=:epoch"
                        ),
                        {"epoch": inserted_epoch},
                    )
                await connection.execute(
                    text(
                        "INSERT INTO operations.erasure_ledger_state("
                        "state_key,recorded_epoch,recorded_head_hash,updated_at) "
                        "VALUES('current',:epoch,:head,:updated_at) "
                        "ON CONFLICT(state_key) DO UPDATE SET "
                        "recorded_epoch=excluded.recorded_epoch,"
                        "recorded_head_hash=excluded.recorded_head_hash,"
                        "updated_at=excluded.updated_at"
                    ),
                    {
                        "epoch": original_state[0],
                        "head": original_state[1],
                        "updated_at": original_state[2],
                    },
                )
        await owner_engine.dispose()
        await authority_engine.dispose()
