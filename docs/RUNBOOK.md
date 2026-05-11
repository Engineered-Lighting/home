# Runbook

Day-to-day operating procedures for the home stack. Read top-to-bottom
the first time; use the table of contents to jump to a specific recipe
afterward.

## Stack operation

### Bring up the stack
```bash
cd /opt/home/stack
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
.\scripts\stack.ps1            # default: up
.\scripts\stack.ps1 up
.\scripts\stack.ps1 down
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs vllm
```
It just SSHes into the AI box (via the `hav-ubuntu` alias in
`~/.ssh/config`) and runs `scripts/stack.sh`.

## What runs where

| Service           | Port  | Container               | Purpose                          |
|-------------------|------:|-------------------------|----------------------------------|
| vllm              |  8000 | hav-vllm                | OpenAI-compatible LLM            |
| wyoming-parakeet  | 10300 | hav-wyoming-parakeet    | STT (Wyoming protocol)           |
| kokoro-tts        |  8880 | hav-kokoro-tts          | TTS service                      |
| wyoming-kokoro    | 10301 | hav-wyoming-kokoro      | wyoming_openai → Kokoro bridge   |
| vision-sidecar    |  8091 | hav-vision-sidecar      | Camera-image description         |
| metrics-sidecar   |  8092 | hav-metrics-sidecar     | Telemetry for the Home desktop   |

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
