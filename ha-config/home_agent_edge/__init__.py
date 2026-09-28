"""Home Agent Edge custom integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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
    DEFAULT_PRIVACY_RECEIPT_PATH,
    DELIVERY_BATCH_SIZE,
    DELIVERY_INTERVAL_SECONDS,
    DOMAIN,
    MAINTENANCE_INTERVAL_SECONDS,
    WHOAMI_NAME,
    WHOAMI_URL,
)
from .crypto import EncryptedEnvelopeCodec
from .lighting import (
    EXECUTE_URL,
    INVENTORY_URL,
    MAX_BODY,
    OUTCOME_URL,
    SIGNATURE_HEADER,
    LightingEndpoint,
    LightingLedger,
    LightingPolicy,
    LightingRejected,
)
from .model import EdgePolicy
from .outbox import EdgeOutbox
from .runtime import CorePrivacyPolicyUnavailable, EdgeRuntime
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


class HomeAgentLightingView(HomeAssistantView):
    """One signed lighting operation. Home Assistant auth is not used: the
    per-home lighting secret authenticates the caller and authorizes only
    allowlisted lights, so it is never an HA token."""

    requires_auth = False

    def __init__(self, url: str, operation: str, endpoint: LightingEndpoint) -> None:
        self.url = url
        self.name = f"api:home_agent_edge:lighting:{operation}"
        self._operation = getattr(endpoint, operation)

    async def post(self, request: Any) -> Any:
        if request.content_length is not None and request.content_length > MAX_BODY:
            return self.json({"error": "body_too_large"}, status_code=413)
        try:
            body = b""
            while len(body) <= MAX_BODY:
                chunk = await request.content.read(MAX_BODY + 1 - len(body))
                if not chunk:
                    break
                body += chunk
            result = await self._operation(body, request.headers.get(SIGNATURE_HEADER))
        except LightingRejected as rejected:
            return self.json({"error": rejected.code}, status_code=rejected.status)
        except Exception as exc:  # fail closed without echoing details
            _LOGGER.error("Home Agent lighting request failed: %s", type(exc).__name__)
            return self.json({"error": "lighting_unavailable"}, status_code=503)
        return self.json(result)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register the authenticated identity view once, and lighting if configured."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(HomeAgentWhoAmIView())
        domain_data[DATA_VIEW_REGISTERED] = True
    settings = config.get(DOMAIN)
    if isinstance(settings, dict) and "lighting" in settings and not domain_data.get("lighting_registered"):
        try:
            policy = LightingPolicy.from_config(settings["lighting"])
            ledger = await hass.async_add_executor_job(LightingLedger, policy.ledger_path)
        except Exception as exc:
            # A bad lighting block disables lighting only; identity keeps working.
            _LOGGER.error("Home Agent lighting disabled: %s", type(exc).__name__)
            return True
        endpoint = LightingEndpoint(
            policy,
            ledger,
            states=lambda entity_id: _light_state(hass, entity_id),
            call_service=lambda service, data: hass.services.async_call(
                "light", service, data, blocking=True
            ),
            run=hass.async_add_executor_job,
        )
        for url, operation in ((INVENTORY_URL, "inventory"), (EXECUTE_URL, "execute"), (OUTCOME_URL, "outcome")):
            hass.http.register_view(HomeAgentLightingView(url, operation, endpoint))
        domain_data["lighting_registered"] = True
    return True


def _light_state(hass: HomeAssistant, entity_id: str) -> dict[str, Any] | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    attributes = state.attributes
    return {
        "state": state.state,
        "brightness": attributes.get("brightness"),
        "name": attributes.get("friendly_name"),
        "supported_color_modes": list(attributes.get("supported_color_modes") or ()),
        # Group markers; the lighting module refuses groups.
        "entity_id": list(attributes.get("entity_id") or ()),
        "is_hue_group": bool(attributes.get("is_hue_group")),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one durable edge stream."""
    data = {**entry.data, **entry.options}
    runtime: EdgeRuntime | None = None
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
            max_age_seconds=int(
                data.get(CONF_SPOOL_MAX_AGE_SECONDS, DEFAULT_SPOOL_MAX_AGE_SECONDS)
            ),
        )
        transport = MutualTLSTransport(
            data[CONF_ENDPOINT],
            client_cert=data[CONF_CLIENT_CERT],
            client_key=data[CONF_CLIENT_KEY],
            ca_cert=data[CONF_CA_CERT],
            edge_token_path=data[CONF_EDGE_TOKEN_PATH],
        )
        runtime = EdgeRuntime(
            hass,
            policy,
            outbox,
            transport,
            batch_size=DELIVERY_BATCH_SIZE,
            privacy_receipt_path=Path(DEFAULT_PRIVACY_RECEIPT_PATH),
        )
        await runtime.async_start()
    except CorePrivacyPolicyUnavailable as exc:
        if runtime is not None:
            await runtime.async_close()
        # A receiver reboot or network outage is temporary. ConfigEntryNotReady
        # lets HA retry with bounded backoff and recover without a manual reload.
        raise ConfigEntryNotReady("Home Agent Core is temporarily unavailable") from exc
    except Exception as exc:
        if runtime is not None:
            await runtime.async_close()
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


def _load_spool_codec(spool_path: Path, spool_key_path: Path) -> EncryptedEnvelopeCodec:
    if spool_path.exists() and not spool_key_path.exists():
        raise RuntimeError("encrypted spool exists but its key is unavailable")
    return EncryptedEnvelopeCodec.from_key_file(spool_key_path)
