#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
readonly PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
export LANG=C LC_ALL=C PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset BASH_ENV CDPATH ENV GLOBIGNORE LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH PYTHONSTARTUP

# This operator is intentionally inert until an operator invokes an explicit
# subcommand as root. It never installs packages, downloads images, decrypts a
# Home Assistant backup, or supplies Home Assistant credentials.

readonly EX_USAGE=64
readonly EX_UNAVAILABLE=69
readonly EX_CONFIG=78

readonly LOCKED_INSTALL_DIR=/usr/local/libexec/home-agent-haos-restore
readonly SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
readonly SCRIPT_DIR="$(dirname -- "$SCRIPT_PATH")"
if [[ "$SCRIPT_DIR" != "$LOCKED_INSTALL_DIR" || -L "${BASH_SOURCE[0]}" ]]; then
  printf 'haos restore drill: run only the root-installed, non-symlink harness at %s\n' \
    "$LOCKED_INSTALL_DIR/haos_restore_drill.sh" >&2
  exit "$EX_CONFIG"
fi

readonly LOCKED_HAOS_VERSION=18.1
readonly LOCKED_HAOS_IMAGE_SHA256=60df08773901e1eac9b9cfe03d53e1d939e67b669c172c3a41037fb3cd295b9d
readonly LOCKED_BACKUP_SLUG=7259fb6d
readonly LOCKED_BACKUP_SHA256=f129c72cb93121de63ef9ba88f3d21e207db6f6e22e84c57b040a2ec3f0a9d28
readonly LOCKED_BACKUP_CORE_VERSION=2026.7.1
readonly LOCKED_BACKUP_ADDON_COUNT=12
readonly LOCKED_POLICY_COMPILER_SHA256=f56cfbb6e2a8ab97e5e8c36e3e5b8e17e7b55811fd45734d019df15a13143004
readonly LOCKED_POLICY_REFRESHER_SHA256=2ef2c1ad3d8b1b891b34f0b8a3f7d4e6f59fbe17a2bec20774a907921be0756c
readonly LOCKED_EXACT_DNS_PROXY_SHA256=f7b76ed40bfa63635ed6d0c1b2f22a0889a7695cc21831cffc89750db8c989f5
readonly LOCKED_NFT_CONTRACT_SHA256=1796ea1bc38d4f135bc54dc570f3dcd038968deecd5e7aa3c066ec0fad9b9662
readonly LOCKED_BOOTSTRAP_POLICY_SHA256=d09c242b81405fbedcfe84dcba8a0b7ff6723f5371ef1aaa4e369eb41d5bd802
readonly LOCKED_RESTORE_POLICY_SHA256=11471298ec4e7ac4fa19dc6e02e05f3db13ca5a3e3a12f0493a3d5bda5561adb

readonly POLICY_COMPILER="$SCRIPT_DIR/policy_compiler.py"
readonly POLICY_REFRESHER="$SCRIPT_DIR/policy_refresher.py"
readonly EXACT_DNS_PROXY="$SCRIPT_DIR/exact_dns_proxy.py"
readonly NFT_CONTRACT="$SCRIPT_DIR/nft_contract.py"
readonly BOOTSTRAP_POLICY="$SCRIPT_DIR/bootstrap-egress.tsv"
readonly RESTORE_POLICY="$SCRIPT_DIR/restore-egress.tsv"
readonly RUN_ROOT=/run/home-agent-haos-restore
readonly STATE_FILE="$RUN_ROOT/state.env"
readonly KEY_FILE="$RUN_ROOT/ephemeral-luks.key"
readonly VM_SHARED_ROOT=/run/home-agent-haos-restore-vm
readonly VM_RUN_DIR="$VM_SHARED_ROOT/vm"
readonly RELAY_RUN_DIR="$VM_SHARED_ROOT/relay"
readonly TOOLS_ROOT=/run/home-agent-haos-restore-tools
readonly UI_SOCKET="$RELAY_RUN_DIR/ui.sock"
readonly DNSMASQ_CONFIG="$VM_RUN_DIR/dnsmasq.conf"
readonly DNSMASQ_LEASES="$VM_RUN_DIR/dnsmasq.leases"
readonly DNS_UPSTREAM_SOCKET="$RELAY_RUN_DIR/dns-upstream.sock"
readonly TOOL_POLICY_COMPILER="$TOOLS_ROOT/policy_compiler.py"
readonly TOOL_POLICY_REFRESHER="$TOOLS_ROOT/policy_refresher.py"
readonly TOOL_EXACT_DNS_PROXY="$TOOLS_ROOT/exact_dns_proxy.py"
readonly TOOL_NFT_CONTRACT="$TOOLS_ROOT/nft_contract.py"
readonly TOOL_BOOTSTRAP_POLICY="$TOOLS_ROOT/bootstrap-egress.tsv"
readonly TOOL_RESTORE_POLICY="$TOOLS_ROOT/restore-egress.tsv"
readonly LOCK_FILE=/run/home-agent-haos-restore.lock
readonly HOST_NFT_TABLE=haos_restore_host
readonly NS_NFT_TABLE=haos_restore
readonly GUEST_CIDR=192.0.2.0/24
readonly GUEST_GATEWAY=192.0.2.1
readonly GUEST_IP=192.0.2.10
readonly GUEST_MAC=52:54:00:18:01:01

readonly CONFIG_KEYS='HAOS_IMAGE_XZ HAOS_BACKUP_TAR HAOS_SCRATCH_PARENT HAOS_SCRATCH_FILE HAOS_MOUNT HAOS_REPORT_DIR HAOS_MAPPER HAOS_VM_USER HAOS_UPLINK HAOS_UPSTREAM_DNS HAOS_OVMF_CODE HAOS_OVMF_VARS HAOS_RELAY_PORT HAOS_VM_MEMORY_MIB HAOS_VM_CPUS HAOS_SCRATCH_SIZE_GIB HAOS_VM_DISK_GIB HAOS_MIN_HOST_FREE_AFTER_MAX_GIB HAOS_NETNS HAOS_TAP HAOS_EGRESS_TAP HAOS_UI_SCHEME'
readonly STATE_KEYS='DRILL_ID PHASE POLICY_SHA256 CONFIG_SHA256 SCRATCH_PATH_SHA256 SCRATCH_LUKS_UUID SCRATCH_LUKS_LABEL SCRATCH_DEV_INODE HOST_NFT_CONTRACT_SHA256 NS_NFT_CONTRACT_SHA256 NETNS_ANCHOR_PID QEMU_PID SLIRP_PID HOST_REFRESHER_PID NS_REFRESHER_PID DNS_UPSTREAM_PID DNS_PROXY_PID DNSMASQ_PID INNER_RELAY_PID OUTER_RELAY_PID'
readonly RECEIPT_KEYS='RECEIPT_VERSION DRILL_ID CONFIG_SHA256 SCRATCH_PATH_SHA256 SCRATCH_LUKS_UUID SCRATCH_LUKS_LABEL SCRATCH_DEV_INODE RECEIPT_HMAC'
readonly ATTESTATION_KEYS='RESTORE_COMPLETED ORIGINAL_LOGIN_SUCCEEDED DASHBOARD_LOADED CORE_CHECK_PASSED CONTROLLED_REBOOT_PASSED SUPERVISOR_HEALTHY FORBIDDEN_EGRESS_REVIEWED OBSERVED_HAOS_VERSION OBSERVED_CORE_VERSION EXPECTED_ADDON_COUNT RESTORED_ADDON_COUNT'

die() {
  printf 'haos restore drill: %s\n' "$*" >&2
  exit "$EX_CONFIG"
}

info() {
  printf 'haos restore drill: %s\n' "$*"
}

usage() {
  cat >&2 <<EOF
usage:
  sudo $0 preflight <config>
  sudo $0 prepare <config> --acknowledge-ephemeral-key-loss
  sudo $0 start-bootstrap <config> --confirm-sterile-bootstrap
  sudo $0 request-shutdown <config>
  sudo $0 transition-restore <config> --confirm-pristine-no-production-data
  sudo $0 status <config>
  sudo $0 validate <config> <root-owned-attestation.env>
  sudo $0 cleanup <config> --confirm-destroy-ephemeral-restore
EOF
  exit "$EX_USAGE"
}

require_root() {
  [[ "$(id -u)" == 0 ]] || die "must run as root"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'haos restore drill: missing command: %s\n' "$1" >&2
    exit "$EX_UNAVAILABLE"
  }
}

contains_key() {
  local key="$1"
  local keys="$2"
  [[ " $keys " == *" $key "* ]]
}

require_safe_value() {
  local label="$1"
  local value="$2"
  [[ -n "$value" && "$value" =~ ^[A-Za-z0-9._:/-]+$ ]] ||
    die "$label contains whitespace, expansion, or shell metacharacters"
}

require_absolute_path() {
  local label="$1"
  local value="$2"
  require_safe_value "$label" "$value"
  [[ "$value" == /* && "$value" != *'//'* && "$value" != *'/../'* &&
     "$value" != */.. && "$value" != *'/./'* && "$value" != */. ]] ||
    die "$label is not a normalized absolute path"
}

require_name() {
  local label="$1"
  local value="$2"
  local maximum="$3"
  [[ ${#value} -le $maximum && "$value" =~ ^[a-z][a-z0-9-]*$ ]] ||
    die "$label must be a lowercase bounded name"
}

require_uint_range() {
  local label="$1"
  local value="$2"
  local minimum="$3"
  local maximum="$4"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$label must be an integer"
  (( value >= minimum && value <= maximum )) ||
    die "$label must be between $minimum and $maximum"
}

require_regular_nonsymlink() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] ||
    die "$path must be a regular, non-symlink file"
}

require_not_group_world_writable() {
  local path="$1"
  local mode
  mode="$(stat -c '%a' "$path")"
  (( (8#$mode & 0022) == 0 )) ||
    die "$path may not be group/world-writable"
}

require_root_directory() {
  local path="$1"
  [[ -d "$path" && ! -L "$path" ]] || die "$path must be a directory"
  [[ "$(stat -c '%u:%a' "$path")" == '0:700' ]] ||
    die "$path must be root-owned mode 0700"
}

require_trusted_ancestors() {
  local path="$1"
  local component mode
  component="$(dirname -- "$path")"
  while true; do
    [[ -d "$component" && ! -L "$component" ]] ||
      die "$path has a missing, non-directory, or symlinked ancestor"
    [[ "$(stat -c '%u' "$component")" == 0 ]] ||
      die "$path has a non-root-owned ancestor"
    mode="$(stat -c '%a' "$component")"
    (( (8#$mode & 0022) == 0 )) ||
      die "$path has a group/world-writable ancestor"
    [[ "$component" == / ]] && break
    component="$(dirname -- "$component")"
  done
}

require_trusted_directory_tree() {
  local path="$1"
  local canonical mode
  canonical="$(readlink -e -- "$path")" || die "$path does not exist"
  [[ "$canonical" == "$path" && -d "$path" && ! -L "$path" ]] ||
    die "$path must be a canonical, non-symlink directory"
  [[ "$(stat -c '%u' "$path")" == 0 ]] || die "$path must be root-owned"
  mode="$(stat -c '%a' "$path")"
  (( (8#$mode & 0022) == 0 )) || die "$path may not be group/world-writable"
  require_trusted_ancestors "$path"
}

require_trusted_file_tree() {
  local path="$1"
  local canonical
  canonical="$(readlink -e -- "$path")" || die "$path does not exist"
  [[ "$canonical" == "$path" ]] || die "$path must be canonical and symlink-free"
  require_regular_nonsymlink "$path"
  [[ "$(stat -c '%u' "$path")" == 0 ]] || die "$path must be root-owned"
  require_not_group_world_writable "$path"
  require_trusted_ancestors "$path"
}

validate_installation() {
  local path name count=0
  require_trusted_directory_tree "$SCRIPT_DIR"
  for path in "$SCRIPT_PATH" "$POLICY_COMPILER" "$POLICY_REFRESHER" \
    "$EXACT_DNS_PROXY" "$NFT_CONTRACT" "$BOOTSTRAP_POLICY" "$RESTORE_POLICY"; do
    require_trusted_file_tree "$path"
  done
  while IFS= read -r path; do
    name="${path##*/}"
    case "$name" in
      haos_restore_drill.sh|policy_compiler.py|policy_refresher.py|exact_dns_proxy.py|nft_contract.py|bootstrap-egress.tsv|restore-egress.tsv) ;;
      *) die "installed harness directory contains unexpected entry $name" ;;
    esac
    count=$((count + 1))
  done < <(find "$SCRIPT_DIR" -mindepth 1 -maxdepth 1 -print)
  [[ "$count" == 7 ]] || die "installed harness directory is incomplete"
}

file_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

load_kv_file() {
  local path="$1"
  local allowed="$2"
  local require_root_owner="$3"
  local key value raw
  declare -A seen=()

  require_regular_nonsymlink "$path"
  require_not_group_world_writable "$path"
  if [[ "$require_root_owner" == 1 ]]; then
    [[ "$(stat -c '%u' "$path")" == 0 ]] || die "$path must be root-owned"
  fi
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    [[ -z "$raw" || "$raw" == \#* ]] && continue
    [[ "$raw" == *=* ]] || die "$path contains a malformed setting"
    key="${raw%%=*}"
    value="${raw#*=}"
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "$path contains an invalid key"
    contains_key "$key" "$allowed" || die "$path contains unknown key $key"
    [[ -z "${seen[$key]:-}" ]] || die "$path repeats key $key"
    require_safe_value "$key" "$value"
    seen[$key]=1
    printf -v "$key" '%s' "$value"
  done < "$path"
  while IFS= read -r key; do
    [[ -n "${seen[$key]:-}" ]] || die "$path is missing $key"
  done < <(tr ' ' '\n' <<< "$allowed")
}

load_config() {
  local config
  config="$(readlink -m -- "$1")"
  require_absolute_path CONFIG_FILE "$config"
  require_trusted_file_tree "$config"
  load_kv_file "$config" "$CONFIG_KEYS" 1
  ACTIVE_CONFIG_SHA256="$(file_sha256 "$config")"
  ACTIVE_SCRATCH_PATH_SHA256="$(printf '%s' "$(readlink -m -- "$HAOS_SCRATCH_FILE")" | sha256sum | awk '{print $1}')"
  RECEIPT_AUTH_KEY="$HAOS_REPORT_DIR/.haos-restore-receipt.key"
  RECEIPT_FILE="$HAOS_REPORT_DIR/haos-restore-$ACTIVE_CONFIG_SHA256.receipt"
}

receipt_payload() {
  printf 'RECEIPT_VERSION=1\n'
  printf 'DRILL_ID=%s\n' "$DRILL_ID"
  printf 'CONFIG_SHA256=%s\n' "$CONFIG_SHA256"
  printf 'SCRATCH_PATH_SHA256=%s\n' "$SCRATCH_PATH_SHA256"
  printf 'SCRATCH_LUKS_UUID=%s\n' "$SCRATCH_LUKS_UUID"
  printf 'SCRATCH_LUKS_LABEL=%s\n' "$SCRATCH_LUKS_LABEL"
  printf 'SCRATCH_DEV_INODE=%s\n' "$SCRATCH_DEV_INODE"
}

validate_receipt_key() {
  require_regular_nonsymlink "$RECEIPT_AUTH_KEY"
  [[ "$(stat -c '%u:%a:%s' "$RECEIPT_AUTH_KEY")" == '0:400:32' ]] ||
    die "durable receipt authentication key has unsafe metadata"
}

receipt_hmac() {
  validate_receipt_key
  python3 -I -c '
import hashlib, hmac, pathlib, sys
key = pathlib.Path(sys.argv[1]).read_bytes()
sys.stdout.write(hmac.new(key, sys.stdin.buffer.read(), hashlib.sha256).hexdigest())
' "$RECEIPT_AUTH_KEY"
}

initialize_receipt_key() {
  local temporary="$HAOS_REPORT_DIR/.haos-restore-receipt.key.new.$DRILL_ID"
  if [[ -e "$RECEIPT_AUTH_KEY" || -L "$RECEIPT_AUTH_KEY" ]]; then
    validate_receipt_key
    return
  fi
  openssl rand -out "$temporary" 32
  chmod 0400 "$temporary"
  chown root:root "$temporary"
  sync -f "$temporary"
  mv -- "$temporary" "$RECEIPT_AUTH_KEY"
  sync -f "$HAOS_REPORT_DIR"
  validate_receipt_key
}

write_durable_receipt() {
  local temporary hmac_value
  [[ ! -e "$RECEIPT_FILE" && ! -L "$RECEIPT_FILE" ]] ||
    die "durable cleanup receipt already exists"
  initialize_receipt_key
  hmac_value="$(receipt_payload | receipt_hmac)"
  [[ "$hmac_value" =~ ^[0-9a-f]{64}$ ]] || die "could not authenticate cleanup receipt"
  temporary="$HAOS_REPORT_DIR/.haos-restore-receipt.new.$DRILL_ID"
  {
    receipt_payload
    printf 'RECEIPT_HMAC=%s\n' "$hmac_value"
  } > "$temporary"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  sync -f "$temporary"
  mv -- "$temporary" "$RECEIPT_FILE"
  sync -f "$HAOS_REPORT_DIR"
  RECEIPT_WRITTEN=yes
}

load_durable_receipt() {
  local expected_config="$ACTIVE_CONFIG_SHA256"
  local expected_path="$ACTIVE_SCRATCH_PATH_SHA256"
  local recorded_hmac expected_hmac
  [[ "$(stat -c '%u:%a' "$RECEIPT_FILE" 2>/dev/null || true)" == '0:600' ]] ||
    die "durable cleanup receipt is missing or has unsafe metadata"
  load_kv_file "$RECEIPT_FILE" "$RECEIPT_KEYS" 1
  [[ "$RECEIPT_VERSION" == 1 ]] || die "unsupported durable cleanup receipt"
  [[ "$CONFIG_SHA256" == "$expected_config" ]] ||
    die "durable receipt belongs to a different configuration"
  [[ "$SCRATCH_PATH_SHA256" == "$expected_path" ]] ||
    die "durable receipt belongs to a different scratch path"
  recorded_hmac="$RECEIPT_HMAC"
  expected_hmac="$(receipt_payload | receipt_hmac)"
  [[ "$recorded_hmac" == "$expected_hmac" ]] ||
    die "durable cleanup receipt authentication failed"
  verify_state_scratch_identity
}

verify_state_scratch_identity() {
  require_regular_nonsymlink "$HAOS_SCRATCH_FILE"
  cryptsetup isLuks "$HAOS_SCRATCH_FILE" >/dev/null ||
    die "state scratch path is no longer a LUKS container; guards were retained"
  [[ "$(cryptsetup luksUUID "$HAOS_SCRATCH_FILE")" == "$SCRATCH_LUKS_UUID" ]] ||
    die "state scratch UUID does not match; guards were retained"
  [[ "$(blkid -p -s LABEL -o value -- "$HAOS_SCRATCH_FILE")" == "$SCRATCH_LUKS_LABEL" ]] ||
    die "state scratch label does not match; guards were retained"
  [[ "$(stat -Lc '%d:%i' "$HAOS_SCRATCH_FILE")" == "$SCRATCH_DEV_INODE" ]] ||
    die "state scratch device/inode identity does not match; guards were retained"
}

verify_active_mapper_identity() {
  local status_output backing loop_file
  [[ -e "/dev/mapper/$HAOS_MAPPER" ]] || return 0
  status_output="$(cryptsetup status "$HAOS_MAPPER")" ||
    die "active scratch mapper status is unreadable; mapper was retained"
  backing="$(awk '$1 == "device:" {print $2}' <<< "$status_output")"
  require_absolute_path MAPPER_BACKING "$backing"
  [[ -b "$backing" && "$backing" == /dev/loop* ]] ||
    die "active mapper is not backed by the expected loop device"
  [[ "$(cryptsetup luksUUID "$backing")" == "$SCRATCH_LUKS_UUID" ]] ||
    die "active mapper backing LUKS UUID does not match; mapper was retained"
  loop_file="$(losetup --noheadings --output BACK-FILE "$backing" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ -n "$loop_file" && "$(readlink -m -- "$loop_file")" == "$(readlink -m -- "$HAOS_SCRATCH_FILE")" ]] ||
    die "active mapper loop file does not match the bound scratch path"
}

load_state() {
  load_kv_file "$STATE_FILE" "$STATE_KEYS" 1
  [[ "$CONFIG_SHA256" == "$ACTIVE_CONFIG_SHA256" ]] ||
    die "state belongs to a different configuration file; guards were retained"
  [[ "$SCRATCH_PATH_SHA256" == "$ACTIVE_SCRATCH_PATH_SHA256" ]] ||
    die "state belongs to a different scratch path; guards were retained"
  verify_state_scratch_identity
}

write_state() {
  local temporary="$RUN_ROOT/state.env.new"
  {
    printf 'DRILL_ID=%s\n' "$DRILL_ID"
    printf 'PHASE=%s\n' "$PHASE"
    printf 'POLICY_SHA256=%s\n' "$POLICY_SHA256"
    printf 'CONFIG_SHA256=%s\n' "$CONFIG_SHA256"
    printf 'SCRATCH_PATH_SHA256=%s\n' "$SCRATCH_PATH_SHA256"
    printf 'SCRATCH_LUKS_UUID=%s\n' "$SCRATCH_LUKS_UUID"
    printf 'SCRATCH_LUKS_LABEL=%s\n' "$SCRATCH_LUKS_LABEL"
    printf 'SCRATCH_DEV_INODE=%s\n' "$SCRATCH_DEV_INODE"
    printf 'HOST_NFT_CONTRACT_SHA256=%s\n' "$HOST_NFT_CONTRACT_SHA256"
    printf 'NS_NFT_CONTRACT_SHA256=%s\n' "$NS_NFT_CONTRACT_SHA256"
    printf 'NETNS_ANCHOR_PID=%s\n' "${NETNS_ANCHOR_PID:-0}"
    printf 'QEMU_PID=%s\n' "${QEMU_PID:-0}"
    printf 'SLIRP_PID=%s\n' "${SLIRP_PID:-0}"
    printf 'HOST_REFRESHER_PID=%s\n' "${HOST_REFRESHER_PID:-0}"
    printf 'NS_REFRESHER_PID=%s\n' "${NS_REFRESHER_PID:-0}"
    printf 'DNS_UPSTREAM_PID=%s\n' "${DNS_UPSTREAM_PID:-0}"
    printf 'DNS_PROXY_PID=%s\n' "${DNS_PROXY_PID:-0}"
    printf 'DNSMASQ_PID=%s\n' "${DNSMASQ_PID:-0}"
    printf 'INNER_RELAY_PID=%s\n' "${INNER_RELAY_PID:-0}"
    printf 'OUTER_RELAY_PID=%s\n' "${OUTER_RELAY_PID:-0}"
  } > "$temporary"
  chmod 0600 "$temporary"
  chown root:root "$temporary"
  mv -f -- "$temporary" "$STATE_FILE"
}

acquire_lock() {
  require_trusted_directory_tree /run
  if [[ -e "$LOCK_FILE" || -L "$LOCK_FILE" ]]; then
    [[ -f "$LOCK_FILE" && ! -L "$LOCK_FILE" &&
       "$(stat -c '%u:%a:%h' "$LOCK_FILE")" == '0:600:1' ]] ||
      die "operator lock has unsafe type, ownership, mode, or link count"
  else
    ( umask 077; : > "$LOCK_FILE" )
    chown root:root "$LOCK_FILE"
    chmod 0600 "$LOCK_FILE"
  fi
  exec 9>>"$LOCK_FILE"
  [[ "$(stat -Lc '%d:%i' "$LOCK_FILE")" == "$(stat -Lc '%d:%i' "/proc/$$/fd/9")" ]] ||
    die "operator lock identity changed while opening"
  flock -n 9 || die "another HAOS restore drill operation is running"
}

validate_paths_and_values() {
  local value
  for value in HAOS_IMAGE_XZ HAOS_BACKUP_TAR HAOS_SCRATCH_PARENT \
    HAOS_SCRATCH_FILE HAOS_MOUNT HAOS_REPORT_DIR HAOS_OVMF_CODE HAOS_OVMF_VARS; do
    require_absolute_path "$value" "${!value}"
  done
  require_name HAOS_MAPPER "$HAOS_MAPPER" 48
  require_name HAOS_VM_USER "$HAOS_VM_USER" 31
  require_name HAOS_NETNS "$HAOS_NETNS" 31
  require_name HAOS_TAP "$HAOS_TAP" 15
  require_name HAOS_EGRESS_TAP "$HAOS_EGRESS_TAP" 15
  require_name HAOS_UPLINK "$HAOS_UPLINK" 15
  [[ "$HAOS_UPLINK" != lo && "$HAOS_UPLINK" != tailscale* &&
     "$HAOS_UPLINK" != docker* && "$HAOS_UPLINK" != br-* &&
     "$HAOS_UPLINK" != veth* ]] || die "HAOS_UPLINK is not an approved physical uplink"
  [[ "$HAOS_UI_SCHEME" == http || "$HAOS_UI_SCHEME" == https ]] ||
    die "HAOS_UI_SCHEME must be http or https"
  require_uint_range HAOS_RELAY_PORT "$HAOS_RELAY_PORT" 1024 65535
  require_uint_range HAOS_VM_MEMORY_MIB "$HAOS_VM_MEMORY_MIB" 4096 16384
  require_uint_range HAOS_VM_CPUS "$HAOS_VM_CPUS" 2 8
  require_uint_range HAOS_SCRATCH_SIZE_GIB "$HAOS_SCRATCH_SIZE_GIB" 160 256
  require_uint_range HAOS_VM_DISK_GIB "$HAOS_VM_DISK_GIB" 128 192
  require_uint_range HAOS_MIN_HOST_FREE_AFTER_MAX_GIB \
    "$HAOS_MIN_HOST_FREE_AFTER_MAX_GIB" 100 512
  (( HAOS_SCRATCH_SIZE_GIB >= HAOS_VM_DISK_GIB + 16 )) ||
    die "scratch size must exceed VM disk by at least 16 GiB"
  [[ "$(dirname -- "$HAOS_SCRATCH_FILE")" == "$HAOS_SCRATCH_PARENT" ]] ||
    die "scratch file must be directly inside HAOS_SCRATCH_PARENT"
  [[ "$HAOS_SCRATCH_FILE" != "$HAOS_BACKUP_TAR" &&
     "$HAOS_SCRATCH_FILE" != "$HAOS_IMAGE_XZ" ]] ||
    die "scratch file may not overlap an input"
  [[ "$HAOS_MAPPER" != home-agent && "$HAOS_MAPPER" != home_agent ]] ||
    die "scratch mapper may not reuse the production mapper name"
  python3 -I -c 'import ipaddress,sys; a=ipaddress.ip_address(sys.argv[1]); raise SystemExit(0 if a.version == 4 and a.is_global else 1)' \
    "$HAOS_UPSTREAM_DNS" || die "HAOS_UPSTREAM_DNS must be a public IPv4 address"
}

validate_vm_user() {
  local passwd_record uid gid shell groups kvm_record kvm_gid group_id saw_kvm=no
  passwd_record="$(getent passwd "$HAOS_VM_USER" || true)"
  [[ -n "$passwd_record" ]] || die "dedicated VM user $HAOS_VM_USER does not exist"
  IFS=: read -r _ _ uid gid _ _ shell <<< "$passwd_record"
  (( uid >= 100 )) || die "dedicated VM user may not be privileged"
  [[ "$shell" == /usr/sbin/nologin || "$shell" == /bin/false ]] ||
    die "dedicated VM user must have a non-login shell"
  groups=" $(id -nG "$HAOS_VM_USER") "
  [[ "$groups" == *' kvm '* ]] || die "dedicated VM user must belong to kvm"
  kvm_record="$(getent group kvm || true)"
  [[ -n "$kvm_record" ]] || die "kvm group is unavailable"
  IFS=: read -r _ _ kvm_gid _ <<< "$kvm_record"
  while IFS= read -r group_id; do
    [[ -n "$group_id" ]] || continue
    [[ "$group_id" == "$gid" || "$group_id" == "$kvm_gid" ]] ||
      die "dedicated VM user may belong only to its primary group and kvm"
    [[ "$group_id" == "$kvm_gid" ]] && saw_kvm=yes
  done < <(id -G "$HAOS_VM_USER" | tr ' ' '\n')
  [[ "$saw_kvm" == yes ]] || die "dedicated VM user lacks numeric kvm membership"
  runuser -u "$HAOS_VM_USER" -- sh -c 'test -r /dev/kvm && test -w /dev/kvm' ||
    die "dedicated VM user cannot open /dev/kvm"
  runuser -u "$HAOS_VM_USER" -- setpriv --no-new-privs --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all -- unshare --user --map-root-user --net \
    sh -c 'test -r /dev/kvm && test -w /dev/kvm' ||
    die "dedicated VM user cannot use KVM from a private user/network namespace"
  VM_UID="$uid"
  VM_GID="$gid"
}

validate_inputs() {
  local metadata image_hash backup_hash compiler_hash refresher_hash dns_proxy_hash
  local nft_contract_hash bootstrap_hash restore_hash
  require_regular_nonsymlink "$HAOS_IMAGE_XZ"
  require_regular_nonsymlink "$HAOS_BACKUP_TAR"
  require_trusted_file_tree "$HAOS_IMAGE_XZ"
  require_trusted_file_tree "$HAOS_BACKUP_TAR"
  image_hash="$(file_sha256 "$HAOS_IMAGE_XZ")"
  [[ "$image_hash" == "$LOCKED_HAOS_IMAGE_SHA256" ]] ||
    die "HAOS 18.1 image digest mismatch"
  xz --test -- "$HAOS_IMAGE_XZ" || die "HAOS image xz integrity check failed"
  backup_hash="$(file_sha256 "$HAOS_BACKUP_TAR")"
  [[ "$backup_hash" == "$LOCKED_BACKUP_SHA256" ]] || die "backup digest mismatch"
  metadata="$(tar -xOf "$HAOS_BACKUP_TAR" ./backup.json 2>/dev/null)" ||
    die "backup metadata is unreadable"
  jq -e --arg slug "$LOCKED_BACKUP_SLUG" --arg core "$LOCKED_BACKUP_CORE_VERSION" \
    --argjson addons "$LOCKED_BACKUP_ADDON_COUNT" '
      .slug == $slug and .type == "full" and .protected == true and
      .compressed == true and .homeassistant.version == $core and
      (.addons | length) == $addons
    ' <<< "$metadata" >/dev/null || die "backup metadata does not match the locked full backup"

  require_regular_nonsymlink "$POLICY_COMPILER"
  require_regular_nonsymlink "$POLICY_REFRESHER"
  require_regular_nonsymlink "$EXACT_DNS_PROXY"
  require_regular_nonsymlink "$NFT_CONTRACT"
  require_regular_nonsymlink "$BOOTSTRAP_POLICY"
  require_regular_nonsymlink "$RESTORE_POLICY"
  compiler_hash="$(file_sha256 "$POLICY_COMPILER")"
  refresher_hash="$(file_sha256 "$POLICY_REFRESHER")"
  dns_proxy_hash="$(file_sha256 "$EXACT_DNS_PROXY")"
  nft_contract_hash="$(file_sha256 "$NFT_CONTRACT")"
  bootstrap_hash="$(file_sha256 "$BOOTSTRAP_POLICY")"
  restore_hash="$(file_sha256 "$RESTORE_POLICY")"
  [[ "$compiler_hash" == "$LOCKED_POLICY_COMPILER_SHA256" ]] ||
    die "policy compiler digest mismatch"
  [[ "$refresher_hash" == "$LOCKED_POLICY_REFRESHER_SHA256" ]] ||
    die "policy refresher digest mismatch"
  [[ "$dns_proxy_hash" == "$LOCKED_EXACT_DNS_PROXY_SHA256" ]] ||
    die "exact DNS proxy digest mismatch"
  [[ "$nft_contract_hash" == "$LOCKED_NFT_CONTRACT_SHA256" ]] ||
    die "nft contract helper digest mismatch"
  [[ "$bootstrap_hash" == "$LOCKED_BOOTSTRAP_POLICY_SHA256" ]] ||
    die "bootstrap policy digest mismatch"
  [[ "$restore_hash" == "$LOCKED_RESTORE_POLICY_SHA256" ]] ||
    die "restore policy digest mismatch"
  python3 "$POLICY_COMPILER" validate "$BOOTSTRAP_POLICY" \
    --expected-sha256 "$LOCKED_BOOTSTRAP_POLICY_SHA256"
  python3 "$POLICY_COMPILER" validate "$RESTORE_POLICY" \
    --expected-sha256 "$LOCKED_RESTORE_POLICY_SHA256"
}

validate_destructive_directories() {
  require_root_directory "$HAOS_SCRATCH_PARENT"
  require_root_directory "$HAOS_REPORT_DIR"
  require_trusted_directory_tree "$HAOS_SCRATCH_PARENT"
  require_trusted_directory_tree "$HAOS_REPORT_DIR"
  [[ "$(readlink -e -- "$HAOS_MOUNT")" == "$HAOS_MOUNT" &&
     -d "$HAOS_MOUNT" && ! -L "$HAOS_MOUNT" ]] ||
    die "HAOS_MOUNT must be a canonical, non-symlink directory"
  require_trusted_ancestors "$HAOS_MOUNT"
}

validate_host() {
  local required fs_type route ovmf_path
  for required in awk blkid cat chmod chown cryptsetup curl date df dirname dnsmasq \
    find findmnt flock getent grep head install ip jq kill losetup mkfs.ext4 mount \
    mountpoint mv nft nsenter openssl pgrep python3 qemu-img qemu-system-x86_64 \
    readlink rm rmdir runuser sed seq setpriv sha256sum sleep slirp4netns \
    socat ss stat sync sysctl tar timeout tr truncate umount unshare xz dig; do
    require_command "$required"
  done
  [[ -c /dev/kvm ]] || die "/dev/kvm is unavailable"
  [[ "$(findmnt -rn -o FSTYPE -T /run)" == tmpfs ]] ||
    die "/run must be tmpfs so the transient LUKS key is never persisted"
  slirp4netns --help 2>&1 | grep -q -- '--disable-host-loopback' ||
    die "slirp4netns lacks --disable-host-loopback"
  slirp4netns --help 2>&1 | grep -q -- '--netns-type' ||
    die "slirp4netns lacks path-based network namespace support"
  nsenter --help 2>&1 | grep -q -- '--setuid' || die "nsenter lacks --setuid"
  nsenter --help 2>&1 | grep -q -- '--setgid' || die "nsenter lacks --setgid"
  unshare --help 2>&1 | grep -q -- '--map-root-user' ||
    die "unshare lacks --map-root-user"
  unshare --help 2>&1 | grep -q -- '--mount-proc' ||
    die "unshare lacks --mount-proc"
  ip link show dev "$HAOS_UPLINK" >/dev/null 2>&1 || die "uplink does not exist"
  [[ "$(cat "/sys/class/net/$HAOS_UPLINK/operstate")" == up ]] || die "uplink is not up"
  route="$(ip route get "$HAOS_UPSTREAM_DNS")"
  [[ " $route " == *" dev $HAOS_UPLINK "* ]] ||
    die "upstream DNS does not route through the reviewed uplink"

  validate_vm_user
  validate_destructive_directories
  if mountpoint -q -- "$HAOS_MOUNT"; then
    [[ "$(findmnt -rn -o SOURCE -T "$HAOS_MOUNT")" == "/dev/mapper/$HAOS_MAPPER" ]] ||
      die "HAOS_MOUNT contains an unexpected filesystem"
    [[ "$(stat -c '%u:%a' "$HAOS_MOUNT")" == "$VM_UID:700" ]] ||
      die "mounted scratch root has unexpected ownership/mode"
  else
    require_root_directory "$HAOS_MOUNT"
  fi
  fs_type="$(findmnt -rn -o FSTYPE -T "$HAOS_SCRATCH_PARENT")"
  case "$fs_type" in
    ext4|xfs|btrfs) ;;
    *) die "scratch parent must be on a local Linux filesystem" ;;
  esac
  for ovmf_path in "$HAOS_OVMF_CODE" "$HAOS_OVMF_VARS"; do
    require_trusted_file_tree "$ovmf_path"
  done
  [[ "${HAOS_OVMF_CODE,,}" != *secure* && "${HAOS_OVMF_CODE,,}" != *secboot* ]] ||
    die "HAOS requires non-Secure-Boot OVMF firmware"
}

runtime_is_clean() {
  local available required_bytes
  [[ ! -e "$RUN_ROOT" ]] || die "$RUN_ROOT already exists"
  [[ ! -e "$VM_SHARED_ROOT" ]] || die "$VM_SHARED_ROOT already exists"
  [[ ! -e "$TOOLS_ROOT" ]] || die "$TOOLS_ROOT already exists"
  [[ ! -e "$RECEIPT_FILE" && ! -L "$RECEIPT_FILE" ]] ||
    die "durable cleanup receipt already exists; clean up the prior drill"
  [[ ! -e "$HAOS_SCRATCH_FILE" ]] || die "scratch file already exists"
  [[ ! -e "/dev/mapper/$HAOS_MAPPER" ]] || die "scratch mapper already exists"
  ! mountpoint -q -- "$HAOS_MOUNT" || die "scratch mount is already mounted"
  ! nft list table inet "$HOST_NFT_TABLE" >/dev/null 2>&1 ||
    die "host drill firewall table already exists"
  [[ -z "$(ss -H -ltn "sport = :$HAOS_RELAY_PORT" 2>/dev/null)" ]] ||
    die "localhost relay port is already in use"
  [[ -z "$(pgrep -u "$VM_UID" || true)" ]] ||
    die "dedicated VM user already owns a process"
  [[ -z "$(find "$HAOS_MOUNT" -mindepth 1 -maxdepth 1 -print -quit)" ]] ||
    die "HAOS_MOUNT must be empty before preparation"
  available="$(df -PB1 "$HAOS_SCRATCH_PARENT" | awk 'NR == 2 {print $4}')"
  required_bytes=$(( (HAOS_SCRATCH_SIZE_GIB + HAOS_MIN_HOST_FREE_AFTER_MAX_GIB) * 1024 * 1024 * 1024 ))
  (( available >= required_bytes )) ||
    die "host cannot absorb maximum scratch allocation and preserve the free-space floor"
}

static_preflight() {
  local config="$1"
  require_root
  validate_installation
  load_config "$config"
  validate_paths_and_values
  validate_inputs
  validate_host
}

preflight_clean() {
  static_preflight "$1"
  runtime_is_clean
}

safe_remove_runtime_dir() {
  local path="$1"
  local expected_uid="$2"
  local expected_mode="$3"
  [[ -d "$path" && ! -L "$path" ]] || return 0
  [[ "$(stat -c '%u:%a' "$path")" == "$expected_uid:$expected_mode" ]] ||
    die "refusing to remove a runtime directory with unexpected ownership or mode"
  [[ "$(findmnt -rn -o FSTYPE -T "$path")" == tmpfs ]] ||
    die "refusing to remove a runtime directory outside tmpfs"
  find "$path" -xdev -depth -mindepth 1 -delete
  rmdir -- "$path"
}

safe_remove_runtime_roots() {
  safe_remove_runtime_dir "$VM_SHARED_ROOT" "$VM_UID" 700
  safe_remove_runtime_dir "$TOOLS_ROOT" 0 750
  safe_remove_runtime_dir "$RUN_ROOT" 0 700
}

verify_tool_copy() {
  local path="$1"
  local expected_sha256="$2"
  [[ -f "$path" && ! -L "$path" &&
     "$(stat -c '%u:%g:%a' "$path")" == "0:$VM_GID:440" ]] ||
    die "runtime tool copy has unsafe metadata"
  [[ "$(file_sha256 "$path")" == "$expected_sha256" ]] ||
    die "runtime tool copy digest mismatch"
}

verify_tool_copies() {
  verify_tool_copy "$TOOL_POLICY_COMPILER" "$LOCKED_POLICY_COMPILER_SHA256"
  verify_tool_copy "$TOOL_POLICY_REFRESHER" "$LOCKED_POLICY_REFRESHER_SHA256"
  verify_tool_copy "$TOOL_EXACT_DNS_PROXY" "$LOCKED_EXACT_DNS_PROXY_SHA256"
  verify_tool_copy "$TOOL_NFT_CONTRACT" "$LOCKED_NFT_CONTRACT_SHA256"
  verify_tool_copy "$TOOL_BOOTSTRAP_POLICY" "$LOCKED_BOOTSTRAP_POLICY_SHA256"
  verify_tool_copy "$TOOL_RESTORE_POLICY" "$LOCKED_RESTORE_POLICY_SHA256"
}

prepare_cleanup_on_failure() {
  local status=$?
  local scratch_removed=no
  trap - EXIT HUP INT TERM
  set +e
  if mountpoint -q -- "$HAOS_MOUNT" &&
     [[ "$(findmnt -rn -o SOURCE -T "$HAOS_MOUNT")" == "/dev/mapper/$HAOS_MAPPER" ]]; then
    umount -- "$HAOS_MOUNT"
  fi
  if [[ -e "/dev/mapper/$HAOS_MAPPER" ]]; then
    cryptsetup close "$HAOS_MAPPER"
  fi
  rm -f -- "$KEY_FILE" 2>/dev/null || true
  if [[ "${SCRATCH_CREATED:-no}" == yes && -f "$HAOS_SCRATCH_FILE" && ! -L "$HAOS_SCRATCH_FILE" &&
        "$(dirname -- "$HAOS_SCRATCH_FILE")" == "$HAOS_SCRATCH_PARENT" &&
        "$(readlink -m -- "$HAOS_SCRATCH_FILE")" == "$HAOS_SCRATCH_FILE" &&
        "$(stat -Lc '%d:%i' "$HAOS_SCRATCH_FILE")" == "$SCRATCH_DEV_INODE" ]]; then
    rm -f -- "$HAOS_SCRATCH_FILE" && scratch_removed=yes
  fi
  if [[ "${RECEIPT_WRITTEN:-no}" == yes && "$scratch_removed" == yes ]]; then
    rm -f -- "$RECEIPT_FILE" 2>/dev/null || true
  fi
  safe_remove_runtime_roots 2>/dev/null || true
  exit "$status"
}

prepare_workspace() {
  local config="$1"
  local acknowledgement="$2"
  local base_image="$HAOS_MOUNT/haos-base.qcow2"
  local vm_disk="$HAOS_MOUNT/haos-restore.qcow2"
  local vm_vars="$HAOS_MOUNT/OVMF_VARS.fd"

  [[ "$acknowledgement" == --acknowledge-ephemeral-key-loss ]] || usage
  preflight_clean "$config"
  acquire_lock
  DRILL_ID="$(date -u +%Y%m%dT%H%M%SZ).$(openssl rand -hex 4)"
  CONFIG_SHA256="$ACTIVE_CONFIG_SHA256"
  SCRATCH_PATH_SHA256="$ACTIVE_SCRATCH_PATH_SHA256"
  SCRATCH_LUKS_UUID=unformatted
  SCRATCH_LUKS_LABEL="haos-restore-$DRILL_ID"
  SCRATCH_CREATED=no
  RECEIPT_WRITTEN=no
  install -d -m 0700 -o root -g root "$RUN_ROOT"
  trap prepare_cleanup_on_failure EXIT HUP INT TERM
  install -d -m 0700 -o "$VM_UID" -g "$VM_GID" "$VM_SHARED_ROOT"
  install -d -m 0700 -o "$VM_UID" -g "$VM_GID" "$VM_RUN_DIR" "$RELAY_RUN_DIR"
  install -d -m 0750 -o root -g "$VM_GID" "$TOOLS_ROOT"
  install -m 0440 -o root -g "$VM_GID" "$POLICY_COMPILER" "$TOOL_POLICY_COMPILER"
  install -m 0440 -o root -g "$VM_GID" "$POLICY_REFRESHER" "$TOOL_POLICY_REFRESHER"
  install -m 0440 -o root -g "$VM_GID" "$EXACT_DNS_PROXY" "$TOOL_EXACT_DNS_PROXY"
  install -m 0440 -o root -g "$VM_GID" "$NFT_CONTRACT" "$TOOL_NFT_CONTRACT"
  install -m 0440 -o root -g "$VM_GID" "$BOOTSTRAP_POLICY" "$TOOL_BOOTSTRAP_POLICY"
  install -m 0440 -o root -g "$VM_GID" "$RESTORE_POLICY" "$TOOL_RESTORE_POLICY"
  verify_tool_copies
  openssl rand -out "$KEY_FILE" 64
  chmod 0600 "$KEY_FILE"
  chown root:root "$KEY_FILE"
  truncate -s "${HAOS_SCRATCH_SIZE_GIB}G" "$HAOS_SCRATCH_FILE"
  SCRATCH_DEV_INODE="$(stat -Lc '%d:%i' "$HAOS_SCRATCH_FILE")"
  [[ "$SCRATCH_DEV_INODE" =~ ^[0-9]+:[0-9]+$ ]] ||
    die "new scratch device/inode identity is malformed"
  SCRATCH_CREATED=yes
  chmod 0600 "$HAOS_SCRATCH_FILE"
  chown root:root "$HAOS_SCRATCH_FILE"
  cryptsetup luksFormat --batch-mode --type luks2 --label "$SCRATCH_LUKS_LABEL" \
    --key-file "$KEY_FILE" \
    "$HAOS_SCRATCH_FILE"
  SCRATCH_LUKS_UUID="$(cryptsetup luksUUID "$HAOS_SCRATCH_FILE")"
  [[ "$SCRATCH_LUKS_UUID" =~ ^[0-9a-f]{8}-[0-9a-f-]{27}$ ]] ||
    die "new scratch LUKS UUID is malformed"
  [[ "$(blkid -p -s LABEL -o value -- "$HAOS_SCRATCH_FILE")" == "$SCRATCH_LUKS_LABEL" ]] ||
    die "new scratch LUKS label did not bind to the drill"
  [[ "$(stat -Lc '%d:%i' "$HAOS_SCRATCH_FILE")" == "$SCRATCH_DEV_INODE" ]] ||
    die "new scratch device/inode changed during formatting"
  write_durable_receipt
  cryptsetup open --key-file "$KEY_FILE" "$HAOS_SCRATCH_FILE" "$HAOS_MAPPER"
  rm -f -- "$KEY_FILE"
  mkfs.ext4 -q -m 0 -L haos-restore "/dev/mapper/$HAOS_MAPPER"
  mount -o rw,nosuid,nodev,noexec "/dev/mapper/$HAOS_MAPPER" "$HAOS_MOUNT"
  chown "$VM_UID:$VM_GID" "$HAOS_MOUNT"
  chmod 0700 "$HAOS_MOUNT"

  xz --decompress --stdout -- "$HAOS_IMAGE_XZ" > "$base_image"
  qemu-img info --output=json "$base_image" |
    jq -e '.format == "qcow2" and (."backing-filename" == null) and (."data-file" == null)' \
      >/dev/null || die "decompressed HAOS image is not a self-contained qcow2"
  qemu-img convert -f qcow2 -O qcow2 "$base_image" "$vm_disk"
  qemu-img resize -f qcow2 "$vm_disk" "${HAOS_VM_DISK_GIB}G"
  install -m 0600 -o "$VM_UID" -g "$VM_GID" "$HAOS_OVMF_VARS" "$vm_vars"
  chown "$VM_UID:$VM_GID" "$vm_disk"
  chmod 0600 "$vm_disk"
  chown root:root "$base_image"
  chmod 0400 "$base_image"
  sync -f "$HAOS_MOUNT"

  PHASE=prepared
  POLICY_SHA256=none
  HOST_NFT_CONTRACT_SHA256=none
  NS_NFT_CONTRACT_SHA256=none
  NETNS_ANCHOR_PID=0
  QEMU_PID=0
  SLIRP_PID=0
  HOST_REFRESHER_PID=0
  NS_REFRESHER_PID=0
  DNS_UPSTREAM_PID=0
  DNS_PROXY_PID=0
  DNSMASQ_PID=0
  INNER_RELAY_PID=0
  OUTER_RELAY_PID=0
  write_state
  trap - EXIT HUP INT TERM
  info "encrypted ephemeral workspace prepared; the key no longer exists on disk"
}

policy_for_phase() {
  local phase="$1"
  case "$phase" in
    bootstrap)
      ACTIVE_POLICY="$TOOL_BOOTSTRAP_POLICY"
      ACTIVE_POLICY_SHA256="$LOCKED_BOOTSTRAP_POLICY_SHA256"
      ;;
    restore)
      ACTIVE_POLICY="$TOOL_RESTORE_POLICY"
      ACTIVE_POLICY_SHA256="$LOCKED_RESTORE_POLICY_SHA256"
      ;;
    *) die "invalid network phase" ;;
  esac
}

ns_exec() {
  pid_is_running "${NETNS_ANCHOR_PID:-0}" || die "network namespace anchor is not running"
  nsenter --target "$NETNS_ANCHOR_PID" --user --mount --net \
    --setuid 0 --setgid 0 -- "$@"
}

create_firewalls() {
  nft -f - <<EOF
table inet $HOST_NFT_TABLE {
  set artifact_v4 { type ipv4_addr; flags timeout; timeout 5m; }
  set ntp_v4 { type ipv4_addr; flags timeout; timeout 5m; }
  counter approved_egress {}
  counter forbidden_egress {}
  chain output {
    type filter hook output priority -40; policy accept;
    meta skuid $VM_UID meta nfproto ipv6 counter name forbidden_egress drop
    meta skuid $VM_UID oifname "lo" ip saddr 127.0.0.1 ip daddr 127.0.0.1 tcp sport $HAOS_RELAY_PORT ct state established accept
    meta skuid $VM_UID ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 } counter name forbidden_egress drop
    meta skuid $VM_UID oifname "$HAOS_UPLINK" ip daddr @artifact_v4 tcp dport 443 counter name approved_egress accept
    meta skuid $VM_UID oifname "$HAOS_UPLINK" ip daddr @ntp_v4 udp dport 123 counter name approved_egress accept
    meta skuid $VM_UID counter name forbidden_egress drop
  }
}
EOF

  ns_exec nft -f - <<EOF
table inet $NS_NFT_TABLE {
  set artifact_v4 { type ipv4_addr; flags timeout; timeout 5m; }
  set ntp_v4 { type ipv4_addr; flags timeout; timeout 5m; }
  counter approved_egress {}
  counter forbidden_egress {}
  chain input {
    type filter hook input priority filter; policy drop;
    iifname "lo" accept
    ct state established,related accept
    iifname "$HAOS_TAP" udp dport 67 accept
    iifname "$HAOS_TAP" ip daddr $GUEST_GATEWAY meta l4proto { tcp, udp } th dport 53 accept
  }
  chain output {
    type filter hook output priority filter; policy drop;
    oifname "lo" accept
    ct state established,related accept
    oifname "$HAOS_TAP" udp sport 67 udp dport 68 accept
    oifname "$HAOS_TAP" ip daddr $GUEST_IP tcp dport 8123 accept
  }
  chain forward {
    type filter hook forward priority filter; policy drop;
    iifname "$HAOS_EGRESS_TAP" oifname "$HAOS_TAP" ct state established,related accept
    iifname "$HAOS_TAP" oifname "$HAOS_EGRESS_TAP" ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4 } counter name forbidden_egress drop
    iifname "$HAOS_TAP" oifname "$HAOS_EGRESS_TAP" ip daddr @artifact_v4 tcp dport 443 counter name approved_egress accept
    iifname "$HAOS_TAP" oifname "$HAOS_EGRESS_TAP" ip daddr @ntp_v4 udp dport 123 counter name approved_egress accept
    iifname "$HAOS_TAP" counter name forbidden_egress drop
    oifname "$HAOS_TAP" counter name forbidden_egress drop
  }
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    oifname "$HAOS_EGRESS_TAP" ip saddr $GUEST_CIDR masquerade
  }
}
EOF

  HOST_NFT_CONTRACT_SHA256="$(nft --json --stateless list table inet "$HOST_NFT_TABLE" | python3 "$TOOL_NFT_CONTRACT")"
  NS_NFT_CONTRACT_SHA256="$(ns_exec nft --json --stateless list table inet "$NS_NFT_TABLE" | python3 "$TOOL_NFT_CONTRACT")"
  [[ "$HOST_NFT_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ &&
     "$NS_NFT_CONTRACT_SHA256" =~ ^[0-9a-f]{64}$ ]] ||
    die "could not record firewall security contracts"
}

set_has_elements() {
  local scope="$1"
  local table="$2"
  local set_name="$3"
  if [[ "$scope" == host ]]; then
    nft --json list set inet "$table" "$set_name"
  else
    ns_exec nft --json list set inet "$table" "$set_name"
  fi | jq -e 'any(.nftables[]; (((.set.elem? // []) | length) > 0))' >/dev/null
}

wait_for_refresher_sets() {
  local scope="$1"
  local table="$2"
  local pid="$3"
  local ready=0
  for _ in $(seq 1 30); do
    if set_has_elements "$scope" "$table" artifact_v4 &&
       set_has_elements "$scope" "$table" ntp_v4; then
      ready=1
      break
    fi
    kill -0 "$pid" 2>/dev/null || die "$scope endpoint refresher failed closed"
    sleep 1
  done
  [[ "$ready" == 1 ]] || die "$scope reviewed endpoint sets did not populate atomically"
}

setup_network() {
  local phase="$1"
  verify_tool_copies
  policy_for_phase "$phase"
  [[ ! -e "$DNSMASQ_CONFIG" ]] || die "stale dnsmasq config exists"
  [[ ! -e "$DNS_UPSTREAM_SOCKET" && ! -L "$DNS_UPSTREAM_SOCKET" ]] ||
    die "stale DNS upstream socket exists"
  python3 "$TOOL_POLICY_COMPILER" dnsmasq "$ACTIVE_POLICY" \
    --expected-sha256 "$ACTIVE_POLICY_SHA256" \
    --interface "$HAOS_TAP" --gateway "$GUEST_GATEWAY" --guest "$GUEST_IP" \
    --upstream "$HAOS_UPSTREAM_DNS" --lease-file "$DNSMASQ_LEASES" \
    > "$DNSMASQ_CONFIG"
  chmod 0600 "$DNSMASQ_CONFIG"
  chown "$VM_UID:$VM_GID" "$DNSMASQ_CONFIG"
  dnsmasq --test --conf-file="$DNSMASQ_CONFIG" >/dev/null

  setpriv --reuid "$VM_UID" --regid "$VM_GID" --init-groups \
    --no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- \
    unshare --user --map-root-user --mount --net --mount-proc \
    python3 -I -c 'import signal; signal.pause()' "haos-namespace-$HAOS_NETNS" \
    </dev/null >/dev/null 2>&1 &
  NETNS_ANCHOR_PID=$!
  for _ in $(seq 1 10); do
    [[ -e "/proc/$NETNS_ANCHOR_PID/ns/net" ]] && break
    kill -0 "$NETNS_ANCHOR_PID" 2>/dev/null || die "network namespace anchor exited"
    sleep 1
  done
  [[ -e "/proc/$NETNS_ANCHOR_PID/ns/net" ]] || die "network namespace did not appear"
  require_process_confinement "$NETNS_ANCHOR_PID" "namespace anchor"
  ns_exec ip link set lo up
  ns_exec ip tuntap add dev "$HAOS_TAP" mode tap user 0 group 0
  ns_exec ip addr add "$GUEST_GATEWAY/24" dev "$HAOS_TAP"
  ns_exec ip link set "$HAOS_TAP" up
  ns_exec sysctl -q -w net.ipv4.ip_forward=1 >/dev/null
  ns_exec sysctl -q -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null
  ns_exec sysctl -q -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null
  create_firewalls

  python3 "$TOOL_POLICY_REFRESHER" "$ACTIVE_POLICY" \
    --expected-sha256 "$ACTIVE_POLICY_SHA256" --upstream "$HAOS_UPSTREAM_DNS" \
    --table "$HOST_NFT_TABLE" --timeout-seconds 300 --interval-seconds 120 \
    </dev/null >/dev/null 2>&1 &
  HOST_REFRESHER_PID=$!
  wait_for_refresher_sets host "$HOST_NFT_TABLE" "$HOST_REFRESHER_PID"

  python3 "$TOOL_EXACT_DNS_PROXY" "$ACTIVE_POLICY" \
    --expected-sha256 "$ACTIVE_POLICY_SHA256" --upstream "$HAOS_UPSTREAM_DNS" \
    --serve-unix "$DNS_UPSTREAM_SOCKET" </dev/null >/dev/null 2>&1 &
  DNS_UPSTREAM_PID=$!
  for _ in $(seq 1 10); do
    [[ -S "$DNS_UPSTREAM_SOCKET" ]] && break
    kill -0 "$DNS_UPSTREAM_PID" 2>/dev/null || die "host exact-DNS forwarder failed closed"
    sleep 1
  done
  [[ -S "$DNS_UPSTREAM_SOCKET" ]] || die "host exact-DNS forwarder socket did not appear"
  chown "$VM_UID:$VM_GID" "$DNS_UPSTREAM_SOCKET"
  chmod 0600 "$DNS_UPSTREAM_SOCKET"

  setpriv --reuid "$VM_UID" --regid "$VM_GID" --init-groups \
    --no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- \
    slirp4netns --configure --mtu=1500 --disable-host-loopback \
    "$NETNS_ANCHOR_PID" "$HAOS_EGRESS_TAP" </dev/null >/dev/null 2>&1 &
  SLIRP_PID=$!
  for _ in $(seq 1 20); do
    ns_exec ip link show dev "$HAOS_EGRESS_TAP" >/dev/null 2>&1 && break
    kill -0 "$SLIRP_PID" 2>/dev/null || die "slirp4netns exited during setup"
    sleep 1
  done
  ns_exec ip link show dev "$HAOS_EGRESS_TAP" >/dev/null 2>&1 ||
    die "slirp4netns egress interface did not appear"
  require_process_confinement "$SLIRP_PID" slirp4netns
  ns_exec sysctl -q -w "net.ipv6.conf.$HAOS_EGRESS_TAP.disable_ipv6=1" >/dev/null

  python3 "$TOOL_POLICY_REFRESHER" "$ACTIVE_POLICY" \
    --expected-sha256 "$ACTIVE_POLICY_SHA256" --upstream "$HAOS_UPSTREAM_DNS" \
    --table "$NS_NFT_TABLE" --timeout-seconds 300 --interval-seconds 120 \
    --netns-pid "$NETNS_ANCHOR_PID" --netns-marker "haos-namespace-$HAOS_NETNS" \
    </dev/null >/dev/null 2>&1 &
  NS_REFRESHER_PID=$!
  wait_for_refresher_sets namespace "$NS_NFT_TABLE" "$NS_REFRESHER_PID"

  nsenter --target "$NETNS_ANCHOR_PID" --user --mount --net \
    --setuid 0 --setgid 0 -- python3 "$TOOL_EXACT_DNS_PROXY" "$ACTIVE_POLICY" \
    --expected-sha256 "$ACTIVE_POLICY_SHA256" --upstream-unix "$DNS_UPSTREAM_SOCKET" \
    --bind "$GUEST_GATEWAY" --port 53 </dev/null >/dev/null 2>&1 &
  DNS_PROXY_PID=$!
  sleep 1
  kill -0 "$DNS_PROXY_PID" 2>/dev/null || die "exact-QNAME DNS proxy did not remain running"

  nsenter --target "$NETNS_ANCHOR_PID" --user --mount --net \
    --setuid 0 --setgid 0 -- dnsmasq --no-daemon --conf-file="$DNSMASQ_CONFIG" \
    </dev/null >/dev/null 2>&1 &
  DNSMASQ_PID=$!
  sleep 1
  kill -0 "$DNSMASQ_PID" 2>/dev/null || die "dnsmasq did not remain running"
  POLICY_SHA256="$ACTIVE_POLICY_SHA256"
}

start_relays() {
  rm -f -- "$UI_SOCKET"
  nsenter --target "$NETNS_ANCHOR_PID" --user --mount --net \
    --setuid 0 --setgid 0 -- setpriv --no-new-privs --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all -- socat "UNIX-LISTEN:$UI_SOCKET,fork,mode=0600" \
    "TCP4:$GUEST_IP:8123" </dev/null >/dev/null 2>&1 &
  INNER_RELAY_PID=$!
  sleep 1
  kill -0 "$INNER_RELAY_PID" 2>/dev/null || die "inner UI relay failed"
  require_process_confinement "$INNER_RELAY_PID" "inner relay"
  setpriv --reuid "$VM_UID" --regid "$VM_GID" --init-groups \
    --no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- socat \
    "TCP4-LISTEN:$HAOS_RELAY_PORT,bind=127.0.0.1,reuseaddr,fork" \
    "UNIX-CONNECT:$UI_SOCKET" </dev/null >/dev/null 2>&1 &
  OUTER_RELAY_PID=$!
  sleep 1
  kill -0 "$OUTER_RELAY_PID" 2>/dev/null || die "loopback UI relay failed"
  require_process_confinement "$OUTER_RELAY_PID" "outer relay"
  local listeners
  listeners="$(ss -H -ltn "sport = :$HAOS_RELAY_PORT")"
  [[ -n "$listeners" ]] || die "UI relay is not listening"
  awk '{print $4}' <<< "$listeners" | grep -Ev '^127\.0\.0\.1:' >/dev/null &&
    die "UI relay escaped loopback"
}

launch_qemu() {
  local vm_disk="$HAOS_MOUNT/haos-restore.qcow2"
  local vm_vars="$HAOS_MOUNT/OVMF_VARS.fd"
  local monitor="$VM_RUN_DIR/monitor.sock"
  local serial="$VM_RUN_DIR/serial.sock"
  local guest_agent="$VM_RUN_DIR/guest-agent.sock"
  rm -f -- "$monitor" "$serial" "$guest_agent"
  nsenter --target "$NETNS_ANCHOR_PID" --user --mount --net \
    --setuid 0 --setgid 0 -- setpriv --no-new-privs --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all -- qemu-system-x86_64 \
    -name "haos-restore-$DRILL_ID" \
    -machine q35,accel=kvm -enable-kvm -cpu host \
    -smp "$HAOS_VM_CPUS" -m "$HAOS_VM_MEMORY_MIB" \
    -nodefaults -no-user-config -display none -rtc base=utc,clock=host \
    -boot order=c,menu=off \
    -drive "if=pflash,format=raw,readonly=on,file=$HAOS_OVMF_CODE" \
    -drive "if=pflash,format=raw,file=$vm_vars" \
    -object rng-random,id=rng0,filename=/dev/urandom \
    -device virtio-rng-pci,rng=rng0 \
    -device virtio-scsi-pci,id=scsi0 \
    -drive "file=$vm_disk,if=none,id=osdisk,format=qcow2,cache=none,discard=unmap,aio=native" \
    -device scsi-hd,drive=osdisk \
    -netdev "tap,id=net0,ifname=$HAOS_TAP,script=no,downscript=no" \
    -device "virtio-net-pci,netdev=net0,mac=$GUEST_MAC" \
    -device virtio-serial-pci \
    -chardev "socket,id=qga0,path=$guest_agent,server=on,wait=off" \
    -device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0 \
    -monitor "unix:$monitor,server=on,wait=off" \
    -serial "unix:$serial,server=on,wait=off" \
    </dev/null >/dev/null 2>&1 &
  QEMU_PID=$!
  for _ in $(seq 1 20); do
    [[ -S "$monitor" ]] && break
    kill -0 "$QEMU_PID" 2>/dev/null || die "QEMU exited during startup"
    sleep 1
  done
  [[ -S "$monitor" ]] || die "QEMU monitor did not appear"
  require_process_confinement "$QEMU_PID" QEMU
}

network_failure_cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  set +e
  stop_pid "${QEMU_PID:-0}" "haos-restore-${DRILL_ID:-missing}" 5
  stop_pid "${OUTER_RELAY_PID:-0}" socat 2
  stop_pid "${INNER_RELAY_PID:-0}" socat 2
  stop_pid "${DNS_PROXY_PID:-0}" exact_dns_proxy.py 2
  stop_pid "${DNSMASQ_PID:-0}" dnsmasq 2
  stop_pid "${NS_REFRESHER_PID:-0}" policy_refresher.py 2
  stop_pid "${SLIRP_PID:-0}" slirp4netns 2
  stop_pid "${NETNS_ANCHOR_PID:-0}" "haos-namespace-$HAOS_NETNS" 2
  stop_pid "${DNS_UPSTREAM_PID:-0}" exact_dns_proxy.py 2
  stop_pid "${HOST_REFRESHER_PID:-0}" policy_refresher.py 2
  teardown_network
  NETNS_ANCHOR_PID=0 QEMU_PID=0 SLIRP_PID=0
  HOST_REFRESHER_PID=0 NS_REFRESHER_PID=0 DNS_UPSTREAM_PID=0 DNS_PROXY_PID=0
  DNSMASQ_PID=0 INNER_RELAY_PID=0 OUTER_RELAY_PID=0
  [[ -f "$STATE_FILE" ]] && write_state
  exit "$status"
}

start_bootstrap() {
  local config="$1"
  local acknowledgement="$2"
  [[ "$acknowledgement" == --confirm-sterile-bootstrap ]] || usage
  static_preflight "$config"
  acquire_lock
  load_state
  [[ "$PHASE" == prepared ]] || die "workspace is not in prepared state"
  [[ "$POLICY_SHA256" == none ]] || die "prepared workspace unexpectedly has a policy"
  trap network_failure_cleanup EXIT HUP INT TERM
  setup_network bootstrap
  launch_qemu
  PHASE=bootstrap
  write_state
  trap - EXIT HUP INT TERM
  info "sterile HAOS bootstrap is running with no host UI relay"
  info "wait for first boot, then request ACPI shutdown before the strict restore transition"
}

pid_is_running() {
  local pid="${1:-0}"
  [[ "$pid" =~ ^[0-9]+$ && "$pid" != 0 && -d "/proc/$pid" ]]
}

pid_has_marker() {
  local pid="$1"
  local marker="$2"
  pid_is_running "$pid" || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$marker"
}

process_status_field() {
  local pid="$1"
  local field="$2"
  awk -v wanted="$field:" '$1 == wanted {print $2}' "/proc/$pid/status"
}

require_process_confinement() {
  local pid="$1"
  local label="$2"
  local field value
  pid_is_running "$pid" || die "$label is not running"
  for field in CapInh CapPrm CapEff CapBnd CapAmb; do
    value="$(process_status_field "$pid" "$field")"
    [[ "$value" =~ ^0+$ ]] || die "$label $field is not empty"
  done
  [[ "$(process_status_field "$pid" NoNewPrivs)" == 1 ]] ||
    die "$label no-new-privileges is not enforced"
}

stop_pid() {
  local pid="${1:-0}"
  local marker="$2"
  local seconds="$3"
  local remaining
  pid_is_running "$pid" || return 0
  pid_has_marker "$pid" "$marker" || die "refusing to stop PID $pid without marker $marker"
  kill -TERM "$pid"
  remaining="$seconds"
  while pid_is_running "$pid" && (( remaining > 0 )); do
    sleep 1
    remaining=$((remaining - 1))
  done
  if pid_is_running "$pid"; then
    pid_has_marker "$pid" "$marker" || die "PID identity changed during shutdown"
    kill -KILL "$pid"
  fi
}

request_shutdown() {
  local config="$1"
  static_preflight "$config"
  acquire_lock
  load_state
  pid_is_running "$QEMU_PID" || die "QEMU is not running"
  pid_has_marker "$QEMU_PID" "haos-restore-$DRILL_ID" || die "QEMU PID identity mismatch"
  [[ -S "$VM_RUN_DIR/monitor.sock" ]] || die "QEMU monitor is unavailable"
  printf 'system_powerdown\n' | timeout 5 socat - "UNIX-CONNECT:$VM_RUN_DIR/monitor.sock" \
    >/dev/null
  info "ACPI shutdown requested; wait for status to report qemu_running=no"
}

stop_helpers() {
  stop_pid "${OUTER_RELAY_PID:-0}" socat 2
  stop_pid "${INNER_RELAY_PID:-0}" socat 2
  stop_pid "${DNS_PROXY_PID:-0}" exact_dns_proxy.py 2
  stop_pid "${DNSMASQ_PID:-0}" dnsmasq 2
  stop_pid "${NS_REFRESHER_PID:-0}" policy_refresher.py 2
  stop_pid "${SLIRP_PID:-0}" slirp4netns 2
  stop_pid "${NETNS_ANCHOR_PID:-0}" "haos-namespace-$HAOS_NETNS" 2
  stop_pid "${DNS_UPSTREAM_PID:-0}" exact_dns_proxy.py 2
  stop_pid "${HOST_REFRESHER_PID:-0}" policy_refresher.py 2
  OUTER_RELAY_PID=0
  INNER_RELAY_PID=0
  DNS_PROXY_PID=0
  DNSMASQ_PID=0
  NS_REFRESHER_PID=0
  DNS_UPSTREAM_PID=0
  HOST_REFRESHER_PID=0
  SLIRP_PID=0
  NETNS_ANCHOR_PID=0
  rm -f -- "$UI_SOCKET" "$DNS_UPSTREAM_SOCKET" "$DNSMASQ_CONFIG" "$DNSMASQ_LEASES"
}

teardown_network() {
  [[ -z "$(pgrep -u "$VM_UID" || true)" ]] ||
    die "refusing to remove network guards while the dedicated UID owns processes"
  nft delete table inet "$HOST_NFT_TABLE" >/dev/null 2>&1 || true
  HOST_NFT_CONTRACT_SHA256=none
  NS_NFT_CONTRACT_SHA256=none
}

transition_restore() {
  local config="$1"
  local acknowledgement="$2"
  [[ "$acknowledgement" == --confirm-pristine-no-production-data ]] || usage
  static_preflight "$config"
  acquire_lock
  load_state
  [[ "$PHASE" == bootstrap ]] || die "workspace is not in bootstrap phase"
  [[ "$POLICY_SHA256" == "$LOCKED_BOOTSTRAP_POLICY_SHA256" ]] ||
    die "bootstrap policy digest is not the locked value"
  ! pid_is_running "$QEMU_PID" ||
    die "shut down the pristine VM before changing to restore policy"
  stop_helpers
  teardown_network
  QEMU_PID=0
  write_state
  trap network_failure_cleanup EXIT HUP INT TERM
  setup_network restore
  start_relays
  launch_qemu
  PHASE=restore
  write_state
  trap - EXIT HUP INT TERM
  info "strict restore phase is available only at $HAOS_UI_SCHEME://127.0.0.1:$HAOS_RELAY_PORT"
  info "the full protected backup may now be uploaded through Home Assistant onboarding"
}

counter_packets() {
  local namespace="$1"
  local table="$2"
  local counter="$3"
  local output
  if [[ "$namespace" == host ]]; then
    output="$(nft list counter inet "$table" "$counter")"
  else
    output="$(ns_exec nft list counter inet "$table" "$counter")"
  fi
  sed -nE 's/.*packets ([0-9]+).*/\1/p' <<< "$output" | head -n 1
}

firewall_contract_digest() {
  local scope="$1"
  local table="$2"
  if [[ "$scope" == host ]]; then
    nft --json --stateless list table inet "$table"
  else
    ns_exec nft --json --stateless list table inet "$table"
  fi | python3 "$TOOL_NFT_CONTRACT"
}

status_runtime() {
  local config="$1"
  local anchor=no qemu=no slirp=no host_refresher=no ns_refresher=no
  local dns_upstream=no dns_proxy=no
  local dnsmasq=no inner=no outer=no mapper=no mounted=no
  static_preflight "$config"
  load_state
  pid_is_running "$NETNS_ANCHOR_PID" && anchor=yes
  pid_is_running "$QEMU_PID" && qemu=yes
  pid_is_running "$SLIRP_PID" && slirp=yes
  pid_is_running "$HOST_REFRESHER_PID" && host_refresher=yes
  pid_is_running "$NS_REFRESHER_PID" && ns_refresher=yes
  pid_is_running "$DNS_UPSTREAM_PID" && dns_upstream=yes
  pid_is_running "$DNS_PROXY_PID" && dns_proxy=yes
  pid_is_running "$DNSMASQ_PID" && dnsmasq=yes
  pid_is_running "$INNER_RELAY_PID" && inner=yes
  pid_is_running "$OUTER_RELAY_PID" && outer=yes
  [[ -e "/dev/mapper/$HAOS_MAPPER" ]] && mapper=yes
  if mountpoint -q -- "$HAOS_MOUNT" &&
     [[ "$(findmnt -rn -o SOURCE -T "$HAOS_MOUNT")" == "/dev/mapper/$HAOS_MAPPER" ]]; then
    mounted=yes
  fi
  printf 'drill_id=%s\nphase=%s\npolicy_sha256=%s\n' "$DRILL_ID" "$PHASE" "$POLICY_SHA256"
  printf 'namespace_anchor_running=%s\nqemu_running=%s\nslirp_running=%s\n' "$anchor" "$qemu" "$slirp"
  printf 'host_policy_refresher_running=%s\nnamespace_policy_refresher_running=%s\n' "$host_refresher" "$ns_refresher"
  printf 'host_exact_dns_forwarder_running=%s\nexact_dns_proxy_running=%s\n' "$dns_upstream" "$dns_proxy"
  printf 'dnsmasq_running=%s\ninner_relay_running=%s\n' "$dnsmasq" "$inner"
  printf 'outer_relay_running=%s\nmapper_active=%s\nmount_verified=%s\n' "$outer" "$mapper" "$mounted"
  if [[ "$PHASE" == restore ]]; then
    printf 'ui=%s://127.0.0.1:%s\n' "$HAOS_UI_SCHEME" "$HAOS_RELAY_PORT"
  else
    printf 'ui=disabled\n'
  fi
}

attestation_bool() {
  [[ "${!1}" == yes ]] || die "$1 must be yes"
}

validate_and_report() {
  local config="$1"
  local attestation
  local report temporary http_code approved_host denied_host approved_ns denied_ns
  local listener_addresses value current_host_contract current_ns_contract result=pass
  attestation="$(readlink -m -- "$2")"
  require_absolute_path ATTESTATION_FILE "$attestation"
  static_preflight "$config"
  acquire_lock
  load_state
  [[ "$PHASE" == restore ]] || die "validation requires restore phase"
  [[ "$POLICY_SHA256" == "$LOCKED_RESTORE_POLICY_SHA256" ]] ||
    die "validation requires the locked restore policy"
  require_trusted_file_tree "$attestation"
  load_kv_file "$attestation" "$ATTESTATION_KEYS" 1
  for value in RESTORE_COMPLETED ORIGINAL_LOGIN_SUCCEEDED DASHBOARD_LOADED \
    CORE_CHECK_PASSED CONTROLLED_REBOOT_PASSED SUPERVISOR_HEALTHY \
    FORBIDDEN_EGRESS_REVIEWED; do
    attestation_bool "$value"
  done
  [[ "$OBSERVED_HAOS_VERSION" == "$LOCKED_HAOS_VERSION" ]] ||
    die "observed HAOS version does not match"
  [[ "$OBSERVED_CORE_VERSION" == "$LOCKED_BACKUP_CORE_VERSION" ]] ||
    die "observed Core version does not match the backup"
  [[ "$EXPECTED_ADDON_COUNT" == "$LOCKED_BACKUP_ADDON_COUNT" &&
     "$RESTORED_ADDON_COUNT" == "$LOCKED_BACKUP_ADDON_COUNT" ]] ||
    die "restored add-on count does not match"

  pid_has_marker "$NETNS_ANCHOR_PID" "haos-namespace-$HAOS_NETNS" ||
    die "network namespace anchor identity mismatch"
  require_process_confinement "$NETNS_ANCHOR_PID" "namespace anchor"
  pid_has_marker "$QEMU_PID" "haos-restore-$DRILL_ID" || die "QEMU is not the drill VM"
  require_process_confinement "$QEMU_PID" QEMU
  pid_has_marker "$SLIRP_PID" slirp4netns || die "slirp4netns is not running"
  require_process_confinement "$SLIRP_PID" slirp4netns
  pid_has_marker "$HOST_REFRESHER_PID" policy_refresher.py &&
    pid_has_marker "$HOST_REFRESHER_PID" "$HOST_NFT_TABLE" ||
    die "host policy refresher is not running"
  pid_has_marker "$NS_REFRESHER_PID" policy_refresher.py &&
    pid_has_marker "$NS_REFRESHER_PID" "$NS_NFT_TABLE" &&
    pid_has_marker "$NS_REFRESHER_PID" "haos-namespace-$HAOS_NETNS" ||
    die "namespace policy refresher is not running"
  pid_has_marker "$DNS_UPSTREAM_PID" exact_dns_proxy.py &&
    pid_has_marker "$DNS_UPSTREAM_PID" "$DNS_UPSTREAM_SOCKET" ||
    die "host exact-DNS forwarder is not running"
  pid_has_marker "$DNS_PROXY_PID" exact_dns_proxy.py ||
    die "exact-QNAME DNS proxy is not running"
  pid_has_marker "$DNSMASQ_PID" dnsmasq || die "dnsmasq is not running"
  pid_has_marker "$INNER_RELAY_PID" socat || die "inner relay is not running"
  pid_has_marker "$OUTER_RELAY_PID" socat || die "outer relay is not running"
  require_process_confinement "$INNER_RELAY_PID" "inner relay"
  require_process_confinement "$OUTER_RELAY_PID" "outer relay"
  [[ -e "/dev/mapper/$HAOS_MAPPER" ]] || die "LUKS mapper is not active"
  [[ "$(findmnt -rn -o SOURCE -T "$HAOS_MOUNT")" == "/dev/mapper/$HAOS_MAPPER" ]] ||
    die "encrypted scratch mount is not verified"
  verify_active_mapper_identity
  nft list table inet "$HOST_NFT_TABLE" >/dev/null
  ns_exec nft list table inet "$NS_NFT_TABLE" >/dev/null
  set_has_elements host "$HOST_NFT_TABLE" artifact_v4 &&
    set_has_elements host "$HOST_NFT_TABLE" ntp_v4 ||
    die "host reviewed endpoint sets are empty"
  set_has_elements namespace "$NS_NFT_TABLE" artifact_v4 &&
    set_has_elements namespace "$NS_NFT_TABLE" ntp_v4 ||
    die "namespace reviewed endpoint sets are empty"
  current_host_contract="$(firewall_contract_digest host "$HOST_NFT_TABLE")"
  current_ns_contract="$(firewall_contract_digest namespace "$NS_NFT_TABLE")"
  [[ "$current_host_contract" == "$HOST_NFT_CONTRACT_SHA256" ]] ||
    die "host firewall security structure changed after creation"
  [[ "$current_ns_contract" == "$NS_NFT_CONTRACT_SHA256" ]] ||
    die "namespace firewall security structure changed after creation"
  listener_addresses="$(ss -H -ltn "sport = :$HAOS_RELAY_PORT" | awk '{print $4}')"
  [[ -n "$listener_addresses" ]] || die "UI relay is not listening"
  grep -Ev '^127\.0\.0\.1:' <<< "$listener_addresses" >/dev/null &&
    die "UI relay has a non-loopback listener"

  if [[ "$HAOS_UI_SCHEME" == https ]]; then
    http_code="$(curl --insecure --silent --show-error --output /dev/null \
      --max-time 10 --max-redirs 0 --write-out '%{http_code}' \
      "https://127.0.0.1:$HAOS_RELAY_PORT/")" || http_code=000
  else
    http_code="$(curl --silent --show-error --output /dev/null --max-time 10 \
      --max-redirs 0 --write-out '%{http_code}' \
      "http://127.0.0.1:$HAOS_RELAY_PORT/")" || http_code=000
  fi
  [[ "$http_code" == 200 ]] || result=fail
  approved_host="$(counter_packets host "$HOST_NFT_TABLE" approved_egress)"
  denied_host="$(counter_packets host "$HOST_NFT_TABLE" forbidden_egress)"
  approved_ns="$(counter_packets "$HAOS_NETNS" "$NS_NFT_TABLE" approved_egress)"
  denied_ns="$(counter_packets "$HAOS_NETNS" "$NS_NFT_TABLE" forbidden_egress)"
  for value in approved_host denied_host approved_ns denied_ns; do
    [[ "${!value}" =~ ^[0-9]+$ ]] || die "could not read redacted firewall counter $value"
  done

  report="$HAOS_REPORT_DIR/haos-restore-$DRILL_ID.json"
  temporary="$RUN_ROOT/report.json.new"
  jq -n \
    --arg result "$result" --arg drill_id "$DRILL_ID" \
    --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg haos_version "$LOCKED_HAOS_VERSION" \
    --arg image_sha256 "$LOCKED_HAOS_IMAGE_SHA256" \
    --arg backup_slug "$LOCKED_BACKUP_SLUG" \
    --arg backup_sha256 "$LOCKED_BACKUP_SHA256" \
    --arg core_version "$LOCKED_BACKUP_CORE_VERSION" \
    --arg policy_sha256 "$LOCKED_RESTORE_POLICY_SHA256" \
    --arg host_nft_contract_sha256 "$HOST_NFT_CONTRACT_SHA256" \
    --arg namespace_nft_contract_sha256 "$NS_NFT_CONTRACT_SHA256" \
    --arg http_code "$http_code" \
    --argjson expected_addons "$LOCKED_BACKUP_ADDON_COUNT" \
    --argjson restored_addons "$RESTORED_ADDON_COUNT" \
    --argjson approved_host "$approved_host" --argjson denied_host "$denied_host" \
    --argjson approved_ns "$approved_ns" --argjson denied_ns "$denied_ns" '
      {
        schema_version: 1,
        result: $result,
        drill_id: $drill_id,
        generated_at: $generated_at,
        immutable_inputs: {
          haos_version: $haos_version,
          haos_image_sha256: $image_sha256,
          backup_slug: $backup_slug,
          backup_sha256: $backup_sha256,
          core_version: $core_version,
          expected_addons: $expected_addons,
          restore_policy_sha256: $policy_sha256,
          host_nft_contract_sha256: $host_nft_contract_sha256,
          namespace_nft_contract_sha256: $namespace_nft_contract_sha256
        },
        isolation: {
          network_namespace: true,
          ipv6_disabled: true,
          ui_loopback_only: true,
          ui_http_status_class: ($http_code[0:1] + "xx"),
          host_approved_packets: $approved_host,
          host_denied_packets: $denied_host,
          namespace_approved_packets: $approved_ns,
          namespace_denied_packets: $denied_ns,
          forbidden_egress_reviewed: true
        },
        recovery: {
          restore_completed: true,
          original_login_succeeded: true,
          dashboard_loaded: true,
          core_check_passed: true,
          controlled_reboot_passed: true,
          supervisor_healthy: true,
          restored_addons: $restored_addons
        }
      }
    ' > "$temporary"
  install -m 0600 -o root -g root "$temporary" "$report"
  rm -f -- "$temporary"
  info "redacted validation report written to $report"
  [[ "$result" == pass ]] || die "validation report recorded a failed UI reachability check"
}

stop_uid_processes_with_marker() {
  local marker="$1"
  local seconds="$2"
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    pid_has_marker "$pid" "$marker" || continue
    stop_pid "$pid" "$marker" "$seconds"
  done < <(pgrep -u "$VM_UID" || true)
}

recover_stale_processes() {
  # Order is deliberate: QEMU and user-facing relays first, egress next, and
  # the namespace anchor last. Firewall rules remain installed throughout.
  stop_uid_processes_with_marker 'haos-restore-' 20
  stop_uid_processes_with_marker socat 3
  stop_uid_processes_with_marker exact_dns_proxy.py 3
  stop_uid_processes_with_marker dnsmasq 3
  stop_uid_processes_with_marker policy_refresher.py 3
  stop_uid_processes_with_marker slirp4netns 3
  stop_uid_processes_with_marker "haos-namespace-$HAOS_NETNS" 3
  [[ -z "$(pgrep -u "$VM_UID" || true)" ]] ||
    die "unknown dedicated-UID process remains; network guards were retained"
}

recover_stale_root_helpers() {
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    if pid_has_marker "$pid" "$TOOL_POLICY_REFRESHER"; then
      stop_pid "$pid" "$TOOL_POLICY_REFRESHER" 3
    elif pid_has_marker "$pid" "$TOOL_EXACT_DNS_PROXY" &&
         pid_has_marker "$pid" "$DNS_UPSTREAM_SOCKET"; then
      stop_pid "$pid" "$DNS_UPSTREAM_SOCKET" 3
    fi
  done < <(pgrep -u 0 || true)
}

cleanup_all() {
  local config="$1"
  local acknowledgement="$2"
  [[ "$acknowledgement" == --confirm-destroy-ephemeral-restore ]] || usage
  require_root
  validate_installation
  load_config "$config"
  validate_paths_and_values
  for command in awk blkid cryptsetup find findmnt flock ip kill losetup mountpoint nft pgrep \
    python3 readlink rm rmdir sed sha256sum stat sync umount; do
    require_command "$command"
  done
  validate_vm_user
  validate_destructive_directories
  acquire_lock
  if [[ -f "$STATE_FILE" ]]; then
    load_state
    load_durable_receipt
  else
    load_durable_receipt
    PHASE=recovery
    POLICY_SHA256=unknown
    HOST_NFT_CONTRACT_SHA256=unknown
    NS_NFT_CONTRACT_SHA256=unknown
    NETNS_ANCHOR_PID=0 QEMU_PID=0 SLIRP_PID=0
    HOST_REFRESHER_PID=0 NS_REFRESHER_PID=0 DNS_UPSTREAM_PID=0
    DNS_PROXY_PID=0 DNSMASQ_PID=0 INNER_RELAY_PID=0 OUTER_RELAY_PID=0
  fi
  if [[ -e "/dev/mapper/$HAOS_MAPPER" ]]; then
    verify_active_mapper_identity
  fi
  stop_pid "$QEMU_PID" "haos-restore-$DRILL_ID" 20
  stop_pid "$OUTER_RELAY_PID" socat 3
  stop_pid "$INNER_RELAY_PID" socat 3
  stop_pid "$DNS_PROXY_PID" exact_dns_proxy.py 3
  stop_pid "$DNSMASQ_PID" dnsmasq 3
  stop_pid "$NS_REFRESHER_PID" policy_refresher.py 3
  stop_pid "$SLIRP_PID" slirp4netns 3
  stop_pid "$NETNS_ANCHOR_PID" "haos-namespace-$HAOS_NETNS" 3
  stop_pid "$DNS_UPSTREAM_PID" "$DNS_UPSTREAM_SOCKET" 3
  stop_pid "$HOST_REFRESHER_PID" "$HOST_NFT_TABLE" 3
  recover_stale_processes
  recover_stale_root_helpers
  teardown_network
  if [[ -e "/dev/mapper/$HAOS_MAPPER" ]]; then
    verify_active_mapper_identity
  fi
  if mountpoint -q -- "$HAOS_MOUNT"; then
    [[ "$(findmnt -rn -o SOURCE -T "$HAOS_MOUNT")" == "/dev/mapper/$HAOS_MAPPER" ]] ||
      die "refusing to unmount an unexpected filesystem"
    umount -- "$HAOS_MOUNT"
  fi
  if [[ -e "/dev/mapper/$HAOS_MAPPER" ]]; then
    verify_active_mapper_identity
    cryptsetup close "$HAOS_MAPPER"
  fi
  rm -f -- "$KEY_FILE" 2>/dev/null || true
  if [[ -e "$HAOS_SCRATCH_FILE" ]]; then
    [[ -f "$HAOS_SCRATCH_FILE" && ! -L "$HAOS_SCRATCH_FILE" &&
       "$(dirname -- "$HAOS_SCRATCH_FILE")" == "$HAOS_SCRATCH_PARENT" ]] ||
      die "refusing to delete an unexpected scratch path"
    cryptsetup isLuks "$HAOS_SCRATCH_FILE" ||
      die "refusing to delete a scratch file that is not a LUKS container"
    verify_state_scratch_identity
    rm -f -- "$HAOS_SCRATCH_FILE"
  fi
  [[ ! -e "$HAOS_SCRATCH_FILE" ]] || die "scratch file deletion did not complete"
  rm -f -- "$RECEIPT_FILE"
  sync -f "$HAOS_REPORT_DIR"
  safe_remove_runtime_roots
  info "cryptographic erasure complete: transient key and LUKS container removed; no SSD overwrite is claimed"
}

main() {
  [[ $# -ge 2 ]] || usage
  local command="$1"
  local config="$2"
  case "$command" in
    preflight)
      [[ $# == 2 ]] || usage
      preflight_clean "$config"
      info "preflight passed; no runtime state was created"
      ;;
    prepare)
      [[ $# == 3 ]] || usage
      prepare_workspace "$config" "$3"
      ;;
    start-bootstrap)
      [[ $# == 3 ]] || usage
      start_bootstrap "$config" "$3"
      ;;
    request-shutdown)
      [[ $# == 2 ]] || usage
      request_shutdown "$config"
      ;;
    transition-restore)
      [[ $# == 3 ]] || usage
      transition_restore "$config" "$3"
      ;;
    status)
      [[ $# == 2 ]] || usage
      status_runtime "$config"
      ;;
    validate)
      [[ $# == 3 ]] || usage
      validate_and_report "$config" "$3"
      ;;
    cleanup)
      [[ $# == 3 ]] || usage
      cleanup_all "$config" "$3"
      ;;
    *) usage ;;
  esac
}

main "$@"
