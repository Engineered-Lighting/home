#!/bin/sh
# Install the source-projection loader the freeze observer imports.
#
# The observer runs on the Home Assistant host and imports
# migrate_legacy_identity from /config/home-agent-operator, then hashes the
# module's bytes into its observation. Nothing deployed that directory, so the
# freeze step failed on import — with Home Assistant already stopped by the
# preceding step.
#
# The runner refuses to proceed unless the installed copy is byte-identical to
# the pinned operator source, so run this after every source pin that changes
# that module.
set -eu
umask 077

ACTIVATION_ROOT="${ACTIVATION_ROOT:-/opt/home/home-agent-integration-test}"
SOURCE="$ACTIVATION_ROOT/stack/home-agent-deploy/operator/migrate_legacy_identity.py"
HA_HOST="${HA_HOST:-192.168.0.125}"
HA_PORT="${HA_PORT:-22222}"
HA_USER="${HA_USER:-root}"
SSH_HOME="${SSH_HOME:-/home/marcelo-lima}"
REMOTE_ROOT=/config/home-agent-operator
REMOTE_MODULE="$REMOTE_ROOT/migrate_legacy_identity.py"

[ -f "$SOURCE" ] || {
  echo "operator source module is missing: $SOURCE" >&2
  exit 78
}

ssh_common="-p $HA_PORT -i $SSH_HOME/.ssh/id_ed25519
  -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$SSH_HOME/.ssh/known_hosts"

# shellcheck disable=SC2086
ssh $ssh_common "$HA_USER@$HA_HOST" "mkdir -p $REMOTE_ROOT && chmod 0700 $REMOTE_ROOT"

# shellcheck disable=SC2086
scp -P "$HA_PORT" -i "$SSH_HOME/.ssh/id_ed25519" \
  -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$SSH_HOME/.ssh/known_hosts" \
  "$SOURCE" "$HA_USER@$HA_HOST:$REMOTE_MODULE"

expected="$(sha256sum "$SOURCE" | cut -d' ' -f1)"
# shellcheck disable=SC2086
actual="$(ssh $ssh_common "$HA_USER@$HA_HOST" "sha256sum $REMOTE_MODULE" | cut -d' ' -f1)"

if [ "$expected" != "$actual" ]; then
  echo "installed operator module does not match the pinned source" >&2
  exit 78
fi

# shellcheck disable=SC2086
ssh $ssh_common "$HA_USER@$HA_HOST" "chmod 0600 $REMOTE_MODULE"
echo "operator module installed and verified: $actual"
