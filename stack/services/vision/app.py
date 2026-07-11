"""Vision sidecar: HA camera snapshot → vLLM (multimodal) → short text.

The primary LLM (Qwen3-VL-30B-A3B-Instruct on vLLM) is now natively
multimodal, so this sidecar's job is just to fetch the right camera
frame from HA and hand it to vLLM as an OpenAI-style multimodal
chat-completion request. Returns a short description.

Why still a sidecar (vs HA's Extended OpenAI Conv handling images
directly): HA's `describe_camera` tool call gives the agent a clean
text-only response, and we keep camera-grab logic out of HA's prompt
template. Also lets us cap image size, normalize formats, and add
camera aliases (driveway / front door / outside → camera.driveway).

M1 (Addendum 27): we also cache the JPEG fetched for /describe under
GET /snapshot/<camera>/latest.jpg so the Home app can render a
thumbnail in perception chat bubbles without a second round-trip
through HA's camera_proxy. Cache is in-memory, per-camera, TTL-bounded.

F-3 (Addendum 27): /describe_clip extends the single-frame /describe
pattern to N frames sampled over a few seconds. Lets the agent reason
about motion ("did the door open?", "is anyone walking past?") that
single-frame describe can't catch. Qwen3-VL handles multi-image
content natively (256K context, hours-long video recall per docs);
Phase 2b probe P9 verified base64 inline content works (URL refs
rejected). Sampling is real-time poll of HA camera_proxy (each frame
is "now" at that instant), so the call BLOCKS for (N-1)*interval_s
plus the vLLM round-trip. Caps protect against pathological calls.

Env:
  HA_URL            default http://192.168.0.125:8123
  HA_TOKEN          optional; production containment leaves it empty
  VLLM_URL          default http://vllm:8000  (sidecar runs in the same
                    docker network; vllm is reachable by service name)
  VISION_MODEL      default qwen3-vl-30b      (served-model-name from vLLM)
  VISION_MAX_TOKENS default 200
  SNAPSHOT_TTL_S    default 90 — how long a cached snapshot stays fresh
                    before /snapshot/X returns 404. New /describe calls
                    refresh the same key.
"""
from __future__ import annotations
import base64
import io
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from collections import deque
from typing import Optional

import asyncio

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from PIL import Image, ImageDraw

log = logging.getLogger("vision-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HA_URL = os.environ.get("HA_URL", "http://192.168.0.125:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
VLLM_URL = os.environ.get("VLLM_URL", "http://vllm:8000")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen3-vl-30b")
VISION_MAX_TOKENS = int(os.environ.get("VISION_MAX_TOKENS", "200"))
SNAPSHOT_TTL_S = float(os.environ.get("SNAPSHOT_TTL_S", "90"))
# /reason (Thinking with Visual Primitives, Phase 0) — a grounded-reasoning
# trace needs far more room than the one-sentence /describe (200 tokens).
REASON_MAX_TOKENS = int(os.environ.get("REASON_MAX_TOKENS", "1024"))
# Broad multi-instance /reason questions ("what is on the counter?") can
# spiral into a degenerate "X is also near Y." repetition loop (~20% of
# runs at temp 0.2). frequency_penalty breaks it without suppressing
# multi-box output; a probe sweep (tools/probe-grounded-reasoning.py)
# put 0.4 at 0/12 runaways on plain and harsher phrasings alike, box
# counts unchanged (0.3 still leaked ~1/12; suppression bites near 0.8).
REASON_FREQUENCY_PENALTY = 0.4

# Full-resolution camera frames (Phase 0.5, enhancement rung #1). HA's
# camera_proxy serves Frigate's 1280x720 *detect* sub-stream; the cameras'
# main streams are 1080p (2304x1296 for the kitchen) and reachable directly
# over RTSP. /reason_zoom grabs that full-res frame with ffmpeg so the
# foveated crop carries real pixels; camera_proxy stays the fallback.
# CAMERA_RTSP_HOSTS maps a camera slug to its IP, e.g.
#   "kitchen=192.168.0.64,living_room=192.168.0.227"
RTSP_USER = os.environ.get("CAMERA_RTSP_USER", "")
RTSP_PASS = os.environ.get("CAMERA_RTSP_PASS", "")
RTSP_PORT = os.environ.get("CAMERA_RTSP_PORT", "554")
RTSP_STREAM = os.environ.get("CAMERA_RTSP_STREAM", "stream1")
RTSP_GRAB_TIMEOUT_S = float(os.environ.get("RTSP_GRAB_TIMEOUT_S", "15"))
RTSP_HOSTS: dict[str, str] = {}
for _pair in os.environ.get("CAMERA_RTSP_HOSTS", "").split(","):
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        RTSP_HOSTS[_k.strip()] = _v.strip()
FFMPEG_BIN = shutil.which("ffmpeg")

# Friendly camera names → HA entity IDs.
CAMERA_ALIASES = {
    "kitchen": "camera.kitchen",
    "living_room": "camera.living_room",
    "living room": "camera.living_room",
    "dining_room": "camera.dining_room",
    "dining room": "camera.dining_room",
    "workshop": "camera.workshop",
    "driveway": "camera.driveway",
    "front door": "camera.driveway",
    "outside": "camera.driveway",
}


def resolve_entity(name: str) -> str:
    n = name.strip().lower()
    if n.startswith("camera."):
        return n
    return CAMERA_ALIASES.get(n) or f"camera.{n.replace(' ', '_')}"


# Distinct camera slugs (one per room). /reason + /reason_zoom use these for
# auto camera routing: when the caller passes camera="auto" (or none), a
# short text-only LLM call picks the room the question is about — so a
# question about the coffee table goes to living_room, not whatever was
# selected. The slugs ARE room names, so the model routes by room knowledge.
ROUTE_CAMERAS = sorted({v.split(".", 1)[-1] for v in CAMERA_ALIASES.values()})


async def route_camera(client, question: str) -> str:
    """Pick the most relevant camera for `question` with a short text-only
    LLM call. Falls back to 'kitchen' on any failure."""
    system = (
        "You route questions to home cameras — there is one camera per "
        "room: " + ", ".join(ROUTE_CAMERAS) + ". Reply with EXACTLY one "
        "camera name from that list and nothing else: the room the "
        "question is most about.")
    try:
        r = await client.post(
            f"{VLLM_URL}/v1/chat/completions",
            json={"model": VISION_MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": question}],
                  "stream": False, "max_tokens": 12, "temperature": 0},
            timeout=30)
        if r.status_code == 200:
            txt = ((r.json().get("choices") or [{}])[0]
                   .get("message", {}).get("content", "") or "").strip().lower()
            for cam in ROUTE_CAMERAS:                  # exact slug / spaced
                if txt == cam or txt == cam.replace("_", " "):
                    return cam
            for cam in ROUTE_CAMERAS:                  # else: contained
                if cam in txt or cam.replace("_", " ") in txt:
                    return cam
            log.warning("camera routing: unrecognized reply %r", txt)
    except Exception as e:
        log.warning("camera routing failed (%s)", e)
    return "kitchen"


app = FastAPI(title="hav-vision-sidecar", version="0.3.0")
# Permissive CORS — same as metrics-sidecar:main.py. The Home app's
# `tauriFetch` wrapper falls back to native `fetch()` when the Tauri
# HTTP plugin isn't picked up at runtime; without this, the `:8091`
# /healthz poll from origin `tauri.localhost` gets CORS-blocked. The
# trust boundary is the LAN/Tailscale interface (same as the rest of
# the stack), not CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


class DescribeIn(BaseModel):
    camera: str
    question: Optional[str] = None


class DescribeOut(BaseModel):
    camera: str
    entity_id: str
    description: str
    latency_ms: int
    model: str


# M1 — per-camera snapshot cache. After every /describe call we stash
# the JPEG we just fetched + the wall-clock ts so the Tauri app can
# render a thumbnail at /snapshot/<camera>/latest.jpg without going
# back through HA's camera_proxy. Bounded by camera count (5-ish
# typical) × one frame each = trivial memory. TTL-gated; stale
# entries return 404 rather than serving outdated frames.
_snapshot_cache: dict[str, tuple[bytes, float]] = {}


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "model": VISION_MODEL}


@app.get("/snapshot/{camera}/latest.jpg")
async def snapshot_latest(camera: str) -> Response:
    """Serve the most recent JPEG that /describe fetched for `camera`,
    if still within SNAPSHOT_TTL_S. Lookup is by the same `camera`
    parameter shape as /describe (slug, alias, or full entity_id).
    Returns 404 if missing or stale — caller can fall back to HA's
    camera_proxy.
    """
    entity_id = resolve_entity(camera)
    entry = _snapshot_cache.get(entity_id)
    if entry is None:
        raise HTTPException(404, f"no cached snapshot for {entity_id}")
    jpeg, ts = entry
    age = time.time() - ts
    if age > SNAPSHOT_TTL_S:
        # Drop the stale entry so we don't keep returning 404 for the
        # same key forever after a long quiet period.
        _snapshot_cache.pop(entity_id, None)
        raise HTTPException(404, f"snapshot for {entity_id} is stale ({age:.0f}s)")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            # Browser can cache for up to TTL — UI is free to <img src=...>
            # without busting; the URL itself doesn't change between
            # refreshes within a TTL window.
            "Cache-Control": f"public, max-age={int(SNAPSHOT_TTL_S)}",
            "X-Snapshot-Age-Seconds": f"{age:.1f}",
        },
    )


@app.get("/cameras")
async def list_cameras() -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{HA_URL}/api/states",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        r.raise_for_status()
        cams = [
            {"entity_id": s["entity_id"], "state": s["state"]}
            for s in r.json()
            if s["entity_id"].startswith("camera.")
        ]
        return {"cameras": cams, "aliases": CAMERA_ALIASES}


@app.post("/describe", response_model=DescribeOut)
async def describe(body: DescribeIn) -> DescribeOut:
    entity_id = resolve_entity(body.camera)
    question = (body.question or "What is happening in this scene? Reply with one short sentence.").strip()

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=60) as c:
        # 1. Fresh JPEG from HA's camera proxy.
        r = await c.get(
            f"{HA_URL}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        if r.status_code != 200:
            raise HTTPException(
                502, f"HA camera_proxy returned {r.status_code} for {entity_id}"
            )
        # M1 — cache the JPEG so the Home app can render a thumbnail
        # via GET /snapshot/<camera>/latest.jpg. Stamping the resolved
        # entity_id as the key so the lookup at serve time matches
        # whatever alias the caller used. Cached even before vLLM
        # processes the frame so a slow vLLM doesn't block the cache
        # update — the image we WILL describe is the one we cache.
        _snapshot_cache[entity_id] = (r.content, time.time())
        jpeg_b64 = base64.b64encode(r.content).decode()
        log.info("camera_proxy %s → %d bytes", entity_id, len(r.content))

        # 2. Multimodal chat completion via vLLM (OpenAI-compatible).
        #    Qwen3-VL accepts image data URLs in the message content list.
        messages = [
            {
                "role": "system",
                "content": (
                    "You are reading a single snapshot from a home security "
                    "camera. Answer briefly and naturally for a voice assistant. "
                    "Use one short sentence. Don't say 'in the image' or "
                    "'I see' — describe directly."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"},
                    },
                    {
                        "type": "text",
                        "text": f"Camera: {entity_id}. {question}",
                    },
                ],
            },
        ]
        vllm_body = {
            "model": VISION_MODEL,
            "messages": messages,
            "stream": False,
            "max_tokens": VISION_MAX_TOKENS,
            "temperature": 0.3,
        }
        r = await c.post(
            f"{VLLM_URL}/v1/chat/completions",
            json=vllm_body,
            timeout=60,
        )
        if r.status_code != 200:
            raise HTTPException(502, f"vLLM returned {r.status_code}: {r.text[:500]}")
        resp = r.json()
        out = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info("describe %s ok in %dms: %r", entity_id, elapsed_ms, out[:80])
    _recent_describes.append({
        "ts": time.time(),
        "camera": body.camera,
        "entity_id": entity_id,
        "description": out,
        "snapshot_url": f"/snapshot/{body.camera}/latest.jpg",
        "latency_ms": elapsed_ms,
        "model": VISION_MODEL,
    })
    return DescribeOut(
        camera=body.camera,
        entity_id=entity_id,
        description=out,
        latency_ms=elapsed_ms,
        model=VISION_MODEL,
    )


# ── F-3 (Addendum 27): multi-frame /describe_clip ─────────────────────

class DescribeClipIn(BaseModel):
    camera: str
    frames: int = 4              # how many frames to sample (capped server-side)
    interval_s: float = 1.0      # seconds between samples
    question: Optional[str] = None


class DescribeClipOut(BaseModel):
    camera: str
    entity_id: str
    description: str
    frames_captured: int
    span_s: float                # actual wall-clock span across captures
    sampling_ms: int             # time spent fetching frames
    inference_ms: int            # time spent in vLLM call
    latency_ms: int              # total
    model: str


# Hard caps: keeps a pathological call from saturating GPU / network.
# 8 frames × 2s = 14s wall + ~3s vLLM = ~17s worst case.
DESCRIBE_CLIP_MAX_FRAMES = 8
DESCRIBE_CLIP_MIN_FRAMES = 2
DESCRIBE_CLIP_MAX_INTERVAL_S = 2.0
DESCRIBE_CLIP_MIN_INTERVAL_S = 0.2


@app.post("/describe_clip", response_model=DescribeClipOut)
async def describe_clip(body: DescribeClipIn) -> DescribeClipOut:
    """Sample N frames from one camera over (N-1) * interval_s seconds
    and have vLLM describe the motion/scene. Real-time poll — each
    frame is "now at sample i", so the call BLOCKS for the duration
    of the sample window. Use frames=4 + interval_s=1 (~3s) for
    typical motion queries; bump to frames=6 + interval_s=2 (~10s)
    for slower events.
    """
    entity_id = resolve_entity(body.camera)
    # Clamp inputs — agent might pass anything.
    frames = max(DESCRIBE_CLIP_MIN_FRAMES, min(DESCRIBE_CLIP_MAX_FRAMES, body.frames))
    interval_s = max(DESCRIBE_CLIP_MIN_INTERVAL_S, min(DESCRIBE_CLIP_MAX_INTERVAL_S, body.interval_s))
    question = (body.question or (
        f"These are {frames} sequential snapshots from the same camera, "
        f"each ~{interval_s:.1f} seconds apart. What is happening across "
        f"this clip? Note any motion, doors opening, people entering or "
        f"leaving. Reply in one or two short sentences for a voice assistant — "
        f"don't say 'in the images' or list each frame."
    )).strip()

    t_total = time.monotonic()
    t_sampling = time.monotonic()

    # Fetch frames sequentially with sleeps in between. Sequential
    # (not parallel) because we want real time progression — parallel
    # would give us N near-identical "now" frames defeating the purpose.
    jpegs: list[bytes] = []
    async with httpx.AsyncClient(timeout=30) as c:
        for i in range(frames):
            try:
                r = await c.get(
                    f"{HA_URL}/api/camera_proxy/{entity_id}",
                    headers={"Authorization": f"Bearer {HA_TOKEN}"},
                )
            except Exception as e:
                raise HTTPException(502, f"camera_proxy fetch failed at frame {i}: {e}")
            if r.status_code != 200:
                raise HTTPException(
                    502, f"camera_proxy returned {r.status_code} for {entity_id} at frame {i}"
                )
            jpegs.append(r.content)
            # Sleep BEFORE next iteration (don't sleep after last frame
            # — wasted time before vLLM call).
            if i < frames - 1:
                await asyncio.sleep(interval_s)

        # Cache the last frame so /snapshot/<camera>/latest.jpg stays
        # warm even when the agent uses describe_clip instead of describe.
        if jpegs:
            _snapshot_cache[entity_id] = (jpegs[-1], time.time())

        sampling_ms = int((time.monotonic() - t_sampling) * 1000)
        log.info(
            "describe_clip %s sampled %d frames in %dms (interval=%.2fs)",
            entity_id, len(jpegs), sampling_ms, interval_s,
        )

        # Build multi-image content. Each frame is an image_url block;
        # the final text block carries the question. Inline base64 per
        # Phase 2b P9 (URL-style image_url refs are rejected by this
        # vLLM build).
        content: list[dict] = []
        for jpeg in jpegs:
            jpeg_b64 = base64.b64encode(jpeg).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"},
            })
        content.append({
            "type": "text",
            "text": f"Camera: {entity_id} ({len(jpegs)} frames, ~{interval_s:.1f}s apart). {question}",
        })

        messages = [
            {
                "role": "system",
                "content": (
                    "You are reading a short sequence of snapshots from a "
                    "single home security camera, ordered earliest to "
                    "latest. Answer briefly for a voice assistant — one or "
                    "two short sentences. Focus on what CHANGED or "
                    "MOVED between frames. Don't list each frame; "
                    "don't say 'in the images'."
                ),
            },
            {"role": "user", "content": content},
        ]

        t_inference = time.monotonic()
        try:
            r = await c.post(
                f"{VLLM_URL}/v1/chat/completions",
                json={
                    "model": VISION_MODEL,
                    "messages": messages,
                    "stream": False,
                    # Multi-image needs a bit more headroom for the
                    # 1-2 sentence answer; not much.
                    "max_tokens": VISION_MAX_TOKENS,
                    "temperature": 0.3,
                },
                timeout=60,
            )
        except Exception as e:
            raise HTTPException(502, f"vLLM request failed: {e}")
        if r.status_code != 200:
            raise HTTPException(502, f"vLLM returned {r.status_code}: {r.text[:500]}")
        resp = r.json()
        out = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        inference_ms = int((time.monotonic() - t_inference) * 1000)

    elapsed_ms = int((time.monotonic() - t_total) * 1000)
    span_s = (len(jpegs) - 1) * interval_s if len(jpegs) > 1 else 0.0
    log.info(
        "describe_clip %s ok in %dms (sampling=%dms, inference=%dms): %r",
        entity_id, elapsed_ms, sampling_ms, inference_ms, out[:80],
    )
    return DescribeClipOut(
        camera=body.camera,
        entity_id=entity_id,
        description=out,
        frames_captured=len(jpegs),
        span_s=round(span_s, 2),
        sampling_ms=sampling_ms,
        inference_ms=inference_ms,
        latency_ms=elapsed_ms,
        model=VISION_MODEL,
    )


# ── /reason — Thinking with Visual Primitives, Phase 0 ────────────────
# Grounded reasoning over a single camera frame: Qwen3-VL is prompted to
# "think by pointing" — emit <ref>label</ref><box>x1,y1,x2,y2</box> for
# every object it reasons about — and we parse those primitives out and
# draw them on the frame. Scoped (Phase 0 Step A verdict) to bounded /
# single-subject questions: small-N grounding is accurate; multi-instance
# counting is NOT reliable (Qwen3-VL #1257) and is a known gap.

# Per-camera cache of the most recent /reason annotated JPEG, served at
# GET /reason/<camera>/annotated.jpg. Same TTL/shape as _snapshot_cache.
_reason_cache: dict[str, tuple[bytes, float]] = {}

# Ring buffer of recent /reason call metadata, served at GET /reason/latest.
# The HA event stream (intent-progress + intent-end) exposes tool *arguments*
# mid-stream but never the tool *result*. So when the Home app sees the LLM
# call the `look` tool with `camera: "auto"`, it has no way to learn which
# camera the sidecar auto-routed to or where the annotated frame lives. This
# ring buffer is the side-channel: the app fetches /reason/latest after the
# turn ends and gets {camera, annotated_url, primitives, ...} for the perception
# card. Size-capped; recency-filtered on read.
_recent_reasons: deque[dict] = deque(maxlen=16)
_recent_describes: deque[dict] = deque(maxlen=16)

# The grounded-reasoning prompt — prompt 'v3', validated in Step A
# (tools/probe-grounded-reasoning.py). The explicit "never invent or tile
# boxes / box once / be concise" rules curb over-boxing, but do NOT stop
# the degenerate enumerate-forever repetition loop on broad questions —
# that is what REASON_FREQUENCY_PENALTY handles. The <ref>label</ref>
# wrapper gives each box a parseable name.
REASON_SYSTEM = (
    "You are a visual reasoning assistant that thinks by pointing. "
    "Whenever you refer to a specific real thing in the image, give it "
    "immediately as <ref>short label</ref><box>x1,y1,x2,y2</box>, with "
    "integer coordinates from 0 to 1000 (top-left origin). Rules: box "
    "only things you can actually, distinctly see — never invent objects "
    "or tile boxes to pad a count; box each distinct object exactly once; "
    "if unsure whether something is there, do not box it; to compare "
    "positions, reason from the box coordinates."
)
REASON_USER_TMPL = (
    "Answer the question. Reference each real object you reason about as "
    "<ref>label</ref><box>...</box>, once. Be decisive and concise — a "
    "few sentences, not a long list. End with a line 'ANSWER: ...'.\n\n"
    "Question: {question}"
)

# <ref>label</ref><box>x1,y1,x2,y2</box> — the format the prompt asks for.
# Tolerant of Qwen3-VL's native variant <box>[[x1,y1,x2,y2]]> too:
# optional square brackets, and a bare '>' close as well as </box>.
# Without this, responses that use the bracketed form parse to zero
# primitives even though the model boxed everything correctly.
_BOX_INNER = (r"<box>\s*\[*\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
              r"\s*\]*\s*(?:</box>|>)")
_REF_BOX_RE = re.compile(r"<ref>\s*(.*?)\s*</ref>\s*" + _BOX_INNER,
                         re.IGNORECASE)
# Bare <box> fallback, if the model skips the <ref> wrapper.
_BARE_BOX_RE = re.compile(_BOX_INNER, re.IGNORECASE)


def _clamp1000(v: int) -> int:
    return max(0, min(1000, v))


def parse_primitives(text: str) -> list[dict]:
    """Pull <ref>label</ref><box>..</box> primitives out of a reasoning
    trace. Boxes are normalised [0,1000]. Tolerant: falls back to bare
    <box> tags (label 'object N') if the model skips the <ref> wrapper.
    Degenerate (zero-area) boxes are dropped."""
    prims: list[dict] = []
    for m in _REF_BOX_RE.finditer(text):
        label = (m.group(1) or "").strip() or "object"
        x1, y1, x2, y2 = (_clamp1000(int(g)) for g in m.groups()[1:])
        if x2 > x1 and y2 > y1:
            prims.append({"label": label, "bbox_1000": [x1, y1, x2, y2]})
    if not prims:  # fallback — bare boxes, no labels
        for i, m in enumerate(_BARE_BOX_RE.finditer(text)):
            x1, y1, x2, y2 = (_clamp1000(int(g)) for g in m.groups())
            if x2 > x1 and y2 > y1:
                prims.append({"label": f"object {i + 1}",
                              "bbox_1000": [x1, y1, x2, y2]})
    return prims


def _strip_primitives(text: str) -> str:
    """Reasoning text with the <ref>..</ref> / <box>..</box> markup removed
    — clean, human-readable prose."""
    t = re.sub(r"<ref>.*?</ref>", "", text, flags=re.IGNORECASE)
    t = re.sub(r"<box>.*?(?:</box>|>)", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def _extract_answer(text: str) -> str:
    """A clean prose answer: the 'ANSWER: ...' line if the model emitted
    one, else the whole reasoning with the primitive markup stripped (the
    model tends to describe-then-ground, so this yields the prose)."""
    m = re.search(r"ANSWER:\s*(.+)", text, re.IGNORECASE)
    return _strip_primitives(m.group(1) if m else text)


def annotate_frame(jpeg: bytes, prims: list[dict]) -> tuple[bytes, int, int]:
    """Draw the parsed boxes (+ index labels) on the frame. Also fills
    each primitive's bbox_px. Returns (annotated_jpeg, width, height)."""
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    for i, p in enumerate(prims):
        x1, y1, x2, y2 = p["bbox_1000"]
        px = [x1 / 1000 * w, y1 / 1000 * h, x2 / 1000 * w, y2 / 1000 * h]
        p["bbox_px"] = [round(v) for v in px]
        d.rectangle(px, outline=(255, 60, 60), width=3)
        d.text((px[0] + 4, px[1] + 4), f"{i + 1} {p['label']}"[:40],
               fill=(255, 60, 60))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue(), w, h


class ReasonIn(BaseModel):
    camera: str = "auto"          # "auto" → the sidecar routes by the question
    question: str


class Primitive(BaseModel):
    label: str
    bbox_1000: list[int]          # [x1,y1,x2,y2] normalised 0-1000
    bbox_px: list[int]            # same, in source-frame pixels (crop-ready)


class ReasonOut(BaseModel):
    camera: str
    entity_id: str
    question: str
    reasoning: str                # the full grounded-reasoning trace
    answer: str                   # the 'ANSWER:' line, '' if none
    primitives: list[Primitive]
    frame_w: int
    frame_h: int
    annotated_url: str            # GET this for the boxes-on-frame image
    latency_ms: int
    model: str


@app.post("/reason", response_model=ReasonOut)
async def reason(body: ReasonIn) -> ReasonOut:
    """Grounded reasoning over one camera frame — the model reasons by
    emitting visual primitives (boxes) inline. Phase 0 of "Thinking with
    Visual Primitives". Scoped to bounded / single-subject questions."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as c:
        # camera "auto" (or empty) → route by the question.
        cam_in = (body.camera or "auto").strip().lower()
        if cam_in in ("", "auto"):
            cam_in = await route_camera(c, question)
        entity_id = resolve_entity(cam_in)
        r = await c.get(
            f"{HA_URL}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        if r.status_code != 200:
            raise HTTPException(
                502, f"HA camera_proxy returned {r.status_code} for {entity_id}")
        jpeg = r.content
        _snapshot_cache[entity_id] = (jpeg, time.time())
        jpeg_b64 = base64.b64encode(jpeg).decode()

        messages = [
            {"role": "system", "content": REASON_SYSTEM},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}},
                {"type": "text",
                 "text": REASON_USER_TMPL.format(question=question)},
            ]},
        ]
        r = await c.post(
            f"{VLLM_URL}/v1/chat/completions",
            json={
                "model": VISION_MODEL,
                "messages": messages,
                "stream": False,
                "max_tokens": REASON_MAX_TOKENS,
                "temperature": 0.2,
                # Breaks the multi-instance repetition runaway; tuned so
                # multi-box output is untouched (see REASON_FREQUENCY_PENALTY).
                "frequency_penalty": REASON_FREQUENCY_PENALTY,
            },
            timeout=120,
        )
        if r.status_code != 200:
            raise HTTPException(502, f"vLLM returned {r.status_code}: {r.text[:500]}")
        resp = r.json()
        content = (resp.get("choices") or [{}])[0].get(
            "message", {}).get("content", "").strip()

    prims = parse_primitives(content)
    annotated, w, h = annotate_frame(jpeg, prims)   # also fills bbox_px
    _reason_cache[entity_id] = (annotated, time.time())
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    answer_text = _extract_answer(content)
    log.info("reason %s ok in %dms: %d primitive(s)",
             entity_id, elapsed_ms, len(prims))
    # Push into the recents ring so GET /reason/latest can give the Home app
    # the camera + annotated_url + primitives for the perception card. The HA
    # tool flow strips everything except `answer` from the tool result, so
    # this side-channel is the only way for the chat client to learn them.
    _recent_reasons.append({
        "ts": time.time(),
        "camera": cam_in,
        "entity_id": entity_id,
        "question": question,
        "answer": answer_text,
        "reasoning": content,
        "primitives": [p for p in prims],   # already plain dicts at this point
        "frame_w": w,
        "frame_h": h,
        "annotated_url": f"/reason/{cam_in}/annotated.jpg",
        "latency_ms": elapsed_ms,
        "model": VISION_MODEL,
    })
    return ReasonOut(
        camera=cam_in,
        entity_id=entity_id,
        question=question,
        reasoning=content,
        answer=answer_text,
        primitives=[Primitive(**p) for p in prims],
        frame_w=w,
        frame_h=h,
        annotated_url=f"/reason/{cam_in}/annotated.jpg",
        latency_ms=elapsed_ms,
        model=VISION_MODEL,
    )


@app.get("/reason/latest")
async def reason_latest(within_ms: int = 30_000) -> dict:
    """Return metadata for the most-recent /reason call within `within_ms`,
    or 404. The Home app uses this after an intent-end where the LLM called
    the `look` tool (with `camera: auto`) to learn which camera was routed
    to and where its annotated frame lives — that information is never
    surfaced through HA's event stream."""
    if not _recent_reasons:
        raise HTTPException(404, "no recent /reason call")
    last = _recent_reasons[-1]
    age_ms = int((time.time() - last["ts"]) * 1000)
    if age_ms > within_ms:
        raise HTTPException(404, f"most recent /reason is {age_ms}ms old (> {within_ms}ms)")
    # Strip the heavy `reasoning` blob unless asked; primitives are kept (small).
    return {
        "ts": last["ts"],
        "age_ms": age_ms,
        "camera": last["camera"],
        "entity_id": last["entity_id"],
        "question": last["question"],
        "answer": last["answer"],
        "primitives": last["primitives"],
        "frame_w": last["frame_w"],
        "frame_h": last["frame_h"],
        "annotated_url": last["annotated_url"],
        "latency_ms": last["latency_ms"],
        "model": last["model"],
    }


@app.get("/describe/latest")
async def describe_latest(within_ms: int = 30_000) -> dict:
    """Symmetric to /reason/latest for the lighter /describe path. Useful
    when the LLM picks `describe_camera` instead of `look` — the app can
    still surface the camera frame as a perception card."""
    if not _recent_describes:
        raise HTTPException(404, "no recent /describe call")
    last = _recent_describes[-1]
    age_ms = int((time.time() - last["ts"]) * 1000)
    if age_ms > within_ms:
        raise HTTPException(404, f"most recent /describe is {age_ms}ms old (> {within_ms}ms)")
    return {
        "ts": last["ts"],
        "age_ms": age_ms,
        "camera": last["camera"],
        "entity_id": last["entity_id"],
        "description": last["description"],
        "snapshot_url": last["snapshot_url"],
        "latency_ms": last["latency_ms"],
        "model": last["model"],
    }


@app.get("/reason/{camera}/annotated.jpg")
async def reason_annotated(camera: str) -> Response:
    """Serve the boxes-on-frame image from the most recent /reason call
    for `camera` — so the grounded reasoning is inspectable with no app
    build. 404 if missing or stale."""
    entity_id = resolve_entity(camera)
    entry = _reason_cache.get(entity_id)
    if entry is None:
        raise HTTPException(404, f"no /reason image for {entity_id}")
    jpeg, ts = entry
    age = time.time() - ts
    if age > SNAPSHOT_TTL_S:
        _reason_cache.pop(entity_id, None)
        raise HTTPException(404, f"/reason image for {entity_id} is stale ({age:.0f}s)")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store",
                 "X-Reason-Age-Seconds": f"{age:.1f}"},
    )


# ── /reason_zoom — the foveated crop-zoom-reason loop (Phase 0.5) ──────
# Two passes: (1) the model locates the single region most relevant to the
# question and boxes it; (2) we crop to that box and re-reason over the
# zoom-in — "the box is the saccade." The frame is grabbed full-resolution
# from the camera's RTSP main stream (enhancement rung #1 — 2304x1296 for
# the kitchen vs the 1280x720 detect sub-stream) so the crop carries real
# pixels; `stack`>1 adds temporal frame-stacking on top (rung #2). The
# 720p HA camera_proxy is the automatic fallback if RTSP is unavailable.

_zoom_overview_cache: dict[str, tuple[bytes, float]] = {}
_zoom_detail_cache: dict[str, tuple[bytes, float]] = {}

ZOOM_LOCATE_SYSTEM = (
    "You locate where to look. Given a question about an image, identify "
    "the SINGLE region that must be examined closely to answer it, and give "
    "it as <ref>region name</ref><box>x1,y1,x2,y2</box> — exactly one box, "
    "integer coordinates 0-1000, top-left origin, tight around the relevant "
    "subject."
)
ZOOM_LOCATE_USER = (
    "Question: {question}\n\nWhich single region should I zoom into to "
    "answer this? Reply with just <ref>name</ref><box>x1,y1,x2,y2</box>."
)
# Padding (fraction of the located box's size) added on each side before
# cropping, so the zoom keeps a little context.
ZOOM_PAD_FRAC = 0.12

# Temporal frame-stacking (opt-in via `stack`): fetch N frames this far
# apart and average them. The C100's per-frame sensor noise is random, so
# averaging cancels it (~sqrt(N) SNR gain) while the static scene
# reinforces — genuine signal, zero hallucination. The subject must hold
# still; motion ghosts. Capped so a big `stack` can't stall the call.
ZOOM_STACK_MAX = 8
ZOOM_STACK_INTERVAL_S = 0.3


def _data_url(jpeg: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}"


def _capped_jpeg(jpeg: bytes, max_side: int = 1280) -> bytes:
    """Re-encode so the longest side is <= max_side (no-op if already
    smaller). The full-res frame is kept for cropping; this only trims what
    gets base64'd to vLLM — Qwen3-VL's processor caps pixels anyway."""
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = im.size
    if max(w, h) <= max_side:
        return jpeg
    s = max_side / max(w, h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _rtsp_url_for(entity_id: str) -> Optional[str]:
    """Full-res RTSP URL for a camera entity, or None when there is no host
    mapping or no ffmpeg — caller then falls back to camera_proxy."""
    if not FFMPEG_BIN:
        return None
    slug = entity_id.split(".", 1)[-1]            # camera.kitchen -> kitchen
    host = RTSP_HOSTS.get(slug)
    if not host:
        return None
    cred = ""
    if RTSP_USER:
        cred = (urllib.parse.quote(RTSP_USER, safe="") + ":"
                + urllib.parse.quote(RTSP_PASS, safe="") + "@")
    return f"rtsp://{cred}{host}:{RTSP_PORT}/{RTSP_STREAM}"


def _grab_rtsp_jpeg(rtsp_url: str) -> bytes:
    """One full-resolution JPEG from a camera's RTSP main stream via ffmpeg.
    Blocking — call through asyncio.to_thread. Raises on failure."""
    proc = subprocess.run(
        [FFMPEG_BIN, "-nostdin", "-loglevel", "error",
         "-rtsp_transport", "tcp", "-i", rtsp_url,
         "-an", "-frames:v", "1", "-q:v", "2",
         "-c:v", "mjpeg", "-f", "image2pipe", "-"],
        capture_output=True, timeout=RTSP_GRAB_TIMEOUT_S)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(
            f"ffmpeg rc={proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-200:]}")
    return proc.stdout


async def _fetch_frame(client, entity_id: str) -> bytes:
    """One fresh JPEG from HA's camera proxy for `entity_id`."""
    r = await client.get(
        f"{HA_URL}/api/camera_proxy/{entity_id}",
        headers={"Authorization": f"Bearer {HA_TOKEN}"})
    if r.status_code != 200:
        raise HTTPException(
            502, f"HA camera_proxy returned {r.status_code} for {entity_id}")
    return r.content


async def _acquire_one(client, entity_id: str) -> tuple[bytes, str]:
    """Best-available single frame: the full-res RTSP main stream, falling
    back to the 720p HA camera_proxy on any failure. Returns (jpeg, source)
    with source 'rtsp' or 'camera_proxy'."""
    url = _rtsp_url_for(entity_id)
    if url:
        try:
            jpeg = await asyncio.to_thread(_grab_rtsp_jpeg, url)
            if jpeg:
                return jpeg, "rtsp"
        except Exception as e:
            log.warning("rtsp grab failed for %s (%s); camera_proxy fallback",
                        entity_id, e)
    return await _fetch_frame(client, entity_id), "camera_proxy"


async def _fetch_stacked(client, entity_id: str,
                         n: int) -> tuple[bytes, int, str]:
    """Acquire one full-res frame, or — if n>1 — N frames averaged into a
    single denoised frame (temporal frame-stacking). Returns (jpeg_bytes,
    frames_used, source). n is clamped to [1, ZOOM_STACK_MAX]. Averaging
    assumes a roughly static subject; a partial stack (camera blips
    mid-capture) is used as-is rather than failing the call."""
    n = max(1, min(ZOOM_STACK_MAX, int(n)))
    first, source = await _acquire_one(client, entity_id)
    if n == 1:
        return first, 1, source
    acc = Image.open(io.BytesIO(first)).convert("RGB")
    used = 1
    for _ in range(n - 1):
        await asyncio.sleep(ZOOM_STACK_INTERVAL_S)
        try:
            nxt_jpeg, _ = await _acquire_one(client, entity_id)
            nxt = Image.open(io.BytesIO(nxt_jpeg)).convert("RGB")
        except Exception:                                 # partial stack is fine
            break
        if nxt.size != acc.size:
            nxt = nxt.resize(acc.size)
        used += 1
        acc = Image.blend(acc, nxt, 1.0 / used)           # running mean
    buf = io.BytesIO()
    acc.save(buf, "JPEG", quality=95)
    return buf.getvalue(), used, source


async def _vllm_chat(client, image_data_urls, system, user_text, max_tokens):
    """One multimodal chat-completion call. Shared by /reason_zoom's locate
    pass and its pass-2 reasoning — pass 2 reuses the runaway-prone REASON
    prompt, so it carries the same REASON_FREQUENCY_PENALTY guard."""
    content = [{"type": "image_url", "image_url": {"url": u}}
               for u in image_data_urls]
    content.append({"type": "text", "text": user_text})
    r = await client.post(
        f"{VLLM_URL}/v1/chat/completions",
        json={"model": VISION_MODEL,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": content}],
              "stream": False, "max_tokens": max_tokens,
              "temperature": 0.2,
              "frequency_penalty": REASON_FREQUENCY_PENALTY},
        timeout=120,
    )
    if r.status_code != 200:
        raise HTTPException(502, f"vLLM returned {r.status_code}: {r.text[:500]}")
    return (r.json().get("choices") or [{}])[0].get(
        "message", {}).get("content", "").strip()


class ZoomReasonOut(BaseModel):
    camera: str
    entity_id: str
    question: str
    frames_stacked: int           # frames averaged into the source (1 = raw)
    frame_source: str             # 'rtsp' (full-res) or 'camera_proxy' (720p)
    zoom_region_px: list[int]     # [x1,y1,x2,y2] the crop taken, full-frame px
    zoom_label: str               # what pass 1 named the region
    reasoning: str                # pass-2 grounded reasoning over the crop
    answer: str                   # pass-2 clean answer
    primitives: list[Primitive]   # pass-2 boxes, mapped to full-frame
    frame_w: int
    frame_h: int
    overview_url: str             # full frame with the zoom region drawn
    detail_url: str               # the crop with pass-2 boxes drawn
    pass1_ms: int
    pass2_ms: int
    latency_ms: int
    model: str


class ZoomIn(ReasonIn):
    # Opt-in temporal frame-stacking: average this many frames into the
    # source frame to denoise the crop. 1 = raw single frame (default,
    # fastest). Clamped server-side to ZOOM_STACK_MAX.
    stack: int = 1


@app.post("/reason_zoom", response_model=ZoomReasonOut)
async def reason_zoom(body: ZoomIn) -> ZoomReasonOut:
    """Foveated crop-zoom-reason: locate the relevant region, crop to it,
    re-reason over the zoom-in. Phase 0.5 of Thinking with Visual
    Primitives. `stack`>1 averages N frames first to denoise the crop."""
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as c:
        # camera "auto" (or empty) → route by the question.
        cam_in = (body.camera or "auto").strip().lower()
        if cam_in in ("", "auto"):
            cam_in = await route_camera(c, question)
        entity_id = resolve_entity(cam_in)
        # Acquire the frame — full-res RTSP (fallback camera_proxy),
        # optionally temporal-stacked (opt-in denoise).
        jpeg, frames_stacked, frame_source = await _fetch_stacked(
            c, entity_id, body.stack)
        _snapshot_cache[entity_id] = (jpeg, time.time())
        full = Image.open(io.BytesIO(jpeg)).convert("RGB")
        fw, fh = full.size

        # ── Pass 1 — locate the region to zoom into ──────────────────
        t1 = time.monotonic()
        locate = await _vllm_chat(
            c, [_data_url(_capped_jpeg(jpeg))], ZOOM_LOCATE_SYSTEM,
            ZOOM_LOCATE_USER.format(question=question), 256)
        pass1_ms = int((time.monotonic() - t1) * 1000)
        loc = parse_primitives(locate)
        zoom_label = loc[0]["label"] if loc else "(whole frame)"
        if loc:
            bx = loc[0]["bbox_1000"]
            x0, y0 = bx[0] / 1000 * fw, bx[1] / 1000 * fh
            x1, y1 = bx[2] / 1000 * fw, bx[3] / 1000 * fh
            px, py = (x1 - x0) * ZOOM_PAD_FRAC, (y1 - y0) * ZOOM_PAD_FRAC
            cx0, cy0 = max(0, int(x0 - px)), max(0, int(y0 - py))
            cx1, cy1 = min(fw, int(x1 + px)), min(fh, int(y1 + py))
        else:
            cx0, cy0, cx1, cy1 = 0, 0, fw, fh
        if cx1 - cx0 < 8 or cy1 - cy0 < 8:      # degenerate -> whole frame
            cx0, cy0, cx1, cy1 = 0, 0, fw, fh
        crop = full.crop((cx0, cy0, cx1, cy1))
        cw, ch = crop.size
        crop_buf = io.BytesIO()
        crop.save(crop_buf, "JPEG", quality=92)
        crop_jpeg = crop_buf.getvalue()

        # ── Pass 2 — reason over the zoom-in ─────────────────────────
        t2 = time.monotonic()
        content2 = await _vllm_chat(
            c, [_data_url(_capped_jpeg(crop_jpeg))], REASON_SYSTEM,
            REASON_USER_TMPL.format(
                question=f"(You are looking at a zoomed-in crop.) {question}"),
            REASON_MAX_TOKENS)
        pass2_ms = int((time.monotonic() - t2) * 1000)

    # Pass-2 primitives are crop-relative [0,1000]; map to full-frame.
    prims2 = parse_primitives(content2)
    out_prims: list[dict] = []
    for p in prims2:
        bx = p["bbox_1000"]
        fx0, fy0 = cx0 + bx[0] / 1000 * cw, cy0 + bx[1] / 1000 * ch
        fx1, fy1 = cx0 + bx[2] / 1000 * cw, cy0 + bx[3] / 1000 * ch
        out_prims.append({
            "label": p["label"],
            "bbox_1000": [round(fx0 / fw * 1000), round(fy0 / fh * 1000),
                          round(fx1 / fw * 1000), round(fy1 / fh * 1000)],
            "bbox_px": [round(fx0), round(fy0), round(fx1), round(fy1)],
        })

    # Annotated overview (zoom region on the full frame) + detail (pass-2
    # boxes on the crop). The paper's "Original / with Visual Primitives".
    overview, _, _ = annotate_frame(jpeg, [{
        "label": zoom_label,
        "bbox_1000": [round(cx0 / fw * 1000), round(cy0 / fh * 1000),
                      round(cx1 / fw * 1000), round(cy1 / fh * 1000)]}])
    _zoom_overview_cache[entity_id] = (overview, time.time())
    detail, _, _ = annotate_frame(crop_jpeg, [
        {"label": p["label"], "bbox_1000": list(p["bbox_1000"])}
        for p in prims2])
    _zoom_detail_cache[entity_id] = (detail, time.time())

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    answer_text = _extract_answer(content2)
    log.info("reason_zoom %s ok in %dms (p1=%dms p2=%dms) src=%s %dx%d "
             "stack=%d: region=%r, %d primitive(s)", entity_id, elapsed_ms,
             pass1_ms, pass2_ms, frame_source, fw, fh, frames_stacked,
             zoom_label, len(out_prims))
    # Push into the SAME recents ring as /reason so the app's chat-side
    # perception card flow (/reason/latest fetch) catches look_zoom tool
    # calls without needing a separate /reason_zoom/latest endpoint. We
    # surface detail_url as `annotated_url` since the detail image is the
    # closest analogue to /reason's annotated frame.
    _recent_reasons.append({
        "ts": time.time(),
        "camera": cam_in,
        "entity_id": entity_id,
        "question": question,
        "answer": answer_text,
        "reasoning": content2,
        "primitives": [p for p in out_prims],
        "frame_w": fw,
        "frame_h": fh,
        "annotated_url": f"/reason_zoom/{cam_in}/detail.jpg",
        "latency_ms": elapsed_ms,
        "model": VISION_MODEL,
    })
    return ZoomReasonOut(
        camera=cam_in, entity_id=entity_id, question=question,
        frames_stacked=frames_stacked, frame_source=frame_source,
        zoom_region_px=[cx0, cy0, cx1, cy1], zoom_label=zoom_label,
        reasoning=content2, answer=answer_text,
        primitives=[Primitive(**p) for p in out_prims],
        frame_w=fw, frame_h=fh,
        overview_url=f"/reason_zoom/{cam_in}/overview.jpg",
        detail_url=f"/reason_zoom/{cam_in}/detail.jpg",
        pass1_ms=pass1_ms, pass2_ms=pass2_ms, latency_ms=elapsed_ms,
        model=VISION_MODEL)


@app.get("/reason_zoom/{camera}/{which}.jpg")
async def reason_zoom_image(camera: str, which: str) -> Response:
    """Serve the overview or detail image from the most recent /reason_zoom
    call for `camera`. 404 if missing or stale."""
    cache = {"overview": _zoom_overview_cache,
             "detail": _zoom_detail_cache}.get(which)
    if cache is None:
        raise HTTPException(404, "which must be 'overview' or 'detail'")
    entity_id = resolve_entity(camera)
    entry = cache.get(entity_id)
    if entry is None:
        raise HTTPException(404, f"no /reason_zoom {which} for {entity_id}")
    jpeg, ts = entry
    age = time.time() - ts
    if age > SNAPSHOT_TTL_S:
        cache.pop(entity_id, None)
        raise HTTPException(404, f"{which} for {entity_id} is stale ({age:.0f}s)")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
