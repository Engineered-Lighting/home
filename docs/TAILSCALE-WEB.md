# Home app Tailscale web access

This is the first travel-access version of the full `app/src` Home app. It
keeps the app private to your tailnet and exposes one same-origin web gateway
instead of exposing the raw HA, AI, camera, or supervisor ports.

## Shape

- `web-gateway/server.mjs` serves `app/src`.
- `app/src/home-web-runtime.js` runs before `home-app.jsx` in a normal browser.
- Browser mode defaults service bases to `/proxy/...`.
- Tauri desktop remote access is handled separately through direct LAN/Tailscale
  service profiles. See [TAURI-REMOTE.md](TAURI-REMOTE.md).
- The Ubuntu AI box is the preferred host for the web gateway because it is
  next to the RTX/model/backend services.
- Tailscale Serve publishes the gateway privately to tailnet devices.
- Tailscale Funnel, router port forwarding, and public DNS are intentionally out
  of scope for v1.

## Run on the Ubuntu AI box

The travel setup should run from `home-app`, not the Windows
desktop:

```bash
cd ~/code/home
npm run web:check
tools/install-home-web-gateway-linux.sh
sudo systemctl status home-web-gateway --no-pager
```

To update the hosted version later:

```bash
cd ~/code/home
git pull --ff-only
sudo systemctl restart home-web-gateway
```

Or use the GitHub Actions manual workflow:

1. Open the `Engineered-Lighting/home` repo on GitHub.
2. Go to **Actions**.
3. Choose **deploy home web**.
4. Click **Run workflow** on `main`.

That workflow runs on the Ubuntu AI box self-hosted runner and performs the same
pull/check/restart through `tools/deploy-home-web.sh`.

`tools/deploy-home-web.sh` now records the previously running commit and rolls
back automatically if checks or the service restart fail. See
[TRAVEL-READINESS.md](TRAVEL-READINESS.md) for rollback commands, runner safety,
and the pre-travel freeze checklist.

The gateway still binds to `127.0.0.1:5181`; Tailscale Serve is the private
tailnet-facing listener.

### Apartment 3D assets

The real Apartment `cloud`, `scan`, and `mesh` views use large files under
`app/data/apartment`. That directory is intentionally gitignored, so a fresh
server checkout only has the small sim fallback unless the data directory is
copied onto the host.

Required for the full web experience:

- `points.ply` for the full cloud view
- `apartment.ply` or `apartment.spz` for the scan/splat view
- `apartment.mobile.ply` is an optional fallback for mobile scan/photo mode.
  The app prefers the full `apartment.ply` for visual quality; deploys generate
  a denser mobile fallback from `apartment.ply` when Python is available.
- `mesh.glb` or `collision.glb` for the mesh view; optional `mesh.mobile.glb`
  is preferred by phone browsers when present
- `frame.json`, `floor.json`, `manifest.json`, and `seed-model.json`

The deploy workflow runs `tools/check-home-web-assets.sh` before restarting the
gateway so missing ignored assets fail loudly instead of leaving the UI with
unavailable 3D modes.

This check is for the Ubuntu-hosted web gateway only. Desktop/Tauri releases do
not bundle `app/data/apartment`; desktop scan/mesh assets remain local runtime
data on the machine running the app.

For the installed Tauri app, Apartment scan/mesh assets are served by the
separate `home-apartment-assets` systemd service on the Ubuntu AI box. That
keeps the desktop installer small and lets a traveling laptop load the same
runtime data over Tailscale.

## Run manually on Windows

```powershell
cd C:\Claude\home
npm run web:check
npm run web:start
```

Open `http://127.0.0.1:5181/` on the home machine.

The gateway binds to `127.0.0.1` by default. Override only when you need to:

```powershell
$env:HOME_WEB_HOST="127.0.0.1"
$env:HOME_WEB_PORT="5181"
npm run web:start
```

## Gateway auth

Tailscale is the real access boundary. By default, the gateway does not show a
second login screen; a Tailscale-connected browser goes straight to the app.

If you ever want the extra gateway password again, opt in with
`HOME_WEB_AUTH_REQUIRED=1`.

Generate one username/password pair:

```powershell
node -e "const c=require('node:crypto'); console.log('marcelo:'+c.randomBytes(18).toString('base64url'))"
```

Seed the first login with the generated value:

```powershell
[Environment]::SetEnvironmentVariable("HOME_WEB_AUTH_REQUIRED", "1", "User")
[Environment]::SetEnvironmentVariable("HOME_WEB_BASIC_AUTH", "marcelo:<generated-password>", "User")
```

Open `/auth` on the tailnet URL to sign in, sign out, or change the password.
Password changes are written as a salted PBKDF2 hash to the local auth file
below, and that file takes precedence over the initial environment variable:

- Windows default auth file: `%APPDATA%\EngineeredLightingHome\web-auth.json`
- Linux default auth file: `$XDG_CONFIG_HOME/EngineeredLightingHome/web-auth.json`
  or `~/.config/EngineeredLightingHome/web-auth.json`
- Override path: `HOME_WEB_AUTH_FILE`

An existing auth file is ignored while `HOME_WEB_AUTH_REQUIRED` is unset or `0`.

Home Assistant bearer tokens still pass through. Stack supervisor bearer auth is
handled server-side by the gateway for browser web access. The gateway strips
its own cookies and Basic auth before proxying requests upstream.

## Start on Windows login

No-admin setup uses the current user's Startup folder. The launcher runs
`tools/start-home-web-gateway.ps1`, which starts `web-gateway/server.mjs` and
passes through `HOME_WEB_BASIC_AUTH` from the user's Windows environment. That
password is ignored unless `HOME_WEB_AUTH_REQUIRED=1` is also set.

To test the same starter manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Claude\home\tools\start-home-web-gateway.ps1
```

## Tailscale Serve

With the gateway running:

```bash
tailscale serve --bg --tls-terminated-tcp=443 127.0.0.1:5181
tailscale serve status
```

Use the HTTPS tailnet URL shown by Tailscale from another device that is signed
into your tailnet. To turn it off:

```bash
tailscale serve --tls-terminated-tcp=443 off
```

Use `--tls-terminated-tcp` rather than the default HTTPS web proxy mode. The
Home app needs HA, tracker, and S2S WebSocket upgrades, and this mode preserves
those upgrades while still giving browsers the Tailscale HTTPS URL.

Reference: https://tailscale.com/docs/reference/tailscale-cli/serve

## Service targets

Override these only if your local addresses change:

```bash
export HOME_WEB_HA_TARGET="http://192.168.0.125:8123"
export HOME_WEB_METRICS_TARGET="http://192.168.0.100:8092"
export HOME_WEB_VLLM_TARGET="http://192.168.0.100:8000"
export HOME_WEB_VISION_TARGET="http://192.168.0.100:8091"
export HOME_WEB_INTELLIGENCE_TARGET="http://192.168.0.100:8095"
export HOME_WEB_SUPERVISOR_TARGET="http://home-app.taild52a15.ts.net:8093"
export HOME_WEB_STACK_TOKEN_FILE="/opt/home-ai-voice/.env"
export HOME_WEB_S2S_TARGET="http://192.168.0.100:8094"
export HOME_WEB_TRACKER_TARGET="http://192.168.0.100:8098"
export HOME_WEB_VIDEO_LABELER_TARGET="http://192.168.0.100:8099"
export HOME_WEB_FRIGATE_TARGET="http://192.168.0.125:5000"
export HOME_WEB_APARTMENT_ASSETS_DIR="$HOME/code/home/app/data/apartment"
```

## Stack supervisor auth

The browser/Tailscale web app does not need to store `STACK_TOKEN` in browser
localStorage. The Ubuntu web gateway reads `STACK_TOKEN` server-side from
`HOME_WEB_STACK_TOKEN`, `STACK_TOKEN`, or `HOME_WEB_STACK_TOKEN_FILE` (default:
`/opt/home-ai-voice/.env`) and injects it only when proxying
`/proxy/supervisor/api/stack/...` or `/proxy/supervisor/api/services/...`.

The gateway strips any browser-supplied `Authorization` header before forwarding
supervisor requests, so the real token stays on Ubuntu. `/proxy/supervisor/healthz`
remains unauthenticated and receives no bearer token.

Gateway `/healthz` reports only `stackTokenProxy.enabled` and `source`; it never
returns the token value.

## Proxy policy

The gateway does not expose whole services. It only allows the path families the
Home app uses:

- `/proxy/ha/api/websocket`, states, services, conversation process, camera
  proxy/stream, TTS proxy, and extended conversation endpoints
- `/proxy/metrics/healthz`, `/metrics`, `/conversations/...`, `/trace...`
- `/proxy/vllm/health...`, `/v1/models`, `/v1/chat/completions`, `/v1/completions`
- `/proxy/vision/healthz`, `/snapshot/...`, `/describe...`, `/reason...`, `/locate`, `/api/...`
- `/proxy/intelligence/healthz`, `/api/...`, and read-only intelligence surfaces
- `/proxy/supervisor/healthz`, `/api/stack/...`, `/api/services/...`
- `/proxy/bridge/healthz`, `/rooms`, `/s2s`
- `/proxy/tracker/healthz`, `/ws/tracks`, `/tracks`, calibration/model/frame paths
- `/proxy/video-labeler/healthz`, `/api/video-labeler/...`
- `/proxy/frigate/api/...`

WebSocket upgrades are supported for HA, tracker, and the S2S bridge. Streaming
HTTP/SSE responses are piped through for conversation streams and supervisor
logs.

## Validation

Before publishing or pushing new remote-access changes:

```powershell
rg -n --hidden --glob '!node_modules/**' --glob '!design-audits/**' --glob '!app/data/**' --glob '!*.png' --glob '!*.jpg' --glob '!*.jpeg' --glob '!*.webp' "(HA_TOKEN|HF_TOKEN|STACK_TOKEN|Authorization: Bearer|password|passwd|secret|token=|hf_[A-Za-z0-9]|AIza|sk-)" .
npm run web:check
npm run test:home2
npm run build:home2
```

For browser layout changes, run both mobile and desktop screenshot gates before
deploying. The desktop gate protects the installed Tauri app's shared frontend
shape by checking Tauri-like and wide desktop browser viewports:

```powershell
npm run web:mobile-audit:matrix
npm run web:mobile-audit:deep
npm run web:desktop-audit
```

For actual screenshots, run against either the local dev server or the private
Tailscale Serve URL. The audit uses Playwright when installed and otherwise
falls back to installed Chrome through the Chrome debugging protocol:

```powershell
npm run web:mobile-audit:deep -- --url https://<your-tailnet-serve-url>/
npm run web:desktop-audit -- --url https://<your-tailnet-serve-url>/
```

Optional Playwright setup:

```powershell
npm install --save-dev playwright
npx playwright install chromium
```

The deep mobile audit captures compact-phone, small-phone, phone, large-phone,
landscape phone, and narrow-tablet viewports for the first-run path, mobile
header actions, slash-command surfaces, travel/profile diagnostics, cameras,
drawers, and Apartment `cloud`, `photo`, `mesh`, mode switching, fly-to-camera,
and back-to-overview behavior. The local audit server also serves
`app/data/apartment` at `/assets/apartment`, so mesh mode must render the real
runtime mesh and photo mode must render the real scan/splat asset instead of
passing with unavailable fallbacks. The desktop audit captures `820x900` and
`1280x900` viewports and checks that desktop controls do not collapse into the
mobile menu. Reports include coarse timing summaries for boot and Apartment
surfaces. Generated screenshots and reports stay under `tools/reports/`, which
is ignored by git.

Chrome-based automation does not fully prove iPhone Safari behavior. Before
travel or after mobile Apartment changes, open the Tailscale URL on iPhone
Safari and manually verify login, safe-area/address-bar behavior, chat,
cameras, lights, remote readiness, and Apartment `cloud`/`photo`/`mesh` plus
camera snap/back with the real Frigate feed.

Manual checks from a browser, then from a second Tailscale-connected device:

- The app loads at the Tailscale Serve URL.
- HA chat connects.
- The local RTX model responds through HA.
- Metrics update.
- Vision and camera panels load through same-origin proxy paths.
- Tracker WebSocket connects.
- Stack status reads, and mutating stack actions work through the gateway's
  server-side supervisor token proxy.
- Raw local service ports are not exposed by router forwarding, public DNS, or
  Tailscale Funnel.

For the full travel pre-flight, run `tools/travel-readiness.sh` on the Ubuntu
AI box, `tools/travel-readiness.ps1` on Windows, and `/travel check` in the
Tauri app.
