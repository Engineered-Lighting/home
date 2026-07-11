#!/bin/sh
set -eu
umask 077

password_file="${POSTGRES_BACKUP_PASSWORD_FILE:?missing POSTGRES_BACKUP_PASSWORD_FILE}"
[ -f "$password_file" ] && [ -r "$password_file" ] || {
  echo "backup database credential is missing or unreadable" >&2
  exit 78
}

backup_password="$(tr -d '\r\n' < "$password_file")"
[ "${#backup_password}" -eq 64 ] || {
  echo "backup database credential has an invalid length" >&2
  exit 78
}
# bootstrap-secrets.sh emits hexadecimal passwords. Reject pgpass metacharacters
# and whitespace so a hand-supplied replacement cannot alter the credential row.
case "$backup_password" in
  *:*|*\\*|*[!0-9a-f]*)
    echo "backup database credential has an invalid format" >&2
    exit 78
    ;;
esac

pgpass_file=/tmp/home-agent-backup.pgpass
printf '*:*:*:home_agent_backup:%s\n' "$backup_password" > "$pgpass_file"
unset backup_password
chmod 0600 "$pgpass_file"
export PGPASSFILE="$pgpass_file"
trap 'rm -f "$pgpass_file"' EXIT HUP INT TERM

# This authenticated query is intentional: pg_isready alone does not prove
# that the dedicated role/password boundary is working.
psql -X -v ON_ERROR_STOP=1 \
  -h /var/run/postgresql -U home_agent_backup -d home_agent \
  -c 'SELECT 1' >/dev/null
pgbackrest --stanza=home-agent stanza-create
pgbackrest --stanza=home-agent check
pgbackrest --stanza=home-agent --type=full backup
