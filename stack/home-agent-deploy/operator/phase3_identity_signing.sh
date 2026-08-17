#!/usr/bin/env bash
set -euo pipefail

# Launch one fixed signing phase in a transient, networkless systemd service.
# Encrypted credentials are host-bound and purpose-separated. No credential or
# private-content path is accepted from the caller.

readonly install_root=/usr/local/libexec/home-agent-identity-signing
readonly manifest="$install_root/installed.sha256"
readonly credential_receipt=/srv/home-agent/config/phase3-identity-signing-credentials-e5ae.json
readonly policy_credential=/etc/credstore.encrypted/home-agent-identity-policy.cred
readonly commitment_credential=/etc/credstore.encrypted/home-agent-identity-commitment.cred
readonly review_credential=/etc/credstore.encrypted/home-agent-identity-review.cred
readonly finalization_credential=/etc/credstore.encrypted/home-agent-identity-finalization.cred
readonly writer_freeze_policy_credential=/etc/credstore.encrypted/home-agent-identity-writer-freeze-policy.cred
readonly writer_freeze_credential=/etc/credstore.encrypted/home-agent-identity-writer-freeze.cred
readonly privacy_probe_policy_credential=/etc/credstore.encrypted/home-agent-identity-privacy-probe-policy.cred
readonly privacy_probe_credential=/etc/credstore.encrypted/home-agent-identity-privacy-probe.cred
readonly semantic_cutover_policy_credential=/etc/credstore.encrypted/home-agent-identity-semantic-cutover-policy.cred
readonly semantic_cutover_credential=/etc/credstore.encrypted/home-agent-identity-semantic-cutover.cred

fail() {
  printf '%s\n' 'identity signing launcher failed closed' >&2
  exit 78
}

[[ $# -eq 1 ]] || fail
phase=$1
required_purpose=
case "$phase" in
  stage)
    unit=home-agent-identity-packet-stage.service
    program=$install_root/phase3_identity_signing_ceremony.py
    program_arguments=(stage)
    purpose_credential=()
    ;;
  review)
    unit=home-agent-identity-packet-review.service
    program=$install_root/phase3_identity_signing_ceremony.py
    program_arguments=(review)
    required_purpose=$review_credential
    purpose_credential=(
      --property="LoadCredentialEncrypted=review.key:$review_credential"
    )
    ;;
  finalize)
    unit=home-agent-identity-packet-finalize.service
    program=$install_root/phase3_identity_signing_ceremony.py
    program_arguments=(finalize)
    required_purpose=$finalization_credential
    purpose_credential=(
      --property="LoadCredentialEncrypted=finalization.key:$finalization_credential"
    )
    ;;
  freeze-evidence)
    unit=home-agent-identity-writer-freeze.service
    program=$install_root/phase3_writer_freeze_ceremony.py
    program_arguments=()
    required_purpose=$writer_freeze_credential
    purpose_credential=(
      --property="LoadCredentialEncrypted=writer-freeze-policy.json:$writer_freeze_policy_credential"
      --property="LoadCredentialEncrypted=writer-freeze.key:$writer_freeze_credential"
    )
    ;;
  privacy-evidence)
    unit=home-agent-identity-privacy-evidence.service
    program=$install_root/phase3_privacy_cutover_ceremony.py
    program_arguments=()
    required_purpose=$privacy_probe_credential
    purpose_credential=(
      --property="LoadCredentialEncrypted=writer-freeze-policy.json:$writer_freeze_policy_credential"
      --property="LoadCredentialEncrypted=privacy-probe-policy.json:$privacy_probe_policy_credential"
      --property="LoadCredentialEncrypted=privacy-probe.key:$privacy_probe_credential"
    )
    ;;
  cutover-packet)
    unit=home-agent-identity-semantic-cutover-packet.service
    program=$install_root/phase3_semantic_cutover_ceremony.py
    program_arguments=()
    required_purpose=$semantic_cutover_credential
    purpose_credential=(
      --property="LoadCredentialEncrypted=writer-freeze-policy.json:$writer_freeze_policy_credential"
      --property="LoadCredentialEncrypted=privacy-probe-policy.json:$privacy_probe_policy_credential"
      --property="LoadCredentialEncrypted=semantic-cutover-policy.json:$semantic_cutover_policy_credential"
      --property="LoadCredentialEncrypted=semantic-cutover.key:$semantic_cutover_credential"
    )
    ;;
  supersede-expired)
    unit=home-agent-identity-packet-supersede.service
    program=$install_root/phase3_identity_signing_ceremony.py
    program_arguments=(supersede-expired)
    purpose_credential=()
    ;;
  *) fail ;;
esac

[[ $(id -u) -eq 0 ]] || fail
[[ -f "$manifest" ]] || fail
[[ -f "$credential_receipt" ]] || fail
[[ -f "$policy_credential" && -f "$commitment_credential" ]] || fail
[[ -z "$required_purpose" || -f "$required_purpose" ]] || fail
[[ "$phase" != freeze-evidence || -f "$writer_freeze_policy_credential" ]] || fail
[[ "$phase" != privacy-evidence || -f "$writer_freeze_policy_credential" ]] || fail
[[ "$phase" != privacy-evidence || -f "$privacy_probe_policy_credential" ]] || fail
[[ "$phase" != cutover-packet || -f "$writer_freeze_policy_credential" ]] || fail
[[ "$phase" != cutover-packet || -f "$privacy_probe_policy_credential" ]] || fail
[[ "$phase" != cutover-packet || -f "$semantic_cutover_policy_credential" ]] || fail
(
  cd "$install_root"
  sha256sum --check --status "$manifest"
) || fail

exec systemd-run \
  --quiet \
  --collect \
  --wait \
  --pty \
  --pipe \
  --unit="$unit" \
  --property=Type=exec \
  --property=User=root \
  --property=Group=root \
  --property=UMask=0077 \
  --property=NoNewPrivileges=yes \
  --property=PrivateNetwork=yes \
  --property=IPAddressDeny=any \
  --property=RestrictAddressFamilies=AF_UNIX \
  --property=ProtectSystem=strict \
  --property=ProtectHome=yes \
  --property=PrivateTmp=yes \
  --property=PrivateDevices=yes \
  --property=ProtectKernelTunables=yes \
  --property=ProtectKernelModules=yes \
  --property=ProtectControlGroups=yes \
  --property=LockPersonality=yes \
  --property=MemoryDenyWriteExecute=yes \
  --property=CapabilityBoundingSet= \
  --property=ReadWritePaths=/srv/home-agent/private/phase3-identity \
  --property=WorkingDirectory="$install_root" \
  --property=RuntimeMaxSec=15min \
  --property="LoadCredentialEncrypted=policy.json:$policy_credential" \
  --property="LoadCredentialEncrypted=commitment.key:$commitment_credential" \
  "${purpose_credential[@]}" \
  /usr/bin/python3 "$program" "${program_arguments[@]}"
