"""Real publisher AutoModel backend. Heavy libraries are imported only in load()."""
import os
from pathlib import Path
import shutil
import tempfile
import time

from .artifacts import check_runtime, cgroup_limits, read_receipt, read_runtime_manifest, verify_snapshot
from .contracts import CHOICES, QUESTION, ContractError, finite, load_rgb_frames


class VjepaBackend:
    def __init__(self, config, profile, *, clock=time.monotonic):
        self.config, self.profile = config, profile
        self.clock = clock
        self.model = self.torch = self.numpy = None
        self.receipt_digest = None
        self.module_cache = None

    def native_gate(self, *, cold):
        from .resources import ControlState, HostProbe, NvidiaProbe
        caps = self.profile["execution"]
        state = ControlState(self.config["control_state_file"],
                             expected_lease_file=self.config["execution_lease_file"],
                             queue_receipt_file=self.config["queue_receipt_file"], queue_receipt_gid=self.config["queue_receipt_gid"],
                             expected_qwen_config_sha256=self.config["qwen_observer_config_sha256"],
                             expected_qwen_policy_sha256=self.config["qwen_binding_policy_sha256"]).read(time.monotonic())
        if state["qwen_busy"]:
            raise ContractError("qwen_busy_before_native_work")
        incremental = caps["gpu_reservation_bytes"] if cold else 0
        free = NvidiaProbe(self.config["gpu_selector"]).free_bytes()
        if free < caps["minimum_free_bytes"] + incremental + state["unallocated_reserve_bytes"]:
            raise ContractError("gpu_headroom_changed_before_native_work")
        host_incremental = caps["cold_host_reservation_bytes"] if cold else 0
        if HostProbe().available_bytes() < caps["host_minimum_available_bytes"] + host_incremental:
            raise ContractError("host_memory_headroom_changed_before_native_work")

    def load(self):
        if self.model is not None:
            return
        # All checks precede execution of the snapshot's reviewed Python code.
        cgroup_limits(self.profile, self.config["io_device"])
        receipt, _ = read_receipt(self.profile, self.config["receipt_root"])
        runtime_manifest = read_runtime_manifest(self.profile, self.config["receipt_root"], receipt)
        check_runtime(self.profile, runtime_manifest)
        snapshot, self.receipt_digest = verify_snapshot(self.profile, self.config["snapshot_root"], receipt)
        caps = self.profile["execution"]
        # Recheck immediately before load; the parent gate is not a memory
        # partition and cannot make another process's allocation atomic.
        self.native_gate(cold=True)
        module_cache = tempfile.mkdtemp(prefix="reviewed-modules-", dir=self.config["cache_root"])
        self.module_cache = Path(module_cache).resolve()
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                          CUDA_VISIBLE_DEVICES=self.config["gpu_selector"], TOKENIZERS_PARALLELISM="false",
                          OMP_NUM_THREADS="4", MKL_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
                          NUMEXPR_NUM_THREADS="4", USE_TF="0", USE_FLAX="0",
                          HF_HOME=str(Path(self.config["cache_root"]) / "hf"),
                          HF_MODULES_CACHE=module_cache)
        import torch
        import numpy
        from transformers import AutoModel, AutoTokenizer
        self.torch, self.numpy = torch, numpy
        if torch.version.cuda != self.profile["runtime"]["cuda"] or not torch.cuda.is_available():
            raise ContractError("cuda_runtime_mismatch")
        if torch.cuda.get_device_capability(0) != (12, 0):
            raise ContractError("unvalidated_gpu_architecture")
        if not any(a.startswith("sm_120") for a in torch.cuda.get_arch_list()):
            raise ContractError("torch_blackwell_kernel_unavailable")
        torch.set_num_threads(caps["cpu_threads"])
        torch.set_num_interop_threads(1)
        total = torch.cuda.get_device_properties(0).total_memory
        # PyTorch allocator cap only; the manifest reservation remains a
        # separate accounting claim and is not advertised as a driver cap.
        torch.cuda.set_per_process_memory_fraction(min(1.0, caps["gpu_reservation_bytes"] / total), 0)
        tokenizer = AutoTokenizer.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=False)
        model = AutoModel.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=True,
                                          dtype=torch.bfloat16, low_cpu_mem_usage=True)
        model.set_tokenizer(tokenizer)  # Prevent publisher's unpinned lazy tokenizer lookup.
        self.model = model.to("cuda:0").eval()
        torch.cuda.synchronize(0)

    def preprocess(self, request):
        """Fixed original-frame ROI -> 384 square; no publisher center crop.

        The tuple API accepts normalized [3,32,384,384] tensors and elapsed
        [32] times directly. Bidirectional attention sees only this already
        causal window. Repeated source frames remain repeated in the tensor.
        """
        rgb_frames = load_rgb_frames(request, self.config["frame_root"])
        w, h = request["source_size"]
        x0, y0, x1, y1 = request["roi_xyxy"]
        torch, np = self.torch, self.numpy
        frames = []
        for rgb in rgb_frames:
            array = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)[y0:y1, x0:x1].copy()
            frames.append(torch.from_numpy(array).permute(2, 0, 1))
        # Treat T as batch only for spatial interpolation, preserving time.
        tensor = torch.stack(frames).float().div_(255)
        tensor = torch.nn.functional.interpolate(tensor, size=(384, 384), mode="bicubic",
                                                 align_corners=False, antialias=True).clamp_(0, 1)
        mean = tensor.new_tensor([.485, .456, .406]).view(1, 3, 1, 1)
        std = tensor.new_tensor([.229, .224, .225]).view(1, 3, 1, 1)
        tensor = ((tensor - mean) / std).permute(1, 0, 2, 3).contiguous()
        origin = request["cutoff_at"] - request["duration_seconds"]
        times = torch.tensor([f["captured_at"] - origin for f in request["frames"]], dtype=torch.float32)
        return tensor, times

    def _live_deadline(self, request):
        now, expiry = self.clock(), request.get("expires_at")
        if not finite(now) or not finite(expiry) or now >= expiry:
            raise ContractError("clip_expired_before_native_work")

    def infer(self, request):
        self._live_deadline(request)
        self.load()
        self._live_deadline(request)
        video = self.preprocess(request)
        self._live_deadline(request)
        self.native_gate(cold=False)
        with self.torch.inference_mode():
            self._live_deadline(request)
            if request["mode"] == "choose":
                index, raw_scores = self.model.choose(video, QUESTION, list(CHOICES))
                if type(index) is not int or not 0 <= index < len(CHOICES) or len(raw_scores) != len(CHOICES):
                    raise ContractError("publisher_choice_contract_changed")
                result = {"intent": CHOICES[index], "raw_option_scores": dict(zip(CHOICES, raw_scores)),
                          "text": None}
            else:
                text = self.model.chat(video, QUESTION, max_new_tokens=64)
                # Free text is evidence for review, never parsed into motion
                # commands or a calibrated categorical decision.
                result = {"intent": "unclear", "raw_option_scores": None, "text": text}
        return result

    def warmup(self):
        self.load()
        # Exercise the actual tuple/attention/RoPE path before live deadlines.
        # Synthetic output is discarded and never becomes an IntentHint.
        frames = self.torch.zeros((3, 32, 384, 384), dtype=self.torch.float32)
        times = self.torch.arange(32, dtype=self.torch.float32) * (4 / 32)
        self.native_gate(cold=False)
        with self.torch.inference_mode():
            self.model.choose((frames, times), QUESTION, list(CHOICES))

    def synchronize(self):
        if self.torch is not None:
            self.torch.cuda.synchronize(0)

    def close(self):
        # Only remove the private directory this worker created. Snapshots,
        # frame blobs, HF assets, receipts, and incident evidence are retained.
        if self.module_cache is not None:
            root = Path(self.config["cache_root"]).resolve()
            if self.module_cache.parent == root and self.module_cache.name.startswith("reviewed-modules-"):
                try:
                    shutil.rmtree(self.module_cache)
                except OSError:
                    pass


def worker_main(connection, config, profile):
    """Private pipe protocol. Result proof is issued only after native sync.

    Unknown native execution, a dead pipe, or a failed synchronization exits
    the child. The host keeps the execution lease until that process is reaped.
    """
    backend = VjepaBackend(config, profile)
    try:
        while True:
            message = connection.recv()
            if message.get("command") == "stop":
                break
            if message.get("command") not in ("infer", "warmup"):
                break
            request = message["request"]
            try:
                if message["command"] == "warmup":
                    backend.warmup()
                    backend.synchronize()
                    connection.send({"kind": "ready", "request_id": request["request_id"],
                                     "snapshot_manifest_digest": backend.receipt_digest,
                                     "proof": "cuda_synchronized"})
                    continue
                result = backend.infer(request)
                backend.synchronize()
                connection.send({"kind": "complete", "request_id": request["request_id"], "output": result,
                                 "snapshot_manifest_digest": backend.receipt_digest,
                                 "proof": "cuda_synchronized"})
            except Exception as exc:
                # Do not expose prompts, file contents, paths, or arbitrary
                # publisher exceptions over the operator interface.
                try:
                    backend.synchronize()
                except Exception:
                    raise RuntimeError("native_completion_unproven") from None
                connection.send({"kind": "failed", "request_id": request["request_id"],
                                 "reason": str(exc) if isinstance(exc, ContractError) else "backend_failed",
                                 "proof": "cuda_synchronized" if backend.torch is not None else "no_native_context"})
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()
        getattr(backend, "close", lambda: None)()
