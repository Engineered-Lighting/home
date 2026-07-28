#!/bin/sh
set -eu
umask 077

read_secret() {
  value="$(tr -d '\r\n' < "$1")"
  [ -n "$value" ] || { echo "empty secret: $1" >&2; exit 78; }
  printf '%s' "$value"
}

[ "$(id -u)" -eq 0 ] || {
  echo "provision-parent-relationship-kernel-role.sh must run as root" >&2
  exit 77
}

export PGPASSWORD="$(read_secret "$POSTGRES_OWNER_PASSWORD_FILE")"

psql -v ON_ERROR_STOP=1 <<'SQL'
DO $parent_kernel_preflight$
DECLARE
  revision_count integer;
  current_revision text;
BEGIN
  IF pg_catalog.current_database() <> 'home_agent'
     OR SESSION_USER <> 'home_agent_owner'
     OR CURRENT_USER <> 'home_agent_owner'
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles
        WHERE rolname = 'home_agent_owner'
          AND rolsuper
     ) THEN
    RAISE EXCEPTION
      'parent relationship kernel role ceremony requires the database owner'
      USING ERRCODE = '42501';
  END IF;

  SELECT pg_catalog.count(*), pg_catalog.max(version_num)
    INTO STRICT revision_count, current_revision
    FROM public.alembic_version;
  IF revision_count <> 1
     OR current_revision IS DISTINCT FROM
        '0018_parent_relationship_e5d' THEN
    RAISE EXCEPTION
      'parent relationship kernel role ceremony requires revision 0018'
      USING ERRCODE = '55000';
  END IF;

  IF pg_catalog.to_regprocedure(
       'identity.stage_authenticated_parent_relationship_e5e('
       'character varying,uuid,uuid,uuid,uuid,uuid,'
       'character varying,character varying)'
     ) IS NOT NULL THEN
    RAISE EXCEPTION
      'parent relationship kernel function exists before role admission'
      USING ERRCODE = '55000';
  END IF;
END
$parent_kernel_preflight$;

SELECT
  'ALTER ROLE home_agent_parent_relationship_kernel '
  'NOLOGIN PASSWORD NULL'
WHERE EXISTS (
  SELECT 1
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_parent_relationship_kernel'
) \gexec

SELECT pg_catalog.format(
         'REVOKE %I FROM %I CASCADE',
         parent.rolname,
         member.rolname
       )
  FROM pg_catalog.pg_auth_members AS membership
  JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
  JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
 WHERE parent.rolname = 'home_agent_parent_relationship_kernel'
    OR member.rolname = 'home_agent_parent_relationship_kernel'
\gexec

DO $parent_kernel_session_cleanup$
BEGIN
  PERFORM pg_catalog.pg_terminate_backend(activity.pid, 5000)
    FROM pg_catalog.pg_stat_activity AS activity
   WHERE activity.pid <> pg_catalog.pg_backend_pid()
     AND activity.backend_type = 'client backend'
     AND (
       activity.datname = pg_catalog.current_database()
       OR activity.usename = 'home_agent_parent_relationship_kernel'
     );

  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.pid <> pg_catalog.pg_backend_pid()
          AND activity.backend_type = 'client backend'
          AND (
            activity.datname = pg_catalog.current_database()
            OR activity.usename =
               'home_agent_parent_relationship_kernel'
          )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members AS membership
         JOIN pg_catalog.pg_roles AS parent
           ON parent.oid = membership.roleid
         JOIN pg_catalog.pg_roles AS member
           ON member.oid = membership.member
        WHERE parent.rolname =
              'home_agent_parent_relationship_kernel'
           OR member.rolname =
              'home_agent_parent_relationship_kernel'
     ) THEN
    RAISE EXCEPTION
      'parent relationship kernel session cleanup could not be proven'
      USING ERRCODE = '55000';
  END IF;
END
$parent_kernel_session_cleanup$;

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

SELECT 'CREATE ROLE home_agent_parent_relationship_kernel NOLOGIN'
WHERE NOT EXISTS (
  SELECT 1
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_parent_relationship_kernel'
) \gexec

ALTER ROLE home_agent_parent_relationship_kernel RESET ALL;
ALTER ROLE home_agent_parent_relationship_kernel PASSWORD NULL;
ALTER ROLE home_agent_parent_relationship_kernel
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON DATABASE %I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
         database_row.datname
       )
  FROM pg_catalog.pg_database AS database_row
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    database_row.datacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY database_row.datname
\gexec

REVOKE pg_monitor, pg_read_all_settings, pg_read_all_stats,
  pg_stat_scan_tables, pg_read_all_data, pg_write_all_data,
  pg_read_server_files, pg_write_server_files, pg_execute_server_program,
  pg_checkpoint, pg_maintain, pg_signal_backend,
  pg_use_reserved_connections, pg_create_subscription
  FROM home_agent_parent_relationship_kernel CASCADE;

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON SCHEMA %I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
         namespace_row.nspname
       )
  FROM pg_catalog.pg_namespace AS namespace_row
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    namespace_row.nspacl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY namespace_row.nspname
\gexec

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON %s %I.%I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
         CASE relation_row.relkind
           WHEN 'S' THEN 'SEQUENCE'
           ELSE 'TABLE'
         END,
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
   AND grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY relation_row.relkind, namespace_row.nspname,
          relation_row.relname
\gexec

SELECT pg_catalog.format(
         'REVOKE SELECT (%3$I), INSERT (%3$I), UPDATE (%3$I), '
         'REFERENCES (%3$I) ON TABLE %1$I.%2$I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
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
   AND grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY namespace_row.nspname, relation_row.relname,
          attribute_row.attname
\gexec

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON ROUTINE %I.%I(%s) '
         'FROM home_agent_parent_relationship_kernel CASCADE',
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
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY function_row.oid, namespace_row.nspname,
          function_row.proname
\gexec

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON TYPE %I.%I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
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
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY namespace_row.nspname, type_row.typname
\gexec

SELECT pg_catalog.format(
         'REVOKE ALL PRIVILEGES ON PARAMETER %I '
         'FROM home_agent_parent_relationship_kernel CASCADE',
         parameter_acl.parname
       )
  FROM pg_catalog.pg_parameter_acl AS parameter_acl
  CROSS JOIN LATERAL pg_catalog.aclexplode(
    parameter_acl.paracl
  ) AS privilege_row
  JOIN pg_catalog.pg_roles AS grantee_role
    ON grantee_role.oid = privilege_row.grantee
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
 GROUP BY parameter_acl.parname
\gexec

SELECT pg_catalog.format(
         'ALTER DEFAULT PRIVILEGES FOR ROLE %I%s '
         'REVOKE ALL PRIVILEGES ON %s '
         'FROM home_agent_parent_relationship_kernel CASCADE',
         grantor_role.rolname,
         CASE
           WHEN default_acl.defaclnamespace = 0 THEN ''
           ELSE pg_catalog.format(
             ' IN SCHEMA %I', namespace_row.nspname
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
 WHERE grantee_role.rolname =
       'home_agent_parent_relationship_kernel'
   AND default_acl.defaclobjtype IN ('r', 'S', 'f', 'T', 'n')
 GROUP BY grantor_role.rolname, default_acl.defaclnamespace,
          namespace_row.nspname, default_acl.defaclobjtype
\gexec

GRANT home_agent_parent_relationship_kernel TO home_agent_owner
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

DO $parent_kernel_validation$
DECLARE
  owner_oid oid;
  kernel_oid oid;
BEGIN
  SELECT oid INTO STRICT owner_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_owner';
  SELECT oid INTO STRICT kernel_oid
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_parent_relationship_kernel';

  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_authid
        WHERE oid = kernel_oid
          AND NOT rolcanlogin
          AND NOT rolinherit
          AND NOT rolsuper
          AND NOT rolcreatedb
          AND NOT rolcreaterole
          AND NOT rolreplication
          AND NOT rolbypassrls
          AND rolconnlimit = 0
          AND rolpassword IS NULL
     )
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members
        WHERE roleid = kernel_oid
          AND member = owner_oid
          AND NOT admin_option
          AND NOT inherit_option
          AND set_option
     ) <> 1
     OR (
       SELECT pg_catalog.count(*)
         FROM pg_catalog.pg_auth_members
        WHERE roleid = kernel_oid
     ) <> 1
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_auth_members
        WHERE member = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_db_role_setting
        WHERE setrole = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend
        WHERE refobjid = kernel_oid
          AND deptype IN ('a', 'o')
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_database AS database_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           database_row.datacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_namespace AS namespace_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           namespace_row.nspacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_class AS relation_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           relation_row.relacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_attribute AS attribute_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           attribute_row.attacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           function_row.proacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_type AS type_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           type_row.typacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_parameter_acl AS parameter_acl
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           parameter_acl.paracl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_default_acl AS default_acl
         CROSS JOIN LATERAL pg_catalog.aclexplode(
           default_acl.defaclacl
         ) AS privilege_row
        WHERE privilege_row.grantee = kernel_oid
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_stat_activity
        WHERE usename = 'home_agent_parent_relationship_kernel'
     ) THEN
    RAISE EXCEPTION
      'parent relationship kernel role ceremony validation failed'
      USING ERRCODE = '42501';
  END IF;
END
$parent_kernel_validation$;

COMMIT;
SQL

unset PGPASSWORD
echo "provisioned dormant parent relationship kernel role at revision 0018"
