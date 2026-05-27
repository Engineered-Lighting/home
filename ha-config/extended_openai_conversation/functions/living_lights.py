"""Living-Lights function tools (Addendum 33 Phase 4.A).

Voice/agent-facing tools for the presence-override layer:

  - set_presence_override(zone, deltas, source_text)
      Apply an override to a zone's lighting until the user leaves +
      grace period. Voice commands like "make the dining lights
      brighter" route here. Writes JSON payload to
      input_text.living_lights_override_text_<zone>; the classifier
      sensor then transitions to `presence_override` state and the
      Phase 2+ actuator script reads brightness/color from the payload.

  - clear_presence_override(zone | "all")
      Clear an active override immediately. Voice "end the override" /
      "reset the lights" routes here.

All actions are gated by:
  - input_boolean.living_lights_enabled = on (master)
  - per-zone input_boolean.living_lights_zone_<slug>_enabled = on

Per Addendum 33 AR33-7: brightness=0 from a voice override is clamped
to 5% (lights-off must come from explicit `light.turn_off` calls, not
overrides). source ∈ {voice, app}; "auto" / null is rejected.

Per Addendum 33 AR33-2: when zone is currently vacant at override
time, payload includes `remote: true` flag → auto-clear automation
respects a longer max-lifetime instead of the 30s vacancy grace.

Per Addendum 33 AR33-3: `pinned: true` flag from the app's Pin button
keeps the override active until explicitly cleared (no auto-clear).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import Function

_LOGGER = logging.getLogger(__name__)

# Zone slug → set of known synonyms (used to resolve "dining room",
# "kitchen island left", etc to canonical slug).
# Phase 0a starts with 5 zones; expand as more zones promote.
ZONE_SYNONYMS: dict[str, list[str]] = {
    "dining_left": ["dining left", "left dining", "dining_left"],
    "dining_right": ["dining right", "right dining", "dining_right"],
    "sink": ["sink", "kitchen sink"],
    "island_left": ["island left", "left island", "kitchen island left"],
    "island_right": ["island right", "right island", "kitchen island right"],
}

# Valid override sources. AR33-7: reject "auto"/null to prevent
# agent-side hallucination from locking lights.
VALID_SOURCES = {"voice", "app", "hardware"}

# Brightness floor for overrides. AR33-7: zero is reserved for
# explicit light.turn_off; overrides never zero out lights.
MIN_OVERRIDE_BRIGHTNESS_PCT = 5
MAX_OVERRIDE_BRIGHTNESS_PCT = 100
# Color-temp clamps roughly the Hue/most-bulbs range.
MIN_OVERRIDE_KELVIN = 2000
MAX_OVERRIDE_KELVIN = 6500


def _resolve_zone(raw: str) -> str | None:
    """Map a free-text zone name to a canonical slug, or None if unknown."""
    if not raw:
        return None
    needle = raw.strip().lower().replace("_", " ")
    for slug, synonyms in ZONE_SYNONYMS.items():
        for syn in synonyms:
            if needle == syn.lower():
                return slug
    # Substring fallback: longest synonym wins
    best: tuple[int, str] | None = None
    for slug, synonyms in ZONE_SYNONYMS.items():
        for syn in synonyms:
            sl = syn.lower()
            if sl in needle or needle in sl:
                if best is None or len(sl) > best[0]:
                    best = (len(sl), slug)
    return best[1] if best else None


def _master_enabled(hass: HomeAssistant) -> bool:
    s = hass.states.get("input_boolean.living_lights_enabled")
    return s is not None and s.state == "on"


def _zone_enabled(hass: HomeAssistant, zone: str) -> bool:
    s = hass.states.get(f"input_boolean.living_lights_zone_{zone}_enabled")
    return s is not None and s.state == "on"


def _zone_occupied(hass: HomeAssistant, zone: str) -> bool:
    """True if the zone's occupancy sensor reads 'on'."""
    s = hass.states.get(f"binary_sensor.{zone}_person_occupancy")
    return s is not None and s.state == "on"


def _clamp_brightness(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return MIN_OVERRIDE_BRIGHTNESS_PCT
    return max(MIN_OVERRIDE_BRIGHTNESS_PCT, min(MAX_OVERRIDE_BRIGHTNESS_PCT, n))


def _clamp_kelvin(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 2700
    return max(MIN_OVERRIDE_KELVIN, min(MAX_OVERRIDE_KELVIN, n))


def _current_zone_state(hass: HomeAssistant, zone: str) -> dict[str, Any]:
    """Read current predicted lighting state for the zone (for delta-style
    'brighter' / 'warmer' inputs that need a baseline)."""
    s = hass.states.get(f"sensor.{_camera_for_zone(zone)}_{zone}_lighting_state")
    if s is None:
        return {"brightness_pct": 50, "color_temp_kelvin": 2700}
    attrs = s.attributes or {}
    return {
        "brightness_pct": int(attrs.get("predicted_brightness_pct", 50) or 50),
        "color_temp_kelvin": int(attrs.get("predicted_color_temp_kelvin", 2700) or 2700),
    }


def _camera_for_zone(zone: str) -> str:
    """Phase 0a zone → camera mapping. Expand as Phase 0c adds zones."""
    cam_map = {
        "dining_left": "dining_room",
        "dining_right": "dining_room",
        "sink": "kitchen",
        "island_left": "kitchen",
        "island_right": "kitchen",
    }
    return cam_map.get(zone, zone)


class LivingLightsFunction(Function):
    """Dispatcher for the living-lights tools (set_presence_override,
    clear_presence_override).

    Same `function: {type: 'living_lights', name: '...'}` config pattern
    as world_state.py.
    """

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
        if name == "set_presence_override":
            return await self._set_presence_override(hass, arguments)
        if name == "clear_presence_override":
            return await self._clear_presence_override(hass, arguments)
        if name == "get_recent_overrides":
            return await self._get_recent_overrides(hass, arguments)
        return {
            "ok": False,
            "error": {
                "kind": "unknown_function",
                "message": f"living_lights tool '{name}' not registered",
            },
        }

    async def _get_recent_overrides(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Return manual adjustments observed in the last <hours> hours.
        Used to answer 'why did the office dim earlier?' / 'show me recent
        manual overrides' from the agent.

        Reads from the world-state aggregator's 1-hour rolling buffer (in
        memory only). For longer windows the caller should fall back to
        /config/lighting_preferences_pending.jsonl on disk (V2).

        M20: bursts of trigger fires on the same light within ~10s are
        collapsed into one logical session record (final value wins)
        before being returned. The agent gets a clean "what the user
        did" view. Pass `collapse=False` in the tool args to see the
        raw per-trigger events (e.g. for counting trigger fires).
        """
        try:
            hours = float(args.get("hours") or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        zone = args.get("zone")
        if zone is not None:
            zone = str(zone).strip().lower() or None
        # collapse defaults True — the agent answering "what did the user
        # do" is always better off with sessions, not raw trigger fires.
        collapse_arg = args.get("collapse")
        if collapse_arg is None:
            collapse = True
        elif isinstance(collapse_arg, bool):
            collapse = collapse_arg
        else:
            collapse = str(collapse_arg).strip().lower() not in (
                "false", "0", "no", "off",
            )

        # Defensive lookup — handles HA startup race + missing integration
        # entry. The DOMAIN constant lives at the integration's package
        # root; importing here keeps the test-imports clean.
        try:
            from ..const import DOMAIN  # type: ignore[no-redef]
        except Exception:  # pylint: disable=broad-except
            DOMAIN = "extended_openai_conversation"
        domain_data = hass.data.get(DOMAIN, {})
        aggregator = domain_data.get("world_state")
        if aggregator is None:
            return {
                "ok": True,
                "data": [],
                "suggested_phrasing": "World state aggregator not ready yet.",
                "freshness": "none",
                "confidence_band": "low",
            }

        try:
            overrides = aggregator.recent_overrides(
                hours=hours, zone=zone, collapse=collapse,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("get_recent_overrides failed: %s", exc)
            overrides = []

        n = len(overrides)
        if n == 0:
            scope = f" in {zone.replace('_', ' ')}" if zone else ""
            phrasing = f"No manual adjustments{scope} in the last {hours:g} hour(s)."
            freshness = "none"
        else:
            scope = f" in {zone.replace('_', ' ')}" if zone else ""
            # M20: report sessions, not raw events. Mention burst count
            # only when meaningful (any session folded > 1 event).
            collapsed_sessions = sum(
                1 for r in overrides if r.get("session_collapsed")
            )
            unit = (
                "manual adjustment" if collapse else "override event"
            )
            phrasing = (
                f"There {'was' if n == 1 else 'were'} {n} {unit}"
                f"{'' if n == 1 else 's'}{scope} in the last {hours:g} hour"
                f"{'' if hours == 1 else 's'}."
            )
            if collapse and collapsed_sessions:
                phrasing += (
                    f" {collapsed_sessions} of those were burst sessions "
                    "(multiple trigger fires within ~10s folded into one)."
                )
            freshness = "fresh"

        return {
            "ok": True,
            "data": overrides,
            "count": n,
            "hours": hours,
            "zone": zone,
            "collapse": collapse,
            "suggested_phrasing": phrasing,
            "confidence_band": "high",
            "freshness": freshness,
        }

    async def _set_presence_override(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        # 1. Validate source (AR33-7)
        source = (args.get("source") or "").strip().lower()
        if source not in VALID_SOURCES:
            return {
                "ok": False,
                "error": {
                    "kind": "invalid_source",
                    "message": f"source must be one of {sorted(VALID_SOURCES)}; "
                               f"got '{source}'",
                },
            }

        # 2. Resolve zone
        zone_raw = args.get("zone", "")
        zone = _resolve_zone(zone_raw)
        if zone is None:
            return {
                "ok": False,
                "error": {
                    "kind": "unknown_zone",
                    "message": f"zone '{zone_raw}' not recognized",
                    "known_zones": sorted(ZONE_SYNONYMS.keys()),
                },
            }

        # 3. Master + per-zone toggles
        if not _master_enabled(hass):
            return {
                "ok": False,
                "error": {
                    "kind": "master_disabled",
                    "message": "input_boolean.living_lights_enabled is OFF; "
                               "override will not take effect",
                },
                "suggested_phrasing": "The living-lights system is off; nothing to override.",
            }
        if not _zone_enabled(hass, zone):
            return {
                "ok": False,
                "error": {
                    "kind": "zone_disabled",
                    "message": f"input_boolean.living_lights_zone_{zone}_enabled is OFF",
                },
            }

        # 4. Build payload from absolute OR delta inputs
        baseline = _current_zone_state(hass, zone)
        bri_abs = args.get("brightness_pct")
        bri_delta = args.get("brightness_delta_pct")
        ct_abs = args.get("color_temp_kelvin")
        ct_delta = args.get("color_temp_delta_kelvin")

        if bri_abs is not None:
            brightness_pct = _clamp_brightness(bri_abs)
        elif bri_delta is not None:
            try:
                brightness_pct = _clamp_brightness(baseline["brightness_pct"] + int(bri_delta))
            except (TypeError, ValueError):
                brightness_pct = baseline["brightness_pct"]
        else:
            brightness_pct = baseline["brightness_pct"]

        if ct_abs is not None:
            color_temp_kelvin = _clamp_kelvin(ct_abs)
        elif ct_delta is not None:
            try:
                color_temp_kelvin = _clamp_kelvin(baseline["color_temp_kelvin"] + int(ct_delta))
            except (TypeError, ValueError):
                color_temp_kelvin = baseline["color_temp_kelvin"]
        else:
            color_temp_kelvin = baseline["color_temp_kelvin"]

        # 5. Remote-set flag (AR33-2): if zone is vacant now, override
        #    has 4h max-lifetime instead of 30s vacancy grace
        remote = not _zone_occupied(hass, zone)

        # 6. Pin flag (AR33-3): persistent override; never auto-clears
        pinned = bool(args.get("pinned", False))

        payload = {
            "brightness_pct": brightness_pct,
            "color_temp_kelvin": color_temp_kelvin,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "prompt": (args.get("source_text") or "")[:160],
            "remote": remote,
            "pinned": pinned,
            "expires_after_vacant_ms": 30000,  # default; Phase 4.A makes per-zone tunable
        }

        # 7. Write to input_text helper
        entity_id = f"input_text.living_lights_override_text_{zone}"
        try:
            await hass.services.async_call(
                "input_text", "set_value",
                {"entity_id": entity_id, "value": json.dumps(payload)},
                blocking=True,
            )
        except Exception as e:
            _LOGGER.exception("living_lights: set_presence_override failed")
            return {
                "ok": False,
                "error": {
                    "kind": "service_call_failed",
                    "message": str(e)[:200],
                },
            }

        # 8. Build suggested_phrasing
        if pinned:
            phrasing = (f"OK — {zone.replace('_', ' ')} pinned to "
                        f"{brightness_pct}% at {color_temp_kelvin}K. "
                        f"Stays put until you tell me to clear it.")
        elif remote:
            phrasing = (f"OK — set {zone.replace('_', ' ')} to "
                        f"{brightness_pct}% at {color_temp_kelvin}K. "
                        f"Will reset 4 hours from now if you don't come in.")
        else:
            phrasing = (f"OK — {zone.replace('_', ' ')} to "
                        f"{brightness_pct}% at {color_temp_kelvin}K. "
                        f"Reverts when you leave.")

        return {
            "ok": True,
            "data": {
                "zone": zone,
                "payload": payload,
                "entity_id": entity_id,
            },
            "suggested_phrasing": phrasing,
            "confidence_band": "high",
            "freshness": "fresh",
        }

    async def _clear_presence_override(
        self, hass: HomeAssistant, args: dict[str, Any]
    ) -> dict[str, Any]:
        scope = (args.get("zone") or "all").strip().lower()
        cleared: list[str] = []
        if scope == "all":
            zones = list(ZONE_SYNONYMS.keys())
        else:
            z = _resolve_zone(scope)
            if z is None:
                return {
                    "ok": False,
                    "error": {
                        "kind": "unknown_zone",
                        "message": f"zone '{scope}' not recognized",
                    },
                }
            zones = [z]
        for zone in zones:
            entity_id = f"input_text.living_lights_override_text_{zone}"
            current = hass.states.get(entity_id)
            if current is None or current.state in ("", "unknown", "unavailable", None):
                continue
            try:
                await hass.services.async_call(
                    "input_text", "set_value",
                    {"entity_id": entity_id, "value": ""},
                    blocking=True,
                )
                cleared.append(zone)
            except Exception as e:
                _LOGGER.warning("living_lights: clear failed for %s: %s", zone, e)
        if not cleared:
            return {
                "ok": True,
                "data": {"cleared": []},
                "suggested_phrasing": "No active overrides to clear.",
                "confidence_band": "high",
                "freshness": "fresh",
            }
        return {
            "ok": True,
            "data": {"cleared": cleared},
            "suggested_phrasing": (
                f"Cleared {len(cleared)} override{'s' if len(cleared) > 1 else ''} "
                f"({', '.join(z.replace('_', ' ') for z in cleared)}). "
                f"Automation back in charge."
            ),
            "confidence_band": "high",
            "freshness": "fresh",
        }
