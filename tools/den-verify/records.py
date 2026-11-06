"""Reading and writing the records rung 10 touches, as JSON Lines.

One record per line, UTF-8, LF on write; CRLF or LF on read, with or without the
byte-order mark PowerShell 5.1's Set-Content -Encoding utf8 writes. Field names
come from docs/architecture/live-v1/schemas.json, and `schema_keys()` reads them
from there so a test can check what the tool writes against the contract.

What a file holds is checked here, against what the tool reads of it, so a file
it cannot read is one sentence naming the file and the line (`BadFile`), not a
TypeError somewhere in the scorer. A field it does not read is not looked at.
"""
import json
import math
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "docs" / "architecture" / "live-v1" / "schemas.json"
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$")
DAY = 86400.0
LATEST_S = 32503680000.0  # the year 3000, as far as Windows' clock goes; before 1970 it stops too
FRAME_APART_S = 0.0005  # how near inputs.light_state_ts must be to a frame's ts to name it
DEN_LOG_SCHEMAS = ("den-event-1", "den-replay-log-1")


class BadFile(ValueError):
    """A file given to the tool that it cannot read. verify.py exits 2 with this sentence and nothing else."""


def read_text(path):
    """A file's text, without the byte-order mark Set-Content -Encoding utf8 writes. UTF-16 is refused, and so is the rest."""
    try:
        raw = Path(path).read_bytes()
    except (OSError, ValueError) as e:
        raise BadFile(f"{path} cannot be read: {getattr(e, 'strerror', None) or e}") from None
    # PowerShell 5.1's `>` writes a byte-order mark, UTF-16LE and CRLF. A NUL this early is the same text with no mark.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\0" in raw[:4]:
        raise BadFile(f"{path} is UTF-16 (PowerShell 5.1's > writes that): save it as UTF-8")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BadFile(f"{path} is not UTF-8 (PowerShell 5.1's Set-Content writes ANSI): save it as UTF-8") from None


def _no_constant(name):
    raise ValueError(f"{name} is not a number JSON has")


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is a number no float holds")
    return value


def _int(text):
    if len(text) > 308:
        raise ValueError(f"a number of {len(text)} digits is one no float holds")
    return int(text)


def loads(text):
    """json.loads, refusing NaN and Infinity, which JSON has no form for, and numbers no float holds."""
    return json.loads(text, parse_constant=_no_constant, parse_float=_float, parse_int=_int)


def read_json(path):
    text = read_text(path)
    try:
        return loads(text)
    except (ValueError, RecursionError) as e:  # RecursionError: nested deeper than a parser goes
        raise BadFile(f"{path} is not JSON: {e}") from None


def _numbered(path, problem):
    """[(line number, record)] of a JSON Lines file. `problem(row)` says what is wrong with a record, or None."""
    out = []
    for n, line in enumerate(read_text(path).replace("\r\n", "\n").split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = loads(line)
        except (ValueError, RecursionError) as e:
            raise BadFile(f"{path} line {n} is not JSON: {e}") from None
        if not isinstance(row, dict):
            raise BadFile(f"{path} line {n} is not a JSON object")
        wrong = problem(row) if problem else None
        if wrong:
            raise BadFile(f"{path} line {n} {wrong}")
        out.append((n, row))
    return out


def read_jsonl(path, problem=None):
    return [] if path is None else [row for _, row in _numbered(path, problem)]


def read_frames(path):
    """The LightState records. Two at one time could not be told apart when a Decision names one, so they are refused."""
    rows = _numbered(path, frame_problem)
    order = sorted((float(row["ts"]), n) for n, row in rows)
    for (a, line_a), (b, line_b) in zip(order, order[1:]):
        if b - a < FRAME_APART_S:
            raise BadFile(f"{path} lines {min(line_a, line_b)} and {max(line_a, line_b)} are both at ts {a!r}: "
                          "two frames at one time can't be told apart")
    return [row for _, row in rows]


# -- what the tool reads of each record ------------------------------------------
#
# A shape is a (words, test) leaf; [shape], a list of it; {key: shape}, an object where those keys may be absent or
# null, or must be there when the key starts with "!"; {"*": shape}, an object of any keys, each value that shape, or
# null as well where the key is "*?".

def is_number(value):
    """A JSON number a float can hold. A bool is not a number here, and neither is NaN or a 401-digit integer."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_time(value):
    return is_number(value) and 0 <= value <= LATEST_S


def _is_time_or_clock(value):
    return _is_time(value) or (isinstance(value, str) and CLOCK.match(value.strip()) is not None)


def _is_xy(value):
    return isinstance(value, list) and len(value) >= 2 and all(map(is_number, value[:2]))


def _is_target(value):
    return not isinstance(value, dict) or value.get("surface_id") is None or isinstance(value["surface_id"], str)


def _is_surface_values(value):
    return isinstance(value, dict) and all(v is None or is_number(v) or k == "unit" for k, v in value.items())


TEXT = ("text", lambda v: isinstance(v, str))
NAME = ("text, not empty", lambda v: isinstance(v, str) and bool(v))
FLAG = ("true or false", lambda v: isinstance(v, bool))
NUMBER = ("a number", is_number)
COUNT = ("a whole number, 0 or more", lambda v: type(v) is int and v >= 0)
TS = ("a time in seconds between 1970 and the year 3000", _is_time)
TIME = ("a time in seconds between 1970 and the year 3000, or a clock string", _is_time_or_clock)
ANY_LIST = ("a list", lambda v: isinstance(v, list))
XY = ("[x, y] in numbers", _is_xy)

SCENE = {"on": FLAG, "level": NUMBER, "cct_k": NUMBER}
PIN = {"on": FLAG, "level_pct": NUMBER, "cct_k": NUMBER}
STEP = {"kind": TEXT, "sent_at": TIME, "transition_s": NUMBER, "result": {"at": TIME}}
RECIPIENT = {"served": [TEXT], "exposed": [TEXT], "targets": [{"kind": TEXT, "target_id": TEXT, "track_id": TEXT}]}
DECISION = {
    "!decision_id": NAME, "!ts": TIME,
    "inputs": {"light_state": TEXT, "light_state_ts": TS, "person_state": ANY_LIST, "actuator_state": {"*?": SCENE},
               "hold_set": {"revision": NUMBER, "held": [{"entity_id": TEXT, "channels": [TEXT], "pinned": PIN}]}},
    "targets": {"*?": ("an object with its surface_id as text", _is_target)},
    "applied_scene": {"*?": {"status": TEXT, "readback": SCENE, "steps": [STEP]}},
    "recipients": {"*?": RECIPIENT},
    "predicted": {"surfaces": ("{surface_id: number or null, unit: text}", _is_surface_values),
                  "eyes": [{"track_id": TEXT}], "surfaces_not_logged": COUNT},
}
FRAME = {
    "!ts": TS, "id": NAME,
    "surface_estimates": [{"!surface_id": TEXT, "value": NUMBER, "clipped_frac": NUMBER, "basis": TEXT}],
    "window_daylight": [{"!window_id": TEXT, "level": NUMBER}],
}
CORRECTION = {
    "!ts": TIME, "action": {"settle_s": NUMBER},
    "snapshot": {"light_state": [TEXT], "person_state": ANY_LIST},
    "hold": {"entity_id": TEXT, "channels": [TEXT], "pinned": PIN, "since": TIME, "ended": TIME, "hold_set_revision": NUMBER},
}
OCCUPANCY = {"!start": TS, "!end": TS, "people": [{"xy": XY}]}
GOLD = {"!start": TS, "!end": TS, "R_eval": {"surface_id": TEXT, "placement_sd": NUMBER}}


def _wrong(value, shape, where):
    """What is wrong with a value that is there, or None."""
    if isinstance(shape, tuple):
        return None if shape[1](value) else f"{where} must be {shape[0]}, not {repr(value)[:40]}"
    if isinstance(shape, list):
        if not isinstance(value, list):
            return f"{where} must be a list, not {repr(value)[:40]}"
        for n, item in enumerate(value):
            if item is None:
                return f"{where}[{n}] must not be null"
            wrong = _wrong(item, shape[0], f"{where}[{n}]")
            if wrong:
                return wrong
        return None
    if not isinstance(value, dict):
        return f"{where} must be an object, not {repr(value)[:40]}"
    return _wrong_keys(value, shape, where + ".")


def _wrong_keys(value, shape, where=""):
    each = shape.get("*?", shape.get("*"))
    if each is not None:
        for key, item in value.items():
            if item is None and "*?" not in shape:
                return f"{where}{key} must not be null"
            wrong = None if item is None else _wrong(item, each, f"{where}{key}")
            if wrong:
                return wrong
        return None
    for key, inner in shape.items():
        must = key.startswith("!")
        key = key.lstrip("!")
        if value.get(key) is None:
            if must:
                return f"has no {where}{key}"
            continue
        wrong = _wrong(value[key], inner, f"{where}{key}")
        if wrong:
            return wrong
    return None


def _in_words(wrong):
    return wrong if wrong is None or wrong.startswith("has no ") else f"is wrong: {wrong}"


def decision_problem(row):
    """What is wrong with a line of --decisions, or None. Each of these is for read_jsonl to name the file and line."""
    if row.get("schema") in DEN_LOG_SCHEMAS:
        return (f"is a line of den-core's log ({row['schema']}; its lines are den-event-1 envelopes), not a Decision: "
                "pass decisions.jsonl instead, which run.py writes beside log.jsonl")
    return _in_words(_wrong_keys(row, DECISION))


def frame_problem(row):
    return _in_words(_wrong_keys(row, FRAME))


def correction_problem(row):
    if row.get("schema") in DEN_LOG_SCHEMAS:
        return (f"is a {row['schema']} line, not a Correction record: --corrections reads canon Corrections, with ts, action "
                "and hold, and correction-capture's corrections.jsonl is for den-core's own --corrections")
    return _in_words(_wrong_keys(row, CORRECTION))


def occupancy_problem(row):
    return _in_words(_wrong_keys(row, OCCUPANCY))


def gold_problem(row):
    return _in_words(_wrong_keys(row, GOLD))


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(", ", ": ")) + "\n")


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def schema_record(record_id):
    data = json.loads(SCHEMAS.read_text(encoding="utf-8"))
    for r in data["records"]:
        if r["id"] == record_id:
            return r
    raise KeyError(record_id)


def schema_keys(record_id, field):
    """The keys of a `{a, b, c[], ...}` type string in schemas.json, in order."""
    for f in schema_record(record_id)["fields"]:
        if f["name"] == field:
            inner = f["type"].strip().strip("[]").strip()[1:-1]
            return [k.strip().rstrip("[]") for k in inner.split(",")]
    raise KeyError(field)


class Clock:
    """Times as Unix seconds. Clock strings are local time on an anchor's day.

    Decision and Correction logs may write times as readable local clock
    strings (19:03:32.55); the contracts give no date with them. A clock string
    resolves to the instant within 12 h of a numeric anchor from the same
    record whose local time of day matches, at `utc_offset_s` (east positive),
    or at the PC's own offset for that anchor when none is given.
    """

    def __init__(self, utc_offset_s=None):
        self.utc_offset_s = utc_offset_s

    def offset(self, near):
        if self.utc_offset_s is not None:
            return self.utc_offset_s
        return time.localtime(near).tm_gmtoff

    def resolve(self, value, near):
        if value is None:
            return None
        if type(value) in (int, float):
            return float(value)
        m = CLOCK.match(str(value).strip())
        if not m:
            raise ValueError(f"not a time: {value!r}")
        if near is None:
            raise ValueError(f"clock string {value!r} has no numeric anchor")
        sod = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3] or 0)
        off = self.offset(near)
        t = (near + off) // DAY * DAY + sod - off
        while t - near > DAY / 2:
            t -= DAY
        while near - t > DAY / 2:
            t += DAY
        return t

    def hhmmss(self, t):
        local = (t + self.offset(t)) % DAY
        return "%02d%02d%02d" % (local // 3600, local % 3600 // 60, local % 60)

    def day(self, t):
        return int((t + self.offset(t)) // DAY)


def light_state_id(rec, clock):
    """A LightState's id: its `id` key if the line has one, else ls-HHMMSS local.

    A frame off the whole second gets its hundredths too (ls-190332.70).
    """
    if rec.get("id"):
        return rec["id"]
    ts = float(rec["ts"])
    frac = round(ts % 1, 2)
    return "ls-" + clock.hhmmss(ts) + (("%.2f" % frac)[1:] if 0 < frac < 1 else "")


def person_state_anchor(ids):
    for sid in ids or []:
        m = re.search(r"@(\d+(?:\.\d+)?)$", str(sid))
        if m:
            return float(m[1])
    return None
