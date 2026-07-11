"""Home Agent Edge custom integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

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
    DATA_RUNTIME,
    DATA_VIEW_REGISTERED,
    DEFAULT_SPOOL_MAX_AGE_SECONDS,
    DEFAULT_SPOOL_MAX_BYTES,
    DELIVERY_BATCH_SIZE,
    DELIVERY_INTERVAL_SECONDS,
    DOMAIN,
    MAINTENANCE_INTERVAL_SECONDS,
    WHOAMI_NAME,
    WHOAMI_URL,
)
from .crypto import EncryptedEnvelopeCodec
from .model import EdgePolicy
from .outbox import EdgeOutbox
from .runtime import EdgeRuntime
from .transport import MutualTLSTransport

_LOGGER = logging.getLogger(__name__)


class HomeAgentWhoAmIView(HomeAssistantView):
    """Return the already-authenticated HA user without minting a token."""

    url = WHOAMI_URL
    name = WHOAMI_NAME
    requires_auth = True

    async def get(self, request: Any) -> Any:
        user = request.get("hass_user")
        if user is None:
            # HA's auth middleware should make this unreachable.  Fail closed
            # rather than accepting a caller-provided identity header.
            return self.json({"error": "authentication_required"}, status_code=401)
        return self.json(
            {
                "user_id": str(user.id),
                "is_admin": bool(getattr(user, "is_admin", False)),
                # A future HA user object without this authorization field is
                # not implicitly active. The BFF requires an explicit true.
                "is_active": bool(getattr(user, "is_active", False)),
            }
        )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the authenticated identity view once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(HomeAgentWhoAmIView())
        domain_data[DATA_VIEW_REGISTERED] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one durable edge stream."""
    data = {**entry.data, **entry.options}
    try:
        policy = EdgePolicy.from_values(
            data[CONF_ENTITY_IDS],
            data.get(CONF_BLOCKED_ENTITY_IDS, ()),
            data.get(CONF_BLOCKED_USER_IDS, ()),
            # Conversation content is not configurable in the MVP.  Only
            # content-free authenticated turn provenance crosses this edge.
            include_conversation_text=False,
            require_authenticated_conversation=True,
        )
        spool_path = Path(data[CONF_SPOOL_PATH])
        spool_key_path = Path(data[CONF_SPOOL_KEY_PATH])
        codec = await hass.async_add_executor_job(
            _load_spool_codec, spool_path, spool_key_path
        )
        outbox = EdgeOutbox(
            spool_path,
            codec,
            max_bytes=int(data.get(CONF_SPOOL_MAX_BYTES, DEFAULT_SPOOL_MAX_BYTES)),
            max_age_seconds=int(data.get(CONF_SPOOL_MAX_AGE_SECONDS, DEFAULT_SPOOL_MAX_AGE_SECONDS)),
        )
        transport = MutualTLSTransport(
            data[CONF_ENDPOINT],
            client_cert=data[CONF_CLIENT_CERT],
            client_key=data[CONF_CLIENT_KEY],
            ca_cert=data[CONF_CA_CERT],
            edge_token_path=data[CONF_EDGE_TOKEN_PATH],
        )
        runtime = EdgeRuntime(
            hass, policy, outbox, transport, batch_size=DELIVERY_BATCH_SIZE
        )
        await runtime.async_start()
    except Exception as exc:
        _LOGGER.error("Home Agent Edge setup failed closed: %s", type(exc).__name__)
        return False

    unsub_delivery = async_track_time_interval(
        hass, runtime.async_deliver_once, timedelta(seconds=DELIVERY_INTERVAL_SECONDS)
    )
    unsub_maintenance = async_track_time_interval(
        hass, runtime.async_maintain, timedelta(seconds=MAINTENANCE_INTERVAL_SECONDS)
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_RUNTIME: runtime,
        "unsubscribers": (unsub_delivery, unsub_maintenance),
    }
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop subscriptions and close the mTLS session."""
    stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if stored is None:
        return True
    for unsubscribe in stored.get("unsubscribers", ()):
        unsubscribe()
    await stored[DATA_RUNTIME].async_close()
    return True


def _load_spool_codec(
    spool_path: Path, spool_key_path: Path
) -> EncryptedEnvelopeCodec:
    if spool_path.exists() and not spool_key_path.exists():
        raise RuntimeError("encrypted spool exists but its key is unavailable")
    return EncryptedEnvelopeCodec.from_key_file(spool_key_path)
