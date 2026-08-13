"""Mutually authenticated HTTPS transport."""

from __future__ import annotations

import json
import os
import ssl
import stat
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from aiohttp import ClientSession, ClientTimeout, TCPConnector


class MutualTLSTransport:
    """POST edge batches using a dedicated client-certificate session."""

    def __init__(
        self,
        endpoint: str,
        *,
        client_cert: str,
        client_key: str,
        ca_cert: str,
        edge_token_path: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Home Agent Edge endpoint must be an https URL")
        if (
            parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or parsed.path != "/v1/ingest/envelopes"
        ):
            raise ValueError(
                "Home Agent Edge endpoint may not contain credentials or fragments"
            )
        self.endpoint = endpoint
        self.privacy_policy_endpoint = urlunparse(
            parsed._replace(path="/v1/ingest/privacy-policy", query="", fragment="")
        )
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca_cert = ca_cert
        self.edge_token_path = edge_token_path
        self.timeout_seconds = timeout_seconds
        self._session: ClientSession | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._edge_token: str | None = None

    def prepare(self) -> None:
        """Load private files and construct TLS state outside HA's event loop."""

        if self._ssl_context is not None and self._edge_token is not None:
            return
        client_key_file = Path(self.client_key)
        token_file = Path(self.edge_token_path)
        for label, private_file in (
            ("client key", client_key_file),
            ("bearer credential", token_file),
        ):
            if not private_file.is_file():
                raise ValueError(f"Home Agent Edge {label} file is unavailable")
            if os.name == "posix" and stat.S_IMODE(private_file.stat().st_mode) & 0o077:
                raise ValueError(
                    f"Home Agent Edge {label} file must be mode 0600 or stricter"
                )
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH, cafile=self.ca_cert
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(self.client_cert, self.client_key)
        token = token_file.read_text(encoding="utf-8").strip()
        if len(token) < 32 or any(character.isspace() for character in token):
            raise ValueError("Home Agent Edge bearer credential is invalid")
        self._ssl_context = context
        self._edge_token = token

    async def start(self) -> None:
        """Create the async HTTP session from already-prepared TLS state."""

        if self._session is not None:
            return
        if self._ssl_context is None or self._edge_token is None:
            raise RuntimeError("mTLS transport has not been prepared")
        self._session = ClientSession(
            connector=TCPConnector(ssl=self._ssl_context),
            timeout=ClientTimeout(total=self.timeout_seconds),
            raise_for_status=False,
        )

    async def send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._session is None:
            raise RuntimeError("mTLS transport has not been started")
        if self._edge_token is None:
            raise RuntimeError("edge bearer credential is unavailable")
        async with self._session.post(
            self.endpoint,
            json=dict(payload),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._edge_token}",
            },
            allow_redirects=False,
        ) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"edge_receiver_http_{response.status}")
            if (
                response.content_length is not None
                and response.content_length > 64 * 1024
            ):
                raise RuntimeError("edge_receiver_response_too_large")
            body = await response.read()
            if len(body) > 64 * 1024:
                raise RuntimeError("edge_receiver_response_too_large")
            try:
                decoded = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("edge_receiver_response_not_json") from exc
            if not isinstance(decoded, Mapping):
                raise RuntimeError("edge_receiver_response_not_object")
            return decoded

    async def fetch_privacy_policy(self) -> Mapping[str, Any]:
        if self._session is None or self._edge_token is None:
            raise RuntimeError("mTLS transport has not been started")
        async with self._session.get(
            self.privacy_policy_endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._edge_token}",
            },
            allow_redirects=False,
        ) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"edge_privacy_policy_http_{response.status}")
            body = await response.read()
            if len(body) > 64 * 1024:
                raise RuntimeError("edge_privacy_policy_response_too_large")
            try:
                decoded = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("edge_privacy_policy_response_not_json") from exc
            if not isinstance(decoded, Mapping):
                raise RuntimeError("edge_privacy_policy_response_not_object")
            return decoded

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._ssl_context = None
        self._edge_token = None
