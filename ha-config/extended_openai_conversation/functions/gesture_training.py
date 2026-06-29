"""Gesture-training capture tools for the voice assistant."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .base import Function

GESTURE_CONTROL_URL = "http://192.168.0.100:8096"
VOICE_PE_TARGET = "assist_satellite.home_assistant_voice_0a92e0_assist_satellite"
CAPTURE_TIMEOUT_S = 8.0

ROOM_TO_CAMERA = {
    "kitchen": "kitchen",
    "dining": "dining_room",
    "dining room": "dining_room",
    "dining_room": "dining_room",
    "living": "living_room",
    "living room": "living_room",
    "living_room": "living_room",
}


class GestureTrainingFunction(Function):
    """Dispatcher for gesture-training capture tools."""

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
        if name == "start_guided_capture":
            return await self._start_guided_capture(hass, arguments)
        return {"error": f"unknown gesture_training tool: {name}"}

    async def _start_guided_capture(self, hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
        room = str(arguments.get("room") or arguments.get("camera_id") or "kitchen").strip().lower()
        camera_id = ROOM_TO_CAMERA.get(room)
        if not camera_id:
            return {
                "data": None,
                "suggested_phrasing": "I can start gesture training in the kitchen, dining room, or living room.",
            }

        script_key = str(arguments.get("script_key") or "segmented").strip().lower()
        if script_key not in {"segmented", "fluid"}:
            script_key = "segmented"

        from homeassistant.helpers.httpx_client import get_async_client

        client = get_async_client(hass)
        try:
            response = await client.post(
                f"{GESTURE_CONTROL_URL}/api/gesture-control/debug-sessions/start-guided",
                json={
                    "camera_id": camera_id,
                    "script_key": script_key,
                    "voice_preview_target_entity_id": VOICE_PE_TARGET,
                    "note": (
                        "Voice assistant requested guided gesture training capture. "
                        "No lights or automations are controlled."
                    ),
                },
                timeout=CAPTURE_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - voice response should name the failure class.
            return {
                "data": {"camera_id": camera_id, "script_key": script_key},
                "suggested_phrasing": f"I could not reach gesture control: {type(exc).__name__}.",
            }

        if response.status_code >= 400:
            return {
                "data": {
                    "camera_id": camera_id,
                    "script_key": script_key,
                    "status_code": response.status_code,
                    "body": response.text[:300],
                },
                "suggested_phrasing": "Gesture control rejected the capture request.",
            }

        payload = response.json()
        job = payload.get("job") or {}
        return {
            "data": {
                "camera_id": camera_id,
                "script_key": script_key,
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "voice_preview_target_entity_id": VOICE_PE_TARGET,
            },
            "suggested_phrasing": (
                f"Starting {camera_id.replace('_', ' ')} gesture training. "
                "Listen for the prompts from the voice assistant."
            ),
        }
