"""The publisher process: MQTT in, beliefs out, no Home Assistant token.

One second at a time:

1. take whatever MQTT delivered (the Home Assistant mirror, Frigate person
   counts) and, every ``OBSERVER_POLL_S``, one observer poll;
2. ask ``health`` whether the mirror is fresh, the observer is fresh and MQTT
   is up. If it is not, stop the heartbeat and publish every belief as
   unknown -- Home Assistant's ``publisher_fresh`` sensor then goes off 180 s
   later and the legacy path keeps the house;
3. otherwise run the TV machine, the asleep estimator and the activity
   tracker, publish retained state on change or every 60 s, and publish the
   heartbeat every 60 s;
4. journal the decision as JSONL.

Discovery is published on every connect and every 10 minutes; the retained
state topics are cleared once at startup so a crashed process's last belief
cannot outlive it; the last will is registered before connect and a clean
shutdown publishes availability ``offline``.

Health is a gate on deciding, not only on publishing: while the inputs are
untrusted the machines are not advanced, and on recovery they are rebuilt
(the estimator keeping its state, the TV machine starting from TV_OFF) so
that an hour of silence cannot be counted as an hour of quiet and latch the
house asleep on the first good tick.

No Home Assistant token exists in this process. The only outbound connections
are the broker and the observer, and ``/healthz`` is served on loopback: the
process exits 78 if it is asked to publish that port anywhere else.

M4 ships no beliefs: ``NoBeliefs`` means every machine takes its
deterministic path and nothing is imported from ``lighting_beliefs.egress``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .activity import ActivityTracker, ZoneMap, load_zones
from .beliefs import BeliefSource, NoBeliefs
from .estimator import AsleepEstimator, CameraSignal, EstimatorInputs
from .health import Health
from .inputs.mirror import FrigateState, MirrorState
from .inputs.observer import DEFAULT_OBSERVER_URL, ObserverClient, ObserverError
from .journal import RETENTION_DAYS, Journal
from .publish.discovery import (DISCOVERY_PREFIX, DISCOVERY_REPUBLISH_S, PAYLOAD_NONE,
                                PAYLOAD_OFF, PAYLOAD_ON, TOPIC_BASE, UNKNOWN_STATE,
                                EntitySet, build_entities)
from .publish.state import StatePublisher
from .stories import HEARTBEAT_S, OBSERVER_POLL_S, iso
from .tv_machine import TvMachine

MODE_SHADOW = "shadow"
MODE_LIVE = "live"
MODES = (MODE_SHADOW, MODE_LIVE)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_BIND_REFUSED = 78
"""Same refusal as the video labeler: a surface that must stay on loopback
does not start at all when it is asked to be reachable."""

DEFAULT_PORT = 8105
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_PUBLISH_BIND_ADDR = "127.0.0.1"
DEFAULT_MQTT_HOST = "192.168.0.125"
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_KEEPALIVE = 60
DEFAULT_JOURNAL_DIR = "/data"
TICK_S = 1.0
JOURNAL_SNAPSHOT_S = 60
UNSPECIFIED_HOSTS = ("0.0.0.0", "::", "")
LOOPBACK_NAMES = ("localhost",)


class ConfigError(RuntimeError):
    """The environment is not usable. Exit 2."""


class BindRefused(RuntimeError):
    """The health port would be reachable off loopback. Exit 78."""


def log(message: str) -> None:
    """One line to stdout; never a payload, a name or a credential."""
    print(f"[lighting-publisher] {message}", flush=True)


def _is_loopback(host: str) -> bool:
    value = (host or "").strip().strip("[]").lower()
    if value in LOOPBACK_NAMES:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _is_unspecified(host: str) -> bool:
    return (host or "").strip().strip("[]") in UNSPECIFIED_HOSTS


def _env_int(env: dict, key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer") from None


@dataclass(frozen=True)
class Config:
    """Everything the process reads from its environment."""

    mode: str = MODE_SHADOW
    mqtt_host: str = DEFAULT_MQTT_HOST
    mqtt_port: int = DEFAULT_MQTT_PORT
    mqtt_username: str = ""
    mqtt_password_file: str | None = None
    mqtt_keepalive: int = DEFAULT_MQTT_KEEPALIVE
    observer_url: str = DEFAULT_OBSERVER_URL
    bind_host: str = DEFAULT_BIND_HOST
    publish_bind_addr: str = DEFAULT_PUBLISH_BIND_ADDR
    port: int = DEFAULT_PORT
    journal_dir: str | None = DEFAULT_JOURNAL_DIR
    journal_retention_days: int = RETENTION_DAYS
    zones_path: str | None = None
    topic_base: str = TOPIC_BASE
    discovery_prefix: str = DISCOVERY_PREFIX
    timezone: str | None = None

    @property
    def shadow(self) -> bool:
        return self.mode == MODE_SHADOW

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        """Build from the environment, refusing a non-loopback publication."""
        env = dict(os.environ if env is None else env)
        mode = (env.get("PUBLISHER_MODE") or MODE_SHADOW).strip().lower()
        if mode not in MODES:
            raise ConfigError(f"PUBLISHER_MODE must be one of {MODES}")
        publish_addr = (env.get("PUBLISH_BIND_ADDR") or DEFAULT_PUBLISH_BIND_ADDR).strip()
        if not _is_loopback(publish_addr):
            raise BindRefused(
                "PUBLISH_BIND_ADDR must be a loopback address: /healthz carries the "
                "house's belief state and is never published on the LAN")
        bind_host = (env.get("BIND_HOST") or DEFAULT_BIND_HOST).strip()
        if not (_is_loopback(bind_host) or _is_unspecified(bind_host)):
            raise BindRefused(
                "BIND_HOST must be loopback, or unspecified inside a container whose "
                "port is published on loopback")
        return cls(
            mode=mode,
            mqtt_host=(env.get("MQTT_HOST") or DEFAULT_MQTT_HOST).strip(),
            mqtt_port=_env_int(env, "MQTT_PORT", DEFAULT_MQTT_PORT),
            mqtt_username=(env.get("MQTT_USERNAME") or "").strip(),
            mqtt_password_file=(env.get("MQTT_PASSWORD_FILE") or "").strip() or None,
            mqtt_keepalive=_env_int(env, "MQTT_KEEPALIVE", DEFAULT_MQTT_KEEPALIVE),
            observer_url=(env.get("OBSERVER_URL") or DEFAULT_OBSERVER_URL).strip(),
            bind_host=bind_host, publish_bind_addr=publish_addr,
            port=_env_int(env, "PORT", DEFAULT_PORT),
            journal_dir=(env.get("JOURNAL_DIR") or DEFAULT_JOURNAL_DIR).strip() or None,
            journal_retention_days=_env_int(env, "JOURNAL_RETENTION_DAYS", RETENTION_DAYS),
            zones_path=(env.get("ZONES_PATH") or "").strip() or None,
            topic_base=(env.get("TOPIC_BASE") or TOPIC_BASE).strip(),
            discovery_prefix=(env.get("DISCOVERY_PREFIX") or DISCOVERY_PREFIX).strip(),
            timezone=(env.get("TZ") or "").strip() or None)


def read_secret(path: str | None) -> str:
    """Read a secret file. The value is never logged, printed or journaled."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as exc:
        raise ConfigError(f"MQTT_PASSWORD_FILE unreadable ({type(exc).__name__})") from None


class Publisher:
    """The tick loop's state: inputs, machines, entities and publishing."""

    def __init__(self, config: Config, client: object, observer: ObserverClient,
                 zones: ZoneMap, journal: Journal | None = None,
                 beliefs: BeliefSource | None = None,
                 clock: Callable[[], dt.datetime] | None = None) -> None:
        self.config = config
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.client = client
        self.observer = observer
        self.zones = zones
        self.journal = journal if journal is not None else Journal(None, enabled=False)
        self.beliefs = beliefs if beliefs is not None else NoBeliefs()
        self.entities: EntitySet = build_entities(
            zones, shadow=config.shadow, topic_base=config.topic_base,
            discovery_prefix=config.discovery_prefix)
        self.mirror = MirrorState()
        self.frigate = FrigateState(zones)
        self.health = Health()
        self.state = StatePublisher(client)
        self.tv = TvMachine()
        self.estimator = AsleepEstimator()
        self.activity = ActivityTracker(zones)
        self.started_at: dt.datetime | None = None
        self.connected = False
        self.ticks = 0
        self.gated_ticks = 0
        self.discovery_publishes = 0
        self._zoneinfo = self._load_timezone(config.timezone)
        self._last_discovery_at: dt.datetime | None = None
        self._last_heartbeat_at: dt.datetime | None = None
        self._last_snapshot_at: dt.datetime | None = None
        self._last_observer_poll_at: dt.datetime | None = None
        self._last_health_ok: bool | None = None
        self._lock = threading.Lock()
        self._healthz: dict = {"ok": False, "mode": config.mode, "reasons": ["starting"]}

    @staticmethod
    def _load_timezone(name: str | None):
        if not name:
            return None
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - an unknown zone must not stop the house
            log(f"TZ {name!r} is not a known timezone; using the system zone")
            return None

    def local_time(self, now: dt.datetime) -> dt.datetime:
        """The estimator's clock: local hours decide the night window."""
        if self._zoneinfo is not None:
            return now.astimezone(self._zoneinfo)
        return now.astimezone()

    # --- MQTT lifecycle --------------------------------------------------------

    def subscriptions(self) -> tuple[str, ...]:
        return self.mirror.topics() + self.frigate.topics()

    def on_connect(self, client=None, userdata=None, flags=None, reason_code=None,
                   properties=None) -> None:
        """Announce, clear, discover, subscribe -- in that order, every connect."""
        self.connected = True
        self.health.set_mqtt(True)
        self.state.forget()
        self.state.announce(self.entities.availability_topic)
        self.state.clear(self.entities.state_topics())
        self.publish_discovery(self.clock())
        for topic in self.subscriptions():
            self.client.subscribe(topic, qos=0)
        log(f"connected: mode={self.config.mode} entities={len(self.entities.all())} "
            f"subscriptions={len(self.subscriptions())}")

    def on_disconnect(self, client=None, userdata=None, flags=None, reason_code=None,
                      properties=None) -> None:
        """A dropped broker is a health failure; paho reconnects on its own."""
        self.connected = False
        self.health.set_mqtt(False)
        log("disconnected from the broker")

    def on_message(self, client=None, userdata=None, message=None) -> None:
        """Route one MQTT message to the mirror or the Frigate parser."""
        if message is None:
            return
        now = self.clock()
        topic = getattr(message, "topic", "") or ""
        payload = getattr(message, "payload", b"")
        kind = self.mirror.apply(topic, payload, now)
        if kind is not None:
            if kind == "heartbeat":
                # Only the heartbeat proves Home Assistant is alive: the entity
                # topics are retained, so a dead house still replays them once
                # on connect. A stamp in the future is clock skew, not freshness.
                stamp = self.mirror.heartbeat_at or now
                self.health.note_mirror(min(stamp, now))
            return
        self.frigate.apply(topic, payload, now)

    def publish_discovery(self, now: dt.datetime) -> int:
        """Publish every discovery config retained."""
        count = 0
        for topic, payload in self.entities.discovery_messages():
            self.client.publish(topic, payload, qos=0, retain=True)
            count += 1
        self._last_discovery_at = now
        self.discovery_publishes += 1
        return count

    def shutdown(self, now: dt.datetime | None = None) -> None:
        """Clean stop: availability offline, one last journal line."""
        now = now or self.clock()
        try:
            self.state.shutdown(self.entities.availability_topic)
        except Exception as exc:  # noqa: BLE001 - a broken socket must not hide the stop
            log(f"shutdown publish failed ({type(exc).__name__})")
        self.journal.write({"t": iso(now), "event": "shutdown", "mode": self.config.mode,
                            "ticks": self.ticks, "gated_ticks": self.gated_ticks}, now)
        log("stopped")

    # --- the observer ----------------------------------------------------------

    def poll_observer(self, now: dt.datetime, force: bool = False) -> bool:
        """Poll every ``OBSERVER_POLL_S``; a failure is a stale observer."""
        due = (force or self._last_observer_poll_at is None
               or (now - self._last_observer_poll_at).total_seconds() >= OBSERVER_POLL_S)
        if not due:
            return False
        self._last_observer_poll_at = now
        try:
            self.observer.poll(now)
        except ObserverError:
            return False
        self.health.note_observer(now)
        return True

    # --- one tick ---------------------------------------------------------------

    def tick(self, now: dt.datetime) -> dict:
        """Advance to ``now``: decide, publish, journal. Returns the record."""
        if self.started_at is None:
            self.started_at = now
        self.ticks += 1
        self.poll_observer(now)
        status = self.health.status(now)
        if (self._last_discovery_at is None
                or (now - self._last_discovery_at).total_seconds() >= DISCOVERY_REPUBLISH_S):
            if self.connected:
                self.publish_discovery(now)
        recovered = self._last_health_ok is False and status.ok
        if recovered:
            self._reset_machines(now)
        self._last_health_ok = status.ok

        if not status.ok:
            self.gated_ticks += 1
            record = self._publish_unknown(now, status)
        else:
            record = self._decide_and_publish(now, status)
        record["recovered"] = recovered
        self._store_healthz(now, status, record)
        self._journal(now, record)
        return record

    def _reset_machines(self, now: dt.datetime) -> None:
        """Rebuild the machines after an outage instead of trusting old clocks.

        Silence is not quiet: the estimator's quiet clock must restart from
        the recovery, or fifteen unhealthy minutes would latch the house
        asleep on the first good tick. The estimator keeps its state (a house
        that was asleep still is); the TV machine starts at TV_OFF and
        re-derives within a tick or two from the live TV state.
        """
        left_asleep_at = self.estimator._left_asleep_at
        self.estimator = AsleepEstimator(journal=self.estimator.journal,
                                         initial=self.estimator.state)
        # The re-arm stamp must survive the rebuild. Every other clock restarts
        # because silence is not evidence, which is fail-closed; this one is the
        # opposite, because forgetting when the latch last cleared lets the
        # house latch again inside the forty-five minutes the legacy automation
        # refuses. Home Assistant's own rule reads the boolean's last_changed,
        # which outlives a publisher restart entirely, so dropping it here would
        # make the shadow diverge from the thing it shadows on the most routine
        # event there is, a broker flap.
        self.estimator._left_asleep_at = left_asleep_at
        self.tv = TvMachine(journal=self.tv.journal)
        log("health recovered: machine clocks restarted")

    def _publish_unknown(self, now: dt.datetime, status) -> dict:
        """Every belief unknown, no heartbeat: Home Assistant falls back."""
        attributes = json.dumps({"unknown": True, "reasons": list(status.reasons),
                                 "mode": self.config.mode}, sort_keys=True)
        published = 0
        if self.state.publish(self.entities.tv.state_topic, PAYLOAD_NONE, now):
            published += 1
        if self.entities.tv.attributes_topic:
            self.state.changed_only(self.entities.tv.attributes_topic, attributes, now)
        if self.state.publish(self.entities.asleep.state_topic, UNKNOWN_STATE, now):
            published += 1
        if self.entities.asleep.attributes_topic:
            self.state.changed_only(self.entities.asleep.attributes_topic, attributes, now)
        for entity in self.entities.activity.values():
            if self.state.publish(entity.state_topic, UNKNOWN_STATE, now):
                published += 1
        return {"t": iso(now), "event": "gated", "mode": self.config.mode,
                "health": status.as_dict(), "published": published,
                "heartbeat": False}

    def _decide_and_publish(self, now: dt.datetime, status) -> dict:
        """Run the three machines and publish what they decided."""
        beliefs = self.beliefs.current(now)
        reading = self.observer.last
        observer_fresh = status.observer_fresh
        living_room = self.zones.living_room_camera
        track = bool(reading is not None and observer_fresh and reading.track_in(living_room))
        occupancy = self.frigate.occupancy()

        tv = self.tv.update(now, tv_state=self.mirror.tv_state,
                            living_room_occupied=self.frigate.living_room_occupied(),
                            sofa_stable=self.frigate.sofa_stable(now),
                            living_room_track=track, beliefs=beliefs)
        estimator_inputs = self._estimator_inputs(now, observer_fresh, beliefs)
        asleep = self.estimator.update(now, estimator_inputs)
        activity = self.activity.update(now, beliefs, occupancy)

        published = 0
        if self.state.publish(self.entities.tv.state_topic,
                              PAYLOAD_ON if tv.tv_watching else PAYLOAD_OFF, now):
            published += 1
        if self.entities.tv.attributes_topic:
            self.state.changed_only(self.entities.tv.attributes_topic,
                                    json.dumps(tv.as_attributes(), sort_keys=True), now)
        if self.state.publish(self.entities.asleep.state_topic, asleep.state, now):
            published += 1
        if self.entities.asleep.attributes_topic:
            self.state.changed_only(
                self.entities.asleep.attributes_topic,
                json.dumps(asleep.as_attributes(), sort_keys=True, default=str), now)
        for zone, value in activity.items():
            entity = self.entities.activity.get(zone)
            if entity is None:
                continue
            if self.state.publish(entity.state_topic, value, now):
                published += 1
        heartbeat = self._publish_heartbeat(now)
        return {"t": iso(now), "event": "decision", "mode": self.config.mode,
                "tv": {"tv_watching": tv.tv_watching, "state": tv.state,
                       "changed": tv.changed, "p_attention": tv.p_attention},
                "asleep": {"state": asleep.state, "changed": asleep.changed,
                           "reassert": asleep.reassert, "evidence": asleep.evidence},
                "activity": activity, "published": published, "heartbeat": heartbeat,
                "health": status.as_dict()}

    def _estimator_inputs(self, now: dt.datetime, observer_fresh: bool,
                          beliefs) -> EstimatorInputs:
        """Assemble story S's inputs from the mirror, Frigate and the observer."""
        reading = self.observer.last
        cameras = {}
        for camera in self.zones.cameras:
            present = None
            if reading is not None and observer_fresh:
                present = reading.present(camera)
            cameras[camera] = CameraSignal(
                frigate_person=self.frigate.camera_person(camera),
                frigate_changed=self.frigate.camera_changed(camera),
                observer_present=present,
                observer_fresh=bool(observer_fresh and present is not None))
        return EstimatorInputs(
            local_time=self.local_time(now), profile=self.mirror.profile,
            user_at_home=self.mirror.user_at_home,
            user_at_home_changed=self.mirror.user_at_home_changed,
            front_door_occupied=self.frigate.front_door_occupied(),
            front_door_changed=self.frigate.front_door_changed(),
            cameras=cameras, tv_state=self.mirror.tv_state,
            p_attention0=(beliefs.p_attention_eq0 if beliefs is not None else None),
            last_brighten=self.mirror.last_command_at(), last_wake=None,
            latch_on=self.mirror.asleep_latch, latch_writer=self.mirror.asleep_writer,
            latch_changed=self.mirror.asleep_latch_changed)

    def _publish_heartbeat(self, now: dt.datetime) -> bool:
        """The heartbeat is the proof of life; it stops when health drops."""
        if (self._last_heartbeat_at is not None
                and (now - self._last_heartbeat_at).total_seconds() < HEARTBEAT_S):
            return False
        self.client.publish(self.entities.heartbeat.state_topic,
                            self.local_time(now).isoformat(timespec="seconds"), qos=0, retain=True)
        self._last_heartbeat_at = now
        return True

    # --- journal and /healthz ---------------------------------------------------

    def _journal(self, now: dt.datetime, record: dict) -> None:
        """Every change, plus a snapshot a minute, so a quiet night is legible."""
        interesting = bool(record.get("published")) or record.get("heartbeat")
        tv = record.get("tv") or {}
        asleep = record.get("asleep") or {}
        interesting = interesting or tv.get("changed") or asleep.get("changed")
        due = (self._last_snapshot_at is None
               or (now - self._last_snapshot_at).total_seconds() >= JOURNAL_SNAPSHOT_S)
        if not (interesting or due):
            return
        if due:
            self._last_snapshot_at = now
        self.journal.write(record, now)

    def _store_healthz(self, now: dt.datetime, status, record: dict) -> None:
        payload = {
            "ok": status.ok,
            "mode": self.config.mode,
            "mirror_fresh": status.mirror_fresh,
            "observer_fresh": status.observer_fresh,
            "mqtt_up": status.mqtt_up,
            "reasons": list(status.reasons),
            "mirror_age_s": status.mirror_age_s,
            "observer_age_s": status.observer_age_s,
            "tv_state_machine": self.tv.state,
            "asleep_state": self.estimator.state,
            "ticks": self.ticks,
            "gated_ticks": self.gated_ticks,
            "uptime_s": (now - self.started_at).total_seconds() if self.started_at else 0.0,
            "last_decision_at": record.get("t"),
            "journal": self.journal.as_dict(),
            "publishes": self.state.as_dict(),
            "mirror": self.mirror.as_dict(),
            "frigate": self.frigate.as_dict(),
            "observer": self.observer.as_dict(),
        }
        with self._lock:
            self._healthz = payload

    def healthz(self) -> dict:
        with self._lock:
            return dict(self._healthz)


class HealthHandler(BaseHTTPRequestHandler):
    """``GET /healthz`` and nothing else."""

    provider: Callable[[], dict] = staticmethod(dict)
    server_version = "lighting-publisher"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        if self.path.split("?")[0] != "/healthz":
            self.send_error(404)
            return
        body = json.dumps(type(self).provider(), sort_keys=True, default=str).encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # noqa: D102 - silence the access log
        return


def serve_health(host: str, port: int, provider: Callable[[], dict]) -> ThreadingHTTPServer:
    """Start the health server on its own thread."""
    handler = type("BoundHealthHandler", (HealthHandler,), {"provider": staticmethod(provider)})
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name="healthz", daemon=True)
    thread.start()
    return server


def build_mqtt_client(config: Config) -> object:
    """A paho client with the last will registered before connect."""
    import paho.mqtt.client as mqtt  # imported here so the tests need no paho

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"lighting-publisher-{config.mode}")
    if config.mqtt_username:
        client.username_pw_set(config.mqtt_username, read_secret(config.mqtt_password_file))
    return client


def run(config: Config, clock: Callable[[], dt.datetime] | None = None) -> int:
    """Wire everything up and tick until a signal says stop."""
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    zones = load_zones(config.zones_path)
    observer = ObserverClient(config.observer_url)
    # The journal's file day must be the publisher's configured zone, not the
    # machine's: the report filters a range by the file name, so a mismatch
    # silently drops a night and still exits clean.
    journal = Journal(config.journal_dir, config.journal_retention_days,
                      tz=Publisher._load_timezone(config.timezone))
    client = build_mqtt_client(config)
    publisher = Publisher(config, client, observer, zones, journal=journal, clock=clock)

    entities = publisher.entities
    client.will_set(entities.availability_topic, "offline", qos=0, retain=True)
    client.on_connect = publisher.on_connect
    client.on_disconnect = publisher.on_disconnect
    client.on_message = publisher.on_message

    server = serve_health(config.bind_host, config.port, publisher.healthz)
    log(f"health on {config.bind_host}:{config.port} (published on "
        f"{config.publish_bind_addr}); mode={config.mode}")

    stopping = threading.Event()

    def _stop(signum, frame) -> None:  # noqa: ANN001 - signal's signature
        stopping.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    journal.rotate(clock())
    # The journal never raises, so a directory the container cannot write --
    # the usual cause being a volume created by root while the process runs as
    # 10001 -- would cost a whole shadow week in silence. Say so once, loudly,
    # at startup.
    failure = journal.probe(clock())
    if failure:
        log(f"WARNING journal is not writable at {config.journal_dir} ({failure}); "
            "the publisher will run and publish, but this week's evidence will be "
            "empty -- fix the directory's ownership and restart")
    client.connect_async(config.mqtt_host, config.mqtt_port, config.mqtt_keepalive)
    client.loop_start()
    try:
        while not stopping.is_set():
            started = time.monotonic()
            try:
                publisher.tick(clock())
            except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the house
                log(f"tick failed ({type(exc).__name__})")
            stopping.wait(max(0.0, TICK_S - (time.monotonic() - started)))
    finally:
        publisher.shutdown(clock())
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:  # noqa: BLE001
            log(f"disconnect failed ({type(exc).__name__})")
        server.shutdown()
        server.server_close()
    return EXIT_OK


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    """Entry point. Exits 78 on a non-loopback bind, 2 on a bad environment."""
    try:
        config = Config.from_env(env)
    except BindRefused as exc:
        print(f"lighting-publisher: {exc}", file=sys.stderr)
        return EXIT_BIND_REFUSED
    except ConfigError as exc:
        print(f"lighting-publisher: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return run(config)
    except ConfigError as exc:
        print(f"lighting-publisher: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except ValueError as exc:
        print(f"lighting-publisher: {exc}", file=sys.stderr)
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
