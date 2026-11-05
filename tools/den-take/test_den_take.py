"""den-take tests: standard-library unittest on synthetic data only.

    python -B -m unittest discover -s tools/den-take -v

No camera, no ssh, no speech. The fake camera listens on 127.0.0.1 only and
every HTTP connection is checked to be loopback.
"""
import contextlib
import dataclasses
import hashlib
import hmac
import http.client
import io
import itertools
import json
import math
import random
import re
import shutil
import socket
import struct
import tempfile
import threading
import time
import types
import unittest
import uuid
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import check
import take

HERE = Path(__file__).resolve().parent
M = check.load_method()
TOKEN = "synthetic-test-token-" + "q7" * 12  # never a real token
GIB = 1024 ** 3
REAL_FREE_BYTES = take.free_bytes
_REAL_CONNECTION = http.client.HTTPConnection


class LoopbackOnly(_REAL_CONNECTION):
    def __init__(self, host, *args, **kwargs):
        if host != "127.0.0.1":
            raise AssertionError(f"tests may only connect to 127.0.0.1, not {host}")
        super().__init__(host, *args, **kwargs)


def setUpModule():
    global _guard
    _guard = mock.patch.object(take.http.client, "HTTPConnection", LoopbackOnly)
    _guard.start()


def tearDownModule():
    _guard.stop()
    if _CLEAN:
        shutil.rmtree(_CLEAN[0].parent, ignore_errors=True)


# ---- synthetic bundles, built like np.savez_compressed with hand-written .npy headers ----

def npy(descr, shape, body):
    header = "{'descr': %r, 'fortran_order': False, 'shape': %s, }" % (descr, tuple(shape))
    header += " " * ((64 - (10 + len(header) + 1) % 64) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + body


def metadata(session, seq, t_host, t_device, exposure=0.008):
    def plane(width, height):
        return {"sequence": seq, "device_timestamp_seconds": t_device,
                "host_monotonic_timestamp_seconds": t_host, "width": width, "height": height,
                "type": "Type.NV12", "exposure_duration_seconds": exposure, "transformation_valid": False}
    return {"schema": 1, "session": session, "captured_unix_ns": 1_800_000_000_000_000_000,
            "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure",
            "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
            "frames": {"rgb": plane(640, 360), "depth": plane(640, 400), "left": plane(640, 400),
                       "right": plane(640, 400)},
            "synchronization": {"method": "device_timestamp_group", "hardware_synchronized": False,
                                "threshold_ms": 20, "measured_skew_ms": 0.0, "within_threshold": True}}


def nv12(screen=None, marker=0):
    """640x360 NV12: a flat room, an optional phone screen, and a unique chroma marker."""
    background, row = bytes([60]) * 640, None
    if screen is None:
        y = background * 360
    else:
        row = bytes([60]) * 240 + bytes([screen]) * 80 + bytes([60]) * 320
        y = background * 120 + row * 80 + background * 160
    return y + struct.pack("<Q", marker) + b"\x80" * (640 * 180 - 8)


def bundle(meta, rgb=None, members=None):
    arrays = {"depth_mm.npy": npy("<u2", (400, 640), bytes(512_000)),
              "left.npy": npy("|u1", (400, 640), bytes(256_000)),
              "right.npy": npy("|u1", (400, 640), bytes(256_000)),
              "rgb_nv12.npy": npy("|u1", (540, 640), rgb if rgb is not None else nv12()),
              "metadata_json.npy": npy("|u1", (0,), b"")}
    raw = json.dumps(meta, separators=(",", ":")).encode()
    arrays["metadata_json.npy"] = npy("|u1", (len(raw),), raw)
    arrays.update(members or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in arrays.items():
            archive.writestr(name, data)
    return output.getvalue()


def screen_value(tau, exposure=0.008, lit=220, dark=20, samples=8):
    frac = sum(check.pattern_lit(tau - exposure / 2 + exposure * (i + 0.5) / samples, M)
               for i in range(samples)) / samples
    return round(dark + (lit - dark) * frac)


def frame_times(start, end, seed=1, locked=False):
    rng, t, out = random.Random(seed), start, []
    while t < end:
        out.append(t)
        t += 0.2 if locked or rng.random() < 0.6 else 4 / 15  # bundles from 15 fps groups
    return out


def slate_frames(times, c, shown=True):
    return [(t, check.y_plane(nv12(screen_value(t - c) if shown else None, i), 4))
            for i, t in enumerate(times)]


def slate_bundles(folder, times, c, first=0):
    """Saved bundles for take.peek: [(path, t_host)]."""
    window = []
    for i, t in enumerate(times, first):
        path = Path(folder) / f"{i}.npz"
        path.write_bytes(bundle(metadata("s", i, t, t - 9.0), nv12(screen_value(t - c), i)))
        window.append((path, t))
    return window


def judge(attempts, m=M):
    """judge_slates on [(slate, slate_frames(...))], one attempt each, received at t_host."""
    record, frames, numbers = {"slates": []}, [], {}
    for which, fit_frames in attempts:
        numbers[which] = numbers.get(which, 0) + 1
        record["slates"].append({"slate": which, "attempt": numbers[which],
                                 "pc_start_s": fit_frames[0][0], "pc_end_s": fit_frames[-1][0]})
        frames += [{"recv": t, "t_host": t, "y": y, "stale": False} for t, y in fit_frames]
    return check.judge_slates(record, frames, m)[0]


# ---- a synthetic take folder, written with take.py's own writer ----

MAKE_TAKE_BASE_UNIX = 1_793_016_000.0  # a fixed recording time, 2026-10-26 12:00 UTC
_TAKE_ORDER = itertools.count()  # each make_take() take starts an hour after the one before


def make_take(root, *, c=3.2137, activity=30.0, step_both=None, step_host=None, freeze=None,
              backwards_at=None, session_change_at=None, drop=None, repeat_at=None, host_bump=None,
              device_drift_ppm=20, seq_repeat_at=None, rejected_start=False, complete=True,
              method_sha=None, commissioning=True, script=None, started_unix=None):
    """Fault times are (anchor, seconds after it); anchors: begin, hold_end, slate_end.

    host_bump=(anchor, seconds, height) ramps t_host up by height at 0.015 s per second,
    holds it 10 s and ramps it back down. seq_repeat_at gives one frame its
    predecessor's rgb_seq. device_drift_ppm sets t_device's straight-line drift.
    script is take.json's script name: commission or free by default, as commissioning
    says, or a staged script from tools/den-take/scripts, whose stage cues inside the
    activity part are logged as take.py logs them. started_unix defaults to a fixed
    base plus an hour per take made."""
    script = script or ("commission" if commissioning else "free")
    staged = take.load_script(script, take.Settings(slate_s=M["slate_length_s"]))
    if started_unix is None:
        started_unix = MAKE_TAKE_BASE_UNIX + 3600.0 * next(_TAKE_ORDER)
    folder = Path(root) / f"take-{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    slate, cues, slates, shown, anchors = M["slate_length_s"], [], [], [], {}

    def cue(name, at, **extra):
        cues.append(dict(cue=name, text=name, pc_mono_s=at, pc_unix=1.8e9 + at, **extra))

    t = 1.0
    cue("hold_phone_start", t)
    t += 5
    for attempt, readable in enumerate([False, True] if rejected_start else [True], 1):
        cue("slate_start", t, attempt=attempt)
        if readable:
            shown.append((t - 2, t + slate + 0.5))
        slates.append({"slate": "start", "attempt": attempt, "pc_start_s": t, "pc_end_s": t + slate,
                       "peek": {"readable": readable}})
        t += slate
        if not readable:
            cue("slate_start_again", t + 0.1)
            t += 3.1
    cue("phone_away", t + 0.1)
    t += 10.1
    cue("begin", t)
    anchors["begin"] = t
    for block in (staged or {}).get("blocks", []):
        if block["at_s"] < activity:
            cues.append(dict(cue="stage", text=block["say"], pc_mono_s=t + block["at_s"],
                             pc_unix=1.8e9 + t + block["at_s"], case=block["case"], at_s=block["at_s"]))
    t += activity
    cue("hold_phone_end", t)
    anchors["hold_end"] = t
    t += 5
    cue("slate_end", t, attempt=1)
    anchors["slate_end"] = t
    shown.append((t - 2, t + slate + 0.5))
    slates.append({"slate": "end", "attempt": 1, "pc_start_s": t, "pc_end_s": t + slate,
                   "peek": {"readable": True}})
    t += slate
    cue("done", t + 0.1)

    def at(fault):
        return anchors[fault[0]] + fault[1]

    def bump(p):  # 0, then a 0.015 s/s ramp to the height, 10 s held, and back down
        if not host_bump:
            return 0.0
        x, height = p - at(host_bump), host_bump[2]
        return max(0.0, min(height, 0.015 * x, 0.015 * (2 * height / 0.015 + 10 - x)))

    sessions = ["sess-" + uuid.uuid4().hex, "sess-" + uuid.uuid4().hex]
    writer = take.TakeWriter(folder)
    host_offset, device_offset, previous_rgb, frozen = 5000.0, 1200.0, None, None
    backwards_done = repeated = seq_repeated = False
    previous_seq = None
    for i, p in enumerate(frame_times(0.1, t + 0.6)):
        if drop and at(drop) <= p < at(drop) + drop[2]:
            continue
        true_host = p + host_offset
        value = screen_value(true_host - c) if any(a <= p <= b for a, b in shown) else None
        rgb = nv12(value, i)
        if freeze and at(freeze) <= p <= at(freeze) + freeze[2]:
            frozen = frozen or previous_rgb
            rgb = frozen
        if repeat_at and p >= at(repeat_at) and not repeated:
            rgb, repeated = previous_rgb, True
        t_host, t_device = true_host + bump(p), true_host - device_offset + p * device_drift_ppm * 1e-6
        if step_both and p >= at(step_both):
            t_host, t_device = t_host + step_both[2], t_device + step_both[2]
        if step_host and p >= at(step_host):
            t_host += step_host[2]
        if backwards_at and p >= at(backwards_at) and not backwards_done:
            t_host, t_device, backwards_done = t_host - 0.5, t_device - 0.5, True
        session = sessions[1] if session_change_at and p >= at(session_change_at) else sessions[0]
        seq = 1000 + i
        if seq_repeat_at and p >= at(seq_repeat_at) and not seq_repeated:
            seq, seq_repeated = previous_seq, True
        data = bundle(metadata(session, seq, t_host, t_device), rgb)
        writer.add(data, check.read_bundle(data)[0], p + 0.06, 1.8e9 + p + 0.06)
        previous_rgb, previous_seq = rgb, seq
    writer.close()
    after = sessions[1] if session_change_at else sessions[0]
    take.write_json(folder / "take.json", {
        "schema": take.SCHEMA, "take": folder.name, "script": script,
        "script_file": staged and {"file": staged["file"], "sha256": staged["sha256"]},
        "commissioning": commissioning, "started_unix": started_unix,
        "method": {"file": check.METHOD_FILE,
                   "sha256": method_sha or check.file_sha256(check.HERE / check.METHOD_FILE)},
        "cues": cues, "slates": slates,
        "health_before": {"status": 200, "body": {"healthy": True, "session": sessions[0]}},
        "health_after": {"status": 200, "body": {"healthy": True, "session": after}},
        "complete": complete, "stop_reason": None if complete else "interrupted",
        "counts": {"kept": len(writer.index)},
        "sha256": {"frames.csv": check.file_sha256(folder / "frames.csv")}})
    return folder


_CLEAN = []  # the one clean take, built on first use


def clean_take(root):
    """A fresh copy of a clean make_take() take: built once per run, copied for each test."""
    if not _CLEAN:
        _CLEAN.append(make_take(tempfile.mkdtemp(prefix="den-take-clean-")))
    folder = Path(root) / f"take-{uuid.uuid4().hex[:8]}"
    shutil.copytree(_CLEAN[0], folder)
    return folder


class Console(io.StringIO):
    def isatty(self):
        return True


def run_check(folder, typed="none", m=None):
    return check.run_check(folder, ask=lambda: typed, show=lambda p: None, out=io.StringIO(),
                           console=Console(), m=m)


# ---- a fake camera with the installed owner's /health and /frame.npz shapes ----

class FakeCamera:
    def __init__(self, token=TOKEN, period=0.25):
        self.token, self.period, self.mode, self.truncate = token, period, "ok", 0
        self.health_failures = 0  # /health answers 500 with no body this many times
        self.session, self.start, self.cache = str(uuid.uuid4()), time.monotonic(), {}
        self.requests, self.lock = [], threading.Lock()
        camera = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                supplied = self.headers.get("Authorization", "")
                if camera.token and not hmac.compare_digest(supplied, "Bearer " + camera.token):
                    camera.log(self.path, 401)
                    self.send_error(401)
                    return
                status, content_type, body = camera.response(self.path)
                camera.log(self.path, status)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if camera.truncate and self.path == "/frame.npz":
                    camera.truncate -= 1
                    body = body[:len(body) // 2]  # the connection then closes part way through
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def log(self, path, status):
        with self.lock:
            self.requests.append((time.monotonic(), path, status))

    def response(self, path):
        healthy = self.mode == "ok"
        if path == "/health" and self.health_failures:
            self.health_failures -= 1
            return 500, "text/plain", b"Internal Server Error\n"
        if path == "/health":
            body = {"healthy": healthy, "session": self.session, "timing_generation": 1,
                    "bundle_age_seconds": 0.1, "encoder_age_seconds": 0.03,
                    "error": None if healthy else "Capture disconnected; reconnecting"}
            return (200 if healthy else 503), "application/json", json.dumps(body).encode()
        if path == "/frame.npz" and healthy:
            index = int((time.monotonic() - self.start) / self.period)
            key = (self.session, index)
            if key not in self.cache:
                t_host = 7000.0 + index * self.period
                self.cache[key] = bundle(metadata(self.session, 500 + index, t_host, t_host - 3000.0),
                                         nv12(None, index))
            return 200, "application/octet-stream", self.cache[key]
        return (503 if path == "/frame.npz" else 404), "text/plain", b"Unavailable\n"

    def frame_polls(self, start=None, end=None):
        with self.lock:
            return [r for r in self.requests if r[1] == "/frame.npz"
                    and (start is None or start <= r[0] <= end)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class SlateCamera(FakeCamera):
    """A fake camera whose frames show the phone slate while `showing` is set.

    Camera time runs five times faster than real time: a new bundle every 0.05 s,
    0.25 s of t_host apart, so a 4 s slate holds 20 s of pattern at 4 frames per second."""

    def __init__(self, c, **kwargs):
        super().__init__(period=0.05, **kwargs)
        self.c, self.showing = c, False

    def response(self, path):
        if path != "/frame.npz" or self.mode != "ok":
            return super().response(path)
        index, showing = int((time.monotonic() - self.start) / self.period), self.showing
        key = (self.session, index, showing)
        if key not in self.cache:
            t_host = 7000.0123456 + index * 0.25  # not round, as real times are not
            screen = screen_value(t_host - self.c) if showing else None
            self.cache[key] = bundle(metadata(self.session, 500 + index, t_host, t_host - 3000.0),
                                     nv12(screen, index))
        return 200, "application/octet-stream", self.cache[key]


FAST = take.Settings(slate_s=1.0, hold_lead_s=0.2, say_gap_s=0.1, away_s=0.2, commission_activity_s=1.0,
                     health_retry_s=0.1)


def readable_peek(window, m):
    return {"readable": True, "reason": None, "flashes": 12, "frames": len(window)}


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="den-take-test-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.token_file = self.tmp / "private" / "camera-token.txt"
        self.token_file.parent.mkdir()
        self.token_file.write_text(TOKEN + "\n", encoding="ascii", newline="\n")
        free = mock.patch.object(take, "free_bytes", return_value=40 * GIB)
        free.start()
        self.addCleanup(free.stop)

    def camera(self, **kwargs):
        camera = FakeCamera(**kwargs)
        self.addCleanup(camera.close)
        return camera

    def args(self, camera, script="commission", name="t1", token_file=None):
        return types.SimpleNamespace(script=script, out=str(self.tmp / "takes" / name), camera=camera.url,
                                     token_file=str(token_file or self.token_file))

    def run_take(self, args, **kwargs):
        kwargs.setdefault("say", lambda text: None)
        kwargs.setdefault("peek", readable_peek)
        kwargs.setdefault("settings", FAST)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = take.run(args, **kwargs)
        record = json.loads((Path(args.out) / "take.json").read_text(encoding="utf-8"))
        return code, record, out.getvalue() + err.getvalue()


# ---- tests ----

class NpzReaderTests(unittest.TestCase):
    def test_reads_metadata_and_y_plane(self):
        data = bundle(metadata("s1", 42, 12.5, 2.25), nv12(200, 7))
        meta, rgb = check.read_bundle(data, with_rgb=True)
        self.assertEqual((meta["session"], meta["rgb_seq"], meta["t_host"], meta["t_device"], meta["exposure"]),
                         ("s1", 42, 12.5, 2.25, 0.008))
        y = check.y_plane(rgb, 4)
        self.assertEqual(len(y), 160 * 90)
        self.assertEqual((y[0], y[35 * 160 + 70]), (60, 200))  # room, then phone screen

    def test_refuses_other_schema(self):
        meta = metadata("s1", 1, 1.0, 1.0)
        meta["schema"] = 2
        with self.assertRaisesRegex(check.BundleError, "schema"):
            check.read_bundle(bundle(meta))

    def test_refuses_missing_timestamp_fields(self):
        for field in ("host_monotonic_timestamp_seconds", "device_timestamp_seconds", "sequence"):
            meta = metadata("s1", 1, 1.0, 1.0)
            del meta["frames"]["rgb"][field]
            with self.assertRaises(check.BundleError, msg=field):
                check.read_bundle(bundle(meta))

    def test_refuses_object_and_other_dtypes(self):
        for descr in ("|O", "<f8"):
            members = {"metadata_json.npy": npy(descr, (8,), bytes(8 * (8 if descr == "<f8" else 1)))}
            with self.assertRaisesRegex(check.BundleError, "refused|size", msg=descr):
                check.read_bundle(bundle(metadata("s1", 1, 1.0, 1.0), members=members))
        members = {"rgb_nv12.npy": npy("|O", (540, 640), bytes(540 * 640))}
        with self.assertRaisesRegex(check.BundleError, "refused"):
            check.read_bundle(bundle(metadata("s1", 1, 1.0, 1.0), members=members), with_rgb=True)


class PollerTests(Workspace):
    def test_401_without_the_token(self):
        camera = self.camera()
        self.assertEqual(take.Camera(camera.url, "").get("/frame.npz")[0], 401)
        wrong = self.tmp / "wrong.txt"
        wrong.write_text("w" * 30, encoding="ascii", newline="\n")
        with self.assertRaisesRegex(take.Refused, "refused the token"):
            take.run(self.args(camera, token_file=wrong), say=lambda t: None, settings=FAST)
        self.assertFalse((self.tmp / "takes" / "t1").exists())

    def test_unreachable_camera_is_named(self):
        with socket.socket() as probe:  # a loopback port that nothing listens on
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        args = self.args(types.SimpleNamespace(url=f"http://127.0.0.1:{port}"))
        with self.assertRaisesRegex(take.Refused, f"cannot reach the camera at 127.0.0.1:{port}"):
            take.run(args, say=lambda t: None, settings=FAST)
        self.assertFalse((self.tmp / "takes" / "t1").exists())

    def test_unreadable_health_is_not_called_unreachable(self):
        camera = self.camera()
        answered = f"camera at 127.0.0.1:{camera.server.server_address[1]} answered but /health was unreadable"
        html = (200, "text/html", b"<!doctype html><title>Sign in</title>\n")
        cut_short = mock.patch.object(take.Camera, "get", side_effect=http.client.IncompleteRead(b"{", 40))
        for name, reply, patch, expected in (
                ("html", html, contextlib.nullcontext(), rf"{answered} \(JSONDecodeError\)"),
                ("cut", html, cut_short, rf"{answered} \(IncompleteRead\)"),
                ("503", (503, "text/plain", b"Service Unavailable\n"), contextlib.nullcontext(),
                 r"not healthy \(status 503, JSONDecodeError\)")):
            camera.response = lambda path, reply=reply: reply
            with self.subTest(name), patch:
                with self.assertRaises(take.Refused) as refused:
                    take.run(self.args(camera, name=name), say=lambda t: None, settings=FAST)
                message = str(refused.exception)
                self.assertRegex(message, expected)
                self.assertNotIn("cannot reach", message)
                self.assertFalse((self.tmp / "takes" / name).exists())

    def test_drops_and_counts_duplicate_polls(self):
        camera = self.camera(period=0.5)  # each bundle is polled about 2.5 times
        folder = self.tmp / "dup"
        folder.mkdir()
        writer = take.TakeWriter(folder)
        poller = take.Poller(take.Camera(camera.url, TOKEN), writer, camera.session, FAST)
        poller.start()
        time.sleep(2.0)
        poller.halt.set()
        poller.join(5)
        writer.close()
        seqs = [check.read_bundle(p.read_bytes())[0]["rgb_seq"] for _, p, _ in writer.index]
        self.assertGreater(poller.duplicates, 0)
        self.assertEqual(len(seqs), len(set(seqs)))
        self.assertEqual(poller.polls, len(seqs) + poller.duplicates + poller.errors)
        self.assertIsNone(poller.stop_reason)

    def test_cut_off_response_is_an_error_not_a_stop(self):
        camera = self.camera()
        folder = self.tmp / "cut"
        folder.mkdir()
        writer = take.TakeWriter(folder)
        poller = take.Poller(take.Camera(camera.url, TOKEN), writer, camera.session, FAST)
        poller.start()
        time.sleep(0.5)
        camera.truncate = 2
        time.sleep(1.0)
        poller.halt.set()
        poller.join(5)
        writer.close()
        self.assertEqual((poller.errors, poller.stop_reason), (2, None))
        self.assertGreater(len(writer.index), 3)

    def test_stops_incomplete_on_503(self):
        camera = self.camera()
        threading.Timer(1.0, lambda: setattr(camera, "mode", "down")).start()
        code, record, _ = self.run_take(self.args(camera))
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "camera_503"))
        self.assertEqual(record["cues"][-1]["cue"], "stopped")

    def test_stops_incomplete_on_session_change(self):
        camera = self.camera()
        threading.Timer(1.0, lambda: setattr(camera, "session", str(uuid.uuid4()))).start()
        code, record, _ = self.run_take(self.args(camera))
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "session_change"))

    def test_polling_continues_while_say_and_peek_block(self):
        camera, blocks = self.camera(), []

        def blocking(result=None):
            def call(*args):
                if not any(b[0] == call for b in blocks):
                    start = time.monotonic()
                    time.sleep(3)
                    blocks.append((call, start, time.monotonic()))
                return result(*args) if result else None
            return call
        code, record, _ = self.run_take(self.args(camera), say=blocking(), peek=blocking(readable_peek))
        self.assertEqual((code, record["complete"]), (0, True))
        self.assertEqual(len(blocks), 2)
        for _, start, end in blocks:
            self.assertGreaterEqual(len(camera.frame_polls(start, end)), 12)

    def test_token_never_printed_or_written(self):
        camera = self.camera()
        spoken = []
        code, record, output = self.run_take(self.args(camera), say=spoken.append)
        self.assertEqual(code, 0)
        threading.Timer(0.8, lambda: setattr(camera, "mode", "down")).start()
        self.run_take(self.args(camera, name="t2"))
        wrong = self.tmp / "wrong.txt"
        wrong.write_text("w" * 30, encoding="ascii", newline="\n")
        with self.assertRaises(take.Refused) as refused:
            take.run(self.args(camera, name="t3", token_file=wrong), settings=FAST)
        texts = [output, " ".join(spoken), str(refused.exception), repr(take.Camera(camera.url, TOKEN))]
        for path in (self.tmp / "takes").rglob("*"):
            if path.is_file():
                texts.append(path.read_bytes().decode("latin-1"))
        self.assertTrue(texts[3:])
        for text in texts:
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(TOKEN[:16], text)


class TakeScriptTests(Workspace):
    def test_commission_take_records_cues_and_counts(self):
        camera = self.camera()
        code, record, _ = self.run_take(self.args(camera))
        self.assertEqual((code, record["complete"], record["commissioning"]), (0, True, True))
        self.assertEqual([c["cue"] for c in record["cues"]],
                         ["hold_phone_start", "slate_start", "phone_away", "begin", "hold_phone_end",
                          "slate_end", "done"])
        self.assertEqual(set(record["sha256"]), {"CHECK-V1.md", "take.py", "check.py", "frames.csv"})
        self.assertEqual(record["method"]["sha256"], check.file_sha256(HERE / "CHECK-V1.md"))
        self.assertEqual(record["health_before"]["body"]["session"], camera.session)
        self.assertEqual(record["health_after"]["body"]["session"], camera.session)
        rows = (Path(self.args(camera).out) / "frames.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[0].split(","), list(take.CSV_FIELDS))
        self.assertEqual(len(rows) - 1, record["counts"]["kept"])
        self.assertNotIn("\r", (Path(self.args(camera).out) / "frames.csv").read_text(encoding="utf-8"))

    def test_final_health_is_tried_three_times(self):
        for failures, statuses, kept in ((2, [500, 500, 200], 200), (5, [500, 500, 500], 500)):
            camera = self.camera()

            def say(text):
                if text.startswith("Slate OK. Done"):
                    camera.health_failures = failures
            code, record, _ = self.run_take(self.args(camera, name=f"h{failures}"), say=say)
            after = [r[2] for r in camera.requests if r[1] == "/health"][1:]
            self.assertEqual((code, after, record["health_after"]["status"]), (0, statuses, kept))
            session = (record["health_after"]["body"] or {}).get("session")
            self.assertEqual(session, camera.session if kept == 200 else None)  # else B2 fails it

    def test_ctrl_c_keeps_an_incomplete_take(self):
        camera = self.camera()

        def say(text):
            if text == "Begin.":
                raise KeyboardInterrupt
        try:
            code, record, _ = self.run_take(self.args(camera), say=say)
        except KeyboardInterrupt:
            self.fail("Ctrl+C escaped take.run")
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "interrupted"))
        self.assertEqual(record["cues"][-1]["cue"], "begin")
        self.assertEqual(record["health_after"]["body"]["session"], camera.session)

    def test_second_ctrl_c_during_final_health_still_saves_the_take(self):
        camera, real_health, calls = self.camera(), take.Camera.health, []

        def say(text):
            if text == "Begin.":
                raise KeyboardInterrupt

        def health(self):
            calls.append(self)
            if len(calls) > 1:  # /health before the take answers; the one after it is cut short
                raise KeyboardInterrupt
            return real_health(self)
        try:
            with mock.patch.object(take.Camera, "health", health):
                code, record, output = self.run_take(self.args(camera), say=say)
        except KeyboardInterrupt:
            self.fail("the second Ctrl+C escaped take.run")
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "interrupted"))
        self.assertEqual(record["health_after"], {"status": None, "body": None, "error": "interrupted"})
        self.assertEqual(record["cues"][-1]["cue"], "begin")
        rows = (Path(self.args(camera).out) / "frames.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(record["counts"]["kept"], len(rows) - 1)
        self.assertGreater(record["counts"]["kept"], 0)
        self.assertIn("frames.csv", record["sha256"])
        self.assertLess(output.index("Asking the camera's /health once more"), output.index("Saved "))
        code, report, _ = run_check(Path(self.args(camera).out))  # a FAIL, not a refusal
        self.assertEqual((code, report["verdict"]), (1, "FAIL"))
        self.assertIn("B1_complete", report["failures"])

    def test_second_ctrl_c_during_poller_join_still_saves_the_take(self):
        camera = self.camera()

        def say(text):
            if text == "Begin.":
                raise KeyboardInterrupt
        join = mock.patch.object(take.Poller, "join", autospec=True, side_effect=KeyboardInterrupt)
        try:
            with join as joined:  # the second Ctrl+C lands while take.py waits for the poller
                code, record, output = self.run_take(self.args(camera), say=say)
        except KeyboardInterrupt:
            self.fail("the second Ctrl+C escaped take.run")
        poller = joined.call_args.args[0]
        joined.assert_called_once_with(poller, timeout=15)
        poller.join(5)  # the real join, so the poller is gone before the folder is removed
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "interrupted"))
        self.assertEqual(record["health_after"]["body"]["session"], camera.session)  # still asked
        self.assertEqual(record["cues"][-1]["cue"], "begin")
        rows = (Path(self.args(camera).out) / "frames.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(record["counts"]["kept"], len(rows) - 1)
        self.assertGreater(record["counts"]["kept"], 0)
        self.assertIn("frames.csv", record["sha256"])
        self.assertLess(output.index("Asking the camera's /health once more"), output.index("Saved "))
        code, report, _ = run_check(Path(self.args(camera).out))  # a FAIL, not a refusal
        self.assertEqual((code, report["verdict"]), (1, "FAIL"))
        self.assertIn("B1_complete", report["failures"])

    def test_take_json_never_finished_is_refused_as_such(self):
        args, writers = self.args(self.camera()), []
        self.addCleanup(lambda: [writer.close() for writer in writers])  # before the folder is removed

        def power_cut(camera, writer, *rest):  # take.py dies right after its first take.json write
            writers.append(writer)
            raise SystemExit("synthetic power cut")
        with mock.patch.object(take, "Poller", power_cut), self.assertRaises(SystemExit):
            take.run(args, say=lambda t: None, settings=FAST)
        self.assertEqual(json.loads((Path(args.out) / "take.json").read_text(encoding="utf-8"))["cues"], [])
        with self.assertRaisesRegex(check.Refused, "^take.json was never finished; take.py stopped before saving$"):
            run_check(Path(args.out))

    def test_no_new_frame_for_stall_s_stops_the_take(self):
        camera = self.camera()
        threading.Timer(1.5, camera.close).start()
        settings = dataclasses.replace(FAST, stall_s=1.0, commission_activity_s=10.0)
        code, record, _ = self.run_take(self.args(camera), settings=settings)
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "no_new_frame"))
        self.assertEqual(record["cues"][-1]["cue"], "stopped")
        self.assertGreater(record["counts"]["errors"], 0)
        self.assertIsNone(record["health_after"]["status"])

    def test_token_change_stops_the_take(self):
        camera = self.camera()
        threading.Timer(1.0, lambda: setattr(camera, "token", "x" * 30)).start()
        code, record, _ = self.run_take(self.args(camera))
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "unauthorized"))

    def test_free_take_ends_on_enter(self):
        camera = self.camera()
        settings = take.Settings(slate_s=1.0, hold_lead_s=0.2, say_gap_s=0.1, away_s=0.2, free_max_s=60)

        started = time.monotonic()

        def enter():
            return time.monotonic() > started + 3.0
        code, record, _ = self.run_take(self.args(camera, script="free"), settings=settings, enter=enter)
        self.assertEqual((code, record["commissioning"]), (0, False))
        cue = {c["cue"]: c["pc_mono_s"] for c in record["cues"]}
        self.assertLess(cue["hold_phone_end"] - cue["begin"], 5)  # Enter, not the 60 s cap
        self.assertLess(time.monotonic() - started, 20)

    def test_unexpected_error_marks_take_incomplete(self):
        camera = self.camera()

        def broken_peek(window, m):
            raise RuntimeError("synthetic failure")
        args = self.args(camera)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            take.run(args, say=lambda t: None, peek=broken_peek, settings=FAST)
        record = json.loads((Path(args.out) / "take.json").read_text(encoding="utf-8"))
        self.assertEqual((record["complete"], record["stop_reason"]), (False, "error: RuntimeError"))

    def test_three_unreadable_start_slates_stop_the_take(self):
        camera = self.camera()  # no phone screen, so the real peek finds no screen
        code, record, _ = self.run_take(self.args(camera), peek=take.peek)
        self.assertEqual((code, record["complete"], record["stop_reason"]), (1, False, "start_slate_unreadable"))
        self.assertEqual([(s["slate"], s["attempt"], s["peek"]["readable"]) for s in record["slates"]],
                         [("start", 1, False), ("start", 2, False), ("start", 3, False)])
        self.assertEqual([c["cue"] for c in record["cues"]].count("slate_start_again"), 2)
        code, report, _ = run_check(Path(self.args(camera).out))  # take.py output feeds check.py
        self.assertEqual((code, report["verdict"]), (1, "FAIL"))
        self.assertIn("B1_complete", report["failures"])


class EndToEndTests(Workspace):
    def test_take_py_output_passes_check_py(self):
        camera, c, spoken = SlateCamera(c=2.3456), 2.3456, []
        self.addCleanup(camera.close)
        # On Linux perf_counter and monotonic are one clock. Shifting take.clock by 100000 s puts any
        # cue or receipt not taken through take.clock outside the slate windows, so this test fails.
        shifted = mock.patch.object(take, "clock", lambda: time.perf_counter() + 100000.0)
        shifted.start()
        self.addCleanup(shifted.stop)

        def say(text):
            spoken.append(text)
            if text.startswith("Hold the phone"):
                camera.showing = True
            elif text.startswith(("Slate OK", "End slate")):
                camera.showing = False
        settings = take.Settings(slate_s=M["slate_length_s"] / 5, poll_s=0.05, hold_lead_s=0.2, say_gap_s=0.1,
                                 away_s=0.4, commission_activity_s=4.0, health_retry_s=0.1)
        code, record, _ = self.run_take(self.args(camera), say=say, peek=take.peek, settings=settings)
        self.assertEqual((code, record["complete"]), (0, True), record["slates"])
        code, report, _ = run_check(Path(self.args(camera).out))
        self.assertEqual((code, report["verdict"], report["failures"]), (0, "PASS", []))
        self.assertGreater(report["usable"]["minutes"], 0)
        self.assertTrue(report["usable"]["segments"])
        for judged in report["reported_only"]["offset_c_mod_period_s"]:
            self.assertLess(abs(check.wrap(judged - c, 10)), 0.05)
        self.assertNotIn("2.3", " ".join(spoken))


class FolderRuleTests(Workspace):
    def refused(self, out, pattern):
        with self.assertRaisesRegex(take.Refused, pattern):
            take.run_dir(out, self.token_file)

    def test_refuses_relative_and_git_checkout(self):
        self.refused("den-takes/c1", "absolute")
        (self.tmp / "repo" / ".git").mkdir(parents=True)
        self.refused(self.tmp / "repo" / "takes" / "c1", "git checkout")

    def test_refuses_onedrive(self):
        self.refused(self.tmp / "OneDrive - Home" / "c1", "OneDrive")
        with mock.patch.dict(take.os.environ, {"OneDrive": str(self.tmp / "synced")}):
            self.refused(self.tmp / "synced" / "c1", "OneDrive")

    def test_refuses_existing_take_and_token_folder(self):
        (self.tmp / "old").mkdir()
        (self.tmp / "old" / "take.json").write_text("{}", encoding="utf-8", newline="\n")
        self.refused(self.tmp / "old", "already holds a take")
        self.refused(self.token_file.parent, "token")

    def test_refuses_under_35_gb_free(self):
        usage = types.SimpleNamespace(total=500 * GIB, used=0, free=34 * GIB)
        with mock.patch.object(take, "free_bytes", REAL_FREE_BYTES), \
                mock.patch.object(take.shutil, "disk_usage", return_value=usage) as disk_usage:
            self.refused(self.tmp / "new" / "c1", "35 GB")
            self.assertEqual(disk_usage.call_args[0][0], self.tmp)  # nearest existing folder
            usage.free = 36 * GIB
            self.assertEqual(take.run_dir(self.tmp / "c1", self.token_file), self.tmp / "c1")

    def test_refuses_a_drive_that_does_not_exist(self):
        calls = []

        def missing(path):
            calls.append(path)
            if len(calls) > 100:  # fail instead of climbing forever
                raise AssertionError("free_bytes went past the drive root")
            return False
        with mock.patch.object(take.Path, "exists", missing):
            with self.assertRaisesRegex(take.Refused, "drive for --out does not exist"):
                REAL_FREE_BYTES(self.tmp / "new" / "c1")
        self.assertEqual(calls[-1], Path(calls[-1].anchor))


class SlateFitTests(unittest.TestCase):
    def test_recovers_offset_at_about_4_fps(self):
        times = frame_times(4000.0, 4000.0 + M["slate_length_s"], seed=3)
        self.assertTrue(4.0 <= len(times) / M["slate_length_s"] <= 4.6)
        for c in (3.2137, -4.9917, 0.0):
            fit = check.fit_slate(slate_frames(times, c), M)
            self.assertTrue(fit["readable"], fit)
            self.assertEqual(fit["window_count"], 1)
            self.assertLessEqual(fit["width_s"], M["max_window_s"])
            self.assertLess(abs(check.wrap(fit["c_s"] - c, 10)), 0.01, fit)

    def test_recovers_offset_at_locked_cadence(self):
        times = [1000.0 + 0.2 * k for k in range(100)]
        fit = check.fit_slate(slate_frames(times, 3.2137), M)
        self.assertTrue(fit["readable"], fit)
        self.assertEqual(fit["window_count"], 1)
        self.assertLess(abs(fit["c_s"] - 3.2137), 0.01, fit)

    def test_unreadable_without_screen(self):
        times = frame_times(4000.0, 4020.0)
        fit = check.fit_slate(slate_frames(times, 1.0, shown=False), M)
        self.assertEqual((fit["readable"], fit["reason"]), (False, "no_screen"))

    def test_peek_calls_a_repeated_image_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            window, rgb = [], None
            for i, t in enumerate(frame_times(4000.0, 4020.0, seed=3)):
                path = Path(tmp) / f"{i}.npz"
                rgb = rgb if i == 40 else nv12(screen_value(t - 1.5), i)  # frame 40 repeats 39
                path.write_bytes(bundle(metadata("s", i, t, t - 9.0), rgb))
                window.append((path, t))
            self.assertEqual(take.peek(window, M)["reason"], "stale_frame")
            self.assertTrue(take.peek(window[:40] + window[41:], M)["readable"])

    def test_wide_window_is_unreadable_and_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            wide = slate_bundles(tmp, frame_times(4000.0, 4020.0, seed=300), 0.5)
            narrow = slate_bundles(tmp, frame_times(4025.0, 4045.0, seed=3), 0.5, first=len(wide))
            self.assertEqual(take.peek(wide, M), {"readable": False, "reason": "wide_window",
                                                  "flashes": 19, "frames": len(wide)})  # never c
            windows, spoken = iter([wide, narrow]), []
            record = {"cues": [], "slates": []}
            script = take.Script(record, types.SimpleNamespace(done=threading.Event()),
                                 types.SimpleNamespace(window=lambda start, end: next(windows)),
                                 take.Settings(slate_s=0.01, hold_lead_s=0.01, say_gap_s=0.01), M,
                                 spoken.append, take.peek)
            self.assertTrue(script.slate("start"))
        self.assertEqual([(a["attempt"], a["peek"]["reason"]) for a in record["slates"]],
                         [(1, "wide_window"), (2, None)])
        self.assertEqual(spoken, ["Hold the phone to the camera.", "Slate.", "Unreadable, again.", "Slate."])

    def test_window_wider_than_max_is_unreadable(self):
        dense = slate_frames([1000.0 + 0.2 * k for k in range(100)], 3.2137)
        width = check.fit_slate(dense, M)["width_s"]
        self.assertTrue(check.fit_slate(dense, dict(M, max_window_s=width))["readable"])
        narrower = dict(M, max_window_s=round(width - 0.001, 6))
        fit = check.fit_slate(dense, narrower)
        self.assertEqual((fit["readable"], fit["reason"], fit["window_count"], fit["width_s"], fit["c_s"]),
                         (False, "wide_window", 1, width, None))
        self.assertFalse(judge([("start", dense), ("end", slate_frames([2000.0 + 0.2 * k for k in range(100)],
                                                                       3.2137))], narrower)["start"]["pass"])

    def test_two_windows_are_unreadable(self):
        # Phone seconds 2 to 9 only: no marker, and a 1 s shift moves each flash just 0.02 s.
        times = frame_times(4002.5, 4010.0, seed=3)
        fit = check.fit_slate(slate_frames(times, 0.5), dict(M, min_flashes=5))
        self.assertEqual([round(sum(w) / 2) for w in fit["windows"]], [0, 2], fit)  # around 0.5 s and 1.5 s
        self.assertEqual((fit["readable"], fit["reason"], fit["window_count"], fit["width_s"], fit["c_s"]),
                         (False, "wide_window", 2, None, None))

    def test_unreadable_with_fewer_than_12_flashes(self):
        times = frame_times(4000.0, 4010.0)  # 10 s holds 9 flashes
        fit = check.fit_slate(slate_frames(times, 1.0), M)
        self.assertEqual((fit["readable"], fit["reason"]), (False, "few_flashes"))
        self.assertLess(fit["flashes"], M["min_flashes"])


class SlateRuleTests(unittest.TestCase):
    """judge_slates: the first readable attempt of each slate is judged, and must be one narrow window."""

    def attempts(self, *fits):
        stub = iter(dict({"frames": 1, "reason": None, "flashes": 12, "screen_pixels": 400, "windows": [[0.98, 1.02]],
                          "window_count": 1, "width_s": 0.04, "c_s": 1.0}, **fit) for fit in fits)
        frames = slate_frames([5000.0, 5000.2], 1.0)
        with mock.patch.object(check, "fit_slate", side_effect=lambda frames, m: next(stub)):
            return judge([("start", frames)] * (len(fits) - 1) + [("end", frames)])

    def test_first_readable_attempt_is_judged_never_a_later_one(self):
        for first in ({"readable": True, "window_count": 2, "width_s": None, "c_s": None},
                      {"readable": True, "width_s": M["max_window_s"] + 0.001}):
            report = self.attempts(first, {"readable": True}, {"readable": True})
            self.assertEqual((report["start"]["judged_attempt"], report["start"]["pass"]), (1, False), first)
            self.assertEqual((report["end"]["judged_attempt"], report["end"]["pass"]), (1, True))
        report = self.attempts({"readable": False, "reason": "wide_window"}, {"readable": True}, {"readable": True})
        self.assertEqual((report["start"]["judged_attempt"], report["start"]["pass"]), (2, True))

    def test_wide_first_attempt_gives_way_to_a_narrow_one(self):
        report = judge([("start", slate_frames(frame_times(4000.0, 4020.0, seed=300), 0.5)),
                        ("start", slate_frames(frame_times(4025.0, 4045.0, seed=3), 0.5)),
                        ("end", slate_frames(frame_times(5000.0, 5020.0, seed=4), 0.5))])
        start = report["start"]
        self.assertEqual([a["reason"] for a in start["attempts"]], ["wide_window", None])
        self.assertEqual((start["judged_attempt"], start["pass"], report["end"]["pass"]), (2, True, True))


class FaultTests(unittest.TestCase):
    """Each injected fault must FAIL; the clean take and a 0.05 s offset pass."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="den-take-faults-")).resolve()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verdict(self, m=None, **faults):
        folder = make_take(self.tmp, **faults) if faults else clean_take(self.tmp)
        code, report, _ = run_check(folder, m=m)
        return report["verdict"], report["failures"], report

    def test_clean_take_passes(self):
        verdict, failures, report = self.verdict()
        self.assertEqual((verdict, failures), ("PASS", []))
        self.assertFalse(report["retained"])  # commissioning takes are never retained
        self.assertLessEqual(report["rules"]["A_elapsed_bound"]["elapsed_bound_s"], 0.1)

    def test_offset_between_slates(self):
        self.assertEqual(self.verdict(step_both=("begin", 15, 0.05))[0], "PASS")
        verdict, failures, _ = self.verdict(step_both=("begin", 15, 0.20))
        self.assertEqual((verdict, failures), ("FAIL", ["A_elapsed_bound"]))
        verdict, failures, report = self.verdict(step_both=("begin", 15, 0.15))  # fails on the widths
        self.assertEqual((verdict, failures), ("FAIL", ["A_elapsed_bound"]))
        self.assertGreater(report["rules"]["A_elapsed_bound"]["elapsed_bound_s"], M["elapsed_bound_s"])

    def test_offset_near_five_seconds_wraps(self):
        verdict, failures, report = self.verdict(c=4.98, step_both=("begin", 15, 0.05))
        self.assertEqual((verdict, failures), ("PASS", []))
        self.assertLess(report["rules"]["A_elapsed_bound"]["elapsed_bound_s"], 0.15)

    def test_slates_too_far_apart(self):
        verdict, failures, report = self.verdict(m=dict(M, max_slate_separation_s=10))
        self.assertEqual((verdict, failures), ("FAIL", ["A_separation"]))
        self.assertGreater(report["rules"]["A_separation"]["separation_s"], 10)

    def test_one_second_error_at_end_slate(self):
        verdict, failures, _ = self.verdict(step_both=("hold_end", -0.5, 1.0))
        self.assertEqual((verdict, failures), ("FAIL", ["A_elapsed_bound"]))

    def test_step_in_t_host_only(self):
        verdict, failures, _ = self.verdict(step_host=("begin", 15, 0.03))
        self.assertEqual((verdict, failures), ("FAIL", ["B4_two_clock"]))

    def test_stale_image_inside_a_slate(self):
        verdict, failures, report = self.verdict(freeze=("slate_end", 6.0, 2.0))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("A_end_slate", failures)

    def test_one_stale_frame_inside_a_slate(self):
        verdict, failures, report = self.verdict(repeat_at=("slate_end", 5.0))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("A_end_slate", failures)
        self.assertEqual(report["rules"]["A_end_slate"]["attempts"][0]["reason"], "stale_frame")

    def test_t_host_bump_off_the_two_clock_line(self):
        verdict, failures, report = self.verdict(host_bump=("begin", 5, 0.04))
        self.assertEqual((verdict, failures), ("FAIL", ["B4_two_clock"]))
        rule = report["rules"]["B4_two_clock"]
        self.assertLessEqual(rule["max_step_s"], M["max_two_clock_step_s"])  # only the line fails
        self.assertLessEqual(abs(rule["slope_ppm"]), M["max_two_clock_slope_ppm"])

    def test_device_clock_drifts_too_fast(self):
        verdict, failures, report = self.verdict(device_drift_ppm=2000)
        self.assertEqual((verdict, failures), ("FAIL", ["B4_two_clock"]))
        rule = report["rules"]["B4_two_clock"]
        self.assertLessEqual(rule["max_step_s"], M["max_two_clock_step_s"])  # only the slope fails
        self.assertLessEqual(rule["max_residual_s"], M["max_two_clock_line_s"])

    def test_repeated_rgb_sequence(self):
        verdict, failures, _ = self.verdict(seq_repeat_at=("begin", 10))
        self.assertEqual((verdict, failures), ("FAIL", ["B3_increasing"]))

    def test_time_goes_backwards(self):
        verdict, failures, _ = self.verdict(backwards_at=("begin", 10))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("B3_increasing", failures)

    def test_session_change(self):
        verdict, failures, _ = self.verdict(session_change_at=("begin", 10))
        self.assertEqual((verdict, failures), ("FAIL", ["B2_session"]))

    def test_incomplete_take(self):
        verdict, failures, _ = self.verdict(complete=False)
        self.assertEqual((verdict, failures), ("FAIL", ["B1_complete"]))

    def test_check_hash_mismatch_is_refused(self):
        folder = make_take(self.tmp, method_sha="0" * 64)
        with self.assertRaisesRegex(check.Refused, "different CHECK file"):
            run_check(folder)
        self.assertEqual(list(folder.glob("check-*.json")), [])

    def test_changed_file_fails_hash_rule(self):
        folder = clean_take(self.tmp)
        path = sorted((folder / "frames").iterdir())[40]
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as src:
            members = {i.filename: src.read(i.filename) for i in src.infolist()}
        members["depth_mm.npy"] = npy("<u2", (400, 640), bytes([7]) * 512_000)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        path.write_bytes(out.getvalue())  # still a valid bundle; only depth changed
        code, report, _ = run_check(folder)
        self.assertEqual((report["verdict"], report["failures"]), ("FAIL", ["B5_hashes"]))
        self.assertEqual(report["rules"]["B5_hashes"]["bad_files"], [path.relative_to(folder).as_posix()])

    def test_row_that_disagrees_with_its_bundle_fails_hash_rule(self):
        folder = clean_take(self.tmp)
        csv_path = folder / "frames.csv"
        lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
        columns = lines[0].rstrip("\n").split(",")
        edited = []
        for row, column in ((30, "rgb_seq"), (60, "t_host_s"), (90, "t_device_s")):
            cells = lines[row].split(",")
            at = columns.index(column)
            digits = cells[at].index(".") + 1 if "." in cells[at] else len(cells[at]) - 1
            cells[at] = cells[at][:digits] + str((int(cells[at][digits]) + 1) % 10) + cells[at][digits + 1:]
            lines[row] = ",".join(cells)  # same width, a different value
            edited.append(cells[columns.index("file")])
        csv_path.write_text("".join(lines), encoding="utf-8", newline="\n")
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        record["sha256"]["frames.csv"] = check.file_sha256(csv_path)  # as if take.py wrote it
        take.write_json(folder / "take.json", record)
        code, report, _ = run_check(folder)
        self.assertEqual((report["verdict"], report["failures"]), ("FAIL", ["B5_hashes"]))
        self.assertEqual(report["rules"]["B5_hashes"]["bad_files"], edited)

    def test_edited_frames_csv_fails_hash_rule(self):
        folder = make_take(self.tmp, backwards_at=("begin", 10))
        csv_path = folder / "frames.csv"
        lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
        bad = next(i for i, line in enumerate(lines[2:], 2)
                   if float(line.split(",")[4]) < float(lines[i - 1].split(",")[4]))
        csv_path.write_text("".join(lines[:bad] + lines[bad + 1:]), encoding="utf-8", newline="\n")
        code, report, _ = run_check(folder)  # the backwards row is gone, but the edit shows
        self.assertEqual((report["verdict"], report["failures"]), ("FAIL", ["B5_hashes"]))
        self.assertEqual(report["rules"]["B5_hashes"]["bad_files"], ["frames.csv"])


class CutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="den-take-cuts-")).resolve()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def kinds(self, report, kind):
        return [c for c in report["cuts"] if c["kind"] == kind]

    def test_repeated_image_is_cut_as_stale(self):
        folder = make_take(self.tmp, repeat_at=("begin", 10))
        code, report, _ = run_check(folder)
        self.assertEqual(report["verdict"], "PASS")
        [cut] = self.kinds(report, "stale")
        self.assertAlmostEqual(cut["seconds"], 2 * M["stale_pad_s"])

    def test_gap_over_one_second_is_cut(self):
        code, report, _ = run_check(make_take(self.tmp, drop=("begin", 10, 2.0)))
        self.assertEqual(report["verdict"], "PASS")
        [cut] = self.kinds(report, "gap")
        self.assertGreater(cut["seconds"], 2.0)
        self.assertEqual(report["reported_only"]["gap_count"], 1)
        self.assertEqual(len(report["usable"]["segments"]), 2)

    def test_touching_gaps_merge_into_one_cut(self):
        frames = [{"stale": False}] * 3
        cuts = check.gap_and_stale_cuts(frames, [0.4, 2.0, 3.6], M)
        self.assertEqual([(c["kind"], c["start_t_s"], c["end_t_s"]) for c in cuts], [("gap", 0.4, 3.6)])

    def test_typed_span_cuts_padded_seconds(self):
        self.assertEqual(check.parse_spans("01:10-01:20", 1.0), [[69.0, 81.0]])
        self.assertEqual(check.parse_spans(" 01:10-01:20 , 01:21-01:25,00:05", 1.0),
                         [[4.0, 6.0], [69.0, 86.0]])
        self.assertEqual(check.parse_spans("None", 1.0), [])
        for bad in ("", "01:70-01:80", "01:20-01:10", "everything"):
            with self.assertRaises(ValueError, msg=bad):
                check.parse_spans(bad, 1.0)
        code, report, _ = run_check(make_take(self.tmp, activity=60.0), typed="01:10-01:20")
        [cut] = self.kinds(report, "second_person")
        self.assertEqual((cut["start_t_s"], cut["end_t_s"], cut["start"], cut["end"]), (69.0, 81.0, "01:09", "01:21"))
        self.assertEqual(report["second_person"], {"asked": True, "typed": "01:10-01:20"})
        segments = report["usable"]["segments"]
        self.assertEqual([s["end_t_s"] for s in segments][0], 69.0)
        self.assertEqual(segments[1]["start_t_s"], 81.0)

    def test_usable_part_runs_from_begin_to_end_hold(self):
        folder = make_take(self.tmp, rejected_start=True)
        code, report, _ = run_check(folder)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["rules"]["A_start_slate"]["judged_attempt"], 2)
        self.assertFalse(report["rules"]["A_start_slate"]["attempts"][0]["readable"])
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        rows = check.read_take(folder, M)[2]
        ts = [f["t_host"] - rows[0]["t_host"] for f in rows]
        cue = {c["cue"]: c["pc_mono_s"] for c in record["cues"]}
        first_after = lambda at: next(t for f, t in zip(rows, ts) if f["recv"] >= at)
        usable = report["usable"]
        self.assertEqual(usable["start_t_s"], round(first_after(cue["begin"]), 6))
        self.assertEqual(usable["end_t_s"], round(first_after(cue["hold_phone_end"]), 6))
        for attempt in record["slates"]:
            inside = [t for f, t in zip(rows, ts) if attempt["pc_start_s"] <= f["recv"] <= attempt["pc_end_s"]]
            self.assertTrue(all(t < usable["start_t_s"] or t >= usable["end_t_s"] for t in inside))
        self.assertAlmostEqual(usable["minutes"] * 60, usable["end_t_s"] - usable["start_t_s"], places=2)

    def test_rerun_writes_next_check_file(self):
        folder = clean_take(self.tmp)
        first = run_check(folder)[2]
        before = first.read_bytes()
        second = run_check(folder, typed="00:50-00:52")[2]
        self.assertEqual((first.name, second.name), ("check-1.json", "check-2.json"))
        self.assertEqual(first.read_bytes(), before)

    def test_span_prompt_refuses_without_console(self):
        with self.assertRaisesRegex(check.Refused, "interactive console"):
            check.ask_spans(stdin=io.StringIO("none\n"), stdout=io.StringIO())
        folder, shown = clean_take(self.tmp), []
        with self.assertRaises(check.Refused):
            check.run_check(folder, ask=lambda: "none", show=shown.append, out=io.StringIO(),
                            console=io.StringIO("none\n"))
        self.assertEqual((list(folder.glob("check-*.json")), shown), ([], []))
        self.assertFalse((folder / "review").exists())  # nothing built before the refusal

    def test_span_prompt_reads_console_answer(self):
        console = Console("01:99\n01:10-01:20\n")
        self.assertEqual(check.ask_spans(stdin=console, stdout=io.StringIO()), "01:10-01:20")


# ---- staged scripts (N1): --script hard-a and hard-b ----

QUICK = {"format": "den-take-script-1", "end_s": 1.2, "blocks": [
    {"at_s": 0.3, "case": "idle_seated", "say": "Sit."}, {"at_s": 0.6, "case": "still_reader", "say": "Read."}]}
TASK_CASES = {"still_reader", "sorting_mail", "wiping", "phone_at_counter", "tidying", "other"}
QUIET_CASES = {"idle_seated", "idle_standing", "lights_down"}


class FakeClock:
    """take.clock and take.sleep for the cue loop: sleeping moves the clock, nothing waits."""

    def __init__(self, t=500.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, seconds)


class StagedScriptTests(Workspace):
    def scripts(self, files):
        """A scripts folder for this test only, in place of tools/den-take/scripts."""
        folder = self.tmp / "scripts"
        folder.mkdir(exist_ok=True)
        for name, data in files.items():
            text = data if isinstance(data, str) else json.dumps(data)
            (folder / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
        patch = mock.patch.object(take, "SCRIPTS", folder)
        patch.start()
        self.addCleanup(patch.stop)
        return folder

    def play(self, name, enter=lambda t: False):
        """Script.run with the real settings on a fake clock: a 25-minute script in a moment."""
        fake, spoken, record = FakeClock(), [], {"cues": [], "slates": []}
        settings = take.Settings(slate_s=M["slate_length_s"])
        staged = take.load_script(name, settings)
        poller = types.SimpleNamespace(done=threading.Event())
        writer = types.SimpleNamespace(window=lambda start, end: [])
        with mock.patch.object(take, "clock", fake), mock.patch.object(take, "sleep", fake.sleep), \
                contextlib.redirect_stdout(io.StringIO()):
            script = take.Script(record, poller, writer, settings, M, lambda text: spoken.append((fake(), text)),
                                 readable_peek)
            script.run(name, lambda: enter(fake()), staged)
        cues = record["cues"]
        begin = next(c["pc_mono_s"] for c in cues if c["cue"] == "begin")
        return staged, cues, spoken, begin

    def test_each_cue_is_spoken_and_logged_at_its_time_on_a_fake_clock(self):
        for name in ("hard-a", "hard-b"):
            staged, cues, spoken, begin = self.play(name)
            stages = [c for c in cues if c["cue"] == "stage"]
            self.assertEqual([(c["at_s"], c["case"], c["text"]) for c in stages],
                             [(b["at_s"], b["case"], b["say"]) for b in staged["blocks"]], name)
            for c in stages:
                self.assertAlmostEqual(c["pc_mono_s"] - begin, c["at_s"], places=6)
            said = [(round(t - begin, 6), text) for t, text in spoken if t >= begin]
            self.assertEqual(said[1:len(stages) + 1], [(float(b["at_s"]), b["say"]) for b in staged["blocks"]])
            hold = next(c for c in cues if c["cue"] == "hold_phone_end")
            self.assertAlmostEqual(hold["pc_mono_s"] - begin, staged["end_s"], places=6)
            self.assertEqual([c["cue"] for c in cues][-3:], ["hold_phone_end", "slate_end", "done"])

    def test_enter_ends_a_staged_script_early_on_a_fake_clock(self):
        begin_at = []

        def enter(t):
            begin_at.append(t)
            return t >= begin_at[0] + 100
        staged, cues, spoken, begin = self.play("hard-a", enter)
        stages = [c for c in cues if c["cue"] == "stage"]
        self.assertEqual([c["at_s"] for c in stages], [3, 90])
        hold = next(c for c in cues if c["cue"] == "hold_phone_end")
        self.assertLess(hold["pc_mono_s"] - begin, 101)

    def test_a_staged_take_speaks_logs_and_records_its_script(self):
        folder = self.scripts({"quick": QUICK})
        camera, spoken = self.camera(), []
        code, record, _ = self.run_take(self.args(camera, script="quick"), say=spoken.append, enter=lambda: False)
        self.assertEqual((code, record["complete"], record["commissioning"], record["script"]),
                         (0, True, False, "quick"))
        self.assertEqual(record["script_file"], {"file": "scripts/quick.json",
                                                 "sha256": hashlib.sha256((folder / "quick.json").read_bytes())
                                                 .hexdigest()})
        self.assertEqual([c["cue"] for c in record["cues"]],
                         ["hold_phone_start", "slate_start", "phone_away", "begin", "stage", "stage",
                          "hold_phone_end", "slate_end", "done"])
        cue = {c["cue"]: c for c in record["cues"]}
        stages = [c for c in record["cues"] if c["cue"] == "stage"]
        self.assertEqual([(c["case"], c["text"], c["at_s"]) for c in stages],
                         [("idle_seated", "Sit.", 0.3), ("still_reader", "Read.", 0.6)])
        for c in stages:
            self.assertAlmostEqual(c["pc_mono_s"] - cue["begin"]["pc_mono_s"], c["at_s"], delta=0.15)
        self.assertAlmostEqual(cue["hold_phone_end"]["pc_mono_s"] - cue["begin"]["pc_mono_s"], 1.2, delta=0.15)
        at = [spoken.index(text) for text in ("Begin.", "Sit.", "Read.")]
        self.assertEqual(at, sorted(at))
        self.assertEqual(spoken[at[-1] + 1], "Hold the phone to the camera.")  # the end slate, at end_s

    def test_enter_ends_a_staged_take_early_and_it_is_still_saved(self):
        camera, spoken = self.camera(), []
        started = time.monotonic()

        def enter():  # Enter, just after the first of hard-a's cues (3 s after "Begin.")
            return any(text.startswith("Go to the desk") for text in spoken)
        code, record, _ = self.run_take(self.args(camera, script="hard-a"), say=spoken.append, enter=enter)
        self.assertEqual((code, record["complete"], record["script"]), (0, True, "hard-a"))
        self.assertEqual(record["script_file"]["sha256"], check.file_sha256(HERE / "scripts" / "hard-a.json"))
        cue = {c["cue"]: c["pc_mono_s"] for c in record["cues"]}
        self.assertEqual([c["at_s"] for c in record["cues"] if c["cue"] == "stage"], [3])
        self.assertLess(cue["hold_phone_end"] - cue["begin"], 6)  # Enter, not the 1,500 s end_s
        self.assertLess(time.monotonic() - started, 30)
        self.assertTrue((Path(self.args(camera).out) / "frames.csv").exists())

    def test_a_malformed_or_unknown_script_is_refused_before_any_camera_read(self):
        first, second = QUICK["blocks"]
        bad = {
            "unknown-key": dict(QUICK, note="x"), "no-end": {k: v for k, v in QUICK.items() if k != "end_s"},
            "no-blocks": dict(QUICK, blocks=[]), "wrong-format": dict(QUICK, format="den-take-script-0"),
            "block-key": dict(QUICK, blocks=[dict(first, cue="x")]),
            "block-missing": dict(QUICK, blocks=[{"at_s": 0.3, "say": "Sit."}]),
            "not-increasing": dict(QUICK, blocks=[second, first]),
            "too-close": dict(QUICK, blocks=[first, dict(second, at_s=0.35)]),
            "first-too-soon": dict(QUICK, blocks=[dict(first, at_s=0.05)]),
            "end-too-soon": dict(QUICK, end_s=0.65), "too-long": dict(QUICK, end_s=28 * 60 + 1),
            "bool-time": dict(QUICK, blocks=[dict(first, at_s=True)]), "text-time": dict(QUICK, end_s="1.2"),
            "nan": json.dumps(QUICK).replace("1.2", "NaN"), "bad-case": dict(QUICK, blocks=[dict(first, case="Idle")]),
            "empty-say": dict(QUICK, blocks=[dict(first, say=" ")]), "not-json": "{",
            "two-lines": dict(QUICK, blocks=[dict(first, say="Sit.\nNow.")]),
        }
        self.scripts(bad)
        camera = self.camera()
        for name in list(bad) + ["missing", "../scripts/quick", "Hard-A", ""]:
            args = self.args(camera, script=name, name=f"t-{len(name)}-{abs(hash(name))}")
            with self.subTest(name), self.assertRaisesRegex(take.Refused, "nothing recorded"):
                take.run(args, say=lambda text: None, peek=readable_peek, settings=FAST)
            self.assertFalse(Path(args.out).exists(), name)
        self.assertEqual(camera.requests, [])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = take.main(["--script", "nope", "--out", str(self.tmp / "takes" / "t9"),
                              "--camera", camera.url, "--token-file", str(self.token_file)])
        self.assertEqual(code, 2)
        self.assertIn("Refused: no script 'nope'", err.getvalue())
        self.assertEqual(camera.requests, [])

    def test_files_read_the_same_with_crlf(self):
        text = (HERE / "scripts" / "hard-b.json").read_text(encoding="utf-8")
        self.scripts({"crlf": text.replace("\n", "\r\n")})
        settings = take.Settings(slate_s=M["slate_length_s"])
        crlf = take.load_script("crlf", settings)
        with mock.patch.object(take, "SCRIPTS", HERE / "scripts"):
            lf = take.load_script("hard-b", settings)
        self.assertEqual((crlf["blocks"], crlf["end_s"]), (lf["blocks"], lf["end_s"]))


class StagedCheckTests(unittest.TestCase):
    def test_check_gives_the_same_verdict_on_a_staged_take_as_on_a_plain_one(self):
        root = Path(tempfile.mkdtemp(prefix="den-take-staged-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        staged = make_take(root, activity=35.0, commissioning=False, script="hard-b")
        record = json.loads((staged / "take.json").read_text(encoding="utf-8"))
        self.assertEqual((record["script"], record["script_file"]["file"]), ("hard-b", "scripts/hard-b.json"))
        self.assertIsInstance(record["started_unix"], float)
        self.assertEqual([(c["at_s"], c["case"]) for c in record["cues"] if c["cue"] == "stage"],
                         [(3, "idle_standing"), (30, "wiping")])
        plain = root / "plain"
        shutil.copytree(staged, plain)
        record["cues"] = [c for c in record["cues"] if c["cue"] != "stage"]
        record["script"], record["script_file"] = "free", None
        take.write_json(plain / "take.json", record)
        for typed in ("none", "00:20"):
            reports = [run_check(folder, typed=typed)[1] for folder in (staged, plain)]
            self.assertEqual(reports[0]["verdict"], "PASS")
            for key in ("verdict", "retained", "failures", "rules", "cuts", "cut_seconds", "usable"):
                self.assertEqual(reports[0][key], reports[1][key], key)

    def test_make_take_writes_its_script_and_started_unix(self):
        root = Path(tempfile.mkdtemp(prefix="den-take-fields-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        records = [json.loads((make_take(root, **kw) / "take.json").read_text(encoding="utf-8")) for kw in
                   ({}, {"commissioning": False}, {"commissioning": False, "script": "hard-a", "started_unix": 7.5})]
        self.assertEqual([r["script"] for r in records], ["commission", "free", "hard-a"])
        self.assertEqual([r["script_file"] is None for r in records], [True, True, False])
        self.assertEqual(records[1]["started_unix"] - records[0]["started_unix"], 3600.0)
        self.assertEqual(records[2]["started_unix"], 7.5)


class ScriptFileTests(unittest.TestCase):
    """Every file in scripts/ obeys the N1 plan's spacing rules, its stop rule and its end_s."""

    def files(self):
        settings = take.Settings(slate_s=M["slate_length_s"])
        names = sorted(p.stem for p in (HERE / "scripts").glob("*.json"))
        self.assertEqual(names, ["hard-a", "hard-b"])
        return {name: take.load_script(name, settings) for name in names}

    def test_two_spacing_rules_and_end_s(self):
        for name, staged in self.files().items():
            blocks = staged["blocks"]
            self.assertTrue({b["case"] for b in blocks} <= TASK_CASES | QUIET_CASES, name)
            starts = [b["at_s"] for b in blocks] + [staged["end_s"]]
            tasks = [(i, b) for i, b in enumerate(blocks) if b["case"] in TASK_CASES]
            gaps, last_end = [], 0  # "Begin." counts as the end of a task
            for i, block in tasks:
                gaps.append(block["at_s"] - last_end)
                last_end = starts[i + 1]
            self.assertTrue(all(gap >= 30 for gap in gaps), (name, gaps))  # rule 1: 30 s with no task first
            rounds = [gaps[k:k + 4] for k in range(0, len(gaps), 4)]
            self.assertEqual(len(rounds), 5, name)
            self.assertEqual([sum(gap >= 90 for gap in r) for r in rounds], [1] * 5, (name, gaps))  # rule 2
            self.assertEqual(staged["end_s"], 1500, name)
            self.assertEqual(staged["end_s"] - blocks[-1]["at_s"], 30, name)  # the final "Hands still."
            self.assertEqual(blocks[0]["at_s"], 3, name)  # spoken 3 s after "Begin."
            dim = [b for b in blocks if b["case"] == "lights_down"]
            self.assertEqual([(b["at_s"], starts[blocks.index(b) + 1] - b["at_s"]) for b in dim], [(870, 20)], name)

    def test_cases_and_the_stop_rule(self):
        files = self.files()
        orders = {}
        for name, staged in files.items():
            blocks = staged["blocks"]
            counts = {case: sum(b["case"] == case for b in blocks) for case in TASK_CASES}
            self.assertEqual(counts, {"still_reader": 5, "sorting_mail": 5, "wiping": 5, "phone_at_counter": 5,
                                      "tidying": 0, "other": 0}, name)
            self.assertEqual(sum(b["case"] in ("idle_seated", "idle_standing") for b in blocks), 21, name)
            for before, block in zip(blocks, blocks[1:]):
                if before["case"] in TASK_CASES:
                    stop = {"still_reader": "Stop. Put the book down.",
                            "phone_at_counter": "Stop. Put the phone away."}.get(before["case"], "Stop.")
                    self.assertTrue(block["say"].startswith(stop + " "), (name, block))
                    self.assertIn(block["case"], QUIET_CASES, (name, block))
            orders[name] = [b["case"] for b in blocks if b["case"] in TASK_CASES][:4]
        self.assertNotEqual(orders["hard-a"], orders["hard-b"])


class MethodTests(unittest.TestCase):
    def block(self):
        text = (HERE / "CHECK-V1.md").read_text(encoding="utf-8")
        [body] = re.findall(r"^```check-v1\n(.*?)^```", text, re.M | re.S)
        return dict(re.findall(r"^(\w+) = (\S+)$", body, re.M))

    def test_tolerances_come_from_the_block(self):
        block = self.block()
        self.assertEqual(set(block), set(check.METHOD_KEYS))
        self.assertEqual(M, {k: json.loads(v) for k, v in block.items()})
        source = (HERE / "check.py").read_text(encoding="utf-8")
        for name in ("epsilon_s", "max_window_s", "elapsed_bound_s", "max_two_clock_step_s", "lit_above",
                     "max_slate_separation_s", "max_two_clock_slope_ppm"):
            found = re.search(r"(?<![\w.])%s(?![\w.])" % re.escape(block[name]), source)
            self.assertIsNone(found, f"check.py hard-codes {name} = {block[name]}")
        self.assertEqual(take.Settings(slate_s=M["slate_length_s"]).slate_s, float(block["slate_length_s"]))

    def test_a_changed_block_changes_the_check(self):
        text = (HERE / "CHECK-V1.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "CHECK-V1.md"
            copy.write_text(text.replace("min_flashes = 12", "min_flashes = 30"), encoding="utf-8", newline="\n")
            changed = check.load_method(copy)
            self.assertEqual(changed["min_flashes"], 30)
            times = frame_times(4000.0, 4020.0, seed=3)
            self.assertTrue(check.fit_slate(slate_frames(times, 1.5), M)["readable"])
            self.assertEqual(check.fit_slate(slate_frames(times, 1.5), changed)["reason"], "few_flashes")
            copy.write_text(text + "\n```\nextra = 1\n```\n", encoding="utf-8", newline="\n")
            with self.assertRaises(ValueError):
                check.load_method(copy)

    def test_check_file_records_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_check(clean_take(tmp))[1]
        self.assertEqual(report["schema"], "den-take-check-1")
        self.assertEqual(report["method"]["file"], "CHECK-V1.md")
        self.assertEqual(report["method"]["sha256"], check.file_sha256(HERE / "CHECK-V1.md"))
        self.assertEqual(report["check_py_sha256"], check.file_sha256(HERE / "check.py"))
        self.assertEqual(report["method"]["tolerances"], M)

    def test_slate_html_pattern_equals_block(self):
        html = (HERE / "slate.html").read_text(encoding="utf-8")
        found = {name: json.loads(value) for name, value in
                 re.findall(r"const (\w+) = ([0-9.]+);", html)}
        self.assertEqual(found, {"PERIOD_S": M["slate_period_s"], "FLASH_S": M["slate_flash_s"],
                                 "STEP_S": M["slate_phase_step_s"], "MARKER_SECOND": M["slate_marker_second"]})
        self.assertIn("Date.now()", html)
        self.assertNotRegex(html, r"https?://")

    def test_pattern_matches_written_rule(self):
        self.assertFalse(check.pattern_lit(1230.3, M))           # k = 0: marker, black
        self.assertFalse(check.pattern_lit(1233.05, M))          # k = 3: white from 0.06 s
        self.assertTrue(check.pattern_lit(1233.07, M))
        self.assertTrue(check.pattern_lit(1233.55, M))
        self.assertFalse(check.pattern_lit(1233.57, M))          # white ends at 0.56 s
        self.assertAlmostEqual(check.change_distance(1239.69, M), 0.01)  # k = 9 ends at 0.68 s


class PngTests(unittest.TestCase):
    def chunks(self, data):
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        position, chunks = 8, []
        while position < len(data):
            length, kind = struct.unpack(">I4s", data[position:position + 8])
            body = data[position + 8:position + 8 + length]
            crc = struct.unpack(">I", data[position + 8 + length:position + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(kind + body) & 0xFFFFFFFF)
            chunks.append((kind, body))
            position += 12 + length
        return chunks

    def test_thumbnails_are_valid_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixels = bytes(range(256)) * 56 + bytes(64)
            check.write_png(Path(tmp) / "t.png", 160, 90, pixels)
            chunks = self.chunks((Path(tmp) / "t.png").read_bytes())
            self.assertEqual([k for k, _ in chunks], [b"IHDR", b"IDAT", b"IEND"])
            self.assertEqual(struct.unpack(">IIBBBBB", chunks[0][1]), (160, 90, 8, 0, 0, 0, 0))
            raw = zlib.decompress(chunks[1][1])
            self.assertEqual(b"".join(raw[r * 161 + 1:(r + 1) * 161] for r in range(90)), pixels)
            folder = clean_take(tmp)
            run_check(folder)
            thumbs = sorted((folder / "review").glob("*.png"))
            last_t = float((folder / "frames.csv").read_text(encoding="utf-8").splitlines()[-1].split(",")[3])
            self.assertEqual(len(thumbs), math.floor(last_t) + 1)
            for path in thumbs[:5]:
                self.assertEqual(self.chunks(path.read_bytes())[0][0], b"IHDR")
            index = (folder / "review" / "index.html").read_text(encoding="utf-8")
            self.assertEqual(index.count("<figure>"), len(thumbs))
            self.assertIn("<figcaption>00:00</figcaption>", index)


if __name__ == "__main__":
    unittest.main()
