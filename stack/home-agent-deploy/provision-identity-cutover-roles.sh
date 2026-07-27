#!/bin/sh
set -eu
umask 077

read_secret() {
  value="$(tr -d '\r\n' < "$1")"
  [ -n "$value" ] || { echo "empty secret: $1" >&2; exit 78; }
  printf '%s' "$value"
}

[ "$(id -u)" -eq 0 ] || {
  echo "provision-identity-cutover-roles.sh must run as root" >&2
  exit 77
}

export PGPASSWORD="$(read_secret "$POSTGRES_OWNER_PASSWORD_FILE")"
cutover_password="$(
  read_secret /run/secrets/postgres_identity_cutover_password
)"

psql -v ON_ERROR_STOP=1 \
  -v identity_cutover_password="$cutover_password" <<'SQL'
-- Re-expire the disposable login in its own committed statement before any
-- catalog preflight can fail. Expiry prevents a replacement session from
-- racing the bounded termination and zero-session proof below.
SELECT pg_catalog.format(
         'ALTER ROLE %I VALID UNTIL %L',
         role_row.rolname,
         timestamptz '1970-01-01 00:00:00+00'
       )
  FROM pg_catalog.pg_roles AS role_row
 WHERE role_row.rolname = 'home_agent_identity_cutover' \gexec

DO $identity_cutover_role_session_cleanup$
DECLARE
  remaining_sessions integer;
BEGIN
  IF NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
        WHERE role_row.rolname = 'home_agent_identity_cutover'
     ) THEN
    RETURN;
  END IF;

  PERFORM pg_catalog.pg_terminate_backend(activity.pid, 5000)
    FROM pg_catalog.pg_stat_activity AS activity
   WHERE activity.usename = 'home_agent_identity_cutover'
     AND activity.pid <> pg_catalog.pg_backend_pid();

  SELECT pg_catalog.count(*)
    INTO STRICT remaining_sessions
    FROM pg_catalog.pg_stat_activity AS activity
   WHERE activity.usename = 'home_agent_identity_cutover';

  IF remaining_sessions <> 0
     OR NOT EXISTS (
       SELECT 1
         FROM pg_catalog.pg_roles AS role_row
        WHERE role_row.rolname = 'home_agent_identity_cutover'
          AND role_row.rolcanlogin
          AND role_row.rolvaliduntil <=
              timestamptz '1970-01-01 00:00:00+00'
     ) THEN
    RAISE EXCEPTION
      'identity cutover role session cleanup could not be proven'
      USING ERRCODE = '55000';
  END IF;
END
$identity_cutover_role_session_cleanup$;

DO $identity_cutover_role_ceremony_preflight$
DECLARE
  cutover_role_count integer;
  authority_kernel_count integer;
BEGIN
  IF (
       SELECT version_num FROM public.alembic_version
     ) IS DISTINCT FROM '0013_identity_finalizer_e3' THEN
    RAISE EXCEPTION
      'identity cutover role ceremony requires revision 0013'
      USING ERRCODE = '55000';
  END IF;
  IF pg_catalog.to_regclass(
       'operations.enforced_legacy_identity_writer_freezes'
     ) IS NOT NULL
     OR pg_catalog.to_regclass(
       'operations.reviewed_identity_cutover_admissions'
     ) IS NOT NULL
     OR pg_catalog.to_regclass(
       'operations.semantic_authority_promotions'
     ) IS NOT NULL
     OR pg_catalog.to_regprocedure(
       'operations.commit_reviewed_identity_cutover(bytea,uuid)'
     ) IS NOT NULL THEN
    RAISE EXCEPTION
      'identity cutover role ceremony found E4 objects before role admission'
      USING ERRCODE = '55000';
  END IF;
  SELECT pg_catalog.count(*) INTO STRICT cutover_role_count
    FROM pg_catalog.pg_roles
   WHERE rolname IN (
     'home_agent_identity_cutover',
     'home_agent_identity_cutover_kernel'
   );
  SELECT pg_catalog.count(*) INTO STRICT authority_kernel_count
    FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_authority_kernel';
  IF cutover_role_count NOT IN (0, 2)
     OR authority_kernel_count NOT IN (0, 1) THEN
    RAISE EXCEPTION 'partial identity cutover role pair'
      USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'home_agent_binding_operator'
     ) THEN
    RAISE EXCEPTION 'identity authority caller role is missing'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
       SELECT 1
         FROM pg_catalog.pg_shdepend AS ownership
         JOIN pg_catalog.pg_roles AS target_role
           ON target_role.oid = ownership.refobjid
        WHERE target_role.rolname IN (
          'home_agent_binding_operator',
          'home_agent_identity_cutover',
          'home_agent_identity_cutover_kernel',
          'home_agent_identity_authority_kernel'
        )
          AND ownership.deptype = 'o'
     ) THEN
    RAISE EXCEPTION 'identity cutover role pair already owns objects'
      USING ERRCODE = '42501';
  END IF;
END
$identity_cutover_role_ceremony_preflight$;

SELECT 'CREATE ROLE home_agent_identity_cutover_kernel NOLOGIN'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_cutover_kernel'
) \gexec
SELECT 'CREATE ROLE home_agent_identity_authority_kernel NOLOGIN'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_authority_kernel'
) \gexec
SELECT 'CREATE ROLE home_agent_identity_cutover LOGIN'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_cutover'
) \gexec

SELECT pg_catalog.format(
         'REVOKE %I FROM %I', parent.rolname, member.rolname
       )
  FROM pg_catalog.pg_auth_members AS membership
  JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid
  JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
 WHERE member.rolname IN (
         'home_agent_binding_operator',
         'home_agent_identity_cutover',
         'home_agent_identity_cutover_kernel',
         'home_agent_identity_authority_kernel'
       )
    OR parent.rolname IN (
         'home_agent_binding_operator',
         'home_agent_identity_cutover',
         'home_agent_identity_cutover_kernel',
         'home_agent_identity_authority_kernel'
       ) \gexec

ALTER ROLE home_agent_identity_cutover_kernel RESET ALL;
ALTER ROLE home_agent_identity_authority_kernel RESET ALL;
ALTER ROLE home_agent_identity_cutover RESET ALL;
-- This ceremony is admitted only after the historical E1-E3 chain. Applying
-- these E5 caller limits in the base provisioner would invalidate the pinned
-- predecessor role contract during a fresh migration replay.
ALTER ROLE home_agent_binding_operator RESET ALL;
ALTER ROLE home_agent_identity_cutover_kernel NOLOGIN NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;
ALTER ROLE home_agent_identity_authority_kernel
  NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;
ALTER ROLE home_agent_binding_operator
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOINHERIT NOBYPASSRLS CONNECTION LIMIT 8;
ALTER ROLE home_agent_identity_cutover PASSWORD :'identity_cutover_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS
  CONNECTION LIMIT 1 VALID UNTIL '1970-01-01 00:00:00+00';
ALTER ROLE home_agent_identity_cutover SET default_transaction_isolation =
  'serializable';
ALTER ROLE home_agent_identity_cutover SET statement_timeout = '120s';
ALTER ROLE home_agent_identity_cutover SET lock_timeout = '5s';
ALTER ROLE home_agent_identity_cutover SET idle_in_transaction_session_timeout =
  '15s';
ALTER ROLE home_agent_identity_cutover SET transaction_timeout = '180s';
ALTER ROLE home_agent_identity_cutover SET log_parameter_max_length_on_error = 0;
ALTER ROLE home_agent_binding_operator SET statement_timeout = '15s';
ALTER ROLE home_agent_binding_operator SET lock_timeout = '5s';
ALTER ROLE home_agent_binding_operator SET
  idle_in_transaction_session_timeout = '15s';
ALTER ROLE home_agent_binding_operator SET transaction_timeout = '30s';

REVOKE ALL PRIVILEGES ON DATABASE home_agent
  FROM home_agent_identity_cutover, home_agent_identity_cutover_kernel,
       home_agent_identity_authority_kernel;
GRANT CONNECT ON DATABASE home_agent TO home_agent_identity_cutover;
REVOKE CREATE, TEMPORARY ON DATABASE home_agent
  FROM home_agent_identity_cutover;
GRANT home_agent_identity_cutover_kernel TO home_agent_owner
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
GRANT home_agent_identity_authority_kernel TO home_agent_owner
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

REVOKE pg_monitor, pg_read_all_settings, pg_read_all_stats,
  pg_stat_scan_tables, pg_read_all_data, pg_write_all_data,
  pg_read_server_files, pg_write_server_files, pg_execute_server_program,
  pg_checkpoint, pg_maintain, pg_signal_backend
  FROM home_agent_binding_operator, home_agent_identity_cutover,
       home_agent_identity_cutover_kernel,
       home_agent_identity_authority_kernel;

DO $identity_cutover_role_ceremony_validation$
DECLARE
  owner_oid oid;
  login_oid oid;
  kernel_oid oid;
  authority_kernel_oid oid;
  caller_oid oid;
  database_oid oid;
BEGIN
  SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_owner';
  SELECT oid INTO STRICT login_oid FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_cutover';
  SELECT oid INTO STRICT kernel_oid FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_cutover_kernel';
  SELECT oid INTO STRICT authority_kernel_oid FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_identity_authority_kernel';
  SELECT oid INTO STRICT caller_oid FROM pg_catalog.pg_roles
   WHERE rolname = 'home_agent_binding_operator';
  SELECT oid INTO STRICT database_oid FROM pg_catalog.pg_database
   WHERE datname = pg_catalog.current_database();

  IF NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_roles AS login_role
        WHERE login_role.oid = login_oid
          AND login_role.rolcanlogin
          AND NOT login_role.rolinherit
          AND NOT login_role.rolsuper
          AND NOT login_role.rolcreatedb
          AND NOT login_role.rolcreaterole
          AND NOT login_role.rolreplication
          AND NOT login_role.rolbypassrls
          AND login_role.rolconnlimit = 1
          AND login_role.rolvaliduntil =
              timestamptz '1970-01-01 00:00:00+00'
          AND login_role.rolconfig = ARRAY[
            'default_transaction_isolation=serializable',
            'statement_timeout=120s',
            'lock_timeout=5s',
            'idle_in_transaction_session_timeout=15s',
            'transaction_timeout=180s',
            'log_parameter_max_length_on_error=0'
          ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_roles AS kernel_role
        WHERE kernel_role.oid = kernel_oid
          AND NOT kernel_role.rolcanlogin
          AND NOT kernel_role.rolinherit
          AND NOT kernel_role.rolsuper
          AND NOT kernel_role.rolcreatedb
          AND NOT kernel_role.rolcreaterole
          AND NOT kernel_role.rolreplication
          AND NOT kernel_role.rolbypassrls
          AND kernel_role.rolconnlimit = 0
          AND kernel_role.rolvaliduntil IS NULL
          AND kernel_role.rolconfig IS NULL
     )
     OR NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_roles AS authority_kernel_role
        WHERE authority_kernel_role.oid = authority_kernel_oid
          AND NOT authority_kernel_role.rolcanlogin
          AND NOT authority_kernel_role.rolinherit
          AND NOT authority_kernel_role.rolsuper
          AND NOT authority_kernel_role.rolcreatedb
          AND NOT authority_kernel_role.rolcreaterole
          AND NOT authority_kernel_role.rolreplication
          AND NOT authority_kernel_role.rolbypassrls
          AND authority_kernel_role.rolconnlimit = 0
          AND authority_kernel_role.rolvaliduntil IS NULL
          AND authority_kernel_role.rolconfig IS NULL
     )
     OR NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_roles AS caller_role
        WHERE caller_role.oid = caller_oid
          AND caller_role.rolcanlogin
          AND NOT caller_role.rolinherit
          AND NOT caller_role.rolsuper
          AND NOT caller_role.rolcreatedb
          AND NOT caller_role.rolcreaterole
          AND NOT caller_role.rolreplication
          AND NOT caller_role.rolbypassrls
          AND caller_role.rolconnlimit = 8
          AND caller_role.rolvaliduntil IS NULL
          AND caller_role.rolconfig = ARRAY[
            'statement_timeout=15s',
            'lock_timeout=5s',
            'idle_in_transaction_session_timeout=15s',
            'transaction_timeout=30s'
          ]::text[]
     )
     OR NOT pg_catalog.has_database_privilege(
       login_oid, database_oid, 'CONNECT'
     )
     OR NOT pg_catalog.has_database_privilege(
       caller_oid, database_oid, 'CONNECT'
     )
     OR pg_catalog.has_database_privilege(
       login_oid, database_oid, 'CREATE,TEMPORARY'
     )
     OR pg_catalog.has_database_privilege(
       kernel_oid, database_oid, 'CONNECT,CREATE,TEMPORARY'
     )
     OR pg_catalog.has_database_privilege(
       authority_kernel_oid, database_oid, 'CONNECT,CREATE,TEMPORARY'
     )
     OR pg_catalog.has_database_privilege(
       caller_oid, database_oid, 'CREATE,TEMPORARY'
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
        WHERE roleid = authority_kernel_oid
          AND member = owner_oid
          AND NOT admin_option
          AND NOT inherit_option
          AND set_option
     ) <> 1
     OR (
       SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members
        WHERE roleid = kernel_oid
     ) <> 1
     OR (
       SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members
        WHERE roleid = authority_kernel_oid
     ) <> 1
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_auth_members
        WHERE member IN (
                login_oid, kernel_oid, authority_kernel_oid, caller_oid
              )
           OR roleid IN (login_oid, caller_oid)
     )
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_db_role_setting
        WHERE setrole IN (
                login_oid, kernel_oid, authority_kernel_oid, caller_oid
              )
          AND setdatabase <> 0
     )
     OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_shdepend
        WHERE refobjid IN (
                login_oid, kernel_oid, authority_kernel_oid, caller_oid
              )
          AND deptype = 'o'
     )
     OR EXISTS (
       SELECT 1
         FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.usename = 'home_agent_identity_cutover'
     ) THEN
    RAISE EXCEPTION 'identity cutover role ceremony validation failed'
      USING ERRCODE = '42501';
  END IF;
END
$identity_cutover_role_ceremony_validation$;
SQL

unset cutover_password PGPASSWORD
echo "provisioned expired dormant identity cutover roles at revision 0013"
