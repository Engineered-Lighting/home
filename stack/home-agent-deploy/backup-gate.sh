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

lock_wait_seconds="${HOME_AGENT_BACKUP_LOCK_WAIT_SECONDS:-300}"
case "$lock_wait_seconds" in
  *[!0-9]*|'') echo "backup lock wait must be a positive integer" >&2; exit 78 ;;
esac
[ "$lock_wait_seconds" -gt 0 ] && [ "$lock_wait_seconds" -le 3600 ] || {
  echo "backup lock wait must be between 1 and 3600 seconds" >&2
  exit 78
}

exec 7>>/run/home-agent-locks/backup.lock
flock -w "$lock_wait_seconds" -x 7 || {
  echo "timed out waiting for the backup serialization lock" >&2
  exit 75
}
exec 8>>/run/home-agent-locks/repository.lock
flock -w "$lock_wait_seconds" -s 8 || {
  echo "timed out waiting for the repository coordination lock" >&2
  exit 75
}

# This authenticated query is intentional: pg_isready alone does not prove
# that the dedicated role/password boundary is working.
psql -X -v ON_ERROR_STOP=1 \
  -h /var/run/postgresql -U home_agent_backup -d home_agent \
  -c 'SELECT 1' >/dev/null

minimum_free_kib="${HOME_AGENT_BACKUP_MIN_FREE_KIB:-2097152}"
case "$minimum_free_kib" in
  *[!0-9]*|'') echo "backup free-space floor must be a positive integer" >&2; exit 78 ;;
esac
[ "$minimum_free_kib" -ge 2097152 ] || {
  echo "backup free-space floor may not be lower than 2097152 KiB" >&2
  exit 78
}
data_kib="$(du -sk /var/lib/postgresql/data | awk '{print $1}')"
filesystem_kib="$(df -Pk /repository | awk 'NR == 2 {print $2}')"
available_kib="$(df -Pk /repository | awk 'NR == 2 {print $4}')"
case "$data_kib:$filesystem_kib:$available_kib" in
  *[!0-9:]*|:*|*::*|*:) echo "cannot calculate backup capacity" >&2; exit 78 ;;
esac
ten_percent_kib=$(((filesystem_kib + 9) / 10))
reserve_kib="$minimum_free_kib"
[ "$reserve_kib" -ge "$ten_percent_kib" ] || reserve_kib="$ten_percent_kib"
required_kib=$((reserve_kib + (data_kib * 3)))
[ "$available_kib" -ge "$required_kib" ] || {
  echo "local backup repository cannot preserve the required free-space reserve" >&2
  exit 75
}

backup_type="${HOME_AGENT_BACKUP_TYPE:-full}"
case "$backup_type" in
  full|diff) ;;
  *) echo "backup type must be full or diff" >&2; exit 78 ;;
esac

# Preflight permits exactly one configured repository. pgBackRest 2.58 does not
# accept --repo for stanza-create/check; with repo2+ rejected, both commands are
# still limited to canonical repo1. Backup does accept and pin the selector.
pgbackrest --stanza=home-agent stanza-create
pgbackrest --stanza=home-agent check
pgbackrest --repo=1 --stanza=home-agent --type="$backup_type" backup
