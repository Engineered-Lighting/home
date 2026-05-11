# Setup guide — from zero to chatting

This walks through a from-scratch setup. Allow 1–2 hours the first time;
most of it is waiting for model weights to download. Skim this once
before starting — knowing what's coming makes the long downloads feel
less ominous.

## Prerequisites

### Hardware

- **AI box** — A Linux machine with an NVIDIA GPU. Tested on:
  - RTX 6000 Ada / Blackwell (≥ 48 GB VRAM)
  - RTX 4090 (24 GB VRAM)

  Friends with a smaller GPU (12–16 GB) will need a smaller model than
  `qwen3-32b`. See **model sizing** below.

- **Home Assistant host** — HAOS on a Pi 5 / HA Yellow / HA Green / x86.
  Anything that runs HACS works.

- **Voice PE (optional)** — Home Assistant Voice Preview Edition. Only
  needed for hands-free wake-word voice. The Home desktop app works
  without one.

- **Network** — Same LAN, or a Tailscale tailnet linking both ends.
  **Don't expose any of these services to the public internet** — they
  ship unauthenticated.

### Software

- Linux on the AI box (Ubuntu 24.04 tested).
- Docker + Docker Compose + the NVIDIA Container Toolkit. Verify with
  `docker run --gpus all --rm nvidia/cuda:12.4.0-base-ubuntu22.04
  nvidia-smi` — the GPU should appear inside the container.
- A Home Assistant install with HACS already added.
- A Windows 10/11 machine for the desktop client (macOS coming later).

### Model sizing

The default unified model is `Qwen/Qwen2.5-VL-32B-Instruct-AWQ`
(~20 GB VRAM, INT4 quantized). It handles text chat, tool calls,
*and* image inputs for camera-aware intents in one process.

| Model                                        | VRAM   | Where it fits | Notes                                        |
|----------------------------------------------|-------:|---------------|----------------------------------------------|
| Qwen/Qwen2.5-VL-32B-Instruct-AWQ (default)   | ~20 GB | RTX 4090 / 6000 | Vision + text + tool calls in one process |
| Qwen/Qwen2.5-VL-7B-Instruct-AWQ              |  ~5 GB | 12 GB+ GPUs   | Smaller; weaker on multi-step intents        |
| Qwen/Qwen2.5-VL-72B-Instruct-AWQ             | ~40 GB | 48 GB+ GPUs   | Flagship; slowest TTFT                       |

To swap, edit the `--model` and `--served-model-name` lines in
`stack/docker-compose.yml`. (Env-var overrides return in v0.2.)

---

## Step 1 — AI box base

1. Install Docker + the NVIDIA Container Toolkit per their respective
   docs. Verify `docker run --gpus all` exposes the GPU.
2. Clone this repo on the AI box at `/opt/home/` (or wherever).
3. Copy `stack/.env.example` to `stack/.env` and set `HF_TOKEN`
   (Hugging Face), `HA_URL`, `HA_TOKEN`. See **Step 4** for the HA
   token.
4. Pull the model — `stack/scripts/stack.sh up` does this on first
   run; weights take 10–30 min on a fast connection.

## Step 2 — Bring up the stack

```bash
cd /opt/home/stack
bash scripts/stack.sh up
```

Idempotent. Re-running on an already-running stack just re-verifies
health. Expect the first run to take longer while images build (vLLM,
Kokoro, Parakeet, metrics sidecar).

Smoke tests run at the end. If anything's red, check
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Step 3 — Wire Home Assistant

### 3a. HACS

If HACS isn't installed yet, follow the [official HACS setup guide](https://hacs.xyz/docs/use/).

### 3b. Extended OpenAI Conversation

In HACS → Integrations → ⋮ → Custom repositories, add:

> `https://github.com/jekalmin/extended_openai_conversation` · *Integration*

Install it, restart HA, then add the integration from Settings →
Devices & Services → Add Integration → Extended OpenAI Conversation.

Configure:
- **Base URL:** `http://<ai-box-ip>:8000/v1`
- **API Key:** any non-empty string (vLLM ignores it)
- **Model:** `qwen2.5-vl-32b` (matches the `--served-model-name`
  in `stack/docker-compose.yml`)
- **Temperature:** 0.4 (lower = more deterministic device actions)
- **Max tokens:** 512

If you previously had this configured for `qwen3.6-27b`, just change
the model name field and reload the integration (Settings → Devices
& Services → Extended OpenAI Conversation → ⋮ → Reload). HA picks up
the new model name without needing a full restart.

The default system prompt covers most home-automation intents.

### 3c. (Optional) Always-on follow-up listening

If you want Voice PE to keep its mic open for ~10 s after every reply
(so you can ask follow-ups without re-wake-wording), apply the
single-line patch documented in [RUNBOOK.md → Always-on
follow-up](RUNBOOK.md#always-on-follow-up).

### 3d. Generate a Long-Lived Access Token

HA → click your name (bottom-left) → **Security** → **Long-Lived
Access Tokens** → **Create Token**.

Copy it somewhere safe — HA only shows it once. The Home desktop app
needs this to drive the WebSocket pipeline.

### 3e. (Optional) Pair Voice PE

Settings → Voice Assistants → Add → pair your Voice PE via Bluetooth.

## Step 4 — Install the desktop app

1. Grab the latest `.msi` from
   [Releases](https://github.com/Engineered-Lighting/home/releases/latest).
2. Double-click. **Windows SmartScreen will warn "Unrecognized app"**
   — that's expected (the .msi isn't code-signed yet). Click `More
   info → Run anyway`.
3. Run through the installer. Default install location is fine.
4. Launch **Home** from the Start menu.
5. On FirstRun, paste:
   - **Home Assistant URL** — `http://<ha-host>:8123`. For Tailscale:
     `http://<tailscale-hostname>:8123` (works identically once both
     ends are on the tailnet).
   - **Long-Lived Access Token** — what you generated in step 3d.
6. Click **Connect ↵**.
7. The app will auto-discover the model on your AI box and prompt you
   to pick one (informational only — HA's agent already has its model
   binding).

## Step 5 — Say hi

Type **"turn off the kitchen lights"** (or whatever fits your house).
You should see:
- A `thinking` line appear within ~200 ms.
- An `action` card (the tool call HA dispatches).
- A streaming `home` reply.
- A jump in the GPU sparkline in the metrics tray.

If you don't have actual lights to control yet, try `"what's the
bedroom temperature?"` or `"tell me a short story about a lamp."` —
both work without device state.

## Where things live

| Where               | What                                  |
|---------------------|---------------------------------------|
| AI box (Linux)      | vLLM, Kokoro, Parakeet, metrics sidecar |
| HA host             | Home Assistant + Extended OpenAI Conv |
| Voice PE            | Wake-word + ambient mic (optional)    |
| Windows machine     | The Home desktop client (this app)    |

## Next steps

- Read [RUNBOOK.md](RUNBOOK.md) — how to operate the stack day-to-day.
- Read [ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit.
- Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — what to do when
  something breaks.
