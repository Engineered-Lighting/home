#!/usr/bin/env python3
"""Record one den take for rung R. Standard library only; runs on the owner's PC.

    python -B tools\\den-take\\take.py --script commission --out C:\\den-takes\\c1
    python -B tools\\den-take\\take.py --script free --out C:\\den-takes\\<date>-<n>
    python -B tools\\den-take\\take.py --script hard-a --out C:\\den-takes\\<date>-<n>

A staged script (a file in tools\\den-take\\scripts, format den-take-script-1)
speaks each of its blocks at its time after "Begin." and starts the end slate
at its end_s; Enter still ends the take early.

A polling thread reads /frame.npz from the installed camera every 0.2 s with
the bearer token from a private file, saves each new bundle unchanged and adds
a row to frames.csv. The main thread speaks the cues without waiting and peeks
at each slate. The take stops at the first 503, a camera session change, or
15 s with no new frame. The token is never printed, logged or written.
"""
import argparse
import csv
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import check

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
SCHEMA = "den-take-1"
SCRIPT_FORMAT = "den-take-script-1"
FIXED_SCRIPTS = ("commission", "free")
SCRIPT_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
CASE_NAME = re.compile(r"[a-z][a-z_]{0,31}")
DEFAULT_CAMERA = "http://192.168.1.168:8090"
DEFAULT_TOKEN_FILE = r"C:\den-takes\private\camera-token.txt"
MIN_FREE_BYTES = 35 * 1024 ** 3  # 35 GB as Windows shows it
MAX_BODY = 4 * 1024 ** 2
CSV_FIELDS = ("n", "file", "rgb_seq", "t_s", "t_host_s", "t_device_s", "exposure_s",
              "npz_sha256", "recv_pc_mono_s", "recv_pc_unix", "bytes")
SPEECH = ("Add-Type -AssemblyName System.Speech; "
          "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:DEN_TAKE_SAY)")
clock = time.perf_counter  # PC monotonic clock for cues and receipts
sleep = time.sleep  # the cue loop's wait, so tests can drive it with a fake clock


@dataclass(frozen=True)
class Settings:
    slate_s: float
    poll_s: float = 0.2
    stall_s: float = 15.0
    attempts: int = 3  # one slate try plus at most two retries
    hold_lead_s: float = 5.0
    say_gap_s: float = 3.0
    away_s: float = 10.0
    commission_activity_s: float = 180.0
    free_max_s: float = 28 * 60.0
    health_retry_s: float = 1.0  # gap between the final /health tries


class Refused(Exception):
    pass


class Stopped(Exception):
    pass


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_script(name, settings):
    """None for commission and free; else the checked script file. Called before any camera read.

    A den-take-script-1 file is {"format", "end_s", "blocks": [{"at_s", "case", "say"}, ...]}: each
    block is spoken at_s seconds after "Begin.", and the end slate starts end_s seconds after it.
    Cues are at least say_gap_s apart, the first at least say_gap_s after "Begin.", and end_s is at
    least say_gap_s after the last cue and at most free_max_s (R's cap on the activity part)."""
    if name in FIXED_SCRIPTS:
        return None
    known = ", ".join(sorted(p.stem for p in SCRIPTS.glob("*.json"))) or "none"
    if type(name) is not str or not SCRIPT_NAME.fullmatch(name):
        raise Refused(f"--script must be commission, free or a script in tools\\den-take\\scripts ({known}); "
                      "nothing recorded")
    path = SCRIPTS / f"{name}.json"
    if not path.is_file():
        raise Refused(f"no script {name!r} in tools\\den-take\\scripts (there: {known}); nothing recorded")
    raw = path.read_bytes()

    def bad(why):
        return Refused(f"scripts\\{name}.json: {why}; nothing recorded")

    def refuse_constant(_):
        raise ValueError("non-finite number")
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=refuse_constant)
    except (UnicodeDecodeError, ValueError):
        raise bad("not valid JSON") from None
    if type(data) is not dict or set(data) != {"format", "end_s", "blocks"}:
        raise bad("the file needs exactly format, end_s and blocks")
    if data["format"] != SCRIPT_FORMAT:
        raise bad(f"format must be {SCRIPT_FORMAT}")
    if type(data["blocks"]) is not list or not data["blocks"]:
        raise bad("blocks must be a list of one or more blocks")
    gap, previous, after = settings.say_gap_s, 0.0, '"Begin."'
    for i, block in enumerate(data["blocks"], 1):
        if type(block) is not dict or set(block) != {"at_s", "case", "say"}:
            raise bad(f"block {i} needs exactly at_s, case and say")
        if not number(block["at_s"]):
            raise bad(f"block {i}: at_s must be a number of seconds")
        if block["at_s"] < previous + gap:
            raise bad(f"block {i} starts {block['at_s']:g} s after \"Begin.\", under {gap:g} s after {after}; "
                      "times must increase, at least the speech gap apart")
        if type(block["case"]) is not str or not CASE_NAME.fullmatch(block["case"]):
            raise bad(f"block {i}: case must be a lower-case name such as idle_seated")
        say_text = block["say"]
        if type(say_text) is not str or not say_text.strip() or len(say_text) > 300 \
                or not all(32 <= ord(ch) < 127 for ch in say_text):
            raise bad(f"block {i}: say must be one line of plain text")
        previous, after = block["at_s"], f"block {i}"
    end = data["end_s"]
    if not number(end) or end < previous + gap:
        raise bad(f"end_s must be a number at least {gap:g} s after the last block")
    if end > settings.free_max_s:
        raise bad(f"end_s {end:g} s is longer than the {settings.free_max_s / 60:g}-minute activity cap")
    return {"name": name, "file": f"scripts/{name}.json", "sha256": hashlib.sha256(raw).hexdigest(),
            "blocks": data["blocks"], "end_s": end}


def say_argv():
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SPEECH]


def say(text):
    """Speak without waiting. The text goes through an env var, never the command line."""
    print(text, flush=True)
    try:
        subprocess.Popen(say_argv(), env=dict(os.environ, DEN_TAKE_SAY=text), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def inside_checkout(path):
    resolved = Path(path).resolve()
    return any((p / ".git").exists() for p in (resolved, *resolved.parents))


def in_onedrive(path):
    resolved = Path(path).resolve()
    if any(part.lower().startswith("onedrive") for part in resolved.parts):
        return True
    roots = [os.environ.get(name) for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    return any(root and (Path(root).resolve() == resolved or Path(root).resolve() in resolved.parents)
               for root in roots)


def free_bytes(path):
    path = Path(path)
    while not path.exists():
        if path.parent == path:
            raise Refused("the drive for --out does not exist")
        path = path.parent
    return shutil.disk_usage(path).free


def run_dir(out, token_file):
    """An absolute folder outside every git checkout and OneDrive, holding no take."""
    path = Path(out)
    if not path.is_absolute():
        raise Refused("--out must be an absolute folder, for example C:\\den-takes\\c1")
    path = path.resolve()
    if inside_checkout(path):
        raise Refused("--out is inside a git checkout; takes never go into git")
    if in_onedrive(path):
        raise Refused("--out is inside OneDrive; takes stay on this PC only")
    if any((path / name).exists() for name in ("take.json", "frames.csv", "frames")):
        raise Refused("--out already holds a take; use a new folder")
    token = Path(token_file).resolve()
    if path == token.parent or path in token.parents:
        raise Refused("--out must not contain the private token file")
    if free_bytes(path) < MIN_FREE_BYTES:
        raise Refused("less than 35 GB free on the take drive")
    return path


def read_token(path):
    try:
        token = Path(path).read_bytes().decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        raise Refused("cannot read the token file (see README step 2)") from None
    if len(token) < 24 or not all(33 <= ord(ch) < 127 for ch in token):
        raise Refused("the token file must hold one token of at least 24 printable characters")
    return token


class Camera:
    """GET-only client for the installed camera's /health and /frame.npz."""

    def __init__(self, url, token):
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query:
            raise Refused("--camera must look like http://<host>:<port>")
        self.host, self.port = parts.hostname, parts.port or 80
        self._headers = {"Authorization": "Bearer " + token} if token else {}

    def __repr__(self):
        return f"Camera({self.host}:{self.port})"

    def get(self, path, timeout=5.0):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request("GET", path, headers=self._headers)
            response = connection.getresponse()
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise http.client.HTTPException("response too large")
            if response.length:  # the connection closed part way through the body
                raise http.client.IncompleteRead(body, response.length)
            return response.status, body
        finally:
            connection.close()

    def health(self):
        try:
            status, body = self.get("/health")
        except OSError as exc:  # camera not reached; RemoteDisconnected is an OSError too, so lands here
            return {"status": None, "body": None, "error": type(exc).__name__}
        except http.client.HTTPException as exc:  # reached, but the reply was not whole HTTP
            return {"status": None, "body": None, "error": type(exc).__name__, "answered": True}
        if status not in (200, 503):
            return {"status": status, "body": None}
        try:
            parsed = json.loads(body)
        except ValueError as exc:  # JSONDecodeError, or bytes that are not UTF-8
            return {"status": status, "body": None, "error": type(exc).__name__}
        return {"status": status, "body": parsed if type(parsed) is dict else None}


def final_health(camera, settings, tries=3):
    """/health after the take: the first reply with a JSON body (a 503 counts), else the last."""
    for attempt in range(tries):
        if attempt:
            time.sleep(settings.health_retry_s)
        reply = camera.health()
        if type(reply["body"]) is dict:
            break
    return reply


class TakeWriter:
    """Saves bundles byte for byte as frames/<n>-<rgb sequence>.npz and rows in frames.csv."""

    def __init__(self, folder):
        self.folder = folder
        (folder / "frames").mkdir(parents=True)
        self.file = open(folder / "frames.csv", "x", newline="", encoding="utf-8")
        self.csv = csv.DictWriter(self.file, CSV_FIELDS, lineterminator="\n")
        self.csv.writeheader()
        self.file.flush()
        self.lock = threading.Lock()
        self.index = []  # (recv_pc_mono_s, path, t_host)
        self.first_t_host = None

    def add(self, data, meta, recv_mono, recv_unix):
        n = len(self.index) + 1
        name = f"frames/{n:06d}-{meta['rgb_seq']}.npz"
        with open(self.folder / name, "xb") as target:
            target.write(data)
        if self.first_t_host is None:
            self.first_t_host = meta["t_host"]
        self.csv.writerow({
            "n": n, "file": name, "rgb_seq": meta["rgb_seq"],
            "t_s": f"{meta['t_host'] - self.first_t_host:.6f}", "t_host_s": repr(meta["t_host"]),
            "t_device_s": repr(meta["t_device"]),
            "exposure_s": "" if meta["exposure"] is None else repr(meta["exposure"]),
            "npz_sha256": hashlib.sha256(data).hexdigest(), "recv_pc_mono_s": f"{recv_mono:.6f}",
            "recv_pc_unix": f"{recv_unix:.6f}", "bytes": len(data)})
        self.file.flush()
        with self.lock:
            self.index.append((recv_mono, self.folder / name, meta["t_host"]))

    def window(self, start, end):
        with self.lock:
            return [(path, t_host) for recv, path, t_host in self.index if start <= recv <= end]

    def close(self):
        self.file.close()


class Poller(threading.Thread):
    def __init__(self, camera, writer, session, settings):
        super().__init__(daemon=True)
        self.camera, self.writer, self.session, self.settings = camera, writer, session, settings
        self.halt, self.done = threading.Event(), threading.Event()
        self.polls = self.duplicates = self.errors = 0
        self.stop_reason = None

    def run(self):
        last, last_new = None, clock()
        next_poll = last_new
        try:
            while not self.halt.is_set():
                self.polls += 1
                try:
                    status, body = self.camera.get("/frame.npz")
                except (OSError, http.client.HTTPException):
                    status, body = None, b""
                received, received_unix = clock(), time.time()
                if status == 503:
                    self.stop_reason = "camera_503"
                    return
                if status in (401, 403):
                    self.stop_reason = "unauthorized"
                    return
                if status != 200:
                    self.errors += 1
                else:
                    try:
                        meta = check.read_bundle(body)[0]
                    except check.BundleError as exc:
                        self.stop_reason = f"bad_bundle: {exc}"
                        return
                    if meta["session"] != self.session:
                        self.stop_reason = "session_change"
                        return
                    if last == (meta["rgb_seq"], meta["t_host"]):
                        self.duplicates += 1
                    else:
                        self.writer.add(body, meta, received, received_unix)
                        last, last_new = (meta["rgb_seq"], meta["t_host"]), received
                if received - last_new > self.settings.stall_s:
                    self.stop_reason = "no_new_frame"
                    return
                next_poll += self.settings.poll_s
                delay = next_poll - clock()
                if delay > 0:
                    self.halt.wait(delay)
                else:
                    next_poll = clock()
        except Exception as exc:  # disk full, permissions: stop the take, never hang
            self.stop_reason = f"error: {type(exc).__name__}"
        finally:
            self.done.set()


def peek(window, m):
    """Is this slate attempt readable? Never returns or speaks the offset."""
    try:
        rgbs = [(t_host, check.read_bundle(path.read_bytes(), with_rgb=True)[1]) for path, t_host in window]
    except (OSError, check.BundleError):
        return {"readable": False, "reason": "unreadable_bundle", "flashes": 0, "frames": len(window)}
    if len({hashlib.sha256(rgb).digest() for _, rgb in rgbs}) < len(rgbs):
        return {"readable": False, "reason": "stale_frame", "flashes": 0, "frames": len(window)}
    fit = check.fit_slate([(t, check.y_plane(rgb, int(m["slate_pixel_step"]))) for t, rgb in rgbs], m)
    return {name: fit[name] for name in ("readable", "reason", "flashes", "frames")}


def clear_typed_input():
    """Drop keys typed before the activity part, so an early Enter cannot end it."""
    if os.name == "nt":
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    elif sys.stdin.isatty():
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)


def enter_pressed():
    """Poll the console for Enter without a blocked reader thread."""
    if os.name == "nt":
        import msvcrt
        while msvcrt.kbhit():
            if msvcrt.getwch() in "\r\n":
                return True
        return False
    import select
    if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
        return bool(sys.stdin.readline())
    return False


class Script:
    def __init__(self, take, poller, writer, settings, m, say, peek):
        self.take, self.poller, self.writer = take, poller, writer
        self.settings, self.m, self.say, self.peek = settings, m, say, peek

    def cue(self, name, text, **extra):
        entry = dict(cue=name, text=text, pc_mono_s=clock(), pc_unix=time.time(), **extra)
        self.take["cues"].append(entry)
        self.say(text)
        return entry["pc_mono_s"]

    def pause(self, seconds, until=None):
        """Wait, or less when until() says so; returns True then. Raises Stopped if the poller stopped."""
        end, early = clock() + seconds, False
        while clock() < end:
            if until and until():
                early = True
                break
            if self.poller.done.is_set():
                raise Stopped()
            sleep(min(0.1, max(0.0, end - clock())))
        if self.poller.done.is_set():
            raise Stopped()
        return early

    def play(self, staged, begin, enter):
        """Speak each block at "Begin." + at_s, then wait for end_s. Enter ends it at once."""
        for block in staged["blocks"]:
            if self.pause(begin + block["at_s"] - clock(), until=enter):
                return
            self.cue("stage", block["say"], case=block["case"], at_s=block["at_s"])
        self.pause(begin + staged["end_s"] - clock(), until=enter)

    def slate(self, which):
        self.cue(f"hold_phone_{which}", "Hold the phone to the camera.")
        self.pause(self.settings.hold_lead_s)
        for attempt in range(1, self.settings.attempts + 1):
            start = self.cue(f"slate_{which}", "Slate.", attempt=attempt)
            self.pause(self.settings.slate_s)
            end = clock()
            result = self.peek(self.writer.window(start, end), self.m)
            self.take["slates"].append({"slate": which, "attempt": attempt, "pc_start_s": start,
                                        "pc_end_s": end, "peek": result})
            if result["readable"]:
                return True
            if attempt < self.settings.attempts:
                self.cue(f"slate_{which}_again", "Unreadable, again.")
                self.pause(self.settings.say_gap_s)
        return False

    def run(self, script, enter, staged=None):
        if not self.slate("start"):
            self.take["stop_reason"] = "start_slate_unreadable"
            self.cue("stopped", "Unreadable three times. Take stopped.")
            return
        self.cue("phone_away", "Slate OK. Put the phone away.")
        self.pause(self.settings.away_s)
        begin = self.cue("begin", "Begin.")
        if script == "commission":
            self.pause(self.settings.commission_activity_s)
        elif staged is None:
            clear_typed_input()
            print("Press Enter to end the take.", flush=True)
            self.pause(self.settings.free_max_s, until=enter)
        else:
            clear_typed_input()
            print(f"Script {staged['name']}: the end slate starts after {staged['end_s'] / 60:g} minutes. "
                  "Press Enter to end the take early.", flush=True)
            self.play(staged, begin, enter)
        ok = self.slate("end")
        self.cue("done", "Slate OK. Done." if ok else "End slate unreadable. Done.")


def write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as target:
        json.dump(data, target, indent=1)
        target.write("\n")
    os.replace(temporary, path)


def run(args, *, say=say, peek=peek, enter=enter_pressed, settings=None, m=None):
    m = m or check.load_method()
    settings = settings or Settings(slate_s=m["slate_length_s"])
    staged = load_script(args.script, settings)  # a bad or unknown script is refused before the camera
    folder = run_dir(args.out, args.token_file)
    camera = Camera(args.camera, read_token(args.token_file))
    before = camera.health()
    session = (before.get("body") or {}).get("session")
    healthy = (before["body"] or {}).get("healthy") is True
    error = before.get("error")
    if before["status"] is None and not before.get("answered"):
        raise Refused(f"cannot reach the camera at {camera.host}:{camera.port} ({error}); "
                      "try the ssh tunnel in README step 4; nothing recorded")
    if before["status"] in (401, 403):
        raise Refused("the camera refused the token; redo README step 2; nothing recorded")
    if before["status"] is None or (before["status"] == 200 and error):
        raise Refused(f"the camera at {camera.host}:{camera.port} answered but /health was unreadable "
                      f"({error}); do not restart it, tell the agent; nothing recorded")
    if before["status"] != 200 or not healthy or type(session) is not str:
        detail = f", {error}" if error else ""
        raise Refused(f"camera /health is not healthy (status {before['status']}{detail}); "
                      "do not restart it, tell the agent; nothing recorded")
    folder.mkdir(parents=True, exist_ok=True)
    writer = TakeWriter(folder)
    take = {"schema": SCHEMA, "take": folder.name, "script": args.script,
            "script_file": None if staged is None else {"file": staged["file"], "sha256": staged["sha256"]},
            "commissioning": args.script == "commission", "camera": f"http://{camera.host}:{camera.port}",
            "pc_clock": "time.perf_counter", "started_unix": time.time(), "ended_unix": None,
            "disk_free_bytes_at_start": free_bytes(folder), "settings": asdict(settings),
            "method": {"file": check.METHOD_FILE, "sha256": check.file_sha256(HERE / check.METHOD_FILE)},
            "cues": [], "slates": [], "health_before": before, "health_after": None,
            "counts": None, "stop_reason": None, "complete": False, "sha256": None}
    write_json(folder / "take.json", take)
    poller = Poller(camera, writer, session, settings)
    poller.start()
    script = Script(take, poller, writer, settings, m, say, peek)
    try:
        script.run(args.script, enter, staged)
    except Stopped:
        take["stop_reason"] = poller.stop_reason or "poller_stopped"
        script.cue("stopped", "Take stopped.")
    except KeyboardInterrupt:
        take["stop_reason"] = "interrupted"
    except Exception as exc:  # never leave an unexpected failure marked complete
        take["stop_reason"] = take["stop_reason"] or f"error: {type(exc).__name__}"
        raise
    finally:
        poller.halt.set()
        print("Asking the camera's /health once more (up to about 25 s); please wait.", flush=True)
        try:
            poller.join(timeout=15)
        except KeyboardInterrupt:  # a second Ctrl+C while the poller finishes its last request
            pass
        writer.close()
        try:
            take["health_after"] = final_health(camera, settings)
        except KeyboardInterrupt:  # a second Ctrl+C: skip the wait but still save the take below
            take["health_after"] = {"status": None, "body": None, "error": "interrupted"}
        take["ended_unix"] = time.time()
        take["counts"] = {"polls": poller.polls, "kept": len(writer.index),
                          "duplicates": poller.duplicates, "errors": poller.errors}
        if take["stop_reason"] is None and poller.stop_reason:  # stopped by itself near the end
            take["stop_reason"] = poller.stop_reason
        take["complete"] = take["stop_reason"] is None
        take["sha256"] = {name: check.file_sha256(path) for name, path in (
            (check.METHOD_FILE, HERE / check.METHOD_FILE), ("take.py", HERE / "take.py"),
            ("check.py", HERE / "check.py"), ("frames.csv", folder / "frames.csv"))}
        write_json(folder / "take.json", take)
    print(f"Saved {len(writer.index)} frames in {folder} (complete: {take['complete']}"
          f"{', ' + take['stop_reason'] if take['stop_reason'] else ''}).", flush=True)
    print(f"Next: python -B tools\\den-take\\check.py {folder}", flush=True)
    return 0 if take["complete"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record one den take (rung R).")
    parser.add_argument("--script", required=True,
                        help="commission, free, or a staged script in tools\\den-take\\scripts, e.g. hard-a")
    parser.add_argument("--out", required=True, help=r"new absolute folder, e.g. C:\den-takes\c1")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="default %(default)s")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="default %(default)s")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
