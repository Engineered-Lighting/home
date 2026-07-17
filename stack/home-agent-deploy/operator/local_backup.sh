#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

die() {
  printf 'local backup: %s\n' "$*" >&2
  exit 78
}

require_mapper() {
  local path="$1"
  local source
  source="$(findmnt -rn -o SOURCE -T "$path" 2>/dev/null || true)"
  [[ "$source" == /dev/mapper/* ]] || die "$path is not on an encrypted mapper"
}

require_stat() {
  local expected="$1"
  local path="$2"
  [[ "$(stat -c '%u:%g:%a' "$path")" == "$expected" ]] ||
    die "$path has an unsafe owner or mode"
}

[[ $# == 1 ]] || die "usage: $0 <home-agent.env>"
[[ "$(id -u)" == 0 ]] || die "must run as root"
env_file="$1"
[[ "$env_file" == /srv/home-agent/config/home-agent.env ]] || die "unexpected environment path"
for trusted_parent in \
  /srv/home-agent \
  /srv/home-agent/config \
  /srv/home-agent/durable \
  /srv/home-agent/locks \
  /srv/home-agent/secrets \
  /srv/home-agent/secrets/runtime \
  /srv/home-agent/secrets/runtime/backup-gate \
  /usr/local \
  /usr/local/libexec \
  /usr/local/libexec/home-agent
do
  [[ -d "$trusted_parent" && ! -L "$trusted_parent" ]] ||
    die "trusted deployment parent is unsafe: $trusted_parent"
  [[ "$(stat -c '%u' "$trusted_parent")" == 0 ]] ||
    die "trusted deployment parent is not root-owned: $trusted_parent"
  trusted_parent_mode="$(stat -c '%a' "$trusted_parent")"
  (( (8#$trusted_parent_mode & 0022) == 0 )) ||
    die "trusted deployment parent is group/world-writable: $trusted_parent"
done
[[ "$(findmnt -rn -M /srv/home-agent -o TARGET 2>/dev/null || true)" == /srv/home-agent ]] ||
  die "/srv/home-agent is not the exact deployment mount point"
require_mapper /srv/home-agent
[[ -f "$env_file" && ! -L "$env_file" ]] || die "environment file is unsafe"
[[ "$(stat -c '%u:%h' "$env_file")" == 0:1 ]] ||
  die "environment file must be root-owned with one hard link"
env_mode="$(stat -c '%a' "$env_file")"
(( (8#$env_mode & 0022) == 0 )) || die "environment file may not be group/world-writable"
env_file="$(readlink -f -- "$env_file")"
[[ "$env_file" == /srv/home-agent/config/home-agent.env ]] ||
  die "environment file resolved outside the reviewed installed path"

# The deployment environment is non-secret and operator-owned.
# shellcheck disable=SC1090
. "$env_file"

[[ "${HOME_AGENT_BACKUP_TOPOLOGY:-}" == local ]] || die "active backup topology is not local"
[[ "${HOME_AGENT_DEPLOYMENT_ROOT:-}" == /srv/home-agent ]] || die "unexpected deployment root"
[[ "${HOME_AGENT_DATA_ROOT:-}" == /srv/home-agent/durable ]] || die "unexpected data root"
[[ "${HOME_AGENT_SECRETS_DIR:-}" == /srv/home-agent/secrets ]] || die "unexpected secrets root"
[[ "${HOME_AGENT_PGBACKREST_CONF:-}" == /srv/home-agent/config/pgbackrest-local.conf ]] ||
  die "unexpected pgBackRest config"
[[ "${HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT:-}" == \
   /srv/home-agent/durable/pgbackrest-repository ]] || die "unexpected repository root"
[[ "${HOME_AGENT_PGBACKREST_SPOOL_ROOT:-}" == \
   /srv/home-agent/durable/pgbackrest-spool-local-v1 ]] || die "unexpected archive spool"
[[ "${HOME_AGENT_PGBACKREST_LOCK_FILE:-}" == \
   /srv/home-agent/locks/pgbackrest-repository.lock ]] || die "unexpected repository lock"
[[ "${HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE:-}" == \
   /srv/home-agent/locks/pgbackrest-backup.lock ]] || die "unexpected backup lock"
[[ "${HOME_AGENT_PGBACKREST_IMAGE_ID:-}" =~ ^sha256:[0-9a-f]{64}$ ]] ||
  die "backup image ID is not immutable"
[[ "${HOME_AGENT_LOCAL_BACKUP_MANIFEST:-}" == \
   /srv/home-agent/config/local-backup-operator.sha256 ]] || die "unexpected operator manifest"

for path in \
  "$HOME_AGENT_DATA_ROOT/postgres" \
  "$HOME_AGENT_PGBACKREST_CONF" \
  "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" \
  "$HOME_AGENT_PGBACKREST_SPOOL_ROOT" \
  "$HOME_AGENT_PGBACKREST_LOCK_FILE" \
  "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"
do
  require_mapper "$path"
done
require_stat 999:999:700 "$HOME_AGENT_DATA_ROOT/postgres"
require_stat 0:999:640 "$HOME_AGENT_PGBACKREST_CONF"
require_stat 999:999:700 "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
require_stat 999:999:700 "$HOME_AGENT_PGBACKREST_SPOOL_ROOT"
for lock_file in "$HOME_AGENT_PGBACKREST_LOCK_FILE" "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"; do
  [[ -f "$lock_file" && ! -L "$lock_file" && "$(stat -c '%h' "$lock_file")" == 1 ]] ||
    die "lock file is unsafe"
  require_stat 999:999:600 "$lock_file"
done

credential="$HOME_AGENT_SECRETS_DIR/runtime/backup-gate/postgres_backup_password"
[[ -f "$credential" && ! -L "$credential" && "$(stat -c '%h' "$credential")" == 1 ]] ||
  die "backup credential is unsafe"
require_mapper "$credential"
require_stat 999:999:400 "$credential"

installed_gate=/usr/local/libexec/home-agent/backup-gate.sh
installed_self=/usr/local/libexec/home-agent/local-backup.sh
[[ "$(readlink -f -- "$0")" == "$installed_self" ]] || die "run the root-installed operator copy"
for installed in "$installed_self" "$installed_gate"; do
  [[ -f "$installed" && ! -L "$installed" && "$(stat -c '%u:%h' "$installed")" == 0:1 ]] ||
    die "installed operator file is unsafe: $installed"
  require_stat 0:0:555 "$installed"
done
manifest="$HOME_AGENT_LOCAL_BACKUP_MANIFEST"
[[ -f "$manifest" && ! -L "$manifest" && "$(stat -c '%u:%g:%a:%h' "$manifest")" == 0:0:600:1 ]] ||
  die "installed operator manifest is unsafe"
require_mapper "$manifest"
(cd / && sha256sum --check --status "$manifest") || die "installed operator digest mismatch"

actual_image_id="$(docker image inspect --format '{{.Id}}' "$HOME_AGENT_PGBACKREST_IMAGE_ID" 2>/dev/null || true)"
[[ "$actual_image_id" == "$HOME_AGENT_PGBACKREST_IMAGE_ID" ]] || die "approved backup image is unavailable"

scheduler_lock="$HOME_AGENT_DEPLOYMENT_ROOT/locks/scheduled-backup.lock"
exec 9>>"$scheduler_lock"
flock -n 9 || die "another scheduled backup is active"

runtime_dir=/run/home-agent-local-backup
install -d -o root -g root -m 0700 "$runtime_dir"
require_stat 0:0:700 "$runtime_dir"
cidfile="$runtime_dir/container.cid"
[[ ! -e "$cidfile" && ! -L "$cidfile" ]] || die "stale backup container ID file exists"
container_name=home-agent-local-backup
if docker container inspect "$container_name" >/dev/null 2>&1; then
  die "reserved backup container already exists"
fi

cleanup_container() {
  set +e
  docker stop --time 30 "$container_name" >/dev/null 2>&1
  docker rm -f "$container_name" >/dev/null 2>&1
  rm -f -- "$cidfile"
}
trap cleanup_container EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker run --rm --pull never \
  --name "$container_name" --cidfile "$cidfile" --stop-timeout 30 \
  --network none --read-only --user 999:999 \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --pids-limit 128 --memory 512m --cpus 0.50 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777,uid=999,gid=999 \
  --mount "type=bind,src=$HOME_AGENT_DATA_ROOT/postgres,dst=/var/lib/postgresql/data,readonly" \
  --mount "type=bind,src=$HOME_AGENT_PGBACKREST_CONF,dst=/etc/pgbackrest/pgbackrest.conf,readonly" \
  --mount "type=bind,src=$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT,dst=/repository" \
  --mount "type=bind,src=$HOME_AGENT_PGBACKREST_SPOOL_ROOT,dst=/var/spool/pgbackrest" \
  --mount "type=bind,src=$HOME_AGENT_PGBACKREST_LOCK_FILE,dst=/run/home-agent-locks/repository.lock" \
  --mount "type=bind,src=$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE,dst=/run/home-agent-locks/backup.lock" \
  --mount "type=bind,src=$credential,dst=/run/secrets/postgres_backup_password,readonly" \
  --mount "type=bind,src=$installed_gate,dst=/deploy/backup-gate.sh,readonly" \
  --mount type=volume,src=home-agent_postgres-socket,dst=/var/run/postgresql,readonly \
  --env POSTGRES_BACKUP_PASSWORD_FILE=/run/secrets/postgres_backup_password \
  --env "HOME_AGENT_BACKUP_MIN_FREE_KIB=${HOME_AGENT_BACKUP_MIN_FREE_KIB:-2097152}" \
  --env HOME_AGENT_BACKUP_LOCK_WAIT_SECONDS=300 \
  --env HOME_AGENT_BACKUP_TYPE=full \
  --entrypoint /usr/bin/ionice \
  "$HOME_AGENT_PGBACKREST_IMAGE_ID" -c 3 /bin/sh /deploy/backup-gate.sh &
docker_pid=$!
wait "$docker_pid"

printf 'local backup: completed type=full\n'
