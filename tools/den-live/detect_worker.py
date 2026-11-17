#!/usr/bin/env python3
"""Step 3's detector process: den-detect's Detector, kept alive, fed bundles over a pipe (plan L3). The detector's venv.

    <venv>\\Scripts\\python.exe -B tools\\den-live\\detect_worker.py --tools-root <observe tree> --state <folder>
    <venv>/bin/python -B tools/den-live/detect_worker.py --tools-root <observe tree> --state <folder> --models <folder>

Python sockets are refused here (den-detect's guard): frames come in on stdin, rows go out on stdout, and this process
has no network. The models load once, as in den-detect's own runs: the Lab's UltralyticsWorker and RTMPoseWorker with
their own checks, the weights held to the lock's digests, the venv held to its versions (requirements-cuda.lock for a
venv with CUDA torch, LA's; requirements.lock for any other). With --device auto (the default) each model runs on
CUDA when torch sees a GPU with room for it, and on the CPU otherwise, each falling back on its own (detect.Detector);
--device cpu keeps both on the CPU. The weights are the
venv's models folder, or --models (the LA service's, where install.sh puts them). With stdin closed at once it loads,
says ready and exits 0: that's install.sh's check of the venv and the weights.

In: for each frame, a 4-byte big-endian length and a JSON header {id, rgb_seq, t_host, delay_s, frame_key?}, then a 4-byte length
and the bundle's bytes. delay_s is how long after capture the loop handed the frame over, so available_ts counts
arrival and waiting plus compute (plan L3), not compute only.
Out: one JSON line per frame: {id, row, compute_s, luma} (row: den-detections-1, as den-detect writes it; luma: the
mean of the frame's Y plane, 0 to 255, for the stream's too-dark rule) or {id, error} with a type name only (a
den-observe RowError adds reason: its own fixed words, any detection id replaced). The first
line is {ready, load_ms, warm_ms, devices: {boxes, pose}, gpu_why?}, or {refused} and exit 2. devices names where
each model runs (cpu or cuda:0); gpu_why, in words, why one isn't on the GPU, only when auto found a reason.

The live body (body.py's BodyChannel) runs here too, so a frame crosses the pipe once and numpy loads once. A header
with a kind of setup_body or body comes alone, with no bundle after it: setup_body binds calibration; body asks for
the body of the last bundle by both detection id and exact pipeline frame key. Their replies carry body_ready,
body_refused or body_id, never id, so the loop tells them apart from detection rows.

The Qwen ask's stills come from here too (stills.py): this process keeps the last few seconds of frames by their
detection id, and a header of kind stills, with no bundle after it, asks for some of them as JPEGs. The reply carries
stills_id, never id.
"""
import argparse
import json
import os
import re
import struct
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "den-detect"))
import detect  # noqa: E402  den-detect: its Detector, guard, lock and containment
sys.path.insert(0, str(HERE))
import stills  # noqa: E402  the Qwen ask's JPEGs, from the frames this process keeps


def read_exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


BODY_KINDS = ("setup_body", "body", "stills")   # headers that come without a bundle


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
PROTOCOL_LOCK = threading.Lock()


def own_stdout():
    """The protocol keeps its own copy of stdout. File descriptor 1 then points at stderr (the worker's log), so a library
    that prints, from Python or from C, can't put a line into the loop's pipe."""
    global PROTOCOL
    PROTOCOL = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr


def row_reason(exc):
    """A RowError's reason with any detection id replaced, at most 160 characters: den-observe's own fixed words."""
    return re.sub(r"detection .{1,200}?: ", "detection <id>: ", str(exc))[:160]   # live ids are rgb-<seq>:<index>


class Guards:
    """How often the row guards fired, said to the worker's log once a minute when any did: rows whose ready time was
    raised to their capture (a capture mapped a hair after its arrival by the clock offset), and boxes fit_to_frame
    dropped (past den-observe's 64, or unusable)."""

    def __init__(self, clock=time.monotonic, every_s=60.0):
        self.clock, self.every_s = clock, every_s
        self.since, self.clamped, self.fitted = clock(), 0, 0

    def count(self, clamped, fitted):
        self.clamped += bool(clamped)
        self.fitted += int(fitted or 0)
        now = self.clock()
        if now - self.since < self.every_s:
            return None
        line = None
        if self.clamped or self.fitted:
            line = "den-detect: in the last %.0f s, %d rows raised to their capture time, %d boxes dropped to fit the row" % (
                now - self.since, self.clamped, self.fitted)
        self.since, self.clamped, self.fitted = now, 0, 0
        return line


def available(capture_ts, delay_s, compute_s):
    """When a frame's row was ready: capture, plus the loop's handover delay and compute, never before capture
    (den-observe refuses a row whose available_ts is; a capture time mapped a hair ahead of the host's clock would)."""
    return max(capture_ts, capture_ts + delay_s + compute_s)


def say(value):
    with PROTOCOL_LOCK:
        PROTOCOL.write(json.dumps(value, separators=(",", ":")) + "\n")
        PROTOCOL.flush()


def optional_masks(models, state):
    """The pinned optional model loads in isolation, without holding up ready or detection."""
    try:
        import held_seg
        import held_async
        why = held_seg.weights_reason(models)
        if why:
            return None, {"held": False, "held_status": "off", "held_why": why}
        return held_async.Masks(models, Path(state) / "segmentation"), {
            "held": True, "held_device": None, "held_status": "loading", "held_deadline_s": held_async.MASK_S}
    except Exception as exc:
        return None, {"held": False, "held_status": "error",
                      "held_why": "the optional pass didn't start (%s)" % type(exc).__name__}


def held_pass(models, devices, *, load=None, state=None):
    """(the held-object segmenter or None, the ready line's held fields): held_seg.py's YOLO26s-seg beside the tracking
    detector, on the same frame, only with its pinned weights in the models folder, on the boxes model's device. Off,
    every frame and reply is exactly as before; held_why says why, in words."""
    try:
        import held_seg
        if load is None:
            segmenter, why = held_seg.load(models, (devices or {}).get("boxes") or "cpu", state=state)
        else:
            segmenter, why = load(models, (devices or {}).get("boxes") or "cpu")
    except Exception as exc:  # a type name only
        segmenter, why = None, "the held-object pass didn't load (%s)" % type(exc).__name__
    return segmenter, {"held": segmenter is not None, **({"held_why": why} if why else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tools-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--models", default=str(Path(sys.prefix) / "models"))
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    args = parser.parse_args(argv)
    models = Path(args.models).resolve()   # before contain(), which moves the working folder into --state
    state = Path(args.state).resolve()
    own_stdout()
    detect.guard()
    try:
        have = detect.installed()
        pinned = detect.lock_for(have)
        detect.contain(state)
        wrong = detect.unlocked(pinned["versions"], have)
        if wrong:
            raise detect.Refused("the detector's venv is not its lock's (%d differences)" % len(wrong))
        detector = detect.Detector(args.tools_root, models, pinned["weights"], device=args.device)
    except detect.Refused as exc:
        say({"refused": str(exc)[:200]})
        return 2
    masks, held = optional_masks(models, state)
    say({"ready": True, "load_ms": round(detector.load_ms), "warm_ms": round(detector.warm_ms), "devices": detector.devices,
         **({"gpu_why": detector.gpu_why} if detector.gpu_why and args.device == "auto" else {}), **held})
    try:
        return serve(detector, masks, state)
    finally:
        if masks is not None:
            masks.close()


def serve(detector, masks=None, state=None):
    """Detect and serve stills immediately. The optional body owner captures its own exact bundle."""
    stream = sys.stdin.buffer
    bodies = None
    if masks is not None:
        import held_async
        bodies = held_async.Bodies(masks, say, state=state)
    try:
        return serve_frames(detector, masks, stream, stills.Ring(), bodies, state=state)
    finally:
        if masks is not None:
            masks.close()
            bodies.close()


def optional_head(row, header, *, estimate=None):
    """A malformed optional fit never replaces the detector row or its reply with an error."""
    calibration = header.get("head_calibration")
    if calibration is None:
        return None
    try:
        if estimate is None:
            import head_pose
            estimate = head_pose.estimate
        result = estimate(row, calibration, header.get("frame_key"))
        if (not isinstance(result, dict) or not isinstance(result.get("candidates"), list)
                or len(result["candidates"]) > 64 or not isinstance(result.get("reasons"), dict)
                or len(json.dumps(result, allow_nan=False)) > 65536):
            raise ValueError("optional head result")
        return result
    except Exception:
        return {"candidates": [], "reasons": {"worker_error": 1}}


def serve_frames(detector, masks, stream, ring, bodies=None, *, state=None):
    np = detector.np
    kept, image_module = None, None
    guards = Guards()
    while True:
        got = read_frame(stream)
        if got is None:
            return 0
        header, data = got
        if data is None and header.get("kind") == "stills":   # the Qwen ask's stills, from the frames kept in the ring
            if image_module is None:
                try:
                    from PIL import Image as image_module
                except Exception as exc:  # a type name only
                    say({"stills_id": header.get("id"), "error": type(exc).__name__})
                    continue
            say(stills.handle(header, ring, np, image_module, detector.decode, detector.check.read_bundle))
            continue
        if data is None:   # the live body: set up once, then the body of the frame kept below
            if masks is not None:
                bodies.submit(header, kept)
                continue
            if bodies is None:
                try:
                    import body
                    bodies = body.BodyChannel(state)   # keeps the learned background in --state across restarts
                except Exception as exc:  # a type name only
                    if header.get("kind") == "setup_body":
                        say({"body_refused": type(exc).__name__, "binding": (header.get("setup") or {}).get("binding")})
                    else:
                        say({"body_id": header.get("id"), "request_id": header.get("request_id"),
                             "frame_key": header.get("frame_key"), "binding": header.get("binding"),
                             "error": type(exc).__name__})
                    continue
            say(bodies.handle(header, kept))
            continue
        kept = (header.get("id"), data, header.get("frame_key"))
        identity = {"frame_key": header["frame_key"]} if "frame_key" in header else {}
        if identity:
            ring.keep(header["frame_key"], data)
        try:
            _meta, nv12 = detector.check.read_bundle(data, with_rgb=True)
            plane = np.frombuffer(nv12, np.uint8).reshape(detect.HEIGHT * 3 // 2, detect.WIDTH)
            luma = float(plane[:detect.HEIGHT].mean())
            rgb = detector.decode(plane, detect.WIDTH, detect.HEIGHT)
            seq, t = int(header["rgb_seq"]), float(header["t_host"])
            result, _merged, compute_s = detector.run(np.ascontiguousarray(rgb[:, :, ::-1]), "rgb-%d" % seq)
            ready = available(t, float(header["delay_s"]), compute_s)
            line = guards.count(ready > t + float(header["delay_s"]) + compute_s, getattr(detector, "fitted", 0))
            if line:
                print(line, file=sys.stderr, flush=True)
            row = detector.row_of(result, capture_ts=t, available_ts=ready, rgb_seq=seq,
                                  width=detect.WIDTH, height=detect.HEIGHT, boxes_version="yolo26s-8.4.0",
                                  keypoints_version=detector.pose.manifest["model_version"])
            head_start = time.perf_counter()
            head = optional_head(row, header)
            head_fields = {}
            if head is not None:
                head_seconds = time.perf_counter() - head_start
                row["available_ts"] += head_seconds
                head_fields = {"head_candidates": head, "head_compute_s": round(head_seconds, 6)}
            health = masks.health() if masks is not None else {}
            say({"id": header["id"], "row": row, "compute_s": round(compute_s, 4), "luma": round(luma, 1),
                 **identity, **health, **head_fields})
            if masks is not None:
                try:
                    masks.submit(header["id"], header.get("frame_key"), np.ascontiguousarray(rgb[:, :, ::-1]), row)
                except Exception:
                    # Optional submission cannot turn an already sent tracking row into a second reply.
                    pass
        except Exception as exc:  # one bad frame is a reply, not the end; a type name only, never its text
            reply = {"id": header.get("id"), "error": type(exc).__name__, **identity}
            if type(exc).__name__ == "RowError":   # den-observe's schema reasons: fixed words, no frame data
                reply["reason"] = row_reason(exc)
                print("den-detect: a frame's row was refused: %s" % reply["reason"], file=sys.stderr, flush=True)
            say(reply)


if __name__ == "__main__":
    sys.exit(main())
