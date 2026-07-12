#!/bin/sh
set -eu
umask 077

read_secret() {
  value="$(tr -d '\r\n' < "$1")"
  [ -n "$value" ] || { echo "empty secret: $1" >&2; exit 78; }
  printf '%s' "$value"
}

export PGPASSWORD="$(read_secret "$POSTGRES_OWNER_PASSWORD_FILE")"
api_password="$(read_secret /run/secrets/postgres_api_password)"
binding_operator_password="$(read_secret /run/secrets/postgres_binding_operator_password)"
identity_migration_password="$(read_secret /run/secrets/postgres_identity_migration_password)"
ingest_password="$(read_secret /run/secrets/postgres_ingest_password)"
worker_password="$(read_secret /run/secrets/postgres_worker_password)"
erasure_password="$(read_secret /run/secrets/postgres_erasure_password)"
rollout_password="$(read_secret /run/secrets/postgres_rollout_password)"
backup_password="$(read_secret /run/secrets/postgres_backup_password)"

psql -v ON_ERROR_STOP=1 \
  -v api_password="$api_password" \
  -v binding_operator_password="$binding_operator_password" \
  -v identity_migration_password="$identity_migration_password" \
  -v ingest_password="$ingest_password" \
  -v worker_password="$worker_password" \
  -v erasure_password="$erasure_password" \
  -v rollout_password="$rollout_password" \
  -v backup_password="$backup_password" <<'SQL'
SELECT 'CREATE ROLE home_agent_api LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_api') \gexec
SELECT 'CREATE ROLE home_agent_binding_operator LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_binding_operator') \gexec
SELECT 'CREATE ROLE home_agent_identity_kernel NOLOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_identity_kernel') \gexec
SELECT 'CREATE ROLE home_agent_identity_migration LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_identity_migration') \gexec
SELECT 'CREATE ROLE home_agent_ingest LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_ingest') \gexec
SELECT 'CREATE ROLE home_agent_worker LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_worker') \gexec
SELECT 'CREATE ROLE home_agent_erasure LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_erasure') \gexec
SELECT 'CREATE ROLE home_agent_rollout LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_rollout') \gexec
SELECT 'CREATE ROLE home_agent_backup LOGIN' WHERE NOT EXISTS
  (SELECT 1 FROM pg_roles WHERE rolname='home_agent_backup') \gexec

SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS parent ON parent.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE member.rolname IN (
  'home_agent_api', 'home_agent_binding_operator', 'home_agent_ingest',
  'home_agent_worker', 'home_agent_erasure', 'home_agent_rollout',
  'home_agent_backup', 'home_agent_identity_migration',
  'home_agent_identity_kernel'
) \gexec

-- Kernel ownership is never a privilege-escalation path for an online role.
-- Remove stale kernel membership from everyone except the offline owner before
-- re-establishing the owner's SET-only ownership-management relationship.
SELECT format('REVOKE %I FROM %I', parent.rolname, member.rolname)
FROM pg_auth_members AS membership
JOIN pg_roles AS parent ON parent.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE parent.rolname = 'home_agent_identity_kernel'
  AND member.rolname <> 'home_agent_owner' \gexec

ALTER ROLE home_agent_api PASSWORD :'api_password' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE home_agent_binding_operator PASSWORD :'binding_operator_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE home_agent_identity_kernel NOLOGIN NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS CONNECTION LIMIT 0;
ALTER ROLE home_agent_identity_migration PASSWORD :'identity_migration_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS
  CONNECTION LIMIT 1 VALID UNTIL '1970-01-01 00:00:00+00';
ALTER ROLE home_agent_ingest PASSWORD :'ingest_password' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE home_agent_worker PASSWORD :'worker_password' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE home_agent_erasure PASSWORD :'erasure_password' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS;
ALTER ROLE home_agent_rollout PASSWORD :'rollout_password' NOSUPERUSER
  NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS CONNECTION LIMIT 1;
ALTER ROLE home_agent_backup PASSWORD :'backup_password' NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION INHERIT NOBYPASSRLS;

ALTER ROLE home_agent_api SET statement_timeout = '15s';
ALTER ROLE home_agent_binding_operator SET statement_timeout = '15s';
ALTER ROLE home_agent_identity_migration SET default_transaction_isolation =
  'serializable';
ALTER ROLE home_agent_identity_migration SET statement_timeout = '120s';
ALTER ROLE home_agent_identity_migration SET lock_timeout = '5s';
ALTER ROLE home_agent_identity_migration SET idle_in_transaction_session_timeout =
  '15s';
ALTER ROLE home_agent_identity_migration SET transaction_timeout = '180s';
ALTER ROLE home_agent_ingest SET statement_timeout = '20s';
ALTER ROLE home_agent_worker SET statement_timeout = '60s';
ALTER ROLE home_agent_erasure SET statement_timeout = '60s';
ALTER ROLE home_agent_rollout SET statement_timeout = '30s';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE home_agent FROM PUBLIC;
GRANT CONNECT ON DATABASE home_agent TO home_agent_api, home_agent_ingest,
  home_agent_worker, home_agent_erasure, home_agent_rollout, home_agent_backup,
  home_agent_binding_operator, home_agent_identity_migration;
REVOKE CREATE, TEMPORARY ON DATABASE home_agent
  FROM home_agent_identity_migration;
REVOKE ALL PRIVILEGES ON DATABASE home_agent FROM home_agent_identity_kernel;
GRANT home_agent_identity_kernel TO home_agent_owner
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

-- pgBackRest copies cluster files as the unprivileged postgres OS account; it
-- does not need SQL access to application rows. PostgreSQL 17 documents these
-- four control functions as individually grantable to a non-superuser. The
-- read-only pg_read_all_settings role is the one pgBackRest requests for
-- cluster/archive discovery. Remove any stale predefined-role membership
-- before granting it, so an upgraded installation cannot retain row, live
-- query, server-file, program, checkpoint, or write authority.
REVOKE pg_monitor, pg_read_all_settings, pg_read_all_stats,
  pg_stat_scan_tables, pg_read_all_data, pg_write_all_data,
  pg_read_server_files, pg_write_server_files, pg_execute_server_program,
  pg_checkpoint, pg_maintain, pg_signal_backend
  FROM home_agent_backup, home_agent_rollout, home_agent_binding_operator,
  home_agent_identity_migration, home_agent_identity_kernel;
GRANT pg_read_all_settings TO home_agent_backup;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_backup_start(text, boolean)
  TO home_agent_backup;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_backup_stop(boolean)
  TO home_agent_backup;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_switch_wal()
  TO home_agent_backup;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_create_restore_point(text)
  TO home_agent_backup;
SQL
