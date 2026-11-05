#!/usr/bin/env python3
"""N1's core: formats, the split rule, boxes, label checks, re-mark agreement, freezing.

Standard library only. label.py imports it on the owner's PC. From a take it
reads only frames.csv, the bundles frames.csv lists (through R's
check.read_bundle), its check-N.json files and two fields of take.json
(script, started_unix). It never reads cue times.
"""
import csv
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEN_TAKE = HERE.parent / "den-take"
if str(DEN_TAKE) not in sys.path:
    sys.path.insert(0, str(DEN_TAKE))
import check  # noqa: E402  R's bundle reader, span parser and segment rule
import take as den_take  # noqa: E402  R's folder rules

PROTOCOL_FILE = "PROTOCOL-V1.md"
PROTOCOL_BLOCK = "n1-protocol-v1"
PROTOCOL_KEYS = ("phone_at_counter_band", "pause_s", "remark_days", "tau_off_s",
                 "pad_start_fraction", "pad_min_px")
TOOLS = ("n1.py", "label.py", "page.html")
WIDTH, HEIGHT = 640, 360
NV12_BYTES = 640 * 540
PERSON_ID = "p1"
SPLITS = ("development", "held_out")
BANDS = ("fine", "casual")
ONSET_KINDS = ("touch", "no_touch", "carried")
OFFSET_KINDS = ("release", "no_touch", "carried")
SPAN_KINDS = ("quiet", "uncertain", "low_light")
SURFACE_TYPES = ("desk", "counter", "shelf", "table", "seat", "lap", "handheld")
FIXED_SCRIPTS = ("commission", "free")
STAGED_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
TAKE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CHECK_NAME = re.compile(r"check-([0-9]+)\.json")
FRAME_FILE = re.compile(r"frames/[0-9]{6}-[0-9]+\.npz")
SURFACE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
FREEZE_LINE = re.compile(r"^den-n1 (fixture|labels)-v([0-9]+) sha256=([0-9a-f]{64})\b", re.M)
INTERVAL_KEYS = {"onset_n", "onset_t_s", "onset_kind", "offset_n", "offset_t_s", "offset_kind",
                 "band", "case", "sure", "by"}
SPAN_KEYS = {"kind", "case", "start_n", "end_n", "by"}
SURE_KEYS = {"onset", "offset", "band"}
FIXTURE_CSV_FIELDS = ("n", "t_s", "x0", "y0", "x1", "y1")
DEFAULT_LAP_CM, DEFAULT_SEATED_CM, DEFAULT_STANDING_CM = 55, 85, 110
now = time.time  # patched in tests


class Refused(Exception):
    """The request breaks a rule; nothing was written."""


# ---- canonical JSON and hashes --------------------------------------------------

def canonical(value):
    """Sorted keys, no spaces, ASCII, one final newline (den-core's dumps rule)."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    return sha256_bytes(Path(path).read_bytes())


def read_json(path):
    def refuse_constant(_):
        raise ValueError("non-finite number")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=refuse_constant)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"{Path(path).name} is missing or not valid JSON ({type(exc).__name__})") from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)


def tool_sha256s():
    return {name: file_sha256(HERE / name) for name in TOOLS}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


# ---- the protocol ---------------------------------------------------------------

@dataclass(frozen=True)
class Protocol:
    file: str
    sha256: str
    settings: dict

    @property
    def phone_band(self):
        return self.settings["phone_at_counter_band"]

    @property
    def interval_cases(self):
        phone = ["phone_at_counter"] if self.phone_band == "casual" else []
        return ["sorting_mail", "still_reader", "wiping"] + phone + ["tidying", "other"]

    @property
    def quiet_cases(self):
        return ["idle_seated", "idle_standing"] + (["phone_at_counter"] if self.phone_band == "none" else [])

    def lists(self):
        return {"bands": list(BANDS), "onset_kinds": list(ONSET_KINDS), "offset_kinds": list(OFFSET_KINDS),
                "interval_cases": self.interval_cases, "span_kinds": list(SPAN_KINDS),
                "quiet_cases": self.quiet_cases, "surface_types": list(SURFACE_TYPES)}


def load_protocol(path=None):
    """The protocol file's sha256 and the settings in its one fenced block."""
    path = Path(path or HERE / PROTOCOL_FILE)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1 or blocks[0][0].strip() != PROTOCOL_BLOCK:
        raise ValueError(f"the protocol must hold exactly one ```{PROTOCOL_BLOCK} block")
    values = {}
    for line in blocks[0][1].splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, value = (part.strip() for part in line.partition("="))
        parsed = json.loads(value) if sep else None
        if name in values or parsed is None:
            raise ValueError(f"bad protocol line: {line!r}")
        values[name] = parsed
    if set(values) != set(PROTOCOL_KEYS):
        raise ValueError("protocol settings differ from n1.py's list")
    if values["phone_at_counter_band"] not in ("casual", "none"):
        raise ValueError("phone_at_counter_band must be \"casual\" or \"none\"")
    for name in PROTOCOL_KEYS[1:]:
        if not finite(values[name]) or type(values[name]) is bool or values[name] <= 0:
            raise ValueError(f"{name} must be a positive number")
    return Protocol(path.name, sha256_bytes(raw), values)


# ---- folders --------------------------------------------------------------------

def n1_folder(path):
    """The N1 folder: absolute, outside every git checkout and OneDrive."""
    path = Path(path)
    if not path.is_absolute():
        raise Refused(r"the N1 folder must be absolute, for example C:\den-takes\n1")
    path = path.resolve()
    if den_take.inside_checkout(path):
        raise Refused("the N1 folder is inside a git checkout; boxes and labels never go into git")
    if den_take.in_onedrive(path):
        raise Refused("the N1 folder is inside OneDrive; N1 output stays on this PC only")
    for sub in ("work/boxes", "work/labels", "work/remark", "work/practice"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def take_folder(takes_root, take_id):
    if not TAKE_ID.fullmatch(take_id or ""):
        raise Refused(f"{take_id!r} is not a take folder name")
    folder = Path(takes_root).resolve() / take_id
    if not (folder / "take.json").is_file():
        raise Refused(f"no take.json in {folder}")
    return folder


# ---- reading a take (only these fields, never cue times) --------------------------------

def read_take_fields(folder):
    """Only `script` and `started_unix` from take.json."""
    data = read_json(Path(folder) / "take.json")
    if type(data) is not dict:
        raise Refused("take.json is not an object")
    script, started = data.get("script"), data.get("started_unix")
    if type(script) is not str:
        raise Refused("take.json has no script name")
    if started is not None and (not finite(started) or type(started) is bool):
        raise Refused("take.json's started_unix is not a number")
    return {"script": script, "started_unix": started}


def is_staged(script):
    return script not in FIXED_SCRIPTS and bool(STAGED_NAME.fullmatch(script))


def list_checks(folder):
    found = []
    for path in Path(folder).glob("check-*.json"):
        match = CHECK_NAME.fullmatch(path.name)
        if match:
            found.append((int(match[1]), path))
    return [path for _, path in sorted(found)]


def read_frames(folder):
    """frames.csv rows: n, t (float), t_s (the csv text), file, npz sha256."""
    path = Path(folder) / "frames.csv"
    try:
        with open(path, newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        frames = [{"n": int(row["n"]), "t": float(row["t_s"]), "t_s": row["t_s"], "file": row["file"],
                   "sha256": row["npz_sha256"]} for row in rows]
    except (OSError, KeyError, ValueError, TypeError):
        raise Refused("frames.csv is missing or malformed") from None
    for frame in frames:
        if not FRAME_FILE.fullmatch(frame["file"]) or not math.isfinite(frame["t"]):
            raise Refused(f"frames.csv row {frame['n']} names a file outside the take's frames folder")
    if [f["n"] for f in frames] != list(range(1, len(frames) + 1)):
        raise Refused("frames.csv frame numbers are not 1, 2, 3, ...")
    return frames


def read_nv12(folder, frame):
    """One frame's raw 640x360 NV12 bytes, after checking its sha256 against frames.csv."""
    data = (Path(folder) / frame["file"]).read_bytes()
    if sha256_bytes(data) != frame["sha256"]:
        raise Refused(f"frame {frame['n']}'s bundle does not match frames.csv")
    _, rgb = check.read_bundle(data, with_rgb=True)
    if len(rgb) != NV12_BYTES:
        raise Refused("unexpected NV12 size")
    return rgb


# ---- second-person spans and the union test ---------------------------------------------

def merge(spans):
    return check.merge([tuple(s) for s in spans])


def person_cuts(report):
    return merge([(c["start_t_s"], c["end_t_s"]) for c in report.get("cuts") or []
                  if c.get("kind") == "second_person"])


def typed_spans(report):
    typed = (report.get("second_person") or {}).get("typed")
    return check.parse_spans(typed, 0) if typed else []


def covers(cuts, needed):
    return all(any(c <= a and b <= d for c, d in cuts) for a, b in needed)


def format_spans(spans):
    """Typed spans as mm:ss-mm:ss, merged; never the raw word none."""
    if not spans:
        return "(no earlier spans)"
    return ", ".join(f"{check.mmss(a)}-{check.mmss(b)}" for a, b in spans)


@dataclass
class TakeState:
    take: str
    folder: Path
    script: str
    started_unix: float
    ok: bool  # the newest check passes the union test
    reason: str
    check: str = None
    check_sha256: str = None
    report: dict = None
    union: str = "(no earlier spans)"
    check_names: tuple = ()

    def segments(self):
        return [(s["first_n"], s["last_n"]) for s in self.report["usable"]["segments"] if s["frames"]]


def take_state(folder, *, allow_commissioning=False):
    """Whether the newest check counts: PASS, retained, and covering every earlier check's cuts."""
    folder = Path(folder)
    fields = read_take_fields(folder)
    paths = list_checks(folder)
    state = TakeState(folder.name, folder, fields["script"], fields["started_unix"], False, "no check file",
                      check_names=tuple(p.name for p in paths))
    if not paths:
        return state
    reports = [(path, path.read_bytes()) for path in paths]
    parsed = []
    for path, raw in reports:
        try:
            parsed.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            state.reason = f"{path.name} is not valid JSON"
            return state
    state.union = format_spans(merge(s for report in parsed for s in typed_spans(report)))
    newest, newest_raw = parsed[-1], reports[-1][1]
    required = merge(s for report in parsed[:-1] for s in person_cuts(report))
    usable_ok = newest.get("commissioning") is False or allow_commissioning
    if newest.get("verdict") != "PASS" or not usable_ok:
        state.reason = f"{paths[-1].name} is not a retained PASS"
    elif not covers(person_cuts(newest), required):
        state.reason = f"{paths[-1].name} does not cover the second-person cuts of every earlier check"
    else:
        state.ok, state.reason = True, "ok"
        state.check, state.check_sha256, state.report = paths[-1].name, sha256_bytes(newest_raw), newest
    return state


def usable_frames(state, frames):
    by_n = {f["n"]: f for f in frames}
    out = []
    for first, last in state.segments():
        for n in range(first, last + 1):
            if n not in by_n:
                raise Refused(f"{state.check} names frame {n}, which frames.csv lacks")
            out.append(by_n[n])
    return out


# ---- the split ---------------------------------------------------------------------

def split_for(order):
    return SPLITS[(order - 1) % 2]


def entry_hash(entry):
    return sha256_bytes(canonical(entry))


def load_split(path):
    """split.json's entries, after checking the hash chain and the alternation rule."""
    path = Path(path)
    if not path.exists():
        return []
    data = read_json(path)
    entries = data.get("entries") if type(data) is dict and data.get("schema") == "den-n1-split-1" else None
    if type(entries) is not list:
        raise Refused("split.json is not a den-n1-split-1 file")
    previous, counts, seen = None, {}, set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"session", "script", "started_unix", "order", "split",
                                                     "registered_unix", "prev"}:
            raise Refused("split.json was edited: an entry has the wrong fields")
        counts[entry["script"]] = counts.get(entry["script"], 0) + 1
        if (entry["prev"] != previous or entry["session"] in seen
                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):
            raise Refused("split.json was edited; it is append-only and a split never changes")
        previous = entry_hash(entry)
        seen.add(entry["session"])
    if data.get("head") != previous:
        raise Refused("split.json was edited; its head hash does not match its last entry")
    return entries


def register(path, sessions):
    """Append sessions [(take, script, started_unix)] in recording order; never reassign."""
    entries = load_split(path)
    known = {e["session"] for e in entries}
    added = []
    for take_id, script, started in sorted(sessions, key=lambda s: (s[2], s[0])):
        if take_id in known:
            continue
        order = 1 + sum(1 for e in entries if e["script"] == script)
        entry = {"session": take_id, "script": script, "started_unix": started, "order": order,
                 "split": split_for(order), "registered_unix": now(),
                 "prev": entry_hash(entries[-1]) if entries else None}
        entries.append(entry)
        added.append(entry)
        known.add(take_id)
    if added:
        write_json(path, {"schema": "den-n1-split-1", "entries": entries, "head": entry_hash(entries[-1])})
    return added


# ---- pad and boxes ---------------------------------------------------------------------

def load_pad(n1root, protocol):
    path = Path(n1root) / "work" / "pad.json"
    if not path.exists():
        return {"fraction": protocol.settings["pad_start_fraction"], "min_px": protocol.settings["pad_min_px"]}
    data = read_json(path)
    return {"fraction": data["fraction"], "min_px": data["min_px"]}


def set_pad(n1root, protocol, fraction, min_px):
    current = load_pad(n1root, protocol)
    if versions(n1root, "fixture"):
        raise Refused("the pad is fixed once a fixture is frozen")
    if not (finite(fraction) and finite(min_px)) or fraction < current["fraction"] or min_px < current["min_px"]:
        raise Refused(f"the pad may only grow (now {current['fraction']:g} and {current['min_px']:g} px)")
    pad = {"fraction": fraction, "min_px": min_px}
    write_json(Path(n1root) / "work" / "pad.json", dict(pad, schema="den-n1-pad-1"))
    return pad


def pad_box(box, pad):
    """Tight box [x0, y0, x1, y1] -> padded whole pixels, clipped to the image."""
    x0, y0, x1, y1 = box
    px = max(pad["min_px"], pad["fraction"] * (x1 - x0))
    py = max(pad["min_px"], pad["fraction"] * (y1 - y0))
    return [max(0, math.floor(round(x0 - px, 6))), max(0, math.floor(round(y0 - py, 6))),
            min(WIDTH, math.ceil(round(x1 + px, 6))), min(HEIGHT, math.ceil(round(y1 + py, 6)))]


def check_box(box):
    if box is None:
        return None
    if (type(box) is not list or len(box) != 4 or not all(finite(v) and type(v) is not bool for v in box)
            or not (0 <= box[0] < box[2] <= WIDTH and 0 <= box[1] < box[3] <= HEIGHT)):
        raise Refused(f"bad box {box!r}")
    return [float(v) for v in box]


def check_keyframes(keyframes, allowed_ns):
    if type(keyframes) is not list:
        raise Refused("keyframes must be a list")
    out, seen = [], set()
    for item in keyframes:
        if type(item) is not list or len(item) != 2 or type(item[0]) is not int:
            raise Refused("each keyframe is [n, box or null]")
        if item[0] in seen or item[0] not in allowed_ns:
            raise Refused(f"keyframe at frame {item[0]} is repeated or not a usable frame")
        seen.add(item[0])
        out.append([item[0], check_box(item[1])])
    return sorted(out)


def tight_boxes(keyframes, frames, t_of):
    """Per-frame tight boxes: straight lines in t_s, empty runs stay empty, the last keyframe holds."""
    keys = sorted(keyframes)
    out, k = [], -1
    for frame in frames:
        while k + 1 < len(keys) and keys[k + 1][0] <= frame["n"]:
            k += 1
        if k < 0 or keys[k][1] is None:
            out.append(None)
            continue
        n0, b0 = keys[k]
        if k + 1 < len(keys) and keys[k + 1][1] is not None and frame["n"] != n0:
            n1_, b1 = keys[k + 1]
            w = (frame["t"] - t_of[n0]) / (t_of[n1_] - t_of[n0])
            out.append([a + (b - a) * w for a, b in zip(b0, b1)])
        else:
            out.append(list(b0))
    return out


def fixture_rows(keyframes, usable, t_of, pad):
    rows = []
    for frame, box in zip(usable, tight_boxes(keyframes, usable, t_of)):
        rows.append([frame["n"], frame["t_s"]] + (pad_box(box, pad) if box else ["", "", "", ""]))
    return rows


def rows_csv(rows):
    lines = [",".join(FIXTURE_CSV_FIELDS)] + [",".join(str(v) for v in row) for row in rows]
    return ("\n".join(lines) + "\n").encode("ascii")


def check_ranges(ranges):
    if type(ranges) is not list or not all(type(r) is list and len(r) == 2 and all(type(v) is int for v in r)
                                           and r[0] <= r[1] for r in ranges):
        raise Refused("audited must be a list of [first_n, last_n]")
    return [list(r) for r in merge(ranges)]


def audited_all(ranges, usable):
    return all(any(a <= f["n"] <= b for a, b in ranges) for f in usable)


# ---- labels ------------------------------------------------------------------------

def segment_index(segments):
    index = {}
    for i, (first, last) in enumerate(segments):
        for n in range(first, last + 1):
            index[n] = i
    return index


def check_labels(intervals, spans, protocol, segments, t_of):
    """Every protocol check the server runs on a save. Fills each end's t_s from frames.csv."""
    seg = segment_index(segments)
    edges_first = {first for first, _ in segments}
    edges_last = {last for _, last in segments}
    if type(intervals) is not list or type(spans) is not list:
        raise Refused("intervals and spans must be lists")
    out_intervals = []
    for i, item in enumerate(intervals, 1):
        where = f"interval {i}"
        if type(item) is not dict or not set(item) <= INTERVAL_KEYS or not {"onset_n", "offset_n"} <= set(item):
            raise Refused(f"{where}: unknown or missing fields")
        a, b = item["onset_n"], item["offset_n"]
        if type(a) is not int or type(b) is not int or a not in seg or b not in seg:
            raise Refused(f"{where}: its ends must be usable frames")
        if seg[a] != seg[b]:
            raise Refused(f"{where} crosses a cut or a segment edge")
        if a >= b:
            raise Refused(f"{where}: the onset must come before the offset")
        if item.get("onset_kind") not in ONSET_KINDS or item.get("offset_kind") not in OFFSET_KINDS:
            raise Refused(f"{where}: onset or offset kind not in the fixed lists")
        if (item["onset_kind"] == "carried") != (a in edges_first):
            raise Refused(f"{where}: an onset is carried exactly when it is on a segment's first frame")
        if (item["offset_kind"] == "carried") != (b in edges_last):
            raise Refused(f"{where}: an offset is carried exactly when it is on a segment's last frame")
        if item.get("band") not in BANDS:
            raise Refused(f"{where}: band not in {BANDS}")
        if item.get("case") not in protocol.interval_cases:
            raise Refused(f"{where}: case {item.get('case')!r} is not an interval case under this protocol")
        sure = item.get("sure")
        if type(sure) is not dict or set(sure) != SURE_KEYS or not all(type(v) is bool for v in sure.values()):
            raise Refused(f"{where}: sure needs onset, offset and band, each true or false")
        if item.get("by") != "owner":
            raise Refused(f"{where}: every item records by: owner")
        out_intervals.append(dict(item, onset_t_s=t_of[a], offset_t_s=t_of[b]))
    out_intervals.sort(key=lambda x: x["onset_n"])
    for prev, item in zip(out_intervals, out_intervals[1:]):
        if item["onset_n"] <= prev["offset_n"]:
            raise Refused(f"intervals at frames {prev['onset_n']} and {item['onset_n']} overlap")
        if (seg[prev["offset_n"]] == seg[item["onset_n"]] and prev["case"] == item["case"]
                and item["onset_t_s"] - prev["offset_t_s"] <= protocol.settings["pause_s"]):
            raise Refused(f"a pause of {protocol.settings['pause_s']:g} s or less does not end an interval "
                          f"(frames {prev['offset_n']} to {item['onset_n']}); join them")
    out_spans = []
    for i, item in enumerate(spans, 1):
        where = f"span {i}"
        if type(item) is not dict or not set(item) <= SPAN_KEYS or not {"kind", "start_n", "end_n"} <= set(item):
            raise Refused(f"{where}: unknown or missing fields")
        a, b, kind = item["start_n"], item["end_n"], item["kind"]
        if kind not in SPAN_KINDS:
            raise Refused(f"{where}: kind not in {SPAN_KINDS}")
        if type(a) is not int or type(b) is not int or a not in seg or b not in seg or seg[a] != seg[b] or a > b:
            raise Refused(f"{where}: start and end must be usable frames in one segment, in order")
        if kind == "quiet" and item.get("case") not in protocol.quiet_cases:
            raise Refused(f"{where}: a quiet span needs a case from {protocol.quiet_cases}")
        if kind != "quiet" and "case" in item:
            raise Refused(f"{where}: only quiet spans have a case")
        if item.get("by") != "owner":
            raise Refused(f"{where}: every item records by: owner")
        out_spans.append(dict(item))
    out_spans.sort(key=lambda x: (x["start_n"], x["kind"]))
    exclusive = [(s["start_n"], s["end_n"]) for s in out_spans if s["kind"] != "low_light"]
    others = [(x["onset_n"], x["offset_n"]) for x in out_intervals]
    for i, (a, b) in enumerate(exclusive):
        for c, d in others + exclusive[i + 1:]:
            if a <= d and c <= b:
                raise Refused(f"a quiet or uncertain span at frames {a} to {b} overlaps another item")
    return out_intervals, out_spans


def split_pieces(a, b, segments):
    return [(max(a, first), min(b, last)) for first, last in segments if max(a, first) <= min(b, last)]


def clip_items(intervals, spans, segments, t_of):
    """Clip intervals and spans to new usable segments.

    An interval end that lands on a segment's first or last frame becomes carried, as the protocol
    requires, whether the cut split the interval or only moved a segment edge onto its end."""
    firsts, lasts = {first for first, _ in segments}, {last for _, last in segments}
    changes, out_intervals, out_spans = [], [], []
    for item in intervals:
        a, b = item["onset_n"], item["offset_n"]
        kept = []
        for first, last in split_pieces(a, b, segments):
            if first >= last:
                continue
            piece = dict(item, onset_n=first, offset_n=last, onset_t_s=t_of[first], offset_t_s=t_of[last])
            if first in firsts:
                piece["onset_kind"] = "carried"
            if last in lasts:
                piece["offset_kind"] = "carried"
            kept.append(piece)
        out_intervals += kept
        if kept == [item]:
            continue
        described = []
        for piece in kept:
            frames = [] if (piece["onset_n"], piece["offset_n"]) == (a, b) else \
                [f"{piece['onset_n']} to {piece['offset_n']}"]
            carried = [end for end in ("onset", "offset") if piece[f"{end}_kind"] != item[f"{end}_kind"]]
            described.append(" ".join(frames + ([" and ".join(carried) + " carried"] if carried else [])))
        changes.append(f"interval {item['case']} at frames {a} to {b}: " + (", ".join(described) or "dropped"))
    for item in spans:
        a, b = item["start_n"], item["end_n"]
        pieces = split_pieces(a, b, segments)
        if pieces == [(a, b)]:
            out_spans.append(item)
            continue
        out_spans += [dict(item, start_n=first, end_n=last) for first, last in pieces]
        changes.append(f"span {item['kind']} at frames {a} to {b}: "
                       + (", ".join(f"{first} to {last}" for first, last in pieces) or "dropped"))
    return out_intervals, out_spans, changes


def quiet_seconds_past_tau(intervals, spans, tau_off_s, t_of):
    """Per quiet case: seconds more than tau_off after the latest labelled offset before them."""
    offsets = sorted(x["offset_t_s"] for x in intervals)
    out = {}
    for span in spans:
        if span["kind"] != "quiet":
            continue
        start, end = t_of[span["start_n"]], t_of[span["end_n"]]
        before = [t for t in offsets if t <= start]
        scored_from = max(start, before[-1] + tau_off_s) if before else start
        out[span["case"]] = out.get(span["case"], 0.0) + max(0.0, end - scored_from)
    return out


def uncertain_seconds(spans, t_of):
    return sum(t_of[s["end_n"]] - t_of[s["start_n"]] for s in spans if s["kind"] == "uncertain")


# ---- re-mark agreement -------------------------------------------------------------------

def percentile(values, pct):
    return check.percentile(values, pct) if values else None


def match_intervals(first, second):
    """One-to-one matches whose overlap is at least half of each interval, largest first."""
    candidates = []
    for i, a in enumerate(first):
        for j, b in enumerate(second):
            overlap = min(a["offset_t_s"], b["offset_t_s"]) - max(a["onset_t_s"], b["onset_t_s"])
            if (overlap >= 0.5 * (a["offset_t_s"] - a["onset_t_s"])
                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):
                candidates.append((-overlap, i, j))
    used_i, used_j, pairs = set(), set(), []
    for _, i, j in sorted(candidates):
        if i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
            pairs.append((first[i], second[j]))
    return pairs


def agreement(first, second):
    pairs = match_intervals(first, second)
    report = {"intervals": {"label": len(first), "remark": len(second), "matched": len(pairs)},
              "band_agreement": None, "case_agreement": None}
    if pairs:
        report["band_agreement"] = round(sum(a["band"] == b["band"] for a, b in pairs) / len(pairs), 6)
        report["case_agreement"] = round(sum(a["case"] == b["case"] for a, b in pairs) / len(pairs), 6)
    for end, placed in (("onset", ("touch", "no_touch")), ("offset", ("release", "no_touch"))):
        diffs, carried, unsure = [], 0, 0
        for a, b in pairs:
            if "carried" in (a[f"{end}_kind"], b[f"{end}_kind"]):
                carried += 1
            elif not (a["sure"][end] and b["sure"][end]) or a[f"{end}_kind"] not in placed \
                    or b[f"{end}_kind"] not in placed:
                unsure += 1
            else:
                diffs.append(abs(a[f"{end}_t_s"] - b[f"{end}_t_s"]))
        report[end] = {"n": len(diffs), "median_s": percentile(diffs, 50) and round(percentile(diffs, 50), 6),
                       "p90_s": percentile(diffs, 90) and round(percentile(diffs, 90), 6),
                       "carried": carried, "unsure": unsure}
    return report


# ---- frozen folders ------------------------------------------------------------------

def versions(n1root, kind):
    found = []
    for path in Path(n1root).glob(f"{kind}-v*"):
        match = re.fullmatch(rf"{kind}-v([0-9]+)", path.name)
        if match and path.is_dir():
            found.append(int(match[1]))
    return sorted(found)


def write_frozen(folder, files):
    """A new frozen folder: refuses an existing one, writes each file once, then makes it read-only."""
    folder = Path(folder)
    folder.mkdir()  # FileExistsError if it exists: a frozen folder is never edited
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as target:
            target.write(data)
    for path in folder.rglob("*"):
        if path.is_file():
            os.chmod(path, stat.S_IREAD)


def manifest_files(manifest):
    """{relative path: sha256} for every file a manifest names, except itself."""
    if manifest.get("schema") == "den-n1-fixture-1":
        files = {manifest["surface_map"]["file"]: manifest["surface_map"]["sha256"]}
        files.update({s["boxes"]["file"]: s["boxes"]["sha256"] for s in manifest["sessions"]})
    elif manifest.get("schema") == "den-n1-labels-1":
        files = {manifest["split"]["file"]: manifest["split"]["sha256"],
                 manifest["remark"]["file"]: manifest["remark"]["sha256"]}
        files.update({s["file"]: s["sha256"] for s in manifest["sessions"]})
    else:
        raise Refused("unknown manifest schema")
    return files


def verify_folder(folder):
    """Problems in one frozen folder: a changed, missing or extra file. Returns (manifest hash, problems)."""
    folder = Path(folder)
    name = "fixture.json" if folder.name.startswith("fixture-") else "labels.json"
    raw = (folder / name).read_bytes()
    problems = []
    manifest = json.loads(raw.decode("ascii"))
    if canonical(manifest) != raw:
        problems.append(f"{name} is not canonical JSON")
    expected = manifest_files(manifest)
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {name}
    for rel in sorted(present - set(expected)):
        problems.append(f"extra file {rel}")
    for rel, sha in sorted(expected.items()):
        if rel not in present:
            problems.append(f"missing {rel}")
        elif file_sha256(folder / rel) != sha:
            problems.append(f"{rel} changed")
    return sha256_bytes(raw), problems


def freeze_lines(text):
    """[('fixture', 1, hash), ...] from FREEZES.md, every line in order (a version may be logged twice)."""
    return [(kind, int(version), sha) for kind, version, sha in FREEZE_LINE.findall(text)]


def next_version(n1root, kind, freezes_text=""):
    """One more than any version on disk or logged in FREEZES.md, so a lost folder's number is never reused."""
    logged = [version for k, version, _ in freeze_lines(freezes_text) if k == kind]
    return 1 + max(versions(n1root, kind) + logged, default=0)


def add_counts(total, counts):
    for name, k in counts.items():
        total[name] = total.get(name, 0) + k
    return total


def utc_date(unix):
    return time.strftime("%Y-%m-%d", time.gmtime(unix))


def local_date(unix):
    return time.strftime("%Y-%m-%d", time.localtime(unix))


# ---- the N1 workspace: sessions, drafts, sync, status, freezes ------------------------------

DRAFT_KINDS = ("boxes", "labels", "remark")


class Workspace:
    """C:\\den-takes\\n1 next to the takes. Every command goes through one of these."""

    def __init__(self, takes_root, n1root, protocol=None):
        self.takes = Path(takes_root).resolve()
        self.root = n1_folder(n1root)
        self.protocol = protocol or load_protocol()
        self.split_path = self.root / "split.json"

    # -- sessions
    def entries(self):
        return load_split(self.split_path)

    def entry(self, take_id):
        for entry in self.entries():
            if entry["session"] == take_id:
                return entry
        raise Refused(f"{take_id} is not a registered session; run label.py sessions first")

    def folder(self, take_id):
        return take_folder(self.takes, take_id)

    def state(self, take_id, allow_commissioning=False):
        return take_state(self.folder(take_id), allow_commissioning=allow_commissioning)

    def require_ok(self, take_id, allow_commissioning=False):
        state = self.state(take_id, allow_commissioning)
        if not state.ok:
            raise Refused(f"{take_id} is blocked: {state.reason}. Re-run check.py on it and type the "
                          f"earlier spans plus any new ones. Earlier spans: {state.union}")
        return state

    def scan(self):
        """Register new retained staged takes in recording order. Returns (added entries, notes)."""
        known = {e["session"] for e in self.entries()}
        candidates, notes = [], []
        for folder in sorted(p for p in self.takes.iterdir() if p.is_dir()):
            if folder == self.root or not (folder / "take.json").is_file() or not TAKE_ID.fullmatch(folder.name):
                continue
            try:
                fields = read_take_fields(folder)
            except Refused as exc:
                notes.append(f"{folder.name}: skipped, {exc}")
                continue
            if not is_staged(fields["script"]) or folder.name in known:
                continue
            state = take_state(folder)
            if not state.ok:
                notes.append(f"{folder.name}: not registered, {state.reason}")
            elif fields["started_unix"] is None:
                notes.append(f"{folder.name}: not registered, take.json has no started_unix")
            else:
                candidates.append((folder.name, fields["script"], fields["started_unix"]))
        return register(self.split_path, candidates), notes

    # -- drafts
    def draft_path(self, kind, take_id, practice=False):
        if practice:
            return self.root / "work" / "practice" / f"{take_id}-{kind}.json"
        if kind not in DRAFT_KINDS:
            raise ValueError(kind)
        return self.root / "work" / kind / f"{take_id}.json"

    def load_draft(self, path):
        if not Path(path).exists():
            return None
        draft = read_json(path)
        if draft.get("protocol_sha256") != self.protocol.sha256:
            take_id = draft.get("take") or (draft.get("reference") or {}).get("take") or "<take>"
            raise Refused(f"{Path(path).name} was made under another protocol sha256 "
                          f"({str(draft.get('protocol_sha256'))[:12]}); it is refused. If the agent changed "
                          f"the protocol, run git pull first; then label.py aside {take_id} moves it aside "
                          "(never deletes it) so you can redo it")
        return draft

    def refused_draft(self, path):
        """Would load_draft refuse this file? Made under another protocol, or unreadable."""
        try:
            self.load_draft(path)
        except Refused:
            return True
        return False

    def aside(self, take_id):
        """Move a take's refused drafts to work/aside/<UTC time>/, keeping their paths. Never deletes."""
        if not TAKE_ID.fullmatch(take_id or ""):
            raise Refused(f"{take_id!r} is not a take folder name")
        work = self.root / "work"
        paths = [self.draft_path(kind, take_id, practice) for practice in (False, True) for kind in DRAFT_KINDS]
        surfaces = self.surfaces_path()
        if surfaces.exists() and self.refused_draft(surfaces):
            try:
                drawn_on = (read_json(surfaces).get("reference") or {}).get("take")
            except Refused:
                drawn_on = None
            if drawn_on in (take_id, None):
                paths.append(surfaces)
        refused = [path for path in paths if path.exists() and self.refused_draft(path)]
        if not refused:
            raise Refused(f"nothing to move aside: {take_id} has no draft that label.py refuses; drafts made "
                          "under the current protocol stay where they are")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now()))
        target, k = work / "aside" / stamp, 1
        while target.exists():
            k += 1
            target = work / "aside" / f"{stamp}-{k}"
        moves = []
        for path in refused:
            rel = path.relative_to(work).as_posix()
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, target / rel)  # a move within work\, never a delete
            moves.append(f"moved aside: work/{rel} -> work/aside/{target.name}/{rel}")
        return moves

    def surfaces_path(self):
        return self.root / "work" / "surfaces.json"

    def sync(self, take_id):
        """Apply a newer accepted check to this session's drafts. Returns the changes, to print."""
        state = self.state(take_id)
        if not state.ok:
            return []
        frames = read_frames(state.folder)
        t_of = {f["n"]: f["t"] for f in frames}
        segments = state.segments()
        changes = []
        for kind in DRAFT_KINDS:
            path = self.draft_path(kind, take_id)
            draft = self.load_draft(path)
            if draft is None or draft["check_sha256"] == state.check_sha256:
                continue
            names = list(state.check_names)
            old = draft.get("check")
            if old not in names or names.index(old) >= names.index(state.check) or \
                    file_sha256(state.folder / old) != draft["check_sha256"]:
                raise Refused(f"{path.name} names a check that is not an earlier check of {take_id}")
            if kind == "boxes":
                changes.append(f"{take_id} boxes: keyframes unchanged; frames in the new cuts get no fixture row")
            else:
                draft["intervals"], draft["spans"], clipped = clip_items(draft["intervals"], draft["spans"],
                                                                         segments, t_of)
                changes += [f"{take_id} {kind}: {line}" for line in clipped]
                if kind == "labels" and self.remark_started(take_id):
                    changes.append(f"{take_id} labels: locked by its re-mark, so its end-to-end mark stands")
                else:
                    draft["actor"]["end_to_end"] = None
                    changes.append(f"{take_id} {kind}: the end-to-end mark is cleared; look again and "
                                   "re-confirm it")
            changes.append(f"{take_id} {kind}: now tied to {state.check} ({state.check_sha256[:12]})")
            draft["check"], draft["check_sha256"] = state.check, state.check_sha256
            write_json(path, draft)
        return changes

    def sync_all(self):
        changes = []
        for entry in self.entries():
            changes += self.sync(entry["session"])
        return changes

    def remark_started(self, take_id):
        """The re-mark's draft exists: from then on the take's first label pass is locked."""
        return self.draft_path("remark", take_id).exists()

    def remark_take(self):
        development = sorted((e for e in self.entries() if e["split"] == "development"),
                             key=lambda e: (e["started_unix"], e["session"]))
        if len(development) < 2:
            raise Refused("the re-mark needs a second development session; register more sessions first")
        return development[1]["session"]

    # -- counts
    def label_counts(self, draft, t_of):
        bands = {band: sum(1 for x in draft["intervals"] if x["band"] == band) for band in BANDS}
        cases = {}
        for item in draft["intervals"]:
            cases[item["case"]] = cases.get(item["case"], 0) + 1
        quiet = {}
        for span in draft["spans"]:
            if span["kind"] == "quiet":
                quiet[span["case"]] = quiet.get(span["case"], 0) + 1
        past = quiet_seconds_past_tau(draft["intervals"], draft["spans"], self.protocol.settings["tau_off_s"],
                                      t_of)
        return {"bands": bands, "cases": cases, "quiet": quiet,
                "quiet_s_past_tau_off": {k: round(v, 3) for k, v in past.items()},
                "uncertain_s": round(uncertain_seconds(draft["spans"], t_of), 3)}

    def status(self):
        """Counts only: per split and band, quiet per case, days per split. No times, no contents."""
        lines = [f"N1 status. Protocol {self.protocol.file} {self.protocol.sha256[:12]}, phone band "
                 f"{self.protocol.phone_band}."]
        entries = self.entries()
        per_split = {s: {"sessions": 0, "days": set(), "fine": 0, "casual": 0, "cases": {}, "quiet": {},
                         "past": {}, "uncertain_s": 0.0} for s in SPLITS}
        rows, blocked = [], 0
        for entry in entries:
            take_id = entry["session"]
            state = self.state(take_id)
            split = per_split[entry["split"]]
            split["sessions"] += 1
            split["days"].add(local_date(entry["started_unix"]))
            if not state.ok:
                blocked += 1
                rows.append(f"  {take_id}  {entry['script']}  {entry['split']}  BLOCKED")
                continue
            frames = read_frames(state.folder)
            usable = usable_frames(state, frames)
            t_of = {f["n"]: f["t"] for f in frames}
            boxes = self.load_draft(self.draft_path("boxes", take_id))
            labels = self.load_draft(self.draft_path("labels", take_id))
            box_state = "none" if boxes is None else (
                "audited" if audited_all(boxes["audited"], usable) else "drawing")
            label_state = "none" if labels is None else (
                "end to end" if labels["actor"]["end_to_end"] else "in progress")
            stale = any(d and d["check_sha256"] != state.check_sha256 for d in (boxes, labels))
            rows.append(f"  {take_id}  {entry['script']}  {entry['split']}  boxes: {box_state}  "
                        f"labels: {label_state}{'  (newer check to apply)' if stale else ''}")
            if labels:
                counts = self.label_counts(labels, t_of)
                split["fine"] += counts["bands"]["fine"]
                split["casual"] += counts["bands"]["casual"]
                split["uncertain_s"] += counts["uncertain_s"]
                add_counts(split["cases"], counts["cases"])
                for case, k in counts["quiet"].items():
                    split["quiet"][case] = split["quiet"].get(case, 0) + k
                for case, s in counts["quiet_s_past_tau_off"].items():
                    split["past"][case] = split["past"].get(case, 0.0) + s
        lines.append(f"Sessions: {len(entries)} registered, {blocked} blocked.")
        lines += rows
        tau = self.protocol.settings["tau_off_s"]
        for name in SPLITS:
            s = per_split[name]
            quiet = ", ".join(f"{case} {k} ({s['past'].get(case, 0.0):.0f} s past {tau:g} s)"
                              for case, k in sorted(s["quiet"].items())) or "none"
            cases = ", ".join(f"{case} {k}" for case, k in sorted(s["cases"].items())) or "none"
            lines.append(f"Split {name}: {s['sessions']} sessions over {len(s['days'])} days; intervals "
                         f"fine {s['fine']}, casual {s['casual']}; cases {cases}; quiet {quiet}; uncertain "
                         f"{s['uncertain_s']:.1f} s.")
        try:
            take_id = self.remark_take()
            remark = self.load_draft(self.draft_path("remark", take_id))
            done = remark and remark["actor"]["end_to_end"]
            lines.append(f"Re-mark: {take_id}, {'done' if done else 'in progress' if remark else 'not started'}.")
        except Refused:
            lines.append("Re-mark: needs a second development session.")
        lines.append(f"Frozen: fixture {', '.join(f'v{v}' for v in versions(self.root, 'fixture')) or 'none'}; "
                     f"labels {', '.join(f'v{v}' for v in versions(self.root, 'labels')) or 'none'}.")
        return lines

    # -- freezes
    def surface_map(self):
        draft = self.load_draft(self.surfaces_path())
        if draft is None or not draft["surfaces"]:
            raise Refused("no surface map yet; run label.py surfaces <take> first")
        return {"schema": "den-n1-surface-map-1", "image": {"width": WIDTH, "height": HEIGHT},
                "reference": draft["reference"], "surfaces": draft["surfaces"]}

    def recheck(self, take_id, kind, draft, state, t_of):
        """A freeze runs every save-time protocol check again on a draft, as it now stands."""
        try:
            return check_labels(draft["intervals"], draft["spans"], self.protocol, state.segments(), t_of)
        except Refused as exc:
            raise Refused(f"{take_id}'s {kind} draft fails a protocol check: {exc}") from None

    def ready_session(self, entry, kind):
        """A session's state, frames and draft, checked for a freeze."""
        take_id = entry["session"]
        state = self.require_ok(take_id)
        draft = self.load_draft(self.draft_path(kind, take_id))
        if draft is None:
            raise Refused(f"{take_id} has no {kind} draft")
        if draft["check_sha256"] != state.check_sha256:
            raise Refused(f"{take_id}'s {kind} draft is tied to a superseded check ({draft['check']})")
        frames = read_frames(state.folder)
        return state, frames, usable_frames(state, frames), draft

    def freeze_fixture(self, freezes_text=""):
        changes = self.sync_all()
        entries = self.entries()
        if not entries:
            raise Refused("no registered sessions")
        surface_bytes = canonical(self.surface_map())
        pad = load_pad(self.root, self.protocol)
        files, sessions, total = {"surface-map.json": surface_bytes}, [], 0
        for entry in entries:
            state, frames, usable, draft = self.ready_session(entry, "boxes")
            if not audited_all(draft["audited"], usable):
                raise Refused(f"{entry['session']} was not audited end to end")
            rows = fixture_rows(draft["keyframes"], usable, {f["n"]: f["t"] for f in frames}, pad)
            name = f"boxes/{entry['session']}.csv"
            files[name] = rows_csv(rows)
            total += len(rows)
            sessions.append({"take": entry["session"], "check": state.check, "check_sha256": state.check_sha256,
                             "frames_csv_sha256": file_sha256(state.folder / "frames.csv"),
                             "segments": [[s["first_n"], s["last_n"], s["start_t_s"], s["end_t_s"]]
                                          for s in state.report["usable"]["segments"] if s["frames"]],
                             "frames": len(rows), "boxes": {"file": name, "sha256": sha256_bytes(files[name])}})
        version = next_version(self.root, "fixture", freezes_text)
        frozen = now()
        manifest = {"schema": "den-n1-fixture-1", "version": version, "frozen_unix": frozen,
                    "protocol": {"file": self.protocol.file, "sha256": self.protocol.sha256},
                    "tools": tool_sha256s(), "person_id": PERSON_ID, "image": {"width": WIDTH, "height": HEIGHT},
                    "padding": {"fraction": pad["fraction"], "min_px": pad["min_px"],
                                "rule": "tight box plus max(min_px, fraction x width) left and right and "
                                        "max(min_px, fraction x height) top and bottom, whole pixels outward, "
                                        "clipped to the image"},
                    "surface_map": {"file": "surface-map.json", "sha256": sha256_bytes(surface_bytes)},
                    "sessions": sessions}
        files["fixture.json"] = canonical(manifest)
        write_frozen(self.root / f"fixture-v{version}", files)
        digest = sha256_bytes(files["fixture.json"])
        line = (f"den-n1 fixture-v{version} sha256={digest} protocol={self.protocol.sha256} "
                f"frozen={utc_date(frozen)} sessions={len(sessions)} frames={total} "
                f"pad={pad['fraction']:g}/{pad['min_px']:g}px")
        return line, changes

    def freeze_labels(self, freezes_text=""):
        changes = self.sync_all()
        entries = self.entries()
        if not entries:
            raise Refused("no registered sessions")
        fixture_versions = versions(self.root, "fixture")
        if not fixture_versions:
            raise Refused("freeze the fixture first")
        fixture_dir = self.root / f"fixture-v{fixture_versions[-1]}"
        fixture_raw = (fixture_dir / "fixture.json").read_bytes()
        fixture = json.loads(fixture_raw)
        files, sessions = {}, []
        counts = {s: {"sessions": 0, "days": set(), "fine": 0, "casual": 0, "cases": {}, "uncertain_s": 0.0}
                  for s in SPLITS}
        checks = {}
        for entry in entries:
            state, frames, usable, draft = self.ready_session(entry, "labels")
            if not draft["actor"]["end_to_end"]:
                raise Refused(f"{entry['session']} is not marked labelled end to end")
            t_of = {f["n"]: f["t"] for f in frames}
            draft["intervals"], draft["spans"] = self.recheck(entry["session"], "labels", draft, state, t_of)
            checks[entry["session"]] = state.check_sha256
            record = {"schema": "den-n1-session-labels-1", "take": entry["session"], "check": state.check,
                      "check_sha256": state.check_sha256, "split": entry["split"],
                      "protocol_sha256": self.protocol.sha256, "actor": draft["actor"],
                      "intervals": draft["intervals"], "spans": draft["spans"]}
            name = f"sessions/{entry['session']}.json"
            files[name] = canonical(record)
            c = self.label_counts(draft, t_of)
            split = counts[entry["split"]]
            split["sessions"] += 1
            split["days"].add(local_date(entry["started_unix"]))
            split["fine"] += c["bands"]["fine"]
            split["casual"] += c["bands"]["casual"]
            split["uncertain_s"] = round(split["uncertain_s"] + c["uncertain_s"], 3)
            add_counts(split["cases"], c["cases"])
            sessions.append({"take": entry["session"], "split": entry["split"], "check": state.check,
                             "check_sha256": state.check_sha256, "file": name, "sha256": sha256_bytes(files[name]),
                             "counts": c["bands"], "cases": c["cases"], "quiet": c["quiet"],
                             "quiet_s_past_tau_off": c["quiet_s_past_tau_off"], "uncertain_s": c["uncertain_s"]})
        if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:
            raise Refused(f"fixture-v{fixture_versions[-1]} does not cover these sessions and checks; "
                          "freeze the fixture again first")
        remark_id = self.remark_take()
        state = self.require_ok(remark_id)
        remark = self.load_draft(self.draft_path("remark", remark_id))
        if remark is None or not remark["actor"]["end_to_end"]:
            raise Refused(f"the re-mark of {remark_id} is not done")
        if remark["check_sha256"] != state.check_sha256:
            raise Refused(f"the re-mark of {remark_id} is tied to a superseded check")
        t_of = {f["n"]: f["t"] for f in read_frames(state.folder)}
        remark["intervals"], remark["spans"] = self.recheck(remark_id, "remark", remark, state, t_of)
        first = self.load_draft(self.draft_path("labels", remark_id))
        first["intervals"], first["spans"] = self.recheck(remark_id, "labels", first, state, t_of)
        if first["actor"]["saved_unix"] and first["actor"]["saved_unix"] > remark["actor"]["started_unix"]:
            raise Refused(f"the first pass of {remark_id} was saved after its re-mark started; the agreement "
                          "is computed from the first pass as it was locked")
        files["remark.json"] = canonical({
            "schema": "den-n1-remark-1", "take": remark_id, "check": state.check,
            "check_sha256": state.check_sha256, "protocol_sha256": self.protocol.sha256, "actor": remark["actor"],
            "intervals": remark["intervals"], "spans": remark["spans"],
            "agreement": agreement(first["intervals"], remark["intervals"])})
        files["split.json"] = self.split_path.read_bytes()
        version = next_version(self.root, "labels", freezes_text)
        frozen = now()
        per_split = {name: dict(c, days=len(c["days"])) for name, c in counts.items()}
        manifest = {"schema": "den-n1-labels-1", "version": version, "frozen_unix": frozen,
                    "protocol": {"file": self.protocol.file, "sha256": self.protocol.sha256},
                    "tools": tool_sha256s(), "phone_at_counter_band": self.protocol.phone_band,
                    "split": {"file": "split.json", "sha256": sha256_bytes(files["split.json"])},
                    "remark": {"file": "remark.json", "sha256": sha256_bytes(files["remark.json"]),
                               "take": remark_id},
                    "sessions": sessions, "counts": per_split,
                    "n_intervals": sum(c["fine"] + c["casual"] for c in counts.values())}
        files["labels.json"] = canonical(manifest)
        write_frozen(self.root / f"labels-v{version}", files)
        digest = sha256_bytes(files["labels.json"])
        splits = " ".join(f"{name}={c['sessions']}s/{c['days']}d/fine{c['fine']}/casual{c['casual']}"
                          for name, c in per_split.items())
        line = (f"den-n1 labels-v{version} sha256={digest} protocol={self.protocol.sha256} "
                f"split={manifest['split']['sha256']} remark={manifest['remark']['sha256']} "
                f"frozen={utc_date(frozen)} {splits}")
        record = {"gold_set": {"n1": {"fixture": {"version": fixture_versions[-1], "hash": sha256_bytes(fixture_raw)},
                                      "labels": {"version": version, "hash": digest}}},
                  "n_intervals": manifest["n_intervals"], "label_version": version, "hash": digest,
                  "refreshed": utc_date(frozen)}
        return line, record, changes

    def verify(self, freezes_text=""):
        """Every frozen folder against its manifest, and every FREEZES.md line against a folder.

        Fails a changed, missing or extra file, a manifest hash that differs from its logged line,
        a logged version with no folder, and one version logged twice with different hashes."""
        logged, lines, ok = {}, [], True
        for kind, version, sha in freeze_lines(freezes_text):
            logged.setdefault((kind, version), [])
            if sha not in logged[kind, version]:
                logged[kind, version].append(sha)
        for kind in ("fixture", "labels"):
            on_disk = versions(self.root, kind)
            for version in sorted(set(on_disk) | {v for k, v in logged if k == kind}):
                hashes = logged.get((kind, version), [])
                problems = []
                if version in on_disk:
                    digest, problems = verify_folder(self.root / f"{kind}-v{version}")
                    head = f"{kind}-v{version} sha256={digest}: "
                    if len(hashes) == 1 and hashes[0] != digest:
                        problems.append(f"manifest hash differs from FREEZES.md ({hashes[0][:12]})")
                else:
                    head = f"{kind}-v{version}: "
                    problems.append("logged in FREEZES.md but no folder for it on this PC; copy it back "
                                    "from your backup copy")
                if len(hashes) > 1:
                    problems.append("logged twice in FREEZES.md with different hashes ("
                                    + ", ".join(h[:12] for h in hashes) + ")")
                note = "" if hashes else " (not yet in FREEZES.md)"
                ok = ok and not problems
                lines.append(head + ("; ".join(problems) if problems else "OK") + note)
        if not lines:
            lines.append("nothing frozen yet")
        return ok, lines
