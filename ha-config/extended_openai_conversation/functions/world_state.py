"""World-state function tools.

Five tools the LLM agent can call to answer identity/presence/perception
questions without guessing from raw entity CSVs:

  - get_all_rooms_state      → compact overview of every room
  - get_room_state(room)     → one room's persons + perception + freshness
  - find_person(name)        → locate a person across cameras + HA presence
  - who_is_in(room)          → identified + generic persons in a room
  - refresh_perception(room) → synchronously trigger vision-sidecar /describe

All read tools return a dict with this shape:

  {
    "data": { ...structured world state slice... },
    "suggested_phrasing": "I see Marcelo in the kitchen.",
    "confidence_band": "high" | "medium" | "low" | "unknown",
    "freshness": "fresh" | "recent" | "stale" | "none"
  }

The `suggested_phrasing` field lets the model use the correctly-hedged
sentence directly rather than re-deriving the rule every turn (belt and
suspenders against prompt drift).

See `world_state.py` for the aggregator + `~/.claude/plans/keen-doodling-parasol.md`
Addendum 4 for the full design.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..const import (
    DOMAIN,
    REFRESH_PERCEPTION_MAX_PER_TURN,
    REFRESH_PERCEPTION_TIMEOUT_S,
    VISION_SIDECAR_URL,
)
from ..exceptions import NativeNotFound
from .base import Function

_LOGGER = logging.getLogger(__name__)

# Per-turn refresh budget tracker. Keyed by conversation_id; HA contexts
# don't carry a turn counter directly, so we approximate "this turn" with
# the conversation_id + a TTL-style reset.
# In practice each turn's tool calls happen synchronously within a few
# seconds; using conversation_id as the bucket key with a soft reset on
# every new ingest is good enough. Stored in hass.data[DOMAIN].
_REFRESH_BUDGET_KEY = "world_state_refresh_budget"
_GROUNDED_LOOK_BUDGET_KEY = "world_state_grounded_look_budget"
_GROUNDED_LOOK_MAX_PER_TURN = 3
_VISION_CAPTURE_OVERRIDE = "input_boolean.living_lights_vision_capture_override"
_TRAVEL_MODE_ENTITY = "input_boolean.living_lights_travel_mode"
_OUTDOOR_ROOMS = {"driveway", "outside", "front_yard", "back_yard"}
_ROOM_LIGHTS: dict[str, list[str]] = {
    "living_room": [
        "light.living_room_lights",
        "light.front_left",
        "light.front_right",
        "light.rear_left",
        "light.rear_right",
        "light.ambient_light_left_mss110_main_channel",
        "light.ambient_light_right_mss110_main_channel",
        "switch.ambient_light_left_mss110_main_channel",
        "switch.ambient_light_right_mss110_main_channel",
    ],
    "kitchen": [
        "light.kitchen_floodlight_timed",
        "light.island_left",
        "light.island_right",
        "light.sink",
        "light.sink_light",
        "light.sink_light_2",
    ],
    "dining_room": [
        "light.dining_table_left",
        "light.dining_table_right",
        "light.dining_light",
        "light.dining_light_2",
        "light.dining_room_floodlight_timed",
    ],
    "workshop": [
        "light.office",
        "switch.workshop_light_left_mss110_main_channel",
        "switch.workshop_light_right_mss110_main_channel",
    ],
}
_LOW_QUALITY_TERMS = (
    "too dark",
    "dark",
    "low light",
    "poor image quality",
    "can't make out",
    "cannot make out",
    "can't tell",
    "cannot tell",
    "hard to tell",
    "unable to determine",
    "unclear",
    "blurry",
    "not enough detail",
)


class WorldStateFunction(Function):
    """Dispatcher for the 5 world-state tools.

    All five tools share the same `function: {type: "world_state", name: "..."}`
    config block; the `name` field selects which method runs."""

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
        if name == "get_all_rooms_state":
            return self._get_all_rooms_state(hass)
        if name == "get_room_state":
            return self._get_room_state(hass, arguments)
        if name == "find_person":
            return self._find_person(hass, arguments)
        if name == "who_is_in":
            return self._who_is_in(hass, arguments)
        if name == "refresh_perception":
            return await self._refresh_perception(hass, arguments, llm_context)
        if name == "grounded_look":
            return await self._grounded_look(hass, arguments, llm_context)
        raise NativeNotFound(name)

    # ─── read tools ─────────────────────────────────────────────────
    def _aggregator(self, hass: HomeAssistant):
        agg = hass.data.get(DOMAIN, {}).get("world_state")
        return agg

    def _get_all_rooms_state(self, hass: HomeAssistant) -> dict[str, Any]:
        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        state = agg.get_world_state()
        if not state.get("enabled", True):
            return {"error": "world state disabled"}
        # Build a compact summary suitable for prompt context — full
        # dump may be too verbose for repeated tool-call rounds.
        rooms_summary = {}
        for room_name, r in state["rooms"].items():
            identified = []
            for p in r.get("persons", []):
                ident = p.get("identity") or {}
                if ident.get("name"):
                    identified.append({
                        "name": ident["name"],
                        "confidence_band": ident.get("confidence_band"),
                        "age_seconds": p.get("age_seconds"),
                    })
            rooms_summary[room_name] = {
                "occupied": r.get("occupied", False),
                "identified_persons": identified,
                "perception_summary": r.get("perception_summary"),
                "perception_age_seconds": r.get("perception_age_seconds"),
            }
        return {
            "data": {
                "rooms": rooms_summary,
                "people": {
                    name: {
                        "currently_seen": p.get("currently_seen"),
                        "last_visual_room": p.get("last_visual_room"),
                        "ha_location": p.get("ha_location"),
                    }
                    for name, p in state["people"].items()
                },
                "system": state["system"],
                "updated_at": state["updated_at"],
            },
            "suggested_phrasing": self._phrase_overview(rooms_summary),
        }

    def _get_room_state(
        self, hass: HomeAssistant, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        room = (arguments.get("room") or "").strip()
        if not room:
            return {"error": "room argument required"}
        return agg.get_room_state(room)

    def _find_person(
        self, hass: HomeAssistant, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        name = (arguments.get("name") or "").strip()
        if not name:
            return {"error": "name argument required"}
        return agg.find_person(name)

    def _who_is_in(
        self, hass: HomeAssistant, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        room = (arguments.get("room") or "").strip()
        if not room:
            return {"error": "room argument required"}
        return agg.who_is_in(room)

    # ─── refresh tool (rate-limited + async) ────────────────────────
    async def _refresh_perception(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
    ) -> dict[str, Any]:
        room = (arguments.get("room") or "").strip()
        if not room:
            return {"error": "room argument required"}

        # Per-turn budget. Keyed by conversation_id when available.
        conv_id = (
            getattr(llm_context, "conversation_id", None) if llm_context else None
        ) or "no_conv"
        budget = hass.data.setdefault(DOMAIN, {}).setdefault(
            _REFRESH_BUDGET_KEY, {}
        )
        used = budget.get(conv_id, 0)
        if used >= REFRESH_PERCEPTION_MAX_PER_TURN:
            return {
                "error": (
                    f"refresh budget exhausted "
                    f"({REFRESH_PERCEPTION_MAX_PER_TURN} per conversation); "
                    f"using cached state from get_room_state instead"
                ),
            }
        budget[conv_id] = used + 1

        # Resolve room → primary camera entity_id.
        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        room_key = agg._resolve_room(room) or room  # noqa: SLF001
        # Pick the first known camera; if none, fall back to camera.<room_key>.
        room_data = agg._rooms.get(room_key, {})  # noqa: SLF001
        cameras = room_data.get("cameras") or [f"camera.{room_key}"]
        camera_entity = cameras[0]

        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not available; refresh_perception disabled"}

        url = f"{VISION_SIDECAR_URL.rstrip('/')}/describe"
        try:
            timeout = aiohttp.ClientTimeout(total=REFRESH_PERCEPTION_TIMEOUT_S)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={"camera": room_key, "entity_id": camera_entity},
                ) as resp:
                    if resp.status != 200:
                        return {
                            "error": f"vision-sidecar http {resp.status}",
                        }
                    body = await resp.json()
        except asyncio.TimeoutError:
            return {"error": "vision-sidecar timeout"}
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning(
                "refresh_perception: vision-sidecar call failed: %s", e
            )
            return {"error": f"vision-sidecar unreachable: {type(e).__name__}"}

        description = (body or {}).get("description") or ""
        if description:
            agg.record_perception(room_key, description)
        return {
            "data": {
                "room": room_key,
                "camera": camera_entity,
                "description": description,
                "latency_ms": (body or {}).get("latency_ms"),
                "model": (body or {}).get("model"),
            },
            "suggested_phrasing": (
                f"Looking at the {room_key} now: {description}"
                if description
                else f"I couldn't get a fresh look at the {room_key} right now."
            ),
            "confidence_band": "unknown",
            "freshness": "fresh",
        }

    # ─── overview phrasing ──────────────────────────────────────────
    async def _grounded_look(
        self,
        hass: HomeAssistant,
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
    ) -> dict[str, Any]:
        room = (arguments.get("room") or "").strip()
        question = (arguments.get("question") or "").strip()
        allow_illumination = bool(arguments.get("allow_illumination", True))
        if not room:
            return {"error": "room argument required"}
        if not question:
            question = f"What do you see in the {room}?"

        conv_id = (
            getattr(llm_context, "conversation_id", None) if llm_context else None
        ) or "no_conv"
        budget = hass.data.setdefault(DOMAIN, {}).setdefault(
            _GROUNDED_LOOK_BUDGET_KEY, {}
        )
        used = budget.get(conv_id, 0)
        if used >= _GROUNDED_LOOK_MAX_PER_TURN:
            return {
                "error": (
                    f"grounded look budget exhausted "
                    f"({_GROUNDED_LOOK_MAX_PER_TURN} per conversation)"
                )
            }
        budget[conv_id] = used + 1

        agg = self._aggregator(hass)
        if agg is None:
            return {"error": "world state aggregator not initialized"}
        room_key = agg._resolve_room(room) or room  # noqa: SLF001
        room_data = agg._rooms.get(room_key, {})  # noqa: SLF001
        cameras = room_data.get("cameras") or [f"camera.{room_key}"]
        camera_entity = cameras[0]

        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not available; grounded_look disabled"}

        first = await self._reason_zoom(room_key, camera_entity, question, aiohttp)
        if first.get("error"):
            return first

        illumination = {
            "used": False,
            "eligible": False,
            "skipped_reason": None,
            "restored": None,
        }
        final = first
        if allow_illumination and self._needs_illumination(first):
            safe = self._room_safe_for_illumination(hass, agg, room_key)
            illumination.update(safe)
            if safe.get("eligible"):
                restore_snapshot = self._capture_light_states(hass, room_key)
                try:
                    await self._enable_capture_lights(hass, restore_snapshot)
                    illumination["used"] = True
                    await asyncio.sleep(1.5)
                    retry = await self._reason_zoom(
                        room_key,
                        camera_entity,
                        question,
                        aiohttp,
                        illuminated=True,
                    )
                    if not retry.get("error"):
                        final = retry
                    else:
                        illumination["retry_error"] = retry.get("error")
                finally:
                    illumination["restored"] = await self._restore_light_states(
                        hass, restore_snapshot
                    )

        answer = (final.get("data") or {}).get("answer") or ""
        if answer:
            try:
                agg.record_perception(room_key, answer)
            except Exception:  # noqa: BLE001
                pass
        suggested = answer or f"I couldn't get a grounded look at the {room_key}."
        if illumination.get("used"):
            suggested = (
                f"I briefly turned on the {room_key.replace('_', ' ')} lights "
                f"for a clearer snapshot and restored them. {suggested}"
            )
        elif illumination.get("skipped_reason"):
            suggested = (
                f"{suggested} I did not turn on lights because "
                f"{illumination['skipped_reason']}."
            )
        return {
            "data": {
                **(final.get("data") or {}),
                "room": room_key,
                "camera": camera_entity,
                "initial": first.get("data"),
                "illumination": illumination,
            },
            "suggested_phrasing": suggested,
            "confidence_band": "unknown",
            "freshness": "fresh",
        }

    async def _reason_zoom(
        self,
        room_key: str,
        camera_entity: str,
        question: str,
        aiohttp: Any,
        illuminated: bool = False,
    ) -> dict[str, Any]:
        url = f"{VISION_SIDECAR_URL.rstrip('/')}/reason_zoom"
        prompt = (
            f"{question}\n"
            "Use grounded visual reasoning. Zoom or crop into the relevant "
            "area if needed. Return segmentation, bounding boxes, annotated "
            "images, and uncertainty when available."
        )
        try:
            timeout = aiohttp.ClientTimeout(total=REFRESH_PERCEPTION_TIMEOUT_S + 6)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={
                        "camera": room_key,
                        "entity_id": camera_entity,
                        "question": prompt,
                    },
                ) as resp:
                    if resp.status != 200:
                        return {"error": f"vision-sidecar http {resp.status}"}
                    body = await resp.json()
        except asyncio.TimeoutError:
            return {"error": "vision-sidecar timeout"}
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("grounded_look: reason_zoom failed: %s", e)
            return {"error": f"vision-sidecar unreachable: {type(e).__name__}"}
        answer = (body or {}).get("answer") or (body or {}).get("description") or ""
        return {
            "data": {
                "room": room_key,
                "camera": camera_entity,
                "answer": answer,
                "overview_url": (body or {}).get("overview_url"),
                "detail_url": (body or {}).get("detail_url"),
                "annotated_url": (body or {}).get("annotated_url"),
                "primitives": (body or {}).get("primitives") or (body or {}).get("boxes"),
                "latency_ms": (body or {}).get("latency_ms"),
                "model": (body or {}).get("model"),
                "illuminated": illuminated,
            }
        }

    def _needs_illumination(self, result: dict[str, Any]) -> bool:
        answer = ((result.get("data") or {}).get("answer") or "").lower()
        return any(term in answer for term in _LOW_QUALITY_TERMS)

    def _room_safe_for_illumination(
        self,
        hass: HomeAssistant,
        agg: Any,
        room_key: str,
    ) -> dict[str, Any]:
        if room_key in _OUTDOOR_ROOMS:
            return {"eligible": False, "skipped_reason": "that camera is not an indoor room"}
        if room_key not in _ROOM_LIGHTS:
            return {"eligible": False, "skipped_reason": "that room has no mapped capture lights"}
        room = agg._render_room(room_key, time.time())  # noqa: SLF001
        if not room.get("cameras"):
            return {"eligible": False, "skipped_reason": "that room has no mapped camera"}
        if room.get("occupied") or room.get("persons"):
            return {"eligible": False, "skipped_reason": "someone may be in that room"}
        return {"eligible": True, "skipped_reason": None}

    def _capture_light_states(
        self,
        hass: HomeAssistant,
        room_key: str,
    ) -> dict[str, Any]:
        snapshot = {
            "room": room_key,
            "entities": {},
            "override_was": self._state_value(hass, _VISION_CAPTURE_OVERRIDE),
            "travel_mode_was": self._state_value(hass, _TRAVEL_MODE_ENTITY),
        }
        for entity_id in _ROOM_LIGHTS.get(room_key, []):
            state = hass.states.get(entity_id)
            if not state:
                continue
            snapshot["entities"][entity_id] = {
                "state": getattr(state, "state", None),
                "attributes": dict(getattr(state, "attributes", {}) or {}),
            }
        return snapshot

    def _state_value(self, hass: HomeAssistant, entity_id: str) -> str | None:
        state = hass.states.get(entity_id)
        return getattr(state, "state", None) if state else None

    async def _enable_capture_lights(
        self,
        hass: HomeAssistant,
        snapshot: dict[str, Any],
    ) -> None:
        await hass.services.async_call(
            "input_boolean",
            "turn_on",
            {"entity_id": _VISION_CAPTURE_OVERRIDE},
        )
        lights = [e for e in snapshot.get("entities", {}) if e.startswith("light.")]
        switches = [e for e in snapshot.get("entities", {}) if e.startswith("switch.")]
        if lights:
            await hass.services.async_call(
                "light",
                "turn_on",
                {
                    "entity_id": lights,
                    "brightness_pct": 75,
                    "color_temp_kelvin": 3500,
                    "transition": 0.2,
                },
            )
        if switches:
            await hass.services.async_call("switch", "turn_on", {"entity_id": switches})

    async def _restore_light_states(
        self,
        hass: HomeAssistant,
        snapshot: dict[str, Any],
    ) -> bool:
        ok = True
        for entity_id, state in (snapshot.get("entities") or {}).items():
            try:
                domain = entity_id.split(".", 1)[0]
                was_on = state.get("state") == "on"
                attrs = state.get("attributes") or {}
                if was_on and domain == "light":
                    data = {"entity_id": [entity_id], "transition": 0.2}
                    for key in ("brightness", "color_temp_kelvin", "effect"):
                        if attrs.get(key) is not None:
                            data[key] = attrs[key]
                    await hass.services.async_call("light", "turn_on", data)
                elif was_on:
                    await hass.services.async_call(domain, "turn_on", {"entity_id": [entity_id]})
                else:
                    await hass.services.async_call(domain, "turn_off", {"entity_id": [entity_id]})
            except Exception:  # noqa: BLE001
                ok = False
        try:
            override_service = "turn_on" if snapshot.get("override_was") == "on" else "turn_off"
            await hass.services.async_call(
                "input_boolean",
                override_service,
                {"entity_id": _VISION_CAPTURE_OVERRIDE},
            )
        except Exception:  # noqa: BLE001
            ok = False
        return ok

    def _phrase_overview(self, rooms_summary: dict[str, Any]) -> str:
        """One-sentence summary of all rooms — used by get_all_rooms_state."""
        occupied = [r for r, d in rooms_summary.items() if d.get("occupied")]
        identified_by_room: dict[str, list[str]] = {}
        for r, d in rooms_summary.items():
            names = [p["name"] for p in d.get("identified_persons") or []]
            if names:
                identified_by_room[r] = names
        if not occupied and not identified_by_room:
            return "I don't currently see anyone in any room."
        parts = []
        for room, names in identified_by_room.items():
            if len(names) == 1:
                parts.append(f"{names[0]} in the {room}")
            else:
                parts.append(f"{', '.join(names[:-1])} and {names[-1]} in the {room}")
        unidentified_rooms = [r for r in occupied if r not in identified_by_room]
        if unidentified_rooms:
            parts.append(
                f"someone in the {', '.join(unidentified_rooms)}"
            )
        return "I see " + "; ".join(parts) + "."


def reset_refresh_budget(hass: HomeAssistant, conversation_id: str | None) -> None:
    """Reset the per-conversation refresh budget. Called by conversation.py
    at the start of each turn so a long-running conversation doesn't run
    out after a few turns."""
    budget = hass.data.setdefault(DOMAIN, {}).setdefault(_REFRESH_BUDGET_KEY, {})
    if conversation_id:
        budget.pop(conversation_id, None)
