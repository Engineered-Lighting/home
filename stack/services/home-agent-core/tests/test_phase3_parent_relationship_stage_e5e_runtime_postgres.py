from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


OWNER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5E_OWNER_DATABASE_URL"
)
COMMITTER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5E_COMMITTER_DATABASE_URL"
)
OPERATOR_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5E_OPERATOR_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
FUNCTION = (
    "identity.stage_authenticated_parent_relationship_e5e("
    "character varying,uuid,uuid,uuid,uuid,uuid,"
    "character varying,character varying)"
)
TABLES = (
    ("identity", "parent_relationship_requests"),
    ("identity", "parent_relationship_proposals"),
    ("identity", "parent_relationship_proposal_edges"),
)


def _runtime_configured() -> bool:
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


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5e_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    assert _runtime_configured()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5e PostgreSQL URLs are not configured",
)
async def test_e5e_catalog_is_split_credential_and_staging_only() -> None:
    engine = _engine(OWNER_DATABASE_ENV)
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one()
            assert revision == "0019_parent_stage_e5e"

            role = (
                await connection.execute(
                    text(
                        "SELECT rolcanlogin, rolinherit, rolsuper, "
                        "rolcreatedb, rolcreaterole, rolreplication, "
                        "rolbypassrls, rolconnlimit, rolpassword IS NULL "
                        "FROM pg_catalog.pg_authid "
                        "WHERE rolname="
                        "'home_agent_parent_relationship_kernel'"
                    )
                )
            ).one()
            assert role == (
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                0,
                True,
            )

            function = (
                await connection.execute(
                    text(
                        "SELECT owner_role.rolname, function_row.prosecdef, "
                        "function_row.provolatile, function_row.proparallel, "
                        "function_row.proconfig "
                        "FROM pg_catalog.pg_proc AS function_row "
                        "JOIN pg_catalog.pg_roles AS owner_role "
                        "ON owner_role.oid=function_row.proowner "
                        "WHERE function_row.oid="
                        "pg_catalog.to_regprocedure(:function)"
                    ),
                    {"function": FUNCTION},
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
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_binding_operator',"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.has_function_privilege("
                        "'home_agent_api',"
                        "pg_catalog.to_regprocedure(:function),'EXECUTE'),"
                        "pg_catalog.has_schema_privilege("
                        "'home_agent_binding_committer','identity','USAGE'),"
                        "pg_catalog.has_schema_privilege("
                        "'home_agent_binding_committer','identity','CREATE')"
                    ),
                    {"function": FUNCTION},
                )
            ).one()
            assert privileges == (True, False, False, True, False)

            assert (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.has_function_privilege("
                        "'home_agent_binding_committer',"
                        "'identity.commit_authenticated_principal_binding_e5b("
                        "uuid,character varying,character varying,uuid,"
                        "uuid,uuid,uuid,uuid)','EXECUTE')"
                    )
                )
            ).scalar_one() is False

            for table_schema, table_name in TABLES:
                relation = (
                    await connection.execute(
                        text(
                            "SELECT relation.relrowsecurity, "
                            "relation.relforcerowsecurity, "
                            "(SELECT count(*) "
                            "FROM pg_catalog.pg_policy AS policy "
                            "WHERE policy.polrelid=relation.oid) "
                            "FROM pg_catalog.pg_class AS relation "
                            "JOIN pg_catalog.pg_namespace AS namespace "
                            "ON namespace.oid=relation.relnamespace "
                            "WHERE namespace.nspname=:table_schema "
                            "AND relation.relname=:table_name"
                        ),
                        {
                            "table_schema": table_schema,
                            "table_name": table_name,
                        },
                    )
                ).one()
                assert relation == (True, True, 6)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5e PostgreSQL URLs are not configured",
)
async def test_e5e_caller_is_table_blind_and_first_statement_only() -> None:
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
                            "SELECT count(*) FROM "
                            "identity.parent_relationship_requests"
                        )
                    )
            assert _sqlstate(denied.value) == "42501"
        finally:
            await engine.dispose()

    read_committed = _engine(COMMITTER_DATABASE_ENV)
    try:
        with pytest.raises(DBAPIError) as wrong_isolation:
            async with read_committed.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT * FROM identity."
                        "stage_authenticated_parent_relationship_e5e("
                        "CAST('synthetic-ha-user' AS varchar),"
                        ":a,:b,:c,:d,:e,"
                        "CAST('ABCDEFGHJKLMNPQR' AS varchar),"
                        "CAST('STUVWXYZ23456789' AS varchar))"
                    ),
                    {
                        key: uuid.uuid4()
                        for key in ("a", "b", "c", "d", "e")
                    },
                )
        assert _sqlstate(wrong_isolation.value) == "25000"
        assert "parent_relationship_e5e_transaction_invalid" in str(
            wrong_isolation.value.orig
        )
    finally:
        await read_committed.dispose()

    serializable = _engine(COMMITTER_DATABASE_ENV, serializable=True)
    try:
        with pytest.raises(DBAPIError) as invalid_input:
            async with serializable.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT * FROM identity."
                        "stage_authenticated_parent_relationship_e5e("
                        "CAST('synthetic-ha-user' AS varchar),"
                        ":a,:b,:c,:d,:e,"
                        "CAST('ABCDEFGHJKLMNPQR' AS varchar),"
                        "CAST('STUVWXYZ23456789' AS varchar))"
                    ),
                    {
                        key: uuid.uuid4()
                        for key in ("a", "b", "c", "d", "e")
                    },
                )
        assert _sqlstate(invalid_input.value) == "22023"
        assert "parent_relationship_e5e_input_invalid" in str(
            invalid_input.value.orig
        )
    finally:
        await serializable.dispose()
