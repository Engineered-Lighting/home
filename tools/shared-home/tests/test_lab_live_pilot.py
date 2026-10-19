"""Live-scene consumer on the camera-pilot ledger, with fakes only: temp SQLite,
a fake model transport, localhost TLS fixtures. No model, host or credential."""
import contextlib
import hashlib
import http.client
import io
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import lab_live_pilot as pilot_live
from home_shared import lab_live_scene as live
from home_shared.admission import AdmissionError, RequestConflict
from home_shared.camera_pilot import BeforeDispatchRejected, CameraPilot
from home_shared.gateway import GatewayHTTPServer, _Handler
from home_shared.ingress import IngressPolicy
from test_gateway import binding_fixture, request_fixture
from test_lab_live_scene import EPOCH, FACTS, LiveFixture

FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"
CONTAINER = "f" * 64


def config_value(**changes):
    value = {"version": 1, "enabled": True, "client_certificate": "live-client.pem", "device_id": "victoria-lab-live",
             "configuration_revision": "lab-live-scene-v1", "artifact_sha256": "a" * 64, "engine_epoch": EPOCH,
             "policy_sha256": live.POLICY_SHA256, "deadline_seconds": 7, "min_execution_seconds": 4,
             "max_input_tokens": 6144, "max_image_bytes": 262144, "max_output_tokens": 192,
             "max_records": 1000, "retention_days": 7}
    value.update(changes)
    return value


def completion(content=None, finish="stop"):
    return json.dumps({"id": "chatcmpl-fixture", "object": "chat.completion", "created": 1, "model": "qwen3-vl-30b",
                       "choices": [{"index": 0, "finish_reason": finish, "logprobs": None,
                                    "message": {"role": "assistant", "content": content or json.dumps(FACTS)}}],
                       "usage": {"prompt_tokens": 1400, "completion_tokens": 60, "total_tokens": 1460}}).encode()


class Model:
    """Fake live transport: records exact bytes, optionally holds or fails."""
    def __init__(self):
        self.calls, self.hold, self.error, self.result = [], None, None, completion()

    def __call__(self, body, deadline):
        self.calls.append((body, deadline))
        if self.hold is not None:
            self.hold.wait(5)
        if self.error is not None:
            raise self.error
        return self.result


class Envelopes(LiveFixture):
    """test_lab_live_scene's envelope builders without its AdmissionController."""


class PilotLiveFixture:
    def build(self, live_options=None, **changes):
        self.temp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.logs, self.camera_calls = [], []
        self.camera_hold = None
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=self.camera_backend, model_revision="pinned", clock=lambda: self.now,
                                 image_validator=lambda _: None, free_space_floor=0)
        self.model = Model()
        self.config = self.load(config_value(**changes))
        self.live = pilot_live.PilotLiveScene(self.pilot, self.config, **{
            "backend": self.model, "clock": lambda: self.now, "log": self.logs.append, **(live_options or {})})
        self.binding = self.live.binding
        self.envelopes = Envelopes()

    def camera_backend(self, request):
        self.camera_calls.append(request)
        if self.camera_hold is not None:
            self.camera_hold.wait(5)
        return "An empty den."

    def load(self, value, name="live-scene.json"):
        path = Path(self.temp.name) / name
        path.write_text(json.dumps(value))
        return pilot_live.load_live_config(path)

    def teardown(self):
        for event in (self.model.hold, self.camera_hold):
            if event is not None:
                event.set()
        self.live.close(5)
        self.pilot.close()
        self.temp.cleanup()

    def envelope(self, **changes):
        return self.envelopes.envelope(deadline=self.now + 7, **changes)

    def reference(self, envelope):
        return {k: v for k, v in envelope.items() if k != "body"}

    def camera(self, rid="camera-1"):
        return {**request_fixture(now=self.now, request_id=rid), "task_class": "interactive"}

    def rows(self):
        return self.pilot.db.execute("SELECT COUNT(*) FROM live_requests").fetchone()[0]

    def finish(self):
        self.live.worker.join(5)
        self.assertFalse(self.live.worker.is_alive())

    def refused(self, envelope, binding=None):
        with self.assertRaises(live.LiveSceneRefusal) as caught:
            self.live.submit(envelope, binding or self.binding)
        pair = (caught.exception.status, caught.exception.reason)
        self.assertIn(pair, live.PRE_EXECUTION)
        return pair


class PilotLiveTests(PilotLiveFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def tearDown(self):
        self.teardown()

    def test_exact_hashed_bytes_reach_the_model_and_a_bound_proof_returns(self):
        envelope = self.envelope()
        proof = self.live.submit(envelope, self.binding)
        self.assertEqual((proof["state"], proof["terminal"], proof["engine_epoch"], proof["artifact_sha256"]),
                         ("dispatching", False, EPOCH, "a" * 64))
        self.finish()
        body, deadline = self.model.calls[0]
        self.assertEqual(hashlib.sha256(body).hexdigest(), envelope["body_sha256"])
        self.assertLessEqual(deadline, self.now + 7)
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["terminal"]), ("completed", True))
        self.assertEqual(json.loads(proof["response"]["choices"][0]["message"]["content"]), FACTS)
        self.assertEqual(self.live.submit(envelope, self.binding)["state"], "completed")  # idempotent, no resend
        self.assertEqual(len(self.model.calls), 1)
        with self.assertRaises(RequestConflict):
            self.live.submit({**envelope, "session_id": "e" * 32}, self.binding)
        self.assertTrue(any(line.startswith("live scene backend seconds=") for line in self.logs))
        self.assertIsNotNone(self.pilot.db.execute("SELECT finished FROM live_requests").fetchone()[0])

    def test_late_completion_is_still_a_completed_answer(self):
        envelope = self.envelope()
        self.model.hold = threading.Event()
        self.live.submit(envelope, self.binding)
        self.now += 30
        self.model.hold.set()
        self.finish()
        row = self.pilot.db.execute("SELECT state,outcome FROM live_requests").fetchone()
        self.assertEqual(tuple(row), ("terminal", "completed_after_deadline"))
        self.assertEqual(self.live.lookup(self.reference(envelope), self.binding)["state"], "completed")

    def test_every_skip_is_typed_and_writes_nothing(self):
        self.assertEqual(self.refused(self.envelope(), binding_fixture()), (403, "consumer_not_registered"))
        self.assertEqual(self.refused(self.envelope(prompt_version="old")), (400, "prompt_version_unsupported"))
        self.assertEqual(self.refused(self.envelopes.envelope(deadline=self.now + 3)), (409, "deadline_insufficient"))
        # A camera request in flight: the perceiver skips, camera is untouched.
        self.camera_hold = threading.Event()
        self.pilot.submit(self.camera(), binding_fixture())
        self.assertEqual(self.refused(self.envelope()), (429, "interactive_queued"))
        self.camera_hold.set()
        self.pilot.worker.join(5)
        self.assertEqual(self.rows(), 0)
        # One perceiver request at a time.
        self.model.hold = threading.Event()
        self.live.submit(self.envelope(), self.binding)
        self.assertEqual(self.refused(self.envelope()), (429, "perceiver_busy"))
        self.model.hold.set()
        self.finish()
        self.assertEqual(self.rows(), 1)
        self.pilot.close_admission()
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.rows(), 1)

    def test_camera_accounting_is_unchanged_while_a_live_request_runs(self):
        self.model.hold = threading.Event()
        self.live.submit(self.envelope(), self.binding)
        status = self.pilot.submit(self.camera(), binding_fixture())
        self.assertEqual(status["status"], "dispatching")  # not refused as busy
        self.pilot.worker.join(5)
        self.assertEqual(self.pilot.status("camera-1", binding_fixture())["outcome"], "completed")
        self.assertEqual(self.pilot.readiness()["ready"], True)
        self.model.hold.set()
        self.finish()
        # The camera cooldown still reads camera rows only.
        self.now += 31
        self.live.submit(self.envelope(), self.binding)
        self.finish()
        self.assertEqual(self.pilot.submit(self.camera("camera-2"), binding_fixture())["status"], "dispatching")

    def test_unresolved_camera_outcome_closes_the_perceiver(self):
        self.pilot.db.execute("INSERT INTO pilot_requests VALUES('stuck','d',?,?,'indeterminate','dispatcher_restart')",
                              (self.now, self.now + 15))
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))

    def test_preflight_rejection_is_terminal_and_keeps_live_open(self):
        self.model.error = BeforeDispatchRejected("preflight_rejected")
        envelope = self.envelope()
        self.live.submit(envelope, self.binding)
        self.finish()
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["terminal"], proof["reason"]), ("failed", True, "preflight_rejected"))
        self.model.error = None
        self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")

    def test_unknown_outcome_closes_the_live_consumer_only(self):
        self.model.error = subprocess.TimeoutExpired("docker", 10)
        envelope = self.envelope()
        self.live.submit(envelope, self.binding)
        self.finish()
        proof = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual((proof["state"], proof["terminal"]), ("indeterminate", False))
        self.model.error = None
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.pilot.submit(self.camera(), binding_fixture())["status"], "dispatching")
        self.assertIn("live scene execution unresolved: TimeoutExpired", self.logs)

    def test_finished_transport_failures_are_terminal_and_three_in_a_row_close(self):
        self.model.error = pilot_live.UpstreamFinished("exit 1")
        for index in range(3):
            envelope = self.envelope()
            self.live.submit(envelope, self.binding)
            self.finish()
            proof = self.live.lookup(self.reference(envelope), self.binding)
            self.assertEqual((proof["state"], proof["reason"]), ("failed", "upstream_failed"))
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))

    def test_invalid_completion_is_terminal_not_unknown(self):
        self.model.result = b"{not json"
        envelope = self.envelope()
        self.live.submit(envelope, self.binding)
        self.finish()
        self.assertEqual(self.live.lookup(self.reference(envelope), self.binding)["reason"], "completion_invalid")
        self.model.result = completion(finish="length")  # a length stop is still an answer
        envelope = self.envelope()
        self.live.submit(envelope, self.binding)
        self.finish()
        self.assertEqual(self.live.lookup(self.reference(envelope), self.binding)["state"], "completed")

    def test_restart_leaves_an_interrupted_dispatch_unresolved_and_live_closed(self):
        self.pilot.db.execute("INSERT INTO live_requests VALUES('x','r','d',?,?,'dispatching',NULL,?,NULL)",
                              (self.now, self.now + 7, EPOCH))
        restarted = pilot_live.PilotLiveScene(self.pilot, self.config, backend=self.model, clock=lambda: self.now)
        self.assertEqual(tuple(self.pilot.db.execute("SELECT state,outcome FROM live_requests").fetchone()),
                         ("indeterminate", "dispatcher_restart"))
        with self.assertRaises(live.LiveSceneRefusal) as caught:
            restarted.submit(self.envelope(), self.binding)
        self.assertEqual(caught.exception.reason, "admission_closed")
        self.assertEqual(self.model.calls, [])

    def test_lookup_without_a_record_is_not_found_then_not_admitted(self):
        reference = self.reference(self.envelope())
        value = self.live.lookup(reference, self.binding)
        self.assertEqual((value["state"], value["terminal"], value["engine_epoch"], value["artifact_sha256"]),
                         ("not_found", False, EPOCH, "a" * 64))
        value = self.live.lookup({**reference, "deadline": self.now - 10}, self.binding)
        self.assertEqual((value["state"], value["terminal"]), ("not_admitted", True))
        with self.assertRaises(live.LiveSceneRefusal):
            self.live.lookup({**reference, "body": {}}, self.binding)

    def test_ledger_cap_refuses_and_retention_frees_old_terminal_rows(self):
        rows = [("r%d" % i, "q", "d", self.now, self.now, "terminal", "completed", EPOCH, self.now) for i in range(1000)]
        self.pilot.db.executemany("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,?,?)", rows)
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.now += 8 * 86400
        # One bounded batch per admission (speed plan S1.4), enough to get under the cap.
        self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")
        self.finish()
        self.assertEqual(self.rows(), 1000 - pilot_live.PRUNE_BATCH_ROWS + 1)
        self.live.submit(self.envelope(), self.binding)
        self.finish()
        self.assertEqual(self.rows(), 2)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "live-scene.json"

    def load(self, value):
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))
        return pilot_live.load_live_config(self.path)

    def test_absent_or_disabled_provisioning_is_no_consumer(self):
        self.assertIsNone(pilot_live.load_live_config(self.path))
        self.assertIsNone(self.load(config_value(enabled=False)))
        self.assertEqual(self.load(config_value()).deadline_seconds, 7.0)

    def test_exact_bounded_provisioning_bound_to_the_reviewed_policy(self):
        bad = [config_value(policy_sha256="9" * 64), config_value(deadline_seconds=9), config_value(deadline_seconds=4),
               config_value(min_execution_seconds=7), config_value(min_execution_seconds=0.5),
               config_value(artifact_sha256="qwen"), config_value(client_certificate="../client.pem"),
               config_value(max_output_tokens=128), config_value(max_image_bytes=262145), config_value(extra=1),
               config_value(enabled="yes"), {k: v for k, v in config_value().items() if k != "max_records"}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)
        with self.assertRaises(ValueError):  # duplicate member
            self.load(json.dumps(config_value())[:-1] + ', "enabled": false}')
        with self.assertRaises(ValueError):  # disabled but malformed is still refused
            self.load(config_value(enabled=False, deadline_seconds=30))


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.private = Path(self.temp.name)
        self.pilot = CameraPilot(self.private / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda _: "x", model_revision="pinned", image_validator=lambda _: None,
                                 free_space_floor=0)
        self.identities = {"c" * 64: binding_fixture()}

    def tearDown(self):
        self.pilot.close()
        self.temp.cleanup()

    def compose(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = pilot_live.compose_pilot_live(self.private, self.pilot, self.identities, container=CONTAINER,
                                                   model_health=lambda: None, enforce_linux=False)
        return result, stderr.getvalue()

    def test_absent_provisioning_composes_nothing(self):
        self.assertEqual(self.compose(), (None, ""))
        self.assertEqual(list(self.identities), ["c" * 64])

    def test_enabled_provisioning_registers_a_distinct_certificate_identity(self):
        (self.private / "live-client.pem").write_bytes((FIXTURES / "client.pem").read_bytes())
        (self.private / "live-scene.json").write_text(json.dumps(config_value()))
        consumer, log = self.compose()
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / "client.pem").read_text())).hexdigest()
        self.assertIs(self.identities[fingerprint], consumer.binding)
        self.assertEqual((consumer.binding.consumer_id, dict(consumer.binding.cameras)),
                         (live.CONSUMER_ID, {live.LIVE_CAMERA: live.LIVE_ROOM}))
        self.assertIn("live scene consumer enabled", log)
        consumer.close()

    def engine(self, **changes):
        from home_shared.model_pin import ModelPin
        from test_model_pin import pin_value
        value = pin_value(**changes)
        pin = ModelPin(value["container_name"], value["container_id"], value["image"], value["started_at"],
                       value["engine_epoch"], value["artifact_sha256"])
        return SimpleNamespace(provisioned=pin, engine_epoch=pin.engine_epoch)

    def test_live_provisioning_must_quote_the_model_pin(self):
        from home_shared.model_pin import EngineIdentityMismatch
        (self.private / "live-client.pem").write_bytes((FIXTURES / "client.pem").read_bytes())
        (self.private / "live-scene.json").write_text(json.dumps(config_value()))
        for changes in ({"engine_epoch": "other-epoch"}, {"artifact_sha256": "c" * 64}):
            with self.subTest(changes=changes), self.assertRaises(EngineIdentityMismatch):
                pilot_live.compose_pilot_live(self.private, self.pilot, self.identities, container=CONTAINER,
                                              model_health=lambda: None, enforce_linux=False,
                                              engine=self.engine(**changes))
        self.assertEqual(list(self.identities), ["c" * 64])
        engine = self.engine()
        with contextlib.redirect_stderr(io.StringIO()):
            consumer = pilot_live.compose_pilot_live(self.private, self.pilot, self.identities, container=CONTAINER,
                                                     model_health=lambda: None, enforce_linux=False, engine=engine)
        self.assertEqual((consumer._reply_wait, consumer._engine_epoch()), (0, EPOCH))  # follows the pilot
        engine.engine_epoch = "vllm-333333333333-20261008T031500Z"
        self.assertEqual(consumer._engine_epoch(), engine.engine_epoch)
        consumer.close()

    def test_invalid_provisioning_keeps_camera_only(self):
        (self.private / "live-scene.json").write_text(json.dumps(config_value()))  # certificate missing
        self.assertEqual(self.compose(), (None, "live scene consumer disabled: FileNotFoundError\n"))
        (self.private / "live-scene.json").write_text(json.dumps(config_value(policy_sha256="0" * 64)))
        self.assertIsNone(self.compose()[0])
        self.assertEqual(list(self.identities), ["c" * 64])


class DockerBackendTests(unittest.TestCase):
    def backend(self, run, health=lambda: None):
        return pilot_live.docker_backend(CONTAINER, health, clock=lambda: 1000, run=run)

    def test_exact_bytes_through_the_fixed_container_without_retry(self):
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=completion().decode())
        body = b'{"model":"qwen3-vl-30b"}'
        self.assertEqual(self.backend(run)(body, 1007), completion())
        args, kwargs = calls[0]
        self.assertEqual(args[:4], ["docker", "exec", "-i", CONTAINER])
        self.assertEqual(json.loads(kwargs["input"])["body"].encode(), body)
        self.assertEqual(kwargs["timeout"], 7 + pilot_live.EXECUTION_GRACE_SECONDS)
        self.assertEqual(len(calls), 1)

    def test_preflight_failure_sends_nothing(self):
        def health():
            raise ValueError("pilot resource guard closed")
        with contextlib.redirect_stderr(io.StringIO()):
            for backend, deadline in ((self.backend(self.fail, health), 1007), (self.backend(self.fail), 1000.5)):
                with self.assertRaises(BeforeDispatchRejected):
                    backend(b"{}", deadline)

    def test_exit_is_finished_and_timeout_is_unknown(self):
        with self.assertRaises(pilot_live.UpstreamFinished):
            self.backend(lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))(b"{}", 1007)

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("docker", 10)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.backend(slow)(b"{}", 1007)


class EngineChangeTests(PilotLiveFixture, unittest.TestCase):
    """The live side of an accepted vLLM re-pin (model_pin.PinnedEngine)."""

    def setUp(self):
        self.epoch = EPOCH
        self.build(live_options={"engine_epoch": lambda: self.epoch})

    def tearDown(self):
        self.teardown()

    def test_a_re_pin_refuses_live_typed_and_keeps_camera_chat(self):
        self.assertEqual(self.live.submit(self.envelope(), self.binding)["state"], "dispatching")
        self.finish()
        self.epoch = "vllm-333333333333-20261008T031500Z"
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.logs.count("live scene admission closed: engine_changed"), 1)
        self.assertEqual(self.rows(), 1)  # the refusals wrote nothing
        self.assertEqual(self.pilot.submit(self.camera(), binding_fixture())["status"], "dispatching")
        self.pilot.worker.join(5)
        self.assertEqual(self.pilot.status("camera-1", binding_fixture())["outcome"], "completed")
        self.epoch = EPOCH  # closed until a restart, never reopened in place
        self.now += 31
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))

    def test_a_consumer_composed_after_a_re_pin_starts_closed(self):
        self.epoch = "vllm-333333333333-20261008T031500Z"
        restarted = pilot_live.PilotLiveScene(self.pilot, self.config, backend=self.model, clock=lambda: self.now,
                                              log=self.logs.append, engine_epoch=lambda: self.epoch)
        self.assertTrue(restarted.closed)
        self.assertEqual(self.logs, ["live scene admission closed: engine_changed"])
        with self.assertRaises(ValueError):
            pilot_live.PilotLiveScene(self.pilot, self.config, backend=self.model, engine_epoch="epoch")
        with self.assertRaises(ValueError):
            pilot_live.PilotLiveScene(self.pilot, self.config, backend=self.model, reply_wait_seconds=9)

    def test_the_live_preflight_refuses_an_engine_its_epoch_does_not_name(self):
        engine = SimpleNamespace(engine_epoch=EPOCH)
        calls = []

        def health():
            calls.append("health")
            engine.engine_epoch = "vllm-333333333333-20261008T031500Z"  # this check accepted a re-pin
        backend = pilot_live.docker_backend(CONTAINER, pilot_live._epoch_bound(health, engine, EPOCH),
                                            clock=lambda: 1000, run=lambda *a, **k: self.fail("must not exec"))
        with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(BeforeDispatchRejected):
            backend(b"{}", 1007)
        self.assertEqual(calls, ["health"])
        self.assertEqual(stderr.getvalue(), "live scene preflight rejected: ValueError\n")


class TimingLineTests(PilotLiveFixture, unittest.TestCase):
    """docker_backend times model_health and the docker exec; the existing
    backend line keeps its text and gains health_ms, exec_ms and total_ms."""

    def setUp(self):
        self.clock_now = [100.0]
        self.health_error = None

        def health():
            self.clock_now[0] += .12
            if self.health_error is not None:
                raise self.health_error

        def run(*args, **kwargs):
            self.clock_now[0] += .59
            return SimpleNamespace(returncode=0, stdout=completion().decode())
        self.build(live_options={"backend": pilot_live.docker_backend(CONTAINER, health, clock=lambda: self.now,
                                                                       run=run)})
        timing = patch.object(pilot_live.backend_timing, "time", SimpleNamespace(monotonic=lambda: self.clock_now[0]))
        timing.start()
        self.addCleanup(timing.stop)

    def tearDown(self):
        self.teardown()

    def line(self):
        self.live.submit(self.envelope(), self.binding)
        self.finish()
        return [line for line in self.logs if line.startswith("live scene backend seconds=")][-1]

    def test_backend_line_appends_the_stage_split(self):
        self.assertRegex(self.line(), r"^live scene backend seconds=\d+\.\d{3} outcome=completed "
                                      r"health_ms=120 exec_ms=590 total_ms=\d+$")
        self.health_error = ValueError("pilot resource guard closed")
        with contextlib.redirect_stderr(io.StringIO()):
            line = self.line()
        self.assertRegex(line, r"^live scene backend seconds=\d+\.\d{3} outcome=preflight_rejected "
                               r"health_ms=120 exec_ms=0 total_ms=\d+$")

    def test_fake_backends_without_stages_report_zero(self):
        self.live._backend = self.model
        self.assertRegex(self.line(), r" health_ms=0 exec_ms=0 total_ms=\d+$")


class Harness(live.LiveSceneRoutes, _Handler):
    pass


CAMERA_TOKEN, LIVE_TOKEN = b"camera-fixture-token-0123456789abcd", b"live-fixture-token-0123456789abcdef"


class RouteTests(PilotLiveFixture, unittest.TestCase):
    def setUp(self):
        self.build()
        self.server = GatewayHTTPServer(("127.0.0.1", 0), self.pilot,
                                        {hashlib.sha256(CAMERA_TOKEN).hexdigest(): binding_fixture(),
                                         hashlib.sha256(LIVE_TOKEN).hexdigest(): self.binding})
        self.server.RequestHandlerClass = Harness
        self.server.live, self.server.lab = self.live, None
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.teardown()

    def request(self, path, value, token=LIVE_TOKEN, method="POST"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = json.dumps(value).encode() if value is not None else None
        connection.request(method, path, body=body, headers={"Authorization": "Bearer " + token.decode(),
                                                               "Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_live_and_camera_routes_share_the_listener_but_not_identities(self):
        envelope = self.envelope()
        status, value = self.request(live.SUBMIT_PATH, envelope)
        self.assertEqual((status, value["state"]), (202, "dispatching"))
        self.finish()
        status, value = self.request(live.LOOKUP_PATH, self.reference(envelope))
        self.assertEqual((status, value["state"]), (200, "completed"))
        # Identity refusals answer before reading a body; empty bodies keep the
        # test free of unread-data resets (the consumer closes those connections).
        self.assertEqual(self.request(live.SUBMIT_PATH, None, token=CAMERA_TOKEN), (403, {"reason": "consumer_not_registered"}))
        self.assertEqual(self.request("/v1/inference", None, token=LIVE_TOKEN), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.request("/v1/inference/camera-1", None, method="GET"), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.request("/v1/lab/saved-activity", None, token=CAMERA_TOKEN), (403, {"reason": "consumer_not_registered"}))
        status, value = self.request("/v1/inference", self.camera(), token=CAMERA_TOKEN)
        self.assertEqual((status, value["status"]), (202, "dispatching"))


class TLSIngressTests(PilotLiveFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def tearDown(self):
        self.teardown()

    def ingress(self, identities):
        return pilot_live.PilotLiveIngress(("127.0.0.1", 0), self.pilot, self.live,
                                           certificate=str(FIXTURES / "server.pem"),
                                           private_key=str(FIXTURES / "server-test-only.key"),
                                           client_ca=str(FIXTURES / "ca.pem"), identities=identities,
                                           policy=IngressPolicy(4, 2, 3, 2))

    def test_live_certificate_identity_reaches_the_live_route_over_mutual_tls(self):
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / "client.pem").read_text())).hexdigest()
        with self.assertRaises(AdmissionError):
            self.ingress({fingerprint: self.binding})
        server = self.ingress({fingerprint: self.binding, "0" * 64: binding_fixture()})
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            context = ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))
            context.check_hostname = False
            context.load_cert_chain(str(FIXTURES / "client.pem"), str(FIXTURES / "client-test-only.key"))
            connection = http.client.HTTPSConnection("127.0.0.1", server.server_address[1], timeout=5, context=context)
            connection.request("POST", live.SUBMIT_PATH, body=json.dumps(self.envelope()).encode(),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual((response.status, json.loads(response.read())["state"]), (202, "dispatching"))
            connection.close()
            self.finish()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.assertTrue(self.live.closed and self.pilot.closed)


if __name__ == "__main__":
    unittest.main()
