"""Speed plan S1.8 on the LA live-scene consumer: an unknown live outcome is
reconciled to terminal unknown_resolved only after the long-lived live worker
proves its exchange ended, then live reopens at most live_reopens_per_hour
times per rolling hour. Fakes, scripted stand-in workers and localhost TLS
fixtures only: no container, model, host or credential."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import lab_live_pilot as pilot_live
from home_shared import lab_live_scene as live
from home_shared import model_worker
from home_shared.camera_pilot import CameraPilot
from home_shared.model_worker import Linger, ModelWorker, WorkerNotSent, WorkerUnproven
from home_shared.pilot_runtime import RuntimeOptions
from test_gateway import binding_fixture
from test_lab_live_pilot import CONTAINER, FIXTURES, Envelopes, Model, PilotLiveFixture, completion, config_value
from test_lab_live_scene import EPOCH
from test_model_worker import HANGS, NOT_READING, Workers, scripted
from test_pilot_sync_reply import VICTORIA, Serving, fingerprint, tls_options, victoria_live_transport

# Each answers (or exits) 2 s after reading the frame; the tests' host deadline
# is 1.5 s, which a stand-in worker start fits in.
LATE_REPLY = scripted("ready()\ntake()\ntime.sleep(2.0)\ndata=b'{\"ok\": 1, \"body\": \"late\"}'\n"
                      "out.write(struct.pack('>I',len(data))+data);out.flush()\ntake()\n")
LATE_ERROR = scripted("ready()\ntake()\ntime.sleep(2.0)\ndata=b'{\"ok\": 0, \"error\": \"timeout\"}'\n"
                      "out.write(struct.pack('>I',len(data))+data);out.flush()\ntake()\n")
EXITS_LATER = scripted("ready()\ntake()\ntime.sleep(2.0)\nsys.exit(0)\n")


class FakeLinger:
    """The ended attribute of an unknown outcome: wait() -> proven ended."""

    def __init__(self, result=True, gate=None):
        self.result, self.gate, self.waits, self.abandoned = result, gate, 0, False

    def wait(self):
        self.waits += 1
        if self.gate is not None:
            self.gate.wait(5)
        return self.result and not self.abandoned

    def abandon(self):
        self.abandoned = True
        if self.gate is not None:
            self.gate.set()


class ReconcileFixture(PilotLiveFixture):
    def setUp(self):
        self.tick, self.health_error, self.epoch = [1000.0], None, None
        self.verdict = SimpleNamespace(gpu_c=60, tctl_c=50.0)
        self.compose()

    def tearDown(self):
        self.teardown()

    def compose(self, runtime=None):
        def health():
            if self.health_error is not None:
                raise self.health_error
        self.epoch = EPOCH
        self.build(live_options={"health": health, "runtime": runtime or RuntimeOptions(),
                                 "monotonic": lambda: self.tick[0], "thermal": lambda: self.verdict,
                                 "engine_epoch": lambda: self.epoch})

    def unknown(self, linger):
        """One live request whose outcome is unknown, its worker kept as linger."""
        self.model.error = WorkerUnproven("no worker reply by the deadline", linger)
        envelope = self.envelope()
        self.live.submit(envelope, self.binding)
        return envelope

    def row(self):
        return tuple(self.pilot.db.execute("SELECT state,outcome FROM live_requests ORDER BY accepted DESC, rowid DESC")
                     .fetchone())

    def wait_for_row(self, state):
        deadline = time.monotonic() + 5
        while self.rows() == 0 or self.row()[0] != state:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)


class ReconcileTests(ReconcileFixture, unittest.TestCase):
    def test_an_ended_exchange_becomes_unknown_resolved_with_no_answer_and_live_reopens(self):
        envelope = self.unknown(FakeLinger(True))
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["terminal"], proof["reason"]), ("failed", True, "unknown_resolved"))
        self.assertNotIn("response", proof)
        self.assertFalse(self.live.closed)
        self.assertEqual([line for line in self.logs if not line.startswith("live scene backend")],
                         ["live scene execution unresolved: WorkerUnproven",
                          "live scene unknown outcome resolved: unknown_resolved",
                          "live scene admission reopened: unknown_resolved reopens=1"])
        self.model.error = None
        self.now += 1
        self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")
        self.finish()

    def test_the_row_turns_terminal_only_after_the_exchange_ended(self):
        gate = threading.Event()
        envelope = self.unknown(FakeLinger(True, gate))
        self.wait_for_row("indeterminate")
        self.assertEqual(self.live.lookup(self.reference(envelope), self.binding)["state"], "indeterminate")
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.rows(), 1)
        self.assertTrue(self.live.worker.is_alive())   # waiting for the worker's word
        gate.set()
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))

    def test_no_proof_keeps_the_row_indeterminate_and_live_closed(self):
        self.unknown(FakeLinger(False))
        self.finish()
        self.assertEqual(self.row(), ("indeterminate", "upstream_outcome_unknown"))
        self.assertTrue(self.live.closed)
        self.assertIn("live scene unknown outcome unresolved: exchange_not_ended", self.logs)
        self.model.error = None
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))

    def test_an_unknown_outcome_without_a_kept_worker_is_never_reconciled(self):
        self.model.error = WorkerUnproven("no worker reply by the deadline")   # camera-style kill: ended None
        self.live.submit(self.envelope(), self.binding)
        self.finish()
        self.assertEqual(self.row(), ("indeterminate", "upstream_outcome_unknown"))
        self.assertFalse(any("unknown outcome" in line for line in self.logs))

    def test_reopens_are_capped_per_rolling_hour(self):
        for index in range(4):
            self.tick[0] += 60
            self.now += 1
            self.unknown(FakeLinger(True))
            self.finish()
            self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        self.assertEqual([line for line in self.logs if line.startswith("live scene admission")],
                         ["live scene admission reopened: unknown_resolved reopens=%d" % n for n in (1, 2, 3)]
                         + ["live scene admission stays closed: reopen_limit"])
        self.model.error = None
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))

    def test_the_hour_rolls(self):
        for offset in (0, 10, 20, 3600, 3610):
            self.tick[0] = 1000.0 + offset
            self.now += 1
            self.unknown(FakeLinger(True))
            self.finish()
        self.assertEqual([line.rsplit(" ", 1)[1] for line in self.logs if "admission reopened" in line],
                         ["reopens=1", "reopens=2", "reopens=3", "reopens=3", "reopens=3"])

    def admission_lines(self):
        return [line for line in self.logs if line.startswith("live scene admission")]

    def test_a_dirty_verdict_defers_the_reopen_and_a_later_submission_retries_it(self):
        self.health_error = ValueError("pilot resource guard closed")
        self.unknown(FakeLinger(True))
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        self.assertTrue(self.live.closed)
        self.assertEqual(self.admission_lines(), ["live scene admission reopen deferred: health"])
        # Still unhealthy: refused before any write, the deferral repeated at
        # most every HEALTH_LOG_SECONDS.
        self.model.error = None
        for advance in (30, 31):
            self.tick[0] += advance
            self.now += 1
            self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.rows(), 1)
        self.assertEqual(self.admission_lines(), ["live scene admission reopen deferred: health"] * 2)
        # The verdict clears: the next submission reopens live and is admitted.
        self.health_error = None
        self.now += 1
        self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")
        self.finish()
        self.assertEqual(self.row(), ("terminal", "completed"))
        self.assertFalse(self.live.closed)
        self.assertEqual(self.admission_lines()[-1], "live scene admission reopened: unknown_resolved reopens=1")
        self.assertFalse(any("stays closed" in line for line in self.logs))
        self.assertIsNone(self.live._reopen_pending)

    def test_a_deferred_reopen_still_counts_against_the_hourly_limit(self):
        self.teardown()
        self.compose(RuntimeOptions(live_reopens_per_hour=1))
        self.health_error = ValueError("pilot resource guard closed")
        self.unknown(FakeLinger(True))
        self.finish()
        self.health_error = None
        self.now += 1
        self.unknown(FakeLinger(True))   # the retry reopens; this request is unknown again
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        self.assertEqual(self.admission_lines(), ["live scene admission reopen deferred: health",
                                                  "live scene admission reopened: unknown_resolved reopens=1",
                                                  "live scene admission stays closed: reopen_limit"])
        self.assertIsNone(self.live._reopen_pending)   # final: no more retries
        self.model.error = None
        self.now += 1
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(len(self.admission_lines()), 3)

    def test_a_deferred_reopen_never_outlives_another_close_reason(self):
        self.health_error = ValueError("pilot resource guard closed")
        self.unknown(FakeLinger(True))
        self.finish()
        self.epoch = "vllm-333333333333-20261008T031500Z"   # an accepted re-pin meanwhile
        self.health_error = None
        self.model.error = None
        self.now += 1
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.admission_lines(), ["live scene admission reopen deferred: health",
                                                  "live scene admission closed: engine_changed",
                                                  "live scene admission stays closed: engine_changed"])
        self.assertIsNone(self.live._reopen_pending)
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(len(self.admission_lines()), 3)

    def test_heat_never_blocks_the_reopen_and_each_hot_request_is_shed(self):
        for gpu in (77, 79):
            with self.subTest(gpu=gpu):
                self.teardown()
                self.setUp()
                gate = threading.Event()
                self.unknown(FakeLinger(True, gate))
                self.wait_for_row("indeterminate")
                self.verdict = SimpleNamespace(gpu_c=gpu, tctl_c=50.0)   # heated up meanwhile
                gate.set()
                self.finish()
                self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
                self.assertFalse(self.live.closed)
                self.assertIn("live scene admission reopened: unknown_resolved reopens=1", self.logs)
                # Hot: every live submission is shed before any write.
                self.model.error = None
                self.now += 1
                self.assertEqual(self.refused(self.envelope()), (503, "thermal_shed"))
                self.assertEqual(self.rows(), 1)
                # Cooled down: the next tick is admitted.
                self.verdict = SimpleNamespace(gpu_c=70, tctl_c=50.0)
                self.now += 1
                self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")
                self.finish()
                self.assertEqual(self.row(), ("terminal", "completed"))
        self.assertFalse(any("stays closed" in line for line in self.logs))

    def test_any_other_close_reason_is_never_undone(self):
        gate = threading.Event()
        self.unknown(FakeLinger(True, gate))
        self.wait_for_row("indeterminate")
        self.epoch = "vllm-333333333333-20261008T031500Z"   # an accepted re-pin meanwhile
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        gate.set()
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        self.assertTrue(self.live.closed)
        self.assertIn("live scene admission stays closed: engine_changed", self.logs)

    def test_shutdown_abandons_the_wait_and_the_row_stays_unresolved(self):
        linger = FakeLinger(True, threading.Event())
        self.unknown(linger)
        self.wait_for_row("indeterminate")
        deadline = time.monotonic() + 5
        while self.live._lingering is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.live.close(5))
        self.assertTrue(linger.abandoned)
        self.assertEqual(self.row(), ("indeterminate", "upstream_outcome_unknown"))
        self.assertIn("live scene unknown outcome unresolved: stopping", self.logs)

    def test_zero_reopens_per_hour_is_the_previous_rule(self):
        self.teardown()
        self.compose(RuntimeOptions(live_reopens_per_hour=0))
        linger = FakeLinger(True)
        self.unknown(linger)
        self.finish()
        self.assertEqual((linger.waits, linger.abandoned), (0, True))
        self.assertEqual(self.row(), ("indeterminate", "upstream_outcome_unknown"))
        self.assertTrue(self.live.closed)

    def test_camera_semantics_are_unchanged(self):
        gate = threading.Event()
        self.unknown(FakeLinger(True, gate))
        self.wait_for_row("indeterminate")

        def unknown_camera(request):
            raise TimeoutError("camera exchange unproven")
        self.pilot.backend = unknown_camera
        with contextlib.redirect_stderr(io.StringIO()):
            self.pilot.submit(self.camera(), binding_fixture())
            self.pilot.worker.join(5)
        gate.set()
        self.finish()
        self.assertEqual(self.row(), ("terminal", "unknown_resolved"))
        self.assertEqual(self.pilot.status("camera-1", binding_fixture())["status"], "indeterminate")
        self.assertTrue(self.pilot.closed)
        self.assertFalse(self.pilot.readiness()["ready"])
        self.model.error = None
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))


class LingerTests(Workers, unittest.TestCase):
    """model_worker.ModelWorker.chat(linger=...) with scripted stand-in workers."""

    def setUp(self):
        self.logs = []

    def chat(self, worker, seconds=1.5, linger=5, body="{}"):
        return worker.chat(CONTAINER, body, timeout=5, limit=65536, seconds=seconds, linger=linger)

    def lingering(self, command, **options):
        worker = self.worker(command, "live")
        with self.assertRaises(WorkerUnproven) as caught:
            self.chat(worker, **options)
        self.assertIsInstance(caught.exception.ended, Linger)
        self.assertNotIn("model worker discarded name=live reason=deadline", self.logs)
        return worker, caught.exception.ended

    def test_a_late_reply_or_error_frame_proves_the_exchange_ended(self):
        for command in (LATE_REPLY, LATE_ERROR):
            self.logs.clear()
            worker, ended = self.lingering(command)
            with self.assertRaises(WorkerNotSent):   # busy while it lingers; nothing else starts
                self.chat(worker, seconds=.2, linger=0)
            self.assertEqual(worker.starts, 1)
            self.assertTrue(ended.wait())
            self.assertIsNone(worker._running)
            self.assertIn("model worker discarded name=live reason=lingered", self.logs)

    def test_a_worker_exiting_without_a_reply_proves_nothing(self):
        # The docker CLI's exit status may be its own (1 when it lost dockerd)
        # while the exec'd call still runs: only a reply frame is proof.
        worker, ended = self.lingering(EXITS_LATER)
        self.assertFalse(ended.wait())
        self.assertIn("model worker discarded name=live reason=exited", self.logs)
        self.assertIsNone(worker._running)

    def test_silence_until_the_bound_proves_nothing(self):
        worker, ended = self.lingering(HANGS, linger=.5)
        started = time.monotonic()
        self.assertFalse(ended.wait())
        self.assertLess(time.monotonic() - started, 4)
        self.assertIn("model worker discarded name=live reason=linger_expired", self.logs)

    def test_abandon_kills_the_worker_and_proves_nothing(self):
        worker, ended = self.lingering(HANGS, linger=30)
        timer = threading.Timer(.2, ended.abandon)
        timer.start()
        started = time.monotonic()
        self.assertFalse(ended.wait())
        self.assertLess(time.monotonic() - started, 5)
        timer.join(5)
        self.assertIn("model worker discarded name=live reason=abandoned", self.logs)
        ended.abandon()   # settling twice is harmless

    def test_an_unsettled_linger_is_discarded_past_its_bound(self):
        worker, _ = self.lingering(HANGS, linger=.3)
        time.sleep(.4)
        with self.assertRaises(WorkerUnproven):
            self.chat(worker, linger=0)
        self.assertIn("model worker discarded name=live reason=linger_expired", self.logs)
        self.assertEqual(worker.starts, 2)

    def test_camera_style_chat_never_lingers(self):
        worker = self.worker(HANGS)
        with self.assertRaises(WorkerUnproven) as caught:
            self.chat(worker, linger=0)
        self.assertIsNone(caught.exception.ended)
        self.assertIn("model worker discarded name=camera reason=deadline", self.logs)

    def test_a_frame_not_written_by_the_deadline_is_still_not_sent(self):
        worker = self.worker(NOT_READING, "live")
        with self.assertRaises(WorkerNotSent):
            self.chat(worker, seconds=1.5, body="x" * (2 * 1024 * 1024))

    def test_only_a_reply_frame_counts(self):
        def linger(error, status, killed=False):
            done = threading.Event()
            done.set()
            running = SimpleNamespace(killed=killed, linger_until=time.monotonic() + 5,
                                      process=SimpleNamespace(wait=lambda timeout: status))
            settled = []
            owner = SimpleNamespace(_clock=time.monotonic, _settle=lambda running, reason: settled.append(reason))
            return Linger(owner, running, SimpleNamespace(done=done, error=error)).wait(), settled
        # No exit status of the docker CLI is proof, whatever it reports.
        for status in (0, 1, 137, -9):
            with self.subTest(status=status):
                self.assertEqual(linger(EOFError(), status), (False, ["exited"]))
        self.assertEqual(linger(EOFError(), 0, killed=True), (False, ["exited"]))
        self.assertEqual(linger(ValueError("frame exceeds limit"), 0), (False, ["failed"]))
        self.assertEqual(linger(None, None), (True, ["lingered"]))   # a reply frame, ok or error

    def test_an_out_of_bound_linger_is_refused_unsent(self):
        worker = self.worker(LATE_REPLY, "live")
        for value in (-1, model_worker.MAX_LINGER_SECONDS + 1, True):
            with self.subTest(value=value), self.assertRaises(WorkerNotSent):
                self.chat(worker, linger=value)
        self.assertEqual(worker.starts, 0)


class LiveWorkerReconcileTests(Workers, PilotLiveFixture, unittest.TestCase):
    """docker_backend(linger=...) and the live consumer with a real worker process."""

    def setUp(self):
        self.logs = []

    def test_a_late_answer_ends_the_unknown_outcome_without_becoming_an_answer(self):
        self.build()
        self.addCleanup(self.teardown)
        worker = self.worker(LATE_REPLY, "live")   # after build(): logs into this build's list
        self.live._health = lambda: None
        # 1.4 s left on the request, so the worker's own deadline passes first.
        self.live._backend = pilot_live.docker_backend(CONTAINER, lambda: None, clock=lambda: self.now + 5.6,
                                                       worker=worker, linger=10)
        with patch.object(pilot_live, "EXECUTION_GRACE_SECONDS", .1):
            envelope = self.envelope()
            self.live.submit(envelope, self.binding)
            self.live.worker.join(10)
        self.assertFalse(self.live.worker.is_alive())
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["reason"]), ("failed", "unknown_resolved"))
        self.assertFalse(self.live.closed)
        self.assertIn("model worker discarded name=live reason=lingered", self.logs)
        self.assertIn("live scene admission reopened: unknown_resolved reopens=1", self.logs)

    def test_a_worker_exit_without_a_reply_leaves_the_row_indeterminate(self):
        self.build()
        self.addCleanup(self.teardown)
        worker = self.worker(EXITS_LATER, "live")
        self.live._health = lambda: None
        self.live._backend = pilot_live.docker_backend(CONTAINER, lambda: None, clock=lambda: self.now + 5.6,
                                                       worker=worker, linger=10)
        with patch.object(pilot_live, "EXECUTION_GRACE_SECONDS", .1):
            envelope = self.envelope()
            self.live.submit(envelope, self.binding)
            self.live.worker.join(10)
        self.assertFalse(self.live.worker.is_alive())
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["terminal"]), ("indeterminate", False))
        self.assertTrue(self.live.closed)
        self.assertIn("live scene unknown outcome unresolved: exchange_not_ended", self.logs)
        self.assertNotIn("live scene admission reopened: unknown_resolved reopens=1", self.logs)

    def test_linger_is_passed_only_when_set(self):
        class Stub:
            def __init__(self):
                self.calls = []

            def chat(self, container, text, **kwargs):
                self.calls.append(kwargs)
                return completion().decode()

            def metrics(self, container, **kwargs):
                self.calls.append(kwargs)
                return {"running": 0.0, "waiting": 0.0}
        stub = Stub()
        execute = pilot_live.docker_backend(CONTAINER, lambda: None, clock=lambda: 1000, worker=stub, linger=60)
        execute(b"{}", 1007)
        self.assertEqual(stub.calls[0]["linger"], 60)
        execute.metrics(timeout=1.0, seconds=3.5)
        self.assertEqual({k: v for k, v in stub.calls[1].items() if k != "before_start"}, {"timeout": 1.0, "seconds": 3.5})
        execute.metrics()
        self.assertEqual(stub.calls[2]["seconds"], 2.0)
        with self.assertRaises(ValueError):
            pilot_live.docker_backend(CONTAINER, lambda: None, worker=stub, linger=pilot_live.LIVE_LINGER_SECONDS + 1)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.private = Path(self.temp.name)
        self.pilot = CameraPilot(self.private / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda _: "x", model_revision="pinned", image_validator=lambda _: None,
                                 free_space_floor=0)
        (self.private / "live-client.pem").write_bytes((FIXTURES / "client.pem").read_bytes())
        (self.private / "live-scene.json").write_text(json.dumps(config_value()))

    def tearDown(self):
        self.pilot.close()
        self.temp.cleanup()

    def compose(self, **options):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            consumer = pilot_live.compose_pilot_live(self.private, self.pilot, {"c" * 64: binding_fixture()},
                                                     container=CONTAINER, model_health=lambda: None,
                                                     enforce_linux=False, **options)
        self.addCleanup(consumer.close)
        return consumer, stderr.getvalue()

    def test_the_guards_follow_the_runtime_and_need_the_live_worker(self):
        worker, reading = ModelWorker("live", spawn=lambda *a, **k: self.fail("no start")), (lambda: None)
        runtime = RuntimeOptions(yield_running_threshold=2)
        consumer, log = self.compose(worker=worker, runtime=runtime, verdict=reading)
        self.assertIs(consumer._runtime, runtime)
        self.assertIs(consumer._thermal, reading)
        self.assertTrue(callable(consumer._metrics) and callable(consumer._health))
        self.assertNotIn("yield unavailable", log)
        consumer, log = self.compose(worker=worker, runtime=RuntimeOptions(yield_enabled=False))
        self.assertIsNone(consumer._metrics)
        consumer, log = self.compose()   # per-request exec: no metrics, no linger
        self.assertIsNone(consumer._metrics)
        self.assertIn("live scene yield unavailable: no live model worker\n", log)


@unittest.skipUnless((VICTORIA / "qwen_live.py").is_file(), "Victoria perception-lab sources not in this checkout")
class VictoriaContractTests(Serving, unittest.TestCase):
    """What Victoria's unchanged live transport makes of the new LA answers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs, self.model = [], Model()
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda request: "An empty den.", model_revision="pinned",
                                 image_validator=lambda _: None, free_space_floor=0)
        self.addCleanup(self.pilot.close)
        path = Path(self.temp.name) / "live-scene.json"
        path.write_text(json.dumps(config_value()))
        self.verdict, self.counts = SimpleNamespace(gpu_c=60, tctl_c=50.0), {"running": 0.0, "waiting": 0.0}
        self.live = pilot_live.PilotLiveScene(self.pilot, pilot_live.load_live_config(path), backend=self.model,
                                              log=self.logs.append, thermal=lambda: self.verdict,
                                              metrics=lambda **kw: dict(self.counts), health=lambda: None)
        self.addCleanup(self.live.close, 5)
        server = pilot_live.PilotLiveIngress(("127.0.0.1", 0), self.pilot, self.live,
                                             **tls_options({fingerprint("client.pem"): self.live.binding,
                                                            "0" * 64: binding_fixture()}))
        port = self.serve(server)
        context = ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))
        context.check_hostname = False
        context.load_cert_chain(str(FIXTURES / "client.pem"), str(FIXTURES / "client-test-only.key"))
        config = {"hostname": "localhost", "port": port, "ca_file": str(FIXTURES / "ca.pem"),
                  "client_certificate": str(FIXTURES / "client.pem"),
                  "client_key": str(FIXTURES / "client-test-only.key"), "deadline_seconds": 7,
                  "response_grace_seconds": 2, "poll_seconds": .1}
        self.transport = victoria_live_transport()(
            config, connection_factory=lambda timeout: http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                                                                    context=context))
        self.qwen_live = sys.modules["qwen_live"]
        self.not_executed = sys.modules["qwen_transport"].QwenNotExecuted

    def envelope(self):
        return Envelopes().envelope(deadline=time.time() + 7)

    def test_heat_and_a_busy_queue_are_typed_skips_victoria_backs_off_or_skips(self):
        self.assertIn((503, "thermal_shed"), self.qwen_live.PRE_EXECUTION)
        self.assertEqual(live.PRE_EXECUTION, self.qwen_live.PRE_EXECUTION)
        self.verdict = SimpleNamespace(gpu_c=77, tctl_c=50.0)
        with self.assertRaises(self.not_executed) as caught:
            self.transport.submit(self.envelope())
        self.assertEqual(str(caught.exception), "thermal_shed")
        self.assertIn("thermal_shed", self.qwen_live.BACKOFF_REFUSALS)
        self.verdict, self.counts = SimpleNamespace(gpu_c=60, tctl_c=50.0), {"running": 3.0, "waiting": 0.0}
        with self.assertRaises(self.not_executed) as caught:
            self.transport.submit(self.envelope())
        self.assertEqual(str(caught.exception), "queue_busy")
        self.assertEqual(self.pilot.db.execute("SELECT COUNT(*) FROM live_requests").fetchone()[0], 0)

    def test_victoria_takes_unknown_resolved_as_terminal_with_no_fact(self):
        self.model.error = WorkerUnproven("no worker reply by the deadline", FakeLinger(True))
        envelope = self.envelope()
        self.transport.submit(envelope)
        self.live.worker.join(5)
        proof = self.transport.lookup({k: v for k, v in envelope.items() if k != "body"})
        self.assertEqual((proof["state"], proof["terminal"], proof["reason"], proof.get("response")),
                         ("failed", True, self.qwen_live.UNKNOWN_RESOLVED, None))


if __name__ == "__main__":
    unittest.main()
