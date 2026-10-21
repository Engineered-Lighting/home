# Home Assistant reachability probe

`ha-reachable.timer` runs `ha_reachable.sh` every 2 minutes on the LA host and
fails the unit when Home Assistant is down. The existing ntfy pipeline turns
that exit code into pages (`OnFailure=ntfy-alert@%n.service`) and RECOVERED
notices (`ExecStartPost=ntfy-alert-reset %n`).

The probe is credential-free. It fetches `GET /auth/providers`, which Home
Assistant serves without auth (`requires_auth = False`, no
`@log_invalid_auth`), so it never becomes a failed-login event in
`http.ban`. Earlier versions probed `/api/`: unauthenticated that logged
"invalid authentication" every run and would get the host IP-banned if a ban
threshold were ever set; with a token, a revoked token became a failed login
and paused probing. Only a 200 whose body lists `providers` counts as up.

## Install

Addresses stay on the host. Create `/etc/default/ha-reachable` from
`systemd/ha-reachable.env.example` (root:root 0644), then install in this
order so a timer run never sees a half-installed probe:

```sh
sudo install -m 0644 stack/home-agent-deploy/operator/systemd/ha-reachable.service \
  stack/home-agent-deploy/operator/systemd/ha-reachable.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo install -m 0755 stack/home-agent-deploy/operator/ha_reachable.sh /usr/local/sbin/.ha-reachable.new
sudo mv -f /usr/local/sbin/.ha-reachable.new /usr/local/sbin/ha-reachable
sudo systemctl enable --now ha-reachable.timer
sudo systemctl start ha-reachable.service
journalctl -u ha-reachable.service -n 3 --no-pager
```

Before restarting HA on purpose, `touch /run/ha-maintenance` so the downtime
does not page; remove it afterwards.
