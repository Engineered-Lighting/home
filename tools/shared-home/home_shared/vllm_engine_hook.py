"""Explicit in-process vLLM 0.20.2 engine utility integration.

Install on the engine loop thread before any requests. This module is not a
startup plugin: API-server registration/await wiring and deployment are separate.
No HTTP caller may access engine utilities or the receipt writer directly.
Unknown modes are rejected; no vLLM setting or existing capability is changed.
"""
from concurrent.futures import Future
import hashlib
import inspect
import json
import math
from pathlib import Path
import threading
from types import MethodType

from .engine_receipts import EngineReceiptStore, ReceiptUnavailable
from .vllm_worker_cleanup import worker_cleanup_rpc_ack


PINS = {
    'vllm.v1.engine.core': 'e1486de709675fa64109bb79f2b35b19c974b38a8bc2616b5e177ea3ff96c421',
    'vllm.v1.core.sched.scheduler': 'ff208267973b16f1cde384cb4d86090b950f35d0e8bcd97433c642551867572d',
    'vllm.v1.core.sched.interface': '65fa9a22359f4103a521379d7471e2063f2b1a80b95646b30f22efdd3d6b7b9e',
    'vllm.v1.core.sched.async_scheduler': '4df37e8842fb06d5cce3bc414e9423a09808e9336a96a25530dd2270f078d093',
}


def _pin(module):
    path = Path(inspect.getfile(module))
    with path.open('rb') as stream:
        raw = stream.read(1000001)
    if len(raw) > 1000000 or hashlib.sha256(raw).hexdigest() != PINS[module.__name__]:
        raise ReceiptUnavailable('vLLM source does not match reviewed engine pin')


def install_engine_hook(core, store, *, cleanup_authority, rpc_timeout_seconds):
    """Install guarded utilities on a fresh, pinned, single-GPU engine instance.

    The caller must hold the store's exact cleanup authority. Installation does
    not grant workload admission or establish installed-platform acceptance.
    """
    import vllm.version
    from vllm.v1.engine import core as core_module
    from vllm.v1.core.sched import scheduler as scheduler_module
    from vllm.v1.core.sched import interface as interface_module
    from vllm.v1.core.sched import async_scheduler as async_module

    if vllm.version.__version__ != '0.20.2':
        raise ReceiptUnavailable('unreviewed vLLM version')
    for module in (core_module, scheduler_module, interface_module, async_module):
        _pin(module)
    if type(core.async_scheduling) is not bool:
        raise ReceiptUnavailable('unresolved scheduling mode')
    scheduler_type = async_module.AsyncScheduler if core.async_scheduling else scheduler_module.Scheduler
    if (type(core) is not core_module.EngineCoreProc
            or type(core.scheduler) is not scheduler_type
            or not isinstance(store, EngineReceiptStore)
            or cleanup_authority is not store._authority
            or store._read_only):
        raise ReceiptUnavailable('unreviewed engine or receipt writer')
    config = core.vllm_config
    parallel = config.parallel_config
    if (config.kv_transfer_config is not None or config.ec_transfer_config is not None
            or config.speculative_config is not None or core.is_pooling_model
            or parallel.tensor_parallel_size != 1 or parallel.pipeline_parallel_size != 1
            or parallel.data_parallel_size != 1 or core.scheduler.connector is not None):
        raise ReceiptUnavailable('engine mode requires additional lifecycle review')
    if core.async_scheduling and any(
        getattr(getattr(core.scheduler, name), '__func__', None) is not getattr(async_module.AsyncScheduler, name)
        for name in ('_update_after_schedule', '_update_request_with_output')
    ):
        raise ReceiptUnavailable('asynchronous scheduler methods were modified')
    if (core.has_work() or core.scheduler.requests or not core.is_running()
            or hasattr(core, 'shared_home_register') or hasattr(core, 'shared_home_terminal')
            or hasattr(core, 'shared_home_runtime')
            or getattr(core.add_request, '__func__', None) is not core_module.EngineCore.add_request
            or getattr(core.scheduler._free_blocks, '__func__', None) is not scheduler_module.Scheduler._free_blocks
            or getattr(core.scheduler.schedule, '__func__', None) is not scheduler_module.Scheduler.schedule):
        raise ReceiptUnavailable('engine must be fresh and unmodified before hook installation')
    hook = _EngineHook(core, store, cleanup_authority, rpc_timeout_seconds)
    hook.install()
    return hook


class _EngineHook:
    """Loop-thread implementation, isolated for deterministic ordering tests."""
    def __init__(self, core, store, authority, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
            raise ValueError('bounded worker RPC timeout required')
        self.core, self.store, self.authority, self.timeout = core, store, authority, timeout
        self.thread = threading.get_ident()
        self.owners, self.seen, self.freed, self.free_hashes = {}, set(), set(), set()
        self.pending = {}
        self.callback_queued = False
        self.failed = False
        self.add = core.add_request
        self.free = core.scheduler._free_blocks
        self.schedule = core.scheduler.schedule

    def _check(self):
        if self.failed or threading.get_ident() != self.thread:
            raise ReceiptUnavailable('engine hook unavailable or called outside engine loop')

    def install(self):
        self._check()
        hook = self
        def register(_core, ledger_id, request_digest, body_digest, internal_ids):
            return hook.register(ledger_id, request_digest, body_digest, internal_ids)
        def terminal(_core, ledger_id):
            return hook.terminal(ledger_id)
        def runtime(_core):
            hook._check()
            manifest, engine, epoch, model = hook.store.binding
            return dict(version=1, manifest_digest=manifest, engine_id=engine,
                        engine_epoch=epoch, model_revision=model)
        def add(_core, request, request_wave=0):
            hook._check()
            identity = request.request_id
            if identity not in hook.owners or identity in hook.seen:
                hook.failed = True
                raise ReceiptUnavailable('engine request lacks a fresh durable registration')
            hook.seen.add(identity)
            try:
                return hook.add(request, request_wave)
            except BaseException:
                hook.failed = True
                raise
        def free(_scheduler, request):
            hook._check()
            result = hook.free(request)
            identity = request.request_id
            if identity not in hook.seen or identity in hook.core.scheduler.requests:
                hook.failed = True
                raise ReceiptUnavailable('scheduler cleanup lacks registered execution')
            hook.freed.add(identity)
            # A disconnected/crashed API process may never ask for terminal
            # status. Once the complete registered set has really been freed,
            # schedule durable acknowledgment independently of that caller.
            ledger_id = hook.owners[identity]
            receipt = hook.store.lookup(ledger_id)
            if receipt is not None and set(receipt.internal_ids) <= hook.seen & hook.freed:
                hook.terminal(ledger_id)
            return result
        def schedule(_scheduler):
            hook._check()
            result = hook.schedule()
            # Only the final applied scheduler output is checked at idle. A
            # hash freed in an earlier step may legitimately have been reused
            # as a shared resident encoder cache by a later request.
            hook.free_hashes = set(result.free_encoder_mm_hashes)
            if len(hook.free_hashes) > 65536:
                hook.failed = True
                raise ReceiptUnavailable('cleanup metadata exceeds supported bound')
            return result
        self.core.shared_home_register = MethodType(register, self.core)
        self.core.shared_home_terminal = MethodType(terminal, self.core)
        self.core.shared_home_runtime = MethodType(runtime, self.core)
        self.core.add_request = MethodType(add, self.core)
        self.core.scheduler._free_blocks = MethodType(free, self.core.scheduler)
        self.core.scheduler.schedule = MethodType(schedule, self.core.scheduler)

    def register(self, ledger_id, request_digest, body_digest, internal_ids):
        self._check()
        decision = self.store.register_for_dispatch(ledger_id, request_digest, body_digest, internal_ids)
        if decision.dispatch_permitted:
            for identity in decision.receipt.internal_ids:
                self.owners[identity] = ledger_id
        return {'registration_digest': decision.receipt.registration_digest,
                'dispatch_permitted': decision.dispatch_permitted}

    def terminal(self, ledger_id):
        self._check()
        receipt = self.store.lookup(ledger_id)
        if receipt is None:
            raise ReceiptUnavailable('request has no engine registration')
        if receipt.cleanup_evidence_sha256 is not None:
            result = Future()
            result.set_result(receipt.cleanup_evidence_sha256)
            return result
        if ledger_id not in self.pending:
            self.pending[ledger_id] = Future()
        if not self.callback_queued:
            self.callback_queued = True
            self.core._idle_state_callbacks.append(self._on_idle)
        return self.pending[ledger_id]

    def _on_idle(self, core):
        self.callback_queued = False
        try:
            self._check()
            if core is not self.core or not core.is_running() or core.has_work() or core.batch_queue:
                raise ReceiptUnavailable('engine cleanup callback is not idle')
            if core.scheduler.requests:
                raise ReceiptUnavailable('scheduler retains request ownership')
            receipts = [self.store.lookup(key) for key in self.pending]
            if any(r is None or not set(r.internal_ids) <= self.seen & self.freed for r in receipts):
                raise ReceiptUnavailable('registered work lacks affirmative scheduler cleanup')
            identities = tuple(sorted({identity for r in receipts for identity in r.internal_ids}))
            # Both pinned schedulers use EngineCore's same idle barrier. Async
            # output-copy futures must have left batch_queue before this point;
            # scheduler-free alone cannot establish completion.
            # Worker RPC is synchronous and occurs on the idle engine loop. New
            # ADD messages remain queued until acknowledgment/commit completes.
            acknowledgments = core.model_executor.collective_rpc(worker_cleanup_rpc_ack,
                timeout=self.timeout, args=(identities, tuple(sorted(self.free_hashes))))
            self._validate_ack(acknowledgments, identities)
            evidence = hashlib.sha256(json.dumps(acknowledgments, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            for receipt in receipts:
                self.store.record_cleanup(receipt.ledger_id, receipt.request_digest,
                    receipt.upstream_body_sha256, receipt.internal_ids,
                    authority=self.authority, evidence_sha256=evidence)
                future = self.pending[receipt.ledger_id]
                if not future.done():
                    future.set_result(evidence)
            self.pending.clear()
            self.free_hashes.clear()
        except Exception:
            self.failed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ReceiptUnavailable('engine cleanup acknowledgment unavailable'))

    def _validate_ack(self, acknowledgments, identities):
        # Exact worker acknowledgment schema is shared with the worker module.
        from .vllm_worker_cleanup import validate_cleanup_ack
        if not isinstance(acknowledgments, list) or len(acknowledgments) != 1:
            raise ReceiptUnavailable('single-worker cleanup acknowledgment required')
        validate_cleanup_ack(acknowledgments[0], identities, tuple(sorted(self.free_hashes)))
