# Changelog

All notable changes to this project will be documented here. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-11

### Added
- Tauri 2 desktop app with frameless 420×720 window (resizable to any
  size, min 360×480).
- Engineered Lighting visual language: dark mode default + parchment
  "paper terminal" light mode.
- HA WebSocket `assist_pipeline/run` integration — text conversation,
  tool calls surface as action cards, conversation memory via
  `conversation_id`.
- FirstRun: HA URL + Long-Lived Access Token; auto-reconnect on
  relaunch.
- Token streaming via HA's `intent-progress` events, with graceful
  single-chunk fallback on `intent-end` for older HA versions.
- `metrics-sidecar` service (port 8092): FastAPI + pynvml + psutil +
  vLLM Prometheus scrape — exposes one JSON endpoint with GPU util,
  VRAM, CPU, RAM, TTFT, tok/s, current model name.
- Live metrics polling in the app every 2 s while online.
- Conversation history persistence: events, `conversation_id`, and
  prefs all in `localStorage`.
- Slash commands: `/connect`, `/token`, `/model`, `/metrics`, `/clear`,
  `/demo`, `/about`, `/help`.
- Keyboard shortcuts: stop (`Ctrl/⌘+.`), clear (`Ctrl/⌘+L`), focus
  input (`Ctrl/⌘+K`), cancel pending-confirm (`Esc`).
- Sun/moon theme toggle, persisted to localStorage.
- ASCII "home" wordmark + light-cone banner at the top of the feed,
  scrolls up with the conversation.

### Deferred to v0.2
- Real voice mode (mic → HA STT → pipeline → HA TTS → speakers).
  Currently the mic button still triggers the scripted demo flow.
- Confirmation gating in the HA agent prompt for security-sensitive
  intents.
- macOS .dmg build.
