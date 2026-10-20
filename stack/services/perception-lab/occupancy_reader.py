"""Victoria-LAN occupancy reader: the one bound occupancy entity, in memory.

Plan S1.2 (with red-team #16): the Lab pumps' occupancy gate no longer has to
wait for the recording's 1 Hz 'ha.observation' rows. One reader per controller
polls exactly one camera-derived Frigate person-occupancy entity (default
binary_sensor.den_oak_person_occupancy) through the existing ha_client (HTTPS
required, origin from the private 'occupancy_reader' block; the owner points it
at a Victoria-local TLS front and LA :10001 stays the rollback) at 2 Hz on the
PreviewPump grid (its 0.5 s floor is the maximum rate).

The latest reading stays in memory with its observed time. reading(now) is the
gate: the same freshness, hold, grace and fail-closed rules as
occupancy_gate.OccupancyGate. A poll's observed_at is the snapshot's
acquisition start (as ha_ingest records it) and it is valid for TTL_SECONDS
(ha_ingest.HAIngestor's default), plus grace. 'on' is occupied, 'off' is vacant
(or occupied within hold_seconds of the newest 'on'), anything else (no
reading, unknown, unavailable, stale, a reading from the future, a stopped
reader) is unknown. A failed poll writes nothing: the last reading ages out as
the ledger gate's last row would. The hold never bridges an unknown or stale
reading.

Edges only: an off/unknown -> on and an on -> off transition each log one line
(no 2 Hz ledger rows, nothing written to any recording), and an
off/unknown -> on edge sets every subscribed pump's wake event so a vacant pump
ticks at once instead of after its fixed sleep. Construction does no I/O; the
poll thread starts with start().
"""
import logging
from pathlib import PurePosixPath
import threading
import time

import occupancy_gate
from occupancy_gate import ENTITY, OCCUPIED, UNKNOWN, VACANT, _finite

_log = logging.getLogger("perception_lab.occupancy")

DEFAULT_ENTITY = "binary_sensor.den_oak_person_occupancy"
# The private 'occupancy_reader' block of the live config (absent: the gate
# reads the recording's ledger rows as before).
REQUIRED = frozenset({"origin", "credential_file"})
OPTIONAL = frozenset({"entity", "api", "ca_file", "expected_hostname", "hold_seconds", "grace_seconds",
                      "interval_seconds"})
DEFAULTS = {"entity": DEFAULT_ENTITY, "api": "core", "hold_seconds": 10.0, "grace_seconds": 5.0,
            "interval_seconds": .5}
# Hold: 10 s until the cadence switch, then 5 s (plan section 15 #8); never outside 3-15 s.
BOUNDS = {"hold_seconds": (3, 15), "grace_seconds": (0, 10), "interval_seconds": (.5, 2)}
TTL_SECONDS = 3
RAW = {OCCUPIED: "on", VACANT: "off", UNKNOWN: "unknown"}


def settings(value):
    """Exact reader settings (no I/O): DEFAULTS for absent optional keys."""
    if not isinstance(value, dict) or set(value) - REQUIRED - OPTIONAL or not REQUIRED <= set(value):
        raise ValueError("Exact occupancy reader provisioning required")
    config = {**DEFAULTS, **value}
    for key, (low, high) in BOUNDS.items():
        if not _finite(config[key]) or not low <= config[key] <= high:
            raise ValueError(f"occupancy reader {key} outside its bound")
    if not isinstance(config["entity"], str) or not ENTITY.fullmatch(config["entity"]):
        raise ValueError("exact binary_sensor occupancy entity required")
    if config["api"] not in ("core", "perception_lab_source"):
        raise ValueError("supported HA read api required")
    for key in ("credential_file", "ca_file"):
        if key in config and (not isinstance(config[key], str) or not PurePosixPath(config[key]).is_absolute()):
            raise ValueError("Absolute occupancy reader credential/trust paths required")
    if "expected_hostname" in config and not isinstance(config["expected_hostname"], str):
        raise ValueError("Pinned HA origin hostname required")
    return config


def check_config(value):
    """Validate the block as the controller will build it; reads no credential
    and contacts no host. Returns the settings."""
    config = settings(value)
    _client(config, trust=False)
    return config


def _client(config, clock=time.time, *, trust=True):
    """The one-entity HA client (HTTPS origin and pinned host checked here);
    trust=False skips loading the CA file, for validation only."""
    import http.client
    from ha_client import HAStateClient
    from managed_sources import _connection_factory, _credential_reader, _pin
    factory = _connection_factory(config) if trust else http.client.HTTPSConnection
    client = HAStateClient(config["origin"], _credential_reader(config["credential_file"]), [config["entity"]],
                           clock=clock, connection_factory=factory, api=config["api"])
    _pin(config, client)
    return client


def from_config(value, *, clock=time.time):
    """The controller's reader from a validated block (not started)."""
    config = settings(value)
    return OccupancyReader(_client(config, clock), config["entity"], hold_seconds=config["hold_seconds"],
                           grace_seconds=config["grace_seconds"], interval_seconds=config["interval_seconds"],
                           clock=clock)


class OccupancyReader:
    """The in-memory gate for one entity. reading() / __call__ return the
    pumps' {'state','reason','observed_at'}; state() the raw on/off/unknown."""

    def __init__(self, client, entity=DEFAULT_ENTITY, *, hold_seconds=DEFAULTS["hold_seconds"],
                 grace_seconds=DEFAULTS["grace_seconds"], interval_seconds=DEFAULTS["interval_seconds"],
                 clock=time.time, monotonic=time.monotonic):
        from preview_client import PreviewPump
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity):
            raise ValueError(occupancy_gate.NOT_PROVISIONED)
        for key, value in (("hold_seconds", hold_seconds), ("grace_seconds", grace_seconds),
                           ("interval_seconds", interval_seconds)):
            low, high = BOUNDS[key]
            if not _finite(value) or not low <= value <= high:
                raise ValueError(f"occupancy reader {key} outside its bound")
        if not callable(client):
            raise ValueError("occupancy reader HA client required")
        self.client, self.entity = client, entity
        self.hold, self.grace, self.ttl = hold_seconds, grace_seconds, TTL_SECONDS
        self.clock = clock
        self.lock = threading.Lock()
        self.latest = None   # {'observed_at','valid_until','state','status'} of the newest poll
        self.last_on = None  # observed_at of the newest 'on' reading
        self.edges = {"on": 0, "off": 0}
        self.last_edge = None
        self.subscribers = set()
        self.started = False
        self.pump = PreviewPump(self._fetch, self._consume, lambda: True, interval=interval_seconds, clock=monotonic)
        self.pump.thread.name = "lab-occupancy"
        self.thread = self.pump.thread

    # --- lifecycle -----------------------------------------------------
    def start(self):
        """Start polling once; later calls do nothing."""
        with self.lock:
            if self.started:
                return
            self.started = True
        self.pump.start()

    def stop(self, timeout=None):
        """Stop polling; every later reading is unknown."""
        self.pump.stop()
        if timeout is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def subscribe(self, event):
        """A pump's wake event, set at every off/unknown -> on edge."""
        with self.lock:
            self.subscribers.add(event)

    def unsubscribe(self, event):
        with self.lock:
            self.subscribers.discard(event)

    # --- polling -------------------------------------------------------
    def _fetch(self):
        return self.client()

    def _consume(self, snapshot, _header=None):
        """One poll's reading; raises (counted by the pump) when unusable."""
        now = self.clock()
        observed = snapshot.get("observed_at") if isinstance(snapshot, dict) else None
        states = snapshot.get("states") if isinstance(snapshot, dict) else None
        if not _finite(observed) or not now - self.ttl <= observed <= now or not isinstance(states, dict):
            raise ValueError("occupancy_snapshot_invalid")
        value = states.get(self.entity)
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128):
            raise ValueError("occupancy_snapshot_invalid")
        known = value not in (None, "unknown", "unavailable")
        reading = {"observed_at": observed, "valid_until": observed + self.ttl,
                   "state": value if known else None, "status": "known" if known else "unknown"}
        with self.lock:
            if self.latest is not None and observed < self.latest["observed_at"]:
                raise ValueError("occupancy_snapshot_out_of_order")
            before = self._raw(self.latest, now)
            self.latest = reading
            if known and value == "on" and (self.last_on is None or observed > self.last_on):
                self.last_on = observed
            after = self._raw(reading, now)
            edge = None
            if after == "on" and before != "on":
                edge = "on"
            elif after == "off" and before == "on":
                edge = "off"
            if edge is None:
                return
            self.edges[edge] += 1
            self.last_edge = {"to": edge, "from": before, "observed_at": observed}
            wake = list(self.subscribers) if edge == "on" else []
        # Edge evidence: one log line per transition, never per poll. WARNING
        # because the controller configures no logging: only WARNING and above
        # reach its journal (as controller.py's own operational lines).
        _log.warning("occupancy edge: entity=%s from=%s to=%s observed_at=%.3f", self.entity, before, edge, observed)
        for event in wake:
            event.set()

    def _raw(self, latest, now):
        """The entity's own on/off/unknown at now, without the hold."""
        if latest is None or latest["observed_at"] > now or now > latest["valid_until"] + self.grace:
            return "unknown"
        if latest["state"] in ("on", "off"):
            return latest["state"]
        return "unknown"

    # --- the gate ------------------------------------------------------
    def _evaluate(self, now):
        """Gate reading at now (caller holds the lock)."""
        if self.last_on is not None and self.last_on > now:
            self.last_on = None  # a clock step back: never hold on a reading from the future
        latest = self.latest
        if self.pump.stopped.is_set():
            return {"state": UNKNOWN, "reason": "occupancy_reader_stopped", "observed_at": None}
        if latest is None or latest["observed_at"] > now:
            return {"state": UNKNOWN, "reason": "occupancy_unavailable", "observed_at": None}
        observed = latest["observed_at"]
        if now > latest["valid_until"] + self.grace:
            return {"state": UNKNOWN, "reason": "occupancy_stale", "observed_at": observed}
        if latest["state"] == "on":
            return {"state": OCCUPIED, "reason": "person_detected", "observed_at": observed}
        if latest["state"] == "off":
            if self.last_on is not None and now - self.last_on <= self.hold:
                return {"state": OCCUPIED, "reason": "occupancy_hold", "observed_at": observed}
            return {"state": VACANT, "reason": "no_person", "observed_at": observed}
        return {"state": UNKNOWN, "reason": "occupancy_state_unknown", "observed_at": observed}

    def reading(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            return self._evaluate(now)

    __call__ = reading

    def state(self, now=None):
        """'on' (occupied, the hold included), 'off' (vacant) or 'unknown'."""
        return RAW[self.reading(now)["state"]]

    def snapshot(self):
        value = self.reading()
        with self.lock:
            edges, last = dict(self.edges), dict(self.last_edge) if self.last_edge else None
        return {"entity": self.entity, "state": value["state"], "reason": value["reason"],
                "edges": edges, "last_edge": last, "poll": self.pump.counters()}
