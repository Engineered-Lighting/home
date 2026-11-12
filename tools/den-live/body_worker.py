#!/usr/bin/env python3
"""den-live's body process: body.py's BodyMaker, kept alive, fed frames over a pipe. The detector's venv (numpy, OpenCV).

    <venv>\\Scripts\\python.exe -B tools\\den-live\\body_worker.py
    <venv>/bin/python -B tools/den-live/body_worker.py

Python sockets are refused here, as in detect_worker.py: frames come in on stdin, frames go out on stdout, and this
process has no network. It writes no file: the empty room it learns lives in its memory only.

In: first one setup message, a 4-byte big-endian length and JSON {setup: {left_k: [fx, fy, cx, cy], room_from_left:
[R, t], home}}, answered with {ready} (or {refused} and exit 2). Then for each frame a 4-byte length and a JSON header
{id, now, capture_ts, seq, person} (person: null when nobody is tracked, else {track_id, xy_m, joints: {name: [xyz,
status]}} in the room frame), then a 4-byte length and the camera bundle's bytes.
Out: one JSON line per frame: {id, frame, why, compute_s, stats}. frame is a den-live-body-1 frame or null, with why
saying why not ("nobody tracked", "learning the empty room", "no body"). {id, error} carries a type name only.
"""
import io
import json
import os
import struct
import sys
import time
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
NETWORK = ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
           "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo")   # den-detect's list


def guard():
    def refuse(event, _args):
        if event in NETWORK:
            raise PermissionError("den-live's body process has no network (%s)" % event)
    sys.addaudithook(refuse)


def read_exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_message(stream):
    size = read_exact(stream, 4)
    if size is None:
        return None
    return read_exact(stream, struct.unpack(">I", size)[0])


PROTOCOL = None


def own_stdout():
    """As detect_worker.py: the protocol keeps its own copy of stdout, and fd 1 points at stderr (the log)."""
    global PROTOCOL
    PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def say(value):
    PROTOCOL.write(json.dumps(value, separators=(",", ":")) + "\n")
    PROTOCOL.flush()


def depth_of(np, data):
    """The bundle's depth_mm plane (400x640 uint16, rectified-left aligned), as den-take reads it."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.getinfo("depth_mm.npy").file_size > 1 << 21:
            raise ValueError("depth_mm is too large")
        depth = np.load(io.BytesIO(archive.read("depth_mm.npy")), allow_pickle=False)
    if depth.dtype != np.dtype("<u2") or depth.shape != (400, 640):
        raise ValueError("depth_mm must be 400x640 uint16")
    return depth


def main(argv=None):
    own_stdout()
    guard()
    try:
        import cv2
        import numpy as np
        import body
        cv2.setNumThreads(1)   # the LA unit's CPUQuota is one and a half cores, shared with the detector
        setup = json.loads((read_message(sys.stdin.buffer) or b"null").decode("utf-8"))["setup"]
        maker = body.BodyMaker(np, cv2, left_k=setup["left_k"], room_from_left=setup["room_from_left"])
        home = str(setup.get("home") or "den")
    except Exception as exc:  # a type name only
        say({"refused": "the body process didn't start (%s)" % type(exc).__name__})
        return 2
    say({"ready": True})
    stream = sys.stdin.buffer
    while True:
        raw = read_message(stream)
        if raw is None:
            return 0
        data = read_message(stream)
        if data is None:
            return 0
        header = {}
        try:
            header = json.loads(raw.decode("utf-8"))
            start = time.perf_counter()
            frame, why = maker.step(depth_of(np, data), person=header.get("person"), now=float(header["now"]),
                                    capture_ts=header.get("capture_ts"), seq=header.get("seq"), home=home)
            say({"id": header.get("id"), "frame": frame, "why": why, "compute_s": round(time.perf_counter() - start, 4),
                 "stats": maker.stats})
        except Exception as exc:  # one bad frame is a reply, not the end; a type name only, never its text
            say({"id": header.get("id"), "error": type(exc).__name__})


if __name__ == "__main__":
    sys.exit(main())
