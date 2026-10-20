"""Controlled Starlette/protocol stubs; NOT real ASGI/vLLM acceptance.

Exercise the installed endpoint function, strict JSON, real dispatch signatures
and _APIHook purpose validation without claiming HTTP server/parser integration.
"""
import asyncio
import hashlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.dispatch_token import DispatchTokenAuthority
from home_shared.engine_receipts import ReceiptUnavailable
from home_shared.vllm_api_hook import HEADER, _APIHook, install_input_route
from home_shared.vllm_input_authority import MAX_BODY


class Headers:
    def __init__(self, values):
        self.values = values

    def getlist(self, name):
        return self.values.get(name, [])


class ReplayRequest:
    def __init__(self, scope, receive):
        self.scope = scope
        self.headers = scope["fixture_headers"]
        self.receive = receive

    async def body(self):
        event = await self.receive()
        assert event["type"] == "http.request" and event["more_body"] is False
        return event["body"]


class Response:
    def __init__(self, content, status_code=200):
        self.content, self.status_code = content, status_code


class Protocol:
    @staticmethod
    def model_validate(value):
        if type(value) is not dict or value.get("model") != "fixture-model":
            raise ValueError("fixture protocol validation")
        return SimpleNamespace(use_beam_search=value.get("use_beam_search", False))


class Hook:
    _binding = _APIHook._binding

    def __init__(self):
        self.manifest = SimpleNamespace(max_deadline_seconds=1)
        self.authority = DispatchTokenAuthority("a" * 64, "epoch", b"k" * 32,
                                                max_deadline_seconds=15, clock=lambda: 1000)
        self.calls = []

    async def prepare(self, request, raw):
        binding = await self._binding(request, raw, "prepare")
        self.calls.append((binding, await raw.body()))
        return {"upper_bound_tokens": 42}


class InputRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        modules = {}
        for name, attributes in (
            ("starlette.requests", {"Request": ReplayRequest}),
            ("starlette.responses", {"JSONResponse": Response}),
            ("vllm.entrypoints.openai.chat_completion.protocol", {"ChatCompletionRequest": Protocol}),
        ):
            module = ModuleType(name)
            module.__dict__.update(attributes)
            modules[name] = module
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.hook = Hook()
        self.app = SimpleNamespace(routes=[])

        def add_route(path, endpoint, methods):
            self.app.routes.append(SimpleNamespace(path=path, endpoint=endpoint, methods=methods))
        self.app.add_route = add_route
        install_input_route(self.app, self.hook)
        self.endpoint = self.app.routes[0].endpoint

    def raw(self, body=b'{"model":"fixture-model"}', *, purpose="prepare", headers=None, chunks=None):
        token = self.hook.authority.issue("ledger", "b" * 64, hashlib.sha256(body).hexdigest(), 1015,
            purpose=purpose, input_bound_tokens=42 if purpose == "execute" else None)
        values = {"content-length": [str(len(body))], HEADER: [token], "x-request-id": ["ledger"]}
        values.update(headers or {})
        headers = Headers(values)
        state = SimpleNamespace(reads=0)

        async def stream():
            for chunk in ([body] if chunks is None else chunks):
                state.reads += 1
                yield chunk
        return SimpleNamespace(headers=headers, scope={"fixture_headers": headers}, stream=stream, state=state)

    async def test_positive_exact_body_replay_and_post_registration(self):
        body = b'{"model": "fixture-model"}'
        response = await self.endpoint(self.raw(body, chunks=[body[:7], body[7:]]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, {"upper_bound_tokens": 42})
        self.assertEqual(self.hook.calls[0][1], body)
        self.assertEqual(self.hook.calls[0][0].purpose, "prepare")
        self.assertEqual(self.app.routes[0].methods, ["POST"])

    async def test_invalid_headers_rejected_before_stream_consumption(self):
        for headers in ({"content-length": []}, {"content-length": ["1", "1"]},
                        {"content-length": ["0"]}, {"content-length": ["-1"]},
                        {"content-length": [" 20"]}, {"content-length": ["١"]},
                        {"content-length": [str(MAX_BODY + 1)]},
                        {"transfer-encoding": ["chunked"]}, {"content-encoding": ["gzip"]}):
            with self.subTest(headers=headers):
                raw = self.raw(headers=headers)
                response = await self.endpoint(raw)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(raw.state.reads, 0)
        self.assertFalse(self.hook.calls)

    async def test_declared_length_mismatch_does_not_reach_preparer(self):
        for chunks in ([b"{"], [b"x" * 100]):
            response = await self.endpoint(self.raw(chunks=chunks))
            self.assertEqual(response.status_code, 400)
        self.assertFalse(self.hook.calls)

    async def test_oversized_chunk_is_not_copied_before_rejection(self):
        copied = []

        class TrackedBuffer(bytearray):
            def extend(self, chunk):
                copied.append(len(chunk))
                return super().extend(chunk)
        with patch("home_shared.vllm_api_hook.bytearray", TrackedBuffer, create=True):
            response = await self.endpoint(self.raw(headers={"content-length": ["1"]}, chunks=[b"x" * 4096]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(copied, [])

    async def test_strict_json_failures_rejected_without_preparation(self):
        for body in (b'{"model":"fixture-model","model":"fixture-model"}', b'{"model":NaN}',
                     b'{"model":Infinity}', b"\xff", b"{} trailing", b"null", b"[]"):
            with self.subTest(body=body):
                response = await self.endpoint(self.raw(body))
                self.assertEqual(response.status_code, 400)
        self.assertFalse(self.hook.calls)

    async def test_excessive_json_nesting_returns_rejection(self):
        body = b"[" * 2000 + b"0" + b"]" * 2000
        response = await self.endpoint(self.raw(body))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.hook.calls)

    async def test_execute_signature_wrong_body_and_duplicate_identity_rejected(self):
        changed = b'{ "model":"fixture-model"}'
        duplicate = self.raw()
        duplicate.headers.values[HEADER] *= 2
        requests = [self.raw(purpose="execute"),
                    self.raw(chunks=[changed], headers={"content-length": [str(len(changed))]}), duplicate,
                    self.raw(headers={HEADER: []}), self.raw(headers={"x-request-id": ["ledger", "ledger"]}),
                    self.raw(headers={"x-request-id": ["different"]}), self.raw(headers={HEADER: ["invalid"]})]
        for raw in requests:
            with self.subTest(headers=raw.headers.values):
                self.assertEqual((await self.endpoint(raw)).status_code, 400)
        self.assertFalse(self.hook.calls)

    async def test_body_stream_timeout_rejected(self):
        self.hook.manifest.max_deadline_seconds = 0.01
        raw = self.raw()

        async def slow_stream():
            await asyncio.Event().wait()
            yield b"never"
        raw.stream = slow_stream
        response = await self.endpoint(raw)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.hook.calls)

    def test_duplicate_route_refused_without_mutation(self):
        with self.assertRaises(ReceiptUnavailable):
            install_input_route(self.app, self.hook)
        self.assertEqual(len(self.app.routes), 1)


if __name__ == "__main__":
    unittest.main()
