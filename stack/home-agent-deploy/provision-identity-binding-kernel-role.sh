#!/bin/sh
set -eu
umask 077

read_secret() {
  value="$(tr -d '\r\n' < "$1")"
  [ -n "$value" ] || { echo "empty secret: $1" >&2; exit 78; }
  printf '%s' "$value"
}

[ "$(id -u)" -eq 0 ] || {
  echo "provision-identity-binding-kernel-role.sh must run as root" >&2
  exit 77
}

export PGPASSWORD="$(read_secret "$POSTGRES_OWNER_PASSWORD_FILE")"

psql -v ON_ERROR_STOP=1 <<'SQL'
DO $identity_binding_kernel_executor_preflight$
BEGIN
  IF pg_catalog.current_database() <> 'home_agent'
     OR SESSION_USER <> 'home_agent_owner'
     OR CURRENT_USER <> 'home_agent_owner'
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
        WHERE role_row.rolname = 'home_agent_owner'
          AND role_row.rolsuper
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony requires the database owner'
      USING ERRCODE = '42501';
  END IF;
END
$identity_binding_kernel_executor_preflight$;

-- Quarantine a pre-existing reserved role in committed statements before
-- admission. NOLOGIN closes direct reconnects; removing every membership edge
-- closes new SET ROLE paths before the database-wide session drain below.
SELECT
  'ALTER ROLE home_agent_identity_binding_kernel NOLOGIN PASSWORD NULL'
WHERE EXISTS (
  SELECT 1
    FROM pg_catalog.pg_roles AS role_row
   WHERE role_row.rolname = 'home_agent_identity_binding_kernel'
) \gexec

-- Remove every membership edge touching the reserved kernel role before
-- inspecting sessions. Each generated REVOKE commits under psql autocommit, so
-- no new non-owner session can acquire the kernel after the drain completes.
SELECT pg_catalog.format(
         'REVOKE %I FROM %I CASCADE',
         parent.rolname,
         member.rolname
       )
  FROM pg_catalog.pg_auth_members AS membership
  JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
  JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
 WHERE parent.rolname = 'home_agent_identity_binding_kernel'
    OR member.rolname = 'home_agent_identity_binding_kernel'
\gexec

DO $identity_binding_kernel_session_cleanup$
DECLARE
  remaining_sessions integer;
BEGIN
  -- pg_stat_activity.usename is the login role, not the current role after
  -- SET ROLE. Drain every other backend attached to this database so a stale
  -- current_user=kernel session cannot survive under another login name.
  -- Direct kernel logins in any database are also removed defensively.
  PERFORM pg_catalog.pg_terminate_backend(activity.pid, 5000)
    FROM pg_catalog.pg_stat_activity AS activity
   WHERE activity.pid <> pg_catalog.pg_backend_pid()
     AND activity.backend_type = 'client backend'
     AND (
       activity.datname = pg_catalog.current_database()
       OR activity.usename = 'home_agent_identity_binding_kernel'
     );

  SELECT pg_catalog.count(*)
    INTO STRICT remaining_sessions
    FROM pg_catalog.pg_stat_activity AS activity
   WHERE activity.pid <> pg_catalog.pg_backend_pid()
     AND activity.backend_type = 'client backend'
     AND (
       activity.datname = pg_catalog.current_database()
       OR activity.usename = 'home_agent_identity_binding_kernel'
     );

  IF remaining_sessions <> 0
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members AS membership
         JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
         JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
        WHERE parent.rolname = 'home_agent_identity_binding_kernel'
           OR member.rolname = 'home_agent_identity_binding_kernel'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_authid AS role_row
        WHERE role_row.rolname = 'home_agent_identity_binding_kernel'
          AND (
            role_row.rolcanlogin
            OR role_row.rolpassword IS NOT NULL
          )
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel session cleanup could not be proven'
      USING ERRCODE = '55000';
  END IF;
END
$identity_binding_kernel_session_cleanup$;

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

DO $identity_binding_kernel_role_ceremony_preflight$
DECLARE
  revision_count integer;
  current_revision text;
  binding_kernel_oid oid;
BEGIN
  IF pg_catalog.current_database() <> 'home_agent'
     OR SESSION_USER <> 'home_agent_owner'
     OR CURRENT_USER <> 'home_agent_owner'
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_database AS database_row
         JOIN pg_catalog.pg_roles AS owner_role
           ON owner_role.oid = database_row.datdba
        WHERE database_row.datname = pg_catalog.current_database()
          AND owner_role.rolname = 'home_agent_owner'
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony requires the database owner'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.count(*), pg_catalog.max(version_num)
    INTO STRICT revision_count, current_revision
    FROM public.alembic_version;

  IF revision_count <> 1
     OR current_revision IS DISTINCT FROM
        '0015_current_authority_e5a' THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony requires revision 0015'
      USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS function_row
         JOIN pg_catalog.pg_roles AS owner_role
           ON owner_role.oid = function_row.proowner
        WHERE function_row.oid = pg_catalog.to_regprocedure(
                'operations.'
                'evaluate_current_identity_semantic_authority(uuid)'
              )
          AND function_row.prosecdef
          AND owner_role.rolname =
              'home_agent_identity_authority_kernel'
     )
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
        WHERE role_row.rolname = 'home_agent_binding_operator'
          AND role_row.rolcanlogin
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony prerequisite is unavailable'
      USING ERRCODE = '55000';
  END IF;

  IF pg_catalog.to_regclass(
       'operations.principal_binding_authority_receipts'
     ) IS NOT NULL
     OR pg_catalog.to_regprocedure(
       'identity.commit_authenticated_principal_binding_e5b('
       'uuid,character varying,character varying,uuid,uuid,uuid,uuid,uuid)'
     ) IS NOT NULL
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_attribute AS attribute_row
        WHERE attribute_row.attrelid =
              pg_catalog.to_regclass('identity.ha_user_bindings')
          AND attribute_row.attname = 'authority_receipt_id'
          AND attribute_row.attnum > 0
          AND NOT attribute_row.attisdropped
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_policy AS policy_row
        WHERE policy_row.polname LIKE '%e5b%'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_trigger AS trigger_row
        WHERE NOT trigger_row.tgisinternal
          AND trigger_row.tgname LIKE '%e5b%'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_constraint AS constraint_row
        WHERE constraint_row.conname LIKE '%e5b%'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS relation_row
        WHERE relation_row.relkind = 'i'
          AND relation_row.relname LIKE '%e5b%'
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony found E5b objects before admission'
      USING ERRCODE = '55000';
  END IF;

  SELECT role_row.oid
    INTO binding_kernel_oid
    FROM pg_catalog.pg_roles AS role_row
   WHERE role_row.rolname = 'home_agent_identity_binding_kernel';

  IF binding_kernel_oid IS NOT NULL
     AND EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS dependency_row
        WHERE dependency_row.refobjid = binding_kernel_oid
          AND dependency_row.deptype = 'o'
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role already owns objects'
      USING ERRCODE = '42501';
  END IF;
END
$identity_binding_kernel_role_ceremony_preflight$;

SELECT 'CREATE ROLE home_agent_identity_binding_kernel NOLOGIN'
WHERE NOT EXISTS (
  SELECT 1
    FROM pg_catalog.pg_roles AS role_row
   WHERE role_row.rolname = 'home_agent_identity_binding_kernel'
) \gexec

ALTER ROLE home_agent_identity_binding_kernel RESET ALL;
ALTER ROLE home_agent_identity_binding_kernel PASSWORD NULL;
ALTER ROLE home_agent_identity_binding_kernel
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;

-- Remove direct database grants from every database in the cluster.
SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON DATABASE %I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         database_row.datname
       )
  FROM pg_catalog.pg_database AS database_row
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    database_row.datacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY database_row.datname
\gexec

REVOKE pg_monitor, pg_read_all_settings, pg_read_all_stats,
  pg_stat_scan_tables, pg_read_all_data, pg_write_all_data,
  pg_read_server_files, pg_write_server_files, pg_execute_server_program,
  pg_checkpoint, pg_maintain, pg_signal_backend,
  pg_use_reserved_connections, pg_create_subscription
  FROM home_agent_identity_binding_kernel CASCADE;

-- Remove every direct schema grant in the Home Agent database.
SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON SCHEMA %I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         namespace_row.nspname
       )
  FROM pg_catalog.pg_namespace AS namespace_row
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    namespace_row.nspacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY namespace_row.nspname
\gexec

-- Relation ACLs and column ACLs are independent catalog surfaces.
SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON %s %I.%I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         CASE relation_row.relkind WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
         namespace_row.nspname,
         relation_row.relname
       )
  FROM pg_catalog.pg_class AS relation_row
  JOIN pg_catalog.pg_namespace AS namespace_row
    ON namespace_row.oid = relation_row.relnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    relation_row.relacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE relation_row.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
   AND grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY relation_row.relkind, namespace_row.nspname, relation_row.relname
\gexec

SELECT pg_catalog.format(
         'REVOKE SELECT (%3$I), INSERT (%3$I), UPDATE (%3$I), '
         'REFERENCES (%3$I) ON TABLE %1$I.%2$I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         namespace_row.nspname,
         relation_row.relname,
         attribute_row.attname
       )
  FROM pg_catalog.pg_attribute AS attribute_row
  JOIN pg_catalog.pg_class AS relation_row
    ON relation_row.oid = attribute_row.attrelid
  JOIN pg_catalog.pg_namespace AS namespace_row
    ON namespace_row.oid = relation_row.relnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    attribute_row.attacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE attribute_row.attnum > 0
   AND NOT attribute_row.attisdropped
   AND grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY namespace_row.nspname, relation_row.relname,
          attribute_row.attname
\gexec

-- ROUTINE covers functions, procedures, and aggregates in PostgreSQL 17.
SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON ROUTINE %I.%I(%s) '
         'FROM home_agent_identity_binding_kernel CASCADE',
         namespace_row.nspname,
         function_row.proname,
         pg_catalog.pg_get_function_identity_arguments(function_row.oid)
       )
  FROM pg_catalog.pg_proc AS function_row
  JOIN pg_catalog.pg_namespace AS namespace_row
    ON namespace_row.oid = function_row.pronamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    function_row.proacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY function_row.oid, namespace_row.nspname, function_row.proname
\gexec

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON TYPE %I.%I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         namespace_row.nspname,
         type_row.typname
       )
  FROM pg_catalog.pg_type AS type_row
  JOIN pg_catalog.pg_namespace AS namespace_row
    ON namespace_row.oid = type_row.typnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    type_row.typacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY namespace_row.nspname, type_row.typname
\gexec

-- PostgreSQL parameter ACLs are independent of database and object ACLs.
SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON PARAMETER %I '
         'FROM home_agent_identity_binding_kernel CASCADE',
         parameter_acl.parname
       )
  FROM pg_catalog.pg_parameter_acl AS parameter_acl
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    parameter_acl.paracl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
 GROUP BY parameter_acl.parname
\gexec

-- Default ACLs can silently restore authority on later objects. Remove every
-- default grant to the kernel, including global schema defaults.
SELECT pg_catalog.format(
         'ALTER DEFAULT PRIVILEGES FOR ROLE %I%s '
         'REVOKE ALL PRIVILEGES ON %s '
         'FROM home_agent_identity_binding_kernel CASCADE',
         grantor_role.rolname,
         CASE
           WHEN default_acl.defaclnamespace = 0 THEN ''
           ELSE pg_catalog.format(
             ' IN SCHEMA %I',
             namespace_row.nspname
           )
         END,
         CASE default_acl.defaclobjtype
           WHEN 'r' THEN 'TABLES'
           WHEN 'S' THEN 'SEQUENCES'
           WHEN 'f' THEN 'ROUTINES'
           WHEN 'T' THEN 'TYPES'
           WHEN 'n' THEN 'SCHEMAS'
         END
       )
  FROM pg_catalog.pg_default_acl AS default_acl
  JOIN pg_catalog.pg_roles AS grantor_role
    ON grantor_role.oid = default_acl.defaclrole
  LEFT JOIN pg_catalog.pg_namespace AS namespace_row
    ON namespace_row.oid = default_acl.defaclnamespace
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    default_acl.defaclacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname = 'home_agent_identity_binding_kernel'
   AND default_acl.defaclobjtype IN ('r', 'S', 'f', 'T', 'n')
 GROUP BY grantor_role.rolname, default_acl.defaclnamespace,
          namespace_row.nspname, default_acl.defaclobjtype
\gexec

GRANT home_agent_identity_binding_kernel TO home_agent_owner
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

DO $identity_binding_kernel_role_ceremony_validation$
DECLARE
  owner_oid oid;
  binding_kernel_oid oid;
BEGIN
  SELECT role_row.oid
    INTO STRICT owner_oid
    FROM pg_catalog.pg_roles AS role_row
   WHERE role_row.rolname = 'home_agent_owner';
  SELECT role_row.oid
    INTO STRICT binding_kernel_oid
    FROM pg_catalog.pg_roles AS role_row
   WHERE role_row.rolname = 'home_agent_identity_binding_kernel';

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_authid AS role_row
        WHERE role_row.oid = binding_kernel_oid
          AND NOT role_row.rolcanlogin
          AND NOT role_row.rolinherit
          AND NOT role_row.rolsuper
          AND NOT role_row.rolcreatedb
          AND NOT role_row.rolcreaterole
          AND NOT role_row.rolreplication
          AND NOT role_row.rolbypassrls
          AND role_row.rolconnlimit = 0
          AND role_row.rolpassword IS NULL
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = binding_kernel_oid
          AND membership.member = owner_oid
          AND NOT membership.admin_option
          AND NOT membership.inherit_option
          AND membership.set_option
     ) <> 1
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.roleid = binding_kernel_oid
     ) <> 1
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members AS membership
        WHERE membership.member = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_db_role_setting AS setting_row
        WHERE setting_row.setrole = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS dependency_row
        WHERE dependency_row.refobjid = binding_kernel_oid
          AND dependency_row.deptype IN ('a', 'o')
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_database AS database_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           database_row.datacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_namespace AS namespace_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           namespace_row.nspacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS relation_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           relation_row.relacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_attribute AS attribute_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           attribute_row.attacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           function_row.proacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_type AS type_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           type_row.typacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_parameter_acl AS parameter_acl
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           parameter_acl.paracl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_default_acl AS default_acl
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           default_acl.defaclacl
         ) AS privilege_row
        WHERE privilege_row.grantee = binding_kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.usename = 'home_agent_identity_binding_kernel'
     ) THEN
    RAISE EXCEPTION
      'identity binding kernel role ceremony validation failed'
      USING ERRCODE = '42501';
  END IF;
END
$identity_binding_kernel_role_ceremony_validation$;

COMMIT;
SQL

unset PGPASSWORD
echo "provisioned dormant identity binding kernel role at revision 0015"
