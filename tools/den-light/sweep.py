#!/usr/bin/env python3
"""Stage 0 runner for rung 8. Standard library only; runs on the owner's PC.

    python -B tools\\den-light\\sweep.py --prepare C:\\den-light\\stage0-<date> --date <yyyymmdd>
    python -B tools\\den-light\\sweep.py --install-record C:\\den-light\\stage0-<date> --camera http://<host>:8090
    python -B tools\\den-light\\sweep.py --run C:\\den-light\\stage0-<date> --camera http://<host>:8090
    python -B tools\\den-light\\sweep.py --rollback-check C:\\den-light\\stage0-<date> --camera http://<host>:8090
    python -B tools\\den-light\\sweep.py --recheck C:\\den-light\\stage0-<date>\\sweep-<n>

--prepare reads the fixture-check lines the owner saved (before.txt) and writes
the systemd drop-in that points oak-den.service at the meter build with every
flag kept. --install-record reads the after-check lines (after.txt), checks the
camera, and writes install.json. --run holds the camera through POST /meter,
steps exposure, releases, and writes the sweep's response.json.
--rollback-check reads rollback.txt, then waits past the restart and needs a
healthy camera without the meter keys. --recheck recomputes a saved sweep's
verdict from its frames and writes recheck-<n>.json. It talks only to the
camera, never to Home Assistant, and writes no lights. The token is never
printed or written.
"""
import argparse
import http.client
import json
import os
import re
import shutil
import signal
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import meter

HERE = Path(__file__).resolve().parent
METER_FILE = "oak-capture-meter.py"
METER_SERVER_SHA256 = "51593ee36411c4deda7810b3523858094d4d853afb81e379a8f16ad9dbb3e17f"  # oak-capture-meter.py; a test keeps them equal
FIXTURE_SHA256 = "d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132"
PINNED_RELEASE = "victoria-camera-pilot/pilot-20260926/"
PINNED_SCRIPT = PINNED_RELEASE + "capture.py"
DROP_IN_NAME = "zz-den-light-meter.conf"
DROP_IN_PATH = "/etc/systemd/system/oak-den.service.d/" + DROP_IN_NAME
UNIT_DIRS = ("/etc/systemd/system/", "/lib/systemd/system/", "/usr/lib/systemd/system/")
UNIT_KEYS = ("FragmentPath", "DropInPaths", "ExecStart", "ActiveState")
DEFAULT_TOKEN_FILE = r"C:\den-takes\private\camera-token.txt"
MIN_FREE_BYTES = 1024 ** 3  # 1 GB; a sweep needs about 15 MB
MAX_BODY = 4 * 1024 ** 2
SWEEP_NAME = re.compile(r"sweep-(\d+)")
SAFE_ARG = re.compile(r"[A-Za-z0-9_./:=,@+{}$-]+")
EXEC_START = re.compile(r"\{ path=(\S+) ; argv\[\]=(.*?) ; ignore_errors=(yes|no) ;")
SHA_LINE = re.compile(r"([0-9a-f]{64}) [ *](/\S+)")
VOID_REASONS = ("repeat_mismatch", "still_scene_wait_over_limit", "camera_503", "camera_unreachable",
                "new_camera_session", "run_over_limit", "interrupted_or_errored_before_repeat")
SCHEMAS = {"prepare": "den-light-stage0-prepare-1", "install": "den-light-stage0-install-1",
           "rollback": "den-light-stage0-rollback-2", "response": "den-light-stage0-response-1",
           "recheck": "den-light-stage0-recheck-1"}
METER_KEYS = ("sensitivity_iso", "color_temperature_kelvin", "lens_position", "meter")
INSTALL_HEALTH_WAIT_S = 30.0  # --install-record and --rollback-check wait this long for a first healthy /health
ROLLBACK_SETTLE_S = 30.0      # then the rollback check waits this long and needs the same camera session:
                              # a crash-looping Restart=always unit still reads active right after a restart
ROLLBACK_ADVICE = "the meter drop-in is installed, so run the rollback now (README, Rollback)"
DEN_BULBS = "front left, front right, rear left or rear right"


class Refused(Exception):
    """Nothing was started or written."""


class CameraUnreachable(Refused):
    """No whole HTTP reply: a network problem (README step 4's ssh tunnel)."""


class CameraUnhealthy(Refused):
    """The camera answered, but not healthy."""


class Void(Exception):
    def __init__(self, reason, detail=""):
        assert reason in VOID_REASONS, reason
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def code_sha256():
    return {name: meter.file_sha256(HERE / name) for name in ("sweep.py", "meter.py", METER_FILE)}


def write_json(path, value):
    """Write a new JSON file; never replace one."""
    with open(path, "x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, indent=2, sort_keys=True, allow_nan=False)
        out.write("\n")


# ---- run folders -------------------------------------------------------------

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
            raise Refused("the drive for the folder does not exist")
        path = path.parent
    return shutil.disk_usage(path).free


def holds_sweep(path):
    """A sweep's own files, or a sweep-<n> folder, are in path."""
    path = Path(path)
    if not path.is_dir():
        return False
    if any((path / name).exists() for name in ("frames", "frames.jsonl", "response.json")):
        return True
    return any(SWEEP_NAME.fullmatch(child.name) for child in path.iterdir())


def run_folder(path, token_file, *, may_hold_sweeps):
    """An absolute folder outside git and OneDrive, away from the token, on a disk with room."""
    path = Path(path)
    if not path.is_absolute():
        raise Refused("the Stage 0 folder must be an absolute path, for example C:\\den-light\\stage0-20261110")
    path = path.resolve()
    if not path.is_dir():
        raise Refused("the Stage 0 folder does not exist (README step 2 makes it)")
    if inside_checkout(path):
        raise Refused("the folder is inside a git checkout; frames never go into git")
    if in_onedrive(path):
        raise Refused("the folder is inside OneDrive; frames stay on this PC only")
    token = Path(token_file).resolve()
    if path == token.parent or path in token.parents:
        raise Refused("the folder must not contain the private token file")
    if not may_hold_sweeps and holds_sweep(path):
        raise Refused("the folder already holds a sweep; use a new Stage 0 folder")
    if free_bytes(path) < MIN_FREE_BYTES:
        raise Refused("less than 1 GB free on the folder's drive")
    return path


def make_sweep_folder(folder):
    """A new, empty sweep folder with its frames folder; never one that holds a sweep."""
    folder = Path(folder)
    if holds_sweep(folder):
        raise Refused(f"{folder.name} already holds a sweep")
    try:
        folder.mkdir()
    except FileExistsError:
        raise Refused(f"{folder.name} already exists") from None
    (folder / "frames").mkdir()
    return folder


def new_sweep_folder(stage0):
    numbers = [int(m.group(1)) for m in (SWEEP_NAME.fullmatch(c.name) for c in stage0.iterdir()) if m]
    return make_sweep_folder(stage0 / f"sweep-{max(numbers, default=0) + 1}")


# ---- the install lines ---------------------------------------------------------

def read_lines(path):
    try:
        return Path(path).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise Refused(f"cannot read {Path(path).name} (see README)") from None


def parse_unit(text):
    """The systemctl show properties and sha256sum lines the owner saved."""
    props, hashes = {}, {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        match = SHA_LINE.fullmatch(line)
        if match:
            if match.group(2) in hashes:
                raise Refused(f"two sha256 lines for {match.group(2)}")
            hashes[match.group(2)] = match.group(1)
            continue
        key, sep, value = line.partition("=")
        if sep and key in UNIT_KEYS:
            if key in props:
                raise Refused(f"{key} appears twice")
            props[key] = value
    return props, hashes


def exec_argv(value):
    """The one plain command line of ExecStart, as a list of arguments."""
    found = EXEC_START.findall(value or "")
    if len(found) != 1:
        raise Refused("ExecStart must hold exactly one command")
    path, argv, ignore = found[0]
    args = argv.split(" ")
    if ignore != "no" or args[0] != path:
        raise Refused("ExecStart must be a plain command (no '-' or '@' prefix)")
    if any(not SAFE_ARG.fullmatch(arg) for arg in args):
        raise Refused("an ExecStart argument has a character the drop-in cannot copy safely")
    return args


def script_index(args):
    scripts = [i for i, arg in enumerate(args) if arg.endswith(".py")]
    if len(scripts) != 1:
        raise Refused("ExecStart must name exactly one .py script")
    return scripts[0]


def drop_ins(value):
    return sorted(part for part in (value or "").split(" ") if part)


def unit_state(text, *, need_active):
    props, hashes = parse_unit(text)
    missing = [key for key in UNIT_KEYS[:3] + (("ActiveState",) if need_active else ()) if key not in props]
    if missing:
        raise Refused("missing from the saved lines: " + ", ".join(missing))
    if not props["FragmentPath"].startswith(UNIT_DIRS):
        raise Refused("oak-den.service is not a system unit in a known directory")
    if need_active and props["ActiveState"] != "active":
        raise Refused(f"oak-den.service is {props['ActiveState']}, not active")
    args = exec_argv(props["ExecStart"])
    return {"fragment": props["FragmentPath"], "drop_ins": drop_ins(props["DropInPaths"]),
            "argv": args, "exec_start": props["ExecStart"]}, hashes


def drop_in_text(args):
    return ("# den-light rung 8 Stage 0, written by sweep.py --prepare. It runs the\n"
            "# metering build with the installed flags. To roll back: delete this file,\n"
            "# then daemon-reload and restart oak-den.service.\n"
            "[Service]\nExecStart=\nExecStart=" + " ".join(args) + "\n")


def prepare(stage0, date, token_file):
    stage0 = run_folder(stage0, token_file, may_hold_sweeps=False)
    if any((stage0 / name).exists() for name in ("prepare.json", "install.json", DROP_IN_NAME)):
        raise Refused("the folder already holds an install; use a new Stage 0 folder")
    if not re.fullmatch(r"20\d{6}", date or ""):
        raise Refused("--date must be yyyymmdd")
    text = read_lines(stage0 / "before.txt")
    before, hashes = unit_state(text, need_active=False)
    args = before["argv"]
    script = args[script_index(args)]
    if not script.startswith("/") or not script.endswith("/" + PINNED_SCRIPT):
        raise Refused("ExecStart does not run the pinned " + PINNED_SCRIPT + "; stop and change nothing")
    if hashes.get(script) != FIXTURE_SHA256:
        raise Refused("the pinned script does not hash d052fc21...; stop and change nothing")
    if DROP_IN_PATH in before["drop_ins"]:
        raise Refused("the den-light drop-in is already installed")
    meter_dir = script[:-len(PINNED_SCRIPT)] + f"victoria-camera-pilot/pilot-{date}-meter"
    meter_path = meter_dir + "/" + METER_FILE
    after_args = list(args)
    after_args[script_index(args)] = meter_path
    conf = drop_in_text(after_args)
    with open(stage0 / DROP_IN_NAME, "x", encoding="ascii", newline="\n") as out:
        out.write(conf)
    record = {"schema": SCHEMAS["prepare"], "date": date, "before": before, "pinned_path": script,
              "pinned_sha256": hashes[script], "meter_dir": meter_dir, "meter_path": meter_path,
              "drop_in_path": DROP_IN_PATH, "drop_in_sha256": meter.sha256_bytes(conf.encode()),
              "after_argv": after_args, "before_txt_sha256": meter.sha256_bytes(text.encode()),
              "code_sha256": code_sha256()}
    write_json(stage0 / "prepare.json", record)
    return record


def load_prepare(stage0):
    try:
        record = json.loads((stage0 / "prepare.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("no readable prepare.json; run --prepare first") from None
    if record.get("schema") != SCHEMAS["prepare"]:
        raise Refused("prepare.json has another schema")
    return record


def install_record(stage0, camera, token_file, method, *, health_wait_s=None):
    health_wait_s = INSTALL_HEALTH_WAIT_S if health_wait_s is None else health_wait_s
    stage0 = run_folder(stage0, token_file, may_hold_sweeps=False)
    if (stage0 / "install.json").exists():
        raise Refused("install.json already exists")
    prep = load_prepare(stage0)
    text = read_lines(stage0 / "after.txt")
    after, hashes = unit_state(text, need_active=True)
    if after["fragment"] != prep["before"]["fragment"]:
        raise Refused("the unit file moved")
    if after["argv"] != prep["after_argv"]:
        raise Refused("ExecStart is not the before line with only the script changed; roll back")
    if after["drop_ins"] != sorted(prep["before"]["drop_ins"] + [DROP_IN_PATH]):
        raise Refused("the drop-ins are not the before ones plus the den-light drop-in; roll back")
    if hashes.get(prep["pinned_path"]) != FIXTURE_SHA256:
        raise Refused("the pinned script no longer hashes d052fc21...; roll back")
    local = meter.file_sha256(HERE / METER_FILE)
    if local != METER_SERVER_SHA256 or hashes.get(prep["meter_path"]) != METER_SERVER_SHA256:
        raise Refused("the installed meter file does not hash the committed oak-capture-meter.py; roll back")
    seen = camera_ready(camera, health_wait_s=health_wait_s, drop_in_installed=True)
    record = {"schema": SCHEMAS["install"], "before": prep["before"], "after": after,
              "pinned_path": prep["pinned_path"], "pinned_sha256_before": prep["pinned_sha256"],
              "pinned_sha256_after": hashes[prep["pinned_path"]], "meter_path": prep["meter_path"],
              "meter_sha256": hashes[prep["meter_path"]], "drop_in_path": DROP_IN_PATH,
              "drop_in_sha256": prep["drop_in_sha256"], "camera": seen,
              "after_txt_sha256": meter.sha256_bytes(text.encode()),
              "method_sha256": method.file_sha256, "method_stage0_block_sha256": method.block_sha256,
              "code_sha256": code_sha256(), "written_pc_unix": time.time()}
    write_json(stage0 / "install.json", record)
    return record


def rollback_check(stage0, camera, token_file, *, health_wait_s=None, settle_s=None,
                   sleep=time.sleep, clock=time.monotonic):
    """The install's after-check, now expecting the before state: the unit lines, then the camera."""
    health_wait_s = INSTALL_HEALTH_WAIT_S if health_wait_s is None else health_wait_s
    settle_s = ROLLBACK_SETTLE_S if settle_s is None else settle_s
    stage0 = run_folder(stage0, token_file, may_hold_sweeps=True)
    if (stage0 / "rollback.json").exists():
        raise Refused("rollback.json already exists")
    prep = load_prepare(stage0)
    text = read_lines(stage0 / "rollback.txt")
    state, hashes = unit_state(text, need_active=True)
    if (state["fragment"], state["drop_ins"], state["argv"]) != (
            prep["before"]["fragment"], prep["before"]["drop_ins"], prep["before"]["argv"]):
        raise Refused("oak-den.service is not back to its before state")
    if hashes.get(prep["pinned_path"]) != FIXTURE_SHA256:
        raise Refused("the pinned script does not hash d052fc21...")
    seen = camera_rolled_back(camera, health_wait_s=health_wait_s, settle_s=settle_s, sleep=sleep, clock=clock)
    record = {"schema": SCHEMAS["rollback"], "state": state, "pinned_sha256": hashes[prep["pinned_path"]],
              "camera": seen, "rollback_txt_sha256": meter.sha256_bytes(text.encode()),
              "written_pc_unix": time.time()}
    write_json(stage0 / "rollback.json", record)
    return record


# ---- the camera -------------------------------------------------------------------

def read_token(path):
    try:
        token = Path(path).read_bytes().decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        raise Refused("cannot read the camera token file (see README)") from None
    if len(token) < 24 or not all(33 <= ord(ch) < 127 for ch in token):
        raise Refused("the token file must hold one token of at least 24 printable characters")
    return token


class Camera:
    """The meter build's /health, /frame.npz, POST /meter and POST /meter/release."""

    def __init__(self, url, token):
        parts = urlsplit(url or "")
        if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query:
            raise Refused("--camera must look like http://<host>:<port>")
        self.host, self.port = parts.hostname, parts.port or 80
        self._token = token

    def __repr__(self):
        return f"Camera({self.host}:{self.port})"

    def request(self, method, path, body=None, timeout=5.0):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        headers = {"Authorization": "Bearer " + self._token}
        if body is not None:
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                raise http.client.HTTPException("response too large")
            if response.length:
                raise http.client.IncompleteRead(data, response.length)
            return response.status, data
        except http.client.HTTPException as exc:
            raise ConnectionError(f"no whole HTTP reply: {type(exc).__name__}") from None
        finally:
            connection.close()

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        status, data = self.request("POST", path, body)
        try:
            reply = json.loads(data) if data else {}
        except ValueError:
            reply = {}
        return status, reply if type(reply) is dict else {}


def unreachable(camera, path, exc):
    return CameraUnreachable(
        f"cannot reach the camera at {camera.host}:{camera.port} ({path}: {type(exc).__name__}), a network "
        "problem: port 8090 may not be reachable from this PC; README step 4 has the ssh tunnel")


def unhealthy(path, status, body, drop_in_installed):
    """The camera answered something other than 200."""
    if status in (401, 403):
        return CameraUnhealthy(f"the camera answers, but {path} answered {status}: it refused the camera "
                               "token; check the token file")
    notes = []
    if isinstance(body.get("error"), str) and body["error"]:
        notes.append("its error: " + body["error"][:200])
    age = body.get("bundle_age_seconds")
    if type(age) in (int, float) and age == age and abs(age) != float("inf"):
        notes.append(f"last bundle {age:.0f} s old")
    text = f"the camera answers but is unhealthy: {path} answered {status}"
    text += f" ({'; '.join(notes)})" if notes else ""
    return CameraUnhealthy(text + ("; " + ROLLBACK_ADVICE if drop_in_installed else ""))


def health_once(camera):
    """(status, body) of one GET /health; CameraUnreachable when there is no whole reply."""
    try:
        status, data = camera.get("/health")
    except OSError as exc:
        raise unreachable(camera, "/health", exc) from None
    try:
        body = json.loads(data) if data else {}
    except ValueError:
        body = {}
    return status, body if type(body) is dict else {}


def wait_healthy(camera, health_wait_s, drop_in_installed, sleep, clock):
    """The body of the first /health that answers 200 within health_wait_s; else the last failure."""
    deadline = clock() + health_wait_s
    while True:
        try:
            status, body = health_once(camera)
            if status == 200:
                return body
            failure = unhealthy("/health", status, body, drop_in_installed)
        except CameraUnreachable as exc:
            failure = exc
        if clock() >= deadline:
            raise failure
        sleep(1.0)


def get_bundle(camera, drop_in_installed):
    """(metadata, report) of one /frame.npz bundle with exactly rgb, depth, left and right."""
    advice = "; " + ROLLBACK_ADVICE if drop_in_installed else ""
    try:
        status, data = camera.get("/frame.npz")
    except OSError as exc:
        raise unreachable(camera, "/frame.npz", exc) from None
    if status != 200:
        raise unhealthy("/frame.npz", status, {}, drop_in_installed)
    try:
        meta, _ = meter.read_bundle(data)
        report = meter.rgb_report(meta)
    except meter.BundleError as exc:
        raise Refused(f"unreadable bundle: {exc}{advice}") from None
    if set(meta.get("frames", {})) != {"rgb", "depth", "left", "right"}:
        raise Refused("the bundle's frames are not exactly rgb, depth, left and right" + advice)
    return meta, report


def camera_ready(camera, *, health_wait_s=0.0, drop_in_installed=False, sleep=time.sleep, clock=time.monotonic):
    """/health answers 200 and one bundle is unheld with the meter keys; else Refused.

    A failed connection is CameraUnreachable (the tunnel advice); a camera that
    answers unhealthy is CameraUnhealthy with its status, and says to roll back
    when the meter drop-in is installed."""
    wait_healthy(camera, health_wait_s, drop_in_installed, sleep, clock)
    meta, report = get_bundle(camera, drop_in_installed)
    if not report["meter_keys"]:
        raise Refused("the camera is not the meter build (no gain, white balance or meter keys)"
                      + ("; " + ROLLBACK_ADVICE if drop_in_installed else ""))
    if report["held"] is not False:
        raise Refused("the camera is held; wait 30 s for an earlier hold to expire, then try again")
    return {"session": report["session"], "held": False, "iso": report["iso"], "exposure_us": report["exposure_us"],
            "kelvin": report["kelvin"], "lens_position": report["lens_position"]}


def camera_rolled_back(camera, *, health_wait_s, settle_s, sleep=time.sleep, clock=time.monotonic):
    """After the rollback: /health healthy, the same camera session settle_s later, one bundle without meter keys."""
    after = "after the rollback; nothing more is changed: tell the agent"
    first = wait_healthy(camera, health_wait_s, False, sleep, clock)
    session = first.get("session")
    if type(session) is not str or not session:
        raise Refused("/health names no camera session " + after)
    sleep(settle_s)
    try:
        status, second = health_once(camera)
    except CameraUnreachable:  # it answered a moment ago, so this is the unit, not the network
        raise Refused(f"the camera stopped answering {settle_s:g} s after it first answered healthy: "
                      f"oak-den.service may be crash-looping while it reads active {after}") from None
    if status != 200:
        raise CameraUnhealthy(f"{unhealthy('/health', status, second, False)}, {settle_s:g} s after it first "
                              f"answered healthy: oak-den.service may be restarting in a loop {after}")
    meta, report = get_bundle(camera, False)
    if second.get("session") != session or report["session"] != session:
        raise Refused(f"the camera restarted within {settle_s:g} s (a new camera session): oak-den.service "
                      f"may be crash-looping while it reads active {after}")
    rgb = meta["frames"]["rgb"]
    present = [key for key in METER_KEYS if type(rgb) is dict and key in rgb]
    if present:
        raise Refused("bundles still carry the meter keys (" + ", ".join(present) + "): the meter build is "
                      "still running, so the rollback has not taken effect; check the drop-in is gone, then "
                      "daemon-reload and restart again")
    return {"session": session, "meter_keys": False, "settle_s": settle_s}


# ---- the sweep ----------------------------------------------------------------------

class OutOfRange(ValueError):
    """A step of the plan is outside the camera's range: the room is too dark (or too bright)."""

    def __init__(self, step, exposure_us, iso, dark):
        super().__init__(f"step {step} needs {exposure_us} us at ISO {iso}, outside the camera's range")
        self.step, self.exposure_us, self.iso, self.dark = step, exposure_us, iso, dark


def exposure_plan(t_pick_us, iso_pick, method):
    """The held settings of every step, the repeat last (METHOD-V1, The sweep, step 5)."""
    low, high, n = method["step_ratio_low"], method["step_ratio_high"], method["step_count"]
    steps = []
    for k in range(n):
        ratio = low * (high / low) ** (k / (n - 1))
        t, iso = t_pick_us * ratio, iso_pick
        if round(t) > method["max_exposure_us"]:
            iso = round(iso_pick * ratio * t_pick_us / method["max_exposure_us"])
            t = method["max_exposure_us"]
        t = round(t)
        if t < 1 or iso > method["iso_max"]:
            raise OutOfRange(k + 1, t, iso, dark=iso > method["iso_max"])
        steps.append({"exposure_us": t, "iso": iso, "white_balance_kelvin": method["white_balance_kelvin"]})
    return steps + [dict(steps[0])]


def pick_problem(t_pick_us, iso_pick, method):
    """None when auto's pick gives a plan inside the camera's range, else a plain 'too dark: ...' line.

    Checked before any hold (METHOD-V1, The sweep, steps 1 and 4): a dim den is
    refused, not voided, so it does not use up a rerun."""
    try:
        exposure_plan(t_pick_us, iso_pick, method)
        return None
    except OutOfRange as exc:
        max_ms = method["max_exposure_us"] / 1000
        if not exc.dark:
            return (f"too bright: auto exposure is at {t_pick_us:.0f} us, so the shortest step would be under "
                    "1 us; dim the den lights a little, then run the same line again")
        need = method["iso_max"] / method["step_ratio_high"]
        return (f"too dark: auto exposure is at {t_pick_us / 1000:.1f} ms and ISO {iso_pick}, so the sweep's "
                f"brightest step would need ISO {exc.iso}, above the camera's {method['iso_max']}. A sweep needs "
                f"auto exposure at or below ISO {need:.0f} at {max_ms:g} ms. Turn on one more den bulb ({DEN_BULBS}) "
                "at full in the HA app, then run the same line again")


class Sweep:
    """One sweep into one new sweep folder. run() returns the response written."""

    def __init__(self, camera, method, folder, *, clock=time.monotonic, sleep=time.sleep, say=print):
        self.camera, self.method, self.folder = camera, method, Path(folder)
        self.clock, self.sleep, self.say = clock, sleep, say
        step = method["pixel_step"]
        self.regions = meter.sample_regions(step, method["grid_cols"], method["grid_rows"])
        self.region_count = method["grid_cols"] * method["grid_rows"]
        self.session = None
        self.last_sequence = None
        self.last_get = None
        self.current = None      # the settings held now
        self.last_post = None
        self.posted = False
        self.complete = False
        self.kept = []           # per step: {hold, generation, frames: [...]}
        self.n = 0
        self.on_frame = None     # tests: called with (step index or None, frame number)
        self.jsonl = None
        self.release = None
        self.collecting = False  # Ctrl+C voids only while the frames are being collected
        self.interrupted = False
        self.ctrl_c_ignored = 0

    # Ctrl+C: the handler never raises, so no Ctrl+C can cut the release, the
    # cleanup or the writing of response.json short. While collecting it marks the
    # sweep, which voids at the next check; afterwards it is only counted.
    def on_ctrl_c(self, signum, frame):
        if self.collecting:
            self.interrupted = True
        else:
            self.ctrl_c_ignored += 1

    def trap_ctrl_c(self):
        if threading.current_thread() is not threading.main_thread():
            return None
        return signal.signal(signal.SIGINT, self.on_ctrl_c)

    def check_interrupt(self):
        if self.interrupted:
            raise Void("interrupted_or_errored_before_repeat", "Ctrl+C before the repeat")

    # camera calls
    def check_run_limit(self):
        self.check_interrupt()
        if self.clock() - self.t0 > self.method["run_limit_s"]:
            raise Void("run_over_limit", f"more than {self.method['run_limit_s']} s since the countdown")

    def post_meter(self, settings):
        body = dict(settings, ttl_s=self.method["hold_ttl_s"])
        self.posted = True
        try:
            status, reply = self.camera.post("/meter", body)
        except OSError as exc:
            raise Void("camera_unreachable", f"POST /meter: {type(exc).__name__}") from None
        if status == 503:
            raise Void("camera_503", "POST /meter answered 503")
        if status != 200 or type(reply.get("generation")) is not int:
            raise Void("interrupted_or_errored_before_repeat", f"POST /meter was refused ({status})")
        self.current, self.last_post = settings, self.clock()
        return reply["generation"]

    def renew_if_due(self):
        if self.current is not None and self.clock() - self.last_post >= self.method["renew_every_s"]:
            self.post_meter(self.current)

    def next_frame(self, step_index):
        """The next new bundle: (report, nv12)."""
        poll = self.method["poll_s"]
        while True:
            self.check_run_limit()
            self.renew_if_due()
            if self.last_get is not None:
                wait = self.last_get + poll - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self.last_get = self.clock()
            try:
                status, data = self.camera.get("/frame.npz")
            except OSError as exc:
                raise Void("camera_unreachable", f"GET /frame.npz: {type(exc).__name__}") from None
            if status == 503:
                raise Void("camera_503", "GET /frame.npz answered 503")
            if status != 200:
                raise Void("interrupted_or_errored_before_repeat", f"GET /frame.npz answered {status}")
            try:
                meta, nv12 = meter.read_bundle(data)
                report = meter.rgb_report(meta)
            except meter.BundleError as exc:
                raise Void("interrupted_or_errored_before_repeat", f"unreadable bundle: {exc}") from None
            if self.session is None:
                self.session = report["session"]
            elif report["session"] != self.session:
                raise Void("new_camera_session", "a bundle came from another camera session")
            if self.last_sequence is not None and report["sequence"] <= self.last_sequence:
                continue
            self.last_sequence = report["sequence"]
            self.n += 1
            if self.on_frame:
                self.on_frame(step_index, self.n)
            return report, nv12

    def means(self, nv12):
        return meter.region_means(meter.decode(nv12, self.method["pixel_step"]), self.regions, self.region_count)

    # phases
    def countdown(self):
        left = self.method["countdown_s"]
        self.say(f"Starting in {left:g} s: step out of the camera's view now.")
        end = self.t0 + left
        while self.clock() < end:
            self.check_interrupt()
            self.sleep(min(1.0, end - self.clock()))

    def wait_still(self):
        start = self.clock()
        limit, tolerance = self.method["still_wait_limit_s"], self.method["noise_tolerance_codes"]
        anchor, window = None, []
        while True:
            if self.clock() - start > limit:
                raise Void("still_scene_wait_over_limit", f"no still scene within {limit} s")
            report, nv12 = self.next_frame(None)
            if report["held"]:
                raise Void("interrupted_or_errored_before_repeat", "the camera was held before the sweep")
            now, means = self.clock(), self.means(nv12)
            if anchor is None or not meter.agree(anchor[1], means, tolerance):
                anchor, window = (now, means), [report]
                continue
            window.append(report)
            if now - anchor[0] >= self.method["still_span_s"]:
                if self.clock() - start > limit:
                    raise Void("still_scene_wait_over_limit", f"no still scene within {limit} s")
                return window

    def save(self, step_index, hold, report, nv12):
        name = f"{self.n:04d}.nv12"
        with open(self.folder / "frames" / name, "xb") as out:
            out.write(nv12)
        line = {"n": self.n, "step": step_index, "file": "frames/" + name,
                "sha256": meter.sha256_bytes(nv12), "report": report, "hold": hold}
        self.jsonl.write(json.dumps(line, sort_keys=True) + "\n")
        self.jsonl.flush()
        return {"n": self.n, "report": report, "nv12": nv12}

    def hold_step(self, index, hold):
        generation = self.post_meter(hold)
        posted_at = self.clock()
        keep, settled = [], False
        while len(keep) < self.method["frames_per_step"]:
            report, nv12 = self.next_frame(index)
            if not settled:
                agreeing = (report["held"] and report["generation"] == generation and meter.gain_agreement(
                    report, hold, self.method["exposure_quantum_us"])[0])
                if not agreeing and self.clock() - posted_at <= self.method["settle_limit_s"]:
                    continue
                settled = True
            keep.append(self.save(index, hold, report, nv12))
        self.kept.append({"hold": hold, "generation": generation, "frames": keep})

    def do_release(self):
        """POST /meter/release once anything was posted. Never raises."""
        if not self.posted:
            return None
        self.current = None
        try:
            status, reply = self.camera.post("/meter/release", {})
        except Exception as exc:
            return {"released": False, "error": type(exc).__name__}
        return {"released": status == 200 and reply.get("held") is False, "status": status}

    def close_jsonl(self):
        try:
            if self.jsonl is not None and not self.jsonl.closed:
                self.jsonl.close()
        except OSError:
            pass

    def delete_frames(self):
        """Delete a void sweep's frames; report what is really gone."""
        self.close_jsonl()
        shutil.rmtree(self.folder / "frames", ignore_errors=True)
        try:
            (self.folder / "frames.jsonl").unlink(missing_ok=True)
        except OSError:
            pass
        left = [name for name in ("frames", "frames.jsonl") if (self.folder / name).exists()]
        return {"frames_deleted": not left, "frames_left": left}

    def remove_empty_folder(self):
        """A refusal before any hold: the new sweep folder holds nothing yet, so it goes."""
        self.close_jsonl()
        try:
            jsonl = self.folder / "frames.jsonl"
            if jsonl.exists() and jsonl.stat().st_size == 0:
                jsonl.unlink()
            (self.folder / "frames").rmdir()   # fails, and keeps it, unless empty
            self.folder.rmdir()
        except OSError:
            pass

    def collect(self, response):
        """Countdown, still scene, auto's pick, then every hold: the data, the repeat last."""
        self.countdown()
        window = self.wait_still()
        exposures = [r["exposure_us"] for r in window]
        isos = [r["iso"] for r in window]
        if None in exposures or None in isos:
            raise Void("interrupted_or_errored_before_repeat", "an auto frame reported no exposure or ISO")
        t_pick, iso_pick = statistics.median(exposures), round(statistics.median(isos))
        response["auto_pick"] = {"exposure_us": t_pick, "iso": iso_pick, "frames": len(window)}
        problem = pick_problem(t_pick, iso_pick, self.method)
        if problem:
            raise Refused(problem)  # before any POST /meter: no hold, no void
        plan = exposure_plan(t_pick, iso_pick, self.method)
        self.say(f"Holding: {len(plan)} exposures.")
        for index, hold in enumerate(plan, 1):
            self.hold_step(index, hold)
        self.check_interrupt()

    def run(self):
        """The whole sweep; returns the response (already written to response.json).

        Raises Refused, with the new sweep folder removed, when the room is too
        dark (or bright) for the plan; nothing was held then."""
        response = {"schema": SCHEMAS["response"], "outcome": None, "void_reason": None, "detail": None,
                    "method_file": meter.METHOD_FILE, "method_sha256": self.method.file_sha256,
                    "method_stage0_block_sha256": self.method.block_sha256, "code_sha256": code_sha256(),
                    "started_pc_unix": time.time()}
        self.t0 = self.clock()
        self.jsonl = open(self.folder / "frames.jsonl", "x", encoding="utf-8", newline="\n")
        self.collecting = True
        previous = self.trap_ctrl_c()
        try:
            try:
                self.collect(response)
                self.complete = True
            except KeyboardInterrupt:
                raise Void("interrupted_or_errored_before_repeat", "Ctrl+C before the repeat") from None
            except (Void, Refused):
                raise
            except Exception as exc:
                raise Void("interrupted_or_errored_before_repeat",
                           f"error before the repeat: {type(exc).__name__}: {exc}") from None
            finally:
                self.collecting = False  # from here, on every path, Ctrl+C waits for response.json
                self.release = self.do_release()
            response["release"] = self.release
            if self.clock() - self.t0 > self.method["run_limit_s"]:
                raise Void("run_over_limit", "the release came more than the run limit after the countdown")
            first, repeat = self.kept[0]["frames"][0], self.kept[-1]["frames"][0]
            if not meter.agree(self.means(first["nv12"]), self.means(repeat["nv12"]),
                               self.method["noise_tolerance_codes"]):
                raise Void("repeat_mismatch", "the repeat disagrees with the first exposure: the scene moved")
            response["frames_kept"] = sum(len(step["frames"]) for step in self.kept)
            self.say("Released. Fitting the response (under a minute)...")
            response.update(verdict_fields(meter.check(self.kept, self.method), self.method))
        except Refused:
            self.remove_empty_folder()
            raise
        except Void as void:
            response.update(outcome="void", void_reason=void.reason, detail=void.detail)
            response.setdefault("release", self.release)
            response.update(self.delete_frames())
        except Exception as exc:  # after the data is complete: keep the frames for --recheck
            response.update(outcome="error", detail=f"{type(exc).__name__}: {exc}")
            response.setdefault("release", self.release)
        finally:
            self.close_jsonl()
            try:
                if response["outcome"] is not None:
                    response.update(camera_session=self.session, steps=[
                        {"step": i, **s["hold"], "generation": s["generation"],
                         "frames": [f["n"] for f in s["frames"]]} for i, s in enumerate(self.kept, 1)],
                        elapsed_s=self.clock() - self.t0, ctrl_c_ignored=self.ctrl_c_ignored)
                    write_json(self.folder / "response.json", response)
            finally:
                if previous is not None:
                    signal.signal(signal.SIGINT, previous)
        return response


def verdict_fields(result, method):
    """The response fields from meter.check's result: outcome, check and meter_norm."""
    fields = {"outcome": result["verdict"], "check": {k: v for k, v in result.items() if k != "curves"}}
    if result["curves"]:
        parameters, digest = meter.meter_norm_parameters(method, result["curves"])
        fields["meter_norm"] = {"version": meter.METER_NORM_VERSION, "parameters": parameters,
                                "parameters_sha256": digest}
    return fields


def start_run(stage0, camera, token_file, method):
    """Every refusal before a sweep folder exists (METHOD-V1, The sweep, step 1)."""
    if method.open:
        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))
    stage0 = run_folder(stage0, token_file, may_hold_sweeps=True)
    try:
        install = json.loads((stage0 / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("no readable install.json in the Stage 0 folder (README step 4)") from None
    if (install.get("schema") != SCHEMAS["install"] or install.get("meter_sha256") != METER_SERVER_SHA256
            or meter.file_sha256(HERE / METER_FILE) != METER_SERVER_SHA256):
        raise Refused("install.json's after hash is not oak-capture-meter.py's committed sha256")
    seen = camera_ready(camera, drop_in_installed=True)
    if seen["exposure_us"] is not None and seen["iso"] is not None:
        problem = pick_problem(seen["exposure_us"], seen["iso"], method)
        if problem:
            raise Refused(problem)
    return stage0, new_sweep_folder(stage0)


# ---- recheck: the verdict again, from a saved sweep folder ---------------------------------

def load_kept(folder, method):
    """The kept steps of a saved sweep from frames.jsonl and frames\\, each frame's sha256 checked."""
    try:
        lines = (folder / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise Refused("no readable frames.jsonl in the sweep folder") from None
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
            raise Refused("frames.jsonl names a frame that is missing or unreadable") from None
        if meter.sha256_bytes(nv12) != line.get("sha256"):
            raise Refused(f"{name} does not hash what frames.jsonl recorded")
        step = steps.setdefault(line["step"], {"hold": line["hold"], "frames": []})
        if step["hold"] != line["hold"]:
            raise Refused(f"step {line['step']} has two holds in frames.jsonl")
        step["frames"].append({"n": line["n"], "report": line["report"], "nv12": nv12})
    if sorted(steps) != list(range(1, method["step_count"] + 2)):
        raise Refused(f"frames.jsonl does not hold steps 1 to {method['step_count'] + 1}")
    return [steps[i] for i in sorted(steps)]


def recheck(folder, method):
    """Recompute a saved sweep's repeat check and verdict; writes recheck-<n>.json beside response.json."""
    if method.open:
        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))
    folder = Path(folder)
    if not folder.is_absolute() or not SWEEP_NAME.fullmatch(folder.name) or not folder.is_dir():
        raise Refused("give a sweep folder by its absolute path, for example C:\\den-light\\stage0-20261110\\sweep-1")
    folder = folder.resolve()
    if inside_checkout(folder) or in_onedrive(folder):
        raise Refused("the folder is inside a git checkout or OneDrive")
    try:
        saved = json.loads((folder / "response.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("no readable response.json in the sweep folder") from None
    if saved.get("outcome") not in ("pass", "fail", "error"):
        raise Refused(f"the sweep's outcome is {saved.get('outcome')}: a void sweep has no frames to recheck")
    steps = load_kept(folder, method)
    agrees = meter.agree(*(meter.region_means(meter.decode(steps[i]["frames"][0]["nv12"], method["pixel_step"]),
                                              meter.sample_regions(method["pixel_step"], method["grid_cols"],
                                                                   method["grid_rows"]),
                                              method["grid_cols"] * method["grid_rows"]) for i in (0, -1)),
                         method["noise_tolerance_codes"])
    record = {"schema": SCHEMAS["recheck"], "sweep": folder.name, "saved_outcome": saved.get("outcome"),
              "method_sha256": method.file_sha256, "method_stage0_block_sha256": method.block_sha256,
              "method_matches_sweep": method.block_sha256 == saved.get("method_stage0_block_sha256"),
              "code_sha256": code_sha256(), "frames": sum(len(s["frames"]) for s in steps),
              "void_reason": None, "written_pc_unix": time.time()}
    if agrees:
        record.update(verdict_fields(meter.check(steps, method), method))
    else:
        record.update(outcome="void", void_reason="repeat_mismatch")
    record["same_as_saved"] = record["outcome"] == saved.get("outcome")
    numbers = [int(m.group(1)) for m in (re.fullmatch(r"recheck-(\d+)\.json", c.name) for c in folder.iterdir()) if m]
    path = folder / f"recheck-{max(numbers, default=0) + 1}.json"
    write_json(path, record)
    return record, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", metavar="STAGE0_FOLDER")
    group.add_argument("--install-record", metavar="STAGE0_FOLDER")
    group.add_argument("--run", metavar="STAGE0_FOLDER")
    group.add_argument("--rollback-check", metavar="STAGE0_FOLDER")
    group.add_argument("--recheck", metavar="SWEEP_FOLDER")
    parser.add_argument("--date", help="yyyymmdd, for --prepare: names the meter directory")
    parser.add_argument("--camera", help="http://<host>:8090, or http://127.0.0.1:18090 through the tunnel")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            record = prepare(Path(args.prepare), args.date, args.token_file)
            print(f"Fixture check passed. Wrote {DROP_IN_NAME} for {record['meter_path']}.")
            print("Next: README step 3 (needs approval 1).")
            return 0
        if args.recheck:
            record, path = recheck(Path(args.recheck), meter.load_method())
            print(f"RECHECK {record['outcome'].upper()}: " + (", ".join(record.get("check", {}).get("reasons", []))
                                                              or record["void_reason"] or "every check passed"))
            print(f"The saved outcome was {record['saved_outcome']}. Wrote {path}")
            return 0 if record["outcome"] == "pass" else 1
        camera = Camera(args.camera, read_token(args.token_file))
        if args.rollback_check:
            rollback_check(Path(args.rollback_check), camera, args.token_file)
            print("Rollback check passed: oak-den.service runs the pinned script as before, and the camera "
                  "stays healthy without the meter keys. Wrote rollback.json.")
            return 0
        method = meter.load_method()
        if args.install_record:
            install_record(Path(args.install_record), camera, args.token_file, method)
            print("After-check passed: the meter build runs, unheld, and the pinned script is unchanged. "
                  "Wrote install.json.")
            return 0
        _, folder = start_run(Path(args.run), camera, args.token_file, method)
        print(f"Sweep folder: {folder}")
        response = Sweep(camera, method, folder).run()
    except Refused as exc:
        print(f"Refused: {exc}. This command changed nothing.", file=sys.stderr)
        return 2
    if response["outcome"] == "void":
        gone = ("The frames were deleted." if response["frames_deleted"] else
                "Could not delete " + " and ".join(response["frames_left"]) + f" in {folder}: delete them by hand.")
        print(f"VOID ({response['void_reason']}): {response['detail']}. {gone}")
    elif response["outcome"] == "error":
        print(f"ERROR after the data was complete: {response['detail']}. The frames are kept: tell the agent, "
              f"then run --recheck {folder}")
    else:
        print(f"{response['outcome'].upper()}: " + (", ".join(response["check"]["reasons"]) or "every check passed"))
    print(f"Wrote {folder / 'response.json'}")
    return 0 if response["outcome"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
