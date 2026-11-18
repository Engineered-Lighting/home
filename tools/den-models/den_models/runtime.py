"""Single-worker, latest-pending scheduler with explicit native completion proof."""
from collections import Counter, OrderedDict
import multiprocessing
import json
from pathlib import Path
import threading
import time

from .artifacts import read_receipt, snapshot_path
from .backend import NONFATAL_FAILURES, worker_main
from .contracts import ContractError, canonical, make_hint, profile_digest, validate_request
from .resources import ControlState, DependencyFlap, FileExecutionLease, HostProbe, NvidiaProbe, process_start_identity

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "invalidated", "superseded", "unsupported"}
PROOFS = {"cuda_synchronized", "no_native_context"}
# A live clip expires at most 10 s after submission (expires_at <= cutoff+10,
# cutoff <= now). A child still busy 20 s after dispatch is hung, not slow:
# retire it rather than hold the single optional execution indefinitely.
CLIP_WINDOW_SECONDS = 10
LIVE_JOB_CEILING_SECONDS = 2 * CLIP_WINDOW_SECONDS


class SpawnWorker:
    def __init__(self, config, profile):
        context = multiprocessing.get_context("spawn")
        self.parent, self.child = context.Pipe()
        child_config = {key: config[key] for key in ("snapshot_root", "receipt_root", "frame_root",
                                                     "cache_root", "gpu_selector", "io_device", "control_state_file", "execution_lease_file",
                                                     "queue_receipt_file", "queue_receipt_gid", "qwen_observer_config_sha256",
                                                     "qwen_binding_policy_sha256")}
        self.process = context.Process(target=worker_main, args=(self.child, child_config, profile),
                                       name="den-vjepa-lm", daemon=False)
        self.started = False

    def start(self):
        self.process.start()
        self.started = True
        self.child.close()

    @property
    def pid(self):
        return self.process.pid

    @property
    def start_identity(self):
        return process_start_identity(self.process.pid)

    def send(self, request):
        self.parent.send({"command": "warmup" if request.get("_operator_warmup") else "infer",
                          "request": request})

    def poll(self):
        messages = []
        try:
            while self.parent.poll():
                messages.append(self.parent.recv())
        except (EOFError, OSError):
            pass
        return messages

    def reaped(self):
        if not self.started:
            return self.process.pid is None
        if self.process.is_alive():
            return False
        self.process.join(timeout=0)
        return self.process.exitcode is not None

    def terminate(self):
        if self.started and self.process.is_alive():
            self.process.terminate()

    def kill(self):
        if self.started and self.process.is_alive():
            self.process.kill()

    def close(self):
        self.parent.close()
        if not self.started:
            self.child.close()
        if self.started:
            self.process.close()


class Runtime:
    """Callbacks allow the Den bridge to join its shared admission broker.

    lease.try_acquire(request_id) -> opaque token or None
    lease.release(token, proof) -> bool
    residency_changed(record_or_none) -> None (memory accounting, not execution)

    A standalone file lease coordinates ONLY callers using that same file;
    it does not claim enforcement over existing Qwen/Den processes. The
    control file reports other unallocated reservations, excluding this
    worker's already driver-accounted resident bytes.
    """
    def __init__(self, config, profile, *, clock=time.monotonic, control=None, probe=None,
                 lease=None, worker_factory=SpawnWorker, admission=None, residency_changed=None, host_probe=None):
        self.config, self.profile, self.clock = dict(config), json.loads(canonical(profile)), clock
        self.control = control or ControlState(config["control_state_file"], expected_lease_file=config["execution_lease_file"],
                                               queue_receipt_file=config["queue_receipt_file"], queue_receipt_gid=config["queue_receipt_gid"],
                                               expected_qwen_config_sha256=config["qwen_observer_config_sha256"],
                                               expected_qwen_policy_sha256=config["qwen_binding_policy_sha256"])
        self.probe = probe or NvidiaProbe(config["gpu_selector"])
        self.host_probe = host_probe or HostProbe()
        self.lease = lease or FileExecutionLease(config["execution_lease_file"])
        self.worker_factory, self.admission = worker_factory, admission or self._admission
        self.residency_changed = residency_changed or (lambda record: None)
        self.lock = threading.RLock()
        self.jobs = OrderedDict()
        self.active = self.pending = self.worker = None
        self.execution_token = None
        self.resident = False
        self.worker_started_at = self.last_activity = 0.0
        self.retiring_at = None
        self.closed = False
        self.counters = Counter()
        self.last_defer_reason = None
        self.resource_fault = False
        self.admission_verified_at = self.admission_valid_until_utc = None
        self.admission_receipt_digest = None
        self.admitted = False
        self.source_validated_at = None
        self.control_valid = False
        self.driver_free_bytes = self.host_available_bytes = self.resources_observed_at = None

    def _admission(self):
        receipt, digest = read_receipt(self.profile, self.config["receipt_root"])
        path = snapshot_path(self.config["snapshot_root"], self.profile)
        if not path.is_dir() or path.is_symlink():
            raise ContractError("complete_local_snapshot_missing")
        # A cheap closure check prevents spawning a heavy child for an absent
        # snapshot. The child hashes the complete closure before code loading.
        if any(not (path / name).is_file() or (path / name).is_symlink() for name in receipt["files"]):
            raise ContractError("complete_local_snapshot_missing")
        self.admission_valid_until_utc = receipt["expires_at_utc"]
        return digest

    def refresh_admission(self):
        """Small metadata check; full hashing belongs to cold child load only."""
        with self.lock:
            try:
                digest = self.admission()
            except Exception:
                self.admitted = False
                raise
            self.admitted = True
            self.admission_receipt_digest = digest
            self.admission_verified_at = self.clock()
            return digest

    def submit(self, request):
        with self.lock:
            if self.closed:
                raise ContractError("runtime_closed")
            request = validate_request(request, profile=self.profile, now=self.clock())
            identity = request["request_id"]
            if identity in self.jobs:
                old = self.jobs[identity]
                if canonical(old["request"]) != canonical(request):
                    raise ContractError("request_id_reused")
                return self._public(old)
            self._prune()
            job = {"request": request, "status": "queued", "reason": None, "hint": None,
                   "submitted_at": self.clock(), "finished_at": None}
            self.jobs[identity] = job
            self.counters["submitted"] += 1
            try:
                self.refresh_admission()
            except (ContractError, OSError, ValueError):
                self._finish(job, "unsupported", "profile_not_admitted")
                return self._public(job)
            if not self.resident:
                # Live clips never start or load the child; a cold load cannot
                # fit their deadline. Only the operator warmup makes it resident.
                self._finish(job, "unsupported", "not_resident")
                self.last_defer_reason = "not_resident"
                return self._public(job)
            if self.pending is not None:
                self._finish(self.pending, "superseded", "newer_pending_clip")
            self.pending = job
            return self._public(job)

    def warmup(self):
        """Operator-only startup path; never exposed as an HTTP request.

        Cold hashing/loading cannot fit a live two-second hint deadline. This
        bounded, frameless operation prepares residency before live inputs.
        """
        with self.lock:
            if self.closed or self.worker is not None or self.pending is not None:
                raise ContractError("warmup_unavailable")
            self.refresh_admission()
            request = {"request_id": "operator-warmup", "_operator_warmup": True,
                       "expires_at": self.clock() + self.profile["execution"]["startup_timeout_seconds"]}
            self.pending = {"request": request, "status": "queued", "reason": None, "hint": None,
                            "submitted_at": self.clock(), "finished_at": None}

    def get(self, request_id):
        with self.lock:
            if request_id not in self.jobs:
                return None
            return self._public(self.jobs[request_id])

    def cancel(self, request_id):
        with self.lock:
            job = self.jobs.get(request_id)
            if job is None:
                return None
            if job["status"] not in TERMINAL:
                if job is self.active:
                    if job["status"] != "stopping":
                        self._discard_active("cancelled", "caller_cancelled")
                else:
                    self._finish(job, "cancelled", "caller_cancelled")
                    if self.pending is job:
                        self.pending = None
            return self._public(job)

    def _public(self, job):
        hint = job["hint"]
        if hint is not None and not self.clock() < hint["expires_at"]:
            hint = None  # Never serve a hint after its own expiry.
        value = {"schema": "den.intent-job/v1", "request_id": job["request"]["request_id"],
                "clip_id": job["request"]["clip_id"], "profile_digest": job["request"]["profile_digest"],
                "status": job["status"], "reason": job["reason"], "hint": hint,
                "submitted_at": job["submitted_at"], "finished_at": job["finished_at"]}
        return json.loads(canonical(value))

    def _finish(self, job, status, reason=None):
        if job["status"] in TERMINAL:
            return
        job.update(status=status, reason=reason, finished_at=self.clock())
        self.counters[status] += 1

    def _prune(self):
        for key in list(self.jobs):
            if len(self.jobs) < 64:
                break
            if self.jobs[key]["status"] in TERMINAL:
                del self.jobs[key]
        if len(self.jobs) >= 64:
            raise ContractError("bounded_result_store_full")

    def _release_execution(self, proof):
        if self.execution_token is None:
            return True
        try:
            released = self.lease.release(self.execution_token, proof)
        except Exception:
            released = False
        if not released:
            self.resource_fault = True
            self.last_defer_reason = "execution_lease_release_unproven"
            return False
        self.execution_token = None
        return True

    def _retire(self):
        if self.worker is not None and self.retiring_at is None:
            self.retiring_at = self.clock()
            self.worker.terminate()

    def _stop_active(self, status, reason):
        """Stop by retiring the child; an escalated discard takes this reason."""
        job = self.active
        if job is not None and (job["status"] != "stopping" or job.pop("discard", False)):
            job.update(status="stopping", reason=reason, stopping_status=status)
        self._retire()

    def _discard_active(self, status, reason):
        """An unwanted live result (expired, cancelled, invalidated).

        The resident child is kept and no hint is emitted. The execution
        lease stays held until this request's own synchronized completion
        proof or the child's reap; LIVE_JOB_CEILING_SECONDS bounds the wait.
        """
        self.active.update(status="stopping", reason=reason, stopping_status=status, discard=True)

    def _compatible(self, job, state):
        return (job["request"].get("_operator_warmup") is True
                or job["request"]["dependencies"] in state["dependencies"])

    def tick(self):
        # Observe potentially slow driver subprocesses outside the state lock.
        # HTTP submit/poll/health can continue while a driver query is delayed.
        # Revalidate request identity, deadlines and control state afterwards.
        observation_job, observation, observation_error = None, None, False
        with self.lock:
            if (not self.closed and self.pending is not None and self.active is None
                    and self.retiring_at is None and not self.resource_fault
                    and self.clock() < self.pending["request"]["expires_at"]):
                try:
                    preflight = self.control.read(self.clock())
                    if not preflight["qwen_busy"] and self._compatible(self.pending, preflight):
                        observation_job = self.pending
                except (ContractError, OSError, ValueError):
                    pass
        if observation_job is not None:
            try:
                digest = self.refresh_admission()
                free, host = self.probe.free_bytes(), self.host_probe.available_bytes()
                observation = digest, free, host, self.clock()
            except (ContractError, OSError, ValueError):
                observation_error = True
        with self.lock:
            now, lapsed = self.clock(), False
            try:
                state = self.control.read(now)
                self.source_validated_at = now
                self.control_valid = True
            except (ContractError, OSError, ValueError) as exc:
                state, lapsed = None, isinstance(exc, DependencyFlap)
                self.control_valid = False
            if self.admission_valid_until_utc is not None and time.time() >= self.admission_valid_until_utc:
                self.admitted = False
                if self.active is not None:
                    self._stop_active("invalidated", "admission_expired")
                self._retire()
            if self.pending is not None:
                if now >= self.pending["request"]["expires_at"]:
                    self._finish(self.pending, "expired", "clip_expired")
                    self.pending = None
                elif state is not None and not self._compatible(self.pending, state):
                    self._finish(self.pending, "invalidated", "dependencies_changed")
                    self.pending = None
            if self.active is not None and self.active["status"] != "stopping":
                # A live clip that expires or whose Den binding changes or
                # lapses is discarded, never its resident child. Warmup has no
                # result to discard, and any other control failure (Ops
                # backoff, Qwen evidence, attestation) still retires as before.
                stop = self._stop_active if self.active["request"].get("_operator_warmup") else self._discard_active
                if now >= self.active["request"]["expires_at"]:
                    stop("expired", "clip_expired")
                elif state is None and not lapsed:
                    self._stop_active("invalidated", "dependencies_unavailable_or_changed")
                elif state is None or not self._compatible(self.active, state):
                    stop("invalidated", "dependencies_unavailable_or_changed")
            if (self.active is not None and not self.active["request"].get("_operator_warmup")
                    and self.retiring_at is None and now - self.active["dispatched_at"] >= LIVE_JOB_CEILING_SECONDS):
                self._stop_active("failed", "live_job_ceiling_exceeded")
            if self.worker is not None:
                if now - self.worker_started_at >= self.profile["execution"]["lifetime_seconds"]:
                    if self.active is not None:
                        self._stop_active("expired", "worker_lifetime_exceeded")
                    self._retire()
                for message in self.worker.poll():
                    job = self.active
                    discarding = job is not None and job.get("discard") is True and self.retiring_at is None
                    if (job is None or (job["status"] == "stopping" and not discarding)
                            or not isinstance(message, dict)
                            or message.get("request_id") != job["request"]["request_id"]
                            or message.get("proof") not in PROOFS):
                        # An unexpected protocol message cannot prove that
                        # this job's kernels stopped. Retire and reap.
                        if job is not None and (job["status"] != "stopping" or discarding):
                            self._stop_active("failed", "worker_protocol_invalid")
                        continue
                    warmup = job["request"].get("_operator_warmup") is True
                    outcome, reason, candidate_hint = "failed", "backend_failed", None
                    if message.get("kind") == "ready" and warmup:
                        if message.get("snapshot_manifest_digest") == job["admission_digest"]:
                            self.resident = True
                            outcome, reason = "succeeded", None
                        else:
                            reason = "snapshot_manifest_mismatch"
                            self._retire()
                    elif message.get("kind") == "complete" and not warmup:
                        try:
                            if message.get("snapshot_manifest_digest") != job["admission_digest"]:
                                raise ContractError("snapshot_manifest_mismatch")
                            if discarding:
                                # Synchronized completion proves the child idle
                                # again; the unwanted result is dropped here.
                                outcome, reason = job["stopping_status"], job["reason"]
                            else:
                                hint = make_hint(job["request"], self.profile,
                                                 message["snapshot_manifest_digest"], message["output"], now)
                                candidate_hint = hint.as_dict()
                                outcome, reason = "succeeded", None
                                self.resident = True
                        except (ContractError, KeyError, TypeError, ValueError):
                            reason = "backend_output_invalid"
                            self._retire()
                    elif (message.get("kind") == "failed" and not warmup
                          and message.get("reason") in NONFATAL_FAILURES):
                        # Refused before native work; the child keeps serving.
                        outcome, reason = ((job["stopping_status"], job["reason"]) if discarding else
                                           (NONFATAL_FAILURES[message["reason"]], message["reason"]))
                    else:
                        self._retire()
                    if not self._release_execution(message["proof"]):
                        outcome, reason, candidate_hint = "failed", "execution_lease_release_unproven", None
                        self._retire()
                    job["hint"] = candidate_hint
                    self._finish(job, outcome, reason)
                    if outcome == "succeeded" and job["request"].get("_operator_warmup"):
                        self.counters["warmups_completed"] += 1
                    self.active = None
                    self.last_activity = now
                if self.worker.reaped():
                    # Process exit + join is the only release proof after
                    # timeout/cancel/unknown native execution.
                    if self.active is not None:
                        job = self.active
                        self._finish(job, job.get("stopping_status", "failed"),
                                     job["reason"] or "worker_exited")
                        self.active = None
                    self._release_execution("process_reaped")
                    self.worker.close()
                    self.worker = None
                    self.resident = False
                    self.retiring_at = None
                    self.residency_changed(None)
                elif self.retiring_at is not None and now - self.retiring_at >= 2:
                    self.worker.kill()
                elif self.active is None and self._idle_expired(now):
                    # Explicit end of residency: live clips are then refused
                    # not_resident until a fresh operator warmup (health
                    # warmup_required); nothing reloads implicitly.
                    self._retire()
            if self.pending is None or self.active is not None or self.retiring_at is not None or self.closed:
                return
            if not self.pending["request"].get("_operator_warmup") and (not self.resident or self.worker is None):
                self._finish(self.pending, "unsupported", "not_resident")
                self.pending = None
                self.last_defer_reason = "not_resident"
                return
            if self.resource_fault:
                return self._defer("resource_lease_fault")
            if state is None:
                return self._defer("control_state_unavailable")
            if state["qwen_busy"]:
                return self._defer("qwen_busy")
            if observation_job is not self.pending or observation is None:
                if observation_error:
                    return self._defer("admission_or_driver_unavailable")
                return self._defer("awaiting_resource_observation")
            admission_digest, free, host_available, observed_at = observation
            self.driver_free_bytes, self.host_available_bytes, self.resources_observed_at = free, host_available, observed_at
            if now - observed_at > .25:
                return self._defer("admission_or_driver_unavailable")
            caps = self.profile["execution"]
            incremental = 0 if self.resident else caps["gpu_reservation_bytes"]
            host_incremental = 0 if self.resident else caps["cold_host_reservation_bytes"]
            if host_available < caps["host_minimum_available_bytes"] + host_incremental:
                return self._defer("host_memory_headroom")
            if free < caps["minimum_free_bytes"] + incremental + state["unallocated_reserve_bytes"]:
                return self._defer("gpu_headroom")
            try:
                token = self.lease.try_acquire(self.pending["request"]["request_id"])
            except (OSError, ValueError):
                token = None
            if token is None:
                return self._defer("gpu_execution_busy")
            self.execution_token = token
            self.active, self.pending = self.pending, None
            self.active.update(status="running", reason=None)
            self.active["admission_digest"] = admission_digest
            self.active["dispatched_at"] = now
            self.last_defer_reason = None
            self.last_activity = now
            try:
                if self.worker is None:
                    self.worker = self.worker_factory(self.config, self.profile)
                    self.worker_started_at = now
                    self.worker.start()
                    self.residency_changed({"schema": "den.model-residency/v1", "pid": self.worker.pid,
                                            "process_start_identity": getattr(self.worker, "start_identity", "test-worker"),
                                            "profile_digest": profile_digest(self.profile),
                                            "reservation_bytes": caps["gpu_reservation_bytes"],
                                            "gpu_selector": self.config["gpu_selector"],
                                            "started_at": now})
                self.worker.send(self.active["request"])
                self.counters["dispatched"] += 1
            except Exception:
                self._stop_active("failed", "worker_start_or_send_failed")
                if self.worker is None:
                    self._finish(self.active, "failed", "worker_start_or_send_failed")
                    self.active = None
                    self._release_execution("no_native_context")

    def _idle_expired(self, now):
        """Idle residency ends idle_seconds after the last dispatch or completion.

        Only a clip no tick has evaluated yet holds it, and for at most one
        clip window. A deferred clip (Qwen busy, control unavailable, headroom)
        never extends residency, so a stream of them cannot pin the model.
        """
        fresh = self.pending is not None and self.pending["status"] == "queued"
        return (now - self.last_activity - self.profile["execution"]["idle_seconds"]
                >= (CLIP_WINDOW_SECONDS if fresh else 0))

    def _defer(self, reason):
        self.last_defer_reason = reason
        if self.pending is not None:
            self.pending.update(status="deferred", reason=reason)

    def health(self):
        with self.lock:
            now = self.clock()
            admitted = self.admitted and (self.admission_valid_until_utc is None
                                         or time.time() < self.admission_valid_until_utc)
            warming = any(job is not None and job["request"].get("_operator_warmup") for job in (self.active, self.pending))
            return {"schema": "den.model-health/v1", "profile_id": self.profile["id"],
                    "model_repository": self.profile["repository"], "model_revision": self.profile["revision"],
                    "observed_at": now, "offline": True,
                    "profile_digest": profile_digest(self.profile), "admitted": admitted,
                    "admission_scope": "bounded_trial" if admitted else "not_admitted",
                    "admission_verified_at": self.admission_verified_at,
                    "snapshot_manifest_digest": self.admission_receipt_digest,
                    "source_validated_at": self.source_validated_at,
                    "source_validation_fresh": self.control_valid and self.source_validated_at is not None and 0 <= now - self.source_validated_at <= 2,
                    "driver_free_bytes": self.driver_free_bytes, "host_available_bytes": self.host_available_bytes,
                    "resources_observed_at": self.resources_observed_at,
                    "loaded": self.resident, "worker_present": self.worker is not None,
                    "resident": self.resident, "warmup_required": not self.resident and not warming,
                    "residency_reservation_bytes":
                    self.profile["execution"]["gpu_reservation_bytes"] if self.worker else 0,
                    "execution_lease_held": self.execution_token is not None,
                    "startup_in_progress": self.active is not None and self.active["request"].get("_operator_warmup") is True,
                    "running": self.active is not None, "pending": self.pending is not None,
                    "retiring": self.retiring_at is not None, "deferred_reason": self.last_defer_reason,
                    "resource_fault": self.resource_fault, "closed": self.closed,
                    "global_enforcement": "cooperative_broker_participants_only"}

    def metrics(self):
        with self.lock:
            return {"schema": "den.model-metrics/v1", "counters": dict(self.counters),
                    "stored_jobs": len(self.jobs), "maximum_stored_jobs": 64,
                    "maximum_running": 1, "maximum_pending": 1}

    def close(self):
        with self.lock:
            self.closed = True
            if self.pending is not None:
                self._finish(self.pending, "cancelled", "runtime_closed")
                self.pending = None
            if self.active is not None:
                self._stop_active("cancelled", "runtime_closed")
            self._retire()
