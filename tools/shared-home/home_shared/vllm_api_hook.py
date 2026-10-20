"""Pinned chat/AsyncLLM integration; explicit installation, no startup side effect.

Signed dispatch bindings originate only from the admission gateway after durable
reservation. A normal HTTP request ID is never execution authority. Stock vLLM
renderers, parsers and serializers remain responsible for response formatting.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
import re
import time
from types import MethodType

from .admission import ResourceManifest
from .dispatch_token import DispatchBinding, DispatchTokenAuthority
from .engine_receipts import ReceiptUnavailable

HEADER = 'x-shared-home-dispatch'
_CURRENT = ContextVar('shared_home_vllm_dispatch', default=None)
PINS = {
    'vllm.v1.engine.async_llm': '8d50b66619da8452b48d32bbadb2724c977e8594bc9ccb1485ef36725e4fa7ec',
    'vllm.v1.engine.input_processor': '17c091fef7bbaa4537335367abf8267345a782914f00fe01919c6efbbeff9c3b',
    'vllm.v1.engine.parallel_sampling': 'bf3f0a3c6640aaf706d1b6f7ec3a8e09a970f3dd9e7e96fa6a901779ea84f61d',
    'vllm.v1.engine.core_client': 'b350e95b61799c1f3f0876274ac5ea3cfc697fd2b263955199739334b010b91d',
    'vllm.entrypoints.openai.chat_completion.serving': '8c41af967ef813bf6fd2e2f16b8eedbbf6098d78c971639a58b6297d1c217c02',
}


def install_input_route(app, hook):
    """Explicit private-listener route; deployment must protect the signing key."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from .dispatch_token import DispatchTokenError
    from .vllm_input_authority import _json, MAX_BODY
    path = '/shared-home/input-bound'
    if any(getattr(route, 'path', None) == path for route in app.routes):
        raise ReceiptUnavailable('input authority route already installed')

    async def endpoint(raw_request):
        try:
            lengths = raw_request.headers.getlist('content-length')
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or not 0 < int(lengths[0]) <= MAX_BODY
                    or raw_request.headers.getlist('transfer-encoding')
                    or raw_request.headers.getlist('content-encoding')):
                raise ValueError('bounded unencoded request required')
            body = bytearray()
            async with asyncio.timeout(hook.manifest.max_deadline_seconds):
                async for chunk in raw_request.stream():
                    if len(chunk) > int(lengths[0]) - len(body):
                        raise ValueError('request length exceeded')
                    body.extend(chunk)
                if len(body) != int(lengths[0]):
                    raise ValueError('request length mismatch')
                # Public Request API: replay the bounded exact bytes for signature
                # verification and stock request metadata, without private fields.
                async def receive():
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                replay = Request(raw_request.scope, receive)
                value = _json(bytes(body))
                request = ChatCompletionRequest.model_validate(value)
                result = await hook.prepare(request, replay)
                return JSONResponse(result)
        except (ValueError, RecursionError, DispatchTokenError, ReceiptUnavailable, TimeoutError):
            return JSONResponse({'error': 'input preflight rejected'}, status_code=400)
    app.add_route(path, endpoint, methods=['POST'])


def install_api_hook(serving, manifest, authority, *, clock=time.time):
    import vllm
    import vllm.envs
    from vllm.v1.engine.async_llm import AsyncLLM
    from vllm.v1.engine.core_client import AsyncMPClient
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    if vllm.__version__ != '0.20.2' or vllm.envs.VLLM_DISABLE_REQUEST_ID_RANDOMIZATION:
        raise ReceiptUnavailable('unreviewed API runtime or request-ID configuration')
    for name, expected in PINS.items():
        module = importlib.import_module(name)
        with Path(inspect.getfile(module)).open('rb') as stream:
            raw = stream.read(1000001)
        if len(raw) > 1000000 or hashlib.sha256(raw).hexdigest() != expected:
            raise ReceiptUnavailable('API source differs from reviewed pin')
    client = serving.engine_client
    if (type(serving) is not OpenAIServingChat or type(client) is not AsyncLLM
            or type(client.engine_core) is not AsyncMPClient
            or getattr(serving.create_chat_completion, '__func__', None) is not OpenAIServingChat.create_chat_completion
            or getattr(client._add_request, '__func__', None) is not AsyncLLM._add_request
            or getattr(client.generate, '__func__', None) is not AsyncLLM.generate):
        raise ReceiptUnavailable('fresh supported API objects required')
    hook = _APIHook(serving, manifest, authority, clock)
    hook.install()
    return hook


@dataclass
class _RequestState:
    binding: DispatchBinding
    internal_ids: tuple[str, ...] = ()
    added: set[str] = field(default_factory=set)
    registration_started: bool = False
    registration_authorized: bool = False
    generation_started: bool = False
    original_iterator: object = None


class _Prepared(BaseException):
    """Escape stock Exception handlers before output registration or engine ADD."""
    def __init__(self, tokens):
        self.tokens = tokens


class _APIHook:
    def __init__(self, serving, manifest, authority, clock):
        if (not isinstance(manifest, ResourceManifest) or not isinstance(authority, DispatchTokenAuthority)
                or authority.manifest_digest != manifest.digest):
            raise ValueError('fixed manifest and authenticated dispatch authority required')
        self.serving, self.client = serving, serving.engine_client
        self.manifest, self.authority, self.clock = manifest, authority, clock
        self.create = serving.create_chat_completion
        self.generate = self.client.generate
        self.add = self.client._add_request

    def _remaining(self, state):
        now = self.clock()
        if type(now) not in (float, int) or not math.isfinite(now):
            raise ReceiptUnavailable('API clock unavailable')
        remaining = state.binding.request_deadline - now
        if remaining <= 0:
            raise ReceiptUnavailable('dispatch deadline elapsed')
        return remaining

    def _state(self):
        state = _CURRENT.get()
        if (not isinstance(state, _RequestState)
                or state.binding.manifest_digest != self.manifest.digest
                or state.binding.engine_epoch != self.authority.engine_epoch):
            raise ReceiptUnavailable('verified gateway binding required')
        self._remaining(state)
        return state

    async def _scoped(self, state, awaitable):
        token = _CURRENT.set(state)
        try:
            return await awaitable
        finally:
            _CURRENT.reset(token)

    async def _close(self, state, iterator):
        try:
            await asyncio.wait_for(self._scoped(state, iterator.aclose()), timeout=1.)
        except Exception:
            # Closure/abort failure supplies no terminal evidence. The durable
            # gateway charge remains until independently reconciled.
            pass

    async def _binding(self, request, raw_request, purpose):
        if raw_request is None:
            raise ReceiptUnavailable('authenticated raw gateway request required')
        tokens = raw_request.headers.getlist(HEADER)
        identifiers = raw_request.headers.getlist('x-request-id')
        if len(tokens) != 1 or len(identifiers) != 1:
            raise ReceiptUnavailable('one authenticated dispatch binding required')
        binding = self.authority.verify(tokens[0], await raw_request.body())
        if (binding.purpose != purpose or identifiers[0] != binding.ledger_id
                or request.use_beam_search):
            raise ReceiptUnavailable('request routing, purpose or generation mode not admitted')
        return binding

    async def prepare(self, request, raw_request):
        """Use stock rendering and input processing, stopping before any engine ADD."""
        binding = await self._binding(request, raw_request, 'prepare')
        state = _RequestState(binding)
        result = None
        try:
            async with asyncio.timeout(self._remaining(state)):
                result = await self._scoped(state, self.create(request, raw_request))
                if isinstance(result, AsyncIterator):
                    async for _ in self._prepared_stream(state, result):
                        raise ReceiptUnavailable('preflight unexpectedly produced output')
                raise ReceiptUnavailable('preflight did not reach validated engine input')
        except _Prepared as prepared:
            self._remaining(state)
            return dict(version=1, manifest_digest=binding.manifest_digest,
                engine_epoch=binding.engine_epoch, model_revision=self.manifest.model_revision,
                upstream_body_sha256=binding.upstream_body_sha256,
                upper_bound_tokens=prepared.tokens, valid_until=binding.request_deadline,
                ledger_id=binding.ledger_id, request_digest=binding.request_digest)
        finally:
            if isinstance(result, AsyncIterator):
                await self._close(state, result)
            if state.original_iterator is not None:
                await self._close(state, state.original_iterator)

    async def _prepared_stream(self, state, iterator):
        while True:
            try:
                yield await self._scoped(state, anext(iterator))
            except StopAsyncIteration:
                return

    def install(self):
        hook = self
        async def create(_serving, request, raw_request=None):
            binding = await hook._binding(request, raw_request, 'execute')
            state = _RequestState(binding)
            try:
                result = await hook._scoped(state, hook.create(request, raw_request))
            except BaseException:
                if state.original_iterator is not None:
                    await hook._close(state, state.original_iterator)
                raise
            if not isinstance(result, AsyncIterator):
                return result
            async def stream():
                try:
                    while True:
                        try:
                            value = await hook._scoped(state, anext(result))
                        except StopAsyncIteration:
                            return
                        yield value
                finally:
                    await hook._close(state, result)
                    if state.original_iterator is not None:
                        await hook._close(state, state.original_iterator)
            return stream()

        async def add(_client, request, prompt, parent_req, index, queue):
            state = hook._state()
            expected_external = 'chatcmpl-' + state.binding.ledger_id
            if request.external_req_id != expected_external:
                raise ReceiptUnavailable('engine request differs from authenticated HTTP binding')
            tokens = request.prompt_token_ids
            params = request.sampling_params
            if (request.prompt_embeds is not None or type(tokens) is not list
                    or not 1 <= len(tokens) <= hook.manifest.max_input_tokens
                    or any(type(value) is not int or value < 0 for value in tokens)
                    or params is None or type(params.max_tokens) is not int
                    or not 1 <= params.max_tokens <= hook.manifest.max_output_tokens):
                raise ReceiptUnavailable('processed engine input outside admitted bounds')
            if state.binding.purpose == 'prepare':
                if parent_req is not None:
                    raise ReceiptUnavailable('parallel preflight is not admitted')
                raise _Prepared(len(tokens))
            if (state.binding.purpose != 'execute'
                    or len(tokens) > state.binding.input_bound_tokens):
                raise ReceiptUnavailable('actual input exceeds signed execution bound')
            if parent_req is None:
                identities = (request.request_id,)
            else:
                if (type(parent_req.n) is not int or not 1 < parent_req.n <= 64
                        or parent_req.external_req_id != expected_external):
                    raise ReceiptUnavailable('parallel request set outside supported bounds')
                identities = tuple(f'{i}_{parent_req.request_id}' for i in range(parent_req.n))
            if not state.registration_started:
                if index != 0:
                    raise ReceiptUnavailable('first engine child must register complete set')
                state.registration_started = True
                state.internal_ids = identities
                binding = state.binding
                decision = await asyncio.wait_for(hook.client.engine_core.call_utility_async(
                    'shared_home_register', binding.ledger_id, binding.request_digest,
                    binding.upstream_body_sha256, list(identities)), hook._remaining(state))
                values = (binding.manifest_digest, hook.manifest.engine_id, binding.engine_epoch,
                    hook.manifest.model_revision, binding.ledger_id, binding.request_digest,
                    binding.upstream_body_sha256, tuple(sorted(identities)))
                expected_digest = hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
                if (type(decision) is not dict or set(decision) != {'registration_digest', 'dispatch_permitted'}
                        or decision['dispatch_permitted'] is not True
                        or decision['registration_digest'] != expected_digest):
                    raise ReceiptUnavailable('engine did not authorize fresh dispatch')
                state.registration_authorized = True
            hook._remaining(state)
            if (not state.registration_authorized or identities != state.internal_ids or request.request_id not in identities
                    or request.request_id in state.added):
                raise ReceiptUnavailable('engine child was changed or replayed')
            state.added.add(request.request_id)
            return await hook.add(request, prompt, parent_req, index, queue)

        async def generate(_client, prompt, sampling_params, request_id, **kwargs):
            state = hook._state()
            if state.generation_started or request_id != 'chatcmpl-' + state.binding.ledger_id:
                raise ReceiptUnavailable('one bound engine generation required')
            state.generation_started = True
            iterator = hook.generate(prompt, sampling_params, request_id, **kwargs)
            state.original_iterator = iterator
            try:
                async for output in iterator:
                    if output.finished:
                        if not state.internal_ids or state.added != set(state.internal_ids):
                            raise ReceiptUnavailable('complete dispatched engine set unavailable')
                        evidence = await asyncio.wait_for(hook.client.engine_core.call_utility_async(
                            'shared_home_terminal', state.binding.ledger_id), hook._remaining(state))
                        if not isinstance(evidence, str) or re.fullmatch('[a-f0-9]{64}', evidence) is None:
                            raise ReceiptUnavailable('durable terminal receipt unavailable')
                        hook._remaining(state)
                    yield output
            finally:
                await hook._close(state, iterator)

        self.serving.create_chat_completion = MethodType(create, self.serving)
        self.client._add_request = MethodType(add, self.client)
        self.client.generate = MethodType(generate, self.client)
