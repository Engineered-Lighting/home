"""The Victoria pump and the LA live-scene consumer pin one contract.

Requires tools/shared-home on PYTHONPATH (as test_lab_saved_activity does); the
module is skipped, not failed, when home_shared is not importable. The
end-to-end test runs the real LA ledger/controller/gateway composition with a
temp ledger, synthetic authorities and an in-memory model socket.
"""
import io
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import qwen_live
import qwen_scene as scene
import qwen_transport
from test_qwen_live import FACTS, PROFILE, StoreFixture, TRANSPORT_CONFIG
from test_qwen_transport import Response

try:
    from home_shared import lab_live_scene as la
    from home_shared.admission import (AdmissionController, AdmissionLedger, RECEIPTS, RESOURCES, RequestConflict,
                                       ResourceManifest, VerifiedReceipt, canonical_digest)
    from home_shared.gateway import CredentialBinding
    from home_shared.lab_saved_activity import LabRequestError
    from home_shared.openai_backend import InputTokenBound
except ImportError:  # pragma: no cover - depends on PYTHONPATH
    la = None

needs_la = unittest.skipIf(la is None, "tools/shared-home (home_shared) is not on PYTHONPATH")


@needs_la
class ContractParityTests(unittest.TestCase):
    def test_prompt_schema_bounds_and_routes_match(self):
        self.assertEqual(la.instruction(), scene.instruction())
        self.assertEqual(la.response_format(), scene.response_format())
        self.assertEqual((la.FIELDS, la.VISIBLE_BASIS, la.KEY_ORDER, la.MAX_BASIS),
                         (scene.FIELDS, scene.VISIBLE_BASIS, scene.KEY_ORDER, scene.MAX_BASIS))
        self.assertEqual((la.PROMPT_VERSION, la.TASK, la.RESULT_SCHEMA, la.PLACEMENT, la.PRIOR_KEY),
                         (scene.PROMPT_VERSION, scene.TASK, scene.SCHEMA, scene.PLACEMENT, scene.PRIOR_KEY))
        self.assertEqual((la.MIN_IMAGES, la.MAX_IMAGES, la.IMAGE_SIZE, la.MAX_OUTPUT_TOKENS, la.MAX_SPAN_SECONDS),
                         (scene.MIN_IMAGES, scene.MAX_IMAGES, scene.IMAGE_SIZE, scene.MAX_OUTPUT_TOKENS, scene.MAX_SPAN_SECONDS))
        self.assertEqual(la._LOCAL_TIME.pattern, scene.LOCAL_TIME.pattern)
        self.assertEqual((la.SUBMIT_PATH, la.LOOKUP_PATH), (qwen_live.SUBMIT_PATH, qwen_live.LOOKUP_PATH))
        self.assertEqual(la.PRE_EXECUTION, qwen_live.PRE_EXECUTION)
        self.assertEqual(la.ENVELOPE_FIELDS, qwen_transport.ENVELOPE_FIELDS)
        self.assertEqual(la.PROFILE_FIELDS, qwen_live.PROFILE_FIELDS)
        self.assertLessEqual(la.MAX_ENVELOPE_BYTES, qwen_transport.MAX_ENVELOPE_BYTES)
        self.assertLessEqual(qwen_live.MAX_DEADLINE_SECONDS, la.MAX_DEADLINE_SECONDS)
        self.assertEqual(la.POLICY["instruction_sha256"], canonical_digest(scene.instruction()))

    def test_every_victoria_body_passes_the_la_validator(self):
        gateway = SimpleNamespace(controller=SimpleNamespace(engine_epoch="epoch-1", manifest=SimpleNamespace(
            model_alias="qwen3-vl-30b", model_revision="a" * 64, max_output_tokens=256, max_image_bytes=256 * 1024,
            max_input_tokens=8192)))
        binding = CredentialBinding("victoria", "victoria-lab-live", la.CONSUMER_ID, {la.LIVE_CAMERA: la.LIVE_ROOM}, "rev")
        from PIL import Image
        encoded = io.BytesIO()
        Image.new("RGB", scene.IMAGE_SIZE, (9, 9, 9)).save(encoded, format="JPEG")
        image = encoded.getvalue()
        for count, prior in ((2, None), (3, FACTS), (4, {**FACTS, "change_vs_prior": "changed"})):
            observed = [1000.0 + 1.3 * i for i in range(count)]
            for hour in (0, 6, 9, 13, 18, 23):
                body = scene.request_body(scene.model_context(scene.local_time_label(hour, 7), prior), [image] * count, observed)
                envelope = {"request_id": scene.digest([count, hour]), "site_id": "victoria", "session_id": "f" * 32,
                            "profile": {**PROFILE, "engine_epoch": "epoch-1"}, "body": body, "body_sha256": scene.digest(body),
                            "prompt_version": scene.PROMPT_VERSION, "task": scene.TASK, "deadline": time.time() + 7}
                request = la.validate_envelope(envelope, binding, gateway)
                self.assertEqual(request.digest, canonical_digest({k: v for k, v in envelope.items() if k not in {"body", "deadline"}}))
                self.assertEqual(request.body, scene.canonical(body).encode())


class _Verifier:
    def verify_admission(self, subject, epoch, now):
        return tuple(VerifiedReceipt(kind, subject.digest, epoch, now, now + 30, "d" * 64) for kind in RECEIPTS)

    def verify_terminal(self, subject, epoch, request_id, evidence):
        return evidence == (epoch, request_id, "finished")


class _Authority:
    def __init__(self, subject, epoch):
        self.manifest_digest, self.engine_epoch = subject.digest, epoch

    def attest(self, observation, deadline):
        return (self.engine_epoch, observation.ledger_id, "finished")

    def assess(self, snapshot, deadline):
        return InputTokenBound(snapshot.manifest_digest, snapshot.engine_epoch, snapshot.model_revision,
                               snapshot.upstream_body_sha256, 42, deadline)


class _Upstream:
    def __init__(self):
        self.bodies = []

    def connect(self, address, timeout=None):
        upstream = self

        class Socket:
            pending = b""

            def settimeout(self, value):
                pass

            def sendall(self, data):
                body = data.split(b"\r\n\r\n", 1)[1]
                upstream.bodies.append(body)
                context = json.loads(json.loads(body)["messages"][1]["content"][0]["text"])
                facts = {**FACTS, "change_vs_prior": "no_prior" if context[scene.PRIOR_KEY] is None else "no_change"}
                raw = json.dumps({"id": "chatcmpl-e2e", "object": "chat.completion", "created": 1, "model": "qwen3-vl-30b",
                                  "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                                               "message": {"role": "assistant", "content": json.dumps(facts)}}],
                                  "usage": {"prompt_tokens": 40, "completion_tokens": 70, "total_tokens": 110}}).encode()
                self.pending = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(raw) + raw

            def recv(self, size):
                chunk, self.pending = self.pending[:size], self.pending[size:]
                return chunk

            def shutdown(self, how):
                pass

            def close(self):
                pass
        return Socket()


class _Bridge:
    """HTTP-shaped connection factory that calls the LA perceiver in-process."""
    def __init__(self, perceiver, binding):
        self.perceiver, self.binding, self.paths = perceiver, binding, []

    def factory(self, timeout):
        bridge = self

        class Connection:
            sock = None

            def connect(inner):
                pass

            def request(inner, method, path, body=None, headers=None):
                inner.path, inner.payload = path, json.loads(body)
                bridge.paths.append(path)

            def getresponse(inner):
                submission = inner.path == la.SUBMIT_PATH
                try:
                    value = (bridge.perceiver.submit if submission else bridge.perceiver.lookup)(inner.payload, bridge.binding)
                    return Response(202 if submission else 200, value)
                except LabRequestError as error:
                    return Response(error.status, {"reason": error.reason})
                except RequestConflict:
                    return Response(409, {"reason": "request_conflict"})

            def close(inner):
                pass
        return Connection()


@needs_la
class EndToEndTests(StoreFixture, unittest.TestCase):
    def setUp(self):
        self.build_store(now=time.time())
        self.addCleanup(self.close_store)
        self.la_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.la_directory.cleanup)
        # Engine fences live in the OS temp directory; keep them test-local.
        self.previous_temp, tempfile.tempdir = tempfile.tempdir, self.la_directory.name
        self.addCleanup(setattr, tempfile, "tempdir", self.previous_temp)
        engine = "fake-e2e-engine-" + uuid.uuid4().hex[:12]
        amount = lambda value: {key: value for key in RESOURCES}
        self.manifest = ResourceManifest.from_dict({
            "version": 3, "engine_id": engine, "model_alias": "qwen3-vl-30b", "model_revision": "a" * 64,
            "upstream": "http://127.0.0.1:19999/v1",
            "services": [{"id": engine, "image_sha256": "b" * 64, "model_sha256": "a" * 64,
                          "startup_peak": amount(15), "steady_peak": amount(10),
                          "caps": {"ram_bytes": 30, "swap_bytes": 0, "cpu_millicores": 30, "pids": 10, "io_bytes_per_second": 30}}],
            "capacity": amount(100), "os_recovery_reserve": amount(20), "resident_overlap_peak": amount(20),
            "request_peak": amount(10), "max_active": 1, "max_queue": 3, "max_ledger_records": 1000,
            "max_input_tokens": 8192, "max_output_tokens": 256, "max_image_bytes": 256 * 1024, "max_deadline_seconds": 15,
            "consumer_routes": {"camera-victoria": "victoria", la.CONSUMER_ID: "victoria"},
            "consumer_deadline_seconds": {"camera-victoria": 15, la.CONSUMER_ID: 7},
            "consumer_policy_sha256": {"camera-victoria": "c" * 64, la.CONSUMER_ID: la.POLICY_SHA256},
            "free_storage_floor_bytes": 1, "health_max_age_seconds": 5, "receipt_max_age_seconds": 60,
            "measurement_artifact_sha256": "c" * 64})
        self.la_ledger = AdmissionLedger(Path(self.la_directory.name) / "ledger.sqlite")
        self.addCleanup(self.la_ledger.close)
        controller = AdmissionController(self.la_ledger, self.manifest, _Verifier())
        controller.enable("la-epoch")
        authority = _Authority(self.manifest, "la-epoch")
        self.upstream = _Upstream()
        backend = la.LiveSceneBackend(self.manifest, "la-epoch", attestor=authority, input_authority=authority,
                                      _connect=self.upstream.connect)
        self.gateway = la.compose_live_scene_gateway(controller, backend, live_min_execution_seconds=4)
        self.perceiver = la.LiveScenePerceiver(self.gateway)
        binding = CredentialBinding("victoria", "victoria-lab-live", la.CONSUMER_ID, {la.LIVE_CAMERA: la.LIVE_ROOM}, "rev")
        self.bridge = _Bridge(self.perceiver, binding)
        # The dispatcher runs where the transport would wait between lookups.
        self.transport = qwen_live.LiveSceneTransport(dict(TRANSPORT_CONFIG), connection_factory=self.bridge.factory,
                                                      clock=self.clock, sleep=lambda seconds: self.gateway.dispatch_once())
        self.frames_every_half_second(self.clock.now - 6, self.clock.now - .3)

    def test_pump_to_la_consumer_and_back_records_enum_facts_only(self):
        pump = qwen_live.QwenLivePump(self.store, self.session, transport=self.transport, profile=dict(PROFILE),
                                      config={"send_prior": True}, clock=self.clock, local_time=lambda epoch: (21, 15),
                                      occupied=lambda: {"state": "occupied", "reason": "person_detected"})
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        [sent] = self.upstream.bodies
        [saved] = self.events("qwen.observation")
        self.assertEqual(scene.digest(json.loads(sent)), saved["body_sha256"])
        self.assertEqual((saved["engine_epoch"], saved["facts"]), ("la-epoch", FACTS))
        self.assertEqual(self.bridge.paths[0], la.LOOKUP_PATH)  # engine-epoch probe first
        self.assertIn(la.SUBMIT_PATH, self.bridge.paths)
        record = self.la_ledger.db.execute("SELECT task_class,consumer_id,state,outcome FROM requests "
                                           "WHERE consumer_id=?", (la.CONSUMER_ID,)).fetchall()
        self.assertEqual([tuple(r) for r in record], [("background", la.CONSUMER_ID, "terminal", "completed")])
        # Next tick: newer frames, Qwen's own earlier facts as the only prior.
        # Victoria's clock now leads the LA's by 4 s: the consumer clamps the
        # deadline to its own bound instead of refusing it as expired.
        self.clock.now += 4
        self.frames_every_half_second(self.clock.now - 3.5, self.clock.now - .3)
        self.assertEqual(pump.tick()["reason"], "recorded_valid")
        context = json.loads(json.loads(self.upstream.bodies[1])["messages"][1]["content"][0]["text"])
        self.assertEqual(context[scene.PRIOR_KEY], FACTS)
        projected = qwen_live.scene_context(self.store, self.session, self.clock.now + 1, generation=1, arms={"a", "b"})
        self.assertEqual(projected["a"][0]["qwen_scene"]["latest"]["facts"]["activity_hypothesis"], "reading")
        self.assertTrue(self.gateway.controller.readiness()["ready"])

    def test_queued_interactive_work_turns_a_tick_into_a_typed_skip(self):
        self.la_ledger.db.execute("INSERT INTO requests(request_id,site_id,consumer_id,digest,state,task_class,submitted_at,deadline)"
                                  " VALUES('camera-x','victoria','camera-victoria',?,'queued','interactive',?,?)",
                                  ("e" * 64, time.time(), time.time() + 15))
        pump = qwen_live.QwenLivePump(self.store, self.session, transport=self.transport, profile=dict(PROFILE),
                                      clock=self.clock, local_time=lambda epoch: (21, 15),
                                      occupied=lambda: {"state": "occupied", "reason": "person_detected"})
        self.assertEqual(pump.tick()["reason"], "interactive_queued")
        self.assertEqual(self.upstream.bodies, [])
        self.assertEqual(self.la_ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
