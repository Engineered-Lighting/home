"""Home sidecar.

Two responsibilities:

1. Telemetry — GPU util / VRAM (NVML), CPU / RAM (psutil), and vLLM
   throughput / TTFT (scraped from vLLM's Prometheus) — exposed at
   GET /metrics for the Home desktop client to poll.

2. Chat tee — proxies vLLM's OpenAI-compatible API (/v1/chat/completions,
   /v1/models, /v1/*) so HA's Extended OpenAI Conv can call this service
   instead of vLLM directly. Every chat completion gets captured and
   re-broadcast on GET /conversations/stream (SSE), so the Home desktop
   sees ALL conversation activity — typed turns, voice mode, Voice PE
   wake-word turns — in one place, regardless of who initiated it.

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

app = FastAPI(title="Home sidecar", version="0.2.0")
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


# ── Chat tee: proxy vLLM + broadcast each completion to subscribers ────

# Each subscriber gets an asyncio.Queue. Producers push completion events
# (dicts). The /conversations/stream SSE endpoint pops from its queue.
_subscribers: set = set()
# Recent completions buffer for clients that connect mid-conversation or
# want to backfill state. Bounded to keep memory tiny.
_recent_completions: list = []
_RECENT_MAX = 50


async def _broadcast_completion(event: dict) -> None:
    _recent_completions.append(event)
    if len(_recent_completions) > _RECENT_MAX:
        del _recent_completions[: len(_recent_completions) - _RECENT_MAX]
    dead = []
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def _accumulate_tool_call_delta(buf: list, deltas: list) -> None:
    """OpenAI streaming tool calls arrive as deltas indexed by `index`.
    Patch the matching slot in `buf` so by stream-end we have full names
    + arguments strings to feed back as a structured tool call list."""
    for tc in deltas or []:
        idx = tc.get("index", 0)
        while len(buf) <= idx:
            buf.append({"id": None, "type": "function",
                        "function": {"name": "", "arguments": ""}})
        slot = buf[idx]
        if tc.get("id"): slot["id"] = tc["id"]
        f = tc.get("function") or {}
        if f.get("name"):      slot["function"]["name"] += f["name"]
        if f.get("arguments"): slot["function"]["arguments"] += f["arguments"]


def _user_message_from_body(body: dict) -> str:
    """Pluck the latest user message from an OpenAI chat-completions body."""
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            # Multimodal content is a list of parts; concat the text ones.
            if isinstance(c, list):
                return " ".join(
                    p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
    return ""


@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    is_stream = bool(body.get("stream"))
    upstream = VLLM_UPSTREAM.rstrip("/") + "/v1/chat/completions"
    user_msg = _user_message_from_body(body)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "connection")}

    if not is_stream:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as c:
            r = await c.post(upstream, json=body, headers=headers)
        try:
            resp = r.json()
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            await _broadcast_completion({
                "ts": time.time(),
                "id": resp.get("id"),
                "model": resp.get("model"),
                "user": user_msg,
                "assistant": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or [],
                "streamed": False,
            })
            return JSONResponse(content=resp, status_code=r.status_code)
        except Exception as e:
            log.warning("non-stream tee failed: %s", e)
            return Response(content=r.content, status_code=r.status_code,
                            media_type=r.headers.get("content-type", "application/json"))

    # Streaming path — tee chunks back to client, accumulate, broadcast on end.
    async def gen() -> AsyncGenerator[bytes, None]:
        full_content = ""
        tool_calls: list = []
        model = body.get("model")
        completion_id = None
        try:
            async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as c:
                async with c.stream("POST", upstream, json=body, headers=headers) as r:
                    async for raw_line in r.aiter_lines():
                        if not raw_line:
                            yield b"\n"
                            continue
                        if raw_line.startswith("data: "):
                            payload = raw_line[6:].strip()
                            yield (raw_line + "\n\n").encode()
                            if payload == "[DONE]":
                                continue
                            try:
                                obj = json.loads(payload)
                                if obj.get("id") and not completion_id:
                                    completion_id = obj["id"]
                                if obj.get("model"):
                                    model = obj["model"]
                                for ch in obj.get("choices") or []:
                                    d = ch.get("delta") or {}
                                    if isinstance(d.get("content"), str):
                                        full_content += d["content"]
                                    if d.get("tool_calls"):
                                        _accumulate_tool_call_delta(tool_calls, d["tool_calls"])
                            except Exception:
                                pass
                        else:
                            yield (raw_line + "\n").encode()
        except Exception as e:
            log.warning("upstream stream error: %s", e)
        # Finalise the tool call args from string → parsed JSON where possible.
        finalised_tcs = []
        for tc in tool_calls:
            tcc = dict(tc)
            fn = dict(tcc.get("function") or {})
            args_str = fn.get("arguments") or ""
            try:
                fn["arguments_parsed"] = json.loads(args_str) if args_str else None
            except Exception:
                fn["arguments_parsed"] = None
            tcc["function"] = fn
            finalised_tcs.append(tcc)
        await _broadcast_completion({
            "ts": time.time(),
            "id": completion_id,
            "model": model,
            "user": user_msg,
            "assistant": full_content,
            "tool_calls": finalised_tcs,
            "streamed": True,
        })
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
def recent_conversations(since: float = 0.0):
    return {
        "now": time.time(),
        "since": since,
        "entries": [e for e in _recent_completions if e["ts"] > since],
    }


@app.get("/conversations/stream")
async def stream_conversations(request: Request, backfill_n: int = 0):
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers.add(q)

    async def gen() -> AsyncGenerator[bytes, None]:
        try:
            yield b": connected\n\n"
            # Optionally send the last N recent completions on connect so
            # a freshly-loaded UI shows recent context immediately.
            if backfill_n > 0:
                for ev in _recent_completions[-backfill_n:]:
                    yield f"data: {json.dumps(ev)}\n\n".encode()
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(ev)}\n\n".encode()
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
        finally:
            _subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
