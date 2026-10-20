"""LA V-JEPA worker, launcher and supervisor factory: pins parity with Victoria, request
validation, the clip engine with a fake model, host/GPU gates and launch arguments.
No torch, GPU, network or systemd; runs on the workstation."""
import ast
import asyncio
import base64
import hashlib
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (HERE, ROOT / "tools/la-detector-trial", ROOT / "stack/services/perception-lab"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import vjepa_pins as pins  # noqa: E402
import vjepa_worker as worker  # noqa: E402
import vjepa_launcher as launcher  # noqa: E402
import vjepa_scene as scene  # noqa: E402
from lifecycle import PREFLIGHT_REASONS, PreflightRejected  # noqa: E402


def literal(path, name):
    for node in ast.parse(Path(path).read_bytes()).body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [name]:
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)


def jpeg(colour=(40, 50, 60)):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", pins.IMAGE_SIZE, colour).save(out, format="JPEG")
    return out.getvalue()


EPOCH = "la-live-" + "1" * 32
PROFILE = pins.profile(EPOCH, "r" * 64)
CONFIG = {"schema_version": 1, "engine_epoch": EPOCH, "expires_at": 2000.0, "runtime_seconds": 360, "maximum_requests": 3,
          "token_sha256": hashlib.sha256(b"t" * 40).hexdigest(), "profile": PROFILE}


class FakeModel:
    def __init__(self):
        self.calls = []

    def run(self, frames, box, single_person):
        import numpy as np
        self.calls.append((len(frames), frames[0].shape, list(box), single_person))
        ntu = [0.0] * 23
        ntu[pins.NTU_CLASSES.index("typing")] = .5
        ntu[pins.NTU_CLASSES.index("phone_call")] = .1
        ntu[pins.NTU_CLASSES.index("phone_or_tablet")] = .2
        ntu[pins.NTU_CLASSES.index("writing")] = .2
        return {"posture": [.7, .1, .1, .05, .03, .02], "reading": .4, "ntu": ntu if single_person else None,
                "motion": .05, "pooled": np.full(768, float(len(self.calls))) + np.arange(768) * (len(self.calls) % 2),
                "timings_ms": {"crop": 1.0, "encode": 20.0, "heads": 2.0}}


def victoria_body(previous=None, single=True, profile=PROFILE, colours=None):
    images = [jpeg((10 * i, 50, 60)) for i in range(8)] if colours is None else colours
    return scene.request_body(site_id="victoria", camera_id="den", session_id="a" * 32, profile=profile, images=images,
                              observed=[100 + .5 * i for i in range(8)], media_ids=["m%d" % i for i in range(8)],
                              box=[200, 40, 380, 340], single_person=single, previous_request_id=previous)


class ParityTests(unittest.TestCase):
    def test_pins_and_identities_match_victoria(self):
        self.assertEqual(pins.PINS, scene.PINS)
        self.assertEqual(pins.ARTIFACT_SHA256, scene.ARTIFACT_SHA256)
        for name in ("MODEL_VERSION", "INPUT_PROFILE", "REQUEST_SCHEMA", "RESULT_SCHEMA", "NTU_CLASSES", "NTU_MAP",
                     "POSTURES", "IMAGE_SIZE"):
            self.assertEqual(getattr(pins, name), getattr(scene, name), name)
        self.assertEqual(scene.check_profile(PROFILE), PROFILE)
        self.assertEqual(pins.PINS["encoder"]["sha256"], pins.ENCODER_SHA256)
        self.assertTrue(pins.ENCODER_PATH.endswith(pins.ENCODER_SHA256))

    def test_readiness_reply_is_exactly_the_supervisor_ready(self):
        ready = literal(ROOT / "tools/la-detector-trial/lifecycle_launcher.py", "READY")
        self.assertEqual(worker.READINESS_REPLY, ready)
        self.assertEqual(launcher.READY, ready)

    def test_host_gates_track_the_detector_preflight(self):
        start_live = ROOT / "tools/la-detector-trial/start_live.py"
        self.assertEqual(launcher.DOCUMENTED_FAILED_UNITS, literal(start_live, "DOCUMENTED_FAILED_UNITS"))
        source = start_live.read_text()
        self.assertIn(launcher.FAULT_BASELINE, source)
        self.assertIn(launcher.FAULT_PATTERN, source)
        self.assertTrue(set(launcher.GATES.values()) <= PREFLIGHT_REASONS)

    def test_the_supervisor_reuses_the_reviewed_lifecycle_unchanged(self):
        app = (HERE / "vjepa_lifecycle_app.py").read_text()
        self.assertIn("from lifecycle import Lifecycle", app)
        self.assertIn("from lifecycle_http import LifecycleHTTP", app)
        self.assertFalse((HERE / "lifecycle.py").exists() or (HERE / "lifecycle_http.py").exists(),
                         "deploy copies the reviewed files; the repo keeps one source")

    def test_importing_the_worker_loads_no_model_runtime(self):
        code = "import sys;sys.path.insert(0,%r);import vjepa_worker;print('torch' in sys.modules)" % str(HERE)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "False")

    def test_the_isolated_interpreter_still_finds_the_staged_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("vjepa_pins.py", "vjepa_worker.py"):
                (Path(directory) / name).write_bytes((HERE / name).read_bytes())
            code = "import runpy,sys;sys.argv=['x'];m=runpy.run_path(%r,run_name='probe');print(m['pins'].ENCODER_SHA256[:8])"
            out = subprocess.run([sys.executable, "-I", "-B", "-c", code % str(Path(directory) / "vjepa_worker.py")],
                                 capture_output=True, text=True, cwd=tempfile.gettempdir())
            self.assertEqual(out.stdout.strip(), "848a77c3", out.stderr)

    def test_worker_never_writes_frames(self):
        tree = ast.parse((HERE / "vjepa_worker.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) in {"open", "write_bytes", "write_text", "save"}:
                modes = [a.value for a in node.args[1:] if isinstance(a, ast.Constant)]
                self.assertFalse(any("w" in str(m) or "a" in str(m) or "x" in str(m) for m in modes), ast.dump(node))
                self.assertNotIn(getattr(node.func, "attr", None), {"write_bytes", "write_text", "save"})


class RequestTests(unittest.TestCase):
    def test_victoria_body_is_accepted(self):
        request, images = worker.validate_request(json.loads(scene.canonical(victoria_body())), profile=PROFILE)
        self.assertEqual(len(images), 8)
        frames = worker.decode_frames(images)
        self.assertEqual(frames[0].shape, (360, 640, 3))

    def test_refusals_are_typed(self):
        body = json.loads(scene.canonical(victoria_body()))
        cases = [({**body, "extra": 1}, 400, "invalid_request"), ({**body, "schema": "x"}, 400, "schema_unsupported"),
                 ({**body, "profile": {**PROFILE, "engine_epoch": "la-live-" + "2" * 32}}, 409, "profile_mismatch"),
                 ({**body, "site_id": "echo"}, 400, "invalid_identity"), ({**body, "request_id": "x"}, 400, "invalid_identity"),
                 ({**body, "person_box": [0, 0, 700, 10]}, 400, "invalid_person_box"),
                 ({**body, "frames": body["frames"][:7]}, 400, "invalid_frames"),
                 ({**body, "frames": [dict(f, jpeg_base64="!!") for f in body["frames"]]}, 400, "invalid_frames"),
                 ({**body, "frames": [dict(f, observed_at=100 + i) for i, f in enumerate(body["frames"])]}, 400,
                  "invalid_frame_times"),
                 ({**body, "single_person": 1}, 400, "invalid_request")]
        for value, status, reason in cases:
            with self.assertRaises(worker.Refused) as caught:
                worker.validate_request(value, profile=PROFILE)
            self.assertEqual((caught.exception.status, caught.exception.reason), (status, reason))
        png = io.BytesIO()
        from PIL import Image
        Image.new("RGB", (320, 180)).save(png, format="JPEG")
        with self.assertRaises(worker.Refused) as caught:
            worker.decode_frames([png.getvalue()])
        self.assertEqual(caught.exception.reason, "frame_decode_failed")


class EngineTests(unittest.TestCase):
    def engine(self, now=1000.0, **config):
        self.model = FakeModel()
        return worker.Engine(self.model, {**CONFIG, **config}, clock=lambda: now)

    def post(self, engine, body):
        return engine.execute(scene.canonical(body).encode())

    def test_one_clip_round_trips_into_a_valid_victoria_result(self):
        engine = self.engine()
        body = victoria_body()
        status, value = self.post(engine, body)
        self.assertEqual(status, 200)
        request = {k: body[k] for k in ("request_id", "profile", "person_box", "single_person", "previous_request_id")}
        checked, reason = scene.validate_result(json.loads(json.dumps(value)), request=request)
        self.assertIsNone(reason)
        for key, expected in {"computer_work": .5, "phone_use": .3, "reading": .2}.items():
            self.assertAlmostEqual(checked["zero_shot"][key], expected)
        self.assertEqual((checked["scene_drift"], checked["drift_reference"]), (None, None))
        self.assertEqual(self.model.calls[0], (8, (360, 640, 3), [200.0, 40.0, 380.0, 340.0], True))
        self.assertEqual(set(value["timings_ms"]), set(scene.TIMINGS))

    def test_drift_only_against_the_named_previous_clip(self):
        engine = self.engine()
        first = victoria_body()
        self.post(engine, first)
        status, chained = self.post(engine, victoria_body(previous=first["request_id"], single=False))
        self.assertEqual((status, chained["drift_reference"]), (200, first["request_id"]))
        self.assertGreater(chained["scene_drift"], 0)
        self.assertEqual((chained["zero_shot"], chained["zero_shot_status"]), (None, "single_person_gate_not_met"))
        status, unknown = self.post(engine, victoria_body(previous="f" * 64))
        self.assertEqual((unknown["scene_drift"], unknown["drift_reference"]), (None, None))

    def test_budget_expiry_and_lookup(self):
        engine = self.engine(maximum_requests=1)
        self.assertEqual(self.post(engine, victoria_body())[0], 200)
        self.assertEqual(self.post(engine, victoria_body()), (429, {"reason": "request_budget_exhausted"}))
        self.assertEqual(self.post(self.engine(now=2000.0), victoria_body()), (503, {"reason": "lease_expired"}))
        ready = engine.lookup(json.dumps({"request_id": "readiness", "profile": PROFILE}).encode())
        self.assertEqual(ready, (409, worker.READINESS_REPLY))
        self.assertEqual(engine.lookup(json.dumps({"profile": {}}).encode())[1]["profile_admitted"], False)
        self.assertEqual(self.engine().execute(b'{"a":1,"a":2}'), (400, {"reason": "invalid_request"}))

    def test_a_model_failure_is_a_typed_refusal_and_is_not_retried(self):
        engine = self.engine()
        engine.model.run = mock.Mock(side_effect=RuntimeError("cuda"))
        self.assertEqual(self.post(engine, victoria_body()), (503, {"reason": "inference_failed"}))
        self.assertEqual(engine.model.run.call_count, 1)

    def test_dispatch_routes_auth_and_type(self):
        engine = self.engine()
        data = scene.canonical(victoria_body()).encode()
        headers = {"Authorization": "Bearer " + "t" * 40, "Content-Type": "application/json", "Content-Length": str(len(data))}
        self.assertEqual(worker.dispatch(engine, CONFIG["token_sha256"], "/v1/specialist/execute", headers, io.BytesIO(data))[0], 200)
        self.assertEqual(worker.dispatch(engine, CONFIG["token_sha256"], "/other", headers, io.BytesIO(data))[0], 404)
        self.assertEqual(worker.dispatch(engine, CONFIG["token_sha256"], "/v1/specialist/execute",
                                         {**headers, "Authorization": "Bearer wrong"}, io.BytesIO(data))[0], 401)
        self.assertEqual(worker.dispatch(engine, CONFIG["token_sha256"], "/v1/specialist/execute",
                                         {**headers, "Content-Type": "text/plain"}, io.BytesIO(data))[0], 415)
        self.assertEqual(worker.dispatch(engine, CONFIG["token_sha256"], "/v1/specialist/execute",
                                         {**headers, "Content-Length": str(worker.MAX_BODY + 1)}, io.BytesIO(data))[0], 413)


class MathTests(unittest.TestCase):
    def test_ntu_mapping_sums_the_named_classes(self):
        probabilities = [1 / 23] * 23
        mapped = worker.ntu_zero_shot(probabilities)
        self.assertAlmostEqual(mapped["phone_use"], 2 / 23)
        self.assertAlmostEqual(mapped["computer_work"], 1 / 23)
        self.assertAlmostEqual(mapped["reading"], 2 / 23)
        with self.assertRaises(ValueError):
            worker.ntu_zero_shot([1.0] * 5)

    def test_motion_and_drift_scalars(self):
        import numpy as np
        rng = np.random.default_rng(0)
        slice_ = rng.normal(size=(576, 768))
        static = np.concatenate([slice_] * 4)
        self.assertAlmostEqual(worker.token_motion(static), 0, places=9)
        moving = rng.normal(size=(2304, 768))
        self.assertGreater(worker.token_motion(moving), .5)
        a = worker.pooled(static)
        self.assertAlmostEqual(worker.drift(a, a), 0, places=9)
        self.assertAlmostEqual(worker.drift(a, -a), 2, places=9)
        self.assertIsNone(worker.drift(np.zeros(768), a))


class HostGateTests(unittest.TestCase):
    def test_config_and_limits(self):
        good = dict(CONFIG, expires_at=1300.0)
        self.assertEqual(worker.validate_config(good, 1000.0, "r" * 64), good)
        for change in ({"expires_at": 999.0}, {"expires_at": 1400.0}, {"maximum_requests": 401}, {"runtime_seconds": 500},
                       {"token_sha256": "x"}, {"engine_epoch": "la-live-" + "2" * 32}, {"surprise": 1}):
            with self.assertRaises(ValueError):
                worker.validate_config({**good, **change}, 1000.0, "r" * 64)
        with self.assertRaises(ValueError):
            worker.validate_config(good, 1000.0, "q" * 64)  # staged tree changed since launch
        limits = {"memory.max": str(pins.WORKER_MEMORY_BYTES) + "\n", "memory.swap.max": "0\n", "pids.max": "64\n",
                  "cpu.max": "100000 100000\n", "RuntimeMaxUSec": "6min", "Restart": "no"}
        worker.validate_limits(limits, 360)
        for change in ({"memory.max": "max"}, {"memory.swap.max": "max"}, {"cpu.max": "max 100000"},
                       {"cpu.max": "200000 100000"}, {"RuntimeMaxUSec": "infinity"}, {"Restart": "on-failure"}):
            with self.assertRaises(ValueError):
                worker.validate_limits({**limits, **change}, 360)

    def test_gpu_admission_refuses_below_the_gate(self):
        self.assertEqual(worker.gpu_admission(lambda: "28480, 75\n"), (28480, 75))
        for reply in ("6000, 60\n", "28480, 81\n", "1, 2\n3, 4\n"):
            with self.assertRaises(ValueError):
                worker.gpu_admission(lambda: reply)

    def test_only_pinned_probe_modules_may_load(self):
        pinned = types.SimpleNamespace(__file__=pins.PROBE_ROOT + "/tools/v3_finetune.py")
        venv = types.SimpleNamespace(__file__=pins.PROBE_ROOT + "/.venv/lib/python3.12/site-packages/numpy/__init__.py")
        other = types.SimpleNamespace(__file__=pins.PROBE_ROOT + "/tools/v3_pose_fusion.py")
        with mock.patch.object(worker.os.path, "realpath", lambda p: p):
            worker.verify_loaded_modules({"a": pinned, "b": venv, "c": types.SimpleNamespace()})
            with self.assertRaises(ValueError):
                worker.verify_loaded_modules({"a": pinned, "c": other})

    def test_artifact_verification_checks_every_pin(self):
        seen = []

        def digest(path):
            seen.append(path)
            for name, value in pins.SOURCES.items():
                if path.endswith(name.split(":", 1)[1]):
                    return value
            for entries in pins.HEADS.values():
                for relative, value in entries:
                    if path.endswith(relative):
                        return value
            return pins.NTU_SHA256 if path == pins.NTU_PATH else "0" * 64
        with mock.patch.object(worker.os.path, "realpath", return_value=pins.BUNDLE_ROOT), \
                mock.patch.object(worker.os.path, "getsize", return_value=pins.ENCODER_BYTES):
            worker.verify_artifacts(digest)
            self.assertEqual(len(seen), len(pins.SOURCES) + 7)
            with self.assertRaises(ValueError):
                worker.verify_artifacts(lambda path: "0" * 64)
        with mock.patch.object(worker.os.path, "realpath", return_value="/opt/vjepa-v2/bundles/other"):
            with self.assertRaises(ValueError):
                worker.verify_artifacts(digest)


class LauncherTests(unittest.TestCase):
    def fake_run(self, failed="", gpu="28480, 75", journal=1):
        def run(args, timeout=15):
            if args[:2] == ["systemctl", "list-units"]:
                return failed
            if args[0] == "nvidia-smi":
                return gpu + "\n"
            if "journalctl" in args:
                assert "sudo" not in args, args
                if journal == 1:
                    raise subprocess.CalledProcessError(1, args, output="", stderr="")
                if journal == "denied":
                    # sudo under NoNewPrivileges, or no journal access: exit 1, empty stdout.
                    raise subprocess.CalledProcessError(1, args, output="", stderr="sudo: no new privileges")
                if journal == "error":
                    raise subprocess.CalledProcessError(2, args, output="")
                return "Sep 29 kernel: Out of memory: Killed process 1\n"
            raise AssertionError(args)
        return run

    def meminfo(self, kib=32 * 1024 * 1024):
        return lambda: "MemTotal: 63000000 kB\nMemAvailable: %d kB\n" % kib

    def test_preflight_gates(self):
        host = lambda: "EngineeredLightingServer1"
        self.assertEqual(launcher.preflight(run=self.fake_run(), meminfo=self.meminfo(), hostname=host), (28480, 75))
        failures = [(dict(failed="observer-health.service loaded failed failed x\nnew.service loaded failed failed y"),
                     {}, "new failed service"),
                    (dict(gpu="5000, 60"), {}, "gpu memory headroom failed"),
                    (dict(gpu="28480, 85"), {}, "gpu thermal headroom failed"),
                    ({}, {"kib": 4 * 1024 * 1024}, "host memory headroom failed"),
                    (dict(journal=0), {}, "new fault or unavailable fault check"),
                    (dict(journal="error"), {}, "new fault or unavailable fault check"),
                    (dict(journal="denied"), {}, "new fault or unavailable fault check")]
        for run_args, memory, message in failures:
            with self.assertRaises(ValueError) as caught:
                launcher.preflight(run=self.fake_run(**run_args), meminfo=self.meminfo(**memory), hostname=host)
            self.assertEqual(str(caught.exception), message)
            self.assertIn(message, launcher.GATES)
        with self.assertRaises(ValueError):
            launcher.preflight(run=self.fake_run(), meminfo=self.meminfo(), hostname=lambda: "other")

    def test_worker_command_is_fixed_and_capped(self):
        args = launcher.worker_command("/home/marcelo-lima/home-lab-vjepa/release-x", "/cfg.json", 360)
        for expected in ("--unit=lab-vjepa-worker.service", "--property=MemoryMax=6G", "--property=MemorySwapMax=0",
                         "--property=CPUQuota=100%", "--property=TasksMax=64", "--property=RuntimeMaxSec=360",
                         "--property=Restart=no", "--property=KillMode=control-group", "--property=NoNewPrivileges=yes",
                         "--setenv=CUDA_VISIBLE_DEVICES=0", pins.PROBE_PYTHON, "-I", "-B"):
            self.assertIn(expected, args)
        self.assertEqual(args[-1], "/home/marcelo-lima/home-lab-vjepa/release-x/vjepa_worker.py")
        self.assertEqual(launcher.stage({"PERCEPTION_LAB_SESSION_SECONDS": "300"}), (355, 360))
        with self.assertRaises(ValueError):
            launcher.stage({"PERCEPTION_LAB_SESSION_SECONDS": "900"})

    def test_staged_source_is_hash_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            release = base / "release-1"
            release.mkdir()
            for name in ("vjepa_pins.py", "vjepa_worker.py"):
                (release / name).write_bytes((HERE / name).read_bytes())
            files = {n: hashlib.sha256((release / n).read_bytes()).hexdigest() for n in ("vjepa_pins.py", "vjepa_worker.py")}
            revision = hashlib.sha256(pins.canonical(files).encode()).hexdigest()
            (base / "vjepa-source.json").write_text(json.dumps({"directory": "release-1", "files": files,
                                                                "runtime_revision": revision}))
            self.assertEqual(launcher.staged_source(base), (release.resolve(), revision))
            self.assertEqual(worker.runtime_revision(release), revision)
            (release / "vjepa_worker.py").write_bytes(b"# changed\n")
            with self.assertRaises(ValueError) as caught:
                launcher.staged_source(base)
            self.assertEqual(str(caught.exception), "staged source changed")

    def bare(self):
        value = object.__new__(launcher.Launcher)
        value.base, value.lease_seconds, value.runtime_seconds = Path("/nonexistent"), 355, 360
        return value

    def test_start_maps_preflight_refusals_to_lifecycle_reasons_without_launching(self):
        value = self.bare()
        value.unit = lambda: {"ActiveState": "inactive", "MainPID": "0"}
        launched = []
        with mock.patch.object(launcher, "run", side_effect=lambda args, timeout=15: launched.append(args)), \
                mock.patch.object(launcher, "preflight", side_effect=ValueError("gpu memory headroom failed")):
            with self.assertRaises(PreflightRejected) as caught:
                value.start(EPOCH, 10**12)
            self.assertEqual(caught.exception.reason, "preflight_vram")
        with self.assertRaises(PreflightRejected) as caught:
            value.start(EPOCH, 10**12, variant="pose")
        value.unit = lambda: {"ActiveState": "active", "MainPID": "5"}
        with self.assertRaises(PreflightRejected) as caught:
            value.start(EPOCH, 10**12)
        self.assertEqual(caught.exception.reason, "preflight_worker_active")
        self.assertEqual(launched, [])


class SupervisorFactoryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX ownership and mode checks")
    def test_factory_requires_private_distinct_credentials(self):
        import vjepa_lifecycle_app as app
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "lifecycle-private").mkdir(mode=0o700)
            (base / "private").mkdir(mode=0o700)
            for path, text in ((base / "lifecycle-private/controller.token", "c" * 40), (base / "private/execution.token", "e" * 40)):
                path.write_text(text)
                path.chmod(0o600)

            class Fake:
                lease_seconds = 355

                def __init__(self, environ):
                    pass

                def warm_start(self):
                    pass
            http = app.create_app(base=base, launcher_factory=Fake)
            self.assertEqual(http.lifecycle.lease_seconds, 355)
            http.lifecycle.db.close()
            http.lifecycle.owner_file.close()
            (base / "private/execution.token").write_text("c" * 40)
            with self.assertRaises(ValueError):
                app.create_app(base=base, launcher_factory=Fake)

    def test_the_factory_starts_the_fault_scan_at_supervisor_start(self):
        import vjepa_lifecycle_app as app
        started = []

        class Fake:
            lease_seconds = 355

            def __init__(self, environ):
                pass

            def warm_start(self):
                started.append(True)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(app, "private", side_effect=lambda path, directory=False: path), \
                mock.patch.object(app, "token", side_effect=lambda path: path.name * 4):
            (Path(directory) / "lifecycle-private").mkdir()
            http_app = app.create_app(base=Path(directory), launcher_factory=Fake)
            http_app.lifecycle.db.close()
            http_app.lifecycle.owner_file.close()
        self.assertEqual(started, [True])


TOKEN = "t" * 40
LOOKUP = "/v1/specialist/lookup"
EXECUTE = "/v1/specialist/execute"
READINESS = json.dumps({"request_id": "readiness", "profile": PROFILE}).encode()


class CountingServer(http.server.HTTPServer):
    """The worker's own single-threaded server on an ephemeral loopback port (no TLS), counting connections."""

    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)
        self.accepted = 0

    def get_request(self):
        request = super().get_request()
        self.accepted += 1
        return request


def serve(test, engine, timeout=None, handler=None):
    handler = handler or worker.handler_class(engine, CONFIG["token_sha256"])
    if timeout is not None:
        handler = type("ShortIdle", (handler,), {"timeout": timeout})
    server = CountingServer(handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(5)
    test.addCleanup(stop)
    return server


def clip_engine(**config):
    return worker.Engine(FakeModel(), {**CONFIG, **config}, clock=lambda: 1000.0)


class WorkerKeepAliveTests(unittest.TestCase):
    def client(self, server):
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        self.addCleanup(client.close)
        return client

    def post(self, client, path, data, token=TOKEN):
        client.request("POST", path, data, {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = client.getresponse()
        return response.status, response.getheader("Connection"), json.loads(response.read())

    def test_requests_share_one_kept_alive_connection(self):
        server = serve(self, clip_engine())
        client = self.client(server)
        for _ in range(3):
            self.assertEqual(self.post(client, LOOKUP, READINESS), (409, None, worker.READINESS_REPLY))
        status, connection, value = self.post(client, EXECUTE, scene.canonical(victoria_body()).encode())
        self.assertEqual((status, connection), (200, None))
        self.assertEqual(server.accepted, 1)

    def test_an_unread_body_or_the_last_allowed_clip_closes_the_connection(self):
        server = serve(self, clip_engine(maximum_requests=1))
        client = self.client(server)
        self.assertEqual(self.post(client, LOOKUP, READINESS, token="wrong")[:2], (401, "close"))
        self.assertEqual(self.post(client, EXECUTE, scene.canonical(victoria_body()).encode())[:2], (200, "close"))
        self.assertEqual(server.accepted, 2)

    def test_replies_are_not_held_by_nagle_on_a_kept_alive_connection(self):
        nodelay = []

        class Recording(worker.handler_class(clip_engine(), CONFIG["token_sha256"])):
            def setup(self):
                super().setup()
                nodelay.append(self.connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY))
        self.assertIs(Recording.disable_nagle_algorithm, True)
        server = serve(self, None, handler=Recording)
        client = self.client(server)
        for _ in range(2):
            self.assertEqual(self.post(client, LOOKUP, READINESS)[0], 409)
        self.assertEqual(server.accepted, 1)
        self.assertEqual(len(nodelay), 1)
        self.assertTrue(nodelay[0])  # set on the accepted socket, before the first reply

    def test_an_idle_connection_never_outlives_the_lease(self):
        server = serve(self, worker.Engine(FakeModel(), {**CONFIG, "expires_at": time.time() + .2}))
        with socket.create_connection(server.server_address, timeout=5) as raw:
            started = time.monotonic()
            self.assertEqual(raw.recv(1), b"")  # closed after max(.5 s, time left), not the 5 s idle timeout
            self.assertLess(time.monotonic() - started, 3)

    def test_the_worker_stays_single_threaded(self):
        tree = ast.parse((HERE / "vjepa_worker.py").read_text())
        names = {getattr(n, "attr", getattr(n, "id", None)) for n in ast.walk(tree)}
        self.assertIn("HTTPServer", names)
        self.assertFalse({"ThreadingHTTPServer", "ThreadingMixIn", "ForkingMixIn"} & names)
        handler = worker.handler_class(None, "0" * 64)
        self.assertEqual((handler.protocol_version, handler.timeout), ("HTTP/1.1", 5))
        self.assertLess(launcher.IDLE_RECONNECT_SECONDS, handler.timeout)


class FakeResponse:
    def __init__(self, status=409, data=b"{}", will_close=False):
        self.status, self.data, self.will_close, self.done = status, data, will_close, False

    def read(self, size):
        self.done = True
        return self.data

    def isclosed(self):
        return self.done

    def getheader(self, name, default=""):
        return "application/json" if name == "Content-Type" else default


class FakeClient:
    def __init__(self, log, send_error=None, response_error=None, gate=None, pooled=False):
        self.log, self.send_error, self.response_error, self.gate = log, send_error, response_error, gate
        self.sock = object() if pooled else None

    def request(self, method, path, body, headers):
        self.log.append(("send", self))
        if self.send_error:
            raise self.send_error
        self.sock = self.sock or object()
        if self.gate:
            self.gate()

    def getresponse(self):
        if self.response_error:
            raise self.response_error
        return FakeResponse()

    def close(self):
        self.sock = None
        self.log.append(("close", self))


def through_lifecycle_http(supervisor, path, body):
    """One proxied request through the reviewed LifecycleHTTP, the way Victoria's clip reaches forward."""
    from lifecycle_http import LifecycleHTTP
    app = LifecycleHTTP(types.SimpleNamespace(launcher=supervisor), controller_token="c" * 40, execution_token=TOKEN)
    app.accepting = True
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)
    scope = {"type": "http", "scheme": "https", "method": "POST", "path": path, "query_string": b"",
             "headers": [(b"authorization", ("Bearer " + TOKEN).encode()), (b"content-type", b"application/json")]}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], sent[1]["body"]


def victoria_sees(status, data):
    """What Victoria's VjepaTransport makes of a supervisor reply."""
    import vjepa_live

    class Connection:
        def request(self, *args):
            pass

        def getresponse(self):
            return FakeResponse(status, data)

        def close(self):
            pass
    transport = vjepa_live.VjepaTransport("https://192.168.0.100:9447", lambda: TOKEN, None,
                                          connection_factory=Connection)
    return transport(victoria_body())


class SupervisorPoolTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.log = []

    def supervisor(self, server=None):
        value = object.__new__(launcher.Launcher)
        value.refuse_gpu_c = 74
        value.reset_pool()
        value.monotonic = lambda: self.now[0]
        value.token = lambda: TOKEN
        value.thermal_refusal = lambda: None
        value.opened = []

        def connect():
            client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            value.opened.append(client)
            return client
        value.connect = connect
        self.addCleanup(lambda: value.pool is not None and value.pool.close())
        return value

    def test_forwards_reuse_one_pooled_connection(self):
        server = serve(self, clip_engine())
        supervisor = self.supervisor(server)
        for _ in range(3):
            status, data = supervisor.forward(LOOKUP, READINESS)
            self.assertEqual((status, json.loads(data)), (409, worker.READINESS_REPLY))
            self.now[0] += 1
        self.assertEqual(supervisor.forward(EXECUTE, scene.canonical(victoria_body()).encode())[0], 200)
        self.assertEqual((server.accepted, len(supervisor.opened)), (1, 1))
        self.assertIs(supervisor.pool, supervisor.opened[0])

    def test_an_idle_connection_is_replaced_before_the_worker_would_close_it(self):
        server = serve(self, clip_engine())
        supervisor = self.supervisor(server)
        supervisor.forward(LOOKUP, READINESS)
        self.now[0] += launcher.IDLE_RECONNECT_SECONDS + .1
        self.assertEqual(supervisor.forward(LOOKUP, READINESS)[0], 409)
        self.assertEqual((server.accepted, len(supervisor.opened)), (2, 2))
        self.assertIsNone(supervisor.opened[0].sock)

    def test_a_connection_the_worker_closed_is_never_sent_on(self):
        server = serve(self, clip_engine(), timeout=.2)
        supervisor = self.supervisor(server)
        supervisor.forward(LOOKUP, READINESS)
        time.sleep(.6)  # the worker's idle timeout closed it; the supervisor clock has not moved
        self.assertTrue(launcher.readable(supervisor.pool.sock))
        self.assertEqual(supervisor.forward(LOOKUP, READINESS)[0], 409)
        self.assertEqual((server.accepted, len(supervisor.opened)), (2, 2))

    def test_the_last_allowed_clip_drops_the_pooled_connection(self):
        server = serve(self, clip_engine(maximum_requests=1))
        supervisor = self.supervisor(server)
        self.assertEqual(supervisor.forward(EXECUTE, scene.canonical(victoria_body()).encode())[0], 200)
        self.assertIsNone(supervisor.pool)

    def test_a_failed_send_on_a_stale_connection_is_resent_fresh_and_is_no_fault(self):
        supervisor = self.supervisor()
        stale = FakeClient(self.log, send_error=BrokenPipeError(), pooled=True)
        fresh = FakeClient(self.log)
        supervisor.pool, supervisor.pool_used = stale, self.now[0]
        supervisor.connect = lambda: fresh
        with mock.patch.object(launcher, "readable", return_value=False):
            self.assertEqual(supervisor.forward(EXECUTE, b"{}"), (409, b"{}"))
        self.assertEqual(self.log, [("send", stale), ("close", stale), ("send", fresh)])
        self.assertIs(supervisor.pool, fresh)

    def test_a_clip_lost_after_its_bytes_left_is_never_resent(self):
        supervisor = self.supervisor()
        supervisor.connect = mock.Mock(side_effect=AssertionError("an execute is never resent"))
        supervisor.pool = FakeClient(self.log, response_error=http.client.RemoteDisconnected("closed"), pooled=True)
        supervisor.pool_used = self.now[0]
        with mock.patch.object(launcher, "readable", return_value=False):
            with self.assertRaises(http.client.RemoteDisconnected):
                supervisor.forward(EXECUTE, b"{}")
            self.assertIsNone(supervisor.pool)
            # The read-only readiness lookup is resent once on a fresh connection.
            supervisor.pool = FakeClient(self.log, response_error=ConnectionResetError(), pooled=True)
            supervisor.connect = lambda: FakeClient(self.log)
            self.assertEqual(supervisor.forward(LOOKUP, b"{}"), (409, b"{}"))

    def test_a_fresh_connection_failure_is_raised_unchanged(self):
        supervisor = self.supervisor()
        supervisor.connect = lambda: FakeClient(self.log, send_error=ConnectionRefusedError())
        with self.assertRaises(ConnectionRefusedError):
            supervisor.forward(LOOKUP, b"{}")
        self.assertIsNone(supervisor.pool)

    def test_concurrent_forwards_serialise_on_the_pooled_connection(self):
        supervisor = self.supervisor()
        active, peak, entered, release = [0], [0], threading.Event(), threading.Event()

        def gate():
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            entered.set()
            release.wait(5)
            active[0] -= 1
        clients = []
        supervisor.connect = lambda: clients.append(FakeClient(self.log, gate=gate)) or clients[-1]
        results = []
        with mock.patch.object(launcher, "readable", return_value=False):
            first = threading.Thread(target=lambda: results.append(supervisor.forward(LOOKUP, b"{}")))
            second = threading.Thread(target=lambda: results.append(supervisor.forward(LOOKUP, b"{}")))
            first.start()
            self.assertTrue(entered.wait(5))
            second.start()
            time.sleep(.2)
            self.assertEqual(len(self.log), 1)  # the second forward waits on the lock
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual((results, peak[0], len(clients)), ([(409, b"{}")] * 2, 1, 1))
        self.assertEqual(self.log, [("send", clients[0])] * 2)

    def test_a_forward_that_cannot_get_the_connection_is_refused_typed_and_never_sent(self):
        import vjepa_live
        supervisor = self.supervisor()
        supervisor.connect = mock.Mock(side_effect=AssertionError("never sent"))
        with supervisor.pool_lock, mock.patch.object(launcher, "FORWARD_TIMEOUT", .05):
            status, data = through_lifecycle_http(supervisor, EXECUTE, b"{}")
        # Not LifecycleHTTP's 408 body_timeout: the clip never left the supervisor.
        self.assertEqual((status, json.loads(data)), (429, {"reason": "worker_busy"}))
        with self.assertRaises(vjepa_live.VjepaNotExecuted) as caught:
            victoria_sees(status, data)
        self.assertEqual(str(caught.exception), "worker_busy")
        self.assertIsNone(supervisor.pool)


class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.now, self.temperature, self.reads = [100.0], [73], []

    def run_(self, args, timeout=15):
        self.assertEqual(args[0], "nvidia-smi")
        self.reads.append(timeout)
        if self.temperature[0] is None:
            raise subprocess.CalledProcessError(9, args)
        return "28480, %d\n" % self.temperature[0]

    def supervisor(self, threshold=74):
        value = object.__new__(launcher.Launcher)
        value.refuse_gpu_c = threshold
        value.reset_pool()
        value.monotonic = lambda: self.now[0]
        value.token = lambda: TOKEN
        value.connect = mock.Mock(side_effect=AssertionError("no clip reaches the worker"))
        return value

    def test_the_threshold_is_config_driven_with_todays_default(self):
        self.assertEqual(launcher.refuse_gpu_c({}), 74)
        self.assertEqual(launcher.refuse_gpu_c({launcher.REFUSE_GPU_C_ENV: "70"}), 70)
        for bad in ("81", "7", "74.5", "x", "", "074"):
            with self.assertRaises(ValueError):
                launcher.refuse_gpu_c({launcher.REFUSE_GPU_C_ENV: bad})

    def test_clips_are_refused_typed_at_the_rung_and_the_read_is_cached(self):
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "run", self.run_):
            self.assertIsNone(supervisor.thermal_refusal())
            self.temperature[0] = 74
            self.assertIsNone(supervisor.thermal_refusal())  # cached
            self.now[0] += launcher.THERMAL_CACHE_SECONDS
            self.assertEqual(supervisor.thermal_refusal(), "thermal_shed")
            status, data = supervisor.forward(EXECUTE, b"{}")
            self.assertEqual((status, json.loads(data)), (503, {"reason": "thermal_shed"}))
            # The readiness lookup is not a clip and is never refused for heat.
            supervisor.connect = lambda: FakeClient([])
            self.assertEqual(supervisor.forward(LOOKUP, b"{}"), (409, b"{}"))
        self.assertEqual(self.reads, [3, 3])

    def test_an_unreadable_gpu_refuses_closed(self):
        supervisor = self.supervisor()
        self.temperature[0] = None
        with mock.patch.object(launcher, "run", self.run_):
            status, data = supervisor.forward(EXECUTE, b"{}")
        self.assertEqual((status, json.loads(data)), (503, {"reason": "thermal_unavailable"}))

    def test_victoria_counts_the_refusal_as_a_typed_skip(self):
        import vjepa_live
        supervisor = self.supervisor()
        self.temperature[0] = 77
        with mock.patch.object(launcher, "run", self.run_):
            status, data = supervisor.forward(EXECUTE, b"{}")
        with self.assertRaises(vjepa_live.VjepaNotExecuted) as caught:
            victoria_sees(status, data)
        self.assertEqual(str(caught.exception), "thermal_shed")


class WarmStartTests(unittest.TestCase):
    def setUp(self):
        saved = dict(launcher._FAULT_CLEAN)
        self.addCleanup(launcher._FAULT_CLEAN.update, saved)
        launcher._FAULT_CLEAN["until"] = None
        self.scans = []

    def run_(self, fault=False):
        def run(args, timeout=15):
            if args[:2] == ["systemctl", "list-units"]:
                return ""
            if args[0] == "nvidia-smi":
                return "28480, 40\n"
            if args[0] == "journalctl":
                self.scans.append(args[args.index("--since") + 1])
                if fault:
                    return "kernel: Out of memory: Killed process 1\n"
                raise subprocess.CalledProcessError(1, args, output="", stderr="")
            raise AssertionError(args)
        return run

    def preflight(self, fault=False):
        return launcher.preflight(run=self.run_(fault), meminfo=lambda: "MemAvailable: 33554432 kB\n",
                                  hostname=lambda: "EngineeredLightingServer1")

    def test_the_full_scan_runs_at_supervisor_start_and_a_lease_scans_incrementally(self):
        object.__new__(launcher.Launcher).warm_start(run=self.run_()).join(5)
        self.assertEqual(self.scans, [launcher.FAULT_BASELINE])
        self.assertEqual(self.preflight(), (28480, 40))
        self.assertEqual(len(self.scans), 2)
        self.assertTrue(self.scans[1].startswith("@"), self.scans)

    def test_a_fault_at_start_leaves_the_lease_to_rescan_from_the_baseline_and_refuse(self):
        self.assertFalse(launcher.warm_fault_scan(run=self.run_(fault=True)))
        self.assertIsNone(launcher._FAULT_CLEAN["until"])
        with self.assertRaises(ValueError) as caught:
            self.preflight(fault=True)
        self.assertEqual(str(caught.exception), "new fault or unavailable fault check")
        self.assertEqual(self.scans, [launcher.FAULT_BASELINE] * 2)

    def test_a_lease_during_the_start_scan_waits_for_it(self):
        with launcher._FAULT_LOCK:
            lease = threading.Thread(target=self.preflight)
            lease.start()
            time.sleep(.2)
            self.assertEqual(self.scans, [])
        lease.join(5)
        self.assertEqual(self.scans, [launcher.FAULT_BASELINE])


if __name__ == "__main__":
    unittest.main()
