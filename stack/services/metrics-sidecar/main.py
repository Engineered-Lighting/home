"""Home sidecar.

Two responsibilities:

1. Telemetry — GPU util / VRAM (NVML), CPU / RAM (psutil), and vLLM
   throughput / TTFT (scraped from vLLM's Prometheus) — exposed at
   GET /metrics for the Home desktop client to poll.

2. Transparent vLLM proxy — forwards OpenAI-compatible API traffic without
   retaining, parsing, broadcasting, or persisting conversation content.

Designed to be tiny — single file, no DB, no auth. Lives behind the
private LAN/Tailnet (same trust boundary as the rest of the stack).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncGenerator, Optional

import httpx
import psutil
import pynvml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("metrics-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://vllm:8000/metrics")
VLLM_UPSTREAM    = os.environ.get("VLLM_UPSTREAM_URL", "http://vllm:8000")
SCRAPE_INTERVAL_S = float(os.environ.get("SCRAPE_INTERVAL_S", "2.0"))
PROXY_TIMEOUT_S = float(os.environ.get("PROXY_TIMEOUT_S", "300.0"))
# Greenfield containment lock. A stale host .env must not restore the old
# unauthenticated conversation tee. Reintroduction requires a separate,
# authenticated design rather than a production environment toggle.
CONVERSATION_CONTENT_ENABLED = False

app = FastAPI(title="Home sidecar", version="0.3.0")  # bump for M0 schema additions
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# NVML — single handle for GPU 0.
pynvml.nvmlInit()
_gpu = pynvml.nvmlDeviceGetHandleByIndex(0)
_gpu_name = pynvml.nvmlDeviceGetName(_gpu)
if isinstance(_gpu_name, bytes):
    _gpu_name = _gpu_name.decode("utf-8", "replace")
log.info("metrics-sidecar bound to GPU 0 (%s)", _gpu_name)

# Prime cpu_percent so the first /metrics call returns a real number
# rather than 0.0 — psutil needs one prior sample to compute deltas.
psutil.cpu_percent(interval=None)

# Rolling window for tps: keep the last two scrape samples and compute
# tokens/second from the delta.
_last_vllm: dict = {"ttft_ms": None, "tps": None, "model": None}
_prev_sample: Optional[dict] = None

# ── Rolling resource sampler (Addendum 29 follow-up: GPU spikes were
# disappearing because nvmlDeviceGetUtilizationRates is a ~1s windowed
# average, and a 200-500ms LLM call lands between samples returning 0.
# Same shape for psutil.cpu_percent(interval=None) which returns 0 when
# called twice in quick succession from different request handlers.
#
# Fix: a thread (NVML is C, doesn't play nicely with asyncio) polls
# every 100ms and keeps a rolling 2s window. /metrics returns the MAX
# over the window so brief spikes survive.  Same window for CPU. This
# is the only correct way to characterize "is the GPU under load right
# now?" when sampling is sparser than the bursts you're measuring. ──
import threading

_RESOURCE_SAMPLE_INTERVAL_S = 0.1     # 100ms — fine enough for any LLM burst
_RESOURCE_WINDOW_S = 2.0              # rolling window: report MAX over last 2s
_RESOURCE_BUF_MAX = int(_RESOURCE_WINDOW_S / _RESOURCE_SAMPLE_INTERVAL_S) + 4

_resource_lock = threading.Lock()
_resource_samples: list[dict] = []  # list of {t, gpu, cpu}
_resource_seq = 0
_resource_boot_id = f"{time.time():.6f}"


def _resource_sampler_loop() -> None:
    """Background thread: poll NVML + psutil every 100ms, keep last 2s.

    cpu_max_core is the load on the BUSIEST core (not the system-wide
    average). On a 32-core box, a single-threaded Python burst at 100%
    shows as system_avg=3% but max_core=100%. Reporting only system_avg
    hides single-threaded work — a vision-sidecar call appears flat
    when really one core IS pegged. We track both and surface the
    busiest-core metric to the line graph because that's the one that
    answers "is anything actually busy right now?"."""
    global _resource_seq
    # First psutil call returns 0; warm both interfaces up.
    psutil.cpu_percent(interval=None, percpu=False)
    psutil.cpu_percent(interval=None, percpu=True)
    time.sleep(_RESOURCE_SAMPLE_INTERVAL_S)
    while True:
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
            mem = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
            vm = psutil.virtual_memory()
            try:
                gpu_temp_c = int(
                    pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU)
                )
            except Exception:
                gpu_temp_c = None
            cpu_avg = psutil.cpu_percent(interval=None, percpu=False)
            cpu_per_core = psutil.cpu_percent(interval=None, percpu=True) or [0.0]
            cpu_max_core = max(cpu_per_core)
            now = time.monotonic()
            with _resource_lock:
                _resource_seq += 1
                _resource_samples.append({
                    "seq": _resource_seq,
                    "t": now,
                    "gpu": int(util.gpu),
                    "cpu_avg": float(cpu_avg),
                    "cpu_max_core": float(cpu_max_core),
                    "gpu_temp_c": gpu_temp_c,
                    "vram_used_gb": round(mem.used / 1e9, 1),
                    "vram_total_gb": round(mem.total / 1e9, 0),
                    "ram_used_gb": round((vm.total - vm.available) / 1e9, 1),
                    "ram_total_gb": round(vm.total / 1e9, 0),
                })
                # Trim anything older than window
                cutoff = now - _RESOURCE_WINDOW_S
                while _resource_samples and _resource_samples[0]["t"] < cutoff:
                    _resource_samples.pop(0)
                # Hard cap for safety
                if len(_resource_samples) > _RESOURCE_BUF_MAX:
                    del _resource_samples[: len(_resource_samples) - _RESOURCE_BUF_MAX]
        except Exception as e:
            log.debug("resource sample failed: %s", e)
        time.sleep(_RESOURCE_SAMPLE_INTERVAL_S)


def _rolling_resource_peak() -> tuple[int, int, int]:
    """Return (gpu_max_pct, cpu_max_core_pct, cpu_avg_pct) over the rolling window.

    Returns (0, 0, 0) if no samples yet."""
    with _resource_lock:
        if not _resource_samples:
            return (0, 0, 0)
        gpu_mx = 0
        cpu_max_core_mx = 0.0
        cpu_avg_mx = 0.0
        for s in _resource_samples:
            if s["gpu"] > gpu_mx:
                gpu_mx = s["gpu"]
            if s["cpu_max_core"] > cpu_max_core_mx:
                cpu_max_core_mx = s["cpu_max_core"]
            if s["cpu_avg"] > cpu_avg_mx:
                cpu_avg_mx = s["cpu_avg"]
        return (int(gpu_mx), int(round(cpu_max_core_mx)), int(round(cpu_avg_mx)))


def _recent_resource_samples() -> list[dict]:
    """Return raw sampler rows with relative age for client-side alignment.

    /metrics keeps the old peak fields for status chips, but the trace tab
    needs the raw 100ms rows to draw nuanced hardware lines. Use age_ms rather
    than wall-clock server timestamps so the desktop app can place samples on
    its own clock without requiring synchronized LAN clocks.
    """
    now = time.monotonic()
    with _resource_lock:
        rows = list(_resource_samples)
    out = []
    for s in rows:
        out.append({
            "seq": s.get("seq"),
            "age_ms": int(max(0.0, now - float(s.get("t", now))) * 1000),
            "gpu_util_pct": s.get("gpu", 0),
            "cpu_pct": s.get("cpu_max_core", 0.0),
            "cpu_avg_pct": s.get("cpu_avg", 0.0),
            "gpu_temp_c": s.get("gpu_temp_c"),
            "vram_used_gb": s.get("vram_used_gb"),
            "vram_total_gb": s.get("vram_total_gb"),
            "ram_used_gb": s.get("ram_used_gb"),
            "ram_total_gb": s.get("ram_total_gb"),
        })
    return out


# Start the sampler thread immediately at import time. daemon=True so
# it dies cleanly when the process exits.
_sampler_thread = threading.Thread(
    target=_resource_sampler_loop, name="resource-sampler", daemon=True,
)
_sampler_thread.start()

# Strip labels from a Prometheus metric line: `name{labels}` → `name`.
_LABEL_RE = re.compile(r"\{[^}]*\}")


async def _scrape_vllm() -> None:
    """Pull token-throughput + TTFT from vLLM's Prometheus endpoint."""
    global _prev_sample, _last_vllm
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(VLLM_METRICS_URL)
        if r.status_code != 200:
            return
    except Exception as e:
        # vLLM may be down; leave _last_vllm at its previous values.
        log.debug("vllm scrape failed: %s", e)
        return

    ttft_sum: Optional[float] = None
    ttft_count: Optional[float] = None
    gen_tokens: Optional[float] = None
    model: Optional[str] = None

    for line in r.text.splitlines():
        if line.startswith("#") or not line:
            continue
        name = _LABEL_RE.sub("", line.split(maxsplit=1)[0])
        try:
            value = float(line.rsplit(maxsplit=1)[-1])
        except ValueError:
            continue

        if name == "vllm:time_to_first_token_seconds_sum":
            ttft_sum = value
        elif name == "vllm:time_to_first_token_seconds_count":
            ttft_count = value
        elif name == "vllm:generation_tokens_total":
            # If the same name appears with multiple label combos we'll
            # see it more than once; sum them.
            gen_tokens = (gen_tokens or 0.0) + value
            # Snip the model out of the label set for the UI display.
            m = re.search(r'model_name="([^"]+)"', line)
            if m:
                model = m.group(1)

    now = time.monotonic()
    if ttft_count and ttft_count > 0 and ttft_sum is not None:
        _last_vllm["ttft_ms"] = int((ttft_sum / ttft_count) * 1000)

    if gen_tokens is not None:
        if _prev_sample is not None:
            dt = now - _prev_sample["t"]
            d_tokens = gen_tokens - _prev_sample["tokens"]
            if dt > 0:
                _last_vllm["tps"] = max(0, round(d_tokens / dt))
        _prev_sample = {"t": now, "tokens": gen_tokens}

    if model:
        _last_vllm["model"] = model


async def _scrape_loop() -> None:
    while True:
        await _scrape_vllm()
        await asyncio.sleep(SCRAPE_INTERVAL_S)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(_scrape_loop())


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/metrics")
def metrics() -> dict:
    mem = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
    vm = psutil.virtual_memory()
    # gpu_temp_c: previously injected by tools/sidecar-temp-patch.py at
    # runtime (Addendum 25 follow-up). Folded into source M0 so deploys
    # don't drop it. Try/except so a temp-read failure (driver hiccup)
    # surfaces as null, not a 500.
    try:
        gpu_temp_c: int | None = int(
            pynvml.nvmlDeviceGetTemperature(_gpu, pynvml.NVML_TEMPERATURE_GPU)
        )
    except Exception:
        gpu_temp_c = None
    # gpu_util_pct + cpu_pct: rolling 2s peak from background sampler
    # thread. nvmlDeviceGetUtilizationRates is windowed-average ~1s,
    # so sub-second LLM bursts get reported as 0 if called directly.
    # The sampler thread polls at 100ms and we return the MAX —
    # surfaces real load even on short calls.
    #
    # cpu_pct is now the MAX-PER-CORE peak, NOT the system average.
    # On a 32-core box a single-threaded Python burst at 100% shows
    # as system_avg=3% but max_core=100% — system_avg hid actual work.
    # cpu_avg_pct preserved for callers that want the old metric.
    gpu_util_pct, cpu_pct, cpu_avg_pct = _rolling_resource_peak()
    return {
        "ts": time.time(),
        "gpu_name": _gpu_name,
        "gpu_util_pct": gpu_util_pct,
        "gpu_temp_c": gpu_temp_c,
        "vram_used_gb": round(mem.used / 1e9, 1),
        "vram_total_gb": round(mem.total / 1e9, 0),
        "cpu_pct": cpu_pct,             # NOW: max-per-core peak
        "cpu_avg_pct": cpu_avg_pct,     # system-wide average (was cpu_pct)
        "ram_used_gb": round((vm.total - vm.available) / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 0),
        "ttft_ms": _last_vllm["ttft_ms"],
        "tps": _last_vllm["tps"],
        "model": _last_vllm["model"],
        "resource_boot_id": _resource_boot_id,
        "resource_window_s": _RESOURCE_WINDOW_S,
        "resource_sample_interval_s": _RESOURCE_SAMPLE_INTERVAL_S,
        "resource_samples": _recent_resource_samples(),
    }


# Content-blind vLLM proxy. Conversation bodies are never retained, parsed for
# telemetry, persisted, or broadcast by this process.


@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    is_stream = bool(body.get("stream"))
    upstream = VLLM_UPSTREAM.rstrip("/") + "/v1/chat/completions"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "connection")}

    if not is_stream:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as c:
            r = await c.post(upstream, json=body, headers=headers)
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

    # Streaming path forwards raw chunks without accumulating content.
    async def gen() -> AsyncGenerator[bytes, None]:
        try:
            async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as c:
                async with c.stream("POST", upstream, json=body, headers=headers) as r:
                    async for chunk in r.aiter_raw():
                        yield chunk
        except Exception as e:
            log.warning("upstream stream error: %s", type(e).__name__)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models")
async def proxy_models():
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(VLLM_UPSTREAM.rstrip("/") + "/v1/models")
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


# Anything else under /v1/* passes through unchanged.
@app.api_route("/v1/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_passthrough(rest_of_path: str, request: Request):
    upstream_url = f"{VLLM_UPSTREAM.rstrip('/')}/v1/{rest_of_path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "connection")}
    async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as c:
        r = await c.request(request.method, upstream_url, content=body, headers=headers)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@app.get("/conversations/recent")
def recent_conversations():
    return JSONResponse(
        {"error": "capability_disabled"},
        status_code=404,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/conversations/stream")
async def stream_conversations():
    return JSONResponse(
        {"error": "capability_disabled"},
        status_code=404,
        headers={"Cache-Control": "no-store"},
    )
