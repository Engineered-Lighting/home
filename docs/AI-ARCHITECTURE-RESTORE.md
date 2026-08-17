# RESTORE — bring the house's models back online

Written 2026-08-17 01:11 PDT, before any live change, per the owner's
instruction to "note how to bring them back online later".

**If anything at all is wrong, run section 1. It is the whole rollback and it
does not require understanding what was being tested.**

---

## 1. The one-command rollback

```bash
cd /opt/home-ai-voice
cp -a docker-compose.yml.pre-arch.20260817-0111 docker-compose.yml
docker compose up -d vllm
```

That file is a byte-identical copy of the compose that was live and working at
01:11 PDT on 2026-08-17:

```
sha256  ed9f176f30e3b7a6929087c1d4c32c802e41078b91c4a48afda6f0c05fb0a627
```

Verify the restore took, then wait for health:

```bash
sha256sum /opt/home-ai-voice/docker-compose.yml   # must match the hash above
docker ps --filter name=hav-vllm --format '{{.Status}}'   # want "(healthy)"
```

First start takes **3–6 minutes** (weights load + torch.compile + CUDA graph
capture). `unhealthy` before that is expected, not a failure.

## 2. Remove any experiment containers

Experiments run as separate containers named `arch-*`. They never appear in the
compose, so the rollback above does not touch them. Remove them explicitly:

```bash
docker ps -a --filter name=arch- --format '{{.Names}}' | xargs -r docker rm -f
```

## 3. Clear the maintenance flag

```bash
sudo rm -f /run/ha-maintenance
```

A stale flag silently suppresses paging — nothing looks wrong, and nobody is
told when something is.

## 4. Restart the ambient driver if it was quiesced

```bash
docker start hav-personaplex-bridge
```

## 5. Verify — a real answer, not a health check

A broken conversation agent returns a **well-formed** empty response in 0.01 s.
`healthz` and "containers are up" cannot see it. Compare the LLM traffic
counter before and after:

```bash
BEFORE=$(docker logs hav-metrics-sidecar 2>&1 | grep -c "POST /v1/chat/completions")
python3 - <<'EOF'
import json,pathlib,time,urllib.request
tok=[l.split('=',1)[1].strip().strip('"\'') for l in
     pathlib.Path('/opt/home-ai-voice/.env').read_text(errors='replace').splitlines()
     if l.startswith('HA_TOKEN=')][0]
for utt in ['Tell me a one sentence joke about cats.','Are any lights on right now?']:
    b=json.dumps({'text':utt,'language':'en',
                  'agent_id':'conversation.extended_openai_conversation'}).encode()
    r=urllib.request.Request('http://192.168.0.125:8123/api/conversation/process',
        data=b,headers={'Authorization':f'Bearer {tok}','Content-Type':'application/json'})
    s=time.time()
    d=json.load(urllib.request.urlopen(r,timeout=120))
    sp=d['response']['speech']['plain']['speech']
    print(f'{time.time()-s:5.2f}s  {sp[:160]!r}')
EOF
sleep 3
AFTER=$(docker logs hav-metrics-sidecar 2>&1 | grep -c "POST /v1/chat/completions")
echo "LLM calls delta=$((AFTER-BEFORE))   # must be > 0"
```

**Pass = real sentences AND a non-zero delta.** Empty speech with delta 0 means
HA is holding stale in-memory config; the fix is
`ssh -p 22222 root@homeassistant.local 'ha core restart'` — a
`reload_config_entry` is NOT equivalent and will not work (verified
2026-08-16).

## 6. Known-good reference state

| | |
|---|---|
| served model name (frozen — never change) | `qwen3-vl-30b` |
| model weights | `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` |
| engine image (digest-pinned — never bump casually) | `vllm/vllm-openai@sha256:70a098d90dbab428a001d9e852fc0fc8d67da5beb03e7851a22247653bf35923` = vLLM 0.20.2 |
| `--gpu-memory-utilization` | `0.70` |
| vLLM process VRAM | 68,702 MiB |
| KV pool | 33.59 GiB = 366,880 tokens |
| GPU used / free with everything up | 80,6xx / 16,6xx MiB of 97,887 |
| containers running | 18 |
| voice e2e (quiet house, n=12) | p50 0.87 s, p95 2.48 s, 0 leaks |
| ambient caption p95 at the sidecar | 0.176–0.224 s (budget 1.5 s) |

The served name is frozen so the bridge's `VLLM_MODEL_NAME` needs no rebuild
when weights are swapped. **Swap the `--model` line, never the
`--served-model-name` line.**

## 7. Weights available locally (nothing re-downloads)

All in docker volume `home-ai-voice_hf_cache`:

| model | on disk |
|---|---|
| `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` ← incumbent | 31 G |
| `Qwen/Qwen3.8-27B-FP8` | 29 G |
| `Qwen/Qwen3.6-27B-FP8` | 29 G |
| `Qwen/Qwen2.5-VL-32B-Instruct-AWQ` | 20 G |
| `Qwen/Qwen3-VL-4B-Instruct-FP8` | 5.7 G |

Rollback is always a file copy plus one container recreate — never a download.

## 8. Non-LLM GPU tenants — do not stop these casually

| tenant | MiB | needed for |
|---|---|---|
| chatterbox (`server.py`) | 5,282 | **voice output (TTS)** |
| parakeet (`wyoming_vad_asr_server.py`) | 3,392 | **voice input (STT)** |
| kokoro (`uvicorn`, port 8880) | 1,422 | TTS (secondary) |
| comfyui (`/srv/comfyui`) | 662 | image generation — **not** voice |

Stopping chatterbox or parakeet breaks spoken voice even while the text
conversation API still answers. ComfyUI is the only safe one to reclaim, and
it is only worth 662 MiB.

To restart comfyui if it was stopped:

```bash
sudo systemctl start comfyui    # confirm unit name with: systemctl list-units | grep -i comfy
```

## 9. Never, regardless of what an experiment seems to need

- No writes to `/config/.storage/*` on a running Home Assistant.
- No host image builds (D9 permanent quarantine).
- No repo copies deployed over live metrics-sidecar / EOC component /
  vision-sidecar / intelligence. Live is truth.
- Nothing written under `/srv/home-agent/**`.
- The s2s profile never starts.
- `--served-model-name` stays `qwen3-vl-30b`.
