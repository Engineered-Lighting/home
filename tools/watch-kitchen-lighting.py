#!/usr/bin/env python3
"""Focused Living Lights watcher for kitchen/manual-override debugging.

Captures Home Assistant websocket events for the kitchen Frigate zone inputs,
Living Lights classifiers, manual override helpers, actuator automations, and
the physical kitchen lights. Also takes periodic REST snapshots so a quiet
period still proves whether the lights stayed on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import websockets  # type: ignore
except ImportError:
    raise SystemExit("Install websockets first: py -3 -m pip install websockets")


REPO = Path(__file__).resolve().parent.parent
REPORT_DIR = REPO / "tools" / "reports"
HA_HTTP = os.environ.get("HA_HTTP", "http://homeassistant.local:8123").rstrip("/")
HA_WS = os.environ.get("HA_WS", HA_HTTP.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket")

KITCHEN_ZONES = ("sink", "island_left", "island_right", "whole_kitchen")
KITCHEN_LIGHTS = ("light.sink", "light.island_left", "light.island_right")
MANUAL_HOLD_EVENTS = (
    "living_lights_manual_hold_armed",
    "living_lights_override_detected",
)

BROAD_OCCUPANCY = (
    "binary_sensor.kitchen_all_occupancy",
    "binary_sensor.kitchen_motion",
    "binary_sensor.kitchen_person_occupancy",
)

WATCHED_ENTITIES = set(BROAD_OCCUPANCY)
WATCHED_ENTITIES.update(f"binary_sensor.{z}_person_occupancy" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"binary_sensor.kitchen_{z}_person_occupancy_stable" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"sensor.kitchen_{z}_lighting_state" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"input_datetime.living_lights_zone_{z}_last_manual_at" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"input_text.living_lights_override_text_{z}" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"input_text.living_lights_zone_{z}_last_command_id" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(f"input_boolean.living_lights_zone_{z}_enabled" for z in KITCHEN_ZONES)
WATCHED_ENTITIES.update(KITCHEN_LIGHTS)
WATCHED_ENTITIES.update(
    {
        "input_boolean.living_lights_enabled",
        "input_boolean.living_lights_shadow",
        "input_boolean.living_lights_asleep",
        "input_boolean.living_lights_night_safe_mode",
        "binary_sensor.living_lights_any_occupied",
        "sensor.living_lights_profile",
        "automation.living_lights_sink_actuator",
        "automation.living_lights_island_left_actuator",
        "automation.living_lights_island_right_actuator",
        "automation.pilar_knob_kitchen",
    }
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_token() -> str:
    for key in ("HA_TOKEN", "HOMEASSISTANT_TOKEN"):
        token = os.environ.get(key, "").strip()
        if token:
            return token

    cache = Path("/tmp/ha_token.txt")
    if cache.exists():
        token = cache.read_text(encoding="utf-8").strip()
        if token:
            return token

    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "hav-ubuntu",
        "grep -hE '^HA_TOKEN|HOMEASSISTANT_TOKEN' /opt/home-ai-voice/.env",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        raise SystemExit(f"Could not load HA token via ssh: {exc}") from exc
    for line in result.stdout.splitlines():
        if "=" in line:
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    raise SystemExit("Could not load HA token from env, /tmp cache, or hav-ubuntu.")


def rest_json(path: str, token: str) -> Any:
    request = urllib.request.Request(
        f"{HA_HTTP}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=7) as response:
        return json.loads(response.read().decode("utf-8"))


def compact_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {"missing": True}
    attrs = state.get("attributes") or {}
    row: dict[str, Any] = {
        "state": state.get("state"),
        "last_changed": state.get("last_changed"),
        "last_updated": state.get("last_updated"),
    }
    for key in (
        "brightness",
        "color_temp_kelvin",
        "friendly_name",
        "predicted_brightness_pct",
        "predicted_color_temp_k",
        "ramp_seconds",
        "last_triggered",
    ):
        if key in attrs:
            row[key] = attrs.get(key)
    if "brightness" in row and isinstance(row["brightness"], (int, float)):
        row["brightness_pct"] = round(float(row["brightness"]) / 255.0 * 100.0, 1)
    return row


def collect_snapshot(token: str) -> dict[str, Any]:
    states = rest_json("/api/states", token)
    by_id = {item.get("entity_id"): item for item in states}
    return {entity_id: compact_state(by_id.get(entity_id)) for entity_id in sorted(WATCHED_ENTITIES)}


def write_jsonl(handle: Any, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")
    handle.flush()


def nested_entity_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        if "." in value:
            found.add(value)
    elif isinstance(value, list):
        for item in value:
            found.update(nested_entity_ids(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(nested_entity_ids(item))
    return found


def interesting_call_service(data: dict[str, Any]) -> bool:
    domain = data.get("domain")
    service_data = data.get("service_data") or {}
    target = data.get("target") or {}
    entities = nested_entity_ids(service_data) | nested_entity_ids(target)
    if domain == "light" and (not entities or entities & set(KITCHEN_LIGHTS)):
        return True
    if entities & WATCHED_ENTITIES:
        return True
    if domain in {"input_datetime", "input_text", "automation"} and entities & WATCHED_ENTITIES:
        return True
    return False


def interesting_frigate_event(data: dict[str, Any]) -> bool:
    raw = json.dumps(data).lower()
    if "kitchen" in raw:
        return True
    return any(zone in raw for zone in KITCHEN_ZONES)


def summarize_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    event_type = event.get("event_type")
    data = event.get("data") or {}
    if event_type == "state_changed":
        entity_id = data.get("entity_id")
        return {
            "entity_id": entity_id,
            "old_state": compact_state(data.get("old_state")),
            "new_state": compact_state(data.get("new_state")),
        }
    if event_type == "call_service":
        return {
            "domain": data.get("domain"),
            "service": data.get("service"),
            "service_data": data.get("service_data") or {},
            "target": data.get("target") or {},
        }
    if event_type == "frigate_event":
        after = data.get("after") if isinstance(data.get("after"), dict) else {}
        before = data.get("before") if isinstance(data.get("before"), dict) else {}
        return {
            "type": data.get("type"),
            "camera": data.get("camera") or after.get("camera") or before.get("camera"),
            "label": data.get("label") or after.get("label") or before.get("label"),
            "entered_zones": data.get("entered_zones") or after.get("entered_zones") or before.get("entered_zones"),
            "current_zones": data.get("current_zones") or after.get("current_zones") or before.get("current_zones"),
            "raw": data,
        }
    if event_type in MANUAL_HOLD_EVENTS:
        keep = (
            "context_id",
            "zone",
            "light_entity",
            "owning_zones",
            "trigger_attribute",
            "source_user_id",
            "source_parent_id",
            "debug_match_ok",
            "debug_match_reason",
            "actual_pct",
            "predicted_pct",
            "delta_pct",
            "state",
            "asleep",
            "any_occupied",
        )
        return {key: data.get(key) for key in keep if key in data}
    return data


def light_line(snapshot: dict[str, Any]) -> str:
    bits = []
    for entity_id in KITCHEN_LIGHTS:
        state = snapshot.get(entity_id, {})
        pct = state.get("brightness_pct")
        suffix = f" {pct:.0f}%" if isinstance(pct, (int, float)) else ""
        bits.append(f"{entity_id.replace('light.', '')}={state.get('state')}{suffix}")
    return ", ".join(bits)


def occupancy_line(snapshot: dict[str, Any]) -> str:
    parts = []
    for entity_id in (
        "binary_sensor.kitchen_all_occupancy",
        "binary_sensor.kitchen_motion",
        "binary_sensor.sink_person_occupancy",
        "binary_sensor.island_left_person_occupancy",
        "binary_sensor.island_right_person_occupancy",
        "binary_sensor.whole_kitchen_person_occupancy",
        "binary_sensor.living_lights_any_occupied",
    ):
        state = snapshot.get(entity_id, {}).get("state")
        parts.append(f"{entity_id.replace('binary_sensor.', '')}={state}")
    return ", ".join(parts)


async def ws_subscribe(ws: Any, event_type: str, msg_id: int) -> None:
    await ws.send(json.dumps({"id": msg_id, "type": "subscribe_events", "event_type": event_type}))
    reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if not reply.get("success"):
        raise RuntimeError(f"subscribe_events failed for {event_type}: {reply}")


async def watch(duration_s: int, token: str, jsonl_path: Path, md_path: Path) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    counters: Counter[str] = Counter()
    entity_changes: Counter[str] = Counter()
    last_snapshot: dict[str, Any] = {}
    light_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    occupancy_samples: list[dict[str, Any]] = []
    frigate_events: list[dict[str, Any]] = []
    call_services: list[dict[str, Any]] = []
    manual_events: list[dict[str, Any]] = []

    with jsonl_path.open("w", encoding="utf-8") as handle:
        start_ts = time.time()
        write_jsonl(
            handle,
            {
                "kind": "run_start",
                "utc": utc_stamp(),
                "duration_s": duration_s,
                "ha_http": HA_HTTP,
                "watched_entities": sorted(WATCHED_ENTITIES),
            },
        )

        try:
            last_snapshot = collect_snapshot(token)
            write_jsonl(handle, {"kind": "snapshot", "phase": "initial", "utc": utc_stamp(), "states": last_snapshot})
            print("Initial lights:", light_line(last_snapshot), flush=True)
            print("Initial occupancy:", occupancy_line(last_snapshot), flush=True)
        except Exception as exc:
            write_jsonl(handle, {"kind": "snapshot_error", "phase": "initial", "utc": utc_stamp(), "error": str(exc)})

        async with websockets.connect(HA_WS, ping_interval=20, close_timeout=5) as ws:
            auth_required = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth_required.get("type") != "auth_required":
                raise RuntimeError(f"unexpected auth handshake: {auth_required}")
            await ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth_ok = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if auth_ok.get("type") != "auth_ok":
                raise RuntimeError(f"auth failed: {auth_ok}")
            await ws_subscribe(ws, "state_changed", 1)
            await ws_subscribe(ws, "call_service", 2)
            await ws_subscribe(ws, "frigate_event", 3)
            for offset, event_type in enumerate(MANUAL_HOLD_EVENTS, start=4):
                await ws_subscribe(ws, event_type, offset)
            print(f"Watching for {duration_s}s. Output: {jsonl_path.name}", flush=True)

            next_sample = 0.0
            next_progress = 0.0
            while time.time() - start_ts < duration_s:
                now = time.time()
                if now >= next_sample:
                    try:
                        last_snapshot = collect_snapshot(token)
                        sample = {"kind": "sample", "utc": utc_stamp(), "states": last_snapshot}
                        write_jsonl(handle, sample)
                        occupancy_samples.append(sample)
                        for light in KITCHEN_LIGHTS:
                            light_samples[light].append(last_snapshot.get(light, {}))
                    except Exception as exc:
                        write_jsonl(handle, {"kind": "snapshot_error", "phase": "sample", "utc": utc_stamp(), "error": str(exc)})
                    next_sample = now + 10

                if now >= next_progress:
                    if last_snapshot:
                        print("Progress:", light_line(last_snapshot), "|", occupancy_line(last_snapshot), flush=True)
                    next_progress = now + 30

                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue

                payload = json.loads(message)
                if payload.get("type") != "event":
                    continue
                event = payload.get("event") or {}
                event_type = event.get("event_type")
                data = event.get("data") or {}

                should_log = False
                if event_type == "state_changed" and data.get("entity_id") in WATCHED_ENTITIES:
                    should_log = True
                    entity_changes[data.get("entity_id")] += 1
                elif event_type == "call_service" and interesting_call_service(data):
                    should_log = True
                elif event_type == "frigate_event" and interesting_frigate_event(data):
                    should_log = True
                elif event_type in MANUAL_HOLD_EVENTS:
                    should_log = True

                if not should_log:
                    continue

                counters[event_type or "unknown"] += 1
                row = {
                    "kind": "event",
                    "utc": utc_stamp(),
                    "event_type": event_type,
                    "time_fired": event.get("time_fired"),
                    "data": summarize_event_payload(event),
                }
                write_jsonl(handle, row)
                if event_type == "frigate_event":
                    frigate_events.append(row)
                elif event_type == "call_service":
                    call_services.append(row)
                elif event_type in MANUAL_HOLD_EVENTS:
                    manual_events.append(row)

        try:
            last_snapshot = collect_snapshot(token)
            write_jsonl(handle, {"kind": "snapshot", "phase": "final", "utc": utc_stamp(), "states": last_snapshot})
        except Exception as exc:
            write_jsonl(handle, {"kind": "snapshot_error", "phase": "final", "utc": utc_stamp(), "error": str(exc)})

    write_summary(
        md_path=md_path,
        jsonl_path=jsonl_path,
        duration_s=duration_s,
        counters=counters,
        entity_changes=entity_changes,
        final_snapshot=last_snapshot,
        light_samples=light_samples,
        occupancy_samples=occupancy_samples,
        frigate_events=frigate_events,
        call_services=call_services,
        manual_events=manual_events,
    )
    print(f"Summary: {md_path}", flush=True)


def pct_values(samples: list[dict[str, Any]]) -> list[float]:
    values = []
    for sample in samples:
        pct = sample.get("brightness_pct")
        if isinstance(pct, (int, float)):
            values.append(float(pct))
    return values


def call_service_entities(row: dict[str, Any]) -> set[str]:
    data = row.get("data") or {}
    return nested_entity_ids(data.get("service_data") or {}) | nested_entity_ids(data.get("target") or {})


def call_service_brightness_pct(row: dict[str, Any]) -> float | None:
    data = row.get("data") or {}
    service_data = data.get("service_data") or {}
    pct = service_data.get("brightness_pct")
    if isinstance(pct, (int, float)):
        return float(pct)
    raw = service_data.get("brightness")
    if isinstance(raw, (int, float)):
        return round(float(raw) / 255.0 * 100.0, 1)
    return None


def is_kitchen_light_call(row: dict[str, Any]) -> bool:
    data = row.get("data") or {}
    if data.get("domain") != "light":
        return False
    entities = call_service_entities(row)
    return not entities or bool(entities & set(KITCHEN_LIGHTS))


def kitchen_light_call_label(row: dict[str, Any]) -> str:
    pct = call_service_brightness_pct(row)
    entities = sorted(call_service_entities(row) & set(KITCHEN_LIGHTS))
    target = ", ".join(entities) if entities else "kitchen lights"
    if pct is None:
        return target
    return f"{target} at {pct:g}%"


def final_light_low(final: dict[str, Any]) -> bool:
    if final.get("state") == "off":
        return True
    pct = final.get("brightness_pct")
    return isinstance(pct, (int, float)) and pct <= 35


def diagnosis_hints(
    final_snapshot: dict[str, Any],
    light_samples: dict[str, list[dict[str, Any]]],
    frigate_events: list[dict[str, Any]],
    call_services: list[dict[str, Any]],
    manual_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    hints: list[str] = []
    broad_on = any(
        final_snapshot.get(entity_id, {}).get("state") == "on"
        for entity_id in ("binary_sensor.kitchen_all_occupancy", "binary_sensor.kitchen_motion")
    )
    zone_on = any(
        final_snapshot.get(f"binary_sensor.{zone}_person_occupancy", {}).get("state") == "on"
        for zone in KITCHEN_ZONES
    )
    asleep_on = final_snapshot.get("input_boolean.living_lights_asleep", {}).get("state") == "on"
    manual_events = manual_events or []
    manual_hold_armed = any(row.get("event_type") == "living_lights_manual_hold_armed" for row in manual_events)

    if broad_on and not zone_on:
        hints.append("- Broad kitchen occupancy/motion is on, but all Living Lights kitchen zone person sensors are off.")
    if asleep_on and not zone_on:
        hints.append("- Asleep is on while the kitchen zones are vacant, so the classifier will continue to predict low/off unless manual override blocks it.")
    if not frigate_events:
        hints.append("- No Frigate bus events were seen during this capture window; that points at either no detected zone event or a bus/integration subscription gap.")

    high_light_call = any(
        is_kitchen_light_call(row)
        and (call_service_brightness_pct(row) is None or call_service_brightness_pct(row) >= 70)
        and (row.get("data") or {}).get("service") in {"turn_on", "toggle"}
        for row in call_services
    )
    low_final_lights = [
        light for light in KITCHEN_LIGHTS
        if final_light_low(final_snapshot.get(light, {}))
    ]
    dropped_lights = []
    for light in KITCHEN_LIGHTS:
        pcts = pct_values(light_samples.get(light, []))
        if pcts and max(pcts) >= 70 and final_light_low(final_snapshot.get(light, {})):
            dropped_lights.append(light)

    high_call_indices = [
        index for index, row in enumerate(call_services)
        if is_kitchen_light_call(row)
        and (row.get("data") or {}).get("service") in {"turn_on", "toggle"}
        and (call_service_brightness_pct(row) is None or call_service_brightness_pct(row) >= 70)
    ]
    later_low_calls = [
        row for index, row in enumerate(call_services)
        if is_kitchen_light_call(row)
        and (row.get("data") or {}).get("service") == "turn_on"
        and isinstance(call_service_brightness_pct(row), (int, float))
        and call_service_brightness_pct(row) <= 35
        and high_call_indices
        and index > min(high_call_indices)
    ]

    if later_low_calls and low_final_lights:
        sample = kitchen_light_call_label(later_low_calls[0])
        hints.append(
            "- A later low kitchen light.turn_on call followed the high user command "
            f"({sample}); that means an automation/cascade write likely overwrote the manual command."
        )
    if high_light_call and low_final_lights:
        hints.append(
            "- A watched kitchen light service call requested high brightness, but at least one kitchen light ended low/off. Focus on manual override hold/cooldown, asleep cap precedence, or an actuator write after the user command."
        )
    elif dropped_lights:
        hints.append(
            "- A kitchen light reached high brightness during the capture and later ended low/off. Focus on which later service call or classifier transition won the cascade."
        )
    if asleep_on and (high_light_call or dropped_lights) and low_final_lights:
        hints.append("- Asleep was still on while brightness failed to stick; verify the manual-override path blocks the asleep cap before the next pilot tick.")
    if (high_light_call or dropped_lights) and low_final_lights and not manual_hold_armed:
        hints.append(
            "- No living_lights_manual_hold_armed event was captured after the high brightness change; verify the fast manual-hold armer is loaded/enabled and watching the same light entity that changed."
        )
    return hints


def write_summary(
    md_path: Path,
    jsonl_path: Path,
    duration_s: int,
    counters: Counter[str],
    entity_changes: Counter[str],
    final_snapshot: dict[str, Any],
    light_samples: dict[str, list[dict[str, Any]]],
    occupancy_samples: list[dict[str, Any]],
    frigate_events: list[dict[str, Any]],
    call_services: list[dict[str, Any]],
    manual_events: list[dict[str, Any]] | None = None,
) -> None:
    manual_events = manual_events or []
    lines = [
        "# Kitchen Lighting Watch",
        "",
        f"Run duration: {duration_s}s",
        f"Raw JSONL: `{jsonl_path.name}`",
        "",
        "## Final State",
        "",
        f"- Lights: {light_line(final_snapshot)}",
        f"- Occupancy: {occupancy_line(final_snapshot)}",
        f"- Asleep: {final_snapshot.get('input_boolean.living_lights_asleep', {}).get('state')}",
        "",
        "## Event Counts",
        "",
    ]
    if counters:
        for key, value in sorted(counters.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No watched websocket events captured.")

    lines.extend(["", "## Entity Changes", ""])
    if entity_changes:
        for entity_id, count in entity_changes.most_common():
            lines.append(f"- {entity_id}: {count}")
    else:
        lines.append("- No watched entity state changes captured.")

    lines.extend(["", "## Manual Hold Events", ""])
    if manual_events:
        for row in manual_events[-10:]:
            data = row.get("data") or {}
            event_type = row.get("event_type")
            zone = data.get("zone", "?")
            light = data.get("light_entity", "?")
            detail = data.get("debug_match_ok") or data.get("debug_match_reason") or ""
            suffix = f" ({detail})" if detail not in ["", None] else ""
            lines.append(f"- {row.get('time_fired') or row.get('utc')}: {event_type} {zone} {light}{suffix}")
    else:
        lines.append("- No manual hold/override events captured.")

    lines.extend(["", "## Light Samples", ""])
    for light in KITCHEN_LIGHTS:
        samples = light_samples.get(light, [])
        pcts = pct_values(samples)
        final = final_snapshot.get(light, {})
        if pcts:
            lines.append(
                f"- {light}: final {final.get('state')} {final.get('brightness_pct', '-')}%, "
                f"min {min(pcts):.1f}%, max {max(pcts):.1f}%, samples {len(samples)}"
            )
        else:
            lines.append(f"- {light}: no brightness samples")

    lines.extend(["", "## Frigate Events", ""])
    if frigate_events:
        for row in frigate_events[-10:]:
            data = row.get("data") or {}
            lines.append(
                f"- {row.get('time_fired')}: camera={data.get('camera')} label={data.get('label')} "
                f"entered={data.get('entered_zones')} current={data.get('current_zones')}"
            )
    else:
        lines.append("- No kitchen/zone Frigate bus events captured.")

    lines.extend(["", "## Service Calls", ""])
    if call_services:
        for row in call_services[-12:]:
            data = row.get("data") or {}
            lines.append(
                f"- {row.get('time_fired')}: {data.get('domain')}.{data.get('service')} "
                f"target={data.get('target')} service_data={data.get('service_data')}"
            )
    else:
        lines.append("- No watched service calls captured.")

    lines.extend(["", "## Diagnosis Hints", ""])
    hints = diagnosis_hints(final_snapshot, light_samples, frigate_events, call_services, manual_events)
    lines.extend(hints or ["- No obvious failure signature in the watched entities; inspect the raw JSONL event order."])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-min", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    jsonl_path = args.output_dir / f"kitchen-lighting-watch-{stamp}.jsonl"
    md_path = args.output_dir / f"kitchen-lighting-watch-{stamp}.md"
    token = load_token()
    asyncio.run(watch(int(args.duration_min * 60), token, jsonl_path, md_path))


if __name__ == "__main__":
    main()
