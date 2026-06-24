"""Frigate-side + vision-sidecar LLM tools.

Two tools:

  find_clips(search, camera?, label?, window_min?, limit?)
    → list of recent Frigate detection events optionally narrowed by a
      semantic-search query.

  describe_clip(camera, frames?, interval_s?, question?)
    → multi-frame motion description via vision-sidecar /describe_clip
      (Addendum 27 F-3). Real-time sample of N frames over a few
      seconds + multi-image vLLM caption. Use for "did X just happen?"
      questions that single-frame describe_camera can't answer.

Pipes into Frigate's `/api/events?search=<text>...` endpoint, which uses
the semantic_search (CLIP) embeddings index when `search` is non-empty
and falls back to a chronological list otherwise. Verified live in
Addendum 27 Phase 2b probe P4 — works on Frigate 0.17.1-416a9b7.

Why this is high-leverage:
  • answers "show me clips of <thing>" without any UI/manual scrubbing
  • powers daily-recap (Addendum 27 M2) when paired with get_history
  • keeps face-rec data the world-state aggregator already consumes —
    no new pipelines, no new GPU load

Return shape mirrors the registry tools (Addendum 27 / M3):
  {
    "data":               {"clips": [...], "count": N},
    "suggested_phrasing": "I found N clips...",
    "freshness":          "fresh" | "stale" | "none"
  }

Each clip entry is compact (<= ~250 chars JSON) so multiple results
together stay well under the 32K vLLM context budget:
  {
    "id":          "<event-id>",
    "ts":          "2026-05-17T22:14:33Z",   # ISO start_time
    "camera":      "kitchen",
    "label":       "person",
    "sub_label":   "marcelo",                # null if unrecognized
    "score":       0.94,                     # face confidence (sub_label)
    "duration_s":  12.3,                     # end_time - start_time
    "has_clip":    true,
    "has_snapshot": true,
    "thumb_url":   "/api/events/<id>/thumbnail.jpg",
    "clip_url":    "/api/events/<id>/clip.mp4",
  }

The agent decides how to phrase the result. The UI can render
thumbnails by appending Frigate's base URL to the relative URLs.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..const import VISION_SIDECAR_URL
from ..frigate_sync import base_url as frigate_base_url
from .base import Function

_LOGGER = logging.getLogger(__name__)

# Defaults chosen to keep per-call latency + payload bounded:
#   limit 8  → enough for "recent activity"; 8 × ~250 char JSON ≈ 2KB
#   window  → 24h ceiling caps the search corpus to a useful slice
DEFAULT_LIMIT = 8
MAX_LIMIT = 25
DEFAULT_WINDOW_MIN = 60          # last hour by default
MAX_WINDOW_MIN = 60 * 24 * 7     # 1 week
FREEZE_DEDUP_S = 300             # treat "fresh" if newest clip < 5 min old

FIND_CLIPS_TIMEOUT_S = 6.0

# F-3 (Addendum 27): describe_clip — multi-frame motion description
# via the vision-sidecar /describe_clip endpoint. Timeout sized for
# worst-case 8 frames × 2s interval + ~3s vLLM inference, plus slack.
DESCRIBE_CLIP_TIMEOUT_S = 30.0
DESCRIBE_CLIP_DEFAULT_FRAMES = 4
DESCRIBE_CLIP_DEFAULT_INTERVAL_S = 1.0
DESCRIBE_CLIP_MAX_FRAMES = 8         # mirrors sidecar cap
DESCRIBE_CLIP_MIN_FRAMES = 2
DESCRIBE_CLIP_MAX_INTERVAL_S = 2.0   # mirrors sidecar cap
DESCRIBE_CLIP_MIN_INTERVAL_S = 0.2


class FrigateFunction(Function):
    """Dispatcher for Frigate tools. Currently one tool; same shape as
    RegistryFunction so adding tools later is just another `if name ==`
    branch."""

    def __init__(self) -> None:
        super().__init__(vol.Schema({vol.Required("name"): str}))

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        name = function_config["name"]
        if name == "find_clips":
            return await self._find_clips(arguments)
        if name == "describe_clip":
            return await self._describe_clip(arguments)
        return {"error": f"unknown frigate tool: {name}"}

    # ─── Tool: find_clips ────────────────────────────────────────────

    async def _find_clips(self, arguments: dict[str, Any]) -> dict[str, Any]:
        search = (arguments.get("search") or "").strip()
        camera = (arguments.get("camera") or "").strip() or None
        label = (arguments.get("label") or "").strip() or None
        sub_label = (arguments.get("sub_label") or "").strip() or None
        # Treat None as "use default"; 0 / negative as "clamp to minimum".
        # `or DEFAULT` would conflate the two (0 falls through to default,
        # masking the user's intent to get the smallest possible value).
        wm_raw = arguments.get("window_min")
        try:
            window_min = int(wm_raw) if wm_raw is not None and wm_raw != "" else DEFAULT_WINDOW_MIN
        except (TypeError, ValueError):
            window_min = DEFAULT_WINDOW_MIN
        window_min = max(1, min(window_min, MAX_WINDOW_MIN))
        lim_raw = arguments.get("limit")
        try:
            limit = int(lim_raw) if lim_raw is not None and lim_raw != "" else DEFAULT_LIMIT
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        base = frigate_base_url()
        params: dict[str, Any] = {"limit": str(limit)}
        if search:
            params["search"] = search
        if camera:
            params["cameras"] = camera        # Frigate accepts CSV; single is fine
        if label:
            params["labels"] = label
        if sub_label:
            params["sub_labels"] = sub_label
        # Time window via `after` (Frigate accepts unix seconds).
        after = time.time() - window_min * 60
        params["after"] = str(int(after))

        url = f"{base.rstrip('/')}/api/events"
        try:
            async with httpx.AsyncClient(timeout=FIND_CLIPS_TIMEOUT_S) as client:
                r = await client.get(url, params=params)
        except httpx.TimeoutException:
            return {
                "error": f"frigate timeout after {FIND_CLIPS_TIMEOUT_S}s",
                "data": {"clips": [], "count": 0},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"frigate unreachable: {type(e).__name__}: {e}",
                "data": {"clips": [], "count": 0},
            }

        if r.status_code != 200:
            return {
                "error": f"frigate http {r.status_code}",
                "data": {"clips": [], "count": 0},
            }

        try:
            raw = r.json() or []
        except Exception as e:  # noqa: BLE001
            return {"error": f"frigate bad json: {e}", "data": {"clips": [], "count": 0}}

        clips = []
        newest_ts = 0.0
        for evt in raw:
            try:
                start = float(evt.get("start_time") or 0)
                end = float(evt.get("end_time") or start)
                if start > newest_ts:
                    newest_ts = start
                eid = evt.get("id")
                if not eid:
                    continue
                # Frigate's sub_label is sometimes a list [name, score],
                # sometimes a bare string. Normalize both shapes.
                sub_label_raw = evt.get("sub_label")
                if isinstance(sub_label_raw, (list, tuple)) and sub_label_raw:
                    sub_label_name = str(sub_label_raw[0])
                    sub_label_score = (
                        float(sub_label_raw[1]) if len(sub_label_raw) > 1 else None
                    )
                elif isinstance(sub_label_raw, str) and sub_label_raw.lower() not in ("none", "unknown", ""):
                    sub_label_name = sub_label_raw
                    sub_label_score = None
                else:
                    sub_label_name = None
                    sub_label_score = None
                # top_score is the overall detection confidence (not face).
                top_score = evt.get("top_score") or evt.get("score")
                if top_score is not None:
                    try:
                        top_score = round(float(top_score), 3)
                    except (TypeError, ValueError):
                        top_score = None
                clips.append({
                    "id": eid,
                    "ts": _iso_from_unix(start),
                    "camera": evt.get("camera"),
                    "label": evt.get("label"),
                    "sub_label": sub_label_name,
                    "sub_label_score": (
                        round(sub_label_score, 3) if sub_label_score is not None else None
                    ),
                    "top_score": top_score,
                    "duration_s": round(max(0.0, end - start), 1),
                    "has_clip": bool(evt.get("has_clip")),
                    "has_snapshot": bool(evt.get("has_snapshot")),
                    "thumb_url": f"/api/events/{eid}/thumbnail.jpg",
                    "clip_url": f"/api/events/{eid}/clip.mp4" if evt.get("has_clip") else None,
                })
            except Exception:  # noqa: BLE001
                # Defensive — a single malformed event can't break the call
                continue

        freshness = _freshness(newest_ts, now=time.time())
        return {
            "data": {"clips": clips, "count": len(clips)},
            "suggested_phrasing": _phrasing(clips, search, camera, label),
            "freshness": freshness,
            "frigate_base_url": base,    # caller appends to thumb_url/clip_url
        }

    # ─── F-3 (Addendum 27): describe_clip — multi-frame motion ───────

    async def _describe_clip(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Sample N frames from one camera over (N-1)*interval_s seconds
        via vision-sidecar /describe_clip; returns the multi-image
        vLLM caption + timing breakdown. Use for motion queries that
        single-frame describe can't answer.
        """
        camera = (arguments.get("camera") or "").strip()
        if not camera:
            return {
                "error": "camera argument required",
                "data": {"description": "", "frames_captured": 0},
                "suggested_phrasing": "I need a camera name to look at.",
            }
        question = (arguments.get("question") or "").strip() or None

        # Same clamping pattern as find_clips. Treat None as "use
        # default"; 0 / out-of-range as "clamp to bound".
        frames_raw = arguments.get("frames")
        try:
            frames = int(frames_raw) if frames_raw is not None and frames_raw != "" else DESCRIBE_CLIP_DEFAULT_FRAMES
        except (TypeError, ValueError):
            frames = DESCRIBE_CLIP_DEFAULT_FRAMES
        frames = max(DESCRIBE_CLIP_MIN_FRAMES, min(DESCRIBE_CLIP_MAX_FRAMES, frames))

        interval_raw = arguments.get("interval_s")
        try:
            interval_s = float(interval_raw) if interval_raw is not None and interval_raw != "" else DESCRIBE_CLIP_DEFAULT_INTERVAL_S
        except (TypeError, ValueError):
            interval_s = DESCRIBE_CLIP_DEFAULT_INTERVAL_S
        interval_s = max(DESCRIBE_CLIP_MIN_INTERVAL_S, min(DESCRIBE_CLIP_MAX_INTERVAL_S, interval_s))

        url = f"{VISION_SIDECAR_URL.rstrip('/')}/describe_clip"
        payload = {"camera": camera, "frames": frames, "interval_s": interval_s}
        if question:
            payload["question"] = question

        try:
            async with httpx.AsyncClient(timeout=DESCRIBE_CLIP_TIMEOUT_S) as client:
                r = await client.post(url, json=payload)
        except httpx.TimeoutException:
            return {
                "error": f"vision-sidecar timeout after {DESCRIBE_CLIP_TIMEOUT_S}s",
                "data": {"description": "", "frames_captured": 0},
                "suggested_phrasing": "The cameras didn't respond in time.",
            }
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"vision-sidecar unreachable: {type(e).__name__}: {e}",
                "data": {"description": "", "frames_captured": 0},
                "suggested_phrasing": "I can't reach the vision service right now.",
            }

        if r.status_code != 200:
            return {
                "error": f"vision-sidecar http {r.status_code}",
                "data": {"description": "", "frames_captured": 0},
                "suggested_phrasing": "The vision service had a problem.",
            }

        try:
            body = r.json() or {}
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"vision-sidecar bad json: {e}",
                "data": {"description": "", "frames_captured": 0},
                "suggested_phrasing": "The vision service returned bad data.",
            }

        description = (body.get("description") or "").strip()
        return {
            "data": {
                "camera": body.get("camera") or camera,
                "entity_id": body.get("entity_id"),
                "description": description,
                "frames_captured": body.get("frames_captured", frames),
                "span_s": body.get("span_s"),
                "sampling_ms": body.get("sampling_ms"),
                "inference_ms": body.get("inference_ms"),
                "latency_ms": body.get("latency_ms"),
                "model": body.get("model"),
            },
            "suggested_phrasing": description or (
                f"I watched {frames} frames over {(frames - 1) * interval_s:.1f}s "
                f"and didn't see anything notable."
            ),
        }


# ── small helpers ─────────────────────────────────────────────────────

def _iso_from_unix(ts: float) -> Optional[str]:
    if ts <= 0:
        return None
    try:
        # UTC ISO with Z suffix — matches the routing log shape.
        from datetime import datetime, timezone
        return (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except Exception:
        return None


def _freshness(newest_start_ts: float, *, now: float) -> str:
    if newest_start_ts <= 0:
        return "none"
    age = now - newest_start_ts
    if age < FREEZE_DEDUP_S:
        return "fresh"
    if age < 60 * 60 * 6:    # last 6 hours
        return "stale"
    return "old"


def _phrasing(
    clips: list[dict[str, Any]],
    search: str,
    camera: Optional[str],
    label: Optional[str],
) -> str:
    n = len(clips)
    if n == 0:
        bits = []
        if search:
            bits.append(f"matching '{search}'")
        if camera:
            bits.append(f"on {camera}")
        if label:
            bits.append(f"of {label}")
        suffix = (" " + " ".join(bits)) if bits else ""
        return f"No clips found{suffix}."
    head = clips[0]
    sub_label = head.get("sub_label")
    cam = head.get("camera") or "a camera"
    ts = head.get("ts") or ""
    who = sub_label if sub_label else (head.get("label") or "something")
    if n == 1:
        return f"Found 1 clip — {who} on {cam} ({ts})."
    return f"Found {n} clips, most recent: {who} on {cam} ({ts})."
