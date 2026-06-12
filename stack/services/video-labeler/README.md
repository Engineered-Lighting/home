# video-labeler

Backend for the Home-app video timeline labeler (plan: "Home Video Timeline
Labeler + V-JEPA 2.1 Training Pipeline"). **M0 scope**: ingest + media + jobs
+ healthz. No VLM, no embeddings, no training code yet — but the FULL v1
SQLite schema ships now (`videolabeler/migrations/0001_init.sql`) so later
milestones never churn migrations.

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
  ontology.py                canonical axes + ACTIVE_SET + custom:<slug> regex
  models.py                  pydantic API schemas
  api/{videos,media,jobs}.py routers (per-request connections)
  jobqueue/{states,queue,runner}.py
  jobs/{run,import_manual,probe,proxy,sprite}.py
  media/ffmpeg.py            ffmpeg/ffprobe wrappers + progress parsing
tests/                       pytest (ffmpeg-dependent tests skip without it)
```

## ENV (defaults)

| var | default | meaning |
|---|---|---|
| `DATA_DIR` | `/data` | everything lives under here |
| `PORT` | `8099` | uvicorn bind port |
| `INBOX_DIR` | `{DATA_DIR}/inbox` | manual-import scan dir |
| `MIN_FREE_VRAM_GB` | `6` | gpu-lane guardrail (reserved; unused in M0) |
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
