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
load_secret HOME_AGENT_RUNTIME_SPOOL_KEY "${HOME_AGENT_RUNTIME_SPOOL_KEY_FILE:-}" "${HOME_AGENT_RUNTIME_SPOOL_KEY:-}"
load_secret HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY "${HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY_FILE:-}" "${HOME_AGENT_KNOWLEDGE_ENCRYPTION_KEY:-}"
load_secret HOME_AGENT_ERASURE_LEDGER_KEY "${HOME_AGENT_ERASURE_LEDGER_KEY_FILE:-}" "${HOME_AGENT_ERASURE_LEDGER_KEY:-}"
load_secret HOME_AGENT_EDGE_TOKEN "${HOME_AGENT_EDGE_TOKEN_FILE:-}" "${HOME_AGENT_EDGE_TOKEN:-}"
load_secret HOME_AGENT_SERVICE_TOKEN "${HOME_AGENT_SERVICE_TOKEN_FILE:-}" "${HOME_AGENT_SERVICE_TOKEN:-}"
load_secret HOME_AGENT_OPERATOR_TOKEN "${HOME_AGENT_OPERATOR_TOKEN_FILE:-}" "${HOME_AGENT_OPERATOR_TOKEN:-}"
load_secret HOME_AGENT_BOOTSTRAP_TOKEN "${HOME_AGENT_BOOTSTRAP_TOKEN_FILE:-}" "${HOME_AGENT_BOOTSTRAP_TOKEN:-}"

role="${1:-${HOME_AGENT_ROLE:-api}}"

if [ "${HOME_AGENT_RUN_MIGRATIONS:-0}" = "1" ]; then
  alembic upgrade head
fi

case "$role" in
  api|ingest|worker|all)
    exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port "${HOME_AGENT_PORT:-8104}" --workers 1
    ;;
  migrate)
    exec alembic upgrade head
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
  *)
    echo "unsupported HOME_AGENT_ROLE: $role" >&2
    exit 64
    ;;
esac
