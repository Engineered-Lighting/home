"""den-event-1: the one line format every den-core input and output travels in.

See README.md (SHARED INTERFACE). Standard library only; no clock, no network.
"""
import hashlib
import json
import math
from pathlib import Path

SCHEMA = "den-event-1"
LOG_SCHEMA = "den-replay-log-1"
ENVELOPE = frozenset({"schema", "t", "seq", "kind", "source", "data"})
LIGHTS = ("light.den_front_left", "light.front_right", "light.den_rear_left", "light.den_rear_right")
ADAPTER_SOURCES = frozenset({"fact_log", "poll_log", "mqtt_timeline", "occupancy_journal", "recorder_commands",
                             "recorder_lights", "correction_capture", "den_lighting_status", "synthetic"})
# la-fact-log-1's 12 keys (la_fact_log.KEYS); sources.fact_log checks the full format.
FACT_KEYS = ("available_at", "engine_epoch", "latency_s", "observation", "observed_at", "outcome", "prompt_version",
             "request_id", "schema", "sent_at", "session_id", "source_id")
REQUIRED = {
    "facts": FACT_KEYS,
    "occupancy": ("value", "via"),
    "light_observed": ("entity_id", "on", "brightness", "kelvin", "context"),
    "correction": ("correction_id", "entity_id", "after", "source"),
    "gaming_sample": ("status", "sampled_at"),
    "tick": (),
    "live_command": ("entity_id", "revision", "target", "context_id"),
    "live_status": ("automatic", "gaming"),
}
INPUT_KINDS = tuple(REQUIRED)
# Logged with the inputs but never handed to a policy, and they never set a run's span or ticks
# (runner.run): live_command is what the live den_lighting sent, and live_status whether its status sensor showed
# it automatic and in a game, references to compare with.
REFERENCE_KINDS = frozenset({"live_command", "live_status"})
DERIVED_KINDS = ("person_state", "mode", "decision")
KINDS = INPUT_KINDS + DERIVED_KINDS
MEDIA_KEYS = frozenset({"image", "images", "jpeg", "jpg", "png", "b64", "base64", "pixels", "bytes"})
MAX_LINE_BYTES = 16384


def dumps(event):
    """The canonical line (no newline) for an event or a log header."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256_json(value):
    return hashlib.sha256(dumps(value).encode("ascii")).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _words_only(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MEDIA_KEYS:
                raise ValueError("media key %r in %s" % (key, where))
            _words_only(item, where)
    elif isinstance(value, list):
        for item in value:
            _words_only(item, where)
    elif isinstance(value, str) and value.startswith("data:"):
        raise ValueError("data: URI in %s" % where)


def validate(event):
    """Raise ValueError unless event is a well-formed den-event-1 dict."""
    if not isinstance(event, dict) or set(event) != ENVELOPE:
        raise ValueError("envelope keys must be exactly %s" % sorted(ENVELOPE))
    if event["schema"] != SCHEMA:
        raise ValueError("schema must be %s" % SCHEMA)
    t, seq = event["t"], event["seq"]
    if type(t) not in (int, float) or not math.isfinite(t):
        raise ValueError("t must be finite")
    if type(seq) is not int:
        raise ValueError("seq must be an int")
    kind, data = event["kind"], event["data"]
    if kind not in KINDS:
        raise ValueError("unknown kind %r" % (kind,))
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    missing = [key for key in REQUIRED.get(kind, ()) if key not in data]
    if missing:
        raise ValueError("%s lacks %s" % (kind, missing))
    _words_only(event, kind)
    if len(dumps(event)) > MAX_LINE_BYTES:
        raise ValueError("%s line over %d bytes" % (kind, MAX_LINE_BYTES))


def inside_checkout(path):
    """True when path or any parent holds a .git file or folder."""
    path = Path(path).resolve()
    return any((p / ".git").exists() for p in (path, *path.parents))


def read(path):
    """(header, events) from a den-replay-log-1 file; every line is checked."""
    lines = Path(path).read_bytes().decode("ascii").split("\n")
    if lines[-1] != "":
        raise ValueError("%s does not end with a newline" % path)
    header, events = None, []
    for number, line in enumerate(lines[:-1], 1):
        try:
            value = json.loads(line)
            if dumps(value) != line:
                raise ValueError("not canonical")
            if number == 1:
                if not isinstance(value, dict) or value.get("schema") != LOG_SCHEMA:
                    raise ValueError("header must be %s" % LOG_SCHEMA)
                header = value
                continue
            validate(value)
            if value["seq"] != len(events):
                raise ValueError("seq %r out of order" % value["seq"])
            events.append(value)
        except ValueError as error:
            raise ValueError("%s line %d: %s" % (path, number, error)) from None
    if header is None:
        raise ValueError("%s is empty" % path)
    return header, events


def write(path, header, events):
    """Write a new log; refuses an existing file and any path inside a git checkout."""
    path = Path(path)
    if inside_checkout(path):
        raise ValueError("refusing to write inside a git checkout: %s" % path)
    text = "".join(dumps(value) + "\n" for value in [header, *events])
    with open(path, "xb") as handle:
        handle.write(text.encode("ascii"))


def scene_matches(target, actual):
    """den_lighting Scene.matches on scene dicts; a null target field matches anything."""
    if target is None or actual is None:
        return target is None and actual is None
    if target["on"] != actual["on"]:
        return False
    if not target["on"]:
        return True
    b, k = target["brightness"], target["kelvin"]
    return ((b is None or actual["brightness"] is not None and abs(b - actual["brightness"]) <= 1) and
            (k is None or actual["kelvin"] is not None and abs(1e6 / k - 1e6 / actual["kelvin"]) <= 2))
