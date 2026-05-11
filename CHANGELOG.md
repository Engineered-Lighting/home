# Changelog

All notable changes to this project will be documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-11

### Stack

- **Unified vision + text + tool-call model.** vLLM serves
  `Qwen/Qwen3-VL-30B-A3B-Instruct` — a Mixture-of-Experts model with
  3 B active parameters per token (fast inference) but 30 B total
  knowledge and native multimodal vision. One process handles chat,
  device control, and camera understanding. `--tool-call-parser hermes`
  (Qwen3-VL emits Hermes-format tool calls).
- **`metrics-sidecar` is now also a chat-tee proxy.** Externally on
  port `:8000`, it proxies HA's `/v1/chat/completions` calls through to
  vLLM and broadcasts every captured turn (user text + assistant text +
  tool_calls) on `/conversations/stream` as SSE. The Home app
  subscribes — every typed turn, voice-mode turn, and Voice PE turn
  appears in the same feed regardless of origin. vLLM itself is no
  longer bound to a host port; only the sidecar is.
- **`vision-sidecar` rewritten** to call our multimodal vLLM directly
  (no more Ollama dependency). The LLM's `describe_camera` tool fetches
  a fresh HA `camera_proxy` JPEG and forwards it to vLLM as a
  multimodal `image_url` message.
- Ollama daemon stopped + disabled — model swap is complete; no
  separate vision model.
- HA prompt patched with a "Rule 0: tool use is mandatory for actions"
  preamble + an example payload + a "no `Hass*` built-in tools" rule.
  Stops the LLM from hallucinating action completion without
  dispatching tool calls.
- `max_tokens` bumped from 220 → 2000 so multi-entity tool calls
  (e.g. "turn off all my lights" with 23 lights) don't get truncated.
- HA pipeline's `prefer_local_intents` set to `false` — every turn
  goes through the LLM with full tool access, ensuring tool_calls
  always reach the chat-tee.

### Home desktop app

- **Vision card at the top of the chat.** Pattern E from the design:
  thin collapsed row showing camera count + occupancy; expands to a
  tab strip + live MJPEG feed of the selected camera. Uses HA's
  `auth/sign_path` WS command for auth-free `<img>` streaming. Five
  cameras out of the box (living room, kitchen, dining room, workshop,
  driveway). Activity labels show `undetected` for now — slot ready
  for V-JEPA-2 wiring.
- Tauri 2 desktop app with frameless 420 × 720 window (resizable; min
  360 × 480; matches the three sizes in the design canvas).
- Engineered Lighting visual language: dark default + parchment "paper
  terminal" light mode.
- HA WebSocket `assist_pipeline/run` integration for typed turns,
  voice mode (`stt` start-stage), and pipeline reconfiguration via WS.
- Voice mode: mic captured via `getUserMedia` → PCM s16le @ 16 kHz →
  HA pipeline → STT → intent → TTS → audio playback via `Audio()`.
- SSE-driven conversation rendering — single source of truth.
  Local user-event dedup keeps instant feedback for typed turns and
  shows Voice PE turns inline marked with `◉`.
- Real-time `call_service` action cards: grouped per turn (800 ms
  burst window), one card per `{domain.service}`, collapsed by default
  with `▸ action ...` (click to expand attrs).
- Conversation memory across turns via `conversation_id`. Events,
  `conversation_id`, prefs persisted to `localStorage`.
- Sidecar auto-probe on connect: tries HA host, same `/24` with .100,
  then localhost — sets `metricsBase` from the first responder.
- Slash commands: `/connect`, `/token`, `/model`, `/metrics`, `/clear`,
  `/demo`, `/about`, `/help`.
- Keyboard shortcuts: stop (`Ctrl/⌘+.`), clear (`Ctrl/⌘+L`), focus
  input (`Ctrl/⌘+K`), cancel pending-confirm (`Esc`).
- Sun/moon theme toggle, persisted.
- ASCII `home` wordmark + light-cone banner at the top of the feed,
  scrolls up with the conversation.

### Distribution

- Windows: `.msi` installer (unsigned — Windows SmartScreen + Smart
  App Control will warn; users click `More info → Run anyway`).
- macOS: `.dmg` built via GitHub Actions on a macOS runner (built on
  tag push).
- v0.1 install requires either disabling Smart App Control on Windows
  OR running the dev build via `cargo tauri dev`. EV code-signing is a
  Phase 3 follow-up.

### Deferred to v0.2

- V-JEPA-2 wiring for real activity labels in the vision card (the
  card already accepts the `activity / activityConfidence` data shape).
- Confirmation gating in the HA agent prompt for security-sensitive
  intents.
- EV code-signing cert for the Windows .msi.
- Bounding box overlays in the vision frame (intentionally removed
  in design after a real-image legibility test).
