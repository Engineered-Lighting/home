"""The owner's own changes to a den fixture, as Home reports them (plan 04b): the request's shape, the parts of a fixture
it touches, and the durable log the coordinator learns from.

Home sends one `den-manual-1` event per settled change (the end of a drag, a slider's release, a toggle), never per
pointer move, with `before` and `after` in Home's own fixture-state shapes and only the keys that changed. A retry
reuses its event_id, so the log can tell it from a new change. The log is the source of truth: what was learned is
replayed from it on start, and Clear sets it aside whole.

Standard library only. No clock, no network.
"""

import json
import hashlib
import math
import os
import re
from pathlib import Path

SCHEMA = "den-manual-1"
MAX_BYTES = 8192
EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,80}$")
FIXTURE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
LIGHT_KEYS = {"on", "brightness", "color_temp_kelvin"}
STATE_KEYS = {"pan_deg", "tilt_deg", "target", "spotlight", "downward", "radials"}
# What each part of a fixture is: the spot (its aim and its light), its glow zones, and its downlight.
PARTS = {"pan_deg": "spot", "tilt_deg": "spot", "target": "spot", "spotlight": "spot", "radials": "radials",
         "downward": "downward"}
KELVIN = (1000, 20000)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _light(value, where):
    """A light channel. Home sends null for what it doesn't know (a channel, or one of its values): that's absent."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - LIGHT_KEYS:
        raise ValueError(f"{where} must be {{on, brightness, color_temp_kelvin}}")
    value = {key: item for key, item in value.items() if item is not None}
    if "on" in value and type(value["on"]) is not bool:
        raise ValueError(f"{where}.on must be true or false")
    if "brightness" in value and not (_number(value["brightness"]) and 0 <= value["brightness"] <= 1):
        raise ValueError(f"{where}.brightness must be 0-1")
    if "color_temp_kelvin" in value and not (_number(value["color_temp_kelvin"])
                                             and KELVIN[0] <= value["color_temp_kelvin"] <= KELVIN[1]):
        raise ValueError(f"{where}.color_temp_kelvin must be 1000-20000")
    return dict(value)


def _point(value, where):
    if not (isinstance(value, (list, tuple)) and len(value) == 3 and all(map(_number, value))):
        raise ValueError(f"{where} must be three finite metres")
    return [float(x) for x in value]


def _state(value, where):
    if not isinstance(value, dict) or not value or set(value) - STATE_KEYS:
        raise ValueError(f"{where} must hold only {sorted(STATE_KEYS)}")
    out = {}
    for key in ("pan_deg", "tilt_deg"):
        if value.get(key) is not None:
            if not _number(value[key]):
                raise ValueError(f"{where}.{key} must be finite degrees")
            out[key] = float(value[key])
    if "target" in value:
        target = value["target"]
        if target is not None:
            if not isinstance(target, dict) or "point" not in target:
                raise ValueError(f"{where}.target must be {{point, normal, surface_id, kind}} or null")
            target = {"point": _point(target["point"], f"{where}.target.point"),
                      **{key: target[key] for key in ("normal", "surface_id", "kind") if key in target}}
        out["target"] = target
    for key in ("spotlight", "downward"):
        if key in value:
            out[key] = _light(value[key], f"{where}.{key}")
    if "radials" in value:
        radials = value["radials"]
        # Empty is allowed: Home's `before` lists only the changed zones it had a state for.
        if not isinstance(radials, list) or len(radials) > 64:
            raise ValueError(f"{where}.radials must be a list of zones")
        zones = []
        for zone in radials:
            if not isinstance(zone, dict) or type(zone.get("number")) is not int:
                raise ValueError(f"{where}.radials needs each zone's number")
            zones.append({"number": zone["number"], **_light({k: v for k, v in zone.items() if k != "number"},
                                                              f"{where}.radials[{zone['number']}]")})
        out["radials"] = zones
    return out


def parse(data):
    """The event, checked and normalised, or ValueError(why). Unknown top-level keys are refused."""
    if not isinstance(data, dict):
        raise ValueError("expected a den-manual-1 object")
    if len(json.dumps(data, allow_nan=False, separators=(",", ":"))) > MAX_BYTES:
        raise ValueError("larger than 8 KB")
    ordering = {"client_id", "client_seq", "hold_id", "expected_revision"}
    allowed = {"schema", "event_id", "fixture_id", "before", "after", "at_ms", "snapshot", "subject", "situation",
               "held_state", *ordering}
    if set(data) - allowed:
        raise ValueError(f"unknown keys {sorted(set(data) - allowed)}")
    if data.get("schema") != SCHEMA:
        raise ValueError("schema must be den-manual-1")
    for key, pattern in (("event_id", EVENT_ID), ("fixture_id", FIXTURE_ID)):
        if not isinstance(data.get(key), str) or not pattern.match(data[key]):
            raise ValueError(f"{key} is missing or malformed")
    if type(data.get("at_ms")) is not int or data["at_ms"] < 0:
        raise ValueError("at_ms must be Home's wall clock in milliseconds")
    snapshot = data.get("snapshot")
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get("epoch"), str)
            or type(snapshot.get("seq")) is not int or snapshot["seq"] < 0):
        raise ValueError("snapshot must be {epoch, seq} of the den-virtual-spots-1 snapshot Home was drawing")
    if not isinstance(data.get("before"), dict) or not isinstance(data.get("after"), dict) or (
            set(data["before"]) != set(data["after"])):
        raise ValueError("before and after must name the same keys")
    before, after = _state(data["before"], "before"), _state(data["after"], "after")
    for key in ("subject", "situation"):
        if data.get(key) is not None and not isinstance(data[key], (dict, str)):
            raise ValueError(f"{key} must be what Home showed, or absent")
    extra = {}
    if ordering & set(data):
        if not ordering <= set(data):
            raise ValueError("manual ordering fields must appear together")
        for key in ("client_id", "hold_id"):
            if not isinstance(data[key], str) or not EVENT_ID.fullmatch(data[key]):
                raise ValueError(f"{key} is malformed")
        for key, low in (("client_seq", 1), ("expected_revision", 0)):
            if type(data[key]) is not int or not low <= data[key] <= 9007199254740991:
                raise ValueError(f"{key} must be an integer >= {low}")
        extra = {key: data[key] for key in ordering}
    if "held_state" in data:
        extra["held_state"] = _state(data["held_state"], "held_state")
    return {"schema": SCHEMA, "event_id": data["event_id"], "fixture_id": data["fixture_id"], "before": before,
            "after": after, "at_ms": data["at_ms"], "snapshot": {"epoch": snapshot["epoch"], "seq": snapshot["seq"]},
            "subject": data.get("subject"), "situation": data.get("situation"), **extra}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def merge_state(previous, patch):
    """Merge actual hand values independently of whether a correction can be learned."""
    result = json.loads(json.dumps(previous))
    for key, value in patch.items():
        if key == "radials":
            zones = {zone["number"]: zone for zone in result.get(key, [])}
            for zone in value:
                zones[zone["number"]] = {**zones.get(zone["number"], {}), **zone}
            result[key] = list(zones.values())
        elif key in ("spotlight", "downward") and value is not None:
            result[key] = {**(result.get(key) or {}), **value}
        else:
            result[key] = value
    return result


def parts(state):
    """The parts of the fixture this state touches: spot, radials, downward."""
    return sorted({PARTS[key] for key in state})


class Log:
    """`coordinator/manual-events.jsonl`: one line per event Home sent, fsynced before the reply says it's recorded.
    Each line is the event and what den-live made of it (the context and the learning inputs), so replay needs nothing
    else."""

    NAME = "manual-events.jsonl"

    def __init__(self, folder):
        self.path = Path(folder) / self.NAME
        self.skipped = 0   # lines read() couldn't use: torn, corrupt, or not an event
        self.unreadable = False

    def append(self, line):
        """One line, fsynced. A torn tail (a crash or a full disk mid-write) is closed off with a newline first, so the
        fragment never swallows this line; a write that fails is cut back to where the file was, so a retry starts on
        a clean line."""
        data = (json.dumps(line, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        with self.path.open("a+b") as log:
            size = log.seek(0, os.SEEK_END)
            if size:
                log.seek(size - 1)
                if log.read(1) != b"\n":
                    data = b"\n" + data
            try:
                log.write(data)
                log.flush()
                os.fsync(log.fileno())
            except OSError:
                try:
                    log.truncate(size)
                except OSError:
                    pass
                raise

    def read(self):
        """Every whole event line, in order. Torn, corrupt or undecodable lines are skipped and counted, and a log that
        can't be read at all reads as empty: the log never stops the coordinator from starting."""
        self.skipped = 0
        self.unreadable = False
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError:
            self.skipped = 1
            self.unreadable = True
            return []
        lines = []
        for chunk in raw.split(b"\n"):
            if not chunk.strip():
                continue
            try:
                line = json.loads(chunk.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self.skipped += 1
                continue
            if isinstance(line, dict) and (isinstance(line.get("event"), dict) or
                                          line.get("schema") == "den-manual-checkpoint-1"):
                lines.append(line)
            else:
                self.skipped += 1
        return lines

    def reset(self, stamp, checkpoint):
        """Keep the previous journal and atomically replace it with a durable clear checkpoint."""
        archive = self.path.with_name(f"manual-events-{stamp}.jsonl")
        n = 1
        while archive.exists():
            archive = self.path.with_name(f"manual-events-{stamp}-{n}.jsonl")
            n += 1
        if self.path.exists():
            with archive.open("xb") as kept:
                kept.write(self.path.read_bytes())
                kept.flush()
                os.fsync(kept.fileno())
        replacement = self.path.with_suffix(".reset")
        with replacement.open("wb") as target:
            target.write((json.dumps(checkpoint, sort_keys=True, allow_nan=False) + "\n").encode())
            target.flush()
            os.fsync(target.fileno())
        os.replace(replacement, self.path)

    def archive(self, stamp):
        """Clear: keep the old log as manual-events-<stamp>.jsonl, and start empty."""
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"manual-events-{stamp}.jsonl"))
