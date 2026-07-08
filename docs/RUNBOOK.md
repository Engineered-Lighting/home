# Runbook

Day-to-day operating procedures for the home stack. Read top-to-bottom
the first time; use the table of contents to jump to a specific recipe
afterward.

## Stack operation

### Bring up the stack
```bash
cd /opt/home-ai-voice
bash scripts/stack.sh up
```
Idempotent. Builds any missing images, brings all containers up, waits
for healthchecks, then runs the smoke-test panel. Typical first-run
takes 5–10 min (model weights cached); subsequent runs are seconds.

### Stop the stack
```bash
bash scripts/stack.sh down
```

### Restart
```bash
bash scripts/stack.sh restart
```

### Status + smoke tests on demand
```bash
bash scripts/stack.sh status
```
Prints `docker compose ps` plus the per-service health probes.

### Tail one service's logs
```bash
bash scripts/stack.sh logs vllm
bash scripts/stack.sh logs metrics-sidecar
```

### From the Windows operator workstation
A wrapper exists so you don't need to ssh:
```powershell
.\stack\scripts\stack.ps1            # default: up
.\stack\scripts\stack.ps1 up
.\stack\scripts\stack.ps1 down
.\stack\scripts\stack.ps1 status
.\stack\scripts\stack.ps1 logs vllm
```
It just SSHes into the AI box (via the `hav-ubuntu` alias in
`~/.ssh/config`) and runs `scripts/stack.sh`.

## What runs where

| Service | Container | Profile | Port | Purpose |
|---|---|---|---:|---|
| `vllm` | `hav-vllm` | `default` | 8000 | OpenAI-compatible LLM/VLM server, internal to the compose network |
| `wyoming-parakeet` | `hav-wyoming-parakeet` | `default` | 10300 | STT over Wyoming protocol |
| `kokoro-tts` | `hav-kokoro-tts` | `default` | 8880 | Fallback OpenAI-compatible TTS engine |
| `chatterbox-tts` | `hav-chatterbox-tts` | `default` | 8881 | Primary OpenAI-compatible TTS engine |
| `wyoming-kokoro` | `hav-wyoming-kokoro` | `default` | 10301 | Wyoming TTS bridge currently fronting Chatterbox |
| `vision-sidecar` | `hav-vision-sidecar` | `default` | 8091 | Camera-image description and visual reasoning |
| `metrics-sidecar` | `hav-metrics-sidecar` | `default` | 8000/8092 | LLM proxy, chat-tee SSE, and telemetry |
| `intelligence` | `hav-intelligence` | `default` | 8095 | Home Intelligence read-only memory/evidence API |
| `s2s-model` | `hav-s2s-model` | `s2s` | 8998 | Retired/experimental full-duplex speech-to-speech model |
| `personaplex-bridge` | `hav-personaplex-bridge` | `s2s` | 8094 | Retired/experimental Home app WebSocket to S2S/HA bridge |
| `stack-supervisor` | `_systemd, not docker_` | `host` | 8093 | HTTP control plane for the stack |

## Stack supervisor

A small Python HTTP service the Home app talks to for the **AI Stack Control**
card (start/stop/status, with live log streaming). Runs as a systemd unit on
the Ubuntu host — **not** in the docker-compose project, so it survives
`stack.sh down` (you couldn't start the stack back up from the UI otherwise).

Current API surface:

- Read-only: `GET /healthz`, `GET /api/stack/status`, `GET /api/stack/tasks`,
  `GET /api/stack/logs/stream`.
- Stack mutations: `POST /api/stack/start`, `POST /api/stack/restart`,
  `POST /api/stack/stop`, `POST /api/stack/free-gpu`, and compatibility
  `POST /api/stack/free_gpu`.
- Per-service actions: `GET /api/services/<svc>/logs?n=50`,
  `POST /api/services/<svc>/restart`, `POST /api/services/<svc>/stop`.

Every endpoint except `/healthz` requires bearer `STACK_TOKEN`. Destructive
routes require explicit `X-Confirm`: stack stop (`stop-ai-stack`), per-service
stop (`stop-<service-confirm>`), and free-GPU (`free-gpu`). Start/restart
routes are bearer-token gated and audited, but do not currently require
`X-Confirm`.

### Endpoint reference

| Method | Path | Status | Auth | X-Confirm | Notes |
|---|---|---:|---|---|---|
| GET | `/healthz` | `200` | `none` | `none` | Unauthenticated liveness probe for the Home app poller. |
| GET | `/api/stack/status` | `200` | `STACK_TOKEN` | `none` | Aggregate stack and service status. |
| GET | `/api/stack/tasks` | `200` | `STACK_TOKEN` | `none` | Recent stack-control task summaries. |
| POST | `/api/stack/start` | `202` | `STACK_TOKEN` | `none` | Start the default AI stack. |
| POST | `/api/stack/restart` | `202` | `STACK_TOKEN` | `none` | Restart the default AI stack. |
| GET | `/api/stack/logs/stream` | `200` | `STACK_TOKEN` | `none` | SSE stream for stack-control task logs. |
| POST | `/api/stack/stop` | `202` | `STACK_TOKEN` | `stop-ai-stack` | Stop the default AI stack. |
| GET | `/api/services/<svc>/logs` | `200` | `STACK_TOKEN` | `none` | Tail allowlisted service logs or supervisor audit logs. |
| POST | `/api/services/<svc>/restart` | `202` | `STACK_TOKEN` | `none` | Restart one allowlisted compose service. |
| POST | `/api/services/<svc>/stop` | `202` | `STACK_TOKEN` | `stop-<service-confirm>` | Stop one allowlisted compose service. |
| POST | `/api/stack/free-gpu` | `202` | `STACK_TOKEN` | `free-gpu` | Stop GPU-heavy services. |
| POST | `/api/stack/free_gpu` | `202` | `STACK_TOKEN` | `free-gpu` | Compatibility alias for `free-gpu`. |

### One-time install

```bash
# On the Ubuntu AI box, after first checkout of /opt/home-ai-voice:
sudo bash /opt/home-ai-voice/services/supervisor/install.sh
```

Idempotent — re-running just updates the venv + systemd unit. Pre-conditions
checked by the script:

- `STACK_TOKEN` set in `/opt/home-ai-voice/.env` (≥16 chars; generate with
  `openssl rand -hex 32`)
- `BIND_ADDR` set in the same `.env` (Tailscale or LAN IP — **never
  `0.0.0.0`**). The supervisor controls Docker (root-equivalent), so the
  bound interface IS the trust boundary.

If either is missing, the unit fails to start. Diagnose with
`journalctl -u hav-stack-supervisor -n 40`.

### Liveness / status

```bash
# Unauthenticated liveness — the Home app's 15-s poller uses this.
curl -s http://$BIND_ADDR:8093/healthz

# Aggregate status (auth required).
curl -s -H "Authorization: Bearer $STACK_TOKEN" \
  http://$BIND_ADDR:8093/api/stack/status | jq

# Recent tasks (mutations land in Phase 2 — list is empty until then).
curl -s -H "Authorization: Bearer $STACK_TOKEN" \
  http://$BIND_ADDR:8093/api/stack/tasks | jq
```

### STACK_TOKEN rotation

Three steps, in this order, so neither side desyncs:

1. Update `/opt/home-ai-voice/.env` on the Ubuntu box.
2. `sudo systemctl restart hav-stack-supervisor` — picks up the new token.
3. Update the workstation's token (see below).

### Workstation token storage

**Phase 4 hardening** (not MVP) migrates to `tauri-plugin-stronghold` /
platform keyring. Until then, three paths:

- **Browser/Tailscale web path:** the Ubuntu web gateway reads `STACK_TOKEN`
  server-side from `/opt/home-ai-voice/.env` (or `HOME_WEB_STACK_TOKEN_FILE`)
  and injects it only for `/proxy/supervisor/api/...` requests. The browser
  receives a non-secret gateway marker, not the token itself. If this path is
  enabled, `/stack-token` in the web app reports that no browser token is
  needed.
- **Dev path (Phase 1 testing):** open DevTools in the Home app and run
  `localStorage.setItem("hg-stack-token-DEV", "<your STACK_TOKEN>")`,
  then reload. The 15-s poller picks it up. To clear:
  `localStorage.removeItem("hg-stack-token-DEV")`.
- **Tauri-bootstrap path (Phase 4):** the Rust side reads from a 0600
  config file under `%APPDATA%\home-app\config.json` (Windows) /
  `~/.config/home-app/config.json` (Linux/macOS) and exposes it via a
  `stack_token()` Tauri command. JS never persists the token.

In every path, the token is **not** in the JS bundle or in git. If the
AiStackCard reads "not configured", neither the gateway proxy nor a local
workstation token path is set up.

### Audit log

Every state-changing op (Phase 2+) appends one line to
`/var/log/hav-supervisor.log`:

```
2026-05-15T12:34:56Z verb=start caller_ip=192.168.0.100 task_id=t_abc12345 exit_code=0
```

Owner `hav-supervisor:adm`, mode `0640`, weekly rotation (8 weeks retained).
This is the only artifact that says who/when/why if the stack mysteriously
stops — read first when investigating.

### Troubleshooting

| Symptom                                              | Fix                                                                                  |
|------------------------------------------------------|--------------------------------------------------------------------------------------|
| `systemctl status hav-stack-supervisor` says failed  | `journalctl -u hav-stack-supervisor -n 40` — usually `STACK_TOKEN` empty / too short |
| AiStackCard says "supervisor offline" but unit is up | Check `BIND_ADDR` matches the interface the workstation can reach (Tailscale vs LAN) |
| AiStackCard says "not configured"                    | Set the dev token in DevTools (above) or wait for Phase 4 Tauri command              |
| `429 rate-limited` in DevTools console               | Auth-failure lockout: wrong token tried ≥5 times; wait 60 s + fix the token          |
| Stack actually up but `ai stack` chip says `partial` | Check `docker ps` — one of the default compose services may have exited; `stack.sh status` |
| Want to disable the supervisor entirely              | `sudo systemctl disable --now hav-stack-supervisor` — stack.sh still works as before |

## External reasoning provider

A general-knowledge fallback for questions the local home agent isn't
optimized for (e.g. "explain quantum physics", "what's the difference
between OLED and Mini LED"). The local home agent stays fully local
and constrained for everything home-control-related — see
`home-external.jsx` and the plan at
`~/.claude/plans/keen-doodling-parasol.md` for the full design.

**Privacy by construction:** the only thing sent to the external
provider is the user's verbatim text plus a bare system prompt
explicitly told it has no access to your home. Entity IDs, room
names, perception text, HA tokens, LAN IPs, conversation history —
**never** leave this workstation.

**Safety by construction:** external answers render as text-only chat
events. They can never call Home Assistant actions. Even if the
external model writes "set lights to 30%", that's just text on the
screen — the user must follow up with a local request to actually
act.

### One-time setup

1. Generate an OpenAI API key at `platform.openai.com → API keys`. Set
   a low spend limit (~$5) as a safety net — the feature is cheap
   (gpt-4o-mini: <$0.001 per call typical).
2. In the Home app, type `/external set-key`. A modal opens; paste the
   key and hit Save. The key is stored in this workstation's
   `localStorage["hg-external-token-DEV"]` only — never in git, never
   in the JS bundle, never sent to the local stack.
3. Auto-routing is enabled automatically on first key save. Type a
   general-knowledge question to trigger; the classifier handles the
   routing. Use `/external off` to disable auto-routing (then only
   `/ask` triggers external).

### Slash commands

| Command | Behavior |
|---|---|
| `/ask <text>`              | Force the external provider, bypassing the classifier (still respects `isConfigured()`). |
| `/local <text>`            | Force the local home agent, bypassing the classifier. |
| `/route <text>`            | Show the classifier's decision without dispatching (debug). |
| `/external on`             | Enable auto-routing of classifier-detected general questions. |
| `/external off`            | Disable auto-routing — only `/ask` triggers external. |
| `/external status`         | Show provider, configured?, auto-routing state, last-call stats. |
| `/external set-key`        | Open the key-entry modal (no DevTools paste needed). |
| `/test classifier`         | Run the built-in classifier fixture set; print pass/fail. |
| `/test external-privacy`   | In sim mode: confirm outgoing request body contains no leak strings. |
| `/test external-suite`     | Run smoke + classifier + privacy + (optionally) one live API call; print summary. |

### After-build verification

After `cargo tauri build` + relaunch, run **one** command:

```
/test external-suite
```

Expected: `✓ N passed · ✗ 0 failed`. If anything's red, the failing
scenario's message points to the issue (most common: forgot to set
the key — fix with `/external set-key`).

### Troubleshooting

| Symptom | Fix |
|---|---|
| `/ask` returns "external reasoning is not configured" | Run `/external set-key` and paste a valid key. |
| `/ask` returns "external provider auth failed" | Key is invalid or revoked — rotate at platform.openai.com, then `/external set-key`. |
| `/ask` returns "rate-limited — try again shortly" | Free-tier limit hit; wait the printed retry-after or upgrade plan. |
| `/ask` returns "external provider unreachable" | Network down or OpenAI degraded; home control is unaffected, just retry later. |
| `/ask` returns "external request timed out (30s)" | Slow response from provider; retry. |
| Classifier mis-routes a question | Use `/local <text>` or `/ask <text>` to override. Run `/route <text>` to see which rule triggered. |
| Want to stop using external entirely | `/external off` keeps the key but disables auto-routing. Or `/external set-key` → Clear to remove the key. |

### Privacy verification (one-shot)

```
/test external-privacy
```

Sets `__SIM_ACTIVE` to true (so no real API call fires), dispatches a
canned input through `askExternal()`, captures the outgoing request
body, and greps for any of: HA entity-ID prefixes (`light.`,
`binary_sensor.`, etc.), `hav-*` container names, `192.168.*` LAN
IPs, JWT prefixes (`eyJ`), the local model name (`qwen3-vl-30b`),
system username, RTSP URLs. Any hit is a leak — hard fail.

### Voice path (HA-side routing)

The Tauri-side classifier handles typed input via `sendInput()`, but
**voice** input goes Voice PE → Wyoming → HA's `assist_pipeline` →
Extended OpenAI Conversation (in `/config/custom_components/extended_openai_conversation/`),
bypassing the Tauri router entirely. To get the same general-knowledge
routing for voice, the HA integration ships its own classifier in
`external_routing.py`, mirroring the JS rules character-for-character.

**Setup (one-time, on HAOS):**

```bash
ssh -p 22222 root@homeassistant.local
# at the HAOS shell — `read -s` keeps the key out of bash history:
read -s KEY && printf '%s' "$KEY" > /config/extended_openai_conversation_external_key && chmod 600 /config/extended_openai_conversation_external_key && unset KEY
ls -la /config/extended_openai_conversation_external_key   # → -rw------- root root
```

Paste your rotated OpenAI key when prompted, hit Enter, key is written
and the variable is cleared. No restart needed — the integration reads
the key per-request, so rotation is just a file edit.

**How it works:**

- `conversation.py:async_process()` runs `classify_intent(user_input.text)`
  *before* assembling the home-context system prompt.
- If `external` AND the key file exists, it calls `ask_external()`
  (OpenAI Chat Completions, `max_tokens=300`, TTS-friendly system
  prompt: plain prose, no markdown, 2–4 sentences).
- The response is wrapped as a HA `IntentResponse` speech body — HA's
  normal TTS path takes over and the answer is spoken.
- If routing is `local` or `ambiguous`, or if external fails for any
  reason, the existing local home agent handles the message unchanged.

**GENERAL classifier coverage** (extended May 2026 to catch real-world
voice questions that previously fell through to `ambiguous → local`):

| Pattern | Examples |
|---|---|
| `when (is\|was\|did\|were\|will)` | "when was Lincoln born", "when did WWII start" |
| `who (is\|was\|invented\|wrote\|made\|created\|discovered\|founded\|painted)` | "who invented the lightbulb" |
| `where (is\|was\|did\|are)` | "where is Paris" |
| `which (is\|are\|was\|of)` | "which is bigger" |
| `what (year\|day\|century)` | "what year did Rome fall" — **deliberately excludes `what time`**, which OpenAI can't answer correctly |
| `tell me (about\|a)` | "tell me a joke" |
| `describe \| define` | "describe the renaissance" |
| `list (the\|some\|all) \| name (the\|a\|some)` | "list the planets" |
| `(tldr\|tl;?dr\|summary\|overview) (on\|of\|for\|about)` | "give me the tldr on X" |

HOME_VERBS / HOME_NOUNS / HOME_QUERIES are checked **before** GENERAL,
so home-control phrasings still match local first: "where is the
office camera" → `office` in HOME_NOUNS → local. The test suite at
[test_external_routing.py](C:/Claude/home/ha-config/extended_openai_conversation/test_external_routing.py)
has 79 fixtures including explicit local-guard cases.

**Privacy invariant (HA side):** same as Tauri side — `ask_external()`
takes only `text` + a fixed system prompt. Entity IDs, room state,
perception, HA tokens, conversation history never reach OpenAI.

**Disable voice external routing:**

```bash
ssh -p 22222 root@homeassistant.local "rm /config/extended_openai_conversation_external_key"
```

Voice goes 100% local-only again — no restart needed.

**Diagnose:**

```bash
ssh -p 22222 root@homeassistant.local "ha core logs 2>&1 | grep -i external_routing | tail -20"
```

Look for lines like `external routing: dispatching '<text>' externally`
on success, or `external routing: ask_external failed (...)` on
fallback-to-local.

### Routing reliability — instrumentation + analyzer

Every conversation turn writes one line of JSONL to
`/config/external_routing.log` on HAOS. The workstation has tooling
to pull + analyze it for misroutes.

| Source | Lives on | What it captures |
|---|---|---|
| `/config/external_routing.log` | HAOS | **Authoritative.** Every turn (local + external). Includes input text, intent, matched rule, dispatch route, latency, and (for local-routed turns) the assistant's response — needed to detect "decline" patterns. |
| `tools/routing-corpus.jsonl` | workstation | Chat-tee SSE tap output. Secondary corroboration with richer detail (TTFT, tool calls, token counts) for LOCAL turns only. External turns bypass vLLM and don't appear here. |

**Three log modes via env var `EXTERNAL_ROUTING_LOG_MODE`:**

- `full` (default) — text + responses captured. Same trust level as HA's existing conversation DB.
- `redacted` — only lengths + intent + matched rule, no content. Use if you want analysis metadata but not transcripts.
- `off` — no log at all. Disables corpus analysis entirely.

Set in HA's environment (via supervisor settings or system env). Default `full`.

**Run the SSE tap** (long-running, captures real-time):

```powershell
cd C:\Claude\home\tools
python3 routing-corpus-tap.py
```

Heartbeats every 60 s to stderr; appends events to
`routing-corpus.jsonl`; auto-rotates at 50 MB / 30 days. Ctrl-C to stop.

```powershell
# one-shot: pull the last N completions and exit
python3 routing-corpus-tap.py --backfill 50
```

**Run the analyzer**:

```powershell
cd C:\Claude\home\tools
python3 analyze-routing.py
# pulls /config/external_routing.log over SSH, reads the local JSONL,
# joins by conv_id, detects declines via regex, writes:
#   routing-report-<timestamp>.md
```

Prints `INSUFFICIENT CORPUS — wait for more usage` if fewer than
30 total turns or 5 declines (gap analysis is statistical noise below
that). By design.

**Slow-pass — LLM-confirmed declines** (~$0.0001 per judgment):

```powershell
python3 analyze-routing.py --llm-judge
```

Asks `gpt-4o-mini` "did this response answer the question?" for each
regex-flagged decline. Filters out false positives.

**Just dump candidate inputs (no analysis):**

```powershell
python3 analyze-routing.py --candidates-only
```

Useful for replaying inputs through a proposed new classifier offline
before deploying.

### Routing visibility in the Home app

Two paste-back paths for testing reliability without SSH.

**Live one-liners** — `/debug on` in the Home app surfaces each routing
decision as a single `[route]` chat line within ~1 s of the assistant
finishing. Format:

```
[route] voice • external      • matched=GENERAL  • "explain quantum physics" → ok 1.4s 284ch
[route] voice • local         • matched=-        • "tell me about lincoln"    → local: "I don't have…"
[route] chat  • external→fb   • matched=GENERAL  • "explain X"                 → fail:RuntimeError, local: "Sure, X is…"
```

Powered by HA event `extended_openai_conversation.routing_decision`,
fired from inside `external_routing.log_decision()` AFTER redaction —
so the payload mirrors the JSONL file write exactly (single source of
truth for what content leaves the integration). `EXTERNAL_ROUTING_LOG_MODE=off`
disables both the file write AND the event fire.

`/debug off` hides the live stream but leaves the file write intact.

**Bulk paste-back** — `/route-log [N]` (default 20, max 200) hits the
`/api/extended_openai_conversation/routing_log?tail=N` REST endpoint and
dumps each entry as a system event containing the **raw single-line JSON**
(intentionally not pretty-printed) so select-all + copy + paste delivers
parseable JSONL.

The endpoint is `requires_auth=True`, GET-only, and returns
`{"entries": []}` if the log file doesn't exist yet (never 500s).

```
/debug on               # live one-liners visible
/route-log              # last 20 entries
/route-log 50           # last 50 entries (1..200)
```

Workflow for an async test session: enable `/debug on`, run ~10 varied
voice/chat queries, then `/route-log 30` and paste the JSONL block back
for analysis. The HAOS log captures everything regardless of whether
`/debug on` is active — visibility is independent of capture.

### Jarvis mute — "Hey Jarvis, stop"

Composite mute that suppresses Jarvis responses when you don't want
interruptions (movies, calls, focus time). Goal: watch a whole movie
without random agent reactions to TV audio.

**The signals** (all OR'd into `binary_sensor.jarvis_muted_effective`):

| Signal | Set by | Auto-clears when |
|---|---|---|
| `input_boolean.jarvis_muted_explicit` | "Hey Jarvis, stop" / `/mute` / header pill | manual unmute or "Hey Jarvis, wake up" |
| `input_datetime.jarvis_mute_until` | "Hey Jarvis, mute for 2 hours" / `/mute 2h` | timestamp passes (re-eval every 60s) |
| `input_boolean.homeai_movie` | "Hey Jarvis, I'm watching a movie" / `/mute movie` | manual OR 6h auto-clear safety net |
| `media_player.lg_tv` active > 5min | TV power-on (auto-detect) | TV off + 5min grace |

The `input_boolean.jarvis_auto_mute_tv` toggle (default ON) lets you
opt out of the TV-based auto-detect if you prefer manual-only mute.

**Voice commands** (all anchored to whole-utterance — "stop the music"
does NOT mute):

- **Mute**: "stop" / "shut up" / "be quiet" / "go quiet" / "hush"
- **Mute with duration**: "stop for 30 minutes" / "mute for 2 hours"
- **Movie mode**: "I'm watching a movie" / "start the show" / "put on the film"
- **Resume**: "wake up" / "resume" / "talk to me" / "I'm back" / "you can talk"
- **One-shot bypass while muted**: prefix with "real quick" / "just this once" / "quick question" — agent answers the one turn, mute holds

**Typed commands** (in the home app input):

```
/mute              # manual mute
/mute 30m          # timer (30 minutes)
/mute 2h           # timer (2 hours)
/mute movie        # movie mode
/unmute            # clear manual mute (TV/movie auto-signals still apply)
```

**Header pill**: when muted, the home app's header shows
`🔇 muted · <reason>` (manual / timer / movie mode / tv active). Click
the pill to unmute.

**TTS cancellation**: when "Hey Jarvis, stop" fires mid-response, the
gate immediately (a) calls `media_player.media_stop` on Sonos to
silence in-flight TTS and (b) fires a `extended_openai_conversation.tts_cancel`
HA event the bridge subscribes to → sets `_closed=True` on the active
Kokoro stream within ~200ms. No more 8-second sentence finishing after
you've already asked it to stop.

**Where the gate fires** (defense in depth):

1. `conversation.py:async_process` — first gate, early-return with empty
   IntentResponse + `continue_conversation=False` (closes Voice PE mic)
2. Bridge `_on_parakeet_transcript` — drops Parakeet transcripts before
   they reach the dispatcher
3. `continue_conversation` override — forces False whenever muted, so
   even if a turn slips through the mic doesn't stay open
4. HA WS `state_changed` subscription on the bridge — updates the mute
   cache to ~100ms latency after manual toggle

**Known limitations**:

- TV `media_player` entity is hardcoded to `media_player.lg_tv`. If you
  add a new TV, update the YAML.
- Voice PE wake-word chime still fires from TV audio (LED + tiny beep).
  Phase 2 will add Wyoming wake-suppression. For now the gate ensures
  no response follows, but the chime itself is cosmetic noise.
- Whole-house mute scope. A guest in another room can't use Jarvis
  while you're watching a movie. Per-room mute needs speaker-ID first
  (deferred).

**Troubleshooting**:

- **Sensor stays `unknown`**: Check `ha core logs | grep template`. The
  template references `media_player.lg_tv` — if that entity is missing,
  re-point the YAML to your actual TV entity_id.
- **Mute doesn't clear after timer expires**: the 60-second update
  automation (`jarvis_refresh_muted_effective_1m`) is the safety net.
  If it's missing or disabled, force a refresh via Developer Tools →
  Services → `homeassistant.update_entity` on the sensor.
- **Stale TV state holds mute**: LG TV's `last_changed` may be hours
  ago if the TV doesn't emit other state updates. Workaround: toggle
  `input_boolean.jarvis_auto_mute_tv` off temporarily to bypass.

### Identity-aware world state

The Extended OpenAI integration aggregates Frigate face recognition,
person occupancy, HA presence (`person.*` / `device_tracker.*`), and
camera availability into a single structured "world state" that the
agent queries via dedicated tools instead of guessing from entity CSVs.

Without this, the agent would say "I see a human" for *"Do you see
me?"* even though Frigate recognized the user. With it, the agent says
*"Yes, I see you in the kitchen"* — and hedges when confidence is low.

**Tools** (registered as OpenAI function-calls in `DEFAULT_CONF_FUNCTION_TOOLS`):

| Tool | Use |
|---|---|
| `get_all_rooms_state()` | Overview of every room — occupancy + identified persons. |
| `get_room_state(room)` | Detailed state for one room (persons + perception + freshness). |
| `find_person(name)` | Locate a person across cameras + HA presence. Accepts `"me"` / `"I"` / `"myself"` → primary user. |
| `who_is_in(room)` | Identified + generic-person count in one room. |
| `refresh_perception(room)` | Sync vision-sidecar `/describe` for a fresh visual (2-5 s, max 2 per turn). |

Each read tool returns:

```json
{
  "data":                {<room/person slice>},
  "suggested_phrasing":  "Yes, I see you in the kitchen.",
  "confidence_band":     "high" | "medium" | "low" | "unknown",
  "freshness":           "fresh" | "recent" | "stale" | "none"
}
```

The `suggested_phrasing` field carries the correctly-hedged sentence
so the agent doesn't have to re-derive the rule (belt-and-suspenders
against prompt drift).

**Tuning constants** (in
[`const.py`](C:/Claude/home/ha-config/extended_openai_conversation/const.py)):

| Constant | Default | Meaning |
|---|---|---|
| `IDENTITY_CONFIDENCE_HIGH` | 0.70 | Name the person without hedging. |
| `IDENTITY_CONFIDENCE_MEDIUM` | 0.40 | Hedge ("I think I see…"). |
| `FRESH_SECONDS` | 60 | `currently_seen` cutoff. |
| `RECENT_SECONDS` | 180 | Hedge on age cutoff. |
| `STALE_SECONDS` | 600 | Explicit "X minutes ago" cutoff. |
| `CAMERA_TO_ROOM` | `{camera.X: X}` for known cameras | Override for cameras whose entity_id doesn't follow the convention. |
| `HA_PERSON_TO_CANONICAL` | `{"engineeredlighting": "Marcelo"}` | Maps `person.<slug>` HA entities to the canonical display name (e.g., HA admin account → primary household user). Without this the aggregator can't unify HA presence with Frigate face-rec. |
| `FRIGATE_PERSON_TRACKER_DEFAULT_CONFIDENCE` | 0.85 | Confidence assumed for `sensor.frigate_<person>_last_camera` when no explicit `score` attribute is present. Frigate only updates this sensor on confident matches. |

**Subscribed entity patterns:**

| Pattern | Purpose |
|---|---|
| `sensor.frigate_<person>_last_camera` | Frigate's per-person canonical "where is X right now" signal. Authoritative for find_person; better than scanning per-camera face sensors. |
| `sensor.<camera>_last_recognized_face` | Per-camera most-recent face-rec match. Used for per-room presence + identity. |
| `binary_sensor.<camera>_person_occupancy` | Generic person detection (no identity). Drives the "I see someone" fallback. |
| `person.<slug>` | HA presence (home/not_home/zone). |
| `device_tracker.<slug>` | Device-level presence (typically the underlying source for person.* entities). |
| `camera.<slug>` | Camera availability + room mapping. |

After a week of real usage, recalibrate `*_CONFIDENCE_*` based on
Frigate's actual score distribution (the routing-log analyzer pattern
can be extended to collect them).

**REST endpoint** (mirrors `/route-log`):

```
GET /api/extended_openai_conversation/world_state[?room=<name>]
   → full world state, or one room's slice
   Auth required; returns `{enabled: false}` if disabled by env var.
```

**`/world-state` slash command** in the Home app fetches this endpoint
and dumps the response as a pretty-printed JSON system event — useful
for debugging "why did the agent answer X" by inspecting what the
aggregator actually has.

```
/world-state           # full state — all rooms + people + system
/world-state kitchen   # one room's slice
/world  …              # alias of /world-state
```

The slash command requires `/connect` to be set (so it has the HA
token). Output is pretty-printed for paste-back — select-all + copy
delivers a valid JSON blob.

**Disable** (clean rollback knob): set environment variable
`EXTENDED_OPENAI_WORLD_STATE=off` in HA's environment. The aggregator
skips subscription and tools return `{"error": "world state disabled"}`.
No code changes required.

**Debug**: tail HA logs for `world_state` startup banner and any errors:

```bash
ssh -p 22222 root@homeassistant.local "ha core logs 2>&1 | grep -iE 'world_state|WorldState' | tail -20"
```

The aggregator logs `world_state: subscribed to N entities across M rooms`
on startup. If that's missing or N=0, the camera/person/face entities
aren't visible to HA — check Frigate / Home Assistant entity registry.

**Tests**: 20 pytest scenarios in
[`test_world_state.py`](C:/Claude/home/ha-config/extended_openai_conversation/test_world_state.py)
covering the seven user stories from
[the plan's Addendum 4](C:/Users/Marcelo/.claude/plans/keen-doodling-parasol.md)
(recognized/unknown/empty rooms, "do you see me" with hedging,
multi-room "find person" recency, conflicting presence, stale visuals,
"unknown must not be Marcelo" adversarial regression, etc.). Run on
the workstation:

```powershell
cd C:\Claude\home\ha-config\extended_openai_conversation
$env:PYTHONIOENCODING="utf-8"; py -3 test_world_state.py
```

Expect "20/20 passed" + the existing 6 routing test suites also pass.

## Lab tab — metrics dashboard (alpha)

The metrics drawer has a new **third tab** named `lab` alongside the
existing `ai` and `infra` tabs. Implements the time-aligned chart
design from Claude Design (Addendum 10): voice-call stage trace on
top + GPU / VRAM / CPU / RAM line graphs underneath, sharing one
x-axis so you can see, e.g., "LLM stage was slow AND VRAM spiked
at exactly that moment."

**Status**: alpha — coexists with the proven `ai` / `infra` tabs
which are unchanged. Once the lab tab is validated in normal use,
the older tabs can be collapsed or removed in a follow-up.

### What's in the Lab tab

Top → bottom:

1. **Summary header** — composite status chip + status word + sub-text
   + action buttons. Derived from a single `deriveLabTier()` classifier
   that maps multiple signals (aiStackOnline, vramPct, service health,
   slow-turn detection) into one of 8 tiers:

   | Tier | Trigger | Visual signal |
   |---|---|---|
   | `ready` | default healthy | cool chip, no banner |
   | `warming` | aiStackState.starting / verb=start | progress bar |
   | `degraded` | 1 service stale | amber banner |
   | `partial` | ≥2 services down | amber banner, "core healthy" |
   | `offline` | aiStackOnline=false | red banner, "start ai stack" |
   | `error` | service crashed (oom/etc) | red banner, "restart" |
   | `pressured` | vramPct > 85% | amber banner, "free gpu" |
   | `slow` | lastTurn > 1.5 × baseline p50 | amber chart badge |

2. **Progress slot** (warming only) — step name + bar + ETA + cancel
3. **Banner slot** (degraded/error/etc) — color-coded border-left strip
4. **Compact stack list** — default compose services surfaced by the supervisor:
   vLLM, Parakeet, Kokoro, Chatterbox, wyoming-kokoro, vision-sidecar,
   metrics-sidecar, and intelligence.
5. **Chart card** — the centerpiece:
   - **Hero**: big "1684ms" + p10–p90 baseline + p50 / p90 / trend stats
   - **History mode** (default): 21 calls left → right, horizontal
     scroll; stack-trace rects + 4 resource lines (VRAM / GPU / CPU /
     RAM) sharing the same x-axis. Hover any stage → tooltip. Click a
     historical turn → switches to NOW mode for that turn.
   - **Now mode**: single turn expanded to card width with in-rect
     stage labels (STT / LLM / Synth / Audio + ms). Resource lines
     render fine-grain within the turn's duration.
   - **Chart badge**: chip in the head shows `fast` / `slowing` /
     `pressured` / `—` driven by the tier
6. **Resource pills** — 4 cards: VRAM hero (GB + %) + GPU / CPU / RAM
   compact, each with a thin progress bar
7. **Diag pane** (toggle button at bottom) — 4 global action buttons
   (free gpu / reload all / pause perception / clear cache) + 4
   per-service blocks (logs / stop / restart) + log filter + log pane.
   All destructive actions use the two-click confirm pattern.

### How the time-aligned chart works

- Resource samples are captured at the existing 750 ms metrics-poll
  cadence and embedded **per-turn** inside each LabTurn (so the 21-call
  history survives even when turns are minutes apart — a shared rolling
  buffer would lose old samples within 7.5 minutes).
- Each turn is timestamped client-side at fetch (`startedAt = Date.now()
  - trace.t_done`) — no backend changes needed for MVP.
- Tooltip shows stage name + ms + % of turn + transcript snippet on
  history hover; in NOW mode adds "2.3× baseline · vram pressure"
  annotation for slow audio.
- Tier classification + baseline (rolling p10/p50/p90/trend over last 30
  turns) recomputed on every render — cheap, no caching needed.

### AI Stack actions

Start / restart / stop are wired through a **shared module**
[`home-stack-actions.jsx`](C:/Claude/home/app/src/home-stack-actions.jsx)
that both the existing `AiStackCard` (AI tab) and `HmLabTab` (Lab tab)
use. No duplicated auth or retry logic. Requires `STACK_TOKEN` set
via `/stack-token <TOKEN>` (see Workstation token storage section
above) — without it, action buttons render disabled with a "token
required" tooltip on hover.

Destructive actions (free gpu / restart all / stop / per-service
restart) use a **two-click confirm**: first click → "✓ confirm" with
3 s timeout; second click → fires. Same UX as the design.

### Validating the Lab tab — 9 sim scenarios

```
/sim metrics-timeline-healthy          # 21 nominal turns, baseline render
/sim metrics-timeline-high-vram        # VRAM 91%, pressured tier
/sim metrics-timeline-slow-llm         # last 5 turns slow LLM
/sim metrics-timeline-stt-cpu-spike    # CPU spikes during STT only
/sim metrics-timeline-tts-slow         # synth + audio widen
/sim metrics-timeline-history          # degradation story (best demo)
/sim metrics-timeline-no-data          # empty buffer ("awaiting 5 turns")
/sim ai-stack-starting                 # warming tier — progress bar
/sim ai-stack-error                    # error tier — vllm crash banner
```

Open the drawer (click the bottom strip) → switch to `lab` tab →
each scenario should render distinctly. Toggle history ↔ now via
the toggle in the chart header. Click any historical call in
history mode to inspect it in NOW mode. Toggle diag at the bottom
right to see the service operations pane.

### Known limitations (alpha)

- **Visual fidelity vs Claude Design mockups**: needs user review.
  Pixel-perfection checks weren't auto-verifiable; the code mirrors
  the design's class structure + color tokens but minor spacing /
  contrast tuning may be needed.
- **Hand-off review**: visual sweep across all 9 sim scenarios in
  both dark + light mode. Specific things to check:
  - Tooltip clipping at chart edges
  - Light-mode contrast (cool→ink-blue swap; glow off)
  - Stack-trace stage tints feel right vs design
  - Pill bar fills look proportional
- **Resource sample density**: live cadence is 750 ms = 2-3 samples
  per ~1.7 s turn. History mode shows coarse lines; NOW mode is
  similarly coarse. Pass 2 could bump to 250 ms during active turns.
- **Diag pane log streaming**: SSE not yet wired (no
  `/logs/stream` endpoint in current metrics-sidecar); shows "no
  recent log lines · streaming when stack is active" placeholder.
  Logs button on services hits `/api/services/<svc>/logs?n=50` poll.
- **VRAM total clamping**: lab reads the raw `vram_total_gb` from
  /metrics, then snaps to known GPU specs (96 / 192 GB) to compute
  the percentage — same logic as the existing AI tab's arc.

### Files

| File | Purpose |
|---|---|
| [home-metrics-lab.jsx](C:/Claude/home/app/src/home-metrics-lab.jsx) | `HmLabTab` + `HmLabChart` + sub-components + pure helpers (`deriveLabTier` / `computeBaseline` / `extractStages`) |
| [home-stack-actions.jsx](C:/Claude/home/app/src/home-stack-actions.jsx) | Shared `HomeStackActions` REST module — supervisor auth + retry + confirm pattern |
| [home-ai-stack.jsx](C:/Claude/home/app/src/home-ai-stack.jsx) | Migrated to delegate to `HomeStackActions` (zero behavior change) |
| [home-app.jsx](C:/Claude/home/app/src/home-app.jsx) | New `labTurnsRef` / `labSamplesRef` / `labTick` state, sample writer, trace writer, sim-injection effect, third tab + `renderLab()` |
| [simulation-data.jsx](C:/Claude/home/app/src/simulation-data.jsx) | `makeLabFixture(scenarioId)` helper + 9 new scenarios |
| [index.html](C:/Claude/home/app/src/index.html) | Adds `home-stack-actions.jsx` + `home-metrics-lab.jsx` to script load order |

## Diagnostic harness — V / D / W suites

`tools/diagnose-identity.py` runs three orthogonal validation modes
against the live agent. All three write to `tools/diagnose-report.md`.

| Mode | Flag | Purpose | Side effects |
|---|---|---|---|
| **V** (validation, default) | *(no flag)* | Spelling rule + classifier + transcript delivery + live conversation regressions (9 scenarios) | Sends ~12 REST conversations |
| **D** (discovery) | `--watch <seconds>` | Tail real traffic for N seconds, flag anomalies in routing log / WS events / bridge logs | Read-only |
| **W** (workflow) | `--workflow` | Multi-step agent planning across read-only and write-gated scenarios, 5 attempts each, asserts on tool-call sequence | Read-only by default. Safe-listed helper/device writes require `--include-write-gated` and are enforced by the safe-entity guard. |

### W mode (workflow planning tests)

```powershell
cd C:\Claude\home
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow             # read-only scenarios × 5 attempts
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --quick     # read-only scenarios × 3 attempts
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --quick --include-write-gated  # opt-in safe-listed writes
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --quick --only travel_mode_blocks_light_on --include-write-gated  # guarded Travel Mode E2E
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --phase1-only  # only the 8 mechanical scenarios
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --strict    # require N/N pass (no drift)
PYTHONIOENCODING=utf-8 py -3 tools/diagnose-identity.py --workflow --only office_dim_warm,who_then_dim   # single-scenario debug
```

**Exit codes** (so the suite can gate CI / autonomous loops):
- `0` — all scenarios green (above threshold)
- `1` — at least one scenario below threshold but above 0 conclusive (drift)
- `2` — at least one scenario at 0 conclusive passes (hard fail — real planner regression)
- `3` — preflight failed (classifier-routing check, sidecar unreachable, safe entity unavailable). Suite never ran scenarios.

**Three pre-flights run before any scenario fires**:
1. Every scenario's query is run through
   `external_routing.classify_intent()`. Any query that routes EXTERNAL
   bypasses vLLM → no SSE event → tool-call assertion is impossible.
   Hard-fails the suite at startup (exit 3).
2. Metrics-sidecar `/healthz` must return 200 — without the
   `/conversations/stream` SSE endpoint, the harness can't capture
   tool calls.
3. Every entity in `SAFE_ENTITIES` must be available in HA (not
   `unavailable` / `unknown`). Catches "office light offline →
   every scenario falsely fails."

**Inconclusive attempts** (per `tools/workflow_scenarios.py`): when
the SSE feed times out but the HA WS conversation.finished event
confirms the agent responded, the attempt is marked `inconclusive`
(sidecar dropped the metrics trace, not a planner bug). Inconclusive
attempts are excluded from the pass threshold. The summary line
shows `2/3 +1 flake` — 2 passes out of 2 conclusive attempts; the
1 flake doesn't penalize.

A typical W suite output:

```
W mode (workflow suite, 5-shot per scenario)
  preflight: classifier verified all 15 queries route local
  preflight: sidecar healthz OK
  preflight: 2 safe entities available

  ✓  office_dim_warm                4/5 +1 flake  PASS  (thr 5)
  ✓  who_then_dim                   4/5            PASS  (thr 4)
  ⚠  multi_tier_conditional         1/5 +1 flake  DRIFT (thr 3)
       ↳ attempt 1: missing required ANY OF: must check 'am I home' before acting
  ✗  perception_branching           0/5 +2 flake  HARD FAIL (thr 3)
       ↳ attempt 1: missing required ANY OF: must take a 'fresh look'

  sidecar flake: 7/75 attempts had SSE timeouts (9%)
```

DRIFT scenarios surface planner non-determinism — the agent
occasionally takes a different (still valid) path. HARD FAIL
scenarios indicate a real regression: every conclusive attempt
failed.

### Safe-entity allow-list

`tools/workflow_scenarios.py` declares `SAFE_ENTITIES` — the only
entities the agent is ALLOWED to mutate during the suite. Any
`execute_services` call targeting an entity OR area outside this
list fails the scenario via the cross-cutting `SAFE_ENTITY_GUARD`
predicate. Update the set when adding scenarios that need new
mutate-safe entities.

```python
SAFE_ENTITIES = {
    "light.office",
    "media_player.living_room",
}
SAFE_AREAS = {"office"}
```

The guard also flags **untargeted** `execute_services` calls (no
entity_id AND no area_id — those fall back to HA's room-binding
which is unpredictable from a static check).

### Cross-cutting invariants

Apply to every scenario regardless of phase, defined in
`tools/workflow_scenarios.py` as `INVARIANTS`:

- non-empty assistant speech (≥ 3 chars after strip)
- speech ≤ 2000 chars (catches runaway hallucinations)
- `expect_silent=True` scenarios intentionally bypass the non-empty speech invariant and instead fail if any assistant speech or forbidden tool call appears.
- every tool call has parseable `arguments_parsed` (catches LLM
  emitting invalid JSON)
- no entity_id with an unknown HA domain (catches `lock.front_door`
  / `garage.*` / similar hallucinated entities)

### Adding a new scenario

1. Append a `WorkflowScenario(...)` to either `PHASE_1_SCENARIOS`
   (mechanical) or `PHASE_2_SCENARIOS` (cognitive stretch) in
   `tools/workflow_scenarios.py`.
2. Run `py -3 tools/diagnose-identity.py --workflow --only <id>`
   to iterate fast.
3. If the pre-flight rejects the query as routing external, rewrite
   to match `HOME_VERBS` / `HOME_NOUNS` / `HOME_QUERIES` (see
   `external_routing.py` regex constants).
4. Use `AnyOf(options=[...])` in `required_tools` when multiple
   plans are valid (e.g., the agent might call `find_person` OR
   `get_room_state` to check presence).
5. If the agent consistently uses a tool you didn't think of (e.g.,
   `describe_camera` instead of `get_room_state`), add it as an
   `AnyOf` option rather than forcing the agent into a specific
   plan.

## Recovery recipes

### One-command recovery after a power cut
```powershell
.\scripts\stack.ps1 up
```
Containers come back via `restart: unless-stopped`. The script verifies
they're healthy + runs smoke tests. If anything's red, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### Force-rebuild after a code change
```bash
docker compose build <service>
bash scripts/stack.sh restart
```

### Forget GPU? Confirm the container sees it
```bash
docker exec -it hav-vllm nvidia-smi -L
```

## Home Assistant operations

### Where the config lives
`/config/` on the HA host. The Extended OpenAI Conversation
integration we patched lives at
`/config/custom_components/extended_openai_conversation/`.

### Restart HA core via supervisor API (after editing custom_components)
```bash
ssh -p 22222 root@<ha-host> \
  'curl -X POST http://supervisor/core/stop \
        -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
   && sleep 3 \
   && curl -X POST http://supervisor/core/start \
        -H "Authorization: Bearer $SUPERVISOR_TOKEN"'
```
This bypasses the queued-job deadlock that can hit `homeassistant.restart`
when the conversation pipeline is in a weird state.

### Refresh a Long-Lived Access Token
Profile → Security → Long-Lived Access Tokens → Create.
Then in the Home desktop app: `/token <new-token>` (or wipe
localStorage and re-run FirstRun).

## Always-on follow-up

By default Voice PE closes its mic after each reply, requiring a fresh
wake-word for follow-ups. To keep it open ~10 s for hands-free
follow-ups:

### Apply
```bash
ssh -p 22222 root@<ha-host> \
  'sed -i "s|continue_conversation=chat_log.continue_conversation,|continue_conversation=True,|" \
   /config/custom_components/extended_openai_conversation/conversation.py'
# Then full HA core restart via supervisor API (see above).
```

### Revert
```bash
ssh -p 22222 root@<ha-host> \
  'sed -i "s|continue_conversation=True,|continue_conversation=chat_log.continue_conversation,|" \
   /config/custom_components/extended_openai_conversation/conversation.py'
# Full HA core restart.
```

## Proactive smart-home

The proactive experience — two-stage arrival confirmation, return-home
lights, room-entry prompts, the follow-up window — spans three layers:
HA automations (detection + reliable physical actions), the Python
bridge (a fast SSE fallback transport), and the Home app coordinator
(`home-proactive.jsx` — all conversational policy). See
[SIMULATION_MODE.md](../SIMULATION_MODE.md) for the design-review
scenarios.

### Install

1. **HA package** — copy `ha-config/homeai_proactive.yaml` into
   `/config/packages/` on the HA host. Ensure `configuration.yaml` has:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
   Edit every `# EDIT:` line in the package for your real entity_ids
   (the `person.*` entities, the per-room `binary_sensor.*_occupancy`
   ids, the entry-camera `sensor.*_last_recognized_face` id, and the
   `script.homeai_return_home` light entities + night exclusions).
   Check Configuration, then Reload.
2. **App config** — in `app/src/home-proactive.jsx` set
   `VOICE_PE_SATELLITE` to the wall puck's `assist_satellite.*`
   entity_id and `ARRIVAL_CONFIRM_CAMERAS` to your interior entry
   camera(s). If your HA is too old for
   `assist_satellite.start_conversation`, set `ANNOUNCE_SERVICE` to the
   `announce` fallback (the follow-up window then won't open — it
   degrades gracefully).
3. **Bridge** — already covered if identity/presence events work today
   (`CHATTEE_URL` set). The bridge change is additive: a new
   `s2s:proactive` SSE event type.
4. Confirm Voice PE honors `continue_conversation` — see
   [Always-on follow-up](#always-on-follow-up) above.

### Validation without leaving home

Three layers, none of which require physically leaving and returning:

- **Simulation Mode** — `/sim arrived-home`, `/sim welcome-home-followup`,
  etc. Pure UI/logic, zero infra. Design-review grade.
- **`/proactive test …`** in the app — fastest real loop. Drives the
  *real* coordinator → real `evaluateProactive` → real Voice PE puck.
  `/proactive test arrived` then `/proactive test confirm <name>` walks
  the full two-stage arrival; you hear the puck speak. `/proactive`
  alone shows status; `/proactive sleep|dnd|focus|movie on|off` flips a
  suppression mode; `/proactive reset` clears the ledger.
- **HA test-harness package** — copy `ha-config/homeai_proactive_test.yaml`
  into `/config/packages/` too. It adds `input_button` test entities you
  tap from any HA dashboard / your phone: `homeai_test_arrival` (full
  two-stage in 3 s), `homeai_test_arrival_unconfirmed`,
  `homeai_test_left_home`, `homeai_test_room_entry`,
  `homeai_test_face_confirm`. These fire the *exact* events the
  production automations fire. To also exercise the bridge SSE path +
  the real `for:` debounce, use **Developer Tools → States** to set
  `person.<you>` `home` ⇄ `not_home` directly.

  Remove the test package once you're satisfied.

### End-to-end scenario tests

The test-harness events carry `test: true` in their `event_data`. The
coordinator treats them exactly like real events **except** it skips the
long time-based rate-limits — welcome-home (30 min), global proactive
(90 s), per-room cooldown, and cross-transport dedupe — so the suite is
freely repeatable without waiting a cooldown out. Everything else runs
for real: the two-stage arrival gate, the mode / quiet-hours
suppression rules, the `assist_satellite.start_conversation` call on the
puck, and the follow-up window. (The *time-based* limits are the only
thing skipped — the *mode* gates below are not, so they stay testable.)

**One-tap full suite.** Tap `input_button.homeai_test_run_all` (or run
`script.homeai_run_all_tests`). It walks the whole story in ~55 s:
leave → return (two-stage → spoken welcome + return-home scene) → room
entry → a stray face-confirm (correctly ignored). Ends with the lights
on and the app idle.

**Per-scenario matrix.** Each row is independently runnable *and*
independently verifiable — the "HA-side proof" column is an objective
artifact you can confirm without watching the puck or squinting at the
app feed (see [Verifying from the HA side](#verifying-from-the-ha-side)).
Turn on `/debug` in the app first so the suppression / pending / window
diag lines are visible in the feed.

| # | Trigger (`input_button.*` / app cmd) | Expect — app + puck | HA-side proof |
|---|---|---|---|
| 1 | `homeai_test_arrival` · `/proactive test arrived` then `… confirm Marcelo` | ~3 s later the puck speaks "Welcome home, Marcelo"; feed: "Welcome home — spoken" + "Return-home scene applied"; follow-up window opens | `assist_satellite.<puck>` → `responding`; `script.homeai_return_home` in the logbook; indoor lights come on |
| 2 | `homeai_test_arrival_unconfirmed` | status line "arrival pending — awaiting camera…"; after the 5-min window, soft/silent fallback per the frigate-online table — **no spoken name** | `assist_satellite` **stays `idle`** the whole window; no `start_conversation` |
| 3 | `homeai_test_left_home` · `/proactive test left` | app flips to "Away mode"; feed-line for lights-off | all indoor `light.*` → `off`; `assist_satellite` stays `idle` |
| 4 | `homeai_test_room_entry` (+ pick `input_select.homeai_test_room`) · `/proactive test room kitchen` | short room prompt on the puck for that room; follow-up window opens | `assist_satellite.<puck>` → `responding` |
| 5 | `homeai_test_face_confirm` · `/proactive test confirm Marcelo` (while idle) | **nothing** — a bare identity with no pending arrival is corroboration-only; with `/debug`, a single diag line | `assist_satellite` stays `idle` |
| 6 | `homeai_test_run_all` | the full 1 → 4 → 5 story arc in sequence | each step's proof above, in order |

**Suppression checks** — the harness skips only *time-based* limits, so
the *mode* gates stay fully testable. Toggle the helper, fire the
trigger, then toggle back:

| Toggle | Then trigger | Expect |
|---|---|---|
| `input_boolean.homeai_dnd` → on | `homeai_test_room_entry` | suppressed — diag "suppressed: dnd"; puck silent |
| `input_boolean.homeai_sleep` → on | `homeai_test_room_entry` | suppressed — diag "suppressed: sleep-mode" |
| `input_select.homeai_house_mode` → focus | `homeai_test_room_entry` | room prompt suppressed; an arrival welcome would still speak |
| `input_boolean.homeai_movie` → on | `homeai_test_room_entry` | suppressed — diag "suppressed: movie-mode" |
| (clock is 23:00–07:00) | `homeai_test_arrival` | welcome downgraded to a silent feed-line — no speech |

The same modes are reachable from the app without HA: `/proactive
sleep|dnd|focus|movie on|off`. `/proactive reset` clears the ledger and
hard-resets the coordinator between runs.

### Verifying from the HA side

The objective proof that the whole chain worked is the **Voice PE
satellite entity** changing state — that only happens if the coordinator
received the event over the WebSocket and called back into HA. You do
not need to hear the puck or read the app:

- **Developer Tools → States** — filter `assist_satellite` and watch it
  flip `idle → responding → listening → idle` across an arrival/room
  test. Filter `automation.homeai_test` to see `last_triggered` advance
  on each button press; filter `light.` to see the return-home scene /
  the left-home turn-off land.
- **Logbook** — `script.homeai_return_home` and each `automation.homeai_*`
  show up with timestamps; the satellite's state changes are logged too.
- **Live tail** — from the operator workstation, run
  `scripts/listen_pipeline.py` (see [Useful one-liners](#useful-one-liners))
  while you tap the test buttons.

| Artifact | Proves |
|---|---|
| `automation.homeai_test_*` `last_triggered` advances | the button press fired the automation |
| `assist_satellite.<puck>` → `responding` | the coordinator received the event and called `start_conversation` — **the full round-trip** |
| `script.homeai_return_home` in the logbook | the return-home lighting path ran |
| indoor `light.*` → `off` after a left-home test | the unconditional HA-side safety action ran |
| `assist_satellite` **stays `idle`** after a bare `homeai_test_face_confirm` | the corroboration-only gate is holding (the negative test) |

## Logs

| Where                                      | What                                |
|--------------------------------------------|-------------------------------------|
| `docker compose logs vllm`                 | LLM request/response, model load    |
| `docker compose logs metrics-sidecar`      | Metrics sidecar + Prometheus scrape |
| `docker compose logs wyoming-parakeet`     | STT events                          |
| `docker compose logs kokoro-tts`           | TTS synthesis                       |
| HA → Settings → System → Logs              | Pipeline runs, agent errors         |
| `%LOCALAPPDATA%\com.engineeredlighting.home\logs\` | Desktop app (Windows)         |
| `~/Library/Logs/Home/` (macOS, future)     | Desktop app (macOS)                 |

## Useful one-liners

### Test a conversation API call directly
```bash
HA_URL=http://<ha-host>:8123 HA_TOKEN=<token> \
  curl -X POST "$HA_URL/api/conversation/process" \
       -H "Authorization: Bearer $HA_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"text":"turn off the kitchen lights"}'
```

### Verify vLLM model list
```bash
curl http://<ai-box>:8000/v1/models | jq
```

### Verify metrics endpoint
```bash
curl http://<ai-box>:8092/metrics | jq
```

### Watch the HA WebSocket pipeline events in real time
```bash
HA_URL=http://<ha-host>:8123 HA_TOKEN=<token> \
  python scripts/listen_pipeline.py
# In another shell: speak to Voice PE, or send a /api/conversation/process curl.
```
(Script lives in the `stack/` companion repo / your existing HomeAIVoice
working copy.)
