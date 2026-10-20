"""Serialized Victoria adapter runtime joining the actual transport boundaries.

Call step at least once per second from one owner thread. Network/camera work
runs in one bounded worker; MQTT callbacks only enqueue events. Neither network
timeouts nor process restarts authorize a repeat POST. The health reader must
be a nonblocking cache populated by separately commissioned health/clock probes.
This module never constructs a permissive health or authority verifier.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
import time
from typing import Callable

from .adapter import Frame, VictoriaAdapter, Work, finite, valid_observation, validate_frame
from .gateway_client import GatewayNotSent
from .mqtt_transport import MqttBusy
from .observation_sink import ObservationSink
from .protocol import FrigateWire, inference_payload
from .rejections import REJECTION_REASONS
from .submissions import SubmissionClosed, SubmissionJournal
from .refresh_mailbox import RefreshMailbox


@dataclass(frozen=True)
class HealthSample:
    camera_session: str
    frigate_instance: str
    checked_mono: float
    healthy: bool
    cross_host_uncertainty: float
    clock_valid_until_mono: float
    timing_generation: int = 0
    clock_evidence_version: int = 1
    camera_boot_id: str | None = None
    clock_validation_id: str | None = None
    camera_host_uncertainty_seconds: float | None = None


def _valid_health(health, now):
    return (isinstance(health, HealthSample) and health.healthy is True
            and type(health.timing_generation) is int and 0 <= health.timing_generation <= 2**63 - 1
            and finite(health.checked_mono) and 0 <= now - health.checked_mono <= 5
            and finite(health.cross_host_uncertainty) and 0 <= health.cross_host_uncertainty <= 1
            and finite(health.clock_valid_until_mono) and now < health.clock_valid_until_mono
            and type(health.clock_evidence_version) is int and health.clock_evidence_version in (1, 2)
            and ((health.clock_evidence_version == 1 and health.camera_host_uncertainty_seconds is None) or (
                health.clock_evidence_version == 2
                and isinstance(health.camera_boot_id, str) and bool(health.camera_boot_id)
                and isinstance(health.clock_validation_id, str) and bool(health.clock_validation_id)
                and finite(health.camera_host_uncertainty_seconds)
                and 0 <= health.camera_host_uncertainty_seconds <= 1 - health.cross_host_uncertainty)))


def _frame_health_matches(frame, health):
    return (frame.capture_session == health.camera_session
            and frame.timing_generation == health.timing_generation
            and frame.timing_evidence_version == health.clock_evidence_version
            and (frame.timing_evidence_version == 1 or (
                frame.camera_boot_id == health.camera_boot_id
                and finite(frame.camera_host_uncertainty_seconds)
                and frame.camera_host_uncertainty_seconds >= health.camera_host_uncertainty_seconds)))


def _continuous_frame_health(frame, selected, current, now):
    return (_valid_health(current, now) and _frame_health_matches(frame, current)
            and (current.frigate_instance, current.camera_boot_id) == (selected.frigate_instance, selected.camera_boot_id)
            and current.cross_host_uncertainty <= selected.cross_host_uncertainty)


@dataclass(frozen=True)
class RuntimeSettings:
    device_id: str
    configuration_revision: str
    max_output_tokens: int

    def __post_init__(self):
        import re
        if (any(not isinstance(v, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", v) is None
                for v in (self.device_id, self.configuration_revision))
                or type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 4096):
            raise ValueError("provisioned runtime device/configuration and bounded tokens required")


class VictoriaRuntime:
    """One process owns this runtime and its submission-journal lifetime lock.

    Inject CameraClient, GatewayClient, MqttTransport and a source-bound store.
    The caller owns provisioning and closes journal/store only after close()
    succeeds. The loop can continue reconciling MQTT and pruning observations
    during WAN/model loss; no inference backlog or saved captions are replayed.
    """

    def __init__(self, *, adapter: VictoriaAdapter, mqtt, camera, gateway,
                 journal: SubmissionJournal, store, settings: RuntimeSettings,
                 health_reader: Callable[[], HealthSample | None],
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 caption_writer=None):
        if journal.device_id != settings.device_id:
            raise ValueError("runtime and journal device bindings differ")
        config = gateway.config
        if (config.device_id != settings.device_id or config.configuration_revision != settings.configuration_revision
                or dict(config.cameras) != {adapter.camera_id: adapter.room_id}
                or gateway.gateway_identity != journal.gateway_identity
                or mqtt.adapter_session != adapter.session):
            raise ValueError("runtime gateway, journal, camera or MQTT session binding differs")
        self.adapter, self.mqtt, self.camera, self.gateway = adapter, mqtt, camera, gateway
        self.journal, self.store, self.settings = journal, store, settings
        self.health_reader, self.clock, self.monotonic = health_reader, clock, monotonic
        self.caption_writer = caption_writer
        self.sink, self.wire = ObservationSink(adapter, store), FrigateWire(adapter)
        self._owner = threading.get_ident()
        self._job = None
        self._mqtt_generation = -1
        self._mqtt_started = False
        self._retry_mono = float("-inf")
        self._heartbeat_at = float("-inf")
        self._heartbeat_value = None
        self._next_poll = float("-inf")
        self._next_prune = float("-inf")
        self._pending_publication = None
        self._selected_frame = self._selected_health = None
        self._closed = False
        self._last_mono = float("-inf")
        self.admission_closed = False
        self.storage_failed = False
        self.last_error = None
        self.counters: dict[str, int] = {}
        self.refresh_backend = RefreshMailbox(self)

    def _count(self, name: str):
        self.counters[name] = self.counters.get(name, 0) + 1

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("adapter runtime must be called from its owner thread")

    def _launch(self, kind: str, work: Work | None, operation: Callable):
        if self._job is not None:
            raise RuntimeError("one runtime I/O worker is allowed")
        completed = queue.Queue(maxsize=1)
        def run():
            try:
                completed.put((True, operation()))
            except GatewayNotSent:
                completed.put((False, "not_sent"))
            except Exception:
                completed.put((False, None))  # no peer data, token or image in diagnostics
        thread = threading.Thread(target=run, name="victoria-" + kind, daemon=True)
        self._job = kind, work, completed, thread
        try:
            thread.start()
        except Exception:
            self._job = None
            self.last_error = "worker_start_failed"
            # A prepared submission stays outstanding even if worker launch fails.
            if kind == "camera" and work:
                self.adapter.complete(work.request_id, None, now_mono=self.monotonic(), now_unix=self.clock())

    def _lost_mqtt(self, now: float):
        self.adapter.disconnect()
        self._pending_publication = None
        self._heartbeat_value = None
        self._mqtt_started = False
        self._retry_mono = now + 5
        self.mqtt.stop()

    def _events(self, now: float):
        for event in self.mqtt.drain(max_events=64):
            data = event.payload
            generation = data.get("generation")
            if type(generation) is not int or generation < self._mqtt_generation:
                continue
            if event.kind == "connected":
                self._mqtt_generation = generation
                self.adapter.connect(clean_session=data.get("clean_session") is True)
                self._heartbeat_value = None
            elif generation != self._mqtt_generation:
                # Overflow/disconnection can precede a successfully delivered CONNECT.
                if event.kind in ("overflow", "disconnected"):
                    self._mqtt_generation = generation
                    self._lost_mqtt(now)
            elif event.kind == "subscribed":
                self.adapter.subscription_ack(tuple(data.get("topics", ())), success=data.get("success") is True)
                if not self.adapter.subscribed:
                    self._lost_mqtt(now)
            elif event.kind in ("disconnected", "overflow"):
                self._lost_mqtt(now)
            elif event.kind == "message":
                received = data.get("received_mono")
                if not finite(received) or not 0 <= now - received <= 5:
                    self.adapter.invalidate()
                    self._pending_publication = None
                    continue
                self.wire.receive(data["topic"], data["payload"], retained=data["retained"],
                                  duplicate=data["duplicate"], now_mono=received)

    def _health(self, now: float) -> HealthSample | None:
        try:
            health = self.health_reader()
        except Exception:
            health = None
        if not _valid_health(health, now):
            if self.adapter.state != "unknown" or self.adapter.health_at != float("-inf"):
                self.adapter.unavailable()
            self._pending_publication = None
            return None
        self.adapter.health(camera_session=health.camera_session, frigate_instance=health.frigate_instance,
                            healthy=True, checked_mono=health.checked_mono, now_mono=now,
                            timing_generation=health.timing_generation,
                            clock_evidence_version=health.clock_evidence_version, camera_boot_id=health.camera_boot_id)
        return health

    def _publish(self, publication, now: float) -> bool:
        try:
            self.mqtt.publish(publication[0], publication[1], retain=publication[2])
            return True
        except MqttBusy:
            return False
        except Exception:
            self.last_error = "mqtt_publish_failed"
            self._lost_mqtt(now)
            return False

    def _outputs(self, now: float, wall: float):
        if not self.adapter.subscribed:
            return
        heartbeat = self.wire.availability(now_mono=now)
        if heartbeat[1] != self._heartbeat_value or now - self._heartbeat_at >= 5:
            if not self._publish(heartbeat, now):
                return
            self._heartbeat_at, self._heartbeat_value = now, heartbeat[1]
        if self._pending_publication:
            publication, work, frame, selected_health = self._pending_publication
            value = json.loads(publication[1])
            if (not valid_observation(value, wall) or wall >= work.request_deadline
                    or now - work.scheduled_mono >= 15 or not self.adapter.work_state_valid(work)
                    or value["adapter_session"] != self.adapter.session
                    or value["occupancy_generation"] != self.adapter.generation
                    or not _continuous_frame_health(frame, selected_health, self._health(now), now)):
                self._pending_publication = None
                self._count("publication_expired")
            elif self._publish(publication, now):
                self._pending_publication = None
                self._count("publication_submitted")
                if self.caption_writer is not None:
                    try:
                        self.caption_writer(value, generated_at=self.clock())
                        self._count("home_caption_published")
                    except Exception:
                        self.last_error = "home_caption_publication_failed"
                        self._count(self.last_error)

    def _finish_job(self, now: float, wall: float, health: HealthSample | None):
        if self._job is None:
            return
        kind, work, completed, thread = self._job
        if thread.is_alive():
            return
        okay, result = completed.get_nowait()
        self._job = None
        if not okay:
            self.last_error = kind + "_unavailable"
            self._count(self.last_error)
            if kind == "camera" and work:
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
            elif kind == "submit" and work and result == "not_sent":
                self.journal.record_not_sent(work.request_id)
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
            self._next_poll = now + 2
            return
        if kind == "camera":
            if not health or not isinstance(result, Frame) or not _frame_health_matches(result, health):
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
                return
            try:
                payload = inference_payload(self.adapter, result, device_id=self.settings.device_id,
                                            configuration_revision=self.settings.configuration_revision,
                                            max_output_tokens=self.settings.max_output_tokens,
                                            now_mono=now, now_unix=wall,
                                            cross_host_uncertainty=health.cross_host_uncertainty)
            except ValueError:
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
                self._count("frame_rejected")
                return
            if self.journal.prepare(payload):
                self._count("submission_prepared")
                self._selected_frame, self._selected_health = result, health
                def submit_current():
                    try:
                        current, now_mono, now_wall = self.health_reader(), self.monotonic(), self.clock()
                        if not _continuous_frame_health(result, health, current, now_mono):
                            raise ValueError("camera authority changed")
                        validate_frame(result, work, now_mono=now_mono, now_unix=now_wall,
                                       cross_host_uncertainty=health.cross_host_uncertainty)
                    except Exception:
                        raise GatewayNotSent("camera authority unavailable before submission") from None
                    return self.gateway.submit(payload)
                self._launch("submit", work, submit_current)
            else:
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
            return
        if not self.journal.accept_status(result):
            self.last_error = "gateway_status_unmatched"
            self._next_poll = now + 2
            return
        self._next_poll = now + 2
        if result["status"] != "terminal":
            return
        if result.get("outcome") in REJECTION_REASONS:
            self._count("inference_rejected")
            self.last_error = result["outcome"]
        # Durable accounting may wait on storage. Its terminal result does not
        # renew the observation, clock authority or original request deadline.
        now, wall = self.monotonic(), self.clock()
        health = self._health(now)
        self.adapter.tick(now, wall)
        if work and self.adapter.active == work:
            if (self._selected_frame is None or self._selected_health is None
                    or not _continuous_frame_health(self._selected_frame, self._selected_health, health, now)):
                self.adapter.complete(work.request_id, None, now_mono=now, now_unix=wall)
                return
            try:
                publication = self.sink.finish(work, result, now_mono=now, now_unix=wall)
                if publication:
                    self._pending_publication = publication, work, self._selected_frame, self._selected_health
                    self._count("observation_stored")
            except Exception:
                self.storage_failed = True
                self.last_error = "observation_storage_failed"
                self._count(self.last_error)
        else:
            # Recovered work belongs to the previous process/session. Its result
            # closes accounting only, never restoring a historical live caption.
            self.adapter.last_scheduled = float("-inf")
            self._count("recovered_terminal")

    def request_refresh(self) -> Work:
        """Owner-thread entry for an already-authorized camera request.

        No HTTP route or principal grant is created here. The existing journal
        still accounts for all dispatched work, including uncertain deliveries.
        """
        self._check_owner()
        if self._closed or self.admission_closed or self.storage_failed:
            raise ValueError("adapter admission unavailable")
        now, wall = self.monotonic(), self.clock()
        if not self._health(now):
            raise ValueError("camera health unavailable")
        return self.adapter.request_refresh(now_mono=now, now_unix=wall)

    def accept_refresh(self, principal_namespace: str, request_id: str) -> dict:
        """Durable request entry for a separately authenticated ingress.

        Caller must derive namespace from current server-side auth generation.
        Repeated POSTs only read prior state; they never renew or re-enqueue work.
        """
        self._check_owner()
        self.journal.expire_refreshes(now=self.clock())
        prior = self.journal.refresh_status(principal_namespace, request_id)
        if prior:
            return prior
        if self._closed or self.admission_closed or self.storage_failed:
            raise ValueError("adapter admission unavailable")
        now, wall = self.monotonic(), self.clock()
        if not finite(now) or now < self._last_mono or not self._health(now):
            raise ValueError("camera health or clock unavailable")
        if self.journal.reserve_refresh(principal_namespace, request_id, now=wall):
            try:
                self.adapter.request_refresh(now_mono=now, now_unix=wall, request_id=request_id)
            except Exception:
                self.journal.refresh_not_queued(request_id)
                raise
        return self.journal.refresh_status(principal_namespace, request_id)

    def step(self):
        self._check_owner()
        if self._closed:
            raise RuntimeError("runtime is stopped")
        now, wall = self.monotonic(), self.clock()
        if not finite(now) or not finite(wall):
            raise ValueError("finite runtime clocks required")
        if now < self._last_mono:
            self._lost_mqtt(now)
            self.admission_closed = True
            self.last_error = "monotonic_clock_reversed"
        self._last_mono = now
        self._events(now)
        if not self._mqtt_started and now >= self._retry_mono:
            try:
                self.mqtt.start()
                self._mqtt_started = True
            except Exception:
                self._retry_mono = now + 5
                self.last_error = "mqtt_connect_unavailable"
        health = self._health(now)
        self.adapter.tick(now, wall)
        request = self.adapter.request_reconciliation(now)
        if request and not self._publish((*request, False), now):
            self.adapter.requested_at = None
            self.adapter.reconcile_hint = True
        try:
            self.journal.expire_refreshes(now=wall)
            if now >= self._next_prune:
                self._next_prune = now + 60
                try:
                    self.store.prune(batch_size=1000)
                    self.storage_failed = False
                except Exception:
                    self.storage_failed = True
                    self.last_error = "retention_unavailable"
                self.journal.prune(before_unix=wall - 30 * 86400)
            if not self.admission_closed:
                # Filesystem/transport callbacks may consume real time. Never
                # validate a frame or persist a POST using step-entry clocks.
                now, wall = self.monotonic(), self.clock()
                health = self._health(now)
                self.adapter.tick(now, wall)
                self._finish_job(now, wall, health)
                outstanding = self.journal.outstanding()
                if self._job is None and outstanding and now >= self._next_poll:
                    work = self.adapter.active
                    self._launch("status", work, lambda: self.gateway.status(outstanding.request_id))
                elif (self._job is None and not outstanding and not self.storage_failed
                      and health and self.adapter.subscribed and self.adapter.work_state_valid(self.adapter.pending)):
                    work = self.adapter.take_work(now)
                    if work:
                        self._launch("camera", work, lambda: self.camera.fetch(
                            work, expected_capture_session=health.camera_session,
                            cross_host_uncertainty=health.cross_host_uncertainty))
        except SubmissionClosed:
            self.admission_closed = True
            self.last_error = "submission_journal_closed"
        self._outputs(self.monotonic(), self.clock())
        self.refresh_backend.drain()

    def close(self, *, wait_seconds: float = 0) -> bool:
        """False means the caller must retain the journal/process lifetime lock."""
        self._check_owner()
        if not finite(wait_seconds) or not 0 <= wait_seconds <= 15:
            raise ValueError("shutdown wait must be bounded by 15 seconds")
        self._closed = True
        self.refresh_backend.close()
        self._pending_publication = None
        self.adapter.disconnect()
        self.mqtt.stop()  # deliberate close must publish offline or abort for LWT
        if self._job:
            self._job[3].join(timeout=wait_seconds)
            if self._job[3].is_alive():
                return False
            self._job = None
        return True
