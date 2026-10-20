"""LA saved-activity consumer with fakes only: temp ledger, synthetic authorities,
an in-memory upstream socket. No model, network host, GPU or credential."""
import base64
import hashlib
import http.client
import io
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from home_shared import lab_saved_activity as lab
from home_shared.admission import (AdmissionController, AdmissionLedger, RECEIPTS, RESOURCES, RequestConflict,
                                   ResourceManifest, VerifiedReceipt, canonical_digest)
from home_shared.gateway import CredentialBinding, GatewayHTTPServer, _Handler
from home_shared.openai_backend import InputTokenBound, PROMPT

EPOCH = "lab-fixture-epoch"
MODEL = "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"
LAB_BINDING = CredentialBinding("victoria", "victoria-lab", lab.CONSUMER_ID, {lab.LAB_CAMERA: lab.LAB_ROOM}, "lab-rev-1")
CAMERA_BINDING = CredentialBinding("victoria", "den-adapter", "camera-victoria", {"den_oak": "den"}, "rev-1", True)
LAB_TOKEN, CAMERA_TOKEN = b"lab-fixture-token-0123456789abcdef", b"camera-fixture-token-0123456789abcd"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def jpeg(width=64, height=36, fmt="JPEG"):
    from PIL import Image
    encoded = io.BytesIO()
    Image.new("RGB", (width, height), (40, 50, 60)).save(encoded, format=fmt)
    return encoded.getvalue()


def manifest(max_queue=3, max_input_tokens=8192, policy=None):
    engine = "fake-lab-engine-" + uuid.uuid4().hex[:12]
    amount = lambda value: {key: value for key in RESOURCES}
    return ResourceManifest.from_dict({
        "version": 3, "engine_id": engine, "model_alias": "qwen3-vl-30b", "model_revision": "a" * 64,
        "upstream": "http://127.0.0.1:19999/v1",
        "services": [{"id": engine, "image_sha256": "b" * 64, "model_sha256": "a" * 64,
                      "startup_peak": amount(15), "steady_peak": amount(10),
                      "caps": {"ram_bytes": 30, "swap_bytes": 0, "cpu_millicores": 30, "pids": 10, "io_bytes_per_second": 30}}],
        "capacity": amount(100), "os_recovery_reserve": amount(20), "resident_overlap_peak": amount(20),
        "request_peak": amount(10), "max_active": 1, "max_queue": max_queue, "max_ledger_records": 1000,
        "max_input_tokens": max_input_tokens, "max_output_tokens": 256, "max_image_bytes": 256 * 1024,
        "max_deadline_seconds": 15, "consumer_routes": {"camera-victoria": "victoria", lab.CONSUMER_ID: "victoria"},
        "consumer_deadline_seconds": {"camera-victoria": 15, lab.CONSUMER_ID: 15},
        "consumer_policy_sha256": {"camera-victoria": "c" * 64, lab.CONSUMER_ID: policy or lab.POLICY_SHA256},
        "free_storage_floor_bytes": 1, "health_max_age_seconds": 5, "receipt_max_age_seconds": 60,
        "measurement_artifact_sha256": "c" * 64})


class Verifier:
    def verify_admission(self, subject, epoch, now):
        return tuple(VerifiedReceipt(kind, subject.digest, epoch, now, now + 30, "d" * 64) for kind in RECEIPTS)

    def verify_terminal(self, subject, epoch, request_id, evidence):
        return evidence == (epoch, request_id, "finished")


class Authority:
    """Synthetic attestor and input bound; never a real model calculation."""
    def __init__(self, subject):
        self.manifest_digest, self.engine_epoch = subject.digest, EPOCH
        self.snapshots = []

    def attest(self, observation, deadline):
        return (self.engine_epoch, observation.ledger_id, "finished")

    def assess(self, snapshot, deadline):
        self.snapshots.append(snapshot)
        return InputTokenBound(snapshot.manifest_digest, snapshot.engine_epoch, snapshot.model_revision,
                               snapshot.upstream_body_sha256, 42, deadline)


class Upstream:
    """In-memory model endpoint: records exact upload bytes, answers once."""
    def __init__(self):
        self.bodies, self.lock = [], threading.Lock()
        self.active = self.peak = 0
        self.finish = "stop"
        self.hold = None

    def connect(self, address, timeout=None):
        return _Socket(self)

    def answer(self, body):
        value = json.loads(body)
        content = value["messages"][-1]["content"]
        refs = [json.loads(part["text"])["media_id"] for part in content[1::2]] if value["messages"][0]["role"] == "system" else []
        text = json.dumps({"schema_version": "lab-activity-1", "activity": "reading", "evidence_refs": refs[:1]})
        completion = {"id": "chatcmpl-fixture", "object": "chat.completion", "created": 1, "model": "qwen3-vl-30b",
                      "choices": [{"index": 0, "finish_reason": self.finish, "logprobs": None,
                                   "message": {"role": "assistant", "content": text}}],
                      "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52}}
        raw = json.dumps(completion).encode()
        return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(raw) + raw


class _Socket:
    def __init__(self, upstream):
        self.upstream, self.pending, self.counted = upstream, b"", False

    def settimeout(self, value):
        pass

    def sendall(self, data):
        head, body = data.split(b"\r\n\r\n", 1)
        with self.upstream.lock:
            self.upstream.bodies.append((head, body))
            self.upstream.active += 1
            self.upstream.peak = max(self.upstream.peak, self.upstream.active)
            self.counted = True
        if self.upstream.hold is not None:
            self.upstream.hold.wait(2)
        self.pending = self.upstream.answer(body)

    def recv(self, size):
        chunk, self.pending = self.pending[:size], self.pending[size:]
        return chunk

    def shutdown(self, how):
        pass

    def close(self):
        with self.upstream.lock:
            if self.counted:
                self.upstream.active -= 1
                self.counted = False


class LaFixture:
    """Real ledger/controller/gateway composition around fakes."""
    def build(self, **kwargs):
        self.directory = tempfile.TemporaryDirectory()
        # Engine fences live in the OS temp directory; keep them run-local
        # (as tools/shared-home/run_tests.py does) and never shared.
        self.previous_temp = tempfile.tempdir
        tempfile.tempdir = self.directory.name
        self.manifest = manifest(**kwargs)
        self.ledger = AdmissionLedger(Path(self.directory.name) / "ledger.sqlite")
        self.controller = AdmissionController(self.ledger, self.manifest, Verifier())
        self.controller.enable(EPOCH)
        self.authority = Authority(self.manifest)
        self.upstream = Upstream()
        self.backend = lab.LabActivityBackend(self.manifest, EPOCH, attestor=self.authority, input_authority=self.authority,
                                              _connect=self.upstream.connect)
        self.gateway = lab.compose_lab_gateway(self.controller, self.backend, min_execution_seconds=2)
        self.lab = lab.LabSavedActivity(self.gateway)

    def close(self):
        self.ledger.close()
        tempfile.tempdir = self.previous_temp
        self.directory.cleanup()

    def profile(self):
        return {"model_version": MODEL, "served_model": "qwen3-vl-30b", "artifact_sha256": "a" * 64,
                "engine_epoch": EPOCH, "runtime_revision": "fixture-runtime", "adapter_revision": lab.PROMPT_VERSION}

    def envelope(self, *, images=1, context=None, image=None, system=None, deadline=None, **changes):
        content = [{"type": "text", "text": canonical(context if context is not None else
                    {"window": {"cutoff": 5, "start": 0, "sample_times": [0, 5]}, "ha": [], "observations": []})}]
        for index in range(images):
            content += [{"type": "text", "text": canonical({"media_id": "%032x" % (index + 1), "observed_at": 100 + index,
                                                           "available_at": 100.5 + index})},
                        {"type": "image_url", "image_url": {"url": image or "data:image/jpeg;base64," + base64.b64encode(jpeg()).decode()}}]
        body = {"model": "qwen3-vl-30b", "messages": [{"role": "system", "content": system or lab.instruction()},
                {"role": "user", "content": content}], "temperature": 0, "max_tokens": 256, "stream": False}
        value = {"request_id": uuid.uuid4().hex * 2, "site_id": "victoria", "session_id": "f" * 32, "profile": self.profile(),
                 "body": body, "body_sha256": canonical_digest(body), "prompt_version": lab.PROMPT_VERSION,
                 "task": lab.TASK, "deadline": deadline if deadline is not None else time.time() + 10}
        value.update(changes)
        return value

    def camera(self, request_id="camera-1", sequence=1):
        now = time.time()
        return {"version": 1, "request_id": request_id, "site_id": "victoria", "device_id": "den-adapter",
                "camera_id": "den_oak", "room_id": "den", "source_event_id": "event-1", "producer_session": "boot-1",
                "producer_sequence": sequence, "occupancy_generation": 1, "capture_session": "camera-1", "frame_id": "frame-%d" % sequence,
                "scheduled_at": now, "captured_at": now, "received_at": now, "clock_uncertainty_seconds": 0.1,
                "timestamp_quality": "validated_observation", "request_deadline": now + 15, "task_class": "interactive",
                "configuration_revision": "rev-1", "max_output_tokens": 128,
                "image_base64": base64.b64encode(jpeg(640, 360)).decode()}

    def reference(self, envelope):
        return {k: v for k, v in envelope.items() if k != "body"}

    def rows(self):
        return self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


class LabSavedActivityTests(LaFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def tearDown(self):
        self.close()

    def test_background_request_uses_the_exact_hashed_body_and_returns_a_bound_proof(self):
        envelope = self.envelope(images=2, context={"window": {"cutoff": 5, "start": 0, "sample_times": [0, 5]}, "ha": [],
                                                    "observations": [{"id": "7", "objects": [{"label": "book", "confidence": .9}]}]})
        queued = self.lab.submit(envelope, LAB_BINDING)
        self.assertEqual((queued["state"], queued["terminal"], queued["body_sha256"]), ("queued", False, envelope["body_sha256"]))
        record = self.ledger.get(canonical_digest(["victoria", "victoria-lab", envelope["request_id"]]))
        self.assertEqual((record.task_class, record.consumer_id), ("background", lab.CONSUMER_ID))
        self.gateway.dispatch_once()
        head, body = self.upstream.bodies[0]
        self.assertEqual(hashlib.sha256(body).hexdigest(), envelope["body_sha256"])
        self.assertEqual(self.authority.snapshots[0].upstream_body_sha256, envelope["body_sha256"])
        proof = self.lab.lookup(self.reference(envelope), LAB_BINDING)
        self.assertEqual((proof["state"], proof["terminal"], proof["engine_epoch"], proof["artifact_sha256"]),
                         ("completed", True, EPOCH, "a" * 64))
        self.assertEqual(json.loads(proof["response"]["choices"][0]["message"]["content"])["activity"], "reading")
        self.assertEqual(proof["usage"], {"prompt_tokens": 40, "completion_tokens": 12})
        self.assertEqual(self.lab.submit(envelope, LAB_BINDING)["state"], "completed")  # idempotent, never re-run
        self.assertEqual(len(self.upstream.bodies), 1)

    def test_queued_interactive_camera_work_is_claimed_before_background_analysis(self):
        envelope = self.envelope()
        self.assertEqual(self.lab.submit(envelope, LAB_BINDING)["state"], "queued")
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")
        self.gateway.dispatch_once()
        self.assertIn(PROMPT.encode(), self.upstream.bodies[0][1])
        self.gateway.dispatch_once()
        self.assertEqual(hashlib.sha256(self.upstream.bodies[1][1]).hexdigest(), envelope["body_sha256"])
        self.assertEqual(self.upstream.peak, 1)

    def test_background_never_takes_the_last_queue_slot(self):
        first, second, third = self.envelope(), self.envelope(), self.envelope()
        self.assertEqual(self.lab.submit(first, LAB_BINDING)["state"], "queued")
        self.assertEqual(self.lab.submit(second, LAB_BINDING)["state"], "queued")
        refused = self.lab.submit(third, LAB_BINDING)
        self.assertEqual((refused["state"], refused["terminal"], refused["reason"]), ("rejected", True, "capacity"))
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")

    def test_length_stop_is_a_terminal_answer_and_keeps_admission_open(self):
        self.upstream.finish = "length"
        envelope = self.envelope()
        self.lab.submit(envelope, LAB_BINDING)
        self.gateway.dispatch_once()
        proof = self.lab.lookup(self.reference(envelope), LAB_BINDING)
        self.assertEqual((proof["state"], proof["response"]["choices"][0]["finish_reason"]), ("completed", "length"))
        self.assertTrue(self.controller.readiness()["ready"])

    def test_refusals_happen_before_any_ledger_access(self):
        oversized = {"window": {"cutoff": 5, "start": 0, "sample_times": []}, "ha": [], "observations": [{"id": "x" * 9000}]}
        cases = [
            (self.envelope(context={"window": {"cutoff": 5, "start": 0, "sample_times": []}, "ha": [],
                                    "observations": [{"id": "1", "human_label": "reading"}]}), "labels_rejected"),
            (self.envelope(context={"window": {"cutoff": 5, "start": 0, "sample_times": []}, "ha": [],
                                    "observations": [{"id": "1", "objects": [{"label": "reading"}]}]}), "labels_rejected"),
            (self.envelope(system="Describe the owner's identity."), "invalid_request"),
            (self.envelope(body_sha256="0" * 64), "invalid_request"),
            (self.envelope(image="data:image/png;base64," + base64.b64encode(jpeg(fmt="PNG")).decode()), "invalid_request"),
            (self.envelope(images=5), "invalid_request"),
            (self.envelope(profile={**self.profile(), "engine_epoch": "older-epoch"}), "profile_mismatch"),
            (self.envelope(prompt_version="lab-qwen-cutoff-1"), "prompt_version_unsupported"),
            (self.envelope(context=oversized), "input_bound_exceeded"),
            (self.envelope(extra=True), "invalid_request"),
        ]
        big = self.envelope()
        big["body"]["max_tokens"] = 512
        big["body_sha256"] = canonical_digest(big["body"])
        cases.append((big, "invalid_request"))
        for envelope, reason in cases:
            with self.assertRaises(lab.LabRequestError) as refused:
                self.lab.submit(envelope, LAB_BINDING)
            self.assertEqual(refused.exception.reason, reason)
        with self.assertRaises(lab.LabRequestError) as refused:
            self.lab.submit(self.envelope(), CAMERA_BINDING)
        self.assertEqual((refused.exception.reason, refused.exception.status), ("consumer_not_registered", 403))
        self.assertEqual(self.rows(), 0)
        self.assertEqual(self.upstream.bodies, [])

    def test_expired_request_gets_a_durable_rejection_and_lookup_distinguishes_absent_work(self):
        expired = self.envelope(deadline=time.time() - 1)
        proof = self.lab.submit(expired, LAB_BINDING)
        self.assertEqual((proof["state"], proof["reason"], proof["terminal"]), ("rejected", "expired", True))
        absent = self.envelope()
        pending = self.lab.lookup(self.reference(absent), LAB_BINDING)
        self.assertEqual((pending["state"], pending["terminal"], pending["engine_epoch"]), ("not_found", False, EPOCH))
        later = lab.LabSavedActivity(self.gateway, clock=lambda: absent["deadline"] + 6)
        ended = later.lookup(self.reference(absent), LAB_BINDING)
        self.assertEqual((ended["state"], ended["terminal"]), ("not_admitted", True))
        self.lab.submit(absent, LAB_BINDING)
        with self.assertRaises(RequestConflict):
            self.lab.lookup({**self.reference(absent), "body_sha256": "1" * 64}, LAB_BINDING)

    def test_completion_without_a_retained_answer_is_terminal_failure_not_invention(self):
        envelope = self.envelope()
        self.lab.submit(envelope, LAB_BINDING)
        self.gateway.dispatch_once()
        self.backend._lab_outputs.clear()
        proof = self.lab.lookup(self.reference(envelope), LAB_BINDING)
        self.assertEqual((proof["state"], proof["reason"], proof["terminal"]), ("failed", "response_not_retained", True))
        self.assertNotIn("response", proof)

    def test_a_late_claim_is_expired_before_it_could_outlive_its_deadline(self):
        envelope = self.envelope(deadline=time.time() + 10)
        self.assertEqual(self.lab.submit(envelope, LAB_BINDING)["state"], "queued")
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")
        self.gateway.dispatch_once()  # the interactive camera request is claimed first
        # Time passes while it runs: less than the measured execution time is left.
        self.gateway.min_execution_seconds = 11
        self.assertIsNone(self.gateway.dispatch_once())
        proof = self.lab.lookup(self.reference(envelope), LAB_BINDING)
        self.assertEqual((proof["state"], proof["terminal"]), ("queue_expired", True))
        self.assertEqual(len(self.upstream.bodies), 1)  # only the camera request reached the model
        self.assertTrue(self.controller.readiness()["ready"])

    def test_deadline_is_clamped_to_the_la_bound_and_requires_execution_headroom(self):
        lead = self.envelope(deadline=time.time() + 15.5)  # a Victoria clock slightly ahead of the LA
        self.assertEqual(self.lab.submit(lead, LAB_BINDING)["state"], "queued")
        record = self.ledger.get(canonical_digest(["victoria", "victoria-lab", lead["request_id"]]))
        self.assertLessEqual(record.deadline, time.time() + 15)
        self.gateway.dispatch_once()
        self.assertEqual(self.lab.lookup(self.reference(lead), LAB_BINDING)["state"], "completed")
        short = self.envelope(deadline=time.time() + 1.5)
        proof = self.lab.submit(short, LAB_BINDING)
        self.assertEqual((proof["state"], proof["reason"], proof["terminal"]), ("rejected", "expired", True))
        self.assertEqual(len(self.upstream.bodies), 1)

    def test_a_row_claimed_before_the_submit_readback_still_has_its_payload(self):
        envelope = self.envelope()
        ledger_id = canonical_digest(["victoria", "victoria-lab", envelope["request_id"]])
        original, workers = self.controller.submit, []
        def submit(*args):
            original(*args)
            # The dispatcher, already past its expiry pass, claims the
            # committed row before the readback.
            from home_shared.gateway import Gateway
            workers.append(threading.Thread(target=Gateway.dispatch_once, args=(self.gateway,)))
            workers[0].start()
            until = time.monotonic() + 5
            while self.ledger.get(ledger_id).state == "queued" and time.monotonic() < until:
                time.sleep(.005)
            return self.ledger.get(ledger_id)
        self.controller.submit = submit
        try:
            self.assertIn(self.lab.submit(envelope, LAB_BINDING)["state"], {"dispatching", "running"})
        finally:
            self.controller.submit = original
        workers[0].join(5)
        proof = self.lab.lookup(self.reference(envelope), LAB_BINDING)
        self.assertEqual((proof["state"], proof["terminal"]), ("completed", True))
        self.assertTrue(self.controller.readiness()["ready"])
        refused = self.envelope(deadline=time.time() + 1)
        self.lab.submit(refused, LAB_BINDING)
        self.assertEqual((self.gateway._pending, self.backend._lab_bodies), ({}, {}))

    def test_lab_gateway_requires_bounded_measured_headroom(self):
        for value in (0, 15, 30, float("nan")):
            with self.assertRaises(ValueError):
                lab.LabGateway(self.controller, self.backend, min_execution_seconds=value)
        from home_shared.gateway import Gateway
        with self.assertRaises(ValueError):
            lab.LabSavedActivity(Gateway(self.controller, self.backend))

    def test_consumer_requires_a_manifest_bound_policy(self):
        self.close()
        with self.assertRaisesRegex(ValueError, "policy"):
            self.build(policy="9" * 64)


class ContractParityTests(unittest.TestCase):
    """The Victoria adapter/transport and the LA consumer pin one contract."""

    def test_versioned_prompt_fields_bounds_and_routes_match(self):
        import qwen_adapter
        import qwen_transport
        from analysis_result import SCHEMA_VERSION
        from reasoning import ACTIVITIES
        self.assertEqual(lab.instruction(), qwen_adapter.instruction())
        self.assertEqual(lab.ACTIVITIES, ACTIVITIES)
        self.assertEqual((lab.PROMPT_VERSION, lab.TASK, lab.RESULT_SCHEMA, lab.MAX_IMAGES, lab.MAX_OUTPUT_TOKENS, lab.MAX_IMAGE_SIZE),
                         (qwen_adapter.PROMPT_VERSION, qwen_adapter.TASK, SCHEMA_VERSION, qwen_adapter.MAX_IMAGES,
                          qwen_adapter.MAX_OUTPUT_TOKENS, qwen_adapter.MAX_IMAGE_SIZE))
        self.assertEqual((lab.EVIDENCE_FIELDS, lab.PROVENANCE_FIELDS),
                         (qwen_adapter.CONTEXT_EVIDENCE_FIELDS, qwen_adapter.CONTEXT_PROVENANCE_FIELDS))
        self.assertEqual((lab.SUBMIT_PATH, lab.LOOKUP_PATH, lab.ENVELOPE_FIELDS),
                         (qwen_transport.SUBMIT_PATH, qwen_transport.LOOKUP_PATH, qwen_transport.ENVELOPE_FIELDS))
        self.assertLessEqual(lab.MAX_ENVELOPE_BYTES, qwen_transport.MAX_ENVELOPE_BYTES)
        emitted = {(400, "invalid_request"), (400, "labels_rejected"), (400, "profile_mismatch"),
                   (400, "prompt_version_unsupported"), (400, "input_bound_exceeded"), (403, "consumer_not_registered"),
                   (411, "length_required"), (413, "body_too_large")}
        self.assertEqual(qwen_transport.PRE_EXECUTION, emitted)
        # The policy digest the manifest must bind changes with any of these.
        self.assertEqual(lab.POLICY["instruction_sha256"], canonical_digest(qwen_adapter.instruction()))
        # Profiles and engine epochs use the admission identifier syntax on both sides.
        self.assertEqual(lab.PROFILE_FIELDS, qwen_adapter.PROFILE_FIELDS)
        self.assertEqual(lab._ID.pattern, qwen_adapter._ID.pattern)
        self.assertEqual(lab._ID.pattern, qwen_transport._ID.pattern)


class Harness(lab.LabRoutes, _Handler):
    pass


class LabHttpHarness:
    """Loopback bearer harness for route tests; production uses LabIngress mTLS."""
    def start_http(self):
        credentials = {hashlib.sha256(LAB_TOKEN).hexdigest(): LAB_BINDING,
                       hashlib.sha256(CAMERA_TOKEN).hexdigest(): CAMERA_BINDING}
        self.server = GatewayHTTPServer(("127.0.0.1", 0), self.gateway, credentials)
        self.server.RequestHandlerClass = Harness
        self.server.lab = self.lab
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def stop_http(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self, path, value, token=LAB_TOKEN, raw=None, length=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = raw if raw is not None else json.dumps(value).encode()
        connection.putrequest("POST", path)
        connection.putheader("Authorization", "Bearer " + token.decode())
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(length if length is not None else len(body)))
        connection.endheaders()
        if length is None:
            connection.send(body)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result


class LabRouteTests(LaFixture, LabHttpHarness, unittest.TestCase):
    def setUp(self):
        self.build()
        self.start_http()

    def tearDown(self):
        self.stop_http()
        self.close()

    def test_routes_return_typed_refusals_and_bound_proofs(self):
        envelope = self.envelope()
        status, value = self.post(lab.SUBMIT_PATH, envelope)
        self.assertEqual((status, value["state"]), (202, "queued"))
        self.gateway.dispatch_once()
        status, value = self.post(lab.LOOKUP_PATH, self.reference(envelope))
        self.assertEqual((status, value["state"]), (200, "completed"))
        self.assertEqual(self.post(lab.SUBMIT_PATH, self.envelope(prompt_version="old")), (400, {"reason": "prompt_version_unsupported"}))
        self.assertEqual(self.post(lab.SUBMIT_PATH, envelope, token=CAMERA_TOKEN), (403, {"reason": "consumer_not_registered"}))
        self.assertEqual(self.post("/v1/inference", self.camera(), token=LAB_TOKEN), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.post(lab.SUBMIT_PATH, None, raw=b"{not json"), (400, {"reason": "invalid_request"}))
        self.assertEqual(self.post(lab.LOOKUP_PATH, None, raw=b"{}", length=lab.MAX_REFERENCE_BYTES + 1), (413, {"reason": "body_too_large"}))
        self.assertEqual(self.post(lab.LOOKUP_PATH, {**self.reference(envelope), "body": {}}), (400, {"reason": "invalid_request"}))


if __name__ == "__main__":
    unittest.main()
