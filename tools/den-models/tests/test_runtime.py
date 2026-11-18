"""Stdlib-only contracts and lifecycle tests: no torch, GPU, or model files."""
import copy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from den_models.artifacts import adapter_bundle_digest, check_receipt, load_profile, read_runtime_manifest, verify_snapshot
from den_models.backend import VjepaBackend
from den_models.contracts import (CHOICES, ContractError, canonical, load_rgb_frames, make_hint,
                                 profile_digest, store_rgb_blob, strict_json, validate_request)
from den_models.resources import FileExecutionLease
from den_models.runtime import Runtime

GIB = 1024**3
PROFILE = load_profile("vjepa-lm-qwen3-8b")
DIGEST = "a" * 64


def dependencies():
    return {"camera_id": "den", "service_run_id": "run", "camera_epoch": "2",
            "source_generation": hashlib.sha256(canonical(["run", 2]).encode()).hexdigest(),
            "track_id": "track-1", "association_revision": "3", "transform_id": "transform",
            "scan_revision": "scan", "calibration_revision": "c" * 64}


def request(identity="job-1", cutoff=100, expiry=None, duration=4):
    frames, seen = [], set()
    for i in range(32):
        sample = cutoff - duration + (i + .5) * duration / 32
        index = int((sample - cutoff + duration) / .2)
        captured = cutoff - duration + index * .2
        plane = {"seq": index, "device_s": captured - 50, "host_s": captured - 10,
                 "width": 640, "height": 360}
        source = {"service_run_id": "run", "camera_epoch": 2, "frame_id": index,
                  "camera_session": "camera-session", "mode": "rgb", "rgb": plane,
                  "right": None, "depth": None, "depth_calibration_sha256": "c" * 64,
                  "transform_id": "transform", "scan_revision": "scan", "capture_mono": captured,
                  "arrival_mono": captured + .01, "capture_ts": 1800000000 + captured}
        frames.append({"frame_id": str(index), "blob_ref": f"frame{index}.ppm", "sha256": "1" * 64,
                       "captured_at": captured, "sample_at": sample, "repeated": index in seen,
                       "track_bbox_xyxy": [10, 10, 100, 200], "source_frame": source})
        seen.add(index)
    return {"schema": "den.intent-request/v1", "request_id": identity, "clip_id": "clip-" + identity,
            "profile_digest": profile_digest(PROFILE), "dependencies": dependencies(), "frames": frames,
            "roi_xyxy": [0, 0, 360, 360], "source_size": [640, 360], "cutoff_at": cutoff,
            "expires_at": cutoff + 2 if expiry is None else expiry, "duration_seconds": duration,
            "mode": "choose"}


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


class Control:
    def __init__(self):
        self.state = {"qwen_busy": False, "unallocated_reserve_bytes": 0, "dependencies": [dependencies()]}
    def read(self, now):
        if self.state is None:
            raise ContractError("control_unavailable")
        return copy.deepcopy(self.state)


class Probe:
    free = 50 * GIB
    def free_bytes(self):
        return self.free


class Host:
    available = 40 * GIB
    def available_bytes(self):
        return self.available


class Lease:
    def __init__(self):
        self.held, self.releases, self.busy = None, [], False
    def try_acquire(self, request_id):
        if self.busy:
            return None
        assert self.held is None
        self.held = request_id
        return request_id
    def release(self, token, proof):
        assert token == self.held
        self.releases.append(proof)
        self.held = None
        return True


class Worker:
    pid = 1234
    def __init__(self, config, profile):
        self.alive, self.started, self.messages, self.sent = True, False, [], []
        self.terminate_requested = self.kill_requested = False
    def start(self):
        self.started = True
    def send(self, value):
        self.sent.append(value)
    def poll(self):
        messages, self.messages = self.messages, []
        return messages
    def reaped(self):
        return not self.alive
    def terminate(self):
        self.terminate_requested = True
    def kill(self):
        self.kill_requested = True
    def close(self):
        assert not self.alive
    def complete(self, identity="job-1", kind="complete", proof="cuda_synchronized"):
        self.messages.append({"kind": kind, "request_id": identity, "proof": proof,
                              "snapshot_manifest_digest": DIGEST,
                              "output": {"intent": "continue", "text": None,
                                         "raw_option_scores": dict(zip(CHOICES, [4, 3, 2, 1, 0]))}})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.control, self.probe, self.lease = Clock(), Control(), Probe(), Lease()
        self.host = Host()
        self.workers, self.residency = [], []
        def factory(config, profile):
            worker = Worker(config, profile)
            self.workers.append(worker)
            return worker
        self.runtime = Runtime({"gpu_selector": "0"}, PROFILE, clock=self.clock, control=self.control,
                               probe=self.probe, lease=self.lease, worker_factory=factory,
                               admission=lambda: DIGEST, residency_changed=self.residency.append, host_probe=self.host)

    def submit(self, value):
        self.runtime.submit(value)
        self.runtime.tick()
        return self.runtime.get(value["request_id"])

    def test_real_native_completion_releases_execution_not_residency(self):
        self.assertEqual(self.submit(request())["status"], "running")
        worker = self.workers[0]
        worker.complete()
        self.runtime.tick()
        result = self.runtime.get("job-1")
        self.assertEqual(result["status"], "succeeded")
        hint = result["hint"]
        self.assertEqual(hint["evidence"]["frames"], request()["frames"])
        self.assertEqual(hint["dependencies"], dependencies())
        self.assertEqual(hint["snapshot_manifest_digest"], DIGEST)
        self.assertEqual(hint["model_revision"], PROFILE["revision"])
        self.assertFalse(hint["evidence"]["additional_center_crop"])
        self.assertEqual(hint["authority"], "semantic_prior_only")
        self.assertEqual(self.lease.releases, ["cuda_synchronized"])
        self.assertTrue(self.runtime.health()["resident"])
        self.assertIsNotNone(self.residency[-1])

    def test_cancel_holds_execution_and_residency_until_reap(self):
        self.submit(request())
        self.assertEqual(self.runtime.cancel("job-1")["status"], "stopping")
        self.assertTrue(self.workers[0].terminate_requested)
        self.submit(request("job-2"))
        self.runtime.tick()
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.lease.held, "job-1")
        self.workers[0].complete()  # Late message cannot undo cancellation.
        self.runtime.tick()
        self.assertEqual(self.lease.held, "job-1")
        self.workers[0].alive = False
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "cancelled")
        self.assertEqual(self.lease.releases, ["process_reaped"])
        self.runtime.tick()  # Observe resources for the now-eligible pending job.
        self.assertEqual(len(self.workers), 2)
        self.assertEqual(self.lease.held, "job-2")

    def test_one_latest_pending_and_idempotent_request(self):
        self.submit(request())
        self.submit(request("job-2"))
        self.submit(request("job-3"))
        self.assertEqual(self.runtime.get("job-2")["status"], "superseded")
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.submit(request("job-3"))["status"], "queued")
        changed = request("job-3")
        changed["mode"] = "chat"
        with self.assertRaisesRegex(ContractError, "request_id_reused"):
            self.submit(changed)
        self.workers[0].complete()
        self.runtime.tick()
        self.runtime.tick()
        self.assertEqual(self.lease.held, "job-3")
        self.assertEqual(len(self.workers), 1)

    def test_qwen_busy_and_unknown_control_defer_without_child(self):
        self.control.state["qwen_busy"] = True
        self.assertEqual(self.submit(request())["reason"], "qwen_busy")
        self.assertFalse(self.workers)
        self.control.state = None
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["reason"], "control_state_unavailable")

    def test_cold_and_warm_incremental_reservation(self):
        self.probe.free = 38 * GIB - 1
        self.assertEqual(self.submit(request())["reason"], "gpu_headroom")
        self.probe.free = 38 * GIB
        self.runtime.tick()
        self.workers[0].complete()
        self.runtime.tick()
        self.probe.free = 16 * GIB
        self.assertEqual(self.submit(request("job-2"))["status"], "running")
        self.workers[0].complete("job-2")
        self.runtime.tick()
        self.control.state["unallocated_reserve_bytes"] = 1
        self.assertEqual(self.submit(request("job-3"))["reason"], "gpu_headroom")

    def test_host_memory_does_not_fall_back_to_unsafe_cpu_load(self):
        self.host.available = 32 * GIB - 1
        self.assertEqual(self.submit(request())["reason"], "host_memory_headroom")
        self.assertFalse(self.workers)
        self.host.available = 32 * GIB
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "running")

    def test_expiry_invalidations_and_lost_worker_require_terminal_proof(self):
        for reason in ("expiry", "dependency", "control", "death"):
            with self.subTest(reason=reason):
                self.setUp()
                self.submit(request())
                if reason == "expiry":
                    self.clock.now = 102
                elif reason == "dependency":
                    self.control.state["dependencies"] = []
                elif reason == "control":
                    self.control.state = None
                else:
                    self.workers[0].alive = False
                self.runtime.tick()
                if reason != "death":
                    self.assertEqual(self.lease.held, "job-1")
                    self.workers[0].alive = False
                    self.runtime.tick()
                self.assertIsNone(self.lease.held)
                self.assertIsNone(self.runtime.get("job-1")["hint"])
                self.assertEqual(self.lease.releases, ["process_reaped"])

    def test_unknown_proof_does_not_release_execution(self):
        self.submit(request())
        self.workers[0].complete(proof="task_cancelled")
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "stopping")
        self.assertEqual(self.lease.held, "job-1")

    def test_unadmitted_snapshot_cannot_launch_worker(self):
        self.runtime.admission = Mock(side_effect=ContractError("profile_not_admitted"))
        self.assertEqual(self.submit(request())["status"], "unsupported")
        self.assertFalse(self.workers)
        self.assertIsNone(self.lease.held)

    def test_operator_warmup_is_frameless_and_releases_compute(self):
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.lease.held, "operator-warmup")
        self.assertTrue(self.workers[0].sent[0]["_operator_warmup"])
        self.workers[0].complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.runtime.metrics()["stored_jobs"], 0)

    def test_idle_timeout_releases_memory_only_after_reap(self):
        self.submit(request())
        self.workers[0].complete()
        self.runtime.tick()
        self.clock.now += 120
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)
        self.workers[0].alive = False
        self.runtime.tick()
        self.assertFalse(self.runtime.resident)
        self.assertIsNone(self.residency[-1])

    def test_shutdown_never_dispatches_pending_and_retains_unknown_lease(self):
        self.submit(request())
        self.submit(request("job-2"))
        self.runtime.close()
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-2")["status"], "cancelled")
        self.assertEqual(self.lease.held, "job-1")
        self.clock.now += 2
        self.runtime.tick()
        self.assertTrue(self.workers[0].kill_requested)
        self.workers[0].alive = False
        self.runtime.tick()
        self.assertIsNone(self.lease.held)

    def test_health_is_cached_and_never_hashes_or_probes(self):
        admission = Mock(return_value=DIGEST)
        self.runtime.admission = admission
        self.runtime.refresh_admission()
        self.runtime.probe.free_bytes = Mock(side_effect=AssertionError("health probed driver"))
        for _ in range(10):
            health = self.runtime.health()
            self.assertEqual(health["model_revision"], PROFILE["revision"])
            self.assertEqual(health["observed_at"], 100)
            self.assertTrue(health["offline"])
        admission.assert_called_once()

    def test_submit_and_health_do_not_wait_for_driver_query(self):
        entered, release, health_returned = threading.Event(), threading.Event(), threading.Event()
        def slow_probe():
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release probe")
            return 50 * GIB
        self.runtime.probe.free_bytes = Mock(side_effect=slow_probe)
        result = self.runtime.submit(request())
        self.assertEqual(result["status"], "queued")
        self.runtime.probe.free_bytes.assert_not_called()
        dispatch = threading.Thread(target=self.runtime.tick)
        dispatch.start()
        self.assertTrue(entered.wait(1))
        health = threading.Thread(target=lambda: (self.runtime.health(), health_returned.set()))
        health.start()
        try:
            self.assertTrue(health_returned.wait(.5), "health blocked behind driver observation")
        finally:
            release.set()
            dispatch.join(timeout=2)
            health.join(timeout=2)
        self.assertEqual(self.runtime.get("job-1")["status"], "running")

    def test_failed_lease_release_never_exposes_hint_or_dispatches_again(self):
        self.submit(request())
        self.lease.release = Mock(return_value=False)
        self.workers[0].complete()
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["hint"])
        self.assertTrue(self.runtime.health()["execution_lease_held"])
        self.submit(request("job-2"))
        self.workers[0].alive = False
        self.runtime.tick()
        self.assertTrue(self.runtime.health()["resource_fault"])
        self.assertEqual(len(self.workers), 1)


class ContractTests(unittest.TestCase):
    def test_complete_causal_5hz_clip_records_repeated_frames(self):
        clip = validate_request(request(), profile=PROFILE, now=100)
        self.assertEqual(sum(f["repeated"] for f in clip["frames"]), 12)
        self.assertEqual(len(clip["frames"]), 32)

    def test_future_stale_incomplete_or_recentered_input_is_rejected(self):
        changes = [lambda r: r.update(cutoff_at=100.001),
                   lambda r: r.update(expires_at=110.001),
                   lambda r: r.update(roi_xyxy=[0, 0, 320, 360]),
                   lambda r: r["frames"][0].update(track_bbox_xyxy=[-1, 0, 100, 100]),
                   lambda r: r["frames"][0].update(repeated=True),
                   lambda r: r["frames"][0].update(captured_at=101),
                   lambda r: r["frames"][0].update(blob_ref="../../private.ppm"),
                   lambda r: r["frames"][0]["source_frame"].update(arrival_mono=100.001),
                   lambda r: r["frames"][0]["source_frame"].update(camera_epoch=3),
                   lambda r: r["frames"][0]["source_frame"]["rgb"].update(device_s=None),
                   lambda r: r["dependencies"].update(source_generation="d" * 64)]
        for mutate in changes:
            with self.subTest(mutate=mutate):
                value = request()
                mutate(value)
                with self.assertRaises(ContractError):
                    validate_request(value, profile=PROFILE, now=100)

    def test_ppm_storage_digest_and_rgb_size_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            rgb = bytes(range(256)) * 3
            blob = store_rgb_blob(directory, rgb, 16, 16)
            self.assertEqual(blob, store_rgb_blob(directory, rgb, 16, 16))
            clip = {"source_size": [16, 16], "frames": [blob, blob]}
            self.assertEqual(load_rgb_frames(clip, directory), [rgb, rgb])
            file = Path(directory) / blob["blob_ref"]
            file.write_bytes(file.read_bytes()[:-1] + b"x")
            with self.assertRaisesRegex(ContractError, "digest_mismatch"):
                load_rgb_frames(clip, directory)

    def test_duplicate_json_keys_are_refused(self):
        with self.assertRaises(ContractError):
            strict_json('{"a":1,"a":2}')

    def test_package_imports_no_heavy_runtime(self):
        root = str(Path(__file__).resolve().parents[1])
        script = ("import sys;sys.path.insert(0," + repr(root) + ");"
                  "import den_models.runtime,den_models.server;"
                  "assert not {'torch','numpy','transformers','cv2'} & set(sys.modules)")
        completed = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())

    def test_runtime_inventory_is_complete_and_receipt_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            versions = {key: value for key, value in PROFILE["runtime"].items() if key not in ("python", "cuda")}
            manifest = {"schema": "den.model-runtime/v1", "profile_digest": profile_digest(PROFILE),
                        "python": "3.12", "cuda": "13.0", "reviewed": True, "distributions": versions,
                        "wheel_sha256": {key: "b" * 64 for key in versions},
                        "record_sha256": {key: "c" * 64 for key in versions}}
            receipt = {"runtime_manifest_sha256": hashlib.sha256(canonical(manifest).encode()).hexdigest()}
            path = Path(directory) / (PROFILE["id"] + ".runtime.json")
            with self.assertRaisesRegex(ContractError, "wheel_closure_not_admitted"):
                read_runtime_manifest(PROFILE, directory, receipt)
            path.write_text(canonical(manifest))
            self.assertEqual(read_runtime_manifest(PROFILE, directory, receipt), manifest)
            del manifest["wheel_sha256"]["torch"]
            path.write_text(canonical(manifest))
            with self.assertRaisesRegex(ContractError, "manifest_digest_mismatch"):
                read_runtime_manifest(PROFILE, directory, receipt)
            receipt["runtime_manifest_sha256"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
            with self.assertRaisesRegex(ContractError, "incomplete_runtime_wheel_closure"):
                read_runtime_manifest(PROFILE, directory, receipt)

    def test_profiles_have_real_revision_and_explicit_admission_gaps(self):
        for name in ("vjepa-lm-qwen3-8b", "vjepa-lm-qwen3-4b"):
            profile = load_profile(name)
            self.assertEqual(len(profile["revision"]), 40)
            self.assertEqual(profile["tokenizer_revision"], profile["revision"])
            self.assertEqual(profile["admission_status"], "not_admitted")
            self.assertEqual(profile["adapter_bundle_sha256"], adapter_bundle_digest())
            self.assertTrue(any(v is None for v in profile["files"].values()))
            for value in profile["files"].values():
                if value is not None:
                    self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_backend_uses_publisher_tuple_api_and_bounds_chat(self):
        backend = VjepaBackend({}, PROFILE, clock=lambda: 100)
        backend.load = Mock()
        backend.native_gate = Mock()
        payload = (object(), object())
        backend.preprocess = Mock(return_value=payload)
        backend.torch = Mock(inference_mode=nullcontext)
        backend.model = Mock()
        backend.model.choose.return_value = (1, [1., 2., 3., 4., 5.])
        output = backend.infer(request())
        self.assertEqual(output["intent"], "stop")
        self.assertIs(backend.model.choose.call_args.args[0], payload)
        self.assertEqual(backend.model.choose.call_args.args[2], list(CHOICES))
        clip = request()
        clip["mode"] = "chat"
        backend.model.chat.return_value = "The person may stop."
        self.assertEqual(backend.infer(clip)["intent"], "unclear")
        self.assertEqual(backend.model.chat.call_args.kwargs, {"max_new_tokens": 64})

    def test_snapshot_requires_every_file_and_known_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            config = canonical({"auto_map": {"AutoModel": "modeling.Jepa", "AutoConfig": "configuration.Jepa"},
                                "model_type": "jepa_llm"})
            contents = {"config.json": config.encode(), "model.safetensors.index.json":
                        canonical({"weight_map": {"weight": "model.safetensors"}}).encode(),
                        "model.safetensors": b"not-loaded-test-weight"}
            profile = {"id": "test", "revision": "1" * 40, "auto_model": "modeling.Jepa",
                       "auto_config": "configuration.Jepa",
                       "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
            path = Path(directory) / "test" / profile["revision"]
            path.mkdir(parents=True)
            for name, data in contents.items():
                (path / name).write_bytes(data)
            receipt = {"schema": "den.model-admission/v1", "profile_digest": profile_digest(profile),
                       "runtime_manifest_sha256": "d" * 64,
                       "revision": profile["revision"], "admission_scope": "bounded_trial",
                       "expires_at_utc": 9000000000, "code_reviewed": True,
                       "research_noncommercial_use_accepted": True, "files": dict(profile["files"])}
            self.assertEqual(verify_snapshot(profile, directory, receipt)[0], path)
            (path / "model.safetensors").write_bytes(b"changed")
            with self.assertRaisesRegex(ContractError, "digest_mismatch"):
                verify_snapshot(profile, directory, receipt)
            del receipt["files"]["model.safetensors"]
            with self.assertRaisesRegex(ContractError, "incomplete_snapshot_manifest"):
                check_receipt(profile, receipt)

    def test_file_execution_lease_never_steals_or_releases_without_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gpu.lease"
            first, second = FileExecutionLease(path), FileExecutionLease(path)
            token = first.try_acquire("job")
            self.assertIsNone(second.try_acquire("job2"))
            self.assertFalse(first.release(token, "timeout"))
            self.assertTrue(path.exists())
            self.assertTrue(first.release(token, "process_reaped"))
            self.assertIsNotNone(second.try_acquire("job2"))


if __name__ == "__main__":
    unittest.main()
