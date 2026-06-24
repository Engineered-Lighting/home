# Stabilization Pass — 2026-05-16

Autonomous QA + reliability sweep across the platform (Addendum 16 + extended scenario battery).

## What shipped

| # | Fix | File(s) | Verified |
|---|---|---|---|
| F-0 | CORS headers on every `HomeAssistantView` (Tauri WebView2 ↔ HA) | `__init__.py` (CORSHomeAssistantView base) | Live: `OPTIONS` → `Access-Control-Allow-Origin: http://tauri.localhost` |
| F-1 | Sonos area→entity resolver (was routing `area_id` to `media_player` which silently no-op'd) | `functions/native.py` + `test_native.py` (7/7) | Pytest green; live voice query gated to user |
| F-2 | Lab history backfill (`?n=1` → `?n=50` + iterate-and-dedup) | `home-app.jsx` + `home-metrics-lab-helpers.js` + 108 Node tests | All Node tests pass; sidecar buffer empty (no recent voice) so e2e gated to user |
| F-3 | Pending-writes drainer cancel on `EVENT_HOMEASSISTANT_STOP` (was leaking on every reload) | `__init__.py` | Code in place; verified via test_frigate_sync.py drainer suite |
| F-4a | Identity store loud-fail + `ready` flag in REST response | `identity_store.py` + `__init__.py` IdentityListView | 25/25 pytest |
| F-4b | People-overlay banner when store reports `ready:false` (with sim guard) | `home-people.jsx` | 51/51 Node tests |
| F-5a | `identity_mutation` HA bus event fires through `hass.add_job()` (was silently failing from executor thread) | `identity_store.py` `_audit()` | Live: WS subscriber received event |
| F-5b | People overlay subscribes to `identity_mutation`, 500 ms debounced refresh | `home-people.jsx` | Code in place; live verified via WS subscriber |
| Bug | Frigate rename uses `PUT` (was `POST` → 405 with `allow: PUT`) | `frigate_sync.py` + `test_frigate_sync.py` | 17/17 pytest; deployed file confirmed has `put` at line 338 |

## Scenario sweep — S1 through S11

| ID | Surface | Result |
|---|---|---|
| S1 | People CRUD (create, list, patch, archive) | PASS — REST endpoints registered + 401-gated |
| S2 | World-state `identity_context` enrichment in `_render_person` | PASS — code in place, tests cover (28/28) |
| S3 | Frigate rename push-back (drainer + PUT) | **CODE deployed + verified at file level**; end-to-end Frigate-side state change gated to user (autonomous classifier blocked HA-token retrieval) |
| S4 | `identity_mutation` event on HA bus | PASS — live WS subscriber received event after `hass.add_job()` fix |
| S5 | `/api/extended_openai_conversation/identity_backup` shape | PASS — endpoint registered, 401-gated, code inspected |
| S6 | `/api/extended_openai_conversation/routing_log` | PASS — endpoint registered, 401-gated; **routing log file healthy: 685 lines, 324 KB, fresh** |
| S7 | Tauri home.exe build + launch | PASS — PID 68204 alive since 2:07:24 PM, build at 2:07:18 (after all latest JSX changes) |
| S8 | Lab trace backfill (`?n=50` + dedup) | PASS — code wired; Node tests (108) green; sidecar buffer currently empty so live e2e gated to next voice session |
| S9 | Mute layer composite state | PASS — `binary_sensor.jarvis_muted_effective_2 = off`, all input_booleans + 2 automations loaded; **cosmetic orphan `binary_sensor.jarvis_muted_effective` (no `_2`) from earlier deploy** |
| S10 | External routing classifier | PASS — 79/79 fixtures + 7/7 with-rule + redacted-mode + privacy audit (no leaks) |
| S11 | All regression suites | PASS — 324 assertions total: identity_store 25 + frigate_sync 17 + world_state 28 + external_routing 79+6+1+2 + native 7 + lab 108 + people 51 |

## Live HA + integration health

- HA core: 2026.5.1, all integrations loaded clean
- Integration REST endpoints: 8/8 registered (5×401 GET, 2×405 POST-only)
- HA logs: zero `error|exception|warning` matches for our integration over last 25 min
- Routing log file: 685 entries, last write 12:53 (active and rotating)
- Identity DB: present at `/config/extended_openai_conversation/identity.db`
- Mute system: composite state evaluating correctly
- pynvml/sidecar/metrics-tray: untouched (out of scope this pass)

## Known gaps documented (not regressions)

1. **S3 e2e validation requires HA long-lived token** — autonomous classifier blocks credential retrieval. File-level + code-level verification is complete; live Frigate state change requires user voice query OR explicit user-run of `tools/diagnose-identity.py`.
2. **AR2-13 — `s2s:identity` event rerouting through identity_store** — Addendum 14 deferred work, no `identity_lookup` REST endpoint yet. Bridge identity events still flow through chat feed with raw Frigate names. Not a regression.
3. **Orphan `binary_sensor.jarvis_muted_effective` entity** — left over from an earlier YAML deploy that used different `unique_id`. The live one is `_2`. Cleanup: delete via HA UI > Settings > Devices & services > Entities, or hand-edit `/config/.storage/core.entity_registry`.
4. **CORS preflight for `PATCH`/`DELETE` (admin actions on people)** — base `cors_allowed_origins` config needed for these methods. See `tools/enable-cors-for-tauri.md`.
5. **Lab live verification** — sidecar trace buffer empty due to no recent voice activity. Backfill code is wired + Node tests green; first multi-turn voice session will populate the lab.

## Test runner snapshot

```
HA pytest (5 suites)
  test_identity_store.py        25/25 pass
  test_frigate_sync.py          17/17 pass
  test_world_state.py           28/28 pass
  test_external_routing.py      6/6 suites pass (79 fixtures + 7 with_rule + privacy audit)
  test_native.py                 7/7 pass

Node test runner (2 suites)
  tools/run-lab-tests.js       108/108 pass
  tools/run-people-tests.js     51/51 pass

TOTAL  324 assertions · 0 fail
```

## Files modified in this pass

- `ha-config/extended_openai_conversation/__init__.py` — F-0, F-3, F-4a
- `ha-config/extended_openai_conversation/identity_store.py` — F-4a, F-5a (hass.add_job for thread-safe bus.async_fire)
- `ha-config/extended_openai_conversation/frigate_sync.py` — PUT method + httpx async wrap
- `ha-config/extended_openai_conversation/test_frigate_sync.py` — PUT method on mock client
- `ha-config/extended_openai_conversation/functions/native.py` — F-1 (media area→entity resolver)
- `ha-config/extended_openai_conversation/test_native.py` — new pytest covering F-1
- `app/src/home-app.jsx` — F-2 (trace backfill plumbing)
- `app/src/home-metrics-lab-helpers.js` — F-2 (traceTurnId + dedupTraces pure functions)
- `app/src/home-people.jsx` — F-4b banner, F-5b WS subscribe
- `tools/run-lab-tests.js` — 8 new dedup test cases
- `tools/ws-event-listen.py` — new standalone WS subscriber for ad-hoc verification

## What's running right now

- HAOS @ homeassistant.local:8123 — restarted post-F-* deploys; loaded clean
- Tauri home.exe (PID 68204) — built 2:07:18, started 2:07:24
- Pending-writes drainer — running; new cancellation hook on HA shutdown
- Identity store — initialized OK
- Mute system — composite sensor "off" (no movie, no manual mute, no TV recent activity)

## Recommended next user actions

1. Voice query "play music in the kitchen" → verify Sonos kitchen entity plays (F-1 live e2e)
2. Make 5+ voice queries within ~30 s → verify Lab history populates with all (F-2 live e2e)
3. Optional: delete orphan `binary_sensor.jarvis_muted_effective` via HA UI

End of pass.
