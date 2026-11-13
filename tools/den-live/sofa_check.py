#!/usr/bin/env python3
"""Step 1 of the live den view: does the re-aimed den camera see a reader on the sofa? The owner's paste. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\sofa_check.py

It speaks a 15 s countdown, then three cues of about 35 s each (8 to 10 s to settle, then a 25 s window): sit on the
sofa and read with the book at chest height, hold your phone instead, then leave the camera's view. Meanwhile it reads the den camera's /frame.npz about 5 times a
second, as den-take does, with den-take's own GET client and the same token file, and keeps the frames of those three
windows under <data root>\\private\\sofa-check-<time>\\. The data root is DEN_LIVE_HOME, or else %USERPROFILE%\\den-live.
Agents never open that folder.

Then, each in its own process with Python sockets refused:
1. --rig: den-register's own EEPROM reader (rig.py, at refs.json's den-register pin) turns the camera's /calibration
   reply and one frame into the camera's intrinsics and its RGB-from-rectified-left transform. No registration is
   needed: these are the camera's own.
2. --analyse, in the detector's venv: den-detect's Detector (YOLO26s and RTMPose-m, as the simulation runs them) on
   every kept frame, and den-observe's lift.lift_pixel for the torso joints, at den-observe's proposed keypoint score
   0.4 and its 67 ms depth-gap rule. It writes counts per window, never a box, a joint or a coordinate.
3. --bodies: for the reading and the phone windows, three Qwen requests each, its stills chosen by the Lab's own
   qwen_live.spread, encoded as the simulation's chain.py encodes them, the body built by qwen_scene.request_body with
   today's prompt and the real local time.

Then the six bodies go to Qwen on LA through the simulation's ask_la.py, unchanged: a preflight visit first, no image
sent unless it passes, then a second visit, 4 s between requests. The replies come back to the check folder.

It prints one block: the counts, Qwen's words, the frame size and rate, and a verdict.
- SOFA SEEN: on at least 80% of the reading frames a person box at 0.5 or more with a face joint and both shoulders,
  and torso depth on at least 80%; Qwen calls at least 2 of the 3 reading requests reading, with one person
  visible; and the empty window shows no person on at least 98% of its frames.
- PERSON ONLY: the detector part passes and Qwen's doesn't. By the owner's rule this counts as unseen for the
  spotlight.
- NOT SEEN: the detector part fails.
- CHECK AGAIN: a person showed in the empty window, or a window got no frame, so it says nothing; paste again.
- DETECTOR ONLY: Qwen's part can't be judged: vLLM down, LA unreachable, fewer than 2 reading requests built, or
  answers missing that could still change the outcome. The detector part is shown alone.
If the ask stops after Qwen has answered, the answers already back still count. If a visit may have left its LA
folder (this check's stills) on LA, the block says so with the line that removes it, and the next paste removes it
first.
"Seated" isn't judged here: it needs the floor, which only step 2's registration gives.

Nothing here prints a box, a joint, a coordinate, a body, a reply's text or exception text (type names only), and
the output is ASCII. It refuses to start with no console, before anything touches the camera, so a background run
can't.
"""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEN_SIM, DETECT = ROOT / "tools" / "den-sim", ROOT / "tools" / "den-detect"
sys.path.insert(0, str(DETECT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))   # source.py, the camera's client
import detect  # noqa: E402  its WORKERS, lock and guard; the models load only in the venv, under --analyse

LAB = "stack/services/perception-lab/"
# qwen_live.spread's import closure, as chain.py lists it (QWEN_LIVE): 8 Lab modules, no third-party package.
QWEN_LIVE = ("qwen_live.py", "qwen_scene.py", "qwen_transport.py", "occupancy_gate.py", "analysis.py", "analysis_result.py",
             "experiment_history.py", "reasoning.py")
COORDINATOR = ("den_coordinator", "body", "bulbs", "eye", "joints", "learn", "scurve", "share", "situation", "spot_light",
               "spot_rule", "need", "need_v1")
BRAIN = ("__init__", "aim", "settings", "geometry")
TREES = {"observe": ("rung-2-observe", ("tools/den-observe", "tools/den-take", *detect.WORKERS)),
         "register": ("den-register", ("tools/den-register", "tools/den-take")),
         "track": ("claude/rung-3-track-local-514d6b", ("tools/den-track",)),   # step 3's live loop
         "core": ("den-core-live", tuple(LAB + name for name in QWEN_LIVE)),
         # The den lighting coordinator's core: only the files it imports, so the release scan reads no others.
         "coordinator": ("den-coordinator", tuple("tools/den-core/%s.py" % name for name in COORDINATOR)
                         + tuple("stack/services/lighting-brain/lighting_brain/%s.py" % name for name in BRAIN))}
REMOTE = "/dev/shm/den-live-check"
try:
    VENV_PY = Path.home() / "ml" / "den-detector" / "Scripts" / "python.exe"
except RuntimeError:   # no home folder known (a service's environment): only this PC's pastes use it
    VENV_PY = Path("den-detector-venv-not-on-this-host")
PERSON_MIN, KEYPOINT_MIN, DEPTH_GAP_S = 0.5, 0.4, 0.067   # den-detect's crop line; den-observe's keypoint_score_min and max_depth_gap_s
FACE, SHOULDERS, HIPS, WRISTS = (0, 1, 2, 3, 4), (5, 6), (11, 12), (9, 10)   # COCO-17
TORSO = (5, 6, 11, 12)
READING_MIN, EMPTY_MIN, QWEN_MIN = 0.80, 0.98, 2
POSES = ("reading", "phone", "empty")
CUES = {"reading": "Sit on the sofa and read your book, held at chest height. Stay until the next cue.",
        "phone": "Now put the book down and hold your phone, the way you use it.",
        "empty": "Now leave the den, or step out of the camera's view, until you hear done."}
REQUESTS_PER_POSE, RIG_TRIES = 3, 10
LA_STATE = "la-folder.json"   # under the data root: whether the last paste may have left its LA folder
JPEG_NOTE = "Pillow JPEG at chain.py's settings"


@dataclass(frozen=True)
class Timing:
    countdown_s: float = 15.0
    settle_s: tuple = (8.0, 8.0, 10.0)   # after each cue, before its window opens
    window_s: float = 25.0
    poll_s: float = 0.2


class Stop(Exception):
    pass


def say(text):
    print(str(text).encode("ascii", "replace").decode("ascii"), flush=True)


def has_console():
    try:
        return sys.stdin is not None and sys.stdout is not None and sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def data_root(env=None):
    env = os.environ if env is None else env
    return Path(env["DEN_LIVE_HOME"]) if env.get("DEN_LIVE_HOME") else Path.home() / "den-live"


def refs():
    return json.loads((DEN_SIM / "refs.json").read_text(encoding="utf-8"))["tools"]


def tree_path(tools_root, key, pins=None):
    """(folder, sha, paths, marker) of one pinned tree under tools_root: the marker its .complete file holds once whole."""
    ref_name, paths = TREES[key]
    sha = (pins or refs())[ref_name]
    return Path(tools_root) / ("%s-%s" % (key, sha)), sha, paths, "%s %s\n" % (sha, " ".join(paths))


def archived(tools_root, key, pins=None):
    """The pinned tree's folder when it's whole (its marker names these paths at this sha), else None. No git."""
    tree, _sha, _paths, marker = tree_path(tools_root, key, pins)
    try:
        return tree if (tree / ".complete").read_text(encoding="utf-8") == marker else None
    except OSError:
        return None


def archive(tools_root, key, pins=None):
    """One folder per pinned tree, written once by git archive; a folder whose marker names other paths is rebuilt."""
    tree, sha, paths, marker = tree_path(tools_root, key, pins)
    ref_name = TREES[key][0]
    if archived(tools_root, key, pins):
        return tree
    checkout = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True)
    if checkout.returncode:
        raise Stop("the git checkout for pinned tools is unavailable")
    done = subprocess.run(["git", "-C", checkout.stdout.strip(), "archive", "--format=tar", sha, *paths],
                          capture_output=True)
    if done.returncode:
        raise Stop("git archive of %s at %s failed" % (ref_name, sha))
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(tree, filter="data")
    (tree / ".complete").write_text(marker, encoding="utf-8")
    return tree


TOKEN_HEADING = "## Step 2, once: the camera token"


def token_lines(tree):
    """The three lines that save the camera token, verbatim from den-take's README, step 2, in the pinned observe tree
    (git archive of den-take at refs.json's pin). None when they aren't there in the shape they had. They're read at run
    time, never copied into den-live: they name the den camera host's address and account."""
    try:
        text = (Path(tree) / "tools" / "den-take" / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    _, heading, rest = text.partition(TOKEN_HEADING + "\n")
    _, fence, block = rest.partition("```powershell\n")
    lines = block.partition("```")[0].splitlines()
    if not (heading and fence) or len(lines) != 3 or not lines[0].startswith("New-Item ") or not lines[2].endswith(".Length"):
        return None
    return lines


def token_refusal(exc, token_file, tree):
    lines = token_lines(tree)
    if not lines:
        return "%s. The token file is %s; den-take's README, step 2, has the lines that save it" % (exc, token_file)
    return ("%s. The token file is %s. These three lines, from den-take's README, step 2, save it. Paste them into "
            "PowerShell; the last prints only the token's length, which should be 24 or more. Never open the file or paste "
            "the token anywhere.\n%s" % (exc, token_file, "\n".join(lines)))


def open_camera(take, url, token_file, tree):
    """den-take's client for the camera, held to /frame.npz and /calibration (source.AllowedCamera). A token file that
    doesn't read stops with the three lines that save it (token_refusal)."""
    import source
    try:
        token = take.read_token(token_file)
    except take.Refused as exc:
        raise Stop(token_refusal(exc, token_file, tree)) from None
    try:
        return source.camera_client(take, url, token)
    except take.Refused as exc:
        raise Stop(str(exc)) from None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child(argv, timeout, what):
    """A step in its own process. Its stdout lines are progress (counts only) and pass through; a failure says only
    its last line, which the steps keep to a refusal sentence."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Stop("%s took over %d s" % (what, timeout)) from None
    for line in done.stdout.splitlines():
        if line.startswith("progress: "):
            say("  " + line[len("progress: "):])
    if done.returncode:
        last = (done.stderr or done.stdout).strip().splitlines()
        raise Stop("%s: %s" % (what, last[-1][:200] if last else "exit %d" % done.returncode))
    return done


# ---- the grab (the paste's own process) ---------------------------------------------------------------------

class Grab(threading.Thread):
    """den-take's Poller, without its take rules: one GET of /frame.npz every poll_s, its time noted; while a
    window is open, each new bundle goes to den-take's TakeWriter, byte for byte, and its session is noted. A new
    camera session is counted, not a stop, since the cues go on. 401 or 403 stops it."""

    def __init__(self, take, camera, writer, poll_s):
        super().__init__(daemon=True)
        self.take, self.camera, self.writer, self.poll_s = take, camera, writer, poll_s
        self.halt, self.recording = threading.Event(), threading.Event()
        self.fetch_ms, self.sessions, self.errors, self.stop_reason = [], {}, 0, None
        self.seen, self.since = set(), float("inf")   # since: the open window's start; a frame that arrived before it is not kept

    def run(self):
        next_poll = self.take.clock()
        try:
            while not self.halt.is_set():
                started = self.take.clock()
                try:
                    status, body = self.camera.get("/frame.npz")
                except (OSError, self.take.http.client.HTTPException):
                    status, body = None, b""
                received, received_unix = self.take.clock(), time.time()
                self.fetch_ms.append((received - started) * 1000)
                if status in (401, 403):
                    self.stop_reason = "the camera refused the token"
                    return
                if status != 200:
                    self.errors += 1
                elif self.recording.is_set() and received >= self.since:
                    try:
                        meta = self.take.check.read_bundle(body)[0]
                    except self.take.check.BundleError:
                        self.errors += 1
                    else:
                        key = (meta["session"], meta["rgb_seq"], meta["t_host"])
                        if key not in self.seen:
                            self.seen.add(key)
                            self.writer.add(body, meta, received, received_unix)
                            self.sessions[self.writer.index[-1][1].name] = meta["session"]
                next_poll += self.poll_s
                delay = next_poll - self.take.clock()
                if delay > 0:
                    self.halt.wait(delay)
                else:
                    next_poll = self.take.clock()
        except Exception as exc:  # disk full, permissions: stop the grab, never hang
            self.stop_reason = "the grab stopped (%s)" % type(exc).__name__


def private_folder(root, take, now):
    folder = (root / "private" / ("sofa-check-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))).resolve()
    if take.inside_checkout(folder):
        raise Stop("the data root is inside a git checkout; frames never go into git")
    if take.in_onedrive(folder):
        raise Stop("the data root is inside OneDrive; frames stay on this PC only")
    folder.mkdir(parents=True)
    return folder


def grab(take, camera, folder, timing, speak, wait):
    """The cues and three windows. Writes frames/, frames.csv, windows.json and sessions.json; returns stats."""
    try:
        status, body = camera.get("/calibration")
    except (OSError, take.http.client.HTTPException) as exc:
        raise Stop("the camera at %s:%s did not answer (%s)" % (camera.host, camera.port, type(exc).__name__)) from None
    if status != 200:
        raise Stop("the camera's /calibration answered %s" % status)
    (folder / "camera-calibration.json").write_bytes(body)
    writer = take.TakeWriter(folder)
    poller = Grab(take, camera, writer, timing.poll_s)
    poller.start()
    windows = {}
    try:
        speak("Sofa check. In %d seconds, be on the sofa with your book and your phone." % round(timing.countdown_s))
        wait(timing.countdown_s)
        for pose, settle in zip(POSES, timing.settle_s):
            if poller.stop_reason:
                break
            speak(CUES[pose])
            wait(settle)
            start = take.clock()
            poller.since = start
            poller.recording.set()
            wait(timing.window_s)
            poller.recording.clear()
            windows[pose] = {"start": start, "end": take.clock()}
        speak("Done. Thank you.")
    finally:
        poller.halt.set()
        poller.join(10)
        writer.close()
    if poller.stop_reason:
        raise Stop(poller.stop_reason)
    with writer.lock:
        index = list(writer.index)
    for pose, window in windows.items():
        window["files"] = [path.name for recv, path, _ in index if window["start"] <= recv <= window["end"]]
    (folder / "windows.json").write_text(json.dumps(windows, indent=1), encoding="utf-8")
    (folder / "sessions.json").write_text(json.dumps(poller.sessions, indent=1), encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO((folder / "frames.csv").read_text(encoding="utf-8"))))
    sizes = [int(row["bytes"]) for row in rows]
    return {"frames": len(rows), "bytes_median": statistics.median(sizes) if sizes else None,
            "fetch_ms_median": round(statistics.median(poller.fetch_ms)) if poller.fetch_ms else None,
            "per_s": {pose: round(len(w["files"]) / max(w["end"] - w["start"], 1e-6), 2) for pose, w in windows.items()},
            "sessions": len(set(poller.sessions.values())), "errors": poller.errors}


# ---- --rig: den-register's EEPROM reader, sockets refused ---------------------------------------------------

def step_rig(folder, tree):
    detect.guard()
    sys.path.insert(0, str(Path(tree) / "tools" / "den-register"))
    import rig
    import scene
    windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
    names = [name for pose in POSES for name in windows.get(pose, {}).get("files", [])]
    if not names:
        raise Stop("no frame was kept")
    try:
        geometry = rig.read_calibration(folder / "camera-calibration.json")
    except scene.Refused as exc:
        raise Stop("den-register refused the camera's /calibration (%s)" % exc.args[0]) from None
    why = None
    for name in names[:RIG_TRIES]:   # one frame den-register refuses doesn't end the check; ten in a row do
        try:
            k = rig.entry_intrinsics(scene.read_bundle((folder / "frames" / name).read_bytes()), geometry)
            break
        except scene.Refused as exc:
            why = exc.args[0]
    else:
        raise Stop("den-register refused the camera's frames (%s)" % why)
    check_size(k)
    (folder / "rig.json").write_text(json.dumps(k, indent=1), encoding="utf-8")


def check_size(k):
    """den-observe's own guard (observe.py refuses a row whose frame size isn't the rig's): every row here is the
    detector's 640x360, so a camera whose RGB frame is another size would lift joints through the wrong intrinsics."""
    if tuple(k["rgb_size"]) != (detect.WIDTH, detect.HEIGHT):
        raise Stop("the camera's RGB frame is %sx%s, not %dx%d" % (*k["rgb_size"], detect.WIDTH, detect.HEIGHT))


# ---- --analyse: the detector's venv, sockets refused ---------------------------------------------------------

def supported(keypoints, j):
    return bool(keypoints) and keypoints[j] is not None and keypoints[j][2] >= KEYPOINT_MIN


def frame_facts(row, meta, depth, rig, lift_pixel):
    """One frame's words, never its numbers: person, face, shoulders, hips, wrists, torso depth, people."""
    people = [d for d in row["detections"] if d["class"] == "person" and d["score"] >= PERSON_MIN]
    facts = {"person": bool(people), "people": len(people), "face": False, "shoulders": False, "hips": False,
             "wrists": False, "torso_depth": False}
    if not people:
        return facts
    keypoints = max(people, key=lambda d: d["score"]).get("keypoints")
    facts.update(face=any(supported(keypoints, j) for j in FACE), shoulders=all(supported(keypoints, j) for j in SHOULDERS),
                 hips=all(supported(keypoints, j) for j in HIPS), wrists=all(supported(keypoints, j) for j in WRISTS))
    if abs(row["frame"]["capture_ts"] - meta["t_depth"]) <= DEPTH_GAP_S:
        measured = sum(lift_pixel(rig, depth, keypoints[j][0], keypoints[j][1])["status"] == "measured"
                       for j in TORSO if supported(keypoints, j))
        facts["torso_depth"] = measured >= 2
    return facts


def tally(facts):
    keys = ("person", "face_shoulders", "torso_depth", "hips", "wrists", "two_or_more")
    out = dict.fromkeys(keys, 0)
    for f in facts:
        out["person"] += f["person"]
        out["face_shoulders"] += f["person"] and f["face"] and f["shoulders"]
        out["torso_depth"] += f["torso_depth"]
        out["hips"] += f["hips"]
        out["wrists"] += f["wrists"]
        out["two_or_more"] += f["people"] >= 2
    return dict(out, frames=len(facts))


def step_analyse(folder, tree, state):
    detect.guard()
    folder, tree, state = Path(folder).resolve(), Path(tree).resolve(), Path(state).resolve()
    pinned = detect.lock()
    detect.contain(state)
    wrong = detect.unlocked(pinned["versions"], detect.installed())
    if wrong:
        raise Stop("the detector's venv is not requirements.lock's (%d differences)" % len(wrong))
    try:
        detector = detect.Detector(tree, Path(sys.prefix) / "models", pinned["weights"])
    except detect.Refused as exc:
        raise Stop(str(exc)) from None
    import lift
    import takes
    k = json.loads((folder / "rig.json").read_text(encoding="utf-8"))
    check_size(k)
    extrinsics = k["rgb_from_rectified_left"]
    rig = lift.Rig(left_k=k["rectified_left"], rgb_k=k["rgb"], rgb_distortion=k.get("rgb_distortion"),
                   rgb_from_left=(extrinsics["rotation"], extrinsics["translation_m"]), rgb_size=k["rgb_size"], depth_size=k["depth_size"])
    windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
    rows = {Path(row["file"]).name: row for row in csv.DictReader(io.StringIO((folder / "frames.csv").read_text(encoding="utf-8")))}
    result, ms = {}, []
    for pose in POSES:
        names = windows.get(pose, {}).get("files", [])
        print("progress: looking at %d %s frames" % (len(names), pose), flush=True)
        facts, unreadable = [], 0
        for name in names:
            row = rows[name]
            try:
                got, _merged, compute_s = detector.frame(folder, row)
                meta, depth = takes.read_frame((folder / row["file"]).read_bytes())
            except (detect.Refused, takes.BundleError):   # one bad bundle is counted, not the end of the check
                unreadable += 1
                continue
            facts.append(frame_facts(got, meta, depth, rig, lift.lift_pixel))
            ms.append(compute_s * 1000)
        result[pose] = dict(tally(facts), unreadable=unreadable)
    result["detector_ms_median"] = round(statistics.median(ms)) if ms else None
    (folder / "analysis.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


# ---- --bodies: the requests, sockets refused ----------------------------------------------------------------

def pools(frames, count=REQUESTS_PER_POSE):
    """Three pools from one window, each ending a step earlier, so each request is a different look: frames are
    (t_host, name, rgb_seq, session); a pool ends at its cutoff, the time spread is called at."""
    if not frames:
        return []
    frames = sorted(frames)
    first, last = frames[0][0], frames[-1][0]
    step = max(1.0, min(6.0, (last - first - 3.5) / max(count - 1, 1)))
    return [(last - k * step, [f for f in frames if f[0] <= last - k * step]) for k in range(count)]


def step_bodies(folder, tree):
    sys.path.insert(0, str(DEN_SIM))
    import chain   # its socket refusal comes with it, and its still encoder is the one reused here
    sys.path.insert(0, str(Path(tree) / LAB))
    import numpy as np
    from PIL import Image
    import qwen_live
    import qwen_scene as scene
    windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
    sessions = json.loads((folder / "sessions.json").read_text(encoding="utf-8"))
    by_name = {Path(row["file"]).name: row for row in csv.DictReader(io.StringIO((folder / "frames.csv").read_text(encoding="utf-8")))}
    stamp = time.localtime()
    context = scene.model_context(scene.local_time_label(stamp.tm_hour, stamp.tm_min))
    owed = folder / "ask" / "owed"
    owed.mkdir(parents=True)
    (folder / "ask" / "replies").mkdir()
    looks, names = [], []
    for pose in ("reading", "phone"):
        frames = [(float(by_name[n]["t_host_s"]), n, int(by_name[n]["rgb_seq"]), sessions.get(n)) for n in windows.get(pose, {}).get("files", [])]
        for cutoff, pool in pools(frames):
            entries = [dict(media_id="frames/" + name, frame_id=str(seq), capture_epoch=session, observed_at=t,
                            valid_until=t + scene.MAX_AGE_SECONDS) for t, name, seq, session in pool]
            try:
                chosen = qwen_live.spread(entries, cutoff)
            except qwen_live.FramesUnavailable as why:
                looks.append({"pose": pose, "body_sha256": None, "not_asked": "qwen_live.spread: %s" % why})
                continue
            try:
                images = chain.still_bytes(np, Image, scene, folder, [c["media_id"] for c in chosen])
            except chain.Refused as why:
                looks.append({"pose": pose, "body_sha256": None, "not_asked": str(why)})
                continue
            body = scene.canonical(scene.request_body(context, images, [c["observed_at"] for c in chosen])).encode()
            name = hashlib.sha256(body).hexdigest()
            if name in names:
                continue
            names.append(name)
            (owed / (name + ".body")).write_bytes(body)
            looks.append({"pose": pose, "body_sha256": name, "stills": [int(c["frame_id"]) for c in chosen],
                          "jpeg_bytes": [len(image) for image in images]})
    (owed / "owed.json").write_text(json.dumps({"schema": "den-live-owed-1", "set": None, "bodies": sorted(names)}), encoding="utf-8")
    (folder / "looks.json").write_text(json.dumps({"jpeg": JPEG_NOTE, "looks": looks}, indent=1), encoding="utf-8")
    print("progress: %d requests built for Qwen" % len(names), flush=True)


# ---- the ask, through ask_la.py unchanged --------------------------------------------------------------------

def left_on_la(root):
    """Whether the last paste's record says it may have left its LA folder, and so this check's stills, on LA."""
    try:
        return json.loads((root / LA_STATE).read_text(encoding="utf-8")).get("la_folder") not in (None, "removed")
    except (OSError, ValueError, AttributeError):
        return False


def ask(folder, root, transport=None):
    """(facts per answered body, why the ask stopped short or None, ask_la's record). Every reply already filed
    counts, however the ask ends. When a visit may have left its folder on LA, the record says so, LA_STATE keeps it,
    and the next paste copies back and removes that folder before anything else, as ask_la.py's own left= does."""
    sys.path.insert(0, str(DEN_SIM))
    import ask_la
    ask_la.REMOTE = REMOTE
    left = left_on_la(root)
    record = {"schema": "den-live-check-ask-1", "stopped_at": None, "reason": None, "step": None, "needed": None, "answered": 0,
              "still_needed": None, "cap": None, "root": None, "ask_exit": None, "vllm": None, "la_folder": None,
              "tools": {"head": None, "blobs": {}}, "filed": {}}
    why = None
    try:
        tools = ask_la.lift(root / "tools" / "lifted")
        code = ask_la.run(folder / "ask", transport or ask_la.SSH(ask_la.load(tools["la.py"], "den_live_la")), tools, record, left=left)
        if code:
            why = record["reason"] or ("the preflight failed" if code == 3 else "the ask stopped (exit %d)" % code)
    except ask_la.Stop as exc:
        why = str(exc)
    except Exception as exc:  # type name only
        why = "the ask stopped (%s)" % type(exc).__name__
    finally:
        if record["la_folder"] is None and left:   # this paste never reached LA, so the folder an earlier one left is still there
            record["la_folder"] = "left by an earlier paste"
        (folder / "ask" / "last-ask.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        (root / LA_STATE).write_text(json.dumps({"la_folder": record["la_folder"], "check": folder.name}), encoding="utf-8")
    return replies(folder, root), why, record


def replies(folder, root):
    """Each filed reply's facts, parsed by the Lab's own qwen_scene: {body_sha256: (facts or None, invalid reason)}."""
    scene = load(archive(root / "tools", "core") / LAB / "qwen_scene.py", "den_live_qwen_scene")
    out = {}
    for path in sorted((folder / "ask" / "replies").glob("*.json")):
        try:
            response = json.loads(json.loads(path.read_text(encoding="utf-8"))["raw"])
        except (OSError, ValueError, KeyError, TypeError):
            out[path.stem] = (None, "unreadable")
            continue
        content, finish = scene.reply_from_proof({"response": response})
        out[path.stem] = scene.parse_reply(content, finish_reason=finish, prior_supplied=False)
    return out


# ---- the verdict ---------------------------------------------------------------------------------------------

def share(count, frames):
    return count / frames if frames else 0.0


def words(facts):
    if not facts:
        return "invalid"
    return "%s/%s/%s" % (facts.get("activity_hypothesis"), facts.get("held_object"), facts.get("people_visible"))


def verdict(analysis, looks, answers, ask_why):
    """(word, lines, said, reads): the rule in the docstring, from counts and Qwen's facts only."""
    reading, empty = analysis["reading"], analysis["empty"]
    seen = (share(reading["face_shoulders"], reading["frames"]) >= READING_MIN
            and share(reading["torso_depth"], reading["frames"]) >= READING_MIN)
    clear = share(empty["frames"] - empty["person"], empty["frames"]) >= EMPTY_MIN
    built = [look for look in looks if look["pose"] == "reading" and look["body_sha256"]]
    # Only the requests Qwen answered: an answer with no valid facts is Qwen's, a missing one isn't.
    said = {pose: [answers[look["body_sha256"]][0] for look in looks if look["pose"] == pose and look["body_sha256"] in answers]
            for pose in ("reading", "phone")}
    reads = sum(1 for f in said["reading"] if f and f.get("activity_hypothesis") == "reading" and f.get("people_visible") == "one")
    unanswered = len(built) - len(said["reading"])
    lines = []
    if not reading["frames"] or not empty["frames"]:
        word = "CHECK AGAIN"
        lines.append("No frame came in the %s window, so it says nothing. Paste the line again."
                     % ("reading" if not reading["frames"] else "empty"))
    elif not seen:
        word = "NOT SEEN"
        lines.append("The detector part fails: the camera does not see a reader on the sofa well enough.")
    elif not clear:
        word = "CHECK AGAIN"
        lines.append("A person showed in the empty window on %d of %d frames. If you were still in view, paste the line again."
                     % (empty["person"], empty["frames"]))
    elif reads >= QWEN_MIN:   # enough, even if the ask stopped before the rest came back
        word = "SOFA SEEN"
        lines.append("The spotlight part goes ahead.")
    elif len(built) < QWEN_MIN:   # the rule needs 2 of 3 looks; fewer than 2 built can't meet it either way
        word = "DETECTOR ONLY"
        unbuilt = [look.get("not_asked") for look in looks if look["pose"] == "reading" and not look["body_sha256"]]
        lines.append("The detector part passes. Only %d of %d reading requests could be built%s, so Qwen's part can't be judged."
                     % (len(built), REQUESTS_PER_POSE, " (%s)" % unbuilt[0] if unbuilt and unbuilt[0] else ""))
    elif reads + unanswered >= QWEN_MIN:   # the answers still missing could change the outcome
        word = "DETECTOR ONLY"
        lines.append("The detector part passes. Qwen didn't answer %d of %d reading requests: %s."
                     % (unanswered, len(built), ask_why or "no reply came back"))
    else:
        word = "PERSON ONLY"
        lines.append("The camera sees you, but Qwen called %d of %d reading requests reading. By your rule the spotlight part stops; "
                     "the markers go on." % (reads, len(said["reading"])))
    if any(f and f.get("activity_hypothesis") == "reading" for f in said["phone"]):
        lines.append("Note: Qwen called a phone request reading, so the spotlight would light for a phone.")
    return word, lines, said, reads


def report(analysis, stats, said, reads, word, lines, folder):
    def window(pose, extra):
        w = analysis[pose]
        bad = " (%d more unreadable)" % w["unreadable"] if w.get("unreadable") else ""
        return "%-9s %s%s" % (pose.capitalize() + ":", extra(w), bad)
    out = ["", "Sofa view check, %s" % time.strftime("%Y-%m-%d %H:%M"),
           window("reading", lambda w: "person + face + both shoulders on %d of %d frames; torso depth on %d; hips on %d; both wrists on %d"
                  % (w["face_shoulders"], w["frames"], w["torso_depth"], w["hips"], w["wrists"])),
           window("phone", lambda w: "person + face + both shoulders on %d of %d frames; torso depth on %d"
                  % (w["face_shoulders"], w["frames"], w["torso_depth"])),
           window("empty", lambda w: "no person on %d of %d frames" % (w["frames"] - w["person"], w["frames"]))]
    for pose in ("reading", "phone"):
        if said[pose]:
            out.append("Qwen %-8s %s" % (pose + ":", ", ".join(words(f) for f in said[pose])))
    mb = stats["bytes_median"] / 1e6 if stats["bytes_median"] else 0
    out.append("Frames:   median %.2f MB, %s a second, fetch median %s ms (this PC), detector median %s ms a frame"
               % (mb, "/".join(str(stats["per_s"].get(p, 0)) for p in POSES), stats["fetch_ms_median"], analysis.get("detector_ms_median")))
    if stats["sessions"] > 1:
        out.append("Note: the camera restarted during the check (%d sessions)." % stats["sessions"])
    out.append("Verdict:  %s. %s" % (word, lines[0]))
    out.extend("          " + line for line in lines[1:])
    out.append("Kept in %s (agents never open it)." % folder)
    for line in out:
        say(line)


def write_verdict(folder, word, analysis, stats, said, reads):
    record = {"schema": "den-live-sofa-check-1", "verdict": word, "counts": analysis, "stats": stats, "qwen_reading_reads": reads,
              "qwen_words": {pose: [words(f) for f in facts] for pose, facts in said.items()},
              "rule": {"reading_min": READING_MIN, "empty_min": EMPTY_MIN, "qwen_min": QWEN_MIN, "person_min": PERSON_MIN, "keypoint_min": KEYPOINT_MIN}}
    (folder / "verdict.json").write_text(json.dumps(record, indent=1), encoding="utf-8")


# ---- the paste -----------------------------------------------------------------------------------------------

def run(*, root, camera_url, token_file, venv, transport, timing, speak, wait, now):
    tools = root / "tools"
    observe, register, _core = (archive(tools, key) for key in ("observe", "register", "core"))
    sys.path.insert(0, str(observe / "tools" / "den-take"))
    import take
    if not Path(venv).is_file():
        raise Stop("the detector's venv is not at %s" % venv)
    camera = open_camera(take, camera_url, token_file, observe)
    folder = private_folder(root, take, now)
    say("Frames go to %s" % folder)
    stats = grab(take, camera, folder, timing, speak, wait)
    if not stats["frames"]:
        raise Stop("no frame came from the camera")
    say("Looking at the frames (a minute or two)...")
    child([sys.executable, "-B", __file__, "--rig", str(folder), str(register)], 120, "reading the camera's geometry")
    child([str(venv), "-B", __file__, "--analyse", str(folder), str(observe), str(root / "state" / "detector")], 1800, "the detector")
    analysis = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    looks, answers, why, record = [], {}, None, {}
    try:   # from here a failure still prints the detector's counts, with Qwen's part said to be missing
        child([sys.executable, "-B", __file__, "--bodies", str(folder), str(archive(tools, "core"))], 300, "building Qwen's requests")
        looks = json.loads((folder / "looks.json").read_text(encoding="utf-8"))["looks"]
        say("Asking Qwen on LA...")
        answers, why, record = ask(folder, root, transport)
    except Stop as exc:
        why = str(exc)
    except Exception as exc:  # type name only
        why = "Qwen's part stopped (%s)" % type(exc).__name__
    word, lines, said, reads = verdict(analysis, looks, answers, why)
    if record.get("la_folder") not in (None, "removed"):
        lines.append("LA may still hold this check's stills in %s. The next paste removes them first; to remove them now, paste: "
                     'ssh hav-ubuntu "rm -rf %s"' % (REMOTE, REMOTE))
    write_verdict(folder, word, analysis, stats, said, reads)
    report(analysis, stats, said, reads, word, lines, folder)
    return 0


def speaker(take):
    def speak(text):
        take.say(text)   # prints it, and speaks it without waiting
    return speak


def main(argv=None, *, root=None, camera_url=None, token_file=None, venv=VENV_PY, transport=None, timing=Timing(),
         speak=None, wait=time.sleep, console=has_console, now=time.time):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rig", nargs=2, metavar=("FOLDER", "TREE"), help=argparse.SUPPRESS)
    parser.add_argument("--analyse", nargs=3, metavar=("FOLDER", "TREE", "STATE"), help=argparse.SUPPRESS)
    parser.add_argument("--bodies", nargs=2, metavar=("FOLDER", "TREE"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.rig:
            step_rig(Path(args.rig[0]), args.rig[1])
            return 0
        if args.analyse:
            step_analyse(*args.analyse)
            return 0
        if args.bodies:
            step_bodies(Path(args.bodies[0]), args.bodies[1])
            return 0
    except Stop as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:  # type name only: a step's text could quote what it read
        print("stopped (%s)" % type(exc).__name__, file=sys.stderr)
        return 2
    if not console():   # a background run is refused before anything: no camera, no ssh
        say("sofa check: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else data_root()
    try:
        observe = archive(root / "tools", "observe")
        sys.path.insert(0, str(observe / "tools" / "den-take"))
        import take
        return run(root=root, camera_url=camera_url or take.DEFAULT_CAMERA, token_file=token_file or take.DEFAULT_TOKEN_FILE,
                   venv=venv, transport=transport, timing=timing, speak=speak or speaker(take), wait=wait, now=now())
    except Stop as exc:
        say("sofa check: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("sofa check: interrupted. Paste the line again when you're ready.")
        return 130
    except Exception as exc:  # type name only
        say("sofa check: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
