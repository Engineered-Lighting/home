"""Mark the authenticated split-credential binding adapter boundary.

Revision ID: 0017_authenticated_binding_e5c
Revises: 0016_principal_binding_e5b
Create Date: 2026-07-27

This revision adds no semantic data and grants no runtime capability. It
quarantines the E5b function and records the exact database revision at which
the separately pinned grant replay may activate the authenticated E5c adapter.
Production remains pinned to 0006a until the hosted gate is accepted.
"""

from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0017_authenticated_binding_e5c"
down_revision: str | None = "0016_principal_binding_e5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CALLER_ROLE = "home_agent_binding_committer"
KERNEL_ROLE = "home_agent_identity_binding_kernel"
FUNCTION = (
    "identity.commit_authenticated_principal_binding_e5b("
    "uuid,character varying,character varying,uuid,uuid,uuid,uuid,uuid)"
)


def _quarantine(*, expected_revision: str) -> None:
    op.execute(
        f"""
        DO $e5c_preconditions$
        DECLARE
          caller_oid oid;
          function_oid oid;
          kernel_oid oid;
          owner_oid oid;
        BEGIN
          IF session_user <> 'home_agent_owner'
             OR current_user <> 'home_agent_owner' THEN
            RAISE EXCEPTION 'authenticated_binding_e5c_migration_role_invalid'
              USING ERRCODE = '42501';
          END IF;
          IF (
               SELECT pg_catalog.count(*)
                 FROM public.alembic_version
             ) <> 1
             OR (
               SELECT version_num FROM public.alembic_version
             ) IS DISTINCT FROM '{expected_revision}' THEN
            RAISE EXCEPTION 'authenticated_binding_e5c_revision_invalid'
              USING ERRCODE = '55000';
          END IF;

          SELECT oid INTO STRICT owner_oid
            FROM pg_catalog.pg_roles WHERE rolname = 'home_agent_owner';
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT kernel_oid
            FROM pg_catalog.pg_roles WHERE rolname = '{KERNEL_ROLE}';
          SELECT oid INTO STRICT function_oid
            FROM pg_catalog.pg_proc WHERE oid = '{FUNCTION}'::regprocedure;

          IF pg_catalog.pg_has_role(
               '{CALLER_ROLE}', '{KERNEL_ROLE}', 'SET'
             )
             OR NOT pg_catalog.pg_has_role(
               'home_agent_owner', '{KERNEL_ROLE}', 'SET'
             )
             OR (
               SELECT proowner FROM pg_catalog.pg_proc
                WHERE oid = function_oid
             ) <> kernel_oid
             OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_shdepend
                WHERE refobjid = caller_oid AND deptype = 'o'
             )
             OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_auth_members
                WHERE roleid = caller_oid OR member = caller_oid
             ) THEN
            RAISE EXCEPTION 'authenticated_binding_e5c_authority_invalid'
              USING ERRCODE = '42501';
          END IF;
        END
        $e5c_preconditions$;

        SET LOCAL ROLE {KERNEL_ROLE};
        REVOKE ALL ON FUNCTION {FUNCTION} FROM {CALLER_ROLE} CASCADE;
        RESET ROLE;
        REVOKE USAGE, CREATE ON SCHEMA identity
          FROM {CALLER_ROLE} CASCADE;

        DO $e5c_quarantine$
        DECLARE
          caller_oid oid;
          function_oid oid;
          identity_schema_oid oid;
        BEGIN
          SELECT oid INTO STRICT caller_oid
            FROM pg_catalog.pg_roles WHERE rolname = '{CALLER_ROLE}';
          SELECT oid INTO STRICT identity_schema_oid
            FROM pg_catalog.pg_namespace WHERE nspname = 'identity';
          SELECT function_row.oid INTO STRICT function_oid
            FROM pg_catalog.pg_proc AS function_row
            JOIN pg_catalog.pg_namespace AS function_namespace
              ON function_namespace.oid = function_row.pronamespace
           WHERE function_namespace.nspname = 'identity'
             AND function_row.proname =
                   'commit_authenticated_principal_binding_e5b'
             AND function_row.proargtypes = ARRAY[
                   'uuid'::regtype::oid,
                   'varchar'::regtype::oid,
                   'varchar'::regtype::oid,
                   'uuid'::regtype::oid,
                   'uuid'::regtype::oid,
                   'uuid'::regtype::oid,
                   'uuid'::regtype::oid,
                   'uuid'::regtype::oid
                 ]::oidvector;

          IF EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   function_row.proacl
                 ) AS function_acl
                WHERE function_row.oid = function_oid
                  AND function_acl.grantee IN (0, caller_oid)
                  AND function_acl.privilege_type = 'EXECUTE'
             )
             OR EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_namespace AS namespace_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                   namespace_row.nspacl
                 ) AS namespace_acl
                WHERE namespace_row.oid = identity_schema_oid
                  AND namespace_acl.grantee IN (0, caller_oid)
                  AND namespace_acl.privilege_type IN ('USAGE', 'CREATE')
             ) THEN
            RAISE EXCEPTION 'authenticated_binding_e5c_quarantine_failed'
              USING ERRCODE = '42501';
          END IF;
        END
        $e5c_quarantine$;
        """
    )


def upgrade() -> None:
    _quarantine(expected_revision="0016_principal_binding_e5b")


def downgrade() -> None:
    _quarantine(expected_revision="0017_authenticated_binding_e5c")
