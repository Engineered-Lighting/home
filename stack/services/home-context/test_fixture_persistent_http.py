"""Bounded real-loopback tests; the small fake journal tests transport only."""
import copy
import http.client
import json
import socket
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from fixture_persistent_http import (FIXTURE_TRANSPORT, BoundedPersistentFixtureServer,
                                     PersistentFixtureHandlerMixin, validate_fixture_transport)
from journal import JournalError, REFERENCE_PROFILE
from server import Handler


class FramingJournal:
    """Explicit authored transport fixture, not an authenticated context proof."""
    profile = REFERENCE_PROFILE

    def __init__(self):
        self.lock = threading.RLock()
        self.limits = SimpleNamespace(max_body_bytes=65536, max_worker_commit_bytes=262144,
                                      max_response_bytes=1048576)
        self.auth_calls = 0
        self.actions = 0
        self.records = {}
        self.block_entered = threading.Event()
        self.block_release = threading.Event()

    def authenticate(self, token):
        self.auth_calls += 1
        if token not in ("one", "two"):
            raise JournalError("unauthorized", "fixture credential required", 401)
        return SimpleNamespace(principal_id=token, operations=frozenset())


class FramingHandler(PersistentFixtureHandlerMixin, Handler):
    def _get(self):
        grant = self._grant()
        self.server.journal.actions += 1
        if self.path == "/blocked":
            self.server.journal.block_entered.set()
            self.server.journal.block_release.wait(5)
        if self.path == "/large":
            return {"authored_transport_padding": "x" * (1024 * 1024 - 100)}
        return {"identity": grant.principal_id, "raw_post": self._request_utf8}

    def _post(self):
        self._grant()
        body = self._body()
        if set(body) != {"id", "value"}:
            raise JournalError("invalid_body", "closed authored transport body required")
        prior = self.server.journal.records.get(body["id"])
        if prior is not None and prior != body:
            raise JournalError("identity_conflict", "authored fixture identity conflict", 409)
        self.server.journal.records[body["id"]] = copy.deepcopy(body)
        return {"original": body, "count": len(self.server.journal.records)}


def wait_for(predicate, seconds=1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("bounded condition did not occur")


class FixturePersistentHttpTests(unittest.TestCase):
    def setUp(self):
        self.journal = FramingJournal()
        self.server = BoundedPersistentFixtureServer(("127.0.0.1", 0), self.journal,
                                                     fixture_transport=copy.deepcopy(FIXTURE_TRANSPORT))
        self.server.RequestHandlerClass = FramingHandler
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=False)
        self.serving.start()
        self.clients = []

    def tearDown(self):
        self.journal.block_release.set()
        for client in self.clients:
            client.close()
        result = self.server.close_transport()
        self.serving.join(2)
        self.assertTrue(result["join_complete"])
        self.assertFalse(self.serving.is_alive())

    def client(self):
        value = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.clients.append(value)
        return value

    def raw(self):
        value = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5)
        self.clients.append(value)
        return value

    def request(self, client, method="GET", body=None, token="one", path="/identity"):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        client.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = client.getresponse()
        raw = response.read()
        return response.status, json.loads(raw), response.getheader("Connection")

    def test_exact_configuration_and_profile_only(self):
        self.assertEqual(validate_fixture_transport(copy.deepcopy(FIXTURE_TRANSPORT)), FIXTURE_TRANSPORT)
        for key in FIXTURE_TRANSPORT:
            value = copy.deepcopy(FIXTURE_TRANSPORT)
            value[key] = False if isinstance(value[key], int) else "other"
            with self.assertRaises(ValueError): validate_fixture_transport(value)
        value = copy.deepcopy(FIXTURE_TRANSPORT); value["extra"] = 1
        with self.assertRaises(ValueError): validate_fixture_transport(value)
        wrong = FramingJournal(); wrong.profile = "legacy"
        with self.assertRaises(ValueError):
            BoundedPersistentFixtureServer(("127.0.0.1", 0), wrong, fixture_transport=FIXTURE_TRANSPORT)
        with self.assertRaises(ValueError):
            BoundedPersistentFixtureServer(("0.0.0.0", 0), self.journal, fixture_transport=FIXTURE_TRANSPORT)

    def test_many_requests_one_socket_and_fresh_authentication(self):
        client = self.client()
        for n in range(1000):
            status, body, connection = self.request(client, token="one" if n % 2 == 0 else "two")
            self.assertEqual(status, 200)
            self.assertEqual(body["identity"], "one" if n % 2 == 0 else "two")
            self.assertEqual(connection, "keep-alive")
        counters = self.server.transport_metrics()
        self.assertEqual(counters["accepted_connections"], 1)
        self.assertEqual(counters["requests_started"], 1000)
        self.assertEqual(counters["reused_requests"], 999)
        self.assertEqual(self.journal.auth_calls, 1000)
        status, _, connection = self.request(client, token="bad")
        self.assertEqual((status, connection), (401, "close"))
        self.assertEqual(self.journal.actions, 1000)

    def test_post_preflight_reauth_exact_identity_and_request_reset(self):
        client = self.client(); body = {"id": "authored-fixture:1", "value": 7}
        first = self.request(client, "POST", body)
        self.assertEqual(first, self.request(client, "POST", body))
        self.assertEqual(self.journal.auth_calls, 4)  # preflight + dispatch every POST
        status, body, _ = self.request(client)
        self.assertEqual((status, body["raw_post"]), (200, None))
        status, _, connection = self.request(client, "POST", {"id": "authored-fixture:1", "value": 8})
        self.assertEqual((status, connection), (409, "close"))
        self.assertEqual(len(self.journal.records), 1)

    def test_request_cap_closes_without_new_admission_or_retry(self):
        client = self.client()
        for n in range(FIXTURE_TRANSPORT["requests_per_connection"]):
            status, _, connection = self.request(client)
            self.assertEqual(status, 200)
            self.assertEqual(connection, "close" if n == 4095 else "keep-alive")
        self.assertEqual(self.server.transport_metrics()["accepted_connections"], 1)
        self.assertEqual(self.journal.actions, 4096)

    def test_pipeline_and_get_body_are_refused_without_second_action(self):
        messages = (
            b"GET /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\nContent-Length: 1\r\n\r\nx",
            b"GET /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n\r\nGET /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer two\r\n\r\n",
        )
        for message in messages:
            sock = self.raw(); sock.sendall(message)
            response = http.client.HTTPResponse(sock); response.begin()
            self.assertEqual(response.status, 400)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
        self.assertEqual(self.journal.actions, 0)

    def test_unread_unauthorized_body_malformed_framing_and_unsupported_close(self):
        messages = (
            (401, b"POST /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer bad\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n"),
            (400, b"POST /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"),
            (400, b"POST /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"),
            (501, b"PUT /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n\r\n"),
            (417, b"POST /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\nExpect: 100-continue\r\nContent-Length: 2\r\n\r\n"),
        )
        for expected, message in messages:
            sock = self.raw(); sock.sendall(message)
            response = http.client.HTTPResponse(sock); response.begin()
            self.assertEqual(response.status, expected)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
        self.assertEqual(self.journal.records, {})

    def test_partial_body_is_outside_journal_lock_and_times_out(self):
        sock = self.raw()
        sock.sendall(b"POST /identity HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{")
        wait_for(lambda: self.server.transport_metrics()["requests_started"] == 1)
        started = time.monotonic()
        self.assertEqual(self.request(self.client())[0], 200)
        self.assertLess(time.monotonic() - started, 1)
        wait_for(lambda: self.server.transport_metrics()["body_read_timeouts"] == 1, 3.5)
        self.assertEqual(self.journal.records, {})

    def test_fixed_workers_queue_refusal_and_bounded_idle_shutdown(self):
        for _ in range(4): self.raw()
        wait_for(lambda: self.server.transport_metrics()["active_connections"] == 4)
        for _ in range(2): self.raw()
        wait_for(lambda: self.server.transport_metrics()["queued_connections"] == 2)
        self.raw()
        wait_for(lambda: self.server.transport_metrics()["queue_refusals"] == 1)
        metrics = self.server.transport_metrics()
        self.assertEqual((metrics["peak_active_connections"], metrics["peak_queued_connections"]), (4, 2))
        self.assertEqual(metrics["worker_threads_alive"], 4)
        result = self.server.close_transport()
        self.assertTrue(result["join_complete"], result)
        self.assertEqual((result["active_connections"], result["queued_connections"]), (0, 0))
        self.assertGreaterEqual(result["drained_connections"], 2)
        self.assertLess(result["elapsed_ms"], 2000)

    def test_join_failure_is_explicit_and_does_not_claim_cleanup(self):
        sock = self.raw()
        sock.sendall(b"GET /blocked HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n\r\n")
        self.assertTrue(self.journal.block_entered.wait(1))
        result = self.server.close_transport()
        self.assertFalse(result["join_complete"])
        self.assertGreater(result["worker_threads_alive"], 0)
        self.assertLess(result["elapsed_ms"], 2300)
        self.journal.block_release.set()
        self.assertTrue(self.server.close_transport()["join_complete"])

    def test_client_connection_close_is_respected(self):
        client = self.client()
        client.request("GET", "/identity", headers={"Authorization": "Bearer one", "Connection": "close"})
        response = client.getresponse()
        self.assertEqual(response.getheader("Connection"), "close")
        response.read()

    def test_idle_timeout_is_counted_without_an_action(self):
        self.raw()
        wait_for(lambda: self.server.transport_metrics()["active_connections"] == 1)
        wait_for(lambda: self.server.transport_metrics()["idle_closures"] == 1, 3.5)
        self.assertEqual(self.journal.actions, 0)

    def test_shutdown_interrupts_response_backpressure_within_grace(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5); sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        sock.connect(("127.0.0.1", self.server.server_port)); self.clients.append(sock)
        sock.sendall(b"GET /large HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer one\r\n\r\n")
        wait_for(lambda: self.journal.actions == 1)
        self.assertTrue(self.server.close_transport()["join_complete"])

    def test_stop_during_startup_handshake_cannot_lose_shutdown(self):
        server = BoundedPersistentFixtureServer(("127.0.0.1", 0), FramingJournal(), fixture_transport=FIXTURE_TRANSPORT)
        entered, release, observed_unstarted = threading.Event(), threading.Event(), threading.Event()
        class GatedStart(threading.Event):
            def set(self):
                entered.set()
                if not release.wait(1): raise AssertionError("bounded startup gate timed out")
                super().set()
            def is_set(self):
                value = super().is_set()
                if threading.current_thread() is server._coordinator and not value:
                    observed_unstarted.set()
                return value
        server._serve_started = GatedStart()
        serving = threading.Thread(target=server.serve_forever, daemon=False)
        stopping = threading.Thread(target=server.request_stop, daemon=False)
        try:
            serving.start()
            self.assertTrue(entered.wait(1))
            stopping.start()
            stopping.join(.03)
            if observed_unstarted.wait(.15):
                server._coordinator.join(.2)
            release.set()
            stopping.join(1)
            serving.join(1)
            self.assertFalse(serving.is_alive(), "startup stop was lost before serve_started")
        finally:
            release.set()
            if serving.is_alive():
                rescue = threading.Thread(target=server.shutdown, daemon=False)
                rescue.start(); rescue.join(1); serving.join(1)
            self.assertTrue(server.close_transport()["join_complete"])


class ReferenceJournalPersistentHttpTests(unittest.TestCase):
    """Actual public R71 service paths, with authored contract input fixtures."""
    @classmethod
    def setUpClass(cls):
        import test_adaptive_feedback_reference_context_service as reference
        cls.reference = reference
        reference.ReferenceFeedbackServiceTests.setUpClass()

    def setUp(self):
        self.h = self.reference.ReferenceFeedbackServiceTests("test_http_canonical_compact_admission_retry_and_checkpoint")
        self.h.setUp()
        self.server = BoundedPersistentFixtureServer(("127.0.0.1", 0), self.h.j,
                                                     fixture_transport=copy.deepcopy(FIXTURE_TRANSPORT))
        from fixture_persistent_http import PersistentFixtureHandler
        self.server.RequestHandlerClass = PersistentFixtureHandler
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=False)
        self.serving.start()
        self.client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)

    def tearDown(self):
        self.client.close()
        report = self.server.close_transport()
        self.serving.join(2)
        self.assertTrue(report["join_complete"])
        self.h.tearDown()

    def request(self, route, body=None, token="test-worker", client=None, query=None):
        from journal import PREFIX, canonical
        path = PREFIX + route + ("?" + urlencode(query) if query else "")
        (client or self.client).request("POST" if body is not None else "GET", path,
            body=canonical(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = (client or self.client).getresponse(); raw = response.read()
        return response.status, json.loads(raw), raw

    def scope(self, **extra):
        return {"site_id": self.h.identity["site_id"], "room_id": self.h.identity["room_id"], **extra}

    def test_actual_compact_request_retry_and_checkpoint_parity(self):
        commit = self.h.bootstrap()
        first = self.request("/worker-commits", commit)
        self.assertEqual(first[0], 200)
        self.assertEqual(first, self.request("/worker-commits", commit))
        state = self.request("/worker-commits/worker", query=self.scope())
        self.assertEqual(state[1]["schema"], "home-context-worker-checkpoint-response-6")
        self.assertEqual(state[1]["checkpoint"], commit["checkpoint"])
        self.assertEqual(self.server.transport_metrics()["accepted_connections"], 1)

    def test_actual_service_delivery_and_worker_processing_FIRST_stay_distinct(self):
        from journal import digest
        preference = self.h.preference(); preference.update(available_at=40, valid_until=120)
        status, receipt, _ = self.request("/preference-snapshots", preference, token="test-owner")
        self.assertEqual(status, 200)
        commit = self.h.bootstrap()  # Authenticated cut delivers the future input at0.
        publication = json.loads(self.h.j.db.execute("SELECT body FROM publications WHERE cursor=?", (receipt["cursor"],)).fetchone()[0])
        descriptor = {"source_ref": self.h.j.dynamic._source_ref(publication), "received_at": 0, "eligible_at": 40}
        descriptor["digest"] = digest(descriptor)
        cp, cut = commit["checkpoint"], commit["input_cut"]
        cp.update(input_cursor=receipt["cursor"], dependency_receipts=[descriptor], pending_inputs=[{"dependency_digest": descriptor["digest"]}],
                  receipts=[{"ref": descriptor["source_ref"]["publication_ref"], "received_at": 0, "eligible_at": 40}])
        cp["revisions"]["rows"] = [{"dependency_digest": descriptor["digest"]}]
        cut.update(receipts=copy.deepcopy(cp["receipts"]), eligible_refs=[], buffered_refs=[descriptor["source_ref"]["publication_ref"]])
        cut["digest"] = digest({k: v for k, v in cut.items() if k != "digest"})
        query = self.scope(cursor=receipt["cursor"], consumer_id="worker")
        before = self.request("/feedback-worker-originals", query=query)
        self.assertEqual(before[0], 200)
        self.assertIsNone(before[1]["worker_receipt"])
        self.assertEqual(before[1]["original"]["receipt"]["received_at"], 0)
        self.assertEqual(self.request("/worker-commits", commit)[0], 200)
        after = self.request("/feedback-worker-originals", query=query)[1]
        expected = {"consumer_id": "worker", "received_at": 0, "eligible_at": 40}
        self.assertEqual(after["worker_receipt"], {**expected, "digest": digest(expected)})
        with self.h.j.lock: self.h.now = 1
        later = self.request("/feedback-worker-originals", query=query)[1]
        self.assertEqual(later["worker_receipt"], after["worker_receipt"])
        self.assertEqual(later["original"]["receipt"], after["original"]["receipt"])
        self.assertEqual(self.request("/feedback-worker-originals", token="test-owner-read", query=query)[0], 403)

    def test_old_close_handler_returns_identical_public_response_bytes(self):
        from server import LocalServer
        old = LocalServer(("127.0.0.1", 0), self.h.j)
        serving = threading.Thread(target=old.serve_forever, daemon=False); serving.start()
        client = http.client.HTTPConnection("127.0.0.1", old.server_port, timeout=5)
        try:
            self.assertEqual(self.request("/worker-commits/worker", query=self.scope()),
                             self.request("/worker-commits/worker", query=self.scope(), client=client))
        finally:
            client.close(); old.shutdown(); serving.join(2); old.server_close()

    def test_late_pipeline_preserves_real_admission_and_exact_receipt_recovery(self):
        from fixture_persistent_http import PersistentFixtureHandler
        from journal import PREFIX, canonical
        committed, release = threading.Event(), threading.Event()
        class GatedMutation(PersistentFixtureHandler):
            def _post(self):
                result = super()._post()
                committed.set()
                if not release.wait(2): raise AssertionError("bounded mutation gate timed out")
                return result
        self.server.RequestHandlerClass = GatedMutation
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5)
        preference = self.h.preference(); wire = canonical(preference).encode()
        head = ("POST " + PREFIX + "/preference-snapshots HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer test-owner\r\nContent-Type: application/json\r\nContent-Length: " + str(len(wire)) + "\r\n\r\n").encode()
        try:
            sock.sendall(head + wire)
            self.assertTrue(committed.wait(1))
            late = ("GET " + PREFIX + "/snapshot?" + urlencode(self.scope(consumer_id="worker")) + " HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer test-worker\r\n\r\n").encode()
            sock.sendall(late); release.set()
            response = http.client.HTTPResponse(sock); response.begin()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Connection"), "close")
            receipt = json.loads(response.read())
            self.assertEqual(receipt["status"], "accepted")
            with self.h.j.lock:
                self.assertEqual(self.h.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 1)
                self.assertEqual(self.h.j.db.execute("SELECT count(*) FROM feedback_consumer_receipts").fetchone()[0], 0)
            self.assertEqual(self.server.transport_metrics()["late_pipeline_closures"], 1)
            self.assertEqual(self.request("/preference-snapshots", preference, token="test-owner")[1], receipt)
            original = self.request("/feedback-originals", query=self.scope(cursor=receipt["cursor"], consumer_id="worker"))[1]
            self.assertEqual(original["publication"]["payload"], preference)
            self.assertEqual(original["publication"]["committed_at"], receipt["committed_at"])
            with self.h.j.lock:
                self.assertEqual(self.h.j.db.execute("SELECT count(*) FROM publications").fetchone()[0], 1)
        finally:
            release.set(); sock.close()


if __name__ == "__main__":
    unittest.main()
