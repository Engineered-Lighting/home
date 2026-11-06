"""den-event-1: the one line format every den-core input and output travels in.

See README.md (SHARED INTERFACE). Standard library only; no clock, no network.
"""
import hashlib
import json
import math
import traceback
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
# What the runner stamps on a decision. A shadow run decides beside what the bulbs really did and sends nothing, as
# a replay does; rung 9's ladder entry runs one after the replays. 'shown' is rung 11's and no den-core run sets it.
ACTUATIONS = ("synthetic", "replay", "shadow")
HEADER_KEYS = ("schema", "sources", "policies", "tick_s", "tail_s", "plant", "shown", "synthetic", "assume_occupied", "actuation")
HEADER_TYPES = {"sources": list, "plant": bool, "synthetic": bool, "assume_occupied": bool, "shown": str, "actuation": str}
POLICY_TEXT = ("id", "module", "version", "code_sha256", "config_sha256")
# What a value of the wrong shape raises in code that reads it as the right one, where a line's own check can't see that far.
SHAPE_ERRORS = (LookupError, TypeError, AttributeError, ArithmeticError, StopIteration, RecursionError)
MEDIA_KEYS = frozenset({"image", "images", "jpeg", "jpg", "png", "b64", "base64", "pixels", "bytes"})
MAX_LINE_BYTES = 16384
MAX_FRAME_ID = 64  # a LightState's own id, as it goes on a Decision's line; coordinator.LIGHT_STATE_ROOM holds room for it


class BadFile(ValueError):
    """A file given to a tool that cannot be read at all. A tool exits 2 with this sentence and nothing else."""


def is_number(value):
    """True for a JSON number a float can hold. json.loads also reads NaN, Infinity and a 401-digit integer, which
    math.isfinite answers with OverflowError, and a bool is not a number here."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def named(error):
    """An exception in words, for a refusal: its type says more than its text for a KeyError or a TypeError."""
    return "%s: %s" % (type(error).__name__, " ".join(str(error).split()))


def stopped(tool, error):
    """A tool's last resort, for what no check before it named: one sentence, and where, so a bug of its own can still be found."""
    frames = traceback.extract_tb(error.__traceback__)
    where = " (%s line %d)" % (Path(frames[-1].filename).name, frames[-1].lineno) if frames else ""
    return "%s stopped on an input of the wrong shape, or a bug of its own: %s%s" % (tool, named(error), where)


def _utf16(head, path):
    """PowerShell 5.1's `>` writes a byte-order mark, UTF-16LE and CRLF. A NUL this early is the same text with no mark."""
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\0" in head[:4]:
        raise BadFile("%s is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8" % path)


def refuse_utf16(path):
    """BadFile for a UTF-16 file. One that can't be opened, a folder of day files for one, is for its reader to say."""
    try:
        with open(path, "rb") as handle:
            _utf16(handle.read(4), path)
    except OSError:
        pass


def read_raw(path):
    """A file's bytes, without the UTF-8 byte-order mark PowerShell 5.1's Out-File -Encoding utf8 writes. UTF-16 is refused."""
    raw = Path(path).read_bytes()
    _utf16(raw[:4], path)
    return raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw


def read_text(path, errors="strict"):
    """A file's text, as read_raw. Under `errors`, strict refuses a file that is not UTF-8."""
    try:
        return read_raw(path).decode("utf-8", errors)
    except UnicodeDecodeError:
        raise BadFile("%s is not UTF-8 (PowerShell 5.1's Set-Content writes ANSI): save it as UTF-8" % path) from None


def read_json(path):
    text = read_text(path)
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as error:  # nested deeper than a parser goes
        raise BadFile("%s is not JSON: %s" % (path, error)) from None


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
    if not is_number(t):
        raise ValueError("t must be finite")
    if type(seq) is not int:
        raise ValueError("seq must be an int")
    kind, data = event["kind"], event["data"]
    if not isinstance(event["source"], str):
        raise ValueError("source must be text")
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


def log_lines(path):
    """A log's lines. A byte-order mark and CRLF are how PowerShell 5.1 copies a file, and say nothing about the log."""
    lines = read_text(path).split("\n")
    if lines[-1] != "":
        raise ValueError("%s does not end with a newline" % path)
    return [line[:-1] if line.endswith("\r") else line for line in lines[:-1]]


def _header_problem(header):
    """What is wrong with a header's values, or None. Each is read as this shape before any replay could say so."""
    for key, kind in HEADER_TYPES.items():
        if not isinstance(header[key], kind):
            return "%s must be %s" % (key, {list: "a list", bool: "true or false", str: "text"}[kind])
    if not isinstance(header["policies"], list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("config"), dict) and all(isinstance(entry.get(key), str) for key in POLICY_TEXT)
            for entry in header["policies"]):
        return "policies must be a list, each with %s as text and its config an object" % ", ".join(POLICY_TEXT)
    if not is_number(header["tick_s"]) or header["tick_s"] <= 0:
        return "tick_s must be a number above 0"
    if not is_number(header["tail_s"]) or header["tail_s"] < 0:
        return "tail_s must be a number, 0 or more"
    return None


def read_run(path):
    """read, for a tool that goes on to use the header: one that lacks a run's keys or holds them in the wrong shape is refused
    here, not as a KeyError or a TypeError there."""
    header, events = read(path)
    missing = [key for key in HEADER_KEYS if key not in header]
    if missing:
        raise ValueError("%s: its header lacks %s" % (path, ", ".join(missing)))
    problem = _header_problem(header)
    if problem:
        raise ValueError("%s: its header is the wrong shape: %s" % (path, problem))
    return header, events


def read(path):
    """(header, events) from a den-replay-log-1 file; every line is checked."""
    header, events = None, []
    for number, line in enumerate(log_lines(path), 1):
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
        except (ValueError, RecursionError) as error:
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
