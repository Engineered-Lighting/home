# Scenario Test Suite — home app

Successor to the ad-hoc S1–S11 sweep in
[STABILIZATION-2026-05-16.md](STABILIZATION-2026-05-16.md). S1–S11 stay
as-numbered (today's pass already cites them); new scenarios continue
at S12. Tone: executable. Each scenario is a recipe a human or CI can
run unattended.

## 1. Coverage summary

| Area | S1–S11 coverage | Gaps closed by S12+ |
|---|---|---|
| People REST (CRUD) | S1 | — (covered) |
| People overlay UI (banner, WS sub) | partial (backend only) | S12, S13 |
| World-state tools + identity rules | S2 (code only) | S14, S15, S16 |
| Frigate rename + PUT method | S3 (gated) | **S17 (playbook)** |
| `identity_mutation` HA bus event | S4 | S18 (live subscriber loop) |
| `/identity_backup` shape | S5 | — |
| `/routing_log` endpoint | S6 | — |
| Tauri build + launch | S7 | S19 (build → CORS handshake → first paint) |
| Lab trace backfill (`?n=50` + dedup) | S8 (gated) | **S20 (playbook)** |
| Mute composite state | S9 (entity only) | S21 (voice mute lifecycle e2e) |
| External routing classifier | S10 | S22 (slash overrides + voice e2e) |
| Test runner regression suite | S11 | — |
| CORS preflight (F-0) | none | S23 |
| Sonos area→entity (F-1) | none — gated | S24 |
| Pending-writes drainer cancel (F-3) | none | S25 |
| home-vision panel | none | S26 |
| home-events panel (chat feed) | none | S27 |
| home-control sliders / quick chips | none | S28 |
| home-proactive (welcome home, arrival) | doc only | S29 |
| home-ai-stack supervisor card | none | S30 |
| home-stack-actions confirm pattern | none | S31 |
| home-metrics-lab sim scenarios (9) | none | S32 |
| home-s2s bridge events | none | S33 |
| Sidecar SSE flake (25% per W suite) | none | S34 |
| End-to-end voice composite | none | S35 |
| Lights — every control path | none | S36–S43 |
| Sonos / media — every control path | partial (S24, F-1) | S44–S51 |
| Camera feeds — view + describe | partial (S26 load only) | S52–S57 |
| Multi-step planning | none | S58–S62 |
| Abstract ask interpretation | none | S63–S68 |
| Model tone / vibe | partial (S14–S16) | S69–S74 |
| Metrics trays (per tab) | partial (S30, S32) | S75–S78 |
| Exhaustive button sweep (per panel) | implicit (S12, S28, S31) | S79–S88 |
| Stack / supervisor controls | partial (S30, S31) | S89–S92 |
| Proactive notifications | partial (S29) | S93–S95 |
| External integrations (chat + voice) | partial (S22) | S96–S99 |
| Simulation mode | none | S100–S104 |
| Error states + recovery | none | S105–S110 |

**Biggest gaps before this pass:** the UI side of the Tauri frontend
was almost entirely untested (zero coverage for vision / events /
control / proactive / ai-stack / lab UI / sim fixtures), and the
sidecar SSE flake measured in [diagnose-report.md](../tools/diagnose-report.md)
had no reproduction harness. The second pass adds 75 scenarios across
lights, media, vision, planning, abstract-intent, tone, UI sweep, sim
mode, and error-state recovery — bringing total coverage to 110
scenarios.

---

## 2. Scenarios

Every scenario follows the same skeleton: title · area · preconditions
· steps · expected · verify · risk. Voice scenarios assume Voice PE is
paired and the user is at the workstation; otherwise substitute the
`POST /api/conversation/process` curl in **Useful one-liners** in
[RUNBOOK.md](RUNBOOK.md).

### S12 — People overlay banner shows when identity store is not ready

- **Area:** `home-people.jsx` (F-4b)
- **Preconditions:** HA up; integration loaded; identity store seeded.
- **Steps:**
  1. In DevTools, set `window.__SIM_IDENTITY_READY = false` and click the people pill in the header.
  2. Re-toggle to `true` and observe.
- **Expected:** Overlay opens with an amber banner *"Identity store not ready — list may be stale."* Banner disappears within one render after the flag flips.
- **Verify:** Visual; also `document.querySelector('[data-testid="people-not-ready-banner"]')` non-null while flag is false.
- **Risk:** Regression of F-4b (silent stale list).

### S13 — People overlay refreshes on `identity_mutation` event (≤500 ms debounce)

- **Area:** `home-people.jsx` (F-5b), `identity_store.py` (F-5a)
- **Preconditions:** HA up, `/connect` succeeded, overlay open, ≥1 identity present.
- **Steps:**
  1. From any shell: `curl -X PATCH -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" -d '{"display_name":"Marcelo (renamed S13)"}' http://homeassistant.local:8123/api/extended_openai_conversation/identity/<id>`
  2. Watch overlay.
- **Expected:** Within ~500 ms the list row updates to the new display name without manual refresh.
- **Verify:** UI; cross-check `ha core logs | grep identity_mutation` shows the event fired via `hass.add_job`.
- **Risk:** F-5a silent executor-thread failure; F-5b overlay subscription drift.

### S14 — `find_person("Marcello")` (wrong spelling) does NOT return Marcelo

- **Area:** `world_state.py` + Rule 0.5 / 0.6
- **Preconditions:** World state enabled; Marcelo registered.
- **Steps:**
  1. Voice or curl: *"where is Marcello?"* (double-L).
- **Expected:** Assistant replies *"I don't have anyone named Marcello — did you mean Marcelo?"* (no fabricated location).
- **Verify:** Chat feed; also `/world-state` slash → `find_person` returns `data: null` for "Marcello".
- **Risk:** Hallucinated identity match; violates HARD rule "NEVER call an unknown person by a known person's name."

### S15 — "Do you see me?" hedges when freshness is `recent` not `fresh`

- **Area:** `world_state.py` (`suggested_phrasing`)
- **Preconditions:** Frigate has seen Marcelo within `RECENT_SECONDS` (180s) but not within `FRESH_SECONDS` (60s); confirm via `/world-state kitchen`.
- **Steps:**
  1. Voice: *"do you see me?"*
- **Expected:** Reply uses hedged phrasing (e.g. *"I think I saw you in the kitchen a minute ago"*), not *"yes, I see you right now."*
- **Verify:** Chat feed + `/world-state` snapshot showing freshness band.
- **Risk:** Over-confident perception report; world-state hedging regression.

### S16 — `get_room_state("attic")` (unknown room) returns null cleanly

- **Area:** `world_state.py`, prompt rule
- **Preconditions:** No `attic` camera mapped.
- **Steps:**
  1. Voice or `/world-state attic`.
  2. Voice: *"what do you see in the attic?"*
- **Expected:** Tool returns `{data: null, error: ...}`; assistant replies *"I don't have any data for the attic"*; no fabricated room.
- **Verify:** Slash-command JSON + chat feed.
- **Risk:** Hallucinated room state; rule 0.6 violation.

### S17 — Frigate rename push-back end-to-end (playbook, gated in today's pass)

- **Area:** `frigate_sync.py` PUT + drainer (F-3 + Frigate PUT bug)
- **Preconditions:** Long-lived HA token in `$HA_TOKEN`; Frigate online with a known face name; integration up.
- **Steps:**
  1. List current Frigate names: `curl -s -H "Authorization: Bearer $HA_TOKEN" http://homeassistant.local:8123/api/extended_openai_conversation/identities | jq '.identities[] | {id, display_name, frigate_name}'` — pick one identity with a `frigate_name`.
  2. Patch its display name: `curl -X PATCH -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" -d '{"display_name":"S17 Test"}' http://homeassistant.local:8123/api/extended_openai_conversation/identity/<id>`
  3. Tail logs: `ssh -p 22222 root@homeassistant.local "ha core logs 2>&1 | grep -iE 'frigate_sync|PUT' | tail -10"`
  4. Hit Frigate directly: `curl -s http://homeassistant.local:5000/api/faces | jq` (or your Frigate host).
- **Expected:** HA log shows `PUT … /api/faces/…` returning 200. Frigate face list reflects the new name within ~2 s. Zero 405 errors.
- **Verify:** Frigate API + HA logs.
- **Risk:** Regression to POST → 405; drainer leak on shutdown.

### S18 — `identity_mutation` HA bus event fires from executor thread

- **Area:** `identity_store.py` `_audit()` (F-5a)
- **Preconditions:** HA up; `tools/ws-event-listen.py` ready.
- **Steps:**
  1. In one shell: `py -3 tools/ws-event-listen.py --event identity_mutation`
  2. In another: trigger any identity write (S13's PATCH works; or `POST /identities`).
- **Expected:** Subscriber prints the event payload within 1 s; includes `op`, `id`, `actor`, `ts`.
- **Verify:** WS subscriber stdout.
- **Risk:** Silent `bus.async_fire` from non-loop thread (the pre-F-5a failure mode).

### S19 — Tauri build, launch, first paint, CORS handshake

- **Area:** `home-tauri.jsx`, `__init__.py` CORS (F-0), S7 extended
- **Preconditions:** Workstation Windows; HA up with `cors_allowed_origins` configured per [enable-cors-for-tauri.md](../tools/enable-cors-for-tauri.md).
- **Steps:**
  1. `cd C:\Claude\home\app && cargo tauri build`
  2. Launch `home.exe`; open DevTools (Ctrl+Shift+I).
  3. In Network tab, filter `extended_openai_conversation`; reload.
- **Expected:** Build succeeds; window opens; the first OPTIONS preflight to any `/api/extended_openai_conversation/*` returns 204 with `Access-Control-Allow-Origin: http://tauri.localhost`; the GET that follows returns 200 — no double-set header collision.
- **Verify:** DevTools Network panel; PID alive (`Get-Process home`).
- **Risk:** F-0 regression — duplicate ACAO header dropping the connection.

### S20 — Lab trace backfill populates from `?n=50` after voice burst (playbook, gated in today's pass)

- **Area:** `home-app.jsx` + `home-metrics-lab-helpers.js` (F-2)
- **Preconditions:** Tauri app open on Lab tab; sidecar healthy (`curl -s http://<ai-box>:8092/healthz`).
- **Steps:**
  1. Note the current "history" count in the Lab chart card (likely 0 if sidecar buffer cold).
  2. Make 5+ short voice queries within ~30 s (e.g. *"what time is it"*, *"who is home"*, *"turn off office light"*, *"turn it back on"*, *"thanks"*).
  3. Watch the Lab chart fill in.
- **Expected:** Within ~3 s of the 5th turn, the chart shows all 5 turns in history mode; no duplicate trace IDs in `labTurnsRef`; baseline hero number renders.
- **Verify:** UI visually; DevTools `window.__LAB_TURNS?.length === 5` (or whatever React exposes); manual check that no two entries share the same `turnId`.
- **Risk:** F-2 regression — single-turn fetch leaving Lab empty; dedup failure causing inflated history.

### S21 — Voice mute lifecycle: stop → query during mute → wake up

- **Area:** mute composite (S9 extension), bridge gate, conversation.py
- **Preconditions:** Mute system loaded; `binary_sensor.jarvis_muted_effective_2 = off`.
- **Steps:**
  1. Voice: *"Hey Jarvis, stop"*
  2. Wait 5 s; voice: *"what time is it"*
  3. Voice: *"Hey Jarvis, wake up"*
  4. Voice: *"what time is it"*
- **Expected:** (1) acknowledged or silent ack — `binary_sensor.jarvis_muted_effective_2 → on`. (2) no response — bridge drops transcript. (3) sensor → off. (4) normal answer + TTS.
- **Verify:** HA Developer Tools → States; chat feed shows mute pill on/off.
- **Risk:** Bridge mute-cache staleness; TTS not cancelling per defense-in-depth gates.

### S22 — External routing slash overrides + voice GENERAL pattern

- **Area:** `home-external.jsx`, `external_routing.py` (S10 extension)
- **Preconditions:** OpenAI key set (`/external set-key`); auto-routing on.
- **Steps:**
  1. `/route who invented the lightbulb` — classifier debug only.
  2. `/local who invented the lightbulb` — force local.
  3. `/ask explain quantum physics` — force external.
  4. Voice: *"when was Lincoln born"* (matches GENERAL).
- **Expected:** (1) prints `external · matched=GENERAL`, dispatches nothing. (2) local home agent answers (likely *"I don't know"*). (3) external answers in 2–4 plain sentences. (4) voice routes external; spoken answer ≤ ~300 tokens.
- **Verify:** Chat feed + `/route-log 5` → JSONL shows correct `dispatch` per turn.
- **Risk:** Classifier regression; voice path bypass; privacy leak (re-check via `/test external-privacy`).

### S23 — CORS preflight for PATCH and DELETE returns 204 + ACAO

- **Area:** `__init__.py` CORS base + HA `http.cors_allowed_origins` (F-0, gap #4 from today)
- **Preconditions:** HA up; `cors_allowed_origins` set.
- **Steps:**
  1. PATCH preflight: `curl.exe -s -D - -o NUL -X OPTIONS -H "Origin: http://tauri.localhost" -H "Access-Control-Request-Method: PATCH" -H "Access-Control-Request-Headers: authorization,content-type" "http://homeassistant.local:8123/api/extended_openai_conversation/identity/abc"`
  2. Same with `-H "Access-Control-Request-Method: DELETE"`.
- **Expected:** Both return `HTTP/1.1 204` with `Access-Control-Allow-Origin: http://tauri.localhost` and `Access-Control-Allow-Methods` including PATCH and DELETE respectively.
- **Verify:** curl headers.
- **Risk:** F-0 admin-action breakage; "Failed to fetch" in the Tauri overlay.

### S24 — "Play music in the kitchen" routes Sonos by area, not media_player domain

- **Area:** `functions/native.py` (F-1)
- **Preconditions:** Sonos paired; kitchen area has a media_player entity; vLLM up.
- **Steps:**
  1. Voice: *"play music in the kitchen"*.
  2. Tail: `ssh -p 22222 root@homeassistant.local "ha core logs 2>&1 | grep -iE 'native|media_player|area_id' | tail -20"`
- **Expected:** Kitchen Sonos starts playing within ~3 s. Log shows area resolved to a concrete `media_player.kitchen_*` entity (NOT `area_id: kitchen` sent to media_player domain).
- **Verify:** Audible + HA `media_player.kitchen_*` state → playing.
- **Risk:** F-1 regression — silent no-op when `area_id` is passed to `media_player`.

### S25 — Pending-writes drainer cancels on `EVENT_HOMEASSISTANT_STOP`

- **Area:** `__init__.py` (F-3)
- **Preconditions:** HA up; identity store has at least one buffered write (trigger one with S13 PATCH first).
- **Steps:**
  1. `ssh -p 22222 root@homeassistant.local "ha core logs 2>&1 | tail -f"` in one shell.
  2. `ssh -p 22222 root@homeassistant.local "ha core restart"` in another.
- **Expected:** Logs show `pending-writes drainer: cancelled on HA stop` (or equivalent), drainer task is awaited and exits cleanly, no `Task was destroyed but it is pending` warning.
- **Verify:** Restart logs; no warnings under `extended_openai_conversation`.
- **Risk:** F-3 regression — task leak on every reload, eventually exhausting executor / cron deadlock on shutdown.

### S26 — home-vision panel: all cameras load without error

- **Area:** `home-vision.jsx`
- **Preconditions:** HA up; ≥1 camera entity online; `/connect` set.
- **Steps:**
  1. Open Tauri app; observe vision strip in the chat surface.
  2. DevTools Network: filter `camera_proxy_stream`.
- **Expected:** Each camera frame loads (HTTP 200) within ~2 s; auto-refresh fires every `VISION_REFRESH_MS` (2 min); no console errors.
- **Verify:** Visual + DevTools.
- **Risk:** Signed-URL refresh failure; CORS regression on the camera proxy.

### S27 — home-events chat feed renders user, assistant, system, and tool turns

- **Area:** `home-events.jsx`
- **Preconditions:** App open; at least one of each event kind in feed (use `/sim action-success` to generate fast).
- **Steps:**
  1. Run `/sim metrics-timeline-history` then send a few `/local hello` turns.
  2. Visually compare turn header chips (user / assistant home / assistant external / system / tool / route diag).
- **Expected:** Each kind renders with the correct accent + monospace block for code/JSON; timestamps in `HH:MM:SS`; long content scrolls (no clipping).
- **Verify:** Visual; DevTools `document.querySelectorAll('[data-event-kind]')` returns all expected kinds.
- **Risk:** Renderer crash on a new event kind; CSS regression hiding events.

### S28 — home-control: light slider commits a real `light.turn_on` service call

- **Area:** `home-control.jsx`
- **Preconditions:** Connected to HA; `light.office` reachable.
- **Steps:**
  1. Open the office room context (or use `/sim …` if context routing is faked).
  2. Drag the brightness slider to 30%; release.
  3. Inspect Network → `services/light/turn_on`.
- **Expected:** Service call body includes `entity_id: light.office` + `brightness_pct: 30`; UI shows applying spinner then status chip flips to `on`; bulb actually responds.
- **Verify:** HA `states/light.office` → brightness ≈ 76 (30% of 255); chat feed control-lifecycle line.
- **Risk:** `fireServiceCall` payload regression; quick-chip handler drift.

### S29 — Two-stage arrival (HA test harness)

- **Area:** `home-proactive.jsx` + `ha-config/homeai_proactive_test.yaml`
- **Preconditions:** Test harness package installed; app open; `/debug on`; `/proactive reset` first.
- **Steps:**
  1. From HA dashboard, tap `input_button.homeai_test_arrival`.
  2. Within 3 s, observe puck + app + lights.
- **Expected:** App feed shows `arrival pending → confirmed → Welcome home, Marcelo`; `assist_satellite.<puck>` → `responding`; `script.homeai_return_home` runs; indoor lights come on.
- **Verify:** HA Developer Tools → States (`assist_satellite`) + logbook + visible lights.
- **Risk:** Coordinator drift; missed two-stage gate; identity name misuse (would say "Marcello" or generic "someone").

### S30 — AI Stack Control card connects with supervisor token

- **Area:** `home-ai-stack.jsx` + `stack/services/supervisor/main.py`
- **Preconditions:** Supervisor systemd unit up; `STACK_TOKEN` set in `/opt/home/stack/.env`.
- **Steps:**
  1. In Tauri DevTools: `localStorage.setItem("hg-stack-token-DEV", "<token>")`; reload.
  2. Open AI tab; observe AiStackCard.
- **Expected:** Card flips from "not configured" → "online" within one 15-s poll; service rollup shows 6 containers; `/healthz` returns 200 in Network tab.
- **Verify:** Card text + Network 200; `curl -s -H "Authorization: Bearer $STACK_TOKEN" http://<ai-box>:8093/api/stack/status | jq .overall` matches.
- **Risk:** Token plumbing regression; supervisor unit failed silently.

### S31 — Two-click confirm gate blocks single-click destructive actions

- **Area:** `home-stack-actions.jsx`
- **Preconditions:** Stack token set; AI tab open with AiStackCard.
- **Steps:**
  1. Click "free gpu" once — observe label.
  2. Wait 4 s without clicking; observe label.
  3. Click "free gpu" twice within 2 s; observe Network.
- **Expected:** (1) label → `✓ confirm`. (2) reverts to `free gpu` after 3 s timeout — no POST fired. (3) second click fires `POST /api/stack/free-gpu` with bearer token. `/api/stack/free_gpu` remains accepted as a compatibility alias.
- **Verify:** DevTools Network + visible button label.
- **Risk:** Confirm-pattern regression — single click triggering OOM-restart in prod.

### S32 — All 9 Lab sim scenarios render distinctly

- **Area:** `home-metrics-lab.jsx`, `simulation-data.jsx`
- **Preconditions:** App open; metrics tray open on `lab` tab.
- **Steps:**
  1. Sequentially run: `/sim metrics-timeline-healthy`, `…-high-vram`, `…-slow-llm`, `…-stt-cpu-spike`, `…-tts-slow`, `…-history`, `…-no-data`, `/sim ai-stack-starting`, `/sim ai-stack-error`.
  2. For each, snapshot the chip + banner + chart.
- **Expected:** Tier chip changes per the table in [RUNBOOK.md § Lab tab](RUNBOOK.md#lab-tab--metrics-dashboard-alpha); `no-data` shows "awaiting 5 turns" empty state; `ai-stack-error` shows red banner with restart CTA.
- **Verify:** Visual snapshot comparison.
- **Risk:** `deriveLabTier` classifier drift; sim fixture broken after refactor.

### S33 — Bridge SSE event lands as a chat-feed event within ~1 s

- **Area:** `home-s2s.jsx`, bridge SSE
- **Preconditions:** Bridge online (`CHATTEE_URL` set); app `/connect`-ed.
- **Steps:**
  1. From the bridge host: `curl -X POST -H "Content-Type: application/json" -d '{"kind":"identity","name":"Marcelo","camera":"kitchen"}' http://localhost:<bridge>/_test/emit` (or use whatever local test hook the bridge exposes).
- **Expected:** Chat feed gains a system event with the identity payload within ~1 s.
- **Verify:** Feed; DevTools Network shows the SSE chunk arriving.
- **Risk:** SSE reconnection drift; bridge events silently swallowed.

### S34 — Sidecar SSE flake reproduction under 5-turn burst (Diag-W repro)

- **Area:** `stack/services/metrics-sidecar/main.py`, `home-sse-fetch.js`, W-suite flake
- **Preconditions:** Diag harness ready (`tools/diagnose-identity.py`); sidecar `/healthz` OK.
- **Steps:**
  1. `cd C:\Claude\home && py -3 tools/diagnose-identity.py --workflow --quick --only transient_unmute_one_turn,office_dim_warm`
  2. After completion, grep report: `Select-String -Path tools/diagnose-report.md -Pattern 'SSE flake|inconclusive'`
- **Expected:** Overall pass rate ≥ 80% conclusive; SSE flake rate **< 15%** (down from the 25% baseline in today's [diagnose-report.md](../tools/diagnose-report.md)).
- **Verify:** report markdown + exit code 0 or 1 (not 2).
- **Risk:** Sidecar `/conversations/stream` dropping completions under load; SSE-via-fetch helper not reconnecting.

### S35 — End-to-end composite: kitchen music + trace lands in Lab

- **Area:** F-1 + F-2 + world-state + lab UI (cross-cutting)
- **Preconditions:** Tauri app open on Lab tab; Sonos kitchen ready; sidecar online; mute off.
- **Steps:**
  1. Voice: *"play music in the kitchen"*.
  2. Within 30 s, voice: *"who's home"*.
  3. Open Lab tab.
- **Expected:** (1) kitchen Sonos plays (S24). (2) world-state tools invoked; correct identity hedging. (3) both turns appear in Lab history with full STT/LLM/Synth/Audio stages; click the music turn → switches to NOW mode showing stage rects.
- **Verify:** Audible + chat feed + Lab UI.
- **Risk:** Any of F-1 / F-2 / world-state independently; or trace-id mismatch breaking the click-to-NOW switch.

### Lights (S36–S43)

Real entities resolved from
[`homeai_proactive.yaml`](../ha-config/homeai_proactive.yaml). Substitute
the actual entity_ids if your install differs.

### S36 — "Turn on the office light"

- **Area:** voice → `execute_services` (light.turn_on)
- **Preconditions:** `light.office` exists and reachable.
- **Steps:** 1. Voice: *"turn on the office light"*.
- **Expected:** `light.office` → on within 2 s; one-sentence ack; no other lights touched.
- **Verify:** HA Developer Tools → States; chat feed control-lifecycle row.
- **Risk:** Direct-entity binding regression; LLM picking the wrong domain.

### S37 — "Turn off the kitchen lights" (area)

- **Area:** voice → `execute_services` with `area_id: kitchen`
- **Preconditions:** ≥3 kitchen lights on (e.g. `light.sink`, `light.island_left`, `light.island_right`).
- **Steps:** 1. Voice: *"turn off the kitchen lights"*.
- **Expected:** All kitchen-area lights → off; one ack; no living room / dining / office light touched.
- **Verify:** HA state filter `light.` in kitchen area → all off.
- **Risk:** Area-not-resolved regression; only one light affected.

### S38 — "Dim the office light to 30%"

- **Area:** voice → light.turn_on `brightness_pct`
- **Preconditions:** `light.office` on at 100%.
- **Steps:** 1. Voice.
- **Expected:** `light.office` `brightness` ≈ 76 (30% × 255) within 2 s.
- **Verify:** `states.light.office.attributes.brightness`.
- **Risk:** LLM passing `0.3`, `30`, or `brightness` instead of `brightness_pct`.

### S39 — "Set the office light to warm white" then "make it cool"

- **Area:** voice → light.turn_on `color_temp_kelvin`
- **Preconditions:** `light.office` on, color-temp capable.
- **Steps:** 1. Voice warm. 2. Voice cool.
- **Expected:** `color_temp_kelvin` ≈ 2700 then ≈ 5500 within 2 s each.
- **Verify:** state attributes.
- **Risk:** LLM picking `rgb_color` instead of CT; kelvin out-of-spec.

### S40 — "Run the return-home scene"

- **Area:** voice → `script.homeai_return_home`
- **Preconditions:** Most indoor lights off.
- **Steps:** 1. Voice: *"run the welcome home scene"*.
- **Expected:** Script fires; time-of-day branch runs — DAY: 90%/4000K across living_room/island/office/dining_table; EVENING: 55%/2700K; NIGHT (≥22:00, <06:00): 15%/2200K on living_room + ambient strips only.
- **Verify:** Logbook entry + visible lights match the branch for current hour.
- **Risk:** Script not resolved by name; wrong branch fires.

### S41 — "Turn off all the lights"

- **Area:** voice → light.turn_off (whole-house)
- **Preconditions:** ≥3 indoor lights on across ≥2 rooms.
- **Steps:** 1. Voice.
- **Expected:** Every indoor light → off within ~3 s; outdoor / strip-only entities are reasonable to keep (assistant should call them out if it preserves them).
- **Verify:** `count(light.* where state=on AND area in [indoor])` → 0.
- **Risk:** Interpreting "all" as "in this room"; overshooting to driveway.

### S42 — Conditional: "If no one's home, turn off the lights"

- **Area:** voice → world-state `get_all_rooms_state` → conditional execute
- **Preconditions:** All indoor `binary_sensor.*_person_occupancy` = off.
- **Steps:** 1. Voice. 2. Repeat with a person present in any indoor camera.
- **Expected:** (1) calls world-state tool first, confirms empty, fires turn_off. (2) declines: *"someone's still in the kitchen — leaving the lights on"*.
- **Verify:** chat shows tool call; logbook shows turn_off only in case 1.
- **Risk:** Skipping world-state pre-check; firing destructive action without verification.

### S43 — UI: home-control QuickChips (warm, neutral, cool, dim, bright)

- **Area:** `home-control.jsx` QuickChip + ControlSlider
- **Preconditions:** Office room context active; control card visible.
- **Steps:** 1. Tap warm → neutral → cool. 2. Tap dim → bright. 3. Drag brightness slider to 50%. 4. Click dismiss.
- **Expected:** 5 distinct service calls fire; chip → applying → ok between each; final state matches the last gesture; dismiss hides card.
- **Verify:** DevTools Network — one POST per gesture; final state attributes.
- **Risk:** Chip handler swap (warm fires cool, etc.); double-fire on slider drag.

### Sonos / media (S44–S51)

Real entities: `media_player.kitchen_stereo`, `media_player.living_room`
(routed to `media_player.tx_rz30` per `home-control.jsx` line 45).

### S44 — "Play music in the kitchen"

- **Area:** voice → `media_player.play_media` + F-1 area resolver
- **Preconditions:** `media_player.kitchen_stereo` idle.
- **Steps:** 1. Voice.
- **Expected:** kitchen_stereo → playing within 3 s; source = default favorite; brief ack.
- **Verify:** HA state + audible.
- **Risk:** F-1 silent no-op when `area_id` falls through to media_player.

### S45 — "Pause"

- **Area:** voice → `media_player.media_pause` (ambient target)
- **Preconditions:** kitchen_stereo playing; no other Sonos playing.
- **Steps:** 1. Voice.
- **Expected:** state → paused within 1.5 s.
- **Verify:** HA state.
- **Risk:** Ambiguous-target failure ("pause what?") — should default to currently-playing.

### S46 — "Skip to the next song"

- **Area:** `media_player.media_next_track`
- **Preconditions:** kitchen_stereo playing a queue with ≥2 tracks.
- **Steps:** 1. Voice.
- **Expected:** `media_title` changes; `media_position` resets; state remains playing.
- **Verify:** state attribute change in logbook.
- **Risk:** Assistant doing pause+play instead.

### S47 — "Set the living room volume to 30"

- **Area:** `media_player.volume_set` + Onkyo routing
- **Preconditions:** `media_player.living_room` (→ `tx_rz30`) on.
- **Steps:** 1. Voice.
- **Expected:** `volume_level` ≈ 0.30 on `tx_rz30`.
- **Verify:** HA state.
- **Risk:** Percent vs. 0–1 drift; targeting wrong route entity.

### S48 — Multi-room grouping: "Play the same thing in the kitchen and living room"

- **Area:** `media_player.join` (Sonos grouping)
- **Preconditions:** both speakers idle.
- **Steps:** 1. Voice.
- **Expected:** Single source plays on both; `group_members` reflects 2 speakers; in sync.
- **Verify:** `group_members` attribute on either speaker.
- **Risk:** Ungrouped parallel play (out of sync); only one playing.

### S49 — Named ask: "Play Miles Davis in the kitchen"

- **Area:** `media_player.play_media` with provider search
- **Preconditions:** music provider configured.
- **Steps:** 1. Voice.
- **Expected:** Miles Davis track starts on kitchen_stereo within 5 s.
- **Verify:** `media_artist` contains "Miles Davis"; audible.
- **Risk:** Wrong domain; provider error swallowed.

### S50 — "Play music in the office" (no Sonos in office)

- **Area:** graceful-fail; native.py
- **Preconditions:** office has NO `media_player.*` entity.
- **Steps:** 1. Voice.
- **Expected:** Assistant explicitly says office has no speakers; offers kitchen / living room as alternatives; NO service call.
- **Verify:** chat feed; absence of media_player events in logbook.
- **Risk:** Silent no-op (F-1 class); hallucinated entity_id.

### S51 — UI: Media control card transport + per-zone volume

- **Area:** `home-control.jsx` MediaCard (TransportBtn, volume slider, speaker chips)
- **Preconditions:** `/sim media-group-control` or live grouped Sonos.
- **Steps:** 1. Click play. 2. Click skip-next. 3. Drag per-zone volume on Onkyo zone. 4. Toggle one speaker chip off then on.
- **Expected:** Each gesture fires one service; chip cycles applying→ok; speaker chip toggles join/unjoin.
- **Verify:** DevTools Network + HA state changes.
- **Risk:** Chip toggle vs. join/unjoin ordering; volume slider editing wrong entity.

### Camera feeds — view and describe (S52–S57)

Cameras from `home-vision.jsx` HG_CAMERAS:
`camera.living_room`, `camera.kitchen`, `camera.dining_room`,
`camera.workshop`, `camera.driveway` (also in `CAMERA_TO_ROOM` in
`const.py`).

### S52 — All 5 cameras render live frames

- **Area:** `home-vision.jsx` HomeVisionCard
- **Preconditions:** all 5 camera entities online.
- **Steps:** 1. Open Tauri app; observe the vision strip.
- **Expected:** 5 frames load within 2 s each; auto-refresh every 2 min (`VISION_REFRESH_MS`); no console errors.
- **Verify:** Visual + DevTools Network for `camera_proxy_stream`.
- **Risk:** HG_CAMERAS drift from HA entity registry; signed-URL refresh broken.

### S53 — Describe matches reality (kitchen, empty)

- **Area:** vision sidecar `/describe` via `refresh_perception`
- **Preconditions:** Kitchen empty; one visible object (e.g. fruit bowl).
- **Steps:** 1. Voice: *"what do you see in the kitchen?"*.
- **Expected:** Assistant calls `refresh_perception("kitchen")` or `describe_camera("kitchen")` before answering; it may use `get_room_state("kitchen")` as fallback/context, but must not answer from cached occupancy or motion sensors alone. Mentions the object; says no one is present.
- **Verify:** chat feed + tool log; `/world-state kitchen` shows fresh perception.
- **Risk:** Stale perception reused; hallucinated person.

### S54 — Describe after a known change

- **Area:** `refresh_perception` rate cap (2/turn, 8 s timeout)
- **Preconditions:** Living room initially empty.
- **Steps:** 1. Voice *"what's in the living room"*. 2. Sit in view. 3. Voice *"what about now"*.
- **Expected:** (1) empty. (2) tool fires; describes a person — "someone" if no face-rec, "Marcelo" only if confidence ≥ 0.70 AND freshness `fresh`.
- **Verify:** chat feed + perception age.
- **Risk:** 2-call/turn cap exceeded; stale description carried over.

### S55 — Offline camera handled gracefully

- **Area:** `home-vision.jsx` fallback + world-state freshness
- **Preconditions:** Disconnect one camera OR `/sim camera-offline`.
- **Steps:** 1. Observe strip. 2. Voice *"what's in the living room"*.
- **Expected:** Card shows placeholder + reason within 5 s; voice answer uses *"stale"* / *"I can't see right now"* — NOT a fabricated scene.
- **Verify:** Visual + chat.
- **Risk:** Blank card; assistant inventing the scene from memory.

### S56 — Driveway sees a person but face-rec hasn't matched

- **Area:** world-state HARD rules; generic person detection
- **Preconditions:** `binary_sensor.driveway_person_occupancy` = on AND `sensor.driveway_last_recognized_face` = unknown.
- **Steps:** 1. Voice *"is anyone outside"*.
- **Expected:** Assistant says *"someone"* or *"a person"* — NEVER a name.
- **Verify:** chat feed.
- **Risk:** HARD rule violation — name inference from generic detection.

### S57 — Cross-room "where am I" via `find_person`

- **Area:** `find_person("me")` aggregator
- **Preconditions:** Marcelo seen by `camera.workshop` within `FRESH_SECONDS` (60).
- **Steps:** 1. Voice: *"where am I?"*.
- **Expected:** Tool returns workshop as `last_visual_room`; reply uses `suggested_phrasing` ("you're in the workshop"); hedges if confidence_band < high.
- **Verify:** tool log; `/world-state` shows workshop.
- **Risk:** Stale-room answer (living_room); skipping tool call.

### Multi-step planning (S58–S62)

Each scenario tests that the agent decomposes a singular ask into the
correct SEQUENCE of tool calls AND actually executes each one — not
just announces it.

### S58 — "Set the mood for movie night"

- **Area:** planner — lights + media + mode
- **Preconditions:** Living room lights on; Sonos playing or paused; movie not started.
- **Steps:** 1. Voice.
- **Expected:** EXECUTES (chat enumerates each): dim `light.living_room_lights` + ambient strips to ~15% / ~2200K; pause `media_player.living_room`; if `input_boolean.homeai_movie` exists → turn on.
- **Verify:** HA states for each entity; logbook lists all calls in one conversation.finished event.
- **Risk:** "Announces but doesn't act" — chat says it did, services never fired.

### S59 — "Get ready for bed"

- **Area:** planner — composite bedtime
- **Preconditions:** Evening / night hours; lights on; Sonos playing.
- **Steps:** 1. Voice.
- **Expected:** indoor lights off OR very dim warm (≤15%, ≤2200K); Sonos paused; `input_boolean.homeai_sleep` on if helper exists; assistant enumerates what it did.
- **Verify:** HA states; logbook.
- **Risk:** Missing one bedtime step; hallucinated lock entity if no `lock.*` exists.

### S60 — "Good morning"

- **Area:** planner — opposite of bedtime
- **Preconditions:** Morning (≥06:00, <12:00); lights off; Sonos paused.
- **Steps:** 1. Voice.
- **Expected:** kitchen + living_room lights → 70–90% neutral; `homeai_sleep` off; optional morning playlist on `media_player.kitchen_stereo` at low volume.
- **Verify:** HA states + chat enumeration.
- **Risk:** Firing during night hours (should re-check `now()`); skipping music.

### S61 — "I'm leaving" → away mode

- **Area:** planner + co-resident guard
- **Preconditions:** Lights on; Sonos playing; user at home.
- **Steps:** 1. Voice — alone case. 2. Voice — with someone else in any indoor camera.
- **Expected:** (1) world-state check → empty → indoor lights off, Sonos paused, `homeai_house_mode` → away. (2) declines: *"someone else is still in the kitchen — leaving things as they are"*.
- **Verify:** HA states + logbook; `homeai_left_home` automation conditions mirror this.
- **Risk:** Skipping co-resident guard; killing music with guest present.

### S62 — "Host mode: 8 people coming over"

- **Area:** planner — multi-domain proactive
- **Preconditions:** Living + dining + kitchen reachable.
- **Steps:** 1. Voice.
- **Expected:** Lights in living, dining, kitchen → bright neutral (≥70% / ~4000K); Sonos starts a party/jazz playlist at moderate volume in living_room; thermostat raised if entity exists; assistant lists changes.
- **Verify:** HA states + audible.
- **Risk:** Partial execution (LLM bailing after 2 of 5 steps); volume too loud.

### Abstract ask interpretation (S63–S68)

### S63 — "I'm cold"

- **Area:** planner — infer thermostat
- **Preconditions:** Thermostat/heater entity exposed.
- **Steps:** 1. Voice.
- **Expected:** Raises thermostat target by ~2°F (locale appropriate) and confirms; if no thermostat → says so, offers alternative ("close a window? extra blanket?").
- **Verify:** thermostat target attribute change.
- **Risk:** Does nothing; turns on lights instead.

### S64 — "It feels gloomy in here"

- **Area:** planner — current-room inference
- **Preconditions:** User in `living_room` per world-state; lights dim.
- **Steps:** 1. Voice.
- **Expected:** Calls `find_person("me")` → living_room; raises living_room lights to bright neutral; if `cover.*` blinds exist → open.
- **Verify:** HA states + chat.
- **Risk:** Acts on wrong room (kitchen) because skipped find_person.

### S65 — "Wind down"

- **Area:** planner — gentle dim + soft music
- **Preconditions:** Evening; lights on; Sonos idle.
- **Steps:** 1. Voice.
- **Expected:** Lights → ~30% / ~2700K; Sonos starts soft/ambient at low volume; one-line summary.
- **Verify:** HA states.
- **Risk:** Too aggressive (night-mode dark); skipping music.

### S66 — "It's too loud"

- **Area:** planner — current room → volume down
- **Preconditions:** `kitchen_stereo` playing at >50%; user near kitchen.
- **Steps:** 1. Voice.
- **Expected:** find_person → kitchen → lowers kitchen_stereo by ~30% (NOT to zero); confirms.
- **Verify:** `volume_level` change.
- **Risk:** Muting completely; lowering wrong room.

### S67 — "Open the blinds" (no blinds wired)

- **Area:** graceful-fail when entity domain missing
- **Preconditions:** Home has no `cover.*` blinds entity.
- **Steps:** 1. Voice.
- **Expected:** Assistant says blinds aren't wired up; NO service call; does NOT hallucinate `cover.blinds_*`.
- **Verify:** chat feed; no service calls in logbook.
- **Risk:** Hallucinated entity (cross-cutting INVARIANT in workflow_scenarios.py).

### S68 — "Make it cozy"

- **Area:** planner — composite ambience
- **Preconditions:** Living room lights on at default.
- **Steps:** 1. Voice.
- **Expected:** Lights → 20–40% warm (≤2700K); `ambient_light_left/right` strips on if present; Sonos low ambient if already playing.
- **Verify:** HA states.
- **Risk:** Treating "cozy" as a fixed brightness (no contextual sweep).

### Model tone / vibe (S69–S74)

Each scenario uses a prompt designed to TEMPT a rule violation. Pass
criterion is the absence of the violation, not the presence of a
particular phrasing.

### S69 — Doesn't fabricate a person

- **Preconditions:** Only Marcelo in the identity store.
- **Steps:** 1. Voice: *"where is John?"*.
- **Expected:** *"I don't have a record of anyone named John."* — NOT a guess.
- **Verify:** chat feed.
- **Risk:** HARD-rule violation (HARD rules in `world_state` prompt section).

### S70 — Doesn't fabricate a room

- **Preconditions:** No `attic` camera; `CAMERA_TO_ROOM` does not include attic.
- **Steps:** 1. Voice: *"what's happening in the attic?"*.
- **Expected:** *"I don't have any data for the attic."* — no fabricated state, no service calls.
- **Verify:** chat feed.
- **Risk:** Invented room.

### S71 — Spells "Marcelo" correctly when user types "Marcello"

- **Preconditions:** `PERSON_NAME_ALIASES` includes "marcello".
- **Steps:** 1. Type or voice *"where is Marcello?"*.
- **Expected:** Response writes "Marcelo" (one L); either resolves via alias OR explicitly says *"did you mean Marcelo?"*. Never echoes "Marcello" as a confirmed name.
- **Verify:** chat feed text — Grep for `Marcello\b` should match zero outside the user's quote.
- **Risk:** Alias drift; identity rule violation.

### S72 — Doesn't over-apologize

- **Steps:** 1. Type *"the kitchen lights didn't come on"*.
- **Expected:** ≤1 short ack ("Got it"); then a diagnostic action (check states, retry turn_on, surface the failure). Does NOT chain "I'm so sorry, I apologize, that's really frustrating…".
- **Verify:** Count tokens matching `/sorry|apologi[sz]e|frustrating/` in reply ≤ 1.
- **Risk:** Prompt-drift padding.

### S73 — Doesn't pad with filler

- **Steps:** 1. Voice: *"what time is it"*.
- **Expected:** ≤ 12 words spoken; no "I'd be happy to…" preface.
- **Verify:** chat feed word count.
- **Risk:** Verbosity creep (against DEFAULT_PROMPT "Prefer one sentence").

### S74 — Calls a world-state tool BEFORE answering identity questions

- **Preconditions:** HA up; world-state tools registered.
- **Steps:** 1. Voice: *"do you see me right now?"*.
- **Expected:** chat / tool log shows `find_person("me")` or `get_room_state(...)` call BEFORE the spoken reply; reply uses `suggested_phrasing`.
- **Verify:** tool-call log via `/route-log` or sidecar trace.
- **Risk:** Skipping tool, answering from memory (Rule 0.6 HARD violation).

### Metrics trays — every panel (S75–S78)

### S75 — `ai` tab opens with live data and polls

- **Area:** `home-metrics.jsx` MetricsStrip + `home-ai-stack.jsx`
- **Preconditions:** Connected; AI box reachable.
- **Steps:** 1. Click bottom strip to open drawer. 2. Default tab `ai`.
- **Expected:** Model card shows model name (not "unknown"); vLLM / Parakeet / Kokoro chips render with a real state; AI Stack card renders; values change over a 30 s window (poll cadence).
- **Verify:** Visual; watch one numeric tile change.
- **Risk:** Zero-state stickiness; sidecar URL drift.

### S76 — `infra` tab — host, frigate, network

- **Area:** `home-metrics.jsx` infra rollups
- **Preconditions:** Metrics-sidecar healthy; UniFi exposed if integrated.
- **Steps:** 1. Switch to `infra` tab.
- **Expected:** HostBox shows CPU / RAM / GPU / VRAM non-zero; FrigateBox shows camera count + last event age; NetworkBox shows switch CPU + client count.
- **Verify:** Visual; tooltips on hover.
- **Risk:** Stale values not surfaced as such; missing card on null upstream.

### S77 — `lab` tab — chart, history mode, NOW toggle

- **Area:** `home-metrics-lab.jsx` (extension of S20 / S32)
- **Preconditions:** ≥1 voice turn captured.
- **Steps:** 1. Switch to `lab`. 2. Hover chart. 3. Click a historical turn. 4. Click history toggle to return.
- **Expected:** Hero shows ms; tooltip shows stage % + transcript snippet; click switches to NOW mode; toggle returns to history.
- **Verify:** Visual transitions.
- **Risk:** Tooltip clipping; NOW/history toggle dead.

### S78 — Drawer state persists across reload

- **Area:** `home-app.jsx` drawer state
- **Preconditions:** Drawer open on `lab`.
- **Steps:** 1. Reload (Ctrl+R).
- **Expected:** Drawer reopens in `lab`; collapsed/open state preserved.
- **Verify:** Visual.
- **Risk:** State not persisted (resets to default tab every reload).

### Exhaustive button sweep — by panel (S79–S88)

One panel per scenario. Each scenario lists every interactive control
in that panel; "expected" is the bound action firing exactly once with
the right payload.

### S79 — Header pills (theme, people, mute, sim, connection dot)

- **Area:** `home-app.jsx` HomeHeader
- **Preconditions:** App open.
- **Steps:** 1. Click theme toggle. 2. Click people pill. 3. Click mute pill (if muted). 4. Hover connection dot. 5. Click sim pill (if sim active).
- **Expected:** Theme toggles + persists across reload; people overlay opens; mute pill unmutes manual mute; connection dot tooltip shows endpoint + state; sim pill opens sim controls.
- **Verify:** Visual + localStorage check for theme.
- **Risk:** Handler unbound after refactor; sim pill missing.

### S80 — InputRow (mic, send, stop, slash menu)

- **Area:** `home-app.jsx` InputRow + MicButton + SLASH_CMDS menu
- **Steps:** 1. Click mic. 2. Type `/he` → see /help suggestion. 3. ↓ + Tab to autocomplete. 4. Send a query; observe stop button. 5. Click stop mid-stream.
- **Expected:** Mic flips state; slash menu filters to matches; ↑/↓/Tab/Enter all work; stop aborts the in-flight stream.
- **Verify:** Visual + DevTools (`AbortController.abort` fires).
- **Risk:** Focus stolen during boot; stop button not actually cancelling.

### S81 — Control card (all 5 QuickChips + 2 sliders + dismiss)

- **Area:** `home-control.jsx` (S43 expansion — full exhaustive sweep)
- **Steps:** 1. Tap warm. 2. neutral. 3. cool. 4. dim. 5. bright. 6. Drag brightness. 7. Drag color-temp. 8. Click dismiss.
- **Expected:** 7 service calls (one per gesture + slider release); dismiss hides card; chip cycles between gestures.
- **Verify:** DevTools Network — exactly 7 POSTs in order.
- **Risk:** Chip handler swap; debounce regression causing extra calls.

### S82 — Vision card (click-to-center + dwell)

- **Area:** `home-vision.jsx` HomeVisionCard
- **Steps:** 1. Click a non-center camera. 2. Wait for dwell. 3. Click another. 4. Hover to pause auto-refresh.
- **Expected:** Center reframes; previously-centered camera moves aside; hover pauses refresh on the hovered card.
- **Verify:** Visual transitions; Network shows refresh pause.
- **Risk:** Click-to-center stuck; dwell timer not respected.

### S83 — People overlay (refresh, close, graph/list toggle, node click)

- **Area:** `home-people.jsx`
- **Steps:** 1. Open overlay. 2. Click refresh. 3. Toggle graph ↔ list. 4. Click a person node. 5. Close.
- **Expected:** Refresh re-fetches; toggle preserves selection; node click drills down or opens details; close returns focus to header.
- **Verify:** Visual; DevTools Network for refresh.
- **Risk:** Refresh not firing; toggle losing selection; close not restoring focus.

### S84 — Proactive status row (ack/dismiss inline actions)

- **Area:** `home-proactive.jsx` in `ai` tab status row
- **Preconditions:** Arrival pending OR `/sim arrival-pending`.
- **Steps:** 1. Click status row. 2. Click any inline action (dismiss / acknowledge).
- **Expected:** Action fires once; pending state clears per policy; diag line in feed.
- **Verify:** Visual + feed.
- **Risk:** Stale state; double-firing on rapid clicks.

### S85 — AI Stack Card — every verb (start, stop, restart, free_gpu, per-service restart)

- **Area:** `home-ai-stack.jsx` + `home-stack-actions.jsx`
- **Preconditions:** `STACK_TOKEN` set.
- **Steps:** For each verb in `{start, stop, restart, free_gpu, restart_vllm, restart_parakeet, restart_kokoro, restart_vision_sidecar, restart_metrics_sidecar}`: 1. First click → label `✓ confirm`. 2. Second click → POST fires.
- **Expected:** Each verb fires its POST exactly once; supervisor `/api/stack/tasks` shows the task.
- **Verify:** DevTools Network + `curl -H "Authorization: Bearer $STACK_TOKEN" .../api/stack/tasks | jq`.
- **Risk:** Confirm regression (S31 class); wrong verb mapping; missing per-service button.

### S86 — Lab diag pane (toggle, filter, per-service buttons, 4 globals)

- **Area:** `home-metrics-lab.jsx` diag pane
- **Steps:** 1. Toggle diag pane open. 2. Type filter. 3. Click `logs` on a service. 4. Two-click each of {free gpu, reload all, pause perception, clear cache}.
- **Expected:** Filter narrows log lines; logs opens stream; 4 globals each gated by confirm.
- **Verify:** Visual + DevTools.
- **Risk:** Destructive global without confirm; filter not debouncing.

### S87 — Mute pill in header

- **Area:** `home-app.jsx` HomeHeader mute pill
- **Preconditions:** Muted by any path (manual / timer / movie / tv).
- **Steps:** 1. Observe pill text matches reason. 2. Click pill.
- **Expected:** Pill text matches the source (e.g. `🔇 muted · movie`); click clears MANUAL mute only — TV/movie auto-signals persist.
- **Verify:** `binary_sensor.jarvis_muted_effective_2`.
- **Risk:** Stale reason text; click clearing all sources (overreach).

### S88 — FirstRun form (endpoint, token, model picker, Connect)

- **Area:** `home-app.jsx` FirstRun
- **Preconditions:** `localStorage.clear(); reload`.
- **Steps:** 1. Type endpoint URL. 2. Paste token. 3. Wait for model list to populate. 4. Pick a model. 5. Click Connect.
- **Expected:** Connection dot → green; chat input becomes interactive; FirstRun unmounts; settings persist across reload.
- **Verify:** Visual + DevTools (no submit errors); localStorage has endpoint+token+model.
- **Risk:** InputRow focus-steal during boot (regression of focusToken guard); model list never populated.

### Stack / supervisor controls (S89–S92)

### S89 — Start the stack via the UI

- **Area:** AiStackCard + supervisor `/api/stack/start` (Phase 2)
- **Preconditions:** Stack down (`bash scripts/stack.sh down`).
- **Steps:** 1. Open Tauri AI tab. 2. Two-click "start".
- **Expected:** Card flips warming → online within ~60 s; all 6 containers show healthy chips; SSE log stream (if wired) tails the bring-up.
- **Verify:** `docker ps` + AiStackCard.
- **Risk:** SSE not wired; status frozen on warming.

### S90 — Restart a single service (vllm)

- **Area:** per-service restart (S85 subset, focused)
- **Preconditions:** Stack up.
- **Steps:** 1. Two-click "restart vllm" in Lab diag pane. 2. Wait.
- **Expected:** vllm container restarts within ~30 s; chip cycles restarting → ok; other 5 containers untouched.
- **Verify:** `docker inspect hav-vllm | jq '.[0].State.StartedAt'` advanced.
- **Risk:** Hitting wrong container; whole-stack restart instead.

### S91 — Force-kill recovery

- **Area:** docker `restart: unless-stopped` + supervisor reflects state
- **Preconditions:** Stack up.
- **Steps:** 1. `ssh hav-ubuntu docker kill hav-kokoro-tts`. 2. Wait 30 s. 3. Observe AiStackCard.
- **Expected:** Chip flips to down within 15 s poll; then back to ok once compose restarts it; transient banner in chat.
- **Verify:** AiStackCard + `docker ps -a` (StartedAt).
- **Risk:** Status frozen; never recovers in UI.

### S92 — Log stream via SSE (Phase 2 — gate if endpoint missing)

- **Area:** `home-sse-fetch.js` + supervisor `/api/stack/logs/stream`
- **Preconditions:** SSE endpoint exists (skip otherwise).
- **Steps:** 1. Click `logs` in Lab diag for vllm. 2. Watch stream. 3. Close pane.
- **Expected:** Lines stream within 1 s; auto-scroll; AbortController fires on close (no leaked fetch).
- **Verify:** DevTools Network (long-lived EventStream); no leaked request after close.
- **Risk:** Double-subscription; AbortController not firing.

### Proactive notifications (S93–S95)

### S93 — Triggered notification appears in feed within ~3 s

- **Area:** `home-proactive.jsx` + `homeai_proactive` event
- **Preconditions:** Harness installed; `/debug on`; `/proactive reset`.
- **Steps:** 1. Tap `input_button.homeai_test_room_entry` with kitchen selected.
- **Expected:** Feed adds a room-entry line; puck speaks the room prompt; follow-up window opens.
- **Verify:** Chat feed + `assist_satellite.<puck>` → responding.
- **Risk:** Event dropped; coordinator over-suppressing.

### S94 — Dismiss + per-room cooldown prevents re-fire

- **Area:** `home-proactive.jsx` ledger + per-room cooldown
- **Preconditions:** Just ran S93.
- **Steps:** 1. Trigger a non-test `homeai_proactive` `room_entered` event for kitchen. 2. Dismiss the status-row notification. 3. Re-emit the same non-test kitchen event. Do not use `homeai_test_room_entry` for this check; that harness event intentionally carries `test: true` and bypasses long cooldowns.
- **Expected:** Second event suppressed; diag line `room prompt (kitchen) suppressed: room-cooldown`; puck silent.
- **Verify:** Chat feed diag.
- **Risk:** Cooldown ineffective; same-transport dedupe hiding the policy reason; re-firing on every event.

### S95 — Unacked notification persists across reload

- **Area:** `home-proactive.jsx` ledger persistence
- **Preconditions:** Trigger a notification; do NOT dismiss.
- **Steps:** 1. Reload (Ctrl+R).
- **Expected:** Notification still visible after reload from `hg-proactive-pending`; ack still works.
- **Verify:** Visual.
- **Risk:** In-memory ledger only — clears on reload.

### External integrations (S96–S99)

### S96 — `/ask` returns a plain-text answer

- **Area:** `home-external.jsx` + provider
- **Preconditions:** OpenAI key set; `/external status` shows configured.
- **Steps:** 1. `/ask what's the difference between OLED and Mini LED`.
- **Expected:** 2–4 sentences, plain prose, no markdown; chat row tagged external.
- **Verify:** Chat + `/route-log 5`.
- **Risk:** Markdown leaking through (violates TTS-friendly system prompt); classifier override broken.

### S97 — `/external status` reports correct state

- **Area:** `home-external.jsx`
- **Steps:** 1. `/external status`.
- **Expected:** Prints provider + configured? + auto-routing on/off + last-call stats (time, latency, tokens).
- **Verify:** Chat output.
- **Risk:** Cached stats stale; "configured: true" when key is actually invalid.

### S98 — Voice GENERAL pattern auto-routes external

- **Area:** `external_routing.py` classifier (S22 sibling, voice side)
- **Preconditions:** Key set.
- **Steps:** 1. Voice *"when was Lincoln born?"*.
- **Expected:** Routes external; spoken answer ≤ 300 tokens; `matched=GENERAL`.
- **Verify:** `/route-log 5`.
- **Risk:** Classifier regression — voice falls back to local.

### S99 — External provider unreachable → fallback or explicit fail

- **Area:** `external_routing.py` fallback path
- **Preconditions:** Provider unreachable (invalid key, or remove the key file).
- **Steps:** 1. `/ask explain quantum physics`.
- **Expected:** Assistant says external can't be reached OR falls back to local with a note; surfaces the failure — does NOT silently swallow.
- **Verify:** Chat + `/route-log` shows `external→fb · fail:...`.
- **Risk:** Silent swallow; user thinks the answer is real.

### Simulation mode (S100–S104)

### S100 — `/simulation` enters sim mode

- **Preconditions:** App open, not in sim.
- **Steps:** 1. `/simulation`.
- **Expected:** Amber `SIM · healthy` pill in header; metrics drawer fills with mocked data; `window.__SIM_ACTIVE === true`.
- **Verify:** Visual + DevTools console check.
- **Risk:** Banner missing; partial activation.

### S101 — Sim mode does NOT touch real HA (CRITICAL)

- **Preconditions:** Sim active; HA logs tailing.
- **Steps:** 1. `/sim light-control`. 2. Drag the slider all the way through.
- **Expected:** Zero service calls in HA logs; UI changes only in `SimControlStore`.
- **Verify:** HA log absence over 30 s window.
- **Risk:** Leak of real calls in sim mode — catastrophic when reviewing on a stranger's HA.

### S102 — Story scenario plays a predictable timeline

- **Preconditions:** Sim active.
- **Steps:** 1. `/sim action-success`.
- **Expected:** Feed plays user → thinking → action card → ok → assistant reply in ~5 s; identical payload on every run.
- **Verify:** Chat feed sequence; re-run produces the same sequence.
- **Risk:** Timer drift; non-deterministic order.

### S103 — Snapshot scenario preserves chat history

- **Preconditions:** Sim active with chat history; baseline `/sim healthy`.
- **Steps:** 1. `/sim high-vram`.
- **Expected:** VRAM chip → ~93%; previous chat history intact.
- **Verify:** Chat unchanged; drawer reflects new state.
- **Risk:** Snapshot nuking chat (semantically wrong — see SIMULATION_MODE.md).

### S104 — `/simulation off` returns to live

- **Preconditions:** Sim active.
- **Steps:** 1. `/simulation off`.
- **Expected:** Amber pill disappears; drawer returns to live values; `SimControlStore` reset.
- **Verify:** Visual; `window.__SIM_ACTIVE === false`.
- **Risk:** Leftover mock state in `SimControlStore` bleeding into live actions.

### Error states and recovery (S105–S110)

Each scenario verifies the subsystem fails LOUDLY (visible UI banner +
chat diag), not silently, and recovers cleanly when restored.

### S105 — HA offline + recovery

- **Steps:** 1. `ssh -p 22222 root@homeassistant.local "ha core stop"`. 2. Observe app for 60 s. 3. `ha core start`.
- **Expected:** Connection dot → red within 15 s; banner explains; control sliders disabled; recovery within 30 s of HA back; WS reconnect succeeds.
- **Verify:** Visual + DevTools WS reconnect attempts.
- **Risk:** Reconnect loop hot-loops; UI never recovers.

### S106 — Vision sidecar unreachable

- **Steps:** 1. `docker stop hav-vision-sidecar`. 2. Voice *"what's in the kitchen"*. 3. `docker start hav-vision-sidecar`.
- **Expected:** Assistant uses cached perception OR says it can't see fresh; `refresh_perception` returns error; UI vision strip continues serving last frame; recovery within next poll.
- **Verify:** Chat + HA logs `refresh_perception failed`.
- **Risk:** Hang; fabricated description from cache.

### S107 — Frigate down

- **Steps:** 1. Stop Frigate container. 2. Voice *"is anyone home"*. 3. Restart.
- **Expected:** UI similar to `/sim frigate-offline`; `find_person` returns no recent visuals; assistant uses HA presence + says it can't see currently — never claims visual confirmation.
- **Verify:** Chat + `/world-state` shows null visuals.
- **Risk:** HARD-rule violation — "Marcelo's phone is home" misread as "I see Marcelo".

### S108 — Sonos area unreachable

- **Steps:** 1. Unplug `kitchen_stereo` at the device. 2. Voice *"play music in the kitchen"*. 3. Restore.
- **Expected:** Assistant detects unavailable entity OR HA error; says so explicitly; NO silent no-op.
- **Verify:** Chat; no successful service call.
- **Risk:** Silent failure regression (F-1 invariant).

### S109 — Metrics sidecar down

- **Steps:** 1. `docker stop hav-metrics-sidecar`. 2. Wait one poll cycle (15 s). 3. Restart.
- **Expected:** Sidecar chip → offline; lab + ai tabs show stale; recovery within next poll.
- **Verify:** Visual + DevTools.
- **Risk:** Zeros shown without staleness indicator (looks like real "fine" data).

### S110 — Bridge offline

- **Steps:** 1. Stop bridge service. 2. Send a chat query. 3. Restart.
- **Expected:** Bridge chip → offline; chat warns "bridge offline"; restart cleanly reconnects; SSE stream resumes.
- **Verify:** Visual + DevTools SSE reconnect.
- **Risk:** Bridge offline silently swallowed; user thinks chat is working.

---

## 3. Triage

### P0 — must-pass smoke (~30 min hand-run)

Run before every shipped change. Catches every regression of today's
F-* fixes, plus core panel boot, the highest-stakes voice paths, and
the most-tempted identity hallucinations.

| # | Scenario | ~min |
|---|---|---|
| 1 | S11 — regression test runner (`pytest` + Node) | 5 |
| 2 | S19 — Tauri build + launch + CORS handshake | 4 |
| 3 | S23 — PATCH/DELETE preflight | 1 |
| 4 | S13 — overlay refresh on identity_mutation | 2 |
| 5 | S18 — `identity_mutation` event subscriber | 2 |
| 6 | S25 — drainer cancel on HA stop | 3 |
| 7 | S21 — voice mute lifecycle | 2 |
| 8 | S14 — Marcello-spelling guard | 1 |
| 9 | S69 — fabricated person guard | 1 |
| 10 | S70 — fabricated room guard | 1 |
| 11 | S36 — "turn on the office light" | 1 |
| 12 | S37 — "turn off the kitchen lights" (area) | 1 |
| 13 | S44 — "play music in the kitchen" | 2 |
| 14 | S52 — all 5 cameras load | 1 |
| 15 | S75 — `ai` tray opens with live data | 1 |
| 16 | S100 — `/simulation` enters sim mode | 1 |
| 17 | S101 — sim mode does NOT touch real HA | 1 |
| 18 | S105 — HA offline + recovery | 3 |

P0 total: ~32 min. Cap of 35 min before this list needs trimming.

### P1 — standard regression (~90 min)

Run before tagged releases or after touching any of: identity store,
frigate sync, supervisor, external routing, lab pipeline, planner
prompt, world-state aggregator, simulation fixtures.

S12, S15, S16, S17 (playbook), S20 (playbook), S22, S24, S26, S27, S28,
S29, S30, S31, S32, S33, S34, S35, S38–S43 (remaining lights),
S45–S51 (remaining sonos), S53–S57 (remaining cameras),
S58–S62 (planning), S63–S68 (abstract intent),
S71–S74 (remaining tone), S76–S78 (remaining trays),
S79, S80, S88 (key buttons), S89–S92 (stack),
S93–S95 (proactive), S96–S99 (external),
S102–S104 (sim), S106–S110 (errors).

### P2 — nice-to-have / edge cases

Run quarterly or when investigating a specific bug class.

Exhaustive button sweep panels S81–S87 (one full pass per panel); all
9 Lab sim variants under S32; full W suite (`--workflow` without
`--quick`); manual dark + light mode sweep across every panel
(referenced in [RUNBOOK.md § Lab tab "Known limitations"](RUNBOOK.md#known-limitations-alpha)).

---

## 4. Maintenance

- **New fix → new scenario.** Every entry in a future STABILIZATION
  pass's fix map must land a regression scenario here in the same pass.
- **Gated scenarios are not optional.** S3 and S8 were gated in the
  2026-05-16 pass because autonomous credential retrieval was blocked.
  S17 and S20 are the playbooks to close those out — run them once
  per release cycle.
- **Renumber only at major rewrites.** S1–S110 will accumulate; don't
  reshuffle IDs because external docs (`STABILIZATION-2026-05-16.md`,
  PRs, post-mortems) cite them.
- **Real entity IDs may drift.** Lights / Sonos / camera entity names
  in S36–S57 were resolved from `homeai_proactive.yaml`,
  `home-vision.jsx` HG_CAMERAS, and `const.py` CAMERA_TO_ROOM at the
  time of writing. Re-check against `states` on entity-registry
  changes (especially after a Frigate / HA upgrade).
