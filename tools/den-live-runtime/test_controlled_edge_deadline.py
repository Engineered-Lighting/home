"""Fixed TLS context adapter deadlines observed through real loopback peers."""
from contextlib import ExitStack
import http.client
from pathlib import Path
import shutil
import socket
import ssl
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_external_io import build_base_configuration
from controlled_session import ControlledSession
from live_context_edge import binding_digest


class ControlledEdgeDeadlineTests(unittest.TestCase):
    def setUp(self):
        cleanup = ExitStack()
        self.addCleanup(cleanup.close)
        directory = cleanup.enter_context(tempfile.TemporaryDirectory(
            dir=Path(__file__).parent, prefix="controlled-edge-deadline-"))
        bundle = build_base_configuration(session_ref={"site_id": "authored-site", "room_id": "authored-room",
            "session_id": "authored-edge-deadline", "domain_epoch": "authored-edge-domain"}, node_runtime=shutil.which("node"))
        self.session = ControlledSession(bundle=bundle, output_directory=directory, node_runtime=shutil.which("node"),
            source_bundle_digest="b" * 64)
        cleanup.callback(self.session.close)
        self.cleanup = cleanup
        self.edge = self.session.start_edge()
        self.port = int(self.edge["url"].rsplit(":", 1)[1])
        self.context = ssl.create_default_context(cafile=self.edge["ca_file"])
        self.headers = {"Authorization": "Bearer " + Path(self.edge["token_file"]).read_text(),
            "X-Home-Context-Binding": binding_digest(self.edge["binding"])}
        self.route = "/api/home-context/v1/snapshot?site_id=authored-site&room_id=authored-room"
        self.session.control({"id": "deadline-session-start", "operation": "start"})
        until = time.monotonic() + 8
        while self.session.status()["status"] == "starting" and time.monotonic() < until:
            time.sleep(.02)
        self.assertEqual(self.session.status()["status"], "running", self.session.status())

    def healthy_request(self, timeout=1):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=timeout)
        try:
            connection.request("GET", self.route, headers=self.headers)
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
        finally:
            connection.close()

    def tls_peer(self):
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        try:
            peer = self.context.wrap_socket(raw, server_hostname="127.0.0.1")
        except BaseException:
            raw.close()
            raise
        self.cleanup.callback(peer.close)
        return peer

    def assert_trickling_peer_is_closed(self, peer, started):
        while time.monotonic() < started + 3.25:
            try:
                peer.sendall(b"x")
            except OSError:
                break
            time.sleep(.2)
        peer.settimeout(max(.01, started + 3.6 - time.monotonic()))
        self.assertEqual(peer.recv(1), b"")
        self.assertLess(time.monotonic() - started, 3.6)
        self.healthy_request()

    def test_stalled_tls_handshake_cannot_block_accept_and_peer_is_closed_at_deadline(self):
        peer = socket.create_connection(("127.0.0.1", self.port), timeout=1)
        self.cleanup.callback(peer.close)
        started = time.monotonic()
        # No ClientHello is sent. A second genuine TLS peer must still reach
        # the ordinary authenticated ContextEdge while this peer is stalled.
        self.healthy_request()
        peer.settimeout(max(.01, started + 3.6 - time.monotonic()))
        self.assertEqual(peer.recv(1), b"")
        self.assertLess(time.monotonic() - started, 3.6)
        self.healthy_request()

    def test_slow_http_headers_do_not_renew_the_original_tls_deadline(self):
        started = time.monotonic()
        peer = self.tls_peer()
        # Keep the header section open with chunks well below the old socket
        # inactivity timeout. No blank line terminates these headers.
        peer.sendall(("GET " + self.route + " HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Authored-Trickle: ").encode())
        self.assert_trickling_peer_is_closed(peer, started)

    def test_slow_body_upload_does_not_renew_the_original_tls_deadline(self):
        started = time.monotonic()
        peer = self.tls_peer()
        headers = "".join(key + ": " + value + "\r\n" for key, value in self.headers.items())
        peer.sendall(("POST /api/home-context/v1/intent-events HTTP/1.1\r\nHost: 127.0.0.1\r\n" + headers
            + "Content-Type: application/json\r\nContent-Length: 65536\r\n\r\n{").encode())
        self.assert_trickling_peer_is_closed(peer, started)


if __name__ == "__main__":
    unittest.main()
