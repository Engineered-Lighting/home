# Architecture

The whole stack is three independent pieces that talk to each other
over LAN or Tailscale. None of them require internet access at
runtime once models are pulled.

```
                            ┌─────────────────────────────┐
                            │ Home Assistant (HAOS / Pi /  │
                            │ Yellow / Green)              │
                            │                              │
                            │   ┌──── Extended OpenAI ──┐ │
                            │   │   Conversation agent  │ │
                            │   └──────┬────────────────┘ │
                            │   ┌──────▼────────────────┐ │
                            │   │ assist_pipeline       │ │
                            │   └─┬───┬───┬─────────────┘ │
                            └─────┼───┼───┼───────────────┘
                                  │   │   │
   ┌────────────────────┐         │   │   │   ┌──────────────────┐
   │  Home desktop app  │─── WS ──┘   │   │   │                  │
   │  (Tauri / WebView) │             │   │   │                  │
   │                    │             │   │   │                  │
   └─────────┬──────────┘             │   │   │                  │
             │ HTTP                   │   │   │                  │
             │ /metrics                │   │   │                  │
   ┌─────────▼──────────┐ ◀───────────┘   │   │                  │
   │  metrics-sidecar   │ Prometheus       │   │                  │
   │  (FastAPI :8092)   │                 │   │                  │
   └────────────────────┘                 │   │                  │
                                          ▼   ▼                  │
                                   ┌──────────────────┐          │
                                   │      vLLM        │          │
                                   │   qwen3 (FP8)    │          │
                                   │      :8000       │          │
                                   └──────────────────┘          │
                                          │                      │
                                          │ Wyoming              │
                                          ▼                      │
                                   ┌──────────────────┐ ┌──────────────┐
                                   │ Parakeet (STT)   │ │ Kokoro (TTS) │
                                   │     :10300       │ │   :10301     │
                                   └──────────────────┘ └──────────────┘
                                          (all on the Linux AI box)
```

## Components

### Home desktop app (this repo, `app/`)

Tauri 2 app — a Rust shell that hosts a WebView and exposes a small
HTTP plugin for cross-origin fetches. The frontend is React 18 +
in-browser Babel; no bundler, no build step beyond Rust compilation.
Frontend sources live in `app/src/`; they're plain `.jsx` files
served straight from disk and compiled at page load.

The app keeps two long-lived connections:

1. **Home Assistant WebSocket** — primary conversation transport.
   Auths with a Long-Lived Access Token. Sends
   `assist_pipeline/run` per user message. Receives the pipeline
   event stream (`run-start` → `intent-start` → `intent-progress`*
   → `intent-end` → `run-end`).

2. **metrics-sidecar HTTP** — polled every 2 s for system telemetry.

No other outbound traffic. Everything passes through HA's existing
conversation pipeline — that's how device actions and conversation
memory come along for free.

### Home Assistant + Extended OpenAI Conversation

HA hosts the pipeline. The Extended OpenAI Conversation custom
integration provides the LLM backend, pointed at vLLM's OpenAI-compatible
endpoint. HA's tool-call dispatch is where device actions actually
fire (`light.turn_on`, `lock.unlock`, etc.) — the desktop app just
visualises the results.

We patched one line in this integration (`continue_conversation=True`)
so Voice PE keeps its mic open after each turn for follow-ups. Optional
— documented in [RUNBOOK.md](RUNBOOK.md#always-on-follow-up).

### vLLM

OpenAI-compatible serving of a quantized Qwen 3 model. Uses the
`qwen3_xml` tool-call parser so HA's tool definitions dispatch
correctly. Streams tokens back via SSE when HA asks (which it does for
`intent-progress`).

### Wyoming Parakeet (STT)

NVIDIA Parakeet TDT v3 + Silero VAD wrapped in the [Wyoming
protocol](https://github.com/rhasspy/wyoming) that HA's voice
integration speaks. Listens on TCP :10300.

### Kokoro (TTS)

Kokoro-FastAPI serves voices; a `wyoming_openai` bridge exposes them
on TCP :10301 so HA can play them through Voice PE / Sonos / wherever.

### metrics-sidecar (this repo, `stack/services/metrics-sidecar/`)

~120 LOC FastAPI service. Two responsibilities:
- Sample NVML (GPU util, VRAM) + psutil (CPU, RAM) on demand.
- Scrape vLLM's Prometheus `/metrics` endpoint every 2 s and surface
  the latest TTFT + tok/s as part of its own JSON response.

The Home desktop app polls one URL — `:8092/metrics` — and receives
everything the metrics tray needs in one round-trip.

## Why HA WebSocket, not vLLM direct

An earlier design called vLLM directly for streaming text chat. That
gave us token-by-token streaming but skipped HA's tool-call dispatch
entirely — meaning no device actions, no conversation memory, no
intent classification. The whole point of this app is to *talk to your
house*, not just a chatbot, so we entered the pipeline at the HA layer
instead. Token streaming is preserved via HA's `intent-progress`
events (HA 2024.10+).

## Why a sidecar for metrics

`pynvml` only works on the GPU host. The desktop app runs on a
different machine (your daily driver). The simplest reliable way to
get GPU telemetry into the UI was to host a tiny service next to
NVML and let the app poll over HTTP — no Tauri-side native deps, no
SSH for telemetry, no GPU passthrough to the Windows machine.
