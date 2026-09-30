# Explicit cross-home lighting

Milestone 2 of shared intelligence, after shared preferences (see
`SHARED-PREFERENCES-RELEASE-2026-09-28.md`). From Home in either house, the
owner can switch lights in Los Angeles (`echo`), Victoria (`victoria`) or both
homes by asking in the chat.

## Rules

- Only `light.*` entities on each home's allowlist, and only on, off or
  brightness 1–100 % (brightness only on dimmable lights). No scenes, scripts,
  locks or other domains. Light groups are refused, even if allowlisted: a
  group would switch member lights that are not on the list.
- After a one-time consent in the Agent panel, a request that resolves to exact
  allowlisted lights is carried out directly, like Los Angeles's own chat (the
  owner's choice, 2026-09-29; it is a hobby project and the stakes are low).
  Core still freezes each request (site, entity and operation for each light,
  the allowlist revision, the authorization generation and a 60-second
  expiry) and the Agent page confirms that exact review immediately, so every
  execution is signed, recorded and sent once.
- "Both homes" produces separate operations and separate outcomes for each home.
  "Turn them off" with no clear referent gets a clarifying question instead of
  a guess. A request that names no home goes to the home the owner is in (see
  "Choosing the home").
- Nothing is retried automatically. An ambiguous result is looked up, never
  resent.
- The LA conversation agent keeps all model-originated actuation disabled, and
  it refuses requests that name Victoria or both homes.

## Choosing the home

A named home ("in Victoria", "in LA", "in both homes") always wins. For a
light command that names no home, Home's **Home** selector decides:

- **Auto** (the default) asks the gateway's `GET /api/home/location`:
  1. **Network.** The requesting device's direct Tailscale path, as the LA
     node sees it (`tailscale status` `CurAddr`), lies on a home LAN
     (`HOME_WEB_SITE_LANS`). Tailscale Serve names the device with a PROXY
     protocol line.
  2. **Phone.** LA HA's `HOME_WEB_LOCATION_PERSON` is in a home zone
     (`HOME_WEB_SITE_ZONES`, for example `home` and a `Victoria` zone added in
     LA HA). HA's own zone logic decides, so the gateway never sees
     coordinates.
  3. Otherwise **the last home a light command changed** (one home only,
     stored in the browser), and failing that Los Angeles.

  Home never asks which home (the owner's choice, 2026-09-29). When it
  inferred Victoria, the reply says why, for example "(You're in Victoria.)".
- **Los Angeles** or **Victoria** picked by hand applies to this tab until it is
  reloaded.

A command whose home resolves to Los Angeles stays on Los Angeles's own chat
path, as before. This is a choice of target only, never an authority: either
home can already be named explicitly. It replaces, for target choice, the
earlier plan's "fresh fix within five minutes; network only corroborates" rule.
The endpoint returns only a site, how it was decided and a short label; never
coordinates, addresses or entity ids.

Camera questions use the same default home, but only when they name no room
and no home ("show me my home", "what's happening at home?"). A named room
already decides the home, since each camera room exists in one home. In
Victoria that means the den, its only camera; in Los Angeles Home asks only
which of its rooms. Other commands have no Victoria path yet and are
unchanged.

## Access to each home

The owner chose (2026-09-28) a light-only endpoint in the existing
`home_agent_edge` component, rather than a Home Assistant user token:

```
POST /api/home_agent_edge/lighting/v1/inventory   allowlisted lights, states, revision
POST /api/home_agent_edge/lighting/v1/execute     one signed operation
POST /api/home_agent_edge/lighting/v1/outcome     the recorded result of one operation
```

- **Authentication.** Every request is signed with a per-home secret: an
  HMAC-SHA256 over the path and exact body, in `X-Home-Agent-Lighting-Signature`.
  The body carries `issued_at`, which must fall within ±60 s, and an exact
  replay is refused. The secret is not a Home Assistant token. If it leaked, it
  could switch only allowlisted lights.
- **Allowlist revision.** The endpoint hashes the site and its sorted allowlist.
  An execute built against a different revision is refused
  (`allowlist_changed`).
- **Expiry.** A proposal lasts 60 s. The endpoint accepts `expires_at` up to
  120 s ahead to absorb clock skew between hosts.
- **Idempotency and outcomes.** Execution is keyed on
  `(request_id, operation_index)` in a SQLite ledger that the component owns.
  Once accepted, an operation runs to completion even if the caller
  disconnects. Statuses:
  - `dispatching`: recorded before the service call; still in progress.
  - `succeeded`: the call returned **and** the light's state then showed the
    change (for brightness, within 2 %) within 3 s.
  - `failed`: the light was unavailable, so no call was made.
  - `indeterminate`: an error, timeout or interruption after the call, or the
    state never showed the change. It is never resent.
  - `absent`: an outcome lookup found no execute. This is final: the key is
    recorded, and a later execute with it is refused (`request_withdrawn`).

  A repeated key returns the record without calling again. The same key with
  different content is a conflict.

Configuration, on each home:

```yaml
home_agent_edge:
  lighting:
    site_id: victoria            # or echo
    secret: !secret home_agent_lighting_secret   # 64 lowercase hex, unique per home
    entities:
      - light.kitchen
```

The whoami view is unchanged. A missing or invalid `lighting` block disables
only lighting. Enabling or changing lighting requires a Home Assistant
restart. On Victoria, coordinate that restart with the Lab owner.

## Remaining work

1. **Core lighting service**, a private TLS listener like `echo-preferences`.
   - Registers and grants `lighting.execute` per site. The database CHECK already
     allows it, and owner consent extends the existing consent flow.
   - Ports the reviewed intent kernel (`tools/shared-home/home_shared/authority.py`),
     without scenes.
   - Freezes proposals and keeps a durable dispatch journal.
   - Calls each home's endpoint with its secret. Victoria is reached through
     `https://home-app.<tailnet>.ts.net:10001` under an egress contract.
2. **BFF** `/api/agent/lighting/{propose,confirm,outcome}`, mirroring the
   personal-memory routes: fresh identity, origin, CSRF, exact shapes and
   `503 … outcome_unknown` on any doubt.
3. **Home**. A lighting intent runs before camera and Home Assistant routing.
   Its confirmation uses the same framed Agent-origin card as in-chat preference
   review (#156), not a popup.
4. **LA conversation guard** (`cross_home_guard.py` in
   `extended_openai_conversation`), refusing actuation that names Victoria or
   both homes. It is hooked into the live integration's conversation agent;
   deploy it to LA Home Assistant, then check that "turn off the kitchen light
   in Victoria" gets the fixed reply.
5. **Live acceptance** in both homes. The owner must first pair at least one
   Victoria light and allowlist it.
