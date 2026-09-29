"""HTTP surface for the writable profile store.

Registered once at integration setup, independently of any config entry, so the
People tab can read and write profile material even while the legacy identity
store is frozen and its own views refuse to serve.

Every route requires Home Assistant authentication. None of them can create or
relate a person -- that is the agent authority's job, reached over its own
channel. These only decorate people who already exist there.
"""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

from . import agent_profiles

LOGGER = logging.getLogger(__name__)


class AgentProfilesView(HomeAssistantView):
    """All profiles, keyed by the agent authority's person_id."""

    url = "/api/extended_openai_conversation/agent_profiles"
    name = "api:extended_openai_conversation:agent_profiles"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        try:
            profiles = await hass.async_add_executor_job(agent_profiles.list_profiles)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("agent profile read failed")
            return self.json({"error": str(error)}, status_code=500)
        # Frigate is independent of the frozen identity store, so the face
        # library stays usable even though that store cannot serve. The tab
        # only learns whether it is configured: every Frigate image and listing
        # reaches the browser through the typed, authenticated frigate_proxy
        # routes, so Frigate's URL and origin are never sent to it.
        frigate_configured = False
        try:
            from .frigate_sync import base_url as _frigate_base_url
            frigate_configured = bool(_frigate_base_url())
        except Exception:  # noqa: BLE001 - decoration, never fail the read
            LOGGER.debug("frigate base url unavailable", exc_info=True)
        return self.json({
            "profiles": profiles,
            "frigate_faces_available": frigate_configured,
        })


class AgentProfileView(HomeAssistantView):
    """Update one person's descriptive fields."""

    url = "/api/extended_openai_conversation/agent_profile/{person_id}"
    name = "api:extended_openai_conversation:agent_profile"
    requires_auth = True

    async def post(self, request: web.Request, person_id: str) -> web.Response:
        hass = request.app["hass"]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return self.json({"error": "invalid JSON"}, status_code=400)
        display_name = str(body.get("display_name") or "")
        try:
            saved = await hass.async_add_executor_job(
                agent_profiles.upsert_profile, person_id, display_name, body
            )
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("agent profile write failed")
            return self.json({"error": str(error)}, status_code=500)
        return self.json({"ok": True, "profile": saved})


class AgentProfileAvatarView(HomeAssistantView):
    """Read, set or clear one person's headshot."""

    url = "/api/extended_openai_conversation/agent_profile/{person_id}/avatar"
    name = "api:extended_openai_conversation:agent_profile_avatar"
    requires_auth = True

    async def get(self, request: web.Request, person_id: str) -> web.Response:
        hass = request.app["hass"]
        found = await hass.async_add_executor_job(agent_profiles.read_avatar, person_id)
        if not found:
            return web.Response(status=404)
        data, mime = found
        # Private and revalidated: a replaced headshot must not linger in a
        # shared cache, and these are pictures of the household.
        return web.Response(
            body=data,
            content_type=mime,
            headers={"Cache-Control": "private, no-cache"},
        )

    async def post(self, request: web.Request, person_id: str) -> web.Response:
        hass = request.app["hass"]
        data = await request.read()
        try:
            await hass.async_add_executor_job(
                agent_profiles.save_avatar, person_id, data
            )
        except ValueError as error:
            return self.json({"error": str(error)}, status_code=400)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("agent avatar write failed")
            return self.json({"error": str(error)}, status_code=500)
        return self.json({"ok": True})

    async def delete(self, request: web.Request, person_id: str) -> web.Response:
        hass = request.app["hass"]
        removed = await hass.async_add_executor_job(
            agent_profiles.delete_avatar, person_id
        )
        return self.json({"ok": True, "removed": removed})


def register(hass) -> None:
    hass.http.register_view(AgentProfilesView())
    hass.http.register_view(AgentProfileView())
    hass.http.register_view(AgentProfileAvatarView())
