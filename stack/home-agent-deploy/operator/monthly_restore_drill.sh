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

assert_name_not_quarantined() {
    source_name="$1"
    observed_name="$2"
    normalized_name="$(printf '%s' "$observed_name" | tr '[:upper:]' '[:lower:]')"
    normalized_name="${normalized_name%%.*}"
    case "$normalized_name" in
        engineeredlightingserver1|home-app)
            fail "workload quarantine blocks the 2026-07-12 incident host via $source_name identity"
            ;;
    esac
}

test "$(id -u)" -eq 0 || fail "must run as root"
for command in bash docker flock jq tr uname; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done
assert_name_not_quarantined host "$(uname -n)"

# A daemon-name check covers invocation from a renamed container or namespace
# that is attached to the physical host's local Docker socket. There is no
# environment-variable bypass for either identity boundary.
daemon_name="$(docker info --format '{{.Name}}')" || fail "cannot inspect Docker daemon identity"
test -n "$daemon_name" || fail "Docker returned an empty daemon name"
assert_name_not_quarantined "Docker daemon" "$daemon_name"

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
