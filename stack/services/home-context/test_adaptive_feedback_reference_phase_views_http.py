"""Opt-in literal-loopback public seam; execution is a separately opened gate."""
import json
import copy
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from journal import PREFIX, canonical
from server import LocalServer
from test_adaptive_feedback_reference_phase_views import Host
import test_adaptive_feedback_reference_context_service as reference


class PhaseViewHttpTests(unittest.TestCase):
    def test_authenticated_public_commit_retry_checkpoint_proof_and_original(self):
        reference.ReferenceFeedbackServiceTests.setUpClass()
        h = Host(reference.ReferenceFeedbackServiceTests.f, optin=True)
        # Pure authoring intercept records a prospective bootstrap only; the
        # actual commits and outputs below are admitted through HTTP.
        authored = []
        with patch.object(h.j.dynamic, "worker_commit", side_effect=lambda c, g: authored.append(copy.deepcopy(c)) or {}):
            genesis = h.semantic_genesis()
        bootstrap = authored[0]
        server = LocalServer(("127.0.0.1", 0), h.j); thread = threading.Thread(target=server.serve_forever, daemon=False); thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_port) + PREFIX; opener = build_opener(ProxyHandler({}))
            def request(route, body=None, token="test-worker", extra=""):
                suffix = "?site_id=" + h.identity["site_id"] + "&room_id=" + h.identity["room_id"] + extra if body is None else ""
                req = Request(base + route + suffix, data=canonical(body).encode() if body is not None else None,
                              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                with opener.open(req, timeout=5) as response: return json.load(response)
            first = request("/worker-commits", bootstrap)
            self.assertEqual(first, request("/worker-commits", bootstrap))
            state = request("/worker-commits/worker"); self.assertEqual(state["checkpoint"], bootstrap["checkpoint"])
            admitted = request("/worker-commits", genesis)
            self.assertEqual(admitted, request("/worker-commits", genesis))
            cursor = next(row["cursor"] for row in admitted["receipts"] if row["id"] == "installed-zero")
            suffix = "&cursor=" + str(cursor) + "&consumer_id=worker"
            proof = request("/feedback-worker-output-proofs", extra=suffix)
            self.assertEqual(proof["commit"], genesis); self.assertEqual(proof["request_canonical_json"], canonical(genesis))
            self.assertEqual(proof["receipt"], admitted); self.assertIsNone(proof["worker_receipt"])
            original = request("/feedback-worker-originals", extra=suffix)
            self.assertIsNone(original["original"]["receipt"]); self.assertIsNone(original["worker_receipt"])
            h.clock.advance(.2); request("/publications", extra="&consumer_id=worker")
            delivered = request("/feedback-worker-originals", extra=suffix)
            self.assertEqual(delivered["original"]["receipt"]["received_at"], .2); self.assertIsNone(delivered["worker_receipt"])
            with self.assertRaises(HTTPError) as caught: request("/worker-commits", bootstrap, token="test-owner-read")
            self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(HTTPError) as caught: request("/feedback-worker-originals", token="test-owner-read", extra=suffix)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close(); h.close()
            self.assertFalse(thread.is_alive())


if __name__ == "__main__": unittest.main()
