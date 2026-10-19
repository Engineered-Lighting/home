"""Real signatures/receipt store with controlled API/engine RPC fixtures."""
import asyncio
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from home_shared.admission import ResourceManifest
from home_shared.dispatch_token import DispatchTokenAuthority, DispatchTokenError
from home_shared.engine_receipts import EngineReceiptStore, ReceiptUnavailable
from home_shared.vllm_api_hook import _APIHook, _CURRENT, HEADER
from test_admission import manifest_fixture


class APIHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manifest = ResourceManifest.from_dict(manifest_fixture())
        self.now = 1000.
        self.authority = DispatchTokenAuthority(self.manifest.digest, 'epoch', b'k' * 32,
                                                max_deadline_seconds=15, clock=lambda: self.now)
        self.cleanup_authority = object()
        self.store = EngineReceiptStore(Path(self.directory.name) / 'engine.db',
            manifest_digest=self.manifest.digest, engine_id=self.manifest.engine_id,
            engine_epoch='epoch', model_revision=self.manifest.model_revision,
            cleanup_authority=self.cleanup_authority, create=True)
        self.addCleanup(self.store.close)
        self.terminal_gate = asyncio.Event()
        self.events = []
        self.closed = []
        self.children = 1
        self.prompt_tokens = [1, 2, 3]
        self.prompt_embeds = None
        async def utility(method, *args):
            self.events.append((method, args[0]))
            if method == 'shared_home_register':
                decision = self.store.register_for_dispatch(*args)
                return dict(registration_digest=decision.receipt.registration_digest,
                            dispatch_permitted=decision.dispatch_permitted)
            await self.terminal_gate.wait()
            receipt = self.store.lookup(args[0])
            return self.store.record_cleanup(receipt.ledger_id, receipt.request_digest,
                receipt.upstream_body_sha256, receipt.internal_ids,
                authority=self.cleanup_authority, evidence_sha256='e' * 64).cleanup_evidence_sha256
        async def add(request, prompt, parent, index, queue):
            self.events.append(('add', request.request_id))
        async def generate(prompt, params, request_id, **kwargs):
            try:
                internal = request_id + '-a1b2c3d4'
                parent = None if self.children == 1 else SimpleNamespace(
                    request_id=internal, external_req_id=request_id, n=self.children)
                for index in range(self.children):
                    identity = internal if parent is None else f'{index}_{internal}'
                    await self.client._add_request(SimpleNamespace(request_id=identity,
                        external_req_id=request_id, prompt_token_ids=self.prompt_tokens,
                        prompt_embeds=self.prompt_embeds, sampling_params=SimpleNamespace(max_tokens=2)),
                        None, parent, index, None)
                yield SimpleNamespace(finished=False, text='partial')
                yield SimpleNamespace(finished=True, text='final')
            finally:
                self.closed.append(request_id)
        self.client = SimpleNamespace(engine_core=SimpleNamespace(call_utility_async=utility),
                                      _add_request=add, generate=generate)
        async def create(request, raw):
            iterator = self.client.generate(None, None, 'chatcmpl-' + raw.identity)
            async def chunks():
                async for output in iterator:
                    yield output.text
            if request.stream:
                return chunks()
            return [item async for item in chunks()]
        self.serving = SimpleNamespace(engine_client=self.client, create_chat_completion=create)
        self.hook = _APIHook(self.serving, self.manifest, self.authority, lambda: self.now)
        self.hook.install()

    def raw(self, identity='request', body=b'{"model":"fake-model"}', *, purpose='execute', bound=3):
        token = self.authority.issue(identity, 'b' * 64, hashlib.sha256(body).hexdigest(), 1015.,
            purpose=purpose, input_bound_tokens=bound if purpose == 'execute' else None)
        headers = {HEADER: [token], 'x-request-id': [identity]}
        async def read():
            return body
        return SimpleNamespace(identity=identity, headers=SimpleNamespace(getlist=lambda name: headers.get(name, [])),
                               body=read, mutable_headers=headers)

    async def stream(self, raw=None):
        return await self.serving.create_chat_completion(SimpleNamespace(stream=True, use_beam_search=False),
                                                         raw or self.raw())

    async def test_stream_final_output_waits_for_durable_receipt(self):
        stream = await self.stream()
        self.assertIsNone(_CURRENT.get())
        self.assertEqual(await anext(stream), 'partial')
        self.assertIsNone(_CURRENT.get())
        self.assertEqual(self.events[0], ('shared_home_register', 'request'))
        self.assertEqual(self.events[1][0], 'add')
        final = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        self.assertFalse(final.done())
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.terminal_gate.set()
        self.assertEqual(await final, 'final')
        self.assertIsNotNone(self.store.lookup('request').cleanup_evidence_sha256)
        await stream.aclose()
        self.assertIsNone(_CURRENT.get())

    async def test_json_and_parallel_children_preserve_registration(self):
        self.children = 2
        self.terminal_gate.set()
        result = await self.serving.create_chat_completion(SimpleNamespace(stream=False, use_beam_search=False), self.raw())
        self.assertEqual(result, ['partial', 'final'])
        self.assertEqual(self.store.lookup('request').internal_ids,
                         ('0_chatcmpl-request-a1b2c3d4', '1_chatcmpl-request-a1b2c3d4'))
        self.assertEqual(sum(name == 'shared_home_register' for name, _ in self.events), 1)
        self.assertIsNone(_CURRENT.get())

    async def test_untrusted_or_replayed_binding_never_adds(self):
        raw = self.raw()
        raw.mutable_headers[HEADER] = ['not-a-signature']
        with self.assertRaises(DispatchTokenError):
            await self.stream(raw)
        self.assertFalse(self.events)
        self.terminal_gate.set()
        stream = await self.stream()
        self.assertEqual([part async for part in stream], ['partial', 'final'])
        add_count = sum(name == 'add' for name, _ in self.events)
        replay = await self.stream()
        with self.assertRaises(ReceiptUnavailable):
            await anext(replay)
        self.assertEqual(sum(name == 'add' for name, _ in self.events), add_count)

    async def test_lazy_stream_cannot_dispatch_after_expiry(self):
        stream = await self.stream()
        self.now = 1016.
        with self.assertRaises(ReceiptUnavailable):
            await anext(stream)
        self.assertFalse(self.events)

    async def test_disconnect_closes_original_without_terminal_receipt(self):
        stream = await self.stream()
        self.assertEqual(await anext(stream), 'partial')
        await stream.aclose()
        self.assertIn('chatcmpl-request', self.closed)
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.assertIsNone(_CURRENT.get())

    async def test_cancellation_during_terminal_wait_closes_original(self):
        stream = await self.stream()
        await anext(stream)
        final = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        final.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await final
        self.assertIn('chatcmpl-request', self.closed)
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.assertIsNone(_CURRENT.get())

    async def test_concurrent_stream_contexts_do_not_cross_bind(self):
        first, second = await self.stream(self.raw('one')), await self.stream(self.raw('two'))
        self.assertEqual(await anext(first), 'partial')
        self.assertEqual(await anext(second), 'partial')
        self.terminal_gate.set()
        self.assertEqual(await anext(first), 'final')
        self.assertEqual(await anext(second), 'final')
        await first.aclose()
        await second.aclose()
        self.assertEqual(self.store.lookup('one').internal_ids, ('chatcmpl-one-a1b2c3d4',))
        self.assertEqual(self.store.lookup('two').internal_ids, ('chatcmpl-two-a1b2c3d4',))

    async def test_preflight_json_and_stream_never_register_or_add(self):
        for streaming in (False, True):
            result = await self.hook.prepare(SimpleNamespace(stream=streaming, use_beam_search=False),
                                             self.raw(purpose='prepare'))
            self.assertEqual(result['upper_bound_tokens'], 3)
            self.assertEqual(result['valid_until'], 1015.)
            self.assertFalse(self.events)
            self.assertIsNone(_CURRENT.get())

    async def test_prepare_token_cannot_execute_and_execute_cannot_prepare(self):
        with self.assertRaises(ReceiptUnavailable):
            await self.stream(self.raw(purpose='prepare'))
        with self.assertRaises(ReceiptUnavailable):
            await self.hook.prepare(SimpleNamespace(stream=False, use_beam_search=False), self.raw())
        self.assertFalse(self.events)

    async def test_changed_preprocessing_and_embeddings_reject_before_registration(self):
        self.prompt_tokens = [1, 2, 3, 4]
        stream = await self.stream()
        with self.assertRaises(ReceiptUnavailable):
            await anext(stream)
        self.prompt_tokens = [1]
        self.prompt_embeds = object()
        with self.assertRaises(ReceiptUnavailable):
            await self.hook.prepare(SimpleNamespace(stream=False, use_beam_search=False),
                                    self.raw(purpose='prepare'))
        self.assertFalse(self.events)


if __name__ == '__main__':
    unittest.main()
