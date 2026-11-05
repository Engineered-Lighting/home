#!/usr/bin/env python3
"""Rung 7's owner tool (experiment E8). Standard library only; runs on the owner's PC.

    python -B tools\\den-photometry\\photometry.py --self-check
    python -B tools\\den-photometry\\photometry.py --new-day C:\\den-photometry --stage0-sweep C:\\den-light\\stage0-<date>\\sweep-<n>
    python -B tools\\den-photometry\\photometry.py --capture C:\\den-photometry\\day-<date> --point desk --illuminant bulbs --blinds closed --light "<what is on>" --camera http://<host>:8090
    python -B tools\\den-photometry\\photometry.py --score C:\\den-photometry --room-model <room_model.jsonl>

--self-check runs the known-card round trip with no camera and no data.
--new-day makes day-<yyyymmdd> with day.json (pinning a passing Stage 0 sweep's
meter-norm/v1) and an empty worksheet.csv. --capture holds the camera through
rung 8's POST /meter at auto's pick and at a shorter exposure, repeats the
first, releases, and adds the capture's row to the worksheet. --score pairs
every typed reading with its frames and writes the records into score-<n>.
It talks only to the camera, never to Home Assistant, and writes no lights.
The token is never printed or written.
"""
import argparse
import csv
import datetime
import io
import json
import math
import re
import signal
import statistics
import struct
import sys
import time
from pathlib import Path

import card  # puts tools/den-light on the path
import meter
import sweep

HERE = Path(__file__).resolve().parent
SCHEMAS = {"day": "den-photometry-day-1", "capture": "den-photometry-capture-1",
           "report": "den-photometry-report-1"}
WORKSHEET = "worksheet.csv"
COLUMNS = ("capture", "point", "illuminant", "blinds", "light", "captured_local",
           "E_before_lx", "E_after_lx", "card_box", "surface_box", "note")
FILLED = ("point", "illuminant", "blinds", "light")
BLINDS = ("open", "closed", "unknown")
DAY_NAME = re.compile(r"day-(20\d{6})")
CAPTURE_NAME = re.compile(r"capture-(\d+)")
SCORE_NAME = re.compile(r"score-(\d+)")
Refused = sweep.Refused


def code_sha256():
    files = {name: HERE / name for name in ("card.py", "photometry.py")}
    files.update({"den-light/" + name: card.DEN_LIGHT / name for name in ("meter.py", "sweep.py")})
    return {name: meter.file_sha256(path) for name, path in files.items()}


def local_text(unix_s):
    return datetime.datetime.fromtimestamp(unix_s).strftime("%Y-%m-%d %H:%M:%S")


# ---- folders ----------------------------------------------------------------------

def check_folder(path, token_file, what):
    """An existing absolute folder outside git and OneDrive, away from the token, on a disk with room."""
    path = Path(path)
    if not path.is_absolute():
        raise Refused(f"the {what} must be an absolute path, for example C:\\den-photometry")
    path = path.resolve()
    if not path.is_dir():
        raise Refused(f"the {what} {path} does not exist (README step 2 makes it)")
    if sweep.inside_checkout(path):
        raise Refused(f"the {what} is inside a git checkout; frames and readings never go into git")
    if sweep.in_onedrive(path):
        raise Refused(f"the {what} is inside OneDrive; frames and readings stay on this PC only")
    token = Path(token_file).resolve()
    if path == token.parent or path in token.parents:
        raise Refused(f"the {what} must not contain the private token file")
    if sweep.free_bytes(path) < sweep.MIN_FREE_BYTES:
        raise Refused("less than 1 GB free on the folder's drive")
    return path


def read_json(path, what):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"no readable {what}") from None
    if type(value) is not dict:
        raise Refused(f"{what} is not a JSON object")
    return value


# ---- the Stage 0 sweep and the day ----------------------------------------------------

def same_meter_norm(parameters, den_method):
    """meter-norm/v1's fixed numbers in the pinned parameters equal rung 8's METHOD block now."""
    return (parameters.get("white_balance_kelvin") == den_method["white_balance_kelvin"]
            and parameters.get("clip_limits") == list(meter.clip_limits(den_method))
            and parameters.get("weights") == [den_method["weight_r"], den_method["weight_g"], den_method["weight_b"]]
            and parameters.get("normalise_code") == den_method["normalise_code"])


def load_stage0(sweep_folder, den_method):
    """meter-norm/v1 from a passing Stage 0 sweep: {version, parameters, sha256, response_sha256}."""
    folder = Path(sweep_folder)
    if not folder.is_absolute() or not folder.is_dir():
        raise Refused("--stage0-sweep must be a sweep folder's absolute path, for example "
                      "C:\\den-light\\stage0-20261110\\sweep-1")
    raw_path = folder / "response.json"
    try:
        raw = raw_path.read_bytes()
    except OSError:
        raise Refused("no response.json in the Stage 0 sweep folder") from None
    response = read_json(raw_path, "Stage 0 response.json")
    if response.get("schema") != sweep.SCHEMAS["response"] or response.get("outcome") != "pass":
        raise Refused("the Stage 0 sweep did not pass (rung 7 needs a passing Stage 0 response.json)")
    norm = response.get("meter_norm")
    if type(norm) is not dict or norm.get("version") != meter.METER_NORM_VERSION:
        raise Refused(f"the Stage 0 response holds no {meter.METER_NORM_VERSION} parameters")
    parameters = norm.get("parameters")
    if type(parameters) is not dict or parameters.get("version") != meter.METER_NORM_VERSION:
        raise Refused("the Stage 0 parameters are not meter-norm/v1's")
    try:
        digest = meter.sha256_bytes(meter.canonical(parameters).encode())
    except (TypeError, ValueError):
        raise Refused("the Stage 0 parameters are not plain JSON numbers") from None
    if digest != norm.get("parameters_sha256"):
        raise Refused("the Stage 0 parameters do not hash what response.json recorded")
    curves = parameters.get("curves")
    if (type(curves) is not dict or set(curves) != set(meter.CHANNELS)
            or any(type(c) is not list or len(c) != 256 or not all(type(x) in (int, float) for x in c)
                   for c in curves.values())):
        raise Refused("the Stage 0 parameters need three curves of 256 numbers")
    for recheck in sorted(folder.glob("recheck-*.json")):
        if read_json(recheck, recheck.name).get("outcome") != "pass":
            raise Refused(f"{recheck.name} of this sweep did not pass")
    if response.get("method_stage0_block_sha256") != den_method.block_sha256:
        raise Refused("rung 8's Stage 0 block is not the one this sweep used")
    if not same_meter_norm(parameters, den_method):
        raise Refused("rung 8's METHOD no longer matches the sweep's meter-norm/v1 numbers")
    return {"version": parameters["version"], "parameters": parameters, "sha256": digest,
            "response_sha256": meter.sha256_bytes(raw), "sweep": str(folder.resolve())}


def ready(method, den_method):
    problems = card.ready_problems(method)
    if den_method.open:
        problems.append("rung 8's METHOD-V1 still has open lines: " + ", ".join(den_method.open))
    if problems:
        raise Refused("; ".join(problems))


def day_folders(root):
    return sorted((p for p in Path(root).iterdir() if p.is_dir() and DAY_NAME.fullmatch(p.name)),
                  key=lambda p: p.name)


def load_day(day, method, den_method):
    """day.json, checked against this method, rung 8's Stage 0 block and its own meter-norm/v1 parameters."""
    match = DAY_NAME.fullmatch(Path(day).name)
    if not match:
        raise Refused(f"{Path(day).name} is not a day folder (day-<yyyymmdd>)")
    record = read_json(Path(day) / "day.json", f"day.json in {Path(day).name}")
    if record.get("schema") != SCHEMAS["day"] or record.get("date") != match.group(1):
        raise Refused(f"{Path(day).name}\\day.json is not a den-photometry day for that date")
    if record.get("method_block_sha256") != method.block_sha256:
        raise Refused(f"{Path(day).name} was made under another METHOD-V1 block; the block is fixed before day 1")
    if record.get("den_light_method_block_sha256") != den_method.block_sha256:
        raise Refused(f"{Path(day).name} was made under another rung 8 Stage 0 block")
    norm = record.get("normalisation") or {}
    parameters = norm.get("parameters")
    if type(parameters) is not dict or meter.sha256_bytes(meter.canonical(parameters).encode()) != norm.get("sha256"):
        raise Refused(f"{Path(day).name}\\day.json's meter-norm/v1 parameters do not hash what it recorded")
    if not same_meter_norm(parameters, den_method):
        raise Refused("rung 8's METHOD no longer matches the day's meter-norm/v1 numbers")
    return record


def new_day(root, stage0_sweep, token_file, method, den_method, date=None):
    ready(method, den_method)
    root = check_folder(root, token_file, "photometry folder")
    date = date or datetime.date.today().strftime("%Y%m%d")
    if not re.fullmatch(r"20\d{6}", date):
        raise Refused("--date must be yyyymmdd")
    norm = load_stage0(stage0_sweep, den_method)
    for other in day_folders(root):
        record = load_day(other, method, den_method)
        if record["normalisation"]["sha256"] != norm["sha256"]:
            raise Refused(f"{other.name} pinned other meter-norm/v1 parameters; one photometry folder keeps one")
    folder = root / f"day-{date}"
    try:
        folder.mkdir()
    except FileExistsError:
        raise Refused(f"{folder.name} already exists") from None
    record = {"schema": SCHEMAS["day"], "date": date, "camera_id": method["camera_id"],
              "normalisation": {"version": norm["version"], "sha256": norm["sha256"],
                                "parameters": norm["parameters"]},
              "stage0_sweep": norm["sweep"], "stage0_response_sha256": norm["response_sha256"],
              "method_sha256": method.file_sha256, "method_block_sha256": method.block_sha256,
              "den_light_method_block_sha256": den_method.block_sha256, "code_sha256": code_sha256(),
              "written_pc_unix": time.time()}
    sweep.write_json(folder / "day.json", record)
    with open(folder / WORKSHEET, "x", encoding="utf-8", newline="") as out:
        csv.writer(out).writerow(COLUMNS)
    return folder


# ---- one capture ----------------------------------------------------------------------

def capture_plan(t_pick_us, iso_pick, kelvin, method, den_method):
    """Hold 1 at auto's pick, hold 2 at the shorter time, hold 3 the repeat (METHOD-V1, A capture, step 4)."""
    holds = []
    for ratio in (1.0, method["invariance_ratio"]):
        t = round(t_pick_us * ratio)
        if t < 1 or t > den_method["max_exposure_us"] or iso_pick > den_method["iso_max"]:
            raise sweep.OutOfRange(len(holds) + 1, t, iso_pick, dark=t >= 1)
        holds.append({"exposure_us": t, "iso": iso_pick, "white_balance_kelvin": kelvin})
    return holds + [dict(holds[0])]


def plan_problem(t_pick_us, iso_pick, kelvin, method, den_method):
    try:
        capture_plan(t_pick_us, iso_pick, kelvin, method, den_method)
        return None
    except sweep.OutOfRange as exc:
        if not exc.dark:
            return (f"too bright: auto exposure is at {t_pick_us:.0f} us, so hold 2 would be under 1 us; "
                    "dim the den lights a little, then run the same line again")
        return (f"out of range: auto exposure is at {t_pick_us:.0f} us and ISO {iso_pick}, outside the camera's "
                f"hold range ({den_method['max_exposure_us']} us, ISO {den_method['iso_max']})")


def gain_results(kept, quantum_us):
    return [{"n": f["n"], "step": i, "pass": ok, "reason": why}
            for i, step in enumerate(kept, 1) for f in step["frames"]
            for ok, why in [meter.gain_agreement(f["report"], step["hold"], quantum_us)]]


def bmp(rgb):
    """A 640x360 24-bit BMP (bottom-up rows, BGR) of full-frame codes, for reading box corners in Paint."""
    rs, gs, bs = rgb
    width, height = meter.WIDTH, meter.HEIGHT
    rows = bytearray()
    for y in range(height - 1, -1, -1):
        at = y * width
        row = bytearray(width * 3)
        row[0::3] = bytes(bs[at:at + width])
        row[1::3] = bytes(gs[at:at + width])
        row[2::3] = bytes(rs[at:at + width])
        rows += row
    header = b"BM" + struct.pack("<IHHI", 54 + len(rows), 0, 0, 54) + struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    return header + bytes(rows)


class Capture(sweep.Sweep):
    """Rung 8's sweep machinery holding three exposures: auto's pick, a shorter one, and the repeat."""

    def __init__(self, camera, den_method, folder, *, method, kelvin, clock=time.monotonic, sleep=time.sleep,
                 say=print, wall=time.time):
        super().__init__(camera, den_method, folder, clock=clock, sleep=sleep, say=say)
        self.photometry, self.kelvin, self.wall = method, kelvin, wall
        self.first_frame_unix = None

    def save(self, step_index, hold, report, nv12):
        if step_index == 1 and self.first_frame_unix is None:
            self.first_frame_unix = self.wall()
        return super().save(step_index, hold, report, nv12)

    def collect(self, record):
        self.countdown()
        window = self.wait_still()
        exposures, isos = [r["exposure_us"] for r in window], [r["iso"] for r in window]
        if None in exposures or None in isos:
            raise sweep.Void("interrupted_or_errored_before_repeat", "an auto frame reported no exposure or ISO")
        t_pick, iso_pick = statistics.median(exposures), round(statistics.median(isos))
        record["auto_pick"] = {"exposure_us": t_pick, "iso": iso_pick, "frames": len(window),
                               "kelvin": window[-1]["kelvin"]}
        problem = plan_problem(t_pick, iso_pick, self.kelvin, self.photometry, self.method)
        if problem:
            raise Refused(problem)  # before any POST /meter: nothing held
        plan = capture_plan(t_pick, iso_pick, self.kelvin, self.photometry, self.method)
        self.say("Holding: 3 exposures.")
        for index, hold in enumerate(plan, 1):
            self.hold_step(index, hold)
        self.check_interrupt()

    def run(self, record):
        """The capture into its folder; returns the record written to capture.json.

        Raises Refused, with the new capture folder removed, when auto's pick is out of the
        camera's hold range; nothing was held then."""
        self.t0 = self.clock()
        self.jsonl = open(self.folder / "frames.jsonl", "x", encoding="utf-8", newline="\n")
        self.collecting = True
        previous = self.trap_ctrl_c()
        try:
            try:
                self.collect(record)
                self.complete = True
            except KeyboardInterrupt:
                raise sweep.Void("interrupted_or_errored_before_repeat", "Ctrl+C before the repeat") from None
            except (sweep.Void, Refused):
                raise
            except Exception as exc:
                raise sweep.Void("interrupted_or_errored_before_repeat",
                                 f"error before the repeat: {type(exc).__name__}: {exc}") from None
            finally:
                self.collecting = False  # from here, on every path, Ctrl+C waits for capture.json
                self.release = self.do_release()
                if self.posted:
                    self.say("Released: you may come back into view.")
            record["release"] = self.release
            if self.clock() - self.t0 > self.method["run_limit_s"]:
                raise sweep.Void("run_over_limit", "the release came more than the run limit after the countdown")
            first, repeat = self.kept[0]["frames"][0], self.kept[-1]["frames"][0]
            if not meter.agree(self.means(first["nv12"]), self.means(repeat["nv12"]),
                               self.method["noise_tolerance_codes"]):
                raise sweep.Void("repeat_mismatch", "the repeat disagrees with hold 1: the light or the scene changed")
            gains = gain_results(self.kept, self.method["exposure_quantum_us"])
            record.update(outcome="kept", gain_agreement=gains,
                          off_hold=not all(g["pass"] for g in gains))
            with open(self.folder / "preview.bmp", "xb") as out:
                out.write(bmp(meter.decode(first["nv12"], 1)))
        except Refused:
            self.remove_empty_folder()
            raise
        except sweep.Void as void:
            record.update(outcome="void", void_reason=void.reason, detail=void.detail)
            record.setdefault("release", self.release)
            record.update(self.delete_frames())
        except Exception as exc:  # after the data is complete: keep the frames
            record.update(outcome="error", detail=f"{type(exc).__name__}: {exc}")
            record.setdefault("release", self.release)
        finally:
            self.close_jsonl()
            try:
                if record.get("outcome") is not None:
                    unix = self.first_frame_unix
                    record.update(camera_session=self.session, captured_pc_unix=unix,
                                  captured_local=local_text(unix) if unix else None,
                                  holds=[{"step": i, **s["hold"], "generation": s["generation"],
                                          "frames": [f["n"] for f in s["frames"]]} for i, s in enumerate(self.kept, 1)],
                                  elapsed_s=self.clock() - self.t0, ctrl_c_ignored=self.ctrl_c_ignored)
                    sweep.write_json(self.folder / "capture.json", record)
            finally:
                if previous is not None:
                    signal.signal(signal.SIGINT, previous)
        return record


def check_labels(point, illuminant, blinds, light, method):
    if not card.NAME.fullmatch(point or ""):
        raise Refused("--point is a surface or meter-point name: lower case letters, digits and '-' (desk, sofa, "
                      "counter-1)")
    if illuminant not in method["illuminant_classes"]:
        raise Refused("--illuminant must be one of " + ", ".join(method["illuminant_classes"]))
    if blinds not in BLINDS:
        raise Refused("--blinds must be one of " + ", ".join(BLINDS))
    if not light or not light.strip() or len(light) > 200 or any(ch in light for ch in "\r\n"):
        raise Refused("--light is one line, up to 200 characters, saying which lights are on (no names)")


def new_capture_folder(day):
    numbers = [int(m.group(1)) for m in (CAPTURE_NAME.fullmatch(c.name) for c in day.iterdir()) if m]
    folder = day / f"capture-{max(numbers, default=0) + 1}"
    folder.mkdir()
    (folder / "frames").mkdir()
    return folder


def start_capture(day, camera, token_file, method, den_method, *, point, illuminant, blinds, light):
    """Every refusal before a capture folder exists (METHOD-V1, A capture, step 1)."""
    ready(method, den_method)
    check_labels(point, illuminant, blinds, light, method)
    day = check_folder(day, token_file, "day folder")
    record = load_day(day, method, den_method)
    kelvin = record["normalisation"]["parameters"]["white_balance_kelvin"]
    seen = sweep.camera_ready(camera, drop_in_installed=True)
    if seen["exposure_us"] is not None and seen["iso"] is not None:
        problem = plan_problem(seen["exposure_us"], seen["iso"], kelvin, method, den_method)
        if problem:
            raise Refused(problem)
    return day, record, new_capture_folder(day)


def append_row(day, values):
    """Add the capture's row to worksheet.csv (the tool's columns filled, the owner's left blank)."""
    path = day / WORKSHEET
    buffer = io.StringIO()
    csv.writer(buffer).writerow([values.get(c, "") for c in COLUMNS])
    row = buffer.getvalue()
    try:
        existing = path.read_bytes()
        prefix = "" if not existing or existing.endswith(b"\n") else "\r\n"
        with open(path, "a", encoding="utf-8", newline="") as out:
            out.write(prefix + row)
        return None
    except OSError:
        return row.strip()


def capture(day, camera, token_file, method, den_method, *, point, illuminant, blinds, light, say=print, **kwargs):
    day, day_record, folder = start_capture(day, camera, token_file, method, den_method, point=point,
                                            illuminant=illuminant, blinds=blinds, light=light)
    n = int(CAPTURE_NAME.fullmatch(folder.name).group(1))
    record = {"schema": SCHEMAS["capture"], "outcome": None, "void_reason": None, "detail": None,
              "capture": n, "day": day_record["date"], "point": point, "illuminant": illuminant,
              "blinds": blinds, "light": light, "camera_id": method["camera_id"],
              "normalisation": {k: day_record["normalisation"][k] for k in ("version", "sha256")},
              "method_sha256": method.file_sha256, "method_block_sha256": method.block_sha256,
              "den_light_method_block_sha256": den_method.block_sha256, "code_sha256": code_sha256(),
              "started_pc_unix": time.time()}
    say(f"Capture folder: {folder}")
    run = Capture(camera, den_method, folder, method=method,
                  kelvin=day_record["normalisation"]["parameters"]["white_balance_kelvin"], say=say, **kwargs)
    result = run.run(record)
    manual = None
    if result["outcome"] == "kept":
        manual = append_row(day, {"capture": n, "point": point, "illuminant": illuminant, "blinds": blinds,
                                  "light": light, "captured_local": result["captured_local"]})
    return folder, result, manual


# ---- the score -----------------------------------------------------------------------------

def read_worksheet(day):
    path = day / WORKSHEET
    try:
        raw = path.read_bytes()
    except OSError:
        raise Refused(f"no {WORKSHEET} in {day.name}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    delimiter = ";" if lines and ";" in lines[0] and "," not in lines[0] else ","
    rows = [r for r in csv.reader(lines, delimiter=delimiter) if any(cell.strip() for cell in r)]
    if not rows or [c.strip() for c in rows[0][:len(COLUMNS)]] != list(COLUMNS):
        raise Refused(f"{day.name}\\{WORKSHEET}'s first row must be the column names: " + ",".join(COLUMNS))
    out = {}
    for row in rows[1:]:
        cells = dict(zip(COLUMNS, (c.strip() for c in row + [""] * len(COLUMNS))))
        if not re.fullmatch(r"\d+", cells["capture"]):
            raise Refused(f"{day.name}\\{WORKSHEET}: capture {cells['capture']!r} is not a capture number")
        n = int(cells["capture"])
        if n in out:
            raise Refused(f"{day.name}\\{WORKSHEET}: capture {n} has two rows")
        out[n] = cells
    return out


def number(text, where):
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise Refused(f"{where}: {text!r} is not a number") from None
    if not math.isfinite(value):
        raise Refused(f"{where}: {text!r} is not a number")
    return value


def load_frames(folder):
    """Kept frames per step from frames.jsonl and frames\\, each frame's sha256 checked."""
    try:
        lines = (folder / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise Refused(f"no readable frames.jsonl in {folder.parent.name}\\{folder.name}") from None
    steps = {}
    for raw in lines:
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
            name = line["file"]
            if not re.fullmatch(r"frames/\d{4}\.nv12", name):
                raise ValueError(name)
            nv12 = (folder / name).read_bytes()
        except (ValueError, KeyError, TypeError, OSError):
            raise Refused(f"{folder.name}: frames.jsonl names a frame that is missing or unreadable") from None
        if meter.sha256_bytes(nv12) != line.get("sha256"):
            raise Refused(f"{folder.name}\\{name} does not hash what frames.jsonl recorded")
        steps.setdefault(line["step"], {"hold": line["hold"], "frames": []})["frames"].append(
            {"n": line["n"], "report": line["report"], "nv12": nv12})
    if sorted(steps) != [1, 2, 3]:
        raise Refused(f"{folder.name}: frames.jsonl does not hold holds 1, 2 and 3")
    return steps


def measure(day, n, row, cap, parameters, method, den_method):
    """One worksheet row and its capture: the card, surface and meter readings (METHOD-V1, The math)."""
    where = f"{day.name} capture {n}"
    for name in FILLED:
        if row[name] != str(cap.get(name)):
            raise Refused(f"{where}: the worksheet's {name} is {row[name]!r}, but the capture logged "
                          f"{cap.get(name)!r}; put it back")
    try:
        card_box, surface_box = card.parse_box(row["card_box"]), card.parse_box(row["surface_box"])
    except ValueError as exc:
        raise Refused(f"{where}: {exc}") from None
    before, after = number(row["E_before_lx"], where), number(row["E_after_lx"], where)
    if (before is not None or after is not None or surface_box) and not card_box:
        raise Refused(f"{where} has a reading or a surface box but no card_box; type the card's box")
    measured = {"id": f"{cap['day']}-{n}", "day": cap["day"], "order": (cap["day"], cap["captured_pc_unix"], n),
                "point": cap["point"], "illuminant": cap["illuminant"], "blinds": cap["blinds"],
                "light": cap["light"], "ts": cap["captured_pc_unix"], "card": None, "surface": None,
                "reading": card.meter_reading(before, after, method), "note": row["note"]}
    if not card_box:
        return measured
    steps = load_frames(day / f"capture-{n}")
    quantum = den_method["exposure_quantum_us"]
    decoded, gains_pass = {}, True
    for index in (1, 2):
        step = steps[index]
        decoded[index] = [{"report": f["report"], "rgb": meter.decode(f["nv12"], 1)} for f in step["frames"]]
        gains_pass &= all(meter.gain_agreement(f["report"], step["hold"], quantum)[0] for f in step["frames"])
    first = steps[1]["frames"][0]["report"]
    measured["exposure"] = {"time_ms": first["exposure_us"] / 1000 if first.get("exposure_us") else None,
                            "gain": first["iso"] / 100 if first.get("iso") else None}
    for name, box in (("card", card_box), ("surface", surface_box)):
        if box:
            measured[name] = card.box_reading(card.box_hold(decoded[1], box, parameters),
                                              card.box_hold(decoded[2], box, parameters), gains_pass, method)
            measured[name]["box"] = list(box)
    return measured


def read_room_model(path):
    """The RoomModel to update: one JSON object, or the last line of a JSON Lines file."""
    try:
        text = card.lf_text(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError):
        raise Refused("cannot read --room-model (a RoomModel as JSON or JSON Lines)") from None
    try:
        room = json.loads(text)
    except ValueError:
        lines = [line for line in text.split("\n") if line.strip()]
        try:
            room = json.loads(lines[-1]) if lines else None
        except ValueError:
            room = None
    if type(room) is not dict or type(room.get("version")) is not str or type(room.get("surfaces")) is not list \
            or type(room.get("cameras")) is not list:
        raise Refused("--room-model must hold a RoomModel with version, surfaces and cameras")
    return room


def previous_groups(root, n):
    if n <= 1:
        return None
    try:
        report = json.loads((root / f"score-{n - 1}" / "report.json").read_text(encoding="utf-8"))
        return {k: {"version": g["version"], "method": "meter", "value": g["k_L"]}
                for k, g in report["result"]["groups"].items() if g.get("version")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def jsonl(path, records):
    with open(path, "x", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def plain(value):
    """report.json's copy of a capture or result: no frames, no object references."""
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if k not in ("rgb", "nv12")}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else None
    return value


def score_root(root, room_model, token_file, method, den_method, location=None, say=print):
    """Pair every reading with its frames, run the checks, and write score-<n>\\ (METHOD-V1, The records)."""
    ready(method, den_method)
    root = check_folder(root, token_file, "photometry folder")
    days = day_folders(root)
    if not days:
        raise Refused("the photometry folder holds no day-<yyyymmdd> folder (README, --new-day)")
    room = read_room_model(room_model)
    captures, normalisation, skipped = [], None, []
    for day in days:
        record = load_day(day, method, den_method)
        if normalisation is None:
            normalisation = record["normalisation"]
        elif record["normalisation"]["sha256"] != normalisation["sha256"]:
            raise Refused(f"{day.name} pinned other meter-norm/v1 parameters")
        rows = read_worksheet(day)
        kept = {}
        for folder in sorted(day.iterdir()):
            match = CAPTURE_NAME.fullmatch(folder.name)
            if not (match and folder.is_dir()):
                continue
            try:
                cap = read_json(folder / "capture.json", "capture.json")
            except Refused:
                skipped.append({"folder": f"{day.name}\\{folder.name}", "reason": "no capture.json (interrupted)"})
                continue
            if cap.get("outcome") == "kept":
                kept[int(match.group(1))] = cap
            else:
                skipped.append({"folder": f"{day.name}\\{folder.name}", "reason": f"outcome {cap.get('outcome')}"})
        for n in sorted(set(rows) - set(kept)):
            raise Refused(f"{day.name}\\{WORKSHEET} has a row for capture {n}, which is not a kept capture")
        for n in sorted(kept):
            cap = kept[n]
            if n not in rows:
                raise Refused(f"{day.name}: capture {n} has no row in {WORKSHEET}; add it back")
            if (cap.get("schema") != SCHEMAS["capture"] or cap.get("method_block_sha256") != method.block_sha256
                    or cap.get("normalisation", {}).get("sha256") != normalisation["sha256"]):
                raise Refused(f"{day.name} capture {n} was taken under another method or meter-norm/v1")
            captures.append(measure(day, n, rows[n], cap, normalisation["parameters"], method, den_method))
    result = card.score(captures, method, [normalisation["version"]])
    surfaces = {s.get("surface_id") for s in room["surfaces"]}
    missing = sorted(set(result["reflectance"]) - surfaces)
    if missing:
        raise Refused("the RoomModel has no surface " + ", ".join(missing) + "; add it there or rename the point")
    if sum(c.get("camera_id") == method["camera_id"] for c in room["cameras"]) != 1:
        raise Refused(f"the RoomModel must hold exactly one camera {method['camera_id']}")
    numbers = [int(m.group(1)) for m in (SCORE_NAME.fullmatch(c.name) for c in root.iterdir()) if m]
    n = max(numbers, default=0) + 1
    by_id = {c["id"]: c for c in captures}
    norm = {"version": normalisation["version"], "sha256": normalisation["sha256"]}
    k_records = card.k_L_records(result, by_id, method, norm, n, previous_groups(root, n))
    rho_records = card.reflectance_records(result, by_id, method, norm, n)
    room_record = card.room_model_record(room, result, k_records, rho_records, method, norm, n)
    used = {c["id"] for g in result["selection"]["groups"].values()
            for c in g["held_out"] + ([g["fit"]] if g["fit"] else [])}
    used |= {c["id"] for caps in result["selection"]["reflectance"].values() for c in caps}
    states = []
    for cap in sorted((by_id[i] for i in used), key=lambda c: c["order"]):
        group = result["groups"].get(card.group_of(cap, method), {})
        k_L = group.get("k_L")
        states.append(card.light_state_record(cap, k_L, result["anchored"] and k_L is not None, method, norm,
                                              room_record["version"], location))
    folder = root / f"score-{n}"
    folder.mkdir()
    jsonl(folder / "calibration_record.jsonl", k_records + rho_records)
    jsonl(folder / "room_model.jsonl", [room_record])
    jsonl(folder / "light_state.jsonl", states)
    selection = result.pop("selection")
    for key, record in zip(sorted(result["groups"]), k_records):
        result["groups"][key]["version"] = record["version"]
    report = {"schema": SCHEMAS["report"], "score": n, "gate": result["gate"], "result": plain(result),
              "captures": plain(captures),
              "unused": [{"capture": c["id"], "reason": why} for c, why in selection["unused"]],
              "skipped_folders": skipped,
              "room_model_in": room["version"], "room_model_out": room_record["version"],
              "days": [d.name for d in days], "normalisation": norm,
              "method_sha256": method.file_sha256, "method_block_sha256": method.block_sha256,
              "den_light_method_block_sha256": den_method.block_sha256, "code_sha256": code_sha256(),
              "sun_from_location": location is not None, "written_pc_unix": time.time()}
    sweep.write_json(folder / "report.json", report)
    summarise(result, report, say)
    say(f"Wrote {folder}")
    return folder, report


def summarise(result, report, say):
    trip, inv, held = result["round_trip"], result["exposure_invariance"], result["held_out"]
    say(f"ROUND TRIP: {trip['result']} (k_L {trip['k_L']:.6f}, L {trip['L_card_cd_m2']:.6f} cd/m2, "
        f"E back {trip['E_back_lx']:.3f} lx)")
    worst = "none" if inv["worst_pct"] is None else f"{inv['worst_pct']:.2f}%"
    say(f"EXPOSURE INVARIANCE: {inv['result']} (worst {worst} of {inv['tolerance_pct']}%, {inv['n_boxes']} boxes)")
    for key, group in sorted(result["groups"].items()):
        if group["k_L"] is None:
            say(f"k_L [{key}]: no fit capture at the fit point on {group['first_day']}")
            continue
        say(f"k_L [{key}]: {group['k_L']:.6g} (cd/m2)/nc +/- {100 * group['u_rel']:.1f}% from capture {group['fit']}")
        for p in group["points"]:
            mark = "ok" if p["pass"] else "OUT"
            say(f"  {p['capture']} {p['point']} ({p['illuminant']}): meter {p['E_meter_lx']:.1f} lx, camera "
                f"{p['E_hat_lx']:.1f} lx, error {p['error_lx']:+.1f} +/- {p['u_error_lx']:.1f} lx  {mark}")
    rms = "" if held["residual_lx_rms"] is None else f", rms {held['residual_lx_rms']:.1f} lx"
    say(f"HELD OUT: {held['result']} ({held['n_points']} points{rms}, tolerance {held['tolerance_lx']} lx)")
    for line in held["missing"]:
        say(f"  missing: {line}")
    for surface, rho in sorted(result["reflectance"].items()):
        say(f"REFLECTANCE {surface}: {rho['rho']:.3f} +/- {100 * rho['u_rel']:.1f}% ({rho['n_captures']} capture(s))")
    for line in result["reflectance_missing"]:
        say(f"REFLECTANCE missing: {line}")
    for item in report["unused"]:
        say(f"NOT USED {item['capture']}: {item['reason']}")
    say(f"RUNG 7 GATE: {report['gate'].upper()}")


# ---- main ---------------------------------------------------------------------------------------

def main(argv=None, *, say=print):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-check", action="store_true")
    group.add_argument("--new-day", metavar="PHOTOMETRY_FOLDER")
    group.add_argument("--capture", metavar="DAY_FOLDER")
    group.add_argument("--score", metavar="PHOTOMETRY_FOLDER")
    parser.add_argument("--stage0-sweep", help="for --new-day: a passing Stage 0 sweep folder")
    parser.add_argument("--date", help=argparse.SUPPRESS)
    parser.add_argument("--point", help="for --capture: where the card and meter are (desk, sofa, counter-1)")
    parser.add_argument("--illuminant", help="for --capture: bulbs, daylight or mixed")
    parser.add_argument("--blinds", help="for --capture: open, closed or unknown")
    parser.add_argument("--light", help="for --capture: which lights are on, in your words")
    parser.add_argument("--camera", help="http://<host>:8090, or http://127.0.0.1:18090 through the tunnel")
    parser.add_argument("--room-model", help="for --score: the RoomModel (JSON or JSON Lines) to update")
    parser.add_argument("--latitude", type=float, help="for --score, optional: for the sun's position; never stored")
    parser.add_argument("--longitude", type=float, help="for --score, optional: east positive")
    parser.add_argument("--token-file", default=sweep.DEFAULT_TOKEN_FILE)
    args = parser.parse_args(argv)
    try:
        method = card.load_method()
        if args.self_check:
            trip = card.round_trip(method)
            say(f"ROUND TRIP {trip['result'].upper()}: C_card {trip['C_card_nc']} nc, rho_card {trip['rho_card']}, "
                f"E_meter {trip['E_meter_lx']} lx -> k_L {trip['k_L']:.6f} (cd/m2)/nc, L {trip['L_card_cd_m2']:.6f} "
                f"cd/m2, E back {trip['E_back_lx']:.3f} lx; {trip['normalisation_version']}")
            if method.open:
                say("METHOD-V1 open lines (a day, a capture and a score refuse until they are closed): "
                    + ", ".join(method.open))
            return 0 if trip["result"] == "pass" else 1
        den_method = meter.load_method()
        if args.new_day:
            if not args.stage0_sweep:
                raise Refused("--new-day needs --stage0-sweep")
            folder = new_day(Path(args.new_day), Path(args.stage0_sweep), args.token_file, method, den_method,
                             args.date)
            say(f"Made {folder} with day.json and an empty {WORKSHEET}.")
            return 0
        if args.score:
            if not args.room_model:
                raise Refused("--score needs --room-model")
            if (args.latitude is None) != (args.longitude is None):
                raise Refused("give both --latitude and --longitude, or neither")
            location = None
            if args.latitude is not None:
                if not (-90 <= args.latitude <= 90 and -180 <= args.longitude <= 180):
                    raise Refused("--latitude must be within -90..90 and --longitude within -180..180")
                location = (args.latitude, args.longitude)
            _, report = score_root(Path(args.score), args.room_model, args.token_file, method, den_method,
                                   location, say=say)
            return 0 if report["gate"] == "pass" else 1
        for name in ("point", "illuminant", "blinds", "light", "camera"):
            if getattr(args, name) is None:
                raise Refused(f"--capture needs --{name}")
        camera = sweep.Camera(args.camera, sweep.read_token(args.token_file))
        folder, record, manual = capture(Path(args.capture), camera, args.token_file, method, den_method,
                                         point=args.point, illuminant=args.illuminant, blinds=args.blinds,
                                         light=args.light, say=say)
    except card.MethodError as exc:
        say(f"Refused: METHOD-V1 cannot be read: {exc}. This command changed nothing.")
        return 2
    except Refused as exc:
        say(f"Refused: {exc}. This command changed nothing.")
        return 2
    if record["outcome"] == "void":
        gone = ("The frames were deleted." if record["frames_deleted"] else
                "Could not delete " + " and ".join(record["frames_left"]) + f" in {folder}: delete them by hand.")
        say(f"VOID ({record['void_reason']}): {record['detail']}. {gone} Run the same line again.")
        return 1
    if record["outcome"] == "error":
        say(f"ERROR after the data was complete: {record['detail']}. Tell the agent.")
        return 1
    say(f"KEPT: capture {record['capture']} at {record['point']}. Read the meter now (E_after_lx), then type both "
        f"readings and the card's box into {WORKSHEET} (README step 7). Preview: {folder / 'preview.bmp'}")
    if record["off_hold"]:
        say("WARNING: some frames were not at the held settings; this capture will not count. Run it again.")
    if manual:
        say(f"Could not add the row to {WORKSHEET} (is it open in Excel?). Close it and add this line by hand:")
        say(manual)
    return 0


if __name__ == "__main__":
    sys.exit(main())
