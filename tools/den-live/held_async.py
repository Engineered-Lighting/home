"""Bounded optional segmentation and serial body dispatch, independent of detection.

One subprocess loads the pinned model, with one inference in flight and one latest
pending image. Deadlines include waiting and I/O. A timeout disables and kills this
optional child until the detector restarts. At most two short-lived exact-frame mask
results stay in memory. Only the body thread may wait for them, up to MASK_S after
frame submission. Setup and body maker calls share that thread and a generation
fence. Detection, pose and still requests never join it.
"""
import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

MASK_S, LOAD_S = 0.75, 20.0
BODY_RESERVE_S = 0.10
MAX_REPLY, MAX_HEADER, IMAGE_BYTES = 256 * 1024, 128 * 1024, 640 * 360 * 3
PARENT_PID_ENV = "DEN_MASK_PARENT_PID"


def _timing(value):
    return type(value) in (int, float) and 0 <= value <= 2000 and math.isfinite(value)


def _release_cuda_cache():
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()


def load_segmenter(models, state, device, detect, held_seg, *, release_cuda=_release_cuda_cache):
    """Auto uses the tracking detector's existing CUDA resource gate, with a fresh CPU model on load failure."""
    fallback = None
    if device == "auto":
        usable, _why = detect.gpu_usable()
        if usable:
            try:
                segmenter, _why = held_seg.load(models, "cuda:0", state=state)
                if segmenter is not None:
                    return segmenter, "cuda:0", None, None
            except Exception:
                pass
            try:
                release_cuda()
            except Exception:
                pass
            fallback = "gpu_load_failed"
        else:
            fallback = "gpu_unavailable"
    segmenter, why = held_seg.load(models, "cpu", state=state)
    return segmenter, "cpu" if segmenter is not None else None, why, fallback


def arm_parent_lifetime():
    """Linux production fence. Windows retains supervisor deadlines and normal child cleanup."""
    if sys.platform != "linux":
        return
    import ctypes
    import signal

    expected = int(os.environ[PARENT_PID_ENV])
    before = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "optional parent fence")
    if before != expected or os.getppid() != expected:
        os.kill(os.getpid(), signal.SIGKILL)


def identity(worker_id, frame_key):
    if frame_key is None:
        return None
    return json.dumps([worker_id, frame_key], separators=(",", ":"), allow_nan=False)


class Masks:
    def __init__(self, models, state, *, command=None, timeout=MASK_S, load_timeout=LOAD_S):
        self.timeout, self.load_timeout = timeout, load_timeout
        self.requested_device = "auto" if os.environ.get("DEN_MASK_DEVICE", "cpu").strip().lower() == "auto" else "cpu"
        self.command = command or [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                                   "--models", str(Path(models).resolve()), "--state", str(Path(state).resolve()),
                                   "--device", self.requested_device]
        self.condition = threading.Condition()
        self.pending = self.inflight = self.process = None
        self.results = OrderedDict()
        self.stopped = False
        self.status = "loading"
        self.failures, self.last_error = 0, None
        self.refused = None
        self.device = None
        self.fallback = None
        self.thread = threading.Thread(target=self._run, name="den-optional-masks", daemon=True)
        self.thread.start()

    def submit(self, worker_id, frame_key, image, row):
        key = identity(worker_id, frame_key)
        if key is None:
            return
        with self.condition:
            if self.stopped or self.status in ("off", "error", "timeout"):
                return
            self.pending = (key, worker_id, frame_key, image, row, time.monotonic() + self.timeout)
            self.condition.notify_all()

    def _metadata(self, key, frame_key):
        now = time.monotonic()
        while self.results and next(iter(self.results.values()))[0] <= now:
            self.results.popitem(last=False)
        found = self.results.get(key)
        if found:
            return {"frame_key": frame_key, "segmentation_status": "ok", **found[1]}
        status = self.status if self.status != "ready" else "pending"
        return {"frame_key": frame_key, "segmentation_status": status}

    def health(self):
        with self.condition:
            reason = self.refused or self.fallback
            return {"held": self.status in ("loading", "ready"), "held_status": self.status,
                    "held_device": self.device, "held_deadline_s": self.timeout, "held_failures": self.failures,
                    **({"held_fallback": self.fallback} if self.fallback else {}),
                    **({"held_error": self.last_error} if self.last_error else {}),
                    **({"held_why": reason} if reason else {})}

    def wait(self, worker_id, frame_key, max_wait_s=MASK_S):
        """Wait only on the body's thread, and only for this frame's original deadline."""
        key = identity(worker_id, frame_key)
        until = time.monotonic() + max(0.0, min(self.timeout, max_wait_s))
        with self.condition:
            while True:
                result = self._metadata(key, frame_key)
                if result["segmentation_status"] == "ok" or self.stopped:
                    return result
                candidates = (self.inflight, self.pending)
                job = next((job for job in candidates if job is not None and job[0] == key), None)
                if job is None or self.status in ("off", "error", "timeout"):
                    return result
                remaining = min(job[-1], until) - time.monotonic()
                if remaining <= 0:
                    return {"frame_key": frame_key, "segmentation_status": "timeout"}
                self.condition.wait(min(remaining, 0.05))

    @staticmethod
    def _read(stream, replies):
        try:
            while True:
                line = stream.readline(MAX_REPLY + 1)
                if not line or len(line) > MAX_REPLY:
                    raise ValueError("optional worker protocol")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("optional worker protocol")
                replies.put_nowait(value)
        except Exception:
            try:
                replies.put_nowait({"error": "ProtocolError"})
            except queue.Full:
                pass

    def _receive(self, replies, deadline):
        while time.monotonic() < deadline:
            with self.condition:
                if self.stopped:
                    raise RuntimeError("stopped")
            try:
                return replies.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                pass
        raise TimeoutError("optional deadline")

    @staticmethod
    def _write(process, packet, done):
        try:
            process.stdin.write(packet)
            process.stdin.flush()
            done.put_nowait({"written": True})
        except Exception:
            done.put_nowait({"error": "WriteError"})

    def _run(self):
        process = reader = writer = None
        try:
            env = dict(os.environ, OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
                       OPENBLAS_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
            if self.requested_device == "cpu":
                env["CUDA_VISIBLE_DEVICES"] = ""
            env[PARENT_PID_ENV] = str(os.getpid())
            process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=None, env=env, close_fds=True,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self.condition:
                self.process = process
                if self.stopped:
                    raise RuntimeError("stopped")
            replies = queue.Queue(maxsize=4)
            reader = threading.Thread(target=self._read, args=(process.stdout, replies), daemon=True)
            reader.start()
            ready = self._receive(replies, time.monotonic() + self.load_timeout)
            if ready.get("ready") is not True:
                with self.condition:
                    why = ready.get("refused")
                    error = ready.get("error")
                    if isinstance(why, str):
                        self.refused = why[:200]
                    if isinstance(error, str) and len(error) <= 80 and error.isidentifier():
                        self.last_error = error
                raise RuntimeError("optional load")
            with self.condition:
                self.device = ready.get("device") if ready.get("device") in ("cpu", "cuda:0") else None
                self.fallback = ready.get("fallback") if ready.get("fallback") in ("gpu_unavailable", "gpu_load_failed") else None
                self.status = "ready"
                self.condition.notify_all()
            while True:
                with self.condition:
                    while self.pending is None and not self.stopped:
                        self.condition.wait()
                    if self.stopped:
                        return
                    job, self.pending = self.pending, None
                    self.inflight = job
                key, worker_id, frame_key, image, row, deadline = job
                if time.monotonic() >= deadline:
                    with self.condition:
                        self.inflight = None
                        self.condition.notify_all()
                    continue
                segment_queue_ms = (time.monotonic() - (deadline - self.timeout)) * 1000
                header = json.dumps({"id": worker_id, "frame_key": frame_key, "row": row},
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")
                pixels = image.tobytes()
                if len(header) > MAX_HEADER or len(pixels) != IMAGE_BYTES:
                    raise ValueError("optional input")
                packet = struct.pack(">I", len(header)) + header + struct.pack(">I", len(pixels)) + pixels
                done = queue.Queue(maxsize=1)
                writer = threading.Thread(target=self._write, args=(process, packet, done), daemon=True)
                writer.start()
                if self._receive(done, deadline).get("written") is not True:
                    raise RuntimeError("optional write")
                result = self._receive(replies, deadline)
                if result.get("id") != worker_id or result.get("frame_key") != frame_key:
                    raise ValueError("optional result")
                error = result.get("error")
                if error is not None:
                    if not isinstance(error, str) or len(error) > 80 or not error.isidentifier():
                        raise ValueError("optional error")
                    metadata = {"segmentation_status": "error", "segmentation_error": error}
                else:
                    if not isinstance(result.get("person_masks"), list) or not isinstance(result.get("held_masks"), list):
                        raise ValueError("optional result")
                    metadata = {"person_masks": result["person_masks"], "held_masks": result["held_masks"]}
                    timings = {}
                    if _timing(segment_queue_ms):
                        timings["segment_queue"] = round(segment_queue_ms, 3)
                    if _timing(result.get("inference_ms")):
                        timings["segment_inference"] = round(float(result["inference_ms"]), 3)
                    if timings:
                        metadata["timings_ms"] = timings
                with self.condition:
                    if error is not None:
                        self.failures += 1
                        self.last_error = error
                    if time.monotonic() < deadline:
                        self.results[key] = (deadline, metadata)
                        while len(self.results) > 2:
                            self.results.popitem(last=False)
                    self.inflight = None
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.status = "timeout" if isinstance(exc, TimeoutError) else "error"
                self.pending = self.inflight = None
                self.condition.notify_all()
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout):
                    stream.close()
            for thread in (reader, writer):
                if thread is not None:
                    thread.join(timeout=0.2)
            with self.condition:
                self.process = None
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stopped = True
            self.pending = None
            process = self.process
            self.condition.notify_all()
        if process is not None and process.poll() is None:
            process.kill()
        self.thread.join(timeout=1)


class Bodies:
    """One serial body owner. Generation changes fence setup against an old running request."""
    def __init__(self, masks, say, *, factory=None, state=None):
        if factory is None:
            from body import BodyChannel
            factory = lambda: BodyChannel(state)
        self.masks, self.say, self.factory = masks, say, factory
        self.condition = threading.Condition()
        self.setup = self.pending = None
        self.generation = 0
        self.busy = self.stopped = False
        self.thread = threading.Thread(target=self._run, name="den-optional-body", daemon=True)
        self.thread.start()

    @staticmethod
    def _reply(header, why):
        return {"body_id": header.get("id"), "request_id": header.get("request_id"),
                "frame_key": header.get("frame_key"), "binding": header.get("binding"),
                "frame": None, "why": why}

    def submit(self, header, kept):
        with self.condition:
            if header.get("kind") == "setup_body":
                self.generation += 1
                self.setup = (self.generation, header)
            elif self.busy or self.pending is not None:
                self.say(self._reply(header, "body_busy"))
                return
            else:
                self.pending = (self.generation, header, kept, time.monotonic())
            self.condition.notify_all()

    def _run(self):
        channel = self.factory()
        while True:
            with self.condition:
                while self.setup is None and self.pending is None and not self.stopped:
                    self.condition.wait()
                if self.stopped:
                    return
                setup = self.setup
                if setup:
                    self.setup = None
                else:
                    generation, header, kept, queued = self.pending
                    self.pending = None
                    self.busy = True
            if setup:
                generation, header = setup
                # A fresh maker discards learning and object history on a new setup.
                try:
                    channel = self.factory()
                    result = channel.handle(header, None)
                except Exception as exc:
                    result = {"body_refused": type(exc).__name__, "binding": (header.get("setup") or {}).get("binding")}
            else:
                try:
                    timings = {"body_queue": round((time.monotonic() - queued) * 1000, 3)}
                    if kept is not None and self.masks is not None:
                        remaining = header.get("remaining_s", MASK_S + BODY_RESERVE_S)
                        if type(remaining) not in (int, float) or not math.isfinite(remaining):
                            remaining = 0.0
                        budget = max(0.0, remaining - (time.monotonic() - queued) - BODY_RESERVE_S)
                        wait_started = time.monotonic()
                        metadata = self.masks.wait(kept[0], kept[2], max_wait_s=budget)
                        timings["mask_wait"] = round((time.monotonic() - wait_started) * 1000, 3)
                        if metadata.get("frame_key") == kept[2] and metadata.get("segmentation_status") == "ok":
                            measured = metadata.get("timings_ms")
                            if isinstance(measured, dict):
                                timings.update({key: measured[key] for key in ("segment_queue", "segment_inference")
                                                if _timing(measured.get(key))})
                        kept = (*kept[:3], metadata)
                    with self.condition:
                        current = generation == self.generation and not self.stopped
                    result = channel.handle(header, kept) if current else self._reply(header, "scope_changed")
                    if type(result.get("compute_s")) in (int, float) and _timing(result["compute_s"] * 1000):
                        timings["body_compute"] = round(result["compute_s"] * 1000, 3)
                    result["timings_ms"] = {key: value for key, value in timings.items() if _timing(value)}
                except Exception as exc:
                    result = {**self._reply(header, "optional_body_error"), "error": type(exc).__name__}
            with self.condition:
                if self.stopped:
                    return
                if generation == self.generation:
                    self.say(result)
                elif not setup:
                    self.say(self._reply(header, "scope_changed"))
                if not setup:
                    self.busy = False
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stopped = True
            self.pending = self.setup = None
            self.condition.notify_all()
        self.thread.join(timeout=1)


def _read_exact(stream, count):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = stream.read(count - len(chunks))
        if not chunk:
            raise EOFError
        chunks.extend(chunk)
    return bytes(chunks)


def worker_main(argv=None):
    try:
        arm_parent_lifetime()
    except Exception:
        return 2
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--models", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--device", choices=("cpu", "auto"), default="cpu")
    args = parser.parse_args(argv)
    models = Path(args.models).resolve()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr

    def say(value):
        protocol.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
        protocol.flush()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "den-detect"))
    import detect
    detect.guard()
    try:
        have = detect.installed()
        pinned = detect.lock_for(have)
        detect.contain(args.state)
        if detect.unlocked(pinned["versions"], have):
            raise detect.Refused("optional interpreter lock")
        import cv2
        import numpy as np
        import torch
        import held_seg
        torch.set_num_threads(1)
        cv2.setNumThreads(1)
        segmenter, device, why, fallback = load_segmenter(models, args.state, args.device, detect, held_seg)
        if segmenter is None:
            say({"refused": why})
            return 2
        failures = held_seg.Failures()
        say({"ready": True, "device": device, **({"fallback": fallback} if fallback else {})})
    except Exception as exc:
        say({"error": type(exc).__name__})
        return 2
    while True:
        try:
            size = struct.unpack(">I", _read_exact(sys.stdin.buffer, 4))[0]
            if size > MAX_HEADER:
                raise ValueError("optional header")
            header = json.loads(_read_exact(sys.stdin.buffer, size))
            size = struct.unpack(">I", _read_exact(sys.stdin.buffer, 4))[0]
            if size != IMAGE_BYTES:
                raise ValueError("optional image")
            image = np.frombuffer(_read_exact(sys.stdin.buffer, size), np.uint8).reshape(360, 640, 3)
            error = None
            inference_started = time.monotonic()
            try:
                masks = segmenter(image, header["row"], cv2)
            except Exception as exc:
                error = type(exc).__name__
                masks = {"error": error}
            line = failures.count(error)
            if line:
                print(line, file=sys.stderr, flush=True)
            say({"id": header["id"], "frame_key": header["frame_key"],
                 "inference_ms": round((time.monotonic() - inference_started) * 1000, 3), **masks})
        except EOFError:
            return 0
        except Exception as exc:
            say({"error": type(exc).__name__})
            return 2


if __name__ == "__main__":
    sys.exit(worker_main())
