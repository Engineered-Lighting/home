#!/usr/bin/env python3
"""Standalone transport preparation tests without a Home Assistant install."""

from __future__ import annotations

from pathlib import Path
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


package = types.ModuleType("home_agent_edge")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules["home_agent_edge"] = package


class FakeConnector:
    def __init__(self, *, ssl):
        self.ssl = ssl


class FakeSession:
    def __init__(self, *, connector, timeout, raise_for_status):
        self.connector = connector
        self.timeout = timeout
        self.raise_for_status = raise_for_status
        self.closed = False

    async def close(self):
        self.closed = True


class FakeTimeout:
    def __init__(self, *, total):
        self.total = total


aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = FakeSession
aiohttp.ClientTimeout = FakeTimeout
aiohttp.TCPConnector = FakeConnector
sys.modules["aiohttp"] = aiohttp

from home_agent_edge.transport import MutualTLSTransport  # noqa: E402


class FakeSSLContext:
    minimum_version = None
    check_hostname = None
    verify_mode = None

    def __init__(self):
        self.cert_chain = None

    def load_cert_chain(self, cert, key):
        self.cert_chain = (cert, key)


class TransportPreparationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ca = root / "ca.crt"
        self.cert = root / "client.crt"
        self.key = root / "client.key"
        self.token = root / "token"
        for path in (self.ca, self.cert, self.key):
            path.write_text("fixture", encoding="utf-8")
        self.token.write_text("t" * 48, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _transport(self):
        return MutualTLSTransport(
            "https://receiver.example/v1/ingest/envelopes",
            client_cert=str(self.cert),
            client_key=str(self.key),
            ca_cert=str(self.ca),
            edge_token_path=str(self.token),
        )

    async def test_start_requires_executor_preparation(self):
        transport = self._transport()
        with self.assertRaisesRegex(RuntimeError, "has not been prepared"):
            await transport.start()

    async def test_prepare_loads_all_blocking_tls_material_before_start(self):
        transport = self._transport()
        context = FakeSSLContext()
        with patch.object(
            ssl, "create_default_context", return_value=context
        ) as create:
            transport.prepare()

        create.assert_called_once_with(ssl.Purpose.SERVER_AUTH, cafile=str(self.ca))
        self.assertEqual((str(self.cert), str(self.key)), context.cert_chain)

        await transport.start()
        self.assertIs(context, transport._session.connector.ssl)
        self.assertEqual(10.0, transport._session.timeout.total)
        await transport.close()
        self.assertIsNone(transport._session)
        self.assertIsNone(transport._ssl_context)
        self.assertIsNone(transport._edge_token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
