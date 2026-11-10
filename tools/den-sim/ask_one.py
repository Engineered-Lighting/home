#!/usr/bin/env python3
"""The den simulation's LA side: ask Qwen about each request body copied here. Runs on LA, never on a click.

ask_la.py copies this file into a fresh private /dev/shm folder with the two it imports, and removes the folder
after: the sofa look's ask.py (its helpers, lifted unchanged at refs.json's pin) and qwen_scene.py (the blob the
click builds bodies with). Standard library only. The model test's look_one.py, proved on LA, with a cap that
follows the bodies:

  python3 -B ask_one.py --check   preflight only: the served name is qwen3-vl-30b with its root named (and
                                  recorded), the GPU under 77 C with 8 GiB free. No chat request.
  python3 -B ask_one.py           the preflight again, then each body in ./bodies: re-check its hash, POST to
                                  127.0.0.1:8000 (20 s timeout, 4 s apart), one re-send on no answer, stop after two
                                  misses, never more requests than the bodies plus 2. An answered reply is kept as it
                                  came, valid or not, and never re-sent: replies/<body_sha256>.json, six fields.

--fake-vllm PORT is for tests on the workstation only: requests and the GPU reading go to a fake vLLM on
127.0.0.1:PORT, whose replies carry root fake-vllm, which a click reads as SCRIPTED.

It never knows which take a body is. Nothing here prints a body, a reply or exception text (type names only), and
this process may open no socket but loopback.
"""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

LOOPBACK = "127.0.0.1"


def loopback_only(event, args):
    if event in ("socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg"):
        if not (len(args) > 1 and type(args[1]) is tuple and args[1][:1] == (LOOPBACK,)):
            raise PermissionError("ask_one.py reaches loopback only (%s)" % event)
    elif event == "socket.getaddrinfo" and args[0] not in (LOOPBACK, LOOPBACK.encode()):
        raise PermissionError("ask_one.py looks up no name")
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"):
        raise PermissionError("ask_one.py looks up no name")


sys.addaudithook(loopback_only)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # the copies beside this file, and nothing else
import ask  # noqa: E402
import qwen_scene as scene  # noqa: E402

MARGIN = 2
REPLY_FIELDS = ("asked_unix", "host", "root", "http_status", "raw", "elapsed_s")
EXIT = {"ok": 0, "not_private": 2, "vllm_down": 10, "not_served": 11, "gpu_hot": 12, "gpu_memory": 13, "gpu_unreadable": 14, "no_root": 15,
        "two_misses": 20, "server_refused": 21, "request_cap": 22, "body_refused": 23}


def say(text):
    print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def preflight(served, gpu):
    """(reason or None, detail, root, temperature C, free MiB). Asks /v1/models and the GPU; no image."""
    try:
        name, root = served()
    except Exception as exc:  # type name only
        return "vllm_down", type(exc).__name__, None, None, None
    if name != scene.SERVED_MODEL:
        return "not_served", None, root, None, None
    if not isinstance(root, str) or not root:   # a reply is filed only with the weights it came from: ask nothing
        return "no_root", None, root, None, None
    try:
        temp, free = gpu()
    except Exception as exc:
        return "gpu_unreadable", type(exc).__name__, root, None, None
    reason = ask.gate(temp, free)
    return reason, ("%d C, %d MiB free" % (temp, free) if reason else None), root, temp, free


def write_reply(folder, name, reply):
    part = folder / (name + ".json.part")
    part.write_text(json.dumps(reply, sort_keys=True), encoding="utf-8")
    os.replace(part, folder / (name + ".json"))


def ask_all(bodies, root, folder, *, request, sleep, clock, wall, host):
    """Send each (name, body) once, re-send once on no answer. Returns (stop reason or None, counts)."""
    counts = {"answered": 0, "invalid": 0, "requests": 0, "refused": 0}
    cap, last, misses = len(bodies) + MARGIN, -ask.SPACING_S, 0
    for number, (name, body) in enumerate(bodies, 1):
        if ask.sha256(body) != name:
            counts["refused"] += 1
            say("body %d of %d: refused, its hash changed" % (number, len(bodies)))
            continue
        for attempt in (1, 2):
            if counts["requests"] >= cap:
                say("stopped: %d requests is the cap" % cap)
                return "request_cap", counts
            sleep(max(0.0, last + ask.SPACING_S - clock()))
            last, asked = clock(), wall()
            status, raw, error = request("POST", "/v1/chat/completions", body)
            elapsed = round(clock() - last, 3)
            counts["requests"] += 1
            try:
                result = ask.outcome(status, raw, error)
            except ask.Refused as exc:
                say("stopped: the server refused the request (%s)" % exc)
                return "server_refused", counts
            if result["outcome"] == "answered":
                write_reply(folder, name, {"asked_unix": round(asked, 3), "host": host, "root": root, "http_status": status,
                                           "raw": raw.decode("utf-8", "replace"), "elapsed_s": elapsed})
                counts["answered"] += 1
                counts["invalid"] += result["facts"] is None
                misses = 0
                say("body %d of %d: answered in %.1f s%s" % (number, len(bodies), elapsed, "" if result["facts"] else " (invalid, kept as it came)"))
                break
            misses += 1
            say("body %d of %d, attempt %d: no answer (%s)" % (number, len(bodies), attempt, result["error"]))
            if misses >= 2:
                say("stopped: two misses in a row")
                return "two_misses", counts
    return ("body_refused" if counts["refused"] else None), counts


def fake_gpu():
    """--fake-vllm: the GPU reading the fake vLLM serves, since no GPU stands behind it."""
    status, raw, error = ask.request("GET", "/fake/gpu")
    if error or status != 200:
        raise ValueError("fake gpu")
    value = json.loads(raw)
    return int(value["temperature_c"]), int(value["free_mib"])


def private_folder(path):
    # Windows has no uid; only tests run this there, and their folder is a scratch one.
    return ask.private_dir(path) if hasattr(os, "getuid") else path.is_dir()


def main(argv=None, *, here=HERE, request=None, served=None, gpu=None, private=private_folder, sleep=time.sleep, clock=time.monotonic, wall=time.time):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="preflight only; sends no chat request")
    parser.add_argument("--fake-vllm", type=int, metavar="PORT", help="tests only: a fake vLLM on 127.0.0.1:PORT")
    args = parser.parse_args(argv)
    if args.fake_vllm:
        ask.PORT = args.fake_vllm
    request, served = request or ask.request, served or ask.served_model
    gpu = gpu or (fake_gpu if args.fake_vllm else ask.gpu_reading)
    os.umask(0o077)
    try:
        import resource  # Unix only: no core dump can hold an image
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except ImportError:
        pass
    reason, detail, root, temp, free = preflight(served, gpu)
    if reason:
        say("preflight failed: %s%s" % (reason, " (%s)" % detail if detail else ""))
        return EXIT[reason]
    say("preflight ok %s" % json.dumps({"served": scene.SERVED_MODEL, "root": root, "gpu_c": temp, "free_mib": free}))
    if args.check:
        return 0
    if not private(here):
        say("refused: this folder must be private to this user")
        return EXIT["not_private"]
    bodies = []
    try:
        for path in sorted((here / "bodies").glob("*.body")):
            bodies.append((path.stem, path.read_bytes()))
    finally:
        for path in (here / "bodies").glob("*.body"):   # in memory now; however this run ends, none stay on disk
            path.unlink()
    folder = here / "replies"
    folder.mkdir(mode=0o700, exist_ok=True)
    say("asking about %d bodies, at most %d requests" % (len(bodies), len(bodies) + MARGIN))
    stop, counts = ask_all(bodies, root, folder, request=request, sleep=sleep, clock=clock, wall=wall, host=socket.gethostname())
    say("asked: %d of %d answered (%d invalid), %d requests, %d refused" % (counts["answered"], len(bodies), counts["invalid"],
                                                                         counts["requests"], counts["refused"]))
    return EXIT[stop or "ok"]


if __name__ == "__main__":
    sys.exit(main())
