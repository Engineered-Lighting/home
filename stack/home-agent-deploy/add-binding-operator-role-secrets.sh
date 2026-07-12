#!/bin/sh
set -eu
umask 077

secrets_root="${1:?usage: add-binding-operator-role-secrets.sh <secrets-root>}"
master_root="$secrets_root/master"
target="$master_root/binding-operator"
temporary="$master_root/.binding-operator.new.$$"

[ "$(id -u)" -eq 0 ] || {
  echo "add-binding-operator-role-secrets.sh must run as root" >&2
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

# Reject both a previously published pair and every partial/legacy layout. The
# pair is represented by one directory so publication can be a single atomic
# rename on the master-secret filesystem.
for path in \
  "$target" \
  "$master_root/postgres_binding_operator_password" \
  "$master_root/database_url_binding_operator"
do
  [ ! -e "$path" ] && [ ! -L "$path" ] || {
    echo "binding operator secret set already exists or is partial; refusing to overwrite" >&2
    exit 73
  }
done
[ ! -e "$temporary" ] && [ ! -L "$temporary" ] || {
  echo "temporary binding operator secret path already exists" >&2
  exit 73
}

cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT HUP INT TERM

install -d -m 0700 -o root -g root "$temporary"
password="$(openssl rand -hex 32)"
case "$password" in
  *[!0-9a-f]*|'') echo "generated binding operator password is invalid" >&2; exit 70 ;;
esac
[ "${#password}" -eq 64 ] || {
  echo "generated binding operator password has the wrong length" >&2
  exit 70
}
for other_path in \
  "$master_root"/postgres_*_password \
  "$master_root/rollout/postgres_rollout_password"
do
  [ -f "$other_path" ] || continue
  other_password="$(tr -d '\r\n' < "$other_path")"
  [ "$password" != "$other_password" ] || {
    echo "generated binding operator password collided with another database role" >&2
    exit 70
  }
done
unset other_password other_path

printf '%s\n' "$password" > "$temporary/postgres_binding_operator_password"
printf 'postgresql+psycopg://home_agent_binding_operator:%s@postgres:5432/home_agent\n' \
  "$password" > "$temporary/database_url_binding_operator"
chmod 0600 "$temporary/postgres_binding_operator_password" \
  "$temporary/database_url_binding_operator"
chown root:root "$temporary/postgres_binding_operator_password" \
  "$temporary/database_url_binding_operator"
sync -f "$temporary/postgres_binding_operator_password"
sync -f "$temporary/database_url_binding_operator"
sync -f "$temporary"
mv -T --no-clobber "$temporary" "$target"
[ -d "$target" ] && [ ! -e "$temporary" ] || {
  echo "binding operator secret publication raced or failed" >&2
  exit 73
}
sync -f "$master_root"
trap - EXIT HUP INT TERM
unset password

echo "created the isolated binding operator role secret set"
