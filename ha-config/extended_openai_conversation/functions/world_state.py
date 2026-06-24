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
