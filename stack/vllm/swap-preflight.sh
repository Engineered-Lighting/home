#!/usr/bin/env bash
# Read-only health gate before each step of the vLLM model swap
# (docs/VLLM-QWEN36-SWAP.md). Changes nothing on the host.
#
# From the Windows workstation (PowerShell 5.1):
#   Get-Content stack\vllm\swap-preflight.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- 72"
# The optional argument is the Tctl ceiling in C (default 82; use 72 before
# the maintenance window). Exits 1 on any failed check; never clears a failed
# unit or restarts anything to get a pass.
set -u
TCTL_MAX=${1:-82}
GPU_MAX=85
DISK_MIN_GB=60
# Failures recorded before this work started (H0, 2026-10-19). Anything else is new.
KNOWN_FAILED="observer-recover.service"
fail=0
bad() { echo "FAIL: $*"; fail=1; }

echo "== $(hostname) $(date -Is)"
echo "system: $(systemctl is-system-running 2>/dev/null)"
for unit in $(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  case " $KNOWN_FAILED " in *" $unit "*) echo "known failed unit: $unit" ;; *) bad "new failed unit $unit" ;; esac
done
faults=$(journalctl -k -b --since "-24h" --no-pager 2>/dev/null | grep -icE "xid|nvrm.*error|out of memory|oom-kill|mce|hardware error|soft lockup|thermal|i/o error|segfault")
[ "${faults:-0}" -eq 0 ] && echo "kernel faults 24h: 0" || bad "kernel faults 24h: $faults (journalctl -k -b --since -24h)"

read -r gpu_temp gpu_used gpu_free <<<"$(nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.free --format=csv,noheader,nounits | tr -d ',')"
echo "gpu: ${gpu_temp}C used=${gpu_used}MiB free=${gpu_free}MiB"
[ "$gpu_temp" -lt "$GPU_MAX" ] || bad "GPU ${gpu_temp}C >= ${GPU_MAX}C"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
tctl=$(sensors 2>/dev/null | awk '/Tctl/ {gsub(/[^0-9.]/,"",$2); print int($2); exit}')
echo "Tctl: ${tctl:-unknown}C (max ${TCTL_MAX}C)"
[ -n "$tctl" ] && [ "$tctl" -lt "$TCTL_MAX" ] || bad "Tctl ${tctl:-unknown}C not below ${TCTL_MAX}C"

root=$(docker info -f '{{.DockerRootDir}}')
avail=$(df -BG --output=avail "$root" | tail -n 1 | tr -dc 0-9)
echo "docker root $root: ${avail}G free"
[ "$avail" -ge "$DISK_MIN_GB" ] || bad "under ${DISK_MIN_GB}G free on $root"
free -g | sed -n 1,3p

echo "hav-vllm: $(docker inspect -f '{{.State.Status}} {{.State.Health.Status}} id={{printf "%.12s" .Id}} started={{.State.StartedAt}}' hav-vllm)"
docker inspect -f '{{json .Args}}' hav-vllm | grep -oE '"--model","[^"]+"|"--revision","[^"]+"'
[ "$(docker inspect -f '{{.State.Health.Status}}' hav-vllm)" = healthy ] || bad "hav-vllm not healthy"
for c in hav-metrics-sidecar hav-vision-sidecar hav-intelligence; do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)
  [ "$s" = healthy ] && echo "$c: healthy" || bad "$c: ${s:-missing}"
done
[ "$fail" -eq 0 ] && echo "PREFLIGHT OK" || echo "PREFLIGHT FAILED: stop and report"
exit "$fail"
