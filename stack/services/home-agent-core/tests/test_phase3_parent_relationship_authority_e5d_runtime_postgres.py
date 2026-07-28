from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


OWNER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5D_OWNER_DATABASE_URL"
)
COMMITTER_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5D_COMMITTER_DATABASE_URL"
)
OPERATOR_DATABASE_ENV = (
    "TEST_PHASE3_PARENT_RELATIONSHIP_E5D_OPERATOR_DATABASE_URL"
)
HOSTED_GATE_SENTINEL_ENV = "TEST_PHASE3_IDENTITY_ERASURE_E1_RUN_SENTINEL"
TABLES = (
    ("identity", "parent_relationship_requests"),
    ("identity", "parent_relationship_proposals"),
    ("identity", "parent_relationship_proposal_edges"),
    ("operations", "parent_relationship_authority_receipts"),
    ("operations", "parent_relationship_authority_receipt_edges"),
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


def _engine(environment_name: str) -> AsyncEngine:
    url = make_url(os.environ[environment_name]).set(
        drivername="postgresql+psycopg"
    )
    return create_async_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
    )


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


@pytest.mark.skipif(
    not os.getenv(HOSTED_GATE_SENTINEL_ENV),
    reason="not running inside the isolated hosted PostgreSQL gate",
)
def test_e5d_hosted_gate_cannot_silently_skip_runtime_contract() -> None:
    assert _runtime_configured()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5d owner/committer/operator URLs are not configured",
)
async def test_e5d_foundation_is_empty_forced_rls_and_owner_only() -> None:
    engine = _engine(OWNER_DATABASE_ENV)
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM public.alembic_version")
                )
            ).scalar_one()
            assert revision == "0018_parent_relationship_e5d"

            for table_schema, table_name in TABLES:
                catalog = (
                    await connection.execute(
                        text(
                            "SELECT relation.relrowsecurity, "
                            "relation.relforcerowsecurity, "
                            "owner_role.rolname, "
                            "(SELECT count(*) FROM pg_catalog.pg_policy policy "
                            "WHERE policy.polrelid=relation.oid), "
                            "(SELECT count(*) "
                            "FROM pg_catalog.aclexplode(relation.relacl) acl "
                            "WHERE acl.grantee NOT IN (owner_role.oid)), "
                            "(SELECT count(DISTINCT acl.privilege_type) "
                            "FROM pg_catalog.aclexplode(relation.relacl) acl "
                            "WHERE acl.grantee=owner_role.oid "
                            "AND NOT acl.is_grantable "
                            "AND acl.privilege_type IN "
                            "('SELECT','INSERT','UPDATE','DELETE')) "
                            "FROM pg_catalog.pg_class relation "
                            "JOIN pg_catalog.pg_namespace namespace "
                            "ON namespace.oid=relation.relnamespace "
                            "JOIN pg_catalog.pg_roles owner_role "
                            "ON owner_role.oid=relation.relowner "
                            "WHERE namespace.nspname=:table_schema "
                            "AND relation.relname=:table_name"
                        ),
                        {
                            "table_schema": table_schema,
                            "table_name": table_name,
                        },
                    )
                ).one()
                assert catalog == (
                    True,
                    True,
                    "home_agent_owner",
                    4,
                    0,
                    4,
                )
                count = (
                    await connection.execute(
                        text(
                            f'SELECT count(*) FROM "{table_schema}".'
                            f'"{table_name}"'
                        )
                    )
                ).scalar_one()
                assert count == 0

            callable_count = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_catalog.pg_proc function_row "
                        "JOIN pg_catalog.pg_namespace namespace "
                        "ON namespace.oid=function_row.pronamespace "
                        "WHERE namespace.nspname='identity' "
                        "AND function_row.proname LIKE "
                        "'%parent%relationship%'"
                    )
                )
            ).scalar_one()
            assert callable_count == 0
            assert (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.has_function_privilege("
                        "'home_agent_binding_committer',"
                        "'identity."
                        "commit_authenticated_principal_binding_e5b("
                        "uuid,character varying,character varying,uuid,"
                        "uuid,uuid,uuid,uuid)',"
                        "'EXECUTE')"
                    )
                )
            ).scalar_one() is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _runtime_configured(),
    reason="E5d owner/committer/operator URLs are not configured",
)
async def test_e5d_runtime_roles_cannot_read_or_write_foundation_tables() -> None:
    for environment_name in (
        COMMITTER_DATABASE_ENV,
        OPERATOR_DATABASE_ENV,
    ):
        engine = _engine(environment_name)
        try:
            for statement in (
                "SELECT count(*) FROM identity.parent_relationship_requests",
                "DELETE FROM identity.parent_relationship_requests",
                "SELECT count(*) FROM "
                "operations.parent_relationship_authority_receipts",
                "DELETE FROM "
                "operations.parent_relationship_authority_receipts",
            ):
                with pytest.raises(DBAPIError) as denied:
                    async with engine.begin() as connection:
                        await connection.execute(text(statement))
                assert _sqlstate(denied.value) == "42501"
        finally:
            await engine.dispose()
