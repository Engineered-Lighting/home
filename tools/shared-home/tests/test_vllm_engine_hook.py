"""Engine-loop ordering fixtures; not installed vLLM/GPU acceptance."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest

from home_shared.engine_receipts import EngineReceiptStore, ReceiptUnavailable
from home_shared.vllm_engine_hook import _EngineHook
from home_shared.vllm_worker_cleanup import worker_cleanup_rpc_ack


class EngineHookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.authority = object()
        self.store = EngineReceiptStore(Path(temporary.name) / 'engine.db',
            manifest_digest='a' * 64, engine_id='engine', engine_epoch='epoch',
            model_revision='revision', cleanup_authority=self.authority, create=True)
        self.addCleanup(self.store.close)
        self.calls = []
        self.busy = False
        self.worker_error = False
        self.fail_commit = False
        self.rpc_arguments = None
        self.freed_hashes = []
        self.scheduler = SimpleNamespace(requests={})
        def free(request):
            self.calls.append('scheduler_free')
            del self.scheduler.requests[request.request_id]
        self.scheduler._free_blocks = free
        self.scheduler.schedule = lambda: SimpleNamespace(free_encoder_mm_hashes=self.freed_hashes)
        def add(request, wave):
            self.scheduler.requests[request.request_id] = request
        self.core = SimpleNamespace(add_request=add, scheduler=self.scheduler,
            _idle_state_callbacks=[], batch_queue=None, has_work=lambda: self.busy,
            is_running=lambda: True, model_executor=SimpleNamespace(collective_rpc=self.rpc))
        self.hook = _EngineHook(self.core, self.store, self.authority, 1.)
        self.hook.install()

    def rpc(self, method, *, timeout, args):
        self.assertIs(method, worker_cleanup_rpc_ack)
        self.calls.append('worker_sync')
        if self.worker_error:
            raise RuntimeError('fixture worker failed')
        identities, hashes = args
        self.rpc_arguments = args
        if self.fail_commit:
            self.store._db.execute('PRAGMA query_only=ON')
        return [{'schema_version': 1, 'vllm_version': '0.20.2', 'worker_rank': 0,
                 'internal_ids': list(identities), 'freed_encoder_hashes': list(hashes),
                 'request_count': len(identities), 'device_synchronized': True,
                 'request_state_absent': True, 'retained_encoder_entries': 0,
                 'retained_encoder_tensor_bytes': 0, 'residency_released': False}]

    def register(self):
        return self.core.shared_home_register('request', 'b' * 64, 'c' * 64, ['internal'])

    def test_runtime_handshake_is_bound_and_fails_with_hook(self):
        self.assertEqual(self.core.shared_home_runtime(), dict(version=1,
            manifest_digest='a' * 64, engine_id='engine', engine_epoch='epoch',
            model_revision='revision'))
        self.hook.failed = True
        with self.assertRaises(ReceiptUnavailable):
            self.core.shared_home_runtime()

    def execute_and_free(self):
        request = SimpleNamespace(request_id='internal')
        self.core.add_request(request)
        self.core.scheduler._free_blocks(request)

    def idle(self):
        for callback in list(self.core._idle_state_callbacks):
            self.core._idle_state_callbacks.remove(callback)
            callback(self.core)

    def test_registration_execution_cleanup_sync_commit_order(self):
        self.assertTrue(self.register()['dispatch_permitted'])
        self.execute_and_free()
        self.freed_hashes = ['hash-1']
        self.scheduler.schedule()
        future = self.core.shared_home_terminal('request')
        self.assertFalse(future.done())
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.idle()
        self.assertEqual(self.calls, ['scheduler_free', 'worker_sync'])
        self.assertEqual(future.result(), self.store.lookup('request').cleanup_evidence_sha256)
        self.assertFalse(self.register()['dispatch_permitted'])
        self.assertEqual(self.core.shared_home_terminal('request').result(), future.result())

    def test_idle_map_absence_without_observed_cleanup_is_not_proof(self):
        self.register()
        future = self.core.shared_home_terminal('request')
        self.idle()
        with self.assertRaises(ReceiptUnavailable):
            future.result()
        self.assertNotIn('worker_sync', self.calls)
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)

    def test_cleanup_persists_without_api_terminal_call(self):
        self.register()
        self.execute_and_free()
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.idle()
        self.assertIsNotNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.assertFalse(self.hook.failed)

    def test_nonempty_batch_queue_cannot_acknowledge(self):
        self.register()
        self.execute_and_free()
        self.core.batch_queue = ['still-running']
        future = self.core.shared_home_terminal('request')
        self.idle()
        with self.assertRaises(ReceiptUnavailable):
            future.result()
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)

    def test_retained_scheduler_ownership_blocks_worker_ack(self):
        self.register()
        self.execute_and_free()
        self.scheduler.requests['other'] = object()
        future = self.core.shared_home_terminal('request')
        self.idle()
        with self.assertRaises(ReceiptUnavailable):
            future.result()
        self.assertNotIn('worker_sync', self.calls)

    def test_worker_failure_never_commits_and_closes_hook(self):
        self.register()
        self.execute_and_free()
        self.worker_error = True
        future = self.core.shared_home_terminal('request')
        self.idle()
        with self.assertRaises(ReceiptUnavailable):
            future.result()
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)
        with self.assertRaises(ReceiptUnavailable):
            self.register()

    def test_failed_receipt_commit_does_not_acknowledge_completed_worker(self):
        self.register()
        self.execute_and_free()
        self.fail_commit = True
        future = self.core.shared_home_terminal('request')
        self.idle()
        self.assertIn('worker_sync', self.calls)
        with self.assertRaises(ReceiptUnavailable):
            future.result()
        payload = self.store._db.execute('SELECT payload FROM requests').fetchone()[0]
        self.assertIsNone(json.loads(payload)[-1])
        self.assertTrue(self.hook.failed)

    def test_unregistered_or_duplicate_add_cannot_execute(self):
        with self.assertRaises(ReceiptUnavailable):
            self.core.add_request(SimpleNamespace(request_id='not-registered'))
        self.assertFalse(self.scheduler.requests)

    def test_duplicate_terminal_wait_reuses_one_callback_and_future(self):
        self.register()
        self.execute_and_free()
        first = self.core.shared_home_terminal('request')
        self.assertIs(self.core.shared_home_terminal('request'), first)
        self.assertEqual(len(self.core._idle_state_callbacks), 1)
        self.idle()
        self.assertTrue(first.done())

    def test_canceled_wait_does_not_turn_execution_into_completion(self):
        self.register()
        future = self.core.shared_home_terminal('request')
        future.cancel()
        self.idle()
        self.assertIsNone(self.store.lookup('request').cleanup_evidence_sha256)

    def test_canceled_wait_does_not_prevent_real_cleanup_receipt(self):
        self.register()
        self.execute_and_free()
        future = self.core.shared_home_terminal('request')
        future.cancel()
        self.idle()
        self.assertIsNotNone(self.store.lookup('request').cleanup_evidence_sha256)
        self.assertFalse(self.hook.failed)

    def test_old_freed_hashes_are_not_confused_with_reused_residency(self):
        self.register()
        self.execute_and_free()
        self.freed_hashes = ['old-freed-hash']
        self.scheduler.schedule()
        self.freed_hashes = ['final-freed-hash']
        self.scheduler.schedule()
        future = self.core.shared_home_terminal('request')
        self.idle()
        self.assertIsNotNone(future.result())
        self.assertEqual(self.rpc_arguments[1], ('final-freed-hash',))


if __name__ == '__main__':
    unittest.main()
