"""Real localhost mutual TLS; fake engine only, no home or model calls."""
import hashlib
import http.client
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.admission import AdmissionController, AdmissionError, AdmissionLedger, ResourceManifest
from home_shared.gateway import Gateway, jpeg_dimensions
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture

FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"
FINGERPRINT = hashlib.sha256(ssl.PEM_cert_to_DER_cert(
    (FIXTURES / "client.pem").read_text())).hexdigest()


def context(client=True):
    value = ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))
    if client:
        value.load_cert_chain(str(FIXTURES / "client.pem"), str(FIXTURES / "client-test-only.key"))
    return value


class IngressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = AdmissionLedger(Path(self.temp.name) / "ledger.db")
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest_fixture()),
                                              FakeVerifier(), clock=lambda: 1000)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: 1000,
                               image_validator=jpeg_dimensions)
        self.servers = []
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.wait_for(lambda: server.active_connections == 0)
        self.ledger.close()
        self.temp.cleanup()

    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition())

    def server(self, *, identities=None, policy=None):
        server = PrivateTLSIngress(("127.0.0.1", 0), self.gateway,
            certificate=str(FIXTURES / "server.pem"), private_key=str(FIXTURES / "server-test-only.key"),
            client_ca=str(FIXTURES / "ca.pem"),
            identities={FINGERPRINT: binding_fixture()} if identities is None else identities,
            policy=policy or IngressPolicy(2, .6, .5, 2))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def connect(self, server, *, tls=True, client=True):
        raw = socket.create_connection(server.server_address, timeout=2)
        self.connections.append(raw)
        if not tls:
            return raw
        connection = context(client).wrap_socket(raw, server_hostname="localhost")
        self.connections.append(connection)
        return connection

    def request(self, server, path="/healthz", payload=None, headers=""):
        connection = self.connect(server)
        body = b"" if payload is None else json.dumps(payload).encode()
        method = "GET" if payload is None else "POST"
        connection.sendall((f"{method} {path} HTTP/1.0\r\nHost: localhost\r\n"
                            f"Content-Length: {len(body)}\r\n{headers}\r\n").encode() + body)
        response = http.client.HTTPResponse(connection)
        response.begin()
        data = response.read()
        return response.status, json.loads(data)

    def test_registered_certificate_snapshot_uses_existing_queue_and_site_binding(self):
        self.controller.enable("epoch-1")
        server = self.server()
        status, result = self.request(server, "/v1/inference", request_fixture(),
                                      "Authorization: Bearer forged\r\nX-Site-ID: echo\r\n")
        self.assertEqual(status, 202)
        self.assertEqual(result["site_id"], "victoria")
        self.assertEqual(self.backend.calls, [])
        self.gateway.dispatch_once()
        self.assertEqual(len(self.backend.calls), 1)
        status, _ = self.request(server, "/v1/inference", request_fixture(site="echo"))
        self.assertEqual(status, 400)
        status, _ = self.request(server, "/v1/models", headers="X-Site-ID: echo\r\n")
        self.assertEqual(status, 401)

    def test_unknown_fingerprint_cannot_access_even_health(self):
        server = self.server(identities={"0" * 64: binding_fixture()})
        with self.assertRaises((OSError, http.client.HTTPException)):
            self.request(server)
        self.wait_for(lambda: server.active_connections == 0)

    def test_missing_client_certificate_rejected(self):
        server = self.server()
        with self.assertRaises((OSError, http.client.HTTPException)):
            connection = self.connect(server, client=False)
            connection.sendall(b"GET /healthz HTTP/1.0\r\n\r\n")
            response = http.client.HTTPResponse(connection)
            response.begin()

    def test_plaintext_rejected(self):
        server = self.server()
        connection = self.connect(server, tls=False)
        connection.sendall(b"GET /healthz HTTP/1.0\r\n\r\n")
        try:
            self.assertEqual(connection.recv(1024), b"")
        except ConnectionResetError:
            pass
        self.wait_for(lambda: server.active_connections == 0)

    def test_stalled_handshake_does_not_block_valid_connection(self):
        server = self.server(policy=IngressPolicy(2, 1.5, .5, 2))
        self.connect(server, tls=False)
        self.wait_for(lambda: server.active_connections == 1)
        self.assertEqual(self.request(server)[0], 200)
        self.assertGreaterEqual(server.active_connections, 1)
        self.wait_for(lambda: server.active_connections == 0)

    def test_capacity_is_held_through_handshake_then_released(self):
        server = self.server(policy=IngressPolicy(1, .4, .5, 2))
        self.connect(server, tls=False)
        self.wait_for(lambda: server.active_connections == 1)
        with self.assertRaises(OSError):
            self.connect(server)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertEqual(self.request(server)[0], 200)

    def test_absolute_deadline_rejects_drip_headers(self):
        server = self.server(policy=IngressPolicy(1, 1, .25, 2))
        connection = self.connect(server)
        for _ in range(10):
            try:
                connection.sendall(b"G")
            except OSError:
                break
            time.sleep(.06)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertEqual(self.backend.calls, [])

    def test_absolute_body_deadline_does_not_dispatch(self):
        server = self.server(policy=IngressPolicy(1, 1, .25, 2))
        connection = self.connect(server)
        connection.sendall(b"POST /v1/inference HTTP/1.0\r\nContent-Length: 100\r\n\r\n{")
        time.sleep(.15)
        connection.sendall(b" ")
        self.wait_for(lambda: server.active_connections == 0)
        self.assertEqual(self.backend.calls, [])

    def test_read_timer_ends_before_gateway_dispatch(self):
        server = self.server(policy=IngressPolicy(1, 1, .15, 2))
        original = self.gateway.submit
        def delayed(*args):
            time.sleep(.3)
            return original(*args)
        with patch.object(self.gateway, "submit", side_effect=delayed):
            status, _ = self.request(server, "/v1/inference", request_fixture())
        self.assertEqual(status, 202)

    def test_body_limit_and_duplicate_length_reused(self):
        server = self.server()
        for headers, expected in (("Content-Length: 999999", 413),
                                  ("Content-Length: 1\r\nContent-Length: 1", 400)):
            connection = self.connect(server)
            connection.sendall(f"POST /v1/inference HTTP/1.0\r\n{headers}\r\n\r\n".encode())
            response = http.client.HTTPResponse(connection)
            response.begin()
            response.read()
            self.assertEqual(response.status, expected)

    def test_close_interrupts_handshake_and_releases_capacity(self):
        server = self.server(policy=IngressPolicy(1, 5, .5, 2))
        self.connect(server, tls=False)
        self.wait_for(lambda: server.active_connections == 1)
        server.shutdown()
        server.server_close()
        self.wait_for(lambda: server.active_connections == 0)

    def test_close_does_not_release_worker_until_gateway_returns(self):
        self.controller.enable("epoch-1")
        server = self.server(policy=IngressPolicy(1, 1, .5, 2))
        entered, release = threading.Event(), threading.Event()
        original = self.gateway.submit
        def delayed(*args):
            entered.set()
            release.wait(3)
            return original(*args)
        errors = []
        def request():
            try:
                self.request(server, "/v1/inference", request_fixture())
            except (OSError, http.client.HTTPException) as exc:
                errors.append(type(exc).__name__)
        with patch.object(self.gateway, "submit", side_effect=delayed):
            thread = threading.Thread(target=request)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                server.shutdown()
                server.server_close()
                self.assertEqual(server.active_connections, 1)
                self.assertFalse(self.controller.readiness()["ready"])
                self.assertFalse(server.wait_handlers(.01))
            finally:
                release.set()
                thread.join(3)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        rows = self.ledger.db.execute('SELECT state,outcome FROM requests').fetchall()
        self.assertEqual([tuple(row) for row in rows], [('terminal', 'rejected_closed')])
        self.assertFalse(self.backend.calls)
        self.assertTrue(server.wait_handlers(1))

    def test_drain_requires_closed_listener_and_finite_bounded_timeout(self):
        server = self.server()
        self.assertFalse(server.wait_handlers(0))
        for timeout in (True, -1, float('nan'), float('inf'), 61, 10**400):
            with self.subTest(timeout=timeout), self.assertRaises(AdmissionError):
                server.wait_handlers(timeout)
        server.shutdown()
        server.server_close()
        self.assertTrue(server.wait_handlers(0))

    def test_unexpected_handler_failure_closes_admission_without_exception_log(self):
        self.controller.enable("epoch-1")
        server = self.server()
        with patch.object(self.gateway, "readiness", side_effect=RuntimeError("private message")), \
                patch("sys.stderr") as stderr:
            with self.assertRaises((OSError, http.client.HTTPException)):
                self.request(server, "/readyz")
            self.wait_for(lambda: server.active_connections == 0)
            stderr.write.assert_not_called()
        self.assertFalse(self.gateway.readiness()["ready"])
        self.assertEqual(self.request(server)[1]["mode"], "private-mtls-ingress")

    def test_configuration_rejects_wildcard_public_names_and_invalid_bounds(self):
        for host in ("0.0.0.0", "::", "8.8.8.8", "localhost", "169.254.1.1", "192.0.2.1"):
            with self.subTest(host=host), self.assertRaises(AdmissionError):
                PrivateTLSIngress((host, 0), self.gateway, certificate="", private_key="", client_ca="",
                                  identities={FINGERPRINT: binding_fixture()}, policy=IngressPolicy(1, 1, 1, 1))
        for args in ((0, 1, 1, 1), (True, 1, 1, 1), (1, 0, 1, 1), (1, 1, float("nan"), 1)):
            with self.assertRaises(AdmissionError):
                IngressPolicy(*args)


if __name__ == "__main__":
    unittest.main()
