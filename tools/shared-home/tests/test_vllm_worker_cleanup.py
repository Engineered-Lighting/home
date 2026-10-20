import hashlib
import importlib
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import patch

from home_shared.vllm_worker_cleanup import worker_cleanup_ack, WorkerCleanupUnavailable, _runtime, validate_cleanup_ack, _check_logits_processors
from home_shared.vllm_worker_cleanup import worker_cleanup_rpc_ack, _RPC_SOURCES


def fixture():
    fields = "req_id_to_index req_ids num_reqs prev_req_id_to_index num_logprobs logprob_token_ids in_progress_prompt_logprobs_cpu generators bad_words_token_ids greedy_reqs random_reqs top_p_reqs top_k_reqs frequency_penalties_reqs presence_penalties_reqs repetition_penalties_reqs has_allowed_token_ids lora_id_to_request_ids lora_id_to_lora_request".split()
    batch = NS(**{key: {} for key in fields})
    batch.req_prompt_embeds = {}
    batch.num_reqs = 0
    batch.req_ids = []
    runner = NS(input_batch=batch, requests={}, num_prompt_logprobs={}, encoder_cache={},
                vllm_config=NS(kv_transfer_config=None, ec_transfer_config=None),
                speculative_config=None, is_pooling_model=False, lora_config=None,
                parallel_config=NS(tensor_parallel_size=1, pipeline_parallel_size=1,
                                   data_parallel_size=1, use_ubatching=False),
                device=NS(type="cuda"), execute_model_state=None, kv_connector_output=None)
    return NS(model_runner=runner, use_v2_model_runner=False, rank=0)


class WorkerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.worker = fixture()
        self.syncs = []
        self.runtime = patch("home_shared.vllm_worker_cleanup._runtime", return_value=NS(
            cuda=NS(synchronize=lambda device: self.syncs.append(device))))
        self.runtime.start()
        self.addCleanup(self.runtime.stop)

    def ack(self, ids=("request-1",), hashes=()):
        return worker_cleanup_ack(self.worker, ids, hashes)

    def test_actual_barrier_precedes_state_inspection(self):
        result = self.ack()
        self.assertEqual(self.syncs, [self.worker.model_runner.device])
        self.assertEqual(result["internal_ids"], ["request-1"])
        self.assertEqual(result["vllm_version"], "0.20.2")
        self.assertFalse(result["residency_released"])

    def test_unrelated_request_blocks_and_is_never_mutated(self):
        self.worker.model_runner.requests["another"] = object()
        with self.assertRaises(WorkerCleanupUnavailable): self.ack()
        self.assertIn("another", self.worker.model_runner.requests)

    def test_every_retained_batch_state_blocks(self):
        for name in vars(self.worker.model_runner.input_batch):
            with self.subTest(name=name):
                self.worker = fixture()
                setattr(self.worker.model_runner.input_batch, name, {"request-1": 1})
                with self.assertRaises(WorkerCleanupUnavailable): self.ack()

    def test_sync_failure_never_returns_receipt(self):
        with patch("home_shared.vllm_worker_cleanup._runtime", return_value=NS(
                cuda=NS(synchronize=lambda _: (_ for _ in ()).throw(RuntimeError("device lost"))))):
            with self.assertRaises(RuntimeError): self.ack()

    def test_shared_encoder_residency_is_reported_not_deleted(self):
        tensor = NS(numel=lambda: 32, element_size=lambda: 2)
        self.worker.model_runner.encoder_cache["shared"] = tensor
        result = self.ack(hashes=("freed",))
        self.assertEqual(result["retained_encoder_tensor_bytes"], 64)
        self.assertEqual(result["freed_encoder_hashes"], ["freed"])
        self.assertIs(self.worker.model_runner.encoder_cache["shared"], tensor)
        with self.assertRaises(WorkerCleanupUnavailable): self.ack(hashes=("shared",))

    def test_unknown_and_outstanding_modes_reject_before_sync(self):
        for field, value in (("speculative_config", object()), ("is_pooling_model", True),
                             ("lora_config", object()), ("execute_model_state", object()),
                             ("kv_connector_output", object())):
            self.worker = fixture()
            setattr(self.worker.model_runner, field, value)
            with self.subTest(field=field), self.assertRaises(WorkerCleanupUnavailable): self.ack()
        self.assertEqual(self.syncs, [])

    def test_invalid_identity_sets(self):
        for ids in ((), ("",), ("a", "a"), "a", (3,)):
            with self.subTest(ids=ids), self.assertRaises(WorkerCleanupUnavailable): self.ack(ids)

    def test_missing_state_field_fails_closed(self):
        del self.worker.model_runner.input_batch.logprob_token_ids
        with self.assertRaises(AttributeError): self.ack()

    def test_runtime_rejects_version_before_source_or_device_work(self):
        vllm = ModuleType("vllm")
        vllm.__version__ = "0.20.3"
        with patch.dict("sys.modules", {"torch": ModuleType("torch"), "vllm": vllm}):
            with self.assertRaisesRegex(WorkerCleanupUnavailable, "version"):
                _runtime(self.worker)

    def test_runtime_rejects_changed_source_before_device_work(self):
        vllm = ModuleType("vllm")
        vllm.__version__ = "0.20.2"
        with patch.dict("sys.modules", {"torch": ModuleType("torch"), "vllm": vllm}), \
                patch("home_shared.vllm_worker_cleanup.inspect.getsourcefile", return_value="changed.py"), \
                patch("home_shared.vllm_worker_cleanup.Path.read_bytes", return_value=b"changed"), \
                patch("home_shared.vllm_worker_cleanup.importlib.import_module"):
            with self.assertRaisesRegex(WorkerCleanupUnavailable, "source differs"):
                _runtime(self.worker)

    def test_ack_rejects_mismatches_unknown_fields_and_noninteger_accounting(self):
        ack = self.ack()
        for field, value in (("internal_ids", ["different"]), ("worker_rank", 1),
                             ("device_synchronized", 1), ("residency_released", True),
                             ("retained_encoder_tensor_bytes", float("inf")),
                             ("retained_encoder_entries", -1), ("unexpected", True)):
            changed = {**ack, field: value}
            with self.subTest(field=field), self.assertRaises(WorkerCleanupUnavailable):
                validate_cleanup_ack(changed, ("request-1",), ())

    def test_slot_retained_embeddings_block_without_deletion(self):
        retained = object()
        self.worker.model_runner.input_batch.req_prompt_embeds[0] = retained
        with self.assertRaises(WorkerCleanupUnavailable): self.ack()
        self.assertIs(self.worker.model_runner.input_batch.req_prompt_embeds[0], retained)

    def test_only_exact_reviewed_processors_and_empty_state_accepted(self):
        names = {"MinTokensLogitsProcessor": "min_toks", "LogitBiasLogitsProcessor": "biases",
                 "MinPLogitsProcessor": "min_p_count", "ThinkingTokenBudgetLogitsProcessor": "_state"}
        builtin = NS(**{name: type(name, (), {}) for name in names})
        processors = []
        for name, field in names.items():
            processor = getattr(builtin, name)()
            setattr(processor, field, 0 if field == "min_p_count" else {})
            processors.append(processor)
        batch = NS(logitsprocs=NS(all=processors))
        _check_logits_processors(batch, builtin)
        custom = type("Custom", (builtin.MinPLogitsProcessor,), {})()
        batch.logitsprocs.all = processors + [custom]
        with self.assertRaisesRegex(WorkerCleanupUnavailable, "unreviewed"):
            _check_logits_processors(batch, builtin)
        batch.logitsprocs.all = processors
        for processor, field in zip(processors, names.values()):
            old = getattr(processor, field)
            setattr(processor, field, 1)
            with self.subTest(field=field), self.assertRaises(WorkerCleanupUnavailable):
                _check_logits_processors(batch, builtin)
            setattr(processor, field, old)


class WorkerRpcAdapterTests(unittest.TestCase):
    def setUp(self):
        # Exact-runtime fixture classes and real file hashes, with no vLLM/GPU
        # import. Production constants remain the reviewed upstream SHA256s.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.Wrapper = type("WorkerWrapperBase", (), {})
        self.wrapper = self.Wrapper()
        self.wrapper.worker = fixture()
        self.wrapper.rpc_rank = self.wrapper.global_rank = 0
        self.paths, self.modules, pins = {}, {}, {}
        for name in _RPC_SOURCES:
            source = Path(directory.name) / (name + ".py")
            raw = ("# fixture-only source for " + name + "\n").encode()
            source.write_bytes(raw)
            self.paths[name] = source
            pins[name] = hashlib.sha256(raw).hexdigest()
            self.modules[name] = ModuleType(name)
        self.modules["vllm.v1.worker.worker_base"].WorkerWrapperBase = self.Wrapper
        self.vllm = ModuleType("vllm")
        self.vllm.__version__ = "0.20.2"
        real_import = importlib.import_module
        def imported(name, *args, **kwargs):
            return self.modules[name] if name in self.modules else real_import(name, *args, **kwargs)
        for replacement in (patch.dict("sys.modules", {"vllm": self.vllm}),
                            patch.dict(_RPC_SOURCES, pins, clear=True),
                            patch("home_shared.vllm_worker_cleanup.importlib.import_module", side_effect=imported),
                            patch("home_shared.vllm_worker_cleanup.inspect.getsourcefile",
                                  side_effect=lambda module: str(self.paths[module.__name__]))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_callable_receives_wrapper_and_preserves_existing_worker_barrier(self):
        synced = []
        with patch("home_shared.vllm_worker_cleanup._runtime", return_value=NS(
                cuda=NS(synchronize=lambda device: synced.append(device)))):
            # Reviewed run_method's callable branch binds obj as first argument.
            from functools import partial
            result = partial(worker_cleanup_rpc_ack, self.wrapper)(("request-1",), ("freed",))
        self.assertEqual(synced, [self.wrapper.worker.model_runner.device])
        self.assertEqual(result["internal_ids"], ["request-1"])
        self.assertEqual(result["freed_encoder_hashes"], ["freed"])
        self.assertFalse(result["residency_released"])

    def test_requires_exact_wrapper_not_raw_worker_subclass_or_proxy(self):
        class CustomWrapper(self.Wrapper):
            pass
        values = (self.wrapper.worker, CustomWrapper(), NS(worker=self.wrapper.worker))
        with patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack") as barrier:
            for value in values:
                with self.subTest(value=type(value)), self.assertRaisesRegex(WorkerCleanupUnavailable, "wrapper"):
                    worker_cleanup_rpc_ack(value, ("request-1",))
            barrier.assert_not_called()

    def test_uninitialized_wrapper_and_unreviewed_ranks_reject_before_barrier(self):
        with patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack") as barrier:
            del self.wrapper.worker
            with self.assertRaisesRegex(WorkerCleanupUnavailable, "initialized"):
                worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
            self.wrapper.worker = None
            with self.assertRaises(WorkerCleanupUnavailable):
                worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
            self.wrapper.worker = fixture()
            for field, value in (("rpc_rank", 1), ("global_rank", 1), ("rpc_rank", False)):
                setattr(self.wrapper, field, value)
                with self.subTest(field=field, value=value), self.assertRaises(WorkerCleanupUnavailable):
                    worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
                setattr(self.wrapper, field, 0)
            barrier.assert_not_called()

    def test_each_changed_rpc_source_is_rejected_before_barrier(self):
        with patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack") as barrier:
            for path in self.paths.values():
                original = path.read_bytes()
                path.write_bytes(original + b"# changed\n")
                with self.subTest(path=path.name), self.assertRaisesRegex(WorkerCleanupUnavailable, "source differs"):
                    worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
                path.write_bytes(original)
            barrier.assert_not_called()

    def test_missing_and_oversized_source_fail_before_barrier(self):
        with patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack") as barrier:
            with patch("home_shared.vllm_worker_cleanup.inspect.getsourcefile", return_value=None), \
                    self.assertRaisesRegex(WorkerCleanupUnavailable, "source differs"):
                worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
            next(iter(self.paths.values())).write_bytes(b"x" * 1_000_001)
            with self.assertRaisesRegex(WorkerCleanupUnavailable, "source differs"):
                worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
            barrier.assert_not_called()

    def test_wrong_version_stops_before_source_import_or_barrier(self):
        self.vllm.__version__ = "0.20.3"
        with patch("home_shared.vllm_worker_cleanup.importlib.import_module") as imported, \
                patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack") as barrier, \
                self.assertRaisesRegex(WorkerCleanupUnavailable, "version"):
            imported.reset_mock()  # Ignore unittest.mock's target resolution.
            worker_cleanup_rpc_ack(self.wrapper, ("request-1",))
        imported.assert_not_called()
        barrier.assert_not_called()

    def test_worker_failure_is_not_converted_into_acknowledgment(self):
        with patch("home_shared.vllm_worker_cleanup.worker_cleanup_ack", side_effect=RuntimeError("device lost")) as barrier:
            with self.assertRaisesRegex(RuntimeError, "device lost"):
                worker_cleanup_rpc_ack(self.wrapper, ("request-1",), ("freed",))
            barrier.assert_called_once_with(self.wrapper.worker, ("request-1",), ("freed",))


if __name__ == "__main__":
    unittest.main()
