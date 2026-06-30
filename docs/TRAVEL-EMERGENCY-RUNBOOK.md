# Travel Emergency Runbook

Save this file or its commands somewhere reachable from the Razer Blade and
phone. Tailscale is the access boundary. Do not enable public DNS, router port
forwarding, or Tailscale Funnel for the Home stack.

## Fast status

```bash
ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
ssh hav-ubuntu 'sudo systemctl status home-web-gateway home-apartment-assets hav-stack-supervisor --no-pager'
ssh hav-ubuntu 'df -h && docker system df'
```

`/healthz` normally returns `200` from the web gateway. If optional gateway auth
is enabled with `HOME_WEB_AUTH_REQUIRED=1`, `401` is also expected and means the
gateway is reachable.

## Logs

```bash
ssh hav-ubuntu 'journalctl -u home-web-gateway -n 100 --no-pager'
ssh hav-ubuntu 'journalctl -u home-apartment-assets -n 100 --no-pager'
ssh hav-ubuntu 'journalctl -u hav-stack-supervisor -n 100 --no-pager'
```

## Safe restarts

```bash
ssh hav-ubuntu 'sudo systemctl restart home-web-gateway'
ssh hav-ubuntu 'sudo systemctl restart home-apartment-assets'
ssh hav-ubuntu 'sudo systemctl restart hav-stack-supervisor'
```

After any restart, verify:

```bash
ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
```

## Deploy and rollback

Normal deploy:

```bash
ssh hav-ubuntu 'cd ~/code/home && tools/deploy-home-web.sh'
```

Manual rollback if a deploy leaves the browser app broken:

```bash
ssh hav-ubuntu 'cd ~/code/home && git log --oneline -5'
ssh hav-ubuntu 'cd ~/code/home && git reset --hard <previous_good_sha> && sudo systemctl restart home-web-gateway'
```

Prefer rollback to a known-good commit over live editing on Ubuntu.

## Desktop/Tauri

On the Razer Blade:

```powershell
cd C:\Claude\home
.\tools\travel-readiness.ps1
```

In the Home app:

```text
/travel check
```

Expected result: `READY`, profile `Remote via Tailscale`.
