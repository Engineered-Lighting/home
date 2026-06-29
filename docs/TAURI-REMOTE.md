# Tauri remote home stack access

The installed Tauri app can use the real Home stack while you travel by talking
directly to tailnet service URLs. This is separate from the browser web gateway:

- Tauri desktop: direct LAN or Tailscale service URLs.
- Browser web app: `web-gateway/server.mjs` plus Tailscale Serve.
- No public DNS, router forwarding, Tailscale Funnel, or `engineered.lighting`
  dependency is required for the desktop app.

## In the app

Open the connection profile from either place:

- First-run connection screen: choose **lan**, **tail**, or **custom** before
  connecting to Home Assistant.
- Header chip: click the small profile chip near the status pills.

Profiles:

| Profile | Purpose |
| --- | --- |
| Home LAN | Uses the existing `192.168.0.x` defaults. |
| Remote via Tailscale | Uses MagicDNS first, full tailnet DNS second, observed `100.x` IPs last. |
| Custom | Lets you edit service URLs per service. |

Useful commands:

```text
/profile status
/profile lan
/profile tailscale
/profile custom
/remote check
/debug bundle
```

`/remote check` probes every service and stores the first healthy Tailscale
candidate for the active profile. `/debug bundle` copies profile, version,
service URLs, probe results, and recent connection errors for remote debugging.

## Tailscale service defaults

The desktop app tries these Tailscale candidates in order:

| Service | Primary | Fallbacks |
| --- | --- | --- |
| Home Assistant | `http://homeassistant:8123` | `http://homeassistant.taild52a15.ts.net:8123`, `http://100.116.3.41:8123` |
| Frigate | `http://homeassistant:5000` | `http://homeassistant.taild52a15.ts.net:5000`, `http://100.116.3.41:5000` |
| AI box services | `http://engineeredlightingserver1:<port>` | `http://engineeredlightingserver1.taild52a15.ts.net:<port>`, `http://100.87.94.18:<port>` |

AI box ports:

| Service | Port |
| --- | --- |
| vLLM | 8000 |
| Vision | 8091 |
| Metrics | 8092 |
| Supervisor | 8093 |
| S2S bridge | 8094 |
| Intelligence | 8095 |
| Tracker | 8098 |
| Video labeler | 8099 |
| Apartment assets | 5190 |

Known readiness note: supervisor `:8093` must be reachable on the tailnet path
before full remote stack control is ready. The rest of the app can still work
while supervisor is down.

## Apartment scan and mesh assets

The heavy Apartment 3D assets are runtime data under `app/data/apartment`; they
are not bundled into the desktop installer. For remote Tauri use, serve them
from the Ubuntu AI box:

```bash
cd ~/code/home
HOME_APARTMENT_ASSET_HOST=0.0.0.0 HOME_APARTMENT_ASSET_PORT=5190 npm run dev:assets
```

Install as a systemd service:

```bash
cd ~/code/home
tools/install-home-apartment-assets-linux.sh
sudo systemctl status home-apartment-assets --no-pager
curl http://engineeredlightingserver1:5190/healthz
```

Keep access tailnet-only with host firewall/Tailscale ACLs where practical.

## Tailscale posture

- Keep raw service ports private to your tailnet.
- Do not enable Funnel for the Home stack.
- Do not expose these ports through router forwarding.
- Prefer ACLs that allow Marcelo's trusted devices to reach the Home Assistant
  host and Ubuntu AI box service ports.

## Smoke test before travel

1. Connect the laptop to Tailscale from outside the LAN or a phone hotspot.
2. Open the installed Home app.
3. Select **Remote via Tailscale**.
4. Run `/remote check`.
5. Connect with the Home Assistant long-lived token.
6. Verify: chat works, local model responds through HA, metrics update, S2S
   connects, tracker connects, Frigate media loads, Video Labeler opens, and
   Apartment scan/mesh modes load from `:5190`.
