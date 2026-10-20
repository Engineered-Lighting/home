"""Per-session V-JEPA 2.1 clip worker for the Lab (LA host, loopback only).

Launched only by vjepa_launcher.Launcher as the transient, resource-bounded
unit lab-vjepa-worker.service, with the probe venv's interpreter
(~/vjepa-probe-0/.venv/bin/python -I -B). Importing this module loads nothing:
no torch, no weights, no GPU.

Startup (main): exact host and dedicated cgroup (RAM/swap/tasks/CPU caps and a
RuntimeMaxSec that matches the lease), a private operator config, the probe
tree's source digests, the pinned head/NTU/encoder digests, then a GPU
admission check (free VRAM and temperature) immediately before any weights
load. The probe's own loader (v3_finetune.load_trainable_encoder) re-verifies
the Meta source records and the 1.66 GB checkpoint digest before loading it.
After import, every module loaded from the probe or Meta roots must be one of
the pinned files.

Service: a single-threaded stdlib HTTPS server on 127.0.0.1:9448 (the probe
venv has no uvicorn, and nothing is installed into it). Two fixed routes, both
behind the supervisor's execution token: /v1/specialist/lookup answers the
readiness probe; /v1/specialist/execute runs one clip. HTTP/1.1 keep-alive for
the supervisor's one pooled loopback connection (5 s idle timeout); it stays
single-threaded because Engine.previous and the request count are not
thread-safe, so a second connection simply waits for the first to close. Frames are decoded in
memory and never written; the only state kept between clips is the previous
clip's mean-pooled embedding (for scene drift), in memory. The process exits
at the lease expiry, after its request budget, or when the supervisor stops
the unit; systemd's RuntimeMaxSec is the backstop.
"""
import base64
import hashlib
import hmac
import http.server
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import threading
import time

# Launched with -I (safe_path): the script directory is not on sys.path, so the
# staged sibling pins module is added explicitly and nothing else is.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vjepa_pins as pins  # noqa: E402

UNIT = "lab-vjepa-worker.service"
BASE = Path("/home/marcelo-lima/home-lab-vjepa")
HOST, PORT = "127.0.0.1", 9448
# Exactly lifecycle_launcher.READY: the reply that proves the exact profile is loaded.
READINESS_REPLY = {"error": "request_unavailable_or_indeterminate", "profile_admitted": True}
MAX_BODY = 6 * 1024**2
MAX_REQUESTS = 400
CONFIG_KEYS = {"schema_version", "engine_epoch", "expires_at", "runtime_seconds", "maximum_requests", "token_sha256",
               "profile"}
REQUEST_KEYS = {"version", "schema", "request_id", "site_id", "camera_id", "session_id", "profile", "person_box",
                "single_person", "previous_request_id", "frames"}
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")


class Refused(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def constant(_):
        raise ValueError("nonfinite")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


# --- Pure request and output helpers (tested without torch) -------------------

def validate_request(value, *, profile):
    """Checked clip request: (request, jpeg bytes list). Raises Refused(400, reason)."""
    if not isinstance(value, dict) or set(value) != REQUEST_KEYS:
        raise Refused(400, "invalid_request")
    if value["version"] != 1 or value["schema"] != pins.REQUEST_SCHEMA:
        raise Refused(400, "schema_unsupported")
    if value["profile"] != profile:
        raise Refused(409, "profile_mismatch")
    if (value["site_id"] != "victoria" or value["camera_id"] != "den" or not isinstance(value["session_id"], str)
            or not HEX32.fullmatch(value["session_id"]) or not isinstance(value["request_id"], str)
            or not HEX64.fullmatch(value["request_id"])):
        raise Refused(400, "invalid_identity")
    previous = value["previous_request_id"]
    if previous is not None and (not isinstance(previous, str) or not HEX64.fullmatch(previous)):
        raise Refused(400, "invalid_identity")
    if type(value["single_person"]) is not bool:
        raise Refused(400, "invalid_request")
    box = value["person_box"]
    width, height = pins.IMAGE_SIZE
    if (not isinstance(box, list) or len(box) != 4 or not all(finite(v) for v in box)
            or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
        raise Refused(400, "invalid_person_box")
    frames = value["frames"]
    if not isinstance(frames, list) or len(frames) != pins.PROFILE["frame_count"]:
        raise Refused(400, "invalid_frames")
    observed, images = [], []
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {"observed_at", "jpeg_base64"} or not finite(frame["observed_at"]):
            raise Refused(400, "invalid_frames")
        if not isinstance(frame["jpeg_base64"], str) or len(frame["jpeg_base64"]) > 360 * 1024:
            raise Refused(400, "invalid_frames")
        try:
            data = base64.b64decode(frame["jpeg_base64"], validate=True)
        except ValueError:
            raise Refused(400, "invalid_frames") from None
        if not 0 < len(data) <= 256 * 1024 or not data.startswith(b"\xff\xd8"):
            raise Refused(400, "invalid_frames")
        observed.append(frame["observed_at"])
        images.append(data)
    if (any(not .25 <= b - a <= .75 for a, b in zip(observed, observed[1:]))
            or not 3.0 <= observed[-1] - observed[0] <= 4.0):
        raise Refused(400, "invalid_frame_times")
    return value, images


def decode_frames(images):
    """RGB uint8 arrays of the exact commissioned preview geometry; nothing is written."""
    import numpy as np
    from PIL import Image
    arrays = []
    for data in images:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG" or image.size != pins.IMAGE_SIZE or getattr(image, "n_frames", 1) != 1:
                    raise ValueError()
                arrays.append(np.asarray(image.convert("RGB"), dtype=np.uint8))
        except Exception:
            raise Refused(400, "frame_decode_failed") from None
    return arrays


def ntu_zero_shot(probabilities):
    """Softmax-23 NTU probabilities to the three den families (sums of classes)."""
    if len(probabilities) != len(pins.NTU_CLASSES):
        raise ValueError("NTU output geometry")
    named = dict(zip(pins.NTU_CLASSES, probabilities))
    return {family: float(sum(named[c] for c in classes)) for family, classes in pins.NTU_MAP.items()}


def token_motion(features):
    """Mean (1 - cosine) between consecutive temporal token slices of one clip.

    features: float array [2304, 768] in the encoder's T,H,W order (4 x 24 x 24).
    Static content gives values near 0; the scale is uncalibrated.
    """
    import numpy as np
    t, h, w = pins.PROFILE["raw_grid"]
    grid = np.asarray(features, dtype=np.float64).reshape(t, h * w, -1)
    a, b = grid[1:], grid[:-1]
    cosine = (a * b).sum(-1) / np.maximum(np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1), 1e-12)
    return float(np.clip(1 - cosine, 0, 2).mean())


def pooled(features):
    import numpy as np
    return np.asarray(features, dtype=np.float64).mean(0)


def drift(previous, current):
    import numpy as np
    norm = float(np.linalg.norm(previous) * np.linalg.norm(current))
    if not norm > 0:
        return None
    return float(min(2.0, max(0.0, 1 - float(np.dot(previous, current)) / norm)))


class Engine:
    """One session's clip service; model is ProbeModel on LA or a fake in tests."""

    def __init__(self, model, config, *, clock=time.time):
        self.model, self.config, self.clock = model, config, clock
        self.profile = config["profile"]
        self.lock = threading.Lock()
        self.count = 0
        self.previous = None  # (request_id, pooled embedding), memory only

    def lookup(self, raw):
        try:
            value = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            return 400, {"reason": "invalid_request"}
        if not isinstance(value, dict) or value.get("profile") != self.profile:
            return 409, {"error": "request_unavailable_or_indeterminate", "profile_admitted": False}
        return 409, dict(READINESS_REPLY)

    def closing(self):
        """True once no further clip can run: the connection then closes so shutdown is not held open."""
        return self.clock() >= self.config["expires_at"] or self.count >= self.config["maximum_requests"]

    def execute(self, raw):
        with self.lock:
            if self.clock() >= self.config["expires_at"]:
                return 503, {"reason": "lease_expired"}
            if self.count >= self.config["maximum_requests"]:
                return 429, {"reason": "request_budget_exhausted"}
            try:
                value = strict_json(raw)
            except (ValueError, UnicodeError, RecursionError):
                return 400, {"reason": "invalid_request"}
            try:
                request, images = validate_request(value, profile=self.profile)
                started = time.perf_counter()
                frames = decode_frames(images)
                decoded = time.perf_counter()
                self.count += 1
                output = self.model.run(frames, request["person_box"], request["single_person"])
            except Refused as error:
                return error.status, {"reason": error.reason}
            except Exception:
                return 503, {"reason": "inference_failed"}
            embedding = output["pooled"]
            scene_drift, reference = None, None
            if request["previous_request_id"] is not None and self.previous is not None \
                    and self.previous[0] == request["previous_request_id"]:
                scene_drift = drift(self.previous[1], embedding)
                reference = request["previous_request_id"] if scene_drift is not None else None
            self.previous = (request["request_id"], embedding)
            timings = dict(output["timings_ms"], decode=round((decoded - started) * 1000, 3))
            timings["total"] = round((time.perf_counter() - started) * 1000, 3)
            zero = ntu_zero_shot(output["ntu"]) if output["ntu"] is not None else None
            return 200, {"version": 1, "schema": pins.RESULT_SCHEMA, "request_id": request["request_id"],
                         "engine_epoch": self.profile["engine_epoch"], "artifact_sha256": self.profile["artifact_sha256"],
                         "posture": dict(zip(pins.POSTURES, (float(p) for p in output["posture"]))),
                         "reading": float(output["reading"]), "zero_shot": zero,
                         "zero_shot_status": "supplied" if zero is not None else "single_person_gate_not_met",
                         "motion": float(output["motion"]), "scene_drift": scene_drift, "drift_reference": reference,
                         "timings_ms": {k: float(timings[k]) for k in ("decode", "crop", "encode", "heads", "total")}}


# --- Host, config and artifact checks (LA) -----------------------------------

def validate_limits(values, runtime_seconds):
    if (values["memory.max"].strip() != str(pins.WORKER_MEMORY_BYTES) or values["memory.swap.max"].strip() != "0"
            or values["pids.max"].strip() != "64"):
        raise ValueError("Dedicated worker RAM/swap/task limits required")
    quota, period = values["cpu.max"].split()
    if not quota.isdecimal() or not period.isdecimal() or not 0 < int(quota) <= int(period):
        raise ValueError("At most one CPU quota required")
    if (values.get("RuntimeMaxUSec") not in {str(runtime_seconds) + "s", str(runtime_seconds // 60) + "min"}
            or values.get("Restart") != "no"):
        raise ValueError("Worker lifetime must match the lease without automatic restart")


def validate_config(config, now, runtime_revision):
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS or config["schema_version"] != 1:
        raise ValueError("Exact worker configuration required")
    runtime = config["runtime_seconds"]
    if runtime not in (120, 180, 360):
        raise ValueError("Provisioned runtime required")
    if not finite(config["expires_at"]) or not now < config["expires_at"] <= now + runtime:
        raise ValueError("Fresh expiry within the provisioned runtime required")
    if type(config["maximum_requests"]) is not int or not 1 <= config["maximum_requests"] <= MAX_REQUESTS:
        raise ValueError("Bounded request count required")
    if not isinstance(config["token_sha256"], str) or not HEX64.fullmatch(config["token_sha256"]):
        raise ValueError("Execution credential digest required")
    if config["profile"] != pins.profile(config["engine_epoch"], runtime_revision) \
            or not re.fullmatch(r"la-live-[a-f0-9]{32}", str(config["engine_epoch"])):
        raise ValueError("Exact pinned profile required")
    return config


def gpu_admission(query=None):
    """(free MiB, temperature C) or a refusal; nothing loads below the gate."""
    query = query or (lambda: subprocess.run(["nvidia-smi", "--query-gpu=memory.free,temperature.gpu",
                                              "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                             check=True, timeout=5).stdout)
    lines = query().strip().splitlines()
    if len(lines) != 1:
        raise ValueError("Exactly one GPU expected")
    free, temperature = (int(v.strip()) for v in lines[0].split(","))
    if free < pins.GPU_MIN_FREE_MIB:
        raise ValueError("GPU memory headroom below the admission gate")
    if temperature > pins.GPU_MAX_TEMPERATURE_C:
        raise ValueError("GPU thermal headroom below the admission gate")
    return free, temperature


def verify_artifacts(digest=sha256_file):
    """Digests of every pinned probe/Meta source, head and the NTU head. The
    encoder checkpoint is verified by the probe loader itself, whose pinned
    source (v3_finetune.py, probe0/v2/encoder.py) is covered here."""
    if os.path.realpath(pins.BUNDLE_LINK) != pins.BUNDLE_ROOT:
        raise ValueError("V-JEPA bundle link moved")
    roots = {"probe": pins.PROBE_ROOT, "meta": pins.META_ROOT}
    for name, expected in pins.SOURCES.items():
        root, relative = name.split(":", 1)
        if digest(os.path.join(roots[root], relative)) != expected:
            raise ValueError("Pinned probe source changed: " + name)
    for task, entries in pins.HEADS.items():
        for relative, expected in entries:
            if digest(os.path.join(pins.PROBE_ROOT, relative)) != expected:
                raise ValueError("Pinned head changed: " + task)
    if digest(pins.NTU_PATH) != pins.NTU_SHA256:
        raise ValueError("Pinned NTU head changed")
    if os.path.getsize(pins.ENCODER_PATH) != pins.ENCODER_BYTES:
        raise ValueError("Pinned encoder size changed")


def verify_loaded_modules(modules=None):
    """Every module file loaded from the probe tree (outside its venv) or the
    Meta source root must be a pinned source."""
    modules = sys.modules if modules is None else modules
    probe, meta, venv = Path(pins.PROBE_ROOT), Path(pins.META_ROOT), Path(pins.PROBE_ROOT) / ".venv"
    for module in list(modules.values()):
        file = getattr(module, "__file__", None)
        if not file or not os.path.isabs(file):
            continue
        path = Path(os.path.realpath(file))
        for tag, root in (("probe", probe), ("meta", meta)):
            if path.is_relative_to(root) and not path.is_relative_to(venv):
                if tag + ":" + path.relative_to(root).as_posix() not in pins.SOURCES:
                    raise ValueError("Unpinned probe module loaded: " + path.name)


class ProbeModel:
    """The probe's own frozen encoder, heads and crop code (LA GPU only)."""

    def __init__(self, device="cuda:0"):
        for entry in (pins.PROBE_ROOT, pins.PROBE_ROOT + "/tools"):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        import importlib.metadata as metadata
        for name, expected in pins.RUNTIME.items():
            if metadata.version(name) != expected:
                raise ValueError("Probe runtime differs from the audited venv")
        import torch
        from probe0.home_v2.contracts import validate_profile
        from probe0.home_v2.models import build_head
        from probe0.home_v2.preprocessing import crop_frames
        from probe0.v2 import encoder as encoder_contract
        from v3_finetune import Head, load_trainable_encoder
        from v4_home_runtime import encode_crop_tokens
        verify_loaded_modules()
        if encoder_contract.CHECKPOINT_SHA256 != pins.ENCODER_SHA256 or encoder_contract.SELECTOR_KEY != pins.ENCODER_SELECTOR:
            raise ValueError("Probe encoder contract differs from the pin")
        validate_profile(pins.PROFILE)
        self.torch, self.device = torch, device
        self.crop_frames, self.encode_crop_tokens = crop_frames, encode_crop_tokens
        torch.set_num_threads(1)
        total = torch.cuda.get_device_properties(0).total_memory
        # Allocator bound only; the CUDA context is outside it (admission covers that).
        torch.cuda.set_per_process_memory_fraction(pins.ALLOCATOR_LIMIT_BYTES / total, 0)
        self.encoder = load_trainable_encoder(pins.PROFILE["frame_count"], device).eval().requires_grad_(False)
        verify_loaded_modules()
        self.heads = {}
        for task in ("posture", "activity"):
            self.heads[task] = []
            for relative, _ in pins.HEADS[task]:
                saved = torch.load(os.path.join(pins.PROBE_ROOT, relative), map_location="cpu", weights_only=True)
                if (saved.get("task") != task or saved.get("schema") != pins.HEAD_SCHEMA
                        or saved["architecture"]["pooling"]["raw_grid"] != pins.PROFILE["raw_grid"]):
                    raise ValueError("unexpected_development_checkpoint")
                labels = pins.POSTURES if task == "posture" else pins.ACTIVITIES
                model = build_head(saved["architecture"], len(labels))
                model.load_state_dict(saved["state_dict"], strict=True)
                self.heads[task].append(model.to(device).eval().requires_grad_(False))
        checkpoint = torch.load(pins.NTU_PATH, map_location="cpu", weights_only=True)
        if checkpoint.get("unfreeze") != 0 or checkpoint.get("blocks") or tuple(checkpoint["classes"]) != pins.NTU_CLASSES:
            raise ValueError("NTU head must be the frozen-encoder 23-class head")
        self.ntu = Head(len(pins.NTU_CLASSES)).to(device).eval().requires_grad_(False)
        self.ntu.load_state_dict(checkpoint["head"], strict=True)

    def run(self, frames, box, single_person):
        """Mirrors probe0.home_v2.live_models.Jepa.infer for one union box, plus
        v3_final_runtime's NTU readout on the same tokens."""
        torch = self.torch
        started = time.perf_counter()
        crops = self.crop_frames(frames, [list(box)] * len(frames), pins.PROFILE)
        cropped = time.perf_counter()
        raw = self.encode_crop_tokens(self.encoder, crops, pins.PROFILE, self.device)
        torch.cuda.synchronize()
        encoded = time.perf_counter()
        tokens = raw.half().float()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            posture = torch.stack([h(tokens).float() for h in self.heads["posture"]]).mean(0).softmax(-1)[0]
            activity = torch.stack([h(tokens).float() for h in self.heads["activity"]]).mean(0).sigmoid()[0]
            ntu = self.ntu(raw).float().softmax(-1)[0] if single_person else None
        features = raw[0].float().cpu().numpy()
        output = {"posture": posture.cpu().tolist(), "reading": float(activity[pins.ACTIVITIES.index("reading")]),
                  "ntu": ntu.cpu().tolist() if ntu is not None else None, "motion": token_motion(features),
                  "pooled": pooled(features)}
        finished = time.perf_counter()
        output["timings_ms"] = {"crop": round((cropped - started) * 1000, 3), "encode": round((encoded - cropped) * 1000, 3),
                                "heads": round((finished - encoded) * 1000, 3)}
        return output


# --- HTTP ----------------------------------------------------------------------

class CountedBody:
    """The request body stream, counting what dispatch read: keep-alive needs the exact body."""

    def __init__(self, stream):
        self.stream, self.count = stream, 0

    def read(self, size):
        data = self.stream.read(size)
        self.count += len(data)
        return data


def handler_class(engine, token_sha256):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 5
        # TCP_NODELAY on each accepted connection: a reply is two writes (headers,
        # then body) and a kept-alive socket has no closing FIN to push the body
        # out, so Nagle would hold it for the supervisor's delayed ACK (~40 ms).
        disable_nagle_algorithm = True

        def log_message(self, *args):
            pass  # never log paths, bodies or peers

        def handle_one_request(self):
            # Wait for the next request on a kept-alive connection no longer than
            # the idle timeout, nor past the lease expiry, so an idle supervisor
            # connection never holds server.shutdown() open.
            remaining = engine.config["expires_at"] - engine.clock()
            self.connection.settimeout(min(self.timeout, max(.5, remaining)))
            super().handle_one_request()

        def parse_request(self):
            self.connection.settimeout(self.timeout)  # a request line arrived: the full timeout for the rest
            return super().parse_request()

        def reply(self, status, value, close=True):
            data = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            if close:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            if close:
                self.close_connection = True

        def do_GET(self):
            self.reply(404, {"reason": "route_not_found"})

        def do_POST(self):
            body = CountedBody(self.rfile)
            status, value = dispatch(engine, token_sha256, self.path, self.headers, body)
            # Reuse the connection only after reading exactly the declared body
            # (the next request line starts there), and never once the lease or
            # the request budget is spent.
            exact = ("Transfer-Encoding" not in self.headers
                     and self.headers.get("Content-Length", "").strip() == str(body.count))
            self.reply(status, value, close=not exact or engine.closing())
    return Handler


def dispatch(engine, token_sha256, path, headers, stream):
    if path not in ("/v1/specialist/execute", "/v1/specialist/lookup"):
        return 404, {"reason": "route_not_found"}
    presented = headers.get("Authorization", "")
    token = presented[7:] if presented.startswith("Bearer ") else ""
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), token_sha256):
        return 401, {"reason": "unauthorized"}
    if headers.get("Content-Type", "").split(";")[0] != "application/json":
        return 415, {"reason": "json_required"}
    try:
        length = int(headers.get("Content-Length", ""))
    except ValueError:
        return 411, {"reason": "length_required"}
    if not 0 < length <= MAX_BODY:
        return 413, {"reason": "body_too_large"}
    raw = stream.read(length)
    if len(raw) != length:
        return 400, {"reason": "body_unavailable"}
    return engine.lookup(raw) if path.endswith("/lookup") else engine.execute(raw)


def load_private_config(path):
    path = Path(path)
    info = path.lstat()
    if not path.is_file() or path.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Private operator provisioning required")
    with path.open("rb") as source:
        raw = source.read(16385)
    if len(raw) > 16384:
        raise ValueError("Configuration too large")
    return strict_json(raw)


def runtime_revision(directory):
    """Digest of this staged worker tree (the files vjepa-source.json names)."""
    files = {name: sha256_file(Path(directory) / name) for name in ("vjepa_pins.py", "vjepa_worker.py")}
    return hashlib.sha256(pins.canonical(files).encode()).hexdigest()


def main():
    if sys.platform != "linux" or socket.gethostname() != "EngineeredLightingServer1":
        raise SystemExit("Explicit LA host required")
    groups = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::/")]
    if len(groups) != 1 or not groups[0].endswith("/" + UNIT):
        raise SystemExit("Dedicated bounded V-JEPA worker unit required")
    os.umask(0o077)
    config = load_private_config(os.environ["PERCEPTION_LAB_VJEPA_WORKER_CONFIG"])
    config = validate_config(config, time.time(), runtime_revision(Path(__file__).resolve().parent))
    cgroup = Path("/sys/fs/cgroup") / groups[0].lstrip("/")
    limits = {k: (cgroup / k).read_text() for k in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")}
    properties = subprocess.run(["systemctl", "--user", "show", UNIT, "-p", "RuntimeMaxUSec", "-p", "Restart"],
                                capture_output=True, text=True, check=True, timeout=3).stdout
    limits.update(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    validate_limits(limits, config["runtime_seconds"])
    verify_artifacts()
    gpu_admission()  # immediately before any weights load
    model = ProbeModel()
    engine = Engine(model, config)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(BASE / "tls/server.pem"), str(BASE / "tls/server.key"))
    # Deliberately not ThreadingHTTPServer: one connection and one clip at a time.
    server = http.server.HTTPServer((HOST, PORT), handler_class(engine, config["token_sha256"]))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.timeout = 1

    def expiry():
        while time.time() < config["expires_at"] and engine.count < config["maximum_requests"]:
            time.sleep(1)
        server.shutdown()
    threading.Thread(target=expiry, name="lease-expiry", daemon=True).start()
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
