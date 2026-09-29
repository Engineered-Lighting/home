# BFF OAuth egress boundary

The browser authorizes against the host's private Tailscale HTTPS listener.
The BFF exchanges and refreshes those codes server-side through that same TLS
endpoint. Because Docker-sourced traffic also traverses the host input
firewall, production permits one exact tuple:

```text
ha-bff-egress0 / 172.22.0.10
  -> this online node's current Tailscale IPv4 / HOME_AGENT_HA_URL port
```

No other container is allowed on `home-agent_bff-public`. The network has a
fixed `/24`, bridge name, gateway, BFF address, and no IPv6. Core remains on
the separate internal `home-agent_api-net`; the BFF's loopback publication is
unchanged.

The helper enforces that tuple twice. Its dedicated `HOME_AGENT_BFF_INPUT`
chain is reached by the first IPv4 `INPUT` rule. It first accepts only
conntrack-confirmed `RELATED,ESTABLISHED` return packets from the exact BFF
address and TCP source port 8097 to the exact Docker bridge gateway used by the
loopback publisher. This lets a host-local client receive the BFF's SYN-ACK and
response without permitting the BFF to open a new connection to any host
service. The next rule accepts only
the reviewed HA tuple above, and a terminal rule drops every other
host-directed packet from the BFF bridge. Later UFW, Docker, or custom accepts
are therefore unreachable for this bridge. An exact commented UFW rule remains
as the persistent fallback and auditable policy record. Reconciliation rejects
chain drift, duplicate jumps, a state rule containing `NEW`, and any state in
which its jump is not first. A reviewed UFW lifecycle hook installs the same
guard during firewall start, stop, reload, and flush handling, before Docker or
Tailscale must be online; the BFF is therefore never exposed while waiting for
the periodic verifier.

`apply` upgrades the exact previous two-rule guard in place by inserting this
return-flow rule first. Any other legacy or drifted chain still fails closed;
the migration never removes the terminal drop or creates an unguarded window.

Install the reviewed helper into a root-owned path before using it. Never run a
root firewall helper from the mutable Git checkout. The final digest comparison
closes the source-copy race:

```bash
cd /opt/home/home-github
expected="$(git hash-object stack/home-agent-deploy/bff-egress/firewall_contract.py)"
sudo install -d -m 0755 -o root -g root \
  /usr/local/libexec/home-agent-bff-egress
sudo install -m 0555 -o root -g root \
  stack/home-agent-deploy/bff-egress/firewall_contract.py \
  /usr/local/libexec/home-agent-bff-egress/firewall_contract.py
installed="$(sudo git hash-object \
  /usr/local/libexec/home-agent-bff-egress/firewall_contract.py)"
test "$installed" = "$expected"
```

`git hash-object` is used only as a byte-for-byte Git blob digest comparison;
the reviewed commit still supplies source authenticity. The installed helper
rejects symlinks, a non-root owner, group/world-writable path components,
non-isolated Python, and any execution path other than the fixed install.

## Install the UFW lifecycle hook

Add the four `HOME_AGENT_BFF_EGRESS_*` values and the exact output of
`tailscale ip -4` as `HOME_AGENT_HA_TAILSCALE_IPV4` to the root-owned
deployment environment. The latter is non-secret static firewall policy; live
validation proves that DNS and Tailscale still identify that same address.

Install the lifecycle hook only if `/etc/ufw/after.init` is still the
unmodified distributor template. This refuses to overwrite another operator's
hook. The final digest comparison closes the copy race, and the explicit
invocation installs the new-interface guard before that interface needs to
exist:

```bash
cd /opt/home/home-github
sudo cmp -s /etc/ufw/after.init /usr/share/ufw/after.init
expected_hook="$(git hash-object \
  stack/home-agent-deploy/bff-egress/ufw_after_init.sh)"
sudo install -m 0555 -o root -g root \
  stack/home-agent-deploy/bff-egress/ufw_after_init.sh \
  /etc/ufw/after.init
installed_hook="$(sudo git hash-object /etc/ufw/after.init)"
test "$installed_hook" = "$expected_hook"
sudo /etc/ufw/after.init start
```

## Existing-install network migration

Docker cannot mutate the subnet, bridge name, or IPAM of an attached existing
network. Do not run a normal whole-project `compose up` across this change.
Migrate only the BFF while Core and Edge remain online:

1. Before stopping anything, capture the old network ID, BFF address, derived
   bridge name, and the exact commented UFW rule. Re-run `ufw status numbered`
   immediately before any numbered deletion; never reuse a previously observed
   rule number.

   ```bash
   sudo docker network inspect home-agent_bff-public
   sudo docker inspect home-agent-bff-1
   sudo ufw status numbered
   ```

2. Complete the lifecycle-hook installation above, then render and review
   Compose before making the outage:

   ```bash
   sudo docker compose \
     --env-file /srv/home-agent/config/home-agent.env \
     -f /opt/home/home-github/stack/home-agent-compose.yml config --quiet
   ```

3. Stop and remove only the BFF container, prove the old network is empty, then
   replace it and recreate only the BFF. OAuth exchange/refresh is unavailable
   during this bounded interval; HA device control and Core ingest continue.

   ```bash
   cd /opt/home/home-github/stack
   sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
     -f home-agent-compose.yml stop bff
   sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
     -f home-agent-compose.yml rm -f bff
   test "$(sudo docker network inspect \
     --format '{{len .Containers}}' home-agent_bff-public)" = 0
   sudo docker network rm home-agent_bff-public
   sudo docker compose --env-file /srv/home-agent/config/home-agent.env \
     -f home-agent-compose.yml up -d --no-deps bff
   ```

4. Install the reviewed helper, run `apply`, and prove the new tuple. Only after
   that succeeds, re-run `sudo ufw status numbered`, match the old rule against
   the captured old bridge/source/destination/port, and delete that exact old
   numbered rule. Run `verify` again and prove no UFW rule retains the old
   bridge name. A stale old bridge rule must never be left for future Docker
   reuse.

After Compose has created the pinned network, apply and prove the boundary
without supplying any OAuth material:

```bash
sudo /usr/bin/python3 -I \
  /usr/local/libexec/home-agent-bff-egress/firewall_contract.py \
  apply --env /srv/home-agent/config/home-agent.env
```

The command fails closed unless all of the following are exact:

- `HOME_AGENT_HA_URL` is this online node's canonical Tailscale DNS name;
- DNS resolves only to the root-pinned `HOME_AGENT_HA_TAILSCALE_IPV4` on this
  online node;
- the Docker network, bridge, one attached BFF, fixed source address, disabled
  IPv6, BFF hardening, and loopback host binding match policy;
- UFW is active and host IPv4 input defaults to `DROP`;
- the exact UFW rule exists;
- the dedicated guard is the sole first `INPUT` jump, contains exactly one
  source- and bridge-gateway-bound `RELATED,ESTABLISHED` accept, then the
  tuple-specific HA accept and a terminal drop, and has no extra rules;
  and
- an anonymous GET from inside the BFF reaches `/auth/token` and receives HA's
  expected `405` rejection.

The probe carries no code, token, cookie, identity, or request body. Output and
errors are fixed and content-free.

Install the reconciliation timer after the first successful apply. The UFW
lifecycle hook prevents a boot/reload gap; the timer independently revalidates
Docker, DNS, Tailscale, UFW, the first-hop guard, and the anonymous HA probe,
and fails on any unreviewed chain contents:

```bash
sudo install -m 0644 \
  stack/home-agent-deploy/operator/systemd/home-agent-bff-egress-verify.service \
  /etc/systemd/system/home-agent-bff-egress-verify.service
sudo install -m 0644 \
  stack/home-agent-deploy/operator/systemd/home-agent-bff-egress-verify.timer \
  /etc/systemd/system/home-agent-bff-egress-verify.timer
sudo systemctl daemon-reload
sudo systemctl enable --now home-agent-bff-egress-verify.timer
sudo systemctl start home-agent-bff-egress-verify.service
```

Rollback the lifecycle hook and rule before removing the pinned network. This
is intentionally ordered so a later UFW lifecycle event cannot recreate the
guard after removal:

```bash
sudo systemctl disable --now home-agent-bff-egress-verify.timer
sudo systemctl stop home-agent-bff-egress-verify.service
test "$(sudo systemctl show -p ActiveState --value \
  home-agent-bff-egress-verify.service)" = inactive
cd /opt/home/home-github
expected_hook="$(git hash-object \
  stack/home-agent-deploy/bff-egress/ufw_after_init.sh)"
installed_hook="$(sudo git hash-object /etc/ufw/after.init)"
test "$installed_hook" = "$expected_hook"
sudo install -m 0755 -o root -g root \
  /usr/share/ufw/after.init /etc/ufw/after.init
sudo /usr/bin/python3 -I \
  /usr/local/libexec/home-agent-bff-egress/firewall_contract.py \
  remove --env /srv/home-agent/config/home-agent.env
```

Removing the rule invalidates browser OAuth refresh, whoami, logout revocation,
and new code exchange. Existing opaque Agent cookies then fail closed; it does
not affect Home Assistant device control.

## Victoria linking profile

The separately provisioned Victoria linking service (`victoria-link` in
`shared-preferences/compose.json`) reaches Victoria Home Assistant through its
own added Serve handler on this same node
(`home-app.<tailnet>.ts.net:10001 → 192.168.1.60:80`). It uses the same reviewed
tool with `--profile victoria-link`. The profile has its own values:

- bridge `ha-vlink-egr0`, subnet `172.26.0.0/24`, source address `172.26.0.10`
- guard chain `HOME_AGENT_VLINK_INPUT`
- UFW comment `home-agent-victoria-link-to-victoria-ha`

It publishes no host port, so its guard has no return-flow rule: just the exact
HA allow and a terminal drop. Each profile's first-hop jump must lead `INPUT`.
The only things allowed in front of it are the other reviewed profiles' exact
jumps (BFF, Victoria linking, lighting), each matching a different bridge.

Provision in `/srv/home-agent/config/home-agent.env`:

```text
HOME_AGENT_VICTORIA_HA_URL=https://home-app.<tailnet>.ts.net:10001
HOME_AGENT_VICTORIA_HA_HOSTNAME=home-app.<tailnet>.ts.net
HOME_AGENT_VICTORIA_LINK_EGRESS_SUBNET=172.26.0.0/24
HOME_AGENT_VICTORIA_LINK_EGRESS_GATEWAY=172.26.0.1
HOME_AGENT_VICTORIA_LINK_EGRESS_IP=172.26.0.10
```

The lifecycle hook guards this profile only once `HOME_AGENT_VICTORIA_HA_URL` is
set (`--if-configured`). Until then no allow rule exists for the bridge, and
UFW's default-deny input policy drops its traffic.

Updating the hook changes its reviewed digest. Install the new helper and hook
together, compare both blob digests, and reload UFW. Then check that the BFF
guard verifies before starting victoria-link:

```bash
sudo /usr/bin/python3 -I /usr/local/libexec/home-agent-bff-egress/firewall_contract.py \
  verify --env /srv/home-agent/config/home-agent.env
# after victoria-link is running:
sudo /usr/bin/python3 -I /usr/local/libexec/home-agent-bff-egress/firewall_contract.py \
  apply --profile victoria-link --env /srv/home-agent/config/home-agent.env
sudo install -m 0644 -o root -g root \
  stack/home-agent-deploy/operator/systemd/home-agent-victoria-link-egress-verify.service \
  stack/home-agent-deploy/operator/systemd/home-agent-victoria-link-egress-verify.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-agent-victoria-link-egress-verify.timer
```

`apply` probes Victoria HA's token endpoint from inside the container with an
empty GET, which must return 405. Roll back with `remove --profile victoria-link`
after disabling its timer. That leaves the BFF profile untouched.

## Lighting profile

The private Core lighting service (compose service `lighting` in project
`home-shared-preferences`, container `home-shared-preferences-lighting-1`)
dispatches to both homes, so it must reach both Serve ports on this node:

- Echo HA at `HOME_AGENT_HA_URL` (`home-app.<tailnet>.ts.net:10000`)
- Victoria HA at `HOME_AGENT_VICTORIA_HA_URL` (`home-app.<tailnet>.ts.net:10001`)

It uses the same reviewed tool with `--profile lighting`. A profile lists a
fixed, ordered tuple of HA URL keys. Each URL must be a canonical HTTPS root, and
all of them must name the same host: this online Tailscale node, which resolves
only to the pinned `HOME_AGENT_HA_TAILSCALE_IPV4`. Two URLs on the same port are
refused. Each URL gets its own exact accept, in key order. The BFF and Victoria
linking profiles each list one key, and their rules are byte-for-byte unchanged.

The profile has its own values:

- network `home-agent_lighting-egress`, bridge `ha-light-egr0`, subnet
  `172.27.0.0/24`, gateway `172.27.0.1`, source address `172.27.0.10`
- guard chain `HOME_AGENT_LIGHT_INPUT`
- UFW comment `home-agent-lighting-to-home-ha`, on one rule per HA port

Like victoria-link it publishes no host port, so it has no return-flow rule.
With the example Tailscale IPv4 `100.87.94.18`, the whole guard is:

```text
-A INPUT -i ha-light-egr0 -j HOME_AGENT_LIGHT_INPUT
-N HOME_AGENT_LIGHT_INPUT
-A HOME_AGENT_LIGHT_INPUT -s 172.27.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp --dport 10000 -j ACCEPT
-A HOME_AGENT_LIGHT_INPUT -s 172.27.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp --dport 10001 -j ACCEPT
-A HOME_AGENT_LIGHT_INPUT -j DROP
```

Any extra, missing, duplicated or reordered rule fails `verify`, and `apply`
refuses to adopt it. The container must be attached only to
`home-agent_api-net` and `home-agent_lighting-egress`, and must publish no port.
Like victoria-link, it resolves the HA host name through an `extra_hosts` entry
that maps to the pinned Tailscale IPv4.

Both HA URL keys already exist for the other profiles, so they do not decide
whether lighting is provisioned. `HOME_AGENT_LIGHTING_EGRESS_IP` does. Add these
to `/srv/home-agent/config/home-agent.env` only when provisioning lighting:

```text
HOME_AGENT_LIGHTING_EGRESS_SUBNET=172.27.0.0/24
HOME_AGENT_LIGHTING_EGRESS_GATEWAY=172.27.0.1
HOME_AGENT_LIGHTING_EGRESS_IP=172.27.0.10
```

The lifecycle hook runs `guard --profile lighting --if-configured` after the
other two profiles. It skips lighting while `HOME_AGENT_LIGHTING_EGRESS_IP` is
unset. Once that key is set, both HA URLs are required, and a missing one fails
the hook rather than skipping it.

This hook change moves its reviewed SHA-256, pinned in `firewall_contract.py` as
`UFW_HOOK_SHA256`, to
`42e83a0338b531c216f98f5d49d7271a5f21db5e2c4fad8838e1b3a2499da5bc`. As with
victoria-link, install the new helper and hook together, compare both blob
digests, reload UFW, and check that the BFF guard verifies. Then, with lighting
running:

```bash
sudo /usr/bin/python3 -I /usr/local/libexec/home-agent-bff-egress/firewall_contract.py \
  apply --profile lighting --env /srv/home-agent/config/home-agent.env
sudo install -m 0644 -o root -g root \
  stack/home-agent-deploy/operator/systemd/home-agent-lighting-egress-verify.service \
  stack/home-agent-deploy/operator/systemd/home-agent-lighting-egress-verify.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now home-agent-lighting-egress-verify.timer
```

The lighting container is a Python Core image, so `apply` and `verify` probe
with `python3 -I` instead of Node. Each HA URL gets an empty GET to
`/auth/token`, with no redirects and no proxy. Each must return 405. To roll
back, disable the lighting timer, then run `remove --profile lighting` while the
container is still running. That removes the guard chain and both UFW rules, and
leaves the other profiles untouched.
