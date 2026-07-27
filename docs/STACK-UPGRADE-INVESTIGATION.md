# Stack Upgrade Investigation

This is the safe process for evaluating new releases across the Home app stack:
models, vLLM, STT/TTS, Frigate, Home Assistant, CUDA/PyTorch, and frontend
runtime packages.

## Production Rule

Do not make disruptive production upgrades while traveling unless recovering a
blocker. Production Home control remains local/Tailscale-only, and novelty is
not a reason to change a working stack.

## Current Baseline To Verify

Before any upgrade decision, run:

```powershell
npm run stack:inventory
npm run stack:upgrade:test
```

The inventory is read-only. It parses repo config and flags mismatches such as
compose comments that disagree with the actual model command.

Known baseline from the repo:

- vLLM command serves `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` as `qwen3-vl-30b`.
- STT is Wyoming Parakeet.
- Chatterbox is the primary TTS engine, with Kokoro retained as fallback.
- CUDA/PyTorch images are custom-built for Blackwell compatibility.

## Investigation Tracks

Use `tools/stack-upgrade-candidates.json` as the source of truth for candidates,
promotion gates, and rollback expectations.

### Inventory First

Capture:

- vLLM image/version, model ID, served name, context length, quantization, and
  tool parser.
- STT/TTS engines, model IDs, voices, and fallback paths.
- Frigate, Home Assistant, Extended OpenAI Conversation, and AI Task support.
- NVIDIA driver, CUDA, PyTorch, torchaudio, transformers, and vLLM support
  packages.
- Frontend runtime versions.

### AI Model Candidates

Keep Qwen3-VL 30B FP8 as production until a candidate passes all gates.

Evaluate split-model architecture as a first-class outcome:

- fast text/tool model for HA control and chat
- Qwen3-VL for `/look`, segmentation, camera reasoning, and apartment visuals
- Parakeet for STT unless beaten
- Chatterbox primary plus Kokoro fallback unless beaten

### Voice Candidates

STT challengers must beat Parakeet on local names, noisy-room commands, false
command resistance, and latency. TTS challengers must preserve interruption,
fallback, and VRAM headroom.

### Frigate And Home Assistant

Frigate and Home Assistant changes require backups first:

- Frigate config and database
- Home Assistant snapshot
- custom Extended OpenAI Conversation integration backup

Pilot semantic triggers, review MQTT events, indoor-only face recognition,
driveway LPR, and AI Tasks as enrichment. Do not move safety-critical control
into experimental AI features.

### Runtime And Frontend

CUDA/PyTorch/vLLM runtime modernization is a separate maintenance-window-only
project. Do not combine it with model changes.

Frontend major upgrades are lower priority and must pass mobile/desktop
screenshot audits, apartment 3D checks, and interaction tests before deployment.

## Required Promotion Gates

- No duplicated assistant messages across repeated scenario runs.
- STOP clears automatically after the final response.
- Travel Mode blocks all light-on/write attempts.
- Abstract prompts like "what do you see in my apartment?" invoke `/look`.
- Tool calls produce one natural final answer.
- Vision quality is equal or better than the Qwen3-VL baseline.
- p95 latency improves or stays within an explicitly accepted bound.
- VRAM headroom remains safe under normal stack load.
- Rollback commands exist for the specific subsystem.

## Rollback Checklist

- vLLM/model: previous model ID, served model name, compose/env values, and
  image tag.
- STT/TTS: previous Wyoming pipeline, model, voice, and upstream URL.
- Frigate: config backup, database backup, and previous add-on/container
  version.
- Home Assistant: snapshot, custom integration backup, and known-good
  automation config.
- Frontend/web: previous Git commit and deployed gateway version.
- CUDA/PyTorch: previous driver/toolkit/container image and recovery steps.

## Scenario Suites

Run deterministic tests first:

```powershell
npm run stack:upgrade:test
npm run llm:test:deterministic
npm run test:natural-look
npm run llm:test:ui
```

Run live tests only from trusted machines and only when the stack is reachable:

```powershell
npm run llm:test:read-only
npm run llm:test:travel-mode
```

Do not run live write-gated tests against arbitrary PR code.
