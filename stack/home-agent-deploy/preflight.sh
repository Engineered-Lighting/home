#!/bin/sh
set -eu

env_file="${1:-/srv/home-agent/config/home-agent.env}"
[ "$#" -le 1 ] || {
  echo "usage: $0 /srv/home-agent/config/home-agent.env" >&2; exit 64;
}
for command in findmnt readlink stat; do
  command -v "$command" >/dev/null 2>&1 || { echo "missing command: $command" >&2; exit 69; }
done
for trusted_parent in /srv/home-agent /srv/home-agent/config /srv/home-agent/durable; do
  [ -d "$trusted_parent" ] && [ ! -L "$trusted_parent" ] || {
    echo "trusted deployment parent is absent or unsafe: $trusted_parent" >&2; exit 78;
  }
  [ "$(stat -c '%u' "$trusted_parent")" = 0 ] || {
    echo "trusted deployment parent must be root-owned: $trusted_parent" >&2; exit 78;
  }
  trusted_parent_mode="$(stat -c '%a' "$trusted_parent")"
  [ $((0$trusted_parent_mode & 022)) -eq 0 ] || {
    echo "trusted deployment parent may not be group/world-writable: $trusted_parent" >&2; exit 78;
  }
done
[ "$(findmnt -rn -M /srv/home-agent -o TARGET 2>/dev/null || true)" = /srv/home-agent ] || {
  echo "/srv/home-agent must be the exact encrypted-volume mount point" >&2; exit 78;
}
case "$(findmnt -rn -M /srv/home-agent -o SOURCE 2>/dev/null || true)" in
  /dev/mapper/*) ;;
  *) echo "/srv/home-agent is not mounted from a verified /dev/mapper source" >&2; exit 78 ;;
esac
[ "$env_file" = /srv/home-agent/config/home-agent.env ] || {
  echo "deployment environment must use the reviewed installed path" >&2; exit 78;
}
[ -f "$env_file" ] && [ ! -L "$env_file" ] && [ -r "$env_file" ] || {
  echo "usage: $0 <root-owned-home-agent.env>" >&2; exit 64;
}
[ "$(stat -c '%u' "$env_file")" = 0 ] && [ "$(stat -c '%h' "$env_file")" = 1 ] || {
  echo "deployment environment must be root-owned with one hard link" >&2; exit 78;
}
env_mode="$(stat -c '%a' "$env_file")"
[ $((0$env_mode & 022)) -eq 0 ] || {
  echo "deployment environment may not be group/world-writable" >&2; exit 78;
}
env_file="$(readlink -f "$env_file")"
[ "$env_file" = /srv/home-agent/config/home-agent.env ] || {
  echo "deployment environment resolved outside the reviewed installed path" >&2; exit 78;
}
# This file is non-secret; secret material must remain in individual files.
. "$env_file"

require_absolute() {
  case "$2" in
    /*) ;;
    *) echo "$1 must be an absolute path" >&2; exit 78 ;;
  esac
}

for command in docker openssl dirname find findmnt grep sha256sum install python3 stat readlink tr wc; do
  command -v "$command" >/dev/null 2>&1 || { echo "missing command: $command" >&2; exit 69; }
done

: "${HOME_AGENT_DATA_ROOT:?missing HOME_AGENT_DATA_ROOT}"
: "${HOME_AGENT_DEPLOYMENT_ROOT:?missing HOME_AGENT_DEPLOYMENT_ROOT}"
: "${HOME_AGENT_RUNTIME_ROOT:?missing HOME_AGENT_RUNTIME_ROOT}"
: "${HOME_AGENT_SESSION_ROOT:?missing HOME_AGENT_SESSION_ROOT}"
: "${HOME_AGENT_ERASURE_LEDGER_ROOT:?missing HOME_AGENT_ERASURE_LEDGER_ROOT}"
: "${HOME_AGENT_SECRETS_DIR:?missing HOME_AGENT_SECRETS_DIR}"
: "${HOME_AGENT_TLS_DIR:?missing HOME_AGENT_TLS_DIR}"
: "${HOME_AGENT_BACKUP_TOPOLOGY:?missing HOME_AGENT_BACKUP_TOPOLOGY}"
: "${HOME_AGENT_PGBACKREST_CONF:?missing HOME_AGENT_PGBACKREST_CONF}"
: "${HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT:?missing HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT}"
: "${HOME_AGENT_PGBACKREST_SPOOL_ROOT:?missing HOME_AGENT_PGBACKREST_SPOOL_ROOT}"
: "${HOME_AGENT_PGBACKREST_LOCK_FILE:?missing HOME_AGENT_PGBACKREST_LOCK_FILE}"
: "${HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE:?missing HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE}"
: "${HOME_AGENT_RESTORE_DRILL_ROOT:?missing HOME_AGENT_RESTORE_DRILL_ROOT}"
: "${HOME_AGENT_PGBACKREST_IMAGE:?missing HOME_AGENT_PGBACKREST_IMAGE}"
: "${HOME_AGENT_PGBACKREST_IMAGE_ID:?missing HOME_AGENT_PGBACKREST_IMAGE_ID}"
: "${HOME_AGENT_LOCAL_BACKUP_MANIFEST:?missing HOME_AGENT_LOCAL_BACKUP_MANIFEST}"
: "${HOME_AGENT_EXPECTED_DB_REVISION:?missing HOME_AGENT_EXPECTED_DB_REVISION}"
: "${HOME_AGENT_EDGE_BIND_ADDR:?missing HOME_AGENT_EDGE_BIND_ADDR}"
: "${HOME_AGENT_POLICY_DIGEST:?missing HOME_AGENT_POLICY_DIGEST}"
: "${HOME_AGENT_ROLLOUT_MODE:?missing HOME_AGENT_ROLLOUT_MODE}"
: "${HOME_AGENT_NATIVE_PUBLIC_ORIGIN:?missing HOME_AGENT_NATIVE_PUBLIC_ORIGIN}"

case "$HOME_AGENT_BACKUP_TOPOLOGY" in
  local) ;;
  *) echo "active deployment requires HOME_AGENT_BACKUP_TOPOLOGY=local" >&2; exit 78 ;;
esac

python3 - "$HOME_AGENT_NATIVE_PUBLIC_ORIGIN" "${HOME_AGENT_OAUTH_CLIENT_ID:-}" \
  "${HOME_AGENT_ALLOWED_ORIGINS:-}" <<'PY'
import sys
from urllib.parse import urlsplit

native, browser, browser_origins = sys.argv[1:]
try:
    parsed = urlsplit(native)
    port = parsed.port
    host = parsed.hostname or ""
    host.encode("ascii")
except (UnicodeError, ValueError):
    raise SystemExit("HOME_AGENT_NATIVE_PUBLIC_ORIGIN must be one exact HTTPS origin")
if (
    parsed.scheme != "https"
    or parsed.username is not None
    or parsed.password is not None
    or not host
    or parsed.path
    or parsed.query
    or parsed.fragment
):
    raise SystemExit("HOME_AGENT_NATIVE_PUBLIC_ORIGIN must be one exact HTTPS origin")
host_text = f"[{host}]" if ":" in host else host
canonical = f"https://{host_text}" + (f":{port}" if port not in (None, 443) else "")
if native != canonical:
    raise SystemExit("HOME_AGENT_NATIVE_PUBLIC_ORIGIN must use its canonical HTTPS spelling")
if browser and native == browser:
    raise SystemExit("native and browser OAuth origins must remain separate")
if native in {value.strip() for value in browser_origins.split(",") if value.strip()}:
    raise SystemExit("native origin may not appear in the browser allowed-origins set")
PY

case "$HOME_AGENT_ROLLOUT_MODE" in
  record_only|shadow|canary) ;;
  *) echo "HOME_AGENT_ROLLOUT_MODE must be record_only, shadow, or canary" >&2; exit 78 ;;
esac

require_absolute HOME_AGENT_DEPLOYMENT_ROOT "$HOME_AGENT_DEPLOYMENT_ROOT"
require_absolute HOME_AGENT_DATA_ROOT "$HOME_AGENT_DATA_ROOT"
require_absolute HOME_AGENT_RUNTIME_ROOT "$HOME_AGENT_RUNTIME_ROOT"
require_absolute HOME_AGENT_SESSION_ROOT "$HOME_AGENT_SESSION_ROOT"
require_absolute HOME_AGENT_ERASURE_LEDGER_ROOT "$HOME_AGENT_ERASURE_LEDGER_ROOT"
require_absolute HOME_AGENT_SECRETS_DIR "$HOME_AGENT_SECRETS_DIR"
require_absolute HOME_AGENT_TLS_DIR "$HOME_AGENT_TLS_DIR"
require_absolute HOME_AGENT_PGBACKREST_CONF "$HOME_AGENT_PGBACKREST_CONF"
require_absolute HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
require_absolute HOME_AGENT_PGBACKREST_SPOOL_ROOT "$HOME_AGENT_PGBACKREST_SPOOL_ROOT"
require_absolute HOME_AGENT_PGBACKREST_LOCK_FILE "$HOME_AGENT_PGBACKREST_LOCK_FILE"
require_absolute HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"
require_absolute HOME_AGENT_LOCAL_BACKUP_MANIFEST "$HOME_AGENT_LOCAL_BACKUP_MANIFEST"
[ "$HOME_AGENT_DEPLOYMENT_ROOT" = /srv/home-agent ] || {
  echo "HOME_AGENT_DEPLOYMENT_ROOT must be the reviewed /srv/home-agent mount" >&2
  exit 78
}
for expected_pair in \
  "HOME_AGENT_DATA_ROOT:$HOME_AGENT_DATA_ROOT:$HOME_AGENT_DEPLOYMENT_ROOT/durable" \
  "HOME_AGENT_RUNTIME_ROOT:$HOME_AGENT_RUNTIME_ROOT:$HOME_AGENT_DEPLOYMENT_ROOT/runtime" \
  "HOME_AGENT_SESSION_ROOT:$HOME_AGENT_SESSION_ROOT:$HOME_AGENT_DEPLOYMENT_ROOT/sessions" \
  "HOME_AGENT_ERASURE_LEDGER_ROOT:$HOME_AGENT_ERASURE_LEDGER_ROOT:$HOME_AGENT_DEPLOYMENT_ROOT/erasure-ledger" \
  "HOME_AGENT_SECRETS_DIR:$HOME_AGENT_SECRETS_DIR:$HOME_AGENT_DEPLOYMENT_ROOT/secrets" \
  "HOME_AGENT_TLS_DIR:$HOME_AGENT_TLS_DIR:$HOME_AGENT_DEPLOYMENT_ROOT/tls" \
  "HOME_AGENT_RESTORE_DRILL_ROOT:$HOME_AGENT_RESTORE_DRILL_ROOT:$HOME_AGENT_DEPLOYMENT_ROOT/restore-drills"
do
  path_name="${expected_pair%%:*}"
  remainder="${expected_pair#*:}"
  actual_path="${remainder%%:*}"
  expected_path="${remainder#*:}"
  [ "$actual_path" = "$expected_path" ] || {
    echo "$path_name must be the reviewed child of HOME_AGENT_DEPLOYMENT_ROOT" >&2
    exit 78
  }
done
[ "$HOME_AGENT_PGBACKREST_CONF" = "$HOME_AGENT_DEPLOYMENT_ROOT/config/pgbackrest-local.conf" ] || {
  echo "HOME_AGENT_PGBACKREST_CONF must be the reviewed local repository config path" >&2
  exit 78
}
[ "$HOME_AGENT_LOCAL_BACKUP_MANIFEST" = "$HOME_AGENT_DEPLOYMENT_ROOT/config/local-backup-operator.sha256" ] || {
  echo "HOME_AGENT_LOCAL_BACKUP_MANIFEST must be the reviewed operator manifest path" >&2
  exit 78
}
[ "$HOME_AGENT_PGBACKREST_LOCK_FILE" = "$HOME_AGENT_DEPLOYMENT_ROOT/locks/pgbackrest-repository.lock" ] || {
  echo "HOME_AGENT_PGBACKREST_LOCK_FILE must be the reviewed repository lock path" >&2
  exit 78
}
[ "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE" = "$HOME_AGENT_DEPLOYMENT_ROOT/locks/pgbackrest-backup.lock" ] || {
  echo "HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE must be the reviewed backup lock path" >&2
  exit 78
}
[ "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" = "$HOME_AGENT_DATA_ROOT/pgbackrest-repository" ] || {
  echo "HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT must be the dedicated pgbackrest-repository child of HOME_AGENT_DATA_ROOT" >&2
  exit 78
}
[ "$HOME_AGENT_PGBACKREST_SPOOL_ROOT" = "$HOME_AGENT_DATA_ROOT/pgbackrest-spool-local-v1" ] || {
  echo "HOME_AGENT_PGBACKREST_SPOOL_ROOT must be the fresh pgbackrest-spool-local-v1 child of HOME_AGENT_DATA_ROOT" >&2
  exit 78
}
require_absolute HOME_AGENT_RESTORE_DRILL_ROOT "$HOME_AGENT_RESTORE_DRILL_ROOT"
[ ! -L "$HOME_AGENT_RESTORE_DRILL_ROOT" ] || {
  echo "HOME_AGENT_RESTORE_DRILL_ROOT may not be a symlink" >&2
  exit 78
}

[ "$HOME_AGENT_EDGE_BIND_ADDR" != "0.0.0.0" ] || {
  echo "HOME_AGENT_EDGE_BIND_ADDR may not be 0.0.0.0" >&2; exit 78;
}
printf '%s' "$HOME_AGENT_POSTGRES_IMAGE" | grep -Eq '@sha256:[0-9a-f]{64}$' || {
  echo "HOME_AGENT_POSTGRES_IMAGE must use an immutable sha256 digest" >&2
  exit 78
}
printf '%s' "$HOME_AGENT_PGBACKREST_IMAGE_ID" | grep -Eq '^sha256:[0-9a-f]{64}$' || {
  echo "HOME_AGENT_PGBACKREST_IMAGE_ID must be an immutable local image ID" >&2
  exit 78
}
actual_pgbackrest_image_id="$(docker image inspect --format '{{.Id}}' "$HOME_AGENT_PGBACKREST_IMAGE" 2>/dev/null || true)"
[ "$actual_pgbackrest_image_id" = "$HOME_AGENT_PGBACKREST_IMAGE_ID" ] || {
  echo "configured pgBackRest image tag does not match its approved immutable ID" >&2
  exit 78
}
printf '%s' "$HOME_AGENT_EXPECTED_DB_REVISION" | grep -Eq '^[A-Za-z0-9._-]+$' || {
  echo "HOME_AGENT_EXPECTED_DB_REVISION is invalid" >&2
  exit 78
}

for existing_root in "$HOME_AGENT_DEPLOYMENT_ROOT" "$HOME_AGENT_DATA_ROOT"; do
  [ -d "$existing_root" ] && [ ! -L "$existing_root" ] || {
    echo "required encrypted deployment root is absent or unsafe: $existing_root" >&2
    exit 78
  }
  case "$(findmnt -n -o SOURCE -T "$existing_root" || true)" in
    /dev/mapper/*) ;;
    *) echo "$existing_root is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
  [ "$(stat -c '%u' "$existing_root")" = 0 ] || {
    echo "deployment structural parent must be root-owned: $existing_root" >&2
    exit 78
  }
  existing_root_mode="$(stat -c '%a' "$existing_root")"
  [ $((0$existing_root_mode & 022)) -eq 0 ] || {
    echo "deployment structural parent may not be group/world-writable: $existing_root" >&2
    exit 78
  }
done
for planned_dir in \
  "$HOME_AGENT_DATA_ROOT/postgres" \
  "$HOME_AGENT_DATA_ROOT/resource-monitor" \
  "$HOME_AGENT_RUNTIME_ROOT" \
  "$HOME_AGENT_SESSION_ROOT" \
  "$HOME_AGENT_ERASURE_LEDGER_ROOT" \
  "$HOME_AGENT_SECRETS_DIR" \
  "$HOME_AGENT_SECRETS_DIR/master" \
  "$HOME_AGENT_TLS_DIR" \
  "$(dirname "$HOME_AGENT_PGBACKREST_LOCK_FILE")" \
  "$HOME_AGENT_RESTORE_DRILL_ROOT"
do
  [ ! -L "$planned_dir" ] || {
    echo "refusing symlinked deployment directory: $planned_dir" >&2
    exit 78
  }
  if [ -e "$planned_dir" ] && [ ! -d "$planned_dir" ]; then
    echo "deployment directory path exists but is not a directory: $planned_dir" >&2
    exit 78
  fi
done

install -d -m 0700 -o 999 -g 999 "$HOME_AGENT_DATA_ROOT/postgres"
restore_parent="$(dirname "$HOME_AGENT_RESTORE_DRILL_ROOT")"
[ -d "$restore_parent" ] || {
  echo "restore-drill parent does not exist: $restore_parent" >&2
  exit 78
}
case "$(findmnt -n -o SOURCE -T "$restore_parent" || true)" in
  /dev/mapper/*) ;;
  *) echo "$restore_parent is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
esac
install -d -m 0700 -o root -g root "$HOME_AGENT_RESTORE_DRILL_ROOT"
install -d -m 0555 -o 10001 -g 10001 "$HOME_AGENT_DATA_ROOT/resource-monitor"
install -d -m 0700 -o 10001 -g 10001 "$HOME_AGENT_RUNTIME_ROOT"
install -d -m 0700 -o 1000 -g 1000 "$HOME_AGENT_SESSION_ROOT"
install -d -m 0700 -o 10001 -g 10001 "$HOME_AGENT_ERASURE_LEDGER_ROOT"
install -d -m 0700 -o root -g root "$HOME_AGENT_SECRETS_DIR"
install -d -m 0700 -o root -g root "$HOME_AGENT_SECRETS_DIR/master"
install -d -m 0750 -o root -g 101 "$HOME_AGENT_TLS_DIR"
lock_parent="$(dirname "$HOME_AGENT_PGBACKREST_LOCK_FILE")"
install -d -m 0700 -o root -g root "$lock_parent"
for lock_file in "$HOME_AGENT_PGBACKREST_LOCK_FILE" "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"; do
  [ ! -L "$lock_file" ] || {
    echo "pgBackRest lock file may not be a symlink" >&2; exit 78;
  }
  if [ -e "$lock_file" ]; then
    [ -f "$lock_file" ] || {
      echo "pgBackRest lock path exists but is not a regular file" >&2; exit 78;
    }
  else
    install -m 0600 -o 999 -g 999 /dev/null "$lock_file"
  fi
  [ "$(stat -c '%h' "$lock_file")" = 1 ] || {
    echo "pgBackRest lock file may not have hard links" >&2; exit 78;
  }
  chown 999:999 "$lock_file"
  chmod 0600 "$lock_file"
  case "$(findmnt -n -o SOURCE -T "$lock_file" || true)" in
    /dev/mapper/*) ;;
    *) echo "pgBackRest lock file is not on the encrypted deployment mapper" >&2; exit 78 ;;
  esac
done

for target in "$HOME_AGENT_DATA_ROOT" "$HOME_AGENT_RUNTIME_ROOT" "$HOME_AGENT_SESSION_ROOT" "$HOME_AGENT_ERASURE_LEDGER_ROOT"; do
  source_device="$(findmnt -n -o SOURCE -T "$target" || true)"
  case "$source_device" in
    /dev/mapper/*) ;;
    *) echo "$target is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
done
pgbackrest_parent="$(dirname "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT")"
[ "$pgbackrest_parent" = "$(dirname "$HOME_AGENT_PGBACKREST_SPOOL_ROOT")" ] || {
  echo "pgBackRest repository and spool must have the same approved parent" >&2
  exit 78
}
[ -d "$pgbackrest_parent" ] && [ ! -L "$pgbackrest_parent" ] || {
  echo "pgBackRest repository parent must be an existing non-symlink directory" >&2
  exit 78
}
pgbackrest_parent_source="$(findmnt -n -o SOURCE -T "$pgbackrest_parent" || true)"
case "$pgbackrest_parent_source" in
  /dev/mapper/*) ;;
  *) echo "$pgbackrest_parent is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
esac
for private_path in "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT" "$HOME_AGENT_PGBACKREST_SPOOL_ROOT"; do
  [ ! -L "$private_path" ] || {
    echo "pgBackRest private path may not be a symlink: $private_path" >&2
    exit 78
  }
  if [ -e "$private_path" ]; then
    [ -d "$private_path" ] || {
      echo "pgBackRest private path exists but is not a directory: $private_path" >&2
      exit 78
    }
  else
    install -d -m 0700 -o 999 -g 999 "$private_path"
  fi
  case "$(findmnt -n -o SOURCE -T "$private_path" || true)" in
    /dev/mapper/*) ;;
    *) echo "$private_path is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
  [ "$(stat -c '%u:%g:%a' "$private_path")" = "999:999:700" ] || {
    echo "pgBackRest private path must be UID/GID 999 and mode 0700: $private_path" >&2
    exit 78
  }
done
restore_source_device="$(findmnt -n -o SOURCE -T "$HOME_AGENT_RESTORE_DRILL_ROOT" || true)"
case "$restore_source_device" in
  /dev/mapper/*) ;;
  *) echo "$HOME_AGENT_RESTORE_DRILL_ROOT is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
esac
verify_restore_root="$(stat -c '%u:%g:%a' "$HOME_AGENT_RESTORE_DRILL_ROOT")"
[ "$verify_restore_root" = "0:0:700" ] || {
  echo "incorrect restore-drill root ownership/mode: $HOME_AGENT_RESTORE_DRILL_ROOT" >&2
  exit 78
}

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
restore_drill_root="$(readlink -f "$HOME_AGENT_RESTORE_DRILL_ROOT")"
postgres_root="$(readlink -f "$HOME_AGENT_DATA_ROOT/postgres")"
pgbackrest_spool_root="$(readlink -f "$HOME_AGENT_PGBACKREST_SPOOL_ROOT")"
local_repo_root="$(readlink -f "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT")"
[ -n "$restore_drill_root" ] && [ -n "$postgres_root" ] &&
  [ -n "$pgbackrest_spool_root" ] && [ -n "$local_repo_root" ] || {
  echo "cannot canonicalize restore-drill roots" >&2; exit 78;
}
assert_separate_roots "$restore_drill_root" "$postgres_root"
[ "$local_repo_root" != "$data_root" ] || {
  echo "local pgBackRest repository may not be the data root" >&2; exit 78;
}
assert_separate_roots "$local_repo_root" "$postgres_root"
assert_separate_roots "$local_repo_root" "$pgbackrest_spool_root"
assert_separate_roots "$local_repo_root" "$restore_drill_root"
assert_separate_roots "$local_repo_root" "$runtime_root"
assert_separate_roots "$local_repo_root" "$session_root"
assert_separate_roots "$local_repo_root" "$ledger_root"

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
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] || {
    echo "missing or unsafe secret file: $path" >&2
    exit 78
  }
  chown root:root "$path"
  chmod 0600 "$path"
  [ ! -e "$HOME_AGENT_SECRETS_DIR/$name" ] || {
    echo "legacy shared secret must be removed after verifying its master copy: $HOME_AGENT_SECRETS_DIR/$name" >&2
    exit 78
  }
done

native_installations_master="$HOME_AGENT_SECRETS_DIR/master/native_installations.json"
[ -f "$native_installations_master" ] && [ ! -L "$native_installations_master" ] &&
  [ -s "$native_installations_master" ] || {
  echo "missing or unsafe native installation registry: $native_installations_master" >&2
  exit 78
}
chown root:root "$native_installations_master"
chmod 0600 "$native_installations_master"
case "$(findmnt -n -o SOURCE -T "$native_installations_master" || true)" in
  /dev/mapper/*) ;;
  *) echo "$native_installations_master is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
esac

binding_operator_master="$HOME_AGENT_SECRETS_DIR/master/binding-operator"
[ -d "$binding_operator_master" ] && [ ! -L "$binding_operator_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$binding_operator_master")" = "0:0:700" ] || {
  echo "binding operator master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$binding_operator_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "binding operator master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_binding_operator_password database_url_binding_operator; do
  path="$binding_operator_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "binding operator master secret is absent or unsafe: $path" >&2
    exit 78
  }
done
binding_operator_password="$(tr -d '\r\n' < "$binding_operator_master/postgres_binding_operator_password")"
binding_operator_url="$(tr -d '\r\n' < "$binding_operator_master/database_url_binding_operator")"
case "$binding_operator_password" in
  *[!0-9a-f]*|'') echo "binding operator password is not lowercase hex" >&2; exit 78 ;;
esac
[ "${#binding_operator_password}" -eq 64 ] || {
  echo "binding operator password has the wrong length" >&2
  exit 78
}
[ "$binding_operator_url" = "postgresql+psycopg://home_agent_binding_operator:${binding_operator_password}@postgres:5432/home_agent" ] || {
  echo "binding operator database URL does not match the isolated role" >&2
  exit 78
}
for other_name in \
  postgres_owner_password \
  postgres_api_password \
  postgres_ingest_password \
  postgres_worker_password \
  postgres_erasure_password \
  postgres_backup_password
do
  other_password="$(tr -d '\r\n' < "$HOME_AGENT_SECRETS_DIR/master/$other_name")"
  [ "$binding_operator_password" != "$other_password" ] || {
    echo "binding operator password must be independent from every other database role" >&2
    exit 78
  }
done
unset binding_operator_url other_password other_name

binding_committer_master="$HOME_AGENT_SECRETS_DIR/master/binding-committer"
[ -d "$binding_committer_master" ] && [ ! -L "$binding_committer_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$binding_committer_master")" = "0:0:700" ] || {
  echo "binding committer master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$binding_committer_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "binding committer master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_binding_committer_password database_url_binding_committer; do
  path="$binding_committer_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "binding committer master secret is absent or unsafe: $path" >&2
    exit 78
  }
done
binding_committer_password="$(tr -d '\r\n' < "$binding_committer_master/postgres_binding_committer_password")"
binding_committer_url="$(tr -d '\r\n' < "$binding_committer_master/database_url_binding_committer")"
case "$binding_committer_password" in
  *[!0-9a-f]*|'') echo "binding committer password is not lowercase hex" >&2; exit 78 ;;
esac
[ "${#binding_committer_password}" -eq 64 ] || {
  echo "binding committer password has the wrong length" >&2
  exit 78
}
[ "$binding_committer_url" = "postgresql+psycopg://home_agent_binding_committer:${binding_committer_password}@postgres:5432/home_agent" ] || {
  echo "binding committer database URL does not match the isolated role" >&2
  exit 78
}
[ "$binding_committer_password" != "$binding_operator_password" ] || {
  echo "binding staging and commit database passwords must differ" >&2
  exit 78
}
for other_name in \
  postgres_owner_password \
  postgres_api_password \
  postgres_ingest_password \
  postgres_worker_password \
  postgres_erasure_password \
  postgres_backup_password
do
  other_password="$(tr -d '\r\n' < "$HOME_AGENT_SECRETS_DIR/master/$other_name")"
  [ "$binding_committer_password" != "$other_password" ] || {
    echo "binding committer password must be independent from every other database role" >&2
    exit 78
  }
done
unset binding_committer_url other_password other_name

rollout_master="$HOME_AGENT_SECRETS_DIR/master/rollout"
[ -d "$rollout_master" ] && [ ! -L "$rollout_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$rollout_master")" = "0:0:700" ] || {
  echo "rollout master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$rollout_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "rollout master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_rollout_password database_url_rollout; do
  path="$rollout_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "rollout master secret is absent or unsafe: $path" >&2
    exit 78
  }
done
rollout_password="$(tr -d '\r\n' < "$rollout_master/postgres_rollout_password")"
rollout_url="$(tr -d '\r\n' < "$rollout_master/database_url_rollout")"
case "$rollout_password" in
  *[!0-9a-f]*|'') echo "rollout password is not lowercase hex" >&2; exit 78 ;;
esac
[ "${#rollout_password}" -eq 64 ] || {
  echo "rollout password has the wrong length" >&2
  exit 78
}
[ "$rollout_url" = "postgresql+psycopg://home_agent_rollout:${rollout_password}@postgres:5432/home_agent" ] || {
  echo "rollout database URL does not match the isolated rollout role" >&2
  exit 78
}
[ "$binding_operator_password" != "$rollout_password" ] || {
  echo "binding operator password must be independent from every other database role" >&2
  exit 78
}
[ "$binding_committer_password" != "$rollout_password" ] || {
  echo "binding committer password must be independent from every other database role" >&2
  exit 78
}

identity_migration_master="$HOME_AGENT_SECRETS_DIR/master/identity-migration"
[ -d "$identity_migration_master" ] && [ ! -L "$identity_migration_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$identity_migration_master")" = "0:0:700" ] || {
  echo "identity migration master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$identity_migration_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "identity migration master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_identity_migration_password database_url_identity_migration; do
  path="$identity_migration_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "identity migration master secret is absent or unsafe: $path" >&2
    exit 78
  }
done
identity_migration_password="$(tr -d '\r\n' < "$identity_migration_master/postgres_identity_migration_password")"
identity_migration_url="$(tr -d '\r\n' < "$identity_migration_master/database_url_identity_migration")"
case "$identity_migration_password" in
  *[!0-9a-f]*|'') echo "identity migration password is not lowercase hex" >&2; exit 78 ;;
esac
[ "${#identity_migration_password}" -eq 64 ] || {
  echo "identity migration password has the wrong length" >&2
  exit 78
}
[ "$identity_migration_url" = "postgresql+psycopg://home_agent_identity_migration:${identity_migration_password}@postgres:5432/home_agent" ] || {
  echo "identity migration database URL does not match the isolated role" >&2
  exit 78
}
for other_path in \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_owner_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_api_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_ingest_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_worker_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_erasure_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_backup_password" \
  "$binding_operator_master/postgres_binding_operator_password" \
  "$binding_committer_master/postgres_binding_committer_password" \
  "$rollout_master/postgres_rollout_password"
do
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$identity_migration_password" != "$other_password" ] || {
    echo "identity migration password must differ from every database role" >&2
    exit 78
  }
done

identity_finalizer_master="$HOME_AGENT_SECRETS_DIR/master/identity-finalizer"
[ -d "$identity_finalizer_master" ] && [ ! -L "$identity_finalizer_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$identity_finalizer_master")" = "0:0:700" ] || {
  echo "identity finalizer master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$identity_finalizer_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "identity finalizer master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_identity_finalizer_password database_url_identity_finalizer; do
  path="$identity_finalizer_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "identity finalizer master secret is absent or unsafe: $path" >&2
    exit 78
  }
done
identity_finalizer_password="$(tr -d '\r\n' < "$identity_finalizer_master/postgres_identity_finalizer_password")"
identity_finalizer_url="$(tr -d '\r\n' < "$identity_finalizer_master/database_url_identity_finalizer")"
case "$identity_finalizer_password" in
  *[!0-9a-f]*|'') echo "identity finalizer password is not lowercase hex" >&2; exit 78 ;;
esac
[ "${#identity_finalizer_password}" -eq 64 ] || {
  echo "identity finalizer password has the wrong length" >&2
  exit 78
}
[ "$identity_finalizer_url" = "postgresql+psycopg://home_agent_identity_finalizer:${identity_finalizer_password}@postgres:5432/home_agent" ] || {
  echo "identity finalizer database URL does not match the isolated role" >&2
  exit 78
}
for other_path in \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_owner_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_api_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_ingest_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_worker_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_erasure_password" \
  "$HOME_AGENT_SECRETS_DIR/master/postgres_backup_password" \
  "$binding_operator_master/postgres_binding_operator_password" \
  "$binding_committer_master/postgres_binding_committer_password" \
  "$rollout_master/postgres_rollout_password" \
  "$identity_migration_master/postgres_identity_migration_password"
do
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$identity_finalizer_password" != "$other_password" ] || {
    echo "identity finalizer password must differ from every database role" >&2
    exit 78
  }
done

unset binding_operator_password binding_committer_password
unset rollout_password rollout_url
unset identity_migration_password identity_migration_url
unset identity_finalizer_password identity_finalizer_url other_password other_path

deploy_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
printf '%s\n' '{"action":"validate"}' |
  python3 "$deploy_dir/operator/manage_native_installations.py" \
    "$native_installations_master" >/dev/null
sh "$deploy_dir/materialize-secrets.sh" "$HOME_AGENT_SECRETS_DIR"
identity_cutover_master="$HOME_AGENT_SECRETS_DIR/master/identity-cutover"
if [ -e "$identity_cutover_master" ] || [ -L "$identity_cutover_master" ]; then
  sh "$deploy_dir/preflight-identity-cutover-roles.sh" \
    "$HOME_AGENT_SECRETS_DIR"
fi

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
for name in postgres_owner_password postgres_api_password postgres_binding_operator_password postgres_binding_committer_password postgres_identity_migration_password postgres_identity_finalizer_password postgres_ingest_password postgres_worker_password postgres_erasure_password postgres_rollout_password postgres_backup_password; do
  verify_secret 0:0:400 "$HOME_AGENT_SECRETS_DIR/runtime/provision-roles/$name"
done
verify_secret 0:0:400 "$HOME_AGENT_SECRETS_DIR/runtime/grant-runtime/postgres_owner_password"
verify_secret 999:999:400 "$HOME_AGENT_SECRETS_DIR/runtime/backup-gate/postgres_backup_password"
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/migrate/database_url"
for name in database_url operator_database_url binding_commit_database_url knowledge_encryption_key service_token operator_token bootstrap_token; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-api/$name"
done
unexpected_operator_secret="$(find "$HOME_AGENT_SECRETS_DIR/runtime" -mindepth 2 -maxdepth 2 \
  -type f -name operator_database_url \
  ! -path "$HOME_AGENT_SECRETS_DIR/runtime/core-api/operator_database_url" -print -quit)"
[ -z "$unexpected_operator_secret" ] || {
  echo "binding operator database URL was materialized outside core-api: $unexpected_operator_secret" >&2
  exit 78
}
unexpected_committer_secret="$(find "$HOME_AGENT_SECRETS_DIR/runtime" -mindepth 2 -maxdepth 2 \
  -type f -name binding_commit_database_url \
  ! -path "$HOME_AGENT_SECRETS_DIR/runtime/core-api/binding_commit_database_url" -print -quit)"
[ -z "$unexpected_committer_secret" ] || {
  echo "binding committer database URL was materialized outside core-api: $unexpected_committer_secret" >&2
  exit 78
}
for name in database_url runtime_spool_key knowledge_encryption_key edge_token; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-ingest/$name"
done
for name in database_url runtime_spool_key erasure_ledger_key; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/core-worker/$name"
done
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/ledger-init/erasure_ledger_key"
verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/rollout-authorize/database_url"
for name in database_url erasure_ledger_key; do
  verify_secret 10001:10001:400 "$HOME_AGENT_SECRETS_DIR/runtime/restore-replay/$name"
done
verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/service_token"
verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/session_encryption_key"
verify_secret 1000:1000:400 "$HOME_AGENT_SECRETS_DIR/runtime/bff/native_installations"
identity_migration_runtime="$HOME_AGENT_SECRETS_DIR/runtime/identity-migration"
stale_identity_runtime="$(find "$HOME_AGENT_SECRETS_DIR/runtime" \
  -mindepth 1 -maxdepth 1 -type d \
  \( -name '.identity-migration.new.*' -o \
     -name '.identity-migration.previous.*' \) -print -quit)"
[ -z "$stale_identity_runtime" ] || {
  echo "stale identity migration runtime publication exists: $stale_identity_runtime" >&2
  exit 78
}
[ -d "$identity_migration_runtime" ] && [ ! -L "$identity_migration_runtime" ] &&
  [ "$(stat -c '%u:%g:%a' "$identity_migration_runtime")" = "0:0:700" ] || {
  echo "identity migration runtime secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$identity_migration_runtime" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ] &&
  [ -f "$identity_migration_runtime/database_url" ] &&
  [ ! -L "$identity_migration_runtime/database_url" ] || {
  echo "identity migration runtime secret directory must contain only database_url" >&2
  exit 78
}
verify_secret 10001:10001:400 "$identity_migration_runtime/database_url"
cmp -s "$identity_migration_runtime/database_url" \
  "$identity_migration_master/database_url_identity_migration" || {
  echo "identity migration runtime database URL does not match its master" >&2
  exit 78
}

identity_finalizer_runtime="$HOME_AGENT_SECRETS_DIR/runtime/identity-finalizer"
stale_identity_finalizer_runtime="$(find "$HOME_AGENT_SECRETS_DIR/runtime" \
  -mindepth 1 -maxdepth 1 -type d \
  \( -name '.identity-finalizer.new.*' -o \
     -name '.identity-finalizer.previous.*' \) -print -quit)"
[ -z "$stale_identity_finalizer_runtime" ] || {
  echo "stale identity finalizer runtime publication exists: $stale_identity_finalizer_runtime" >&2
  exit 78
}
[ -d "$identity_finalizer_runtime" ] && [ ! -L "$identity_finalizer_runtime" ] &&
  [ "$(stat -c '%u:%g:%a' "$identity_finalizer_runtime")" = "0:0:700" ] || {
  echo "identity finalizer runtime secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$identity_finalizer_runtime" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ] &&
  [ -f "$identity_finalizer_runtime/database_url" ] &&
  [ ! -L "$identity_finalizer_runtime/database_url" ] || {
  echo "identity finalizer runtime secret directory must contain only database_url" >&2
  exit 78
}
verify_secret 10001:10001:400 "$identity_finalizer_runtime/database_url"
cmp -s "$identity_finalizer_runtime/database_url" \
  "$identity_finalizer_master/database_url_identity_finalizer" || {
  echo "identity finalizer runtime database URL does not match its master" >&2
  exit 78
}

verify_root_owned_input() {
  input_path="$1"
  [ -f "$input_path" ] && [ ! -L "$input_path" ] && [ -s "$input_path" ] || {
    echo "operator input must be a non-symlink regular file: $input_path" >&2
    exit 78
  }
  [ "$(stat -c '%u' "$input_path")" = 0 ] && [ "$(stat -c '%h' "$input_path")" = 1 ] || {
    echo "operator input must be root-owned with one hard link: $input_path" >&2
    exit 78
  }
  input_mode="$(stat -c '%a' "$input_path")"
  [ $((0$input_mode & 022)) -eq 0 ] || {
    echo "operator input may not be group/world-writable: $input_path" >&2
    exit 78
  }
  case "$(findmnt -n -o SOURCE -T "$input_path" || true)" in
    /dev/mapper/*) ;;
    *) echo "$input_path is not on a verified /dev/mapper encrypted mount" >&2; exit 78 ;;
  esac
}

for name in server.crt server.key client-ca.crt; do
  path="$HOME_AGENT_TLS_DIR/$name"
  verify_root_owned_input "$path"
  chown root:101 "$path"
  chmod 0640 "$path"
done
config_parent="$(dirname "$HOME_AGENT_PGBACKREST_CONF")"
[ -d "$config_parent" ] && [ ! -L "$config_parent" ] &&
  [ "$(stat -c '%u' "$config_parent")" = 0 ] || {
  echo "pgBackRest config parent must be a root-owned non-symlink directory" >&2
  exit 78
}
config_parent_mode="$(stat -c '%a' "$config_parent")"
[ $((0$config_parent_mode & 022)) -eq 0 ] || {
  echo "pgBackRest config parent may not be group/world-writable" >&2; exit 78;
}
verify_root_owned_input "$HOME_AGENT_PGBACKREST_CONF"
chown root:999 "$HOME_AGENT_PGBACKREST_CONF"
chmod 0640 "$HOME_AGENT_PGBACKREST_CONF"
verify_secret 0:999:640 "$HOME_AGENT_PGBACKREST_CONF"
python3 - "$HOME_AGENT_PGBACKREST_CONF" <<'PY'
import configparser
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if re.search(r"(?m)^[ \t]+[^#; \t]|^[A-Za-z0-9_-]+[ \t]+=", text):
    raise SystemExit(
        "pgBackRest config contains a non-canonical whitespace-prefixed directive"
    )

parser = configparser.ConfigParser(
    interpolation=None,
    strict=True,
    empty_lines_in_values=False,
)
parser.optionxform = str
try:
    parser.read_string(text)
except configparser.Error:
    raise SystemExit("pgBackRest config is not a strict canonical INI file") from None

if parser.defaults() or parser.sections() != ["global", "home-agent"]:
    raise SystemExit("pgBackRest config must contain only [global] and [home-agent]")

global_expected = {
    "repo1-type": "posix",
    "repo1-path": "/repository",
    "repo1-cipher-type": "aes-256-cbc",
    "repo1-cipher-pass": None,
    "repo1-bundle": "y",
    "repo1-retention-full": "4",
    "repo1-retention-diff": "7",
    "repo1-retention-archive-type": "full",
    "archive-async": "y",
    "spool-path": "/var/spool/pgbackrest",
    "process-max": "1",
    "start-fast": "n",
    "log-level-console": "info",
    "log-level-file": "off",
}
stanza_expected = {
    "pg1-path": "/var/lib/postgresql/data",
    "pg1-port": "5432",
    "pg1-user": "home_agent_backup",
    "pg1-database": "home_agent",
}
for section, expected in (("global", global_expected), ("home-agent", stanza_expected)):
    actual = dict(parser.items(section, raw=True))
    if set(actual) != set(expected):
        raise SystemExit(f"pgBackRest [{section}] contains missing or unapproved options")
    for key, value in expected.items():
        if value is not None and actual[key] != value:
            raise SystemExit(f"pgBackRest [{section}] has an invalid {key} setting")

cipher_pass = parser.get("global", "repo1-cipher-pass", raw=True)
if re.fullmatch(r"[0-9a-f]{64}", cipher_pass) is None:
    raise SystemExit("pgBackRest repo1 cipher passphrase must be 256-bit lowercase hex")

PY
verify_secret 999:999:700 "$HOME_AGENT_PGBACKREST_SPOOL_ROOT"
verify_secret 999:999:700 "$HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"
verify_secret 999:999:600 "$HOME_AGENT_PGBACKREST_LOCK_FILE"
verify_secret 999:999:600 "$HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"
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
  --tmpfs /var/cache/nginx:rw,size=16m,mode=0700,uid=101,gid=101 \
  -v "$deploy_dir/nginx-edge.conf:/etc/nginx/nginx.conf:ro" \
  -v "$HOME_AGENT_TLS_DIR/server.crt:/run/secrets/server.crt:ro" \
  -v "$HOME_AGENT_TLS_DIR/server.key:/run/secrets/server.key:ro" \
  -v "$HOME_AGENT_TLS_DIR/client-ca.crt:/run/secrets/client-ca.crt:ro" \
  "${HOME_AGENT_NGINX_IMAGE:-nginxinc/nginx-unprivileged:1.29-alpine}" nginx -t

echo "Home Agent preflight passed; no service was started"
