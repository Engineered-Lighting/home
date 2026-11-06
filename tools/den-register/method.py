"""REGISTER-V1.md's one fenced block: the numbers rung 1 uses, and its open slots.

A line is `name = value  # note`. A value is a number, or `open` with a
proposed value in the note (`# open, proposed 0.25`). The click page samples
depth with proposed values so the owner sees feedback; a scored fit refuses
while any slot is open. The file is read with CRLF or LF alike, and its hash is
taken over LF text, so a Windows checkout hashes the same.
"""
import hashlib
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHOD_FILE = "REGISTER-V1.md"
BLOCK = "register-v1"
KEYS = ("min_fit_pairs", "min_checks", "gate_m", "min_spread_m", "depth_window_px",
        "min_depth_valid_fraction", "max_left_depth_skew_s", "mount_tolerance_mm",
        "mount_tolerance_fraction", "mount_changed_fraction", "mount_min_cells", "mount_view_reach_m",
        "mount_min_spread_m", "mount_min_reach_m")
PROPOSED = re.compile(r"\bproposed\s+(-?[0-9]+(?:\.[0-9]+)?)\b")


def lf_text(path):
    return Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")


def sha256_lf(path):
    return hashlib.sha256(lf_text(path).encode("utf-8")).hexdigest()


class Method:
    def __init__(self, values, open_slots, sha256, file=METHOD_FILE):
        self.values, self.open, self.sha256, self.file = values, open_slots, sha256, file

    def __getitem__(self, name):
        """A closed value; an open slot raises KeyError, so a scored step cannot read it."""
        return self.values[name]

    def get(self, name):
        """The closed value, or the proposed one for an open slot (clicks only)."""
        return self.values[name] if name in self.values else self.open[name]

    def scored(self):
        """Values for a scored run, which needs every slot closed."""
        return dict(self.values) if not self.open else None

    def describe_open(self):
        return [f"{name} (proposed {value:g})" for name, value in sorted(self.open.items())]


def parse(text, file=METHOD_FILE, sha256=None):
    text = text.replace("\r\n", "\n")
    blocks = re.findall(r"^```" + BLOCK + r"[ \t]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"{file} must hold exactly one ```{BLOCK} block")
    values, open_slots = {}, {}
    for raw_line in blocks[0].splitlines():
        line, _, note = raw_line.partition("#")
        line = line.strip()
        if not line:
            continue
        name, sep, raw = (part.strip() for part in line.partition("="))
        if not sep or name in values or name in open_slots:
            raise ValueError(f"bad method line: {raw_line!r}")
        if raw == "open":
            match = PROPOSED.search(note)
            if not match:
                raise ValueError(f"open slot {name} needs '# open, proposed <number>'")
            open_slots[name] = json.loads(match[1])
            continue
        value = json.loads(raw)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"bad method line: {raw_line!r}")
        values[name] = value
    if set(values) | set(open_slots) != set(KEYS):
        raise ValueError(f"{file} keys differ from method.py's list")
    return Method(values, open_slots, sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(), file)


def load(path=None):
    path = Path(path or HERE / METHOD_FILE)
    return parse(lf_text(path), path.name, sha256_lf(path))
