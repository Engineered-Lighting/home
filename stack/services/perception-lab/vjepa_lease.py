"""Per-recording LA V-JEPA worker lease and the leased clip pump (Victoria side).

The V-JEPA worker on LA runs only under a lease from its own supervisor
(lab-vjepa-lifecycle.service, https://192.168.0.100:9447, the reviewed
lifecycle.Lifecycle): start by a canonical request id, status until ready,
stop by engine epoch. VjepaLease owns that lease for one recording, and
LeasedVjepaPump is the vjepa_live pump that asks for it.

When: lazily, from the pump thread, on the first tick that would send. The
runtime starts the pump only after the paired detector lease is ready and the
pair is activated for a recipe granting temporal_encoder to an arm; each tick
then passes the recording, reasoning, household-grant (vjepa-den-1) and Frigate
occupancy gates before the lease is requested. With the in-memory
occupancy_reader gate that tick is the one an off/unknown -> on occupancy edge
wakes (speed plan S1.2/S1.7), so the lease is requested at the edge, and only
because the room is occupied then. An empty room, a withheld recipe or a
revoked grant never starts the LA worker.

Fail closed: a refused start (worker_busy, preflight gates), a failed or
indeterminate lease, a readiness timeout, a receipt whose profile is not the
pinned one, or a lease inside its expiry margin skips with a typed reason and
sends nothing. No clip leaves without a ready lease whose exact profile the
request carries. The lease request id is written to the ledger before it is
transmitted and never reinvented after a lost reply.

Release: when the pump stops (session stop, reap, runtime close) its thread
stops the lease by engine epoch and waits, bounded, for 'stopped'. A release
it cannot prove is recorded as indeterminate; the supervisor's lease expiry and
the worker unit's RuntimeMaxSec remain the backstop. vjepa.lease events carry
identity, state and controlled reasons only.
"""
import re
import threading
import time
import uuid

import vjepa_scene as scene
from vjepa_live import VjepaLivePump

READY_SECONDS = 120          # model load bound (weights verified and loaded on LA)
EXPIRY_MARGIN_SECONDS = 10   # no clip is sent this close to lease expiry
RETRY_SECONDS = 30           # backoff after a refused or failed lease
MAX_ATTEMPTS = 3             # new leases per recording
RELEASE_SECONDS = 10         # bounded wait for 'stopped' after a stop
POLL_SECONDS = .25           # status reads while the worker starts (one small GET each)
TERMINAL = ("stopped", "failed")
REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")


class LeaseUnavailable(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _reason(value, default):
    return value if isinstance(value, str) and REASON.fullmatch(value) else default


class VjepaLease:
    """One recording's LA lifecycle lease. Used from the pump thread only."""

    def __init__(self, store, session, client, *, runtime_revision, clock=time.time, monotonic=time.monotonic,
                 sleep=time.sleep, ready_seconds=READY_SECONDS):
        if not re.fullmatch(r"[0-9a-f]{32}", str(session.get("id"))):
            raise ValueError("lease session identity required")
        self.store, self.session, self.client = store, dict(session), client
        self.runtime_revision = runtime_revision
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.ready_seconds = ready_seconds
        self.request_id = None
        self.receipt = None
        self.profile = None
        self.state = "idle"       # idle, requesting, starting, ready, failed, unknown, released, exhausted
        self.reason = None
        self.attempts = 0
        self.retry_at = None
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            expires = (self.receipt or {}).get("expires_at")
            return {"state": self.state, "reason": self.reason, "attempts": self.attempts,
                    "expires_in_s": round(expires - self.clock(), 1) if scene.finite(expires) else None}

    def _event(self, state, **extra):
        try:
            self.store.append_event(self.session["id"], "vjepa.lease", {
                "request_id": self.request_id, "state": state, "attempt": self.attempts,
                "available_at": self.store.clock(), **extra})
        except Exception:
            pass

    def _set(self, state, reason=None):
        with self.lock:
            self.state, self.reason = state, reason

    def _backoff(self, state, reason):
        self._set(state, reason)
        self.retry_at = self.monotonic() + RETRY_SECONDS
        return LeaseUnavailable(reason)

    def acquire(self, cancelled):
        """The ready lease's exact worker profile, else LeaseUnavailable."""
        if self.state in ("released", "exhausted"):
            raise LeaseUnavailable("lease_" + self.state)
        if self.profile is not None:
            if self.clock() < self.receipt["expires_at"] - EXPIRY_MARGIN_SECONDS:
                return self.profile
            self._set("expired", "lease_expiring")
            raise LeaseUnavailable("lease_expiring")
        if self.retry_at is not None and self.monotonic() < self.retry_at:
            raise LeaseUnavailable("lease_backoff")
        if self.request_id is None or self.state == "failed":
            if self.attempts >= MAX_ATTEMPTS:
                self._set("exhausted", "lease_attempts_exhausted")
                raise LeaseUnavailable("lease_attempts_exhausted")
            self._start()
        return self._wait_ready(cancelled)

    def _start(self):
        from worker_lifecycle_client import StartRejected
        self.request_id, self.receipt, self.attempts = str(uuid.uuid4()), None, self.attempts + 1
        self._set("requesting")
        # Durable identity before transmission: a lost reply is re-read by
        # this id, never replaced by a new request.
        self._event("requesting")
        try:
            self.receipt = self.client.start(self.request_id, self.session["id"])
        except StartRejected as error:
            self._event("failed", reason=_reason("la_" + str(error), "la_start_rejected"))
            raise self._backoff("failed", _reason("la_" + str(error), "la_start_rejected")) from None
        except Exception:
            self._event("unknown", reason="lease_start_unproven")
            raise self._backoff("unknown", "lease_start_unproven") from None
        self._set("starting")

    def _wait_ready(self, cancelled):
        deadline = self.monotonic() + self.ready_seconds
        while True:
            if cancelled.is_set():
                raise LeaseUnavailable("stopped")
            try:
                receipt = self.client.status(self.request_id)
            except Exception:
                raise self._backoff("unknown", "lease_status_unavailable") from None
            self.receipt = receipt
            state = receipt["state"]
            if state == "ready":
                try:
                    profile = scene.check_profile(receipt.get("profile"), runtime_revision=self.runtime_revision)
                    if profile["engine_epoch"] != receipt["engine_epoch"]:
                        raise ValueError()
                except ValueError:
                    # A configuration error, never retried: release and stop leasing.
                    self._event("profile_mismatch")
                    self.release()
                    self._set("exhausted", "lease_profile_mismatch")
                    raise LeaseUnavailable("lease_profile_mismatch") from None
                if self.clock() >= receipt["expires_at"] - EXPIRY_MARGIN_SECONDS:
                    self.release()
                    self._set("exhausted", "lease_expiring")
                    raise LeaseUnavailable("lease_expiring")
                self.profile = profile
                self._set("ready")
                self._event("ready", engine_epoch=receipt["engine_epoch"], expires_at=receipt["expires_at"])
                return profile
            if state != "starting":
                reason = _reason(receipt.get("reason"), state)
                self._event("failed" if state in TERMINAL else state, reason=reason)
                if state not in TERMINAL:
                    # stopping or indeterminate: this id stays the one to read.
                    raise self._backoff("unknown", "lease_" + state)
                raise self._backoff("failed", "lease_" + reason if not reason.startswith("lease_") else reason)
            if self.monotonic() >= deadline:
                self._event("ready_timeout")
                self.release(final=False)
                raise self._backoff("failed" if self.state == "failed" else "unknown", "lease_ready_timeout")
            cancelled.wait(POLL_SECONDS)

    def release(self, final=True):
        """Stop this recording's lease (if any) and wait, bounded, for proof."""
        if self.request_id is None or self.state in ("released", "exhausted"):
            if final:
                self._set("released" if self.state != "exhausted" else "exhausted", self.reason)
            return self.state
        outcome, reason = "indeterminate", "release_unproven"
        try:
            receipt = self.client.status(self.request_id)
            if receipt["state"] not in TERMINAL:
                receipt = self.client.stop(self.request_id, receipt["engine_epoch"])
            deadline = self.monotonic() + RELEASE_SECONDS
            while receipt["state"] not in TERMINAL and self.monotonic() < deadline:
                self.sleep(.25)
                receipt = self.client.status(self.request_id)
            if receipt["state"] in TERMINAL:
                outcome, reason = receipt["state"], None
        except Exception:
            pass
        self.profile = None
        self._event("released" if outcome in TERMINAL else "indeterminate",
                    **({"reason": reason} if reason else {"lease_state": outcome}))
        if final:
            self._set("released", reason)
        else:
            self._set("failed" if outcome in TERMINAL else "unknown", reason)
        return outcome


class LeasedVjepaPump(VjepaLivePump):
    """The vjepa_live pump with its per-session LA lease: acquired on the first
    tick past every gate, its exact profile carried by every clip, released
    when the pump thread ends."""

    def __init__(self, store, session, *, lease, transport, runtime_revision, **kwargs):
        placeholder = {"model_version": scene.MODEL_VERSION, "artifact_sha256": scene.ARTIFACT_SHA256,
                       "runtime_revision": runtime_revision, "engine_epoch": "unleased",
                       "input_profile": scene.INPUT_PROFILE}
        super().__init__(store, session, transport=transport, profile=placeholder, runtime_revision=runtime_revision,
                         **kwargs)
        self.lease = lease

    def prepare(self):
        with self.lock:
            if self.lease.profile is None:
                self.status = "leasing"
        try:
            profile = self.lease.acquire(self.stopped)
        except LeaseUnavailable as error:
            return error.reason
        if profile != self.profile:
            # A new worker keeps no earlier embedding: drift restarts.
            self.profile, self.previous = dict(profile), None
        return None

    def _run(self):
        try:
            super()._run()
        finally:
            self.lease.release()

    def snapshot(self):
        return {**super().snapshot(), "lease": self.lease.snapshot()}
