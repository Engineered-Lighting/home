#!/usr/bin/env python3
"""Read-only Frigate/HA occupancy watcher.

Polls Frigate's direct events API and Home Assistant occupancy/classifier
entities side by side. This is meant for the failure mode where HA lighting
stays vacant and we need to know whether Frigate is missing `person`, HA is
not mirroring it, or the Living Lights classifier/actuator path is ignoring it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "tools" / "reports"
DEFAULT_FRIGATE_HTTP = os.environ.get("FRIGATE_HTTP", "http://192.168.0.125:5000").rstrip("/")
DEFAULT_HA_HTTP = os.environ.get("HA_HTTP", "http://homeassistant.local:8123").rstrip("/")

ZONE_BY_CAMERA = {
    "living_room": ("sofa", "front_left", "weights", "office", "front_door", "whole_living_room"),
    "kitchen": ("sink", "island_left", "island_right", "whole_kitchen"),
    "dining_room": ("dining_left", "dining_right", "whole_dining_room"),
    "workshop": ("workshop_zone",),
    "driveway": ("e28",),
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_token() -> str | None:
    for key in ("HA_TOKEN", "HOMEASSISTANT_TOKEN"):
        token = os.environ.get(key, "").strip()
        if token:
            return token

    cache = Path("/tmp/ha_token.txt")
    if cache.exists():
        token = cache.read_text(encoding="utf-8").strip()
        if token:
            return token

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "ConnectTimeout=5",
                "hav-ubuntu",
                "grep -hE '^HA_TOKEN|HOMEASSISTANT_TOKEN' /opt/home-ai-voice/.env",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None

    for line in result.stdout.splitlines():
        if "=" in line:
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    return None


def get_json(url: str, token: str | None = None, timeout: int = 8) -> Any:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def compact_state(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {"missing": True}
    attrs = item.get("attributes") or {}
    out = {
        "state": item.get("state"),
        "last_changed": item.get("last_changed"),
        "last_updated": item.get("last_updated"),
    }
    for key in ("friendly_name", "predicted_brightness_pct", "ramp_initial_pct", "dwell_ms"):
        if key in attrs:
            out[key] = attrs[key]
    return out


def normalize_zone_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def event_zones(event: dict[str, Any]) -> list[str]:
    data = event.get("data") or {}
    candidates = (
        event.get("zones"),
        event.get("current_zones"),
        event.get("entered_zones"),
        data.get("zones"),
        data.get("current_zones"),
        data.get("entered_zones"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [str(item) for item in candidate if str(item).strip()]
        if isinstance(candidate, str) and candidate.strip():
            return [candidate.strip()]
    return []


def event_summary(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") or {}
    return {
        "id": event.get("id"),
        "camera": event.get("camera"),
        "label": event.get("label"),
        "sub_label": event.get("sub_label"),
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "zones": event_zones(event),
        "score": data.get("score"),
        "top_score": data.get("top_score"),
        "box": data.get("box"),
    }


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def camera_health_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    stats_seen = 0
    detection_disabled = False
    zero_camera_fps = False
    zero_process_fps = False
    zero_detection_fps = False
    camera_fps: list[float] = []
    process_fps: list[float] = []
    detection_fps: list[float] = []

    for record in records:
        stats = ((record.get("frigate") or {}).get("camera_stats") or {})
        if not isinstance(stats, dict) or not stats:
            continue
        stats_seen += 1
        if stats.get("detection_enabled") is False:
            detection_disabled = True

        for key, bucket in (
            ("camera_fps", camera_fps),
            ("process_fps", process_fps),
            ("detection_fps", detection_fps),
        ):
            number = _num(stats.get(key))
            if number is not None:
                bucket.append(number)

    if camera_fps and max(camera_fps) <= 0:
        zero_camera_fps = True
    if process_fps and max(process_fps) <= 0:
        zero_process_fps = True
    if detection_fps and max(detection_fps) <= 0:
        zero_detection_fps = True

    issues: list[str] = []
    if detection_disabled:
        issues.append("detection is disabled")
    if zero_camera_fps:
        issues.append("camera FPS is zero")
    if zero_process_fps:
        issues.append("process FPS is zero")
    if zero_detection_fps:
        issues.append("detection FPS is zero")

    def avg(values: list[float]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    return {
        "stats_seen": stats_seen,
        "issues": issues,
        "healthy": stats_seen > 0 and not issues,
        "camera_fps_avg": avg(camera_fps),
        "process_fps_avg": avg(process_fps),
        "detection_fps_avg": avg(detection_fps),
    }


def collect_ha_snapshot(ha_http: str, token: str | None, camera: str, zones: tuple[str, ...]) -> dict[str, Any]:
    if not token:
        return {"error": "no_ha_token"}
    states = get_json(f"{ha_http}/api/states", token=token, timeout=12)
    by_id = {item.get("entity_id"): item for item in states}
    entity_ids = [
        f"binary_sensor.{camera}_motion",
        f"binary_sensor.{camera}_all_occupancy",
        f"binary_sensor.{camera}_person_occupancy",
        "binary_sensor.living_lights_any_occupied",
        "input_boolean.living_lights_asleep",
        "input_boolean.homeai_sleep",
    ]
    for zone in zones:
        entity_ids.extend(
            [
                f"binary_sensor.{zone}_person_occupancy",
                f"binary_sensor.{camera}_{zone}_person_occupancy_stable",
                f"sensor.{camera}_{zone}_lighting_state",
            ]
        )
    return {entity_id: compact_state(by_id.get(entity_id)) for entity_id in entity_ids}


def collect_frigate_snapshot(frigate_http: str, camera: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"camera": camera, "limit": 25, "include_thumbnails": 0})
    events = get_json(f"{frigate_http}/api/events?{query}")
    if not isinstance(events, list):
        events = events.get("events") or []
    stats = get_json(f"{frigate_http}/api/stats")
    camera_stats = (stats.get("cameras") or {}).get(camera) or {}
    return {
        "camera_stats": {
            "camera_fps": camera_stats.get("camera_fps"),
            "process_fps": camera_stats.get("process_fps"),
            "detection_fps": camera_stats.get("detection_fps"),
            "detection_enabled": camera_stats.get("detection_enabled"),
        },
        "recent_events": [event_summary(item) for item in events[:15]],
    }


def write_jsonl(handle: Any, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")
    handle.flush()


def load_jsonl_records(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    camera: str | None = None
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if payload.get("kind") == "run_start":
                camera = str(payload.get("camera") or "") or camera
                continue
            if payload.get("kind") == "sample":
                records.append(payload)
                if camera is None:
                    events = ((payload.get("frigate") or {}).get("recent_events") or [])
                    for event in events:
                        if event.get("camera"):
                            camera = str(event["camera"])
                            break
    return camera, records


def summarize(records: list[dict[str, Any]], camera: str, md_path: Path, jsonl_path: Path) -> None:
    seen_event_ids: set[str] = set()
    label_counts: Counter[str] = Counter()
    person_events: list[dict[str, Any]] = []
    person_events_in_expected_zone: list[dict[str, Any]] = []
    recent_events: list[dict[str, Any]] = []
    occupancy_on_seen = False
    classifier_non_vacant_seen = False
    expected_zone_names = {
        normalize_zone_name(zone)
        for zone in ZONE_BY_CAMERA.get(camera, ())
    }

    for record in records:
        for event in (record.get("frigate") or {}).get("recent_events") or []:
            event_id = event.get("id")
            if not event_id or event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            recent_events.append(event)
            label = event.get("label") or "unknown"
            label_counts[label] += 1
            if label == "person":
                person_events.append(event)
                normalized_event_zones = {
                    normalize_zone_name(zone)
                    for zone in (event.get("zones") or [])
                }
                if normalized_event_zones & expected_zone_names:
                    person_events_in_expected_zone.append(event)
        for entity_id, state in (record.get("ha") or {}).items():
            if not isinstance(state, dict):
                continue
            if entity_id.endswith("_person_occupancy") and state.get("state") == "on":
                occupancy_on_seen = True
            if entity_id.endswith("_lighting_state") and state.get("state") not in (None, "vacant", "unknown", "unavailable"):
                classifier_non_vacant_seen = True

    health = camera_health_summary(records)
    lines = [
        f"# Frigate Occupancy Watch - {camera}",
        "",
        f"Raw JSONL: `{jsonl_path.name}`",
        "",
        "## Frigate Camera Health",
        "",
    ]
    if health["stats_seen"]:
        lines.append(
            "- avg camera/process/detection FPS: "
            f"{health['camera_fps_avg']}/{health['process_fps_avg']}/{health['detection_fps_avg']}"
        )
        if health["issues"]:
            lines.append(f"- Health issue: {', '.join(health['issues'])}.")
        else:
            lines.append("- Camera stats looked healthy during the watch window.")
    else:
        lines.append("- No Frigate camera stats were returned by the direct API.")

    lines.extend([
        "",
        "## Frigate Labels Seen",
        "",
    ])
    if label_counts:
        for label, count in label_counts.most_common():
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- No Frigate events were returned by the direct API.")

    lines.extend(["", "## Recent Events", ""])
    for event in recent_events[:12]:
        lines.append(
            f"- {event.get('label')} zones={event.get('zones')} "
            f"score={event.get('score')} top={event.get('top_score')} id={event.get('id')}"
        )
    if not recent_events:
        lines.append("- None")

    lines.extend(["", "## Diagnosis", ""])
    if health["issues"]:
        lines.append(
            "- Frigate camera/detection pipeline looked unhealthy "
            f"({', '.join(health['issues'])}). Fix camera/detect throughput before debugging the HA mirror or lighting cascade."
        )
    elif person_events and not occupancy_on_seen:
        if not person_events_in_expected_zone:
            expected = ", ".join(sorted(expected_zone_names)) or "no configured zones"
            lines.append(
                "- Frigate produced person events, but none landed in the watched lighting zones "
                f"({expected}). Focus on Frigate zone geometry, masks, and zone-name mapping before debugging the HA mirror or lighting cascade."
            )
        else:
            lines.append("- Frigate produced person events in watched zones, but HA never showed a watched person occupancy entity on. Focus on MQTT/Home Assistant integration or zone-name mapping.")
    elif not person_events and label_counts:
        lines.append("- Frigate produced events, but none were labeled person. Focus on Frigate model/filter/camera conditions before debugging the lighting cascade.")
    elif occupancy_on_seen and not classifier_non_vacant_seen:
        lines.append("- HA person occupancy turned on, but the Living Lights classifier stayed vacant. Focus on template/classifier triggers.")
    elif classifier_non_vacant_seen:
        lines.append("- The classifier became non-vacant; if lights still did not change, focus on actuator/manual-override/cascade logic.")
    else:
        lines.append("- No useful movement evidence was captured. Re-run while walking through the camera view.")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default=None, choices=sorted(ZONE_BY_CAMERA))
    parser.add_argument("--duration-min", type=float, default=5.0)
    parser.add_argument("--interval-s", type=float, default=10.0)
    parser.add_argument("--frigate-http", default=DEFAULT_FRIGATE_HTTP)
    parser.add_argument("--ha-http", default=DEFAULT_HA_HTTP)
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--from-jsonl", type=Path, default=None, help="Re-render an existing watcher JSONL without polling live services.")
    parser.add_argument("--md-output", type=Path, default=None, help="Markdown output path for --from-jsonl. Defaults to the JSONL path with .md.")
    args = parser.parse_args()

    if args.from_jsonl:
        camera, records = load_jsonl_records(args.from_jsonl)
        camera = args.camera or camera or "living_room"
        md_path = args.md_output or args.from_jsonl.with_suffix(".md")
        summarize(records, camera, md_path, args.from_jsonl)
        print(f"Summary: {md_path}")
        return 0

    camera = args.camera or "living_room"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    jsonl_path = args.output_dir / f"frigate-occupancy-watch-{camera}-{stamp}.jsonl"
    md_path = args.output_dir / f"frigate-occupancy-watch-{camera}-{stamp}.md"
    token = load_token()
    zones = ZONE_BY_CAMERA[camera]
    deadline = time.time() + max(1, int(args.duration_min * 60))
    records: list[dict[str, Any]] = []

    with jsonl_path.open("w", encoding="utf-8") as handle:
        write_jsonl(
            handle,
            {
                "kind": "run_start",
                "utc": utc_stamp(),
                "camera": camera,
                "zones": zones,
                "frigate_http": args.frigate_http,
                "ha_http": args.ha_http,
                "ha_token_loaded": bool(token),
            },
        )
        while time.time() < deadline:
            record = {"kind": "sample", "utc": utc_stamp()}
            try:
                record["frigate"] = collect_frigate_snapshot(args.frigate_http.rstrip("/"), camera)
            except Exception as exc:
                record["frigate_error"] = f"{type(exc).__name__}: {exc}"
            try:
                record["ha"] = collect_ha_snapshot(args.ha_http.rstrip("/"), token, camera, zones)
            except Exception as exc:
                record["ha_error"] = f"{type(exc).__name__}: {exc}"
            records.append(record)
            write_jsonl(handle, record)
            labels = Counter(
                item.get("label") or "unknown"
                for item in (record.get("frigate") or {}).get("recent_events") or []
            )
            print(f"{record['utc']} labels={dict(labels)}", flush=True)
            time.sleep(args.interval_s)

    summarize(records, camera, md_path, jsonl_path)
    print(f"Summary: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
