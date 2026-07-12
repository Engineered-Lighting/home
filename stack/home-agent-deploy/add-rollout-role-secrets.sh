#!/bin/sh
set -eu
umask 077

secrets_root="${1:?usage: add-rollout-role-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
target="$master_root/rollout"
temporary="$master_root/.rollout.new.$$"
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || {
  echo "add-rollout-role-secrets.sh must run as root" >&2
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
[ ! -e "$target" ] && [ ! -L "$target" ] || {
  echo "rollout master secret set already exists; refusing to overwrite" >&2
  exit 73
}
[ ! -e "$temporary" ] && [ ! -L "$temporary" ] || {
  echo "temporary rollout secret path already exists" >&2
  exit 73
}

cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT HUP INT TERM

install -d -m 0700 -o root -g root "$temporary"
password="$(openssl rand -hex 32)"
case "$password" in
  *[!0-9a-f]*|'') echo "generated rollout password is invalid" >&2; exit 70 ;;
esac
[ "${#password}" -eq 64 ] || {
  echo "generated rollout password has the wrong length" >&2
  exit 70
}
printf '%s\n' "$password" > "$temporary/postgres_rollout_password"
printf 'postgresql+psycopg://home_agent_rollout:%s@postgres:5432/home_agent\n' \
  "$password" > "$temporary/database_url_rollout"
chmod 0600 "$temporary/postgres_rollout_password" \
  "$temporary/database_url_rollout"
chown root:root "$temporary/postgres_rollout_password" \
  "$temporary/database_url_rollout"
sync -f "$temporary/postgres_rollout_password"
sync -f "$temporary/database_url_rollout"
sync -f "$temporary"
mv -T --no-clobber "$temporary" "$target"
[ -d "$target" ] && [ ! -e "$temporary" ] || {
  echo "rollout secret set publication raced or failed" >&2
  exit 73
}
sync -f "$master_root"
trap - EXIT HUP INT TERM
unset password

sh "$script_dir/materialize-secrets.sh" "$secrets_root"
echo "created and materialized the isolated rollout role secret set"
