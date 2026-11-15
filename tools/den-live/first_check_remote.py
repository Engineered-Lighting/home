"""Step 4's first check, the half that runs on LA. Standard library only; read only; it keeps nothing.

first_check.py sends this file's text to `ssh <host> python3 -` on stdin, after one line that sets PAYLOAD (the
gateway's unit name and checkout, the den's site, the camera's URL and token, the pull's length). So the token goes
over ssh's stdin, never on a command line, and nothing is copied to LA. It runs read-only commands and one 60 s pull
of frames as den-live pulls them (/frame-slim.npz on one kept connection, else /frame.npz), and prints one JSON line of
what it found, raw: first_check.py reads it and prints words. No frame is kept: each body is hashed, counted and dropped.
"""
import hashlib
import http.client
import json
import os
import socket
import string
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

FRAME_PATH = "/frame.npz"   # den-live's camera paths: /frame-slim.npz, /frame.npz and /calibration (source.ALLOWED_PATHS)
SLIM_PATH = "/frame-slim.npz"   # den-camera-slim-1, on one kept connection, as source.KeptLink asks for it
SLIM_SCHEMA = "den-camera-slim-1"
MAX_BODY = 4 * 1024 * 1024
CLOSED = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
NAME_OK = set(string.ascii_letters + string.digits + "._-")   # a folder name that's safe to print


def command(argv, timeout=20):
    """A read-only command's exit code and output, or why it didn't run."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"code": None, "why": "missing"}
    except subprocess.TimeoutExpired:
        return {"code": None, "why": "timeout"}
    except OSError as exc:
        return {"code": None, "why": type(exc).__name__}
    return {"code": done.returncode, "out": done.stdout[-50000:]}


def gateway_process(unit, proc=Path("/proc")):
    """(pid, its environment, its working folder) of the running gateway, or (pid or 0, {}, None)."""
    shown = command(["systemctl", "show", "-p", "MainPID", "--value", unit])
    try:
        pid = int((shown.get("out") or "0").strip() or 0)
    except ValueError:
        pid = 0
    if pid <= 0:
        return 0, {}, None
    try:
        raw = (proc / str(pid) / "environ").read_bytes()
        cwd = Path(os.readlink(proc / str(pid) / "cwd"))
    except OSError:
        return pid, {}, None
    return pid, dict(item.split("=", 1) for item in raw.decode("utf-8", "replace").split("\0") if "=" in item), cwd


def packs_dir(env, cwd, checkout):
    """The content-pack folder as the gateway's server.mjs finds it, from its environment and working folder."""
    root = cwd or checkout
    if env.get("HOME_CONTENT_PACKS_DIR"):
        return Path(os.path.normpath(root / env["HOME_CONTENT_PACKS_DIR"]))
    assets = root / env["HOME_WEB_APARTMENT_ASSETS_DIR"] if env.get("HOME_WEB_APARTMENT_ASSETS_DIR") else root / "app" / "data" / "apartment"
    return Path(os.path.normpath(assets.parent / "home-content"))


def packs(folder, site):
    """The den site's pack revisions by folder name, and its ACTIVE one: names only, never a file's contents."""
    site_dir = Path(folder) / site
    if not site_dir.is_dir():
        return {"found": Path(folder).is_dir(), "site": False, "revisions": [], "active": None}
    names = sorted(p.name for p in site_dir.iterdir() if p.is_dir() and set(p.name) <= NAME_OK)
    try:
        active = (site_dir / "ACTIVE").read_text(encoding="utf-8").strip()
    except OSError:
        active = None
    return {"found": True, "site": True, "revisions": names[:20], "count": len(names),
            "active": active if active and set(active) <= NAME_OK else None}


def pull(url, token, seconds, poll_s=0.2, recheck_s=0.05, clock=time.monotonic, sleep=time.sleep):
    """As den-live's source pulls, for seconds. A camera with the slim path is asked for /frame-slim.npz on one kept
    connection with If-None-Match: each new frame's fetch time and size, and the 304s (the frame was still the one it
    had), asking again recheck_s after a 304 and poll_s after a new frame. A camera release without it (404) is pulled
    as before: /frame.npz every poll_s, a fresh connection each time, every answer's fetch time. Both give the statuses
    and the errors by type, and link says which way it pulled."""
    parts = urlsplit(url)
    fetch_ms, sizes, statuses, errors, seen = [], [], {}, {}, set()
    link = {"connection": None, "etag": None, "name": None, "connections": 0, "not_modified": 0}
    start = clock()
    next_poll = start
    while clock() - start < seconds:
        began = clock()
        if link["name"] != "full-fresh":
            status = slim_pull(parts, token, link, fetch_ms, sizes, statuses, errors, seen, began, clock)
            next_poll = began + (recheck_s if status == 304 else poll_s)
            delay = next_poll - clock()
            if delay > 0:
                sleep(delay)
            continue
        status = body = None
        try:
            connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=5)
            try:
                connection.request("GET", FRAME_PATH, headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                body = response.read(MAX_BODY + 1)
                status = response.status
            finally:
                connection.close()
        except (OSError, http.client.HTTPException) as exc:
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
        if status is not None:
            fetch_ms.append(round((clock() - began) * 1000, 1))
            statuses[str(status)] = statuses.get(str(status), 0) + 1
            if status == 200 and body and len(body) <= MAX_BODY:
                digest = hashlib.sha1(body).digest()
                if digest not in seen:
                    seen.add(digest)
                    sizes.append(len(body))
        body = None
        next_poll += poll_s
        delay = next_poll - clock()
        if delay > 0:
            sleep(delay)
        else:
            next_poll = clock()
    if link["connection"] is not None:
        link["connection"].close()
    return {"seconds": round(clock() - start, 2), "fetch_ms": fetch_ms, "new_frames": len(seen), "bytes": sizes,
            "statuses": statuses, "errors": errors, "link": link["name"] or "none",
            "connections": link["connections"], "not_modified": link["not_modified"]}


def slim_pull(parts, token, link, fetch_ms, sizes, statuses, errors, seen, began, clock):
    """One ask of /frame-slim.npz on the kept connection: its status, or None. A 404 switches link to the full pull,
    as den-live's source does; a 200 that doesn't declare den-camera-slim-1 counts as an error and switches too."""
    headers = {"Authorization": "Bearer " + token}
    if link["etag"]:
        headers["If-None-Match"] = link["etag"]
    for attempt in (0, 1):
        reused = link["connection"] is not None
        if not reused:
            link["connection"] = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=5)
            link["connections"] += 1
        connection = link["connection"]
        try:
            connection.request("GET", SLIM_PATH, headers=headers)
            response = connection.getresponse()
            body = response.read(MAX_BODY + 1)
        except CLOSED as exc:
            connection.close()
            link["connection"] = None
            if reused and attempt == 0:
                continue   # the camera closed the idle connection first: once more on a new one
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            return None
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            link["connection"] = None
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            return None
        break
    if response.will_close:
        connection.close()
        link["connection"] = None
    status = response.status
    if status == 404:
        link["name"] = "full-fresh"
        return status
    statuses[str(status)] = statuses.get(str(status), 0) + 1
    if status == 304:
        link["not_modified"] += 1
        return status
    if status == 200:
        if response.getheader("X-Den-Bundle-Schema") != SLIM_SCHEMA:
            errors["schema"] = errors.get("schema", 0) + 1
            link["name"] = "full-fresh"
            return status
        link["name"], link["etag"] = "slim-kept", response.getheader("ETag")
        if body and len(body) <= MAX_BODY:
            digest = hashlib.sha1(body).digest()
            if digest not in seen:
                seen.add(digest)
                sizes.append(len(body))
                fetch_ms.append(round((clock() - began) * 1000, 1))
    return status


def libraries():
    listed = command(["ldconfig", "-p"]).get("out") or ""
    return {"libGL": "libGL.so.1" in listed, "libglib": "libglib-2.0.so.0" in listed, "listed": bool(listed)}


def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def collect(payload):
    unit = payload["gateway"] + ".service"
    pid, env, cwd = gateway_process(unit)
    checkout = cwd or Path(os.path.expanduser(payload["checkout"]))
    git = ["git", "-C", str(checkout), "--no-optional-locks"]
    facts = {"schema": "den-live-first-check-1",
             "gateway": {"unit": payload["gateway"], "active": command(["systemctl", "is-active", unit]), "pid": pid,
                         "checkout_from": "gateway" if cwd else "default"},
             "git": {"head": command(git + ["rev-parse", "HEAD"]), "branch": command(git + ["rev-parse", "--abbrev-ref", "HEAD"]),
                     "date": command(git + ["log", "-1", "--format=%cs"]), "status": command(git + ["status", "--porcelain"])},
             "serve": {"json": command(["tailscale", "serve", "status", "--json"]), "text": command(["tailscale", "serve", "status"])},
             "packs": packs(packs_dir(env, cwd, checkout), payload["site"]),
             "cpu": {"cores": os.cpu_count(), "loadavg": Path("/proc/loadavg").read_text(encoding="ascii") if Path("/proc/loadavg").is_file() else None},
             "lab_worker": command(["systemctl", "--user", "is-active", payload["lab_worker"]]),
             "install": {"python312": command(["python3.12", "-c", "import sys, ensurepip, venv; print(sys.version.split()[0])"]),
                         "libraries": libraries(), "port_8111": port_in_use(8111),
                         "den_live": command(["systemctl", "is-active", "den-live.service"])}}
    facts["pull"] = pull(payload["camera_url"], payload["token"], payload["seconds"])
    return facts


if "PAYLOAD" in globals():
    try:
        print(json.dumps(collect(globals()["PAYLOAD"]), separators=(",", ":")))
    except Exception as exc:  # a type name only: never what it read
        print(json.dumps({"schema": "den-live-first-check-1", "failed": type(exc).__name__}))
    sys.stdout.flush()
