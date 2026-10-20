"""S1.1 task budget: the private TLS ingress enforces its absolute read deadline
on the handler thread itself, with no per-connection Timer thread. Real
localhost mutual TLS; fake engine only."""
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.gateway import Gateway, jpeg_dimensions
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture
from test_ingress import FINGERPRINT, FIXTURES, context

CONNECTIONS = 8   # IngressPolicy max_connections after S1.5


class ConnectionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = AdmissionLedger(Path(self.temp.name) / "ledger.db")
        self.addCleanup(self.ledger.close)
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest_fixture()),
                                              FakeVerifier(), clock=lambda: 1000)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: 1000, image_validator=jpeg_dimensions)

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition())

    def serve(self, policy):
        server = PrivateTLSIngress(("127.0.0.1", 0), self.gateway, certificate=str(FIXTURES / "server.pem"),
                                   private_key=str(FIXTURES / "server-test-only.key"),
                                   client_ca=str(FIXTURES / "ca.pem"), identities={FINGERPRINT: binding_fixture()},
                                   policy=policy)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        return server

    def connect(self, server):
        raw = socket.create_connection(server.server_address, timeout=5)
        connection = context().wrap_socket(raw, server_hostname="localhost")
        self.addCleanup(connection.close)
        return connection

    def test_eight_concurrent_connections_create_no_timer_threads(self):
        created = []
        timer = threading.Timer

        class Recording(timer):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)
        self.controller.enable("epoch-1")
        server = self.serve(IngressPolicy(CONNECTIONS, 2, 1.5, 2))
        with patch.object(threading, "Timer", Recording):
            before = set(threading.enumerate())
            connections = [self.connect(server) for _ in range(CONNECTIONS)]
            for connection in connections:
                # Headers and a partial body: every handler is inside its read deadline.
                connection.sendall(b"POST /v1/inference HTTP/1.0\r\nContent-Length: 100\r\n\r\n{")
            self.wait_for(lambda: server.active_connections == CONNECTIONS)
            time.sleep(.3)   # each handler has run setup() and is blocked reading the body
            new = [thread for thread in threading.enumerate() if thread not in before]
            self.assertEqual(created, [])
            self.assertEqual(len(new), CONNECTIONS)   # one handler thread per connection, nothing else
            self.assertFalse(any(isinstance(thread, timer) for thread in threading.enumerate()))
            # The absolute deadline still ends every stalled request, dispatching nothing.
            self.wait_for(lambda: server.active_connections == 0, timeout=5)
        self.assertEqual(self.backend.calls, [])
        # Deadline closes are transport errors: no handler failure closed admission.
        self.assertTrue(self.gateway.readiness()["ready"])

    def test_a_dripping_peer_is_cut_at_the_deadline_while_others_proceed(self):
        server = self.serve(IngressPolicy(CONNECTIONS, 2, .4, 2))
        drip = self.connect(server)
        self.wait_for(lambda: server.active_connections == 1)
        started = time.monotonic()
        while server.active_connections and time.monotonic() - started < 3:
            try:
                drip.sendall(b"G")   # one byte per 50 ms never reaches a full request line
            except OSError:
                break
            time.sleep(.05)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertLess(time.monotonic() - started, 1.5)
        healthy = self.connect(server)
        healthy.sendall(b"GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n")
        response = http.client.HTTPResponse(healthy)
        response.begin()
        self.assertEqual((response.status, json.loads(response.read())["mode"]), (200, "private-mtls-ingress"))
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
