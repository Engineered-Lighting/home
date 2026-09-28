# Tailnet browser-origin nodes

Shared preferences need browser hostnames that differ from `home-app`. Cookies
ignore ports, so `home-app:<port>` cannot isolate a session:

- The Echo Agent origin moves off `home-app` because preference Home origins may
  not share its hostname (`stack/services/home-agent-bff/src/echo-link-runtime.mjs`).
- Victoria sign-in needs its own hostname, distinct from every Echo origin
  (`src/bff.mjs` Victoria session store).

Tailscale Services would require tagging the host, and the existing user-owned
`home-app` node must not be retagged or renamed. So each extra name is a
separate, user-owned node: a bounded userspace `tailscaled` instance on the LA
host with its own state, socket and UDP port. Userspace nodes own no kernel
interface, routes or firewall chains, so the host's primary `tailscaled` and its
Serve handlers are untouched.

| Instance | Tailnet name | Serve route | TLS terminated by |
| --- | --- | --- | --- |
| `echo-agent` | `echo-agent.<tailnet>.ts.net` | `--https=443 http://172.23.0.10:8096` | tailscaled (same Origin container as today's `home-app:8443`) |
| `victoria-agent` | `victoria-agent.<tailnet>.ts.net` | `--tcp=443 tcp://172.23.0.36:9450` | victoria-link. It requires an exact Host header, a TLS socket and no forwarded headers, so only raw passthrough works. |

Victoria HA's HTTPS origin is not one of these nodes. It is an added
TLS-terminated TCP handler on `home-app` (`:10001 → 192.168.1.60:80`), so
victoria-link reuses the existing host-INPUT egress model. It also keeps the
Victoria session cookie away from HA.

## Install

Install only from a reviewed commit, never from a mutable checkout. Compare blob
digests after installing:

```sh
cd /opt/home/home-github            # checkout at the reviewed merge commit
src=stack/home-agent-deploy/tailnet-origins
sudo install -d -m 0755 -o root -g root /usr/local/libexec/home-agent/tailnet-origins \
  /usr/local/share/home-agent/tailnet-origins /etc/tailscale-origin
sudo install -m 0555 -o root -g root "$src/refresh-origin-cert.sh" /usr/local/libexec/home-agent/tailnet-origins/
sudo install -m 0644 -o root -g root "$src/README.md" /usr/local/share/home-agent/tailnet-origins/
for unit in tailscaled-origin@.service tailscaled-origin-cert@.service tailscaled-origin-cert@.timer; do
  sudo install -m 0644 -o root -g root "$src/$unit" /etc/systemd/system/
done
reviewed=<merge commit sha>
check() { test "$(git rev-parse "$reviewed:$src/$1")" = "$(sudo git hash-object "$2")" || { echo "digest mismatch: $1" >&2; return 1; }; }
check refresh-origin-cert.sh /usr/local/libexec/home-agent/tailnet-origins/refresh-origin-cert.sh
check tailscaled-origin@.service /etc/systemd/system/tailscaled-origin@.service
check tailscaled-origin-cert@.service /etc/systemd/system/tailscaled-origin-cert@.service
check tailscaled-origin-cert@.timer /etc/systemd/system/tailscaled-origin-cert@.timer
# First install only; never overwrite reviewed instance configuration.
for i in echo-agent victoria-agent; do
  test -e /etc/tailscale-origin/$i.env ||
    sudo install -m 0644 -o root -g root "$src/$i.env.example" /etc/tailscale-origin/$i.env
done
sudo systemctl daemon-reload
```

## Join each node (owner approval)

```sh
sudo systemctl enable --now tailscaled-origin@echo-agent.service
sudo tailscale --socket=/run/tailscale-origin-echo-agent/tailscaled.sock \
  up --hostname=echo-agent --accept-dns=false --accept-routes=false --timeout=2m
```

`up` prints a login URL. The owner opens it and signs in. In the admin console,
the owner then approves the device if device approval is on, and chooses
**Disable key expiry** on it. Repeat for `victoria-agent`. Verify:

```sh
sudo tailscale --socket=/run/tailscale-origin-victoria-agent/tailscaled.sock status --self
sudo tailscale serve status        # the primary node's handlers must be unchanged
```

If `tailscale ping` from a client shows DERP-only paths, add exact UDP allows for
41651/41652 on the LAN interface. That is optional; relayed paths still work.

## Enable Serve (at the cutover only)

Activate the routes only in the reviewed cutover window, after the Origin/BFF
swap and victoria-link start:

```sh
e=/run/tailscale-origin-echo-agent/tailscaled.sock
v=/run/tailscale-origin-victoria-agent/tailscaled.sock
sudo tailscale --socket=$e serve --bg --https=443 http://172.23.0.10:8096
sudo tailscale --socket=$v serve --bg --tcp=443 tcp://172.23.0.36:9450
```

Never use Funnel. These instances hold only the handlers above.

## Victoria certificate

`victoria-link` loads `browser.crt`/`browser.key` at startup from a read-only
mount. The helper runs daily and only replaces the files when the certificate
changes:

- The certificate directory must be root-owned (`root:1000 0750`), and every
  parent must be root-owned and not group- or other-writable. The helper
  refuses otherwise.
- It stages only in the node's root-only state directory. The key is
  installed as `root:1000 0440`.
- Each change records a persistent restart-pending marker that only a
  successful container restart clears.
- An open owner sign-in window (`/run/tailscale-origin-hold`) defers the
  restart. A restart still pending after 7 days fails the unit, so
  `OnFailure` alerts.

```sh
sudo install -d -m 0750 -o root -g 1000 \
  /srv/home-agent/shared-preferences/prepared-20260928/victoria-link-tls
sudo /usr/local/libexec/home-agent/tailnet-origins/refresh-origin-cert.sh victoria-agent
sudo systemctl enable --now tailscaled-origin-cert@victoria-agent.timer
sudo touch /run/tailscale-origin-hold    # before an owner sign-in window
sudo rm -f /run/tailscale-origin-hold    # after it
```

After the hold is removed, the next run performs any deferred restart. To do it
immediately, rerun the helper. Any victoria-link restart ends in-flight
Victoria linking sessions. `OnFailure=ntfy-alert@%n.service` uses the host's
existing alert template, which `tailscaled.service` also uses.

## Rollback

These nodes never touch the primary node. To remove one:

```sh
s=/run/tailscale-origin-victoria-agent/tailscaled.sock
sudo tailscale --socket=$s serve --tcp=443 off
sudo tailscale --socket=$s logout
sudo systemctl disable --now tailscaled-origin-cert@victoria-agent.timer tailscaled-origin@victoria-agent.service
```

Then remove the device in the admin console. Keep `/var/lib/tailscale-origin/<i>`
until removal is confirmed. On the primary node, never run `tailscale serve reset`.
Remove the Victoria HA handler only with
`sudo tailscale serve --tls-terminated-tcp=10001 off`.
