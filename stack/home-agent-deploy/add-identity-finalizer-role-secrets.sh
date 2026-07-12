#!/bin/sh
set -eu
umask 077

secrets_root="${1:?usage: add-identity-finalizer-role-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
target="$master_root/identity-finalizer"
temporary="$master_root/.identity-finalizer.new.$$"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || {
  echo "add-identity-finalizer-role-secrets.sh must run as root" >&2
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
  "$master_root/postgres_identity_finalizer_password" \
  "$master_root/database_url_identity_finalizer"
do
  [ ! -e "$path" ] && [ ! -L "$path" ] || {
    echo "identity finalizer secret set already exists or is partial; refusing to overwrite" >&2
    exit 73
  }
done
[ ! -e "$temporary" ] && [ ! -L "$temporary" ] || {
  echo "temporary identity finalizer secret path already exists" >&2
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
  *[!0-9a-f]*|'') echo "generated identity finalizer password is invalid" >&2; exit 70 ;;
esac
[ "${#password}" -eq 64 ] || {
  echo "generated identity finalizer password has the wrong length" >&2
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
  "$master_root/rollout/postgres_rollout_password" \
  "$master_root/identity-migration/postgres_identity_migration_password"
do
  [ -f "$other_path" ] && [ ! -L "$other_path" ] || {
    echo "existing database role secret is absent or unsafe: $other_path" >&2
    exit 78
  }
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$password" != "$other_password" ] || {
    echo "identity finalizer password must differ from every database role" >&2
    exit 70
  }
done
unset other_password other_path

printf '%s\n' "$password" > "$temporary/postgres_identity_finalizer_password"
printf 'postgresql+psycopg://home_agent_identity_finalizer:%s@postgres:5432/home_agent\n' \
  "$password" > "$temporary/database_url_identity_finalizer"
chmod 0600 "$temporary/postgres_identity_finalizer_password" \
  "$temporary/database_url_identity_finalizer"
chown root:root "$temporary/postgres_identity_finalizer_password" \
  "$temporary/database_url_identity_finalizer"
sync -f "$temporary/postgres_identity_finalizer_password"
sync -f "$temporary/database_url_identity_finalizer"
sync -f "$temporary"
mv -T --no-clobber "$temporary" "$target"
[ -d "$target" ] && [ ! -e "$temporary" ] || {
  echo "identity finalizer secret set publication raced or failed" >&2
  exit 73
}
sync -f "$master_root"
trap - EXIT HUP INT TERM
unset password

sh "$script_dir/materialize-secrets.sh" "$secrets_root"
echo "created the isolated identity finalizer role secret set"
