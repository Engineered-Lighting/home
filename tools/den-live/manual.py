"""The owner's own changes to a den fixture, as Home reports them (plan 04b): the request's shape, the parts of a fixture
it touches, and the durable log the coordinator learns from.

Home sends one `den-manual-1` event per settled change (the end of a drag, a slider's release, a toggle), never per
pointer move, with `before` and `after` in Home's own fixture-state shapes and only the keys that changed. A retry
reuses its event_id, so the log can tell it from a new change. The log is the source of truth: what was learned is
replayed from it on start, and Clear sets it aside whole.

Standard library only. No clock, no network.
"""

import json
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
    if not isinstance(value, dict) or not value or set(value) - LIGHT_KEYS:
        raise ValueError(f"{where} must be {{on, brightness, color_temp_kelvin}}")
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
        if key in value:
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
        if not isinstance(radials, list) or not radials or len(radials) > 32:
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
    allowed = {"schema", "event_id", "fixture_id", "before", "after", "at_ms", "snapshot", "subject", "situation"}
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
    before, after = _state(data.get("before"), "before"), _state(data.get("after"), "after")
    if set(before) != set(after):
        raise ValueError("before and after must name the same keys")
    for key in ("subject", "situation"):
        if data.get(key) is not None and not isinstance(data[key], (dict, str)):
            raise ValueError(f"{key} must be what Home showed, or absent")
    return {"schema": SCHEMA, "event_id": data["event_id"], "fixture_id": data["fixture_id"], "before": before,
            "after": after, "at_ms": data["at_ms"], "snapshot": {"epoch": snapshot["epoch"], "seq": snapshot["seq"]},
            "subject": data.get("subject"), "situation": data.get("situation")}


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

    def append(self, line):
        with self.path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(line, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def read(self):
        """Every whole line, in order. A torn last line (a crash mid-write, before the reply) is skipped."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        lines = []
        for raw in text.splitlines():
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if isinstance(line, dict) and isinstance(line.get("event"), dict):
                lines.append(line)
        return lines

    def archive(self, stamp):
        """Clear: keep the old log as manual-events-<stamp>.jsonl, and start empty."""
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"manual-events-{stamp}.jsonl"))
