"""Actual two-ledger/TLS recovery with synthetic evidence and no model calls."""
import hashlib
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest, canonical_digest
from home_shared.gateway import Gateway, GatewayHTTPServer, _Handler, jpeg_dimensions
from home_shared.gateway_client import GatewayClient, GatewayNotSent, GatewayTransportError
from home_shared.submissions import SubmissionJournal
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture
from test_gateway_client import FIXTURES, config


class FaultHandler(_Handler):
    """Use real authenticated routes; inject only post-commit network loss."""

    def do_POST(self):
        self.server.post_count += 1
        try:
            super().do_POST()
        finally:
            self.server.post_finished.set()

    def do_GET(self):
        self.server.get_count += 1
        super().do_GET()

    def _write(self, status, payload):
        if self.command == "POST" and self.server.drop_next_post:
            self.server.drop_next_post = False
            self.server.lost_response = payload
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        super()._write(status, payload)


class RejectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rejection-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger_path = self.root / "gateway.sqlite"
        self.journal_path = self.root / "source.sqlite"
        self.now = time.time()
        self.payload = request_fixture(self.now)
        self.binding = binding_fixture()
        self.open_gateway()
        self.addCleanup(lambda: self.ledger.close())
        digest = hashlib.sha256(b"test-device-secret").hexdigest()
        self.server = GatewayHTTPServer(("127.0.0.1", 0), self.gateway, {digest: self.binding})
        self.server.RequestHandlerClass = FaultHandler
        self.server.post_count = self.server.get_count = 0
        self.server.drop_next_post = True
        self.server.post_finished = threading.Event()
        self.server.lost_response = None
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(FIXTURES / "server.pem"), str(FIXTURES / "server-test-only.key"))
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        # Pin fixture dialing to IPv4 without replacing real TLS or peer checks.
        self.client = GatewayClient(config(self.server.server_port),
                                    _connect=lambda address, timeout: socket.create_connection(
                                        ("127.0.0.1", address[1]), timeout=timeout))
        self.open_journal()
        self.addCleanup(lambda: self.journal.close())

    def open_gateway(self):
        self.ledger = AdmissionLedger(self.ledger_path)
        manifest = manifest_fixture()
        manifest["max_ledger_records"] = 1  # Lookup must still work with metadata capacity exhausted.
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest),
                                              FakeVerifier(), clock=lambda: self.now)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: self.now, image_validator=jpeg_dimensions)

    def open_journal(self):
        self.journal = SubmissionJournal(self.journal_path, device_id="den-adapter",
                                         gateway_identity=self.client.gateway_identity,
                                         max_records=10, free_space_floor_bytes=0)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def lose_post(self):
        self.assertTrue(self.journal.prepare(self.payload))
        with self.assertRaises(GatewayTransportError) as caught:
            self.client.submit(self.payload)
        self.assertNotIsInstance(caught.exception, GatewayNotSent)
        self.assertTrue(self.server.post_finished.wait(1))
        self.assertIsNotNone(self.journal.outstanding())
        self.assertEqual(self.server.post_count, 1)

    def test_lost_rejection_then_both_process_restarts_resolve_only_through_authenticated_get(self):
        # Admission is deliberately closed. The real gateway durably rejects
        # before the TLS fixture drops the only POST reply.
        self.lose_post()
        lost = self.server.lost_response
        self.assertEqual(lost["outcome"], "rejected_closed")
        self.assertEqual(lost["rejection"]["request_digest"], canonical_digest(self.payload))
        self.assertFalse(self.backend.calls)
        self.assertFalse(self.journal.prepare(self.payload))

        self.journal.close()
        self.open_journal()
        self.assertEqual(self.journal.outstanding().state, "indeterminate")
        self.ledger.close()
        self.open_gateway()
        self.server.gateway = self.gateway
        self.assertFalse(self.gateway._pending)
        self.assertFalse(self.gateway._results)

        response = self.client.status(self.payload["request_id"])
        self.assertEqual(response, lost)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0],
                         self.controller.manifest.max_ledger_records)
        self.assertTrue(self.journal.accept_status(response))
        self.assertIsNone(self.journal.outstanding())
        self.assertFalse(self.journal.prepare(self.payload))
        fresh = request_fixture(time.time(), request_id="fresh-opportunity")
        self.assertTrue(self.journal.prepare(fresh))
        self.assertEqual((self.server.post_count, self.server.get_count), (1, 1))
        self.assertFalse(self.backend.calls)
        record = self.ledger.get(canonical_digest(["victoria", "den-adapter", self.payload["request_id"]]))
        self.assertEqual((record.state, record.outcome, dict(record.reservation)), ("terminal", "rejected_closed", {}))

    def test_accepted_and_indeterminate_lookups_cannot_become_rejection_or_release_source(self):
        self.controller.enable("synthetic-epoch")
        self.lose_post()
        self.assertEqual(self.server.lost_response["status"], "queued")
        queued = self.client.status(self.payload["request_id"])
        self.assertEqual(queued["status"], "queued")
        self.assertNotIn("rejection", queued)
        self.assertTrue(self.journal.accept_status(queued))
        self.assertIsNotNone(self.journal.outstanding())

        record = self.controller.claim_next()
        self.controller.mark_running(record.request_id)
        self.controller.mark_indeterminate(record.request_id, "upstream_outcome_unknown")
        winner = self.controller.reject_if_absent(record.request_id, record.site_id, record.consumer_id,
                                                  record.digest, record.task_class, record.deadline, "rejected_closed")
        self.assertEqual(winner.state, "indeterminate")
        self.assertTrue(winner.reservation)

        self.journal.close()
        self.open_journal()
        self.ledger.close()
        self.open_gateway()
        self.server.gateway = self.gateway
        unresolved = self.client.status(self.payload["request_id"])
        self.assertEqual(unresolved["status"], "indeterminate")
        self.assertNotIn("rejection", unresolved)
        self.assertTrue(self.journal.accept_status(unresolved))
        self.assertIsNotNone(self.journal.outstanding())
        self.assertTrue(self.ledger.get(record.request_id).reservation)
        self.assertEqual((self.server.post_count, self.server.get_count), (1, 2))
        self.assertFalse(self.backend.calls)


if __name__ == "__main__":
    unittest.main()
