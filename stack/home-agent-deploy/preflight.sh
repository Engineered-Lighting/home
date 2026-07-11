#!/bin/sh
set -eu

env_file="${1:-./home-agent.env}"
[ -r "$env_file" ] || { echo "usage: $0 <home-agent.env>" >&2; exit 64; }
# This file is non-secret; secret material must remain in individual files.
. "$env_file"

require_absolute() {
  case "$2" in
    /*) ;;
    *) echo "$1 must be an absolute path" >&2; exit 78 ;;
  esac
}

for command in docker openssl findmnt grep sha256sum install stat readlink; do
  command -v "$command" >/dev/null 2>&1 || { echo "missing command: $command" >&2; exit 69; }
done

: "${HOME_AGENT_DATA_ROOT:?missing HOME_AGENT_DATA_ROOT}"
: "${HOME_AGENT_RUNTIME_ROOT:?missing HOME_AGENT_RUNTIME_ROOT}"
: "${HOME_AGENT_SESSION_ROOT:?missing HOME_AGENT_SESSION_ROOT}"
: "${HOME_AGENT_ERASURE_LEDGER_ROOT:?missing HOME_AGENT_ERASURE_LEDGER_ROOT}"
: "${HOME_AGENT_SECRETS_DIR:?missing HOME_AGENT_SECRETS_DIR}"
: "${HOME_AGENT_TLS_DIR:?missing HOME_AGENT_TLS_DIR}"
: "${HOME_AGENT_PGBACKREST_CONF:?missing HOME_AGENT_PGBACKREST_CONF}"
: "${HOME_AGENT_PGBACKREST_SFTP_KEY:?missing HOME_AGENT_PGBACKREST_SFTP_KEY}"
: "${HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY:?missing HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY}"
: "${HOME_AGENT_EDGE_BIND_ADDR:?missing HOME_AGENT_EDGE_BIND_ADDR}"
: "${HOME_AGENT_POLICY_DIGEST:?missing HOME_AGENT_POLICY_DIGEST}"
: "${HOME_AGENT_ROLLOUT_MODE:?missing HOME_AGENT_ROLLOUT_MODE}"

case "$HOME_AGENT_ROLLOUT_MODE" in
  record_only|shadow|canary) ;;
  *) echo "HOME_AGENT_ROLLOUT_MODE must be record_only, shadow, or canary" >&2; exit 78 ;;
esac

require_absolute HOME_AGENT_DATA_ROOT "$HOME_AGENT_DATA_ROOT"
require_absolute HOME_AGENT_RUNTIME_ROOT "$HOME_AGENT_RUNTIME_ROOT"
require_absolute HOME_AGENT_SESSION_ROOT "$HOME_AGENT_SESSION_ROOT"
require_absolute HOME_AGENT_ERASURE_LEDGER_ROOT "$HOME_AGENT_ERASURE_LEDGER_ROOT"
require_absolute HOME_AGENT_SECRETS_DIR "$HOME_AGENT_SECRETS_DIR"
require_absolute HOME_AGENT_TLS_DIR "$HOME_AGENT_TLS_DIR"
require_absolute HOME_AGENT_PGBACKREST_CONF "$HOME_AGENT_PGBACKREST_CONF"
require_absolute HOME_AGENT_PGBACKREST_SFTP_KEY "$HOME_AGENT_PGBACKREST_SFTP_KEY"
require_absolute HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY "$HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY"

[ "$HOME_AGENT_EDGE_BIND_ADDR" != "0.0.0.0" ] || {
  echo "HOME_AGENT_EDGE_BIND_ADDR may not be 0.0.0.0" >&2; exit 78;
}

install -d -m 0700 -o 999 -g 999 "$HOME_AGENT_DATA_ROOT/postgres"
install -d -m 0700 -o 999 -g 999 "$HOME_AGENT_DATA_ROOT/pgbackrest-spool"
install -d -m 0555 -o 10001 -g 10001 "$HOME_AGENT_DATA_ROOT/resource-monitor"
install -d -m 0700 -o 10001 -g 10001 "$HOME_AGENT_RUNTIME_ROOT"
install -d -m 0700 -o 1000 -g 1000 "$HOME_AGENT_SESSION_ROOT"
install -d -m 0700 -o 10001 -g 10001 "$HOME_AGENT_ERASURE_LEDGER_ROOT"
install -d -m 0700 -o root -g root "$HOME_AGENT_SECRETS_DIR"
install -d -m 0700 -o root -g root "$HOME_AGENT_SECRETS_DIR/master"
install -d -m 0750 -o root -g 101 "$HOME_AGENT_TLS_DIR"

for target in "$HOME_AGENT_DATA_ROOT" "$HOME_AGENT_RUNTIME_ROOT" "$HOME_AGENT_SESSION_ROOT" "$HOME_AGENT_ERASURE_LEDGER_ROOT"; do
  source_device="$(findmnt -n -o SOURCE -T "$target" || true)"
  case "$source_device" in
    /dev/mapper/*) ;;
    *) echo "$target is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
done

# These stores have distinct backup/retention semantics. Canonical paths may
# neither coincide nor contain one another, even through symlinks.
data_root="$(readlink -f "$HOME_AGENT_DATA_ROOT")"
runtime_root="$(readlink -f "$HOME_AGENT_RUNTIME_ROOT")"
session_root="$(readlink -f "$HOME_AGENT_SESSION_ROOT")"
ledger_root="$(readlink -f "$HOME_AGENT_ERASURE_LEDGER_ROOT")"
for canonical in "$data_root" "$runtime_root" "$session_root" "$ledger_root"; do
  [ -n "$canonical" ] || { echo "cannot canonicalize storage roots" >&2; exit 78; }
done
assert_separate_roots() {
  left="$1"
  right="$2"
  case "$right/" in "$left/"*)
    echo "$right may not be inside $left" >&2; exit 78 ;;
  esac
  case "$left/" in "$right/"*)
    echo "$left may not be inside $right" >&2; exit 78 ;;
  esac
}
assert_separate_roots "$data_root" "$runtime_root"
assert_separate_roots "$data_root" "$session_root"
assert_separate_roots "$data_root" "$ledger_root"
assert_separate_roots "$runtime_root" "$session_root"
assert_separate_roots "$runtime_root" "$ledger_root"
assert_separate_roots "$session_root" "$ledger_root"

ledger_path="$HOME_AGENT_ERASURE_LEDGER_ROOT/ledger.jsonl"
ledger_head_path="$HOME_AGENT_ERASURE_LEDGER_ROOT/ledger.head.json"
[ -e "$ledger_path" ] && [ -e "$ledger_head_path" ] || {
  echo "erasure ledger is not initialized; run the one-time ledger-init profile" >&2
  exit 78
}
chown 10001:10001 "$ledger_path" "$ledger_head_path"
chmod 0600 "$ledger_path" "$ledger_head_path"

required_secrets="postgres_owner_password postgres_api_password postgres_ingest_password postgres_worker_password postgres_erasure_password postgres_backup_password database_url_owner database_url_api database_url_ingest database_url_worker database_url_erasure runtime_spool_key knowledge_encryption_key erasure_ledger_key edge_token service_token operator_token bootstrap_token session_encryption_key"
for name in $required_secrets; do
  path="$HOME_AGENT_SECRETS_DIR/master/$name"
  [ -s "$path" ] || { echo "missing secret file: $path" >&2; exit 78; }
  chown root:root "$path"
  chmod 0600 "$path"
  [ ! -e "$HOME_AGENT_SECRETS_DIR/$name" ] || {
    echo "legacy shared secret must be removed after verifying its master copy: $HOME_AGENT_SECRETS_DIR/$name" >&2
    exit 78
  }
done

deploy_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
sh "$deploy_dir/materialize-secrets.sh" "$HOME_AGENT_SECRETS_DIR"

verify_secret() {
  expected="$1"
  path="$2"
  actual="$(stat -c '%u:%g:%a' "$path")"
  [ "$actual" = "$expected" ] || {
    echo "incorrect secret ownership/mode ($actual, expected $expected): $path" >&2
    exit 78
  }
}

verify_secret 999:999:400 "$HOME_AGENT_SECRETS_DIR/runtime/postgres/postgres_owner_password"
for name in postgres_owner_password postgres_api_password postgres_ingest_password postgres_worker_password postgres_erasure_password postgres_backup_password; do
  verify_secret 0:0:400 "$HOME_AGENT_SECRETS_DIR/runtime/provision-roles/$name"
done
verify_secret 0:0:400 "$HOME_AGENT_SECRETS_DIR/runtime/grant-runtime/postgres_owner_password"
verify_secret 999:999:400 "$HOME_AGENT_SECRETS_DIR/runtime/backup-gate/postgres_backup_password"
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/migrate/database_url"
for name in database_url knowledge_encryption_key service_token operator_token bootstrap_token; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-api/$name"
done
for name in database_url runtime_spool_key knowledge_encryption_key edge_token; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-ingest/$name"
done
for name in database_url runtime_spool_key erasure_ledger_key; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-worker/$name"
done
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/ledger-init/erasure_ledger_key"
for name in database_url erasure_ledger_key; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/restore-replay/$name"
done
verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/service_token"
verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/session_encryption_key"
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/identity-migration/operator_token"
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/identity-migration/bootstrap_token"

for name in server.crt server.key client-ca.crt; do
  path="$HOME_AGENT_TLS_DIR/$name"
  [ -s "$path" ] || {
    echo "missing TLS file: $HOME_AGENT_TLS_DIR/$name" >&2; exit 78;
  }
  chown root:101 "$path"
  chmod 0640 "$path"
done
[ -s "$HOME_AGENT_PGBACKREST_CONF" ] || { echo "missing pgBackRest config" >&2; exit 78; }
config_source="$(findmnt -n -o SOURCE -T "$HOME_AGENT_PGBACKREST_CONF" || true)"
case "$config_source" in
  /dev/mapper/*) ;;
  *) echo "$HOME_AGENT_PGBACKREST_CONF is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
esac
chown root:999 "$HOME_AGENT_PGBACKREST_CONF"
chmod 0640 "$HOME_AGENT_PGBACKREST_CONF"
verify_secret 0:999:640 "$HOME_AGENT_PGBACKREST_CONF"
require_pgbackrest_setting() {
  pattern="$1"
  description="$2"
  grep -Eq "$pattern" "$HOME_AGENT_PGBACKREST_CONF" || {
    echo "pgBackRest config must set $description" >&2
    exit 78
  }
}
require_pgbackrest_setting '^repo1-type=sftp$' 'repo1-type=sftp'
require_pgbackrest_setting '^repo1-path=/[^[:space:]]+$' 'an absolute repository path'
require_pgbackrest_setting '^repo1-sftp-host=[^[:space:]]+$' 'a dedicated SFTP host'
require_pgbackrest_setting '^repo1-sftp-host-user=homeagent_backup$' 'the dedicated non-admin SFTP user'
require_pgbackrest_setting '^repo1-sftp-private-key-file=/run/pgbackrest-sftp/id_ed25519$' 'the mounted private-key path'
require_pgbackrest_setting '^repo1-sftp-public-key-file=/run/pgbackrest-sftp/id_ed25519.pub$' 'the mounted public-key path'
require_pgbackrest_setting '^repo1-sftp-host-key-check-type=fingerprint$' 'strict fingerprint host-key checking'
require_pgbackrest_setting '^repo1-sftp-host-key-hash-type=sha256$' 'SHA-256 host-key hashing'
require_pgbackrest_setting '^repo1-sftp-host-fingerprint=[0-9a-f]{64}$' 'a verified SHA-256 host-key fingerprint'
require_pgbackrest_setting '^repo1-cipher-type=aes-256-cbc$' 'AES-256-CBC repository encryption'
require_pgbackrest_setting '^repo1-cipher-pass=[0-9a-f]{64}$' 'a 256-bit repository cipher passphrase'
require_pgbackrest_setting '^pg1-user=home_agent_backup$' 'the least-privilege PostgreSQL backup role'
for key_path in "$HOME_AGENT_PGBACKREST_SFTP_KEY" "$HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY"; do
  [ -s "$key_path" ] || { echo "missing pgBackRest SFTP key file: $key_path" >&2; exit 78; }
  key_source="$(findmnt -n -o SOURCE -T "$key_path" || true)"
  case "$key_source" in
    /dev/mapper/*) ;;
    *) echo "$key_path is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
  chown root:999 "$key_path"
  chmod 0640 "$key_path"
  verify_secret 0:999:640 "$key_path"
done
verify_secret 999:999:700 "$HOME_AGENT_DATA_ROOT/pgbackrest-spool"
verify_secret 10001:10001:700 "$HOME_AGENT_ERASURE_LEDGER_ROOT"
verify_secret 10001:10001:600 "$ledger_path"
verify_secret 10001:10001:600 "$ledger_head_path"

policy_file="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/policy/home-agent-mvp-v1.json"
actual_digest="$(sha256sum "$policy_file" | awk '{print $1}')"
[ "$actual_digest" = "$HOME_AGENT_POLICY_DIGEST" ] || {
  echo "HOME_AGENT_POLICY_DIGEST does not match $policy_file" >&2; exit 78;
}

docker compose --env-file "$env_file" \
  -f "$deploy_dir/../home-agent-compose.yml" config --quiet

docker run --rm --user 101:101 --read-only \
  --add-host core-ingest:127.0.0.1 \
  --tmpfs /tmp:rw,size=16m,mode=1777 \
  --tmpfs /var/cache/nginx:rw,size=16m,mode=0755 \
  -v "$deploy_dir/nginx-edge.conf:/etc/nginx/nginx.conf:ro" \
  -v "$HOME_AGENT_TLS_DIR/server.crt:/run/secrets/server.crt:ro" \
  -v "$HOME_AGENT_TLS_DIR/server.key:/run/secrets/server.key:ro" \
  -v "$HOME_AGENT_TLS_DIR/client-ca.crt:/run/secrets/client-ca.crt:ro" \
  "${HOME_AGENT_NGINX_IMAGE:-nginxinc/nginx-unprivileged:1.29-alpine}" nginx -t

echo "Home Agent preflight passed; no service was started"
