# video-labeler

Backend for the Home-app video timeline labeler (plan: "Home Video Timeline
Labeler + V-JEPA 2.1 Training Pipeline"). Shipped: **M0** ingest + media +
jobs + healthz, **M1** segments-canonical labels, **M2** perception + VLM
prelabel, **M4** V-JEPA 2.1 embeddings + clusters + neighbors. No training
code yet. The FULL v1 SQLite schema shipped at M0
(`videolabeler/migrations/0001_init.sql`) so later milestones barely churn
migrations (M4 added only `embeddings.clip_idx`).

FastAPI + uvicorn + stdlib sqlite3 (WAL). The API process is CPU-only; every
job runs as a `python -m videolabeler.jobs.run --job-id X` subprocess (crash
isolation; later: full VRAM reclaim). Job lanes: cpu (concurrency 2), gpu
(concurrency 1) — gpu is unused in M0.

## Layout

```
main.py                      FastAPI wiring only (routers, lifespan, /healthz)
videolabeler/
  config.py                  ENV-driven config
  db.py                      pragmas, migrations, tx(), wal_size()
  migrations/0001_init.sql   full v1 schema
  migrations/0002_*.sql      M4: embeddings.clip_idx + per-row unique index
  ontology.py                canonical axes + ACTIVE_SET + custom:<slug> regex
  models.py                  pydantic API schemas
  api/{videos,media,jobs,labels,custom_labels,prelabel,embeddings}.py
                             routers (per-request connections)
  jobqueue/{states,queue,runner}.py
  jobs/{run,import_manual,probe,proxy,sprite,windows,perceive,prelabel,
        embed,cluster}.py
  media/ffmpeg.py            ffmpeg/ffprobe wrappers + progress parsing
  media/frames.py            PyAV decode helpers (ALL heavy imports lazy)
  vlm/{client,prompts,schema}.py
                             OpenAI-compatible VLM client + 2-pass prompts
  embeddings/store.py        fp16 .npy shard store (stdlib-only, np-compatible)
  embeddings/backbone.py     V-JEPA encoder loader (weights downloaded once)
tests/                       pytest (ffmpeg-dependent tests skip without it)
```

## ENV (defaults)

| var | default | meaning |
|---|---|---|
| `DATA_DIR` | `/data` | everything lives under here |
| `PORT` | `8099` | uvicorn bind port |
| `INBOX_DIR` | `{DATA_DIR}/inbox` | manual-import scan dir |
| `MIN_FREE_VRAM_GB` | `6` | gpu-lane guardrail (reserved; unused in M0) |
| `VLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible chat endpoint (M2) |
| `VLM_MODEL` | `qwen2.5vl:32b` | part of the prelabel idempotency key |
| `VLM_API_KEY` | (empty) | bearer token when the server wants one |
| `VLM_TIMEOUT_S` | `180` | per chat-completions call |
| `VLM_KEEP_ALIVE` | `10m` | passed through to Ollama (keep_alive) only |
| `VJEPA_MODEL_NAME` | `vjepa2_1_vit_base_384` | M4 backbone (torch.hub entrypoint name) |
| `VJEPA_WEIGHTS_PATH` | (empty) | explicit local checkpoint; disables download AND fallback |
| `VJEPA_WEIGHTS_URL` | (empty) | overrides the dl.fbaipublicfiles.com bucket url |
| `LOG_LEVEL` | `INFO` | |

## Data layout (under DATA_DIR)

```
videolabeler.db            sqlite (WAL)
inbox/                     drop *.mp4/*.mkv/*.mov/*.webm (+ optional
                           '<filename>.meta.json' sidecar with e.g.
                           {"drive_file_id": "...", "is_holdout": true})
videos/originals/{vid}/    originals moved out of the inbox (id = vid_<sha256[:16]>)
proxies/{vid}.mp4          rotation-normalized 480p h264 +faststart
thumbs/{vid}/sheet_{n}.jpg sprite sheets (160x90 tiles, 10 cols, <=100/sheet)
models/yolo11n-pose.pt     downloaded once by the perceive job (M2)
keyframes/{vid}/{wid}/     frame_{k}.jpg (<=768px) + crop_{k}.jpg (<=512px)
                           + meta.json -- VLM input AND the M3 evidence strip
prelabels/{vid}/{wid}.json sidecar: raw pass-1/pass-2 text + disagreement
models/backbone/*.pt       V-JEPA checkpoint, downloaded ONCE by the embed job
models/torchhub/           torch.hub cache of the facebookresearch/vjepa2 repo
embeddings/<model>/        fp16 .npy shards (<=2048 rows; sqlite rows point
                           at shard_path+row_index)
```

## API (M0 contract — frozen; the frontend builds against this)

- `GET /healthz` -> `{ok, db, jobs_running, gpu_free_gb, disk_free_gb, wal_size, gpu_exclusive, uptime_s}`
- `POST /api/video-labeler/import/manual` `{batch_name?}` -> `{job}` — scans the
  inbox, dedupes on sha256, moves files into originals, fans out probe ->
  proxy + sprite jobs. Re-POSTing a finished batch retries (re-scans) it.
- `GET /api/video-labeler/videos` -> `{videos: [...]}` (artifact presence flags)
- `GET|DELETE /api/video-labeler/videos/{id}` (DELETE is soft:
  `import_status='deleted'`; artifacts are GC'd by a later cleanup pass)
- `GET /api/video-labeler/videos/{id}/stream?original=0` — proxy mp4 with
  hand-rolled single-range 206 support (`Range: bytes=a-b`, open-ended,
  suffix; 416 past EOF; 200 full without Range)
- `GET /api/video-labeler/videos/{id}/sprite` -> manifest
  `{tile_w, tile_h, cols, interval_s, count, sheets: [urls]}`
- `GET /api/video-labeler/videos/{id}/sprite/{n}` -> image/jpeg
- `GET /api/video-labeler/jobs?state=&type=` (newest first, limit 200),
  `GET /jobs/{id}`, `POST /jobs/{id}/cancel` (cooperative for running jobs),
  `POST /jobs/{id}/retry` (terminal jobs only; checkpoint preserved)

M2 perception + prelabel:

- probe -> windows -> **perceive** auto-chain fills person_presence /
  motion_energy / pose_summary_json per analysis window; zero-person windows
  get merged `no_person` rule suggestions (no VLM).
- `POST /api/video-labeler/prelabel` `{video_ids?: [...] | all_pending: true}`
  -> enqueues perceive (when pose summaries are missing, with the prelabel
  job chained at its tail) or the gpu-lane prelabel job directly. Holdout
  videos are always skipped. The prelabel job fails fast with
  `insufficient_vram` below 22 GB free (the gpu-exclusive eviction module
  orchestrates freeing -- this service never evicts anything itself).
- `GET /api/video-labeler/videos/{id}/windows/{window_id}/keyframes` ->
  `{keyframes: [{t, frame, crop}]}` urls (+ `/{name}` serves the JPEGs)
- `GET /api/video-labeler/calibration?axis=` -> per-axis acceptance curves
  + `bulk_ok` (axis-pooled Wilson lower bound >= 0.95 over deciles 8-9)

M4 embeddings + clusters + neighbors:

- `POST /api/video-labeler/embed` `{video_ids?: [...] | all_pending: true,
  model?: personcrop_v1|wholeframe_v1}` -> gpu-lane embed jobs (HOLDOUT
  videos INCLUDED -- embeddings are not labels). Per window: 3x 16-frame
  clips @~7.5 effective fps from the ORIGINAL at exact PyAV frame indices,
  **person-crop primary** (1.8x square around pose median_bbox, whole-frame
  short-side-384 center-crop as the no-person fallback and the
  `wholeframe_v1` secondary variant); per-clip mean-pooled vectors +
  the window mean land in fp16 .npy shards + sqlite index rows
  (`model_id = vjepa2_1_vit_base_384@personcrop_v1`). Fails fast below
  4 GB free VRAM; ViT-B runs WITHOUT a gpu-exclusive window and never
  evicts; CUDA OOM halves the window batch down to 1. Cache keys
  (sha1 over model|checksum|frames|crop|preproc) make re-runs/resume skip
  finished windows.
- `POST /api/video-labeler/cluster` `{model?}` -> L2-norm -> PCA-64 ->
  MiniBatchKMeans (k=clamp(sqrt(n),20,200)) over NON-HOLDOUT pooled_window
  vectors; members carry distance-to-centroid + an is_outlier flag (top 5%).
- `GET /api/video-labeler/segments/{id}/neighbors?k=8` -> embed-space
  nearest REVIEWED non-holdout windows (cosine), shaped for the M3 evidence
  panel: `{window_id, video_id, t_center, distance, labels: {activity,
  posture}, keyframes}`. Same-video candidates excluded by default
  (`?exclude_same_video=0` re-admits all but the query segment's footprint).
- `GET /api/video-labeler/videos/{id}/window-signals` -> per-window
  `{cluster_id, cluster_dist, is_outlier}` from the latest ready cluster
  run -- the review-queue triage hook (cluster outliers rank for review).
- The V-JEPA 2.1 weights download ONCE from the verified
  `https://dl.fbaipublicfiles.com/vjepa2/vjepa2_1_vitb_dist_vitG_384.pt`
  (the repo's own hubconf points at localhost and cannot fetch); if that
  fails the loader falls back LOUDLY to the non-2.1 `vjepa2_vit_large`
  @256px and stamps `model_id` accordingly -- never a silent substitute.

Media endpoints are UNAUTHENTICATED by design (LAN-only) — that is what lets
`<video>` elements hit stream URLs directly.

## Job queue semantics

- `enqueue(idempotency_key)` returns the existing job if the key exists.
- Heartbeat every <=15s (side thread in the worker); the runner requeues
  running jobs with a stale heartbeat (>90s) while `attempts < max_attempts`,
  preserving `checkpoint_json` for resume; afterwards they fail.
- Cancel: queued jobs die immediately; running jobs get `cancel_requested=1`
  and finalize at their next heartbeat/checkpoint.
- ffmpeg outputs are written as `.part` + atomic rename.

## Tests (Windows-friendly)

```
py -3 -m pip install -r requirements.txt pytest httpx
py -3 -m pytest tests -q       # run from stack/services/video-labeler
```

`httpx` is needed only by the Range tests (starlette TestClient); they skip
without it. ffmpeg-dependent tests (`test_import_pipeline.py`) skip cleanly
when ffmpeg/ffprobe are not on PATH. Do not create a `.venv` inside this
directory before deploying (the deploy script scp's the whole tree).

## Deploy

```
powershell -File tools\deploy-video-labeler.ps1   # from the repo root
```

4 stages: scp -> docker build -> standalone `docker run --network host
--gpus all` (NOT part of the compose stack; see compose-snippet.yml) ->
healthz verify loop. Port 8099. Data volume:
`/opt/home-ai-voice/video-labeler-data:/data`. docker.sock is mounted for the
M2 GPU-eviction/deadman machinery (unused in M0).

Rollback: `ssh hav-ubuntu 'docker rm -f hav-video-labeler'`

M0 import runbook: scp the corpus MP4s into
`/opt/home-ai-voice/video-labeler-data/inbox/` on hav-ubuntu, then
`POST /api/video-labeler/import/manual`.
