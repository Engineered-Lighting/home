# Cross-home lighting release receipt — 2026-09-29

Status: **cross-home lighting live in both homes and accepted by the owner** (Home chat, 2026-09-29), together with
travel-aware defaults. See `CROSS-HOME-LIGHTING.md` for the rules and design. Times are PDT unless marked UTC.

## What runs where

| Piece | Where | Version / identity |
|---|---|---|
| Lighting listener | LA host, `home-shared-preferences-lighting-1`, `172.23.0.37:9448` | Core image `0389ea9c…` (signed, from `e927f948`) |
| Core API, worker, ingest | LA host, `home-agent` project | `ad01483b…` (signed, from `f4af3f02`, #181) |
| Other private listeners | echo/victoria identity, link-coordinator, echo-preferences | `065463f0…` (unchanged) |
| BFF | `home-agent-bff-1` | `014710aa…` |
| Agent Origin | `home-agent-origin-origin-1` | `5a03ce42…` (from `b0f408d8`, People session); lighting-review page unchanged since `4d00a39e…` |
| Light-only HA endpoint | `home_agent_edge` 0.2.0 in LA HA (2026.9.4) and Victoria HA (2026.9.3) | Signed per-home secret; allowlists: LA 14 lights, Victoria 1 (Rear Left) |
| Home chat | `home-web-gateway`, served checkout `/home/marcelo-lima/code/home` | See "Home files" below |

- **Listener profile:** `lighting/config/listener.json` (`dc9dd2cf…`) with 365-day consent grants. The owner's first
  consent was issued for 30 days; choosing **Review lighting control** again extends it to 365.
- **Limits:** 0.5 CPU, 512 MiB RAM (no swap), 64 processes. Observed about 94 MiB.
- **TLS leaf:** `CN=lighting`, IP SAN `172.23.0.37`, from the retained internal CA, expires **2026-12-28 07:41 UTC**.
  The daily `home-agent-internal-tls-expiry` check covers it (first reported `ok`, 89 days left, on the
  2026-09-29 09:29 run) and warns from 30 days out; `renew_internal_tls.py` renews it like the other leaves.
- **Egress:** bridge `home-agent_lighting-egress` (`ha-light-egr0`, `172.27.0.10`) to the Serve ports `:10000` (LA HA)
  and `:10001` (Victoria HA) only. Enforced by firewall profile `lighting` (chain `HOME_AGENT_LIGHT_INPUT`, hook
  digest `42e83a03…`) and checked every 5 minutes by `home-agent-lighting-egress-verify.timer` (last result success).
- **Restart policy:** `unless-stopped` from this change on, after an 8-hour sustained check (below). Before this it was
  `no`, so a crash or reboot left lighting down until someone started it.

## Home files (served checkout)

| File | sha256 prefix | From |
|---|---|---|
| `app/src/home-app.jsx` | `5bb0a6ca` | #178 hunks on the mixed served file |
| `app/src/home-lighting-intent.js` | `13216c3d` | #178 |
| `app/src/home-lighting-control.js` | `6cb9429e` | #179 |
| `web-gateway/server.mjs` | `97681a4f` | #178 hunks, CRLF kept, Lab hooks untouched |
| `web-gateway/home-location.mjs` | `3e3fae4a` | #178 |
| `app/src/index.html` | `a62f2974` | unchanged |

- **Gateway env:** `HOME_WEB_SITE_LANS`, `HOME_WEB_LOCATION_PERSON=person.engineeredlighting` and
  `HOME_WEB_SITE_ZONES=echo=home,victoria=Victoria`.
- **LA HA zone:** the owner added a `Victoria` zone (not passive, 100 m).
- **Tailscale Serve:** `:443` on the LA node is `--tls-terminated-tcp` with `--proxy-protocol=1`. Every other handler is
  byte-identical to the pre-change baseline.

## Timeline

- **09:47** Final lighting deploy from `e927f948`: lighting container, firewall profile, BFF and Origin; the owner
  confirmed a live cross-home switch.
- **09:51** Lighting listener recreated with 365-day grants. No restart since.
- **11:49** #178 (travel-aware defaults) deployed: one gateway restart, then the Serve `:443` PROXY flag. The live
  locator reported the Victoria desktop as "on the Victoria network" and the phone as "your phone is in Victoria".
- **11:59** #179 (lighting card works out of sight) deployed: one gateway restart.
- **~11:52, 12:23, 14:46, 15:32, 16:16** LA HA restarts by other sessions: the 2026.9.4 upgrade, its chat fix (#180),
  the Frigate 5.15.6 update, #184 with #186 (the cross-home guard now live in LA's chat), and #187.
  - Victoria HA restarted once for the Lab (2026.9.3).
  - After each restart, a signed read-only inventory from the lighting container returned LA 14 and Victoria 1, and
    `person.engineeredlighting` read `Victoria`.
- **14:49** #181 Core deploy (`ad01483b`); lighting unaffected.

## Sustained check before auto-start

Checked 2026-09-29 18:00, about 8 hours after the 09:51 start:
- The container was running with 0 restarts and was not OOM-killed.
- CPU was 0.06 % and memory 94 MiB of 512 MiB, with 3 processes.
- The logs had 0 error lines in 24 hours.
- The egress verify timer was succeeding.
- It had survived five LA HA restarts and one Victoria HA restart without intervention.

## Attestation outage (resolved)

Signed-image CI failed while the repository was private: GitHub artifact attestations require a public repository on the
Free plan. The owner made the repository public again, the failed jobs were re-run, and every image above was verified
with `gh attestation verify` and imported with `operator/imported_image_identity.py`.

## Rollback

- **Core:** `prepared-20260928/core-runtime-0047-5bb2eb55.json` (`065463f0…`); receipt `receipts/core-181-f4af3f02.json`.
- **Lighting profile:** `prepared-20260928/rollback-lighting/` (30-day listener, previous env).
- **Home, #178:** first `tailscale serve --bg --tls-terminated-tcp=443 127.0.0.1:5181`, then restore
  `~/travel-rollback-20260929-114921/`.
- **Home, #179:** restore `~/quiet-card-rollback-20260929-115937/`.

## Open items

- Workshop camera: its Frigate feed is down (pre-existing).
- LA `extended_openai_conversation`: the `IdentityStore` `legacy_identity_semantics_frozen` error predates this work.
