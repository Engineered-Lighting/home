#!/bin/sh
set -eu

secrets_root="${1:?usage: preflight-identity-cutover-roles.sh <secrets-root>}"
master_root="$secrets_root/master"
cutover_master="$master_root/identity-cutover"
runtime_root="$secrets_root/runtime"

[ "$(id -u)" -eq 0 ] || {
  echo "preflight-identity-cutover-roles.sh must run as root" >&2
  exit 77
}
case "$secrets_root" in
  /*) ;;
  *) echo "secrets root must be absolute" >&2; exit 78 ;;
esac
[ -d "$secrets_root" ] && [ ! -L "$secrets_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$secrets_root")" = "0:0:700" ] || {
  echo "secret root is absent or unsafe" >&2
  exit 78
}
[ -d "$master_root" ] && [ ! -L "$master_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$master_root")" = "0:0:700" ] || {
  echo "master secret directory is absent or unsafe" >&2
  exit 78
}
[ -d "$runtime_root" ] && [ ! -L "$runtime_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$runtime_root")" = "0:0:700" ] || {
  echo "runtime secret directory is absent or unsafe" >&2
  exit 78
}

stale_cutover_master="$(
  find "$master_root" -mindepth 1 -maxdepth 1 \
    \( -name '.identity-cutover.new.*' \
       -o -name '.identity-cutover.previous.*' \) \
    -print -quit
)"
[ -z "$stale_cutover_master" ] || {
  echo "stale identity cutover master publication exists" >&2
  exit 73
}

if [ ! -e "$cutover_master" ] && [ ! -L "$cutover_master" ]; then
  orphaned_cutover_runtime="$(
    find "$runtime_root" -mindepth 1 -maxdepth 1 \
      \( -name 'identity-cutover' \
         -o -name 'provision-identity-cutover-roles' \
         -o -name '.identity-cutover.new.*' \
         -o -name '.identity-cutover.previous.*' \
         -o -name '.provision-identity-cutover-roles.new.*' \
         -o -name '.provision-identity-cutover-roles.previous.*' \) \
      -print -quit
  )"
  [ -z "$orphaned_cutover_runtime" ] || {
    echo "orphaned identity cutover runtime exists without its master" >&2
    exit 78
  }
  echo "identity cutover role-secret preflight passed (not installed)"
  exit 0
fi
unset orphaned_cutover_runtime stale_cutover_master

[ -d "$cutover_master" ] && [ ! -L "$cutover_master" ] &&
  [ "$(stat -c '%u:%g:%a' "$cutover_master")" = "0:0:700" ] || {
  echo "identity cutover master secret directory is absent or unsafe" >&2
  exit 78
}
[ "$(find "$cutover_master" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "identity cutover master secret directory must contain exactly two files" >&2
  exit 78
}
for name in postgres_identity_cutover_password database_url_identity_cutover; do
  path="$cutover_master/$name"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] &&
    [ "$(stat -c '%u:%g:%a' "$path")" = "0:0:600" ] || {
    echo "identity cutover master secret is absent or unsafe: $path" >&2
    exit 78
  }
done

cutover_password="$(
  tr -d '\r\n' < "$cutover_master/postgres_identity_cutover_password"
)"
cutover_url="$(
  tr -d '\r\n' < "$cutover_master/database_url_identity_cutover"
)"
case "$cutover_password" in
  *[!0-9a-f]*|'')
    echo "identity cutover password is not lowercase hex" >&2
    exit 78
    ;;
esac
[ "${#cutover_password}" -eq 64 ] || {
  echo "identity cutover password has the wrong length" >&2
  exit 78
}
[ "$(wc -c < "$cutover_master/postgres_identity_cutover_password")" -eq 65 ] &&
  [ "$(wc -l < "$cutover_master/postgres_identity_cutover_password")" -eq 1 ] &&
  [ "$(tr -cd '\r' < "$cutover_master/postgres_identity_cutover_password" | wc -c)" -eq 0 ] &&
  [ -z "$(tail -c 1 "$cutover_master/postgres_identity_cutover_password")" ] || {
  echo "identity cutover password file is not canonical" >&2
  exit 78
}
[ "$cutover_url" = "postgresql+psycopg://home_agent_identity_cutover:${cutover_password}@postgres:5432/home_agent" ] || {
  echo "identity cutover database URL does not match the isolated role" >&2
  exit 78
}
expected_url_size=$((
  ${#cutover_url} + 1
))
[ "$(wc -c < "$cutover_master/database_url_identity_cutover")" -eq "$expected_url_size" ] &&
  [ "$(wc -l < "$cutover_master/database_url_identity_cutover")" -eq 1 ] &&
  [ "$(tr -cd '\r' < "$cutover_master/database_url_identity_cutover" | wc -c)" -eq 0 ] &&
  [ -z "$(tail -c 1 "$cutover_master/database_url_identity_cutover")" ] || {
  echo "identity cutover database URL file is not canonical" >&2
  exit 78
}
unset expected_url_size

for other_path in \
  "$master_root/postgres_owner_password" \
  "$master_root/postgres_api_password" \
  "$master_root/postgres_ingest_password" \
  "$master_root/postgres_worker_password" \
  "$master_root/postgres_erasure_password" \
  "$master_root/postgres_backup_password" \
  "$master_root/binding-operator/postgres_binding_operator_password" \
  "$master_root/rollout/postgres_rollout_password" \
  "$master_root/identity-migration/postgres_identity_migration_password" \
  "$master_root/identity-finalizer/postgres_identity_finalizer_password"
do
  [ -f "$other_path" ] && [ ! -L "$other_path" ] || {
    echo "existing database role secret is absent or unsafe: $other_path" >&2
    exit 78
  }
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$cutover_password" != "$other_password" ] || {
    echo "identity cutover password must differ from every database role" >&2
    exit 78
  }
done
unset other_password other_path

verify_runtime_file() {
  expected="$1"
  path="$2"
  [ -f "$path" ] && [ ! -L "$path" ] && [ -s "$path" ] || {
    echo "identity cutover runtime secret is absent or unsafe: $path" >&2
    exit 78
  }
  actual="$(stat -c '%u:%g:%a' "$path")"
  [ "$actual" = "$expected" ] || {
    echo "incorrect identity cutover runtime ownership/mode ($actual): $path" >&2
    exit 78
  }
}

for service in provision-identity-cutover-roles identity-cutover; do
  stale_runtime="$(find "$runtime_root" -mindepth 1 -maxdepth 1 \
    \( -name ".$service.new.*" -o -name ".$service.previous.*" \) \
    -print -quit)"
  [ -z "$stale_runtime" ] || {
    echo "stale identity cutover runtime publication exists: $stale_runtime" >&2
    exit 78
  }
  service_dir="$runtime_root/$service"
  [ -d "$service_dir" ] && [ ! -L "$service_dir" ] &&
    [ "$(stat -c '%u:%g:%a' "$service_dir")" = "0:0:700" ] || {
    echo "identity cutover runtime directory is absent or unsafe: $service_dir" >&2
    exit 78
  }
done

provision_runtime="$runtime_root/provision-identity-cutover-roles"
[ "$(find "$provision_runtime" -mindepth 1 -maxdepth 1 | wc -l)" -eq 2 ] || {
  echo "identity cutover role provisioner must receive exactly two secrets" >&2
  exit 78
}
verify_runtime_file 0:0:400 "$provision_runtime/postgres_owner_password"
verify_runtime_file 0:0:400 \
  "$provision_runtime/postgres_identity_cutover_password"
cmp -s "$provision_runtime/postgres_owner_password" \
  "$master_root/postgres_owner_password" || {
  echo "identity cutover role provisioner owner secret does not match master" >&2
  exit 78
}
cmp -s "$provision_runtime/postgres_identity_cutover_password" \
  "$cutover_master/postgres_identity_cutover_password" || {
  echo "identity cutover role provisioner password does not match master" >&2
  exit 78
}

cutover_runtime="$runtime_root/identity-cutover"
[ "$(find "$cutover_runtime" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ] &&
  [ -f "$cutover_runtime/database_url" ] &&
  [ ! -L "$cutover_runtime/database_url" ] || {
  echo "identity cutover runtime directory must contain only database_url" >&2
  exit 78
}
verify_runtime_file 10001:10001:400 "$cutover_runtime/database_url"
cmp -s "$cutover_runtime/database_url" \
  "$cutover_master/database_url_identity_cutover" || {
  echo "identity cutover runtime database URL does not match master" >&2
  exit 78
}

[ ! -e "$runtime_root/provision-roles/postgres_identity_cutover_password" ] &&
  [ ! -L "$runtime_root/provision-roles/postgres_identity_cutover_password" ] || {
  echo "historical role provisioner must not receive the E4 credential" >&2
  exit 78
}

unset cutover_password cutover_url
echo "identity cutover role-secret preflight passed"
