from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ingest import canonical_json, stable_hash, utc_now


PROMPT_VERSION = "home_observation_labeler_v1"
SCHEMA_VERSION = "home_observation_labels_v1"

PRIMARY_ACTIVITY = [
    "desk_work",
    "reading",
    "watching_tv",
    "cooking",
    "eating",
    "exercise",
    "cleaning",
    "relaxing",
    "passing_through",
    "arriving_or_leaving",
    "sleeping_or_napping",
    "standing_idle",
    "unknown",
]

NOT_LABELABLE_REASONS = [
    "empty_room",
    "too_dark",
    "occluded",
    "no_person_visible",
    "ambiguous",
]

LABEL_AUDIT_ACTIONS = {
    "correct_primary_activity",
    "confirm_qwen_activity",
    "confirm_unknown_activity",
    "not_labelable_activity",
}

ENUMS: dict[str, list[str]] = {
    "presence": [
        "empty",
        "person_present",
        "multiple_people",
        "non_person_motion",
        "uncertain",
    ],
    "primary_activity": PRIMARY_ACTIVITY,
    "motion_phase": [
        "still",
        "moving_within_room",
        "entering_zone",
        "leaving_zone",
        "passing_through",
        "uncertain",
    ],
    "posture": [
        "seated",
        "standing",
        "lying_down",
        "bending_or_reaching",
        "moving",
        "not_visible",
        "uncertain",
    ],
    "task_surface": [
        "desk",
        "kitchen_counter",
        "island",
        "sink",
        "dining_table",
        "sofa",
        "weights_area",
        "doorway",
        "floor",
        "other",
        "none_visible",
        "uncertain",
    ],
    "visual_focus": [
        "screen",
        "book_or_paper",
        "laptop_or_keyboard",
        "phone_or_tablet",
        "food_or_drink",
        "cookware",
        "cleaning_item",
        "exercise_equipment",
        "none_visible",
        "uncertain",
    ],
    "screen_visual_state": [
        "tv_visible_on",
        "tv_visible_off",
        "screen_light_visible",
        "no_screen_visible",
        "uncertain",
    ],
    "room_lighting_visual": [
        "dark",
        "dim",
        "bright",
        "glare_or_overlit",
        "uneven",
        "uncertain",
    ],
    "image_quality": [
        "clear",
        "blurry",
        "too_dark",
        "overexposed",
        "occluded",
        "partial_person",
        "camera_blocked",
        "uncertain",
    ],
    "privacy_sensitivity": [
        "normal",
        "private_activity_possible",
        "face_closeup",
        "screen_content_visible",
        "uncertain",
    ],
    "metadata_consistency": [
        "consistent",
        "metadata_contradiction",
        "insufficient_visual_evidence",
        "uncertain",
    ],
}

AXES = list(ENUMS.keys())
AXIS_KEYS = {"label", "confidence", "evidence_frames"}
TOP_LEVEL_KEYS = {
    "schema_version",
    "presence",
    "primary_activity",
    "secondary_activities",
    "motion_phase",
    "posture",
    "task_surface",
    "visual_focus",
    "screen_visual_state",
    "room_lighting_visual",
    "image_quality",
    "privacy_sensitivity",
    "metadata_consistency",
    "abstain_reason",
    "contradictions",
    "short_visible_summary",
}

ZONE_CAMERA = {
    "office": "living_room",
    "sofa": "living_room",
    "weights": "living_room",
    "front_left": "living_room",
    "front_door": "driveway",
    "whole_living_room": "living_room",
    "dining_left": "dining_room",
    "dining_right": "dining_room",
    "whole_dining_room": "dining_room",
    "sink": "kitchen",
    "island_left": "kitchen",
    "island_right": "kitchen",
    "whole_kitchen": "kitchen",
    "workshop_zone": "workshop",
    "e28": "driveway",
}

CAMERA_ALIASES = {
    "kitchen": "camera.kitchen",
    "living_room": "camera.living_room",
    "dining_room": "camera.dining_room",
    "workshop": "camera.workshop",
    "driveway": "camera.driveway",
}

DEFAULT_RETENTION = {
    "raw_frame_retention_days": 14,
    "labels_durable": True,
    "training_clip_enabled": False,
    "cloud_upload_allowed": False,
}

INFERENCE_DEFAULTS = {
    "temperature": 0,
    "max_tokens": 700,
    "image_max": 4,
    "video_max": 0,
    "one_packet_per_call": True,
}

SYSTEM_PROMPT = (
    "You label visible home context from a small set of timestamped camera frames. "
    "Return only valid JSON matching the requested schema. Use only the allowed labels. "
    "Prefer unknown or uncertain when evidence is weak. Do not identify people. "
    "Do not infer emotion, health, protected traits, intent, or preference. "
    "Do not read or transcribe screens. Home Assistant metadata is provided as "
    "device truth for reconciliation only. Visual labels must be based on what "
    "is visible in the frames."
)

_ring_task: asyncio.Task | None = None
_ring_state: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_tick_at": None,
    "last_error": None,
}
_ring_index: dict[str, deque[dict[str, Any]]] = {}


def multimodal_data_dir() -> Path:
    raw = os.environ.get("INTELLIGENCE_MULTIMODAL_DIR")
    if raw:
        return Path(raw)
    data_dir = Path(os.environ.get("INTELLIGENCE_DATA_DIR", "/data"))
    return data_dir / "multimodal"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def multimodal_config() -> dict[str, Any]:
    cameras_raw = os.environ.get(
        "INTELLIGENCE_MULTIMODAL_CAMERAS",
        "living_room,kitchen,dining_room,workshop,driveway",
    )
    cameras = [c.strip() for c in cameras_raw.split(",") if c.strip()]
    pilot_zones_raw = os.environ.get("INTELLIGENCE_MULTIMODAL_PILOT_ZONES", "office")
    pilot_kinds_raw = os.environ.get(
        "INTELLIGENCE_MULTIMODAL_PILOT_KINDS",
        "pending_preference,override_event",
    )
    return {
        "enabled": _env_bool("INTELLIGENCE_MULTIMODAL_ENABLED", True),
        "ring_enabled": _env_bool("INTELLIGENCE_MULTIMODAL_RING_ENABLED", False),
        "ring_interval_s": _env_float("INTELLIGENCE_MULTIMODAL_RING_INTERVAL_S", 2.0, 1.0),
        "ring_retention_s": _env_float("INTELLIGENCE_MULTIMODAL_RING_RETENTION_S", 90.0, 30.0),
        "capture_frames_default": _env_bool("INTELLIGENCE_MULTIMODAL_CAPTURE_FRAMES", True),
        "max_captures_per_hour": _env_int("INTELLIGENCE_MULTIMODAL_MAX_CAPTURES_PER_HOUR", 24, 1),
        "max_qwen_jobs_per_hour": _env_int("INTELLIGENCE_MULTIMODAL_MAX_QWEN_JOBS_PER_HOUR", 12, 1),
        "min_disk_free_mb": _env_int("INTELLIGENCE_MULTIMODAL_MIN_DISK_FREE_MB", 2048, 256),
        "frame_longest_edge_px": _env_int("INTELLIGENCE_MULTIMODAL_FRAME_EDGE_PX", 768, 128),
        "qwen_base_url": os.environ.get("INTELLIGENCE_QWEN_BASE_URL")
        or os.environ.get("INTELLIGENCE_VLLM_URL")
        or os.environ.get("VLLM_URL")
        or "http://vllm:8000",
        "qwen_model": os.environ.get("INTELLIGENCE_QWEN_MODEL")
        or os.environ.get("VISION_MODEL")
        or "qwen3-vl-30b",
        "ha_base_url": os.environ.get("INTELLIGENCE_HA_BASE_URL")
        or os.environ.get("HA_URL")
        or "http://192.168.0.125:8123",
        "has_ha_token": bool(os.environ.get("INTELLIGENCE_HA_TOKEN") or os.environ.get("HA_TOKEN")),
        "cameras": cameras,
        "pilot_enabled": _env_bool("INTELLIGENCE_MULTIMODAL_PILOT_ENABLED", False),
        "pilot_zones": [z.strip() for z in pilot_zones_raw.split(",") if z.strip()],
        "pilot_kinds": [k.strip() for k in pilot_kinds_raw.split(",") if k.strip()],
        "pilot_lookback_s": _env_float("INTELLIGENCE_MULTIMODAL_PILOT_LOOKBACK_S", 180.0, 30.0),
        "pilot_max_packets_per_run": _env_int("INTELLIGENCE_MULTIMODAL_PILOT_MAX_PACKETS_PER_RUN", 2, 1),
        "pilot_auto_label": _env_bool("INTELLIGENCE_MULTIMODAL_PILOT_AUTO_LABEL", True),
        "pilot_min_label_frames": _env_int("INTELLIGENCE_MULTIMODAL_PILOT_MIN_LABEL_FRAMES", 2, 1),
        "data_dir": str(multimodal_data_dir()),
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "inference_defaults": INFERENCE_DEFAULTS,
    }


def _ha_token() -> str | None:
    return os.environ.get("INTELLIGENCE_HA_TOKEN") or os.environ.get("HA_TOKEN")


def camera_for_zone(zone: str | None) -> str | None:
    if not zone:
        return None
    return ZONE_CAMERA.get(zone) or ZONE_CAMERA.get(zone.lower())


def resolve_camera_entity(camera: str | None) -> str | None:
    if not camera:
        return None
    if camera.startswith("camera."):
        return camera
    return CAMERA_ALIASES.get(camera) or f"camera.{camera.replace(' ', '_')}"


def _packet_id(prefix: str = "op") -> str:
    now = datetime.now(timezone.utc)
    return f"{prefix}-{int(now.timestamp())}-{now.microsecond:06d}"


def _safe_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _row_to_observation_metadata(row: sqlite3.Row) -> dict[str, Any]:
    context = _decode_json(row["context_json"], {})
    predictions = _decode_json(row["predictions_by_zone_json"], {})
    return {
        "source": "preference_observations",
        "observation_id": row["id"],
        "kind": row["kind"],
        "ts": row["ts"],
        "zone": row["zone"],
        "primary_zone": row["primary_zone"],
        "light_entity": row["light_entity"],
        "light_state": row["light_state"],
        "actual_brightness_pct": row["actual_brightness_pct"],
        "predicted_brightness_pct": row["predicted_brightness_pct"],
        "delta_pct": row["delta_pct"],
        "profile": row["profile"],
        "state": row["state"],
        "tv_playing": bool(row["tv_playing"]) if row["tv_playing"] is not None else None,
        "asleep": bool(row["asleep"]) if row["asleep"] is not None else None,
        "night_safe": bool(row["night_safe"]) if row["night_safe"] is not None else None,
        "user_at_home": bool(row["user_at_home"]) if row["user_at_home"] is not None else None,
        "shadow_mode": bool(row["shadow_mode"]),
        "context_id": row["context_id"],
        "evidence_id": row["evidence_id"],
        "dedupe_key": row["dedupe_key"],
        "related_override_context_id": row["related_override_context_id"],
        "predictions_by_zone": predictions,
        "capture_provenance": _decode_json(row["capture_provenance_json"], {}),
        "prediction_consistency": _decode_json(row["prediction_consistency_json"], {}),
        "occupancy": _decode_json(row["occupancy_json"], {}),
        "context": context,
    }


def _privacy_flags_from_metadata(metadata: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if metadata.get("context", {}).get("asleep") or metadata.get("asleep"):
        flags.append("private_activity_possible")
    return flags


def _disk_ok(path: Path) -> tuple[bool, str | None]:
    cfg = multimodal_config()
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_mb = usage.free / (1024 * 1024)
    if free_mb < cfg["min_disk_free_mb"]:
        return False, f"disk_free_low:{free_mb:.0f}MB"
    return True, None


def _prepare_jpeg(raw: bytes) -> tuple[bytes, int | None, int | None, list[str]]:
    flags: list[str] = []
    try:
        from PIL import Image, ImageOps

        cfg = multimodal_config()
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image).convert("RGB")
        longest = max(image.size)
        if longest > cfg["frame_longest_edge_px"]:
            scale = cfg["frame_longest_edge_px"] / float(longest)
            new_size = (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale)))
            image = image.resize(new_size)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), image.size[0], image.size[1], flags
    except Exception as exc:
        flags.append(f"jpeg_prepare_failed:{type(exc).__name__}")
        return raw, None, None, flags


def fetch_camera_frame(camera: str) -> tuple[bytes, dict[str, Any]]:
    cfg = multimodal_config()
    token = _ha_token()
    entity = resolve_camera_entity(camera)
    if not token:
        raise RuntimeError("HA token unavailable")
    if not entity:
        raise RuntimeError("camera unavailable")
    url = f"{cfg['ha_base_url'].rstrip('/')}/api/camera_proxy/{entity}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HA camera_proxy returned {exc.code} for {entity}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HA camera_proxy unreachable for {entity}: {exc.reason}") from exc
    return raw, {
        "entity_id": entity,
        "status": status,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "source": "ha_camera_proxy",
    }


def _insert_frame(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    frame_id: str,
    frame_role: str,
    relative_ts_s: float | None,
    captured_at: str,
    camera: str,
    path: Path,
    width: int | None,
    height: int | None,
    byte_count: int,
    source: str,
    promoted_from_ring: bool = False,
    privacy_flags: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO observation_frames
            (id, packet_id, frame_id, frame_role, relative_ts_s, captured_at,
             camera, path, width, height, bytes, source, promoted_from_ring,
             privacy_flags_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{packet_id}:{frame_id}",
            packet_id,
            frame_id,
            frame_role,
            relative_ts_s,
            captured_at,
            camera,
            str(path),
            width,
            height,
            byte_count,
            source,
            1 if promoted_from_ring else 0,
            canonical_json(privacy_flags or []),
        ),
    )


def _ring_dir(camera: str) -> Path:
    return multimodal_data_dir() / "ring" / _safe_slug(camera)


def _packet_frame_dir(packet_id: str) -> Path:
    return multimodal_data_dir() / "frames" / _safe_slug(packet_id)


def _prune_ring(camera: str, now_epoch: float) -> None:
    cfg = multimodal_config()
    cutoff = now_epoch - cfg["ring_retention_s"]
    entries = _ring_index.setdefault(camera, deque())
    while entries and float(entries[0].get("epoch", 0)) < cutoff:
        old = entries.popleft()
        with suppress(Exception):
            Path(old["path"]).unlink(missing_ok=True)
    root = _ring_dir(camera)
    if root.exists():
        for path in root.glob("*.jpg"):
            try:
                stem = path.stem
                epoch_ms = int(stem.split("-", 1)[0])
                if epoch_ms / 1000.0 < cutoff:
                    path.unlink(missing_ok=True)
            except Exception:
                continue

def _store_ring_frame(camera: str) -> dict[str, Any]:
    raw, source_meta = fetch_camera_frame(camera)
    jpeg, width, height, flags = _prepare_jpeg(raw)
    now_epoch = time.time()
    captured_at = _iso_from_epoch(now_epoch)
    root = _ring_dir(camera)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{int(now_epoch * 1000)}-{stable_hash(jpeg)[:8]}.jpg"
    path.write_bytes(jpeg)
    entry = {
        "camera": camera,
        "captured_at": captured_at,
        "epoch": now_epoch,
        "path": str(path),
        "width": width,
        "height": height,
        "bytes": len(jpeg),
        "source": source_meta,
        "prepare_flags": flags,
    }
    _ring_index.setdefault(camera, deque()).append(entry)
    _prune_ring(camera, now_epoch)
    return entry


async def _ring_loop() -> None:
    cfg = multimodal_config()
    _ring_state.update({"enabled": cfg["ring_enabled"], "running": True, "last_error": None})
    try:
        while multimodal_config()["ring_enabled"]:
            cfg = multimodal_config()
            for camera in cfg["cameras"]:
                try:
                    await asyncio.to_thread(_store_ring_frame, camera)
                except Exception as exc:
                    _ring_state["last_error"] = f"{camera}: {exc}"
            _ring_state["last_tick_at"] = utc_now()
            await asyncio.sleep(cfg["ring_interval_s"])
    finally:
        _ring_state["running"] = False


def start_ring_buffer() -> None:
    global _ring_task
    cfg = multimodal_config()
    _ring_state.update({"enabled": cfg["ring_enabled"]})
    if not cfg["ring_enabled"] or _ring_task is not None:
        return
    _ring_task = asyncio.create_task(_ring_loop())


async def stop_ring_buffer() -> None:
    global _ring_task
    if _ring_task is None:
        return
    _ring_task.cancel()
    try:
        await _ring_task
    except asyncio.CancelledError:
        pass
    _ring_task = None


def ring_buffer_status() -> dict[str, Any]:
    cfg = multimodal_config()
    return {
        **_ring_state,
        "configured": cfg["ring_enabled"],
        "interval_s": cfg["ring_interval_s"],
        "retention_s": cfg["ring_retention_s"],
        "cameras": [
            {
                "camera": camera,
                "frames": len(_ring_index.get(camera, [])),
                "latest_at": (_ring_index.get(camera) or [{}])[-1].get("captured_at")
                if _ring_index.get(camera)
                else None,
            }
            for camera in cfg["cameras"]
        ],
    }


def _nearest_ring_frame(camera: str, target: datetime | None) -> dict[str, Any] | None:
    entries = list(_ring_index.get(camera, []))
    if not entries or target is None:
        return None
    target_epoch = target.timestamp()
    cfg = multimodal_config()
    best = min(entries, key=lambda e: abs(float(e.get("epoch", 0)) - target_epoch))
    if abs(float(best.get("epoch", 0)) - target_epoch) > cfg["ring_retention_s"]:
        return None
    return best


def _promote_ring_frame(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    camera: str,
    frame_id: str,
    role: str,
    relative_ts_s: float,
    target: datetime | None,
    privacy_flags: list[str],
) -> bool:
    entry = _nearest_ring_frame(camera, target)
    if not entry:
        return False
    src = Path(entry["path"])
    if not src.exists():
        return False
    dst_root = _packet_frame_dir(packet_id)
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / f"{frame_id}.jpg"
    shutil.copyfile(src, dst)
    _insert_frame(
        conn,
        packet_id=packet_id,
        frame_id=frame_id,
        frame_role=role,
        relative_ts_s=relative_ts_s,
        captured_at=entry["captured_at"],
        camera=camera,
        path=dst,
        width=entry.get("width"),
        height=entry.get("height"),
        byte_count=int(entry.get("bytes") or dst.stat().st_size),
        source="ring_buffer",
        promoted_from_ring=True,
        privacy_flags=privacy_flags,
    )
    return True


def _capture_current_frame(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    camera: str,
    frame_id: str,
    role: str,
    relative_ts_s: float | None,
    privacy_flags: list[str],
) -> dict[str, Any]:
    raw, source_meta = fetch_camera_frame(camera)
    jpeg, width, height, flags = _prepare_jpeg(raw)
    root = _packet_frame_dir(packet_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{frame_id}.jpg"
    path.write_bytes(jpeg)
    captured_at = utc_now()
    _insert_frame(
        conn,
        packet_id=packet_id,
        frame_id=frame_id,
        frame_role=role,
        relative_ts_s=relative_ts_s,
        captured_at=captured_at,
        camera=camera,
        path=path,
        width=width,
        height=height,
        byte_count=len(jpeg),
        source=source_meta.get("source", "ha_camera_proxy"),
        privacy_flags=privacy_flags + flags,
    )
    return {"captured_at": captured_at, "prepare_flags": flags, **source_meta}


def _rate_limit_ok(conn: sqlite3.Connection, job_kind: str, max_per_hour: int) -> tuple[bool, int]:
    cutoff = datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat()
    if job_kind == "capture":
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM observation_packets WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()["c"]
    else:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM qwen_label_results WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()["c"]
    return count < max_per_hour, int(count)


def create_observation_packet(
    conn: sqlite3.Connection,
    *,
    observation_id: str,
    capture_frames: bool | None = None,
    force: bool = False,
    trigger: str = "manual_api",
    post_frame_delay_s: float = 0.0,
    allow_current_fallback: bool = True,
) -> dict[str, Any]:
    cfg = multimodal_config()
    if not cfg["enabled"]:
        raise ValueError("multimodal capture is disabled")
    existing = conn.execute(
        """
        SELECT id
        FROM observation_packets
        WHERE observation_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (observation_id,),
    ).fetchone()
    if existing and not force:
        return get_observation_packet(conn, existing["id"]) | {"created": False}

    ok, used = _rate_limit_ok(conn, "capture", cfg["max_captures_per_hour"])
    if not ok:
        raise ValueError(f"capture rate limit exceeded ({used}/hour)")

    row = conn.execute(
        "SELECT * FROM preference_observations WHERE id = ?",
        (observation_id,),
    ).fetchone()
    if not row:
        raise ValueError("observation not found")

    metadata = _row_to_observation_metadata(row)
    raw_row = conn.execute(
        "SELECT redacted_payload_json FROM raw_events WHERE id = ?",
        (row["raw_event_id"],),
    ).fetchone()
    if raw_row:
        raw_payload = _decode_json(raw_row["redacted_payload_json"], {})
        if isinstance(raw_payload, dict) and raw_payload.get("light_transition"):
            metadata["light_transition"] = raw_payload.get("light_transition")
    zone = row["zone"]
    camera = camera_for_zone(zone)
    camera_entity = resolve_camera_entity(camera)
    packet_id = _packet_id("op")
    created_at = utc_now()
    event_ts = row["ts"]
    privacy_flags = _privacy_flags_from_metadata(metadata)
    source_ids = {
        "observation_id": row["id"],
        "raw_event_id": row["raw_event_id"],
        "context_id": row["context_id"],
        "evidence_id": row["evidence_id"],
        "related_override_context_id": row["related_override_context_id"],
    }
    model_versions = {
        "qwen_model": cfg["qwen_model"],
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }
    conn.execute(
        """
        INSERT INTO observation_packets
            (id, observation_id, event_id, event_type, zone, camera, camera_entity,
             trigger, event_ts, created_at, status, privacy_flags_json,
             retention_policy_json, ha_metadata_json, source_ids_json,
             model_versions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            packet_id,
            row["id"],
            row["context_id"] or row["evidence_id"] or row["id"],
            row["kind"],
            zone,
            camera,
            camera_entity,
            trigger,
            event_ts,
            created_at,
            "packet_created",
            canonical_json(privacy_flags),
            canonical_json(DEFAULT_RETENTION),
            canonical_json(metadata),
            canonical_json(source_ids),
            canonical_json(model_versions),
        ),
    )

    should_capture = cfg["capture_frames_default"] if capture_frames is None else capture_frames
    if should_capture and camera:
        capture_packet_frames(
            conn,
            packet_id=packet_id,
            post_frame_delay_s=post_frame_delay_s,
            allow_current_fallback=allow_current_fallback,
        )
    conn.commit()
    return get_observation_packet(conn, packet_id) | {"created": True}


def create_recent_observation_packets(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
    kind: str | None = None,
    capture_frames: bool = False,
    trigger: str = "recent_batch",
    allow_current_fallback: bool = True,
) -> dict[str, Any]:
    params: list[Any] = []
    where = [
        """
        NOT EXISTS (
          SELECT 1
          FROM observation_packets op
          WHERE op.observation_id = po.id
        )
        """
    ]
    if kind:
        where.append("po.kind = ?")
        params.append(kind)
    sql = "SELECT po.id FROM preference_observations po WHERE " + " AND ".join(where)
    sql += " ORDER BY po.ts DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    packets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        try:
            packets.append(
                create_observation_packet(
                    conn,
                    observation_id=row["id"],
                    capture_frames=capture_frames,
                    force=False,
                    trigger=trigger,
                    allow_current_fallback=allow_current_fallback,
                )["packet"]
            )
        except Exception as exc:
            errors.append({"observation_id": row["id"], "error": str(exc)[:240]})
    return {"ok": not errors, "created": len(packets), "packets": packets, "errors": errors}


def capture_packet_frames(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    post_frame_delay_s: float = 0.0,
    allow_current_fallback: bool = True,
) -> dict[str, Any]:
    cfg = multimodal_config()
    root = multimodal_data_dir()
    disk_ok, disk_reason = _disk_ok(root)
    if not disk_ok:
        conn.execute(
            "UPDATE observation_packets SET status = ?, error = ? WHERE id = ?",
            ("capture_deferred", disk_reason, packet_id),
        )
        conn.commit()
        return {"ok": False, "reason": disk_reason, "captured": 0}

    packet = conn.execute("SELECT * FROM observation_packets WHERE id = ?", (packet_id,)).fetchone()
    if not packet:
        raise ValueError("packet not found")
    camera = packet["camera"]
    if not camera:
        raise ValueError("packet has no camera")
    privacy_flags = _decode_json(packet["privacy_flags_json"], [])
    event_dt = _parse_ts(packet["event_ts"])
    roles = [
        ("frame_1", "pre_event", -5.0),
        ("frame_2", "event", 0.0),
        ("frame_3", "user_landed_or_capture", 0.0),
    ]
    captured = 0
    missing: list[str] = []
    for frame_id, role, rel in roles:
        target = None
        if event_dt is not None:
            target = datetime.fromtimestamp(event_dt.timestamp() + rel, timezone.utc)
        if _promote_ring_frame(
            conn,
            packet_id=packet_id,
            camera=camera,
            frame_id=frame_id,
            role=role,
            relative_ts_s=rel,
            target=target,
            privacy_flags=privacy_flags,
        ):
            captured += 1
        else:
            missing.append(frame_id)

    if captured == 0 and allow_current_fallback:
        try:
            _capture_current_frame(
                conn,
                packet_id=packet_id,
                camera=camera,
                frame_id="frame_2",
                role="event_or_current",
                relative_ts_s=0.0,
                privacy_flags=privacy_flags,
            )
            captured += 1
        except Exception as exc:
            missing.append(f"current:{exc}")

    if post_frame_delay_s > 0 and captured < 4:
        time.sleep(min(post_frame_delay_s, 15.0))
        try:
            _capture_current_frame(
                conn,
                packet_id=packet_id,
                camera=camera,
                frame_id="frame_4",
                role="post_event",
                relative_ts_s=post_frame_delay_s,
                privacy_flags=privacy_flags,
            )
            captured += 1
        except Exception as exc:
            missing.append(f"post:{exc}")

    frame_count = conn.execute(
        "SELECT COUNT(*) AS c FROM observation_frames WHERE packet_id = ? AND deleted_at IS NULL",
        (packet_id,),
    ).fetchone()["c"]
    status = "frames_ready" if frame_count else "frames_missing"
    error = None if frame_count else "; ".join(missing)[:500]
    conn.execute(
        "UPDATE observation_packets SET status = ?, frame_count = ?, error = ? WHERE id = ?",
        (status, frame_count, error, packet_id),
    )
    conn.commit()
    return {"ok": bool(frame_count), "captured": frame_count, "missing": missing, "ring_used": captured > 0}


def _capture_timing_from_frames(
    packet: dict[str, Any],
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    active_frames = [f for f in frames if not f.get("deleted_at")]
    roles = {f.get("frame_role") for f in active_frames}
    ring_frames = [f for f in active_frames if f.get("source") == "ring_buffer" or f.get("promoted_from_ring")]
    rels = [
        float(f["relative_ts_s"])
        for f in active_frames
        if f.get("relative_ts_s") is not None
    ]
    has_pre = any(rel <= -3 for rel in rels)
    has_event = any(abs(rel) <= 2 for rel in rels)
    has_post = any(rel >= 3 for rel in rels) or "post_event" in roles
    aligned = bool(len(active_frames) >= 2 and has_event and (has_pre or has_post))
    quality = "aligned" if aligned else "partial" if active_frames else "missing"
    if active_frames and not ring_frames:
        quality = "current_only"
    return {
        "quality": quality,
        "event_aligned": aligned,
        "frame_count": len(active_frames),
        "ring_frame_count": len(ring_frames),
        "has_pre_event": has_pre,
        "has_event": has_event,
        "has_post_event": has_post,
        "roles": sorted(r for r in roles if r),
        "source": "ring_buffer" if ring_frames else "current_or_manual" if active_frames else "none",
    }


def _label_usability(
    packet: dict[str, Any],
    labels: dict[str, Any] | None,
    timing: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if not labels:
        blockers.append("no valid qwen labels")
    if not timing.get("event_aligned"):
        blockers.append("capture timing is not event-aligned")
    if int(packet.get("frame_count") or 0) < 2:
        blockers.append("fewer than 2 frames")
    if labels:
        activity = labels.get("primary_activity") or {}
        quality = labels.get("image_quality") or {}
        privacy = labels.get("privacy_sensitivity") or {}
        if float(activity.get("confidence") or 0) < 0.65:
            blockers.append("primary activity confidence below 0.65")
        if quality.get("label") in {"blurry", "too_dark", "overexposed", "occluded", "camera_blocked", "uncertain"}:
            blockers.append(f"image quality is {quality.get('label')}")
        if privacy.get("label") not in {None, "normal"}:
            blockers.append(f"privacy sensitivity is {privacy.get('label')}")
    return {
        "usable_for_clustering": not blockers,
        "usable_for_proposals": False,
        "reason": "visual labels are context only; proposals still require preference evidence and human approval",
        "blockers": blockers,
    }


def _axis(label: str, confidence: float, evidence_frames: list[str] | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "evidence_frames": evidence_frames or [],
    }


def _primary_activity(labels: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(labels, dict):
        return _axis("unknown", 0.0)
    value = labels.get("primary_activity")
    if isinstance(value, dict):
        return _axis(
            str(value.get("label") or "unknown"),
            float(value.get("confidence") or 0.0),
            list(value.get("evidence_frames") or []),
        )
    return _axis("unknown", 0.0)


def _axis_value(labels: dict[str, Any] | None, axis: str) -> dict[str, Any]:
    if not isinstance(labels, dict):
        return _axis("unknown" if axis == "primary_activity" else "uncertain", 0.0)
    value = labels.get(axis)
    if isinstance(value, dict):
        return {
            "label": value.get("label") or ("unknown" if axis == "primary_activity" else "uncertain"),
            "confidence": float(value.get("confidence") or 0.0),
            "evidence_frames": list(value.get("evidence_frames") or []),
        }
    return _axis("unknown" if axis == "primary_activity" else "uncertain", 0.0)


def _confident_empty(labels: dict[str, Any] | None) -> bool:
    presence = _axis_value(labels, "presence")
    return presence.get("label") == "empty" and float(presence.get("confidence") or 0.0) >= 0.75


def _label_audit_target_id(audit: dict[str, Any]) -> int | None:
    body = audit.get("body") or {}
    raw = body.get("target_audit_id") or body.get("retracts_audit_id") or body.get("audit_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _human_label_state(
    audits: list[dict[str, Any]],
    qwen_labels: dict[str, Any] | None,
) -> dict[str, Any]:
    retracted = {
        target
        for audit in audits
        if audit.get("action") == "retract_label_audit"
        for target in [_label_audit_target_id(audit)]
        if target is not None
    }
    overrides: dict[str, Any] = {}
    latest_review: dict[str, Any] | None = None
    qwen_activity = _primary_activity(qwen_labels)
    for audit in sorted(audits, key=lambda a: int(a.get("id") or 0)):
        audit_id = int(audit.get("id") or 0)
        if audit_id in retracted:
            continue
        action = audit.get("action")
        body = audit.get("body") or {}
        if action == "correct_primary_activity":
            label = str(body.get("label") or "")
            if label in PRIMARY_ACTIVITY:
                latest_review = {
                    "audit_id": audit_id,
                    "action": action,
                    "axis": "primary_activity",
                    "created_at": audit.get("created_at"),
                }
                overrides["primary_activity"] = {
                    "label": label,
                    "confidence": float(body.get("confidence") or 1.0),
                    "evidence_frames": list(body.get("evidence_frames") or []),
                    "source": "human_correction",
                    "audit_id": audit_id,
                    "created_at": audit.get("created_at"),
                    "previous_qwen_label": body.get("previous_qwen_label") or qwen_activity.get("label"),
                    "previous_qwen_confidence": body.get("previous_qwen_confidence", qwen_activity.get("confidence")),
                }
        elif action == "confirm_qwen_activity":
            latest_review = {
                "audit_id": audit_id,
                "action": action,
                "axis": "primary_activity",
                "created_at": audit.get("created_at"),
            }
            overrides["primary_activity"] = {
                **qwen_activity,
                "source": "human_confirmed_qwen",
                "audit_id": audit_id,
                "created_at": audit.get("created_at"),
            }
        elif action == "confirm_unknown_activity":
            latest_review = {
                "audit_id": audit_id,
                "action": action,
                "axis": "primary_activity",
                "created_at": audit.get("created_at"),
            }
            overrides["primary_activity"] = {
                "label": "unknown",
                "confidence": 1.0,
                "evidence_frames": [],
                "source": "human_confirmed_unknown",
                "audit_id": audit_id,
                "created_at": audit.get("created_at"),
            }
        elif action == "not_labelable_activity":
            latest_review = {
                "audit_id": audit_id,
                "action": action,
                "axis": "primary_activity",
                "created_at": audit.get("created_at"),
                "reason": body.get("reason"),
            }
            overrides["primary_activity"] = {
                "label": "unknown",
                "confidence": 1.0,
                "evidence_frames": [],
                "source": "human_not_labelable",
                "audit_id": audit_id,
                "created_at": audit.get("created_at"),
                "reason": body.get("reason"),
            }
    return {
        "overrides": overrides,
        "latest_review": latest_review,
        "retracted_audit_ids": sorted(retracted),
    }


def _needs_label_review(
    *,
    packet: dict[str, Any],
    qwen_labels: dict[str, Any] | None,
    qwen_status: str | None,
    human_state: dict[str, Any],
) -> dict[str, Any]:
    if human_state.get("latest_review"):
        return {"primary_activity": False, "reasons": [], "reviewed": True}
    reasons: list[str] = []
    if qwen_status == "invalid":
        reasons.append("invalid qwen label")
    elif qwen_status in {None, "not_requested", "no_frames"} or not qwen_labels:
        reasons.append("no qwen label")
    if qwen_labels:
        activity = _primary_activity(qwen_labels)
        label = activity.get("label")
        confidence = float(activity.get("confidence") or 0.0)
        if label in {None, "", "unknown"}:
            if not _confident_empty(qwen_labels):
                reasons.append("unknown activity")
        elif confidence < 0.55:
            reasons.append("low confidence")
        quality = _axis_value(qwen_labels, "image_quality")
        if quality.get("label") in {"blurry", "too_dark", "overexposed", "occluded", "camera_blocked", "uncertain"}:
            reasons.append(f"image quality: {quality.get('label')}")
        privacy = _axis_value(qwen_labels, "privacy_sensitivity")
        if privacy.get("label") in {"uncertain", "private_activity_possible", "face_closeup", "screen_content_visible"}:
            reasons.append(f"privacy: {privacy.get('label')}")
        consistency = _axis_value(qwen_labels, "metadata_consistency")
        if consistency.get("label") in {"metadata_contradiction", "insufficient_visual_evidence", "uncertain"}:
            reasons.append(f"metadata: {consistency.get('label')}")
    if int(packet.get("frame_count") or 0) == 0 and "no qwen label" not in reasons:
        reasons.append("no frames")
    deduped = list(dict.fromkeys(reasons))
    return {
        "primary_activity": bool(deduped),
        "reasons": deduped,
        "reviewed": False,
    }


def _apply_effective_labels(
    packet: dict[str, Any],
    *,
    qwen_labels: dict[str, Any] | None,
    qwen_status: str | None,
    audits: list[dict[str, Any]],
) -> dict[str, Any]:
    effective = dict(qwen_labels or {})
    human_state = _human_label_state(audits, qwen_labels)
    overrides = human_state["overrides"]
    if overrides.get("primary_activity"):
        effective["primary_activity"] = {
            k: v
            for k, v in overrides["primary_activity"].items()
            if k in {"label", "confidence", "evidence_frames"}
        }
    packet["human_label_overrides"] = overrides
    packet["effective_labels"] = effective or None
    packet["needs_review"] = _needs_label_review(
        packet=packet,
        qwen_labels=qwen_labels,
        qwen_status=qwen_status,
        human_state=human_state,
    )
    packet["label_review_state"] = {
        "latest_review": human_state.get("latest_review"),
        "retracted_audit_ids": human_state.get("retracted_audit_ids", []),
    }
    return packet


def run_pilot_capture(conn: sqlite3.Connection) -> dict[str, Any]:
    cfg = multimodal_config()
    if not cfg["pilot_enabled"] or not cfg["enabled"]:
        return {
            "ok": True,
            "skipped": True,
            "reason": "multimodal pilot disabled",
            "config": {
                "pilot_enabled": cfg["pilot_enabled"],
                "enabled": cfg["enabled"],
            },
        }

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=float(cfg["pilot_lookback_s"]))
    params: list[Any] = []
    where = [
        """
        NOT EXISTS (
          SELECT 1
          FROM observation_packets op
          WHERE op.observation_id = po.id
        )
        """
    ]
    if cfg["pilot_zones"]:
        placeholders = ",".join("?" for _ in cfg["pilot_zones"])
        where.append(f"po.zone IN ({placeholders})")
        params.extend(cfg["pilot_zones"])
    if cfg["pilot_kinds"]:
        placeholders = ",".join("?" for _ in cfg["pilot_kinds"])
        where.append(f"po.kind IN ({placeholders})")
        params.extend(cfg["pilot_kinds"])
    sql = "SELECT po.* FROM preference_observations po WHERE " + " AND ".join(where)
    sql += " ORDER BY po.ts DESC LIMIT 50"
    rows = conn.execute(sql, params).fetchall()

    eligible: list[sqlite3.Row] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        ts = _parse_ts(row["ts"])
        if ts is None or ts < cutoff:
            skipped.append({"observation_id": row["id"], "reason": "outside pilot lookback", "ts": row["ts"]})
            continue
        camera = camera_for_zone(row["zone"])
        if not camera or camera not in cfg["cameras"]:
            skipped.append({"observation_id": row["id"], "reason": "zone camera not in pilot cameras", "zone": row["zone"], "camera": camera})
            continue
        eligible.append(row)
        if len(eligible) >= cfg["pilot_max_packets_per_run"]:
            break

    packets: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in eligible:
        try:
            created = create_observation_packet(
                conn,
                observation_id=row["id"],
                capture_frames=True,
                force=False,
                trigger="pilot_auto_capture",
                post_frame_delay_s=0.0,
                allow_current_fallback=False,
            )
            packet = created["packet"]
            packets.append(packet)
            if (
                cfg["pilot_auto_label"]
                and int(packet.get("frame_count") or 0) >= int(cfg["pilot_min_label_frames"])
            ):
                labels.append(label_observation_packet(conn, packet["id"]))
        except Exception as exc:
            errors.append({"observation_id": row["id"], "error": str(exc)[:300]})

    return {
        "ok": not errors,
        "skipped": False,
        "created": len(packets),
        "labeled": len(labels),
        "packets": packets,
        "labels": labels,
        "errors": errors,
        "not_selected": skipped[:10],
        "config": {
            "pilot_zones": cfg["pilot_zones"],
            "pilot_kinds": cfg["pilot_kinds"],
            "pilot_lookback_s": cfg["pilot_lookback_s"],
            "pilot_max_packets_per_run": cfg["pilot_max_packets_per_run"],
            "pilot_auto_label": cfg["pilot_auto_label"],
            "pilot_min_label_frames": cfg["pilot_min_label_frames"],
            "cameras": cfg["cameras"],
        },
    }


def build_prompt_contract(packet: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    frame_lines = []
    for frame in frames[:4]:
        rel = frame.get("relative_ts_s")
        rel_text = "unknown"
        if rel is not None:
            rel_text = "t0" if abs(float(rel)) < 0.001 else f"t{float(rel):+g}s"
        frame_lines.append(f"- {frame.get('frame_id')}: {rel_text} ({frame.get('frame_role')})")
    response_template: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for axis in AXES:
        default_label = "unknown" if "unknown" in ENUMS[axis] else "uncertain"
        response_template[axis] = {
            "label": default_label,
            "confidence": 0.0,
            "evidence_frames": [],
        }
    response_template["secondary_activities"] = []
    response_template["abstain_reason"] = None
    response_template["contradictions"] = []
    response_template["short_visible_summary"] = ""
    user_prompt = "\n".join(
        [
            "Observation:",
            f"- observation_id: {packet.get('id')}",
            f"- room: {packet.get('camera') or 'unknown'}",
            f"- zone: {packet.get('zone') or 'unknown'}",
            f"- event_type: {packet.get('event_type')}",
            "- frame order:",
            *frame_lines,
            "",
            "Home Assistant metadata:",
            canonical_json(packet.get("ha_metadata") or {}),
            "",
            "Allowed enum values by field:",
            canonical_json({"schema_version": SCHEMA_VERSION, "axes": ENUMS, "secondary_activities": PRIMARY_ACTIVITY}),
            "",
            "Required JSON response shape:",
            canonical_json(response_template),
            "",
            "Task:",
            "1. Label only visible context.",
            "2. Return exactly the top-level keys in the required JSON response shape.",
            "3. Do not use an axes wrapper in the response.",
            "4. Every axis must be a top-level object with exactly label, confidence, and evidence_frames.",
            "5. Report confidence for every axis as a number from 0 to 1.",
            "6. Use frame ids such as frame_1 or frame_2 in evidence_frames.",
            "7. If evidence is weak, use unknown or uncertain with low confidence.",
            "8. secondary_activities must be a list of primary_activity-style objects, not strings.",
            "9. Do not add unknown keys.",
            "10. If metadata contradicts visual evidence, keep the visual label and add a contradiction note.",
            "11. Return only JSON.",
        ]
    )
    return {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "allowed_labels": ENUMS,
        "inference_defaults": INFERENCE_DEFAULTS,
    }


def _validate_axis(value: Any, axis: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{axis} must be an object")
        return {"label": "uncertain", "confidence": 0.0, "evidence_frames": []}
    unknown = set(value.keys()) - AXIS_KEYS
    if unknown:
        errors.append(f"{axis} has unknown keys: {sorted(unknown)}")
    label = value.get("label")
    if label not in ENUMS[axis]:
        errors.append(f"{axis}.label invalid: {label}")
        label = "unknown" if "unknown" in ENUMS[axis] else "uncertain"
    confidence_raw = value.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        errors.append(f"{axis}.confidence invalid: {confidence_raw}")
        confidence = 0.0
    if confidence < 0 or confidence > 1:
        errors.append(f"{axis}.confidence out of range: {confidence}")
        confidence = max(0.0, min(1.0, confidence))
    evidence_frames = value.get("evidence_frames") or []
    if not isinstance(evidence_frames, list) or any(not isinstance(x, str) for x in evidence_frames):
        errors.append(f"{axis}.evidence_frames must be a list of strings")
        evidence_frames = []
    return {"label": label, "confidence": confidence, "evidence_frames": evidence_frames}


def validate_qwen_labels(payload: Any) -> tuple[bool, dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return False, {}, ["payload must be an object"]
    unknown = set(payload.keys()) - TOP_LEVEL_KEYS
    if unknown:
        errors.append(f"unknown top-level keys: {sorted(unknown)}")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    normalized: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for axis in AXES:
        if axis not in payload:
            errors.append(f"missing axis: {axis}")
            normalized[axis] = {"label": "unknown" if "unknown" in ENUMS[axis] else "uncertain", "confidence": 0.0, "evidence_frames": []}
        else:
            normalized[axis] = _validate_axis(payload.get(axis), axis, errors)
    secondaries = payload.get("secondary_activities") or []
    if not isinstance(secondaries, list):
        errors.append("secondary_activities must be a list")
        secondaries = []
    normalized_secondaries: list[dict[str, Any]] = []
    for idx, item in enumerate(secondaries[:5]):
        if not isinstance(item, dict):
            errors.append(f"secondary_activities[{idx}] must be an object")
            continue
        fake_errors: list[str] = []
        axis = _validate_axis(item, "primary_activity", fake_errors)
        if fake_errors:
            errors.extend([f"secondary_activities[{idx}]: {err}" for err in fake_errors])
        normalized_secondaries.append(axis)
    normalized["secondary_activities"] = normalized_secondaries
    contradictions = payload.get("contradictions") or []
    if not isinstance(contradictions, list):
        errors.append("contradictions must be a list")
        contradictions = []
    normalized["contradictions"] = contradictions[:10]
    abstain = payload.get("abstain_reason")
    normalized["abstain_reason"] = abstain if isinstance(abstain, str) or abstain is None else str(abstain)
    summary = payload.get("short_visible_summary") or ""
    if not isinstance(summary, str):
        errors.append("short_visible_summary must be a string")
        summary = str(summary)
    normalized["short_visible_summary"] = summary[:280]
    return not errors, normalized, errors


def _extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _call_qwen(packet: dict[str, Any], frames: list[dict[str, Any]], retry_errors: list[str] | None = None) -> tuple[str, int]:
    cfg = multimodal_config()
    content: list[dict[str, Any]] = []
    for frame in frames[:4]:
        raw = Path(frame["path"]).read_bytes()
        jpeg_b64 = base64.b64encode(raw).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{jpeg_b64}"}})
    prompt = build_prompt_contract(packet, frames)
    user_text = prompt["user_prompt"]
    if retry_errors:
        user_text += "\n\nYour previous response failed validation:\n"
        user_text += "\n".join(f"- {err}" for err in retry_errors[:8])
        user_text += (
            "\nReturn corrected JSON only. Do not wrap the labels inside an axes key. "
            "Each required axis must be a top-level object with label, confidence, and evidence_frames."
        )
    content.append({"type": "text", "text": user_text})
    body = {
        "model": cfg["qwen_model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "stream": False,
        "temperature": INFERENCE_DEFAULTS["temperature"],
        "max_tokens": INFERENCE_DEFAULTS["max_tokens"],
    }
    req = urllib.request.Request(
        cfg["qwen_base_url"].rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"qwen returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"qwen unreachable: {exc.reason}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)
    data = json.loads(raw)
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return str(text).strip(), latency_ms


def label_observation_packet(conn: sqlite3.Connection, packet_id: str) -> dict[str, Any]:
    cfg = multimodal_config()
    ok, used = _rate_limit_ok(conn, "qwen", cfg["max_qwen_jobs_per_hour"])
    if not ok:
        raise ValueError(f"qwen label rate limit exceeded ({used}/hour)")
    packet = get_observation_packet(conn, packet_id)
    frames = [f for f in packet["frames"] if not f.get("deleted_at")]
    result_id = _packet_id("ql")
    created_at = utc_now()
    request_contract = build_prompt_contract(packet["packet"], frames)
    if not frames:
        errors = ["no usable frames for packet"]
        conn.execute(
            """
            INSERT INTO qwen_label_results
                (id, packet_id, status, prompt_version, schema_version, model,
                 model_base_url, request_json, labels_json, validation_errors_json,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                packet_id,
                "skipped_no_frames",
                PROMPT_VERSION,
                SCHEMA_VERSION,
                cfg["qwen_model"],
                cfg["qwen_base_url"],
                canonical_json(request_contract),
                "{}",
                canonical_json(errors),
                created_at,
            ),
        )
        conn.execute(
            "UPDATE observation_packets SET qwen_status = ?, status = ? WHERE id = ?",
            ("no_frames", "frames_missing", packet_id),
        )
        conn.commit()
        return {"ok": False, "result_id": result_id, "status": "skipped_no_frames", "errors": errors}

    raw_output = ""
    latency_ms = None
    status = "invalid"
    labels: dict[str, Any] = {}
    errors: list[str] = []
    try:
        raw_output, latency_ms = _call_qwen(packet["packet"], frames)
        parsed = _extract_json(raw_output)
        valid, labels, errors = validate_qwen_labels(parsed)
        if not valid:
            raw_output, latency_ms = _call_qwen(packet["packet"], frames, retry_errors=errors)
            parsed = _extract_json(raw_output)
            valid, labels, errors = validate_qwen_labels(parsed)
        status = "valid" if valid else "invalid"
    except Exception as exc:
        status = "error"
        errors = [str(exc)[:500]]
    conn.execute(
        """
        INSERT INTO qwen_label_results
            (id, packet_id, status, prompt_version, schema_version, model,
             model_base_url, request_json, raw_output_text, labels_json,
             validation_errors_json, latency_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            packet_id,
            status,
            PROMPT_VERSION,
            SCHEMA_VERSION,
            cfg["qwen_model"],
            cfg["qwen_base_url"],
            canonical_json(request_contract),
            raw_output,
            canonical_json(labels),
            canonical_json(errors),
            latency_ms,
            created_at,
        ),
    )
    conn.execute(
        "UPDATE observation_packets SET qwen_status = ?, status = ? WHERE id = ?",
        (status, "labeled" if status == "valid" else "label_failed", packet_id),
    )
    conn.commit()
    return {"ok": status == "valid", "result_id": result_id, "status": status, "errors": errors, "labels": labels}


def label_next_packets(conn: sqlite3.Connection, *, limit: int = 1) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id
        FROM observation_packets
        WHERE qwen_status IN ('not_requested', 'invalid', 'error')
          AND frame_count > 0
        ORDER BY event_ts DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    results = []
    for row in rows:
        results.append(label_observation_packet(conn, row["id"]))
    return {"ok": True, "processed": len(results), "results": results}


def _packet_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["privacy_flags"] = _decode_json(data.pop("privacy_flags_json", None), [])
    data["retention_policy"] = _decode_json(data.pop("retention_policy_json", None), {})
    data["ha_metadata"] = _decode_json(data.pop("ha_metadata_json", None), {})
    data["source_ids"] = _decode_json(data.pop("source_ids_json", None), {})
    data["model_versions"] = _decode_json(data.pop("model_versions_json", None), {})
    return data


def _frame_from_row(row: sqlite3.Row, *, include_path: bool = False) -> dict[str, Any]:
    data = dict(row)
    data["privacy_flags"] = _decode_json(data.pop("privacy_flags_json", None), [])
    if not include_path:
        data.pop("path", None)
    data["image_url"] = f"/api/intelligence/observation-frames/{data['id']}/image"
    return data


def _label_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["request"] = _decode_json(data.pop("request_json", None), {})
    data["labels"] = _decode_json(data.pop("labels_json", None), {})
    data["validation_errors"] = _decode_json(data.pop("validation_errors_json", None), [])
    return data


def _audit_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["body"] = _decode_json(data.pop("body_json", None), {})
    return data


def list_observation_packets(
    conn: sqlite3.Connection,
    *,
    zone: str | None = None,
    activity: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where: list[str] = []
    if zone:
        where.append("op.zone = ?")
        params.append(zone)
    if activity:
        where.append(
            """
            EXISTS (
              SELECT 1 FROM qwen_label_results qlr
              WHERE qlr.packet_id = op.id
                AND qlr.status = 'valid'
                AND qlr.labels_json LIKE ?
            )
            """
        )
        params.append(f'%"primary_activity":{{"confidence":%,"evidence_frames":%,"label":"{activity}"%')
    sql = """
        SELECT op.*,
               (
                 SELECT qlr.labels_json
                 FROM qwen_label_results qlr
                 WHERE qlr.packet_id = op.id
                 ORDER BY qlr.created_at DESC
                 LIMIT 1
               ) AS latest_labels_json,
               (
                 SELECT qlr.status
                 FROM qwen_label_results qlr
                 WHERE qlr.packet_id = op.id
                 ORDER BY qlr.created_at DESC
                 LIMIT 1
               ) AS latest_label_status
        FROM observation_packets op
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY op.event_ts DESC, op.created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    packets = []
    for row in rows:
        d = _packet_from_row(row)
        latest_raw = d.pop("latest_labels_json", None)
        d["latest_labels"] = _decode_json(latest_raw, {}) if latest_raw else None
        d["latest_label_status"] = d.pop("latest_label_status", None)
        audit_rows = conn.execute(
            "SELECT * FROM observation_audits WHERE packet_id = ? ORDER BY created_at DESC",
            (d["id"],),
        ).fetchall()
        audits = [_audit_from_row(a) for a in audit_rows]
        frame_rows = conn.execute(
            "SELECT * FROM observation_frames WHERE packet_id = ? ORDER BY frame_id",
            (d["id"],),
        ).fetchall()
        timing = _capture_timing_from_frames(d, [_frame_from_row(f) for f in frame_rows])
        _apply_effective_labels(
            d,
            qwen_labels=d["latest_labels"],
            qwen_status=d["latest_label_status"] or d.get("qwen_status"),
            audits=audits,
        )
        d["capture_timing"] = timing
        d["label_usability"] = _label_usability(d, d["latest_labels"], timing)
        packets.append(d)
    return packets


def get_observation_packet(conn: sqlite3.Connection, packet_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM observation_packets WHERE id = ?", (packet_id,)).fetchone()
    if not row:
        raise ValueError("packet not found")
    frames = conn.execute(
        "SELECT * FROM observation_frames WHERE packet_id = ? ORDER BY frame_id",
        (packet_id,),
    ).fetchall()
    label_row = conn.execute(
        """
        SELECT *
        FROM qwen_label_results
        WHERE packet_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (packet_id,),
    ).fetchone()
    audits = conn.execute(
        "SELECT * FROM observation_audits WHERE packet_id = ? ORDER BY created_at DESC",
        (packet_id,),
    ).fetchall()
    packet = _packet_from_row(row)
    frame_out = [_frame_from_row(f, include_path=True) for f in frames]
    label = _label_from_row(label_row)
    labels = label.get("labels") if label else None
    packet["latest_labels"] = labels
    packet["latest_label_status"] = (label or {}).get("status")
    audit_out = [_audit_from_row(a) for a in audits]
    _apply_effective_labels(
        packet,
        qwen_labels=labels,
        qwen_status=(label or {}).get("status") or packet.get("qwen_status"),
        audits=audit_out,
    )
    timing = _capture_timing_from_frames(packet, frame_out)
    return {
        "packet": packet,
        "frames": frame_out,
        "latest_label": label,
        "human_label_overrides": packet.get("human_label_overrides", {}),
        "effective_labels": packet.get("effective_labels"),
        "needs_review": packet.get("needs_review"),
        "capture_timing": timing,
        "label_usability": _label_usability(packet, labels, timing),
        "audits": audit_out,
    }


def observation_map(conn: sqlite3.Connection, *, limit: int = 300) -> dict[str, Any]:
    packets = list_observation_packets(conn, limit=limit)
    centers = {
        "desk_work": (24, 30),
        "reading": (38, 24),
        "watching_tv": (72, 30),
        "cooking": (30, 72),
        "eating": (48, 68),
        "exercise": (78, 70),
        "cleaning": (62, 78),
        "relaxing": (70, 48),
        "passing_through": (52, 52),
        "arriving_or_leaving": (86, 48),
        "sleeping_or_napping": (18, 50),
        "standing_idle": (48, 42),
        "unknown": (50, 50),
    }
    points = []
    for packet in packets:
        labels = packet.get("effective_labels") or packet.get("latest_labels") or {}
        activity = ((labels.get("primary_activity") or {}).get("label")) or "unknown"
        confidence = float((labels.get("primary_activity") or {}).get("confidence") or 0)
        cx, cy = centers.get(activity, centers["unknown"])
        jitter_hash = stable_hash(packet["id"])
        jx = (int(jitter_hash[:4], 16) / 65535.0 - 0.5) * 12
        jy = (int(jitter_hash[4:8], 16) / 65535.0 - 0.5) * 12
        privacy = packet.get("privacy_flags") or []
        points.append(
            {
                "id": packet["id"],
                "x": max(2, min(98, cx + jx)),
                "y": max(2, min(98, cy + jy)),
                "activity": activity,
                "confidence": confidence,
                "zone": packet.get("zone"),
                "camera": packet.get("camera"),
                "event_ts": packet.get("event_ts"),
                "event_type": packet.get("event_type"),
                "frame_count": packet.get("frame_count"),
                "qwen_status": packet.get("qwen_status"),
                "linked": bool(packet.get("observation_id")),
                "privacy_sensitive": bool(privacy and privacy != ["normal"]),
                "human_corrected": bool((packet.get("human_label_overrides") or {}).get("primary_activity")),
                "needs_review": packet.get("needs_review") or {},
                "review_reasons": (packet.get("needs_review") or {}).get("reasons", []),
                "qwen_activity": ((packet.get("latest_labels") or {}).get("primary_activity") or {}).get("label"),
            }
        )
    return {"points": points, "projection": "deterministic_activity_layout_v1"}


def get_frame_path(conn: sqlite3.Connection, frame_row_id: str) -> Path:
    row = conn.execute(
        "SELECT path, deleted_at FROM observation_frames WHERE id = ?",
        (frame_row_id,),
    ).fetchone()
    if not row:
        raise ValueError("frame not found")
    if row["deleted_at"]:
        raise ValueError("frame deleted")
    path = Path(row["path"])
    if not path.exists():
        raise ValueError("frame file missing")
    return path


def delete_packet_frames(conn: sqlite3.Connection, packet_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, path FROM observation_frames WHERE packet_id = ? AND deleted_at IS NULL",
        (packet_id,),
    ).fetchall()
    deleted_at = utc_now()
    deleted = 0
    for row in rows:
        path = Path(row["path"])
        with suppress(Exception):
            path.unlink(missing_ok=True)
        conn.execute("UPDATE observation_frames SET deleted_at = ? WHERE id = ?", (deleted_at, row["id"]))
        deleted += 1
    conn.execute(
        "UPDATE observation_packets SET frame_count = 0, status = ? WHERE id = ?",
        ("frames_deleted", packet_id),
    )
    conn.commit()
    return {"ok": True, "deleted_frames": deleted, "deleted_at": deleted_at}


def record_packet_audit(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    action: str,
    actor: str = "home_app",
    note: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if action not in {
        "label_looks_right",
        "label_looks_wrong",
        "correct_primary_activity",
        "confirm_qwen_activity",
        "confirm_unknown_activity",
        "not_labelable_activity",
        "retract_label_audit",
        "label_eval_audit",
        "ignore_observation",
        "do_not_learn_context",
        "delete_frames",
        "exclude_from_training",
        "exclude_from_proposals",
    }:
        raise ValueError("unsupported audit action")
    row = conn.execute("SELECT id FROM observation_packets WHERE id = ?", (packet_id,)).fetchone()
    if not row:
        raise ValueError("packet not found")
    body = body or {}
    if action == "correct_primary_activity":
        label = body.get("label")
        if label not in PRIMARY_ACTIVITY or label == "unknown":
            raise ValueError("correct_primary_activity requires a non-unknown primary activity label")
    elif action == "not_labelable_activity":
        reason = body.get("reason")
        if reason not in NOT_LABELABLE_REASONS:
            raise ValueError(f"not_labelable_activity reason must be one of: {', '.join(NOT_LABELABLE_REASONS)}")
    elif action == "retract_label_audit":
        target = body.get("target_audit_id")
        try:
            target_id = int(target)
        except (TypeError, ValueError) as exc:
            raise ValueError("retract_label_audit requires target_audit_id") from exc
        target_row = conn.execute(
            """
            SELECT id, action
            FROM observation_audits
            WHERE id = ? AND packet_id = ?
            """,
            (target_id, packet_id),
        ).fetchone()
        if not target_row or target_row["action"] not in LABEL_AUDIT_ACTIONS:
            raise ValueError("target audit is not a label audit for this packet")
    created_at = utc_now()
    cur = conn.execute(
        """
        INSERT INTO observation_audits (packet_id, action, actor, note, body_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (packet_id, action, actor, note, canonical_json(body), created_at),
    )
    if action == "delete_frames":
        delete_packet_frames(conn, packet_id)
    else:
        conn.commit()
    return {
        "ok": True,
        "audit": {
            "id": cur.lastrowid,
            "packet_id": packet_id,
            "action": action,
            "actor": actor,
            "note": note,
            "body": body,
            "created_at": created_at,
        },
    }


def _latest_label_for_packet(conn: sqlite3.Connection, packet_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM qwen_label_results
        WHERE packet_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (packet_id,),
    ).fetchone()


def _eval_set_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["selection"] = _decode_json(data.pop("selection_json", None), {})
    return data


def _eval_item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["packet_snapshot"] = _decode_json(data.pop("packet_snapshot_json", None), {})
    data["qwen_labels"] = _decode_json(data.pop("qwen_labels_json", None), {})
    data["capture_timing"] = _decode_json(data.pop("capture_timing_json", None), {})
    data["human_labels"] = _decode_json(data.pop("human_labels_json", None), {})
    return data


def _axis_label(payload: dict[str, Any] | None, axis: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get(axis)
    if isinstance(direct, dict):
        return direct.get("label")
    if isinstance(direct, str):
        return direct
    axes = payload.get("axes")
    if isinstance(axes, dict):
        nested = axes.get(axis)
        if isinstance(nested, dict):
            return nested.get("label")
        if isinstance(nested, str):
            return nested
    return None


def label_eval_status(conn: sqlite3.Connection) -> dict[str, Any]:
    sets = conn.execute("SELECT COUNT(*) AS c FROM label_eval_sets").fetchone()["c"]
    items = conn.execute("SELECT COUNT(*) AS c FROM label_eval_items").fetchone()["c"]
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM label_eval_items WHERE status = 'audited'"
    ).fetchone()["c"]
    ready = int(audited or 0) >= 50
    latest = conn.execute(
        """
        SELECT *
        FROM label_eval_sets
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    axis_rows = conn.execute(
        "SELECT qwen_labels_json, human_labels_json, status FROM label_eval_items"
    ).fetchall()
    coverage: dict[str, dict[str, int]] = {
        axis: {"qwen": 0, "human": 0} for axis in AXES
    }
    for row in axis_rows:
        qwen = _decode_json(row["qwen_labels_json"], {})
        human = _decode_json(row["human_labels_json"], {})
        for axis in AXES:
            if _axis_label(qwen, axis):
                coverage[axis]["qwen"] += 1
            if _axis_label(human, axis):
                coverage[axis]["human"] += 1
    return {
        "ok": True,
        "sets": sets,
        "items": items,
        "audited_items": audited,
        "unaudited_items": max(0, int(items or 0) - int(audited or 0)),
        "ready_for_prompt_gate": ready,
        "minimum_audited_items": 50,
        "latest_set": _eval_set_from_row(latest) if latest else None,
        "axis_coverage": coverage,
        "prompt_gate": qwen_prompt_gate(conn)["gate"],
    }


def list_label_eval_sets(conn: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT les.*,
               (SELECT COUNT(*) FROM label_eval_items lei WHERE lei.set_id = les.id) AS item_count,
               (SELECT COUNT(*) FROM label_eval_items lei WHERE lei.set_id = les.id AND lei.status = 'audited') AS audited_count
        FROM label_eval_sets les
        ORDER BY les.created_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    sets = []
    for row in rows:
        data = _eval_set_from_row(row)
        data["item_count"] = row["item_count"]
        data["audited_count"] = row["audited_count"]
        data["score"] = score_label_eval_set(conn, row["id"])["score"]
        sets.append(data)
    return {"ok": True, "sets": sets}


def seed_label_eval_set(
    conn: sqlite3.Connection,
    *,
    name: str = "heldout_v1",
    target_size: int = 50,
    require_valid_labels: bool = False,
) -> dict[str, Any]:
    target_size = max(1, min(200, int(target_size)))
    created_at = utc_now()
    set_id = _packet_id("lev")
    cfg = multimodal_config()
    conn.execute(
        """
        INSERT INTO label_eval_sets
            (id, name, status, prompt_version, schema_version, model, target_size,
             selection_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            set_id,
            name,
            "seeded",
            PROMPT_VERSION,
            SCHEMA_VERSION,
            cfg["qwen_model"],
            target_size,
            canonical_json(
                {
                    "source": "observation_packets",
                    "require_valid_labels": require_valid_labels,
                    "selection": "recent_diverse_packets_with_frames",
                }
            ),
            created_at,
            created_at,
        ),
    )
    label_filter = "AND qlr.status = 'valid'" if require_valid_labels else ""
    rows = conn.execute(
        f"""
        SELECT op.*, qlr.id AS qwen_result_id, qlr.status AS label_status,
               qlr.labels_json AS labels_json
        FROM observation_packets op
        LEFT JOIN qwen_label_results qlr
          ON qlr.id = (
            SELECT id
            FROM qwen_label_results
            WHERE packet_id = op.id
            ORDER BY created_at DESC
            LIMIT 1
          )
        WHERE op.frame_count >= 2
          {label_filter}
        ORDER BY op.event_ts DESC, op.created_at DESC
        LIMIT ?
        """,
        (target_size,),
    ).fetchall()
    inserted = 0
    for row in rows:
        packet_detail = get_observation_packet(conn, row["id"])
        packet = packet_detail["packet"]
        labels = _decode_json(row["labels_json"], {}) if row["labels_json"] else {}
        selection_reason = "recent_packet_with_frames"
        if row["label_status"] == "valid":
            selection_reason += ";valid_qwen_labels"
        if packet_detail["capture_timing"].get("event_aligned"):
            selection_reason += ";event_aligned"
        conn.execute(
            """
            INSERT OR IGNORE INTO label_eval_items
                (id, set_id, packet_id, qwen_result_id, status, selection_reason,
                 packet_snapshot_json, qwen_labels_json, capture_timing_json,
                 human_labels_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"lei-{stable_hash(set_id + ':' + row['id'])[:18]}",
                set_id,
                row["id"],
                row["qwen_result_id"],
                "unaudited",
                selection_reason,
                canonical_json(packet),
                canonical_json(labels),
                canonical_json(packet_detail["capture_timing"]),
                "{}",
                utc_now(),
            ),
        )
        inserted += 1
    conn.commit()
    return get_label_eval_set(conn, set_id) | {"created": True, "inserted": inserted}


def _latest_label_eval_set_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM label_eval_sets
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return row["id"] if row else None


def get_label_eval_set(conn: sqlite3.Connection, set_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_eval_sets WHERE id = ?", (set_id,)).fetchone()
    if not row:
        raise ValueError("label eval set not found")
    item_rows = conn.execute(
        """
        SELECT *
        FROM label_eval_items
        WHERE set_id = ?
        ORDER BY created_at DESC
        """,
        (set_id,),
    ).fetchall()
    return {
        "ok": True,
        "set": _eval_set_from_row(row),
        "items": [_eval_item_from_row(item) for item in item_rows],
        "score": score_label_eval_set(conn, set_id)["score"],
    }


def label_eval_queue(
    conn: sqlite3.Connection,
    *,
    set_id: str | None = None,
    status: str = "unaudited",
    limit: int = 20,
) -> dict[str, Any]:
    resolved_set_id = set_id or _latest_label_eval_set_id(conn)
    if not resolved_set_id:
        return {"ok": True, "set": None, "items": [], "score": None}
    eval_set = conn.execute("SELECT * FROM label_eval_sets WHERE id = ?", (resolved_set_id,)).fetchone()
    if not eval_set:
        raise ValueError("label eval set not found")
    params: list[Any] = [resolved_set_id]
    where = ["lei.set_id = ?"]
    if status != "all":
        where.append("lei.status = ?")
        params.append(status)
    params.append(max(1, min(int(limit), 100)))
    rows = conn.execute(
        f"""
        SELECT lei.*, op.zone, op.event_type, op.event_ts, op.frame_count, op.qwen_status
        FROM label_eval_items lei
        JOIN observation_packets op ON op.id = lei.packet_id
        WHERE {" AND ".join(where)}
        ORDER BY op.event_ts DESC, lei.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    items = []
    for row in rows:
        item = _eval_item_from_row(row)
        item["packet_summary"] = {
            "zone": row["zone"],
            "event_type": row["event_type"],
            "event_ts": row["event_ts"],
            "frame_count": row["frame_count"],
            "qwen_status": row["qwen_status"],
        }
        items.append(item)
    return {
        "ok": True,
        "set": _eval_set_from_row(eval_set),
        "items": items,
        "score": score_label_eval_set(conn, resolved_set_id)["score"],
    }


def audit_label_eval_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    human_labels: dict[str, Any],
    actor: str = "home_app",
    note: str | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_eval_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ValueError("label eval item not found")
    audited_at = utc_now()
    conn.execute(
        """
        UPDATE label_eval_items
        SET status = 'audited',
            human_labels_json = ?,
            audit_note = ?,
            audited_by = ?,
            audited_at = ?
        WHERE id = ?
        """,
        (canonical_json(human_labels), note, actor, audited_at, item_id),
    )
    conn.execute(
        """
        INSERT INTO observation_audits (packet_id, action, actor, note, body_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            row["packet_id"],
            "label_eval_audit",
            actor,
            note,
            canonical_json({"label_eval_item_id": item_id, "human_labels": human_labels}),
            audited_at,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM label_eval_items WHERE id = ?", (item_id,)).fetchone()
    return {"ok": True, "item": _eval_item_from_row(updated), "score": score_label_eval_set(conn, row["set_id"])["score"]}


def accept_qwen_label_eval_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    actor: str = "home_app",
    note: str | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM label_eval_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise ValueError("label eval item not found")
    labels = _decode_json(row["qwen_labels_json"], {})
    if not labels:
        raise ValueError("eval item has no qwen labels to accept")
    return audit_label_eval_item(
        conn,
        item_id,
        human_labels=labels,
        actor=actor,
        note=note or "accepted qwen labels as human audit",
    )


def score_label_eval_set(conn: sqlite3.Connection, set_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT qwen_labels_json, human_labels_json, status
        FROM label_eval_items
        WHERE set_id = ?
        """,
        (set_id,),
    ).fetchall()
    per_axis: dict[str, dict[str, int | float | None]] = {}
    total_compared = 0
    total_correct = 0
    for axis in AXES:
        compared = 0
        correct = 0
        for row in rows:
            if row["status"] != "audited":
                continue
            qwen_label = _axis_label(_decode_json(row["qwen_labels_json"], {}), axis)
            human_label = _axis_label(_decode_json(row["human_labels_json"], {}), axis)
            if not qwen_label or not human_label:
                continue
            compared += 1
            total_compared += 1
            if qwen_label == human_label:
                correct += 1
                total_correct += 1
        per_axis[axis] = {
            "compared": compared,
            "correct": correct,
            "accuracy": (correct / compared) if compared else None,
        }
    return {
        "ok": True,
        "score": {
            "set_id": set_id,
            "audited_items": sum(1 for row in rows if row["status"] == "audited"),
            "total_items": len(rows),
            "compared_axes": total_compared,
            "correct_axes": total_correct,
            "overall_accuracy": (total_correct / total_compared) if total_compared else None,
            "per_axis": per_axis,
            "ready_for_prompt_gate": sum(1 for row in rows if row["status"] == "audited") >= 50,
            "minimum_audited_items": 50,
        },
    }


def qwen_prompt_gate(conn: sqlite3.Connection) -> dict[str, Any]:
    latest_id = _latest_label_eval_set_id(conn)
    if not latest_id:
        return {
            "ok": True,
            "gate": {
                "ready": False,
                "status": "blocked",
                "reason": "no held-out label eval set exists",
                "minimum_audited_items": 50,
                "audited_items": 0,
                "set_id": None,
                "required_checks": [
                    "seed held-out set",
                    "audit at least 50 items",
                    "compare candidate prompt against baseline",
                    "reject ties",
                ],
            },
        }
    score = score_label_eval_set(conn, latest_id)["score"]
    audited = int(score["audited_items"] or 0)
    blockers = []
    if audited < 50:
        blockers.append(f"needs 50 audited items; currently {audited}")
    if score["compared_axes"] == 0:
        blockers.append("no audited axis comparisons yet")
    ready = not blockers
    return {
        "ok": True,
        "gate": {
            "ready": ready,
            "status": "ready" if ready else "blocked",
            "reason": "ready for prompt comparison" if ready else "; ".join(blockers),
            "set_id": latest_id,
            "minimum_audited_items": 50,
            "audited_items": audited,
            "score": score,
            "required_checks": [
                "strict improvement on held-out audited set",
                "no privacy flag regression",
                "no JSON validity regression",
                "ties rejected",
                "old prompt remains rollback option",
            ],
        },
    }


def _score_candidate_labels(
    rows: list[sqlite3.Row],
    labels_by_item: dict[str, Any],
) -> dict[str, Any]:
    per_axis: dict[str, dict[str, int | float | None]] = {}
    total_compared = 0
    total_correct = 0
    candidate_by_row: dict[str, dict[str, Any]] = {}
    invalid_items = 0
    for row in rows:
        if row["status"] != "audited":
            continue
        candidate = labels_by_item.get(row["id"]) or labels_by_item.get(row["packet_id"])
        if isinstance(candidate, dict):
            candidate_by_row[row["id"]] = candidate
        else:
            invalid_items += 1
    privacy_compared = 0
    privacy_correct = 0
    for axis in AXES:
        compared = 0
        correct = 0
        for row in rows:
            if row["status"] != "audited":
                continue
            candidate = candidate_by_row.get(row["id"])
            if not candidate:
                continue
            human_label = _axis_label(_decode_json(row["human_labels_json"], {}), axis)
            candidate_label = _axis_label(candidate, axis)
            if not human_label or not candidate_label:
                continue
            compared += 1
            total_compared += 1
            if candidate_label == human_label:
                correct += 1
                total_correct += 1
                if axis == "privacy_sensitivity":
                    privacy_correct += 1
            if axis == "privacy_sensitivity":
                privacy_compared += 1
        per_axis[axis] = {
            "compared": compared,
            "correct": correct,
            "accuracy": (correct / compared) if compared else None,
        }
    return {
        "audited_items": sum(1 for row in rows if row["status"] == "audited"),
        "valid_candidate_items": len(candidate_by_row),
        "invalid_candidate_items": invalid_items,
        "compared_axes": total_compared,
        "correct_axes": total_correct,
        "overall_accuracy": (total_correct / total_compared) if total_compared else None,
        "privacy_accuracy": (privacy_correct / privacy_compared) if privacy_compared else None,
        "per_axis": per_axis,
    }


def run_qwen_prompt_trial(
    conn: sqlite3.Connection,
    *,
    candidate_name: str,
    candidate_prompt: dict[str, Any] | None = None,
    eval_set_id: str | None = None,
    candidate_labels_by_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = qwen_prompt_gate(conn)["gate"]
    resolved_set_id = eval_set_id or gate.get("set_id") or _latest_label_eval_set_id(conn)
    created_at = utc_now()
    trial_id = _packet_id("qpt")
    baseline_score: dict[str, Any] = {}
    candidate_score: dict[str, Any] = {}
    comparison: dict[str, Any] = {
        "accepted": False,
        "activation_allowed": False,
        "ties_rejected": True,
        "no_prompt_activation_path": True,
    }
    status = "blocked_gate"
    if not gate.get("ready"):
        comparison["blockers"] = [gate.get("reason") or "prompt gate blocked"]
    elif not resolved_set_id:
        comparison["blockers"] = ["no eval set selected"]
    elif not candidate_labels_by_item:
        status = "blocked_missing_candidate_outputs"
        comparison["blockers"] = ["candidate labels are required for offline comparison"]
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM label_eval_items
            WHERE set_id = ?
            """,
            (resolved_set_id,),
        ).fetchall()
        baseline_score = score_label_eval_set(conn, resolved_set_id)["score"]
        candidate_score = _score_candidate_labels(rows, candidate_labels_by_item)
        baseline_acc = baseline_score.get("overall_accuracy")
        candidate_acc = candidate_score.get("overall_accuracy")
        baseline_privacy = (baseline_score.get("per_axis") or {}).get("privacy_sensitivity", {}).get("accuracy")
        candidate_privacy = candidate_score.get("privacy_accuracy")
        blockers = []
        if candidate_acc is None:
            blockers.append("candidate has no comparable audited labels")
        elif baseline_acc is not None and candidate_acc <= baseline_acc:
            blockers.append("candidate did not strictly improve overall accuracy")
        if baseline_privacy is not None and candidate_privacy is not None and candidate_privacy < baseline_privacy:
            blockers.append("candidate regressed privacy sensitivity accuracy")
        if candidate_score.get("invalid_candidate_items"):
            blockers.append("candidate labels missing or invalid for some audited items")
        status = "accepted_shadow" if not blockers else "rejected"
        comparison.update(
            {
                "accepted": not blockers,
                "activation_allowed": False,
                "blockers": blockers,
                "baseline_overall_accuracy": baseline_acc,
                "candidate_overall_accuracy": candidate_acc,
                "baseline_privacy_accuracy": baseline_privacy,
                "candidate_privacy_accuracy": candidate_privacy,
            }
        )
    conn.execute(
        """
        INSERT INTO qwen_prompt_trials
            (id, eval_set_id, candidate_name, status, gate_json, candidate_prompt_json,
             baseline_score_json, candidate_score_json, comparison_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trial_id,
            resolved_set_id,
            candidate_name,
            status,
            canonical_json(gate),
            canonical_json(candidate_prompt or {}),
            canonical_json(baseline_score),
            canonical_json(candidate_score),
            canonical_json(comparison),
            created_at,
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "trial": {
            "id": trial_id,
            "eval_set_id": resolved_set_id,
            "candidate_name": candidate_name,
            "status": status,
            "gate": gate,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "comparison": comparison,
            "created_at": created_at,
        },
    }


def list_qwen_prompt_trials(conn: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM qwen_prompt_trials
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    trials = []
    for row in rows:
        item = dict(row)
        item["gate"] = _decode_json(item.pop("gate_json", None), {})
        item["candidate_prompt"] = _decode_json(item.pop("candidate_prompt_json", None), {})
        item["baseline_score"] = _decode_json(item.pop("baseline_score_json", None), {})
        item["candidate_score"] = _decode_json(item.pop("candidate_score_json", None), {})
        item["comparison"] = _decode_json(item.pop("comparison_json", None), {})
        trials.append(item)
    return {"ok": True, "trials": trials}


def _clip_window(event_ts: str | None, tier: str) -> tuple[str | None, str | None, dict[str, Any]]:
    event_dt = _parse_ts(event_ts)
    if tier == "medium_clip":
        before_s, after_s, frames = 8.0, 8.0, 64
    elif tier == "qwen_packet":
        before_s, after_s, frames = 0.0, 0.0, 4
    else:
        before_s, after_s, frames = 2.0, 2.0, 16
        tier = "short_clip"
    if not event_dt:
        return None, None, {
            "tier": tier,
            "fps": 4,
            "target_frames": frames,
            "window_before_s": before_s,
            "window_after_s": after_s,
            "export_enabled": False,
            "source": "manifest_only",
        }
    return (
        (event_dt - timedelta(seconds=before_s)).isoformat(),
        (event_dt + timedelta(seconds=after_s)).isoformat(),
        {
            "tier": tier,
            "fps": 4,
            "target_frames": frames,
            "window_before_s": before_s,
            "window_after_s": after_s,
            "export_enabled": False,
            "source": "manifest_only",
        },
    )


def create_training_clip_manifest(
    conn: sqlite3.Connection,
    *,
    packet_id: str,
    tier: str = "short_clip",
    actor: str = "home_app",
) -> dict[str, Any]:
    detail = get_observation_packet(conn, packet_id)
    packet = detail["packet"]
    labels = (detail.get("latest_label") or {}).get("labels") or {}
    timing = detail.get("capture_timing") or {}
    privacy_flags = list(packet.get("privacy_flags") or [])
    privacy_label = _axis_label(labels, "privacy_sensitivity")
    if privacy_label and privacy_label != "normal":
        privacy_flags.append(privacy_label)
    blockers: list[str] = []
    if privacy_flags:
        blockers.append("privacy review required")
    if int(packet.get("frame_count") or 0) < 2:
        blockers.append("fewer than 2 packet frames")
    if not timing.get("event_aligned"):
        blockers.append("packet frames are not event-aligned")
    if packet.get("qwen_status") not in {"valid", "not_requested"}:
        blockers.append(f"qwen status is {packet.get('qwen_status')}")
    status = "draft_eligible" if not blockers else "draft_review_required"
    start_ts, end_ts, fps_plan = _clip_window(packet.get("event_ts"), tier)
    quality = {
        "privacy_eligible": not privacy_flags,
        "blockers": blockers,
        "capture_timing": timing,
        "qwen_primary_activity": _axis_label(labels, "primary_activity"),
        "qwen_privacy_sensitivity": privacy_label,
        "export_allowed": False,
        "training_allowed": False,
        "created_by": actor,
    }
    manifest_id = _packet_id("tcm")
    conn.execute(
        """
        INSERT INTO training_clip_manifests
            (id, packet_id, status, camera, zone, start_ts, end_ts,
             fps_plan_json, frame_count_tier, linked_observation_ids_json,
             privacy_flags_json, quality_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            manifest_id,
            packet_id,
            status,
            packet.get("camera"),
            packet.get("zone"),
            start_ts,
            end_ts,
            canonical_json(fps_plan),
            fps_plan["tier"],
            canonical_json([packet.get("observation_id")] if packet.get("observation_id") else []),
            canonical_json(sorted(set(privacy_flags))),
            canonical_json(quality),
            utc_now(),
        ),
    )
    conn.commit()
    return get_training_clip_manifest(conn, manifest_id) | {"created": True}


def _training_manifest_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["fps_plan"] = _decode_json(data.pop("fps_plan_json", None), {})
    data["linked_observation_ids"] = _decode_json(data.pop("linked_observation_ids_json", None), [])
    data["privacy_flags"] = _decode_json(data.pop("privacy_flags_json", None), [])
    data["quality"] = _decode_json(data.pop("quality_json", None), {})
    return data


def get_training_clip_manifest(conn: sqlite3.Connection, manifest_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM training_clip_manifests WHERE id = ?", (manifest_id,)).fetchone()
    if not row:
        raise ValueError("training clip manifest not found")
    return {"ok": True, "manifest": _training_manifest_from_row(row)}


def list_training_clip_manifests(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    params: list[Any] = []
    sql = "SELECT * FROM training_clip_manifests"
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    rows = conn.execute(sql, params).fetchall()
    return {"ok": True, "manifests": [_training_manifest_from_row(row) for row in rows]}


def multimodal_status(conn: sqlite3.Connection) -> dict[str, Any]:
    init_counts = {}
    for table in (
        "observation_packets",
        "observation_frames",
        "qwen_label_results",
        "training_clip_manifests",
        "observation_audits",
        "label_eval_sets",
        "label_eval_items",
        "qwen_prompt_trials",
    ):
        init_counts[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    pending_labels = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM observation_packets
        WHERE frame_count > 0 AND qwen_status IN ('not_requested', 'invalid', 'error')
        """
    ).fetchone()["c"]
    stale_packets = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM observation_packets
        WHERE status IN ('capture_deferred', 'frames_missing', 'label_failed')
        """
    ).fetchone()["c"]
    return {
        "ok": True,
        "config": multimodal_config(),
        "counts": init_counts,
        "pending_label_jobs": pending_labels,
        "stale_or_failed_packets": stale_packets,
        "ring_buffer": ring_buffer_status(),
        "prompt_contract": {
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "allowed_labels": ENUMS,
            "inference_defaults": INFERENCE_DEFAULTS,
        },
    }
