#!/usr/bin/env python3
"""Check one den take under the written method (rung R). Standard library only.

    python -B tools\\den-take\\check.py C:\\den-takes\\<id>

Reads take.json, frames.csv and every saved bundle (zipfile and a small .npy
header parser: no numpy, no pickle, only uint8 and uint16). Every tolerance
comes from METHOD_FILE's fenced block. Runs the slate fit and the rules, builds
review/ (owner only) and asks for second-person spans at an interactive
console, then writes the next check-N.json. It never edits an earlier one.
"""
import argparse
import bisect
import csv
import hashlib
import io
import json
import math
import operator
import re
import struct
import sys
import time
import webbrowser
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
METHOD_FILE = "CHECK-V1.md"
SCHEMA = "den-take-check-1"
TAKE_SCHEMA = "den-take-1"
METHOD_KEYS = (
    "slate_period_s", "slate_flash_s", "slate_phase_step_s", "slate_marker_second",
    "slate_length_s", "slate_pixel_step", "screen_std_fraction", "class_percentile_low",
    "class_percentile_high", "lit_above", "dark_below", "fit_grid_s", "epsilon_s",
    "min_flashes", "max_window_s", "elapsed_bound_s", "max_slate_separation_s",
    "max_two_clock_step_s", "max_two_clock_line_s", "max_two_clock_slope_ppm",
    "gap_cut_s", "stale_pad_s", "person_pad_s")
NPY_ITEMSIZE = {"|u1": 1, "<u2": 2}
NPY_HEADER = re.compile(r"\{'descr': *'([^']*)', *'fortran_order': *(True|False), *"
                        r"'shape': *\(([0-9, ]*)\),? *\}")
Y_WIDTH, Y_HEIGHT = 640, 360
NV12_SHAPE = (540, 640)
SPAN = re.compile(r"(\d{1,3}):([0-5]\d)(?:-(\d{1,3}):([0-5]\d))?")


class Refused(Exception):
    """The take cannot be checked under this method; no check file is written."""


class BundleError(ValueError):
    pass


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_method(path=None):
    """Return the numbers in the method file's one fenced block."""
    text = Path(path or HERE / METHOD_FILE).read_text(encoding="utf-8")
    blocks = re.findall(r"^```[^\n]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError("the method file must hold exactly one fenced block")
    values = {}
    for line in blocks[0].splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, raw = (part.strip() for part in line.partition("="))
        value = json.loads(raw) if sep else None
        if (name in values or type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"bad method line: {line!r}")
        values[name] = value
    if set(values) != set(METHOD_KEYS):
        raise ValueError("method keys differ from check.py's list")
    return values


# ---- bundles -------------------------------------------------------------

def _npy(raw, name):
    if raw[:6] != b"\x93NUMPY" or len(raw) < 12 or raw[6] not in (1, 2, 3):
        raise BundleError(f"{name}: not a .npy member")
    size, start = (struct.unpack("<H", raw[8:10])[0], 10) if raw[6] == 1 else (
        struct.unpack("<I", raw[8:12])[0], 12)
    match = NPY_HEADER.fullmatch(raw[start:start + size].decode("latin-1").strip())
    if not match:
        raise BundleError(f"{name}: unreadable .npy header")
    descr, fortran, shape_text = match.groups()
    if descr not in NPY_ITEMSIZE or fortran != "False":
        raise BundleError(f"{name}: dtype {descr} refused (only uint8 and uint16, C order)")
    shape = tuple(int(part) for part in shape_text.replace(" ", "").split(",") if part)
    body = raw[start + size:]
    if len(body) != math.prod(shape) * NPY_ITEMSIZE[descr]:
        raise BundleError(f"{name}: size does not match its header")
    return descr, shape, body


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse_metadata(raw):
    def refuse_constant(_):
        raise BundleError("metadata holds a non-finite number")
    try:
        meta = json.loads(raw.decode("utf-8"), parse_constant=refuse_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BundleError("metadata is not JSON") from None
    if type(meta) is not dict or type(meta.get("schema")) is not int or meta["schema"] != 1:
        raise BundleError("metadata schema must be 1")
    frames = meta.get("frames")
    rgb = frames.get("rgb") if type(frames) is dict else None
    session = meta.get("session")
    if type(rgb) is not dict or type(session) is not str or not session:
        raise BundleError("metadata needs a session and an rgb frame")
    sequence, exposure = rgb.get("sequence"), rgb.get("exposure_duration_seconds")
    t_host, t_device = rgb.get("host_monotonic_timestamp_seconds"), rgb.get("device_timestamp_seconds")
    if type(sequence) is not int or sequence < 0 or not _finite(t_host) or not _finite(t_device):
        raise BundleError("rgb sequence or timestamp fields missing")
    if exposure is not None and not _finite(exposure):
        raise BundleError("rgb exposure must be a number or null")
    return {"session": session, "rgb_seq": sequence, "t_host": float(t_host),
            "t_device": float(t_device), "exposure": exposure}


def read_bundle(data, with_rgb=False):
    """Return (metadata, rgb_nv12 bytes or None) from one saved bundle."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            def member(name, limit):
                if archive.getinfo(name).file_size > limit:
                    raise BundleError(f"{name} is too large")
                return _npy(archive.read(name), name)
            descr, shape, body = member("metadata_json.npy", 1 << 20)
            if descr != "|u1" or len(shape) != 1:
                raise BundleError("metadata_json must be 1-D uint8")
            meta = parse_metadata(body)
            rgb = None
            if with_rgb:
                descr, shape, rgb = member("rgb_nv12.npy", 1 << 21)
                if descr != "|u1" or shape != NV12_SHAPE:
                    raise BundleError("rgb_nv12 must be 540x640 uint8 (640x360 NV12)")
    except (zipfile.BadZipFile, KeyError, EOFError, zlib.error) as exc:
        raise BundleError(f"bad bundle archive: {type(exc).__name__}") from None
    return meta, rgb


def y_plane(nv12, step):
    """Every step-th pixel of the Y plane in both directions (160x90 for step 4)."""
    return b"".join(nv12[row * Y_WIDTH:(row + 1) * Y_WIDTH:step] for row in range(0, Y_HEIGHT, step))


# ---- slate fit -------------------------------------------------------------

def pattern_lit(tau, m):
    second = math.floor(tau)
    k = int(second % m["slate_period_s"])
    if k == m["slate_marker_second"]:
        return False
    start = m["slate_phase_step_s"] * k
    return start <= tau - second < start + m["slate_flash_s"]


def change_distance(tau, m):
    """Distance from phone time tau to the nearest switch between white and black."""
    best = math.inf
    for second in range(math.floor(tau) - 1, math.floor(tau) + 2):
        k = int(second % m["slate_period_s"])
        if k != m["slate_marker_second"]:
            rise = second + m["slate_phase_step_s"] * k
            best = min(best, abs(tau - rise), abs(tau - rise - m["slate_flash_s"]))
    return best


def percentile(values, pct):
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def wrap(value, period):
    return (value + period / 2) % period - period / 2


def fit_slate(frames, m):
    """Fit one attempt. frames: [(t_host, y_plane bytes)] in frame order."""
    result = {"frames": len(frames), "readable": False, "reason": None, "flashes": 0,
              "screen_pixels": 0, "windows": [], "window_count": 0, "width_s": None, "c_s": None}
    if len(frames) < 2:
        return dict(result, reason="no_screen")
    count = len(frames)
    stds = []
    for column in zip(*(y for _, y in frames)):
        total = sum(column)
        stds.append(max(0.0, sum(map(operator.mul, column, column)) / count - (total / count) ** 2) ** 0.5)
    largest = max(stds)
    if largest <= 0:
        return dict(result, reason="no_screen")
    screen = [i for i, value in enumerate(stds) if value >= m["screen_std_fraction"] * largest]
    pick = operator.itemgetter(*screen) if len(screen) > 1 else (lambda y: (y[screen[0]],))
    means = [sum(pick(y)) / len(screen) for _, y in frames]
    low = percentile(means, m["class_percentile_low"])
    high = percentile(means, m["class_percentile_high"])
    result["screen_pixels"] = len(screen)
    if high <= low:
        return dict(result, reason="no_screen")
    classes = []
    for mean in means:
        x = (mean - low) / (high - low)
        classes.append(True if x > m["lit_above"] else False if x < m["dark_below"] else None)
    flashes, inside = 0, False
    for lit in classes:
        if lit is True and not inside:
            flashes, inside = flashes + 1, True
        elif lit is False:
            inside = False
    result["flashes"] = flashes
    if flashes < m["min_flashes"]:
        return dict(result, reason="few_flashes")
    period, grid, eps = m["slate_period_s"], m["fit_grid_s"], m["epsilon_s"]
    constrained = [(t, lit) for (t, _), lit in zip(frames, classes) if lit is not None]
    steps = round(period / grid)
    consistent = []
    for j in range(steps):
        c = -period / 2 + j * grid
        for t, lit in constrained:
            tau = t - c
            if change_distance(tau, m) >= eps and pattern_lit(tau, m) != lit:
                break
        else:
            consistent.append(j)
    if not consistent:
        return dict(result, reason="no_consistent_offset")
    runs = []
    for j in consistent:
        if runs and j == runs[-1][1] + 1:
            runs[-1][1] = j
        else:
            runs.append([j, j])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == steps - 1:
        runs[-1][1] = runs.pop(0)[1] + steps
    windows = [[round(-period / 2 + a * grid, 6), round(-period / 2 + b * grid, 6)] for a, b in runs]
    result.update(readable=True, windows=windows[:5], window_count=len(windows))
    if len(windows) == 1:
        result["width_s"] = round(windows[0][1] - windows[0][0], 6)
        result["c_s"] = round(wrap((windows[0][0] + windows[0][1]) / 2, period), 6)
    return result


# ---- reading a take ------------------------------------------------------------

def read_take(take_dir, m, out=None):
    try:
        take = json.loads((take_dir / "take.json").read_text(encoding="utf-8"))
        with open(take_dir / "frames.csv", newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        slate_windows = [(float(a["pc_start_s"]), float(a["pc_end_s"])) for a in take["slates"]]
        cues = [(c["cue"], float(c["pc_mono_s"])) for c in take["cues"]]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("take.json or frames.csv is missing or malformed") from None
    if type(take) is not dict or take.get("schema") != TAKE_SCHEMA or not cues:
        raise Refused(f"take.json schema is not {TAKE_SCHEMA}")
    method_sha = file_sha256(HERE / METHOD_FILE)
    if take.get("method") != {"file": METHOD_FILE, "sha256": method_sha}:
        raise Refused(f"take was recorded under a different CHECK file than {METHOD_FILE} "
                      f"({method_sha[:12]})")
    step, thumb_seconds = int(m["slate_pixel_step"]), -1
    frames, bad_files, seen = [], [], set()
    if (file_sha256(take_dir / "frames.csv") != (take.get("sha256") or {}).get("frames.csv")
            or len(rows) != (take.get("counts") or {}).get("kept")):
        bad_files.append("frames.csv")
    for count, row in enumerate(rows, 1):
        if out and count % 500 == 0:
            print(f"read {count} of {len(rows)} frames", file=out, flush=True)
        name = row.get("file", "")
        path = take_dir / name
        try:
            if not re.fullmatch(r"frames/[0-9]{6}-[0-9]+\.npz", name):
                raise BundleError("bad file name")
            data = path.read_bytes()
            meta, rgb = read_bundle(data, with_rgb=True)
            n, recv, recv_unix = int(row["n"]), float(row["recv_pc_mono_s"]), float(row["recv_pc_unix"])
            ok = (hashlib.sha256(data).hexdigest() == row["npz_sha256"]
                  and int(row["rgb_seq"]) == meta["rgb_seq"]
                  and float(row["t_host_s"]) == meta["t_host"]
                  and float(row["t_device_s"]) == meta["t_device"])
        except (OSError, ValueError, KeyError, TypeError):
            bad_files.append(name)
            continue
        if not ok:
            bad_files.append(name)
        rgb_sha = hashlib.sha256(rgb).hexdigest()
        frame = dict(meta, n=n, file=name, recv=recv, recv_unix=recv_unix, bytes=len(data),
                     rgb_sha=rgb_sha, stale=rgb_sha in seen, y=None)
        seen.add(rgb_sha)
        t_rel = meta["t_host"] - (frames[0]["t_host"] if frames else meta["t_host"])
        in_slate = any(a <= recv <= b for a, b in slate_windows)
        if in_slate or math.floor(t_rel) > thumb_seconds:  # first frame at or after some k
            frame["y"] = y_plane(rgb, step)
        thumb_seconds = max(thumb_seconds, math.floor(t_rel))
        frames.append(frame)
    return take, rows, frames, bad_files


# ---- rules -------------------------------------------------------------------

def judge_slates(take, frames, m):
    report, judged = {}, {}
    for which in ("start", "end"):
        attempts = []
        for attempt in (a for a in take.get("slates", []) if a.get("slate") == which):
            window = [f for f in frames if attempt["pc_start_s"] <= f["recv"] <= attempt["pc_end_s"]]
            fit = fit_slate([(f["t_host"], f["y"]) for f in window if f["y"] is not None], m)
            if any(f["stale"] for f in window):
                fit.update(readable=False, reason="stale_frame")
            fit.update(attempt=attempt.get("attempt"), peek=attempt.get("peek"),
                       first_t_host=window[0]["t_host"] if window else None)
            attempts.append(fit)
        chosen = next((a for a in attempts if a["readable"]), None)
        ok = bool(chosen and chosen["window_count"] == 1
                  and round(chosen["width_s"], 9) <= m["max_window_s"])
        report[which] = {"attempts": attempts, "judged_attempt": chosen and chosen["attempt"], "pass": ok}
        judged[which] = chosen if ok else None
    return report, judged


def check_rules(take, rows, frames, bad_files, m):
    rules = {}
    rules["B1_complete"] = {"pass": take.get("complete") is True, "stop_reason": take.get("stop_reason")}
    sessions = {f["session"] for f in frames}
    health = [(take.get(k) or {}).get("body") or {} for k in ("health_before", "health_after")]
    health_sessions = [h.get("session") for h in health]
    rules["B2_session"] = {"pass": bool(frames) and len(sessions) == 1
                           and health_sessions == [next(iter(sessions))] * 2,
                           "bundle_sessions": len(sessions)}
    backwards = [b["n"] for a, b in zip(frames, frames[1:])
                 if not (b["rgb_seq"] > a["rgb_seq"] and b["t_host"] > a["t_host"]
                         and b["t_device"] > a["t_device"])]
    rules["B3_increasing"] = {"pass": bool(frames) and not backwards, "first_bad_n": backwards[:5]}
    d = [f["t_host"] - f["t_device"] for f in frames]
    steps = [abs(b - a) for a, b in zip(d, d[1:])]
    slope = residual = 0.0
    if len(frames) > 1:
        xs = [f["t_host"] for f in frames]
        mx, my = sum(xs) / len(xs), sum(d) / len(d)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, d)) / sxx if sxx else 0.0
        residual = max(abs(y - (my + slope * (x - mx))) for x, y in zip(xs, d))
    max_step = max(steps, default=0.0)
    rules["B4_two_clock"] = {"pass": bool(frames) and max_step <= m["max_two_clock_step_s"]
                             and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]
                             and residual <= m["max_two_clock_line_s"],
                             "max_step_s": max_step, "slope_ppm": slope * 1e6, "max_residual_s": residual}
    rules["B5_hashes"] = {"pass": bool(rows) and not bad_files, "bad_files": bad_files[:5],
                          "bad_count": len(bad_files)}
    report, judged = judge_slates(take, frames, m)
    rules["A_start_slate"] = report["start"]
    rules["A_end_slate"] = report["end"]
    start, end = judged["start"], judged["end"]
    elapsed = separation = None
    if start and end:
        delta = wrap(end["c_s"] - start["c_s"], m["slate_period_s"])
        elapsed = abs(delta) + (start["width_s"] + end["width_s"]) / 2
        separation = end["first_t_host"] - start["first_t_host"]
    rules["A_elapsed_bound"] = {"pass": elapsed is not None and round(elapsed, 9) <= m["elapsed_bound_s"],
                                "elapsed_bound_s": elapsed}
    rules["A_separation"] = {"pass": separation is not None and separation <= m["max_slate_separation_s"],
                             "separation_s": separation}
    return rules


# ---- cuts and the usable part ----------------------------------------------------

def mmss(seconds):
    seconds = max(0, int(math.floor(seconds + 1e-9)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def parse_spans(text, pad):
    """'01:10-01:20, 02:05' -> merged [start, end) cuts; 'none' -> []."""
    text = text.strip()
    if text.lower() == "none":
        return []
    spans = []
    for part in text.split(","):
        match = SPAN.fullmatch(part.replace(" ", ""))
        if not match:
            raise ValueError(f"cannot read {part.strip()!r}; use mm:ss-mm:ss or none")
        a = int(match[1]) * 60 + int(match[2])
        b = int(match[3]) * 60 + int(match[4]) if match[3] else a
        if b < a:
            raise ValueError(f"{part.strip()!r} ends before it starts")
        spans.append((a - pad, b + pad))
    return merge(spans)


def require_console(stdin):
    if not stdin.isatty():
        raise Refused("the second-person prompt needs an interactive console; "
                      "a script or an agent cannot answer it")


def ask_spans(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    require_console(stdin)
    while True:
        stdout.write("Second person in view? Type spans like 01:10-01:20, 02:05-02:07, or none: ")
        stdout.flush()
        line = stdin.readline()
        if not line:
            raise Refused("no answer to the second-person prompt")
        try:
            parse_spans(line, 0)
            return line.strip()
        except ValueError as exc:
            stdout.write(f"{exc}\n")


def cue_t(take, frames, name):
    cue = next((c for c in take.get("cues", []) if c.get("cue") == name), None)
    after = next((f for f in frames if cue and f["recv"] >= cue["pc_mono_s"]), None)
    return None if after is None else after["t_host"] - frames[0]["t_host"]


def describe(kind, start, end):
    return {"kind": kind, "start_t_s": round(start, 6), "end_t_s": round(end, 6),
            "start": mmss(start), "end": mmss(end), "seconds": round(end - start, 6)}


def gap_and_stale_cuts(frames, ts, m):
    gaps = [(a, b) for a, b in zip(ts, ts[1:]) if b - a > m["gap_cut_s"]]
    stale = [(t - m["stale_pad_s"], t + m["stale_pad_s"]) for f, t in zip(frames, ts) if f["stale"]]
    return ([describe("gap", a, b) for a, b in merge(gaps)]
            + [describe("stale", a, b) for a, b in merge(stale)])


def usable_segments(usable, cuts, frames, ts):
    """The usable part [begin, end) minus every cut, with the frames left in each piece."""
    if not usable:
        return []
    low, high = usable
    segments, cursor = [], low
    for a, b in merge([(c["start_t_s"], c["end_t_s"]) for c in cuts]) + [[high, high]]:
        a, b = min(max(a, low), high), min(max(b, low), high)
        if a > cursor:
            inside = [f["n"] for f, t in zip(frames, ts) if cursor <= t < a]
            segment = describe("usable", cursor, a)
            del segment["kind"]
            segments.append(dict(segment, frames=len(inside), first_n=inside[0] if inside else None,
                                 last_n=inside[-1] if inside else None))
        cursor = max(cursor, b)
    return segments


def write_png(path, width, height, pixels):
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    rows = b"".join(b"\x00" + pixels[r * width:(r + 1) * width] for r in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                           + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def write_review(take_dir, frames, ts, usable, m):
    """One contrast-stretched thumbnail per second of t_s, 10 to a row. Owner only."""
    review = take_dir / "review"
    review.mkdir(exist_ok=True)
    width = len(range(0, Y_WIDTH, int(m["slate_pixel_step"])))
    height = len(range(0, Y_HEIGHT, int(m["slate_pixel_step"])))
    cells = []
    for k in range(int(math.floor(ts[-1])) + 1):
        frame = frames[bisect.bisect_left(ts, k)]
        name = f"f{frame['n']:06d}.png"
        if not (review / name).exists():
            low, high = min(frame["y"]), max(frame["y"])
            table = bytes(min(255, max(0, v - low) * 255 // max(1, high - low)) for v in range(256))
            write_png(review / name, width, height, frame["y"].translate(table))
        cells.append(f'<figure><img src="{name}" alt=""><figcaption>{mmss(k)}</figcaption></figure>')
    span = f"{mmss(usable[0])} to {mmss(usable[1])}" if usable else "none"
    (review / "index.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>den take review</title><style>"
        "body{font:14px sans-serif;background:#222;color:#eee}main{display:grid;"
        "grid-template-columns:repeat(10,1fr);gap:4px}figure{margin:0}img{width:100%;"
        "image-rendering:pixelated}figcaption{text-align:center}</style>"
        f"<p>Owner only. Thumbnail k is the first frame at or after k s. Usable part: {span}. "
        "Type the first and last thumbnail of each span that shows any part of a second "
        "person (a hand or the edge of a body counts; if in doubt, cut).</p><main>"
        + "".join(cells) + "</main>\n", encoding="utf-8", newline="\n")
    return review / "index.html"


def open_review(path):
    webbrowser.open(Path(path).resolve().as_uri())


# ---- the check -------------------------------------------------------------------

def run_check(take_dir, *, ask=ask_spans, show=open_review, m=None, out=None, console=None):
    out, console = out or sys.stdout, console or sys.stdin
    take_dir = Path(take_dir).resolve()
    m = m or load_method()
    take, rows, frames, bad_files = read_take(take_dir, m, out)
    rules = check_rules(take, rows, frames, bad_files, m)
    failures = [name for name, rule in rules.items() if not rule["pass"]]
    ts = [f["t_host"] - frames[0]["t_host"] for f in frames]
    cuts = gap_and_stale_cuts(frames, ts, m)
    begin, hold = cue_t(take, frames, "begin"), cue_t(take, frames, "hold_phone_end")
    usable = (begin, hold) if begin is not None and hold is not None and hold > begin else None
    person = {"asked": False, "typed": None}
    if not failures:
        require_console(console)  # before any thumbnail is written or the browser opens
        review = write_review(take_dir, frames, ts, usable, m)
        print(f"A and B rules pass. Review page (owner only): {review}", file=out, flush=True)
        show(review)
        typed = ask()
        person = {"asked": True, "typed": typed}
        cuts += [describe("second_person", a, b) for a, b in parse_spans(typed, m["person_pad_s"])]
    segments = usable_segments(usable, cuts, frames, ts)
    usable_seconds = sum(s["seconds"] for s in segments)
    span = ts[-1] if ts and ts[-1] > 0 else None
    total_bytes = sum(f["bytes"] for f in frames)
    start, end = (rules["A_start_slate"], rules["A_end_slate"])
    judged = [next((a for a in s["attempts"] if a["readable"]), None) for s in (start, end)]
    verdict = "FAIL" if failures else "PASS"
    report = {
        "schema": SCHEMA, "take": take_dir.name, "verdict": verdict,
        "commissioning": take.get("commissioning") is True,
        "retained": verdict == "PASS" and take.get("commissioning") is False,
        "failures": failures, "rules": rules,
        "method": {"file": METHOD_FILE, "sha256": file_sha256(HERE / METHOD_FILE), "tolerances": m},
        "check_py_sha256": file_sha256(Path(__file__)),
        "take_json_sha256": file_sha256(take_dir / "take.json"),
        "cuts": cuts, "cut_count": len(cuts),
        "cut_seconds": round(usable[1] - usable[0] - usable_seconds, 6) if usable else 0.0,
        "usable": {"start_t_s": usable and round(usable[0], 6), "end_t_s": usable and round(usable[1], 6),
                   "segments": segments, "minutes": round(usable_seconds / 60, 3)},
        "second_person": person,
        "reported_only": {
            "approx_wall_start_utc": frames and time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(frames[0]["recv_unix"])) or None,
            "offset_c_mod_period_s": [j and j["c_s"] for j in judged],
            "frames": len(frames), "take_seconds": span,
            "frames_per_s": span and round((len(frames) - 1) / span, 3),
            "mb_per_s": span and round(total_bytes / span / 1e6, 3),
            "bytes_total": total_bytes,
            "bytes_per_frame_mean": round(total_bytes / len(frames)) if frames else None,
            "gap_count": sum(1 for c in cuts if c["kind"] == "gap")},
        "checked_at_unix": time.time()}
    number = 1 + max((int(p.stem.split("-")[1]) for p in take_dir.glob("check-*.json")
                      if re.fullmatch(r"check-[0-9]+", p.stem)), default=0)
    path = take_dir / f"check-{number}.json"
    with open(path, "x", encoding="utf-8", newline="\n") as target:
        json.dump(report, target, indent=1)
        target.write("\n")
    for label, rule in (("start", start), ("end", end)):
        for a in rule["attempts"]:
            state = "readable" if a["readable"] else a["reason"]
            print(f"{label} slate attempt {a['attempt']}: {state}, {a['flashes']} flashes, "
                  f"windows {a['windows']}, width {a['width_s']} s", file=out)
    bound = rules["A_elapsed_bound"]["elapsed_bound_s"]
    print(f"elapsed bound: {bound} s (limit {m['elapsed_bound_s']} s)", file=out)
    ro = report["reported_only"]
    print(f"frames: {ro['frames']}, {ro['frames_per_s']} frames/s, {ro['mb_per_s']} MB/s", file=out)
    for c in cuts:
        print(f"cut {c['kind']}: {c['start']} to {c['end']} (t_s {c['start_t_s']:.2f} to "
              f"{c['end_t_s']:.2f}, {c['seconds']:.2f} s)", file=out)
    print(f"cuts: {len(cuts)}, {report['cut_seconds']} s; usable: {len(segments)} segments, "
          f"{report['usable']['minutes']} min", file=out)
    print(f"{verdict}{': ' + ', '.join(failures) if failures else ''} -> {path.name}", file=out)
    return (0 if verdict == "PASS" else 1), report, path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check one den take under " + METHOD_FILE)
    parser.add_argument("take_dir")
    args = parser.parse_args(argv)
    try:
        return run_check(args.take_dir)[0]
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
