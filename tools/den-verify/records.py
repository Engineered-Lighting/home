"""Reading and writing the records rung 10 touches, as JSON Lines.

One record per line, UTF-8, LF on write; CRLF or LF on read. Field names come
from docs/architecture/live-v1/schemas.json, and `schema_keys()` reads them
from there so a test can check what the tool writes against the contract.
"""
import json
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "docs" / "architecture" / "live-v1" / "schemas.json"
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$")
DAY = 86400.0


def read_jsonl(path):
    if path is None:
        return []
    text = Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"{Path(path).name} line {n}: {e}") from None
    return out


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
