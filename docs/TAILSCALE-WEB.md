# Home app Tailscale web access

This is the first travel-access version of the full `app/src` Home app. It
keeps the app private to your tailnet and exposes one same-origin web gateway
instead of exposing the raw HA, AI, camera, or supervisor ports.

## Shape

- `web-gateway/server.mjs` serves `app/src`.
- `app/src/home-web-runtime.js` runs before `home-app.jsx` in a normal browser.
- Browser mode defaults service bases to `/proxy/...`.
- Tauri desktop behavior keeps the existing LAN defaults.
- Tailscale Serve publishes the local gateway privately to tailnet devices.
- Tailscale Funnel, router port forwarding, and public DNS are intentionally out
  of scope for v1.

## Run locally

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

## Native login

Tailscale is the real access boundary. The gateway also has a small first-party
login screen so a browser left open is not enough by itself.

Generate one username/password pair:

```powershell
node -e "const c=require('node:crypto'); console.log('marcelo:'+c.randomBytes(18).toString('base64url'))"
```

Seed the first login with the generated value:

```powershell
[Environment]::SetEnvironmentVariable("HOME_WEB_BASIC_AUTH", "marcelo:<generated-password>", "User")
```

Open `/auth` on the tailnet URL to sign in, sign out, or change the password.
Password changes are written as a salted PBKDF2 hash to the local auth file
below, and that file takes precedence over the initial environment variable:

- Default auth file: `%APPDATA%\EngineeredLightingHome\web-auth.json`
- Override path: `HOME_WEB_AUTH_FILE`

Bearer tokens used by Home Assistant and the stack supervisor still pass
through. The gateway strips its own cookies and Basic auth before proxying
requests upstream.

## Start on Windows login

No-admin setup uses the current user's Startup folder. The launcher runs
`tools/start-home-web-gateway.ps1`, which starts `web-gateway/server.mjs` and
reads `HOME_WEB_BASIC_AUTH` from the user's Windows environment.

To test the same starter manually:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Claude\home\tools\start-home-web-gateway.ps1
```

## Tailscale Serve

With the gateway running:

```powershell
tailscale serve --bg --tls-terminated-tcp=443 127.0.0.1:5181
tailscale serve status
```

Use the HTTPS tailnet URL shown by Tailscale from another device that is signed
into your tailnet. To turn it off:

```powershell
tailscale serve --tls-terminated-tcp=443 off
```

Use `--tls-terminated-tcp` rather than the default HTTPS web proxy mode. The
Home app needs HA, tracker, and S2S WebSocket upgrades, and this mode preserves
those upgrades while still giving browsers the Tailscale HTTPS URL.

Reference: https://tailscale.com/docs/reference/tailscale-cli/serve

## Service targets

Override these only if your local addresses change:

```powershell
$env:HOME_WEB_HA_TARGET="http://192.168.0.125:8123"
$env:HOME_WEB_METRICS_TARGET="http://192.168.0.100:8092"
$env:HOME_WEB_VLLM_TARGET="http://192.168.0.100:8000"
$env:HOME_WEB_VISION_TARGET="http://192.168.0.100:8091"
$env:HOME_WEB_INTELLIGENCE_TARGET="http://192.168.0.100:8095"
$env:HOME_WEB_SUPERVISOR_TARGET="http://192.168.0.100:8093"
$env:HOME_WEB_S2S_TARGET="http://192.168.0.100:8094"
$env:HOME_WEB_TRACKER_TARGET="http://192.168.0.100:8098"
$env:HOME_WEB_VIDEO_LABELER_TARGET="http://192.168.0.100:8099"
$env:HOME_WEB_FRIGATE_TARGET="http://192.168.0.125:5000"
$env:HOME_WEB_APARTMENT_ASSETS_DIR="C:\Claude\home\app\data\apartment"
```

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

Manual checks from a browser, then from a second Tailscale-connected device:

- The app loads at the Tailscale Serve URL.
- HA chat connects.
- The local RTX model responds through HA.
- Metrics update.
- Vision and camera panels load through same-origin proxy paths.
- Tracker WebSocket connects.
- Stack status reads, and mutating stack actions still require the supervisor
  bearer token.
- Raw local service ports are not exposed by router forwarding, public DNS, or
  Tailscale Funnel.
