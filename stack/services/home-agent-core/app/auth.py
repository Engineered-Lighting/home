from __future__ import annotations

import hmac
import re
from dataclasses import dataclass

from fastapi import Header, Request

from .errors import AuthenticationError


ATTESTED_NATIVE_CHANNEL = "private_tauri_attested_v1"
_NATIVE_INSTALLATION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def native_installation_id_valid(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and _NATIVE_INSTALLATION_ID.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    ha_user_id: str


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing bearer credential")
    return authorization.removeprefix("Bearer ").strip()


async def require_service_identity(
    request: Request,
    authorization: str | None = Header(default=None),
    x_authenticated_ha_user: str | None = Header(default=None),
) -> ServiceIdentity:
    supplied = _bearer(authorization)
    configured = request.app.state.settings.service_token
    if configured is None:
        raise AuthenticationError("service authentication is disabled for this role")
    expected = configured.get_secret_value()
    if not hmac.compare_digest(supplied, expected):
        raise AuthenticationError("invalid service credential")
    if not x_authenticated_ha_user or len(x_authenticated_ha_user) > 64:
        raise AuthenticationError("authenticated HA user is required")
    return ServiceIdentity(ha_user_id=x_authenticated_ha_user)


async def require_native_service_identity(
    request: Request,
    authorization: str | None = Header(default=None),
    x_authenticated_ha_user: str | None = Header(default=None),
    x_home_agent_channel: str | None = Header(default=None),
    x_home_agent_installation: str | None = Header(default=None),
) -> ServiceIdentity:
    """Require the BFF's narrow, authenticated native channel.

    The BFF constructs this header after validating the HA bearer on an exact
    native route. Browser input headers are never forwarded. Keeping the
    check in Core prevents its service credential alone from exposing private
    initiative or precise-place query endpoints.
    """

    identity = await require_service_identity(
        request,
        authorization=authorization,
        x_authenticated_ha_user=x_authenticated_ha_user,
    )
    if not x_home_agent_channel or not hmac.compare_digest(
        x_home_agent_channel,
        ATTESTED_NATIVE_CHANNEL,
    ):
        raise AuthenticationError("private native channel is required")
    if not native_installation_id_valid(x_home_agent_installation):
        raise AuthenticationError("attested native installation is required")
    return identity


async def require_service_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    supplied = _bearer(authorization)
    configured = request.app.state.settings.service_token
    if configured is None:
        raise AuthenticationError("service authentication is disabled for this role")
    if not hmac.compare_digest(supplied, configured.get_secret_value()):
        raise AuthenticationError("invalid service credential")


async def require_operator_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    supplied = _bearer(authorization)
    configured = request.app.state.settings.operator_token
    if configured is None:
        raise AuthenticationError("operator authentication is disabled")
    if not hmac.compare_digest(supplied, configured.get_secret_value()):
        raise AuthenticationError("invalid operator credential")


async def require_edge(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    supplied = _bearer(authorization)
    configured = request.app.state.settings.edge_token
    if configured is None:
        raise AuthenticationError("edge authentication is disabled for this role")
    expected = configured.get_secret_value()
    if not hmac.compare_digest(supplied, expected):
        raise AuthenticationError("invalid edge credential")


async def require_bootstrap(
    request: Request,
    x_home_agent_bootstrap: str | None = Header(default=None),
) -> None:
    configured = request.app.state.settings.bootstrap_token
    if configured is None:
        raise AuthenticationError("operator bootstrap is disabled for this role")
    expected = configured.get_secret_value()
    if not x_home_agent_bootstrap or not hmac.compare_digest(
        x_home_agent_bootstrap,
        expected,
    ):
        raise AuthenticationError("operator bootstrap credential is required")
