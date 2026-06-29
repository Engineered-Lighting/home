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

## Optional basic auth

Tailscale is the real access boundary. Basic auth is optional and is useful as a
small second lock if a browser session is left open.

Generate one username/password pair:

```powershell
node -e "const c=require('node:crypto'); console.log('marcelo:'+c.randomBytes(18).toString('base64url'))"
```

Then start the gateway with the generated value:

```powershell
$env:HOME_WEB_BASIC_AUTH="marcelo:<generated-password>"
npm run web:start
```

The gateway accepts the initial `Basic` authorization header, sets an HttpOnly
cookie for the web app, and strips Basic auth before proxying requests upstream.
Bearer tokens used by Home Assistant and the stack supervisor still pass
through.

## Tailscale Serve

With the gateway running:

```powershell
tailscale serve --bg http://127.0.0.1:5181
tailscale serve status
```

Use the HTTPS tailnet URL shown by Tailscale from another device that is signed
into your tailnet. To turn it off:

```powershell
tailscale serve reset
```

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
