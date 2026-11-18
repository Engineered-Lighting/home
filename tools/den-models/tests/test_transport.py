import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.backend import worker_main
from den_models.contracts import ContractError, canonical
from den_models.resources import ControlState
from den_models.server import bearer_token, handler_for
import test_runtime as fixtures

DIGEST = fixtures.DIGEST
request = fixtures.request


class WireSocket:
    """Byte-level HTTP stream; no socket privilege or live listener required."""
    def __init__(self, data):
        self.input, self.output = io.BytesIO(data), io.BytesIO()
    def makefile(self, *args):
        return self.input
    def sendall(self, data):
        self.output.write(data)
    def settimeout(self, timeout):
        pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.RuntimeTests()
        fixture.setUp()
        self.fixture, self.runtime = fixture, fixture.runtime
        self.runtime.refresh_admission()
        self.token = "e" * 64
        self.handler = handler_for(self.runtime, self.token)

    def call(self, method, path, value=None, *, authenticated=True):
        data = None if value is None else canonical(value).encode()
        return self.raw_call(method, path, data, authenticated=authenticated)

    def raw_call(self, method, path, data, *, authenticated=True):
        headers = {"Host": "127.0.0.1", "Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Length"] = str(len(data))
        head = f"{method} {path} HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        stream = WireSocket(head.encode() + (data or b""))
        self.handler(stream, ("127.0.0.1", 1234), object())
        response_head, response_body = stream.output.getvalue().split(b"\r\n\r\n", 1)
        return int(response_head.split()[1]), json.loads(response_body)

    def test_auth_status_async_submit_cancel_and_bounded_routes(self):
        self.assertEqual(self.call("GET", "/healthz", authenticated=False)[0], 401)
        self.assertEqual(self.call("GET", "/healthz")[1]["admission_scope"], "bounded_trial")
        status, job = self.call("POST", "/v1/intent-jobs", request())
        self.assertEqual((status, job["status"]), (202, "queued"))
        self.runtime.tick()
        self.assertEqual(self.call("GET", "/v1/intent-jobs/job-1")[1]["status"], "running")
        self.assertEqual(self.call("DELETE", "/v1/intent-jobs/job-1")[1]["status"], "stopping")
        self.assertEqual(self.call("GET", "/v1/intent-jobs/job-1")[1]["status"], "stopping")
        self.assertTrue(self.call("GET", "/healthz")[1]["execution_lease_held"])
        self.assertEqual(self.call("GET", "/v1/intent-jobs/../../private")[0], 404)

    def test_oversize_body_and_duplicate_json_refused(self):
        for data in (b"x" * (64 * 1024 + 1), b'{"a":1,"a":2}'):
            self.assertEqual(self.raw_call("POST", "/v1/intent-jobs", data)[0], 400)

    def test_control_freshness_and_private_bearer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            state = {"schema": "den.model-control/v1", "observed_at": 100, "qwen_busy": False,
                     "unallocated_reserve_bytes": 0, "dependencies": []}
            path.write_text(canonical(state))
            # A manual idle JSON receipt is no longer an admission source.
            with self.assertRaises(ContractError):
                ControlState(path).read(101)
            for stamp in (98, 102):
                state["observed_at"] = stamp
                path.write_text(canonical(state))
                with self.assertRaises(ContractError):
                    ControlState(path).read(101)
            token = Path(directory) / "token"
            token.write_text("a" * 64)
            token.chmod(0o600)
            self.assertEqual(bearer_token(token), "a" * 64)
            token.write_text("not-a-credential")
            with self.assertRaises(ContractError):
                bearer_token(token)


class Connection:
    def __init__(self, messages, events):
        self.messages, self.events, self.sent, self.closed = messages, events, [], False
    def recv(self):
        return self.messages.pop(0)
    def send(self, value):
        self.events.append("send")
        self.sent.append(value)
    def close(self):
        self.closed = True


class NativeProtocolTests(unittest.TestCase):
    def test_child_proves_sync_before_sending_native_completion(self):
        events = []
        class Backend:
            torch = object()
            receipt_digest = DIGEST
            def __init__(self, config, profile):
                pass
            def warmup(self):
                events.append("warmup")
            def infer(self, request):
                events.append("infer")
                return {"intent": "unclear"}
            def synchronize(self):
                events.append("synchronize")
        connection = Connection([{"command": "warmup", "request": {"request_id": "warmup"}},
                                 {"command": "infer", "request": {"request_id": "job"}},
                                 {"command": "stop"}], events)
        with patch("den_models.backend.VjepaBackend", Backend):
            worker_main(connection, {}, {})
        self.assertEqual(events, ["warmup", "synchronize", "send", "infer", "synchronize", "send"])
        self.assertEqual([m["kind"] for m in connection.sent], ["ready", "complete"])
        self.assertTrue(all(m["proof"] == "cuda_synchronized" for m in connection.sent))
        self.assertTrue(connection.closed)

    def test_failed_native_synchronization_never_emits_false_terminal_proof(self):
        events = []
        class Backend:
            torch = object()
            def __init__(self, config, profile):
                pass
            def infer(self, request):
                raise RuntimeError("native failed")
            def synchronize(self):
                raise RuntimeError("context still unknown")
        connection = Connection([{"command": "infer", "request": {"request_id": "job"}}], events)
        with patch("den_models.backend.VjepaBackend", Backend):
            with self.assertRaises(RuntimeError):
                worker_main(connection, {}, {})
        self.assertEqual(connection.sent, [])
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
