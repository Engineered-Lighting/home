#!/usr/bin/env python3
"""Step 3's detector process: den-detect's Detector, kept alive, fed bundles over a pipe (plan L3). The detector's venv.

    <venv>\\Scripts\\python.exe -B tools\\den-live\\detect_worker.py --tools-root <observe tree> --state <folder>
    <venv>/bin/python -B tools/den-live/detect_worker.py --tools-root <observe tree> --state <folder> --models <folder>

Python sockets are refused here (den-detect's guard): frames come in on stdin, rows go out on stdout, and this process
has no network. The models load once, as in den-detect's own runs: the Lab's UltralyticsWorker and RTMPoseWorker with
their own checks, the weights held to requirements.lock's digests, the venv held to its versions. The weights are the
venv's models folder, or --models (the LA service's, where install.sh puts them). With stdin closed at once it loads,
says ready and exits 0: that's install.sh's check of the venv and the weights.

In: for each frame, a 4-byte big-endian length and a JSON header {id, rgb_seq, t_host, delay_s}, then a 4-byte length
and the bundle's bytes. delay_s is how long after capture the loop handed the frame over, so available_ts counts
arrival and waiting plus compute (plan L3), not compute only.
Out: one JSON line per frame: {id, row, compute_s, luma} (row: den-detections-1, as den-detect writes it; luma: the
mean of the frame's Y plane, 0 to 255, for the stream's too-dark rule) or {id, error} with a type name only. The first
line is {ready, load_ms, warm_ms}, or {refused} and exit 2.

The live body (body.py's BodyChannel) runs here too, so a frame crosses the pipe once and numpy loads once. A header
with a kind of setup_body or body comes alone, with no bundle after it: setup_body sets the body up once; body asks for
the body of the frame this process detected last (it keeps that bundle), with the person the loop tracked on it. Their
replies carry body_ready, body_refused or body_id, never id, so the loop tells them apart from detection rows.
"""
import argparse
import json
import os
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "den-detect"))
import detect  # noqa: E402  den-detect: its Detector, guard, lock and containment


def read_exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


BODY_KINDS = ("setup_body", "body")   # headers that come without a bundle


def read_frame(stream):
    """(header, bundle bytes or None for a body header), or None when the loop closed the pipe."""
    out = []
    for _ in range(2):
        size = read_exact(stream, 4)
        if size is None:
            return None
        body = read_exact(stream, struct.unpack(">I", size)[0])
        if body is None:
            return None
        out.append(body)
        if len(out) == 1:
            header = json.loads(body.decode("utf-8"))
            if header.get("kind") in BODY_KINDS:
                return header, None
    return header, out[1]


PROTOCOL = None   # the pipe the loop reads; nothing else may write to it


def own_stdout():
    """The protocol keeps its own copy of stdout. File descriptor 1 then points at stderr (the worker's log), so a library
    that prints, from Python or from C, can't put a line into the loop's pipe."""
    global PROTOCOL
    PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def say(value):
    PROTOCOL.write(json.dumps(value, separators=(",", ":")) + "\n")
    PROTOCOL.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tools-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--models", default=str(Path(sys.prefix) / "models"))
    args = parser.parse_args(argv)
    models = Path(args.models).resolve()   # before contain(), which moves the working folder into --state
    own_stdout()
    detect.guard()
    try:
        pinned = detect.lock()
        detect.contain(args.state)
        wrong = detect.unlocked(pinned["versions"], detect.installed())
        if wrong:
            raise detect.Refused("the detector's venv is not requirements.lock's (%d differences)" % len(wrong))
        detector = detect.Detector(args.tools_root, models, pinned["weights"])
    except detect.Refused as exc:
        say({"refused": str(exc)[:200]})
        return 2
    np = detector.np
    say({"ready": True, "load_ms": round(detector.load_ms), "warm_ms": round(detector.warm_ms)})
    stream = sys.stdin.buffer
    kept, bodies = None, None
    while True:
        got = read_frame(stream)
        if got is None:
            return 0
        header, data = got
        if data is None:   # the live body: set up once, then the body of the frame kept below
            if bodies is None:
                try:
                    import body
                    bodies = body.BodyChannel()
                except Exception as exc:  # a type name only
                    say({"body_refused": type(exc).__name__})
                    continue
            say(bodies.handle(header, kept))
            continue
        kept = (header.get("id"), data)
        try:
            _meta, nv12 = detector.check.read_bundle(data, with_rgb=True)
            plane = np.frombuffer(nv12, np.uint8).reshape(detect.HEIGHT * 3 // 2, detect.WIDTH)
            luma = float(plane[:detect.HEIGHT].mean())
            rgb = detector.decode(plane, detect.WIDTH, detect.HEIGHT)
            seq, t = int(header["rgb_seq"]), float(header["t_host"])
            result, _merged, compute_s = detector.run(np.ascontiguousarray(rgb[:, :, ::-1]), "rgb-%d" % seq)
            row = detector.row_of(result, capture_ts=t, available_ts=t + float(header["delay_s"]) + compute_s, rgb_seq=seq,
                                  width=detect.WIDTH, height=detect.HEIGHT, boxes_version="yolo26s-8.4.0",
                                  keypoints_version=detector.pose.manifest["model_version"])
            say({"id": header["id"], "row": row, "compute_s": round(compute_s, 4), "luma": round(luma, 1)})
        except Exception as exc:  # one bad frame is a reply, not the end; a type name only, never its text
            say({"id": header.get("id"), "error": type(exc).__name__})


if __name__ == "__main__":
    sys.exit(main())
