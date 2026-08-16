# home — local AI for your house

A self-hosted chat client for [Home Assistant](https://www.home-assistant.io/). Talk
to your house, watch it think, see what it's doing in real time. Runs
entirely on your hardware — your voice, your model, your data.

![home in dark and light modes](docs/images/cover.png)

## Download

[**Latest release →**](https://github.com/Engineered-Lighting/home/releases/latest)

- Windows 10/11 · `Home_x.y.z_x64_en-US.msi`
- macOS · coming soon

> First-time install on Windows shows a SmartScreen "Unrecognized app"
> dialog — click `More info → Run anyway`. The binary isn't code-signed
> yet (that's planned, but an EV cert costs real money).

## What you get

- A 420×720 always-on chat window in your favourite corner, resizable
  to whatever shape suits you.
- **Live camera card** at the top of the chat — collapsed row showing
  camera count, expands to a tab strip + live MJPEG feed of the
  selected camera. Slots for V-JEPA-2 activity labels.
- Real-time view of every assistant step: thinking, tool calls, device
  actions (collapsed `▸ action` cards), streaming reply text.
- **Voice mode** in the app: click the mic, talk, get a TTS reply
  back. Same HA pipeline as your Voice PE.
- **All conversations in one feed** — typed turns from the app, voice
  mode from the app, and Voice PE wake-word turns at your speaker
  all appear in the same Home window via the chat-tee SSE stream.
- Live system metrics from the AI box: GPU util, VRAM, CPU, RAM, plus
  TTFT + tok/s from the local LLM.
- Dark default + parchment "paper terminal" light mode.
- Conversation memory across turns. History persists between launches.

## What you need

| Piece                     | Purpose                                     | Notes                                                                 |
|---------------------------|---------------------------------------------|-----------------------------------------------------------------------|
| Linux box w/ NVIDIA GPU   | Runs the LLM + STT + TTS stack              | RTX 4090 / 6000 / A6000 (48 GB+ VRAM for the default 30B model; smaller models fit smaller GPUs) |
| Home Assistant            | Your home's brain (HAOS / Yellow / Green)   | Any recent HA release with HACS support                               |
| Home Assistant Voice PE   | Optional voice satellite                    | Hands-free wake-word. Not required — desktop app works standalone     |
| LAN or Tailscale          | Connection between desktop app and AI box   | Don't expose any of this to the public internet                       |

## Quick start

1. **Set up the AI box** → see [stack/README.md](stack/README.md)
   (one GPU host running vLLM + Kokoro + Parakeet + the metrics
   sidecar via Docker Compose).
2. **Wire Home Assistant** → see [docs/SETUP.md](docs/SETUP.md) (HACS,
   Extended OpenAI Conversation, Voice PE pairing, the long-lived
   access token).
3. **Install the desktop app** — download the .msi from the link
   above, launch it, paste your HA URL + token, pick a model.
4. Say *"turn off the kitchen lights"*. Watch the action card land.

## Architecture

```
                                ┌──────────────────────────────┐
                                │  Home Assistant (HAOS)        │
   ┌────────────┐  WebSocket    │  + Extended OpenAI Conv       │
   │  Home app  │ ───────────▶  │  + Voice PE integration       │
   │  (Tauri)   │  pipeline/run │                               │
   └────┬───────┘               │       │            │          │
        │                       │       │ OpenAI     │ Wyoming  │
        │ HTTP 8092             │       ▼            ▼          │
        │ metrics               │  ┌────────────┐ ┌──────────┐  │
        │                       │  │   vLLM     │ │ Parakeet │  │
        │                       │  │qwen3-vl-30b│ │   STT    │  │
        │                       │  └────────────┘ └──────────┘  │
        │                       │            ┌──────────┐       │
        │                       │            │  Kokoro  │       │
        │                       │            │   TTS    │       │
        │                       │            └──────────┘       │
        │                       │       ┌─────────────┐         │
        ▼                       │       │  metrics    │         │
   metrics-sidecar  ───────────▶│       │  sidecar    │         │
   (NVML + psutil +             │       └─────────────┘         │
    vLLM Prometheus)            └──────────────────────────────┘
        │                                  ▲
        └──────────────────────────────────┘
                       both run on the same Linux box
```

The desktop client only talks to two things: HA over WebSocket (for
the conversation pipeline) and the metrics sidecar over HTTP (for
system telemetry). Everything else — LLM dispatch, STT, TTS, device
actions — is HA's existing pipeline working unchanged.

## Documentation

- [Setup guide](docs/SETUP.md) — full installation walkthrough
- [Runbook](docs/RUNBOOK.md) — operate the stack (start/stop/recover/logs)
- [Architecture](docs/ARCHITECTURE.md) — how the pieces fit
- [Troubleshooting](docs/TROUBLESHOOTING.md) — common failures

## Status

**v0.1** — text chat, tool calls, device actions, live metrics, dark +
paper-terminal modes, resizable window, conversation history.

**v0.2 (next)** — real voice mode (mic → STT → pipeline → TTS); confirmation
gating in the HA agent for security-sensitive actions; macOS .dmg build.

**later** — auto-update, system tray / always-on-top, EV code-signing,
SQLite-backed history.

## Slash commands

Available in the input bar:

- `/connect <url> [<token>]` — point at an HA endpoint
- `/token <ha long-lived access token>` — replace just the token
- `/model <name>` — display label for the active model
- `/metrics <url>` — override the metrics sidecar URL
- `/clear` — wipe the feed and start a fresh conversation
- `/demo` — replay the scripted design demo
- `/about` `/version` — version info
- `/help` — list these commands

## Keyboard shortcuts

- `Ctrl/⌘ + .` — stop generation
- `Ctrl/⌘ + L` — clear conversation
- `Ctrl/⌘ + K` — focus the input field
- `Esc` — cancel a pending-confirm action

## License

[MIT](LICENSE).
