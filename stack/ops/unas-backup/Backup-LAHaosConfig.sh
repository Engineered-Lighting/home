#!/usr/bin/env bash
set -euo pipefail
umask 077

[[ $(hostname) == EngineeredLightingServer1 && $(id -u) == 0 ]]
mountpoint -q /srv/home-agent
source=/srv/home-agent/ha-precious
destination=rclone:unas:LinuxBackup/engineered-lighting-server1
state=/var/lib/unas-la-haos-config
credentials=${CREDENTIALS_DIRECTORY:-/etc/unas-linux-backup}
mkdir -p -- "$state"
export RCLONE_CONFIG=$credentials/rclone.conf
restic=(/usr/bin/restic -r "$destination" --password-file "$credentials/restic-password" --no-cache -o 'rclone.args=serve restic --stdio --bwlimit 8M')

exec 9>/var/lib/unas-linux-backup/repository-operation.lock
flock -w 300 9

last_success=$(systemctl show haos-precious-sync.service -p ExecMainExitTimestamp --value)
[[ $(systemctl show haos-precious-sync.service -p Result --value) == success ]]
age=$(( $(date +%s) - $(date -d "$last_success" +%s) ))
(( age >= 0 && age <= 10800 )) || { echo 'The HAOS configuration mirror is stale.' >&2; exit 1; }

head=$(git -C "$source" rev-parse HEAD)
pathspec=(homeassistant/configuration.yaml homeassistant/automations.yaml homeassistant/scripts.yaml homeassistant/scenes.yaml homeassistant/spatial_model.json homeassistant/apartment_model.json homeassistant/.storage/core.area_registry homeassistant/.storage/core.device_registry homeassistant/.storage/core.entity_registry homeassistant/.storage/core.floor_registry homeassistant/.storage/core.label_registry homeassistant/packages addon_configs/ccab4aaf_frigate-fa/config.yaml addon_configs/ccab4aaf_frigate-fa/go2rtc_homekit.yml)
entries=$(git -C "$source" ls-tree -r --full-tree "$head" -- "${pathspec[@]}" |
    awk '/^100(644|755) blob / { sub(/^[^\t]+\t/, ""); if ($0 !~ /^homeassistant\/packages\// || $0 ~ /^homeassistant\/packages\/[a-zA-Z0-9_\/-]+\.ya?ml$/) print }')
mapfile -t selected <<< "$entries"
(( ${#selected[@]} > 5 && ${#selected[@]} <= 250 ))
printf '%s\n' "${selected[@]}" | grep -qxF homeassistant/configuration.yaml
printf '%s\n' "${selected[@]}" | grep -qxF homeassistant/.storage/core.entity_registry

work=$(mktemp -d "$state/.work.XXXXXXXX")
trap 'rm -f -- "$work/la-haos-config.tar" "$work/restore/la-haos-config.tar" "$work/restic.json"; rmdir -- "$work/restore" "$work" 2>/dev/null || true' EXIT
mkdir "$work/restore"
git -C "$source" archive --format=tar "$head" -- "${selected[@]}" > "$work/la-haos-config.tar"
[[ $(stat -c %s "$work/la-haos-config.tar") -le 33554432 ]]
digest=$(sha256sum "$work/la-haos-config.tar" | cut -d ' ' -f 1)
if [[ -f "$state/last-success.json" ]] && python3 - "$state/last-success.json" "$digest" <<'PY'
import json, sys
with open(sys.argv[1]) as receipt:
    match = json.load(receipt)['archive_sha256'] == sys.argv[2]
raise SystemExit(0 if match else 1)
PY
then
    printf 'The selected HAOS configuration matches the last verified copy.\n'
    exit 0
fi

"${restic[@]}" backup --tag la-haos-config --json --stdin --stdin-filename la-haos-config.tar < "$work/la-haos-config.tar" > "$work/restic.json"
snapshot=$(python3 - "$work/restic.json" <<'PY'
import json, re, sys
with open(sys.argv[1]) as output:
    rows = [json.loads(line) for line in output]
assert not any(row['message_type'] == 'error' for row in rows)
snapshots = [row['snapshot_id'] for row in rows if row['message_type'] == 'summary']
assert len(snapshots) == 1 and re.fullmatch(r'[0-9a-f]{64}', snapshots[0])
print(snapshots[0])
PY
)
"${restic[@]}" restore "$snapshot" --target "$work/restore" --verify
cmp -s -- "$work/la-haos-config.tar" "$work/restore/la-haos-config.tar"
[[ $(git get-tar-commit-id < "$work/restore/la-haos-config.tar") == "$head" ]]
python3 - "$state/last-success.json" "$snapshot" "$head" "$digest" "${#selected[@]}" <<'PY'
import datetime, json, os, pathlib, sys, tempfile
path, snapshot, head, sha256, count = sys.argv[1:]
target = pathlib.Path(path)
record = {'scope': 'supplementary configuration only', 'snapshot': snapshot, 'mirror_commit': head, 'archive_sha256': sha256, 'files': int(count), 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as receipt:
    json.dump(record, receipt)
    receipt.write('\n')
    receipt.flush()
    os.fsync(receipt.fileno())
pathlib.Path(receipt.name).replace(target)
print(json.dumps(record))
PY
