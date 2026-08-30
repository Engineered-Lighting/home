"""Read and write the household through the agent authority.

The legacy identity store was frozen by the E4 cutover: it refuses to serve, so
the People tab renders nothing. The same household lives in the agent's
authority, reachable over the mutual-TLS listener Home Assistant already uses
for telemetry.

Home Assistant authenticates with the client certificate it already holds. It
does NOT hold Core's service credential -- nginx injects that on the Ubuntu
host, because that token can act as any user against Core and must not sit on
this box. What Home Assistant asserts is the acting user, which it knows
first-hand.

Only four exact paths are reachable through that listener; everything else on it
returns 404. This module cannot widen that surface.
"""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

LOGGER = logging.getLogger(__name__)

EDGE_DOMAIN = "home_agent_edge"
INGEST_PATH = "/v1/ingest/envelopes"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

# The legacy vocabulary this view renders, keyed by the authority's predicates.
# An unmapped predicate keeps its own stem rather than being coerced into a
# neighbouring category: guessing here would misdescribe a relationship.
PREDICATE_TO_REL_TYPE = {
    "parent_of": "parent",
    "partner_of": "partner",
    "sibling_of": "sibling",
    "friend_of": "friend",
    "roommate_of": "roommate",
    "neighbor_of": "neighbor",
    "colleague_of": "colleague",
}

# What the legacy per-person relationship_type becomes, given the strongest
# edge recorded against that person. Anyone with no recorded edge stays
# "unknown"; the authority never asserted a category for them and neither
# should this.
REL_TYPE_TO_PERSON_TYPE = {
    "partner": "partner",
    "parent": "family_immediate",
    "sibling": "family_immediate",
    "friend": "friend",
    "roommate": "roommate",
    "neighbor": "neighbor",
    "colleague": "friend",
}

# The reverse direction: what the People tab's relationship_type means when a
# person is created through it.
PERSON_TYPE_TO_PREDICATE = {
    "partner": "partner_of",
    "family_immediate": "parent_of",
    "friend": "friend_of",
    "roommate": "roommate_of",
    "neighbor": "neighbor_of",
}


class AgentHouseholdUnavailable(RuntimeError):
    """The agent authority could not be reached or refused the request."""


def new_ceremony_id() -> str:
    """A UUIDv7 ceremony seed.

    Core's request models reject anything else, and the adapters derive every
    other identifier from this value's embedded timestamp.
    """

    import uuid

    raw = bytearray(uuid.uuid4().bytes)
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _edge_entry(hass: HomeAssistant):
    entries = hass.config_entries.async_entries(EDGE_DOMAIN)
    if not entries:
        raise AgentHouseholdUnavailable("home_agent_edge is not configured")
    return entries[0]


def _base_url(endpoint: str) -> str:
    """Strip the ingest path, leaving the listener root."""

    parts = urlsplit(endpoint)
    path = parts.path
    if path.endswith(INGEST_PATH):
        path = path[: -len(INGEST_PATH)]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _build_ssl_context(client_cert: str, client_key: str, ca_cert: str) -> ssl.SSLContext:
    """Blocking; callers must run this in an executor."""

    context = ssl.create_default_context(cafile=ca_cert)
    context.load_cert_chain(client_cert, client_key)
    context.check_hostname = False
    return context


class AgentHouseholdClient:
    """Four exact calls against the agent authority."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._ssl_context: ssl.SSLContext | None = None

    async def _context(self) -> tuple[str, ssl.SSLContext]:
        entry = _edge_entry(self._hass)
        data = entry.data
        missing = [
            key for key in ("endpoint", "client_cert", "client_key", "ca_cert")
            if not data.get(key)
        ]
        if missing:
            raise AgentHouseholdUnavailable(
                f"home_agent_edge is missing {', '.join(missing)}"
            )
        if self._ssl_context is None:
            self._ssl_context = await self._hass.async_add_executor_job(
                _build_ssl_context,
                data["client_cert"], data["client_key"], data["ca_cert"],
            )
        return _base_url(data["endpoint"]), self._ssl_context

    async def _request(
        self, method: str, path: str, ha_user_id: str, body: dict[str, Any] | None = None,
    ) -> Any:
        base, context = await self._context()
        session = async_get_clientsession(self._hass)
        headers = {
            "Accept": "application/json",
            "X-Authenticated-HA-User": ha_user_id,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with session.request(
                method, f"{base}{path}",
                headers=headers,
                data=None if body is None else json.dumps(body),
                ssl=context,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    raise AgentHouseholdUnavailable(
                        f"{method} {path} returned {response.status}: {text[:200]}"
                    )
                return json.loads(text) if text else {}
        except aiohttp.ClientError as error:
            raise AgentHouseholdUnavailable(f"{method} {path} failed: {error}") from error

    # ---------------------------------------------------------------- reads
    async def identities(self, ha_user_id: str) -> tuple[list[dict], list[dict]]:
        """Return (identities, relationship rows) in the legacy shapes."""

        household = await self._request("GET", "/v1/household", ha_user_id)
        try:
            relationships = await self._request("GET", "/v1/relationships", ha_user_id)
        except AgentHouseholdUnavailable as error:
            # The roster is readable and the edges are not. That is a valid
            # household, so render it rather than failing the whole view.
            LOGGER.debug("relationships unavailable, rendering roster only: %s", error)
            relationships = {"relationships": []}

        edges = [
            {
                "id": edge.get("fact_id"),
                "from_uuid": edge.get("subject_person_id"),
                "to_uuid": edge.get("object_person_id"),
                "rel_type": PREDICATE_TO_REL_TYPE.get(
                    edge.get("predicate"),
                    str(edge.get("predicate") or "").removesuffix("_of"),
                ),
                "status": "active",
            }
            for edge in relationships.get("relationships", [])
        ]

        by_person: dict[str, str] = {}
        for edge in edges:
            for person_id in (edge["from_uuid"], edge["to_uuid"]):
                by_person.setdefault(person_id, edge["rel_type"])

        identities = [
            {
                "uuid": person.get("person_id"),
                "display_name": person.get("display_name"),
                "pronouns": person.get("pronouns"),
                "relationship_type": (
                    "me" if person.get("is_self")
                    else REL_TYPE_TO_PERSON_TYPE.get(
                        by_person.get(person.get("person_id"), ""), "unknown"
                    )
                ),
                "relationship_subrole": None,
                "status": person.get("status"),
                "enrollment_count": 0,
                "avatar_present": False,
                "source": "agent_authority",
            }
            for person in household.get("people", [])
        ]
        return identities, edges

    # --------------------------------------------------------------- writes
    async def create_person(
        self, ha_user_id: str, ceremony_id: str, display_name: str,
        privacy_scope: str = "household", pronouns: str | None = None,
    ) -> dict:
        return await self._request(
            "POST", "/v1/household-person", ha_user_id,
            {
                "ceremony_id": ceremony_id,
                "display_name": display_name,
                "pronouns": pronouns,
                "privacy_scope": privacy_scope,
            },
        )

    async def record_relationship(
        self, ha_user_id: str, ceremony_id: str, attestation_nonce: str,
        subject_person_id: str | None, object_person_id: str, predicate: str,
    ) -> dict:
        body = {
            "ceremony_id": ceremony_id,
            "attestation_nonce": attestation_nonce,
            "partner_person_id": object_person_id,
            "predicate": predicate,
        }
        if subject_person_id:
            body["subject_person_id"] = subject_person_id
        return await self._request("POST", "/v1/partner-attestation", ha_user_id, body)
