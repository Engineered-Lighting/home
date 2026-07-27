from __future__ import annotations

import base64
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL"
CUTOVER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL"
SUCCESS_DOCUMENT_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DOCUMENT_B64"
SUCCESS_ADMISSION_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_ADMISSION_ID"
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"


def _runtime_configured() -> bool:
    return bool(
        os.getenv(OWNER_DATABASE_ENV) and os.getenv(CUTOVER_DATABASE_ENV)
    )


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e4_hosted_gate_cannot_silently_skip_admitted_success() -> None:
    assert _runtime_configured()
    encoded_document = os.getenv(SUCCESS_DOCUMENT_ENV)
    admission_value = os.getenv(SUCCESS_ADMISSION_ENV)
    assert encoded_document
    assert admission_value
    document = base64.b64decode(encoded_document, validate=True)
    admission_id = uuid.UUID(admission_value)
    assert 2 <= len(document) <= 1048576
    assert admission_id.version == 7
    assert admission_id.variant == uuid.RFC_4122


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def _async_url(value: str):
    return make_url(value).set(drivername="postgresql+psycopg")


async def _catalog_contract(
    connection,
) -> tuple[bool, bool, bool, bool, bool]:
    row = (
        await connection.execute(
            text(
                "SELECT "
                "(SELECT relrowsecurity AND relforcerowsecurity "
                "FROM pg_catalog.pg_class "
                "WHERE oid='operations.semantic_authority_promotions'"
                "::regclass), "
                "EXISTS (SELECT 1 FROM pg_catalog.pg_policy "
                "WHERE polrelid="
                "'operations.reviewed_identity_migration_runs'::regclass "
                "AND polname='identity_cutover_e4_select'), "
                "NOT has_table_privilege("
                "'home_agent_identity_cutover',"
                "'operations.semantic_authority_promotions','SELECT'), "
                "NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger "
                "WHERE tgrelid IN ("
                "'operations.enforced_legacy_identity_writer_freezes'"
                "::regclass,"
                "'operations.reviewed_identity_cutover_admissions'::regclass,"
                "'operations.semantic_authority_promotions'::regclass"
                ") AND NOT tgisinternal), "
                "NOT EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite "
                "WHERE ev_class IN ("
                "'operations.enforced_legacy_identity_writer_freezes'"
                "::regclass,"
                "'operations.reviewed_identity_cutover_admissions'::regclass,"
                "'operations.semantic_authority_promotions'::regclass"
                "))"
            )
        )
    ).one()
    return tuple(row)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E4 PostgreSQL owner/finalizer test URLs are not configured",
)
async def test_e4_installed_contract_is_dormant_and_direct_roles_are_dark() -> None:
    owner_engine = create_async_engine(
        _async_url(os.environ[OWNER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    finalizer_engine = create_async_engine(
        _async_url(os.environ[CUTOVER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with owner_engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one()
            assert revision == "0014_identity_cutover_e4"
            rows = (
                await connection.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity "
                        "FROM pg_catalog.pg_class "
                        "WHERE oid IN ("
                        "'operations.enforced_legacy_identity_writer_freezes'"
                        "::regclass,"
                        "'operations.reviewed_identity_cutover_admissions'"
                        "::regclass,"
                        "'operations.semantic_authority_promotions'::regclass"
                        ") ORDER BY relname"
                    )
                )
            ).all()
            assert len(rows) == 3
            assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
            promotion_count = (
                await connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM operations.semantic_authority_promotions"
                    )
                )
            ).scalar_one()
            assert promotion_count == 0
            kernel_contract = (
                await connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege("
                        "'home_agent_identity_cutover_kernel',"
                        "'operations.reviewed_identity_migration_runs',"
                        "'SELECT'), "
                        "has_table_privilege("
                        "'home_agent_identity_cutover_kernel',"
                        "'operations.erasure_ledger_state','SELECT'), "
                        "has_column_privilege("
                        "'home_agent_identity_cutover_kernel',"
                        "'operations.erasure_ledger_state','updated_at',"
                        "'UPDATE'), "
                        "has_column_privilege("
                        "'home_agent_identity_cutover_kernel',"
                        "'operations.erasure_ledger_state','recorded_epoch',"
                        "'UPDATE'), "
                        "(SELECT count(*) FROM pg_catalog.pg_policy "
                        "WHERE polrelid = "
                        "'operations.reviewed_identity_migration_runs'"
                        "::regclass "
                        "AND polname = 'identity_cutover_e4_select')"
                    )
                )
            ).one()
            assert tuple(kernel_contract) == (True, True, True, False, 1)

        async with finalizer_engine.connect() as connection:
            direct_table_access = (
                await connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege("
                        "current_user,"
                        "'operations.semantic_authority_promotions',"
                        "'INSERT'), "
                        "has_table_privilege("
                        "current_user,"
                        "'operations.reviewed_identity_cutover_admissions',"
                        "'SELECT'), "
                        "has_function_privilege("
                        "current_user,"
                        "'operations.commit_reviewed_identity_cutover"
                        "(bytea,uuid)',"
                        "'EXECUTE')"
                    )
                )
            ).one()
            assert direct_table_access == (False, False, True)
    finally:
        await owner_engine.dispose()
        await finalizer_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E4 PostgreSQL owner/finalizer test URLs are not configured",
)
async def test_e4_function_rejects_unknown_admission_without_writing() -> None:
    finalizer_engine = create_async_engine(
        _async_url(os.environ[CUTOVER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    owner_engine = create_async_engine(
        _async_url(os.environ[OWNER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with finalizer_engine.connect() as connection:
            with pytest.raises(DBAPIError) as exc_info:
                await connection.execute(
                    text(
                        "SELECT operations.commit_reviewed_identity_cutover("
                        "convert_to('{}','UTF8'),"
                        "'0197f6f0-0000-7000-8000-000000000001'::uuid)"
                    )
                )
            assert _sqlstate(exc_info.value) == "22023"
            await connection.rollback()
        async with owner_engine.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM operations.semantic_authority_promotions"
                    )
                )
            ).scalar_one() == 0
    finally:
        await finalizer_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E4 PostgreSQL owner/finalizer test URLs are not configured",
)
async def test_e4_function_rejects_unbounded_input_before_evidence_lookup() -> None:
    finalizer_engine = create_async_engine(
        _async_url(os.environ[CUTOVER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with finalizer_engine.connect() as connection:
            for document, admission_id in (
                (b"x" * (1024 * 1024 + 1), uuid.uuid4()),
                (b"{}", uuid.uuid4()),
            ):
                with pytest.raises(DBAPIError) as exc_info:
                    await connection.execute(
                        text(
                            "SELECT operations.commit_reviewed_identity_cutover("
                            ":document,:admission_id)"
                        ),
                        {
                            "document": document,
                            "admission_id": admission_id,
                        },
                    )
                assert _sqlstate(exc_info.value) == "22023"
                await connection.rollback()
    finally:
        await finalizer_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E4 PostgreSQL owner/finalizer test URLs are not configured",
)
async def test_e4_canonical_jsonb_bytes_reject_duplicates_and_whitespace() -> None:
    owner_engine = create_async_engine(
        _async_url(os.environ[OWNER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with owner_engine.connect() as connection:
            results = []
            for document in (
                b'{"x":"1","x":"2"}',
                b'{"x":"2"}',
                b'{"x": "2"}',
            ):
                results.append(
                    (
                        await connection.execute(
                            text(
                                "SELECT CAST(:document AS bytea) = "
                                "convert_to(("
                                "convert_from(CAST(:document AS bytea),'UTF8')"
                                "::jsonb)::text,'UTF8')"
                            ),
                            {"document": document},
                        )
                    ).scalar_one()
                )
            assert results == [False, False, True]
    finally:
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E4 PostgreSQL owner/finalizer test URLs are not configured",
)
async def test_e4_catalog_contract_detects_transactional_tamper() -> None:
    owner_engine = create_async_engine(
        _async_url(os.environ[OWNER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with owner_engine.connect() as connection:
            assert await _catalog_contract(connection) == (
                True,
                True,
                True,
                True,
                True,
            )
            await connection.rollback()

            await connection.execute(
                text(
                    "ALTER TABLE operations.semantic_authority_promotions "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            assert await _catalog_contract(connection) == (
                False,
                True,
                True,
                True,
                True,
            )
            await connection.rollback()

            await connection.execute(
                text(
                    "DROP POLICY identity_cutover_e4_select ON "
                    "operations.reviewed_identity_migration_runs"
                )
            )
            assert await _catalog_contract(connection) == (
                True,
                False,
                True,
                True,
                True,
            )
            await connection.rollback()

            await connection.execute(
                text(
                    "GRANT SELECT ON "
                    "operations.semantic_authority_promotions "
                    "TO home_agent_identity_cutover"
                )
            )
            assert await _catalog_contract(connection) == (
                True,
                True,
                False,
                True,
                True,
            )
            await connection.rollback()
            await connection.execute(
                text(
                    "CREATE FUNCTION operations.e4_test_trigger() "
                    "RETURNS trigger LANGUAGE plpgsql AS "
                    "$$BEGIN RETURN NEW; END$$"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER e4_test_trigger BEFORE INSERT ON "
                    "operations.semantic_authority_promotions "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "operations.e4_test_trigger()"
                )
            )
            assert await _catalog_contract(connection) == (
                True,
                True,
                True,
                False,
                True,
            )
            await connection.rollback()

            await connection.execute(
                text(
                    "CREATE RULE e4_test_rule AS ON INSERT TO "
                    "operations.semantic_authority_promotions "
                    "DO INSTEAD NOTHING"
                )
            )
            assert await _catalog_contract(connection) == (
                True,
                True,
                True,
                True,
                False,
            )
            await connection.rollback()
            assert await _catalog_contract(connection) == (
                True,
                True,
                True,
                True,
                True,
            )
    finally:
        await owner_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (
        _runtime_configured()
        and os.getenv(SUCCESS_DOCUMENT_ENV)
        and os.getenv(SUCCESS_ADMISSION_ENV)
    ),
    reason="E4 admitted success fixture is not configured",
)
async def test_e4_admitted_commit_is_atomic_and_exact_replay_is_read_only() -> None:
    document = base64.b64decode(
        os.environ[SUCCESS_DOCUMENT_ENV], validate=True
    )
    admission_id = uuid.UUID(os.environ[SUCCESS_ADMISSION_ENV])
    cutover_engine = create_async_engine(
        _async_url(os.environ[CUTOVER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    owner_engine = create_async_engine(
        _async_url(os.environ[OWNER_DATABASE_ENV]),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    try:
        async with cutover_engine.connect() as connection:
            transaction = await connection.begin()
            first = (
                await connection.execute(
                    text(
                        "SELECT operations.commit_reviewed_identity_cutover("
                        ":document,:admission_id)"
                    ),
                    {"document": document, "admission_id": admission_id},
                )
            ).scalar_one()
            # The function holds a row-level SHARE lock on the admitted
            # erasure-ledger head through transaction commit. A concurrent
            # head advance must wait rather than commit against the old head.
            async with owner_engine.connect() as owner_connection:
                await owner_connection.execute(
                    text("SET LOCAL lock_timeout = '200ms'")
                )
                with pytest.raises(DBAPIError) as exc_info:
                    await owner_connection.execute(
                        text(
                            "UPDATE operations.erasure_ledger_state "
                            "SET updated_at = clock_timestamp() "
                            "WHERE state_key = 'current'"
                        )
                    )
                assert _sqlstate(exc_info.value) == "55P03"
                await owner_connection.rollback()
            await transaction.commit()
        async with cutover_engine.begin() as connection:
            replay = (
                await connection.execute(
                    text(
                        "SELECT operations.commit_reviewed_identity_cutover("
                        ":document,:admission_id)"
                    ),
                    {"document": document, "admission_id": admission_id},
                )
            ).scalar_one()
        assert replay == first

        async with owner_engine.connect() as connection:
            receipt = (
                await connection.execute(
                    text(
                        "SELECT authority_scope, authority_status, "
                        "authoritative_at_commit, writer_freeze_id, "
                        "count(*) OVER () "
                        "FROM operations.semantic_authority_promotions "
                        "WHERE admission_id=:admission_id"
                    ),
                    {"admission_id": admission_id},
                )
            ).one()
            assert tuple(receipt[:3]) == (
                "identity_semantics",
                "historical_authoritative_commit",
                True,
            )
            assert receipt[4] == 1

            receipt_count = (
                await connection.execute(
                    text(
                        "SELECT count(*) "
                        "FROM operations.semantic_authority_promotions "
                        "WHERE admission_id=:admission_id"
                    ),
                    {"admission_id": admission_id},
                )
            ).scalar_one()
            assert receipt_count == 1

        # Evidence is immutable to the narrowly scoped runtime cutover login.
        # The deployment/migration superuser remains an explicit trusted-root
        # boundary and is intentionally not modeled as constrained by RLS.
        for statement, parameters in (
            (
                "UPDATE operations.semantic_authority_promotions "
                "SET policy_digest=repeat('0',64) "
                "WHERE admission_id=:admission_id",
                {"admission_id": admission_id},
            ),
            (
                "DELETE FROM operations.semantic_authority_promotions "
                "WHERE admission_id=:admission_id",
                {"admission_id": admission_id},
            ),
            (
                "UPDATE operations.reviewed_identity_cutover_admissions "
                "SET policy_digest=repeat('0',64) "
                "WHERE admission_id=:admission_id",
                {"admission_id": admission_id},
            ),
            (
                "UPDATE operations.enforced_legacy_identity_writer_freezes "
                "SET policy_digest=repeat('0',64) "
                "WHERE freeze_id=:freeze_id",
                {"freeze_id": receipt.writer_freeze_id},
            ),
        ):
            async with cutover_engine.connect() as connection:
                with pytest.raises(DBAPIError) as exc_info:
                    await connection.execute(text(statement), parameters)
                assert _sqlstate(exc_info.value) == "42501"
                await connection.rollback()
    finally:
        await cutover_engine.dispose()
        await owner_engine.dispose()
