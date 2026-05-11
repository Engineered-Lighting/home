"""
Vision sidecar: HA camera snapshot + Ollama vision model -> short text.

Exposes a tiny HTTP API that the primary text LLM (Qwen3.6-27B-FP8 in vLLM)
calls via the Extended OpenAI Conversation `describe_camera` tool. The
sidecar pulls a fresh JPEG from Home Assistant's camera_proxy and passes it
to Ollama running on the host. Returns a one-paragraph description.

Why a sidecar (instead of switching the primary LLM to a VL model):
- Keeps the text path fast (Qwen3.6 first-token <100 ms)
- Vision query is rare; pay the latency only when asked
- Vision model loads on demand and unloads after OLLAMA_KEEP_ALIVE

Env:
  HA_URL                   default http://192.168.0.125:8123
  HA_TOKEN                 required
  OLLAMA_URL               default http://192.168.0.100:11434 (Ubuntu LAN IP)
  VISION_MODEL             default qwen3-vl:8b
  VISION_NUM_CTX           default 8192
"""
from __future__ import annotations
import base64
import os
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

HA_URL = os.environ.get("HA_URL", "http://192.168.0.125:8123")
HA_TOKEN = os.environ["HA_TOKEN"]
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.0.100:11434")
VISION_MODEL = os.environ.get("VISION_MODEL", "qwen3-vl:8b")
VISION_NUM_CTX = int(os.environ.get("VISION_NUM_CTX", "8192"))

# Map friendly camera names the LLM is likely to say -> HA entity IDs.
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


app = FastAPI(title="hav-vision-sidecar")


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
    """List camera entities exposed in HA. Useful for debugging."""
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
    async with httpx.AsyncClient(timeout=30) as c:
        # 1. Get a fresh JPEG from HA's camera proxy.
        r = await c.get(
            f"{HA_URL}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        if r.status_code != 200:
            raise HTTPException(
                502, f"HA camera_proxy returned {r.status_code} for {entity_id}"
            )
        jpeg_b64 = base64.b64encode(r.content).decode()

        # 2. Hand the image + question to the vision model on Ollama.
        ollama_body = {
            "model": VISION_MODEL,
            "prompt": (
                "You are looking at a fresh snapshot from a home security camera "
                f"named {entity_id}. Answer briefly and naturally for a voice "
                f"assistant. Question: {question}"
            ),
            "images": [jpeg_b64],
            "stream": False,
            "options": {
                "num_ctx": VISION_NUM_CTX,
                "temperature": 0.3,
                "num_predict": 200,
            },
        }
        r = await c.post(
            f"{OLLAMA_URL}/api/generate", json=ollama_body, timeout=90
        )
        if r.status_code != 200:
            raise HTTPException(502, f"Ollama returned {r.status_code}: {r.text[:500]}")
        out = r.json().get("response", "").strip()

    return DescribeOut(
        camera=body.camera,
        entity_id=entity_id,
        description=out,
        latency_ms=int((time.monotonic() - t0) * 1000),
        model=VISION_MODEL,
    )
