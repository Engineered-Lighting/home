#!/bin/sh
# Install every module step 20 executes on the Home Assistant host.
#
# Step 19 stops Home Assistant and step 20 runs the freeze and its observation
# on the HA host. Three files have to be there and have to be byte-identical to
# the pinned source:
#
#   - migrate_legacy_identity.py, which the observer imports and whose bytes it
#     hashes into the observation, so a stale copy describes code that did not
#     run;
#   - freeze_legacy_identity_semantics.py, the freeze itself;
#   - collect_legacy_identity_freeze_observation.py, the observer.
#
# Originally this installed only the first. That was enough to notice the
# directory was missing entirely, and not enough to notice the observer had
# gone stale: it sat three revisions behind its pinned source -- still shelling
# out to `ha core info` for a run-state key this deployment does not return --
# while a readiness audit recorded it as "present". Presence is exactly what a
# stale copy also satisfies.
#
# The runner refuses to proceed unless every installed copy matches, so run
# this after any source pin that changes one of them.
set -eu
umask 077

ACTIVATION_ROOT="${ACTIVATION_ROOT:-/opt/home/home-agent-integration-test}"
HA_HOST="${HA_HOST:-192.168.0.125}"
HA_PORT="${HA_PORT:-22222}"
HA_USER="${HA_USER:-root}"
SSH_HOME="${SSH_HOME:-/home/marcelo-lima}"
REMOTE_OPERATOR_ROOT=/config/home-agent-operator
REMOTE_EOC_ROOT=/config/extended_openai_conversation

ssh_common="-p $HA_PORT -i $SSH_HOME/.ssh/id_ed25519
  -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$SSH_HOME/.ssh/known_hosts"

# shellcheck disable=SC2086
ssh $ssh_common "$HA_USER@$HA_HOST" \
  "mkdir -p $REMOTE_OPERATOR_ROOT && chmod 0700 $REMOTE_OPERATOR_ROOT"

install_module() {
  source_path="$1"
  remote_path="$2"

  [ -f "$source_path" ] || {
    echo "source module is missing: $source_path" >&2
    exit 78
  }

  # shellcheck disable=SC2086
  scp -P "$HA_PORT" -i "$SSH_HOME/.ssh/id_ed25519" \
    -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$SSH_HOME/.ssh/known_hosts" \
    "$source_path" "$HA_USER@$HA_HOST:$remote_path"

  expected="$(sha256sum "$source_path" | cut -d' ' -f1)"
  # shellcheck disable=SC2086
  actual="$(ssh $ssh_common "$HA_USER@$HA_HOST" "sha256sum $remote_path" |
    cut -d' ' -f1)"

  if [ "$expected" != "$actual" ]; then
    echo "installed module does not match the pinned source: $remote_path" >&2
    exit 78
  fi

  # shellcheck disable=SC2086
  ssh $ssh_common "$HA_USER@$HA_HOST" "chmod 0600 $remote_path"
  echo "installed and verified: $remote_path $actual"
}

install_module \
  "$ACTIVATION_ROOT/stack/home-agent-deploy/operator/migrate_legacy_identity.py" \
  "$REMOTE_OPERATOR_ROOT/migrate_legacy_identity.py"

install_module \
  "$ACTIVATION_ROOT/ha-config/extended_openai_conversation/freeze_legacy_identity_semantics.py" \
  "$REMOTE_EOC_ROOT/freeze_legacy_identity_semantics.py"

install_module \
  "$ACTIVATION_ROOT/ha-config/extended_openai_conversation/collect_legacy_identity_freeze_observation.py" \
  "$REMOTE_EOC_ROOT/collect_legacy_identity_freeze_observation.py"
