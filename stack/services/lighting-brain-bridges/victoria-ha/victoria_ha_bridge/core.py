"""Bridge core: Frigate MQTT observations in, contract v1 events out.

No I/O here. The clock and the transport are injected, so tests replay MQTT
timelines with a fake clock and a fake brain.

State model, per subject (`occupancy`, `zone:desk`, `zone:sofa`):
- unknown until Frigate (or the Home Assistant seed) tells us a count;
- a known `(present, people_count)` pair after that.

Frigate publishes person counts only when they change and does not retain them,
so a person sitting still produces no messages. The bridge therefore holds the
last count indefinitely while Frigate is healthy, and re-asserts it in
heartbeats. Liveness comes from `frigate/stats` (every 60 s by default) and the
retained `frigate/available` topic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

EVENTS_SCHEMA = "activity-lighting-events-1"
CONTRACT_ZONES = ("desk", "sofa")
OCCUPANCY = "occupancy"

# A broker that is down this long after start-up (or after a disconnect) makes
# health false. Short on purpose: an unreachable broker is never "warming up".
MQTT_CONNECT_GRACE_S = 10.0

Clock = Callable[[], float]
Value = tuple[bool, "int | None"]  # (present, people_count)

log = logging.getLogger("victoria_ha_bridge")


@dataclass(frozen=True)
class Timing:
    """Contract v1 cadences. Adjustable, but the defaults are the contract."""

    state_heartbeat_s: float = 30.0
    state_ttl_s: float = 90.0
    health_heartbeat_s: float = 60.0
    health_ttl_s: float = 150.0
    retry_initial_s: float = 2.0
    retry_max_s: float = 30.0


@dataclass(frozen=True)
class PostResult:
    ok: bool
    status: int | None = None
    retryable: bool = False
    detail: str = ""


class Transport(Protocol):
    def post(self, body: dict[str, Any]) -> PostResult: ...


@dataclass
class OutboxStats:
    sent_events: int = 0
    sent_batches: int = 0
    failed_attempts: int = 0
    superseded: int = 0
    dropped_expired: int = 0
    dropped_rejected: int = 0


class Outbox:
    """Latest event per subject, retried with backoff, dropped once expired.

    Keeping only the newest event per subject bounds the buffer (at most one
    event per subject) and means a brain that comes back sees current state,
    not a replay. An event the brain could no longer use (past
    `observed_at + ttl_s`) is dropped rather than sent.
    """

    def __init__(self, transport: Transport, timing: Timing) -> None:
        self._transport = transport
        self._timing = timing
        self._pending: dict[str, dict[str, Any]] = {}
        self._failures = 0
        self._next_attempt_at = 0.0
        self.stats = OutboxStats()

    @property
    def pending(self) -> list[dict[str, Any]]:
        return list(self._pending.values())

    @property
    def brain_reachable(self) -> bool:
        return self._failures == 0

    def put(self, key: str, event: dict[str, Any]) -> None:
        if key in self._pending:
            del self._pending[key]
            self.stats.superseded += 1
        self._pending[key] = event

    def flush(self, now: float) -> None:
        for key, event in list(self._pending.items()):
            if event["observed_at"] + event["ttl_s"] <= now:
                del self._pending[key]
                self.stats.dropped_expired += 1
        if not self._pending or now < self._next_attempt_at:
            return

        sent_at = round(now, 3)
        events = [dict(event, sent_at=sent_at) for event in self._pending.values()]
        result = self._transport.post({"schema": EVENTS_SCHEMA, "events": events})

        if result.ok:
            if self._failures:
                log.info("brain reachable again after %d failed attempt(s)", self._failures)
            self._pending.clear()
            self._failures = 0
            self._next_attempt_at = 0.0
            self.stats.sent_batches += 1
            self.stats.sent_events += len(events)
        elif result.retryable:
            self._failures += 1
            self.stats.failed_attempts += 1
            delay = min(self._timing.retry_initial_s * 2 ** (self._failures - 1), self._timing.retry_max_s)
            self._next_attempt_at = now + delay
            if self._failures == 1:
                log.warning("brain unreachable (%s); buffering, retry in %.0f s", result.detail, delay)
        else:
            # The brain refused the batch (bad token, schema mismatch). Retrying
            # the same bytes cannot help, so drop it and say why.
            log.error("brain rejected %d event(s): HTTP %s %s", len(events), result.status, result.detail)
            self.stats.dropped_rejected += len(events)
            self._pending.clear()
            self._failures = 0
            self._next_attempt_at = 0.0


@dataclass
class BridgeConfig:
    home: str = "victoria"
    room: str = "den"
    source: str = "victoria-ha-bridge"
    topic_prefix: str = "frigate"
    camera: str = "den_oak"
    object_label: str = "person"
    # Contract zone ("desk"/"sofa") -> Frigate zone name. Empty until the owner
    # has drawn the zones in Frigate; an unlisted zone is never reported.
    zones: dict[str, str] = field(default_factory=dict)
    silence_timeout_s: float = 150.0
    timing: Timing = field(default_factory=Timing)

    def __post_init__(self) -> None:
        unknown = set(self.zones) - set(CONTRACT_ZONES)
        if unknown:
            raise ValueError(f"zones must be among {CONTRACT_ZONES}, got {sorted(unknown)}")


class Bridge:
    def __init__(self, config: BridgeConfig, clock: Clock, transport: Transport) -> None:
        self.config = config
        self._clock = clock
        self.outbox = Outbox(transport, config.timing)

        p = config.topic_prefix
        self._topic_available = f"{p}/available"
        self._topic_stats = f"{p}/stats"
        self._topic_detect = f"{p}/{config.camera}/detect/state"
        self._count_topics = {f"{p}/{config.camera}/{config.object_label}": OCCUPANCY}
        for zone, frigate_zone in config.zones.items():
            self._count_topics[f"{p}/{frigate_zone}/{config.object_label}"] = f"zone:{zone}"

        now = clock()
        self._started_at = now
        self._connected = False
        self._ever_connected = False
        self._disconnected_at = now
        self._connected_at: float | None = None
        self._last_frigate_at: float | None = None
        self._available: str | None = None
        self._detect_on: bool | None = None
        self._camera_fps: float | None = None
        self._camera_in_stats: bool | None = None

        self._values: dict[str, Value] = {}
        self._sent_values: dict[str, Value] = {}
        self._last_state_emit: dict[str, float] = {}
        self._health: tuple[bool, str | None] | None = None
        self._sent_health: tuple[bool, str | None] | None = None
        self._last_health_emit: float | None = None

    # ── inputs ──────────────────────────────────────────────────────────

    @property
    def subjects(self) -> list[str]:
        return list(self._count_topics.values())

    def subscriptions(self) -> list[str]:
        return [self._topic_available, self._topic_stats, self._topic_detect, *self._count_topics]

    def on_connection(self, connected: bool) -> None:
        now = self._clock()
        if connected:
            if self._ever_connected:
                # Messages may have been missed while disconnected, and counts
                # are not retained. Forget them; the runner reseeds from HA.
                self._values.clear()
                self._available = None
            self._connected = True
            self._ever_connected = True
            self._connected_at = now
            log.info("mqtt connected")
        else:
            if self._connected:
                log.warning("mqtt disconnected")
            self._connected = False
            self._disconnected_at = now
        self.tick()

    def on_message(self, topic: str, payload: bytes | str, retained: bool = False) -> None:
        now = self._clock()
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
        text = text.strip()
        self._last_frigate_at = now

        if topic == self._topic_available:
            self._on_available(text.lower())
        elif topic == self._topic_stats:
            self._on_stats(text)
        elif topic == self._topic_detect:
            self._detect_on = text.upper() == "ON"
        elif topic in self._count_topics:
            self._on_count(self._count_topics[topic], text)
        self.tick()

    def seed(self, values: dict[str, Value]) -> list[str]:
        """Fill unknown subjects from a Home Assistant snapshot. Never overrides MQTT."""
        seeded = []
        for subject, value in values.items():
            if subject in self._count_topics.values() and subject not in self._values:
                self._values[subject] = value
                seeded.append(subject)
        if seeded:
            log.info("seeded from home assistant: %s", ", ".join(f"{s}={self._values[s]}" for s in seeded))
        self.tick()
        return seeded

    def _on_available(self, state: str) -> None:
        previous = self._available
        self._available = state
        if state == "offline" and previous != "offline":
            log.warning("frigate reported offline")
            self._values.clear()
        elif state == "online" and previous == "offline":
            # Frigate (re)started with nothing tracked. It publishes a count as
            # soon as it sees anyone, after this message, so zero is correct.
            log.info("frigate back online; counts reset to zero")
            for subject in self._count_topics.values():
                self._values[subject] = (False, 0)

    def _on_stats(self, text: str) -> None:
        try:
            stats = json.loads(text)
            camera = stats.get("cameras", {}).get(self.config.camera)
        except (ValueError, AttributeError):
            return
        self._camera_in_stats = isinstance(camera, dict)
        fps = camera.get("camera_fps") if isinstance(camera, dict) else None
        self._camera_fps = float(fps) if isinstance(fps, (int, float)) else None

    def _on_count(self, subject: str, text: str) -> None:
        try:
            count = int(float(text))
        except ValueError:
            return
        count = max(count, 0)
        value = (count > 0, count)
        if self._values.get(subject) != value:
            log.info("%s: %d %s", subject, count, self.config.object_label)
        self._values[subject] = value
        if subject == OCCUPANCY and count == 0:
            # Nobody on camera means nobody in any of its zones.
            for zone_subject in self._count_topics.values():
                if zone_subject != OCCUPANCY:
                    self._values[zone_subject] = (False, 0)

    # ── health ──────────────────────────────────────────────────────────

    def _evaluate_health(self, now: float) -> tuple[bool, str | None] | None:
        cfg = self.config
        if not self._connected:
            since = self._disconnected_at if self._ever_connected else self._started_at
            if now - since < MQTT_CONNECT_GRACE_S:
                return self._health  # a brief blip keeps the last verdict
            return (False, "mqtt broker not connected")
        if self._available == "offline":
            return (False, "frigate reported offline")
        reference = self._last_frigate_at if self._last_frigate_at is not None else self._connected_at
        if reference is not None and now - reference > cfg.silence_timeout_s:
            return (False, f"no frigate messages within {cfg.silence_timeout_s:.0f} s")
        if self._last_frigate_at is None:
            return None  # connected, waiting for the retained availability message
        if self._detect_on is False:
            return (False, f"detection is off for {cfg.camera}")
        if self._camera_in_stats is False:
            return (False, f"camera {cfg.camera} missing from frigate stats")
        if self._camera_fps == 0:
            return (False, f"camera {cfg.camera} has no frames (camera_fps 0)")
        return (True, None)

    # ── outputs ─────────────────────────────────────────────────────────

    def tick(self) -> None:
        """Emit changes and due heartbeats, then try to deliver."""
        now = self._clock()
        timing = self.config.timing

        health = self._evaluate_health(now)
        if health != self._health:
            if health is not None:
                log.log(logging.INFO if health[0] else logging.WARNING,
                        "frigate health: %s", "ok" if health[0] else health[1])
            self._health = health
        if health is not None and (
            health != self._sent_health
            or self._last_health_emit is None
            or now - self._last_health_emit >= timing.health_heartbeat_s
        ):
            self.outbox.put("health:frigate", self._event(
                "health", now, timing.health_ttl_s, component="frigate", ok=health[0], detail=health[1]))
            self._sent_health = health
            self._last_health_emit = now

        # Occupancy is only asserted while Frigate is known healthy. Otherwise
        # the last events simply expire at the brain after ttl_s.
        if health is not None and health[0]:
            for subject in self._count_topics.values():
                value = self._values.get(subject)
                if value is None:
                    continue
                last = self._last_state_emit.get(subject)
                if value != self._sent_values.get(subject) or last is None or now - last >= timing.state_heartbeat_s:
                    self.outbox.put(subject, self._state_event(subject, value, now))
                    self._sent_values[subject] = value
                    self._last_state_emit[subject] = now
        else:
            # Re-send immediately once healthy again.
            self._sent_values.clear()
            self._last_state_emit.clear()

        self.outbox.flush(now)

    def _state_event(self, subject: str, value: Value, now: float) -> dict[str, Any]:
        present, count = value
        ttl = self.config.timing.state_ttl_s
        if subject == OCCUPANCY:
            return self._event("occupancy", now, ttl, present=present, people_count=count)
        return self._event("zone", now, ttl, zone=subject.split(":", 1)[1], present=present, people_count=count)

    def _event(self, type_: str, now: float, ttl: float, **extra: Any) -> dict[str, Any]:
        cfg = self.config
        return {
            "type": type_,
            "home": cfg.home,
            "room": cfg.room,
            "source": cfg.source,
            "observed_at": round(now, 3),
            "sent_at": None,
            "ttl_s": ttl,
            **extra,
        }

    def snapshot(self) -> dict[str, Any]:
        """Current view, for logs and tests."""
        return {
            "mqtt_connected": self._connected,
            "frigate_available": self._available,
            "health": self._health,
            "values": dict(self._values),
            "pending": len(self.outbox.pending),
            "brain_reachable": self.outbox.brain_reachable,
        }
