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

**Live facts and the LA camera pilot need a re-pin.** The camera pilot pins
the vLLM container incarnation in its `private/model-pin.json` (container id,
image digest, StartedAt, `artifact_sha256`). Recreating `hav-vllm`, for the
swap and for a rollback, changes that identity. Camera chat continues after a
re-pin. The live consumer stays closed (`engine_changed`) until
`live-scene.json` and Victoria's `runtime_revision` name the new epoch.
Agree that re-pin with the activity-lighting planning session before the
window. Until it is done, JEV runs on its ambient fallback.

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

- **Resource limits:** 2 CPUs, 4 GB RAM, the pinned image, the existing
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
