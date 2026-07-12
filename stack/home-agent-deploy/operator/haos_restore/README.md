# Isolated Home Assistant OS restore harness

This source-only operator harness is for a supervised restore of the protected
Home Assistant backup `7259fb6d` into a disposable Home Assistant OS 18.1 VM.
It does not install packages, download an image, decrypt a backup, modify the
production Home Assistant instance, or start anything merely by existing in
the repository.

The harness is deliberately specific to the validated recovery artifacts:

- HAOS image: `haos_ova-18.1.qcow2.xz`
- Image SHA-256:
  `60df08773901e1eac9b9cfe03d53e1d939e67b669c172c3a41037fb3cd295b9d`
- Backup slug: `7259fb6d`
- Backup SHA-256:
  `f129c72cb93121de63ef9ba88f3d21e207db6f6e22e84c57b040a2ec3f0a9d28`
- Expected Core version: `2026.7.1`
- Expected add-on count: `12`

The script rejects different artifacts. Supporting another recovery point is a
reviewed source change, not a runtime override.

This recovery boundary blocks private networks and arbitrary destination IPs,
but IP/port sets cannot authenticate TLS SNI or a tenant behind a shared CDN
address. A malicious restored guest could attempt another tenant on an already
approved CDN IP and port 443. Use this harness only for a recovery image not
suspected of active compromise. A forensic/hostile restore requires a separate
offline artifact mirror or authenticated SNI-aware proxy before boot.

## Security shape

The VM runs with KVM as a dedicated non-login user inside an unprivileged user
and network namespace. `slirp4netns --disable-host-loopback` supplies outbound
transport without adding a bridge, route, NAT rule, or UFW exception to the
host. No USB, PCI, radio, GPU, filesystem share, or host device is passed into
the VM. The TAP is pre-owned by mapped UID/GID 0, then QEMU starts with empty
inheritable, permitted, effective, bounding, and ambient capability sets plus
`NoNewPrivs=1`. Startup and validation inspect those fields in `/proc`; QEMU
cannot rewrite namespace nftables during the secret-bearing phase.

Networking has two enforcement layers:

1. The namespace nftables policy defaults to deny. Guest DNS can reach only a
   minimal proxy that accepts byte-for-byte exact reviewed names. It strips
   additional records, normalizes QNAME case, and replaces client transaction
   IDs before forwarding; arbitrary subdomains are refused. `dnsmasq` provides
   DHCP only. HTTPS and NTP are allowed only to the current short-lived IP sets
   for those names.
2. A host nftables OUTPUT guard applies to the dedicated UID. It blocks every
   private, loopback, link-local, CGNAT/Tailscale, Docker, documentation,
   multicast, and reserved range plus all IPv6, then permits only endpoint-pinned
   TCP 443 and UDP 123 and established replies from the localhost UI relay. It
   permits no DNS from the VM UID. Guest DNS crosses a mode-`0600` Unix socket
   to a root-owned host-side instance of the same exact-name verifier. This
   second layer remains in force even if namespace-root nftables were
   compromised.

Two root-owned `policy_refresher.py` processes independently maintain the host
and namespace sets. Each resolves in the host network namespace through the one
configured public resolver, rejects any non-global answer, and replaces both
sets in one nft transaction; the namespace instance enters the reviewed
namespace only for the final nft transaction.
Elements expire after 300 seconds and refresh every 120 seconds. A failed
resolution or policy check atomically empties both sets before the refresher
exits. It never retains a stale destination until timeout.

The UI relay does not exist during bootstrap. It is created only after the
strict restore transition and binds only `127.0.0.1`. Access it from the
operator workstation through a local SSH forward; never publish it on LAN or
Tailscale:

```sh
ssh -N -L 18123:127.0.0.1:18123 hav-ubuntu
```

## Host prerequisites (not installed by this harness)

The eventual operator must install reviewed Ubuntu packages that provide:

```text
qemu-system-x86_64  qemu-img  OVMF  cryptsetup  nft  slirp4netns
dnsmasq  dig  socat  nsenter  unshare  setpriv  jq  xz  python3
blkid  losetup  openssl
```

Create one system user with no home and `/usr/sbin/nologin`, add only the `kvm`
group, and ensure it belongs to none of `sudo`, `adm`, `docker`, `lxd`,
`libvirt`, or `wheel`. Preflight performs an actual throwaway user/network
namespace probe and requires that this user can open `/dev/kvm` from inside the
namespace. It fails closed if the host's user-namespace or KVM policy differs.

Download the exact official HAOS 18.1 qcow2 asset manually and verify the
digest out of band. Copy both the image and backup into a canonical root-owned,
non-writable cache whose every ancestor is root-owned and non-writable; the
original user-owned backup path is intentionally rejected. Create the scratch
parent, empty mount point, and report directory as root-owned mode `0700` with
the same trusted-ancestor rule. Copy `haos-restore.conf.example` to a
root-owned mode-`0600` operator file and adjust paths only. The parser rejects
quotes, expansions, whitespace, unknown keys, and repeated keys.

The operational script must not run from a developer checkout. Install exactly
the seven reviewed runtime files into the locked root-owned directory; the
harness rejects symlinks, unexpected adjacent files (including Python import
shadows), non-root ownership, and writable ancestors:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec/home-agent-haos-restore
sudo install -o root -g root -m 0755 haos_restore_drill.sh \
  /usr/local/libexec/home-agent-haos-restore/
sudo install -o root -g root -m 0444 policy_compiler.py policy_refresher.py \
  exact_dns_proxy.py nft_contract.py bootstrap-egress.tsv restore-egress.tsv \
  /usr/local/libexec/home-agent-haos-restore/
```

At preparation, executable helpers and policy copies move into a root-owned
tmpfs tools directory that the VM group may read but cannot write, replace, or
unlink. VM-owned runtime directories contain sockets and generated DHCP state
only; no later root process executes code from them.

The default capacity contract creates a 176 GiB sparse LUKS2 container and a
160 GiB guest disk while requiring capacity for the container's maximum growth
plus 200 GiB of host free space. The restored VM receives 4 vCPUs and 8 GiB
RAM. The LUKS key exists only beneath tmpfs `/run`, is removed immediately after
opening the mapper, and is never written to an argument, environment variable,
report, or persistent log. State records the exact configuration digest,
normalized scratch-path digest, LUKS UUID, and drill-specific LUKS label;
subsequent commands fail closed if any binding changes. The same content-free
identity plus the scratch device/inode is written to a durable root-only cleanup
receipt authenticated with a root-only HMAC key in the report directory. It
contains no backup key, path, identity, location, or Home Assistant content.

## State machine

Run only the installed absolute path through a clean sudo environment; never
use `sudo --preserve-env`. None accepts a backup password or Home Assistant
credential.

```sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh preflight /root/haos-restore.conf

sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh prepare /root/haos-restore.conf \
  --acknowledge-ephemeral-key-loss

sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh start-bootstrap /root/haos-restore.conf \
  --confirm-sterile-bootstrap
```

Bootstrap is for a pristine VM only. It deliberately has no UI relay, so the
production backup cannot be uploaded through this harness in that phase. Do
**not** upload the production backup by any alternate path during bootstrap.
The broader, separately pinned bootstrap policy allows Home Assistant and add-on
metadata endpoints while the VM contains no production secrets. Let the
pristine appliance finish initialization, then request a clean shutdown and
wait for `qemu_running=no`:

```sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh request-shutdown /root/haos-restore.conf
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh status /root/haos-restore.conf
```

Transition only after the pristine QEMU process has exited. The transition
kills the DNS proxy, both refresh loops, slirp process, namespace anchor, and
any relay; removes the old namespace and host guard; installs the narrower
restore policy; and boots the same disk in a fresh namespace. Only then is the
loopback UI relay created. There is no interval in which restored data can
inherit bootstrap egress.

```sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh transition-restore /root/haos-restore.conf \
  --confirm-pristine-no-production-data
```

Now upload the protected backup through Home Assistant onboarding from the
operator workstation. Enter its emergency-kit key only in the local browser.
After restoration, log in using the original account and perform the checks in
`validation-attestation.env.example`. Put only those closed categorical fields
in a root-owned, mode-`0600` attestation file. Names, notes, entity IDs,
coordinates, URLs, tokens, camera data, and free-form text are prohibited.

```sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh validate /root/haos-restore.conf \
  /root/haos-restore-attestation.env
```

Validation verifies live process identities, the encrypted mount, non-empty
endpoint sets, normalized structural digests of both exact nft rulesets,
loopback-only binding, the locked restore policy, an exact HTTP 200 response,
and the closed attestation. Its JSON contains only versions, random drill ID,
digests, booleans, add-on counts, HTTP status class, and firewall packet
counters.

Finally destroy the disposable recovery environment:

```sh
sudo env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/local/libexec/home-agent-haos-restore/haos_restore_drill.sh cleanup /root/haos-restore.conf \
  --confirm-destroy-ephemeral-restore
```

Cleanup stops QEMU and slirp before removing network guards or the namespace.
It refuses to remove the guard while any unknown process remains under the
dedicated UID. It then unmounts the exact mapper, closes LUKS, removes the exact
guarded container, and removes tmpfs runtime state. Before closing an active
mapper, cleanup verifies its backing loop path and LUKS UUID; after deletion it
removes the durable receipt. This is cryptographic erasure
through destruction of the never-persisted key; the harness does not
make a false SSD overwrite claim.

## Failure and endpoint review

After a process crash or lost operator shell, run the same explicit `cleanup`
command with the unchanged configuration file. If `/run` state was lost in a
reboot, cleanup authenticates the durable receipt and rechecks the config hash,
canonical scratch-path hash, LUKS UUID/label, and device/inode before deleting
anything. If either state or receipt conflicts, cleanup refuses destructive
recovery and retains the guards; never recreate identity from guesses. Cleanup
still identifies only approved command markers, stops them in safety order,
and retains the host guard if anything unrecognized survives.

If restoration needs another endpoint, let it fail closed. Do not add an IP to
the live nft set and do not switch to general NAT. Review the exact FQDN and
purpose, update the appropriate TSV, update its locked digest in source, run
the tests, and repeat the drill. Bootstrap endpoints must not be copied into
the restore policy merely because they were observed during pristine setup.

This drill validates application recovery, not device availability. Cameras,
MQTT peers, radios, Tailscale, private integrations, and cloud device APIs are
expected to be unavailable. No physical action should succeed.
