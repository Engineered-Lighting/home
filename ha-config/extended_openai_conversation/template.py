"""Template functions for Extended OpenAI Conversation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import TemplateEnvironment

from .const import DEFAULT_WORKING_DIRECTORY, DOMAIN
from .helpers import get_exposed_entities
from .room_binding import resolve as resolve_bound_room
from .skills import SkillManager

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

DATA_TEMPLATE_MANAGER = "template_manager"

TEMPLATE_EXTENDED_OPENAI = "extended_openai"
TEMPLATE_GET_ENTITIES = "exposed_entities"
TEMPLATE_WORKING_DIRECTORY = "working_directory"
TEMPLATE_SKILL_DIR = "skill_dir"
TEMPLATE_CURRENT_ROOM_CONTEXT = "current_room_context"


async def async_setup_templates(hass: HomeAssistant) -> bool:
    """Set up template functions for Extended OpenAI Conversation."""
    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get(DATA_TEMPLATE_MANAGER):
        return True

    manager = ExtendedOpenAITemplateManager(hass)
    hass.data[DOMAIN][DATA_TEMPLATE_MANAGER] = manager
    await manager.async_setup()
    return True


async def async_unload_templates(hass: HomeAssistant) -> bool:
    """Unload template functions for Extended OpenAI Conversation."""
    if len(hass.config_entries.async_entries(DOMAIN)) == 1:
        manager = hass.data.get(DOMAIN, {}).get(DATA_TEMPLATE_MANAGER)
        if manager:
            await manager.async_on_unload()
            hass.data[DOMAIN].pop(DATA_TEMPLATE_MANAGER, None)
    return True


class ExtendedOpenAITemplateManager:
    """Class to manage template functions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the template manager."""
        self.hass = hass
        self._extended_openai = {
            TEMPLATE_GET_ENTITIES: self._get_exposed_entities,
            TEMPLATE_WORKING_DIRECTORY: self._get_working_directory,
            TEMPLATE_SKILL_DIR: self._get_skill_dir,
            TEMPLATE_CURRENT_ROOM_CONTEXT: self._current_room_context,
        }
        self._original_init = None

    def _get_exposed_entities(self) -> list[dict[str, Any]]:
        return get_exposed_entities(self.hass)

    def _current_room_context(self, device_id: str | None = None) -> str:
        """A-5 (Addendum 27): per-room context preamble. Returns a
        compact human-readable summary of the room currently bound to
        `device_id`, suitable for inline interpolation in the prompt
        template:

          {{ extended_openai.current_room_context(device_id) }}

        Returns "" when:
          - `device_id` is None or empty (no per-device call site)
          - device has no live room binding (TTL expired or never set)
          - world_state aggregator is disabled or empty for that room

        Otherwise returns a 2-5 line summary like:

          Current room context (kitchen):
          - Marcelo (high confidence, seen 12s ago)
          - perception: Marcelo standing at the island, mug in hand.

        Designed for compactness — the goal is to let the agent SKIP
        a get_room_state tool call on the first turn of a room-bound
        conversation, NOT to dump every world_state field. If the
        prompt wants raw data, it can call get_room_state directly
        from the agent's tool loop.
        """
        if not device_id:
            return ""
        try:
            room = resolve_bound_room(device_id)
        except Exception:
            return ""
        if not room:
            return ""
        try:
            agg = self.hass.data.get(DOMAIN, {}).get("world_state")
            if agg is None:
                return ""
            result = agg.get_room_state(room)
        except Exception as e:
            _LOGGER.debug("current_room_context: get_room_state(%s) failed: %s", room, e)
            return ""
        if not result or result.get("error"):
            return ""
        data = result.get("data")
        if not data:
            return f"Current room context ({room}): no occupancy data yet."

        lines = [f"Current room context ({room}):"]
        # Identified persons (high/medium confidence only — low conf
        # is noise the agent will mishedge on).
        persons = data.get("persons") or []
        for p in persons:
            ident = p.get("identity") or {}
            name = ident.get("name")
            if not name:
                continue
            conf_band = ident.get("confidence_band") or "unknown"
            if conf_band == "low":
                continue
            age = p.get("age_seconds")
            age_str = f"seen {int(age)}s ago" if isinstance(age, (int, float)) else "seen recently"
            lines.append(f"- {name} ({conf_band} confidence, {age_str})")
        # Generic occupancy when no identified person made it through
        if len(lines) == 1 and data.get("occupied"):
            lines.append("- occupied (no identified person)")
        # Perception caption — useful even without an identified person
        perception = data.get("perception_summary")
        if perception:
            p_age = data.get("perception_age_seconds")
            age_suffix = f" ({int(p_age)}s ago)" if isinstance(p_age, (int, float)) else ""
            lines.append(f"- perception: {perception}{age_suffix}")
        # If neither persons nor perception nor occupied → say so explicitly
        if len(lines) == 1:
            lines.append("- empty / no recent activity")
        return "\n".join(lines)

    def _get_working_directory(self) -> str:
        """Get the absolute working directory path."""
        working_dir = DEFAULT_WORKING_DIRECTORY
        if Path(working_dir).is_absolute():
            return str(Path(working_dir))
        return str(Path(self.hass.config.config_dir) / working_dir)

    def _get_skill_dir(self, name: str) -> str:
        """Get the absolute directory path for a skill by name.

        Args:
            name: The skill name (e.g., 'crypto', 'skill-creator')

        Returns:
            Absolute path to the skill directory

        Raises:
            ValueError: If the skill is not found
        """
        manager = SkillManager._instance
        if manager is None:
            raise ValueError("SkillManager not initialized")
        skill = manager.get_skill(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        return str(skill.path.parent)

    async def async_setup(self) -> None:
        """Set up the template functions."""
        _LOGGER.debug("Setting up Extended OpenAI Conversation template functions")

        # Register in existing environments
        if "template.environment" in self.hass.data:
            self.hass.data["template.environment"].globals[TEMPLATE_EXTENDED_OPENAI] = (
                self._extended_openai
            )

        # Patch TemplateEnvironment
        self._original_init = TemplateEnvironment.__init__  # type: ignore[assignment]

        def template_environment_init(
            template_env_self: TemplateEnvironment,
            hass: HomeAssistant | None,
            limited: bool | None = False,
            strict: bool | None = False,
            log_fn: Callable[[int, str], None] | None = None,
        ) -> None:
            if self._original_init:
                self._original_init(template_env_self, hass, limited, strict, log_fn)  # type: ignore[unreachable]
            if hass:
                template_env_self.globals[TEMPLATE_EXTENDED_OPENAI] = (
                    self._extended_openai
                )

        TemplateEnvironment.__init__ = template_environment_init  # type: ignore[method-assign,assignment]

    async def async_on_unload(self) -> None:
        """Tear down the template functions."""
        _LOGGER.debug("Tearing down Extended OpenAI Conversation template functions")

        if self._original_init:
            TemplateEnvironment.__init__ = self._original_init  # type: ignore[unreachable]
            self._original_init = None

        if "template.environment" in self.hass.data:
            self.hass.data["template.environment"].globals.pop(
                TEMPLATE_EXTENDED_OPENAI, None
            )
