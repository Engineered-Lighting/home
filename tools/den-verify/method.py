"""VERIFY-V1.md's one fenced block: the numbers rung 10 uses, and its open slots.

A line is `name = value  # note`. A value is a JSON number or a quoted string;
`open` marks an empty slot whose proposed value is in the note
(`# open, proposed 0.05` or `# open, proposed "zero"`). A scored run refuses
while any slot is open; a preview runs on the proposed values. The file reads
the same with CRLF or LF, and its hash is taken over LF text, so a Windows
checkout hashes the same.
"""
import hashlib
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHOD_FILE = "VERIFY-V1.md"
BLOCK = "verify-v1"
NUMBERS = ("tolerance", "hit_rate_min", "fade_s_den_lighting", "fade_s_den_lighting_restore_on",
           "fade_s_probe", "v_min", "tau_r_cm", "interval_level", "bootstrap_rounds",
           "settle_margin_s", "change_allowance", "held_share_min", "uncertainty_rel", "uncertainty_abs_lx",
           "spill_allowance_lx", "daylight_change_min", "shadow_radius_m", "max_frame_gap_s",
           "delta_hit_rate")
CHOICES = {"background": ("reference_frame", "zero"),
           "held_rule": ("free_bulbs_count", "exclude_both"),
           "spill_band_top": ("target_lx",),
           "record_extensions": ("verify-ext/v1",)}
KEYS = NUMBERS + tuple(CHOICES)
PROPOSED = re.compile(r'\bproposed\s+(-?[0-9]+(?:\.[0-9]+)?|"[^"]*")')

# Pending owner decisions (decisions.json) that rung 10 depends on, built as
# named options. `selected` stays None until the owner decides; a case that
# depends on one is reported but not admitted as pass or fail.
PENDING = {
    "envelope-parameters": {"options": ("set",), "selected": None,
                            "affects": "the shadow explanation's nominal body envelope"},
    "fixture-capabilities": {"options": ("set",), "selected": None,
                             "affects": "Hue landing on a command sent mid-transition"},
}
# Answered in owner-choices.md (choice 6); its deltas stay open (delta_hit_rate).
ANSWERED = {"promotion-margins": "two-sided-95"}


def lf_text(path):
    return Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")


def sha256_lf(path):
    return hashlib.sha256(lf_text(path).encode("utf-8")).hexdigest()


class Method:
    def __init__(self, values, open_slots, sha256, file=METHOD_FILE):
        self.values, self.open, self.sha256, self.file = values, open_slots, sha256, file

    def scored(self):
        """Values for a scored run, which needs every slot closed; else None."""
        return dict(self.values) if not self.open else None

    def preview(self):
        """Values with every open slot at its proposed value (writes no records)."""
        return {**self.open, **self.values}

    def describe_open(self):
        return [f"{name} (proposed {json.dumps(value)})" for name, value in sorted(self.open.items())]


def _check(name, value, line):
    if name in CHOICES:
        if value not in CHOICES[name]:
            raise ValueError(f"{name} must be one of {CHOICES[name]}: {line!r}")
    elif type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} needs a number: {line!r}")
    return value


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
        if not sep or name not in KEYS or name in values or name in open_slots:
            raise ValueError(f"bad method line: {raw_line!r}")
        if raw == "open":
            match = PROPOSED.search(note)
            if not match:
                raise ValueError(f"open slot {name} needs '# open, proposed <value>'")
            open_slots[name] = _check(name, json.loads(match[1]), raw_line)
        else:
            values[name] = _check(name, json.loads(raw), raw_line)
    if set(values) | set(open_slots) != set(KEYS):
        missing = sorted(set(KEYS) - set(values) - set(open_slots))
        raise ValueError(f"{file} keys differ from method.py's list: missing {missing}")
    return Method(values, open_slots, sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(), file)


def load(path=None):
    path = Path(path or HERE / METHOD_FILE)
    return parse(lf_text(path), path.name, sha256_lf(path))


def closed_copy(text, overrides=None):
    """The method text with every open slot closed at its proposal (or an override).

    For tests and for the owner's own edits: it shows exactly what closing a
    slot looks like. Nothing in the tool calls it on a real run.
    """
    overrides = overrides or {}
    out, inside = [], False
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        if raw_line.startswith("```"):
            inside = raw_line.strip() == "```" + BLOCK
            out.append(raw_line)
            continue
        line, _, note = raw_line.partition("#")
        name, sep, raw = (part.strip() for part in line.partition("="))
        if not inside:
            pass
        elif sep and raw == "open" and name in KEYS:
            value = overrides.get(name, json.loads(PROPOSED.search(note)[1]))
            raw_line = f"{name} = {json.dumps(value)}  # closed for this copy"
        elif sep and name in overrides:
            raw_line = f"{name} = {json.dumps(overrides[name])}  # overridden for this copy"
        out.append(raw_line)
    return "\n".join(out)
