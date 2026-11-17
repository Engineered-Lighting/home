#!/usr/bin/env python3
"""Step 2 of the live den view: register the re-aimed den camera to your scan. The owner's paste. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py
    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\register_now.py --again

The first line records a new take and starts the clicks and scan picks afresh. --again keeps the newest take, your
clicks and your scan picks, so after a failed gate you re-click only what the fit named, and fit again. If the method
file's depth scale changed since, --again makes a new scaled copy of the same take and carries your clicks onto it.

1. A 20 s take of the empty den. It speaks the cues: leave the camera's view, recording, done. It reads the camera's
   /frame.npz about 5 times a second with den-take's own client and token file, keeps each bundle byte for byte with
   den-take's TakeWriter, and saves the camera's /calibration reply. Everything goes under
   <data root>\\private\\register\\. The data root is DEN_LIVE_HOME, or else %USERPROFILE%\\den-live. Agents never open
   that folder.
2. The source check: since 2026-11-11 the camera service corrects depth itself (x0.889) and says so in every frame's
   depth_correction record. Any frame that doesn't say applied stops the paste here.
   Then a copy of the take with each depth_mm plane multiplied by the method file's depth_scale (1.0: no factor of
   den-live's own), 0 kept as 0, every other byte the same, frames.csv rewritten with the new hashes, and take.json
   naming the source and the scale. It's named for its scale (<take>-x1). den-register reads only this copy.
3. The floor guard: a RANSAC floor plane in the depth, the lens's height above it against your tape. Off by more than
   5%, it stops before any clicking.
4. den-register's own click page (unedited, at the den simulation's pin): click six features on the camera's picture.
5. The scan page, on 127.0.0.1: Home's own code on your scan. Click the same six features there. Home's display-mesh
   picker gives each one's room position, written to the worksheet as measured_with scan-pick.
6. den-register's fit, with REGISTER-LIVE-20261111.md as its method: gate 0.10 m.
7. The features' own scale: what a similarity fit of the six would pick. Printed, never used, except to say whether
   a refusal is the depth scale (every feature fits with that scale) or a click (one doesn't).
8. On a pass, the scan page again, showing where the fit puts the camera and your six features.

den-register prints the camera's place and each residual, for you. Tell an agent the REGISTERED or REFUSED line, the
reasons under it, and the summary block. The output is ASCII.
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
import threading
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

say, Stop = sc.say, sc.Stop
METHOD_FILE = HERE / "REGISTER-LIVE-20261111.md"
PICK_PAGE = HERE / "scan_pick.mjs"
LIVE_BLOCK = "den-live-v1"
LIVE_KEYS = ("depth_scale", "require_source_correction", "lens_height_tape_m", "floor_guard_fraction",
             "floor_min_below_m", "floor_max_tilt_deg", "floor_band_m", "floor_min_share", "own_scale_band")
SCAN_PICK = "scan-pick"
METHOD_LABEL = "scan-pick + fit"
POINTS = (("f1", "fit", "floor level, on the left of the camera's view"),
          ("f2", "fit", "floor level, on the right of the camera's view"),
          ("f3", "fit", "table height"),
          ("f4", "fit", "high up: a door frame or a shelf"),
          ("c1", "check", "table height, away from f3"),
          ("c2", "check", "high up, away from f4"))
WIDTH, HEIGHT = 640, 400
GUARD_FRAMES, RANSAC_TRIES, SAMPLE_STEP = 15, 400, 2


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


# ---- the method file ------------------------------------------------------------------------------------------

def read_live(text):
    """REGISTER-LIVE's den-live-v1 block: every slot a number, none open. ValueError names what is wrong."""
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
    if not 0.5 <= values["depth_scale"] <= 1.5 or not 0.5 <= values["lens_height_tape_m"] <= 3.0:
        raise ValueError("depth_scale or lens_height_tape_m is outside what a den camera could be")
    if values["require_source_correction"] not in (0, 1):
        raise ValueError("require_source_correction is 0 or 1")
    return values


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


def check_source(R, take_dir, live):
    found = source_correction(R, take_dir)
    if not live["require_source_correction"]:
        say("Depth correction at the camera: not required by this method file (%d of %d frames say applied)."
            % (found["applied"], found["frames"]))
        return found
    if found["applied"] != found["frames"] or len(found["factors"]) != 1:
        raise Stop("the camera's depth isn't corrected at its source on every frame: %d of %d say applied%s. Nothing "
                   "was clicked. Tell an agent this line"
                   % (found["applied"], found["frames"], "; " + "; ".join(found["why"]) if found["why"] else
                      "; they name %d different factors" % len(found["factors"])))
    say("Depth corrected at the camera: x%g on all %d frames." % (found["factors"][0], found["frames"]))
    return found


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


# ---- den-register and den-take, at the den simulation's pin -----------------------------------------------------

def den_register(tree):
    """den-register's modules from its pinned archive, its labels set in memory only, as den-sim's prepare.py does."""
    for sub in ("den-take", "den-register"):
        folder = str(Path(tree) / "tools" / sub)
        if folder not in sys.path:
            sys.path.insert(0, folder)
    import check
    import method
    import register
    import rig
    import scene
    import take
    import worksheet
    register.METHOD = METHOD_LABEL
    if SCAN_PICK not in worksheet.TOOLS:
        worksheet.TOOLS = (*worksheet.TOOLS, SCAN_PICK)
    return SimpleNamespace(check=check, method=method, register=register, rig=rig, scene=scene, take=take,
                           worksheet=worksheet)


# ---- 1. the clear-room take ---------------------------------------------------------------------------------------

def record(R, camera, folder, timing, speak, wait):
    """A den-take-1 take of the empty den. Its cues begin and hold_phone_end bracket the window, so den-register's
    default frame is the window's middle. No slates: nobody is in view to hold one."""
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
    doc = {"schema": R.take.SCHEMA, "take": folder.name, "script": "den-live-register-clear-room",
           "recorded_by": "tools/den-live/register_now.py", "camera": "http://%s:%s" % (camera.host, camera.port),
           "pc_clock": "time.perf_counter", "started_unix": time.time(), "ended_unix": None, "cues": [], "slates": [],
           "note": "No slates: the room is clear. The begin and hold_phone_end cues bracket the clear-room window.",
           "counts": None, "stop_reason": None, "complete": False, "sha256": None}

    def cue(name, text):
        doc["cues"].append({"cue": name, "text": text, "pc_mono_s": R.take.clock(), "pc_unix": time.time()})
        speak(text)

    grab.start()
    try:
        cue("leave", "Registration take. Please leave the den, or step out of the camera's view. Recording starts in "
                     "%d seconds." % round(timing.lead_s))
        wait(timing.lead_s)
        start = R.take.clock()
        grab.since = start
        grab.recording.set()
        cue("begin", "Recording the empty room.")
        wait(timing.window_s)
        grab.recording.clear()
        cue("hold_phone_end", "Done. You can come back.")
    finally:
        grab.halt.set()
        grab.join(10)
        writer.close()
    if grab.stop_reason:
        raise Stop(grab.stop_reason)
    doc["ended_unix"] = time.time()
    doc["counts"] = {"kept": len(writer.index), "errors": grab.errors, "sessions": len(set(grab.sessions.values()))}
    doc["complete"] = True
    doc["sha256"] = {"frames.csv": sha256_bytes((folder / "frames.csv").read_bytes())}
    write_json(folder / "take.json", doc)
    if len(writer.index) < 10:
        raise Stop("only %d frames came from the camera in %d s" % (len(writer.index), round(timing.window_s)))
    return len(writer.index)


# ---- 2. the scaled copy ---------------------------------------------------------------------------------------------

def scale_bundle(R, data, scale):
    """The bundle with depth_mm.npy's values times scale (0 stays 0), its .npy header and every other member as they
    were, each with its own compression."""
    import numpy as np
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            raw = source.read(info)
            if info.filename == "depth_mm.npy":
                descr, shape, body = R.check._npy(raw, info.filename)
                if descr != "<u2" or shape != (HEIGHT, WIDTH):
                    raise Stop("a depth plane is not %dx%d millimetres" % (WIDTH, HEIGHT))
                depth = np.frombuffer(body, dtype="<u2").astype(np.float64)
                scaled = np.where(depth > 0, np.clip(np.rint(depth * scale), 1, 65535), 0).astype("<u2")
                raw = raw[:len(raw) - len(body)] + scaled.tobytes()
            member = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            member.compress_type, member.external_attr = info.compress_type, info.external_attr
            target.writestr(member, raw)
    return out.getvalue()


def scaled_copy(R, source, target, scale, method):
    """Write the scaled take at target. take.json says where it came from and what changed."""
    rows = list(csv.DictReader(io.StringIO((source / "frames.csv").read_text(encoding="utf-8"), newline="")))
    fields = list(rows[0].keys()) if rows else list(R.take.CSV_FIELDS)
    target.mkdir(parents=True)
    (target / "frames").mkdir()
    for row in rows:
        data = (source / row["file"]).read_bytes()
        if sha256_bytes(data) != row["npz_sha256"]:
            raise Stop("frame %s of the take changed since it was recorded" % row["n"])
        scaled = scale_bundle(R, data, scale)
        (target / row["file"]).write_bytes(scaled)
        row["npz_sha256"], row["bytes"] = sha256_bytes(scaled), str(len(scaled))
    text = io.StringIO()
    writer = csv.DictWriter(text, fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    (target / "frames.csv").write_text(text.getvalue(), encoding="utf-8", newline="")
    doc = json.loads((source / "take.json").read_text(encoding="utf-8"))
    source_csv = sha256_bytes((source / "frames.csv").read_bytes())
    doc["take"] = target.name
    doc["derived"] = {"from_take": source.name, "from_frames_csv_sha256": source_csv,
                      "depth_scale": scale, "changed": "depth_mm.npy in every bundle: value x depth_scale, rounded; 0 stays 0",
                      "method_file": method["file"], "method_sha256": method["sha256"]}
    doc["sha256"] = {"frames.csv": sha256_bytes((target / "frames.csv").read_bytes())}
    write_json(target / "take.json", doc)
    shutil.copyfile(source / "camera-calibration.json", target / "camera-calibration.json")
    return len(rows)


# ---- 3. the floor guard ---------------------------------------------------------------------------------------------

def guard_points(R, take_dir, count=GUARD_FRAMES):
    """Camera-frame points (rectified left: +x right, +y down, +z forward) of the per-pixel median depth over the
    count frames nearest the take's middle, every SAMPLE_STEP-th pixel."""
    import numpy as np
    take, rows = R.scene.read_take(take_dir)
    middle = R.scene.default_frame(take, rows)
    by_n = sorted(rows, key=lambda row: abs(row["n"] - middle))[:count]
    planes, k = [], None
    for row in by_n:
        try:
            bundle = R.scene.read_bundle((Path(take_dir) / row["file"]).read_bytes())
        except R.scene.Refused:
            continue
        planes.append(np.frombuffer(bundle["depth"], dtype=np.uint16).reshape(HEIGHT, WIDTH)[::SAMPLE_STEP, ::SAMPLE_STEP])
        k = bundle["intrinsics"]
    if not planes:
        raise Stop("no frame of the take reads")
    stack = np.stack(planes).astype(np.float64)
    stack[stack == 0] = np.nan
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            z = np.nanmedian(stack, axis=0) / 1000.0
    v, u = np.mgrid[0:HEIGHT:SAMPLE_STEP, 0:WIDTH:SAMPLE_STEP]
    ok = np.isfinite(z)
    z, u, v = z[ok], u[ok].astype(np.float64), v[ok].astype(np.float64)
    return np.column_stack(((u - k["cx"]) * z / k["fx"], (v - k["cy"]) * z / k["fy"], z)), len(planes)


def floor_plane(points, live, tries=RANSAC_TRIES, seed=0):
    """The floor: among near-level planes RANSAC finds, the lowest whose support is at least half the best one's,
    refined by least squares on its inliers. Returns (height_m, share, pitch_deg, roll_deg) or None."""
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
    return {"height_m": d, "share": share, "pitch_down_deg": math.degrees(math.asin(max(-1.0, min(1.0, -n[2])))),
            "roll_deg": math.degrees(math.atan2(n[0], -n[1])), "low_points": int(len(low))}


def floor_guard(R, take_dir, live):
    points, frames = guard_points(R, take_dir)
    plane = floor_plane(points, live)
    tape = live["lens_height_tape_m"]
    if plane is None or plane["share"] < live["floor_min_share"]:
        share = "no level plane" if plane is None else "%d%% of the low points" % round(plane["share"] * 100)
        return {"ok": False, "frames": frames, "plane": plane, "tape_m": tape,
                "why": "the guard can't find the floor in the camera's view (%s; it needs %d%%)"
                       % (share, round(live["floor_min_share"] * 100))}
    off = plane["height_m"] / tape - 1
    ok = abs(off) <= live["floor_guard_fraction"]
    return {"ok": ok, "frames": frames, "plane": plane, "tape_m": tape, "off": off,
            "why": None if ok else "the floor is %.2f m below the lens with depth x%g, and your tape says %.2f m: %+.1f%%, "
                                   "past %g%%" % (plane["height_m"], live["depth_scale"], tape, off * 100,
                                                  live["floor_guard_fraction"] * 100)}


def guard_lines(guard, live):
    plane = guard["plane"]
    if not plane:
        return ["Floor guard: no level plane found in the empty room's depth."]
    raw = plane["height_m"] / live["depth_scale"]
    lines = ["Floor guard: the floor is %.2f m below the lens with depth x%g (%.2f m raw); your tape says %.2f m"
             % (plane["height_m"], live["depth_scale"], raw, guard["tape_m"])
             + (": %+.1f%%, inside %g%%." % (guard["off"] * 100, live["floor_guard_fraction"] * 100) if guard["ok"] else ".")]
    lines.append("  The floor plane holds %d%% of the low points; the camera points %.1f degrees down, rolled %.1f."
                 % (round(plane["share"] * 100), plane["pitch_down_deg"], plane["roll_deg"]))
    return lines


# ---- 4. the worksheet and the clicks ----------------------------------------------------------------------------------

def ensure_worksheet(R, work, *, clear=False):
    """den-register's worksheet with six features and no positions yet. One already there is kept; clear=True (a fresh
    paste, whose new take may be clicked on other features) empties its scan picks."""
    path = work / R.register.WORKSHEET
    if path.exists():
        if clear:
            sheet = json.loads(path.read_text(encoding="utf-8-sig"))
            for point in sheet.get("points") or []:
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
    say("Now the camera's picture opens in your browser. Click six features the camera sees, one per row:")
    for pid, role, label in POINTS:
        say("  %s (%s): %s" % (pid, role, label))
    say("Pick solid, matte corners or edges with depth: a rug or skirting corner, a table corner, a door frame or "
        "shelf edge. Not glass, screens or black fabric. Each click shows the depth it found; depth_missing means "
        "click a little to the side. Remember what each one is: you click the same six in your scan next. Press Done "
        "on the page when all six are in.")
    return R.register.run_click(work, take_dir, m=m, out=sys.stdout, open_browser=True)


def left_png(R, work, take_dir):
    """The clicked frame's left image as a PNG beside the worksheet, for the scan page's reminder of what you clicked."""
    from PIL import Image
    data, _ = clicked(R, work, take_dir)
    n = data["frame"]["n"] if data else R.register.frame_number(take_dir, None, work)
    bundle = R.scene.load_frame(take_dir, n)[3]
    (work / "pick").mkdir(exist_ok=True)
    Image.frombytes("L", (WIDTH, HEIGHT), bytes(bundle["left"])).save(work / "pick" / "left.png")


# ---- 5 and 8. the scan page ----------------------------------------------------------------------------------------

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


def pick_page(work, take_dir, **kw):
    say("")
    say("Now your scan opens in the browser, beside the camera's picture with your clicks numbered. For each row, "
        "orbit to the same feature and click it once, without dragging. Re-pick any row by selecting it again. "
        "Press Save when all six are in.")
    code = scan_page(work, take_dir, "pick", **kw)
    if code != 0:
        raise Stop("the scan page closed without saving the picks")


# ---- 7. the features' own scale ---------------------------------------------------------------------------------------

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
    line = ("Features' own scale: %.3f from %d features, on depth already x%g (so %.3f on the raw depth). Printed only."
            % (found["scale"], found["features"], scale, found["scale"] * scale))
    if abs(found["scale"] - 1) > live["own_scale_band"]:
        return [line, "  That's outside 1 +/- %g: the depth scale doesn't hold at these features' depths, or a feature "
                      "is clicked on the wrong thing in one of the two pictures." % live["own_scale_band"]]
    return [line]


def depth_scale_to_blame(found, live, gate):
    """The depth scale, not a click: the features' own scale is off, and with that scale every feature fits inside
    the gate. One badly placed feature moves the scale too, but stays outside the gate."""
    return bool(found) and abs(found["scale"] - 1) > live["own_scale_band"] and found["worst_m"] <= gate


# ---- the run ------------------------------------------------------------------------------------------------------

RAW_TAKE = re.compile(r"[0-9]{8}-[0-9]{6}")


def scaled_name(raw, scale):
    """A raw take's copy at one depth scale: <raw>-x1. A new scale gets a new copy, never an edit of the old one."""
    return raw.parent / ("%s-x%s" % (raw.name, format(scale, "g")))


def newest_raw(work):
    folder = work / "takes"
    raws = sorted(p for p in folder.iterdir() if RAW_TAKE.fullmatch(p.name) and (p / "take.json").is_file()) if folder.is_dir() else []
    if not raws:
        raise Stop("no earlier take to use again; paste the line without --again")
    return raws[-1]


def carry_clicks(R, work, raw, target):
    """--again after the depth scale changed: the newest other copy's clicks, onto the same frame of the new copy.
    The pixels are the same left image; only the frame's hash (its depth changed) is new."""
    copies = sorted((p for p in raw.parent.glob(raw.name + "-x*") if p != target and R.register.clicks_path(work, p).exists()),
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


def use_again(R, work, live, method):
    """The newest take at the method's depth scale: its copy if there is one, else a new copy with the clicks carried."""
    raw = newest_raw(work)
    scaled = scaled_name(raw, live["depth_scale"])
    if not (scaled / "take.json").is_file():
        scaled_copy(R, raw, scaled, live["depth_scale"], method)
        carried = carry_clicks(R, work, raw, scaled)
        say("The depth scale is now x%g, so the take %s got a new copy, %s; %d clicks carried over."
            % (live["depth_scale"], raw.name, scaled.name, carried))
    elif json.loads((scaled / "take.json").read_text(encoding="utf-8"))["derived"]["depth_scale"] != live["depth_scale"]:
        raise Stop("%s doesn't hold depth x%g; paste the line without --again" % (scaled.name, live["depth_scale"]))
    say("Using the take %s again, depth x%g." % (raw.name, live["depth_scale"]))
    return raw, scaled


def run(*, root, again, camera_url, token_file, timing, speak, wait, now, method_file=METHOD_FILE, clicker=None,
        picker=None, shower=None):
    try:
        import numpy  # noqa: F401  the depth, the floor and the features' scale
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
    receipt = {"schema": "den-live-register-now-1", "started_unix": round(now, 3), "again": again, "method": method,
               "live": live}
    if again:
        raw, scaled = use_again(R, work, live, method)
    else:
        camera = sc.open_camera(R.take, camera_url, token_file, sc.archive(root / "tools", "observe"))
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        raw = work / "takes" / stamp
        say("Frames go to %s" % raw)
        frames = record(R, camera, raw, timing, speak, wait)
        scaled = scaled_name(raw, live["depth_scale"])
        scaled_copy(R, raw, scaled, live["depth_scale"], method)
        shutil.copyfile(raw / "camera-calibration.json", work / R.register.CAMERA_CALIBRATION)
        say("Kept %d frames; depth scaled x%g in %s." % (frames, live["depth_scale"], scaled.name))
    receipt.update(raw_take=raw.name, take=scaled.name)
    try:
        receipt["source_correction"] = check_source(R, raw, live)
    except Stop:
        write_receipt(work, receipt)
        raise
    guard = floor_guard(R, scaled, live)
    receipt["floor_guard"] = guard
    for line in guard_lines(guard, live):
        say(line)
    if not guard["ok"]:
        write_receipt(work, receipt)
        raise Stop("floor guard: %s. Nothing was clicked. Tell an agent this line" % guard["why"])
    ensure_worksheet(R, work, clear=not again)
    code = (clicker or (lambda work, take: click_page(R, work, take, m)))(work, scaled)
    if code not in (None, 0):
        raise Stop("the click page stopped before Done; paste with --again to keep the take and the clicks so far")
    _, clicks = clicked(R, work, scaled)
    receipt["clicked"] = sorted(clicks)
    say("Clicked %d of %d features on the camera's picture." % (len(clicks), len(POINTS)))
    if len(clicks) < len(POINTS):
        write_receipt(work, receipt)
        raise Stop("%s not clicked with depth on the camera's picture. Each of the six needs one; pick another feature "
                   "for a row if its spot has no depth. Paste with --again to click the rest"
                   % ", ".join(pid for pid, _, _ in POINTS if pid not in clicks))
    left_png(R, work, scaled)
    (picker or (lambda work, take: pick_page(work, take)))(work, scaled)
    say("")
    code, report = R.register.run_fit(work, scaled, m=m, out=sys.stdout)
    reasons = [r["reason"] for r in report.get("reasons", [])]
    receipt["fit"] = {"result": report["result"], "transform_id": report.get("transform_id"), "reasons": reasons}
    found = own_scale(report.get("pairs", []))
    receipt["own_scale"] = found
    receipt["scan_revision"] = scan_revision(work)
    write_receipt(work, receipt)
    say("")
    say("Summary (words and numbers only; this block is what to tell an agent):")
    for line in guard_lines(guard, live) + scale_lines(found, live["depth_scale"], live):
        say("  " + line)
    fit = report.get("fit") or {}
    if report["result"] == "registered":
        say("  REGISTERED %s: gate %g cm, error bound %.1f cm." % (report["transform_id"], m["gate_m"] * 100,
                                                                   fit.get("error_bound_m", float("nan")) * 100))
        try:
            (shower or (lambda work, take: show_page(work, take)))(work, scaled)
        except KeyboardInterrupt:
            say("The registration stands: %s." % report["transform_id"])
        return code
    if reasons == ["already_registered"]:
        detail = report["reasons"][0].get("detail", "")
        match = re.search(r"registered as (\S+)", detail)
        say("  Already registered as %s, from these same clicks and picks: nothing changed, nothing to do."
            % (match.group(1) if match else "before"))
        return 0
    named = ", ".join("%s x%d" % (r, reasons.count(r)) if reasons.count(r) > 1 else r
                      for r in dict.fromkeys(reasons)) or "no fit"
    if depth_scale_to_blame(found, live, m["gate_m"]):
        advice = ("With their own scale, %.3f, every feature fits within %g cm, so it's the depth scale, which "
                  "re-clicking won't fix. Tell an agent this block." % (found["scale"], m["gate_m"] * 100))
    else:
        advice = "Re-click or re-pick what it names, then paste again with --again."
        if found and found["worst_m"] > m["gate_m"]:
            advice += " %s sits furthest from the rest (%.0f cm)." % (found["worst_id"], found["worst_m"] * 100)
    say("  REFUSED: %s. %s" % (named, advice))
    return code


def show_page(work, take_dir):
    say("")
    say("Your scan opens again with the camera where the fit puts it, and your six features as the camera sees them. "
        "Each dot should sit on its feature. Press Done on the page when you've looked. If you closed the tab, press "
        "Ctrl+C here: the registration stands either way.")
    scan_page(work, take_dir, "show")


def scan_revision(work):
    """The scan revision the worksheet's picks were made on (the scan page writes it into each pick's note), or None
    when they don't all say one. Step 3 holds the registration to the scan it draws on with it."""
    try:
        sheet = json.loads((work / "worksheet.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    found = {str(p.get("note") or "").rpartition("content revision ")[2] for p in sheet.get("points") or []}
    return found.pop() if len(found) == 1 and "" not in found else None


def write_receipt(work, receipt):
    taken = [int(m[1]) for p in work.glob("register-now-*.json") if (m := re.fullmatch(r"register-now-([0-9]+)\.json", p.name))]
    write_json(work / ("register-now-%d.json" % (max(taken, default=0) + 1)), receipt)


def main(argv=None, *, root=None, camera_url=None, token_file=None, timing=Timing(), speak=None, wait=time.sleep,
         console=sc.has_console, now=time.time, method_file=METHOD_FILE, clicker=None, picker=None, shower=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--again", action="store_true", help="keep the newest take, your clicks and picks; fit again")
    args = parser.parse_args(argv)
    if not console():   # a background run is refused before anything: no camera
        say("register: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    try:
        R = den_register(sc.archive(root / "tools", "register"))
        return run(root=root, again=args.again, camera_url=camera_url or R.take.DEFAULT_CAMERA,
                   token_file=token_file or R.take.DEFAULT_TOKEN_FILE, timing=timing,
                   speak=speak or sc.speaker(R.take), wait=wait, now=now(), method_file=method_file, clicker=clicker,
                   picker=picker, shower=shower)
    except Stop as exc:
        say("register: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("register: interrupted. Paste the line again (with --again to keep the take) when you're ready.")
        return 130
    except Exception as exc:
        if type(exc).__name__ in ("Refused", "NotUtf8") and hasattr(exc, "reason"):
            # den-register's own refusal outside the fit (a worksheet saved as UTF-16, a frame it can't use): its reason
            # and detail name files and points, never a coordinate.
            say("register: stopped: den-register refused, %s: %s" % (exc.reason, str(exc.detail)[:300]))
            return 2
        say("register: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
