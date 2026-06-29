# Enable CORS for the Home App ↔ HA integration

## Why this is required

The Tauri Home app's webview runs at origin `http://tauri.localhost`. Every
request it makes to the HA integration's REST endpoints
(`/api/extended_openai_conversation/…`) is cross-origin, and the browser
enforces CORS:

- **GET with `Authorization: Bearer …`** is a *non-simple* request
  (`Authorization` is not a CORS-safelisted header), so the browser sends an
  `OPTIONS` preflight first. If preflight fails, the GET never happens.
- **PATCH / DELETE with a JSON body** are always preflighted.

The integration's views are registered with `cors_allowed = True`, which opts
them in to HA's `aiohttp_cors` middleware. The middleware:

- Auto-handles the preflight (`OPTIONS`) and returns the right
  `Access-Control-*` headers.
- Adds `Access-Control-Allow-Origin` (echoing the request's `Origin`) to the
  actual response.

**But the middleware only fires if `http: cors_allowed_origins` is configured
in `configuration.yaml`.** Without that key, preflights return 401 and the
browser sees `Failed to fetch`.

(Earlier the integration also set `Access-Control-Allow-Origin: *` on every
response from inside `CORSHomeAssistantView.json()`. That was removed because
when the operator had `cors_allowed_origins` set, the explicit header
collided with the middleware-set header, `aiohttp_cors` raised, and the
connection was dropped before any body was written. The middleware is now
the single source of truth.)

## Required `configuration.yaml`

```yaml
http:
  cors_allowed_origins:
    - "http://tauri.localhost"
    - "https://tauri.localhost"
```

Or, on a trusted LAN, the wildcard:

```yaml
http:
  cors_allowed_origins:
    - "*"
```

Then restart HA: `ha core restart`.

## Why a wildcard is acceptable on a trusted LAN

- HA's bearer-token auth still applies — CORS only controls which *origins*
  can READ a response, not whether the request is authenticated.
- Without the bearer token, every request still returns 401.
- The threat model assumes a trusted LAN (per Addendum 14).

## Verification

After the config change + HA restart:

```powershell
$token = (ssh hav-ubuntu "grep '^HA_TOKEN=' /opt/home-ai-voice/.env | cut -d= -f2-").Trim()

# OPTIONS preflight should return 200 with the Origin echoed back
curl.exe -s -D - -o NUL -X OPTIONS `
  -H "Origin: http://tauri.localhost" `
  -H "Access-Control-Request-Method: PATCH" `
  -H "Access-Control-Request-Headers: authorization,content-type" `
  "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc"

# Actual GET with Origin should return 200 and ACAO: http://tauri.localhost
curl.exe -s -D - -o NUL `
  -H "Authorization: Bearer $token" `
  -H "Origin: http://tauri.localhost" `
  "http://homeassistant.local:8123/api/extended_openai_conversation/identities"
```

Both should return `HTTP/1.1 200` (or 204 for OPTIONS) with
`Access-Control-Allow-Origin: http://tauri.localhost` in the headers.

If GET returns `Empty reply from server`, the integration is double-setting
the header again — `CORSHomeAssistantView.json()` must not include any
`Access-Control-*` headers.
