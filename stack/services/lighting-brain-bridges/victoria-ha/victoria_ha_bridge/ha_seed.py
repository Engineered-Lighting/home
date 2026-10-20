"""Optional cold-start seed from Home Assistant.

Frigate's person counts are not retained on MQTT, so a bridge that starts (or
reconnects) while someone sits still would not know they are there. Home
Assistant's Frigate integration has been holding those counts all along, so
one read of `/api/states` fills the gap. MQTT always wins afterwards.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

from .core import OCCUPANCY

log = logging.getLogger("victoria_ha_bridge")

UNUSABLE = {"unavailable", "unknown", "none", ""}


def default_entities(camera: str, zones: dict[str, str], object_label: str = "person") -> dict[str, list[str]]:
    """Entity ids the Frigate integration creates, count sensor first."""
    def pair(name: str) -> list[str]:
        return [f"sensor.{name}_{object_label}_count", f"binary_sensor.{name}_{object_label}_occupancy"]

    entities = {OCCUPANCY: pair(camera)}
    for zone, frigate_zone in zones.items():
        entities[f"zone:{zone}"] = pair(frigate_zone)
    return entities


def subject_values(
    states: list[dict[str, Any]], entities: dict[str, list[str]]
) -> dict[str, tuple[bool, int | None]]:
    """Map an `/api/states` list onto bridge subjects. Unusable states are skipped."""
    by_id = {item.get("entity_id"): str(item.get("state", "")).strip().lower() for item in states}
    values: dict[str, tuple[bool, int | None]] = {}
    for subject, candidates in entities.items():
        for entity_id in candidates:
            state = by_id.get(entity_id)
            if state is None or state in UNUSABLE:
                continue
            if entity_id.startswith("binary_sensor."):
                if state in ("on", "off"):
                    # The binary sensor knows presence, not how many.
                    values[subject] = (True, None) if state == "on" else (False, 0)
                    break
                continue
            try:
                count = max(int(float(state)), 0)
            except ValueError:
                continue
            values[subject] = (count > 0, count)
            break
    return values


def fetch_states(
    base_url: str, token: str, *, timeout_s: float = 5.0, opener: Callable[..., Any] | None = None
) -> list[dict[str, Any]]:
    """GET /api/states. Returns [] on any failure; the seed is best effort."""
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/states",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout_s) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("home assistant seed skipped: %s", exc)
        return []
    return data if isinstance(data, list) else []
