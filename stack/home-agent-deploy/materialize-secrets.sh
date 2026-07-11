#!/bin/sh
set -eu

# Docker Compose implements file-backed secrets as bind mounts, so the source
# file's numeric owner must match the unprivileged container user. Keep the
# generated master set root-only and expose a distinct mode-0400 copy to each
# service. Compose mounts individual files, never these directories.

secrets_root="${1:?usage: materialize-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
runtime_root="$secrets_root/runtime"

[ "$(id -u)" -eq 0 ] || {
  echo "materialize-secrets.sh must run as root" >&2
  exit 77
}
[ -d "$master_root" ] || {
  echo "missing root-only master secret directory: $master_root" >&2
  exit 78
}

umask 077
install -d -m 0700 -o root -g root "$runtime_root"

install_secret() {
  service="$1"
  source_name="$2"
  target_name="$3"
  owner_uid="$4"
  owner_gid="$5"
  source_path="$master_root/$source_name"
  service_dir="$runtime_root/$service"
  target_path="$service_dir/$target_name"
  temporary_path="$service_dir/.$target_name.tmp.$$"

  [ -s "$source_path" ] || {
    echo "missing master secret: $source_path" >&2
    exit 78
  }
  install -d -m 0700 -o root -g root "$service_dir"
  install -m 0400 -o "$owner_uid" -g "$owner_gid" \
    "$source_path" "$temporary_path"
  mv -f "$temporary_path" "$target_path"
}

# The official postgres entrypoint re-executes as UID/GID 999 before reading
# POSTGRES_PASSWORD_FILE, so this copy must be readable only by that account.
install_secret postgres postgres_owner_password postgres_owner_password 999 999

# Root-started bootstrap jobs.
for name in \
  postgres_owner_password \
  postgres_api_password \
  postgres_ingest_password \
  postgres_worker_password \
  postgres_erasure_password \
  postgres_backup_password
do
  install_secret provision-roles "$name" "$name" 0 0
done
install_secret grant-runtime postgres_owner_password postgres_owner_password 0 0

# pgBackRest authenticates as a dedicated non-superuser over the shared Unix
# socket. It never receives the bootstrap owner password.
install_secret backup-gate postgres_backup_password postgres_backup_password 999 999

# Core runs as the fixed homeagent UID/GID 10001.
install_secret migrate database_url_owner database_url 10001 10001
install_secret core-api database_url_api database_url 10001 10001
install_secret core-api knowledge_encryption_key knowledge_encryption_key 10001 10001
install_secret core-api service_token service_token 10001 10001
install_secret core-api operator_token operator_token 10001 10001
install_secret core-api bootstrap_token bootstrap_token 10001 10001
install_secret core-ingest database_url_ingest database_url 10001 10001
install_secret core-ingest runtime_spool_key runtime_spool_key 10001 10001
install_secret core-ingest knowledge_encryption_key knowledge_encryption_key 10001 10001
install_secret core-ingest edge_token edge_token 10001 10001
install_secret core-worker database_url_worker database_url 10001 10001
install_secret core-worker runtime_spool_key runtime_spool_key 10001 10001
install_secret core-worker erasure_ledger_key erasure_ledger_key 10001 10001
install_secret ledger-init erasure_ledger_key erasure_ledger_key 10001 10001
install_secret restore-replay database_url_erasure database_url 10001 10001
install_secret restore-replay erasure_ledger_key erasure_ledger_key 10001 10001

# The official Node Alpine image's node account is fixed at UID/GID 1000.
install_secret bff service_token service_token 1000 1000
install_secret bff session_encryption_key session_encryption_key 1000 1000

# One-shot reviewed migration profile: no database URL or knowledge/spool key.
install_secret identity-migration operator_token operator_token 10001 10001
install_secret identity-migration bootstrap_token bootstrap_token 10001 10001

echo "materialized least-privilege Home Agent service secrets"
