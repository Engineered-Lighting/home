"""qwen38_capture.py — the migration's measurement instrument.

Phase 0 found that `/trace` has no producer: its only writer is the s2s
bridge, which never runs, so nothing records a production turn. Every gate
that was going to read `/trace` (G5 transcripts, G6 latency, R4's
`num_cached_tokens`) needs its evidence captured by the harness itself
instead. This module is that capture layer.

Two rules from the migration doc are enforced here rather than left to each
caller, because both have already been got wrong once:

  * **Every latency number states its measurement point AND cache state.**
    A `CapturedTurn` cannot be constructed without them; the label travels
    with the number into the NDJSON and out into any summary.
  * **`cached_tokens` is recorded from day one** (R4: prefix caching is
    likely inert on this hybrid, and the plan turns on knowing). It is read
    from `usage.prompt_tokens_details.cached_tokens`, defaulting to None —
    *not* 0 — when the field is absent, so "the engine reported no cache
    hit" stays distinguishable from "nobody looked".

The archive is NDJSON, one record per turn, holding the full response. That
is what G5 scans and what a later re-score can replay without re-running
the model.
"""
from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

# Where a latency number was taken. Naming these explicitly stops the
# apples-to-oranges comparison the trap index warns about: the sidecar
# proxy, the raw engine, and the HA conversation pipeline measure
# different spans and are not interchangeable.
MEASUREMENT_POINTS = {
    "sidecar": "metrics-sidecar proxy (127.0.0.1:8000) -> vllm; the production path",
    "engine": "vllm directly (in-network only; not host-published)",
    "ha_conversation": "HA /api/conversation/process; the D1 voice instrument",
    "vision_sidecar": "vision-sidecar /reason|/describe; the D1 ambient instrument",
}

# Cache state of the request. "cold" and "warm" are claims about the KV
# prefix cache; "busted" means the caller actively defeated it.
CACHE_STATES = ("cold", "warm", "busted", "unknown")


@dataclass
class CapturedTurn:
    """One request/response pair plus everything a gate needs to score it."""
    cell: str                       # which matrix cell / gate produced this
    measurement_point: str
    cache_state: str
    latency_s: float
    status: str = "ok"              # "ok" | "error"
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    model: str | None = None
    finish_reason: str | None = None
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.measurement_point not in MEASUREMENT_POINTS:
            raise ValueError(
                f"unknown measurement_point {self.measurement_point!r}; "
                f"choose one of {sorted(MEASUREMENT_POINTS)} — an unlabelled "
                "latency number is not admissible evidence")
        if self.cache_state not in CACHE_STATES:
            raise ValueError(
                f"unknown cache_state {self.cache_state!r}; "
                f"choose one of {CACHE_STATES}")

    @property
    def cache_hit_ratio(self) -> float | None:
        if not self.prompt_tokens or self.cached_tokens is None:
            return None
        return self.cached_tokens / self.prompt_tokens

    def content(self) -> str:
        try:
            c = self.response["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError):
            return ""
        if isinstance(c, list):
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        return c or ""


def _usage(resp: dict) -> tuple[int | None, int | None, int | None]:
    u = (resp or {}).get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = u.get("cached_tokens")     # some builds hoist it
    return u.get("prompt_tokens"), u.get("completion_tokens"), cached


def encode_image(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def bust_jpeg(img_bytes: bytes) -> bytes:
    """Perturb one pixel so the multimodal prefix cache misses.

    Without this, repeated runs measure one cold prefill and N warm
    replays, which is the single easiest way to publish a latency number
    that production will never see.
    """
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    im.putpixel((random.randrange(w), random.randrange(h)),
                (random.randrange(256), random.randrange(256),
                 random.randrange(256)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


class Capturer:
    """Issues chat completions and records them as CapturedTurns."""

    def __init__(self, endpoint: str, model: str = "qwen3-vl-30b",
                 measurement_point: str = "sidecar", timeout: float = 180.0):
        if measurement_point not in MEASUREMENT_POINTS:
            raise ValueError(f"unknown measurement_point {measurement_point!r}")
        self.endpoint = endpoint
        self.model = model
        self.measurement_point = measurement_point
        self.timeout = timeout
        self.turns: list[CapturedTurn] = []

    def chat(self, messages: list[dict], cell: str, cache_state: str,
             meta: dict | None = None, **params) -> CapturedTurn:
        """One completion. `cache_state` is required and unguessable from
        here — only the caller knows whether it busted the cache."""
        body = {"model": self.model, "messages": messages, "stream": False}
        body.update(params)
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=payload,
            headers={"Content-Type": "application/json"})

        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read())
            latency = time.time() - t0
            pt, ct, cached = _usage(resp)
            try:
                finish = resp["choices"][0].get("finish_reason")
            except (KeyError, IndexError, TypeError):
                finish = None
            turn = CapturedTurn(
                cell=cell, measurement_point=self.measurement_point,
                cache_state=cache_state, latency_s=latency,
                prompt_tokens=pt, completion_tokens=ct, cached_tokens=cached,
                model=resp.get("model"), finish_reason=finish,
                request=_redact_request(body), response=resp,
                meta=meta or {})
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            turn = CapturedTurn(
                cell=cell, measurement_point=self.measurement_point,
                cache_state=cache_state, latency_s=time.time() - t0,
                status="error", error=f"{type(e).__name__}: {e}",
                request=_redact_request(body), meta=meta or {})

        self.turns.append(turn)
        return turn

    def write(self, path: str) -> int:
        """Append every captured turn as NDJSON. Returns the count."""
        with open(path, "a", encoding="utf-8") as f:
            for t in self.turns:
                f.write(json.dumps(asdict(t), default=str) + "\n")
        return len(self.turns)


def _redact_request(body: dict) -> dict:
    """Keep the request auditable without embedding megabytes of base64.

    Image bytes are replaced by a length marker: the gate needs to know an
    image was sent and how big it was, never the pixels again — the frozen
    corpus is the place images live.
    """
    out = json.loads(json.dumps(body, default=str))
    for msg in out.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        part["image_url"] = {"url": f"<image {len(url)} b64 chars>"}
    return out


# ── R4 instrument ────────────────────────────────────────────────────────
# vLLM 0.20.2 does NOT emit `usage.prompt_tokens_details.cached_tokens`
# (verified against the live engine 2026-08-16), so the migration doc's
# "instrument num_cached_tokens from day one" cannot be done from the
# response. These Prometheus counters are the instrument that exists.
# Read them either side of a cell and take the delta: a lifetime rate
# averages over months of traffic and says nothing about the cell.
PREFIX_CACHE_METRICS = (
    "vllm:prefix_cache_queries_total",
    "vllm:prefix_cache_hits_total",
    "vllm:mm_cache_queries_total",     # multimodal encoder cache, separate
    "vllm:mm_cache_hits_total",
)


def read_cache_counters(metrics_text: str) -> dict[str, float]:
    """Parse the prefix/mm cache counters out of a Prometheus scrape.

    Takes text rather than a URL because vLLM is not host-published on this
    stack — the caller fetches it however it can reach the engine.
    """
    import re
    out: dict[str, float] = {}
    for line in metrics_text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        m = re.match(r"^(vllm:[A-Za-z_]+)\{[^}]*\}\s+([0-9.eE+-]+)$", line)
        if m and m.group(1) in PREFIX_CACHE_METRICS:
            out[m.group(1)] = float(m.group(2))
    return out


def cache_hit_delta(before: dict, after: dict) -> dict:
    """Hit rate over the interval between two counter reads.

    Returns None rates rather than 0.0 when no queries occurred, so an idle
    interval is never reported as a 0% hit rate.
    """
    def rate(hits_key, queries_key):
        dq = after.get(queries_key, 0) - before.get(queries_key, 0)
        dh = after.get(hits_key, 0) - before.get(hits_key, 0)
        return {"queries": dq, "hits": dh,
                "rate": (dh / dq) if dq > 0 else None}
    return {
        "prefix_cache": rate("vllm:prefix_cache_hits_total",
                             "vllm:prefix_cache_queries_total"),
        "mm_cache": rate("vllm:mm_cache_hits_total",
                         "vllm:mm_cache_queries_total"),
    }


def percentile(values, p: float):
    """Nearest-rank percentile. No interpolation: at the n<=100 samples
    these gates use, interpolating invents a number no run produced."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = int(round((p / 100.0) * (len(vals) - 1)))
    return vals[min(max(k, 0), len(vals) - 1)]


def summarize(turns, label: str = "") -> str:
    """A latency summary that cannot be quoted without its measurement
    point and cache state — they are printed on the same line."""
    ok = [t for t in turns if t.status == "ok"]
    if not ok:
        return f"{label}: no successful turns ({len(turns)} attempted)"
    points = sorted({t.measurement_point for t in ok})
    states = sorted({t.cache_state for t in ok})
    lat = [t.latency_s for t in ok]
    ratios = [t.cache_hit_ratio for t in ok if t.cache_hit_ratio is not None]
    line = (f"{label}: n={len(ok)}/{len(turns)} "
            f"p50={percentile(lat, 50):.2f}s p95={percentile(lat, 95):.2f}s "
            f"max={max(lat):.2f}s "
            f"[measured at {'+'.join(points)}; cache {'+'.join(states)}]")
    if ratios:
        line += (f"\n    prefix-cache hit ratio (R4): "
                 f"p50={percentile(ratios, 50):.3f} "
                 f"mean={sum(ratios) / len(ratios):.3f} "
                 f"zero-hit={sum(1 for r in ratios if r == 0)}/{len(ratios)}")
    else:
        line += "\n    prefix-cache: engine reported no cached_tokens field"
    return line
