#!/bin/sh
set -eu

load_secret() {
  target="$1"
  file_path="$2"
  direct_value="$3"
  [ -n "$file_path" ] || return 0
  if [ -n "$direct_value" ]; then
    echo "$target and ${target}_FILE may not both be set" >&2
    exit 78
  fi
  if [ ! -f "$file_path" ] || [ ! -r "$file_path" ]; then
    echo "secret file for $target is missing or unreadable" >&2
    exit 78
  fi
  secret_value="$(cat -- "$file_path")"
  if [ -z "$secret_value" ]; then
    echo "secret file for $target is empty" >&2
    exit 78
  fi
  case "$secret_value" in
    *[[:space:]]*)
      echo "secret file for $target contains whitespace" >&2
      exit 78
      ;;
  esac
  export "$target=$secret_value"
}

load_secret HOME_AGENT_DATABASE_URL "${HOME_AGENT_DATABASE_URL_FILE:-}" "${HOME_AGENT_DATABASE_URL:-}"
load_secret HOME_AGENT_OPERATOR_DATABASE_URL "${HOME_AGENT_OPERATOR_DATABASE_URL_FILE:-}" "${HOME_AGENT_OPERATOR_DATABASE_URL:-}"
load_secret HOME_AGENT_BINDING_COMMIT_DATABASE_URL "${HOME_AGENT_BINDING_COMMIT_DATABASE_URL_FILE:-}" "${HOME_AGENT_BINDING_COMMIT_DATABASE_URL:-}"
load_secret HOME_AGENT_RUNTIME_SPOOL_KEY "${HOME_AGENT_RUNTIME_SPOOL_KEY_FILE:-}" "${HOME_AGENT_RUNTIME_SPOOL_KEY:-}"
load_secret HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY "${HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY_FILE:-}" "${HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY:-}"
load_secret HOME_AGENT_ERASURE_LEDGER_KEY "${HOME_AGENT_ERASURE_LEDGER_KEY_FILE:-}" "${HOME_AGENT_ERASURE_LEDGER_KEY:-}"
load_secret HOME_AGENT_EDGE_TOKEN "${HOME_AGENT_EDGE_TOKEN_FILE:-}" "${HOME_AGENT_EDGE_TOKEN:-}"
load_secret HOME_AGENT_SERVICE_TOKEN "${HOME_AGENT_SERVICE_TOKEN_FILE:-}" "${HOME_AGENT_SERVICE_TOKEN:-}"
load_secret HOME_AGENT_OPERATOR_TOKEN "${HOME_AGENT_OPERATOR_TOKEN_FILE:-}" "${HOME_AGENT_OPERATOR_TOKEN:-}"
load_secret HOME_AGENT_BOOTSTRAP_TOKEN "${HOME_AGENT_BOOTSTRAP_TOKEN_FILE:-}" "${HOME_AGENT_BOOTSTRAP_TOKEN:-}"

DEPLOYABLE_MIGRATION_REVISION="0006a_worker_lease_arbitration"
PHASE3_FINALIZER_REVISION="0013_identity_finalizer_e3"
PHASE3_CURRENT_AUTHORITY_REVISION="0015_current_authority_e5a"
PHASE3_AUTHENTICATED_BINDING_REVISION="0017_authenticated_binding_e5c"
PHASE3_PARENT_AUTHORITY_REVISION="0018_parent_relationship_e5d"
PHASE3_PARENT_STATUS_REVISION="0021_parent_status_e5h"
PHASE3_OWNER_PERSON_REVISION="0027_owner_person_e5n"

required_migration_target() {
  target="${HOME_AGENT_EXPECTED_DB_REVISION:-}"
  if [ -z "$target" ]; then
    echo "HOME_AGENT_EXPECTED_DB_REVISION is required for migrations" >&2
    exit 78
  fi
  if [ "$target" != "$DEPLOYABLE_MIGRATION_REVISION" ]; then
    echo "HOME_AGENT_EXPECTED_DB_REVISION is not deployable by this image" >&2
    exit 78
  fi
  printf '%s' "$target"
}

run_exact_migration() {
  if [ "$#" -ne 1 ]; then
    echo "exact migration target is required" >&2
    exit 64
  fi
  migration_target="$1"
  if [ -z "$migration_target" ]; then
    echo "exact migration target is required" >&2
    exit 64
  fi
  alembic upgrade "$migration_target"
  python -m app.migration_guard "$migration_target"
}

run_migration() {
  migration_target="$(required_migration_target)"
  run_exact_migration "$migration_target"
}

run_phase3_migration() {
  if [ "$#" -ne 1 ]; then
    echo "phase3 migration accepts one fixed target" >&2
    exit 64
  fi
  migration_target="$1"
  if [ "${HOME_AGENT_RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "phase3 migration cannot use automatic startup migration" >&2
    exit 78
  fi
  run_exact_migration "$migration_target"
}

role="${1:-${HOME_AGENT_ROLE:-api}}"

if [ "${HOME_AGENT_RUN_MIGRATIONS:-0}" = "1" ] && [ "$role" != "migrate" ]; then
  case "$role" in
    phase3-migrate-*)
      echo "phase3 migration cannot use automatic startup migration" >&2
      exit 78
      ;;
    *)
      run_migration
      ;;
  esac
fi

case "$role" in
  api|ingest|worker|all)
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${HOME_AGENT_PORT:-8104}" --workers 1
    ;;
  migrate)
    run_migration
    ;;
  phase3-migrate-finalizer)
    [ "$#" -eq 1 ] || {
      echo "phase3 finalizer migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_FINALIZER_REVISION"
    ;;
  phase3-migrate-current-authority)
    [ "$#" -eq 1 ] || {
      echo "phase3 current-authority migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_CURRENT_AUTHORITY_REVISION"
    ;;
  phase3-migrate-authenticated-binding)
    [ "$#" -eq 1 ] || {
      echo "phase3 authenticated-binding migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_AUTHENTICATED_BINDING_REVISION"
    ;;
  phase3-migrate-parent-authority)
    [ "$#" -eq 1 ] || {
      echo "phase3 parent-authority migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_PARENT_AUTHORITY_REVISION"
    ;;
  phase3-migrate-parent-status)
    [ "$#" -eq 1 ] || {
      echo "phase3 parent-status migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_PARENT_STATUS_REVISION"
    ;;
  phase3-migrate-owner-person)
    [ "$#" -eq 1 ] || {
      echo "phase3 owner-person migration accepts no arguments" >&2
      exit 64
    }
    run_phase3_migration "$PHASE3_OWNER_PERSON_REVISION"
    ;;
  ledger-init)
    exec python -m app.cli ledger-init
    ;;
  restore-replay|restore-verify)
    export HOME_AGENT_ROLE=restore
    exec python -m app.cli "$role"
    ;;
  authorize-shadow)
    export HOME_AGENT_ROLE=rollout
    shift
    exec python -m app.cli authorize-shadow "$@"
    ;;
  identity-finalize)
    [ "$#" -eq 1 ] || {
      echo "identity finalizer accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_authority_executor finalize
    ;;
  identity-cutover)
    [ "$#" -eq 1 ] || {
      echo "identity cutover accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_authority_executor cutover
    ;;
  identity-evidence-freeze)
    [ "$#" -eq 1 ] || {
      echo "identity writer freeze evidence accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_evidence_writer freeze
    ;;
  identity-evidence-privacy)
    [ "$#" -eq 1 ] || {
      echo "identity privacy cutover evidence accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_evidence_writer privacy
    ;;
  identity-evidence-cutover)
    [ "$#" -eq 1 ] || {
      echo "identity semantic cutover candidate accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_evidence_writer cutover
    ;;
  identity-register-run)
    [ "$#" -eq 1 ] || {
      echo "identity migration registration accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_migration_registrar
    ;;
  identity-admit-finalizer)
    [ "$#" -eq 1 ] || {
      echo "identity finalizer admission accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_admission_writer finalizer
    ;;
  identity-admit-cutover)
    [ "$#" -eq 1 ] || {
      echo "identity cutover admission accepts no arguments" >&2
      exit 64
    }
    exec python -m app.identity_admission_writer cutover
    ;;
  phase3-activation-probe)
    [ "$#" -eq 2 ] || {
      echo "phase3 activation probe requires one fixed operation" >&2
      exit 64
    }
    exec python -m app.phase3_activation_probe "$2"
    ;;
  phase3-signing-material)
    [ "$#" -eq 1 ] || {
      echo "phase3 signing-material probe accepts no arguments" >&2
      exit 64
    }
    [ "${HOME_AGENT_ROLE:-}" = "api" ] || {
      echo "phase3 signing-material probe requires the API role" >&2
      exit 78
    }
    exec python -m app.phase3_signing_material
    ;;
  *)
    echo "unsupported HOME_AGENT_ROLE: $role" >&2
    exit 64
    ;;
esac
