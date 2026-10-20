"""S1.5 keep-alive on the private TLS ingress: HTTP/1.1 on the TLS handler only,
a read deadline per request, a 20 s idle close, a request cap with Connection:
close, connection-counted slots, TCP_NODELAY and no TLS session resumption.
Real localhost mutual TLS; fake engine only."""
import http.client
import json
from pathlib import Path
import select
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import PropertyMock, patch

from home_shared import lab_live_pilot as pilot_live
from home_shared.admission import AdmissionController, AdmissionError, AdmissionLedger, ResourceManifest
from home_shared.camera_pilot import REPLY_WAIT_SECONDS, CameraPilot
from home_shared.gateway import Gateway, GatewayHTTPServer, _Handler, jpeg_dimensions
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture
from test_ingress import FINGERPRINT, FIXTURES, context
from test_lab_live_pilot import Envelopes, Model, config_value
from test_pilot_sync_reply import VICTORIA, Serving, victoria_live_transport


def keep_alive(connections=4, read=1.0, idle=20, requests=100):
    return IngressPolicy(connections, 2, read, 2, requests, idle)


def read_response(connection, timeout=3):
    """One response, byte-exact: the head, then exactly Content-Length bytes.
    Returns (status line, lower-case headers, body); nothing is read past it."""
    connection.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = connection.recv(1)
        if not chunk:
            raise ConnectionError("closed before a response head")
        data += chunk
    head = data[:-4].decode("ascii").split("\r\n")
    headers = {}
    for line in head[1:]:
        name, _, value = line.partition(":")
        if name.lower() in headers:
            raise AssertionError("duplicate response header " + name)
        headers[name.lower()] = value.strip()
    length = int(headers["content-length"])
    body = b""
    while len(body) < length:
        chunk = connection.recv(length - len(body))
        if not chunk:
            raise ConnectionError("closed inside a Content-Length body")
        body += chunk
    return head[0], headers, body


def closed_by_peer(connection, timeout=2):
    """True when the server closes (EOF or reset) within timeout, sending nothing more."""
    connection.settimeout(timeout)
    try:
        return connection.recv(1) == b""
    except (ConnectionResetError, ssl.SSLError):
        return True
    except socket.timeout:
        return False


def still_open(connection, quiet=.2):
    """Nothing more arrives (no stray bytes, no close) for quiet seconds."""
    return connection.pending() == 0 and not select.select([connection], [], [], quiet)[0]


class IngressFixture(unittest.TestCase):
    """A real PrivateTLSIngress over the fake engine; accepted connections counted."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = AdmissionLedger(Path(self.temp.name) / "ledger.db")
        self.addCleanup(self.ledger.close)
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest_fixture()),
                                              FakeVerifier(), clock=lambda: 1000)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: 1000, image_validator=jpeg_dimensions)
        self.accepted = []

    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition())

    def serve(self, policy):
        server = PrivateTLSIngress(("127.0.0.1", 0), self.gateway, certificate=str(FIXTURES / "server.pem"),
                                   private_key=str(FIXTURES / "server-test-only.key"),
                                   client_ca=str(FIXTURES / "ca.pem"), identities={FINGERPRINT: binding_fixture()},
                                   policy=policy)
        process = server.process_request

        def counted(request, address):
            self.accepted.append(address)
            return process(request, address)
        server.process_request = counted
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.wait_for(lambda: server.active_connections == 0)
        self.addCleanup(stop)
        return server

    def connect(self, server, tls_context=None, **kwargs):
        raw = socket.create_connection(server.server_address, timeout=3)
        self.addCleanup(raw.close)
        connection = (tls_context or context()).wrap_socket(raw, server_hostname="localhost", **kwargs)
        self.addCleanup(connection.close)
        return connection

    @staticmethod
    def send(connection, method="GET", path="/healthz", body=b"", version="HTTP/1.1", headers=""):
        length = f"Content-Length: {len(body)}\r\n" if body or method == "POST" else ""
        connection.sendall(f"{method} {path} {version}\r\nHost: localhost\r\n{length}{headers}\r\n".encode() + body)


class KeepAliveTests(IngressFixture):
    def test_several_requests_share_one_connection_and_one_slot(self):
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive())
        connection = self.connect(server)
        statuses = []
        for method, path, body in (("GET", "/healthz", b""),
                                   ("POST", "/v1/inference", json.dumps(request_fixture()).encode()),
                                   ("GET", "/v1/inference/request-1", b""), ("GET", "/healthz", b"")):
            self.send(connection, method, path, body)
            line, headers, value = read_response(connection)
            statuses.append(line.split(" ", 2)[:2])
            self.assertNotIn("connection", headers)   # kept alive
            self.assertEqual(len(value), int(headers["content-length"]))
            json.loads(value)
            self.assertEqual(server.active_connections, 1)
            self.assertTrue(still_open(connection))
        self.assertEqual(statuses, [["HTTP/1.1", "200"], ["HTTP/1.1", "202"], ["HTTP/1.1", "200"],
                                    ["HTTP/1.1", "200"]])
        self.assertEqual(len(self.accepted), 1)
        self.gateway.dispatch_once()
        self.assertEqual(len(self.backend.calls), 1)

    def test_http_client_reuses_its_connection(self):
        # The way Victoria's live transport drives it: one HTTPSConnection,
        # every response read to its end before the next request.
        server = self.serve(keep_alive())
        tls = context()
        tls.check_hostname = False
        client = http.client.HTTPSConnection("127.0.0.1", server.server_address[1], timeout=3, context=tls)
        self.addCleanup(client.close)
        sockets = []
        for _ in range(5):
            client.request("GET", "/healthz")
            response = client.getresponse()
            self.assertEqual((response.status, json.loads(response.read())["mode"]), (200, "private-mtls-ingress"))
            self.assertFalse(response.will_close)
            sockets.append(client.sock)
        self.assertEqual(len(set(map(id, sockets))), 1)
        self.assertEqual(len(self.accepted), 1)

    def test_request_cap_says_close_on_the_last_request_then_closes(self):
        server = self.serve(keep_alive(requests=3))
        connection = self.connect(server)
        seen = []
        for _ in range(3):
            self.send(connection)
            _, headers, body = read_response(connection)
            self.assertEqual(json.loads(body)["mode"], "private-mtls-ingress")
            seen.append(headers.get("connection"))
        self.assertEqual(seen, [None, None, "close"])
        self.assertTrue(closed_by_peer(connection))
        self.wait_for(lambda: server.active_connections == 0)
        self.assertEqual(len(self.accepted), 1)

    def test_idle_connection_closes_quietly_and_frees_its_slot(self):
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive(read=.2, idle=.5))
        connection = self.connect(server)
        self.send(connection)
        read_response(connection)
        started = time.monotonic()
        self.assertTrue(closed_by_peer(connection, timeout=3))   # no response bytes, just the close
        self.assertGreaterEqual(time.monotonic() - started, .4)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertTrue(self.gateway.readiness()["ready"])     # an idle close is not a handler failure

    def test_first_request_may_wait_the_idle_timeout_not_the_read_deadline(self):
        # A pre-opened camera-chat connection sends its request after the
        # frame capture; its read deadline starts with that request.
        server = self.serve(keep_alive(read=.2, idle=1.5))
        connection = self.connect(server)
        time.sleep(.5)   # well past the read deadline, inside the idle timeout
        self.assertEqual(server.active_connections, 1)
        self.send(connection)
        line, _, _ = read_response(connection)
        self.assertEqual(line.split(" ")[1], "200")
        silent = self.connect(server)
        started = time.monotonic()
        self.assertTrue(closed_by_peer(silent, timeout=4))
        self.assertGreaterEqual(time.monotonic() - started, 1.2)

    def test_each_request_has_its_own_read_deadline(self):
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive(connections=1, read=.3, idle=5))
        connection = self.connect(server)
        self.send(connection)
        read_response(connection)
        time.sleep(.5)   # longer than one read deadline: the next request still gets a full one
        self.send(connection)
        self.assertEqual(read_response(connection)[0].split(" ")[1], "200")
        # A dripping third request is cut at its own deadline, before any dispatch.
        started = time.monotonic()
        while server.active_connections and time.monotonic() - started < 3:
            try:
                connection.sendall(b"P")
            except OSError:
                break
            time.sleep(.05)
        self.wait_for(lambda: server.active_connections == 0)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(self.gateway.readiness()["ready"])   # a deadline close is no handler failure

    def test_a_stalled_body_on_a_kept_connection_is_never_dispatched(self):
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive(read=.3, idle=5))
        connection = self.connect(server)
        self.send(connection)
        read_response(connection)
        connection.sendall(b"POST /v1/inference HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n{")
        self.wait_for(lambda: server.active_connections == 0)
        self.gateway.dispatch_once()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
        self.assertTrue(self.gateway.readiness()["ready"])

    def test_slots_count_connections_and_are_released_on_close(self):
        server = self.serve(keep_alive(connections=2))
        first, second = self.connect(server), self.connect(server)
        for connection in (first, second, first, second, first):
            self.send(connection)
            self.assertEqual(read_response(connection)[0].split(" ")[1], "200")
        self.assertEqual(server.active_connections, 2)
        # Both slots are held by idle kept-alive connections: a third is refused at once.
        with self.assertRaises((OSError, ConnectionError)):
            third = self.connect(server)
            self.send(third)
            read_response(third)
        first.close()
        self.wait_for(lambda: server.active_connections == 1)
        fourth = self.connect(server)
        self.send(fourth)
        self.assertEqual(read_response(fourth)[0].split(" ")[1], "200")
        self.assertEqual(server.active_connections, 2)

    def test_unread_bodies_and_closing_peers_get_connection_close(self):
        server = self.serve(keep_alive())
        cases = (
            # (request bytes, expected status): each response says close and the server closes.
            (b"POST /v1/inference HTTP/1.1\r\nHost: localhost\r\nContent-Length: 999999\r\n\r\n", "413"),
            (b"POST /v1/inference HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\n{",
             "400"),
            (b"POST /v1/inference HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", "400"),
            (b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nhello", "200"),
            (b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n", "200"),
            (b"GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n", "200"),
            (b"GET /healthz HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n", "200"),
            (b"PUT /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n", "501"),
        )
        for request, status in cases:
            with self.subTest(request=request[:40]):
                connection = self.connect(server)
                self.send(connection)   # a kept request first: the close is per request
                self.assertNotIn("connection", read_response(connection)[1])
                connection.sendall(request)
                line, headers, body = read_response(connection)
                self.assertEqual(line.split(" ")[1], status)
                self.assertEqual(headers.get("connection"), "close")
                self.assertEqual(len(body), int(headers["content-length"]))
                self.assertTrue(closed_by_peer(connection))
        self.wait_for(lambda: server.active_connections == 0)
        self.assertEqual(self.backend.calls, [])

    def test_listener_close_ends_idle_kept_connections(self):
        server = self.serve(keep_alive())
        connection = self.connect(server)
        self.send(connection)
        read_response(connection)
        self.assertEqual(server.active_connections, 1)
        server.shutdown()
        server.server_close()
        self.assertTrue(closed_by_peer(connection))
        self.assertTrue(server.wait_handlers(2))

    def test_accepted_sockets_disable_nagle(self):
        server = self.serve(keep_alive())
        seen = []
        finish = server.finish_request

        def recording(connection, address):
            seen.append(connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY))
            return finish(connection, address)
        server.finish_request = recording
        connection = self.connect(server)
        self.send(connection)
        read_response(connection)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0])

    def test_the_rollback_cap_of_one_closes_every_connection_but_keeps_the_first_wait(self):
        server = self.serve(keep_alive(read=.2, idle=1.5, requests=1))
        connection = self.connect(server)
        time.sleep(.5)   # a pre-opened camera-chat connection still has its idle allowance
        self.send(connection)
        line, headers, body = read_response(connection)
        self.assertEqual((line.split(" ")[:2], headers.get("connection")), (["HTTP/1.1", "200"], "close"))
        self.assertEqual(len(body), int(headers["content-length"]))
        self.assertTrue(closed_by_peer(connection))
        self.wait_for(lambda: server.active_connections == 0)

    def test_policy_bounds(self):
        self.assertEqual(IngressPolicy(4, 2, 3, 2), IngressPolicy(4, 2, 3, 2, None, 20))
        for args in ((4, 2, 3, 2, 0), (4, 2, 3, 2, 1001), (4, 2, 3, 2, True), (4, 2, 3, 2, 2.0),
                     (4, 2, 3, 2, 100, 0), (4, 2, 3, 2, 100, 61), (4, 2, 3, 2, 100, float("nan"))):
            with self.subTest(args=args), self.assertRaises(AdmissionError):
                IngressPolicy(*args)


class UnchangedHTTP10Tests(IngressFixture):
    """Without keep-alive in the policy, and on the loopback gateway harness,
    every response stays HTTP/1.0 with one request per connection."""

    def test_gateway_handler_stays_http_1_0(self):
        self.assertEqual(_Handler.protocol_version, "HTTP/1.0")
        server = GatewayHTTPServer(("127.0.0.1", 0), self.gateway)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            connection = socket.create_connection(server.server_address, timeout=3)
            with connection:
                connection.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
                line, headers, body = read_response(connection)
                self.assertTrue(line.startswith("HTTP/1.0 200"))
                self.assertNotIn("connection", headers)
                self.assertEqual(json.loads(body)["mode"], "local-boundary-harness")
                self.assertEqual(connection.recv(1), b"")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_default_policy_is_one_request_per_connection_over_http_1_0(self):
        server = self.serve(IngressPolicy(2, 2, 1, 2))
        connection = self.connect(server)
        self.send(connection)
        line, headers, body = read_response(connection)
        self.assertTrue(line.startswith("HTTP/1.0 200"))
        self.assertNotIn("connection", headers)
        self.assertEqual(json.loads(body)["mode"], "private-mtls-ingress")
        self.assertTrue(closed_by_peer(connection))
        self.wait_for(lambda: server.active_connections == 0)


class SessionResumptionTests(IngressFixture):
    """No TLS session resumption: identity always comes from a full handshake
    that verified the client certificate (speed plan S1.5)."""

    def client_context(self, maximum):
        value = context()
        value.maximum_version = maximum
        return value

    def test_tls_1_3_issues_no_ticket_so_nothing_resumes(self):
        # The first connection stays open, kept alive, while the second offers
        # its session: the case in which a server still holds the session.
        server = self.serve(keep_alive())
        tls = self.client_context(ssl.TLSVersion.TLSv1_3)
        first = self.connect(server, tls)
        self.send(first)
        read_response(first)
        self.assertEqual(first.version(), "TLSv1.3")
        session = first.session
        self.assertFalse(session is not None and session.has_ticket)
        second = self.connect(server, tls, session=session)
        self.send(second)
        self.assertEqual(read_response(second)[0].split(" ")[1], "200")
        self.assertFalse(second.session_reused)
        self.assertEqual(server.active_connections, 2)

    def test_a_resumed_tls_1_2_session_cannot_stand_in_for_a_verified_certificate(self):
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive())
        tls = self.client_context(ssl.TLSVersion.TLSv1_2)
        first = self.connect(server, tls)   # kept open: its session stays in the server cache
        self.send(first)
        read_response(first)
        self.assertEqual(first.version(), "TLSv1.2")
        session = first.session
        self.assertFalse(session.has_ticket)   # no TLS 1.2 tickets either
        with patch("sys.stderr") as stderr:
            resumed = self.connect(server, tls, session=session)
            # Tickets are off, but OpenSSL's server cache still resumes a TLS
            # 1.2 session ID, skipping certificate verification. The ingress
            # serves that connection nothing and leaves admission open.
            self.assertTrue(resumed.session_reused)
            try:
                self.send(resumed, "POST", "/v1/inference", json.dumps(request_fixture()).encode())
            except OSError:
                pass
            self.assertTrue(closed_by_peer(resumed))
            self.wait_for(lambda: server.active_connections == 1)   # only the kept first connection
            stderr.write.assert_not_called()
        self.gateway.dispatch_once()
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
        self.assertTrue(self.gateway.readiness()["ready"])
        # A full handshake is still served.
        fresh = self.connect(server, tls)
        self.send(fresh)
        self.assertEqual(read_response(fresh)[0].split(" ")[1], "200")
        self.assertFalse(fresh.session_reused)

    def test_any_reused_session_is_refused_before_identity_lookup(self):
        # Whatever the TLS version or library, a handshake that reports a
        # reused session is never served and never closes admission.
        self.controller.enable("epoch-1")
        server = self.serve(keep_alive())
        with patch.object(ssl.SSLSocket, "session_reused", new_callable=PropertyMock, return_value=True), \
                patch("sys.stderr") as stderr:
            connection = self.connect(server)
            try:
                self.send(connection, "POST", "/v1/inference", json.dumps(request_fixture()).encode())
            except OSError:
                pass
            self.assertTrue(closed_by_peer(connection))
            self.wait_for(lambda: server.active_connections == 0)
            stderr.write.assert_not_called()
        self.gateway.dispatch_once()
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(self.gateway.readiness()["ready"])


@unittest.skipUnless((VICTORIA / "qwen_live.py").is_file(), "Victoria perception-lab sources not in this checkout")
class VictoriaLiveKeepAliveTests(Serving, unittest.TestCase):
    """Victoria's live transport (perception-lab, imported unchanged) over the
    :9446 keep-alive policy: successive live requests share one connection."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = Model()
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda request: "An empty den.", model_revision="pinned",
                                 image_validator=lambda _: None, free_space_floor=0)
        self.addCleanup(self.pilot.close)
        path = Path(self.temp.name) / "live-scene.json"
        path.write_text(json.dumps(config_value()))
        self.live = pilot_live.PilotLiveScene(self.pilot, pilot_live.load_live_config(path), backend=self.model,
                                              log=lambda line: None, reply_wait_seconds=REPLY_WAIT_SECONDS)
        self.addCleanup(self.live.close, 5)

    def transport(self, policy):
        server = pilot_live.PilotLiveIngress(("127.0.0.1", 0), self.pilot, self.live,
                                             certificate=str(FIXTURES / "server.pem"),
                                             private_key=str(FIXTURES / "server-test-only.key"),
                                             client_ca=str(FIXTURES / "ca.pem"),
                                             identities={FINGERPRINT: self.live.binding, "0" * 64: binding_fixture()},
                                             policy=policy)
        self.accepted = []
        process = server.process_request
        server.process_request = lambda request, address: self.accepted.append(address) or process(request, address)
        port = self.serve(server)
        tls = context()
        tls.check_hostname = False
        config = {"hostname": "localhost", "port": port, "ca_file": str(FIXTURES / "ca.pem"),
                  "client_certificate": str(FIXTURES / "client.pem"),
                  "client_key": str(FIXTURES / "client-test-only.key"), "deadline_seconds": 7,
                  "response_grace_seconds": 2, "poll_seconds": .1}
        transport = victoria_live_transport()(
            config, connection_factory=lambda timeout: http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                                                                    context=tls))
        self.addCleanup(transport.close)
        return server, transport

    def submit_three(self, transport):
        proofs = [transport(Envelopes().envelope(deadline=time.time() + 7)) for _ in range(3)]
        return [(proof["state"], proof["terminal"]) for proof in proofs]

    def test_successive_live_requests_share_one_kept_connection(self):
        server, transport = self.transport(keep_alive(connections=8, read=3))
        self.assertEqual(self.submit_three(transport), [("completed", True)] * 3)
        self.assertEqual(len(self.accepted), 1)
        self.assertEqual(server.active_connections, 1)   # kept for the next request
        self.assertEqual(len(self.model.calls), 3)

    def test_the_rollback_policy_still_serves_one_request_per_connection(self):
        _, transport = self.transport(IngressPolicy(8, 2, 3, 2, 1, 20))
        self.assertEqual(self.submit_three(transport), [("completed", True)] * 3)
        self.assertEqual(len(self.accepted), 3)


if __name__ == "__main__":
    unittest.main()
