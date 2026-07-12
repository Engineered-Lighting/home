#!/bin/sh
set -eu
umask 077

secrets_root="${1:?usage: add-identity-migration-role-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
target="$master_root/identity-migration"
temporary="$master_root/.identity-migration.new.$$"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || {
  echo "add-identity-migration-role-secrets.sh must run as root" >&2
  exit 77
}
case "$secrets_root" in
  /*) ;;
  *) echo "secrets root must be absolute" >&2; exit 78 ;;
esac
[ -d "$master_root" ] && [ ! -L "$master_root" ] &&
  [ "$(stat -c '%u:%g:%a' "$master_root")" = "0:0:700" ] || {
  echo "existing master secret directory is absent or unsafe" >&2
  exit 78
}

for path in \
  "$target" \
  "$master_root/postgres_identity_migration_password" \
  "$master_root/database_url_identity_migration"
do
  [ ! -e "$path" ] && [ ! -L "$path" ] || {
    echo "identity migration secret set already exists or is partial; refusing to overwrite" >&2
    exit 73
  }
done
[ ! -e "$temporary" ] && [ ! -L "$temporary" ] || {
  echo "temporary identity migration secret path already exists" >&2
  exit 73
}

cleanup() {
  if [ -d "$temporary" ] && [ ! -L "$temporary" ]; then
    find "$temporary" -mindepth 1 -maxdepth 1 -type f -delete
    rmdir "$temporary" 2>/dev/null || true
  fi
}
trap cleanup EXIT HUP INT TERM

install -d -m 0700 -o root -g root "$temporary"
password="$(openssl rand -hex 32)"
case "$password" in
  *[!0-9a-f]*|'') echo "generated identity migration password is invalid" >&2; exit 70 ;;
esac
[ "${#password}" -eq 64 ] || {
  echo "generated identity migration password has the wrong length" >&2
  exit 70
}

for other_path in \
  "$master_root/postgres_owner_password" \
  "$master_root/postgres_api_password" \
  "$master_root/postgres_ingest_password" \
  "$master_root/postgres_worker_password" \
  "$master_root/postgres_erasure_password" \
  "$master_root/postgres_backup_password" \
  "$master_root/binding-operator/postgres_binding_operator_password" \
  "$master_root/rollout/postgres_rollout_password"
do
  [ -f "$other_path" ] && [ ! -L "$other_path" ] || {
    echo "existing database role secret is absent or unsafe: $other_path" >&2
    exit 78
  }
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$password" != "$other_password" ] || {
    echo "identity migration password must differ from every database role" >&2
    exit 70
  }
done
unset other_password other_path

printf '%s\n' "$password" > "$temporary/postgres_identity_migration_password"
printf 'postgresql+psycopg://home_agent_identity_migration:%s@postgres:5432/home_agent\n' \
  "$password" > "$temporary/database_url_identity_migration"
chmod 0600 "$temporary/postgres_identity_migration_password" \
  "$temporary/database_url_identity_migration"
chown root:root "$temporary/postgres_identity_migration_password" \
  "$temporary/database_url_identity_migration"
sync -f "$temporary/postgres_identity_migration_password"
sync -f "$temporary/database_url_identity_migration"
sync -f "$temporary"
mv -T --no-clobber "$temporary" "$target"
[ -d "$target" ] && [ ! -e "$temporary" ] || {
  echo "identity migration secret set publication raced or failed" >&2
  exit 73
}
sync -f "$master_root"
trap - EXIT HUP INT TERM
unset password

sh "$script_dir/materialize-secrets.sh" "$secrets_root"
echo "created the isolated identity migration role secret set"
