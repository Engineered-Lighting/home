#!/bin/sh
set -eu
umask 077

mode="${1:-}"
authority="${2:-}"

case "$mode" in
  activate|deactivate|status) ;;
  *) echo "identity authority role ceremony mode is invalid" >&2; exit 64 ;;
esac
case "$authority" in
  finalizer) role_name="home_agent_identity_finalizer" ;;
  cutover) role_name="home_agent_identity_cutover" ;;
  *) echo "identity authority role ceremony target is invalid" >&2; exit 64 ;;
esac
[ "$#" -eq 2 ] || {
  echo "identity authority role ceremony accepts two fixed arguments" >&2
  exit 64
}

read_secret() {
  path="$1"
  [ -f "$path" ] && [ ! -L "$path" ] || {
    echo "identity authority role ceremony input is missing" >&2
    exit 78
  }
  value="$(tr -d '\r\n' < "$path")"
  [ -n "$value" ] || {
    echo "identity authority role ceremony input is empty" >&2
    exit 78
  }
  printf '%s' "$value"
}

permit="$(read_secret "${HOME_AGENT_PHASE3_GRANT_PERMIT_FILE:-}")"
[ "$permit" = \
  "phase3-grant-permit-e5m-v1:0017_authenticated_binding_e5c:0021_parent_status_e5h" ] || {
  echo "identity authority role ceremony permit is invalid" >&2
  exit 78
}
export PGPASSWORD="$(
  read_secret "${POSTGRES_OWNER_PASSWORD_FILE:-}"
)"

psql -X -v ON_ERROR_STOP=1 \
  -v ceremony_mode="$mode" \
  -v ceremony_role="$role_name" <<'SQL'
SELECT pg_catalog.set_config(
  'home_agent.phase3_ceremony_mode',
  :'ceremony_mode',
  false
);
SELECT pg_catalog.set_config(
  'home_agent.phase3_ceremony_role',
  :'ceremony_role',
  false
);

DO $identity_authority_role_ceremony$
DECLARE
  requested_mode text := pg_catalog.current_setting(
    'home_agent.phase3_ceremony_mode'
  );
  requested_role text := pg_catalog.current_setting(
    'home_agent.phase3_ceremony_role'
  );
  revision text;
  role_row pg_catalog.pg_roles%ROWTYPE;
  active_sessions integer;
  expiry timestamptz;
BEGIN
  IF requested_mode NOT IN ('activate', 'deactivate', 'status')
     OR requested_role NOT IN (
       'home_agent_identity_finalizer',
       'home_agent_identity_cutover'
     ) THEN
    RAISE EXCEPTION 'identity_authority_role_request_invalid';
  END IF;

  SELECT version_num INTO revision FROM public.alembic_version;
  IF revision IS DISTINCT FROM '0015_current_authority_e5a' THEN
    RAISE EXCEPTION 'identity_authority_role_revision_invalid';
  END IF;

  SELECT * INTO role_row
    FROM pg_catalog.pg_roles
   WHERE rolname = requested_role;
  IF NOT FOUND
     OR role_row.rolsuper
     OR role_row.rolcreatedb
     OR role_row.rolcreaterole
     OR role_row.rolreplication
     OR role_row.rolbypassrls
     OR role_row.rolinherit
     OR NOT role_row.rolcanlogin
     OR role_row.rolconnlimit <> 1 THEN
    RAISE EXCEPTION 'identity_authority_role_shape_invalid';
  END IF;

  SELECT pg_catalog.count(*) INTO active_sessions
    FROM pg_catalog.pg_stat_activity
   WHERE usename = requested_role
     AND pid <> pg_catalog.pg_backend_pid();

  IF requested_mode = 'activate' THEN
    IF active_sessions <> 0 THEN
      RAISE EXCEPTION 'identity_authority_role_session_exists';
    END IF;
    expiry := pg_catalog.clock_timestamp() + interval '2 minutes';
    EXECUTE pg_catalog.format(
      'ALTER ROLE %I VALID UNTIL %L',
      requested_role,
      expiry
    );
  ELSIF requested_mode = 'deactivate' THEN
    EXECUTE pg_catalog.format(
      'ALTER ROLE %I VALID UNTIL %L',
      requested_role,
      '1970-01-01 00:00:00+00'
    );
    PERFORM pg_catalog.pg_terminate_backend(activity.pid)
      FROM pg_catalog.pg_stat_activity AS activity
     WHERE activity.usename = requested_role
       AND activity.pid <> pg_catalog.pg_backend_pid();
  END IF;
END
$identity_authority_role_ceremony$;

DO $identity_authority_role_validation$
DECLARE
  requested_mode text := pg_catalog.current_setting(
    'home_agent.phase3_ceremony_mode'
  );
  requested_role text := pg_catalog.current_setting(
    'home_agent.phase3_ceremony_role'
  );
  valid_until timestamptz;
  active_sessions integer;
BEGIN
  SELECT rolvaliduntil INTO valid_until
    FROM pg_catalog.pg_authid
   WHERE rolname = requested_role;
  SELECT pg_catalog.count(*) INTO active_sessions
    FROM pg_catalog.pg_stat_activity
   WHERE usename = requested_role
     AND pid <> pg_catalog.pg_backend_pid();

  IF requested_mode = 'activate' THEN
    IF valid_until <= pg_catalog.clock_timestamp()
       OR valid_until >
          pg_catalog.clock_timestamp() + interval '2 minutes 5 seconds'
       OR active_sessions <> 0 THEN
      RAISE EXCEPTION 'identity_authority_role_activation_invalid';
    END IF;
  ELSE
    IF valid_until > pg_catalog.clock_timestamp()
       OR active_sessions <> 0 THEN
      RAISE EXCEPTION 'identity_authority_role_deactivation_invalid';
    END IF;
  END IF;
END
$identity_authority_role_validation$;
SQL

echo "identity authority role ceremony verified"
