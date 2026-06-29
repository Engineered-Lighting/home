# Simulation Mode

A fully self-contained design-review harness baked into the home app.
Lets anyone exercise every UI state — voice flow, action cards, error
states, resource pressure, network outage, mocked cameras — **without
any access to Home Assistant, the AI box, cameras, or any private
infrastructure**.

No `.env`. No Tailscale. No tokens. No local services.

---

## Why this exists

The shipped app is tightly coupled to one specific home stack:
Home Assistant, vLLM, Frigate, Chatterbox TTS, RTSP camera streams,
UniFi network. To get UI feedback from a remote collaborator, granting
network access into a home is intrusive and unnecessary. Simulation
Mode replaces every live data source with bundled synthetic fixtures
so the app can be reviewed end-to-end on any laptop with internet
access for the React CDN.

---

## Requirements

- **Node.js 18+** (any LTS works)
- **Internet access** at runtime — React + ReactDOM + Babel-standalone
  are loaded from `unpkg.com` CDN. Offline-first is on the roadmap.
- One free TCP port (default **5180**)

That's it. No Tauri install, no Rust toolchain, no `.env`, no secrets.

---

## Quick start (Windows or macOS)

```bash
# Once
npm install

# Each session
npm run dev
```

Open `http://localhost:5180/` in any modern browser. Then type:

```
/simulation
```

The header gains an amber `SIM · healthy` pill. The drawer fills with
mocked metrics. Type `/sim scenarios` to see the catalog.

### Deep links

Hand someone a URL pre-pinned to a scenario:

```
http://localhost:5180/?simulation=1&scenario=action-success
http://localhost:5180/?simulation=1&scenario=high-vram
http://localhost:5180/?simulation=1&scenario=slow-voice
```

URL params take precedence over localStorage. Sim state persists
across reloads via `localStorage` until you type `/simulation off`.

> **Tauri context note**: the URL-param activation path only works in
> the browser. Inside the shipped Tauri app, sim mode is off by default
> and is only toggled via the in-app slash command, never via
> localStorage (so the shipped app can't accidentally boot mocked).

---

## Slash commands

| Command | What it does |
|---|---|
| `/help` | Lists all commands. When sim is active, lists scenarios first. |
| `/simulation` | Enter sim mode (or show status if already active). |
| `/simulation off` | Exit sim mode. |
| `/simulation scenarios` | List available scenarios. |
| `/simulation reset` | Re-apply the `healthy` baseline. |
| `/simulation <scenario>` | Switch to a named scenario. |
| `/sim <scenario>` | Same as `/simulation <scenario>`. |
| `/sim off` | Exit sim mode. |
| `/sim reset` | Re-apply healthy baseline. |
| `/sim scenarios` | List scenarios. |

Unknown scenario names fall back to `healthy` with a chat warning.

---

## Scenario catalog

Each scenario falls into one of two classes:

- **Snapshot scenarios** (e.g. `high-vram`, `network-degraded`,
  `model-offline`) patch only metric/health state. Your chat history
  is preserved so you can compare the SAME conversation across
  different system states.
- **Story scenarios** (e.g. `action-success`, `movie-mode`,
  `slow-voice`) replace chat history and play a scripted timeline of
  user message → thinking → action card → result → assistant reply
  over a few seconds. Switching scenarios cancels any pending timers.

### Baseline

| ID | Description |
|---|---|
| `healthy` | Everything online, model warm, recent realistic chat history. |
| `empty` | Calm baseline — no recent activity, minimal history. |

### Voice state

| ID | Description |
|---|---|
| `listening` | Mic open, voice waveform active. |
| `thinking` | Transcript dispatched, model generating. |
| `speaking` | TTS streaming back. |

### Action flows (story timelines)

| ID | Description |
|---|---|
| `action-success` | user → thinking → action card → ok → assistant reply |
| `action-failed` | action card → error → assistant explains |
| `movie-mode` | start a movie, Apple TV plays, lights dim, confirmation |

### Inline control cards

Exercise `home-control.jsx` — the cards read/write the mock
`window.SimControlStore` (`simulation-controls.jsx`) instead of real HA.

| ID | Description |
|---|---|
| `light-control` | single light — brightness + color-temp sliders |
| `light-group-control` | multi-light group — averaged/mixed values; one light has no color temp |
| `media-control` | single Sonos — transport, volume, now-playing |
| `media-group-control` | grouped Sonos — per-zone volume (living room routed to the Onkyo) + speaker chips |
| `action-card-expired` | older control cards — read-only (one superseded, one expired) |
| `action-card-error` | failed action — disabled controls + the error reason |

### Proactive smart-home

Exercise `home-proactive.jsx` — the proactive-assistant coordinator. In
Simulation Mode the coordinator hook is **inert**; these scenarios drive
the `proactive` UI state directly and append the feed/diag lines it would
emit. The `proactive` status row lives in the metrics drawer's **ai** tab.

| ID | Description |
|---|---|
| `arrival-pending` | GPS says home — `arrival_pending_confirmation`, awaiting Frigate face-rec, no speech |
| `arrived-home` | full two-stage: pending → Frigate confirms → return-home scene + welcome spoken |
| `arrival-unconfirmed` | pending → confirmation window times out → soft fallback line, no spoken name |
| `left-home` | departure → away mode, lights-off line, status row flips to away |
| `welcome-home-followup` | confirm → welcome → mic opens → user reply → processed → idle |
| `room-entry-kitchen` | enter the kitchen → short room-aware prompt + follow-up window |
| `room-entry-suppressed` | room prompt suppressed by context (movie mode) — diag explains why |
| `room-entry-cooldown` | re-enter a room within the per-room cooldown → suppressed |
| `proactive-timeout` | welcome spoken → follow-up window opens → no reply → returns to idle |

### Performance

| ID | Description |
|---|---|
| `slow-voice` | Synth stage degraded; total turn ~3.5s |

### Resource / model

| ID | Description |
|---|---|
| `high-vram` | VRAM at 93/96 GB |
| `model-warming` | Container started, vLLM not ready |
| `model-offline` | vLLM/model runtime unavailable |
| `tts-offline` | Chatterbox unavailable; text still works |

### Integration

| ID | Description |
|---|---|
| `haos-offline` | HAOS unreachable |
| `frigate-offline` | Frigate down; cameras go stale |
| `camera-offline` | One camera (living room) offline, rest healthy |
| `media-unavailable` | Media integrations stale (Apple TV / Sonos) |

### Network

| ID | Description |
|---|---|
| `network-degraded` | Switch RAM high, clients dropping |
| `network-healthy` | Compact summary, no warnings |

### Full outage

| ID | Description |
|---|---|
| `bridge-offline` | Core backend unavailable |
| `everything-offline` | Every dependency down — UI still renders |

---

## How it works (one paragraph)

Every real-service `useEffect` in `app/src/home-app.jsx` has an early-
return: `if (sim.active) return undefined;`. A single synthesis effect
watches the active scenario and writes mocked values directly into the
same React state setters the live pollers would. Camera `<img>` tags
get a `data:image/svg+xml,…` URI instead of the signed MJPEG URL. The
HA WebSocket and voice WebSocket construction sites refuse to open
when sim is active. `tauriFetch` is guarded as a last-resort backstop.
No real network calls are made.

---

## What is mocked

- All metrics (TTFT, GPU, VRAM, CPU, RAM, tokens/sec, history arrays)
- Bridge `/healthz` (warmup, ha_connected, uptime, feature flags, stale media)
- HAOS host (CPU, RAM, disk, uptime)
- Frigate stats (per-camera FPS, Coral inference, total CPU, uptime)
- UniFi network (UDM, switches, clients)
- Vision sidecar phash hit rate
- Chat events (user / assistant / action / perception)
- Voice state machine (idle / listening / thinking / speaking)
- Action cards with status transitions
- Camera streams (inline SVG placeholders per room with bounding-box overlays)
- Identity / face recognition
- Media player state (Apple TV / Sonos)
- Last voice turn pipeline trace + p50/p90 percentiles

## What is NOT mocked

- Microphone audio playback — `listening` / `speaking` states animate
  the waveform with idle bars (no real audio capture or playback).
- HA's actual conversation pipeline — the chat in story scenarios is
  fully scripted, not generated.
- TTS audio output.

---

## Privacy promise

Mocked data uses generic placeholders only:

- Identity persona: `Alex` (never the maintainer's real name)
- Rooms: `living_room`, `kitchen`, `dining_room`, `workshop`,
  `driveway`, `office` — the same generic names used in the public HA
  prompt's example block
- Entity IDs: standard HA conventions (`light.living_room_lights`,
  `media_player.living_room_2`) — no private entities
- No real IPs, no Tailscale hostnames, no real camera URLs, no tokens

If you find a leak, file an issue.

---

## Adding a scenario

Edit `app/src/simulation-data.jsx`. Add an entry to the `scenarios`
object:

```js
"my-scenario": {
  id: "my-scenario",
  label: "short label",
  description: "what this exercises",
  preservesChatHistory: true,   // patch-only, keep chat
  baseline: "healthy",          // optional inheritance
  snapshot: {
    // Any of: metrics, bridgeHealth, frigateMetrics, hostMetrics,
    // networkMetrics, visionHealth, traceSummary, lastTrace, identity,
    // media, cameraLabels, cameraStates, connection, sidecarOnline,
    // bridgeOnline, voice, events
  },
  timeline: [                   // optional story sequence
    { at: 100,  delta: (apply) => apply({ __append: { kind: "user", text: "..." } }) },
    { at: 800,  delta: { voice: { state: "thinking" } } },
    // ...
  ],
}
```

Reload the page — your scenario shows up in `/sim scenarios`.

---

## Maintaining scenarios

When the canonical state shapes evolve (a new field added to
`bridgeHealth`, a new metric in `metrics`), the mock fixtures must be
synced. The shape sources live in:

| State key | Canonical source |
|---|---|
| `metrics` | `services/metrics-sidecar/main.py` `/metrics` response |
| `bridgeHealth` | `services/personaplex-bridge/main.py` `/healthz` response |
| `lastTrace`, `traceSummary` | `services/metrics-sidecar/main.py` `/traces/*` |
| `frigateMetrics`, `hostMetrics` | `app/src/home-app.jsx` HA WS subscription effects |
| `networkMetrics` | same as above (UniFi sensor subscription) |
| `events` | `app/src/home-events.jsx` renderers + reducer |
| Camera state list | `app/src/simulation-cameras.jsx` (mock-only) |

---

## Troubleshooting

### Port 5180 is already in use
Change the port in `package.json` scripts. `serve` accepts `-p <port>`.

### "Cannot read property of undefined" on boot
Make sure `npm install` finished cleanly. The `serve` package is the
only runtime dep.

### CDN-loaded React doesn't load (network panel shows unpkg.com errors)
You need internet for the CDN. Offline mode is on the roadmap.

### My deep link doesn't activate the scenario
Make sure you're using the browser path (`http://localhost:5180/...`),
not the Tauri shipped app. Tauri ignores URL params for sim mode.

### Sim mode wouldn't turn off
`/simulation off` exits cleanly. If that fails, open DevTools and
delete the `hg-simulation` key from `localStorage`, then reload.

### I want to ship a new scenario
Edit `app/src/simulation-data.jsx`. See "Adding a scenario" above.
No build step; reload the page.

---

## What this is NOT

- A real backend mock. The "model" never actually generates text.
  Story scenarios use hard-coded assistant lines.
- A way to develop against real HA without a token. The live app is
  Tauri; sim mode is for design review only.
- An offline mode. The React CDN is required.

Built natively into the app at `app/src/simulation*.jsx`. Loaded
unconditionally — sim mode is opt-in via the slash command, URL
param, or `npm run dev:simulation`.
