#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# Local restore drills stage repo1 while holding the deployment's exclusive
# repository lock. Legacy SFTP drills use native OpenSSH to stage a
# host-key-pinned encrypted snapshot. Every pgBackRest and PostgreSQL operation
# against the staged copy then runs with Docker network_mode=none.

readonly EX_USAGE=64
readonly EX_UNAVAILABLE=69
readonly EX_CONFIG=78
readonly STANZA=home-agent
readonly DATABASE=home_agent
readonly DATABASE_USER=home_agent_owner
readonly EXPECTED_SCHEMAS=engagement,identity,ingest,knowledge,media,operations,privacy

die() {
  printf 'restore drill: %s\n' "$*" >&2
  exit "$EX_CONFIG"
}

info() {
  printf 'restore drill: %s\n' "$*"
}

usage() {
  printf 'usage: sudo %s <home-agent.env> <pgBackRest-backup-label>\n' "$0" >&2
  exit "$EX_USAGE"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'restore drill: missing command: %s\n' "$1" >&2
    exit "$EX_UNAVAILABLE"
  }
}

require_absolute_safe_path() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] ||
    die "$name must be an absolute path without whitespace or shell metacharacters"
  [[ "$value" != *'//'* && "$value" != *'/../'* && "$value" != */.. &&
     "$value" != *'/./'* && "$value" != */. ]] ||
    die "$name contains a prohibited path component"
}

require_mapper_path() {
  local path="$1"
  local source
  source="$(findmnt -rn -o SOURCE -T "$path" 2>/dev/null || true)"
  [[ "$source" == /dev/mapper/* ]] ||
    die "$path is not on a verified /dev/mapper encrypted mount"
}

require_regular_nonsymlink() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] ||
    die "$path must be a regular, non-symlink file"
}

require_stat() {
  local expected="$1"
  local path="$2"
  local actual
  actual="$(stat -c '%u:%g:%a' "$path")"
  [[ "$actual" == "$expected" ]] ||
    die "$path has ownership/mode $actual; expected $expected"
}

assert_disjoint_paths() {
  local left="$1"
  local right="$2"
  [[ "$left" != "$right" ]] || die "$left and $right must be distinct"
  [[ "$right/" != "$left/"* ]] || die "$right may not be inside $left"
  [[ "$left/" != "$right/"* ]] || die "$left may not be inside $right"
}

pgbackrest_global_value() {
  local key="$1"
  awk -v wanted="$key" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    /^[[:space:]]*[#;]/ { next }
    /^[[:space:]]*\[/ {
      section=$0
      sub(/^[[:space:]]*\[/, "", section)
      sub(/\][[:space:]]*$/, "", section)
      section=trim(section)
      next
    }
    section == "global" {
      separator=index($0, "=")
      if (separator == 0) next
      candidate=trim(substr($0, 1, separator - 1))
      if (candidate == wanted) {
        count++
        result=trim(substr($0, separator + 1))
      }
    }
    END {
      if (count != 1 || result == "") exit 1
      print result
    }
  ' "$HOME_AGENT_PGBACKREST_CONF"
}

known_hosts_contains_target() {
  local lookup="$1"
  local known_hosts="$2"
  ssh-keygen -F "$lookup" -f "$known_hosts" >/dev/null 2>&1 &&
    ssh-keygen -lf "$known_hosts" -E sha256 >/dev/null 2>&1
}

scrub_file() {
  local path="${1:-}"
  [[ -n "$path" && -e "$path" ]] || return 0
  if command -v shred >/dev/null 2>&1; then
    shred -u -z -- "$path" >/dev/null 2>&1 || rm -f -- "$path"
  else
    rm -f -- "$path"
  fi
}

safe_remove_workspace() {
  local candidate="${1:-}"
  local canonical mount_target
  [[ -n "$candidate" && -d "$candidate" && ! -L "$candidate" ]] || return 0
  canonical="$(readlink -f -- "$candidate")"
  case "$canonical" in
    "$restore_root"/drill.[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.*) ;;
    *) die "refusing to remove unexpected workspace $canonical" ;;
  esac
  [[ "$canonical" != "$production_data" ]] ||
    die "refusing to remove production PostgreSQL data"
  mount_target="$(findmnt -rn -o TARGET -T "$canonical" 2>/dev/null || true)"
  [[ "$mount_target" == "$restore_mount_target" ]] ||
    die "refusing cross-mount cleanup for $canonical"
  ! mountpoint -q -- "$canonical" || die "refusing to remove a mount point"
  find "$canonical" -xdev -depth -mindepth 1 -delete
  rmdir -- "$canonical"
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

remove_container() {
  local name="${1:-}"
  [[ -n "$name" ]] || return 0
  docker rm -f "$name" >/dev/null 2>&1 || true
}

forget_container() {
  local target="$1"
  local current
  local retained=()
  for current in "${active_containers[@]}"; do
    [[ -n "$current" && "$current" != "$target" ]] && retained+=("$current")
  done
  active_containers=("${retained[@]}")
}

workspace=''
temporary_key=''
temporary_known_hosts=''
local_pgbackrest_config=''
active_containers=()
drill_succeeded=0

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  set +e
  local container
  for container in "${active_containers[@]}"; do
    remove_container "$container"
  done
  scrub_file "$temporary_key"
  scrub_file "$local_pgbackrest_config"
  rm -f -- "$temporary_known_hosts" 2>/dev/null || true
  if [[ -n "$workspace" ]]; then
    if [[ "$drill_succeeded" == 1 || "${HOME_AGENT_RESTORE_KEEP_FAILED:-0}" != 1 ]]; then
      safe_remove_workspace "$workspace"
    else
      info "failure artifacts retained on encrypted storage; temporary credentials were removed"
    fi
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

[[ $# == 2 ]] || usage
[[ "$(id -u)" == 0 ]] || die "must run as root"

for command in awk chmod chown cp date df dirname docker find findmnt flock grep install \
  mktemp mountpoint python3 readlink rm rmdir sleep stat timeout; do
  require_command "$command"
done

operator_dir="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
repository_guard="$operator_dir/pgbackrest_repository_guard.py"
receipt_writer="$operator_dir/phase3_evidence_receipts.py"
require_regular_nonsymlink "$repository_guard"
require_regular_nonsymlink "$receipt_writer"

env_file="$1"
backup_label="$2"
require_absolute_safe_path HOME_AGENT_ENV_FILE "$(readlink -m -- "$env_file")"
require_regular_nonsymlink "$env_file"
[[ "$(stat -c '%u' "$env_file")" == 0 ]] || die "$env_file must be root-owned"
env_mode="$(stat -c '%a' "$env_file")"
(( (8#$env_mode & 0022) == 0 )) || die "$env_file may not be group/world-writable"
env_file="$(readlink -f -- "$env_file")"

# The deployment environment is explicitly non-secret and operator-owned.
# shellcheck disable=SC1090
. "$env_file"

: "${HOME_AGENT_DATA_ROOT:?missing HOME_AGENT_DATA_ROOT}"
: "${HOME_AGENT_RESTORE_DRILL_ROOT:?missing HOME_AGENT_RESTORE_DRILL_ROOT}"
: "${HOME_AGENT_SECRETS_DIR:?missing HOME_AGENT_SECRETS_DIR}"
: "${HOME_AGENT_BACKUP_TOPOLOGY:?missing HOME_AGENT_BACKUP_TOPOLOGY}"
: "${HOME_AGENT_PGBACKREST_CONF:?missing HOME_AGENT_PGBACKREST_CONF}"
: "${HOME_AGENT_POSTGRES_IMAGE:?missing HOME_AGENT_POSTGRES_IMAGE}"
: "${HOME_AGENT_PGBACKREST_IMAGE:?missing HOME_AGENT_PGBACKREST_IMAGE}"
: "${HOME_AGENT_EXPECTED_DB_REVISION:?missing HOME_AGENT_EXPECTED_DB_REVISION}"

case "$HOME_AGENT_BACKUP_TOPOLOGY" in
  local)
    require_command pgrep
    : "${HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT:?missing HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT}"
    : "${HOME_AGENT_PGBACKREST_LOCK_FILE:?missing HOME_AGENT_PGBACKREST_LOCK_FILE}"
    ;;
  sftp_legacy)
    require_command sftp
    require_command ssh-keygen
    : "${HOME_AGENT_PGBACKREST_SFTP_KEY:?missing HOME_AGENT_PGBACKREST_SFTP_KEY}"
    : "${HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS:?missing HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS}"
    ;;
  *) die "HOME_AGENT_BACKUP_TOPOLOGY must be local or sftp_legacy" ;;
esac

[[ "$backup_label" =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]] ||
  die "invalid pgBackRest backup label"
[[ "$HOME_AGENT_EXPECTED_DB_REVISION" =~ ^[A-Za-z0-9._-]+$ ]] ||
  die "invalid expected database revision"
[[ "${HOME_AGENT_RESTORE_KEEP_FAILED:-0}" =~ ^[01]$ ]] ||
  die "HOME_AGENT_RESTORE_KEEP_FAILED must be 0 or 1"

sftp_timeout="${HOME_AGENT_RESTORE_SFTP_TIMEOUT_SECONDS:-7200}"
phase_timeout="${HOME_AGENT_RESTORE_PHASE_TIMEOUT_SECONDS:-3600}"
minimum_free_kib="${HOME_AGENT_RESTORE_MIN_FREE_KIB:-2097152}"
[[ "$sftp_timeout" =~ ^[1-9][0-9]*$ && "$phase_timeout" =~ ^[1-9][0-9]*$ &&
   "$minimum_free_kib" =~ ^[1-9][0-9]*$ ]] ||
  die "restore timeout and free-space inputs must be positive integers"
sftp_timeout=$((10#$sftp_timeout))
phase_timeout=$((10#$phase_timeout))
minimum_free_kib=$((10#$minimum_free_kib))
((sftp_timeout <= 86400 && phase_timeout <= 86400)) ||
  die "restore timeout inputs may not exceed 86400 seconds"

for path_name in HOME_AGENT_DATA_ROOT HOME_AGENT_RESTORE_DRILL_ROOT \
  HOME_AGENT_SECRETS_DIR HOME_AGENT_PGBACKREST_CONF; do
  require_absolute_safe_path "$path_name" "${!path_name}"
done
if [[ "$HOME_AGENT_BACKUP_TOPOLOGY" == local ]]; then
  require_absolute_safe_path HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT \
    "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
  require_absolute_safe_path HOME_AGENT_PGBACKREST_LOCK_FILE \
    "$HOME_AGENT_PGBACKREST_LOCK_FILE"
else
  for path_name in HOME_AGENT_PGBACKREST_SFTP_KEY HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS; do
    require_absolute_safe_path "$path_name" "${!path_name}"
  done
fi

require_mapper_path "$HOME_AGENT_DATA_ROOT"
restore_parent="$(dirname -- "$HOME_AGENT_RESTORE_DRILL_ROOT")"
[[ -d "$restore_parent" ]] || die "restore drill parent does not exist"
require_mapper_path "$restore_parent"
[[ ! -L "$HOME_AGENT_RESTORE_DRILL_ROOT" ]] || die "restore drill root may not be a symlink"
install -d -m 0700 -o root -g root "$HOME_AGENT_RESTORE_DRILL_ROOT"
require_mapper_path "$HOME_AGENT_RESTORE_DRILL_ROOT"
require_stat 0:0:700 "$HOME_AGENT_RESTORE_DRILL_ROOT"

restore_root="$(readlink -f -- "$HOME_AGENT_RESTORE_DRILL_ROOT")"
production_data="$(readlink -f -- "$HOME_AGENT_DATA_ROOT/postgres")"
[[ -n "$restore_root" && -n "$production_data" ]] || die "cannot canonicalize drill paths"
assert_disjoint_paths "$production_data" "$restore_root"
restore_mount_target="$(findmnt -rn -o TARGET -T "$restore_root")"
[[ -n "$restore_mount_target" ]] || die "cannot resolve encrypted restore mount"

require_regular_nonsymlink "$HOME_AGENT_PGBACKREST_CONF"
for protected_path in "$HOME_AGENT_PGBACKREST_CONF"; do
  require_mapper_path "$protected_path"
  require_stat 0:999:640 "$protected_path"
done
if [[ "$HOME_AGENT_BACKUP_TOPOLOGY" == sftp_legacy ]]; then
  require_regular_nonsymlink "$HOME_AGENT_PGBACKREST_SFTP_KEY"
  require_regular_nonsymlink "$HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS"
  for protected_path in "$HOME_AGENT_PGBACKREST_SFTP_KEY" "$HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS"; do
    require_mapper_path "$protected_path"
    require_stat 0:999:640 "$protected_path"
  done
else
  [[ -d "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" && ! -L "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" ]] ||
    die "local pgBackRest repository must be a non-symlink directory"
  require_mapper_path "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
  require_stat 999:999:700 "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
  require_regular_nonsymlink "$HOME_AGENT_PGBACKREST_LOCK_FILE"
  require_mapper_path "$HOME_AGENT_PGBACKREST_LOCK_FILE"
  require_stat 999:999:600 "$HOME_AGENT_PGBACKREST_LOCK_FILE"
fi

owner_password="$HOME_AGENT_SECRETS_DIR/runtime/postgres/postgres_owner_password"
require_regular_nonsymlink "$owner_password"
require_mapper_path "$owner_password"
require_stat 999:999:400 "$owner_password"

[[ "$HOME_AGENT_POSTGRES_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] ||
  die "HOME_AGENT_POSTGRES_IMAGE must use an immutable sha256 digest"
postgres_image_id="$(docker image inspect --format '{{.Id}}' "$HOME_AGENT_POSTGRES_IMAGE" 2>/dev/null)" ||
  die "pinned PostgreSQL image is not available locally"
pgbackrest_image_id="$(docker image inspect --format '{{.Id}}' "$HOME_AGENT_PGBACKREST_IMAGE" 2>/dev/null)" ||
  die "pgBackRest PostgreSQL image is not available locally"
[[ "$postgres_image_id" =~ ^sha256:[0-9a-f]{64}$ &&
   "$pgbackrest_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] ||
  die "Docker returned a non-immutable image ID"
image_base="$(
  docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.base.name"}}' \
    "$pgbackrest_image_id" 2>/dev/null
)" || die "cannot inspect pgBackRest image base"
[[ "$image_base" == "$HOME_AGENT_POSTGRES_IMAGE" ]] ||
  die "pgBackRest image was not built from the configured pinned PostgreSQL image"

postgres_tag="${HOME_AGENT_POSTGRES_IMAGE%%@sha256:*}"
[[ "$postgres_tag" =~ postgres:([0-9]+)\.([0-9]+)- ]] ||
  die "cannot derive PostgreSQL version from HOME_AGENT_POSTGRES_IMAGE"
expected_pg_major="${BASH_REMATCH[1]}"
expected_pg_minor="${BASH_REMATCH[2]}"
expected_version_num=$((10#$expected_pg_major * 10000 + 10#$expected_pg_minor))
pg_bin="/usr/lib/postgresql/$expected_pg_major/bin"

repo_type="$(pgbackrest_global_value repo1-type)" || die "missing unique repo1-type"
repo_path="$(pgbackrest_global_value repo1-path)" || die "missing unique repository path"
cipher_type="$(pgbackrest_global_value repo1-cipher-type)" || die "missing unique cipher type"
cipher_pass="$(pgbackrest_global_value repo1-cipher-pass)" || die "missing unique cipher passphrase"

[[ "$cipher_type" == aes-256-cbc && "$cipher_pass" =~ ^[0-9a-f]{64}$ ]] ||
  die "repository must use a 256-bit AES-256-CBC passphrase"

case "$HOME_AGENT_BACKUP_TOPOLOGY" in
  local)
    [[ "$repo_type" == posix && "$repo_path" == /repository ]] ||
      die "local restore source must be repo1 POSIX at /repository"
    ;;
  sftp_legacy)
    repo_host="$(pgbackrest_global_value repo1-sftp-host)" || die "missing unique SFTP host"
    repo_user="$(pgbackrest_global_value repo1-sftp-host-user)" || die "missing unique SFTP user"
    repo_port="$(pgbackrest_global_value repo1-sftp-host-port)" || die "missing unique SFTP port"
    host_check="$(pgbackrest_global_value repo1-sftp-host-key-check-type)" || die "missing unique host-key check type"
    host_hash="$(pgbackrest_global_value repo1-sftp-host-key-hash-type)" || die "missing unique host-key hash type"
    host_fingerprint="$(pgbackrest_global_value repo1-sftp-host-fingerprint)" || die "missing unique host-key fingerprint"
    [[ "$repo_type" == sftp && "$repo_user" == homeagent_backup ]] ||
      die "restore source must be the dedicated homeagent_backup SFTP repository"
    [[ "$repo_host" =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid SFTP host"
    [[ "$repo_port" =~ ^[0-9]+$ ]] || die "invalid SFTP port"
    repo_port=$((10#$repo_port))
    ((repo_port >= 1 && repo_port <= 65535)) || die "invalid SFTP port"
    require_absolute_safe_path repo1-path "$repo_path"
    [[ "$repo_path" != / && "$repo_path" != */ ]] ||
      die "repository path must name a directory below the SFTP root"
    [[ "$host_check" == fingerprint && "$host_hash" == sha256 ]] ||
      die "pgBackRest SFTP host verification must use a SHA-256 fingerprint"
    [[ "$host_fingerprint" =~ ^[0-9a-f]{64}$ ]] || die "invalid host fingerprint"
    host_lookup="$repo_host"
    [[ "$repo_port" == 22 ]] || host_lookup="[$repo_host]:$repo_port"
    known_hosts_contains_target "$host_lookup" "$HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS" ||
      die "known_hosts does not contain a valid pinned entry for the SFTP target"
    ;;
esac

available_kib="$(df -Pk "$restore_root" | awk 'NR == 2 {print $4}')"
[[ "$available_kib" =~ ^[0-9]+$ && available_kib -ge minimum_free_kib ]] ||
  die "restore drill volume does not meet the minimum free-space floor"

exec 9>"$restore_root/.restore-drill.lock"
chmod 0600 "$restore_root/.restore-drill.lock"
flock -n 9 || die "another restore drill is already running"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
workspace="$(mktemp -d "$restore_root/drill.$stamp.XXXXXXXX")"
[[ "$(readlink -f -- "$workspace")" == "$restore_root"/drill."$stamp".* ]] ||
  die "mktemp returned an unexpected workspace"
require_mapper_path "$workspace"
require_stat 0:0:700 "$workspace"

prefix="home-agent-restore-${stamp,,}-$$"
verify_container="$prefix-verify"
restore_container="$prefix-restore"
control_container="$prefix-control"
production_control_container="$control_container-production"
restored_control_container="$control_container-restored"
database_container="$prefix-db"
checksum_container="$prefix-checksums"
for container in "$verify_container" "$restore_container" "$control_container" \
  "$production_control_container" "$restored_control_container" \
  "$database_container" "$checksum_container"; do
  container_exists "$container" && die "unexpected existing container $container"
done

install -d -m 0700 -o root -g root "$workspace/ssh" "$workspace/stage"
if [[ "$HOME_AGENT_BACKUP_TOPOLOGY" == local ]]; then
  exec 8>>"$HOME_AGENT_PGBACKREST_LOCK_FILE"
  flock -n -x 8 || die "repository writer or another local restore holds the coordination lock"
  production_postgres_container=home-agent-postgres-1
  production_postgres_running=0
  for running_container in $(docker ps -q); do
    running_mounts="$(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$running_container")"
    running_name="$(docker inspect --format '{{.Name}}' "$running_container")"
    running_name="${running_name#/}"
    mounts_repository=0
    mounts_production_data=0
    printf '%s\n' "$running_mounts" |
      grep -Fxq "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" &&
      mounts_repository=1
    printf '%s\n' "$running_mounts" |
      grep -Fxq "$production_data" &&
      mounts_production_data=1
    if ((mounts_repository == 1 || mounts_production_data == 1)); then
      [[ "$running_name" == "$production_postgres_container" ]] ||
        die "an unreviewed running container mounts protected Home Agent storage"
      ((mounts_repository == 1 && mounts_production_data == 1)) ||
        die "production PostgreSQL protected-storage mounts drifted"
      production_postgres_running=1
      running_mount_pairs="$(
        docker inspect --format \
          '{{range .Mounts}}{{println .Source "|" .Destination}}{{end}}' \
          "$running_container"
      )"
      printf '%s\n' "$running_mount_pairs" |
        grep -Fxq "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT|/repository" ||
        die "production PostgreSQL repository mount drifted"
      printf '%s\n' "$running_mount_pairs" |
        grep -Fxq "$production_data|/var/lib/postgresql/data" ||
        die "production PostgreSQL data mount drifted"
      printf '%s\n' "$running_mount_pairs" |
        grep -Fxq "$HOME_AGENT_PGBACKREST_LOCK_FILE|/run/home-agent-locks/repository.lock" ||
        die "production PostgreSQL repository lock mount drifted"
    fi
  done
  repository_source="$(readlink -f -- "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT")"
  [[ -n "$repository_source" ]] || die "cannot canonicalize local repository"
  assert_disjoint_paths "$production_data" "$repository_source"
  assert_disjoint_paths "$restore_root" "$repository_source"
  repo_local="$workspace/stage/pgbackrest-repository"
  install -d -m 0700 -o 999 -g 999 "$repo_local"
  info "staging a locked encrypted repository snapshot"
  cp --archive --one-file-system --reflink=auto \
    "$repository_source/." "$repo_local/"
  require_stat 999:999:700 "$repo_local"
  flock -u 8
  exec 8>&-
  if ((production_postgres_running == 1)); then
    info "production PostgreSQL remained online behind the repository lock"
  fi
  info "using the staged encrypted local repository read-only"
else
  temporary_key="$workspace/ssh/id_ed25519"
  temporary_known_hosts="$workspace/ssh/known_hosts"
  install -m 0600 -o root -g root "$HOME_AGENT_PGBACKREST_SFTP_KEY" "$temporary_key"
  install -m 0600 -o root -g root \
    "$HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS" "$temporary_known_hosts"
  require_stat 0:0:600 "$temporary_key"
  require_stat 0:0:600 "$temporary_known_hosts"
  known_hosts_contains_target "$host_lookup" "$temporary_known_hosts" ||
    die "temporary known_hosts copy does not contain the pinned SFTP target"

  batch_file="$workspace/ssh/stage.batch"
  printf 'lcd %s\nget -pR %s\n' "$workspace/stage" "$repo_path" >"$batch_file"
  chmod 0600 "$batch_file"

  info "staging the encrypted repository with native OpenSSH SFTP"
  timeout --signal=TERM --kill-after=30s "$sftp_timeout" \
    sftp -q -F /dev/null -b "$batch_file" -P "$repo_port" -i "$temporary_key" \
      -o BatchMode=yes \
      -o IdentitiesOnly=yes \
      -o IdentityAgent=none \
      -o StrictHostKeyChecking=yes \
      -o "UserKnownHostsFile=$temporary_known_hosts" \
      -o GlobalKnownHostsFile=/dev/null \
      -o UpdateHostKeys=no \
      -o VerifyHostKeyDNS=no \
      -o CanonicalizeHostname=no \
      -o ProxyCommand=none \
      -o ProxyJump=none \
      -o ClearAllForwardings=yes \
      -o PasswordAuthentication=no \
      -o KbdInteractiveAuthentication=no \
      -o PreferredAuthentications=publickey \
      -o NumberOfPasswordPrompts=0 \
      -o ConnectionAttempts=1 \
      -o ConnectTimeout=20 \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o LogLevel=ERROR \
      "$repo_user@$repo_host"
  repo_local="$workspace/stage/${repo_path##*/}"
fi

[[ -d "$repo_local" && ! -L "$repo_local" ]] || die "staged repository is missing"
python3 "$repository_guard" "$repo_local" "$STANZA" ||
  die "staged repository tree validation failed"
[[ -f "$repo_local/backup/$STANZA/backup.info" ]] || die "staged backup.info is missing"
[[ -d "$repo_local/backup/$STANZA/$backup_label" ]] ||
  die "requested backup label is absent from the staged repository"
[[ -d "$repo_local/archive/$STANZA" ]] || die "staged WAL archive is missing"
if [[ "$HOME_AGENT_BACKUP_TOPOLOGY" == sftp_legacy ]]; then
  chown -R 999:999 "$repo_local"
  find "$repo_local" -xdev -type d -exec chmod 0500 {} +
  find "$repo_local" -xdev -type f -exec chmod 0400 {} +
fi

install -d -m 0700 -o root -g root "$workspace/container"
local_pgbackrest_config="$workspace/container/pgbackrest.conf"
{
  printf '[global]\n'
  printf 'repo1-type=posix\n'
  printf 'repo1-path=/repository\n'
  printf 'repo1-cipher-type=%s\n' "$cipher_type"
  printf 'repo1-cipher-pass=%s\n' "$cipher_pass"
  printf 'archive-async=n\n'
  printf 'spool-path=/var/spool/pgbackrest\n'
  printf 'process-max=1\n'
  printf 'log-level-console=info\n'
  printf 'log-level-file=off\n\n'
  printf '[%s]\n' "$STANZA"
  printf 'pg1-path=/var/lib/postgresql/data\n'
} >"$local_pgbackrest_config"
chown 999:999 "$local_pgbackrest_config"
chmod 0400 "$local_pgbackrest_config"
unset cipher_pass

install -d -m 0700 -o 999 -g 999 "$workspace/pgdata"

common_readonly=(
  --network none
  --read-only
  --user 999:999
  --security-opt no-new-privileges:true
  --cap-drop ALL
  --pids-limit 128
  --memory 512m
  --cpus 1
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777,uid=999,gid=999
  --mount "type=bind,src=$repo_local,dst=/repository,readonly"
  --mount "type=bind,src=$local_pgbackrest_config,dst=/etc/pgbackrest/pgbackrest.conf,readonly"
)

info "verifying the selected backup in the staged POSIX repository"
active_containers+=("$verify_container")
timeout --signal=TERM --kill-after=30s "$phase_timeout" \
  docker run --rm --name "$verify_container" "${common_readonly[@]}" \
    --entrypoint /usr/bin/pgbackrest "$pgbackrest_image_id" \
    --config=/etc/pgbackrest/pgbackrest.conf --stanza="$STANZA" \
    --set="$backup_label" --process-max=1 verify
forget_container "$verify_container"

info "restoring the selected backup from the local POSIX repository"
active_containers+=("$restore_container")
timeout --signal=TERM --kill-after=30s "$phase_timeout" \
  docker run --rm --name "$restore_container" "${common_readonly[@]}" \
    --mount "type=bind,src=$workspace/pgdata,dst=/var/lib/postgresql/data" \
    --entrypoint /usr/bin/pgbackrest "$pgbackrest_image_id" \
    --config=/etc/pgbackrest/pgbackrest.conf --stanza="$STANZA" \
    --set="$backup_label" --pg1-path=/var/lib/postgresql/data \
    --process-max=1 --type=immediate --target-action=promote \
    --archive-mode=off restore
forget_container "$restore_container"

[[ "$(<"$workspace/pgdata/PG_VERSION")" == "$expected_pg_major" ]] ||
  die "restored PG_VERSION does not match the pinned image"
[[ ! -e "$workspace/pgdata/postmaster.pid" ]] || die "restored cluster is unexpectedly running"
require_stat 999:999:700 "$workspace/pgdata"

pg_control_id() {
  local data_path="$1"
  local container_name="$2"
  timeout --signal=TERM --kill-after=10s 60 \
    docker run --rm --name "$container_name" --network none --read-only \
      --user 999:999 --security-opt no-new-privileges:true --cap-drop ALL \
      --mount "type=bind,src=$data_path,dst=/var/lib/postgresql/data,readonly" \
      --entrypoint "$pg_bin/pg_controldata" "$pgbackrest_image_id" \
      /var/lib/postgresql/data |
    awk -F: '/Database system identifier/ {gsub(/[[:space:]]/, "", $2); result=$2} END {print result}'
}

active_containers+=("$production_control_container" "$restored_control_container")
production_system_id="$(pg_control_id "$production_data" "$production_control_container")"
restored_system_id="$(pg_control_id "$workspace/pgdata" "$restored_control_container")"
forget_container "$production_control_container"
forget_container "$restored_control_container"
[[ "$production_system_id" =~ ^[0-9]+$ && "$production_system_id" == "$restored_system_id" ]] ||
  die "restored database system identifier does not match production"

info "booting the restored database with network_mode=none"
active_containers+=("$database_container")
docker run -d --name "$database_container" \
  --network none \
  --read-only \
  --user 999:999 \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --pids-limit 128 \
  --memory 1g \
  --cpus 1 \
  --shm-size 256m \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777,uid=999,gid=999 \
  --tmpfs /var/spool/pgbackrest:rw,nosuid,nodev,noexec,size=64m,mode=0700,uid=999,gid=999 \
  --mount "type=bind,src=$workspace/pgdata,dst=/var/lib/postgresql/data" \
  --mount "type=bind,src=$repo_local,dst=/repository,readonly" \
  --mount "type=bind,src=$local_pgbackrest_config,dst=/etc/pgbackrest/pgbackrest.conf,readonly" \
  --mount "type=bind,src=$owner_password,dst=/run/secrets/postgres_owner_password,readonly" \
  --entrypoint "$pg_bin/postgres" "$pgbackrest_image_id" \
  -D /var/lib/postgresql/data \
  -c listen_addresses= \
  -c unix_socket_directories=/tmp \
  -c archive_mode=off \
  -c archive_command= \
  -c logging_collector=off \
  -c log_statement=none \
  -c fsync=on \
  -c synchronous_commit=on >/dev/null

[[ "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$database_container")" == none ]] ||
  die "restore database unexpectedly has a network"
port_bindings="$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$database_container")"
[[ "$port_bindings" == '{}' || "$port_bindings" == null ]] ||
  die "restore database unexpectedly publishes a port"
[[ -z "$(docker port "$database_container" 2>/dev/null)" ]] ||
  die "restore database unexpectedly publishes a port"

db_state=''
recovery_deadline=$((SECONDS + phase_timeout))
while ((SECONDS < recovery_deadline)); do
  db_state="$(
    docker exec --user 999:999 "$database_container" sh -ec '
      export PGPASSWORD="$(cat /run/secrets/postgres_owner_password)"
      exec psql -h /tmp -U home_agent_owner -d home_agent -XAtq \
        -c "select pg_is_in_recovery()::text"
    ' 2>/dev/null || true
  )"
  [[ "$db_state" == false ]] && break
  sleep 2
done
[[ "$db_state" == false ]] || {
  docker logs --tail 100 "$database_container" >&2 || true
  die "restored database did not complete recovery"
}

validation="$(
  docker exec --user 999:999 "$database_container" sh -ec '
    export PGPASSWORD="$(cat /run/secrets/postgres_owner_password)"
    exec psql -h /tmp -U home_agent_owner -d home_agent -XAtq -v ON_ERROR_STOP=1 -c "
      select '\''version='\'' || current_setting('\''server_version_num'\'');
      select '\''checksums='\'' || current_setting('\''data_checksums'\'');
      select '\''listen='\'' || current_setting('\''listen_addresses'\'');
      select '\''recovery='\'' || pg_is_in_recovery()::text;
      select '\''revision='\'' || version_num from public.alembic_version;
      select '\''worker_maintenance_table='\'' ||
        case when to_regclass('\''operations.worker_maintenance_state'\'') is null
             then '\''missing'\'' else '\''present'\'' end;
      select '\''worker_maintenance_persistence='\'' || relpersistence::text
        from pg_class
       where oid = to_regclass('\''operations.worker_maintenance_state'\'');
      select '\''worker_maintenance_rows='\'' || count(*)::text
        from operations.worker_maintenance_state;
      select '\''schemas='\'' || string_agg(nspname, '\'','\'' order by nspname)
        from pg_namespace
       where nspname = any(array[
         '\''engagement'\'', '\''identity'\'', '\''ingest'\'', '\''knowledge'\'',
         '\''media'\'', '\''operations'\'', '\''privacy'\''
       ]);
    "
  '
)"
expected_validation="$(printf 'version=%s\nchecksums=on\nlisten=\nrecovery=false\nrevision=%s\nworker_maintenance_table=present\nworker_maintenance_persistence=u\nworker_maintenance_rows=0\nschemas=%s' \
  "$expected_version_num" "$HOME_AGENT_EXPECTED_DB_REVISION" "$EXPECTED_SCHEMAS")"
if [[ "$validation" != "$expected_validation" ]]; then
  printf 'restore drill: expected contract:\n%s\n' "$expected_validation" >&2
  printf 'restore drill: observed contract:\n%s\n' "$validation" >&2
  die "restored database contract validation failed"
fi

docker exec --user 999:999 "$database_container" sh -ec '
  export PGPASSWORD="$(cat /run/secrets/postgres_owner_password)"
  exec pg_dump -h /tmp -U home_agent_owner -d home_agent \
    --schema-only --no-owner --no-acl >/dev/null
'

info "stopping the restored database cleanly"
docker stop --time 60 "$database_container" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$database_container")" == 0 ]] ||
  die "restored database did not stop cleanly"
docker rm "$database_container" >/dev/null
forget_container "$database_container"

active_containers+=("$control_container")
cluster_state="$(
  timeout --signal=TERM --kill-after=10s 60 \
    docker run --rm --name "$control_container" --network none --read-only \
      --user 999:999 --security-opt no-new-privileges:true --cap-drop ALL \
      --mount "type=bind,src=$workspace/pgdata,dst=/var/lib/postgresql/data,readonly" \
      --entrypoint "$pg_bin/pg_controldata" "$pgbackrest_image_id" \
      /var/lib/postgresql/data |
    awk -F: '/Database cluster state/ {sub(/^[[:space:]]+/, "", $2); result=$2} END {print result}'
)"
forget_container "$control_container"
[[ "$cluster_state" == 'shut down' ]] || die "restored cluster is not cleanly shut down"

info "checking every restored page checksum offline"
active_containers+=("$checksum_container")
timeout --signal=TERM --kill-after=30s "$phase_timeout" \
  docker run --rm --name "$checksum_container" --network none --read-only \
    --user 999:999 --security-opt no-new-privileges:true --cap-drop ALL \
    --pids-limit 64 --memory 512m --cpus 1 \
    --mount "type=bind,src=$workspace/pgdata,dst=/var/lib/postgresql/data,readonly" \
    --entrypoint "$pg_bin/pg_checksums" "$pgbackrest_image_id" \
    --check -D /var/lib/postgresql/data
forget_container "$checksum_container"

drill_succeeded=1
python3 "$receipt_writer" restore \
  --backup-label "$backup_label" \
  --schema-revision "$HOME_AGENT_EXPECTED_DB_REVISION" \
  --database-system-identifier "$restored_system_id"
info "isolated restore drill passed for $backup_label at revision $HOME_AGENT_EXPECTED_DB_REVISION"
