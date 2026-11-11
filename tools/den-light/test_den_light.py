"""Tests for den-light Stage 0 (rung 8). Synthetic data and fakes on 127.0.0.1 only.

    python -B -m unittest discover -s tools/den-light -v

No camera, no Jetson, no Home Assistant and no other host is contacted.
"""
import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import math
import os
import random
import shutil
import signal
import struct
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import meter
import sweep

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURE = ROOT / "tools/fixtures/installed-oak-capture-20261016.py"
SERVER = HERE / "oak-capture-meter.py"
TOKEN = "t" * 32
PLANE = 640 * 360


def load_server():
    spec = importlib.util.spec_from_file_location("oak_capture_meter_under_test", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SRV = load_server()


# ---- synthetic data ---------------------------------------------------------

def encode_pixel(r, g, b):
    """BT.601 limited-range RGB to (Y, U, V), the usual integer forward transform."""
    clip = lambda v: min(255, max(0, v))
    return (clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16),
            clip(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128),
            clip(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128))


def encode_samples(rgb, step):
    """An NV12 plane whose sampled pixels (every step-th each way) hold rgb; the rest is black."""
    nv = bytearray(meter.NV12_BYTES)
    rs, gs, bs = rgb
    k = 0
    for y in range(0, 360, step):
        for x in range(0, 640, step):
            yy, u, v = encode_pixel(rs[k], gs[k], bs[k])
            k += 1
            nv[y * 640 + x] = yy
            at = PLANE + (y // 2) * 640 + (x // 2) * 2
            nv[at], nv[at + 1] = u, v
    return bytes(nv)


def s_curve(k=1.3):
    return lambda y: 255 * y ** k / (y ** k + 1)


def s_inverse(k=1.3):
    return lambda z: ((z / 255) / (1 - z / 255)) ** (1 / k)


def test_method(**changes):
    base = meter.load_method()
    return meter.Method({**base.values, **changes}, (), base.file_sha256, base.block_sha256)


def render(energy, gains, x, curves, rng, noise):
    """Codes per channel for one frame: curve(x/8 · E · gain) plus noise, clipped."""
    out = ([], [], [])
    for i, e in enumerate(energy):
        curve = curves(i)
        for c in range(3):
            y = x / 8.0 * e * gains[c]
            out[c].append(min(255, max(0, round(curve(y) + rng.gauss(0, noise)))))
    return out


def synthetic_steps(method, *, curves, noise=0.7, frames_per_step=2, seed=5, t_pick=8000, iso=100):
    """Kept frames of a full sweep, the repeat last, for meter.check()."""
    rng = random.Random(seed)
    step = method["pixel_step"]
    n = len(meter.sample_points(step))
    energy = [math.exp(rng.uniform(math.log(0.05), math.log(10))) for _ in range(n)]
    steps, number = [], 0
    for hold in sweep.exposure_plan(t_pick, iso, method):
        x = hold["exposure_us"] / 1000 * hold["iso"] / 100
        frames = []
        for _ in range(frames_per_step):
            number += 1
            report = {"exposure_us": hold["exposure_us"] + 10, "iso": hold["iso"],
                      "kelvin": hold["white_balance_kelvin"]}
            rgb = render(energy, (1.0, 0.9, 0.75), x, curves, rng, noise)
            frames.append({"n": number, "report": report, "nv12": encode_samples(rgb, step)})
        steps.append({"hold": hold, "frames": frames})
    return steps


def npy(descr, shape, data):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (
        descr, "(" + ", ".join(str(s) for s in shape) + ("," if len(shape) == 1 else "") + ")")
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + data


ZERO_DEPTH, ZERO_MONO = bytes(640 * 400 * 2), bytes(640 * 400)


def bundle(meta, nv12):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("depth_mm.npy", npy("<u2", (400, 640), ZERO_DEPTH))
        archive.writestr("left.npy", npy("|u1", (400, 640), ZERO_MONO))
        archive.writestr("right.npy", npy("|u1", (400, 640), ZERO_MONO))
        archive.writestr("rgb_nv12.npy", npy("|u1", (540, 640), nv12))
        raw = json.dumps(meta).encode()
        archive.writestr("metadata_json.npy", npy("|u1", (len(raw),), raw))
    return out.getvalue()


# ---- a fake meter-build camera on 127.0.0.1 ------------------------------------

class FakeCamera:
    """Renders a synthetic den through a tone curve under auto or held settings."""

    def __init__(self, step=20, *, token=TOKEN, latency=2, seed=11):
        self.step, self.token, self.latency = step, token, latency
        self.rng = random.Random(seed)
        n = len(meter.sample_points(step))
        self.energy = [math.exp(self.rng.uniform(math.log(0.05), math.log(10))) for _ in range(n)]
        self.curve = s_curve()
        self.session, self.sequence, self.gets = "session-a", 0, 0
        self.auto = {"exposure_us": 8000, "iso": 100, "kelvin": 4600}
        self.applied = dict(self.auto)
        self.held, self.generation, self.hold, self.until_applied = False, 0, None, 0
        self.lock = threading.Lock()
        self.requests = []
        self.last = None
        self.scale = 1.0
        self.fail_get_at = {}       # GET number -> 503 or 'drop'
        self.session_change_at = None
        self.refuse_post_at = None  # the n-th POST /meter answers 400
        self.posts = 0
        self.flicker = False
        self.scale_after_posts = None  # (post count, scale): the scene changes
        self.health = None          # (status, body) for /health: a camera that answers unhealthy
        self.metered = True         # False: the installed server, whose bundles have no meter keys
        self.restart_each_health = False  # a crash-looping unit: every /health is a new session
        self.health_calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def frame(self):
        self.sequence += 1
        if self.until_applied:
            self.until_applied -= 1
            if not self.until_applied:
                self.applied = dict(self.hold) if self.held else dict(self.auto)
        a = self.applied
        x = a["exposure_us"] / 1000 * a["iso"] / 100
        scale = self.scale * (self.rng.uniform(0.5, 1.5) if self.flicker else 1.0)
        rgb = render([e * scale for e in self.energy], (1.0, 0.9, 0.75), x, lambda i: self.curve, self.rng, 0.5)
        rgb_meta = {"sequence": self.sequence, "host_monotonic_timestamp_seconds": 100 + self.sequence / 5,
                    "device_timestamp_seconds": 50 + self.sequence / 5, "width": 640, "height": 360,
                    "type": "Type.NV12", "exposure_duration_seconds": (a["exposure_us"] + 20) / 1e6}
        if self.metered:
            rgb_meta.update({"sensitivity_iso": a["iso"], "color_temperature_kelvin": a["kelvin"],
                             "lens_position": 120, "meter": {"held": self.held, "generation": self.generation}})
        other = {"sequence": self.sequence, "host_monotonic_timestamp_seconds": 100 + self.sequence / 5,
                 "device_timestamp_seconds": 50 + self.sequence / 5, "width": 640, "height": 400}
        meta = {"schema": 1, "session": self.session, "captured_unix_ns": 1,
                "frames": {"rgb": rgb_meta, "depth": dict(other), "left": dict(other), "right": dict(other)}}
        return bundle(meta, encode_samples(rgb, self.step))

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorised(self):
                return self.headers.get("Authorization") == "Bearer " + fake.token

            def do_GET(self):
                with fake.lock:
                    fake.requests.append(("GET", self.path, self.headers.get("Authorization") is not None))
                    if not self.authorised():
                        return self.reply(401, b"{}")
                    if self.path == "/health":
                        fake.health_calls += 1
                        if fake.restart_each_health:
                            fake.session = f"session-{fake.health_calls}"
                        if fake.health is not None:
                            status, body = fake.health
                            return self.reply(status, json.dumps(dict(body, session=fake.session)).encode())
                        return self.reply(200, json.dumps({"healthy": True, "session": fake.session}).encode())
                    if self.path != "/frame.npz":
                        return self.reply(404, b"{}")
                    fake.gets += 1
                    failure = fake.fail_get_at.get(fake.gets)
                    if failure == 503:
                        return self.reply(503, b"Unavailable\n", "text/plain")
                    if failure == "drop":
                        self.close_connection = True
                        return None
                    if fake.session_change_at and fake.gets >= fake.session_change_at:
                        fake.session = "session-b"
                    if fake.last is not None and fake.gets % 4 == 0:
                        body = fake.last  # the same bundle twice, as a latest-only server does
                    else:
                        body = fake.last = fake.frame()
                return self.reply(200, body, "application/octet-stream")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                with fake.lock:
                    fake.requests.append(("POST", self.path, self.headers.get("Authorization") is not None))
                    if not self.authorised():
                        return self.reply(401, b"{}")
                    if self.path == "/meter/release":
                        if fake.held:
                            fake.held, fake.generation = False, fake.generation + 1
                            fake.until_applied = fake.latency
                        return self.reply(200, json.dumps({"held": False, "generation": fake.generation}).encode())
                    fake.posts += 1
                    if fake.refuse_post_at and fake.posts >= fake.refuse_post_at:
                        return self.reply(400, b'{"error": "refused"}')
                    body = json.loads(raw)
                    hold = {"exposure_us": body["exposure_us"], "iso": body["iso"],
                            "kelvin": body["white_balance_kelvin"]}
                    if not fake.held or hold != fake.hold:
                        fake.held, fake.hold, fake.generation = True, hold, fake.generation + 1
                        fake.until_applied = fake.latency
                    if fake.scale_after_posts and fake.posts == fake.scale_after_posts[0]:
                        fake.scale = fake.scale_after_posts[1]
                    return self.reply(200, json.dumps({"held": True, "generation": fake.generation,
                                                       "ttl_s": body["ttl_s"]}).encode())

        return Handler


def sweep_method(**changes):
    values = dict(countdown_s=0, still_span_s=0.15, still_wait_limit_s=3, poll_s=0.005,
                  settle_limit_s=1.0, frames_per_step=2, run_limit_s=60, pixel_step=20,
                  fit_iterations=10, region_min_code_samples=3, region_tolerance_percent=10)
    values.update(changes)
    return test_method(**values)


@contextlib.contextmanager
def stage0_folder(install=True):
    """A Stage 0 folder with an install.json as --install-record writes, and a token file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage0 = root / "den-light" / "stage0-20261110"
        stage0.mkdir(parents=True)
        token = root / "private" / "camera-token.txt"
        token.parent.mkdir()
        token.write_text(TOKEN)
        if install:
            (stage0 / "install.json").write_text(json.dumps(
                {"schema": sweep.SCHEMAS["install"], "meter_sha256": sweep.METER_SERVER_SHA256}))
        yield stage0, token


def quiet(*_args, **_kwargs):
    pass


class SweepCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCamera()
        self.addCleanup(self.fake.close)

    def run_sweep(self, method=None, *, hook=None, cls=sweep.Sweep):
        method = method or sweep_method()
        with stage0_folder() as (stage0, token):
            camera = sweep.Camera(self.fake.url, TOKEN)
            _, folder = sweep.start_run(stage0, camera, token, method)
            run = cls(camera, method, folder, say=quiet)
            run.on_frame = hook
            response = run.run()
            on_disk = json.loads((folder / "response.json").read_text())
            files = sorted(p.name for p in folder.iterdir())
            frames = sorted(p.name for p in (folder / "frames").iterdir()) if (folder / "frames").exists() else None
        self.assertEqual(on_disk["outcome"], response["outcome"])
        return response, files, frames

    def assertVoid(self, result, reason):
        response, files, frames = result
        self.assertEqual((response["outcome"], response["void_reason"]), ("void", reason), response.get("detail"))
        self.assertEqual(files, ["response.json"], "a void deletes its frames and frames.jsonl")
        self.assertIsNone(frames)
        self.assertTrue(response["frames_deleted"])

    def released(self):
        posts = [path for method, path, _ in self.fake.requests if method == "POST"]
        return bool(posts) and posts[-1] == "/meter/release"


# ---- METHOD-V1 ------------------------------------------------------------------

class MethodTests(unittest.TestCase):
    def test_method_v1_loads_with_every_key(self):
        method = meter.load_method()
        self.assertEqual(set(method.values), set(meter.METHOD_KEYS))
        self.assertEqual(len(method.file_sha256), 64)
        self.assertTrue(set(method.open) <= set(meter.METHOD_KEYS))

    def test_numbers_the_plan_fixes(self):
        m = meter.load_method()
        fixed = dict(countdown_s=15, still_wait_limit_s=20, run_limit_s=100, hold_ttl_s=30, renew_every_s=10,
                     step_ratio_low=0.0625, step_ratio_high=2, step_count=11, grid_cols=4, grid_rows=4,
                     region_code_low=16, region_code_high=235, normalise_code=128)
        self.assertEqual({k: m[k] for k in fixed}, fixed)
        self.assertFalse(set(fixed) & set(m.open), "plan-fixed numbers are not open")

    def test_the_parser_refuses_bad_blocks(self):
        good = (HERE / meter.METHOD_FILE).read_text(encoding="utf-8")
        meter.parse_method(good)
        bad = [good.replace("pixel_step = 4", "pixel_step = 4\npixel_step = 4"),
               good.replace("pixel_step = 4", "pixel_step = four"),
               good.replace("pixel_step = 4", "pixel_step = 4.5"),
               good.replace("countdown_s = 15", ""),
               good.replace("countdown_s = 15", "countdown_s = 15\nextra_key = 1"),
               good + "\n```method-v1 stage0\ncountdown_s = 15\n```\n"]
        for text in bad:
            with self.assertRaises(ValueError):
                meter.parse_method(text)

    def test_open_marks_are_read(self):
        text = (HERE / meter.METHOD_FILE).read_text(encoding="utf-8")
        _, marked, _ = meter.parse_method(text.replace("pixel_step = 4                 # open:", "pixel_step = 4 #"))
        self.assertNotIn("pixel_step", marked)
        _, marked, _ = meter.parse_method(text)
        self.assertIn("pixel_step", marked)


# ---- NV12 and meter-norm/v1 ---------------------------------------------------------

class DecodeTests(unittest.TestCase):
    def test_nv12_decode_round_trips(self):
        rng = random.Random(1)
        rgb = tuple([rng.randrange(256) for _ in range(len(meter.sample_points(8)))] for _ in range(3))
        back = meter.decode(encode_samples(rgb, 8), 8)
        self.assertLessEqual(max(abs(a - b) for c in range(3) for a, b in zip(rgb[c], back[c])), 3)
        grey = tuple([v % 256 for v in range(len(meter.sample_points(8)))] for _ in range(3))
        back = meter.decode(encode_samples(grey, 8), 8)
        self.assertLessEqual(max(abs(a - b) for c in range(3) for a, b in zip(grey[c], back[c])), 1)

    def test_decode_is_the_servers_bt601_limited_range(self):
        nv = bytearray(meter.NV12_BYTES)
        for (x, y), (Y, U, V) in {(0, 0): (235, 128, 128), (2, 0): (16, 128, 128), (4, 0): (126, 90, 200)}.items():
            nv[y * 640 + x] = Y
            nv[PLANE + (y // 2) * 640 + (x // 2) * 2: PLANE + (y // 2) * 640 + (x // 2) * 2 + 2] = bytes((U, V))
        r, g, b = meter.decode(bytes(nv), 2)
        self.assertEqual([(r[i], g[i], b[i]) for i in range(3)], [(255, 255, 255), (0, 0, 0), (243, 84, 51)])

    def test_regions_and_agreement(self):
        regions = meter.sample_regions(4, 4, 4)
        self.assertEqual(len(regions), 160 * 90)
        self.assertEqual(sorted(set(regions)), list(range(16)))
        flat = ([100] * len(regions),) * 3
        means = meter.region_means(flat, regions, 16)
        shifted = meter.region_means(([102] * len(regions),) * 3, regions, 16)
        self.assertTrue(meter.agree(means, shifted, 2))
        self.assertFalse(meter.agree(means, shifted, 1.9))

    def test_meter_norm_value(self):
        params, digest = meter.meter_norm_parameters(test_method(), {c: [z / 128 for z in range(256)] for c in "rgb"})
        self.assertEqual(len(digest), 64)
        one = meter.meter_norm_value(128, 128, 128, 10.0, 1.0, params)
        self.assertAlmostEqual(one, 0.1)
        self.assertAlmostEqual(meter.meter_norm_value(128, 128, 128, 20.0, 1.0, params), one / 2)
        self.assertAlmostEqual(meter.meter_norm_value(128, 128, 128, 10.0, 2.0, params), one / 2)
        self.assertIsNone(meter.meter_norm_value(250, 10, 10, 10.0, 1.0, params), "any channel at its limit flags")
        self.assertIsNone(meter.meter_norm_value(10, 10, 251, 10.0, 1.0, params))


# ---- bundles ----------------------------------------------------------------------

class BundleTests(unittest.TestCase):
    def test_bundle_report(self):
        fake = FakeCamera()
        self.addCleanup(fake.close)
        meta, nv12 = meter.read_bundle(fake.frame())
        report = meter.rgb_report(meta)
        self.assertEqual(len(nv12), meter.NV12_BYTES)
        self.assertEqual((report["iso"], report["kelvin"], report["held"], report["meter_keys"]),
                         (100, 4600, False, True))
        self.assertAlmostEqual(report["exposure_us"], 8020)

    def test_refuses_bad_bundles(self):
        with self.assertRaises(meter.BundleError):
            meter.read_bundle(b"not a zip")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("metadata_json.npy", npy("|u1", (2,), b"{}"))
            archive.writestr("rgb_nv12.npy", npy("<f8", (1,), bytes(8)))
        with self.assertRaises(meter.BundleError):
            meter.read_bundle(out.getvalue())


# ---- the response fit and the global check ----------------------------------------------

class FitTests(unittest.TestCase):
    def samples_for(self, curve, n=2000, noise=0.7, seed=1):
        rng = random.Random(seed)
        energy = [math.exp(rng.uniform(math.log(0.03), math.log(20))) for _ in range(n)]
        samples = meter.Samples(n)
        for k in range(11):
            x = 2 ** (-4 + 0.5 * k)
            for _ in range(3):
                codes = [min(255, max(0, round(curve(x * e) + rng.gauss(0, noise)))) for e in energy]
                samples.add_frame((codes, codes, codes), x, (250, 250, 250))
        return samples

    def test_recovers_a_known_s_curve_within_one_percent(self):
        method = test_method()
        samples = self.samples_for(s_curve(1.3))
        fit = meter.fit_response(samples, 0, method)
        truth = s_inverse(1.3)
        errors = [abs(fit.curve[z] / (truth(z) / truth(128)) - 1) for z in range(16, 236)
                  if fit.counts[z] >= method["region_min_code_samples"]]
        self.assertGreater(len(errors), 200)
        self.assertLess(max(errors), 0.01)
        self.assertEqual(fit.curve[128], 1.0)
        self.assertEqual(fit.curve, sorted(fit.curve), "monotone")

    def test_residual_matches_a_hand_worked_rms(self):
        samples = meter.Samples(2)
        samples.pix.extend([0, 0, 1])
        samples.xs.extend([1.0, 2.0, 1.0])
        for c in range(3):
            samples.zs[c].extend([64, 130, 10])
        fit = meter.Fit([z / 128 for z in range(256)], [0.5, 0.1], [0] * 256)
        # F(y) = 128·y: predictions 64, 128 and 12.8, errors 0, -2 and 2.8.
        self.assertAlmostEqual(meter.residual_rms(fit, samples, 0), math.sqrt((0 + 4 + 7.84) / 3))
        # The hat-weighted E-step: (64·1·0.5 + 125·2·130/128) / (64·1 + 125·4).
        energy = meter.irradiances([z / 128 for z in range(256)], samples, 0)
        self.assertAlmostEqual(energy[0], (32 + 250 * 130 / 128) / 564)
        self.assertAlmostEqual(energy[1], 10 / 128)

    def test_a_pixel_is_dropped_only_in_the_frames_where_it_clips(self):
        samples = meter.Samples(2)
        for r0 in (100, 252, 100):
            samples.add_frame(([r0, 50], [90, 50], [80, 50]), 1.0, (250, 250, 250))
        self.assertEqual(list(samples.pix), [0, 1, 1, 0, 1])
        self.assertEqual(list(samples.zs[1]), [90, 50, 50, 90, 50], "the clipped frame drops every channel")
        self.assertEqual(samples.flagged, 1)

    def test_gain_agreement(self):
        hold = {"exposure_us": 4000, "iso": 200, "white_balance_kelvin": 4000}
        q = 50
        good = {"exposure_us": 4050, "iso": 200, "kelvin": 4000}
        self.assertEqual(meter.gain_agreement(good, hold, q), (True, None))
        self.assertTrue(meter.gain_agreement(dict(good, exposure_us=3950), hold, q)[0])
        self.assertFalse(meter.gain_agreement(dict(good, exposure_us=4100), hold, q)[0])
        self.assertFalse(meter.gain_agreement(dict(good, exposure_us=3900), hold, q)[0])
        self.assertFalse(meter.gain_agreement(dict(good, iso=201), hold, q)[0])
        self.assertFalse(meter.gain_agreement(dict(good, kelvin=3999), hold, q)[0])
        for missing in ("exposure_us", "iso", "kelvin"):
            self.assertFalse(meter.gain_agreement(dict(good, **{missing: None}), hold, q)[0])

    def test_a_region_curve_at_another_scale_passes_once_scaled(self):
        method = test_method()
        truth = s_inverse(1.3)
        global_curve = [truth(z) / truth(128) if z < 255 else 1e9 for z in range(256)]
        region = meter.Fit([1.7 * v for v in global_curve], [], [100] * 256)
        codes, scale, deviation = meter.compare_region(region, global_curve, method)
        self.assertEqual(codes, 220)
        self.assertAlmostEqual(scale, 1 / 1.7)
        self.assertLess(deviation, 1e-9)
        shaped = meter.Fit([v * (1 + 0.2 * z / 255) for z, v in enumerate(global_curve)], [], [100] * 256)
        self.assertGreater(meter.compare_region(shaped, global_curve, method)[2], 0.05)
        sparse = meter.Fit(global_curve, [], [method["region_min_code_samples"] - 1] * 256)
        self.assertIsNone(meter.compare_region(sparse, global_curve, method), "no supported code: untested")

    def test_a_local_tone_map_fails_the_region_test_and_one_global_curve_passes(self):
        method = test_method(pixel_step=10, fit_iterations=20)
        columns = 640 // 10
        global_steps = synthetic_steps(method, curves=lambda i: s_curve(1.3))
        result = meter.check(global_steps, method)
        self.assertEqual(result["verdict"], "pass", result["reasons"])
        self.assertTrue(result["region_test"]["pass"])
        self.assertTrue(result["residual"]["pass"])
        # Left half of the view through another tone curve: a spatially varying (local) tone map.
        local = synthetic_steps(method, curves=lambda i: s_curve(0.8) if i % columns < columns // 2 else s_curve(1.3))
        result = meter.check(local, method)
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("region test", result["reasons"])
        failing = {e["region"] % 4 for e in result["region_test"]["regions"] if not e["pass"]}
        self.assertTrue(failing, "some region fails")

    def test_a_dark_region_is_untested_and_does_not_fail(self):
        method = test_method(pixel_step=10, fit_iterations=15)
        columns = 640 // 10
        dark = lambda y: min(10.0, s_curve(1.3)(y))  # never reaches code 16
        steps = synthetic_steps(method, curves=lambda i: dark if (i // columns) < 9 and i % columns < 16 else s_curve(1.3))
        result = meter.check(steps, method)
        untested = [e for e in result["region_test"]["regions"] if not e["tested"]]
        self.assertTrue(any(e["region"] == 0 for e in untested))
        self.assertTrue(all(e["pass"] for e in untested))

    def test_held_frames_off_the_hold_fail_the_check(self):
        method = test_method(pixel_step=20, fit_iterations=8, region_min_code_samples=3)
        steps = synthetic_steps(method, curves=lambda i: s_curve(1.3))
        steps[4]["frames"][1]["report"]["iso"] += 1
        result = meter.check(steps, method)
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("gain agreement", result["reasons"])

    def test_a_degenerate_fit_fails(self):
        method = test_method(pixel_step=40, fit_iterations=3)
        steps = synthetic_steps(method, curves=lambda i: (lambda y: 0.0), noise=0)
        result = meter.check(steps, method)
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("degenerate" in r for r in result["reasons"]))


# ---- the meter build of the camera server -----------------------------------------------------

class FakeControl:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name,) + args)


class FakeDai:
    class CameraControl(FakeControl):
        class AutoWhiteBalanceMode:
            AUTO = "AWB_AUTO"

        class AutoFocusMode:
            CONTINUOUS_VIDEO = "AF_CONTINUOUS_VIDEO"

    class CameraBoardSocket:
        CAM_A = "CAM_A"


class FakeFrame:
    def __init__(self, iso=100, kelvin=4600, lens=120):
        self.values = {"getSensitivity": iso, "getColorTemperature": kelvin, "getLensPosition": lens}

    def __getattr__(self, name):
        if name not in self.values:
            raise AttributeError(name)
        value = self.values[name]
        if isinstance(value, Exception):
            def fail():
                raise value
            return fail
        return lambda: value


HOLD = {"exposure_us": 4000, "iso": 200, "white_balance_kelvin": 4000, "ttl_s": 30}
RESTORE_AF = [("setAutoExposureEnable",), ("setAutoWhiteBalanceMode", "AWB_AUTO"),
              ("setAutoFocusMode", "AF_CONTINUOUS_VIDEO")]


class MeterServerTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.meter = SRV.Meter(clock=lambda: self.now[0])
        self.sent = []
        self.meter.attach(lambda spec: self.sent.append(SRV.meter_control(FakeDai, spec).calls), True)
        self.meter.annotate({}, FakeFrame(lens=120))

    def post(self, body, path="/meter"):
        return self.meter.request(path, json.dumps(body).encode() if not isinstance(body, bytes) else body)

    def test_the_file_is_the_installed_fixture_outside_its_meter_blocks(self):
        kept, inside, blocks = [], False, 0
        for line in SERVER.read_text().splitlines(keepends=True):
            mark = line.strip()
            if mark == "# >>> meter":
                self.assertFalse(inside, "blocks do not nest")
                inside, blocks = True, blocks + 1
                continue
            if mark == "# <<< meter":
                self.assertTrue(inside)
                inside = False
                continue
            if not inside:
                kept.append(line)
        self.assertFalse(inside)
        self.assertGreaterEqual(blocks, 8)
        stripped = "".join(kept).encode()
        self.assertEqual(stripped, FIXTURE.read_bytes())
        self.assertEqual(hashlib.sha256(stripped).hexdigest(), sweep.FIXTURE_SHA256)

    def test_sweep_pins_the_committed_server(self):
        self.assertEqual(meter.file_sha256(SERVER), sweep.METER_SERVER_SHA256)

    def test_request_checks(self):
        cases = [b"not json", b'{"exposure_us": 1, "exposure_us": 2, "iso": 100, "white_balance_kelvin": 4000, "ttl_s": 5}',
                 {k: v for k, v in HOLD.items() if k != "iso"}, dict(HOLD, extra=1),
                 dict(HOLD, exposure_us=True), dict(HOLD, iso=200.0), dict(HOLD, exposure_us=0),
                 dict(HOLD, exposure_us=33001), dict(HOLD, iso=99), dict(HOLD, iso=1601),
                 dict(HOLD, white_balance_kelvin=999), dict(HOLD, white_balance_kelvin=12001),
                 dict(HOLD, ttl_s=0), dict(HOLD, ttl_s=121), dict(HOLD, ttl_s=True), [1, 2]]
        for body in cases:
            self.assertEqual(self.post(body)[0], 400, body)
        self.assertEqual(self.post(HOLD, "/other")[0], 404)
        self.assertEqual(self.sent, [])
        self.meter.tick()
        self.assertEqual(self.sent, [], "a refused request sends nothing")

    def test_no_hold_without_a_running_camera_or_a_known_focus(self):
        idle = SRV.Meter(clock=lambda: self.now[0])
        self.assertEqual(idle.request("/meter", json.dumps(HOLD).encode())[0], 503)
        idle.attach(lambda spec: None, None)
        self.assertEqual(idle.request("/meter", json.dumps(HOLD).encode())[0], 503, "focus capability unknown")
        idle.attach(lambda spec: None, True)
        self.assertEqual(idle.request("/meter", json.dumps(HOLD).encode())[0], 503, "no lens position yet")

    def test_hold_sends_exactly_exposure_white_balance_and_lens(self):
        status, reply = self.post(HOLD)
        self.assertEqual((status, reply["held"], reply["generation"], reply["lens_position"]), (200, True, 1, 120))
        self.meter.tick()
        self.assertEqual(self.sent, [[("setManualExposure", 4000, 200), ("setManualWhiteBalance", 4000),
                                      ("setManualFocus", 120)]])
        fixed = SRV.Meter(clock=lambda: self.now[0])
        sent = []
        fixed.attach(lambda spec: sent.append(SRV.meter_control(FakeDai, spec).calls), False)
        fixed.request("/meter", json.dumps(HOLD).encode())
        fixed.tick()
        self.assertEqual(sent, [[("setManualExposure", 4000, 200), ("setManualWhiteBalance", 4000)]])

    def test_renewal_keeps_the_generation_and_extends_the_hold(self):
        self.post(HOLD)
        self.meter.tick()
        self.now[0] += 25
        status, reply = self.post(HOLD)
        self.assertEqual((status, reply["generation"]), (200, 1))
        self.now[0] += 25  # 50 s after the first POST, 25 s after the renewal
        self.meter.tick()
        self.assertEqual(len(self.sent), 1, "renewal sends no control and the hold has not expired")
        meta = {}
        self.meter.annotate(meta, FakeFrame())
        self.assertEqual(meta["meter"], {"held": True, "generation": 1})

    def test_a_new_setting_is_a_new_generation_with_the_same_lens(self):
        self.post(HOLD)
        self.meter.tick()
        self.meter.annotate({}, FakeFrame(lens=90))  # reported while held: ignored
        status, reply = self.post(dict(HOLD, exposure_us=8000))
        self.assertEqual((status, reply["generation"], reply["lens_position"]), (200, 2, 120))
        self.meter.tick()
        self.assertEqual(self.sent[-1], [("setManualExposure", 8000, 200), ("setManualWhiteBalance", 4000),
                                         ("setManualFocus", 120)])

    def test_expiry_restores_exactly_the_installed_modes(self):
        self.post(dict(HOLD, ttl_s=30))
        self.meter.tick()
        self.now[0] += 29.9
        self.meter.tick()
        self.assertEqual(len(self.sent), 1)
        self.now[0] += 0.1
        self.meter.tick()
        self.assertEqual(self.sent[-1], RESTORE_AF)
        meta = {}
        self.meter.annotate(meta, FakeFrame())
        self.assertEqual(meta["meter"], {"held": False, "generation": 2})

    def test_release_restores_exactly_the_installed_modes(self):
        self.post(HOLD)
        self.meter.tick()
        self.assertEqual(self.post({}, "/meter/release"), (200, {"held": False, "generation": 2}))
        self.meter.tick()
        self.assertEqual(self.sent[-1], RESTORE_AF)
        self.post({}, "/meter/release")
        self.meter.tick()
        self.assertEqual(len(self.sent), 2, "releasing an unheld camera sends nothing")
        fixed = SRV.Meter(clock=lambda: self.now[0])
        sent = []
        fixed.attach(lambda spec: sent.append(SRV.meter_control(FakeDai, spec).calls), False)
        fixed.request("/meter", json.dumps(HOLD).encode())
        fixed.request("/meter/release", b"")
        fixed.tick()
        self.assertEqual(sent[-1], RESTORE_AF[:2], "no focus control on a fixed-focus module")

    def test_a_restart_starts_unheld_and_sends_nothing(self):
        self.post(HOLD)
        self.meter.reset()  # a new capture session (or a new process, whose Meter is new)
        self.meter.attach(lambda spec: self.sent.append(SRV.meter_control(FakeDai, spec).calls), True)
        self.meter.tick()
        self.assertEqual(self.sent, [], "the hold was never sent and nothing replaces DepthAI's defaults")
        meta = {}
        self.meter.annotate(meta, FakeFrame())
        self.assertFalse(meta["meter"]["held"])
        fresh = SRV.Meter()
        self.assertIsNone(fresh.held)

    def test_frames_report_gain_white_balance_and_lens_inside_frames_rgb_only(self):
        meta = {"frames": {name: {} for name in ("rgb", "depth", "left", "right")}}
        self.meter.annotate(meta["frames"]["rgb"], FakeFrame(iso=320, kelvin=4100, lens=77))
        self.assertEqual(set(meta), {"frames"})
        self.assertEqual(set(meta["frames"]), {"rgb", "depth", "left", "right"})
        self.assertEqual(meta["frames"]["rgb"], {"sensitivity_iso": 320, "color_temperature_kelvin": 4100,
                                                 "lens_position": 77, "meter": {"held": False, "generation": 0}})
        broken = {}
        self.meter.annotate(broken, FakeFrame(iso=RuntimeError("no"), kelvin=1.5))
        self.assertEqual((broken["sensitivity_iso"], broken["color_temperature_kelvin"]), (None, None))

    def test_a_failed_send_raises_so_the_session_restarts(self):
        meter_ = SRV.Meter()
        meter_.attach(lambda spec: (_ for _ in ()).throw(RuntimeError("usb")), False)
        meter_.request("/meter", json.dumps(HOLD).encode())
        with self.assertRaises(RuntimeError):
            meter_.tick()

    def test_the_token_rule_over_http(self):
        def serve(token):
            state = SRV.State(15)
            state.meter.attach(lambda spec: None, False)
            server = SRV.serve(state, "127.0.0.1", 0, token)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            return server.server_address[1]

        def call(port, method, path, body=None, auth=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Authorization": auth} if auth else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            connection.close()
            return response.status, data

        body = json.dumps(HOLD).encode()
        open_port = serve("")  # loopback with no token configured: GET works, POST never does
        self.assertEqual(call(open_port, "GET", "/health")[0], 503)
        self.assertEqual(call(open_port, "POST", "/meter", body)[0], 401)
        self.assertEqual(call(open_port, "POST", "/meter", body, "Bearer ")[0], 401)
        port = serve(TOKEN)
        self.assertEqual(call(port, "POST", "/meter", body)[0], 401)
        self.assertEqual(call(port, "POST", "/meter", body, "Bearer " + "x" * 32)[0], 401)
        self.assertEqual(call(port, "POST", "/meter", body, "Bearer é")[0], 401)
        status, data = call(port, "POST", "/meter", body, "Bearer " + TOKEN)
        self.assertEqual((status, json.loads(data)["held"]), (200, True))
        self.assertEqual(call(port, "POST", "/meter", b"x" * 2000, "Bearer " + TOKEN)[0], 413)
        status, data = call(port, "POST", "/meter/release", b"", "Bearer " + TOKEN)
        self.assertEqual((status, json.loads(data)["held"]), (200, False))


# ---- the install lines -----------------------------------------------------------------

PINNED = "/home/owner/victoria-camera-pilot/pilot-20260926/capture.py"
METER_PATH = "/home/owner/victoria-camera-pilot/pilot-20261110-meter/oak-capture-meter.py"
FLAGS = "--bind 0.0.0.0 --port 8090 --bundle-fps 5"


def unit_lines(script=PINNED, flags=FLAGS, drop_ins="", active=None, hashes=None, ignore="no", path=None):
    python = path or "/home/owner/victoria-camera/venv/bin/python"
    lines = ["FragmentPath=/etc/systemd/system/oak-den.service", "DropInPaths=" + drop_ins,
             f"ExecStart={{ path={python} ; argv[]=/home/owner/victoria-camera/venv/bin/python {script} {flags} "
             f"; ignore_errors={ignore} ; start_time=[Thu 2026-11-05 20:01:02 PST] ; stop_time=[n/a] ; pid=812 ; "
             "code=(null) ; status=0/0 }"]
    if active:
        lines.append("ActiveState=" + active)
    for name, digest in ({PINNED: sweep.FIXTURE_SHA256} if hashes is None else hashes).items():
        lines.append(f"{digest}  {name}")
    return "﻿" + "\r\n".join(lines) + "\r\n"  # as PowerShell 5.1's Set-Content -Encoding utf8 writes


class InstallCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCamera()
        self.addCleanup(self.fake.close)
        self.camera = sweep.Camera(self.fake.url, TOKEN)

    @contextlib.contextmanager
    def prepared(self, before=None):
        with stage0_folder(install=False) as (stage0, token):
            (stage0 / "before.txt").write_text(before or unit_lines(), encoding="utf-8")
            sweep.prepare(stage0, "20261110", token)
            yield stage0, token

    def after(self, stage0, **changes):
        values = dict(script=METER_PATH, drop_ins=sweep.DROP_IN_PATH, active="active",
                      hashes={PINNED: sweep.FIXTURE_SHA256, METER_PATH: sweep.METER_SERVER_SHA256})
        values.update(changes)
        (stage0 / "after.txt").write_text(unit_lines(**values), encoding="utf-8")


class InstallTests(InstallCase):
    def test_prepare_writes_a_drop_in_that_keeps_every_flag(self):
        with self.prepared() as (stage0, _):
            conf = (stage0 / sweep.DROP_IN_NAME).read_text()
            record = json.loads((stage0 / "prepare.json").read_text())
        self.assertIn("[Service]\nExecStart=\nExecStart=/home/owner/victoria-camera/venv/bin/python "
                      f"{METER_PATH} {FLAGS}\n", conf)
        self.assertEqual(record["meter_path"], METER_PATH)
        self.assertEqual(record["pinned_path"], PINNED)
        self.assertNotIn(PINNED, conf.split("ExecStart=")[-1], "the drop-in no longer runs the pinned file")

    def test_prepare_refuses_a_fixture_mismatch(self):
        other = "/home/owner/victoria-camera/oak_capture.py"
        cases = {"wrong hash": unit_lines(hashes={PINNED: "a" * 64}),
                 "not the pinned file": unit_lines(script=other, hashes={other: sweep.FIXTURE_SHA256}),
                 "two scripts": unit_lines(flags=FLAGS + " /tmp/extra.py"),
                 "prefix": unit_lines(ignore="yes"),
                 "unsafe": unit_lines(flags=FLAGS + " --name=%i"),
                 "already installed": unit_lines(drop_ins=sweep.DROP_IN_PATH),
                 "no hash": unit_lines(hashes={})}
        for name, text in cases.items():
            with stage0_folder(install=False) as (stage0, token):
                (stage0 / "before.txt").write_text(text, encoding="utf-8")
                with self.assertRaises(sweep.Refused, msg=name):
                    sweep.prepare(stage0, "20261110", token)
                self.assertEqual(sorted(p.name for p in stage0.iterdir()), ["before.txt"], name)
        with stage0_folder(install=False) as (stage0, token):
            (stage0 / "before.txt").write_text(unit_lines(), encoding="utf-8")
            with self.assertRaises(sweep.Refused):
                sweep.prepare(stage0, "2026-11-10", token)

    def test_install_record_writes_install_json(self):
        with self.prepared() as (stage0, token):
            self.after(stage0)
            record = sweep.install_record(stage0, self.camera, token, meter.load_method())
            on_disk = json.loads((stage0 / "install.json").read_text())
        self.assertEqual(on_disk["meter_sha256"], sweep.METER_SERVER_SHA256)
        self.assertEqual((record["pinned_sha256_before"], record["pinned_sha256_after"]),
                         (sweep.FIXTURE_SHA256, sweep.FIXTURE_SHA256))
        self.assertEqual(record["camera"]["held"], False)
        self.assertIn(FLAGS, " ".join(record["after"]["argv"]))

    def test_install_record_refuses_anything_off(self):
        cases = {"changed flags": dict(flags=FLAGS + " --stale-seconds 10"),
                 "extra drop-in": dict(drop_ins=sweep.DROP_IN_PATH + " /etc/systemd/system/oak-den.service.d/af.conf"),
                 "pinned hash changed": dict(hashes={PINNED: "b" * 64, METER_PATH: sweep.METER_SERVER_SHA256}),
                 "wrong meter hash": dict(hashes={PINNED: sweep.FIXTURE_SHA256, METER_PATH: "c" * 64}),
                 "not active": dict(active="failed"),
                 "still the pinned script": dict(script=PINNED)}
        for name, change in cases.items():
            with self.prepared() as (stage0, token):
                self.after(stage0, **change)
                with self.assertRaises(sweep.Refused, msg=name):
                    sweep.install_record(stage0, self.camera, token, meter.load_method())
                self.assertFalse((stage0 / "install.json").exists(), name)

    def test_install_record_refuses_a_held_or_unmetered_camera(self):
        self.fake.held = True
        with self.prepared() as (stage0, token):
            self.after(stage0)
            with self.assertRaises(sweep.Refused):
                sweep.install_record(stage0, self.camera, token, meter.load_method())
            self.assertFalse((stage0 / "install.json").exists())

    def test_rollback_check(self):
        self.fake.metered = False  # the installed server again
        check = lambda stage0, token: sweep.rollback_check(stage0, self.camera, token, health_wait_s=0, settle_s=0.01)
        with self.prepared() as (stage0, token):
            (stage0 / "rollback.txt").write_text(unit_lines(active="active"), encoding="utf-8")
            check(stage0, token)
            self.assertTrue((stage0 / "rollback.json").exists())
        for change in (dict(drop_ins=sweep.DROP_IN_PATH), dict(script=METER_PATH, hashes={
                PINNED: sweep.FIXTURE_SHA256}), dict(hashes={PINNED: "d" * 64}), dict(active="activating")):
            with self.prepared() as (stage0, token):
                (stage0 / "rollback.txt").write_text(unit_lines(**dict(dict(active="active"), **change)),
                                                     encoding="utf-8")
                with self.assertRaises(sweep.Refused):
                    check(stage0, token)
                self.assertFalse((stage0 / "rollback.json").exists())


# ---- run folders -----------------------------------------------------------------------------

class RunFolderTests(unittest.TestCase):
    def check_refused(self, stage0, token):
        before = sorted(p.name for p in stage0.rglob("*"))
        (stage0 / "before.txt").write_text(unit_lines(), encoding="utf-8")
        with self.assertRaises(sweep.Refused):
            sweep.prepare(stage0, "20261110", token)
        (stage0 / "before.txt").unlink()
        self.assertEqual(sorted(p.name for p in stage0.rglob("*")), before, "a refusal writes nothing")

    def test_refuses_a_folder_inside_a_git_checkout(self):
        with stage0_folder(install=False) as (stage0, token):
            (stage0.parent / ".git").mkdir()
            self.check_refused(stage0, token)

    def test_refuses_a_folder_inside_onedrive(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage0 = Path(tmp) / "OneDrive - Home" / "stage0-20261110"
            stage0.mkdir(parents=True)
            token = Path(tmp) / "token.txt"
            token.write_text(TOKEN)
            self.check_refused(stage0, token)
        with stage0_folder(install=False) as (stage0, token), mock.patch.dict(os.environ, {"OneDrive": str(stage0.parent)}):
            self.check_refused(stage0, token)

    def test_refuses_a_folder_that_already_holds_a_sweep(self):
        with stage0_folder(install=False) as (stage0, token):
            (stage0 / "sweep-1").mkdir()
            (stage0 / "sweep-1" / "response.json").write_text("{}")
            self.check_refused(stage0, token)
            with self.assertRaises(sweep.Refused):
                sweep.make_sweep_folder(stage0 / "sweep-1")
            self.assertEqual(sorted(p.name for p in (stage0 / "sweep-1").iterdir()), ["response.json"])
            self.assertEqual(sweep.new_sweep_folder(stage0).name, "sweep-2", "a run takes the next folder")

    def test_refuses_low_disk(self):
        with stage0_folder(install=False) as (stage0, token), mock.patch.object(sweep, "free_bytes", return_value=10 ** 8):
            self.check_refused(stage0, token)

    def test_refuses_a_relative_folder_or_one_holding_the_token(self):
        with stage0_folder(install=False) as (stage0, token):
            with self.assertRaises(sweep.Refused):
                sweep.run_folder(Path("stage0"), token, may_hold_sweeps=False)
            with self.assertRaises(sweep.Refused):
                sweep.run_folder(token.parent.parent, token, may_hold_sweeps=False)


# ---- the sweep --------------------------------------------------------------------------------

class ExposurePlanTests(unittest.TestCase):
    def test_the_exposure_list(self):
        method = test_method()
        plan = sweep.exposure_plan(8000, 200, method)
        self.assertEqual(len(plan), 12)
        self.assertEqual(plan[-1], plan[0], "the repeat holds the first exposure again")
        self.assertEqual([p["exposure_us"] for p in plan[:11]],
                         [round(8000 * 2 ** (-4 + k / 2)) for k in range(11)])
        self.assertEqual({p["iso"] for p in plan}, {200}, "time changes, not gain")
        self.assertEqual({p["white_balance_kelvin"] for p in plan}, {method["white_balance_kelvin"]})
        ratios = [b["exposure_us"] / a["exposure_us"] for a, b in zip(plan, plan[1:11])]
        self.assertTrue(all(abs(r - 2 ** 0.5) < 0.01 for r in ratios))

    def test_time_stops_at_the_limit_and_gain_takes_over(self):
        method = test_method()
        plan = sweep.exposure_plan(30000, 100, method)
        self.assertEqual([p["exposure_us"] for p in plan[9:11]], [33000, 33000])
        self.assertEqual([p["iso"] for p in plan[9:11]], [round(100 * 2 ** 0.5 * 30000 / 33000),
                                                          round(100 * 2 * 30000 / 33000)])
        with self.assertRaises(sweep.OutOfRange) as caught:
            sweep.exposure_plan(33000, 1200, method)
        self.assertTrue(caught.exception.dark)
        with self.assertRaises(sweep.OutOfRange) as caught:
            sweep.exposure_plan(5, 100, method)
        self.assertFalse(caught.exception.dark)


class SweepTests(SweepCase):
    def test_a_full_sweep_passes_releases_and_calls_only_the_camera(self):
        hosts = []
        original = http.client.HTTPConnection.__init__

        def record(self_, host, port=None, *args, **kwargs):
            hosts.append((host, port))
            original(self_, host, port, *args, **kwargs)

        with mock.patch.object(http.client.HTTPConnection, "__init__", record):
            response, files, frames = self.run_sweep(sweep_method(renew_every_s=0.004))
        self.assertEqual(response["outcome"], "pass", response.get("check", {}).get("reasons"))
        self.assertEqual(set(hosts), {("127.0.0.1", self.fake.port)}, "no host but the fake camera")
        self.assertTrue(self.released())
        self.assertEqual(files, ["frames", "frames.jsonl", "response.json"])
        self.assertEqual(len(frames), 24)
        self.assertEqual(len(response["steps"]), 12)
        self.assertEqual(response["release"]["released"], True)
        self.assertIn("parameters_sha256", response["meter_norm"])
        self.assertTrue(all(auth for _, _, auth in self.fake.requests), "every request carries the token")
        posts = [p for m, p, _ in self.fake.requests if m == "POST" and p == "/meter"]
        self.assertGreater(len(posts), 12, "renewals re-POST the held settings")
        self.assertEqual(self.fake.generation, 13, "12 holds and one release; renewals keep the generation")
        source = (HERE / "sweep.py").read_text() + (HERE / "meter.py").read_text()
        self.assertNotIn("/api/", source)
        self.assertNotIn("8123", source)

    def test_settling_skips_frames_until_the_hold_applies(self):
        response, _, _ = self.run_sweep()
        gains = response["check"]["gain_agreement"]
        self.assertTrue(gains["pass"])
        self.assertEqual(len(gains["frames"]), 24)

    def test_release_on_error_and_ctrl_c(self):
        self.fake.refuse_post_at = 3
        self.run_sweep()
        self.assertTrue(self.released())

        def ctrl_c(step, n):
            if step == 4:
                raise KeyboardInterrupt
        self.fake.refuse_post_at, self.fake.requests = None, []
        self.fake.last = None  # no replay of a bundle from the earlier sweep's hold
        self.run_sweep(hook=ctrl_c)
        self.assertTrue(self.released())

    def test_void_repeat_mismatch(self):
        self.fake.scale_after_posts = (8, 1.6)
        self.assertVoid(self.run_sweep(), "repeat_mismatch")
        self.assertTrue(self.released())

    def test_void_still_scene_wait_over_limit(self):
        self.fake.flicker = True
        self.assertVoid(self.run_sweep(sweep_method(still_wait_limit_s=0.5)), "still_scene_wait_over_limit")

    def test_void_on_a_503(self):
        self.fake.fail_get_at = {30: 503}
        self.assertVoid(self.run_sweep(), "camera_503")
        self.assertTrue(self.released())

    def test_void_on_an_unreachable_camera(self):
        self.fake.fail_get_at = {30: "drop"}
        self.assertVoid(self.run_sweep(), "camera_unreachable")

    def test_void_on_a_new_camera_session(self):
        self.fake.session_change_at = 30
        self.assertVoid(self.run_sweep(), "new_camera_session")

    def test_void_on_a_run_over_the_run_limit(self):
        self.assertVoid(self.run_sweep(sweep_method(run_limit_s=0.4)), "run_over_limit")
        self.assertTrue(self.released())

    def test_void_on_ctrl_c_before_the_repeat(self):
        def ctrl_c(step, n):
            if step == 6:
                signal.raise_signal(signal.SIGINT)
        result = self.run_sweep(hook=ctrl_c)
        self.assertVoid(result, "interrupted_or_errored_before_repeat")
        self.assertIn("Ctrl+C", result[0]["detail"])
        self.assertTrue(self.released())

    def test_void_on_a_refused_post_before_the_repeat(self):
        self.fake.refuse_post_at = 5
        result = self.run_sweep()
        self.assertVoid(result, "interrupted_or_errored_before_repeat")
        self.assertIn("POST /meter was refused", result[0]["detail"])

    def test_ctrl_c_after_the_repeat_does_not_void(self):
        class LateCtrlC(sweep.Sweep):
            def do_release(self):
                signal.raise_signal(signal.SIGINT)  # ignored: the data is complete
                return super().do_release()

        response, files, _ = self.run_sweep(cls=LateCtrlC)
        self.assertEqual(response["outcome"], "pass")
        self.assertIn("frames.jsonl", files)
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler, "the handler comes back")

    def test_refusals_before_any_sweep_folder(self):
        camera = sweep.Camera(self.fake.url, TOKEN)
        with stage0_folder(install=False) as (stage0, token):
            with self.assertRaises(sweep.Refused):
                sweep.start_run(stage0, camera, token, sweep_method())
            (stage0 / "install.json").write_text(json.dumps({"schema": sweep.SCHEMAS["install"],
                                                             "meter_sha256": "e" * 64}))
            with self.assertRaises(sweep.Refused):
                sweep.start_run(stage0, camera, token, sweep_method())
            self.assertEqual([p.name for p in stage0.iterdir()], ["install.json"])
        with stage0_folder() as (stage0, token):
            with self.assertRaises(sweep.Refused) as caught:
                sweep.start_run(stage0, camera, token, meter.load_method().replace(countdown_s=0))
            self.assertIn("not confirmed", str(caught.exception))
            self.fake.held = True
            with self.assertRaises(sweep.Refused):
                sweep.start_run(stage0, camera, token, sweep_method())
            self.assertEqual([p.name for p in stage0.iterdir()], ["install.json"])

    def test_main_exit_codes(self):
        with stage0_folder() as (stage0, token):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(sweep.main(["--run", str(stage0), "--camera", "ftp://x",
                                             "--token-file", str(token)]), 2)
                self.assertEqual(sweep.main(["--rollback-check", str(stage0), "--token-file", str(token)]), 2)


# ---- review fixes: each test failed on the code before its fix ---------------------------------

def closed_port():
    """A 127.0.0.1 port with nothing listening: connections are refused."""
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


UNHEALTHY = (503, {"healthy": False, "bundle_age_seconds": 41.0, "error": "Starting camera"})


class CameraReadyTests(InstallCase):
    """Fix 1: a camera that answers unhealthy is not reported as a network problem."""

    def test_an_unhealthy_answer_is_not_called_unreachable(self):
        self.fake.health = UNHEALTHY
        with self.assertRaises(sweep.Refused) as caught:
            sweep.camera_ready(self.camera)
        text = str(caught.exception)
        self.assertNotIn("ssh tunnel", text)
        self.assertNotIn("cannot reach", text)
        self.assertIn("503", text)
        self.assertIn("unhealthy", text)
        self.assertIn("Starting camera", text, "the camera's own error is shown")

    def test_a_failed_connection_gives_the_tunnel_advice(self):
        camera = sweep.Camera(f"http://127.0.0.1:{closed_port()}", TOKEN)
        with self.assertRaises(sweep.Refused) as caught:
            sweep.camera_ready(camera, drop_in_installed=True)
        text = str(caught.exception)
        self.assertIn("ssh tunnel", text)
        self.assertNotIn("rollback", text.lower())

    def test_after_the_install_an_unhealthy_camera_says_roll_back_now(self):
        self.fake.health = UNHEALTHY
        with self.prepared() as (stage0, token):
            self.after(stage0)
            with self.assertRaises(sweep.Refused) as caught:
                sweep.install_record(stage0, self.camera, token, meter.load_method(), health_wait_s=0)
            self.assertFalse((stage0 / "install.json").exists())
        text = str(caught.exception)
        self.assertIn("503", text)
        self.assertIn("run the rollback now", text)
        self.assertNotIn("ssh tunnel", text)

    def test_main_prints_the_rollback_advice_not_the_tunnel(self):
        self.fake.health = UNHEALTHY
        with self.prepared() as (stage0, token):
            self.after(stage0)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), mock.patch.object(sweep, "INSTALL_HEALTH_WAIT_S", 0):
                code = sweep.main(["--install-record", str(stage0), "--camera", self.fake.url,
                                   "--token-file", str(token)])
        self.assertEqual(code, 2)
        self.assertIn("run the rollback now", err.getvalue())
        self.assertNotIn("ssh tunnel", err.getvalue())

    def test_a_sweep_start_on_an_unhealthy_meter_build_says_roll_back(self):
        self.fake.health = UNHEALTHY
        with stage0_folder() as (stage0, token):
            with self.assertRaises(sweep.Refused) as caught:
                sweep.start_run(stage0, self.camera, token, sweep_method())
            self.assertEqual([p.name for p in stage0.iterdir()], ["install.json"])
        self.assertIn("run the rollback now", str(caught.exception))


class RollbackCheckTests(InstallCase):
    """Fix 2: the rollback check asks the camera, after waiting past the restart."""

    def rolled_back(self, stage0, **change):
        (stage0 / "rollback.txt").write_text(unit_lines(active="active", **change), encoding="utf-8")

    def check(self, stage0, token):
        return sweep.rollback_check(stage0, self.camera, token, health_wait_s=0, settle_s=0.05)

    def test_a_clean_rollback_passes_and_records_the_camera(self):
        self.fake.metered = False  # the installed server again
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            record = self.check(stage0, token)
            on_disk = json.loads((stage0 / "rollback.json").read_text())
        self.assertEqual(on_disk["camera"]["session"], "session-a")
        self.assertFalse(record["camera"]["meter_keys"])
        self.assertGreaterEqual(self.fake.health_calls, 2, "/health is read again after the wait")

    def test_refuses_a_crash_looping_unit_that_still_reads_active(self):
        self.fake.metered = False
        self.fake.restart_each_health = True  # a new camera session at every look
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            with self.assertRaises(sweep.Refused) as caught:
                self.check(stage0, token)
            self.assertFalse((stage0 / "rollback.json").exists())
        self.assertIn("restarted", str(caught.exception))

    def test_a_camera_that_stops_answering_during_the_wait_is_not_called_a_network_problem(self):
        self.fake.metered = False
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            with self.assertRaises(sweep.Refused) as caught:
                sweep.rollback_check(stage0, self.camera, token, health_wait_s=0, settle_s=1,
                                     sleep=lambda _: self.fake.close())  # the unit goes down in the wait
            self.assertFalse((stage0 / "rollback.json").exists())
        self.assertIn("stopped answering", str(caught.exception))
        self.assertNotIn("ssh tunnel", str(caught.exception))

    def test_refuses_a_camera_that_does_not_answer(self):
        self.fake.metered = False
        self.camera = sweep.Camera(f"http://127.0.0.1:{closed_port()}", TOKEN)
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            with self.assertRaises(sweep.Refused):
                self.check(stage0, token)
            self.assertFalse((stage0 / "rollback.json").exists())

    def test_refuses_an_unhealthy_camera(self):
        self.fake.metered = False
        self.fake.health = UNHEALTHY
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            with self.assertRaises(sweep.Refused) as caught:
                self.check(stage0, token)
            self.assertFalse((stage0 / "rollback.json").exists())
        self.assertIn("503", str(caught.exception))

    def test_refuses_bundles_that_still_carry_meter_keys(self):
        with self.prepared() as (stage0, token):  # the fake is still the meter build
            self.rolled_back(stage0)
            with self.assertRaises(sweep.Refused) as caught:
                self.check(stage0, token)
            self.assertFalse((stage0 / "rollback.json").exists())
        self.assertIn("meter", str(caught.exception))

    def test_the_rollback_check_waits_past_the_restart(self):
        self.fake.metered = False
        slept = []
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            sweep.rollback_check(stage0, self.camera, token, health_wait_s=0, settle_s=7.0,
                                 sleep=slept.append)
        self.assertIn(7.0, slept)

    def test_main_needs_the_camera_for_the_rollback_check(self):
        self.fake.metered = False
        with self.prepared() as (stage0, token):
            self.rolled_back(stage0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(sweep.main(["--rollback-check", str(stage0), "--token-file", str(token)]), 2)
            self.assertFalse((stage0 / "rollback.json").exists())


class TooDarkTests(SweepCase):
    """Fix 4: a dim den is refused before the hold, not voided."""

    def test_the_rule_follows_the_camera_limits(self):
        method = sweep_method()
        self.assertIsNone(sweep.pick_problem(33000, 800, method), "ISO 800 at 33 ms just fits")
        problem = sweep.pick_problem(33000, 801, method)
        self.assertTrue(problem.startswith("too dark: "), problem)
        self.assertIn("Turn on one more den bulb", problem)
        self.assertIsNone(sweep.pick_problem(8000, 100, method))

    def test_start_refuses_a_dark_den_and_writes_nothing(self):
        self.fake.auto = self.fake.applied = {"exposure_us": 33000, "iso": 1000, "kelvin": 3000}
        with stage0_folder() as (stage0, token):
            camera = sweep.Camera(self.fake.url, TOKEN)
            with self.assertRaises(sweep.Refused) as caught:
                sweep.start_run(stage0, camera, token, sweep_method())
            self.assertEqual([p.name for p in stage0.iterdir()], ["install.json"])
        self.assertTrue(str(caught.exception).startswith("too dark: "), str(caught.exception))
        self.assertFalse([p for m, p, _ in self.fake.requests if m == "POST"], "no hold was taken")

    def test_a_den_that_darkens_after_the_start_is_refused_before_the_hold(self):
        with stage0_folder() as (stage0, token):
            camera = sweep.Camera(self.fake.url, TOKEN)
            _, folder = sweep.start_run(stage0, camera, token, sweep_method())
            self.fake.auto = self.fake.applied = {"exposure_us": 33000, "iso": 1200, "kelvin": 3000}
            with self.assertRaises(sweep.Refused) as caught:
                sweep.Sweep(camera, sweep_method(), folder, say=quiet).run()
            self.assertEqual([p.name for p in stage0.iterdir()], ["install.json"], "the empty sweep folder is removed")
        self.assertTrue(str(caught.exception).startswith("too dark: "))
        self.assertFalse([p for m, p, _ in self.fake.requests if m == "POST"], "no hold was taken")


class SecondCtrlCTests(SweepCase):
    """Fix 5: a second Ctrl+C during the release or the cleanup keeps the record and deletes the frames."""

    def guarded(self, **kwargs):
        try:
            return self.run_sweep(**kwargs)
        except KeyboardInterrupt:
            self.fail("a Ctrl+C escaped the sweep before response.json was written")

    def test_second_ctrl_c_during_the_release(self):
        class CtrlCInRelease(sweep.Sweep):
            def do_release(self):
                signal.raise_signal(signal.SIGINT)
                return super().do_release()

        def ctrl_c(step, n):
            if step == 3:
                signal.raise_signal(signal.SIGINT)
        result = self.guarded(hook=ctrl_c, cls=CtrlCInRelease)
        self.assertVoid(result, "interrupted_or_errored_before_repeat")
        self.assertTrue(self.released())
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_ctrl_c_during_the_release_after_an_error(self):
        class CtrlCInRelease(sweep.Sweep):
            def do_release(self):
                signal.raise_signal(signal.SIGINT)
                return super().do_release()
        self.fake.refuse_post_at = 4
        result = self.guarded(cls=CtrlCInRelease)
        self.assertVoid(result, "interrupted_or_errored_before_repeat")
        self.assertIn("POST /meter was refused", result[0]["detail"])
        self.assertTrue(self.released())

    def test_second_ctrl_c_during_the_void_cleanup(self):
        real = shutil.rmtree

        def interrupted_rmtree(path, *args, **kwargs):
            if Path(path).name == "frames":  # the sweep's cleanup, not the test's temporary folder
                signal.raise_signal(signal.SIGINT)
            return real(path, *args, **kwargs)

        def ctrl_c(step, n):
            if step == 2:
                signal.raise_signal(signal.SIGINT)
        with mock.patch.object(sweep.shutil, "rmtree", interrupted_rmtree):
            result = self.guarded(hook=ctrl_c)
        self.assertVoid(result, "interrupted_or_errored_before_repeat")

    def test_an_error_after_the_data_is_complete_is_recorded_and_keeps_the_frames(self):
        with mock.patch.object(sweep.meter, "check", side_effect=RuntimeError("fit crashed")):
            response, files, frames = self.run_sweep()
        self.assertEqual(response["outcome"], "error")
        self.assertIn("fit crashed", response["detail"])
        self.assertEqual(files, ["frames", "frames.jsonl", "response.json"], "--recheck can finish it")
        self.assertEqual(len(frames), 24)
        self.assertTrue(self.released())


class FramesDeletedTests(SweepCase):
    """Fix 6: frames_deleted says whether frames\\ and frames.jsonl are really gone."""

    def test_a_failed_rmtree_is_not_reported_as_deleted(self):
        self.fake.refuse_post_at = 4
        with mock.patch.object(sweep.shutil, "rmtree", lambda *a, **k: None):
            response, files, frames = self.run_sweep()
        self.assertEqual(response["outcome"], "void")
        self.assertFalse(response["frames_deleted"])
        self.assertIn("frames", response["frames_left"])
        self.assertIn("frames", files)

    def test_a_failed_unlink_is_recorded_not_raised(self):
        self.fake.refuse_post_at = 4
        real = Path.unlink

        def locked(path, *args, **kwargs):
            if path.name == "frames.jsonl":
                raise PermissionError("in use")
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", locked):
            response, files, frames = self.run_sweep()
        self.assertEqual(response["outcome"], "void")
        self.assertFalse(response["frames_deleted"])
        self.assertEqual(response["frames_left"], ["frames.jsonl"])
        self.assertIsNone(frames, "frames\\ itself went")


class RecheckTests(SweepCase):
    """--recheck recomputes a saved sweep's verdict from its folder."""

    def swept(self, method, **patches):
        stage = contextlib.ExitStack()
        stage0, token = stage.enter_context(stage0_folder())
        camera = sweep.Camera(self.fake.url, TOKEN)
        _, folder = sweep.start_run(stage0, camera, token, method)
        with contextlib.ExitStack() as during:
            for target, value in patches.items():
                during.enter_context(mock.patch.object(sweep.meter, target, value))
            response = sweep.Sweep(camera, method, folder, say=quiet).run()
        return stage, folder, response

    def test_recheck_matches_the_saved_verdict_and_never_overwrites(self):
        method = sweep_method()
        stage, folder, response = self.swept(method)
        with stage:
            saved = (folder / "response.json").read_bytes()
            record, path = sweep.recheck(folder, method)
            second, path2 = sweep.recheck(folder, method)
            self.assertEqual((path.name, path2.name), ("recheck-1.json", "recheck-2.json"))
            self.assertEqual((folder / "response.json").read_bytes(), saved)
            on_disk = json.loads(path.read_text())
        self.assertEqual(response["outcome"], "pass")
        self.assertEqual((record["outcome"], record["same_as_saved"], record["method_matches_sweep"]),
                         ("pass", True, True))
        self.assertEqual(on_disk["meter_norm"]["parameters_sha256"], response["meter_norm"]["parameters_sha256"])
        self.assertEqual(record["frames"], 24)

    def test_recheck_finishes_a_sweep_whose_fit_errored(self):
        method = sweep_method()
        stage, folder, response = self.swept(method, check=mock.Mock(side_effect=RuntimeError("bug")))
        with stage:
            record, _ = sweep.recheck(folder, method)
        self.assertEqual(response["outcome"], "error")
        self.assertEqual((record["outcome"], record["saved_outcome"], record["same_as_saved"]), ("pass", "error", False))

    def test_recheck_refuses_a_changed_frame_a_void_or_open_numbers(self):
        method = sweep_method()
        stage, folder, _ = self.swept(method)
        with stage:
            frame = sorted((folder / "frames").iterdir())[3]
            frame.write_bytes(b"\0" + frame.read_bytes()[1:])
            with self.assertRaises(sweep.Refused):
                sweep.recheck(folder, method)
            with self.assertRaises(sweep.Refused):
                sweep.recheck(folder, meter.Method(method.values, ("pixel_step",)))  # a number still open
            self.assertFalse(list(folder.glob("recheck-*.json")))
        self.fake.refuse_post_at, self.fake.last = 4, None
        stage, folder, response = self.swept(method)
        with stage:
            self.assertEqual(response["outcome"], "void")
            with self.assertRaises(sweep.Refused):
                sweep.recheck(folder, method)

    def test_main_recheck(self):
        method = sweep_method()
        stage, folder, _ = self.swept(method)
        with stage, mock.patch.object(sweep.meter, "load_method", return_value=method):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sweep.main(["--recheck", str(folder)]), 0)
        self.assertIn("RECHECK PASS", out.getvalue())


class MeterRangeTests(unittest.TestCase):
    def test_method_numbers_fit_the_servers_hard_coded_ranges(self):
        """oak-capture-meter.py's METER_RANGES are hard-coded; raising a METHOD limit needs a rebuild."""
        m = meter.load_method()
        self.assertEqual(SRV.METER_RANGES, {"exposure_us": (1, 33000), "iso": (100, 1600),
                                            "white_balance_kelvin": (1000, 12000)})
        self.assertLessEqual(m["max_exposure_us"], SRV.METER_RANGES["exposure_us"][1])
        self.assertLessEqual(m["iso_max"], SRV.METER_RANGES["iso"][1])
        low, high = SRV.METER_RANGES["white_balance_kelvin"]
        self.assertTrue(low <= m["white_balance_kelvin"] <= high)
        text = (HERE / meter.METHOD_FILE).read_text(encoding="utf-8")
        self.assertIn("METER_RANGES", text, "METHOD-V1 says the server's ranges are hard-coded")


if __name__ == "__main__":
    unittest.main()
