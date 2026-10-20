# vLLM swap: Qwen3-VL-30B → Qwen3.6-35B-A3B

This is the procedure for replacing the model served by `hav-vllm` on the LA AI
host, EngineeredLightingServer1 (`hav-ubuntu`), and for rolling it back. It
follows the [stack-upgrade gates](STACK-UPGRADE-INVESTIGATION.md). The owner
approves each host step separately.

## What changes

| | Today | Trial |
| --- | --- | --- |
| Model | `Qwen/Qwen3-VL-30B-A3B-Instruct-FP8` (unpinned revision) | `Qwen/Qwen3.6-35B-A3B-FP8` @ `95a723d08a9490559dae23d0cff1d9466213d989` (Apache-2.0, 37.5 GB) |
| Served names | `qwen3-vl-30b` | `qwen3-vl-30b` (first, so clients are unchanged) and `qwen3.6-35b-a3b` |
| Tool-call parser | `hermes` | `qwen3_coder` |
| Reasoning | thinking off | thinking off, plus `--reasoning-parser qwen3` |
| Multimodal limit | `{"image": 8, "video": 0}` | `{"image": 8, "video": 1}` |
| `--block-size` | 32 | unset (vLLM sizes it for the hybrid DeltaNet layers) |
| `/tmp` tmpfs | 2g | 4g (more Triton kernels) |
| Unchanged | image digest (vLLM 0.20.2, cu130), max-model-len 32768, GPU fraction 0.50, max-num-seqs 4, prefix caching, chunked prefill | |

The trial is a compose override,
[`stack/vllm/qwen3.6-35b-a3b.override.yml`](../stack/vllm/qwen3.6-35b-a3b.override.yml),
layered on the host's unchanged base file. Everything not named in the
override is inherited from the base `vllm` service: image, volumes, the
read-only rootfs policy and the healthcheck. The old model's files stay in
`hf_cache`.

### Host facts (P0 inventory, 2026-10-20)

- **Compose project:** `home-ai-voice`, from `/opt/home-ai-voice/docker-compose.yml`.
  That directory is not a git checkout; its `vllm` block is pinned by digest
  and runs hardened (read-only rootfs, `cap_drop: ALL`, JIT caches on tmpfs).
- **Running settings:** `--gpu-memory-utilization 0.50`, not the 0.70 the
  repo used to say. vLLM holds about 50 GB with about 33 GB free; no Moshi
  listener is resident.
- **Volumes:** `hav_hf_cache` is 147 GB, and `/` has 180 GB free.
- **Image:** vLLM 0.20.2, transformers 5.8, torch 2.11 + cu130, `cv2` present
  (video decode), `hf` CLI 1.14.

## Who uses the endpoint

All of these ask for `qwen3-vl-30b`, so none needs a change for the swap:

- the HA voice agent (`BRIDGE_VLLM_MODEL`);
- the vision sidecar and `/look` (`VISION_MODEL`);
- intelligence (`INTELLIGENCE_QWEN_MODEL`);
- the supervisor ready check (`EXPECTED_VLLM_MODEL`, compared with
  `/v1/models` `data[0]`);
- the LA latency probes;
- the Perception Lab live facts (`qwen_scene.py`, on the Den backend branch).
  These reject any reply whose `model` field differs.
- the V-JEPA live scene memory (`vjepa-live-prototype.service`, a user unit
  on LA). It counts tokens with a local Qwen3-VL tokenizer, so its context
  budgets are approximate on Qwen3.6.

**Live facts and the LA camera pilot need a re-pin.** The camera pilot pins
the vLLM container incarnation in its `private/model-pin.json` (container id,
image digest, StartedAt, `artifact_sha256`). Recreating `hav-vllm`, for the
swap and for a rollback, changes that identity. Camera chat continues after a
re-pin. The live consumer stays closed (`engine_changed`) until
`live-scene.json` and Victoria's `runtime_revision` name the new epoch.
The re-pin is part of the window (procedure below). Until it is done, the
always-on activity loop gets no Qwen facts and JEV runs on its ambient
fallback.

### Re-pin method (from the 2026-09-27 live-scene provisioning)

| Field | Where | Value |
| --- | --- | --- |
| `artifact_sha256` | LA `private/model-pin.json`, LA `private/live-scene.json`, Victoria `managed-live.json` `qwen_live` | The **vLLM image digest**, without the `sha256:` prefix (`70a098d9…`). It is not a model hash. The swap keeps the image, so the value **does not change**, and nobody hand-edits it. |
| `engine_epoch` | pin (written by auto re-pin), `live-scene.json` | `hav-vllm-<container id[:12]>-<StartedAt without - and :>`, logged by the pilot as `engine re-pinned ... engine_epoch=<new>` |
| `runtime_revision` | Victoria `managed-live.json` `la_live_perceiver` (the active path) and `qwen_live` (disabled; kept equal) | `vllm-0.20.2-<engine_epoch>` |

The ledger label `MODEL_REVISION` (`qwen3-vl-30b-f579e204-20260920`) is
deliberately frozen: it is a label, not the engine identity. Since the alias
`qwen3-vl-30b` now serves Qwen3.6, neither field tells the models apart; the
compose override's pinned `--revision` is the record of which model ran.

The swap keeps the container name (`hav-vllm`) and the image digest. The
deployed `auto_repin: true` therefore accepts the new incarnation on its own,
and camera chat continues. To reopen live facts (procedure from
`LA-SPEED-OPERATIONS-RUNBOOK.md`, "Re-pin after a vLLM restart"):

1. **Before the window, back up** these files:
   - On LA: `~/home-camera-pilot/private/{model-pin,live-scene,pilot-runtime}.json`.
   - On Victoria: `~/perception-lab/private/managed-live.json`.

   Save each as `*.before-qwen36-<UTC>`.
2. **After vLLM is healthy, read the new epoch** on LA:

   ```sh
   journalctl --user -u victoria-camera-pilot --since -15min | grep -E 'engine re-pinned|refusing'
   ```

3. **Update the two files:**
   - LA `live-scene.json`: set `engine_epoch` to that value.
   - Victoria `managed-live.json`: set `la_live_perceiver.runtime_revision`
     (and the disabled `qwen_live.runtime_revision`) to `vllm-0.20.2-<engine_epoch>`.

   If the pilot cannot re-pin by itself (for example its health check is
   failing), hand-edit `model-pin.json` instead: `container_id`, `started_at`
   and `engine_epoch` from `docker inspect hav-vllm`, with the epoch from
   `home_shared.model_pin.repinned_epoch`.

   Leave `artifact_sha256` unchanged everywhere.
4. **Check before restarting** (it runs every check, never re-pins and never
   sends to the model). Run it on LA, from the release directory, as the
   service user:

   ```sh
   python3 -c 'from home_shared.camera_pilot_service import model_health; model_health()'
   ```

5. **Restart at idle:**
   - LA: `systemctl --user restart victoria-camera-pilot.service`. If it
     exits with `Address already in use` on :9446, the old connection is still
     in TIME-WAIT; wait until `ss -tan | grep ':9446 '` is empty (under 60 s),
     then start it.
   - Victoria: `systemctl --user restart perception-lab-managed.service`, with no recording running.
6. **Verify:**
   - the LA log shows `live scene consumer enabled: ... epoch=<new>`;
   - live facts flow again at the 4 s cadence.

A rollback recreates the container too. Repeat steps 2–6 with the rollback
container's new epoch. Restore the `.before-qwen36` copies only if an edit
itself went wrong.

The live facts' hard-coded `model_version`
(`Qwen/Qwen3-VL-30B-A3B-Instruct-FP8`) will mislabel records after the swap.
That fix belongs with the Den backend code.

## Steps

Each command is for Windows PowerShell 5.1 on the workstation, run from the
repo root. Before every step, run the preflight. It is read-only, and it
stops on any failed unit other than the known `observer-recover.service`, on
new kernel faults, heat, low disk or unhealthy containers:

```powershell
Get-Content stack\vllm\swap-preflight.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s"
```

### 1. Baseline (production stays up, a few seconds of light load)

```powershell
Get-Content tools\vllm-swap-smoke.py -Raw | ssh hav-ubuntu "tr -d '\r' | docker exec -i hav-vllm python3 - --runs 5 --video-optional"
```

### 2. Download (production stays up; network and disk only)

```powershell
Get-Content stack\vllm\qwen36-download.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- start"
Get-Content stack\vllm\qwen36-download.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- status"
Get-Content stack\vllm\qwen36-download.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- verify"
Get-Content stack\vllm\qwen36-download.sh -Raw | ssh hav-ubuntu "tr -d '\r' | bash -s -- cleanup"
```

- **Resource limits:** 2 CPUs, 8 GB RAM, 2 parallel files, the pinned image, the existing
  volume, and a disk floor of 60 GB.
- **Interrupted?** Re-run `start`; it resumes.
- **Verify:** every LFS blob is checked against its sha256 content address,
  and the snapshot must hold 56 files.

Then copy the override next to the host compose file and check the merged
config. Neither step changes the running container:

```powershell
ssh hav-ubuntu "mkdir -p /opt/home-ai-voice/vllm"
scp stack\vllm\qwen3.6-35b-a3b.override.yml hav-ubuntu:/opt/home-ai-voice/vllm/
ssh hav-ubuntu "cd /opt/home-ai-voice && docker compose -f docker-compose.yml -f vllm/qwen3.6-35b-a3b.override.yml config vllm | grep -A40 'command:'"
```

### 3. Maintenance window (owner-chosen, not while travelling)

- **What goes offline:** the voice agent, `/look`, camera chat and the live
  Qwen facts. Den lighting falls back to ambient.
- **How long:** about 10–15 min if it goes ahead, plus about 5 min for a
  rollback. Book 60 min and abort at 90.
- **Live facts** stay on ambient until the re-pin is done, even after vLLM is
  back.
- **Start condition:** the preflight passes with a 72 °C ceiling:
  `... | bash -s -- 72`.

1. Record the pre-swap state: the preflight output (container id, StartedAt,
   image).
2. Start the trial:

   ```powershell
   ssh hav-ubuntu "cd /opt/home-ai-voice && docker compose -f docker-compose.yml -f vllm/qwen3.6-35b-a3b.override.yml up -d --no-deps vllm"
   ```

3. Watch the load until healthy (first start compiles kernels):

   ```powershell
   ssh hav-ubuntu "docker logs -f --since 1m hav-vllm 2>&1 | grep -iE 'error|KV cache|maximum concurrency|Application startup|loading took'"
   ```

   - If startup reports too little KV cache, rerun step 2 with
     `VLLM_GPU_MEMORY_UTILIZATION=0.55 docker compose ...` (still about 20 GB
     free).
   - On a prefix-cache error, remove `--enable-prefix-caching` from the
     override.
4. Smoke and latency, with video required:

   ```powershell
   Get-Content tools\vllm-swap-smoke.py -Raw | ssh hav-ubuntu "tr -d '\r' | docker exec -i hav-vllm python3 - --runs 5"
   ```

5. Real clients:
   - a read-only voice question ("what lights are on?");
   - an abstract `/look` ("what do you see in the living room?");
   - one `/reason` annotated image. Qwen3.6 grounding boxes must still draw.
   - Then STOP and Travel Mode per the promotion gates.
6. Live facts, once re-pinned: a few `lab-live-scene-1` replies valid, at the
   same 4 s cadence.
7. VRAM: `ssh hav-ubuntu "nvidia-smi --query-gpu=memory.used,memory.free --format=csv"`.
   It must leave at least 20 GB free.
8. **Go** only if the smoke run passes and all of steps 5–7 hold, with p95
   latency within about 20% of the baseline unless you explicitly accept
   more. Otherwise roll back.

**After a go:** make the override survive a plain `docker compose up -d` of
the whole project, which would otherwise recreate `vllm` from the base file:

```powershell
ssh hav-ubuntu "cd /opt/home-ai-voice && grep -q '^COMPOSE_FILE=' .env || echo 'COMPOSE_FILE=docker-compose.yml:vllm/qwen3.6-35b-a3b.override.yml' >> .env"
```

### Rollback (one command)

```powershell
ssh hav-ubuntu "cd /opt/home-ai-voice && sed -i '/^COMPOSE_FILE=docker-compose.yml:vllm\/qwen3.6/d' .env; docker compose -f docker-compose.yml up -d --no-deps vllm"
```

- **What it does:** it recreates `hav-vllm` from the untouched base file. That
  restores the Qwen3-VL-30B weights already in `hf_cache` and the compiled
  cache in `vllm_cache`, in about 5 min. The `sed` is a no-op unless the
  override was made persistent.
- **Afterwards:** the camera pilot and live consumer need the same re-pin as
  after the swap.
- **Downloaded files:** remove them only once Qwen3.6 is abandoned:

  ```powershell
  ssh hav-ubuntu "docker run --rm --network none -v hav_hf_cache:/hf --entrypoint rm vllm/vllm-openai@sha256:70a098d90dbab428a001d9e852fc0fc8d67da5beb03e7851a22247653bf35923 -rf /hf/hub/models--Qwen--Qwen3.6-35B-A3B-FP8"
  ```

## Video input

The server accepts one `video_url` item per request after the swap. Image-only
clients are unaffected.

- **Sampling:** set it per request with `mm_processor_kwargs: {"fps": 1}`.
  Qwen3.6 defaults to 2 fps.
- **Live facts:** they still send 2–4 stills. Moving them to a causal 15 s,
  1 fps clip is a prompt and output-shape change (a new `PROMPT_VERSION` and
  consumer policy digest). It lands with the Den backend after the benchmark
  in the den activity-lighting plan, not in this swap.

## Hazards found in the 2026-10-20 run

- **Any kernel OOM line trips two fail-closed guards**, even one confined to
  a throwaway container's memory cgroup:
  - The V-JEPA live observer stops and its preflight refuses restarts
    (`new_kernel_faults_require_inspection`). Clear it with an owner-approved
    `kernel-fault-inspection-<date>.json` receipt and by moving
    `last-preflight.json` `at` past the fault, as on 2026-09-29.
  - The LA camera pilot rejects every request: camera chat and live facts.
    Its scan starts from `_KERNEL_BASELINE`, hard-coded in
    `camera_pilot_service.py`, and a restart re-scans from there. The only fix
    is a pilot release that moves that baseline past the inspected fault.

  So cap downloads generously: the first run, with 8 workers under 4g, was
  OOM-killed. The script now uses 2 workers under 8g.
- **The V-JEPA observer needs 150 GiB free on `/`** (and 100 GiB on
  `/srv/data`) to start. The 35 GB download took `/` under that. The
  unused `Qwen/Qwen3.6-27B-FP8` cache was parked at `/srv/data/hf-parked/`
  to make room; move it back into `hav_hf_cache` if it is ever needed.

## Result (2026-10-20)

**Go.** Qwen3.6 has served since 19:39 PDT. The override is persistent
through `COMPOSE_FILE` in the host `.env`.

| | Qwen3-VL-30B | Qwen3.6-35B-A3B |
| --- | --- | --- |
| Smoke, warm p50 / p95: text TTFT | 0.021 / 0.51* s | 0.042 / 0.044 s |
| Tool call (10/10 correct) | 0.120 / 0.126 s | 0.155 / 0.157 s |
| Image | 0.029 / 0.044 s | 0.051 / 0.054 s |
| Live-facts-shaped JSON | 0.604 / 0.955 s | 0.504 / 0.507 s |
| Video, 15 s at 1 fps (1,861 prompt tokens) | rejected (`video: 0`) | 0.054 / 0.055 s, 10/10 |
| Live facts end to end, p50 / p95 | 1.16 / 1.95 s (1288/1289 valid) | 1.25 / 1.97 s (21/21 valid) |
| vLLM VRAM / GPU free | 50.0 / 33.3 GB | 48.0 / 35.4 GB |
| Cold start | | 3 min (weights 34.2 GiB, KV 10.25 GiB = 438k tokens) |

\* The baseline p95 included a cold first request.

- **Owner checks passed:** voice, STOP and Travel Mode.
- **Sidecar checks passed:** `/look` (274 ms) and `/reason`. Qwen3.6 keeps
  the `<ref>…</ref><box>…</box>` grounding format.
- **Short requests are 20–35 ms slower.** vLLM warns that it has no tuned
  fused-MoE config for this GPU and model.
