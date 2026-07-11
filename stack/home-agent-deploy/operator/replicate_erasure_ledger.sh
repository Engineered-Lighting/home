#!/bin/sh
set -eu
umask 077

base="${HOME_AGENT_ERASURE_LEDGER_ROOT:-/srv/home-agent/erasure-ledger}"
config="${HOME_AGENT_RCLONE_CONFIG:?set HOME_AGENT_RCLONE_CONFIG}"
remote_root="${HOME_AGENT_ERASURE_LEDGER_REMOTE:?set HOME_AGENT_ERASURE_LEDGER_REMOTE}"
expected_mapper="${HOME_AGENT_EXPECTED_MAPPER:-/dev/mapper/home-agent}"
stage_root="${HOME_AGENT_STAGE_ROOT:-/srv/home-agent/ledger-replication}"

fail() {
    echo "erasure-ledger replication failed: $*" >&2
    exit 65
}

require_expected_mapper() {
    path="$1"
    source="$(findmnt -n -o SOURCE -T "$path")"
    case "$source" in
        "$expected_mapper"|"$expected_mapper"[[]*) ;;
        *) fail "$path is not on the expected encrypted mapper" ;;
    esac
}

for command in awk chmod cmp cp findmnt flock grep md5sum mktemp python3 \
    rclone rm sha256sum sync; do
    command -v "$command" >/dev/null 2>&1 || fail "missing command: $command"
done

test -f "$config" && test -r "$config" || fail "rclone configuration is unreadable"
test -s "$base/ledger.head.json" || fail "ledger head is missing"
test -e "$base/ledger.jsonl" || fail "ledger is missing"
require_expected_mapper "$config"
require_expected_mapper "$base"
require_expected_mapper "$stage_root"

exec 9>/run/lock/home-agent-ledger-backup.lock
flock -n 9 || exit 0

stage="$(mktemp -d "$stage_root/.ledger-backup.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT HUP INT TERM
chmod 0700 "$stage"

# Core holds this lock while it advances the append-only ledger and head.
exec 8>"$base/ledger.jsonl.lock"
flock -x 8
cp "$base/ledger.jsonl" "$stage/ledger.jsonl"
cp "$base/ledger.head.json" "$stage/ledger.head.json"
sync -f "$stage/ledger.jsonl"
sync -f "$stage/ledger.head.json"
flock -u 8

set -- $(python3 - "$stage/ledger.head.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    head = json.load(stream)
epoch = head.get("epoch")
digest = head.get("head_hash")
if not isinstance(epoch, int) or epoch < 0:
    raise SystemExit("invalid ledger epoch")
if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit("invalid ledger head hash")
print(epoch, digest)
PY
)
epoch="$1"
head_hash="$2"
version="$(printf '%020d-%s' "$epoch" "$head_hash")"
remote="$remote_root/epochs/$version"

(
    cd "$stage"
    sha256sum ledger.jsonl ledger.head.json > complete.sha256
)

# Fast path for the normal case: `current` is published only after an
# immutable epoch has its verified commit marker. Matching that marker means
# this exact ledger head is already durably represented off-host.
if rclone --config "$config" cat "$remote_root/current/complete.sha256" \
    > "$stage/current-fast-complete.sha256" 2>/dev/null; then
    if cmp -s "$stage/complete.sha256" "$stage/current-fast-complete.sha256"; then
        exit 0
    fi
fi

rclone --config "$config" mkdir "$remote_root/epochs"
rclone --config "$config" mkdir "$remote_root/current"
rclone --config "$config" lsf "$remote_root/epochs" --dirs-only > "$stage/epoch-dirs"

epoch_complete=false
if grep -Fxq "$version/" "$stage/epoch-dirs"; then
    rclone --config "$config" lsf "$remote" --files-only > "$stage/epoch-files"
    if grep -Fxq complete.sha256 "$stage/epoch-files"; then
        rclone --config "$config" cat "$remote/complete.sha256" \
            > "$stage/remote-complete.sha256"
        cmp -s "$stage/complete.sha256" "$stage/remote-complete.sha256" \
            || fail "immutable epoch marker collision"
        epoch_complete=true
    fi
else
    : > "$stage/epoch-files"
fi

if test "$epoch_complete" = false; then
    # A crash may leave data objects without the commit marker. Reuse an
    # identical object, reject a collision, and publish the marker last.
    for name in ledger.jsonl ledger.head.json; do
        local_md5="$(md5sum "$stage/$name" | awk '{print $1}')"
        if grep -Fxq "$name" "$stage/epoch-files"; then
            set -- $(rclone --config "$config" md5sum "$remote/$name")
            test "$#" -ge 1 && test "$1" = "$local_md5" \
                || fail "immutable epoch object collision: $name"
        else
            rclone --config "$config" copyto \
                "$stage/$name" "$remote/$name" --immutable
            set -- $(rclone --config "$config" md5sum "$remote/$name")
            test "$#" -ge 1 && test "$1" = "$local_md5" \
                || fail "remote verification failed: $name"
        fi
    done

    rclone --config "$config" copyto \
        "$stage/complete.sha256" "$remote/complete.sha256" --immutable
    rclone --config "$config" cat "$remote/complete.sha256" \
        > "$stage/remote-complete.sha256"
    cmp -s "$stage/complete.sha256" "$stage/remote-complete.sha256" \
        || fail "remote commit-marker verification failed"
fi

# `current` is only a repairable convenience view. The immutable epoch above
# is authoritative and its marker was verified before this view is touched.
for name in ledger.jsonl ledger.head.json complete.sha256; do
    rclone --config "$config" copyto \
        "$stage/$name" "$remote_root/current/$name" --checksum
done
rclone --config "$config" cat "$remote_root/current/complete.sha256" \
    > "$stage/current-complete.sha256"
cmp -s "$stage/complete.sha256" "$stage/current-complete.sha256" \
    || fail "current commit-marker verification failed"
