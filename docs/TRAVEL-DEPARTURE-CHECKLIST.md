# Multi-Month Travel Departure Checklist

Use this checklist before leaving home for an extended trip. The Razer Blade is
the primary travel workstation. The Home stack should remain private to
Tailscale.

## Razer Blade proof test

Do this from the Razer Blade on a phone hotspot, not on the home LAN:

```powershell
cd C:\Claude\home
.\tools\pre-departure-check.ps1
.\tools\travel-readiness.ps1
```

Then open the installed Home app:

```text
/travel check
```

Expected result: `READY`, profile `Remote via Tailscale`, and `11/11`
reachable in the Remote access / Travel readiness dialog.

## Home machine hardening

- `FormD-T1` should not sleep or hibernate on AC power if it is staying home and
  you expect to SSH into it.
- Windows `Tailscale` and `sshd` should be running and automatic on any Windows
  machine you expect to recover remotely.
- Browser access should stay hosted on Ubuntu through Tailscale Serve; Windows
  Tailscale Serve is not part of the travel path.

## Ubuntu and Home Assistant

Before leaving:

```bash
ssh hav-ubuntu 'cd ~/code/home && tools/travel-readiness.sh'
ssh hav-ubuntu 'sudo systemctl status home-web-gateway home-apartment-assets hav-stack-supervisor --no-pager'
ssh hav-ubuntu 'df -h / && docker system df'
```

Ubuntu disk is acceptable below 90%. Clean up at 90%; treat 97% as urgent.

## Backups and credentials

- Take a Home Assistant snapshot and copy it somewhere outside HA.
- Confirm `Engineered-Lighting/home` is pushed and clean.
- Preserve `app/data/apartment`; it is external runtime data and not bundled in
  desktop installers.
- Confirm the Razer Blade or password manager has GitHub, Tailscale admin, SSH,
  HA token, stack token, and web gateway password access.

## Freeze rule

Starting June 29, 2026, avoid nonessential HAOS, Tailscale, NVIDIA driver,
CUDA, router firmware, BIOS, Frigate, Docker, and major AI stack upgrades until
you are back or have a physical recovery path ready.

## Physical recovery

- Keep router/modem, switch, Home Assistant, Ubuntu AI box, and JetKVM on UPS
  where possible.
- Confirm Ubuntu and any home Windows PC power back on after power loss.
- Confirm JetKVM or a trusted helper can recover Ubuntu, Home Assistant, and the
  router if SSH/Tailscale recovery fails.
- Use smart plugs only as controlled last-resort power cycles.
