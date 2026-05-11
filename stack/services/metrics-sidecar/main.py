"""Home metrics sidecar.

Exposes one JSON endpoint for the Home desktop client to poll:

    GET /metrics  →  { gpu_util_pct, vram_used_gb, vram_total_gb,
                       cpu_pct, ram_used_gb, ram_total_gb,
                       ttft_ms, tps, ts }

`gpu/vram` come from NVML, `cpu/ram` from psutil, and `ttft/tps` are
scraped from vLLM's own Prometheus endpoint and rolling-windowed.

Designed to be tiny — single file, no DB, no auth. Lives behind the
private LAN/Tailnet (same trust boundary as the rest of the stack).
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from typing import Optional

import httpx
import psutil
import pynvml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

log = logging.getLogger("metrics-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VLLM_METRICS_URL = os.environ.get("VLLM_METRICS_URL", "http://vllm:8000/metrics")
SCRAPE_INTERVAL_S = float(os.environ.get("SCRAPE_INTERVAL_S", "2.0"))

app = FastAPI(title="Home metrics sidecar", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
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
    util = pynvml.nvmlDeviceGetUtilizationRates(_gpu)
    mem = pynvml.nvmlDeviceGetMemoryInfo(_gpu)
    vm = psutil.virtual_memory()
    return {
        "ts": time.time(),
        "gpu_name": _gpu_name,
        "gpu_util_pct": int(util.gpu),
        "vram_used_gb": round(mem.used / 1e9, 1),
        "vram_total_gb": round(mem.total / 1e9, 0),
        "cpu_pct": int(psutil.cpu_percent(interval=None)),
        "ram_used_gb": round((vm.total - vm.available) / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 0),
        "ttft_ms": _last_vllm["ttft_ms"],
        "tps": _last_vllm["tps"],
        "model": _last_vllm["model"],
    }
