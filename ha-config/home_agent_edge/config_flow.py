"""Config flow for Home Agent Edge."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BLOCKED_ENTITY_IDS,
    CONF_BLOCKED_USER_IDS,
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_ENDPOINT,
    CONF_EDGE_TOKEN_PATH,
    CONF_ENTITY_IDS,
    CONF_SPOOL_KEY_PATH,
    CONF_SPOOL_MAX_AGE_SECONDS,
    CONF_SPOOL_MAX_BYTES,
    CONF_SPOOL_PATH,
    DEFAULT_ENTITY_IDS,
    DEFAULT_EDGE_TOKEN_PATH,
    DEFAULT_SPOOL_KEY_PATH,
    DEFAULT_SPOOL_MAX_AGE_SECONDS,
    DEFAULT_SPOOL_MAX_BYTES,
    DEFAULT_SPOOL_PATH,
    DOMAIN,
)


class HomeAgentEdgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one mTLS edge receiver."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = _validate_input(user_input)
            except ValueError as exc:
                errors["base"] = str(exc)
            else:
                await self.async_set_unique_id(normalized[CONF_ENDPOINT])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Home Agent Edge", data=normalized)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_ENDPOINT, default=values.get(CONF_ENDPOINT, "")): str,
            vol.Required(CONF_CLIENT_CERT, default=values.get(CONF_CLIENT_CERT, "/config/ssl/home_agent_edge.crt")): str,
            vol.Required(CONF_CLIENT_KEY, default=values.get(CONF_CLIENT_KEY, "/config/ssl/home_agent_edge.key")): str,
            vol.Required(CONF_CA_CERT, default=values.get(CONF_CA_CERT, "/config/ssl/home_agent_ca.crt")): str,
            vol.Required(CONF_EDGE_TOKEN_PATH, default=values.get(CONF_EDGE_TOKEN_PATH, DEFAULT_EDGE_TOKEN_PATH)): str,
            vol.Required(CONF_ENTITY_IDS, default=values.get(CONF_ENTITY_IDS, ",".join(DEFAULT_ENTITY_IDS))): str,
            vol.Optional(CONF_BLOCKED_ENTITY_IDS, default=values.get(CONF_BLOCKED_ENTITY_IDS, "")): str,
            vol.Optional(CONF_BLOCKED_USER_IDS, default=values.get(CONF_BLOCKED_USER_IDS, "")): str,
            vol.Required(CONF_SPOOL_PATH, default=values.get(CONF_SPOOL_PATH, DEFAULT_SPOOL_PATH)): str,
            vol.Required(CONF_SPOOL_KEY_PATH, default=values.get(CONF_SPOOL_KEY_PATH, DEFAULT_SPOOL_KEY_PATH)): str,
            vol.Required(CONF_SPOOL_MAX_BYTES, default=values.get(CONF_SPOOL_MAX_BYTES, DEFAULT_SPOOL_MAX_BYTES)): vol.All(int, vol.Range(min=64 * 1024)),
            vol.Required(CONF_SPOOL_MAX_AGE_SECONDS, default=values.get(CONF_SPOOL_MAX_AGE_SECONDS, DEFAULT_SPOOL_MAX_AGE_SECONDS)): vol.All(int, vol.Range(min=60, max=24 * 60 * 60)),
        }
    )


def _validate_input(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    endpoint = str(result[CONF_ENDPOINT]).strip()
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or parsed.path != "/v1/ingest/envelopes"
    ):
        raise ValueError("invalid_endpoint")
    result[CONF_ENDPOINT] = endpoint

    for key in (CONF_CLIENT_CERT, CONF_CLIENT_KEY, CONF_CA_CERT, CONF_EDGE_TOKEN_PATH):
        path = Path(str(result[key]))
        if not path.is_absolute() or not path.is_file():
            raise ValueError("certificate_file_not_found")
        result[key] = str(path)

    entity_ids = _split_csv(result.get(CONF_ENTITY_IDS))
    if not entity_ids or any("." not in entity_id for entity_id in entity_ids):
        raise ValueError("invalid_entity_allowlist")
    result[CONF_ENTITY_IDS] = entity_ids
    result[CONF_BLOCKED_ENTITY_IDS] = _split_csv(result.get(CONF_BLOCKED_ENTITY_IDS))
    result[CONF_BLOCKED_USER_IDS] = _split_csv(result.get(CONF_BLOCKED_USER_IDS))
    # Older entries may contain the removed option.  Force it off rather than
    # allowing a stale value to re-enable ordinary utterance persistence.
    result["include_conversation_text"] = False
    spool_path = Path(str(result[CONF_SPOOL_PATH]))
    spool_key_path = Path(str(result[CONF_SPOOL_KEY_PATH]))
    backed_up_roots = (Path("/config"), Path("/backup"), Path("/share"))
    if (
        not spool_path.is_absolute()
        or not spool_key_path.is_absolute()
        or spool_path == spool_key_path
    ):
        raise ValueError("invalid_spool_path")
    if any(spool_path == root or root in spool_path.parents for root in backed_up_roots):
        raise ValueError("spool_path_must_be_backup_excluded")
    result[CONF_SPOOL_PATH] = str(spool_path)
    result[CONF_SPOOL_KEY_PATH] = str(spool_key_path)
    return result


def _split_csv(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value or []
    return sorted({str(item).strip().lower() for item in values if str(item).strip()})
