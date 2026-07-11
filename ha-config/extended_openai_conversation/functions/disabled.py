"""Fail-closed implementation for model action-capable function types."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import Function


class DisabledModelActionFunction(Function):
    """A second runtime boundary after model-tool configuration filtering."""

    def __init__(self) -> None:
        super().__init__(vol.Schema({}, extra=vol.ALLOW_EXTRA))

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "success": False,
            "ok": False,
            "capability_disabled": True,
            "error_kind": "model_action_disabled",
            "error_message": (
                "Model-originated actions are disabled until the external "
                "safety kernel is available."
            ),
        }
