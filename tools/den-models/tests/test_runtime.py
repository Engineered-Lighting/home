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
from den_models.contracts import (CHOICES, MAX_HOLD_SECONDS, SAMPLING, ContractError, canonical, load_rgb_frames,
                                 make_hint, profile_digest, store_rgb_blob, strict_json, validate_request)
from den_models.resources import DependencyFlap, FileExecutionLease
from den_models.runtime import CLIP_WINDOW_SECONDS, LIVE_JOB_CEILING_SECONDS, Runtime

GIB = 1024**3
PROFILE = load_profile("vjepa-lm-qwen3-8b")
DIGEST = "a" * 64


def dependencies(transform="transform"):
    return {"camera_id": "den", "service_run_id": "run", "camera_epoch": "2",
            "source_generation": hashlib.sha256(canonical(["run", 2]).encode()).hexdigest(),
            "track_id": "track-1", "association_revision": "3", "transform_id": transform,
            "scan_revision": "scan", "calibration_revision": "c" * 64}


def request(identity="job-1", cutoff=100, expiry=None, duration=4, transform="transform"):
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
                  "transform_id": transform, "scan_revision": "scan", "capture_mono": captured,
                  "arrival_mono": captured + .01, "capture_ts": 1800000000 + captured}
        frames.append({"frame_id": str(index), "blob_ref": f"frame{index}.ppm", "sha256": "1" * 64,
                       "captured_at": captured, "sample_at": sample, "repeated": index in seen,
                       "track_bbox_xyxy": [10, 10, 100, 200], "source_frame": source})
        seen.add(index)
    return {"schema": "den.intent-request/v1", "request_id": identity, "clip_id": "clip-" + identity,
            "profile_digest": profile_digest(PROFILE), "dependencies": dependencies(transform), "frames": frames,
            "roi_xyxy": [0, 0, 360, 360], "source_size": [640, 360], "cutoff_at": cutoff,
            "expires_at": cutoff + 2 if expiry is None else expiry, "duration_seconds": duration,
            "mode": "choose", "sampling": SAMPLING}


class Clock:
    now = 100.0
    def __call__(self):
        return self.now


class Control:
    def __init__(self):
        self.state = {"qwen_busy": False, "unallocated_reserve_bytes": 0, "dependencies": [dependencies()]}
        self.lapsed = False
    def read(self, now):
        if self.lapsed:  # Only the Den binding lapsed; every other check held (ControlState's verified state).
            raise DependencyFlap("control_state_unavailable", dict(copy.deepcopy(self.state), dependencies=[]))
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

    def warm(self):
        """Operator warmup to residency: the only path that starts a child."""
        self.runtime.warmup()
        self.runtime.tick()
        worker = self.workers[-1]
        worker.complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)
        return worker

    def test_real_native_completion_releases_execution_not_residency(self):
        self.warm()
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
        self.assertEqual(self.lease.releases, ["cuda_synchronized", "cuda_synchronized"])
        self.assertTrue(self.runtime.health()["resident"])
        self.assertIsNotNone(self.residency[-1])

    def test_cancel_holds_execution_until_proof_and_keeps_resident_worker(self):
        worker = self.warm()
        self.submit(request())
        self.assertEqual(self.runtime.cancel("job-1")["status"], "stopping")
        self.assertFalse(worker.terminate_requested)
        self.submit(request("job-2"))
        self.runtime.tick()
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.lease.held, "job-1")
        worker.complete()  # Late message cannot undo cancellation.
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual((job["status"], job["reason"], job["hint"]), ("cancelled", "caller_cancelled", None))
        self.assertEqual(self.lease.releases, ["cuda_synchronized", "cuda_synchronized"])
        self.runtime.tick()  # Observe resources for the now-eligible pending job.
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.lease.held, "job-2")
        self.assertFalse(worker.terminate_requested)

    def test_one_latest_pending_and_idempotent_request(self):
        self.warm()
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

    def test_qwen_busy_and_unknown_control_defer_without_dispatch(self):
        worker = self.warm()
        self.control.state["qwen_busy"] = True
        self.assertEqual(self.submit(request())["reason"], "qwen_busy")
        self.assertEqual(len(worker.sent), 1)  # The warmup only.
        self.control.state = None
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["reason"], "control_state_unavailable")
        self.assertEqual(len(self.workers), 1)

    def test_cold_and_warm_incremental_reservation(self):
        self.probe.free = 38 * GIB - 1
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.runtime.health()["deferred_reason"], "gpu_headroom")
        self.assertFalse(self.workers)
        self.probe.free = 38 * GIB
        self.runtime.tick()
        self.workers[0].complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.probe.free = 16 * GIB
        self.assertEqual(self.submit(request("job-2"))["status"], "running")
        self.workers[0].complete("job-2")
        self.runtime.tick()
        self.control.state["unallocated_reserve_bytes"] = 1
        self.assertEqual(self.submit(request("job-3"))["reason"], "gpu_headroom")

    def test_host_memory_does_not_fall_back_to_unsafe_cpu_load(self):
        self.host.available = 32 * GIB - 1
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.runtime.health()["deferred_reason"], "host_memory_headroom")
        self.assertFalse(self.workers)
        self.host.available = 32 * GIB
        self.runtime.tick()
        self.assertTrue(self.runtime.health()["startup_in_progress"])

    def test_expiry_and_invalidation_discard_result_but_keep_resident_worker(self):
        for reason in ("expiry", "dependency", "lapse"):
            with self.subTest(reason=reason):
                self.setUp()
                worker = self.warm()
                self.submit(request())
                if reason == "expiry":
                    self.clock.now = 102
                elif reason == "dependency":
                    self.control.state["dependencies"] = []
                else:
                    self.control.lapsed = True
                self.runtime.tick()
                self.assertEqual(self.runtime.get("job-1")["status"], "stopping")
                self.assertEqual(self.lease.held, "job-1")
                worker.complete()
                self.runtime.tick()
                job = self.runtime.get("job-1")
                self.assertEqual(job["status"], "expired" if reason == "expiry" else "invalidated")
                self.assertIsNone(job["hint"])
                self.assertIsNone(self.lease.held)
                self.assertEqual(self.lease.releases, ["cuda_synchronized", "cuda_synchronized"])
                self.assertFalse(worker.terminate_requested)
                self.assertTrue(self.runtime.resident)

    def test_other_control_failures_while_running_still_retire(self):
        # Ops backoff, Qwen evidence or attestation failures are not a lapse.
        worker = self.warm()
        self.submit(request())
        self.control.state = None
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "stopping")
        self.assertTrue(worker.terminate_requested)
        worker.complete()  # A retiring child's message is not proof.
        self.runtime.tick()
        self.assertEqual(self.lease.held, "job-1")
        worker.alive = False
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual((job["status"], job["hint"]), ("invalidated", None))
        self.assertEqual(self.lease.releases, ["cuda_synchronized", "process_reaped"])
        self.assertFalse(self.runtime.resident)

    def test_lost_worker_requires_reap_proof(self):
        worker = self.warm()
        self.submit(request())
        worker.alive = False
        self.runtime.tick()
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.runtime.get("job-1")["status"], "failed")
        self.assertIsNone(self.runtime.get("job-1")["hint"])
        self.assertEqual(self.lease.releases, ["cuda_synchronized", "process_reaped"])
        self.assertFalse(self.runtime.resident)

    def test_unknown_proof_does_not_release_execution(self):
        self.warm()
        self.submit(request())
        self.workers[0].complete(proof="task_cancelled")
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "stopping")
        self.assertEqual(self.lease.held, "job-1")
        self.assertTrue(self.workers[0].terminate_requested)

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
        self.warm()
        self.submit(request())
        self.workers[0].complete()
        self.runtime.tick()
        self.clock.now += PROFILE["execution"]["idle_seconds"]
        self.runtime.tick()
        self.assertTrue(self.workers[0].terminate_requested)
        self.assertTrue(self.runtime.resident)
        self.workers[0].alive = False
        self.runtime.tick()
        self.assertFalse(self.runtime.resident)
        self.assertIsNone(self.residency[-1])

    def test_shutdown_never_dispatches_pending_and_retains_unknown_lease(self):
        self.warm()
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
        self.warm()
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
        self.warm()
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

    # M2 residency: live clips never cold-start, and their expiry never unloads the model.
    def test_warmup_then_live_jobs_reuse_the_single_resident_worker(self):
        worker = self.warm()
        for identity in ("job-1", "job-2"):
            self.assertEqual(self.submit(request(identity))["status"], "running")
            worker.complete(identity)
            self.runtime.tick()
            self.assertEqual(self.runtime.get(identity)["status"], "succeeded")
        self.assertEqual(len(self.workers), 1)
        self.assertEqual([m.get("_operator_warmup", False) for m in worker.sent], [True, False, False])

    def test_live_expiry_never_terminates_or_restarts_the_resident_worker(self):
        worker = self.warm()
        self.submit(request())
        self.clock.now = 102
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "stopping")
        self.assertFalse(worker.terminate_requested)
        self.assertEqual(self.submit(request("job-2", cutoff=102))["status"], "queued")
        worker.complete()
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["status"], "expired")
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-2")["status"], "running")
        self.assertEqual(worker.sent[-1]["request_id"], "job-2")
        self.assertEqual(len(self.workers), 1)
        self.assertFalse(worker.terminate_requested or worker.kill_requested)

    def test_non_resident_live_job_is_refused_without_starting_a_worker(self):
        job = self.submit(request())
        self.assertEqual((job["status"], job["reason"], job["hint"]), ("unsupported", "not_resident", None))
        self.assertFalse(self.workers)
        self.assertIsNone(self.lease.held)
        health = self.runtime.health()
        self.assertEqual(health["deferred_reason"], "not_resident")
        self.assertTrue(health["warmup_required"])
        # A pending operator warmup is never superseded by a live clip.
        self.runtime.warmup()
        self.assertFalse(self.runtime.health()["warmup_required"])
        self.assertEqual(self.submit(request("job-2"))["reason"], "not_resident")
        self.assertTrue(self.workers[0].sent[0]["_operator_warmup"])

    def test_residency_lost_while_a_clip_waits_refuses_it_instead_of_cold_starting(self):
        worker = self.warm()
        self.control.state["qwen_busy"] = True
        self.submit(request())
        worker.alive = False
        self.runtime.tick()
        self.control.state["qwen_busy"] = False
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["reason"], "not_resident")
        self.assertEqual(len(self.workers), 1)

    def test_resource_faults_and_admission_loss_still_retire(self):
        worker = self.warm()
        self.submit(request())
        worker.messages.append({"kind": "failed", "request_id": "job-1", "proof": "cuda_synchronized",
                                "reason": "gpu_headroom_changed_before_native_work"})
        self.runtime.tick()
        self.assertTrue(worker.terminate_requested)
        self.setUp()
        worker = self.warm()
        self.submit(request())
        self.lease.release = Mock(return_value=False)
        worker.complete()
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["reason"], "execution_lease_release_unproven")
        self.assertTrue(worker.terminate_requested)
        self.setUp()
        worker = self.warm()
        self.submit(request())
        self.clock.now = 102
        self.runtime.tick()  # The live clip expired first: discard only.
        self.assertFalse(worker.terminate_requested)
        self.runtime.admission_valid_until_utc = 1
        self.runtime.tick()
        self.assertTrue(worker.terminate_requested)
        worker.alive = False
        self.runtime.tick()
        self.assertEqual(self.runtime.get("job-1")["reason"], "admission_expired")

    def test_child_refusal_before_native_work_keeps_the_worker(self):
        worker = self.warm()
        self.submit(request())
        worker.messages.append({"kind": "failed", "request_id": "job-1", "proof": "cuda_synchronized",
                                "reason": "clip_expired_before_native_work"})
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual((job["status"], job["reason"]), ("expired", "clip_expired_before_native_work"))
        self.assertFalse(worker.terminate_requested)
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.submit(request("job-2"))["status"], "running")
        self.assertEqual(len(self.workers), 1)

    def test_past_expiry_completion_and_expired_hints_are_never_emitted(self):
        worker = self.warm()
        succeeded = self.runtime.counters["succeeded"]  # The warmup.
        self.submit(request())
        worker.complete()
        self.clock.now = 102  # Completion is first observed after expiry.
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual((job["status"], job["hint"]), ("expired", None))
        self.assertEqual(self.runtime.counters["succeeded"], succeeded)
        self.submit(request("job-2", cutoff=102))
        worker.complete("job-2")
        self.runtime.tick()
        self.assertIsNotNone(self.runtime.get("job-2")["hint"])
        self.clock.now = 104
        self.assertIsNone(self.runtime.get("job-2")["hint"])

    def test_hung_live_job_hits_the_hard_ceiling_and_retires(self):
        worker = self.warm()
        self.submit(request())
        self.clock.now = 102
        self.runtime.tick()
        self.assertFalse(worker.terminate_requested)
        self.clock.now = 100 + LIVE_JOB_CEILING_SECONDS
        self.runtime.tick()
        self.assertTrue(worker.terminate_requested)
        worker.complete()  # Too late: a retiring child's message is not proof.
        self.runtime.tick()
        self.assertEqual(self.lease.held, "job-1")
        worker.alive = False
        self.runtime.tick()
        job = self.runtime.get("job-1")
        self.assertEqual((job["status"], job["reason"], job["hint"]), ("failed", "live_job_ceiling_exceeded", None))
        self.assertEqual(self.lease.releases[-1], "process_reaped")
        self.assertFalse(self.runtime.resident)

    def test_idle_retirement_requires_an_explicit_fresh_warmup(self):
        worker = self.warm()
        self.clock.now += PROFILE["execution"]["idle_seconds"]
        self.runtime.tick()
        self.assertTrue(worker.terminate_requested)
        worker.alive = False
        self.runtime.tick()
        self.assertTrue(self.runtime.health()["warmup_required"])
        self.assertEqual(self.submit(request(cutoff=self.clock.now))["reason"], "not_resident")
        self.assertEqual(len(self.workers), 1)
        self.warm()
        self.assertEqual(len(self.workers), 2)
        self.assertFalse(self.runtime.health()["warmup_required"])

    def test_clip_submitted_at_the_idle_boundary_still_dispatches(self):
        worker = self.warm()
        self.clock.now += PROFILE["execution"]["idle_seconds"]
        self.assertEqual(self.submit(request(cutoff=self.clock.now))["status"], "running")
        self.assertFalse(worker.terminate_requested)

    def test_deferred_clips_never_extend_idle_residency(self):
        idle = PROFILE["execution"]["idle_seconds"]
        for cause in ("qwen_busy", "control_unavailable", "lapse"):
            for ticks_per_clip in (20, 1):
                with self.subTest(cause=cause, ticks_per_clip=ticks_per_clip):
                    self.setUp()
                    worker = self.warm()
                    start = self.clock.now
                    if cause == "qwen_busy":
                        self.control.state["qwen_busy"] = True
                    elif cause == "lapse":
                        self.control.lapsed = True
                    else:
                        self.control.state = None
                    # Den submits a 2 s clip each second; the server ticks at 20 Hz.
                    for step in range((idle + CLIP_WINDOW_SECONDS + 2) * ticks_per_clip):
                        self.clock.now = start + step / ticks_per_clip
                        if step % ticks_per_clip == 0:
                            self.runtime.submit(request(f"job-{step}", cutoff=self.clock.now))
                        self.runtime.tick()
                        if worker.terminate_requested:
                            break
                    self.assertTrue(worker.terminate_requested)
                    self.assertEqual(self.runtime.counters["dispatched"], 1)  # The warmup only.
                    # A clip no tick has evaluated yet holds residency for at most one clip window.
                    limit = idle + (.05 if ticks_per_clip == 20 else CLIP_WINDOW_SECONDS)
                    self.assertLessEqual(self.clock.now - start, limit + 1e-9)

    # M3: dependencies gate live clips only; warmup needs no Den binding.
    def test_warmup_dispatches_and_completes_through_a_pure_dependency_lapse(self):
        self.control.lapsed = True
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.lease.held, "operator-warmup")
        worker = self.workers[0]
        self.assertTrue(worker.sent[0]["_operator_warmup"])
        for _ in range(3):  # The lapse continues while the child hashes and loads.
            self.clock.now += 30
            self.runtime.tick()
        self.assertFalse(worker.terminate_requested)
        self.assertTrue(self.runtime.health()["startup_in_progress"])
        worker.complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.runtime.counters["warmups_completed"], 1)
        # Live clips stay refused while the binding is lapsed; the warmup is still the only dispatch.
        self.assertEqual(self.submit(request(cutoff=self.clock.now))["reason"], "control_state_unavailable")
        self.assertEqual(self.runtime.counters["dispatched"], 1)

    def test_warmup_lapse_flips_never_retire_the_loading_child(self):
        self.runtime.warmup()
        self.runtime.tick()
        worker = self.workers[0]
        for step in range(400):  # Matching-to-None flips, as the M3 replay measured per bound minute and more.
            self.clock.now += .05
            self.control.lapsed = step % 4 == 0
            self.runtime.tick()
        self.assertFalse(worker.terminate_requested)
        worker.complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)

    def test_warmup_during_a_lapse_still_respects_qwen_headroom_and_other_failures(self):
        # Qwen busy and GPU headroom still come from the verified lapse state.
        self.control.lapsed = True
        self.control.state["qwen_busy"] = True
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.runtime.health()["deferred_reason"], "qwen_busy")
        self.control.state["qwen_busy"] = False
        self.control.state["unallocated_reserve_bytes"] = 50 * GIB
        self.runtime.tick()
        self.assertEqual(self.runtime.health()["deferred_reason"], "gpu_headroom")
        self.assertFalse(self.workers)
        # Any other control failure (Ops backoff, Qwen evidence, attestation) gates nothing, as before.
        self.control.state["unallocated_reserve_bytes"] = 0
        self.control.lapsed, self.control.state = False, None
        self.runtime.tick()
        self.assertEqual(self.runtime.health()["deferred_reason"], "control_state_unavailable")
        self.assertFalse(self.workers)
        for cause in ("control_unavailable", "admission_expired", "flap_without_verified_state"):
            with self.subTest(cause=cause):
                self.setUp()
                self.runtime.warmup()
                self.runtime.tick()
                worker = self.workers[0]
                self.control.lapsed = True
                self.runtime.tick()
                self.assertFalse(worker.terminate_requested)
                if cause == "control_unavailable":
                    self.control.lapsed, self.control.state = False, None
                elif cause == "admission_expired":
                    self.runtime.admission_valid_until_utc = 1
                else:
                    # A lapse still never retires a running warmup, but cannot dispatch one without verified state.
                    self.control.read = Mock(side_effect=DependencyFlap("control_state_unavailable"))
                    self.runtime.tick()
                    self.assertFalse(worker.terminate_requested)
                    worker.alive = False
                    self.runtime.tick()
                    self.runtime.warmup()
                    self.runtime.tick()
                    self.assertEqual(self.runtime.health()["deferred_reason"], "control_state_unavailable")
                    self.assertEqual(len(self.workers), 1)
                    continue
                self.runtime.tick()
                self.assertTrue(worker.terminate_requested)
                worker.alive = False
                self.runtime.tick()
                self.assertFalse(self.runtime.resident)
                self.assertEqual(self.lease.releases, ["process_reaped"])

    def test_trial_window_keeps_a_quiet_resident_worker_inside_the_hard_cap(self):
        execution = PROFILE["execution"]
        self.assertEqual((execution["idle_seconds"], execution["startup_timeout_seconds"],
                          execution["lifetime_seconds"]), (900, 900, 1800))
        # Hash every weight byte at the I/O cap twice (verify, then an uncached load) inside the startup deadline.
        self.assertLess(2 * PROFILE["weights_total_bytes"] / execution["io_read_bytes_per_second"],
                        execution["startup_timeout_seconds"] / 2)
        self.runtime.warmup()
        self.runtime.tick()
        self.assertEqual(self.runtime.pending, None)
        self.assertEqual(self.runtime.active["request"]["expires_at"], 100 + 900)
        worker = self.workers[0]
        self.clock.now += 600  # A slow cold load still inside the startup deadline.
        worker.complete("operator-warmup", kind="ready")
        self.runtime.tick()
        self.assertTrue(self.runtime.resident)
        self.clock.now += 899  # A quiet Den: no clip for almost 15 minutes.
        self.runtime.tick()
        self.assertFalse(worker.terminate_requested)
        self.assertEqual(self.submit(request(cutoff=self.clock.now))["status"], "running")
        worker.complete("job-1")
        self.runtime.tick()
        self.clock.now = 100 + execution["lifetime_seconds"]  # The hard cap still ends the run.
        self.runtime.tick()
        self.assertTrue(worker.terminate_requested)


def request_at(captures, cutoff, duration=4, identity="job-hold"):
    """Den's causal sample-hold over explicit capture times: newest capture at or before each slot centre."""
    value = request(identity, cutoff=cutoff, duration=duration)
    frames, seen = [], set()
    for frame in value["frames"]:
        index = max(k for k, stamp in enumerate(captures) if stamp <= frame["sample_at"])
        captured = captures[index]
        rgb = dict(frame["source_frame"]["rgb"], seq=index, device_s=captured - 50, host_s=captured - 10)
        source = dict(frame["source_frame"], frame_id=index, capture_mono=captured, arrival_mono=captured + .01,
                      capture_ts=1800000000 + captured, rgb=rgb)
        frames.append(dict(frame, frame_id=str(index), blob_ref=f"frame{index}.ppm", captured_at=captured,
                           repeated=index in seen, source_frame=source))
        seen.add(index)
    value["frames"] = frames
    return value


def holds(value):
    return [frame["sample_at"] - frame["captured_at"] for frame in value["frames"]]


class ContractTests(unittest.TestCase):
    def test_v2_sampling_admits_a_half_second_hold_and_names_its_rule(self):
        self.assertEqual((SAMPLING, MAX_HOLD_SECONDS), ("causal_slot_center_sample_hold/v2;max_hold_s=0.5", .5))
        for name in ("vjepa-lm-qwen3-8b", "vjepa-lm-qwen3-4b"):
            self.assertEqual(load_profile(name)["inputs"]["sampling"], SAMPLING)
        # Den's measured ~2.3 frames/s: every hold under 0.5 s, some over the former 0.4 s.
        captures = [100 - 8 + k / 2.3 for k in range(int(8 * 2.3) + 1)]
        for duration in (4, 8):
            with self.subTest(duration=duration):
                value = request_at(captures, 100, duration)
                self.assertGreater(max(holds(value)), .4)
                self.assertLessEqual(max(holds(value)), MAX_HOLD_SECONDS)
                clip = validate_request(value, profile=PROFILE, now=100)
                self.assertEqual(clip["sampling"], SAMPLING)
                self.assertTrue(any(frame["repeated"] for frame in clip["frames"]))
                self.assertEqual([f["captured_at"] for f in clip["frames"]], [f["captured_at"] for f in value["frames"]])
                hint = make_hint(clip, PROFILE, DIGEST, {"intent": "continue", "raw_option_scores": None, "text": None}, 100)
                self.assertEqual(hint.evidence["sampling"], SAMPLING)

    def test_holds_over_half_a_second_and_other_sampling_rules_are_refused(self):
        # 1.9 frames/s: some slot would hold a frame longer than 0.5 s.
        sparse = request_at([100 - 5 + k / 1.9 for k in range(10)], 100)
        self.assertGreater(max(holds(sparse)), MAX_HOLD_SECONDS)
        with self.assertRaisesRegex(ContractError, "noncausal_or_stale_frame"):
            validate_request(sparse, profile=PROFILE, now=100)
        for sampling in ("causal_slot_center_sample_hold/v1", "causal_slot_center_sample_hold/v2",
                         "causal_slot_center_sample_hold/v2;max_hold_s=0.6", None):
            with self.subTest(sampling=sampling), self.assertRaisesRegex(ContractError, "unsupported_sampling"):
                validate_request(dict(request(), sampling=sampling), profile=PROFILE, now=100)
        value = request()
        del value["sampling"]
        with self.assertRaisesRegex(ContractError, "invalid_request_keys"):
            validate_request(value, profile=PROFILE, now=100)
        # A profile that names another rule admits nothing, even for a v2 request bound to it.
        other = copy.deepcopy(PROFILE)
        other["inputs"]["sampling"] = "causal_slot_center_sample_hold/v1"
        value = dict(request(), profile_digest=profile_digest(other))
        with self.assertRaisesRegex(ContractError, "unsupported_sampling"):
            validate_request(value, profile=other, now=100)

    def test_first_stamp_may_precede_the_window_by_the_hold(self):
        # Slot 0 (cutoff - 4 + 0.0625) holding a frame for 0.45 s: older than the former cutoff-duration-0.25 bound.
        first = 100 - 4 + .0625 - .45
        value = request_at([first] + [100 - 4 + .1 + k * .2 for k in range(20)], 100)
        self.assertLess(value["frames"][0]["captured_at"], 100 - 4 - .25)
        self.assertEqual(validate_request(value, profile=PROFILE, now=100)["frames"][0]["captured_at"], first)
        value["frames"][0]["captured_at"] = value["frames"][0]["source_frame"]["capture_mono"] = 100 - 4 - .5 - 1e-3
        with self.assertRaisesRegex(ContractError, "noncausal_or_stale_frame"):
            validate_request(value, profile=PROFILE, now=100)

    def test_complete_causal_5hz_clip_records_repeated_frames(self):
        clip = validate_request(request(), profile=PROFILE, now=100)
        self.assertEqual(sum(f["repeated"] for f in clip["frames"]), 12)
        self.assertEqual(len(clip["frames"]), 32)

    def test_real_den_transform_lineage_is_accepted_and_unsafe_values_stay_rejected(self):
        # M1: real Den transforms carry "@" (den-oak@2026-11-13.1, registered-room@2).
        for transform in ("den-oak@2026-11-13.1", "registered-room@2", "den-oak@other"):
            with self.subTest(transform=transform):
                clip = validate_request(request(transform=transform), profile=PROFILE, now=100)
                self.assertEqual(clip["dependencies"]["transform_id"], transform)
        for bad in ("den-oak@2026/../x", "../den-oak", "den oak@1", "den-oak@1\n", "den-oak@1\t", "",
                    "den-oak@" + "1" * 121, "den-oak@1/2", "den\\oak@1"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ContractError, "invalid_dependencies"):
                validate_request(request(transform=bad), profile=PROFILE, now=100)
        # "@" is admitted only for lineage fields, never for ids that become URLs.
        value = request()
        value["dependencies"]["track_id"] = "track@1"
        with self.assertRaisesRegex(ContractError, "invalid_dependencies"):
            validate_request(value, profile=PROFILE, now=100)
        with self.assertRaisesRegex(ContractError, "invalid_request_identity"):
            validate_request(request("job@1"), profile=PROFILE, now=100)

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
