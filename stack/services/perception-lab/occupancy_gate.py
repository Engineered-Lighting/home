"""Owner-required occupancy gate for the Lab's continuous model pumps.

Qwen and V-JEPA run only while the camera-derived Frigate person-occupancy
sensor says someone is there: that sensor is cheap and the model stack is not.
The gate reads the recording's own 'ha.observation' ledger rows (the HA pump
already ingests them about every second); it never calls HA, never writes a
row, never adds evidence and never refreshes any freshness.

A reading is usable only when it is causally available now (available_at and
observed_at <= now), belongs to this recording's site and camera, carries the
bound entity with its camera-derived origin, and is fresh (now <= valid_until
+ grace). 'on' is occupied, 'off' is vacant, anything else (missing, unknown,
unavailable, stale, unreadable) is unknown. The gate is fail-closed: only
'occupied' lets a pump send.

Hysteresis: a fresh 'off' within hold_seconds of the newest causal 'on'
reading still counts as occupied, so a person sitting still (occupancy briefly
flickering off) does not stall readings. The hold never bridges an unknown or
stale reading.

Cost: one indexed (session_id, kind, id) walk of at most SCAN_ROWS rows per
call, extracting a few scalar fields in SQLite; no payload is parsed in Python
and no whole-session scan is possible.

When the live config provisions an 'occupancy_reader' block, the pumps get
occupancy_reader.OccupancyReader instead (the same rules over a 2 Hz in-memory
reading); it also wakes them at an off/unknown -> on edge (subscribe/wait).
It is used only for the HA source's camera-derived occupancy entity
(reader_entity) and never with a pump-level hold or grace other than its own.
A reader whose entity is not the bound one is passed over (one log line) for
this ledger gate, as without the block, so adding a reader never ungates a
pump or the JEV reasoning cadence.
"""
import math
import re
import time

OCCUPIED, VACANT, UNKNOWN = "occupied", "vacant", "unknown"
STATES = (OCCUPIED, VACANT, UNKNOWN)
# The pumps' typed skip reasons.
SKIP_REASONS = {VACANT: "no_person", UNKNOWN: "occupancy_unknown"}
NOT_PROVISIONED = "occupancy_gate_not_provisioned"
# The only fact a JEV binding may map a camera-derived occupancy entity to.
OCCUPANCY_FACT = "person_present"
ENTITY = re.compile(r"binary_sensor\.[a-z0-9_]{1,100}")
CAMERA_ORIGIN = "same_camera_visual"
# Newest ha.observation rows read per call. At ~1 row/s per HA source this
# covers the default hold even with a second (presence) source interleaved.
SCAN_ROWS = 24
# Optional provisioning keys (on the 'qwen_live' block, or a V-JEPA factory's).
CONFIG_KEYS = ("occupancy_entity", "occupancy_hold_seconds", "occupancy_grace_seconds")
# Each key's gate setting name.
SETTING_NAMES = {"occupancy_entity": "entity", "occupancy_hold_seconds": "hold_seconds",
                 "occupancy_grace_seconds": "grace_seconds"}
DEFAULTS = {"entity": None, "hold_seconds": 10.0, "grace_seconds": 5.0}
BOUNDS = {"hold_seconds": (0, 30), "grace_seconds": (0, 10)}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def gate_config(value=None):
    """Exact optional occupancy keys with conservative bounds -> gate settings."""
    value = dict(value or {})
    if set(value) - set(CONFIG_KEYS):
        raise ValueError("unknown occupancy gate setting")
    config = {"entity": value.get("occupancy_entity", DEFAULTS["entity"]),
              "hold_seconds": value.get("occupancy_hold_seconds", DEFAULTS["hold_seconds"]),
              "grace_seconds": value.get("occupancy_grace_seconds", DEFAULTS["grace_seconds"])}
    for key, (low, high) in BOUNDS.items():
        if not _finite(config[key]) or not low <= config[key] <= high:
            raise ValueError(f"occupancy {key} outside its bound")
    if config["entity"] is not None and (not isinstance(config["entity"], str) or not ENTITY.fullmatch(config["entity"])):
        raise ValueError("exact binary_sensor occupancy entity required")
    return config


def explicit_settings(block):
    """The occupancy settings a pump block names itself, as gate setting names
    ({'entity'?, 'hold_seconds'?, 'grace_seconds'?}), validated like
    gate_config."""
    named = {k: block[k] for k in CONFIG_KEYS if k in (block or {})}
    gate_config(named)
    return {SETTING_NAMES[k]: v for k, v in named.items()}


def reader_mismatch(reader, explicit):
    """The first setting a pump block names itself that differs from the
    occupancy reader's (reader: {'entity','hold_seconds','grace_seconds'}),
    else None. With a reader, its entity, hold and grace apply to every live
    pump, so a pump-level value may only repeat them: a stricter pump hold or
    grace is never silently widened."""
    for name in ("entity", "hold_seconds", "grace_seconds"):
        if name in (explicit or {}) and explicit[name] != reader[name]:
            return name
    return None


def resolve_entity(camera_derived, explicit=None, bindings=None):
    """The bound camera-derived occupancy entity, or None when not provisioned.

    An explicit entity must itself be camera-derived. Otherwise the one
    camera-derived binary sensor bound to person_present wins, else the only
    camera-derived binary sensor; anything ambiguous is not provisioned."""
    candidates = sorted(e for e in (camera_derived or ()) if isinstance(e, str) and ENTITY.fullmatch(e))
    if explicit is not None:
        return explicit if explicit in candidates else None
    bindings = bindings if isinstance(bindings, dict) else {}
    bound = [e for e in candidates if bindings.get(e) == OCCUPANCY_FACT]
    if len(bound) == 1:
        return bound[0]
    return candidates[0] if len(candidates) == 1 else None


def reader_entity(camera_derived, entity, explicit=None, bindings=None):
    """The in-memory reader's entity when it is the occupancy entity this gate
    would bind, else None (the reader does not apply; the runtime falls back
    to the ledger gate). The reader names its entity itself, so, like an
    explicit occupancy_entity, it must be camera-derived; a pump naming
    another entity, or a person_present binding (or a single camera-derived
    entity) that resolves to another one, does not use it. No camera-derived
    entity at all: None."""
    if not isinstance(entity, str) or (explicit is not None and explicit != entity):
        return None
    if resolve_entity(camera_derived, entity) != entity:
        return None
    resolved = resolve_entity(camera_derived, None, bindings)
    return entity if resolved in (None, entity) else None


def readings(store, session, entity, now, *, scan_rows=SCAN_ROWS):
    """(latest, newest_on_observed_at) from one bounded indexed query.

    latest: the newest usable row's {'event_id','observed_at','valid_until',
    'state','status'}, or None; newest_on_observed_at: observed_at of the
    newest usable row whose state is 'on', or None. Rows available after now
    (a future row) are ignored, as are rows without the bound entity (another
    HA source) or without its camera-derived origin."""
    if not isinstance(entity, str) or not ENTITY.fullmatch(entity):
        raise ValueError("exact binary_sensor occupancy entity required")
    base = f'$.ha."{entity}"'
    with store.lock:
        rows = store.db.execute("""SELECT id,json_extract(payload,'$.available_at'),json_extract(payload,'$.observed_at'),
            json_extract(payload,'$.valid_until'),json_extract(payload,'$.site_id'),json_extract(payload,'$.camera_id'),
            json_extract(payload,?),json_extract(payload,?),json_extract(payload,?)
            FROM events INDEXED BY events_session_kind_id WHERE session_id=? AND kind='ha.observation'
            ORDER BY id DESC LIMIT ?""", (base + ".state", base + ".status", base + ".origin",
                                          session["id"], scan_rows)).fetchall()
    latest, newest_on = None, None
    for row in rows:
        event_id, available, observed, valid_until, site, camera, state, status, origin = tuple(row)
        if (not _finite(available) or not _finite(observed) or not _finite(valid_until)
                or available > now or observed > now or site != session["site_id"] or camera != session["camera_id"]
                or status not in ("known", "unknown") or origin != CAMERA_ORIGIN):
            continue
        if latest is None:
            latest = {"event_id": event_id, "observed_at": observed, "valid_until": valid_until,
                      "state": state if status == "known" else None, "status": status}
        if status == "known" and state == "on" and (newest_on is None or observed > newest_on):
            newest_on = observed
    return latest, newest_on


class OccupancyGate:
    """occupied() for one pump of one recording: {'state','reason','observed_at'}."""

    def __init__(self, store, session, entity, *, hold_seconds=DEFAULTS["hold_seconds"],
                 grace_seconds=DEFAULTS["grace_seconds"], clock=None, scan_rows=SCAN_ROWS):
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity):
            raise ValueError(NOT_PROVISIONED)
        settings = gate_config({"occupancy_hold_seconds": hold_seconds, "occupancy_grace_seconds": grace_seconds})
        if type(scan_rows) is not int or not 1 <= scan_rows <= 64:
            raise ValueError("bounded occupancy scan required")
        self.store, self.session, self.entity = store, {k: session[k] for k in ("id", "site_id", "camera_id")}, entity
        self.hold, self.grace = settings["hold_seconds"], settings["grace_seconds"]
        self.clock = clock or getattr(store, "clock", time.time)
        self.scan_rows = scan_rows
        self.last_on = None  # observed_at of the newest causal 'on' reading seen

    def __call__(self):
        now = self.clock()
        try:
            latest, newest_on = readings(self.store, self.session, self.entity, now, scan_rows=self.scan_rows)
        except Exception:
            return {"state": UNKNOWN, "reason": "occupancy_read_failed", "observed_at": None}
        if self.last_on is not None and self.last_on > now:
            self.last_on = None  # a clock step back: never hold on a reading from the future
        if newest_on is not None and (self.last_on is None or newest_on > self.last_on):
            self.last_on = newest_on
        if latest is None:
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


def check(gate):
    """A pump's view of one gate call; never raises. Anything but a well-formed
    reading is unknown (fail-closed). Returns (skip_reason or None, reading)."""
    try:
        value = gate()
    except Exception:
        value = None
    if not isinstance(value, dict) or value.get("state") not in STATES:
        value = {"state": UNKNOWN, "reason": "occupancy_unavailable"}
    reason = value.get("reason")
    reading = {"state": value["state"],
               "reason": reason if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) else value["state"]}
    return SKIP_REASONS.get(reading["state"]), reading


def required(occupied):
    """The pumps refuse to exist without a gate: never silently ungated."""
    if not callable(occupied):
        raise ValueError("occupancy gate not provisioned")
    return occupied


def subscribe(occupied, wake):
    """Attach a pump's wake event to a gate that reports occupancy edges
    (occupancy_reader.OccupancyReader); a ledger gate has none. Never raises."""
    try:
        hook = getattr(occupied, "subscribe", None)
        if callable(hook):
            hook(wake)
    except Exception:
        pass


def unsubscribe(occupied, wake):
    try:
        hook = getattr(occupied, "unsubscribe", None)
        if callable(hook):
            hook(wake)
    except Exception:
        pass


def wait(stopped, wake, delay, woken=lambda: True):
    """The pumps' sleep: True once stopped, else False after delay, or earlier
    at an occupancy edge (wake set) while woken() says the pump is skipping
    for occupancy. A wake is cleared when seen; any other wake keeps waiting
    for the rest of the delay, so an edge never shortens a send interval."""
    deadline = time.monotonic() + max(0.0, delay)
    while not stopped.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if wake.wait(remaining):
            wake.clear()
            if not stopped.is_set() and woken():
                return False
    return True


def occupancy_skip(reason):
    """True for no previous tick or a tick skipped for vacancy/unknown occupancy."""
    return reason is None or reason in SKIP_REASONS.values()
