"""Actual loopback HTTP ingress; synthetic backend never invokes a model."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest, RequestConflict
from home_shared.gateway import Gateway, GatewayHTTPServer, jpeg_dimensions
from home_shared.legacy_chat import LegacyChatPolicy, LegacyChatResult
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture


class ChatBackend:
    policy = LegacyChatPolicy(10000, 1, 640*360, 10000, 10000, frozenset({"tools"}))

    def __init__(self, controller):
        self.controller = controller
        self.calls = []
        self.fail = False
        self.on_call = None

    def execute(self, request, deadline, on_event):
        record = self.controller.ledger.get(request.ledger_id)
        assert record.state == "running" and record.reservation
        self.calls.append(request)
        if self.on_call:
            self.on_call(request)
        if self.fail:
            raise ConnectionError("private data")
        if request.stream:
            on_event(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]}}]}\n\n')
            on_event(b'data: [DONE]\n\n')
        return LegacyChatResult(200, "text/event-stream" if request.stream else "application/json",
                                b"" if request.stream else b'{"choices":[{"message":{"content":"hello"}}]}',
                                (record.engine_epoch, request.ledger_id, "finished"))


def chat(stream=False):
    return {"model": "fake-model", "messages": [{"role": "user", "content": "private prompt"}],
            "max_tokens": 32, "stream": stream}


class LegacyGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = AdmissionLedger(Path(self.temp.name)/"ledger.db")
        manifest = manifest_fixture()
        manifest.update(version=3, consumer_deadline_seconds={"fake-echo": 15, "fake-victoria": 15},
                        consumer_policy_sha256={"fake-echo": ChatBackend.policy.digest, "fake-victoria": "d"*64})
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest), FakeVerifier())
        self.controller.enable("epoch-1")
        self.chat_backend = ChatBackend(self.controller)
        self.snapshot_backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.snapshot_backend, legacy_backend=self.chat_backend,
                               image_validator=jpeg_dimensions)
        self.binding = binding_fixture("echo")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_common_priority_queue_and_running_before_execute(self):
        self.gateway.submit(request_fixture(now=time.time()), binding_fixture())
        request = self.gateway.submit_legacy(chat(), self.binding, "chat-1")
        self.gateway.dispatch_once()
        self.assertEqual(len(self.chat_backend.calls), 1)
        self.assertEqual(len(self.snapshot_backend.calls), 0)
        record, result = self.gateway.legacy_result(request)
        self.assertEqual(record.state, "terminal")
        self.assertEqual(json.loads(result.body)["choices"][0]["message"]["content"], "hello")
        self.gateway.dispatch_once()
        self.assertEqual(len(self.snapshot_backend.calls), 1)

    def test_dedup_conflict_restart_and_volatile_expiry(self):
        request = self.gateway.submit_legacy(chat(), self.binding, "same")
        self.gateway.dispatch_once()
        self.gateway.submit_legacy(chat(), self.binding, "same")
        self.assertIsNone(self.gateway.dispatch_once())
        changed = chat()
        changed["messages"][0]["content"] = "different"
        with self.assertRaises(RequestConflict):
            self.gateway.submit_legacy(changed, self.binding, "same")
        restarted = Gateway(self.controller, legacy_backend=self.chat_backend)
        recovered = restarted.submit_legacy(chat(), self.binding, "same")
        self.assertEqual(recovered.request_deadline, request.request_deadline)
        self.assertIsNone(restarted.dispatch_once())
        self.assertIsNone(restarted.legacy_result(recovered)[1])
        self.gateway.clock = lambda: request.request_deadline + 1
        self.assertIsNone(self.gateway.legacy_result(request)[1])
        self.assertEqual(len(self.chat_backend.calls), 1)
        self.assertNotIn(b"private prompt", self.ledger.path.read_bytes())

    def test_backend_failure_retains_charge(self):
        self.chat_backend.fail = True
        request = self.gateway.submit_legacy(chat(), self.binding, "failure")
        self.gateway.dispatch_once()
        record, _ = self.gateway.legacy_result(request)
        self.assertEqual(record.state, "indeterminate")
        self.assertTrue(record.reservation)
        self.assertFalse(self.controller.readiness()["ready"])

    def test_disconnected_stream_retains_charge_and_is_not_retried(self):
        def disconnected(event):
            raise BrokenPipeError("client disconnected")
        request = self.gateway.submit_legacy(chat(True), self.binding, "disconnect", disconnected)
        self.gateway.dispatch_once()
        record, _ = self.gateway.legacy_result(request)
        self.assertEqual(record.state, "indeterminate")
        self.assertTrue(record.reservation)
        self.gateway.submit_legacy(chat(True), self.binding, "disconnect", disconnected)
        self.assertEqual(len(self.chat_backend.calls), 1)

    def with_http(self, action):
        credentials = {hashlib.sha256(b"echo-secret").hexdigest(): self.binding,
                       hashlib.sha256(b"victoria-secret").hexdigest(): binding_fixture()}
        with GatewayHTTPServer(("127.0.0.1", 0), self.gateway, credentials) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def call(path, payload=None, token="echo-secret", extra=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    headers = {"Authorization": "Bearer " + token, **(extra or {})}
                    body = None if payload is None else json.dumps(payload)
                    connection.request("GET" if body is None else "POST", path, body, headers)
                    response = connection.getresponse()
                    result = response.status, response.getheader("Content-Type"), response.read()
                    connection.close()
                    return result
                action(call)
            finally:
                server.shutdown()
                thread.join(3)

    def test_http_json_stream_models_and_identity(self):
        def action(call):
            status, _, body = call("/v1/chat/completions", chat(), extra={"X-Site-ID": "victoria"})
            self.assertEqual(status, 200)
            self.assertIn(b"hello", body)
            self.assertEqual(self.chat_backend.calls[-1].site_id, "echo")
            status, kind, body = call("/v1/chat/completions", chat(True))
            self.assertEqual((status, kind), (200, "text/event-stream"))
            self.assertIn(b"tool_calls", body)
            self.assertTrue(body.endswith(b"data: [DONE]\n\n"))
            self.assertEqual(call("/v1/models")[0], 200)
            self.assertEqual(call("/v1/chat/completions", chat(), token="wrong")[0], 401)
            self.assertEqual(call("/v1/chat/completions", chat(), token="victoria-secret")[0], 401)
            self.assertEqual(call("/v1/completions", chat())[0], 503)
        self.with_http(action)

    def test_http_default_closed_and_conflicting_duplicate(self):
        def action(call):
            headers = {"Idempotency-Key": "recorded"}
            self.assertEqual(call("/v1/chat/completions", chat(), extra=headers)[0], 200)
            changed = chat()
            changed["max_tokens"] = 33
            self.assertEqual(call("/v1/chat/completions", changed, extra=headers)[0], 409)
            self.gateway.legacy_backend = None
            self.assertEqual(call("/v1/chat/completions", chat())[0], 503)
            self.assertEqual(call("/v1/models")[0], 503)
        self.with_http(action)

    def test_sse_first_event_arrives_before_backend_completion(self):
        released = threading.Event()
        first = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        original = self.chat_backend.execute
        def execute(request, deadline, on_event):
            on_event(first)
            if not released.wait(2):
                raise TimeoutError()
            return original(request, deadline, on_event)
        self.chat_backend.execute = execute
        credentials = {hashlib.sha256(b"echo-secret").hexdigest(): self.binding}
        with GatewayHTTPServer(("127.0.0.1", 0), self.gateway, credentials) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                connection.request("POST", "/v1/chat/completions", json.dumps(chat(True)),
                                   {"Authorization": "Bearer echo-secret"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.readline(), first.splitlines(keepends=True)[0])
                self.assertFalse(released.is_set())
                released.set()
                self.assertIn(b"[DONE]", response.read())
            finally:
                released.set()
                connection.close()
                server.shutdown()
                thread.join(3)

    def test_verified_upstream_error_preserves_status_and_exact_json_for_stream_and_json(self):
        body = b'{ "error" : {"message":"bounded rejection"} }'
        def execute(request, deadline, callback):
            record = self.ledger.get(request.ledger_id)
            return LegacyChatResult(400, "application/json", body,
                                    (record.engine_epoch, request.ledger_id, "finished"))
        self.chat_backend.execute = execute
        def action(call):
            for stream in (False, True):
                status, kind, received = call("/v1/chat/completions", chat(stream))
                self.assertEqual((status, kind, received), (400, "application/json", body))
                self.assertTrue(self.controller.readiness()["ready"])
        self.with_http(action)

    def test_late_terminal_evidence_reconciles_without_output_or_readmission(self):
        released = threading.Event()
        original = self.chat_backend.execute
        def execute(request, deadline, callback):
            result = original(request, deadline, callback)
            released.wait(2)
            return result
        self.chat_backend.execute = execute
        request = self.gateway.submit_legacy(chat(), self.binding, "late")
        # Advance gateway wait clock only, retaining the admitted ledger deadline.
        self.gateway.clock = lambda: request.request_deadline
        try:
            self.gateway.dispatch_once()
            self.assertEqual(self.ledger.get(request.ledger_id).state, "indeterminate")
            released.set()
            until = time.monotonic() + 2
            count = 0
            while not count and time.monotonic() < until:
                count = self.gateway.reconcile_late_completions()
                if not count:
                    time.sleep(0.01)
            self.assertEqual(count, 1)
            self.assertFalse(self.controller.readiness()["ready"])
            self.assertIsNone(self.gateway.legacy_result(request)[1])
        finally:
            released.set()


if __name__ == "__main__":
    unittest.main()
