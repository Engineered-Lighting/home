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

#### Frigate Integration Version Before Home Assistant Core Upgrades

Home Assistant 2026.9 deprecated the `via_device` device-registry parameter
(core PR #178465). The Frigate custom integration (HACS,
`blakeblackshear/frigate-hass-integration`) must be **5.15.5 or newer, and
5.15.6 is preferred**, before a home runs HA 2026.9 or later:

- 5.15.4 and older crash on HA 2026.9. Only the first camera in Frigate's
  config order loads; every other `camera.*` entity fails with
  `RuntimeError: Detected code that calls device_registry.async_get_or_create
  with a deprecated via_device parameter` and `Error adding entity None for
  domain camera with platform frigate`. HA `camera_proxy` then returns 404 for
  those cameras, which breaks the Home camera carousel, the vision sidecar
  `camera_proxy` paths, and `multimodal.py`. Other Frigate entities only log a
  `homeassistant.helpers.frame` warning.
- 5.15.5 fixes the crash (integration PR #1116) but still logs an
  `async_get_device` deprecation warning. 5.15.6 fixes that too (PR #1117).
- The 5.15.5 release still reports `5.15.4` in its manifest. To tell which code
  is installed, check whether
  `custom_components/frigate/__init__.py` defines `get_frigate_via_device`.

Update path: take a Home Assistant backup, update Frigate in HACS, run
`ha core check`, then restart core in a window coordinated with the sessions
that depend on HA. The Frigate add-on does not restart, so go2rtc RTSP on 8554
and the Frigate API on 5000 stay up. Verify with `ha core logs` (no
`via_device` or `Error adding entity` lines) and a 200 from `camera_proxy` for
a camera that is not first in Frigate's config.

Upgrade the Frigate server add-on separately from the integration. Frigate
0.18 migrates the config and `frigate.db` and restarts go2rtc, and the Perception
Lab observer reads Frigate occupancy and RTSP directly, so coordinate that
window with Perception Lab first.

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
