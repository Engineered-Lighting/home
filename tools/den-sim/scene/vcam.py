"""Hop 2: a virtual camera serving rendered frames in the installed camera's shapes, on 127.0.0.1 only.

The camera does not exist. It serves GET /health, /calibration and /frame.npz the way the installed
camera does, behind a bearer token read from a file, so the real take.py records it unchanged. Its
camera_id is den-virtual and every session starts `virtual-`. The metadata is the same for every
scene; only the four planes differ. Frames come from render.mjs's output for one name:

- rgb_nv12: the RGB render, rows flipped, BT.601 limited-range NV12 (the inverse of the Lab's
  nv12_to_rgb), plus seeded sensor noise on Y, the frame index in eight pixels of the last row, and
  the slate patch while one is due. A blank picture replaces only the render here: left, right and
  depth are the same scene's, so a dark beam on blank is the picture alone.
- left and right: the left and right renders, rows flipped, grey.
- depth_mm: the left camera's eye-space depth, rows flipped, millimetres, 0 where nothing was hit.

Frame index = camera time since start (wall time times speed) on gaps of 0.2 s or 4/15 s; a fixed
0.25 s grid failed the slate fit. The pose holds for POSE_FRAMES frames, so the eight poses cycle
and two looks seconds apart are two pictures. Every frame differs, because identical frames stop a
take as stale_frame.
"""
import hmac
import io
import json
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

CAMERA_ID = "den-virtual"
GAPS_S = (0.2, 4 / 15)
GAP_ODDS = (0.6, 0.4)
POSE_FRAMES = 8
NOISE_FIELDS = 8
NOISE_SIGMA = 1.5
DEPTH_SKEW_S = 0.004          # t_depth = t_rgb - 4 ms
DEVICE_OFFSET_S = 3000.0      # t_device = t_host - 3000 s
SLATE_BOX = (slice(120, 200), slice(240, 320))
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def k_matrix(k):
    return [[k["fx"], 0.0, k["cx"]], [0.0, k["fy"], k["cy"]], [0.0, 0.0, 1.0]]


def rgb_to_nv12(rgb):
    """BT.601 limited range, 4:2:0 by 2x2 means. Copied unchanged from the probe the model test used."""
    r, g, b = [rgb[:, :, i].astype(np.float32) for i in range(3)]
    y = 16 + 0.257 * r + 0.504 * g + 0.098 * b
    u = 128 - 0.148 * r - 0.291 * g + 0.439 * b
    v = 128 + 0.439 * r - 0.368 * g - 0.071 * b
    h, w = y.shape
    su = u.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    sv = v.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    uv = np.empty((h // 2, w), np.uint8)
    uv[:, 0::2] = np.clip(su + 0.5, 0, 255).astype(np.uint8)
    uv[:, 1::2] = np.clip(sv + 0.5, 0, 255).astype(np.uint8)
    return np.clip(y + 0.5, 0, 255).astype(np.uint8), uv


def load_pose(render_dir, stem, camera, blank=False):
    """One pose's four planes from render.mjs's files <stem>.rgb.rgba, .left.rgba, .right.rgba, .depth.f32."""
    (rw, rh), (w, h) = camera["rgb_size"], camera["depth_size"]
    folder = Path(render_dir)

    def rgba(kind, width, height):
        return np.frombuffer((folder / f"{stem}.{kind}.rgba").read_bytes(), np.uint8).reshape(height, width, 4)[::-1, :, :3]
    rgb = rgba("rgb", rw, rh)
    y, uv = rgb_to_nv12(np.zeros_like(rgb) if blank else rgb)
    grey = lambda a: np.asarray(Image.fromarray(np.ascontiguousarray(a)).convert("L"), np.uint8)
    z = np.frombuffer((folder / f"{stem}.depth.f32").read_bytes(), np.float32).reshape(h, w)[::-1]
    depth = np.clip(np.round(z * 1000.0), 0, 65535).astype(np.uint16)
    return {"y": y, "uv": uv, "left": grey(rgba("left", w, h)), "right": grey(rgba("right", w, h)), "depth": np.ascontiguousarray(depth)}


def calibration_reply(session, camera):
    """The /calibration shape den-register's rig.eeprom_geometry reads: left to right to RGB, in centimetres."""
    baseline_cm, rgb_cm = camera["baseline_m"] * 100, camera["rgb_offset_m"] * 100
    return {"session": session, "camera_id": CAMERA_ID, "depthai": "virtual-camera", "device_id": CAMERA_ID,
            "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
            "stereo_size": camera["depth_size"], "rgb_preview_size": camera["rgb_size"],
            "eeprom": {"stereoRectificationData": {"leftCameraSocket": 1, "rightCameraSocket": 2, "rectifiedRotationLeft": IDENTITY},
                       "cameraData": [[0, {"extrinsics": {"toCameraSocket": -1}}],
                                      [1, {"extrinsics": {"rotationMatrix": IDENTITY, "toCameraSocket": 2,
                                                          "translation": {"x": -baseline_cm, "y": 0.0, "z": 0.0}}}],
                                      [2, {"extrinsics": {"rotationMatrix": IDENTITY, "toCameraSocket": 0,
                                                          "translation": {"x": baseline_cm - rgb_cm, "y": 0.0, "z": 0.0}}}]]},
            "note": "VIRTUAL CAMERA: invented geometry, not an EEPROM"}


def pose_of(index, poses):
    return (index // POSE_FRAMES) % poses


class VirtualCamera:
    """Serves one render name's poses. pattern_lit and slate_tau draw the phone slate while `showing`."""

    def __init__(self, render_dir, name, poses, camera, *, token_file, picture="rendered", speed=1.0, seed=1,
                 pattern_lit=None, slate_tau=None):
        if picture not in ("rendered", "blank"):
            raise ValueError("picture is rendered or blank")
        self.poses = [load_pose(render_dir, f"{name}.p{k}", camera, blank=picture == "blank") for k in range(poses)]
        self.camera, self.picture, self.speed = camera, picture, speed
        self.pattern_lit, self.slate_tau = pattern_lit, slate_tau
        self.token = Path(token_file).read_text(encoding="ascii").strip()
        rng = np.random.default_rng(seed)
        shape = self.poses[0]["y"].shape
        self.noise = [rng.normal(0.0, NOISE_SIGMA, shape).astype(np.float32) for _ in range(NOISE_FIELDS)]
        self.times = np.concatenate(([0.0], np.cumsum(rng.choice(GAPS_S, size=20000, p=GAP_ODDS))))
        self.session = "virtual-" + uuid.uuid4().hex
        self.showing = False
        self.start = self.t0 = time.monotonic()
        self.cache, self.lock = {}, threading.Lock()
        self.served = set()
        camera_ = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + camera_.token):
                    self.send_error(401)
                    return
                status, kind, body = camera_.response(self.path)
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def screen_value(self, tau, exposure=0.008, lit=220, dark=20, samples=8):
        frac = sum(self.pattern_lit(tau - exposure / 2 + exposure * (i + 0.5) / samples) for i in range(samples)) / samples
        return round(dark + (lit - dark) * frac)

    def metadata(self, index):
        t_host = self.t0 + float(self.times[index])
        t_depth = t_host - DEPTH_SKEW_S
        rgb_k, left_k = k_matrix(self.camera["rgb_k"]), k_matrix(self.camera["left_k"])
        (rw, rh), (w, h) = self.camera["rgb_size"], self.camera["depth_size"]

        def plane(seq, t, width, height, kind, k):
            return {"sequence": seq, "device_timestamp_seconds": t - DEVICE_OFFSET_S, "host_monotonic_timestamp_seconds": t,
                    "width": width, "height": height, "type": kind, "exposure_duration_seconds": 0.008,
                    "transformation_valid": True, "intrinsics": k, "source_to_output_matrix": IDENTITY,
                    "source_size": [width, height], "source_distortion_coefficients": [0.0] * 14}
        return {"schema": 1, "session": self.session, "captured_unix_ns": time.time_ns(),
                "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure",
                "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
                "frames": {"rgb": plane(500 + index, t_host, rw, rh, "Type.NV12", rgb_k),
                           "depth": plane(900 + index, t_depth, w, h, "Type.RAW16", left_k),
                           "left": plane(900 + index, t_depth, w, h, "Type.RAW8", left_k),
                           "right": plane(900 + index, t_depth, w, h, "Type.RAW8", left_k)},
                "synchronization": {"method": "device_timestamp_group", "hardware_synchronized": False, "threshold_ms": 20,
                                    "measured_skew_ms": DEPTH_SKEW_S * 1000, "within_threshold": True}}

    def bundle(self, index, showing):
        pose = self.poses[pose_of(index, len(self.poses))]
        y = np.clip(pose["y"].astype(np.float32) + self.noise[index % NOISE_FIELDS], 0, 255).astype(np.uint8)
        y[-1, :8] = np.frombuffer(int(index).to_bytes(8, "little"), np.uint8)   # every frame's bytes differ
        if showing:
            y[SLATE_BOX] = self.screen_value(self.slate_tau(self.t0 + float(self.times[index])))
        arrays = {"depth_mm": pose["depth"], "left": pose["left"], "right": pose["right"],
                  "rgb_nv12": np.concatenate((y, pose["uv"]), axis=0),
                  "metadata_json": np.frombuffer(json.dumps(self.metadata(index), separators=(",", ":")).encode(), np.uint8)}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name, array in arrays.items():
                with io.BytesIO() as member:
                    np.lib.format.write_array(member, array, allow_pickle=False)   # numpy's own .npy writer, as np.savez does
                    archive.writestr(name + ".npy", member.getvalue())
        self.served.add(index)
        return output.getvalue()

    def response(self, path):
        if path == "/health":
            return 200, "application/json", json.dumps({"healthy": True, "session": self.session, "camera_id": CAMERA_ID,
                                                        "timing_generation": 1, "bundle_age_seconds": 0.1,
                                                        "encoder_age_seconds": 0.03, "error": None}).encode()
        if path == "/calibration":
            return 200, "application/json", json.dumps(calibration_reply(self.session, self.camera)).encode()
        if path == "/frame.npz":
            index = int(np.searchsorted(self.times, (time.monotonic() - self.start) * self.speed, side="right") - 1)
            key = (index, self.showing)
            with self.lock:
                if key not in self.cache:
                    self.cache.clear()
                    self.cache[key] = self.bundle(index, self.showing)
                return 200, "application/octet-stream", self.cache[key]
        return 404, "text/plain", b"Unavailable\n"
