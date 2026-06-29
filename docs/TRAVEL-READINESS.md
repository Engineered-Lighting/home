# Travel Readiness

This is the pre-flight and recovery runbook for working on the Home app while
away from the apartment. Tailscale is the access boundary. Do not use public
DNS, router forwarding, or Tailscale Funnel for the Home stack.

## Before leaving

From the Windows travel machine:

```powershell
cd C:\Claude\home
.\tools\travel-readiness.ps1
```

From the Ubuntu AI box:

```bash
cd ~/code/home
tools/travel-readiness.sh
```

In the installed Tauri app:

```text
/profile tailscale
/travel check
```

The app is travel-ready when there are no blocker failures. Degraded failures
are acceptable only if you are comfortable losing that feature while away.

## Fallback addresses

Keep these handy in case MagicDNS is unreliable on hotel Wi-Fi or another VPN:

| Host | MagicDNS | Full tailnet DNS | Observed Tailscale IP |
| --- | --- | --- | --- |
| Ubuntu AI box | `engineeredlightingserver1` | `engineeredlightingserver1.taild52a15.ts.net` | `100.87.94.18` |
| Home Assistant | `homeassistant` | `homeassistant.taild52a15.ts.net` | `100.116.3.41` |

## Recovery path

Use SSH/systemd/GitHub Actions for recovery. The Home app diagnoses and copies
commands, but it does not execute shell commands on your home machines.

Useful first checks:

```bash
ssh hav-ubuntu 'tailscale status'
ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
ssh hav-ubuntu 'sudo systemctl status home-web-gateway home-apartment-assets hav-stack-supervisor --no-pager'
```

Gateway logs:

```bash
ssh hav-ubuntu 'journalctl -u home-web-gateway -n 100 --no-pager'
```

Apartment asset logs:

```bash
ssh hav-ubuntu 'journalctl -u home-apartment-assets -n 100 --no-pager'
```

Supervisor logs:

```bash
ssh hav-ubuntu 'journalctl -u hav-stack-supervisor -n 100 --no-pager'
```

## Web deploy rollback

`tools/deploy-home-web.sh` records the previous commit before pulling. If
`web:check`, the Apartment asset check, or the gateway restart fails, it
automatically rolls the Ubuntu checkout back to the previous commit and
restarts `home-web-gateway`.

Disable automatic rollback only for manual debugging:

```bash
HOME_WEB_NO_AUTO_ROLLBACK=1 tools/deploy-home-web.sh
```

Manual rollback:

```bash
cd ~/code/home
git log --oneline -5
git reset --hard <previous_good_sha>
sudo systemctl restart home-web-gateway
```

## Self-hosted runner safety

The `deploy home web` GitHub Actions workflow is manual and deploy-only. It
must run from `main`; do not enable PR-triggered deploys on the Ubuntu AI box.
That runner has access to your home stack, so treat it as trusted automation,
not general CI.

## Freeze rule

Do not install major HAOS, Tailscale, NVIDIA driver, CUDA, Docker, or OS updates
right before travel. Finish changes, run `/travel check` from a phone hotspot,
and leave the stack boring.

## App commands

```text
/travel check     run live probes and print readiness
/travel status    print the last readiness result without probing
/travel recovery  copy recovery commands
/travel bundle    copy debug bundle with readiness, URLs, and failures
```
