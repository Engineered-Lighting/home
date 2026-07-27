from __future__ import annotations

import os

import psycopg
import pytest


OWNER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_OWNER_DATABASE_URL"
CUTOVER_DATABASE_ENV = "TEST_PHASE3_IDENTITY_CUTOVER_E4_DATABASE_URL"
LOGIN_ROLE = "home_agent_identity_cutover"
KERNEL_ROLE = "home_agent_identity_cutover_kernel"


pytestmark = pytest.mark.skipif(
    not os.getenv(OWNER_DATABASE_ENV),
    reason="requires the isolated hosted PostgreSQL E4 scaffold gate",
)


def _fetchone(query: str) -> tuple[object, ...]:
    with psycopg.connect(os.environ[OWNER_DATABASE_ENV]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            result = cursor.fetchone()
            assert result is not None
            return result


def test_e4_scaffold_is_zero_object_and_zero_authority_at_revision_0013() -> None:
    (
        revision,
        admission_table,
        cutover_function,
        login_attributes,
        kernel_attributes,
        login_config,
        kernel_config,
        membership_count,
        ownership_count,
        application_acl_count,
        default_acl_count,
    ) = _fetchone(
        f"""
        WITH target_roles AS (
          SELECT oid, rolname
            FROM pg_catalog.pg_roles
           WHERE rolname IN ('{LOGIN_ROLE}', '{KERNEL_ROLE}')
        ),
        application_acl AS (
          SELECT schema_acl.grantee
            FROM pg_catalog.pg_namespace AS namespace_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              coalesce(
                namespace_row.nspacl,
                pg_catalog.acldefault('n', namespace_row.nspowner)
              )
            ) AS schema_acl
           WHERE namespace_row.nspname IN (
             'public','ingest','identity','knowledge','engagement',
             'privacy','operations','media'
           )
          UNION ALL
          SELECT relation_acl.grantee
            FROM pg_catalog.pg_class AS relation_row
            JOIN pg_catalog.pg_namespace AS namespace_row
              ON namespace_row.oid = relation_row.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              coalesce(
                relation_row.relacl,
                pg_catalog.acldefault(
                  CASE
                    WHEN relation_row.relkind = 'S' THEN 's'::"char"
                    ELSE 'r'::"char"
                  END,
                  relation_row.relowner
                )
              )
            ) AS relation_acl
           WHERE namespace_row.nspname IN (
             'public','ingest','identity','knowledge','engagement',
             'privacy','operations','media'
           )
          UNION ALL
          SELECT column_acl.grantee
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation_row
              ON relation_row.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace_row
              ON namespace_row.oid = relation_row.relnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl)
              AS column_acl
           WHERE namespace_row.nspname IN (
             'public','ingest','identity','knowledge','engagement',
             'privacy','operations','media'
           )
             AND attribute.attnum > 0
             AND NOT attribute.attisdropped
          UNION ALL
          SELECT function_acl.grantee
            FROM pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS namespace_row
              ON namespace_row.oid = function_row.pronamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              coalesce(
                function_row.proacl,
                pg_catalog.acldefault('f', function_row.proowner)
              )
            ) AS function_acl
           WHERE namespace_row.nspname IN (
             'public','ingest','identity','knowledge','engagement',
             'privacy','operations','media'
           )
          UNION ALL
          SELECT type_acl.grantee
            FROM pg_catalog.pg_type AS type_row
            JOIN pg_catalog.pg_namespace AS namespace_row
              ON namespace_row.oid = type_row.typnamespace
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              coalesce(
                type_row.typacl,
                pg_catalog.acldefault('T', type_row.typowner)
              )
            ) AS type_acl
           WHERE namespace_row.nspname IN (
             'public','ingest','identity','knowledge','engagement',
             'privacy','operations','media'
           )
        )
        SELECT
          (SELECT version_num FROM public.alembic_version),
          pg_catalog.to_regclass(
            'operations.reviewed_identity_cutover_admissions'
          )::text,
          pg_catalog.to_regprocedure(
            'operations.commit_reviewed_identity_cutover(bytea,uuid)'
          )::text,
          (
            SELECT pg_catalog.jsonb_build_array(
              rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
              rolreplication, rolbypassrls, rolconnlimit,
              rolvaliduntil = timestamptz '1970-01-01 00:00:00+00'
            )
              FROM pg_catalog.pg_roles WHERE rolname = '{LOGIN_ROLE}'
          ),
          (
            SELECT pg_catalog.jsonb_build_array(
              rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
              rolreplication, rolbypassrls, rolconnlimit,
              rolvaliduntil IS NULL
            )
              FROM pg_catalog.pg_roles WHERE rolname = '{KERNEL_ROLE}'
          ),
          (SELECT rolconfig FROM pg_catalog.pg_roles
            WHERE rolname = '{LOGIN_ROLE}'),
          (SELECT rolconfig FROM pg_catalog.pg_roles
            WHERE rolname = '{KERNEL_ROLE}'),
          (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_auth_members AS membership
             WHERE membership.member IN (SELECT oid FROM target_roles)
                OR membership.roleid IN (SELECT oid FROM target_roles)
          ),
          (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_shdepend AS dependency
             WHERE dependency.refobjid IN (SELECT oid FROM target_roles)
               AND dependency.deptype = 'o'
          ),
          (
            SELECT pg_catalog.count(*)
              FROM application_acl
             WHERE grantee IN (SELECT oid FROM target_roles)
          ),
          (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_default_acl AS default_acl
              CROSS JOIN LATERAL pg_catalog.aclexplode(default_acl.defaclacl)
                AS acl
             WHERE acl.grantee IN (SELECT oid FROM target_roles)
          )
        """
    )

    assert revision == "0013_identity_finalizer_e3"
    assert admission_table is None
    assert cutover_function is None
    assert login_attributes == [
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        1,
        True,
    ]
    assert kernel_attributes == [
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        0,
        True,
    ]
    assert login_config == [
        "default_transaction_isolation=serializable",
        "statement_timeout=120s",
        "lock_timeout=5s",
        "idle_in_transaction_session_timeout=15s",
        "transaction_timeout=180s",
        "log_parameter_max_length_on_error=0",
    ]
    assert kernel_config is None
    # The sole membership is the owner's SET-only kernel relationship.
    assert membership_count == 1
    assert ownership_count == 0
    assert application_acl_count == 0
    assert default_acl_count == 0


def test_e4_login_cannot_authenticate_while_expired() -> None:
    database_url = os.getenv(CUTOVER_DATABASE_ENV)
    assert database_url
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(database_url, connect_timeout=5)
