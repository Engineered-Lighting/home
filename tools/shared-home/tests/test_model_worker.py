"""The S1.1 long-lived model worker: the fixed WORKER script run by this Python
against a localhost stand-in for vLLM (it closes idle keep-alive sockets), and
small scripted workers for death, hangs and start failures. No container,
model or host is touched."""
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import camera_pilot_service as service
from home_shared import lab_live_pilot as pilot_live
from home_shared import model_worker
from home_shared.camera_pilot import BeforeDispatchRejected, CameraPilot
from home_shared.model_worker import (ModelWorker, WorkerExited, WorkerFailed, WorkerNotSent, WorkerUnproven,
                                      encode_frame, read_frame, write_all)
from test_gateway import binding_fixture
from test_lab_live_pilot import CONTAINER, PilotLiveFixture, completion

CAMERA_RESULT = json.dumps({"model": "qwen3-vl-30b", "usage": {"completion_tokens": 4},
                            "choices": [{"finish_reason": "stop", "message": {"content": "A quiet den."}}]}).encode()
METRICS = ("# HELP vllm:num_requests_running Number of requests in model execution batches.\n"
           "# TYPE vllm:num_requests_running gauge\n"
           'vllm:num_requests_running{engine="0",model_name="qwen3-vl-30b"} 2.0\n'
           'vllm:num_requests_running{engine="1",model_name="qwen3-vl-30b"} 1.0\n'
           'vllm:num_requests_waiting{engine="0",model_name="qwen3-vl-30b"} 1.0\n'
           "vllm:num_requests_waiting_total 99.0\n").encode()


class FakeVLLM:
    """127.0.0.1 stand-in for vLLM's API server: HTTP/1.1 keep-alive replies,
    idle sockets closed after idle_seconds (as vLLM's keep-alive timeout does),
    every accepted connection recorded."""

    def __init__(self, reply=CAMERA_RESULT, idle_seconds=.05):
        self.reply, self.status, self.hold = reply, 200, None
        self.bodies, self.peers, self.paths = [], [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            timeout = idle_seconds

            def setup(self):
                owner.peers.append(self.client_address)
                super().setup()

            def log_message(self, *args):
                pass

            def answer(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.paths.append(self.path)
                owner.bodies.append(body)
                if owner.hold is not None:
                    owner.hold.wait(10)
                self.answer(owner.status, owner.reply, "application/json")

            def do_GET(self):
                owner.paths.append(self.path)
                self.answer(200, METRICS, "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        if self.hold is not None:
            self.hold.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def local_worker(port):
    """The production WORKER text, run by this interpreter against the fake."""
    script = model_worker.WORKER.replace("8000", str(port))
    return lambda container: [sys.executable, "-I", "-S", "-u", "-c", script]


def scripted(body):
    """A stand-in worker process with the given behaviour after start."""
    script = ("import sys,struct,time\n"
              "out=sys.stdout.buffer\n"
              "def ready():\n"
              " data=b'{\"ready\": 1}';out.write(struct.pack('>I',len(data))+data);out.flush()\n"
              "def take():\n"
              " size=struct.unpack('>I',sys.stdin.buffer.read(4))[0];return sys.stdin.buffer.read(size)\n" + body)
    return lambda container: [sys.executable, "-I", "-S", "-u", "-c", script]


DIES_AFTER_FRAME = scripted("ready()\ntake()\nsys.exit(3)\n")
HANGS = scripted("ready()\ntake()\ntime.sleep(30)\n")
NEVER_READY = scripted("sys.exit(1)\n")
NOT_READING = scripted("ready()\ntime.sleep(30)\n")
ERROR_FRAME = scripted("ready()\ntake()\ndata=b'{\"ok\": 0, \"error\": \"ConnectionRefusedError\"}'\n"
                       "out.write(struct.pack('>I',len(data))+data);out.flush()\ntake()\n")


class Workers:
    def worker(self, command, name="camera"):
        worker = ModelWorker(name, command=command, log=self.logs.append)
        self.addCleanup(worker.close)
        return worker

    def fake(self, **options):
        server = FakeVLLM(**options)
        self.addCleanup(server.close)
        return server


class FramingTests(unittest.TestCase):
    def test_length_prefixed_ascii_json_round_trip(self):
        frame = encode_frame({"op": "chat", "body": "caf\u00e9 \"x\"", "timeout": 60, "limit": 65536})
        self.assertEqual(struct.unpack(">I", frame[:4])[0], len(frame) - 4)
        frame[4:].decode("ascii")
        self.assertEqual(read_frame(io.BytesIO(frame))["body"], "caf\u00e9 \"x\"")

    def test_oversize_truncated_duplicate_and_nonfinite_frames_are_refused(self):
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(struct.pack(">I", model_worker.MAX_REPLY_FRAME + 1)))
        with self.assertRaises(EOFError):
            read_frame(io.BytesIO(encode_frame({"ok": 1})[:-1]))
        with self.assertRaises(EOFError):
            read_frame(io.BytesIO(b"\x00\x00"))
        raw = b'{"ok": 1, "ok": 0}'
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(struct.pack(">I", len(raw)) + raw))
        with self.assertRaises(ValueError):
            encode_frame({"timeout": float("nan")})
        with self.assertRaises(ValueError):
            encode_frame({"body": "x" * 64}, limit=32)

    def test_a_raw_pipe_taking_writes_in_parts_still_gets_every_byte(self):
        class Trickle:
            def __init__(self):
                self.data = b""

            def write(self, view):
                self.data += bytes(view[:3])
                return min(3, len(view))
        stream = Trickle()
        write_all(stream, b"0123456789")
        self.assertEqual(stream.data, b"0123456789")

        class Stuck:
            def write(self, view):
                return 0
        with self.assertRaises(OSError):
            write_all(Stuck(), b"x")

    def test_the_worker_text_is_fixed_and_execs_only_the_pinned_container(self):
        self.assertEqual(model_worker.WORKER.count("8000"), 1)
        self.assertIn("http.client.HTTPConnection('127.0.0.1',8000", model_worker.WORKER)
        self.assertEqual(model_worker.docker_command(CONTAINER),
                         ["docker", "exec", "-i", CONTAINER, "python3", "-I", "-S", "-u", "-c", model_worker.WORKER])
        worker = ModelWorker("camera", spawn=lambda *a, **k: self.fail("must not spawn"))
        for container in ("vllm", "F" * 64, None):
            with self.subTest(container=container), self.assertRaises(WorkerNotSent):
                worker.chat(container, "{}", timeout=60, limit=65536, seconds=65)


class WorkerScriptTests(Workers, unittest.TestCase):
    """The real WORKER text against a fake vLLM that closes idle sockets."""

    def setUp(self):
        self.logs = []

    def test_each_request_opens_a_fresh_loopback_connection_in_one_interpreter(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        bodies = [json.dumps({"n": index, "text": "caf\u00e9"}) for index in range(3)]
        for body in bodies:
            self.assertEqual(worker.chat(CONTAINER, body, timeout=5, limit=65536, seconds=10), CAMERA_RESULT.decode())
            time.sleep(.2)   # longer than the fake's idle close: a pooled socket would now be dead
        self.assertEqual(server.bodies, [body.encode() for body in bodies])
        self.assertEqual(server.paths, ["/v1/chat/completions"] * 3)
        self.assertEqual(len(server.peers), 3)
        self.assertEqual(len({peer[1] for peer in server.peers}), 3)   # three distinct client sockets
        self.assertEqual(worker.starts, 1)
        self.assertEqual(len(self.logs), 1)
        self.assertRegex(self.logs[0], r"^model worker started name=camera starts=1 pid=[0-9]+$")

    def test_metrics_report_running_and_waiting_on_their_own_connection(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port), "live")
        self.assertEqual(worker.metrics(CONTAINER), {"running": 3.0, "waiting": 1.0})
        self.assertEqual(server.paths, ["/metrics"])
        self.assertEqual(worker.chat(CONTAINER, "{}", timeout=5, limit=131072, seconds=10), CAMERA_RESULT.decode())
        self.assertEqual(len(server.peers), 2)

    def test_a_refused_or_oversize_model_answer_is_a_failed_call_and_the_worker_stays(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        server.status = 503
        with self.assertRaises(WorkerFailed):
            worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10)
        server.status = 200
        with self.assertRaises(WorkerFailed):
            worker.chat(CONTAINER, "{}", timeout=5, limit=len(CAMERA_RESULT) - 1, seconds=10)
        self.assertEqual(worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10), CAMERA_RESULT.decode())
        self.assertEqual(worker.starts, 1)

    def test_watchdog_restarts_a_worker_that_died_between_requests(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10)
        process = worker._running.process
        process.kill()
        process.wait(5)
        self.assertEqual(worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10), CAMERA_RESULT.decode())
        self.assertEqual(worker.starts, 2)
        self.assertIn("model worker discarded name=camera reason=exited", self.logs)
        self.assertEqual(len(server.bodies), 2)   # nothing was resent

    def test_a_new_pinned_container_gets_a_new_worker(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10)
        worker.chat("3" * 64, "{}", timeout=5, limit=65536, seconds=10)
        self.assertEqual((worker.starts, worker._running.container), (2, "3" * 64))
        self.assertIn("model worker discarded name=camera reason=container_changed", self.logs)

    def test_an_engine_check_runs_before_every_worker_start_and_only_then(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        checks = []
        for _ in range(2):
            worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10, before_start=lambda: checks.append(1))
        self.assertEqual(len(checks), 1)   # a running worker needs no new check
        worker._running.process.kill()
        worker._running.process.wait(5)
        worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10, before_start=lambda: checks.append(1))
        self.assertEqual((len(checks), worker.starts), (2, 2))

        def refused():
            raise ValueError("vLLM StartedAt changed")
        worker._running.process.kill()
        worker._running.process.wait(5)
        with self.assertRaises(WorkerNotSent):
            worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=10, before_start=refused)
        self.assertEqual((worker.starts, len(server.bodies)), (2, 3))   # nothing started, nothing sent


class DeliveryBoundaryTests(Workers, unittest.TestCase):
    """Only a request frame proven never written is 'not sent'."""

    def setUp(self):
        self.logs = []

    def chat(self, worker, seconds=10, body="{}"):
        return worker.chat(CONTAINER, body, timeout=5, limit=65536, seconds=seconds)

    def test_start_failures_are_not_sent(self):
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("docker")
        for worker in (ModelWorker("camera", spawn=unavailable, log=self.logs.append), self.worker(NEVER_READY)):
            with self.subTest(worker=worker), self.assertRaises(WorkerNotSent):
                self.chat(worker)
        self.assertEqual(worker.starts, 0)

    def test_a_worker_exit_after_the_frame_is_an_exit_not_a_rejection(self):
        worker = self.worker(DIES_AFTER_FRAME)
        with self.assertRaises(WorkerExited):
            self.chat(worker)
        self.assertIsNone(worker._running)
        self.assertIn("model worker discarded name=camera reason=exited", self.logs)

    def test_a_hang_after_the_frame_is_killed_at_the_deadline_and_unproven(self):
        worker = self.worker(HANGS)
        started = time.monotonic()
        with self.assertRaises(WorkerUnproven):
            self.chat(worker, seconds=1.5)
        self.assertLess(time.monotonic() - started, 6)
        self.assertIsNone(worker._running)
        self.assertIn("model worker discarded name=camera reason=deadline", self.logs)

    def test_a_frame_the_worker_never_read_is_not_sent(self):
        worker = self.worker(NOT_READING)
        with self.assertRaises(WorkerNotSent):
            self.chat(worker, seconds=1.5, body="x" * (2 * 1024 * 1024))   # far beyond any pipe buffer

    def test_an_error_frame_is_a_failed_call(self):
        worker = self.worker(ERROR_FRAME)
        with self.assertRaises(WorkerFailed) as caught:
            self.chat(worker)
        self.assertIn("ConnectionRefusedError", str(caught.exception))

    def test_one_request_at_a_time_and_a_busy_worker_is_not_sent(self):
        server = self.fake()
        server.hold = threading.Event()
        worker = self.worker(local_worker(server.port))
        first = threading.Thread(target=lambda: self.chat(worker))
        first.start()
        deadline = time.monotonic() + 5
        while not server.bodies and time.monotonic() < deadline:
            time.sleep(.01)
        with self.assertRaises(WorkerNotSent):
            self.chat(worker, seconds=.3)
        server.hold.set()
        first.join(5)
        self.assertEqual(len(server.bodies), 1)

    def test_no_model_error_is_a_value_error(self):
        # The live consumer maps ValueError to a terminal invalid completion.
        for error in (WorkerNotSent, WorkerFailed, WorkerExited, WorkerUnproven):
            self.assertFalse(issubclass(error, ValueError))


def slow_start(seconds, then="ready()"):
    """A stand-in worker that takes `seconds` to say ready, then answers metrics."""
    return scripted("time.sleep(%r)\n%s\nwhile 1:\n take()\n data=b'{\"ok\": 1, \"running\": 0.0, \"waiting\": 0.0}'\n"
                    " out.write(struct.pack('>I',len(data))+data);out.flush()\n" % (seconds, then))


class ColdStartTests(Workers, unittest.TestCase):
    """A worker start that one short exchange cannot wait out (the S1.4 yield's
    metrics budget) is kept, within its own start bound, for a later exchange."""

    def setUp(self):
        self.logs, self.spawned = [], []

    def counted(self, *commands):
        def command(container):
            self.spawned.append(container)
            return commands[min(len(self.spawned), len(commands)) - 1](container)
        return command

    def wait_ready(self, worker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (worker._running is not None and worker._running.ready.is_set()):
            time.sleep(.02)

    def test_a_start_longer_than_one_exchange_is_kept_and_adopted(self):
        worker = self.worker(self.counted(slow_start(.6)), "live")
        with self.assertRaisesRegex(WorkerNotSent, "still starting"):
            worker.metrics(CONTAINER, timeout=.2, seconds=.3)
        self.assertEqual((worker.starts, len(self.spawned)), (0, 1))
        self.wait_ready(worker)
        self.assertEqual(worker.metrics(CONTAINER, timeout=.2, seconds=.3), {"running": 0.0, "waiting": 0.0})
        self.assertEqual((worker.starts, len(self.spawned)), (1, 1))
        self.assertEqual(len(self.logs), 1)
        self.assertRegex(self.logs[0], r"^model worker started name=live starts=1 pid=[0-9]+$")

    def test_a_metrics_exchange_keeps_its_own_call_timeout_after_a_start(self):
        # Ready after 0.3 s: a 1 s exchange with a 0.8 s call timeout waits at
        # most 0.2 s for the start, so the call is never squeezed into the rest.
        worker = self.worker(self.counted(slow_start(.3)), "live")
        started = time.monotonic()
        with self.assertRaisesRegex(WorkerNotSent, "still starting"):
            worker.metrics(CONTAINER, timeout=.8, seconds=1.0)
        self.assertLess(time.monotonic() - started, .8)
        self.wait_ready(worker)
        self.assertEqual(worker.metrics(CONTAINER, timeout=.8, seconds=1.0)["waiting"], 0.0)
        self.assertEqual(len(self.spawned), 1)

    def test_a_start_past_its_own_bound_is_discarded_and_proves_nothing_sent(self):
        worker = ModelWorker("live", command=self.counted(slow_start(30)), log=self.logs.append, start_seconds=.3)
        self.addCleanup(worker.close)
        with self.assertRaises(WorkerNotSent):
            worker.metrics(CONTAINER, timeout=.1, seconds=.2)
        process = worker._running.process
        time.sleep(.4)
        with self.assertRaisesRegex(WorkerNotSent, "did not start"):
            worker.metrics(CONTAINER, timeout=.1, seconds=.2)
        self.assertIsNone(worker._running)
        self.assertIsNotNone(process.wait(5))   # killed
        self.assertIn("model worker discarded name=live reason=start_expired", self.logs)
        self.assertEqual(worker.starts, 0)

    def test_a_failed_kept_start_is_replaced_in_the_same_exchange(self):
        worker = self.worker(self.counted(slow_start(.3, "sys.exit(1)"), slow_start(0)), "live")
        with self.assertRaises(WorkerNotSent):
            worker.metrics(CONTAINER, timeout=.1, seconds=.2)
        self.wait_ready(worker)
        checks = []
        self.assertEqual(worker.metrics(CONTAINER, timeout=1, seconds=4, before_start=lambda: checks.append(1)),
                         {"running": 0.0, "waiting": 0.0})
        self.assertEqual((len(self.spawned), worker.starts, len(checks)), (2, 1, 1))
        self.assertIn("model worker discarded name=live reason=start_failed", self.logs)

    def test_a_chat_that_outlasts_the_start_bound_never_keeps_a_start(self):
        # Camera chat (65 s exchanges) and live chat waited for the whole start
        # bound before; a start that never readies is still stopped at once.
        worker = ModelWorker("camera", command=self.counted(slow_start(30)), log=self.logs.append, start_seconds=.3)
        self.addCleanup(worker.close)
        with self.assertRaisesRegex(WorkerNotSent, "did not start"):
            worker.chat(CONTAINER, "{}", timeout=5, limit=65536, seconds=5)
        self.assertIsNone(worker._running)
        self.assertEqual(worker.starts, 0)

    def test_a_failed_engine_check_is_marked(self):
        worker = ModelWorker("live", spawn=lambda *a, **k: self.fail("must not start"), log=self.logs.append)

        def refused():
            raise ValueError("vLLM StartedAt changed")
        with self.assertRaises(WorkerNotSent) as caught:
            worker.metrics(CONTAINER, before_start=refused)
        self.assertIs(caught.exception.engine_check, True)
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertEqual(type(caught.exception), WorkerNotSent)   # log lines keep 'WorkerNotSent'
        with self.assertRaises(WorkerNotSent) as caught:
            worker.metrics("F" * 64)
        self.assertIs(caught.exception.engine_check, False)


class CameraWorkerTests(Workers, unittest.TestCase):
    """camera_pilot_service.infer through CAMERA_WORKER, under CameraPilot."""

    def setUp(self):
        self.logs = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target in (patch.object(service, 'model_health'), patch.object(service, 'fresh_health'),
                       patch.object(service, 'ENGINE', SimpleNamespace(container_id=CONTAINER))):
            target.start()
            self.addCleanup(target.stop)
        self.now = time.time()
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=service.infer, model_revision="pinned", clock=lambda: self.now,
                                 image_validator=lambda _: None, free_space_floor=0)
        self.addCleanup(self.pilot.close)

    def use(self, worker):
        target = patch.object(service, 'CAMERA_WORKER', worker)
        target.start()
        self.addCleanup(target.stop)

    def camera(self, rid):
        from test_gateway import request_fixture
        return {**request_fixture(now=self.now, request_id=rid), "task_class": "interactive"}

    def run_request(self, rid="camera-1"):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.pilot.submit(self.camera(rid), binding_fixture())
            self.pilot.worker.join(10)
        self.assertFalse(self.pilot.worker.is_alive())
        return self.pilot.status(rid, binding_fixture()), stderr.getvalue()

    def test_the_same_bounded_request_reaches_the_worker(self):
        calls = []

        class Recording:
            def chat(self, container, body, **kwargs):
                calls.append((container, json.loads(body), kwargs))
                return CAMERA_RESULT.decode()
        self.use(Recording())
        status, _ = self.run_request()
        self.assertEqual((status["status"], status["outcome"]), ("terminal", "completed"))
        container, body, kwargs = calls[0]
        self.assertEqual(container, CONTAINER)
        self.assertEqual((body["max_tokens"], body["stream"], body["model"]), (128, False, "qwen3-vl-30b"))
        self.assertEqual(kwargs, {"timeout": 60, "limit": 65536, "seconds": 65,
                                  "before_start": service.fresh_health})

    def test_worker_death_mid_request_is_indeterminate_and_closes_camera_chat(self):
        self.use(self.worker(DIES_AFTER_FRAME))
        status, log = self.run_request()
        self.assertEqual((status["status"], status["outcome"]), ("indeterminate", "upstream_outcome_unknown"))
        self.assertTrue(self.pilot.closed)
        self.assertFalse(self.pilot.readiness()["ready"])
        self.assertIn("camera pilot execution unresolved: WorkerExited", log)

    def test_a_killed_hung_worker_is_indeterminate_too(self):
        class Hung:
            def chat(self, *args, **kwargs):
                raise WorkerUnproven("no worker reply by the deadline")
        self.use(Hung())
        status, _ = self.run_request()
        self.assertEqual(status["status"], "indeterminate")
        self.assertTrue(self.pilot.closed)

    def test_a_worker_that_cannot_start_is_a_terminal_rejection_and_keeps_camera_open(self):
        self.use(self.worker(NEVER_READY))
        status, log = self.run_request()
        self.assertEqual((status["status"], status["outcome"]), ("terminal", "preflight_rejected"))
        self.assertFalse(self.pilot.closed)
        self.assertIn("camera pilot preflight rejected: WorkerNotSent", log)

    def test_watchdog_restart_between_camera_requests(self):
        server = self.fake()
        worker = self.worker(local_worker(server.port))
        self.use(worker)
        self.assertEqual(self.run_request("camera-1")[0]["outcome"], "completed")
        worker._running.process.kill()
        worker._running.process.wait(5)
        self.now += 31
        self.assertEqual(self.run_request("camera-2")[0]["outcome"], "completed")
        self.assertEqual(worker.starts, 2)
        self.assertFalse(self.pilot.closed)
        self.assertEqual(len(server.peers), 2)


class LiveWorkerTests(Workers, PilotLiveFixture, unittest.TestCase):
    """docker_backend through the live consumer's own worker."""

    def setUp(self):
        self.logs = []

    def backend(self, worker, health=lambda: None):
        return pilot_live.docker_backend(CONTAINER, health, clock=lambda: 1000, worker=worker)

    def test_exact_live_bytes_through_the_worker_and_metrics(self):
        server = self.fake(reply=completion())
        execute = self.backend(self.worker(local_worker(server.port), "live"))
        body = json.dumps({"model": "qwen3-vl-30b", "messages": [{"role": "user", "content": "\u00e9"}]},
                          ensure_ascii=True, separators=(",", ":")).encode()
        self.assertEqual(execute(body, 1007), completion())
        self.assertEqual(hashlib.sha256(server.bodies[0]).hexdigest(), hashlib.sha256(body).hexdigest())
        self.assertEqual(execute.metrics(), {"running": 3.0, "waiting": 1.0})
        self.assertFalse(hasattr(pilot_live.docker_backend(CONTAINER, lambda: None), "metrics"))

    def test_the_per_request_mapping_is_kept(self):
        class Stub:
            def __init__(self, error):
                self.error, self.calls = error, []

            def chat(self, container, text, **kwargs):
                self.calls.append(kwargs)
                raise self.error
        expected = ((WorkerNotSent("x"), BeforeDispatchRejected), (WorkerFailed("x"), pilot_live.UpstreamFinished),
                    (WorkerExited("x"), pilot_live.UpstreamFinished), (WorkerUnproven("x"), WorkerUnproven))
        for error, raised in expected:
            stub = Stub(error)
            with self.subTest(error=error), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(raised):
                self.backend(stub)(b"{}", 1007)
            self.assertEqual([{k: v for k, v in call.items() if k != "before_start"} for call in stub.calls],
                             [{"timeout": 8, "limit": 131072, "seconds": 7 + pilot_live.EXECUTION_GRACE_SECONDS}])
            self.assertTrue(callable(stub.calls[0]["before_start"]))
        stub = Stub(AssertionError("must not run"))

        def health():
            raise ValueError("pilot resource guard closed")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(BeforeDispatchRejected):
            self.backend(stub, health)(b"{}", 1007)
        self.assertEqual(stub.calls, [])

    def test_a_worker_start_after_a_re_pin_is_refused_before_anything_runs(self):
        engine = SimpleNamespace(engine_epoch="live-fixture-epoch")

        def fresh():
            engine.engine_epoch = "vllm-333333333333-20261008T031500Z"   # this check accepted a re-pin
        worker = ModelWorker("live", spawn=lambda *a, **k: self.fail("must not start"), log=self.logs.append)
        execute = pilot_live.docker_backend(CONTAINER, lambda: None, clock=lambda: 1000, worker=worker,
                                            fresh_health=pilot_live._epoch_bound(fresh, engine, "live-fixture-epoch"))
        with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(BeforeDispatchRejected):
            execute(b"{}", 1007)
        self.assertEqual(stderr.getvalue(), "live scene preflight rejected: WorkerNotSent\n")
        self.assertEqual(worker.starts, 0)

    def test_a_live_hang_does_not_block_camera_chat(self):
        live_server = self.fake(reply=completion())
        live_server.hold = threading.Event()
        camera_server = self.fake()
        live_worker = self.worker(local_worker(live_server.port), "live")
        camera_worker = self.worker(local_worker(camera_server.port))
        self.build(live_options={"backend": pilot_live.docker_backend(CONTAINER, lambda: None, worker=live_worker)})
        self.addCleanup(self.teardown)
        self.pilot.backend = service.infer
        for target in (patch.object(service, 'model_health'), patch.object(service, 'fresh_health'),
                       patch.object(service, 'CAMERA_WORKER', camera_worker),
                       patch.object(service, 'ENGINE', SimpleNamespace(container_id=CONTAINER))):
            target.start()
            self.addCleanup(target.stop)
        self.live.submit(self.envelope(), self.binding)
        deadline = time.monotonic() + 5
        while not live_server.bodies and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(live_server.bodies), 1)   # the live request is in the model and held
        started = time.monotonic()
        self.pilot.submit(self.camera(), binding_fixture())
        self.pilot.worker.join(5)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(self.pilot.status("camera-1", binding_fixture())["outcome"], "completed")
        self.assertTrue(self.live.worker.is_alive())   # still held
        live_server.hold.set()
        self.finish()
        self.assertEqual(self.pilot.db.execute("SELECT outcome FROM live_requests").fetchone()[0], "completed")


if __name__ == "__main__":
    unittest.main()
