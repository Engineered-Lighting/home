#!/bin/sh
set -eu

destination="${1:-/etc/home-agent/secrets}"
master="$destination/master"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || {
  echo "bootstrap-secrets.sh must run as root" >&2
  exit 77
}
umask 077
mkdir -p "$destination" "$master"
chmod 0700 "$destination"
chown root:root "$destination" "$master"
chmod 0700 "$master"

write_new() {
  path="$master/$1"
  [ ! -e "$path" ] || { echo "refusing to overwrite $path" >&2; exit 73; }
  printf '%s\n' "$2" > "$path"
  chmod 0600 "$path"
}

hex_secret() { openssl rand -hex "$1"; }
base64url_key() { openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'; }

owner_password="$(hex_secret 32)"
api_password="$(hex_secret 32)"
ingest_password="$(hex_secret 32)"
worker_password="$(hex_secret 32)"
erasure_password="$(hex_secret 32)"
backup_password="$(hex_secret 32)"
rollout_password="$(hex_secret 32)"

write_new postgres_owner_password "$owner_password"
write_new postgres_api_password "$api_password"
write_new postgres_ingest_password "$ingest_password"
write_new postgres_worker_password "$worker_password"
write_new postgres_erasure_password "$erasure_password"
write_new postgres_backup_password "$backup_password"
install -d -m 0700 -o root -g root "$master/rollout"
write_new rollout/postgres_rollout_password "$rollout_password"
write_new database_url_owner "postgresql+psycopg://home_agent_owner:${owner_password}@postgres:5432/home_agent"
write_new database_url_api "postgresql+psycopg://home_agent_api:${api_password}@postgres:5432/home_agent"
write_new database_url_ingest "postgresql+psycopg://home_agent_ingest:${ingest_password}@postgres:5432/home_agent"
write_new database_url_worker "postgresql+psycopg://home_agent_worker:${worker_password}@postgres:5432/home_agent"
write_new database_url_erasure "postgresql+psycopg://home_agent_erasure:${erasure_password}@postgres:5432/home_agent"
write_new rollout/database_url_rollout "postgresql+psycopg://home_agent_rollout:${rollout_password}@postgres:5432/home_agent"
write_new runtime_spool_key "$(base64url_key)"
write_new knowledge_encryption_key "$(base64url_key)"
write_new erasure_ledger_key "$(base64url_key)"
write_new edge_token "$(hex_secret 32)"
write_new service_token "$(hex_secret 32)"
write_new operator_token "$(hex_secret 32)"
write_new bootstrap_token "$(hex_secret 32)"
write_new session_encryption_key "$(base64url_key)"

sh "$script_dir/materialize-secrets.sh" "$destination"

echo "created root-only Home Agent master secrets in $master"
echo "created per-service mode-0400 copies in $destination/runtime"
echo "copy edge_token to the HA host as a separate mode-0600 file"
