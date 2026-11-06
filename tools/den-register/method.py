"""REGISTER-V1.md's one fenced block: the numbers rung 1 uses, and its open slots.

A line is `name = value  # note`. A value is a number, or `open` with a
proposed value in the note (`# open, proposed 0.25`). The click page samples
depth with proposed values so the owner sees feedback; a scored fit refuses
while any slot is open. The file is read with CRLF or LF alike, and its hash is
taken over LF text, so a Windows checkout hashes the same.

A file that cannot be read, or a block this cannot use, is no verdict on anything:
load() refuses it as `method_invalid`, and a command that needs the method stops
there with exit 2 and saves no report.
"""
import hashlib
import json
import math
import re
from pathlib import Path

from scene import Refused, utf8_text

HERE = Path(__file__).resolve().parent
METHOD_FILE = "REGISTER-V1.md"
BLOCK = "register-v1"
KEYS = ("min_fit_pairs", "min_checks", "gate_m", "min_spread_m", "depth_window_px",
        "min_depth_valid_fraction", "max_left_depth_skew_s", "mount_tolerance_mm",
        "mount_tolerance_fraction", "mount_changed_fraction", "mount_min_cells", "mount_view_reach_m",
        "mount_min_spread_m", "mount_min_reach_m")
PROPOSED = re.compile(r"\bproposed\s+(-?[0-9]+(?:\.[0-9]+)?)\b")
FEWEST_FIT_PAIRS = 3  # rigid.fit's own floor: two points cannot fix a rotation


def lf_text(path):
    raw = Path(path).read_bytes()
    utf8_text(raw, path)  # UTF-16 is refused by name; the hash stays over what this has always read
    return raw.decode("utf-8").replace("\r\n", "\n")


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


def _number(raw):
    """The finite number raw holds, or None. Past a float or Python's limit on an integer's length is no number here."""
    try:
        value = json.loads(raw)
        return value if type(value) in (int, float) and math.isfinite(value) else None
    except (ValueError, OverflowError, RecursionError):
        return None


def _shown(text):
    """text for a refusal: a long line cut, and in ASCII, so that printing the refusal cannot fail on a code page."""
    return ascii(text[:100])


def _bad_line(line):
    return ValueError(f"bad method line: {_shown(line)}. "
                      "A slot is name = number, or name = open  # open, proposed <number>")


def parse(text, file=METHOD_FILE, sha256=None):
    """The Method in this text, or ValueError with a sentence naming what is wrong with it."""
    text = text.replace("\r\n", "\n")
    blocks = re.findall(r"^```" + BLOCK + r"[ \t]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"it must hold exactly one ```{BLOCK} block, closed")
    values, open_slots = {}, {}
    for raw_line in blocks[0].splitlines():
        line, _, note = raw_line.partition("#")
        line = line.strip()
        if not line:
            continue
        name, _, raw = (part.strip() for part in line.partition("="))
        if not name:
            raise _bad_line(line)
        if name in values or name in open_slots:
            raise ValueError(f"slot {_shown(name)} is there twice")
        if raw == "open":
            match = PROPOSED.search(note)
            value = _number(match[1]) if match else None
            if value is None:
                raise ValueError(f"open slot {_shown(name)} needs '# open, proposed <number>'")
            open_slots[name] = value
            continue
        value = _number(raw)
        if value is None:
            raise _bad_line(line)
        values[name] = value
    found = set(values) | set(open_slots)
    if found != set(KEYS):
        differ = ([f"missing {name}" for name in KEYS if name not in found]
                  + [f"not a slot: {_shown(name)}" for name in sorted(found - set(KEYS))])
        raise ValueError(f"its slots differ from method.py's list: {'; '.join(differ)}")
    fit_pairs = values.get("min_fit_pairs", open_slots.get("min_fit_pairs"))
    if fit_pairs < FEWEST_FIT_PAIRS:
        raise ValueError(f"min_fit_pairs is {fit_pairs:g}, and a rigid fit needs at least {FEWEST_FIT_PAIRS} points")
    return Method(values, open_slots, sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(), file)


def load(path=None):
    """The method in this file (REGISTER-V1.md beside this code), or Refused method_invalid."""
    path = Path(path or HERE / METHOD_FILE)
    try:
        return parse(lf_text(path), path.name, sha256_lf(path))
    except OSError as exc:
        raise Refused("method_invalid", f"{path} cannot be read ({exc.strerror})") from None
    except ValueError as exc:
        raise Refused("method_invalid", f"{path}: {exc}") from None
