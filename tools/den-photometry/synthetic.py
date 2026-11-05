"""Synthetic data and a fake meter-build camera for den-photometry's tests. 127.0.0.1 only.

A scene is grey rectangles of known meter-norm/v1 value (nc) on a grey
background. Under the linear test curves f(z) = z / 128, a pixel of value C
held at X = t_ms · ISO/100 has code z = round(128 · C · X), so the tool's
reading of a box comes back as C up to code rounding (exactly, when 128·C·X is
a whole number).
"""
import io
import json
import struct
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import card  # noqa: F401  puts tools/den-light on the path
import meter
import sweep

TOKEN = "t" * 32
AUTO_KELVIN = 4600
PLANE = meter.WIDTH * meter.HEIGHT


def den_method(**changes):
    """Rung 8's Stage 0 block with fast timing for tests and no open marks."""
    base = meter.load_method()
    values = dict(countdown_s=0, still_span_s=0.05, still_wait_limit_s=3, poll_s=0.005, settle_limit_s=1.0,
                  frames_per_step=2, run_limit_s=60)
    values.update(changes)
    return meter.Method({**base.values, **values}, (), base.file_sha256, base.block_sha256)


def photometry_method(**changes):
    """METHOD-V1 with its open lines closed at test values (the owner has not chosen these)."""
    values = dict(commissioning_option="card-and-meter", k_L_scope="single", meter_tolerance_lx=10,
                  meter_class="test-meter")
    values.update(changes)
    return card.load_method().closed(**values)


def linear_parameters(method=None):
    curves = {c: [z / 128 for z in range(256)] for c in meter.CHANNELS}
    return meter.meter_norm_parameters(method or den_method(), curves)


def stage0_response(method=None, **changes):
    method = method or den_method()
    parameters, digest = linear_parameters(method)
    response = {"schema": sweep.SCHEMAS["response"], "outcome": "pass",
                "method_stage0_block_sha256": method.block_sha256,
                "meter_norm": {"version": meter.METER_NORM_VERSION, "parameters": parameters,
                               "parameters_sha256": digest}}
    response.update(changes)
    return response


def code(value_nc, x, pedestal=0):
    return max(0, min(255, round(128 * value_nc * x) + pedestal))


def grey_y(z):
    """The BT.601 limited-range Y of grey (z, z, z); U and V are 128."""
    return ((220 * z + 128) >> 8) + 16


def nv12(scene, x, pedestal=0):
    """A 640x360 NV12 plane of the scene at exposure X (ms · ISO/100)."""
    background = bytes([grey_y(code(scene["background"], x, pedestal))])
    rects = [(box, bytes([grey_y(code(value, x, pedestal))])) for box, value in scene.get("rects", ())]
    out = bytearray()
    for y in range(meter.HEIGHT):
        row = bytearray(background * meter.WIDTH)
        for (x0, y0, x1, y1), luma in rects:
            if y0 <= y <= y1:
                row[x0:x1 + 1] = luma * (x1 - x0 + 1)
        out += row
    out += bytes([128]) * (PLANE // 2)
    return bytes(out)


def npy(descr, shape, data):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (
        descr, "(" + ", ".join(str(s) for s in shape) + ("," if len(shape) == 1 else "") + ")")
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + data


def bundle(meta, plane):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("rgb_nv12.npy", npy("|u1", (540, 640), plane))
        raw = json.dumps(meta).encode()
        archive.writestr("metadata_json.npy", npy("|u1", (len(raw),), raw))
    return out.getvalue()


def frame_report(exposure_us, iso, kelvin=4000):
    return {"exposure_us": float(exposure_us), "iso": iso, "kelvin": kelvin}


def held_frames(scene, hold, count=2, pedestal=0):
    """Decoded frames of one hold, as photometry.measure gives them to card.box_hold."""
    x = hold["exposure_us"] / 1000 * hold["iso"] / 100
    plane = nv12(scene, x, pedestal)
    rgb = meter.decode(plane, 1)
    report = frame_report(hold["exposure_us"], hold["iso"], hold["white_balance_kelvin"])
    return [{"report": dict(report), "rgb": rgb} for _ in range(count)]


class FakeCamera:
    """A meter-build camera on 127.0.0.1 showing a scene under auto or held settings."""

    def __init__(self, scene, *, auto=(10, 100), token=TOKEN, latency=1):
        self.scene, self.auto, self.token, self.latency = scene, auto, token, latency
        self.session, self.sequence = "session-a", 0
        self.held, self.generation, self.applied, self.pending = False, 0, None, None
        self.pedestal = 0
        self.scene_after_posts = None  # (POST count, scene): the light changes mid-capture
        self.posts = 0
        self.requests = []
        self.lock = threading.Lock()
        self.cache = {}
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

    def settings(self):
        if self.applied is not None:
            return self.applied
        return {"exposure_us": self.auto[0], "iso": self.auto[1], "kelvin": AUTO_KELVIN}

    def frame(self):
        self.sequence += 1
        if self.pending is not None:
            hold, left = self.pending
            self.pending = (hold, left - 1) if left > 1 else None
            if left <= 1:
                self.applied = hold
        a = self.settings()
        x = a["exposure_us"] / 1000 * a["iso"] / 100
        key = (id(self.scene), x, self.pedestal)
        if key not in self.cache:
            self.cache[key] = nv12(self.scene, x, self.pedestal)
        rgb = {"sequence": self.sequence, "host_monotonic_timestamp_seconds": 100 + self.sequence / 5,
               "width": 640, "height": 360, "type": "Type.NV12",
               "exposure_duration_seconds": a["exposure_us"] / 1e6, "sensitivity_iso": a["iso"],
               "color_temperature_kelvin": a["kelvin"], "lens_position": 120,
               "meter": {"held": self.held, "generation": self.generation}}
        other = {"sequence": self.sequence, "host_monotonic_timestamp_seconds": 100 + self.sequence / 5}
        meta = {"schema": 1, "session": self.session, "captured_unix_ns": 1,
                "frames": {"rgb": rgb, "depth": dict(other), "left": dict(other), "right": dict(other)}}
        return bundle(meta, self.cache[key])

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
                    fake.requests.append(("GET", self.path))
                    if not self.authorised():
                        return self.reply(401, b"{}")
                    if self.path == "/health":
                        return self.reply(200, json.dumps({"healthy": True, "session": fake.session}).encode())
                    if self.path != "/frame.npz":
                        return self.reply(404, b"{}")
                    return self.reply(200, fake.frame(), "application/octet-stream")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with fake.lock:
                    fake.requests.append(("POST", self.path))
                    if not self.authorised():
                        return self.reply(401, b"{}")
                    if self.path == "/meter/release":
                        if fake.held:
                            fake.held, fake.generation = False, fake.generation + 1
                            fake.pending = (None, fake.latency)
                        return self.reply(200, json.dumps({"held": False, "generation": fake.generation}).encode())
                    if self.path != "/meter":
                        return self.reply(404, b"{}")
                    fake.posts += 1
                    body = json.loads(raw)
                    hold = {"exposure_us": body["exposure_us"], "iso": body["iso"],
                            "kelvin": body["white_balance_kelvin"]}
                    target = fake.pending[0] if fake.pending is not None else fake.applied
                    if not fake.held or hold != target:
                        fake.held, fake.generation = True, fake.generation + 1
                        fake.pending = (hold, fake.latency)
                    if fake.scene_after_posts and fake.posts == fake.scene_after_posts[0]:
                        fake.scene = fake.scene_after_posts[1]
                    return self.reply(200, json.dumps({"held": True, "generation": fake.generation,
                                                       "ttl_s": body["ttl_s"]}).encode())

        return Handler
