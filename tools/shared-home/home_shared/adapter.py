"""Victoria reconciliation and observation lifecycle; no HA action authority.

The transport must use a clean MQTT session and pass subscription ACKs, retained
and duplicate flags honestly. Frigate camera_activity has no source sequence or
nonce. Receipt after onConnect is NOT proof of an atomic correlated snapshot.
The state machine owns only its own session/sequence and uses independently
checked camera/Frigate health. No state here is suitable for physical actuation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math
from typing import Any
import uuid


def finite(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


@dataclass(frozen=True)
class Work:
    request_id: str
    site_id: str
    camera_id: str
    room_id: str
    adapter_session: str
    sequence: int
    occupancy_generation: int
    scheduled_at: float
    scheduled_mono: float
    request_deadline: float
    detection_anchor_mono: float
    kind: str


@dataclass(frozen=True)
class Frame:
    capture_session: str
    frame_id: str
    observed_at: float
    observed_mono: float
    uncertainty_seconds: float
    clock_mapping_revision: str
    jpeg: bytes
    width: int = 640
    height: int = 360
    timing_generation: int = 0
    timing_evidence_version: int = 1
    camera_boot_id: str | None = None
    host_clock_validation_id: str | None = None
    camera_host_uncertainty_seconds: float | None = None


class VictoriaAdapter:
    """Single-threaded state engine. Call tick at least once a second.

    One active request remains accounted until completion is acknowledged, even
    after vacancy/disconnection. Pending work contains no image and expires;
    disconnected work is never replayed. Health TTL is a local guard, not a
    claimed Frigate source timestamp.
    """

    def __init__(self, camera_id: str = "den_oak", room_id: str = "den"):
        if camera_id != "den_oak" or room_id != "den":
            raise ValueError("pilot registration permits only victoria/den_oak/den")
        self.site_id, self.camera_id, self.room_id = "victoria", camera_id, room_id
        self.session = uuid.uuid4().hex
        self.sequence = 0
        self.generation = 0
        self.connected = False
        self.subscribed = False
        self.health_identity: tuple | None = None
        self.health_at = float("-inf")
        self.snapshot_at = float("-inf")
        self.requested_at: float | None = None
        self.last_reconcile = float("-inf")
        self.reconcile_hint = False
        self.state = "unknown"
        self.people: int | None = None
        self.pets = 0
        self.positive_since: float | None = None
        self.zero_since: float | None = None
        self.occupied = False
        self.last_scheduled = float("-inf")
        self.last_dispatch = float("-inf")
        self.dispatches: deque[float] = deque()
        self.pending: Work | None = None
        self.active: Work | None = None
        self.active_frame: Frame | None = None

    @property
    def subscriptions(self) -> tuple[str, ...]:
        return ("frigate/camera_activity", "frigate/available", "frigate/den_oak/person",
                "frigate/den_oak/all")

    def invalidate(self) -> None:
        self.generation += 1
        self.people, self.pets = None, 0
        self.state, self.occupied = "unknown", False
        self.positive_since = self.zero_since = None
        self.snapshot_at = float("-inf")
        self.pending = None
        self.requested_at = None
        self.reconcile_hint = True

    def connect(self, *, clean_session: bool) -> None:
        if not clean_session:
            raise ValueError("a clean MQTT session is mandatory")
        self.invalidate()
        self.connected, self.subscribed = True, False
        self.health_at = float("-inf")

    def disconnect(self) -> None:
        self.invalidate()
        self.connected = self.subscribed = False
        self.health_at = float("-inf")

    def subscription_ack(self, topics: tuple[str, ...], *, success: bool) -> None:
        self.subscribed = self.connected and success and set(topics) == set(self.subscriptions)
        if not self.subscribed:
            self.invalidate()

    def health(self, *, camera_session: str, frigate_instance: str, healthy: bool,
               checked_mono: float, now_mono: float, timing_generation: int = 0,
               clock_evidence_version: int = 1, camera_boot_id: str | None = None) -> None:
        if (not healthy or not camera_session or not frigate_instance
                or not finite(checked_mono) or not finite(now_mono)
                or not 0 <= now_mono - checked_mono <= 5
                or type(timing_generation) is not int or not 0 <= timing_generation <= 2**63 - 1
                or type(clock_evidence_version) is not int or clock_evidence_version not in (1, 2)
                or (clock_evidence_version == 2 and (not isinstance(camera_boot_id, str) or not camera_boot_id))):
            self.invalidate()
            self.health_at = float("-inf")
            return
        identity = camera_session, frigate_instance, timing_generation, clock_evidence_version, camera_boot_id
        if (self.health_identity != identity or
                (math.isfinite(self.health_at) and not 0 <= now_mono - self.health_at <= 10)):
            self.invalidate()
        self.health_identity, self.health_at = identity, checked_mono

    def request_reconciliation(self, now_mono: float) -> tuple[str, str] | None:
        if not self.connected or not self.subscribed:
            return None
        if not self.reconcile_hint and now_mono - self.last_reconcile < 5:
            return None
        # Hints may not flood onConnect; one request/second is the hard ceiling.
        if now_mono - self.last_reconcile < 1:
            return None
        self.last_reconcile = self.requested_at = now_mono
        self.reconcile_hint = False
        return "frigate/onConnect", ""

    def count_hint(self, *, retained: bool = False) -> None:
        # Even a retained count can ask for fresh reconciliation, never set state.
        self.reconcile_hint = True

    def unavailable(self) -> None:
        self.invalidate()
        self.health_at = float("-inf")

    def snapshot(self, payload: dict, now_mono: float, *, retained: bool = False,
                 duplicate: bool = False) -> bool:
        if math.isfinite(self.snapshot_at) and not 0 <= now_mono - self.snapshot_at <= 10:
            self.invalidate()
            return False
        if (retained or duplicate or not self.connected or not self.subscribed
                or self.requested_at is None or not 0 <= now_mono - self.requested_at <= 5
                or not 0 <= now_mono - self.health_at <= 10):
            return False
        camera = payload.get(self.camera_id) if isinstance(payload, dict) else None
        objects = camera.get("objects") if isinstance(camera, dict) else None
        if (not isinstance(objects, list) or len(objects) > 256
                or any(not isinstance(obj, dict) or not isinstance(obj.get("label"), str)
                       for obj in objects)):
            self.invalidate()
            return False
        self.requested_at = None
        self.snapshot_at = now_mono
        labels = [obj["label"].removesuffix("-verified") for obj in objects]
        self.people = labels.count("person")
        self.pets = labels.count("cat") + labels.count("dog")
        if self.people:
            self.zero_since = None
            if self.positive_since is None:
                self.positive_since = now_mono
            self.state = "occupied" if self.occupied else "confirming"
        else:
            self.positive_since = None
            if self.zero_since is None:
                self.zero_since = now_mono
            self.state = "vacant" if self.state == "vacant" else "vacancy_pending"
        return True

    def tick(self, now_mono: float, now_unix: float) -> None:
        if not finite(now_mono) or not finite(now_unix):
            raise ValueError("finite clocks required")
        if (not self.connected or not self.subscribed or not 0 <= now_mono - self.health_at <= 10
                or not 0 <= now_mono - self.snapshot_at <= 10):
            if self.state != "unknown":
                self.invalidate()
            return
        if self.zero_since is not None and now_mono - self.zero_since >= 60 and self.state != "vacant":
            self.generation += 1
            self.occupied, self.pending, self.state = False, None, "vacant"
        arrival = (not self.occupied and self.positive_since is not None
                   and now_mono - self.positive_since >= 5)
        if arrival:
            self.occupied, self.state = True, "occupied"
        if (self.occupied and self.people and (arrival or now_mono - self.last_scheduled >= 120)
                and not (self.pending and self.pending.kind == "interactive")):
            self.sequence += 1
            self.pending = Work(uuid.uuid4().hex, self.site_id, self.camera_id, self.room_id,
                                self.session, self.sequence, self.generation, now_unix, now_mono,
                                now_unix + 15, self.positive_since, "arrival" if arrival else "refresh")
            self.last_scheduled = now_mono
        if self.pending and now_mono - self.pending.scheduled_mono >= 15:
            self.pending = None

    def request_refresh(self, *, now_mono: float, now_unix: float, request_id: str | None = None) -> Work:
        """Queue a trusted caller's explicit look, including a known empty room.

        This internal method grants no user access. The ingress must authorize
        the principal/site/camera before calling it. Reuse the same single queue,
        deadline and dispatch limits; never cancel a charged running request.
        """
        if request_id is not None and str(uuid.UUID(request_id)) != request_id:
            raise ValueError("canonical refresh UUID required")
        self.tick(now_mono, now_unix)
        if (self.state == "unknown" or not self.connected or not self.subscribed
                or not 0 <= now_mono - self.health_at <= 10
                or not 0 <= now_mono - self.snapshot_at <= 10):
            raise ValueError("fresh reconciled camera state required")
        if self.pending and self.pending.kind == "interactive":
            raise ValueError("interactive request already pending")
        self.sequence += 1
        self.pending = Work(request_id or uuid.uuid4().hex, self.site_id, self.camera_id, self.room_id,
                            self.session, self.sequence, self.generation, now_unix, now_mono,
                            now_unix + 15, now_mono, "interactive")
        return self.pending

    def work_state_valid(self, work: Work | None) -> bool:
        return bool(work and (self.state == "occupied" or (
            work.kind == "interactive" and self.state in {"vacant", "vacancy_pending", "confirming"})))

    def take_work(self, now_mono: float) -> Work | None:
        if (not self.connected or not self.subscribed
                or not 0 <= now_mono - self.health_at <= 10
                or not 0 <= now_mono - self.snapshot_at <= 10):
            self.invalidate()
            return None
        if self.pending and now_mono - self.pending.scheduled_mono >= 15:
            self.pending = None
        while self.dispatches and now_mono - self.dispatches[0] >= 60:
            self.dispatches.popleft()
        if (self.active or not self.pending or not self.work_state_valid(self.pending)
                or now_mono - self.last_dispatch < 30 or len(self.dispatches) >= 2):
            return None
        self.active, self.pending = self.pending, None
        self.dispatches.append(now_mono)
        self.last_dispatch = now_mono
        return self.active

    def bind_frame(self, frame: Frame, *, now_mono: float, now_unix: float,
                   cross_host_uncertainty: float) -> dict:
        if not self.active or self.active.occupancy_generation != self.generation:
            raise ValueError("no current active work")
        if (not self.work_state_valid(self.active) or not self.connected or not self.subscribed
                or not 0 <= now_mono - self.health_at <= 10
                or not 0 <= now_mono - self.snapshot_at <= 10):
            self.invalidate()
            raise ValueError("observation health expired")
        if (not self.health_identity or frame.capture_session != self.health_identity[0]
                or frame.timing_generation != self.health_identity[2]
                or frame.timing_evidence_version != self.health_identity[3]
                or (frame.timing_evidence_version == 2 and frame.camera_boot_id != self.health_identity[4])):
            raise ValueError("camera session or timing generation changed")
        metadata = validate_frame(frame, self.active, now_mono=now_mono, now_unix=now_unix,
                                  cross_host_uncertainty=cross_host_uncertainty)
        self.active_frame = replace(frame, uncertainty_seconds=metadata["uncertainty_seconds"])
        return metadata

    def complete(self, request_id: str, result: dict | None, *, now_mono: float,
                 now_unix: float) -> dict | None:
        work = self.active
        if work is None or work.request_id != request_id:
            return None
        frame, self.active_frame = self.active_frame, None
        self.active = None  # only local in-flight accounting; gateway owns execution reservation
        self.tick(now_mono, now_unix)
        if (not result or not frame or not self.work_state_valid(work) or work.occupancy_generation != self.generation
                or work.adapter_session != self.session or now_unix >= work.request_deadline
                or now_mono - work.scheduled_mono >= 15):
            return None
        identity = {"request_id": work.request_id, "site_id": work.site_id,
                    "camera_id": work.camera_id, "room_id": work.room_id,
                    "adapter_session": work.adapter_session, "sequence": work.sequence,
                    "occupancy_generation": work.occupancy_generation}
        if any(result.get(key) != value for key, value in identity.items()):
            return None
        expected = {"capture_session": frame.capture_session, "frame_id": frame.frame_id,
                    "observed_at": frame.observed_at, "valid_until": frame.observed_at + 180,
                    "uncertainty_seconds": frame.uncertainty_seconds}
        if any(result.get(key) != value for key, value in expected.items()):
            return None
        if result.get("status") != "ok" or not valid_observation(result, now_unix):
            return None
        return dict(result)


def validate_frame(frame: Frame, work: Work, *, now_mono: float, now_unix: float,
                   cross_host_uncertainty: float) -> dict:
    values = (frame.observed_at, frame.observed_mono, frame.uncertainty_seconds,
              now_mono, now_unix, cross_host_uncertainty)
    if not all(finite(v) for v in values):
        raise ValueError("invalid clock metadata")
    uncertainty = frame.uncertainty_seconds + cross_host_uncertainty
    if (not 0 <= cross_host_uncertainty <= 1 or not 0 <= frame.uncertainty_seconds <= 1 or uncertainty > 1
            or type(frame.timing_generation) is not int or not 0 <= frame.timing_generation <= 2**63 - 1
            or type(frame.timing_evidence_version) is not int or frame.timing_evidence_version not in (1, 2)
            or not frame.clock_mapping_revision or not frame.capture_session or not frame.frame_id):
        raise ValueError("unvalidated capture clock")
    if frame.timing_evidence_version == 2:
        if (not isinstance(frame.camera_boot_id, str) or not frame.camera_boot_id
                or not isinstance(frame.host_clock_validation_id, str) or not frame.host_clock_validation_id
                or not finite(frame.camera_host_uncertainty_seconds)
                or not 0 <= frame.camera_host_uncertainty_seconds <= frame.uncertainty_seconds):
            raise ValueError("causal frame host-clock evidence missing")
    elif any(v is not None for v in (frame.camera_boot_id, frame.host_clock_validation_id,
                                      frame.camera_host_uncertainty_seconds)):
        raise ValueError("legacy frame cannot claim causal clock evidence")
    if (not 0 <= now_mono - frame.observed_mono <= 3 - uncertainty
            or not 0 <= now_unix - frame.observed_at <= 3 - uncertainty
            or now_mono - work.scheduled_mono >= 15 or now_unix >= work.request_deadline):
        raise ValueError("stale frame or request")
    if (frame.width, frame.height) != (640, 360) or not 4 <= len(frame.jpeg) <= 256 * 1024:
        raise ValueError("preview bounds exceeded")
    # Enforce actual encoded dimensions, not caller-provided width/height. A
    # production model connector additionally requires bounded codec validation.
    from .gateway import jpeg_dimensions
    if jpeg_dimensions(frame.jpeg) != (640, 360):
        raise ValueError("encoded preview dimensions disagree")
    return {"observed_at": frame.observed_at, "valid_until": frame.observed_at + 180,
            "capture_session": frame.capture_session, "frame_id": frame.frame_id,
            "clock_mapping_revision": frame.clock_mapping_revision,
            "uncertainty_seconds": uncertainty}


def valid_observation(record: dict, now: float) -> bool:
    observed, until = record.get("observed_at"), record.get("valid_until")
    uncertainty = record.get("uncertainty_seconds")
    return (finite(now) and finite(observed) and finite(until) and finite(uncertainty)
            and 0 <= uncertainty <= 1 and observed <= now < until
            and until == observed + 180 and isinstance(record.get("description"), str)
            and len(record["description"]) <= 4096)


class CurrentContext:
    """Independent consumer; poll current() on input, startup and every second.

    A retained 'online' flag cannot establish liveness after restart. Transport
    must supply live heartbeat messages from the credential-bound site adapter.
    Retired sessions cannot become current again in this consumer lifetime.
    """

    def __init__(self, site_id: str, camera_id: str):
        if site_id not in ("echo", "victoria") or not camera_id:
            raise ValueError("registered site/camera required")
        self.site_id, self.camera_id = site_id, camera_id
        self.session: str | None = None
        self.retired: set[str] = set()
        self.generation = -1
        self.heartbeat_at = float("-inf")
        self.sequence = -1
        self.record: dict | None = None
        self.received_at = float("-inf")
        self.expires_mono = float("-inf")

    def availability(self, session: str, generation: int, online: bool, now_mono: float,
                     *, retained: bool = False) -> bool:
        if (retained or not isinstance(session, str) or not 1 <= len(session) <= 128
                or session in self.retired or type(generation) is not int or generation < 0
                or type(online) is not bool or not finite(now_mono)):
            return False
        if session == self.session and not online:
            # LWT was bound at CONNECT, before later health generations. A
            # same-session offline signal invalidates regardless of its older
            # generation, without moving the generation high-water backward.
            self.heartbeat_at, self.record = float("-inf"), None
            self.generation = max(self.generation, generation)
            return True
        if session == self.session and generation < self.generation:
            return False
        if session != self.session or generation != self.generation:
            if self.session and session != self.session:
                self.retired.add(self.session)
            self.record, self.sequence = None, -1
        self.session, self.generation = session, generation
        self.heartbeat_at = now_mono if online else float("-inf")
        if not online:
            self.record = None
        return True

    def accept(self, record: dict, *, now_unix: float, now_mono: float,
               retained: bool = False) -> bool:
        if (retained or record.get("site_id") != self.site_id or record.get("camera_id") != self.camera_id
                or not self.session or record.get("adapter_session") != self.session
                or type(record.get("occupancy_generation")) is not int
                or record.get("occupancy_generation") != self.generation
                or type(record.get("sequence")) is not int or record["sequence"] <= self.sequence
                or not 0 <= now_mono - self.heartbeat_at <= 10
                or not valid_observation(record, now_unix)):
            return False
        self.record, self.sequence, self.received_at = dict(record), record["sequence"], now_mono
        self.expires_mono = now_mono + min(180, record["valid_until"] - now_unix)
        return True

    def current(self, *, now_unix: float, now_mono: float) -> dict | None:
        if self.record and (now_mono >= self.expires_mono or not valid_observation(self.record, now_unix)):
            self.record = None
        if (not self.record or not 0 <= now_mono - self.heartbeat_at <= 10
                or not 0 <= now_mono - self.received_at < 180
                or not valid_observation(self.record, now_unix)):
            return None
        return dict(self.record)
