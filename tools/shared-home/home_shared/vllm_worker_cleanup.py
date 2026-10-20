"""Pinned vLLM worker-side completion barrier, invoked through collective_rpc.

The EngineCore caller must serialize this with scheduling, drain all execute/output
futures, prove scheduler request/KV ownership gone, and execute the final empty
scheduler step first. This callable never deletes state or treats GPU free memory
as completion. CUDA synchronization and absence checks are affirmative worker
evidence, not a replacement for the scheduler proof.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
from pathlib import Path


class WorkerCleanupUnavailable(RuntimeError):
    pass


_SOURCES = {
    "vllm.v1.worker.gpu_worker": "dbefd82871bff199996cee87c76cf3de6debbd4e228a61134cb5e725478dbf29",
    "vllm.v1.worker.gpu_model_runner": "d71f4eb4547717a26a893d46d1dcb877731f9f5cba1de2f65376d24a6f95221f",
    "vllm.v1.worker.gpu_input_batch": "a371c9831d7ee248b42d284cffe96a31bcb3d59b7900b9adafdcb5d0272ae943",
    "vllm.v1.sample.logits_processor.builtin": "d7872b504d198b710da2d1ac0fab2ccc5976a1831f09530d8dd901c93018e226",
    "vllm.v1.sample.logits_processor.state": "a96411f14362c45f839e181dcb3ac01d5b880812bccb3552fc17863b219f44ac",
}

# Official v0.20.2 bytes: https://github.com/vllm-project/vllm/tree/v0.20.2/vllm/v1
# UniProcExecutor creates WorkerWrapperBase and calls run_method on that wrapper.
# run_method binds a callable's first argument directly; __getattr__ forwarding
# applies to string-method lookup, not to the callable's runtime type.
_RPC_SOURCES = {
    "vllm.v1.worker.worker_base": "e6017acf80e442405f09f99413c0227515ae097d3e1dc79005b0b5c266932f12",
    "vllm.v1.serial_utils": "a87f4cc50114eef931834270242b94c2ed2fc7fb78e9bdb7636428d0a99bf998",
    "vllm.v1.executor.uniproc_executor": "b4783f43e65ac2a6b0c4faa93e5d65a18f93a8a21fe9d886c221bd0b39811c6a",
}


def worker_cleanup_rpc_ack(wrapper, internal_ids, freed_encoder_hashes=()):
    """Pinned collective_rpc adapter; the existing worker barrier stays intact.

    Callable RPC receives the wrapper, not its GPU Worker. Do not weaken the
    worker's exact-type checks or rely on attribute forwarding to bridge this
    boundary. Missing initialization and custom wrappers fail before CUDA work.
    """
    import vllm
    if vllm.__version__ != "0.20.2":
        raise WorkerCleanupUnavailable("unsupported vLLM version")
    modules = {}
    for name, digest in _RPC_SOURCES.items():
        module = importlib.import_module(name)
        source = inspect.getsourcefile(module)
        if source is None:
            raise WorkerCleanupUnavailable("RPC source differs from reviewed upstream")
        with Path(source).open("rb") as stream:
            raw = stream.read(1_000_001)
        if len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != digest:
            raise WorkerCleanupUnavailable("RPC source differs from reviewed upstream")
        modules[name] = module
    if type(wrapper) is not modules["vllm.v1.worker.worker_base"].WorkerWrapperBase:
        raise WorkerCleanupUnavailable("unsupported worker RPC wrapper")
    try:
        # Avoid WorkerWrapperBase.__getattr__ recursion before init_worker.
        worker = object.__getattribute__(wrapper, "worker")
        ranks = (object.__getattribute__(wrapper, "rpc_rank"), object.__getattribute__(wrapper, "global_rank"))
    except AttributeError:
        raise WorkerCleanupUnavailable("worker RPC wrapper is not initialized") from None
    if worker is None or any(type(rank) is not int or rank != 0 for rank in ranks):
        raise WorkerCleanupUnavailable("unsupported or uninitialized worker RPC wrapper")
    return worker_cleanup_ack(worker, internal_ids, freed_encoder_hashes)


def _runtime(worker):
    import torch
    import vllm
    if vllm.__version__ != "0.20.2":
        raise WorkerCleanupUnavailable("unsupported vLLM version")
    modules = {}
    for name, digest in _SOURCES.items():
        module = importlib.import_module(name)
        source = inspect.getsourcefile(module)
        if source is None or hashlib.sha256(Path(source).read_bytes()).hexdigest() != digest:
            raise WorkerCleanupUnavailable("worker source differs from reviewed upstream")
        modules[name] = module
    if (type(worker) is not modules["vllm.v1.worker.gpu_worker"].Worker
            or type(worker.model_runner) is not modules["vllm.v1.worker.gpu_model_runner"].GPUModelRunner
            or type(worker.model_runner.input_batch) is not modules["vllm.v1.worker.gpu_input_batch"].InputBatch):
        raise WorkerCleanupUnavailable("unsupported worker implementation")
    from vllm.distributed.ec_transfer import has_ec_transfer
    from vllm.distributed.kv_transfer import has_kv_transfer_group
    if has_ec_transfer() or has_kv_transfer_group():
        raise WorkerCleanupUnavailable("connector completion requires separate review")
    batch = worker.model_runner.input_batch
    if type(batch.logitsprocs) is not modules["vllm.v1.sample.logits_processor.state"].LogitsProcessors:
        raise WorkerCleanupUnavailable("unsupported logits processor collection")
    _check_logits_processors(batch, modules["vllm.v1.sample.logits_processor.builtin"])
    return torch


def _require_empty(obj, names):
    for name in names:
        value = getattr(obj, name)  # Missing/changed fields must fail closed.
        if value:
            raise WorkerCleanupUnavailable("worker retains state: " + name)


def _check_logits_processors(batch, builtin):
    # Inspect the instantiated collection, covering both configured FQCNs and
    # entry-point plugins; the config alone does not enumerate both sources.
    reviewed = {
        builtin.MinTokensLogitsProcessor: "min_toks",
        builtin.LogitBiasLogitsProcessor: "biases",
        builtin.MinPLogitsProcessor: "min_p_count",
        builtin.ThinkingTokenBudgetLogitsProcessor: "_state",
    }
    processors = list(batch.logitsprocs.all)
    if len(processors) != len(reviewed) or {type(p) for p in processors} != set(reviewed):
        raise WorkerCleanupUnavailable("unreviewed logits processor implementation")
    for processor in processors:
        _require_empty(processor, (reviewed[type(processor)],))


def worker_cleanup_ack(worker, internal_ids, freed_encoder_hashes=()):
    """Return only after actual device synchronization and idle state inspection.

    Remaining encoder tensors and preallocated/prefix buffers are *resident*
    allocations. The admission manifest must charge their full bounded capacity;
    this result never authorizes releasing that residency charge. Hashes passed
    here must come from the final SchedulerOutput, not client input.
    """
    for values, bound in ((internal_ids, 4096), (freed_encoder_hashes, 65536)):
        if (not isinstance(values, (tuple, list)) or len(values) > bound
                or any(not isinstance(v, str) or not v or len(v) > 4096 for v in values)
                or len(set(values)) != len(values)):
            raise WorkerCleanupUnavailable("invalid cleanup identities")
    if not internal_ids:
        raise WorkerCleanupUnavailable("empty cleanup identity set")
    torch = _runtime(worker)
    runner = worker.model_runner
    config = runner.vllm_config
    if (worker.use_v2_model_runner or runner.speculative_config is not None
            or runner.is_pooling_model or runner.lora_config is not None
            or config.kv_transfer_config is not None or config.ec_transfer_config is not None
            or runner.parallel_config.tensor_parallel_size != 1
            or runner.parallel_config.pipeline_parallel_size != 1
            or runner.parallel_config.data_parallel_size != 1
            or runner.parallel_config.use_ubatching
            or runner.device.type != "cuda"):
        raise WorkerCleanupUnavailable("unsupported worker configuration")
    if runner.execute_model_state is not None or runner.kv_connector_output is not None:
        raise WorkerCleanupUnavailable("worker execution is outstanding")
    # Synchronize all CUDA streams on this exact worker device; a failed sync is
    # an exception, never a receipt. Core has already joined output-copy futures.
    torch.cuda.synchronize(runner.device)
    _require_empty(runner, ("requests", "num_prompt_logprobs"))
    batch = runner.input_batch
    _require_empty(batch, (
        "req_id_to_index", "req_ids", "num_reqs", "prev_req_id_to_index",
        "num_logprobs", "logprob_token_ids", "in_progress_prompt_logprobs_cpu",
        "generators", "bad_words_token_ids", "greedy_reqs", "random_reqs",
        "top_p_reqs", "top_k_reqs", "frequency_penalties_reqs",
        "presence_penalties_reqs", "repetition_penalties_reqs", "has_allowed_token_ids",
        "lora_id_to_request_ids", "lora_id_to_lora_request",
        "req_prompt_embeds",
    ))
    if any(key in runner.encoder_cache for key in freed_encoder_hashes):
        raise WorkerCleanupUnavailable("worker retains freed encoder output")
    resident_bytes = sum(t.numel() * t.element_size() for t in runner.encoder_cache.values())
    ack = {
        "schema_version": 1, "vllm_version": "0.20.2", "worker_rank": worker.rank,
        "internal_ids": list(internal_ids),
        "freed_encoder_hashes": list(freed_encoder_hashes),
        "request_count": len(internal_ids), "device_synchronized": True,
        "request_state_absent": True,
        "retained_encoder_entries": len(runner.encoder_cache),
        "retained_encoder_tensor_bytes": resident_bytes,
        "residency_released": False,
    }
    validate_cleanup_ack(ack, internal_ids, freed_encoder_hashes)
    return ack


def validate_cleanup_ack(value, internal_ids, freed_encoder_hashes):
    expected = {
        "schema_version": 1, "vllm_version": "0.20.2", "worker_rank": 0,
        "internal_ids": list(internal_ids), "freed_encoder_hashes": list(freed_encoder_hashes),
        "request_count": len(internal_ids), "device_synchronized": True,
        "request_state_absent": True, "residency_released": False,
    }
    counters = {"retained_encoder_entries": 65536, "retained_encoder_tensor_bytes": 2**63 - 1}
    if type(value) is not dict or set(value) != set(expected) | set(counters):
        raise WorkerCleanupUnavailable("invalid worker acknowledgment schema")
    for key, item in expected.items():
        if type(value[key]) is not type(item) or value[key] != item:
            raise WorkerCleanupUnavailable("worker acknowledgment mismatch: " + key)
    for key, maximum in counters.items():
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise WorkerCleanupUnavailable("invalid retained residency accounting")
