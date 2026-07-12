#!/bin/sh
set -eu
umask 077

env_file="${1:-/srv/home-agent/config/home-agent.env}"
checkout="${HOME_AGENT_CHECKOUT:-/opt/home/home-github}"
postgres_container="${HOME_AGENT_POSTGRES_CONTAINER:-home-agent-postgres-1}"
operator="$checkout/stack/home-agent-deploy/operator/isolated_restore_drill.sh"

fail() {
    echo "monthly restore drill failed: $*" >&2
    exit 65
}

test "$(id -u)" -eq 0 || fail "must run as root"
for command in bash docker flock jq; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done
test -f "$env_file" && test ! -L "$env_file" || fail "unsafe environment file"
test -x "$operator" && test ! -L "$operator" || fail "restore operator is unavailable"

exec 9>/run/lock/home-agent-monthly-restore-drill.lock
flock -n 9 || fail "another monthly restore selector is already running"

label="$(
    docker exec "$postgres_container" \
        pgbackrest --stanza=home-agent --output=json info |
    jq -er '
      .[0].backup
      | map(select(.error == false and .type == "full"))
      | sort_by(.timestamp.stop)
      | last
      | .label
    '
)" || fail "cannot select a completed full backup"

case "$label" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]F) ;;
    *) fail "pgBackRest returned an invalid full-backup label" ;;
esac

echo "monthly restore drill selected completed backup $label"
exec bash "$operator" "$env_file" "$label"
