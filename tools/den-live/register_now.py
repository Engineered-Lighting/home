#!/usr/bin/env python3
"""Step 2 of the live den view: register the den camera to your scan, and qualify it. The owner's paste. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py --auto
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py --auto --again
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py --again

Registration v2, with REGISTER-LIVE-20261113.md as its method. The first line asks for your two tapes, records a new
take and starts the clicks and scan picks afresh. --again keeps the newest take, its tapes, your clicks and your scan
picks, so after a refusal you change only what it named and go on. It uses only a whole take recorded with this
method file, or one a v2 receipt names: a take stopped part way, or refused with fewer than 10 frames, keeps nothing,
and a v1 take or its clicks never come back. After a camera release changes its depth correction, --again stops: paste
without it, for a new take. --packs <folder> uses those content packs, for the scan page and the held-out check, in
place of the workstation's own.

--auto registers with no clicks and no scan page: steps 6 and 7 are done for you (below). With --again it reuses the
newest whole take and its tapes, as without. --sofa-mark <path> (with --auto) reads your sofa mark, a
home-registered-sofa-target-1 record, so one check lands on fixed structure near the sofa; its numbers never print.

1. Two tapes, typed at the console: the lens's height above the floor, and the lens to the facing wall, square. They're
   kept privately only: beside the take (<take>.tapes.json) as soon as it's recorded, and in the receipt. Nothing
   prints them. --again reads them from beside the take, however the paste that typed them ended.
2. A 20 s take of the empty den. It speaks the cues: leave the camera's view, recording, done. It reads the camera's
   /frame.npz about 5 times a second with den-take's own client and token file, keeps each bundle byte for byte with
   den-take's TakeWriter, and saves the camera's /calibration reply. Everything goes under
   <data root>\\private\\register\\. The data root is DEN_LIVE_HOME, or else %USERPROFILE%\\den-live. Agents never open
   that folder.
3. The source check: the camera service corrects depth itself (x0.889) and says so in every frame's depth_correction
   record. Any frame that doesn't say applied stops the paste here.
4. The depth check, on the take's per-pixel median depth: the floor plane and the facing wall's plane by RANSAC, each
   against its tape, as signed percentages. More than 5 points of bend between them: STOP, the depth bends more than a
   scale can fix. A mean within 1.5%: no scale. Otherwise den-live's depth_scale = 1 / (1 + mean), within 0.90-1.10,
   or STOP.
5. The median copy, <take>-x<scale>-m: every bundle of the take, its depth_mm the take's per-pixel median of valid
   depth (valid in at least half the frames, else 0) times depth_scale, every other byte the same. den-register reads
   one frame of it, so the clicks and the fit see the median, not one frame.
6. den-register's own click page (unedited, at the den simulation's pin): nine features on the camera's picture, f1-f6
   to fit and c1-c3 to check, on fixed structure. Then the edge guard: a click whose 7 x 7 depth window spreads
   (p90 - p10, about the window's own plane) over max(4 cm, 2% of its depth) sits on a depth edge, and the page opens
   again for it, at most twice.
7. The scan page, on 127.0.0.1: Home's own code on your scan. Click the same nine there. Home's display-mesh picker
   gives each one's room position, written to the worksheet as measured_with scan-pick.
6-7 with --auto: the active scan's points.ply and floor.json. Tilt and height from the camera's own floor plane, put on
   the scan's floor; yaw all the way round and x, y over the den floor's box (0.5 m wider), coarse to fine on a distance
   grid of the scan above its floor, then point-to-plane IRLS. The best must match clearly more of the camera's view
   than the best pose elsewhere, or the paste stops in one sentence; so it does with no floor in the take or the scan.
   Then nine points on stable structure (within 3 cm of the scan at that pose, flat 7 x 7 windows, valid median
   depth), six fit spread across the picture and in height and three checks apart from them, written as clicks and as
   measured_with auto room positions (the nearest scan point), so steps 8-11 and den-register run unchanged.
8. The distance check: all 36 pair distances, camera against scan. A rigid fit keeps distances, so a feature whose
   median difference over its 8 pairs is over den-register's gate (the method file's gate_m, 0.15 m) is a wrong click
   or pick that no fit could pass. It's named, worst first, in centimetres, and the paste stops; --again keeps
   everything and opens both pages again. One over 6 cm but within the gate is only noted, and the paste goes on.
9. den-register's fit at the method's gate, 0.15 m. Then register_now's own rule: each sofa check, c1-c3, within
   0.10 m (with --auto, each automatic check). The features' own scale (Umeyama) is printed above the summary as a
   diagnostic.
10. The held-out take, after a pass: leave the view again, 20 s. The scan's points.ply on stable surfaces (the floor,
    within 5 cm of z = 0, and everything above 1.8 m), z-buffered into the left camera at the new registration, against
    that take's median depth times depth_scale. The scan's floor is judged only where the room still shows it: a pixel
    more than 15 cm above the take's own floor plane is covered, and one whose point falls outside the den's walkable
    polygon (floor.json, holes excluded) is outside; both are counted, neither is judged. A pass: at least 75% within
    noise, max(3 cm, 2 sigma(z)), and no 1 m range bin with a median error over 10 cm, the floor and the upper walls
    each.
11. The receipt, den-live-register-now-2, and the summary block. QUALIFIED: yes is what den-live needs to start.

On a pass the scan page opens again (not with --auto), showing where the fit puts the camera and your nine features.
Tell an agent the summary block only: it's words and numbers, never a pose, a coordinate or a tape. The output is
ASCII.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import time
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sofa_check as sc  # noqa: E402  the data root, the pinned archives, the grab thread, Stop and say
from contracts import DEPTH_SCALE_RANGE, REGISTER_SCHEMA  # noqa: E402

say, Stop = sc.say, sc.Stop
METHOD_FILE = HERE / "REGISTER-LIVE-20261113.md"
PICK_PAGE = HERE / "scan_pick.mjs"
LIVE_BLOCK = "den-live-v2"
LIVE_KEYS = ("require_source_correction", "median_min_valid", "scale_none_pct", "scale_bend_max_pts",
             "floor_min_below_m", "floor_max_tilt_deg", "floor_band_m", "floor_min_share",
             "wall_min_above_floor_m", "wall_search_fraction", "wall_tape_band", "wall_min_axis_cos", "wall_max_floor_cos",
             "wall_min_points", "edge_min_m", "edge_fraction", "edge_rounds", "distance_max_m", "sofa_check_max_m",
             "held_floor_band_m", "held_wall_min_z_m", "held_noise_min_m", "noise_a_m", "noise_b_per_m",
             "held_within_min", "held_bin_m", "held_bin_max_m", "held_bin_min_pixels", "held_min_pixels",
             "held_covered_m", "own_scale_band", "auto_stable_m", "auto_min_share", "auto_runner_up_max",
             "auto_sofa_near_m")
SCAN_PICK = "scan-pick"
METHOD_LABEL = "scan-pick + fit"
POINTS = (("f1", "fit", "floor level, left: an inside corner where walls meet the floor, or skirting meets a door frame"),
          ("f2", "fit", "floor level, right: the same kind of corner, on the other side of the view"),
          ("f3", "fit", "mid height, left: the inside corner of a door or window frame, or a corner of a switch plate"),
          ("f4", "fit", "mid height, right: the same kind, on the other side of the view"),
          ("f5", "fit", "high, left: the top inside corner of a frame, or where two walls meet the ceiling"),
          ("f6", "fit", "high, right: the same kind, on the other side of the view"),
          ("c1", "check", "fixed structure beside or behind the sofa, not the sofa itself"),
          ("c2", "check", "fixed structure beside or behind the sofa, away from c1"),
          ("c3", "check", "fixed structure beside or behind the sofa, away from c1 and c2"))
IDS = tuple(pid for pid, _, _ in POINTS)
CHECKS = tuple(pid for pid, role, _ in POINTS if role == "check")
TAPES = (("lens_height_m", "the lens's height above the floor", 0.5, 3.0),
         ("facing_wall_m", "the lens to the facing wall, square to it", 1.0, 10.0))
WIDTH, HEIGHT = 640, 400
MIN_FRAMES = 10   # a take with fewer is refused, and never whole
RANSAC_TRIES, WALL_TRIES, SAMPLE_STEP, SEED = 400, 1500, 2, 0
HIDDEN = re.compile(r"^\s*camera at x |^Next: |^Or, if this take")   # den-register's lines for its own command line
# A den-register line about the camera (camera_below_floor's "the fit puts the camera -50.0 cm from the floor", the
# mount check's "... 2.50 m from the camera") would carry the pose: its numbers are withheld. So is a click's pixel.
CAMERA_WORDS = re.compile(r"camera", re.I)
NUMBER = re.compile(r"[-+]?[0-9]+(?:\.[0-9]+)?(?:\s*(?:cm|mm|m|degrees|deg)(?![A-Za-z]))?")
PIXEL = re.compile(r"\(\s*[-+]?[0-9.]+\s*,\s*[-+]?[0-9.]+\s*\)")


@dataclass(frozen=True)
class Timing:
    lead_s: float = 15.0      # after "leave the view", before recording
    window_s: float = 20.0    # the clear-room window
    poll_s: float = 0.2


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def cm(metres):
    return "%.1f" % (metres * 100)


def names(ids):
    ids = list(ids)
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " and " + ids[-1]


# ---- the method file ------------------------------------------------------------------------------------------

def read_live(text):
    """The method file's den-live-v2 block: every slot a number, none open. ValueError names what is wrong."""
    text = text.replace("\r\n", "\n")
    blocks = re.findall(r"^```" + LIVE_BLOCK + r"[ \t]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError("it must hold exactly one ```%s block" % LIVE_BLOCK)
    values = {}
    for raw in blocks[0].splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        name, _, value = (part.strip() for part in line.partition("="))
        try:
            number = float(value)
        except ValueError:
            raise ValueError("bad line in the %s block: %s" % (LIVE_BLOCK, ascii(line[:80]))) from None
        if name in values or not math.isfinite(number):
            raise ValueError("bad line in the %s block: %s" % (LIVE_BLOCK, ascii(line[:80])))
        values[name] = number
    if set(values) != set(LIVE_KEYS):
        raise ValueError("the %s block's slots differ: it needs %s" % (LIVE_BLOCK, ", ".join(LIVE_KEYS)))
    if values["require_source_correction"] not in (0, 1):
        raise ValueError("require_source_correction is 0 or 1")
    if not 0 < values["median_min_valid"] <= 1 or not 0 < values["held_within_min"] <= 1:
        raise ValueError("median_min_valid and held_within_min are shares, above 0 and at most 1")
    if not 0 < values["sofa_check_max_m"] <= 0.5 or not 0 < values["held_bin_max_m"] <= 0.5:
        raise ValueError("sofa_check_max_m and held_bin_max_m are metres, above 0 and at most 0.5")
    if values["edge_rounds"] < 1 or values["edge_rounds"] != int(values["edge_rounds"]):
        raise ValueError("edge_rounds is a whole number, at least 1")
    if not 0 < values["auto_min_share"] <= 1 or not 0 < values["auto_runner_up_max"] <= 1:
        raise ValueError("auto_min_share and auto_runner_up_max are shares, above 0 and at most 1")
    if not 0 < values["held_covered_m"] <= 0.5 or not 0 < values["auto_stable_m"] <= 0.10:
        raise ValueError("held_covered_m is metres above 0 and at most 0.5; auto_stable_m above 0 and at most 0.10")
    if not 0 < values["auto_sofa_near_m"] <= 5:
        raise ValueError("auto_sofa_near_m is metres, above 0 and at most 5")
    return values


def load_method(R, path=METHOD_FILE):
    """(den-register's Method, the den-live numbers) from the one file. A scored fit needs every slot closed."""
    m = R.method.load(path)
    if m.open:
        raise Stop("%s leaves slots open: %s" % (Path(path).name, ", ".join(sorted(m.open))))
    try:
        live = read_live(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop("%s: %s" % (Path(path).name, exc)) from None
    return m, live


# ---- the camera's own depth correction (oak-den, release pilot-20261111-depth) ------------------------------------

def source_correction(R, take_dir):
    """What each frame's metadata says about the depth correction the camera service applied: how many frames say
    applied, the factors and calibration hashes they name, and why any frame says it isn't."""
    take, rows = R.scene.read_take(take_dir)
    applied, factors, calibrations, why = 0, set(), set(), set()
    for row in rows:
        try:
            full = R.scene.read_bundle((Path(take_dir) / row["file"]).read_bytes())["full"]
        except R.scene.Refused as exc:
            why.add("a frame doesn't read (%s)" % exc.reason)
            continue
        record = full.get("depth_correction")
        if type(record) is not dict:
            why.add("no depth_correction record (a camera service from before the switch)")
        elif record.get("applied") is not True:
            why.add("applied is false: %s" % str(record.get("reason") or "no reason given")[:120])
        elif type(record.get("factor")) not in (int, float) or not 0.5 <= record["factor"] <= 1.5:
            why.add("applied, but its factor is not a number near 1")
        else:
            applied += 1
            factors.add(record["factor"])
            calibrations.add(str(record.get("calibration_sha256")))
    return {"frames": len(rows), "applied": applied, "factors": sorted(factors), "calibrations": sorted(calibrations),
            "why": sorted(why)}


def correction_of(found):
    """The one correction a source_correction record names, (factor, calibration_sha256): every frame applied, one
    factor and one calibration hash. None when it names no single one (no records, as on den-sim's renders, or a mix)."""
    if not isinstance(found, dict) or not found.get("frames") or found.get("applied") != found["frames"]:
        return None
    factors, calibrations = found.get("factors") or [], found.get("calibrations") or []
    return (factors[0], calibrations[0]) if len(factors) == 1 and len(calibrations) == 1 else None


def camera_correction(R, camera, wait, tries=5, poll_s=0.2):
    """The camera's depth correction now, as correction_of would name it for a take of it: one fresh /frame.npz, read
    as source_correction reads a take's frames. A camera release with a re-measured factor or another calibration
    changes it."""
    for _ in range(tries):
        try:
            status, body = camera.get("/frame.npz")
        except (OSError, R.take.http.client.HTTPException) as exc:
            raise Stop("the camera at %s:%s did not answer (%s)" % (camera.host, camera.port, type(exc).__name__)) from None
        if status in (401, 403):
            raise Stop("the camera refused the token")
        try:
            full = R.scene.read_bundle(body)["full"] if status == 200 else None
        except R.scene.Refused:
            full = None   # a frame that doesn't read: ask again
        if full is not None:
            record = full.get("depth_correction")
            if (type(record) is not dict or record.get("applied") is not True
                    or type(record.get("factor")) not in (int, float) or not 0.5 <= record["factor"] <= 1.5):
                return None
            return record["factor"], str(record.get("calibration_sha256"))
        wait(poll_s)
    raise Stop("the camera gave no frame to read its depth correction from")


def source_line(found, live):
    if not live["require_source_correction"]:
        return ("Depth correction at the camera: not required by this method file (%d of %d frames say applied)."
                % (found["applied"], found["frames"]))
    return "Depth corrected at the camera: x%g on all %d frames." % (found["factors"][0], found["frames"])


def check_source(R, take_dir, live, *, quiet=False):
    found = source_correction(R, take_dir)
    if live["require_source_correction"] and (found["applied"] != found["frames"] or len(found["factors"]) != 1):
        raise Stop("the camera's depth isn't corrected at its source on every frame: %d of %d say applied%s. Nothing "
                   "was clicked. Tell an agent this line"
                   % (found["applied"], found["frames"], "; " + "; ".join(found["why"]) if found["why"] else
                      "; they name %d different factors" % len(found["factors"])))
    if not quiet:
        say(source_line(found, live))
    return found


# ---- den-register and den-take, at the den simulation's pin -----------------------------------------------------

def den_register(tree):
    """den-register's modules from its pinned archive, its labels set in memory only, as den-sim's prepare.py does."""
    for sub in ("den-take", "den-register"):
        folder = str(Path(tree) / "tools" / sub)
        if folder not in sys.path:
            sys.path.insert(0, folder)
    import check
    import method
    import records
    import register
    import rig
    import scene
    import take
    import worksheet
    register.METHOD = METHOD_LABEL
    for tool in (SCAN_PICK, "auto"):
        if tool not in worksheet.TOOLS:
            worksheet.TOOLS = (*worksheet.TOOLS, tool)
    return SimpleNamespace(check=check, method=method, records=records, register=register, rig=rig, scene=scene,
                           take=take, worksheet=worksheet)


def scrub(line):
    """One of den-register's lines as the console may show it: None for its camera's-place and command-line lines,
    any line about the camera with its numbers withheld, and a click's pixel named, not given. Residuals, errors and
    counts pass as they are."""
    if HIDDEN.search(line):
        return None
    line = PIXEL.sub("(the click)", line)
    return NUMBER.sub("(withheld)", line) if CAMERA_WORDS.search(line) else line


class Lines:
    """den-register's own printing, passed to the console line by line, through scrub."""

    def __init__(self):
        self.part = ""

    def write(self, text):
        self.part += text
        while "\n" in self.part:
            line, self.part = self.part.split("\n", 1)
            line = scrub(line)
            if line is not None:
                say(line)
        return len(text)

    def flush(self):
        pass


# ---- 1. the tapes ---------------------------------------------------------------------------------------------------

def console_ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        raise Stop("no tapes were typed; paste the line again in PowerShell when you have them") from None


def ask_tapes(ask):
    """Your two tapes in metres, each asked up to three times. Never printed, never echoed back."""
    say("Two tapes, in metres, as on 2026-11-11. They're kept privately, beside the take and in the receipt; nothing "
        "prints them.")
    tapes = {}
    for key, words, low, high in TAPES:
        for _ in range(3):
            try:
                value = float(str(ask("Type %s in metres, then press Enter: " % words)).strip().replace(",", "."))
            except ValueError:
                value = None
            if value is not None and math.isfinite(value) and low <= value <= high:
                tapes[key] = value
                break
            say("That isn't %s in metres between %g and %g; type it again." % (words, low, high))
        else:
            raise Stop("the tapes weren't typed as metres; paste the line again when you have them")
    return tapes


def tapes_path(raw):
    """Where a take's tapes are kept: beside the take, in the private data root."""
    return raw.parent / (raw.name + ".tapes.json")


def keep_tapes(raw, tapes):
    """Keep the tapes beside their take as soon as it's recorded, so --again has them however the paste ends: a scan
    tab closed without Save, Ctrl+C and den-register's refusals write no receipt."""
    write_json(tapes_path(raw), {"take": raw.name, "tapes": tapes})


def tapes_of(tapes):
    """The two tapes from a kept record, or None unless both are metres in range."""
    if type(tapes) is dict and all(type(tapes.get(key)) in (int, float) and low <= tapes[key] <= high
                                   for key, _, low, high in TAPES):
        return {key: tapes[key] for key, _, _, _ in TAPES}
    return None


def known_tapes(work, raw):
    """The tapes typed for this take: kept beside it, else (a take from before they were) from an earlier paste's
    receipt. None when neither has them."""
    try:
        kept = json.loads(tapes_path(raw).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = None
    if type(kept) is dict and kept.get("take") == raw.name and tapes_of(kept.get("tapes")):
        return tapes_of(kept["tapes"])
    for path in sorted(work.glob("register-now-*.json"), key=receipt_number, reverse=True):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if receipt.get("raw_take") == raw.name and tapes_of(receipt.get("tapes")):
            return tapes_of(receipt["tapes"])
    return None


# ---- 2. a take of the empty den -----------------------------------------------------------------------------------

REGISTRATION_CUES = {"leave": "Registration take. Please leave the den, or step out of the camera's view. Recording "
                              "starts in %d seconds.",
                     "begin": "Recording the empty room.", "end": "Done. You can come back."}
HELD_OUT_CUES = {"leave": "Leave the view again. Recording the held-out take in %d seconds.",
                 "begin": "Recording the held-out take.", "end": "Done. You can come back."}


def record(R, camera, folder, timing, speak, wait, cues=REGISTRATION_CUES, script="den-live-register-clear-room",
           method=None):
    """A den-take-1 take of the empty den. Its cues begin and hold_phone_end bracket the window, so den-register's
    default frame is the window's middle. No slates: nobody is in view to hold one. take.json, written only once the
    window has closed, names the method file it was recorded under: --again uses no take from another method. It says
    complete only once the take has MIN_FRAMES frames; a shorter one (a slow link) is refused, and its take.json says
    complete false with the stop_reason, so --again never uses it either."""
    try:
        status, body = camera.get("/calibration")
    except (OSError, R.take.http.client.HTTPException) as exc:
        raise Stop("the camera at %s:%s did not answer (%s)" % (camera.host, camera.port, type(exc).__name__)) from None
    if status in (401, 403):
        raise Stop("the camera refused the token")
    if status != 200:
        raise Stop("the camera's /calibration answered %s" % status)
    folder.mkdir(parents=True)
    (folder / "camera-calibration.json").write_bytes(body)
    writer = R.take.TakeWriter(folder)
    grab = sc.Grab(R.take, camera, writer, timing.poll_s)
    doc = {"schema": R.take.SCHEMA, "take": folder.name, "script": script,
           "recorded_by": "tools/den-live/register_now.py", "camera": "http://%s:%s" % (camera.host, camera.port),
           "pc_clock": "time.perf_counter", "started_unix": time.time(), "ended_unix": None, "cues": [], "slates": [],
           "note": "No slates: the room is clear. The begin and hold_phone_end cues bracket the clear-room window.",
           "counts": None, "stop_reason": None, "complete": False, "sha256": None}
    if method:
        doc["method"] = {"file": method["file"], "sha256": method["sha256"]}

    def cue(name, text):
        doc["cues"].append({"cue": name, "text": text, "pc_mono_s": R.take.clock(), "pc_unix": time.time()})
        speak(text)

    grab.start()
    try:
        cue("leave", cues["leave"] % round(timing.lead_s))
        wait(timing.lead_s)
        start = R.take.clock()
        grab.since = start
        grab.recording.set()
        cue("begin", cues["begin"])
        wait(timing.window_s)
        grab.recording.clear()
        cue("hold_phone_end", cues["end"])
    finally:
        grab.halt.set()
        grab.join(10)
        writer.close()
    if grab.stop_reason:
        raise Stop(grab.stop_reason)
    kept = len(writer.index)
    doc["ended_unix"] = time.time()
    doc["counts"] = {"kept": kept, "errors": grab.errors, "sessions": len(set(grab.sessions.values()))}
    doc["sha256"] = {"frames.csv": sha256_bytes((folder / "frames.csv").read_bytes())}
    if kept < MIN_FRAMES:
        doc["stop_reason"] = "only %d frames came from the camera in %d s" % (kept, round(timing.window_s))
        write_json(folder / "take.json", doc)   # complete stays false: refused, never whole
        raise Stop(doc["stop_reason"])
    doc["complete"] = True
    write_json(folder / "take.json", doc)
    return kept


# ---- 4 and 5. the take median and its copy ------------------------------------------------------------------------

def median_plane(R, take_dir, min_valid):
    """(the per-pixel median of the take's valid depth in mm, float32 HEIGHT x WIDTH, 0 where fewer than min_valid of
    the frames have depth there; the intrinsics; how many frames) over every frame of the take."""
    import numpy as np
    _, rows = R.scene.read_take(take_dir)
    planes, k = [], None
    for row in rows:
        try:
            bundle = R.scene.read_bundle((Path(take_dir) / row["file"]).read_bytes())
        except R.scene.Refused:
            continue
        planes.append(np.frombuffer(bundle["depth"], dtype=np.uint16).reshape(HEIGHT, WIDTH))
        k = bundle["intrinsics"]
    if not planes:
        raise Stop("no frame of the take reads")
    stack = np.stack(planes)
    stack.sort(axis=0)   # the zeros (no depth) first, then each pixel's valid values in order
    n = len(planes)
    valid = np.count_nonzero(stack, axis=0)
    first = n - valid
    lo = np.minimum(first + np.maximum(valid - 1, 0) // 2, n - 1)
    hi = np.minimum(first + valid // 2, n - 1)
    plane = (np.take_along_axis(stack, lo[None], 0)[0].astype(np.float32)
             + np.take_along_axis(stack, hi[None], 0)[0].astype(np.float32)) / 2
    plane[valid < math.ceil(min_valid * n)] = 0
    return plane, k, n


def scaled_plane(plane, scale):
    """The median plane as the copy holds it: times scale, rounded to whole millimetres, 0 kept as 0."""
    import numpy as np
    return np.where(plane > 0, np.clip(np.rint(plane * scale), 1, 65535), 0).astype("<u2")


def rewrite_depth(R, data, depth):
    """The bundle with depth_mm.npy's values from depth (a function of the old values), its .npy header and every other
    member as they were, each with its own compression."""
    import numpy as np
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == "depth_mm.npy":
                descr, shape, body = R.check._npy(raw, info.filename)
                if descr != "<u2" or shape != (HEIGHT, WIDTH):
                    raise Stop("a depth plane is not %dx%d millimetres" % (WIDTH, HEIGHT))
                new = depth(np.frombuffer(body, dtype="<u2"))
                raw = raw[:len(raw) - len(body)] + np.asarray(new, dtype="<u2").tobytes()
            member = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            member.compress_type, member.external_attr = info.compress_type, info.external_attr
            target.writestr(member, raw)
    return out.getvalue()


def scale_bundle(R, data, scale):
    """The bundle with depth_mm.npy's values times scale (0 stays 0), everything else as it was."""
    import numpy as np
    return rewrite_depth(R, data, lambda depth: np.where(depth > 0, np.clip(np.rint(depth.astype(np.float64) * scale),
                                                                             1, 65535), 0))


def median_copy(R, source, target, plane, scale, method, frames_used):
    """Write <take>-x<scale>-m at target: every bundle with the median plane times scale as its depth. take.json says
    where it came from and what changed. It's written in <name>.tmp and renamed whole at the end, so a paste stopped
    part way (Ctrl+C, a frame that changed) leaves no copy that looks finished; the next paste clears what it left."""
    rows = list(csv.DictReader(io.StringIO((source / "frames.csv").read_text(encoding="utf-8"), newline="")))
    fields = list(rows[0].keys()) if rows else list(R.take.CSV_FIELDS)
    body = scaled_plane(plane, scale).reshape(-1)
    final, target = target, target.with_name(target.name + ".tmp")
    shutil.rmtree(target, ignore_errors=True)
    if final.exists() and not (final / "take.json").is_file():
        shutil.rmtree(final)   # a copy from before the .tmp folder, cut short: never read
    target.mkdir(parents=True)
    (target / "frames").mkdir()
    for row in rows:
        data = (source / row["file"]).read_bytes()
        if sha256_bytes(data) != row["npz_sha256"]:
            raise Stop("frame %s of the take changed since it was recorded" % row["n"])
        copy = rewrite_depth(R, data, lambda _depth: body)
        (target / row["file"]).write_bytes(copy)
        row["npz_sha256"], row["bytes"] = sha256_bytes(copy), str(len(copy))
    text = io.StringIO()
    writer = csv.DictWriter(text, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (target / "frames.csv").write_text(text.getvalue(), encoding="utf-8", newline="")
    doc = json.loads((source / "take.json").read_text(encoding="utf-8"))
    doc["take"] = final.name
    doc["derived"] = {"from_take": source.name, "from_frames_csv_sha256": sha256_bytes((source / "frames.csv").read_bytes()),
                      "depth_scale": scale, "median_of_frames": frames_used,
                      "changed": "depth_mm.npy in every bundle: the take's per-pixel median of valid depth (valid in at "
                                 "least half the frames, else 0), x depth_scale, rounded",
                      "method_file": method["file"], "method_sha256": method["sha256"]}
    doc["sha256"] = {"frames.csv": sha256_bytes((target / "frames.csv").read_bytes())}
    shutil.copyfile(source / "camera-calibration.json", target / "camera-calibration.json")
    write_json(target / "take.json", doc)
    target.rename(final)
    return len(rows)


# ---- 4. the depth check: floor and facing wall against the tapes --------------------------------------------------

def camera_points(plane, k, step=SAMPLE_STEP):
    """Camera-frame points (rectified left: +x right, +y down, +z forward), metres, of every step-th pixel with depth."""
    import numpy as np
    v, u = np.mgrid[0:HEIGHT:step, 0:WIDTH:step]
    z = plane[::step, ::step].astype(np.float64) / 1000.0
    ok = z > 0
    z, u, v = z[ok], u[ok].astype(np.float64), v[ok].astype(np.float64)
    return np.column_stack(((u - k["cx"]) * z / k["fx"], (v - k["cy"]) * z / k["fy"], z))


def floor_plane(points, live, tries=RANSAC_TRIES, seed=SEED):
    """The floor: among near-level planes RANSAC finds, the lowest whose support is at least half the best one's,
    refined by least squares on its inliers. Returns {height_m, share, normal, ...} or None."""
    import numpy as np
    low = points[points[:, 1] > live["floor_min_below_m"]]
    if len(low) < 100:
        return None
    up_cos, band = math.cos(math.radians(live["floor_max_tilt_deg"])), live["floor_band_m"]
    rng = np.random.default_rng(seed)
    found = []
    for _ in range(tries):
        a, b, c = low[rng.choice(len(low), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n = n / norm
        if n[1] > 0:
            n = -n   # up is -y in the camera's frame
        if -n[1] < up_cos:
            continue
        d = -float(n @ a)
        found.append((int((np.abs(low @ n + d) < band).sum()), d, n))
    if not found:
        return None
    best = max(count for count, _, _ in found)
    strong = [item for item in found if item[0] >= best / 2]
    lowest = max(d for _, d, _ in strong)
    # The lowest group of strong planes (a table top sits well above the floor), and the best-supported plane in it:
    # the group's lowest member is just the floor's noise band tilted the furthest.
    _, d, n = max((item for item in strong if item[1] >= lowest - 0.10), key=lambda item: item[0])
    for _ in range(3):   # least squares on the inliers, then the inliers of that, until it settles
        inliers = low[np.abs(low @ n + d) < band]
        centre = inliers.mean(axis=0)
        n = np.linalg.svd(inliers - centre, full_matrices=False)[2][-1]   # 3 x 3: never an N x N matrix
        if n[1] > 0:
            n = -n
        d = -float(n @ centre)
    share = float((np.abs(low @ n + d) < band).sum()) / len(low)
    return {"height_m": d, "share": share, "normal": [float(x) for x in n], "low_points": int(len(low)),
            "pitch_down_deg": math.degrees(math.asin(max(-1.0, min(1.0, -n[2])))),
            "roll_deg": math.degrees(math.atan2(n[0], -n[1]))}


def refine_plane(points, normal, distance):
    """The densest 1 cm layer near the plane, then a trimmed least-squares plane through it, three times: robust to
    skirting, a picture frame or a rug edge (depth_tape_check.py's refine)."""
    import numpy as np
    for _ in range(3):
        heights = points @ normal
        near = np.abs(heights - distance) < 0.10
        if near.sum() < 3:
            break
        counts, edges = np.histogram(heights[near], bins=np.arange(distance - 0.10, distance + 0.1001, 0.01))
        mode = edges[np.argmax(counts)] + 0.005
        layer = points[np.abs(heights - mode) < 0.015 + 0.006 * points[:, 2]]
        if len(layer) < 3:
            break
        centre = layer.mean(axis=0)
        fitted = np.linalg.svd(layer - centre, full_matrices=False)[2][2]
        normal = fitted if fitted @ normal > 0 else -fitted
        distance = float(normal @ centre)
    return normal, distance


def wall_plane(points, floor, wall_tape, live, tries=WALL_TRIES, seed=SEED):
    """The facing wall: among planes square to the floor and facing the camera, found by RANSAC among points well above
    the floor and at least wall_search_fraction of the wall tape ahead, only those within wall_tape_band of the tape's
    distance; of those, the farthest whose support is at least half the best one's, refined. A bookcase or wardrobe front
    in front of the wall can out-support it, but the wall is the farthest strong plane (floor_plane's rule, turned).
    Its perpendicular distance from the lens, or None."""
    import numpy as np
    nf = np.array(floor["normal"])
    above = points @ nf + floor["height_m"]
    candidates = points[(above > live["wall_min_above_floor_m"]) & (points[:, 2] > live["wall_search_fraction"] * wall_tape)]
    if len(candidates) < live["wall_min_points"]:
        return None
    tolerance = 0.015 + 0.006 * candidates[:, 2]
    near_tape = lambda d: abs(d / wall_tape - 1) <= live["wall_tape_band"]
    rng = np.random.default_rng(seed)
    found = []
    for _ in range(tries):
        a, b, c = candidates[rng.integers(len(candidates), size=3)]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n)
        if length < 1e-9:
            continue
        n = n / length
        if abs(n[2]) < live["wall_min_axis_cos"] or abs(n @ nf) > live["wall_max_floor_cos"]:
            continue
        d = float(n @ a)
        if d < 0:
            n, d = -n, -d
        if near_tape(d):
            found.append((int((np.abs(candidates @ n - d) < tolerance).sum()), d, n))
    if not found:
        return None
    best = max(count for count, _, _ in found)
    strong = [item for item in found if item[0] >= best / 2]
    farthest = max(d for _, d, _ in strong)
    # The farthest group of strong planes, and the best-supported plane in it: the group's farthest member is just the
    # wall's noise band tilted the furthest.
    _, d, n = max((item for item in strong if item[1] >= farthest - 0.10), key=lambda item: item[0])
    n, d = refine_plane(candidates[np.abs(candidates @ n - d) < 0.15], n, d)
    inliers = int((np.abs(candidates @ n - d) < 0.015 + 0.006 * candidates[:, 2]).sum())
    if inliers < live["wall_min_points"] or not near_tape(d):
        return None
    return {"distance_m": d, "normal": [float(x) for x in n], "inliers": inliers,
            "share": inliers / len(candidates)}


def scale_check(points, tapes, live):
    """The depth against the two tapes: floor %, wall %, the bend between them, and the rule's verdict."""
    floor = floor_plane(points, live)
    if floor is None or floor["share"] < live["floor_min_share"]:
        return {"result": "stop", "why": "the depth check can't find the floor in the camera's view"}
    wall = wall_plane(points, floor, tapes["facing_wall_m"], live)
    if wall is None:
        return {"result": "stop", "why": "the depth check can't find the facing wall within %.0f%% of your wall tape"
                                         % (live["wall_tape_band"] * 100)}
    floor_pct = (floor["height_m"] / tapes["lens_height_m"] - 1) * 100
    wall_pct = (wall["distance_m"] / tapes["facing_wall_m"] - 1) * 100
    bend, mean = abs(floor_pct - wall_pct), (floor_pct + wall_pct) / 2
    angle = math.degrees(math.acos(min(1.0, abs(sum(a * b for a, b in zip(floor["normal"], wall["normal"]))))))
    found = {"floor_pct": round(floor_pct, 3), "wall_pct": round(wall_pct, 3), "bend_pts": round(bend, 3),
             "mean_pct": round(mean, 3), "floor_share": round(floor["share"], 3), "wall_inliers": wall["inliers"],
             "floor_wall_deg": round(angle, 2)}
    if bend > live["scale_bend_max_pts"]:
        return {**found, "result": "stop", "why": "the depth bends more than a scale can fix"}
    if abs(mean) <= live["scale_none_pct"]:
        return {**found, "result": "none", "depth_scale": 1.0}
    scale = round(1 / (1 + mean / 100), 3)
    if not DEPTH_SCALE_RANGE[0] <= scale <= DEPTH_SCALE_RANGE[1]:
        return {**found, "result": "stop", "depth_scale": scale,
                "why": "the depth is %+.1f%% off, more than den-live will scale (x%g to x%g)" % (mean, *DEPTH_SCALE_RANGE)}
    return {**found, "result": "scale", "depth_scale": scale}


def depth_line(check):
    if "floor_pct" not in check:
        return "Depth check: STOP: %s." % check["why"]
    verdict = ("no scale" if check["result"] == "none" else "scale x%g applied" % check["depth_scale"]
               if check["result"] == "scale" else "STOP: %s" % check.get("why"))
    return ("Depth check: floor %+.1f%%, wall %+.1f%%, bend %.1f points: %s"
            % (check["floor_pct"], check["wall_pct"], check["bend_pts"], verdict))


# ---- 6. the worksheet, the clicks and the edge guard ---------------------------------------------------------------

def ensure_worksheet(R, work, *, clear=False):
    """den-register's worksheet with the nine features and no positions yet. One with these nine already there is
    kept; clear=True (a fresh paste, whose new take may be clicked on other features) empties its scan picks. One with
    other features (registration v1's six) is replaced."""
    path = work / R.register.WORKSHEET
    if path.exists():
        sheet = json.loads(path.read_text(encoding="utf-8-sig"))
        if [p.get("id") for p in sheet.get("points") or []] == list(IDS):
            if clear:
                for point in sheet["points"]:
                    point.update(room_xyz_m=None, measured_from=None, note="")
                write_json(path, sheet)
            return path
    write_json(path, {
        "schema": R.worksheet.SCHEMA, "room_id": "den", "camera_id": "den-oak", "frame": R.worksheet.FRAME,
        "references": {"x": "the scan's own frame (Home's apartment frame), picked on its display mesh",
                       "y": "the scan's own frame (Home's apartment frame), picked on its display mesh",
                       "z": "the scan's floor, z = 0"},
        "measured_by": "A", "measured_on": None,
        "points": [{"id": pid, "role": role, "label": label, "room_xyz_m": None, "measured_from": None,
                    "measured_with": SCAN_PICK, "scan_xyz_m": None, "note": ""} for pid, role, label in POINTS]})
    return path


def clicked(R, work, take_dir):
    path = R.register.clicks_path(work, take_dir)
    if not path.exists():
        return None, {}
    data = R.register.read_clicks(path)
    return data, data["clicks"]


def click_page(R, work, take_dir, m):
    say("")
    say("Now the camera's picture opens in your browser. Click nine features the camera sees, one per row:")
    for pid, role, label in POINTS:
        say("  %s (%s): %s" % (pid, role, label))
    say("Fixed structure only: inside corners or flat marks, never an edge with background behind it. No furniture, "
        "rugs, cushions, screens, glass or black fabric. Prefer middle depths over the far wall's corners. Each click "
        "shows the depth it found; depth_missing means click a little to the side. Remember what each one is: you "
        "click the same nine in your scan next. Press Done on the page when all nine are in.")
    return R.register.run_click(work, take_dir, m=m, out=Lines(), open_browser=True)


def window_spread(plane, u, v, w):
    """(p90 - p10 of the window's depth about its own least-squares plane, the window's median depth), metres, or None
    with under 4 pixels of depth. The plane takes out a surface's slope: the floor seen at a few metres changes depth
    by 1-3 cm a pixel, so its raw spread alone would name a good floor-level corner. What's left is a step between two
    surfaces."""
    import numpy as np
    dv, du = np.mgrid[max(0, v - w) - v:min(HEIGHT, v + w + 1) - v, max(0, u - w) - u:min(WIDTH, u + w + 1) - u]
    window = plane[max(0, v - w):v + w + 1, max(0, u - w):u + w + 1]
    ok = window > 0
    if ok.sum() < 4:
        return None
    z = window[ok]
    design = np.column_stack((np.ones(len(z)), du[ok], dv[ok]))
    residual = z - design @ np.linalg.lstsq(design, z, rcond=None)[0]
    p10, p90 = np.percentile(residual, [10, 90])
    return float(p90 - p10), float(np.median(z))


def edge_guard(R, work, take_dir, clicks, m, live):
    """{pid: (the click window's spread, its limit)} on the copy's depth, and the clicks it names, in row order: a
    window whose spread is over max(edge_min_m, edge_fraction x its median depth) straddles two surfaces."""
    import numpy as np
    data, _ = clicked(R, work, take_dir)
    bundle = R.scene.load_frame(take_dir, data["frame"]["n"])[3]
    plane = np.frombuffer(bundle["depth"], dtype=np.uint16).reshape(HEIGHT, WIDTH).astype(np.float64) / 1000
    spreads, named = {}, []
    for pid in IDS:
        if pid not in clicks:
            continue
        found = window_spread(plane, int(round(clicks[pid]["u"])), int(round(clicks[pid]["v"])), int(m["depth_window_px"]))
        if found is None:
            continue
        spread, depth = found
        limit = max(live["edge_min_m"], live["edge_fraction"] * depth)
        spreads[pid] = {"spread_m": round(spread, 4), "limit_m": round(limit, 4)}
        if spread > limit:
            named.append(pid)
    return {"spreads": spreads, "named": named}


def edge_line(named):
    if not named:
        return "Edge guard: all clear."
    return "Edge guard: %s %s on a depth edge: click a little inside one surface." % (names(named),
                                                                                       "sits" if len(named) == 1 else "sit")


def left_png(R, work, take_dir):
    """The clicked frame's left image as a PNG beside the worksheet, for the scan page's reminder of what you clicked."""
    from PIL import Image
    data, _ = clicked(R, work, take_dir)
    n = data["frame"]["n"] if data else R.register.frame_number(take_dir, None, work)
    bundle = R.scene.load_frame(take_dir, n)[3]
    (work / "pick").mkdir(exist_ok=True)
    Image.frombytes("L", (WIDTH, HEIGHT), bytes(bundle["left"])).save(work / "pick" / "left.png")


# ---- 7. the scan page ----------------------------------------------------------------------------------------------

def scan_page(work, take_dir, mode, *, opener=webbrowser.open, extra=()):
    """node scan_pick.mjs, which prints 'ready <url>' when it listens; its exit code says what the page did:
    0 saved (pick, spots) or Done (show). However this ends, Node doesn't outlive it. Spots mode (step 4's walk test)
    has no take: take_dir is None."""
    node = shutil.which("node")
    if not node:
        raise Stop("Node isn't on the PATH; the scan page needs it (the den simulation does too)")
    take = ["--take", str(take_dir)] if take_dir is not None else []
    proc = subprocess.Popen([node, str(PICK_PAGE), "--work", str(work), *take, "--mode", mode, *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    try:
        url = None
        for _ in range(20):   # Node may warn before it listens; its own refusal is one line
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("ready "):
                url = line.split(" ", 1)[1].strip()
                break
            say("  " + line.rstrip())
        if not url:
            raise Stop("the scan page didn't start (its words, if any, are above)")
        say("The scan page is at %s (it opens in your browser)." % url)
        opener(url)
        for line in proc.stdout:
            say("  " + line.rstrip())
        return proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        proc.stdout.close()


def pick_page(work, take_dir, extra=()):
    say("")
    say("Now your scan opens in the browser, beside the camera's picture with your clicks numbered. For each row, "
        "orbit to the same feature and click it once, without dragging. Re-pick any row by selecting it again. "
        "Press Save when all nine are in.")
    code = scan_page(work, take_dir, "pick", extra=extra)
    if code != 0:
        raise Stop("the scan page closed without saving the picks")


def show_page(work, take_dir, extra=()):
    say("")
    say("Your scan opens again with the camera where the fit puts it, and your nine features as the camera sees them. "
        "Each dot should sit on its feature. Press Done on the page when you've looked. If you closed the tab, press "
        "Ctrl+C here: the registration stands either way.")
    scan_page(work, take_dir, "show", extra=extra)


# ---- 8. the distance check ----------------------------------------------------------------------------------------

def feature_pairs(R, work, take_dir, m):
    """{pid: (camera point, scan point)} for every clicked and picked feature: the click's depth exactly as
    den-register's fit takes it (the same window on the same frame of the copy), the pick from the worksheet."""
    data, clicks = clicked(R, work, take_dir)
    bundle = R.scene.load_frame(take_dir, data["frame"]["n"])[3]
    sheet = json.loads((work / R.register.WORKSHEET).read_text(encoding="utf-8-sig"))
    out = {}
    for point in sheet["points"]:
        c, room = clicks.get(point["id"]), point.get("room_xyz_m")
        if c is None or room is None:
            continue
        try:
            depth = R.scene.depth_at(bundle, c["u"], c["v"], window_px=m["depth_window_px"],
                                     min_valid_fraction=m["min_depth_valid_fraction"])
        except R.scene.Refused:
            continue
        out[point["id"]] = (R.scene.back_project(bundle["intrinsics"], c["u"], c["v"], depth["z_m"]), room)
    return out


def distance_check(pairs, live, gate):
    """Every pair's distance in the camera against the scan. Each feature's median difference over its pairs. A feature
    over den-register's own gate is named, worst first, and stops the paste: no fit could pass it. One over
    distance_max_m but within the gate is only noted: a far feature's few centimetres of range error, or the depth's
    bend, are inside what the owner allows."""
    import statistics
    ids = [pid for pid in IDS if pid in pairs]
    diffs = {pid: [] for pid in ids}
    every = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            diff = abs(math.dist(pairs[a][0], pairs[b][0]) - math.dist(pairs[a][1], pairs[b][1]))
            diffs[a].append(diff)
            diffs[b].append(diff)
            every.append(diff)
    note = live["distance_max_m"]
    median = {pid: statistics.median(values) for pid, values in diffs.items() if values}
    worst_first = sorted(median, key=lambda pid: -median[pid])
    named = [pid for pid in worst_first if median[pid] > gate]
    noted = [pid for pid in worst_first if note < median[pid] <= gate]
    over = {pid: sum(d > (gate if pid in named else note) for d in values) for pid, values in diffs.items()}
    return {"features": len(ids), "pairs": len(every), "median_m": {pid: round(v, 4) for pid, v in median.items()},
            "over": over, "named": named, "noted": noted, "gate_m": gate,
            "pair_median_m": round(statistics.median(every), 4) if every else None}


def distance_line(found, live):
    if found["named"]:
        worst = found["named"]
        each = "; ".join("%s disagrees with %d of %d others (median %.0f cm)"
                         % (pid, found["over"][pid], found["features"] - 1, found["median_m"][pid] * 100) for pid in worst)
        return ("Distance check: %s: re-pick %s or re-click %s, then paste with --again."
                % (each, names(worst), "it" if len(worst) == 1 else "them"))
    count = "nine" if found["features"] == 9 else found["features"]
    if found.get("noted"):
        return ("Distance check: all %s agree within %.0f cm. A note, no stop: %s." % (count, found["gate_m"] * 100, "; ".join(
            "%s is %.0f cm off the others (median over its %d pairs)" % (pid, found["median_m"][pid] * 100, found["features"] - 1)
            for pid in found["noted"])))
    return "Distance check: all %s agree within %.0f cm." % (count, live["distance_max_m"] * 100)


# ---- 9. the features' own scale ---------------------------------------------------------------------------------------

def own_scale(pairs):
    """Umeyama's similarity fit, camera to scan, over the clicked features with depth: its scale, and how far the
    worst feature sits from it. None under three, or when they sit on one line."""
    import numpy as np
    usable = [p for p in pairs if p.get("camera_xyz_m") and p.get("room_xyz_m")]
    if len(usable) < 3:
        return None
    cam = np.array([p["camera_xyz_m"] for p in usable], dtype=float)
    room = np.array([p["room_xyz_m"] for p in usable], dtype=float)
    cam_c, room_c = cam - cam.mean(axis=0), room - room.mean(axis=0)
    variance = (cam_c ** 2).sum() / len(usable)
    u, d, vt = np.linalg.svd(room_c.T @ cam_c / len(usable))
    if variance < 1e-9 or d[1] < 1e-9 * d[0]:
        return None
    sign = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(u) * np.linalg.det(vt)) or 1.0)])
    scale = float(np.trace(np.diag(d) @ sign) / variance)
    rotation = u @ sign @ vt
    off = np.linalg.norm(scale * cam_c @ rotation.T - room_c, axis=1)
    worst = int(np.argmax(off))
    return {"scale": scale, "features": len(usable), "worst_m": float(off[worst]), "worst_id": usable[worst]["id"]}


def scale_lines(found, scale, live):
    if not found:
        return ["Features' own scale: not enough clicked features with depth to say."]
    line = ("Features' own scale: %.3f from %d features, on depth already x%g (so %.3f on the camera's depth). Printed "
            "only." % (found["scale"], found["features"], scale, found["scale"] * scale))
    if abs(found["scale"] - 1) > live["own_scale_band"]:
        return [line, "  That's outside 1 +/- %g: the depth scale doesn't hold at these features' depths, or a feature "
                      "is clicked on the wrong thing in one of the two pictures." % live["own_scale_band"]]
    return [line]


def depth_scale_to_blame(found, live, gate):
    """The depth scale, not a click: the features' own scale is off, and with that scale every feature fits inside
    the gate. One badly placed feature moves the scale too, but stays outside the gate."""
    return bool(found) and abs(found["scale"] - 1) > live["own_scale_band"] and found["worst_m"] <= gate


def fit_numbers(report):
    """The fit's numbers for the summary and the receipt: residuals, leave-one-out, each check, the bound."""
    pairs = report.get("pairs") or []
    residuals = [p["residual_m"] for p in pairs if type(p.get("residual_m")) in (int, float)]
    loo = [p["loo_m"] for p in pairs if type(p.get("loo_m")) in (int, float)]
    checks = {p["id"]: p["error_m"] for p in pairs if p.get("role") == "check" and type(p.get("error_m")) in (int, float)}
    fit = report.get("fit") or {}
    return {"n_fit": sum(p.get("role") == "fit" for p in pairs), "n_check": sum(p.get("role") == "check" for p in pairs),
            "residual_min_m": min(residuals) if residuals else None, "residual_max_m": max(residuals) if residuals else None,
            "loo_max_m": max(loo) if loo else None, "checks_m": checks, "error_bound_m": fit.get("error_bound_m")}


# ---- 10. the held-out take ------------------------------------------------------------------------------------------

PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "<i2", "int16": "<i2", "ushort": "<u2",
             "uint16": "<u2", "int": "<i4", "int32": "<i4", "uint": "<u4", "uint32": "<u4", "float": "<f4",
             "float32": "<f4", "double": "<f8", "float64": "<f8"}


def read_ply(data):
    """x, y, z (N x 3, float64) of a binary little-endian PLY's vertex element, as Home's points.ply writes it."""
    import numpy as np
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        raise ValueError("not a PLY file")
    body = data.index(b"\n", end) + 1
    lines = data[:end].decode("ascii").replace("\r", "").split("\n")
    if "format binary_little_endian 1.0" not in lines:
        raise ValueError("not a binary little-endian PLY file")
    fields, count, element = [], None, None
    for line in lines:
        words = line.split()
        if words[:1] == ["element"]:
            element = words[1]
            if element == "vertex":
                count = int(words[2])
            elif count is not None:
                break
        elif words[:1] == ["property"] and element == "vertex":
            if words[1] == "list" or words[1] not in PLY_TYPES:
                raise ValueError("the vertex has a property this reader doesn't know")
            fields.append((words[2], PLY_TYPES[words[1]]))
    if count is None or not {"x", "y", "z"} <= {name for name, _ in fields}:
        raise ValueError("no vertex x, y and z")
    vertex = np.frombuffer(data, dtype=np.dtype(fields), count=count, offset=body)
    return np.column_stack((vertex["x"], vertex["y"], vertex["z"])).astype(np.float64)


def scan_points(packs, site, revision):
    """(the scan's points.ply as N x 3 in the scan's frame, None), or (None, why). The active pack must be the scan
    revision the picks were made on, and the file its manifest's."""
    folder = Path(packs) / site
    try:
        active = (folder / "ACTIVE").read_text(encoding="utf-8").strip()
        manifest = json.loads((folder / active / "content-pack.json").read_text(encoding="utf-8"))
        asset = manifest["assets"]["points.ply"]
        data = (folder / active / asset["path"]).read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return None, "the scan's active content pack has no readable points.ply"
    if active != revision:
        return None, "the active scan is %s, but the picks were made on %s" % (active, revision)
    if sha256_bytes(data) != asset.get("sha256"):
        return None, "the scan's points.ply doesn't match its pack's manifest"
    try:
        return read_ply(data), None
    except (ValueError, UnicodeDecodeError):
        return None, "the scan's points.ply doesn't read"


def active_revision(packs, site):
    """The scan revision the content packs say is active, or None."""
    try:
        return (Path(packs) / site / "ACTIVE").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def polygon_of(value):
    """value as an M x 2 polygon of finite metres, M >= 3, or None."""
    import numpy as np
    if type(value) is not list or len(value) < 3:
        return None
    if not all(type(p) is list and len(p) >= 2 and all(type(c) in (int, float) and math.isfinite(c) for c in p[:2])
               for p in value):
        return None
    return np.array([p[:2] for p in value], dtype=np.float64)


def scan_floor_area(packs, site, revision):
    """({polygon, holes}: the den's walkable floor from the active pack's floor.json, None), or (None, why). The same
    pack as scan_points: the scan revision the registration was made on, and the file its manifest's."""
    folder = Path(packs) / site
    try:
        active = (folder / "ACTIVE").read_text(encoding="utf-8").strip()
        manifest = json.loads((folder / active / "content-pack.json").read_text(encoding="utf-8"))
        asset = manifest["assets"]["floor.json"]
        data = (folder / active / asset["path"]).read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return None, "the scan's active content pack has no readable floor.json"
    if active != revision:
        return None, "the active scan is %s, but the registration was made on %s" % (active, revision)
    if sha256_bytes(data) != asset.get("sha256"):
        return None, "the scan's floor.json doesn't match its pack's manifest"
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None, "the scan's floor.json doesn't read"
    polygon = polygon_of(doc.get("walkable_polygon")) if type(doc) is dict else None
    holes = [polygon_of(hole) for hole in (doc.get("holes") or [])] if polygon is not None else []
    if polygon is None or type(doc.get("holes") or []) is not list or any(hole is None for hole in holes):
        return None, "the scan's floor.json holds no walkable polygon of three or more points, with its holes"
    return {"polygon": polygon, "holes": holes}, None


FLOOR, UPPER = 1, 2                     # what the scan shows at a pixel: its floor, or a surface above held_wall_min_z_m
SURFACE_VOXEL_M, SURFACE_REACH = 0.05, 3   # the scan's local planes: 5 cm voxels, searched 3 voxels each way
SURFACE_BELOW_M = 0.25                  # local planes from this far below held_wall_min_z_m: an upper point just under it
RESIDUAL_CAP_M = 0.20                   # no local plane within reach: counted as this far off, past every limit


def scan_view(points, room_from_camera, k, live):
    """Which surface of the scan each pixel sees from the registration: FLOOR (within held_floor_band_m of z = 0),
    UPPER (at or above held_wall_min_z_m) or 0. Every point is z-buffered, so furniture hides the floor behind it as it
    does for the camera; each point covers its 3 x 3 pixels, and a pixel well behind its 5 x 5 neighbourhood's nearest
    is a gap in the points, not a surface, so it's 0. Only the labels are used: the pose is judged in the room."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    M = np.asarray(room_from_camera, dtype=np.float64)
    rotation, translation = M[:3, :3], M[:3, 3]
    cam = (points - translation) @ rotation   # rotation^T (p - t), row by row
    z = cam[:, 2]
    ahead = z > 0.2
    cam, room_z, z = cam[ahead], points[ahead, 2], z[ahead]
    kind = np.where(np.abs(room_z) <= live["held_floor_band_m"], FLOOR,
                    np.where(room_z >= live["held_wall_min_z_m"], UPPER, 0)).astype(np.int8)
    u = np.rint(k["fx"] * cam[:, 0] / z + k["cx"]).astype(np.int64)
    v = np.rint(k["fy"] * cam[:, 1] / z + k["cy"]).astype(np.int64)
    pixel, depth, kinds = [], [], []
    for du in (-1, 0, 1):
        for dv in (-1, 0, 1):
            uu, vv = u + du, v + dv
            inside = (uu >= 0) & (uu < WIDTH) & (vv >= 0) & (vv < HEIGHT)
            pixel.append(vv[inside] * WIDTH + uu[inside])
            depth.append(z[inside])
            kinds.append(kind[inside])
    pixel, depth, kinds = np.concatenate(pixel), np.concatenate(depth), np.concatenate(kinds)
    zbuf = np.full(WIDTH * HEIGHT, np.inf)
    label = np.zeros(WIDTH * HEIGHT, np.int8)
    if len(pixel):
        order = np.lexsort((depth, pixel))
        pixel, depth, kinds = pixel[order], depth[order], kinds[order]
        first = np.r_[True, pixel[1:] != pixel[:-1]]
        zbuf[pixel[first]], label[pixel[first]] = depth[first], kinds[first]
    zbuf, label = zbuf.reshape(HEIGHT, WIDTH), label.reshape(HEIGHT, WIDTH)
    nearest = sliding_window_view(np.pad(zbuf, 2, constant_values=np.inf), (5, 5)).min(axis=(2, 3))
    label[~np.isfinite(zbuf) | (zbuf > nearest + np.maximum(0.05, 0.03 * nearest))] = 0
    return label


def lift(measured, k, room_from_camera):
    """Every pixel of the held-out take's depth (metres) as a room point through the registration: HEIGHT x WIDTH x 3."""
    import numpy as np
    M = np.asarray(room_from_camera, dtype=np.float64)
    v, u = np.mgrid[0:HEIGHT, 0:WIDTH]
    cam = np.stack(((u - k["cx"]) * measured / k["fx"], (v - k["cy"]) * measured / k["fy"], measured), -1)
    return cam @ M[:3, :3].T + M[:3, 3]


def _cell_keys(cells):
    """One int64 per voxel: 21 bits an axis, offset so 5 cm voxels reach 50 km either way."""
    shifted = cells + (1 << 20)
    return (shifted[:, 0] << 42) | (shifted[:, 1] << 21) | shifted[:, 2]


def surface_planes(points, voxel=SURFACE_VOXEL_M):
    """The scan's surface as local planes: for each voxel with at least 3 points, its key, centroid and normal (the
    least-spread direction of its points), sorted by key."""
    import numpy as np
    keys = _cell_keys(np.floor(points / voxel).astype(np.int64))
    unique, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    inverse = inverse.reshape(-1)
    centroid = np.stack([np.bincount(inverse, weights=points[:, i], minlength=len(unique)) for i in range(3)], 1)
    centroid /= counts[:, None]
    offset = points - centroid[inverse]
    covariance = np.empty((len(unique), 3, 3))
    for i in range(3):
        for j in range(i, 3):
            covariance[:, i, j] = covariance[:, j, i] = np.bincount(inverse, weights=offset[:, i] * offset[:, j],
                                                                    minlength=len(unique)) / counts
    keep = counts >= 3
    normal = np.linalg.eigh(covariance[keep])[1][:, :, 0]
    return unique[keep], centroid[keep], normal


def nearest_plane(queries, planes, voxel=SURFACE_VOXEL_M, reach=SURFACE_REACH):
    """(each room point's nearest local plane of the scan, as an index into planes or -1 where none is within reach;
    its distance to that plane: across it, plus how far past the voxel's patch it lies along it)."""
    import numpy as np
    keys, centroid, normal = planes
    best, index = np.full(len(queries), np.inf), np.full(len(queries), -1, dtype=np.int64)
    if not len(keys) or not len(queries):
        return index, best
    cells = np.floor(queries / voxel).astype(np.int64)
    patch = voxel * math.sqrt(3) / 2
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                wanted = _cell_keys(cells + (dx, dy, dz))
                at = np.minimum(np.searchsorted(keys, wanted), len(keys) - 1)
                hit = np.flatnonzero(keys[at] == wanted)
                if not len(hit):
                    continue
                found = at[hit]
                offset = queries[hit] - centroid[found]
                across = np.abs((offset * normal[found]).sum(1))
                along = np.sqrt(np.maximum((offset * offset).sum(1) - across ** 2, 0))
                distance = np.hypot(across, np.maximum(along - patch, 0))
                closer = distance < best[hit]
                best[hit[closer]], index[hit[closer]] = distance[closer], found[closer]
    return index, best


def surface_residual(queries, planes, voxel=SURFACE_VOXEL_M, reach=SURFACE_REACH, cap=RESIDUAL_CAP_M):
    """Each room point's distance to the scan's surface: to the local plane of a voxel within reach, plus how far past
    that voxel's patch it lies along the plane, the smallest over the voxels searched; cap where none is near."""
    import numpy as np
    return np.minimum(nearest_plane(queries, planes, voxel, reach)[1], cap)


def judge(name, z, residual, live):
    """One surface's numbers: the share within noise, max(held_noise_min_m, 2 sigma(z)) with z the pixel's range, and
    the median |residual| per range bin of held_bin_m."""
    import numpy as np
    found = {"pixels": int(len(z)), "within_noise": None, "worst_bin_m": None, "bins": []}
    if len(z) < live["held_min_pixels"]:
        return {**found, "why": "only %d %s pixels to compare, under %d" % (len(z), name, live["held_min_pixels"])}
    sigma = live["noise_a_m"] + live["noise_b_per_m"] * z ** 2
    found["within_noise"] = round(float((residual <= np.maximum(live["held_noise_min_m"], 2 * sigma)).mean()), 4)
    worst = 0.0
    index = np.floor(z / live["held_bin_m"]).astype(int)
    for b in np.unique(index):
        chosen = residual[index == b]
        if len(chosen) < live["held_bin_min_pixels"]:
            continue
        median = float(np.median(chosen))
        found["bins"].append({"from_m": round(float(b * live["held_bin_m"]), 2), "pixels": int(len(chosen)),
                              "median_m": round(median, 4)})
        worst = max(worst, median)
    found["worst_bin_m"] = round(worst, 4)
    if found["within_noise"] < live["held_within_min"]:
        found["why"] = "%.0f%% of its %s pixels within noise, under %.0f%%" % (
            found["within_noise"] * 100, name, live["held_within_min"] * 100)
    elif worst > live["held_bin_max_m"]:
        found["why"] = "a range bin of its %s pixels %s cm off, over %.0f cm" % (name, cm(worst), live["held_bin_max_m"] * 100)
    else:
        found["why"] = None
    return found


def inside_polygon(xy, polygon):
    """Whether each of the points xy (N x 2) is inside polygon (M x 2), by the even-odd rule."""
    import numpy as np
    polygon = np.asarray(polygon, dtype=np.float64)
    x, y = xy[:, 0], xy[:, 1]
    inside = np.zeros(len(xy), dtype=bool)
    for (x0, y0), (x1, y1) in zip(polygon, np.roll(polygon, -1, axis=0)):
        if y0 == y1:
            continue
        crosses = (y0 > y) != (y1 > y)
        inside ^= crosses & (x < x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    return inside


def in_floor_area(xy, area):
    """Whether each point xy (N x 2) is on the den's walkable floor: inside its polygon and in none of its holes."""
    inside = inside_polygon(xy, area["polygon"])
    for hole in area["holes"]:
        inside &= ~inside_polygon(xy, hole)
    return inside


def camera_floor_heights(measured, k, live):
    """(each pixel's height above the camera's own floor plane, metres, HEIGHT x WIDTH; the plane's record), or (None,
    None) when the take's depth shows no floor. The plane is floor_plane's on the take's own depth, in the camera's
    frame: no registration enters it, so no pose being judged can move what it marks."""
    import numpy as np
    floor = floor_plane(camera_points(measured * 1000.0, k), live)
    if floor is None or floor["share"] < live["floor_min_share"]:
        return None, None
    v, u = np.mgrid[0:HEIGHT, 0:WIDTH]
    cam = np.stack(((u - k["cx"]) * measured / k["fx"], (v - k["cy"]) * measured / k["fy"], measured), -1)
    return cam @ np.array(floor["normal"]) + floor["height_m"], floor


def held_out_numbers(label, measured, room, planes, live, *, heights=None, area=None):
    """The held-out take judged in the room. label: scan_view's. measured: the take's median depth times depth_scale,
    metres, 0 where none. room: those pixels lifted through the registration. A floor pixel's residual is its room
    height; an upper one's is its distance to the scan's surface (surface_residual). The floor and the upper surfaces are
    judged apart, so neither can outvote the other: each needs held_within_min within noise, and no range bin of either
    may be more than held_bin_max_m off. A sideways shift or a turn moves the upper walls off the scan, a tilt or a
    height error lifts the floor off z = 0; depth along each pixel's ray, as before, was blind to the first two.

    The scan's floor view is judged only where the room still shows that floor. heights (camera_floor_heights'):
    a pixel more than held_covered_m above the camera's own floor plane is covered, something standing where the scan
    saw floor; the plane is the take's, so the pose being judged can't mask the pixels that would expose it. area (the
    den's walkable floor): a pixel whose lifted point falls outside it, or in one of its holes, is outside (through a
    doorway, say); that one does use the pose, as a small error barely moves the edge. Neither is judged, and both are
    counted. The upper surfaces are never masked: they carry the test that doesn't lean on the floor plane."""
    import numpy as np
    view = (label == FLOOR) & (measured > 0)
    covered = view & (heights > live["held_covered_m"]) if heights is not None else np.zeros_like(view)
    outside = np.zeros_like(view)
    if area is not None:
        rest = view & ~covered
        outside[rest] = ~in_floor_area(room[rest][:, :2], area)
    floor = view & ~covered & ~outside
    upper = (label == UPPER) & (measured > 0)
    surfaces = {"floor": judge("floor", measured[floor], np.abs(room[floor][:, 2]), live),
                "upper": judge("upper-wall", measured[upper], surface_residual(room[upper], planes), live)}
    failed = [name for name, found in surfaces.items() if found["why"]]
    why = surfaces[failed[0]]["why"] if failed else None
    shares = [found["within_noise"] for found in surfaces.values()]
    worst = [found["worst_bin_m"] for found in surfaces.values() if found["worst_bin_m"] is not None]
    seen = int(view.sum())
    return {"floor": surfaces["floor"], "upper": surfaces["upper"], "pixels": int(floor.sum() + upper.sum()),
            "within_noise": None if None in shares else min(shares), "worst_bin_m": max(worst) if worst else None,
            "ok": why is None, "why": why, "failed_on": failed,
            "covered_share": round(float(covered.sum()) / seen, 4) if seen else 0.0,
            "outside_share": round(float(outside.sum()) / seen, 4) if seen else 0.0,
            "floor_view": {"pixels": seen, "covered": int(covered.sum()), "outside": int(outside.sum()),
                           "camera_floor": heights is not None, "floor_area": area is not None}}


def held_out_check(points, room_from_camera, k, measured, live, area=None):
    """The scan against a take's depth (metres, times depth_scale) at a registration: held_out_numbers on scan_view's
    labels and the lifted pixels, the floor masked by the take's own floor plane and, given one, by the den's floor
    area."""
    label = scan_view(points, room_from_camera, k, live)
    room = lift(measured, k, room_from_camera)
    planes = surface_planes(points[points[:, 2] >= live["held_wall_min_z_m"] - SURFACE_BELOW_M])
    heights, _ = camera_floor_heights(measured, k, live)
    return held_out_numbers(label, measured, room, planes, live, heights=heights, area=area)


def held_out(R, work, take_dir, transform_id, scale, points, live, area=None):
    """The held-out take against the scan at the new registration."""
    plane, k, _ = median_plane(R, take_dir, live["median_min_valid"])
    models = R.records.read_jsonl(work / R.register.ROOM_MODEL, "room_model_unreadable")
    pose = R.records.resolve(models, transform_id)["entry"]["pose"]["room_from_camera"]
    return held_out_check(points, pose, k, scaled_plane(plane, scale).astype("float64") / 1000, live, area)


SURFACE_WORDS = {"floor": "the floor", "upper": "the upper walls"}


def held_line(found):
    floor, upper = found.get("floor") or {}, found.get("upper") or {}
    if floor.get("within_noise") is None or upper.get("within_noise") is None:
        return "Held-out take: %s." % (found.get("why") or "judged by an earlier paste")
    failed = [SURFACE_WORDS[name] for name in found.get("failed_on") or [] if name in SURFACE_WORDS]
    return ("Held-out take: %.0f%% of floor and %.0f%% of upper-wall pixels within noise; worst range bin %s cm%s."
            % (floor["within_noise"] * 100, upper["within_noise"] * 100, cm(found["worst_bin_m"]),
               "; failed on %s" % " and ".join(failed) if failed else ""))


def masked_line(found):
    """The held-out take's one line on the floor it didn't judge, or None for a receipt from before it was masked."""
    found = found or {}
    view = found.get("floor_view") or {}
    if not view.get("pixels") or found.get("covered_share") is None:
        return None
    if not view.get("camera_floor"):
        return ("Held-out take: the take shows no floor plane, so no floor is taken as covered; %.0f%% of the scan's "
                "floor view lies outside the den's floor and isn't judged." % (found["outside_share"] * 100))
    return ("Held-out take: %.0f%% of the scan's floor view is now covered by things not in the scan, and %.0f%% lies "
            "outside the den's floor; neither is judged." % (found["covered_share"] * 100, found["outside_share"] * 100))


# ---- --auto: the camera's pose from its own floor and the scan, then nine points for den-register ------------------

AUTO = "auto"                      # the worksheet's measured_with for an automatic point, set in memory as scan-pick is
AUTO_METHOD_LABEL = "auto + fit"
AUTO_FLOOR_BAND_M = 0.25           # the scan's floor height: the mode of its z within this of z = 0, in 5 mm bins
AUTO_FLOOR_MIN_POINTS = 500        # fewer scan points in that mode's 2 cm: the scan shows no floor
AUTO_SOURCE_ABOVE_M = 0.20         # the camera's points this far above the floor are matched (the floor gave tilt, height)
AUTO_TARGET_ABOVE_M = 0.15         # against the scan's points this far above its floor
AUTO_PAD_M = 0.5                   # the camera is looked for over the den floor's box, this much wider on every side
AUTO_MIN_SOURCE = 300              # fewer camera points above the floor: nothing to match
# The search, coarse to fine: (yaw step in degrees, xy step in metres, distance grid cell and reach in metres, points).
AUTO_COARSE = (4.0, 0.20, 0.10, 0.40, 400)          # yaw all the way round, over the whole box
AUTO_MEDIUM = (1.0, 0.05, 0.05, 0.20, 1500, 4.0, 0.20)   # and the best few again, +/- 4 degrees and 20 cm
AUTO_COARSE_KEEP, AUTO_MEDIUM_KEEP = 12, 6          # how many distinct poses go on from each
AUTO_DISTINCT_DEG, AUTO_DISTINCT_M = 8.0, 0.40      # poses this close in turn and place count as one, while searching
AUTO_DIFFERENT_DEG, AUTO_DIFFERENT_M = 10.0, 0.50   # the runner-up is a pose at least this far from the best
AUTO_REFINE_POINTS, AUTO_SCORE_POINTS, AUTO_REFINE_ITERATIONS = 4000, 8000, 30
AUTO_MATCH_M = 0.05                # a camera point within this of the scan's surface matches it (the search's share)
AUTO_GRID_PX, AUTO_MARGIN_PX = 4, 8   # candidate points: every 4th pixel, clear of the picture's edge
AUTO_DEPTH_RANGE_M = (0.5, 4.0)    # at middle depths, as the click page asks; up to 6 m if that leaves too few
AUTO_FAR_M = 6.0
AUTO_EDGE_SHARE = 0.5              # a candidate's 7 x 7 window spreads at most half what the edge guard allows
AUTO_APART_M = 0.30                # a check point at least this far from every fit point
AUTO_ON_SOFA_M, AUTO_SOFA_TOP_M = 0.15, 1.2   # this near the sofa's footprint and under this height: the sofa itself
AUTO_POINTS = (("f1", "fit", "automatic: low, left of the picture"),
               ("f2", "fit", "automatic: low, right of the picture"),
               ("f3", "fit", "automatic: mid height, left"), ("f4", "fit", "automatic: mid height, right"),
               ("f5", "fit", "automatic: high, left"), ("f6", "fit", "automatic: high, right"),
               ("c1", "check", "automatic check on stable structure"),
               ("c2", "check", "automatic check on stable structure"),
               ("c3", "check", "automatic check on stable structure"))
assert tuple(pid for pid, _, _ in AUTO_POINTS) == IDS


def scan_floor_height(points):
    """The scan's floor height: the mode of its z within AUTO_FLOOR_BAND_M of z = 0, or None when too few points sit
    there to call it a floor."""
    import numpy as np
    z = points[:, 2][np.abs(points[:, 2]) < AUTO_FLOOR_BAND_M]
    if len(z) < AUTO_FLOOR_MIN_POINTS:
        return None
    counts, edges = np.histogram(z, bins=np.arange(-AUTO_FLOOR_BAND_M, AUTO_FLOOR_BAND_M + 1e-9, 0.005))
    mode = float(edges[np.argmax(counts)] + 0.0025)
    if (np.abs(z - mode) < 0.02).sum() < AUTO_FLOOR_MIN_POINTS:
        return None
    return float(np.median(z[np.abs(z - mode) < 0.01]))


def turn_onto(a, b):
    """The rotation turning unit vector a onto unit vector b."""
    import numpy as np
    v, c = np.cross(a, b), float(a @ b)
    s = float(np.linalg.norm(v))
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s ** 2)


def yaw_turn(angle):
    import numpy as np
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


class DistanceGrid:
    """The distance from a voxel grid's cells to the nearest occupied cell, capped at reach: an exact truncated
    Euclidean distance transform in three separable passes. Cells past the points by more than reach say reach, so a
    lookup clipped onto the grid's border reads reach too."""

    def __init__(self, points, cell, reach):
        import numpy as np
        self.cell, self.reach = cell, reach
        self.lo = points.min(axis=0) - reach - 2 * cell
        self.shape = tuple(int(n) for n in np.ceil((points.max(axis=0) + reach + 2 * cell - self.lo) / cell) + 1)
        index = np.floor((points - self.lo) / cell).astype(np.int64)
        f = np.full(self.shape, np.inf, dtype=np.float32)
        f[index[:, 0], index[:, 1], index[:, 2]] = 0
        m = int(math.ceil(reach / cell))
        for axis in range(3):
            n = f.shape[axis]
            pad = [(0, 0)] * 3
            pad[axis] = (m, m)
            padded = np.pad(f, pad, constant_values=np.inf)
            for d in range(-m, m + 1):
                if d:
                    window = [slice(None)] * 3
                    window[axis] = slice(m + d, m + d + n)
                    np.minimum(f, padded[tuple(window)] + d * d, out=f)
        self.distance = np.minimum(np.sqrt(f) * cell, reach).astype(np.float32).reshape(-1)

    def costs(self, rotated, start, ni, nj, step):
        """The mean distance of the points rotated, moved by start + (i, j, 0) x step cells, for i < ni and j < nj:
        ni x nj. One floor per point, then whole cells: a step that's a whole number of cells moves every point by
        exactly that many."""
        import numpy as np
        base = np.floor((rotated + start - self.lo) / self.cell).astype(np.int64)
        sx, sy, sz = self.shape
        ix = np.clip(base[:, 0, None] + step * np.arange(ni)[None], 0, sx - 1) * (sy * sz)
        iy = np.clip(base[:, 1, None] + step * np.arange(nj)[None], 0, sy - 1) * sz
        iz = np.clip(base[:, 2], 0, sz - 1)
        return self.distance[ix[:, :, None] + iy[:, None, :] + iz[:, None, None]].mean(axis=0)


def angle_apart(a, b):
    return abs((a - b + math.pi) % (2 * math.pi) - math.pi)


def distinct(poses, keep, degrees, metres):
    """The best poses ((cost, yaw, x, y), lowest cost first), none within degrees and metres of a better one."""
    out = []
    for pose in poses:
        if not any(angle_apart(pose[1], o[1]) < math.radians(degrees)
                   and math.hypot(pose[2] - o[2], pose[3] - o[3]) < metres for o in out):
            out.append(pose)
            if len(out) == keep:
                break
    return out


def sample(points, count, rng):
    return points if len(points) <= count else points[rng.choice(len(points), count, replace=False)]


def refine_pose(yaw, x, y, tz, source, planes, iterations=AUTO_REFINE_ITERATIONS):
    """Yaw, x and y by point-to-plane IRLS against the scan's local planes, robust weights 1 / max(|r|, 1 cm); tilt and
    height stay the floor's. Correspondences within about 15 cm at first, then 10, then 5."""
    import numpy as np
    keys, centroid, normal = planes
    for it in range(iterations):
        q = source @ yaw_turn(yaw).T + (x, y, tz)
        index, distance = nearest_plane(q, planes, reach=3 if it < 3 else 2 if it < 8 else 1)
        ok = (index >= 0) & (distance < (0.15 if it < 3 else 0.10 if it < 8 else 0.05))
        if ok.sum() < 50:
            break
        n, qq = normal[index[ok]], q[ok]
        r = ((qq - centroid[index[ok]]) * n).sum(axis=1)
        rel = qq - (x, y, tz)
        weight = np.sqrt(1 / np.maximum(np.abs(r), 0.01))
        jacobian = np.column_stack((n[:, 1] * rel[:, 0] - n[:, 0] * rel[:, 1], n[:, 0], n[:, 1]))
        step = np.linalg.lstsq(jacobian * weight[:, None], -r * weight, rcond=None)[0]
        step = np.clip(step, -0.1, 0.1)
        yaw, x, y = yaw + step[0], x + step[1], y + step[2]
        if np.abs(step).max() < 1e-5:
            break
    return yaw, x, y


def match_share(yaw, x, y, tz, source, planes):
    """The share of the camera's points (floor turned onto the scan's) within AUTO_MATCH_M of the scan's surface."""
    import numpy as np
    distance = nearest_plane(source @ yaw_turn(yaw).T + (x, y, tz), planes, reach=1)[1]
    return float((distance <= AUTO_MATCH_M).mean())


def auto_pose(cam, scan, area, live, seed=SEED):
    """The camera's pose in the scan from the camera's points (metres, its own frame) alone, and how sure the search
    is: {pose: room_from_camera 4 x 4, share, runner_up, ...}, or {why} for a plain-sentence stop.

    Tilt and height come from the camera's own floor plane (floor_plane, as the depth check finds it), put on the scan's
    floor height, so only yaw, x and y are searched: yaw all the way round and x, y over the den floor's box, AUTO_PAD_M
    wider, on a truncated distance grid of the scan's points above its floor, coarse then fine, then point-to-plane
    IRLS from the best few. The best must match clearly more of the camera's view than the best pose elsewhere."""
    import numpy as np
    started = time.time()
    floor = floor_plane(cam, live)
    if floor is None or floor["share"] < live["floor_min_share"]:
        return {"why": "the camera's floor plane isn't found in the take"}
    z0 = scan_floor_height(scan)
    if z0 is None:
        return {"why": "the scan shows no floor near z = 0"}
    level = turn_onto(np.array(floor["normal"]), np.array([0.0, 0.0, 1.0]))
    tz = z0 + floor["height_m"]
    turned = cam @ level.T
    source = turned[turned[:, 2] + tz > z0 + AUTO_SOURCE_ABOVE_M]
    targets = scan[scan[:, 2] > z0 + AUTO_TARGET_ABOVE_M]
    if len(source) < AUTO_MIN_SOURCE or len(targets) < AUTO_MIN_SOURCE:
        return {"why": "too little of the camera's view, or of the scan, stands above the floor to match"}
    targets = targets[np.unique(_cell_keys(np.floor(targets / 0.02).astype(np.int64)), return_index=True)[1]]
    rng = np.random.default_rng(seed)
    x0, y0 = area["polygon"].min(axis=0) - AUTO_PAD_M
    x1, y1 = area["polygon"].max(axis=0) + AUTO_PAD_M

    # Coarse: every yaw step, every xy step over the box.
    yaw_step, xy_step, cell, reach, count = AUTO_COARSE
    grid = DistanceGrid(targets, cell, reach)
    small = sample(source, count, rng)
    ni, nj = int((x1 - x0) // xy_step) + 1, int((y1 - y0) // xy_step) + 1
    step = int(round(xy_step / cell))
    found = []
    for yaw in np.radians(np.arange(0.0, 360.0, yaw_step)):
        costs = grid.costs(small @ yaw_turn(yaw).T, np.array([x0, y0, tz]), ni, nj, step)
        for flat in np.argsort(costs, axis=None)[:AUTO_COARSE_KEEP]:
            i, j = divmod(int(flat), nj)
            found.append((float(costs[i, j]), float(yaw), x0 + i * xy_step, y0 + j * xy_step))
    coarse = distinct(sorted(found), AUTO_COARSE_KEEP, AUTO_DISTINCT_DEG, AUTO_DISTINCT_M)

    # Medium: around each, finer.
    yaw_step, xy_step, cell, reach, count, yaw_reach, xy_reach = AUTO_MEDIUM
    grid = DistanceGrid(targets, cell, reach)
    middle = sample(source, count, rng)
    n = int(round(2 * xy_reach / xy_step)) + 1
    step = int(round(xy_step / cell))
    found = []
    for _, yaw0, xc, yc in coarse:
        for yaw in yaw0 + np.radians(np.arange(-yaw_reach, yaw_reach + 1e-9, yaw_step)):
            costs = grid.costs(middle @ yaw_turn(yaw).T, np.array([xc - xy_reach, yc - xy_reach, tz]), n, n, step)
            i, j = np.unravel_index(int(np.argmin(costs)), costs.shape)
            found.append((float(costs[i, j]), float(yaw), xc - xy_reach + i * xy_step, yc - xy_reach + j * xy_step))
    medium = distinct(sorted(found), AUTO_MEDIUM_KEEP, AUTO_DISTINCT_DEG, AUTO_DISTINCT_M)

    # Fine: IRLS from each, then the share of the camera's view each matches.
    planes = surface_planes(targets)
    refine, score = sample(source, AUTO_REFINE_POINTS, rng), sample(source, AUTO_SCORE_POINTS, rng)
    results = []
    for _, yaw, x, y in medium:
        yaw, x, y = refine_pose(yaw, x, y, tz, refine, planes)
        results.append((match_share(yaw, x, y, tz, score, planes), yaw, x, y))
    results.sort(key=lambda r: -r[0])
    share, yaw, x, y = results[0]
    runner = next((r[0] for r in results[1:] if angle_apart(r[1], yaw) > math.radians(AUTO_DIFFERENT_DEG)
                   or math.hypot(r[2] - x, r[3] - y) > AUTO_DIFFERENT_M), 0.0)
    pose = np.eye(4)
    pose[:3, :3] = yaw_turn(yaw) @ level
    pose[:3, 3] = (x, y, tz)
    out = {"pose": pose, "share": round(share, 4), "runner_up": round(runner, 4), "poses_refined": len(results),
           "seconds": round(time.time() - started, 1), "scan_floor_m": z0}
    if share < live["auto_min_share"]:
        return {**out, "why": "the automatic search found no pose where the scan meets the camera's view: the best "
                              "matches %.0f%% of it, under %.0f%%" % (share * 100, live["auto_min_share"] * 100)}
    if runner >= live["auto_runner_up_max"] * share:
        return {**out, "why": "the automatic search can't tell two poses apart: the best matches %.0f%% of the "
                              "camera's view and another, elsewhere, %.0f%%" % (share * 100, runner * 100)}
    return out


def window_spreads(depth, w=3):
    """p90 - p10 of each full (2w+1)^2 window's depth about its own least-squares plane, as window_spread finds it, for
    every pixel at least w from the edge: NaN where a window has a pixel without depth, or near the edge."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    dv, du = np.mgrid[-w:w + 1, -w:w + 1]
    design = np.column_stack((np.ones(du.size), du.ravel(), dv.ravel()))
    residual_of = np.eye(du.size) - design @ np.linalg.pinv(design)
    windows = sliding_window_view(depth, (2 * w + 1, 2 * w + 1)).reshape(HEIGHT - 2 * w, WIDTH - 2 * w, -1)
    residual = windows @ residual_of.T
    spread = np.percentile(residual, 90, axis=2) - np.percentile(residual, 10, axis=2)
    spread[(windows <= 0).any(axis=2)] = np.nan
    out = np.full((HEIGHT, WIDTH), np.nan)
    out[w:HEIGHT - w, w:WIDTH - w] = spread
    return out


def sofa_mark(path):
    """The seat point and footprint of a home-registered-sofa-target-1 record, or Stop. Its numbers never print."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise Stop("the sofa mark given with --sofa-mark doesn't read") from None
    finite = lambda p: type(p) is list and len(p) == 3 and all(type(v) in (int, float) and math.isfinite(v) for v in p)
    seat = (doc.get("seat") or {}).get("point_m") if type(doc) is dict else None
    if type(doc) is not dict or doc.get("schema") != "home-registered-sofa-target-1" or not finite(seat):
        raise Stop("the file given with --sofa-mark isn't a home-registered-sofa-target-1 record with a seat point")
    vertices = (doc.get("zone") or {}).get("vertices_m") if type(doc.get("zone")) is dict else None
    footprint = polygon_of(vertices) if type(vertices) is list and all(finite(v) for v in vertices) else None
    return {"seat": [float(v) for v in seat], "footprint": footprint}


def sofa_distance(xy, sofa):
    """Each point's distance across the floor (N x 2) to the sofa: to its footprint, 0 inside it, or to its seat point
    when the mark has no footprint."""
    import numpy as np
    if sofa["footprint"] is None:
        return np.hypot(xy[:, 0] - sofa["seat"][0], xy[:, 1] - sofa["seat"][1])
    polygon = sofa["footprint"]
    best = np.full(len(xy), np.inf)
    for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
        ab = b - a
        t = np.clip(((xy - a) @ ab) / max(float(ab @ ab), 1e-12), 0, 1)
        best = np.minimum(best, np.linalg.norm(xy - (a + t[:, None] * ab), axis=1))
    return np.where(inside_polygon(xy, polygon), 0.0, best)


def auto_candidates(depth_mm, k, pose, planes, live, m):
    """Pixels the nine points may sit on: every AUTO_GRID_PX-th pixel clear of the picture's edge whose 7 x 7 window
    has depth everywhere, spreads at most AUTO_EDGE_SHARE of what the edge guard allows, at a middle depth, and whose
    point, at the automatic pose, is within auto_stable_m of the scan's surface: stable structure, where the scan saw it.
    {u, v, depth, room, residual}, arrays."""
    import numpy as np
    w = int(m["depth_window_px"])
    depth = depth_mm.astype(np.float64) / 1000
    spread = window_spreads(depth, w)
    edge, step = AUTO_MARGIN_PX, AUTO_GRID_PX
    v, u = np.mgrid[edge:HEIGHT - edge:step, edge:WIDTH - edge:step]
    u, v = u.ravel(), v.ravel()
    windows = np.stack([depth_mm[v + dv, u + du] for dv in range(-w, w + 1) for du in range(-w, w + 1)], 1)
    z = np.median(windows, axis=1) / 1000   # den-register's depth_at: the window's median, all of it valid here
    s = spread[v, u]
    limit = np.maximum(live["edge_min_m"], live["edge_fraction"] * z)
    ok = np.isfinite(s) & (s <= AUTO_EDGE_SHARE * limit) & (z >= AUTO_DEPTH_RANGE_M[0]) & (z <= AUTO_FAR_M)
    u, v, z = u[ok], v[ok], z[ok]
    cam = np.column_stack(((u - k["cx"]) * z / k["fx"], (v - k["cy"]) * z / k["fy"], z))
    room = cam @ pose[:3, :3].T + pose[:3, 3]
    residual = surface_residual(room, planes, reach=1)
    ok = residual <= live["auto_stable_m"]
    found = {"u": u[ok], "v": v[ok], "depth": z[ok], "room": room[ok], "residual": residual[ok]}
    middle = found["depth"] <= AUTO_DEPTH_RANGE_M[1]
    if middle.sum() >= 2 * len(IDS):
        found = {key: value[middle] for key, value in found.items()}
    return found


def auto_choose(found, floor_z, sofa, live):
    """Nine of the candidates: six fit points, one for each of low, mid and high (thirds of the candidates' height
    range) on each side of the picture, each as far as it can be from those already chosen; then three checks, each at
    least AUTO_APART_M from every fit point, the first on fixed structure within auto_sofa_near_m of the sofa when a
    mark is given and the room has one there (not the sofa itself). ({pid: candidate index}, near the sofa: True,
    False or None without a mark), or (None, why)."""
    import numpy as np
    count = len(found["u"])
    if count < len(IDS):
        return None, "only %d points on stable structure, under the %d needed" % (count, len(IDS))
    room = found["room"]
    z = room[:, 2] - floor_z
    lo, hi = float(z.min()), float(z.max())
    band = np.minimum(((z - lo) / max(hi - lo, 1e-6) * 3).astype(int), 2)
    side = (found["u"] >= WIDTH / 2).astype(int)
    taken, chosen = np.zeros(count, dtype=bool), {}

    def farthest(allowed):
        allowed = allowed & ~taken
        if not allowed.any():
            return None
        if chosen:
            gap = np.min(np.linalg.norm(room[:, None, :] - room[list(chosen.values())][None], axis=2), axis=1)
        else:
            gap = np.linalg.norm(room - room.mean(axis=0), axis=1)
        gap = np.where(allowed, gap - found["residual"], -np.inf)
        return int(np.argmax(gap))

    for (pid, role, _), (b, s) in zip(AUTO_POINTS[:6], ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1))):
        index = farthest((band == b) & (side == s))
        index = farthest(np.ones(count, dtype=bool)) if index is None else index
        chosen[pid] = index
        taken[index] = True
    fits = room[[chosen[pid] for pid in IDS[:6]]]
    apart = np.min(np.linalg.norm(room[:, None, :] - fits[None], axis=2), axis=1) >= AUTO_APART_M
    near = None
    if sofa is not None:
        across = sofa_distance(room[:, :2], sofa)
        on_sofa = (across <= AUTO_ON_SOFA_M) & (z < AUTO_SOFA_TOP_M)
        index = farthest(apart & (across <= live["auto_sofa_near_m"]) & ~on_sofa)
        near = index is not None
        if near:
            chosen["c1"] = index
            taken[index] = True
    for pid in CHECKS:
        if pid in chosen:
            continue
        index = farthest(apart)
        if index is None:
            return None, "only %d points on stable structure stand %.0f cm from every fit point, under the 3 checks " \
                         "needed" % (int(apart.sum()), AUTO_APART_M * 100)
        chosen[pid] = index
        taken[index] = True
    return chosen, near


def nearest_scan_point(scan, point, reach=0.10):
    """The scan point nearest point, from those within reach on every axis (all of them when none is)."""
    import numpy as np
    box = np.all(np.abs(scan - point) <= reach, axis=1)
    near = scan[box] if box.any() else scan
    return near[int(np.argmin(np.linalg.norm(near - point, axis=1)))]


def auto_register(R, work, take_dir, scan, revision, area, sofa, m, live):
    """--auto's step 6 and 7: the automatic pose, then nine points written as clicks on the copy's frame and as
    measured_with auto room positions in the worksheet, so den-register's fit, the distance check and the receipt run
    unchanged. Each room position is the scan point nearest the camera point at the automatic pose, the camera point
    taken from the frame exactly as den-register's fit takes it. {search, points, near_sofa}, or {why}."""
    import numpy as np
    take, rows = R.scene.read_take(take_dir)
    n = R.scene.default_frame(take, rows)
    _, row, _, bundle = R.scene.load_frame(take_dir, n)
    depth_mm = np.frombuffer(bundle["depth"], dtype=np.uint16).reshape(HEIGHT, WIDTH)
    k = bundle["intrinsics"]
    search = auto_pose(camera_points(depth_mm.astype(np.float64), k), scan, area, live)
    receipt = {key: value for key, value in search.items() if key not in ("pose", "scan_floor_m")}
    if "why" in search:
        return {"search": receipt, "why": search["why"]}
    pose = search["pose"]
    planes = surface_planes(scan)
    found = auto_candidates(depth_mm, k, pose, planes, live, m)
    chosen, near = auto_choose(found, search["scan_floor_m"], sofa, live)
    if chosen is None:
        return {"search": receipt, "why": "the automatic points: %s" % near}
    clicks, points = {}, []
    for pid, role, label in AUTO_POINTS:
        u, v = int(found["u"][chosen[pid]]), int(found["v"][chosen[pid]])
        depth = R.scene.depth_at(bundle, u, v, window_px=m["depth_window_px"],
                                 min_valid_fraction=m["min_depth_valid_fraction"])
        camera = np.array(R.scene.back_project(k, u, v, depth["z_m"]))
        room = nearest_scan_point(scan, pose[:3, :3] @ camera + pose[:3, 3])
        clicks[pid] = {"u": float(u), "v": float(v)}
        points.append({"id": pid, "role": role, "label": label, "room_xyz_m": [round(float(c), 5) for c in room],
                       "measured_from": {"x": "scan", "y": "scan", "z": "scan"}, "measured_with": AUTO,
                       "scan_xyz_m": None, "note": "auto on the scan's points.ply, content revision %s" % revision})
    sheet = json.loads((work / R.register.WORKSHEET).read_text(encoding="utf-8-sig"))
    sheet["points"] = points
    write_json(work / R.register.WORKSHEET, sheet)
    write_json(R.register.clicks_path(work, take_dir), {
        "schema": R.register.SCHEMA_CLICKS, "take": Path(take_dir).name,
        "frame": {"n": row["n"], "file": row["file"], "sha256": row["sha256"]}, "saved_unix": round(time.time(), 3),
        "clicks": clicks, "chosen_by": "register_now.py --auto"})
    return {"search": {**receipt, "candidates": int(len(found["u"]))}, "near_sofa": near}


# ---- the summary block ----------------------------------------------------------------------------------------------

def summary_lines(receipt, m, live):
    """02-2's block, from the receipt: words and numbers, never a pose, a coordinate or a tape. A step not reached has
    no line."""
    lines = ["Method %s, scan %s." % (Path(receipt["method"]["file"]).stem, receipt.get("scan_revision") or "not picked yet")]
    auto = receipt.get("registration") == AUTO
    if auto:
        lines.append("Registration: automatic (no clicks).")
    if receipt.get("source_correction"):
        lines.append(source_line(receipt["source_correction"], live))
    check = receipt.get("scale_check")
    if check:
        if "floor_pct" in check:
            after = ("den-live scale none" if check["result"] == "none" else "den-live scale x%g" % check["depth_scale"]
                     if check["result"] == "scale" else "STOP: %s" % check.get("why"))
            lines.append("Depth check: floor %+.1f%%, wall %+.1f%%, bend %.1f points; %s."
                         % (check["floor_pct"], check["wall_pct"], check["bend_pts"], after))
        else:
            lines.append("Depth check: STOP: %s." % check["why"])
    edge = receipt.get("edge_guard")
    if auto and receipt.get("auto"):
        lines.append(auto_line(receipt))
    elif edge:
        if edge["named"]:
            guard = "%s on a depth edge" % names(edge["named"])
        elif edge["reclicked"]:
            guard = "re-clicked %s" % names(edge["reclicked"])
        else:
            guard = "clear"
        lines.append("Clicks: %d of %d with depth. Edge guard: %s." % (len(receipt.get("clicked") or []), len(IDS), guard))
    distance = receipt.get("distance_check")
    if distance:
        if distance["named"]:
            lines.append("Distance check: %s disagree%s (median %s cm)." % (
                names(distance["named"]), "s" if len(distance["named"]) == 1 else "",
                ", ".join("%.0f" % (distance["median_m"][pid] * 100) for pid in distance["named"])))
        else:
            lines.append("Distance check: %d agree; median pair difference %s cm." % (distance["features"],
                                                                                    cm(distance["pair_median_m"])))
    fit = receipt.get("fit")
    if fit:
        n = fit.get("numbers") or {}
        if n.get("residual_max_m") is not None:
            checks = n.get("checks_m") or {}
            lines.append("Fit: %d fit + %d check. Residuals %s-%s cm. Leave-one-out worst %s cm. %s %s cm."
                         % (n["n_fit"], n["n_check"], cm(n["residual_min_m"]), cm(n["residual_max_m"]),
                            "-" if n["loo_max_m"] is None else cm(n["loo_max_m"]), "Checks" if auto else "Sofa checks",
                            ", ".join(cm(checks[pid]) if pid in checks else "-" for pid in CHECKS)))
        if auto and "checks_near_sofa" in receipt:
            lines.append(near_sofa_line(receipt["checks_near_sofa"]))
        if fit["result"] == "registered":
            lines.append("REGISTERED %s: gate %g cm, error bound %s cm." % (fit["transform_id"], m["gate_m"] * 100,
                                                                         cm(n.get("error_bound_m") or 0)))
        else:
            lines.append("REFUSED: %s. %s" % (fit.get("named") or "no fit", fit.get("advice") or ""))
    held = receipt.get("held_out")
    if held:
        lines.append(held_line(held))
        if masked_line(held):
            lines.append(masked_line(held))
    lines.append("QUALIFIED: yes" if receipt.get("qualified") else "QUALIFIED: no, because %s." % (
        receipt.get("why_not") or "the paste stopped"))
    return lines


def print_summary(receipt, m, live):
    say("")
    say("Summary (words and numbers only; this block is what to tell an agent):")
    for line in summary_lines(receipt, m, live):
        say("  " + line.rstrip())


# ---- the takes on disk -----------------------------------------------------------------------------------------------

RAW_TAKE = re.compile(r"[0-9]{8}-[0-9]{6}")


def scaled_name(raw, scale):
    """A raw take's median copy at one depth scale: <raw>-x1-m. A new scale gets a new copy, never an edit of the old."""
    return raw.parent / ("%s-x%s-m" % (raw.name, format(scale, "g")))


def whole(doc):
    """Whether a take.json says its take is whole: complete, with at least MIN_FRAMES frames kept. record() writes
    complete false on a take it refused; a take from before that rule may say complete with fewer frames kept."""
    counts = doc.get("counts") if type(doc) is dict else None
    return (type(doc) is dict and doc.get("complete") is True and type(counts) is dict
            and type(counts.get("kept")) is int and counts["kept"] >= MIN_FRAMES)


def newest_raw(work, method):
    """The newest whole take, for --again: one recorded under this method file (its take.json says so), or one a
    den-live-register-now-2 receipt names. A take stopped part way has no take.json, so the newest whole one can be
    older, even registration v1's, whose f1-f4, c1 and c2 were other features: anything else stops. So does a newest
    take record() refused (too few frames): its take.json isn't whole."""
    folder = work / "takes"
    raws = sorted(p for p in folder.iterdir() if RAW_TAKE.fullmatch(p.name) and (p / "take.json").is_file()) if folder.is_dir() else []
    if not raws:
        raise Stop("no earlier take to use again; paste the line without --again")
    raw = raws[-1]
    try:
        doc = json.loads((raw / "take.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if not whole(doc):
        raise Stop("the newest take, %s, isn't whole (it was refused, with fewer than %d frames, or never finished); "
                   "paste the line without --again" % (raw.name, MIN_FRAMES))
    recorded = type(doc.get("method")) is dict and doc["method"].get("file") == method["file"]
    named = any(r.get("schema") == REGISTER_SCHEMA and r.get("raw_take") == raw.name for r in receipts(work))
    if not (recorded or named):
        raise Stop("the newest whole take, %s, wasn't recorded with %s; paste the line without --again"
                   % (raw.name, method["file"]))
    return raw


def median_copies(raw):
    """This take's median copies, <raw>-x<scale>-m. Registration v1's plain copies, <raw>-x<scale>, aren't among them:
    their clicks were v1's six features."""
    pattern = re.compile(re.escape(raw.name) + r"-x[0-9.]+-m")
    return [p for p in raw.parent.glob(raw.name + "-x*") if pattern.fullmatch(p.name) and p.is_dir()]


def carry_clicks(R, work, raw, target):
    """A new copy of the same take (another depth scale): the newest other median copy's clicks, onto the same frame of
    the new copy. The pixels are the same left image; only the frame's hash (its depth changed) is new."""
    copies = sorted((p for p in median_copies(raw) if p != target and R.register.clicks_path(work, p).exists()),
                    key=lambda p: R.register.clicks_path(work, p).stat().st_mtime_ns)
    if not copies:
        return 0
    old = R.register.read_clicks(R.register.clicks_path(work, copies[-1]))
    row = next((r for r in R.scene.read_take(target)[1] if r["n"] == old["frame"].get("n")), None)
    if row is None:
        return 0
    write_json(R.register.clicks_path(work, target), {
        "schema": old["schema"], "take": target.name, "frame": {"n": row["n"], "file": row["file"], "sha256": row["sha256"]},
        "saved_unix": round(time.time(), 3), "clicks": old["clicks"], "carried_from": copies[-1].name})
    return len(old["clicks"])


def receipt_number(path):
    match = re.fullmatch(r"register-now-([0-9]+)\.json", path.name)
    return int(match[1]) if match else 0


def receipts(work):
    """Every receipt in work that reads, newest first."""
    out = []
    for path in sorted(work.glob("register-now-*.json"), key=receipt_number, reverse=True):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def write_receipt(work, receipt):
    taken = [receipt_number(p) for p in work.glob("register-now-*.json")]
    path = work / ("register-now-%d.json" % (max(taken, default=0) + 1))
    write_json(path, receipt)
    return path


def scan_revision(work):
    """The scan revision the worksheet's picks were made on (the scan page writes it into each pick's note), or None
    when they don't all say one. Step 3 holds the registration to the scan it draws on with it."""
    try:
        sheet = json.loads((work / "worksheet.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    found = {str(p.get("note") or "").rpartition("content revision ")[2] for p in sheet.get("points") or []}
    return found.pop() if len(found) == 1 and "" not in found else None


def current_transform(R, work, camera_id="den-oak"):
    """The room model's newest registration of the camera, as a transform id, or None."""
    try:
        models = R.records.read_jsonl(work / R.register.ROOM_MODEL, "room_model_unreadable")
        entry = R.records.camera_entry(R.records.current(models, "den"), camera_id)
    except Exception:  # no room model yet, or one den-register itself refuses: there's no current registration
        return None
    return R.records.transform_id(camera_id, entry["version"]) if entry else None


# ---- the run ------------------------------------------------------------------------------------------------------

class Paste:
    """One paste's state: den-register, the method, the receipt being built, and the ways out."""

    def __init__(self, R, m, live, work, receipt):
        self.R, self.m, self.live, self.work, self.receipt = R, m, live, work, receipt

    def end(self, code, why=None):
        """Write the receipt and print the summary block. why: what keeps it from qualifying."""
        if why and not self.receipt.get("qualified"):
            self.receipt["why_not"] = why
        self.receipt["qualified"] = bool(self.receipt.get("qualified"))
        write_receipt(self.work, self.receipt)
        print_summary(self.receipt, self.m, self.live)
        return code

    def stop(self, why, words=None):
        """A Stop that also writes the receipt and prints the summary block first."""
        self.end(2, why)
        return Stop(words or why)


def automatic(paste, scaled, packs, site, sofa):
    """Steps 6 and 7 with --auto: the active scan and the den's floor, the automatic pose, and nine points written for
    den-register, then the edge guard on them as on clicks. (the scan's points, the den's floor area) for the held-out
    take. A stop is one plain sentence, with the receipt and the summary block first."""
    R, m, live, work, receipt = paste.R, paste.m, paste.live, paste.work, paste.receipt
    revision = active_revision(packs, site) if packs is not None else None
    if revision is None:
        raise paste.stop("no active scan in the content packs", "--auto needs your scan: no active scan in the content "
                         "packs. Nothing was registered")
    scan, why = scan_points(packs, site, revision)
    area, why_area = scan_floor_area(packs, site, revision) if scan is not None else (None, None)
    if why or why_area:
        raise paste.stop(why or why_area, "%s. Nothing was registered" % (why or why_area))
    receipt["scan_revision"] = revision
    ensure_worksheet(R, work, clear=True)
    say("")
    say("Finding the camera in your scan from its own view: no clicks, no scan page. It takes a few seconds, at most "
        "a minute or two.")
    found = auto_register(R, work, scaled, scan, revision, area, sofa, m, live)
    receipt["auto"] = found["search"]
    if "why" in found:
        raise paste.stop(found["why"], "%s. Nothing was registered; register by clicking instead: paste the line "
                                       "without --auto" % found["why"])
    receipt.update(checks=AUTO, checks_near_sofa=found["near_sofa"])
    _, clicks = clicked(R, work, scaled)
    guard = edge_guard(R, work, scaled, clicks, m, live)
    receipt["clicked"] = [pid for pid in IDS if pid in clicks]
    receipt["edge_guard"] = {**guard, "rounds": 0, "reclicked": []}
    say(auto_line(receipt))
    if guard["named"] or len(clicks) != len(IDS):
        raise paste.stop("the automatic points didn't pass the edge guard", "the automatic points didn't pass the edge "
                         "guard. Register by clicking instead: paste the line without --auto")
    return scan, area


def auto_line(receipt):
    search = receipt.get("auto") or {}
    if "share" not in search:
        return "Automatic pose: %s." % (search.get("why") or "not found")
    line = ("Automatic pose: it matches %.0f%% of the camera's view within %.0f cm; the best pose elsewhere, %.0f%%."
            % (search["share"] * 100, AUTO_MATCH_M * 100, search["runner_up"] * 100))
    if receipt.get("clicked"):
        line += " Points: %d of %d on stable structure, clear of depth edges." % (len(receipt["clicked"]), len(IDS))
    return line


def near_sofa_line(near):
    if near is None:
        return "Checks near the sofa: not known; no sofa mark was given (--sofa-mark)."
    if near:
        return "Checks near the sofa: yes, one on stable structure near the sofa mark."
    return "Checks near the sofa: no; the room shows no stable structure the checks could use near the sofa mark."


def clicks_and_picks(paste, raw, scaled, again, clicker, picker, page_extra):
    """Steps 6 and 7 by hand: your nine clicks on the camera's picture, the edge guard, and your nine picks on the
    scan page."""
    R, m, live, work, receipt = paste.R, paste.m, paste.live, paste.work, paste.receipt
    if not again:
        ensure_worksheet(R, work, clear=True)
    else:
        ensure_worksheet(R, work)
        named = next((r.get("distance_check", {}).get("named") for r in receipts(work)
                      if r.get("raw_take") == raw.name and (r.get("distance_check") or {}).get("named")), None)
        if named:
            say("The last paste named %s: re-click %s on the camera's picture if the click was off, and re-pick %s in "
                "your scan if the pick was." % (names(named), "it" if len(named) == 1 else "them",
                                                "it" if len(named) == 1 else "them"))

    # 6: the clicks and the edge guard
    click = clicker or (lambda work, take: click_page(R, work, take, m))
    ever_named = []
    for round_ in range(int(live["edge_rounds"])):
        code = click(work, scaled)
        if code not in (None, 0):
            raise paste.stop("the click page stopped before Done",
                             "the click page stopped before Done; paste with --again to keep the take and the clicks so far")
        _, clicks = clicked(R, work, scaled)
        missing = [pid for pid in IDS if pid not in clicks]
        guard = edge_guard(R, work, scaled, clicks, m, live) if clicks else {"spreads": {}, "named": []}
        ever_named += [pid for pid in guard["named"] if pid not in ever_named]
        receipt["clicked"] = [pid for pid in IDS if pid in clicks]
        receipt["edge_guard"] = {**guard, "rounds": round_ + 1,
                                 "reclicked": [pid for pid in ever_named if pid not in guard["named"] and pid in clicks]}
        say("Clicked %d of %d features on the camera's picture." % (len(clicks), len(IDS)))
        if missing:
            say("%s not clicked with depth: pick another feature for %s if %s spot has none."
                % (names(missing), "that row" if len(missing) == 1 else "those rows", "its" if len(missing) == 1 else "their"))
        say(edge_line(guard["named"]))
        if not missing and not guard["named"]:
            break
        if round_ + 1 < live["edge_rounds"]:
            say("The camera's picture opens again: click %s again, then press Done." % names(missing + guard["named"]))
    else:
        if missing:
            raise paste.stop("%s %s no click with depth" % (names(missing), "has" if len(missing) == 1 else "have"),
                             "%s not clicked with depth on the camera's picture. Each of the nine needs one; pick another "
                             "feature for a row if its spot has no depth. Paste with --again to click the rest" % names(missing))
        raise paste.stop("%s %s on a depth edge" % (names(guard["named"]), "sits" if len(guard["named"]) == 1 else "sit"),
                         "the edge guard still names %s: click a little inside one surface, then paste with --again"
                         % names(guard["named"]))

    # 7: the scan picks
    left_png(R, work, scaled)
    (picker or (lambda work, take: pick_page(work, take, page_extra)))(work, scaled)
    receipt["scan_revision"] = scan_revision(work)


def run(*, root, again, camera_url, token_file, timing, speak, wait, now, method_file=METHOD_FILE, clicker=None,
        picker=None, shower=None, ask=console_ask, packs=None, site=None, page_extra=(), progress=None, auto=False,
        sofa=None):
    """One paste. progress, if given, gets "take" once this paste has a whole take to keep: from then on, --again can
    use it. auto: --auto, no click page and no scan page; sofa: --sofa-mark's record, read by sofa_mark."""
    progress = {} if progress is None else progress
    try:
        import numpy  # noqa: F401  the depth, the planes and the scan
        import PIL.Image  # noqa: F401  the camera's picture beside your scan
    except ImportError as exc:
        raise Stop("this paste needs Python's %s (pip install numpy pillow); nothing was recorded" % exc.name) from None
    R = den_register(sc.archive(root / "tools", "register"))
    m, live = load_method(R, method_file)
    method = {"file": Path(method_file).name, "sha256": m.sha256}
    work = (root / "private" / "register").resolve()
    if R.take.inside_checkout(work):
        raise Stop("the data root is inside a git checkout; frames never go into git")
    if R.take.in_onedrive(work):
        raise Stop("the data root is inside OneDrive; frames stay on this PC only")
    work.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": REGISTER_SCHEMA, "started_unix": round(now, 3), "again": again, "method": method, "live": live,
               "registration": AUTO if auto else "clicked", "depth_scale": None, "qualified": False}
    paste = Paste(R, m, live, work, receipt)
    cameras = []

    def camera():
        if not cameras:
            cameras.append(sc.open_camera(R.take, camera_url, token_file, sc.archive(root / "tools", "observe")))
        return cameras[0]

    # 1 and 2: the tapes and the take, or the newest take again with its tapes
    if again:
        raw = newest_raw(work, method)
        progress["take"] = raw.name
        tapes = known_tapes(work, raw)
        if tapes is None:
            say("No tapes were kept for the take %s." % raw.name)
            tapes = ask_tapes(ask)
            keep_tapes(raw, tapes)
        say("Using the take %s again, with its tapes." % raw.name)
    else:
        camera()   # the token first: a missing one stops before any question
        tapes = ask_tapes(ask)
        raw = work / "takes" / time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        say("Frames go to %s" % raw)
        frames = record(R, camera(), raw, timing, speak, wait, method=method)
        keep_tapes(raw, tapes)   # now, however this paste ends: --again reads them from here
        progress["take"] = raw.name
        shutil.copyfile(raw / "camera-calibration.json", work / R.register.CAMERA_CALIBRATION)
        say("Kept %d frames." % frames)
    receipt.update(tapes=tapes, raw_take=raw.name)

    # 3: the source check
    try:
        receipt["source_correction"] = check_source(R, raw, live)
    except Stop as exc:
        write_receipt(work, receipt)
        raise exc
    if again and camera_correction(R, camera(), wait) != correction_of(receipt["source_correction"]):
        # A camera release changed its correction since this take: den-register would find these clicks registered
        # already, and the shortcut at step 9 would qualify the old correction again, which den-live refuses.
        raise Stop("the camera's depth correction isn't the one the take %s was made on: paste the line without --again, "
                   "which records a new take" % raw.name)

    # 4: the depth check on the take's median
    plane, k, used = median_plane(R, raw, live["median_min_valid"])
    check = scale_check(camera_points(plane, k), tapes, live)
    receipt["scale_check"] = check
    say(depth_line(check))
    if check["result"] == "stop":
        raise paste.stop(check["why"], "depth check: %s. Nothing was clicked. Tell an agent the summary block" % check["why"])
    scale = check["depth_scale"]
    receipt["depth_scale"] = scale

    # 5: the median copy at that scale
    scaled = scaled_name(raw, scale)
    if not (scaled / "take.json").is_file():
        median_copy(R, raw, scaled, plane, scale, method, used)
        carried = carry_clicks(R, work, raw, scaled) if again and not auto else 0
        say("The take's median depth, x%g, is in %s%s." % (scale, scaled.name,
                                                           "; %d clicks carried over" % carried if carried else ""))
    elif json.loads((scaled / "take.json").read_text(encoding="utf-8"))["derived"]["depth_scale"] != scale:
        raise paste.stop("%s doesn't hold depth x%g" % (scaled.name, scale),
                         "%s doesn't hold depth x%g; paste the line without --again" % (scaled.name, scale))
    receipt["take"] = scaled.name
    if auto:
        scan, area = automatic(paste, scaled, packs, site, sofa)
    else:
        clicks_and_picks(paste, raw, scaled, again, clicker, picker, page_extra)
        scan = area = None

    # 8: the distance check
    pairs = feature_pairs(R, work, scaled, m)
    distance = distance_check(pairs, live, m["gate_m"])
    receipt["distance_check"] = distance
    say(distance_line(distance, live))
    if distance["named"]:
        found = own_scale([{"id": pid, "camera_xyz_m": c, "room_xyz_m": r} for pid, (c, r) in pairs.items()])
        receipt["own_scale"] = found
        if depth_scale_to_blame(found, live, m["gate_m"]):
            # Every pair is off in proportion: one scale explains them all, so it's the depth (or a tape), not a click.
            say("Every feature fits with their own scale, %.3f: it's the depth scale or a tape, which re-clicking won't "
                "fix. Tell an agent the summary block." % found["scale"])
            return paste.end(1, "the features' own scale is %.3f: the depth scale or a tape is off" % found["scale"])
        return paste.end(1, "the distance check named %s" % names(distance["named"]))

    # 9: the fit, then the sofa checks (with --auto, the automatic checks)
    say("")
    R.register.METHOD = AUTO_METHOD_LABEL if auto else METHOD_LABEL   # in memory, as den_register sets it
    code, report = R.register.run_fit(work, scaled, m=m, out=Lines())
    reasons = [r["reason"] for r in report.get("reasons", [])]
    numbers = fit_numbers(report)
    found = own_scale(report.get("pairs", []))
    receipt["own_scale"] = found
    fit = receipt["fit"] = {"result": report["result"], "transform_id": report.get("transform_id"), "reasons": reasons,
                            "numbers": numbers}
    for line in scale_lines(found, scale, live):
        say(line)
    if reasons == ["already_registered"]:
        match = re.search(r"registered as (\S+)", report["reasons"][0].get("detail", ""))
        earlier = match.group(1) if match else None
        if earlier is None or earlier != current_transform(R, work):
            fit.update(result="refused", named="already registered as %s, not the current registration" % (earlier or "before"),
                       advice="Paste without --again." if auto else
                       "Re-click or re-pick a feature, or paste without --again.")
            return paste.end(1, "these %s registered an earlier registration"
                             % ("points" if auto else "clicks and picks"))
        before = next((r for r in receipts(work) if (r.get("fit") or {}).get("transform_id") == earlier
                       and (r.get("fit") or {}).get("result") == "registered"), None)
        say("Already registered as %s, from these same %s." % (earlier, "points" if auto else "clicks and picks"))
        if before and before.get("qualified") is True and before.get("depth_scale") == scale:
            say("It's qualified already: nothing changed, nothing to do.")
            fit.update(result="registered", transform_id=earlier)
            receipt.update(sofa_check_errors_m=before.get("sofa_check_errors_m"), held_out=before.get("held_out"),
                           qualified=True)
            return paste.end(0)
        fit.update(result="registered", transform_id=earlier)
    elif report["result"] != "registered":
        named = ", ".join("%s x%d" % (r, reasons.count(r)) if reasons.count(r) > 1 else r
                          for r in dict.fromkeys(reasons)) or "no fit"
        if depth_scale_to_blame(found, live, m["gate_m"]):
            advice = ("With their own scale, %.3f, every feature fits within %g cm, so it's the depth scale, which "
                      "re-clicking won't fix. Tell an agent this block." % (found["scale"], m["gate_m"] * 100))
        elif auto:
            advice = "Register by clicking instead: paste the line without --auto."
        else:
            advice = "Re-click or re-pick what it names, then paste again with --again."
            if found and found["worst_m"] > m["gate_m"]:
                advice += " %s sits furthest from the rest (%.0f cm)." % (found["worst_id"], found["worst_m"] * 100)
        fit.update(named=named, advice=advice)
        return paste.end(1, "the fit was refused")
    transform_id = fit["transform_id"]
    checks = {pid: numbers["checks_m"].get(pid) for pid in CHECKS}
    receipt["sofa_check_errors_m"] = checks   # the name kept for den-live; with --auto, the automatic checks
    over = [pid for pid in CHECKS if checks[pid] is None or checks[pid] > live["sofa_check_max_m"]]

    def show():
        if auto:
            return   # no scan page with --auto
        try:
            (shower or (lambda work, take: show_page(work, take, page_extra)))(work, scaled)
        except KeyboardInterrupt:
            say("The registration stands: %s." % transform_id)

    if over:
        code = paste.end(1, "; ".join("%s %s is %s cm, over %.0f cm" % ("check" if auto else "sofa check", pid,
                                                                       "-" if checks[pid] is None else cm(checks[pid]),
                                                                       live["sofa_check_max_m"] * 100) for pid in over))
        show()
        return code

    # 10: the held-out take
    if auto:
        points, why = scan, None
    else:
        points, why = (scan_points(packs, site, receipt["scan_revision"]) if packs is not None
                       else (None, "no content packs"))
    if points is not None and area is None:
        area, why = scan_floor_area(packs, site, receipt["scan_revision"])
        points = points if area is not None else None
    if points is None:
        receipt["held_out"] = {"take": None, "within_noise": None, "worst_bin_m": None, "ok": False, "why": why}
        code = paste.end(1, "the held-out take couldn't be checked: %s" % why)
        show()
        return code
    say("")
    say("Now the held-out take: the empty den again, checked against your scan at the new registration.")
    held = work / "takes" / (time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time())) + "-held")
    try:
        record(R, camera(), held, timing, speak, wait, cues=HELD_OUT_CUES, script="den-live-register-held-out", method=method)
        held_source = check_source(R, held, live, quiet=True)
    except Stop as exc:
        receipt["held_out"] = {"take": held.name, "within_noise": None, "worst_bin_m": None, "ok": False,
                               "why": "the held-out take stopped"}
        raise paste.stop("the held-out take stopped: %s" % exc, "the held-out take stopped: %s. The registration "
                         "stands, unqualified; paste with --again to take the held-out take again" % exc) from None
    if correction_of(held_source) != correction_of(receipt["source_correction"]):
        # The camera's correction changed since the registration's take (a camera release): the receipt would qualify
        # a correction den-live then refuses on every frame.
        receipt["held_out"] = {"take": held.name, "within_noise": None, "worst_bin_m": None, "ok": False,
                               "why": "made on another camera depth correction than the registration's take",
                               "source_frames": held_source["frames"]}
        say(held_line(receipt["held_out"]))
        code = paste.end(1, "the held-out take was made on another camera depth correction than the registration's "
                            "take: paste the line without --again, which records a new take")
        show()
        return code
    numbers = held_out(R, work, held, transform_id, scale, points, live, area)
    receipt["held_out"] = {"take": held.name, **numbers, "source_frames": held_source["frames"]}
    say(held_line(numbers))
    if masked_line(numbers):
        say(masked_line(numbers))
    receipt["qualified"] = bool(numbers["ok"])
    code = paste.end(0 if numbers["ok"] else 1, None if numbers["ok"] else "the held-out take has %s" % numbers["why"])
    show()
    return code


def main(argv=None, *, root=None, camera_url=None, token_file=None, timing=Timing(), speak=None, wait=time.sleep,
         console=sc.has_console, now=time.time, method_file=METHOD_FILE, clicker=None, picker=None, shower=None,
         ask=console_ask, packs=None, site=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--again", action="store_true", help="keep the newest take, its tapes, your clicks and picks")
    parser.add_argument("--packs", type=Path, help="content packs for the scan page and the held-out check")
    parser.add_argument("--auto", action="store_true", help="register with no clicks and no scan page")
    parser.add_argument("--sofa-mark", type=Path, help="with --auto: your sofa mark, to put a check near the sofa")
    args = parser.parse_args(argv)
    if not console():   # a background run is refused before anything: no camera
        say("register: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    if args.sofa_mark and not args.auto:
        say("register: refused, --sofa-mark goes with --auto. Nothing was recorded.")
        return 2
    root = Path(root) if root else sc.data_root()
    progress = {}
    try:
        import run_local   # the den's site and the workstation's content packs, read where step 3 reads them
        R = den_register(sc.archive(root / "tools", "register"))
        packs = args.packs or packs or run_local.content_packs()
        sofa = sofa_mark(args.sofa_mark) if args.sofa_mark else None   # read first: a bad file stops before the camera
        return run(root=root, again=args.again, camera_url=camera_url or R.take.DEFAULT_CAMERA,
                   token_file=token_file or R.take.DEFAULT_TOKEN_FILE, timing=timing,
                   speak=speak or sc.speaker(R.take), wait=wait, now=now(), method_file=method_file, clicker=clicker,
                   picker=picker, shower=shower, ask=ask, packs=packs, site=site or run_local.den_site(),
                   page_extra=("--packs", str(args.packs)) if args.packs else (), progress=progress, auto=args.auto,
                   sofa=sofa)
    except Stop as exc:
        say("register: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        if progress.get("take"):
            say("register: interrupted. Paste the line again (with --again to keep the take) when you're ready.")
        else:
            say("register: interrupted before the take was whole, so nothing is kept. Paste the line again, without "
                "--again, when you're ready.")
        return 130
    except Exception as exc:
        if type(exc).__name__ in ("Refused", "NotUtf8") and hasattr(exc, "reason"):
            # den-register's own refusal outside the fit (a worksheet saved as UTF-16, a frame it can't use): its reason
            # and detail name files and points, never a coordinate.
            say("register: stopped: den-register refused, %s: %s" % (exc.reason, scrub(str(exc.detail)[:300]) or ""))
            return 2
        say("register: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
