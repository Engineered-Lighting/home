# Explicit cross-home lighting

Milestone 2 of shared intelligence, after shared preferences (see
`SHARED-PREFERENCES-RELEASE-2026-09-28.md`). From Home in either house, the
owner can switch lights in Los Angeles (`echo`), Victoria (`victoria`) or both
homes, and nothing moves until they confirm the exact change in the chat.

## Rules

- Only `light.*` entities on each home's allowlist, and only on, off or
  brightness 1–100 %. No scenes, scripts, groups, locks or other domains.
- The owner confirms a frozen proposal. It fixes the site, entity and operation
  for each light, the allowlist revision, the authorization generation and a
  60-second expiry.
- "Both homes" produces separate operations and separate outcomes for each home.
  "Turn them off" with no clear referent, or a request that names no home, gets
  a clarifying question instead of a guess.
- Nothing is retried automatically. An ambiguous result is looked up, never
  resent.
- The LA conversation agent keeps all model-originated actuation disabled, and
  it refuses requests that name Victoria or both homes.

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
- **Idempotency.** Execution is keyed on `(request_id, operation_index)` in a
  SQLite ledger that the component owns.
  - The ledger records `dispatching` before the service call.
  - A success is recorded as `succeeded`.
  - An unavailable light is recorded as `failed`, with no call made.
  - An error, timeout or interruption after the call is recorded as
    `indeterminate`.
  - A repeated key returns the record without calling again. The same key with
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
4. **LA conversation guard** in `extended_openai_conversation`, refusing
   actuation that names Victoria or both homes.
5. **Live acceptance** in both homes. The owner must first pair at least one
   Victoria light and allowlist it.
