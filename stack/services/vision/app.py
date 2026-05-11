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

Env:
  HA_URL            default http://192.168.0.125:8123
  HA_TOKEN          required
  VLLM_URL          default http://vllm:8000  (sidecar runs in the same
                    docker network; vllm is reachable by service name)
  VISION_MODEL      default qwen3-vl-30b      (served-model-name from vLLM)
  VISION_MAX_TOKENS default 200
"""
from __future__ import annotations
import base64
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("vision-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HA_URL = os.environ.get("HA_URL", "http://192.168.0.125:8123")
HA_TOKEN = os.environ["HA_TOKEN"]
VLLM_URL = os.environ.get("VLLM_URL", "http://vllm:8000")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen3-vl-30b")
VISION_MAX_TOKENS = int(os.environ.get("VISION_MAX_TOKENS", "200"))

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


app = FastAPI(title="hav-vision-sidecar", version="0.2.0")


class DescribeIn(BaseModel):
    camera: str
    question: Optional[str] = None


class DescribeOut(BaseModel):
    camera: str
    entity_id: str
    description: str
    latency_ms: int
    model: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "model": VISION_MODEL}


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
    return DescribeOut(
        camera=body.camera,
        entity_id=entity_id,
        description=out,
        latency_ms=elapsed_ms,
        model=VISION_MODEL,
    )
