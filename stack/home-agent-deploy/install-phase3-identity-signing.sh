#!/usr/bin/env bash
set -euo pipefail

# Install the immutable Phase 3 signing code and its fixed systemd-run launcher.
# Key provisioning is deliberately separate; this installer never sees a key.

[[ $# -eq 0 ]] || {
  printf '%s\n' 'identity signing installer accepts no arguments' >&2
  exit 64
}
[[ $(id -u) -eq 0 ]] || {
  printf '%s\n' 'identity signing installer requires root' >&2
  exit 77
}

readonly source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly operator="$source_root/operator"
readonly target=/usr/local/libexec/home-agent-identity-signing
readonly launcher=/usr/local/sbin/home-agent-identity-signing

install -d -o root -g root -m 0755 "$target"
for name in \
  migrate_legacy_identity.py \
  reviewed_identity_payload.py \
  reviewed_identity_packet_compiler.py \
  phase3_identity_signing_ceremony.py \
  phase3_identity_credential_provisioner.py \
  phase3_writer_freeze_evidence.py \
  phase3_writer_freeze_ceremony.py \
  phase3_privacy_cutover_evidence.py \
  phase3_privacy_cutover_ceremony.py \
  phase3_semantic_cutover_packet.py \
  phase3_semantic_cutover_ceremony.py
do
  install -o root -g root -m 0644 "$operator/$name" "$target/$name"
done
install -o root -g root -m 0755 \
  "$operator/phase3_identity_signing.sh" "$launcher"
install -o root -g root -m 0750 \
  "$operator/phase3_identity_credential_provisioner.sh" \
  /usr/local/sbin/home-agent-provision-identity-signing-credentials

temporary=$(mktemp "$target/.installed.sha256.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
(
  cd "$target"
  sha256sum \
    migrate_legacy_identity.py \
    reviewed_identity_payload.py \
    reviewed_identity_packet_compiler.py \
    phase3_identity_signing_ceremony.py \
    phase3_identity_credential_provisioner.py \
    phase3_writer_freeze_evidence.py \
    phase3_writer_freeze_ceremony.py \
    phase3_privacy_cutover_evidence.py \
    phase3_privacy_cutover_ceremony.py \
    phase3_semantic_cutover_packet.py \
    phase3_semantic_cutover_ceremony.py
) >"$temporary"
chown root:root "$temporary"
chmod 0644 "$temporary"
mv -f -- "$temporary" "$target/installed.sha256"
trap - EXIT

for credential in \
  home-agent-identity-policy.cred \
  home-agent-identity-commitment.cred \
  home-agent-identity-review.cred \
  home-agent-identity-finalization.cred \
  home-agent-identity-writer-freeze-policy.cred \
  home-agent-identity-writer-freeze.cred \
  home-agent-identity-privacy-probe-policy.cred \
  home-agent-identity-privacy-probe.cred \
  home-agent-identity-semantic-cutover-policy.cred \
  home-agent-identity-semantic-cutover.cred
do
  [[ ! -e "/etc/credstore.encrypted/$credential" ]] || \
    chmod 0600 "/etc/credstore.encrypted/$credential"
done

printf '%s\n' 'identity signing code installed; encrypted credential provisioning remains fail-closed'
