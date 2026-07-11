# stack/ — the Ubuntu AI box services

The governed Home Agent is intentionally a separate Compose project at
`home-agent-compose.yml`; it does not join this GPU/legacy Intelligence
lifecycle. Use `docs/HOME-AGENT-RUNBOOK.md` for its fail-closed storage,
backup, mTLS, OAuth, and rollout gates.

The Docker Compose stack the desktop client talks to. Runs vLLM,
Wyoming Parakeet (STT), Kokoro (TTS), a vision sidecar, and the
metrics sidecar that the Home app polls.

Designed for one Linux machine with at least one NVIDIA GPU. Tested on
RTX 4090 + RTX 6000 Blackwell.

## Prerequisites

1. Linux + NVIDIA driver (recent — Blackwell needs CUDA 12.8+).
2. Docker + Docker Compose plugin.
3. NVIDIA Container Toolkit. Verify with:
   ```bash
   docker run --gpus all --rm nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
   ```
4. ~50 GB free disk for model weights.
5. A Hugging Face token (`HF_TOKEN`) — required for gated model
   downloads.

## Layout

```
stack/
├── docker-compose.yml      # one source of truth for the whole stack
├── .env.example            # template for HF_TOKEN, bind addresses, and ports
├── scripts/
│   ├── stack.sh            # up / down / restart / status / logs
│   └── stack.ps1           # Windows wrapper (ssh → AI box)
└── services/
    ├── vllm/               # (optional) custom vLLM rebuild for Blackwell
    ├── stt/                # Wyoming Parakeet wrapper
    ├── tts/                # Kokoro-FastAPI + wyoming_openai bridge
    ├── vision/             # camera-snapshot description (Ollama qwen3-vl)
    └── metrics-sidecar/    # this repo's contribution — port 8092
```

## First-time setup

1. Clone this repo to the AI box at `/opt/home/`.
2. Copy the env template and fill it in:
   ```bash
   cp .env.example .env
   $EDITOR .env
   ```
3. Bring it up:
   ```bash
   bash scripts/stack.sh up
   ```
   On first run, vLLM pulls ~25 GB of model weights from Hugging Face
   (10–30 min on a fast connection). The script waits for healthchecks
   then runs a smoke-test panel.

## Day-to-day

```bash
bash scripts/stack.sh up        # ensure running + healthy (idempotent)
bash scripts/stack.sh down      # stop all services
bash scripts/stack.sh restart   # graceful restart
bash scripts/stack.sh status    # current state + smoke tests
bash scripts/stack.sh logs vllm # tail one service's logs
```

See [docs/RUNBOOK.md](../docs/RUNBOOK.md) for the full operating
guide, including recovery recipes and Home Assistant configuration.

## Services + ports

| Service          | Port  | Role                                |
|------------------|------:|-------------------------------------|
| vllm             |  8000 | OpenAI-compatible LLM               |
| wyoming-parakeet | 10300 | Speech to text (Wyoming protocol)   |
| kokoro-tts       |  8880 | TTS service                         |
| wyoming-kokoro   | 10301 | wyoming_openai bridge → Kokoro      |
| vision-sidecar   |  8091 | Camera image description            |
| metrics-sidecar  |  8092 | Telemetry for the Home desktop app  |

Sensitive model, vision, metrics/chat-proxy, and S2S bridge ports bind to
`127.0.0.1` by default. Set their explicit `*_BIND_ADDR` variables only to a
reviewed host address protected by the host firewall. The separate Agent Core
publishes neither PostgreSQL nor its application roles.

## metrics-sidecar in particular

A small FastAPI service that:
- Calls `pynvml` for GPU utilization + VRAM (in/total).
- Calls `psutil` for CPU + RAM (in/total).
- Scrapes vLLM's Prometheus `/metrics` endpoint every 2 s, derives a
  rolling tokens/sec rate, and exposes the most recent
  `time_to_first_token` average.
- Returns everything in one JSON response at `GET /metrics`.

The Home desktop client polls this endpoint every 2 s. That's the
sole source of the GPU sparklines + the TTFT/tok/s readings in the
metrics tray.
