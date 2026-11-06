"""METHOD.md's one fenced block: the numbers and field forms den-observe uses, and its open slots.

A line is `name = value  # note`. A value is a number or a JSON string, or
`open` with a proposed value in the note (`# open, proposed 0.25` or
`# open, proposed "freshness"`). An unscored run uses the proposed values and
says so; a scored run refuses while any slot is open. The file reads the same
with CRLF or LF, and its hash is taken over LF text, so a Windows checkout
hashes the same.
"""
import hashlib
import json
import math
import re
from pathlib import Path

import textfile

HERE = Path(__file__).resolve().parent
METHOD_FILE = "METHOD.md"
BLOCK = "observe-v1"
KEYS = ("max_depth_gap_s", "max_validity_s", "pixel_sigma_px", "max_error_bound_m",
        "detector_max_age_s", "max_expired_fraction", "keypoint_score_min", "disparity_sigma_px",
        "plane_head_h_m", "plane_head_sigma_m", "plane_arms_h_m", "plane_arms_sigma_m",
        "plane_legs_h_m", "plane_legs_sigma_m", "plane_feet_h_m", "plane_feet_sigma_m",
        "plane_max_sigma_m", "freshness_field", "validity_form", "camera_entry_form")
TEXT_KEYS = ("freshness_field", "validity_form", "camera_entry_form")
# The forms this code writes and reads. A closed text slot must name one of them.
FORMS = {"freshness_field": "freshness", "validity_form": "rejected:<reason>",
         "camera_entry_form": "den-observe-camera-1"}
PROPOSED = re.compile(r"\bproposed\s+(-?[0-9]+(?:\.[0-9]+)?|\"[^\"]*\")")


class MethodError(ValueError):
    """METHOD.md cannot be read as the observe-v1 block."""


def lf_text(path):
    return textfile.read_text(path).replace("\r\n", "\n")


class Method:
    def __init__(self, values, open_slots, sha256, file=METHOD_FILE):
        self.values, self.open, self.sha256, self.file = values, open_slots, sha256, file

    def get(self, name):
        """The closed value, or the proposed one for an open slot (unscored runs only)."""
        return self.values[name] if name in self.values else self.open[name]

    def scored(self):
        """Every value, for a scored run, or None while any slot is open."""
        return dict(self.values) if not self.open else None

    def describe_open(self):
        return [f"{name} (proposed {json.dumps(value)})" for name, value in sorted(self.open.items())]

    def record(self):
        """What a run writes about the method it used."""
        return {"file": self.file, "sha256_lf": self.sha256, "open_slots": dict(sorted(self.open.items())),
                "values_used": {k: self.get(k) for k in KEYS}}


def _value(raw, name, line):
    try:
        value = json.loads(raw)
    except ValueError:
        raise MethodError(f"bad method line: {line!r}") from None
    if name in TEXT_KEYS:
        if value != FORMS[name]:
            raise MethodError(f"{name}: this code writes {FORMS[name]!r}, not {value!r}")
    elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise MethodError(f"bad method line: {line!r}")
    return value


def parse(text, file=METHOD_FILE):
    text = text.replace("\r\n", "\n")
    blocks = re.findall(r"^```" + BLOCK + r"[ \t]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise MethodError(f"{file} must hold exactly one ```{BLOCK} block")
    values, open_slots = {}, {}
    for raw_line in blocks[0].splitlines():
        line, _, note = raw_line.partition("#")
        line = line.strip()
        if not line:
            continue
        name, sep, raw = (part.strip() for part in line.partition("="))
        if not sep or name not in KEYS or name in values or name in open_slots:
            raise MethodError(f"bad method line: {raw_line!r}")
        if raw == "open":
            match = PROPOSED.search(note)
            if not match:
                raise MethodError(f"open slot {name} needs '# open, proposed <value>'")
            open_slots[name] = _value(match[1], name, raw_line)
        else:
            values[name] = _value(raw, name, raw_line)
    if set(values) | set(open_slots) != set(KEYS):
        raise MethodError(f"{file} keys differ from method.py's list: missing "
                          f"{sorted(set(KEYS) - set(values) - set(open_slots))}")
    return Method(values, open_slots, hashlib.sha256(text.encode("utf-8")).hexdigest(), file)


def load(path=None):
    path = Path(path or HERE / METHOD_FILE)
    try:
        text = lf_text(path)
    except textfile.NotUtf8 as exc:
        raise MethodError(str(exc)) from None
    except OSError:
        raise MethodError(f"cannot read {path}") from None
    return parse(text, path.name)
