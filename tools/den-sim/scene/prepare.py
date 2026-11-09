#!/usr/bin/env python3
"""Hops 1 to 5, recorded once: the rendered scenes through the real recorder, check and fit.

    python -B -X utf8 tools\\den-sim\\scene\\prepare.py
    python -B -X utf8 tools\\den-sim\\scene\\prepare.py --refit --set SET --mark MARK --export EXPORT --floor FLOOR [--facing FACING]

Without --refit it makes one new set under %LOCALAPPDATA%\\den-sim\\prepared\\<random id>\\ and never
reads the private folder, so an agent can run it:

1. render.mjs draws the marks shot and the scenes in shots.json, eight poses each, under a random word
   per scene. The word is the scene's only name: in the render's file names, the display file and the
   truth folder, and nowhere else.
2. The marks take first, then the scene takes in a random order, each through the virtual camera
   (vcam.py, 127.0.0.1 only) and the real take.run (script free, cues silenced, a 5x clock), then the
   real check.run_check, whose "no second person" prompt the harness answers through a stand-in console.
3. The fit on the marks take with real ClickSession and run_fit, unedited den-register, its open slots
   closed and its labels set in memory, so the record says `virtual-scene + fit`. Its text goes to a
   log in the set, never to the console.
4. prepare-receipt.json beside each take, keyed by the take's digest (sha256 of frames.csv); set.json,
   display.json and truth\\<word>\\ in the set; one line appended to prepared\\tries.jsonl.

--refit is for the owner's run.mjs (and later fresh_run.py), on the owner's files, when the mark's,
export's or facing's digest changed. It copies a set's clicks and calibration into a new folder under
private\\fits\\, writes the worksheet moved by a private transform (the couch's middle onto the mark's
middle at the seat point's height, the reader facing away from the mark's longest edge toward the floor's
centre, turned by facing.json's degrees), and runs the fit there. It prints no coordinate.

Every socket is refused, except the virtual camera's loopback while preparing.
"""
import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import string
import subprocess
import sys
import tarfile
import time
import types
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import vcam  # noqa: E402

SCHEMA_RECEIPT, SCHEMA_SET, SCHEMA_DISPLAY = "den-sim-prepare-receipt-1", "den-sim-prepared-set-1", "den-sim-display-1"
SCHEMA_TRUTH, SCHEMA_REFIT, SCHEMA_PLACEMENT = "den-sim-truth-1", "den-sim-refit-1", "den-sim-placement-1"
MARK_SCHEMA = "home-registered-sofa-target-1"
HARNESS = "den-sim-harness"
SPEED = 5.0           # camera time runs five times wall time: a 20 s activity part takes about 17 s of wall
ACTIVITY_S = 20.0
PHONE_CLOCK_S = 2.3456  # the slate's clock is camera time minus this; check.py fits any constant
LOOPBACK = "127.0.0.1"
SCENE_FILES = ("shots.json", "figure.js", "page.html", "render.mjs", "vcam.py", "prepare.py", "frame_hash.py")
TOOLS = {"den-register": "tools/den-register", "den-take": "tools/den-take"}
NETWORK = ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
           "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo")


class Stop(Exception):
    """A hop refused: (hop, reason)."""

    def __init__(self, hop, reason):
        super().__init__(f"hop {hop}: {reason}")
        self.hop, self.reason = hop, reason


def guard(loopback):
    """Refuse every socket; with loopback, allow 127.0.0.1 only, for the virtual camera and take.py."""
    def hook(event, args):
        if event not in NETWORK:
            return
        if loopback and event == "socket.getaddrinfo" and args[0] in (LOOPBACK, LOOPBACK.encode()):
            return
        if loopback and event in ("socket.connect", "socket.bind") and type(args[1]) is tuple and args[1][:1] == (LOOPBACK,):
            return
        raise PermissionError(f"den-sim prepare has no network ({event})")
    sys.addaudithook(hook)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=1, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def read_owner_json(path):
    """PowerShell 5.1 writes UTF-16 with > and a UTF-8 BOM with Set-Content; read all three."""
    raw = Path(path).read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    return json.loads(text)


def random_id():
    return secrets.token_hex(6)


def random_word(taken):
    """Ten lower-case letters, never all hex, so a word can't be mistaken for a folder id."""
    while True:
        word = "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))
        if word not in taken and any(ch not in "abcdef" for ch in word):
            return word


class Log:
    def __init__(self, folder):
        self.text, self.argv = folder / "prepare.log", folder / "argv.jsonl"

    def say(self, line):
        print(line, flush=True)
        with open(self.text, "a", encoding="utf-8", newline="\n") as target:
            target.write(line + "\n")

    def keep(self, text):
        with open(self.text, "a", encoding="utf-8", newline="\n") as target:
            target.write(text if text.endswith("\n") else text + "\n")

    def run(self, argv, **kwargs):
        with open(self.argv, "a", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps({"argv": [str(a) for a in argv], "unix": round(time.time(), 3)}) + "\n")
        return subprocess.run([str(a) for a in argv], capture_output=True, **kwargs)


def git(*args):
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if done.returncode:
        raise Stop(0, "git %s: %s" % (args[0], done.stderr.decode("utf-8", "replace").strip()[-200:]))
    return done.stdout


def pins():
    refs = json.loads((ROOT / "tools/den-sim/refs.json").read_text(encoding="utf-8"))["tools"]
    return {name: git("rev-parse", refs[name] + "^{commit}").decode().strip() for name in TOOLS}


def archive(tree, commits, log=None):
    """den-register and den-take at their pins into one folder, so den-register finds den-take beside it.
    Returns {tool: {commit, files: {path: {blob, sha256}}}}."""
    record = {}
    for name, path in TOOLS.items():
        sha = commits[name]
        argv = ["git", "-C", ROOT, "archive", "--format=tar", sha, path]
        done = log.run(argv) if log else subprocess.run([str(a) for a in argv], capture_output=True)
        if done.returncode:
            raise Stop(0, f"git archive {name}: {done.stderr.decode('utf-8', 'replace').strip()[-200:]}")
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
            tar.extractall(tree, filter="data")
        files = {}
        for line in git("ls-tree", "-r", sha, "--", path).decode().splitlines():
            meta, file = line.split("\t", 1)
            files[file] = {"blob": meta.split()[2], "sha256": sha256_file(tree / file)}
        record[name] = {"commit": sha, "pin": f"refs.json tools.{name}", "files": files}
    sys.path[:0] = [str(tree / TOOLS["den-register"]), str(tree / TOOLS["den-take"])]
    return record


def harness_files():
    """This branch's files that prepare ran, each against its blob at HEAD: an uncommitted edit shows."""
    head = git("rev-parse", "HEAD").decode().strip()
    files, dirty = {}, []
    for name in SCENE_FILES:
        rel = f"tools/den-sim/scene/{name}"
        at_head = git("ls-tree", "HEAD", "--", rel).decode().split()
        blob = at_head[2] if len(at_head) > 2 else None
        working = git("hash-object", rel).decode().strip()
        files[rel] = {"blob_at_head": blob, "blob": working, "sha256": sha256_file(ROOT / rel)}
        if blob != working:
            dirty.append(rel)
    return {"commit": head, "pin": "HEAD", "files": files, "uncommitted": dirty}


def label_in_memory(register, worksheet, rig):
    """den-register stays unedited. These four, set in memory only, make its record say what happened."""
    register.METHOD = "virtual-scene + fit"
    if "virtual-scene" not in worksheet.TOOLS:
        worksheet.TOOLS = (*worksheet.TOOLS, "virtual-scene")
    if HARNESS not in worksheet.PEOPLE:
        worksheet.PEOPLE = (*worksheet.PEOPLE, HARNESS)
    rig.SOURCE = "the virtual camera's /calibration reply: invented geometry, not an EEPROM"


def closed_method(method):
    m = method.load()
    proposals = dict(m.open)
    m.values.update(m.open)
    m.open.clear()
    return m, proposals


class StandInConsole(io.StringIO):
    """check.py asks for an interactive console before its second-person prompt, because a script or
    an agent can't answer it (check.py's require_console). Nobody is in a render to be the second
    person, so the harness answers "none" through this stand-in, and every receipt says so."""

    def isatty(self):
        return True


# ---- one take -----------------------------------------------------------------------------------------

def record_take(take, check, m, camera, folder, token_file, log):
    s = SPEED
    settings = take.Settings(slate_s=m["slate_length_s"] / s, poll_s=0.2 / s, hold_lead_s=5.0 / s, say_gap_s=3.0 / s,
                             away_s=10.0 / s, commission_activity_s=ACTIVITY_S / s, health_retry_s=1.0 / s)
    began = {}

    def say(text):   # the cues, silenced: they only tell the virtual camera when the phone slate is up
        if text == "Begin.":
            began["t"] = time.monotonic()
        if text.startswith("Hold the phone"):
            camera.showing = True
        elif text.startswith(("Slate OK", "End slate")):
            camera.showing = False

    def enter():     # the harness ends the free script after ACTIVITY_S of camera time
        return "t" in began and time.monotonic() - began["t"] >= ACTIVITY_S / s

    call = types.SimpleNamespace(script="free", out=str(folder), camera=camera.url, token_file=str(token_file))
    started, text = time.monotonic(), io.StringIO()
    try:
        with contextlib.redirect_stdout(text):
            code = take.run(call, say=say, peek=take.peek, enter=enter, settings=settings, m=m)
    except take.Refused as exc:
        log.keep(text.getvalue())
        return ({"exit": 2, "complete": False, "stop_reason": None, "counts": None, "refused": str(exc), "wrote": []},
                None, f"take.py refused: {exc}")
    log.keep(text.getvalue())
    wall = time.monotonic() - started
    record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
    hop3 = {"exit": code, "complete": record["complete"], "stop_reason": record["stop_reason"], "counts": record["counts"],
            "wall_s": round(wall, 1), "slates": [{"slate": a["slate"], "attempt": a["attempt"], "readable": a["peek"]["readable"],
                                                    "reason": a["peek"]["reason"]} for a in record["slates"]],
            "settings": record["settings"],
            "wrote": [{"path": f"{folder.name}/{name}", "sha256": sha256_file(folder / name)} for name in ("take.json", "frames.csv")]}
    if not record["complete"]:
        return hop3, None, f"take.py stopped: {record['stop_reason']}"
    out = io.StringIO()
    try:
        rc, report, path = check.run_check(folder, ask=lambda: "none", show=lambda _page: None, m=m, out=out, console=StandInConsole())
    except check.Refused as exc:
        return hop3, {"exit": 2, "verdict": None, "retained": False, "segments": 0, "refused": str(exc), "wrote": []}, f"check.py refused: {exc}"
    log.keep(out.getvalue())
    usable = report["usable"]
    hop4 = {"exit": rc, "verdict": report["verdict"], "retained": report["retained"], "failures": report["failures"],
            "usable_s": round(sum(seg["seconds"] for seg in usable["segments"]), 3), "segments": len(usable["segments"]),
            "second_person": {"asked": report["second_person"]["asked"], "typed": report["second_person"]["typed"],
                              "answered_by": HARNESS, "through": "a stand-in console that says it is interactive"},
            "wrote": [{"path": f"{folder.name}/{path.name}", "sha256": sha256_file(path)}]}
    problem = None
    if report["verdict"] != "PASS" or not report["retained"] or not usable["segments"]:
        problem = f"check.py: {report['verdict']}, retained {report['retained']}, {len(usable['segments'])} usable segments"
    return hop3, hop4, problem


# ---- the fit ------------------------------------------------------------------------------------------

def worksheet_doc(marks, references, note, date):
    return {"schema": "den-register-worksheet-1", "room_id": "den", "camera_id": vcam.CAMERA_ID, "frame": "z_up_metric_floor0",
            "references": references, "measured_by": HARNESS, "measured_on": date,
            "points": [{"id": m["id"], "role": m["role"], "label": m["label"], "room_xyz_m": m["xyz"],
                        "measured_from": {"x": "x = 0", "y": "y = 0", "z": "floor"}, "measured_with": "virtual-scene", "note": note}
                       for m in marks]}


def disc_clicks(left, camera, marks):
    """Each mark's disc centroid in the take's own left plane, near where the camera's pose projects it."""
    pose = np.array(camera["room_from_left"], dtype=float)
    rotation, position, k = pose[:3, :3], pose[:3, 3], camera["left_k"]
    clicks = []
    for mark in marks:
        p = rotation.T @ (np.array(mark["xyz"], dtype=float) - position)
        u, v = k["fx"] * p[0] / p[2] + k["cx"], k["fy"] * p[1] / p[2] + k["cy"]
        y0, y1, x0, x1 = max(0, int(v) - 14), min(400, int(v) + 15), max(0, int(u) - 14), min(640, int(u) + 15)
        ys, xs = np.nonzero(left[y0:y1, x0:x1] >= 245)
        if not len(xs):
            raise Stop(5, f"mark {mark['id']}: no bright disc near where it should be")
        clicks.append({"id": mark["id"], "u": round(float(xs.mean() + x0), 2), "v": round(float(ys.mean() + y0), 2)})
    return clicks


def fit(modules, fit_dir, marks_take, camera_reply, shots, date):
    register, method, worksheet, rig = modules
    label_in_memory(register, worksheet, rig)
    references = {"x": "the picture's own frame: x = 0 through the couch's middle", "y": "the picture's own frame: y = 0 through the couch's middle",
                  "z": "the picture's own frame: the floor"}
    write_json(fit_dir / "worksheet.json", worksheet_doc(shots["marks"], references, "SYNTHETIC: shots.json's authored mark; nobody measured it", date))
    write_json(fit_dir / "camera-calibration.json", camera_reply)
    m, proposals = closed_method(method)
    sheet = worksheet.load(fit_dir / "worksheet.json", need_positions=False)
    n = register.frame_number(marks_take, None, fit_dir)
    session = register.ClickSession(fit_dir, marks_take, n, sheet, m)
    left = np.frombuffer(bytes(session.bundle["left"]), np.uint8).reshape(400, 640)
    for click in disc_clicks(left, shots["camera"], shots["marks"]):
        try:
            session.click(click)
        except Exception as exc:  # serve.PageError carries den-register's refusal
            raise Stop(5, f"ClickSession refused {click['id']}: {exc}") from None
    session.done()
    text = io.StringIO()
    code, report = register.run_fit(fit_dir, marks_take, m=m, out=text)   # never the console: it prints the camera's place
    (fit_dir / "fit.log").write_text(text.getvalue(), encoding="utf-8", newline="\n")
    return code, report, proposals, n


def fit_hop(fit_dir, marks_take, report, proposals, frame, tools):
    files = sorted(p for p in fit_dir.iterdir() if p.is_file())
    row = next(r for r in csv.DictReader(io.StringIO((marks_take / "frames.csv").read_text(encoding="utf-8"))) if int(r["n"]) == frame)
    registered = report["result"] == "registered"
    return {"hop": "Camera registration", "word": "ADAPTER" if registered else "MISSING", "ran": "recorded",
            "tool": "tools/den-register/register.py", "commit": tools["den-register"]["commit"], "pin": tools["den-register"]["pin"],
            "tool_files": tools["den-register"]["files"], "result": report["result"], "transform_id": report.get("transform_id"),
            "reasons": [r["reason"] for r in report.get("reasons", [])], "fit_folder": fit_dir.name, "marks_take": marks_take.name,
            "error_bound_mm": round(report["fit"]["error_bound_m"] * 1000, 2) if report.get("fit") else None,
            "read": [{"path": f"{marks_take.name}/{row['file']}", "sha256": row["npz_sha256"]}]
                    + [{"path": f"{fit_dir.name}/{p.name}", "sha256": sha256_file(p)} for p in files if p.name in ("worksheet.json", "camera-calibration.json") or p.name.startswith("clicks-")],
            "wrote": [{"path": f"{fit_dir.name}/{p.name}", "sha256": sha256_file(p)} for p in files
                      if p.name in ("room-model.jsonl", "calibration-records.jsonl", "fit.log") or p.name.startswith("fit-")],
            "room_model": {"path": f"{fit_dir.name}/room-model.jsonl", "sha256": sha256_file(fit_dir / "room-model.jsonl")} if registered else None,
            "slots_closed_in_memory": proposals,
            "stand_ins": ["the method's open slots closed in memory at their proposed values",
                          "register.METHOD, worksheet.TOOLS, worksheet.PEOPLE and rig.SOURCE set in memory, so the record says virtual-scene + fit",
                          "marks rendered from shots.json and clicked by the harness at disc centroids in the take's own left plane",
                          "the pose is in the picture's own frame; only the owner's re-fit places it on a sofa mark"]}


# ---- the set ------------------------------------------------------------------------------------------

def render_hop(result, files, tools_head, shots_path):
    return {"hop": "Input image", "word": "SCRIPTED", "ran": "recorded", "tool": "tools/den-sim/scene/render.mjs",
            "commit": tools_head["commit"], "pin": "HEAD", "renderer": result["renderer"], "three": result["three"],
            "external_requests": result["external_requests"],
            "read": [{"path": path, "sha256": digest} for path, digest in sorted(result["read"].items())],
            "wrote": files, "shots_sha256": sha256_file(shots_path),
            "stand_ins": ["procedural-1, an artificial person, in an authored room and couch, seen by an invented camera (shots.json)",
                          "software GL (SwiftShader) in chrome-headless-shell"]}


def render_files(render_dir, name, poses):
    return [{"kind": kind, "pose": k, "sha256": sha256_file(render_dir / f"{name}.p{k}.{kind}.{ext}")}
            for k in range(poses) for kind, ext in (("rgb", "rgba"), ("left", "rgba"), ("right", "rgba"), ("depth", "f32"))]


def camera_hop(camera, picture, tools_head):
    return {"hop": "Virtual camera and depth", "word": "SCRIPTED", "ran": "recorded", "tool": "tools/den-sim/scene/vcam.py",
            "commit": tools_head["commit"], "pin": "HEAD", "camera_id": vcam.CAMERA_ID, "session": camera.session, "picture": picture,
            "speed": SPEED, "gaps_s": list(vcam.GAPS_S), "pose_frames": vcam.POSE_FRAMES, "frames_built": len(camera.served),
            "stand_ins": ["this camera does not exist: an invented place and intrinsics, perfect rendered depth",
                          "sensor noise, the frame index pixels and the phone slate are drawn by the harness"]}


def take_hop(detail, tools):
    complete = detail["complete"] and detail["exit"] == 0
    return {"hop": "Frames", "word": "ADAPTER" if complete else "MISSING", "ran": "recorded", "tool": "tools/den-take/take.py",
            "commit": tools["den-take"]["commit"], "pin": tools["den-take"]["pin"], "tool_files": tools["den-take"]["files"], **detail,
            "stand_ins": ["cues silenced: the say hook only tells the virtual camera when the slate is up",
                          f"a {SPEED:g}x clock: take.py's settings divided by {SPEED:g}",
                          f"script free, ended by the harness after {ACTIVITY_S:g} s of activity"]}


def check_hop(detail, tools):
    good = detail and detail["verdict"] == "PASS" and detail["retained"] and detail["segments"]
    return {"hop": "Check", "word": "ADAPTER" if good else "MISSING", "ran": "recorded", "tool": "tools/den-take/check.py",
            "commit": tools["den-take"]["commit"], "pin": tools["den-take"]["pin"], "tool_files": tools["den-take"]["files"], **(detail or {}),
            "stand_ins": ["the harness answered the second-person prompt 'none' through a stand-in console, past check.py's console guard: "
                          "nobody is there to answer for a render"]}


def prepare(args):
    guard(loopback=True)
    shots_path = HERE / "shots.json"
    shots = json.loads(shots_path.read_text(encoding="utf-8"))
    seed = shots["seed"] if args.seed is None else args.seed
    if not os.environ.get("LOCALAPPDATA") and not args.out:
        raise SystemExit("LOCALAPPDATA is not set")
    root = Path(args.out or Path(os.environ["LOCALAPPDATA"]) / "den-sim" / "prepared").resolve()
    root.mkdir(parents=True, exist_ok=True)
    set_dir, work = root / random_id(), root / (".work-" + random_id())
    set_dir.mkdir()
    work.mkdir()
    log = Log(set_dir)
    started, date = time.time(), time.strftime("%Y-%m-%d")
    takes, refusal, receipts, fit_info, tools_head = [], None, {}, None, None
    try:
        tools_head = harness_files()
        tools = archive(work / "tools", pins(), log)
        import check
        import method
        import register
        import rig
        import take
        import worksheet
        m = check.load_method()
        log.say(f"Set {set_dir.name} in {root}")
        if tools_head["uncommitted"]:
            log.say("Note: uncommitted edits in " + ", ".join(tools_head["uncommitted"]) + "; the receipts say so.")

        # Hop 1: one word per scene; blank uses its scene's render with the picture blanked by the camera.
        words = []
        for _ in shots["scenes"]:
            words.append(random_word(set(words)))
        render_name = [words[scene.get("render_of_scene", i)] for i, scene in enumerate(shots["scenes"])]
        items = [{"name": "marks", "person": None, "marks": True, "poses": 1}]
        items += [{"name": words[i], "person": scene["person"], "marks": False, "poses": shots["poses"]}
                  for i, scene in enumerate(shots["scenes"]) if "render_of_scene" not in scene]
        render_dir, truth_dir = set_dir / "render", set_dir / "truth"
        job = work / "render-job.json"
        write_json(job, {"shots": str(shots_path), "seed": seed, "items": items, "out": str(render_dir), "truth": str(truth_dir),
                         "scratch": str(work / "render")})
        (work / "render" / "tmp").mkdir(parents=True)
        env = {**os.environ, "TEMP": str(work / "render" / "tmp"), "TMP": str(work / "render" / "tmp")}
        done = log.run(["node", HERE / "render.mjs", "--job", job], env=env, timeout=600)
        lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
        result = json.loads(lines[-1]) if lines else {"ok": False, "reason": done.stderr.decode("utf-8", "replace")[-300:]}
        if done.returncode or not result.get("ok"):
            raise Stop(1, "render.mjs: " + str(result.get("reason") or result))
        log.say(f"Render: {len(items) - 1} scenes and the marks in {result['wall_s']:.1f} s on {result['renderer']}, "
                f"three r{result['three']}, {result['external_requests']} requests left loopback")

        token = work / "camera-token.txt"
        token.write_text(secrets.token_urlsafe(32) + "\n", encoding="ascii", newline="\n")
        lit = lambda tau: check.pattern_lit(tau, m)
        clock = lambda t_host: t_host - PHONE_CLOCK_S

        def one_take(name, poses, picture):
            folder = set_dir / random_id()
            camera = vcam.VirtualCamera(render_dir, name, poses, shots["camera"], token_file=token, picture=picture,
                                        speed=SPEED, seed=seed, pattern_lit=lit, slate_tau=clock)
            try:
                hop3, hop4, problem = record_take(take, check, m, camera, folder, token, log)
            finally:
                camera.close()
            digest = sha256_file(folder / "frames.csv") if (folder / "frames.csv").exists() else None
            hops = {"1": render_hop(result, render_files(render_dir, name, poses), tools_head, shots_path),
                    "2": camera_hop(camera, picture, tools_head), "3": take_hop(hop3, tools), "4": check_hop(hop4, tools)}
            return folder, digest, hops, problem, camera

        # The marks take, then the fit, before any scene take: a take older than its registration is refused.
        folder, digest, hops, problem, camera = one_take("marks", 1, "rendered")
        takes.append({"take": folder.name, "digest": digest, "kind": "marks"})
        receipts[folder] = ("marks", hops)
        if problem:
            raise Stop(3 if hops["3"]["word"] == "MISSING" else 4, problem)
        fit_dir = set_dir / random_id()
        fit_dir.mkdir()
        code, report, proposals, frame = fit((register, method, worksheet, rig), fit_dir, folder, vcam.calibration_reply(camera.session, shots["camera"]), shots, date)
        hop5 = fit_hop(fit_dir, folder, report, proposals, frame, tools)
        hops["5"] = hop5
        fit_info = {"folder": fit_dir.name, "result": report["result"], "transform_id": report.get("transform_id"),
                    "room_model_sha256": hop5["room_model"]["sha256"] if hop5["room_model"] else None}
        log.say(f"Marks take {folder.name}: {kept(hops)} frames, {hops['4'].get('verdict')}; "
                f"fit {report['result']} {report.get('transform_id') or ''} (error bound {hop5['error_bound_mm']} mm)")
        if code:
            raise Stop(5, "run_fit refused: " + ", ".join(hop5["reasons"]))

        # The scene takes, in a random order, so nothing reads the scene from when its take was recorded.
        order = list(range(len(shots["scenes"])))
        secrets.SystemRandom().shuffle(order)
        display = {}
        for count, i in enumerate(order, 1):
            scene = shots["scenes"][i]
            poses = shots["poses"]
            folder, digest, hops, problem, _ = one_take(render_name[i], poses, scene.get("picture", "rendered"))
            hops["5"] = hop5
            takes.append({"take": folder.name, "digest": digest, "kind": "scene"})
            receipts[folder] = ("scene", hops)
            display[i] = {"take": folder.name, "word": words[i], "label": scene["display"]}
            if digest:
                write_truth(truth_dir, words[i], render_name[i], scene, folder, poses)
            log.say(f"Take {count} of {len(order)}, {folder.name}: {kept(hops)} frames, "
                    f"{hops['4'].get('verdict') or 'not checked'}, {hops['4'].get('usable_s', 0)} s usable")
            if problem:
                raise Stop(3 if hops["3"]["word"] == "MISSING" else 4, problem)
        write_json(set_dir / "display.json", {"schema": SCHEMA_DISPLAY, "note": "The display file: read only by the panel. "
                                              "A scene's word is its only name, also in the render's file names and the truth folder.",
                                              "scenes": [display[i] for i in sorted(display)]})
    except Stop as stop:
        refusal = {"hop": stop.hop, "reason": stop.reason}
        log.say(f"Stopped at hop {stop.hop}: {stop.reason}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for folder, (kind, hops) in receipts.items():
        failed = refusal if refusal and folder.name == takes[-1]["take"] else None
        write_json(folder / "prepare-receipt.json", receipt(folder, kind, hops, failed, set_dir, seed, date, started, tools_head))
    write_json(set_dir / "set.json", {
        "schema": SCHEMA_SET, "set": set_dir.name, "recorded": date, "started_unix": round(started, 3), "ended_unix": round(time.time(), 3),
        "seed": seed, "speed": SPEED, "activity_s": ACTIVITY_S, "figure": shots["figure"]["id"], "way": shots["figure"]["way"],
        "marks_take": takes[0] if takes else None, "fit": fit_info, "refusal": refusal,
        "takes": sorted(takes, key=lambda t: t["take"]),
        "files": {"receipt": "<take>/prepare-receipt.json", "display": "display.json", "truth": "truth/<word>/", "render": "render/",
                  "log": "prepare.log", "argv": "argv.jsonl"},
        "note": "Takes are listed by folder id, not by scene, and were recorded in a random order after the marks take."})
    if not refusal:
        append_try(root, set_dir, seed, shots, [t["digest"] for t in takes if t["kind"] == "scene"])
        size = sum(p.stat().st_size for p in set_dir.rglob("*") if p.is_file())
        log.say(f"Done in {time.time() - started:.0f} s: {len(takes)} takes, {size / 1e6:.0f} MB in {set_dir}")
    return 0 if not refusal else 1


def kept(hops):
    return (hops["3"].get("counts") or {}).get("kept", 0)


def receipt(folder, kind, hops, refusal, set_dir, seed, date, started, harness):
    digest = sha256_file(folder / "frames.csv") if (folder / "frames.csv").exists() else None
    return {"schema": SCHEMA_RECEIPT, "take_digest": digest, "take": folder.name, "kind": kind, "set": set_dir.name,
            "recorded": date, "recorded_unix": round(started, 3), "seed": seed, "harness": harness, "hops": hops, "refusal": refusal}


def write_truth(truth_dir, word, render_name, scene, folder, poses):
    """The truth folder: what the scene really holds, for the report and (until step 4) fake_detector only."""
    target = truth_dir / word
    target.mkdir(parents=True, exist_ok=True)
    if render_name != word and (truth_dir / render_name / "poses.json").exists():
        shutil.copyfile(truth_dir / render_name / "poses.json", target / "poses.json")
    rows = list(csv.DictReader(io.StringIO((folder / "frames.csv").read_text(encoding="utf-8"))))
    write_json(target / "take.json", {"schema": SCHEMA_TRUTH, "take": folder.name, "person": scene["person"],
                                      "picture": scene.get("picture", "rendered"), "poses": poses, "pose_frames": vcam.POSE_FRAMES,
                                      "pose_of_rgb_seq": {r["rgb_seq"]: vcam.pose_of(int(r["rgb_seq"]) - 500, poses) for r in rows}})


def append_try(root, set_dir, seed, shots, digests):
    """tries.jsonl is append-only. A line counts as a Qwen try only when Qwen was asked: `root` is the
    served model's name and `body_sha256` is a request's 64 hex. prepare's lines carry neither."""
    line = {"schema": "den-sim-try-1", "by": "prepare.py", "kind": "prepared", "figure": shots["figure"]["id"], "way": shots["figure"]["way"],
            "seed": seed, "set": set_dir.name, "takes": digests, "body_sha256": None, "root": None, "facts": None,
            "invalid_reason": None, "outcome": "prepared", "written_unix": round(time.time(), 3)}
    with open(root / "tries.jsonl", "a", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(line, sort_keys=True) + "\n")


# ---- the owner's re-fit -------------------------------------------------------------------------------

def floor_centre(floor_file):
    """The middle of the pack's walkable floor, as run.mjs computes it."""
    polygon = read_owner_json(floor_file)["walkable_polygon"]
    return [round(sum(corner[axis] for corner in polygon) / len(polygon) * 100) / 100 for axis in (0, 1)]


def turn_degrees(facing_file):
    if not facing_file:
        return 0.0
    value = read_owner_json(facing_file)
    value = value.get("degrees") if type(value) is dict else value
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Stop(5, "facing.json must hold one number of degrees, or {\"degrees\": n}")
    return float(value)


def placement(mark, centre, turn_deg, seat_height_m):
    """The private transform: picture frame -> the owner's room. Rotation about z, then a shift."""
    finite = lambda p: type(p) is list and len(p) == 3 and all(type(v) in (int, float) and math.isfinite(v) for v in p)
    zone, seat = mark.get("zone") or {}, (mark.get("seat") or {}).get("point_m")
    vertices = zone.get("vertices_m")
    if mark.get("schema") != MARK_SCHEMA or not finite(seat) or type(vertices) is not list or len(vertices) < 3 or not all(map(finite, vertices)):
        raise Stop(5, "the sofa mark has no seat point or zone corners")
    middle = zone.get("centre_m") if finite(zone.get("centre_m")) else [sum(v[i] for v in vertices) / len(vertices) for i in range(3)]
    a, b = max(zip(vertices, vertices[1:] + vertices[:1]), key=lambda edge: math.dist(*edge))
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length < 1e-6:
        raise Stop(5, "the sofa mark's longest edge is vertical")
    normal = [-(b[1] - a[1]) / length, (b[0] - a[0]) / length]
    if normal[0] * (centre[0] - middle[0]) + normal[1] * (centre[1] - middle[1]) < 0:
        normal = [-normal[0], -normal[1]]
    angle = math.atan2(normal[0], -normal[1]) + math.radians(turn_deg)      # the picture's reader faces -y
    return {"rotation_z_rad": angle, "facing_unit": [math.sin(angle), -math.cos(angle)],
            "translation_m": [middle[0], middle[1], seat[2] - seat_height_m]}


def place(point, transform):
    c, s = math.cos(transform["rotation_z_rad"]), math.sin(transform["rotation_z_rad"])
    t = transform["translation_m"]
    return [c * point[0] - s * point[1] + t[0], s * point[0] + c * point[1] + t[1], point[2] + t[2]]


def refit(args):
    guard(loopback=False)
    set_dir = Path(args.set).resolve()
    info = json.loads((set_dir / "set.json").read_text(encoding="utf-8"))
    if info.get("schema") != SCHEMA_SET or not info.get("fit") or info["fit"].get("result") != "registered":
        print("re-fit refused: the set has no registered fit", file=sys.stderr)
        return 2
    source, marks_take = set_dir / info["fit"]["folder"], set_dir / info["marks_take"]["take"]
    inputs = {"mark": sha256_file(args.mark), "export": sha256_file(args.export), "floor": sha256_file(args.floor),
              "facing": sha256_file(args.facing) if args.facing else None, "set_worksheet": sha256_file(source / "worksheet.json"),
              "marks_take": info["marks_take"]["digest"]}
    fits = Path(args.fits or Path(os.environ["LOCALAPPDATA"]) / "den-sim" / "private" / "fits").resolve()
    fits.mkdir(parents=True, exist_ok=True)
    for earlier in sorted(fits.glob("*/refit-receipt.json")):
        old = json.loads(earlier.read_text(encoding="utf-8"))
        if old.get("set") == set_dir.name and old.get("inputs") == inputs and old.get("result") == "registered":
            print(f"re-fit unchanged: {earlier.parent.name} was built from these files")
            return 0
    folder, work = fits / random_id(), fits / (".work-" + random_id())
    try:
        shots = json.loads((HERE / "shots.json").read_text(encoding="utf-8"))
        transform = placement(read_owner_json(args.mark), floor_centre(args.floor), turn_degrees(args.facing), shots["couch"]["seat_height_m"])
        folder.mkdir()
        work.mkdir()
        tools = archive(work / "tools", pins())
        import method
        import register
        import rig
        import worksheet
        label_in_memory(register, worksheet, rig)
        for item in source.iterdir():
            if item.name == "camera-calibration.json" or item.name == f"clicks-{marks_take.name}.json":
                shutil.copyfile(item, folder / item.name)
        sheet = json.loads((source / "worksheet.json").read_text(encoding="utf-8"))
        for point in sheet["points"]:
            point["room_xyz_m"] = [round(v, 6) for v in place(point["room_xyz_m"], transform)]
            point["note"] = "SYNTHETIC: the authored mark, placed in your room by the private transform"
        sheet["references"] = {"x": "your room frame, through the private placement on your sofa mark", "y": "your room frame",
                               "z": "your room frame: the floor"}
        write_json(folder / "worksheet.json", sheet)
        write_json(folder / "transform.json", {"schema": SCHEMA_PLACEMENT, "private": True, **transform,
                                               "turn_deg": turn_degrees(args.facing), "inputs": inputs})
        m, proposals = closed_method(method)
        text = io.StringIO()
        code, report = register.run_fit(folder, marks_take, m=m, out=text)    # the camera's place goes to the private log only
        (folder / "fit.log").write_text(text.getvalue(), encoding="utf-8", newline="\n")
    except Stop as stop:
        print(f"re-fit refused: {stop.reason}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    registered = report["result"] == "registered"
    write_json(folder / "refit-receipt.json", {
        "schema": SCHEMA_REFIT, "set": set_dir.name, "result": report["result"], "transform_id": report.get("transform_id"),
        "reasons": [r["reason"] for r in report.get("reasons", [])], "inputs": inputs, "slots_closed_in_memory": proposals,
        "room_model_sha256": sha256_file(folder / "room-model.jsonl") if registered else None,
        "tools": tools, "refit_unix": round(time.time(), 3)})
    if registered:
        print(f"re-fit registered {report['transform_id']} in {folder.name}")
        return 0
    print(f"re-fit refused: {', '.join(r['reason'] for r in report.get('reasons', []))} (see {folder.name}\\fit.log)", file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare the den simulation's recorded scenes, or re-fit a set on the owner's mark.")
    parser.add_argument("--out", help=r"the prepared folder (default %%LOCALAPPDATA%%\den-sim\prepared)")
    parser.add_argument("--seed", type=int, help="moves the small motion and the noise (default: shots.json's)")
    parser.add_argument("--refit", action="store_true", help="re-fit one set on the owner's files; only run.mjs and fresh_run.py start this")
    parser.add_argument("--set", help="--refit: the prepared set")
    parser.add_argument("--mark", help="--refit: the owner's sofa mark")
    parser.add_argument("--export", help="--refit: the owner's registration export the mark names")
    parser.add_argument("--floor", help="--refit: the pack's floor.json, for the floor's centre")
    parser.add_argument("--facing", help="--refit: facing.json, degrees from the stated facing (left is positive)")
    parser.add_argument("--fits", help=r"--refit: where the new fit goes (default %%LOCALAPPDATA%%\den-sim\private\fits)")
    args = parser.parse_args(argv)
    if args.refit:
        if not (args.set and args.mark and args.export and args.floor):
            parser.error("--refit needs --set, --mark, --export and --floor")
        return refit(args)
    return prepare(args)


if __name__ == "__main__":
    sys.exit(main())
