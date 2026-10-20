"""LA live-scene perceiver with fakes only: temp ledger, synthetic authorities,
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

from home_shared import lab_live_scene as live
from home_shared import lab_saved_activity as lab
from home_shared.admission import (AdmissionController, AdmissionLedger, RECEIPTS, RESOURCES, RequestConflict,
                                   ResourceManifest, VerifiedReceipt, canonical_digest)
from home_shared.gateway import CredentialBinding, GatewayHTTPServer, _Handler
from home_shared.openai_backend import InputTokenBound, PROMPT

EPOCH = "live-fixture-epoch"
LIVE_BINDING = CredentialBinding("victoria", "victoria-lab-live", live.CONSUMER_ID, {live.LIVE_CAMERA: live.LIVE_ROOM}, "live-rev-1")
LAB_BINDING = CredentialBinding("victoria", "victoria-lab", lab.CONSUMER_ID, {lab.LAB_CAMERA: lab.LAB_ROOM}, "lab-rev-1")
CAMERA_BINDING = CredentialBinding("victoria", "den-adapter", "camera-victoria", {"den_oak": "den"}, "rev-1", True)
LIVE_TOKEN, LAB_TOKEN, CAMERA_TOKEN = (b"live-fixture-token-0123456789abcdef", b"lab-fixture-token-0123456789abcdef",
                                       b"camera-fixture-token-0123456789abcd")
FACTS = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
         "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
         "activity_hypothesis": "reading", "visible_basis": ["person_visible", "book_in_hands", "book_pages_open"],
         "change_vs_prior": "no_prior"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def jpeg(width=640, height=360, fmt="JPEG"):
    from PIL import Image
    encoded = io.BytesIO()
    Image.new("RGB", (width, height), (40, 50, 60)).save(encoded, format=fmt)
    return encoded.getvalue()


def manifest(max_queue=3, saved=True, live_deadline=7, policy=None, max_input_tokens=8192):
    engine = "fake-live-engine-" + uuid.uuid4().hex[:12]
    amount = lambda value: {key: value for key in RESOURCES}
    routes = {"camera-victoria": "victoria", live.CONSUMER_ID: "victoria"}
    deadlines = {"camera-victoria": 15, live.CONSUMER_ID: live_deadline}
    policies = {"camera-victoria": "c" * 64, live.CONSUMER_ID: policy or live.POLICY_SHA256}
    if saved:
        routes[lab.CONSUMER_ID], deadlines[lab.CONSUMER_ID], policies[lab.CONSUMER_ID] = "victoria", 15, lab.POLICY_SHA256
    return ResourceManifest.from_dict({
        "version": 3, "engine_id": engine, "model_alias": "qwen3-vl-30b", "model_revision": "a" * 64,
        "upstream": "http://127.0.0.1:19999/v1",
        "services": [{"id": engine, "image_sha256": "b" * 64, "model_sha256": "a" * 64,
                      "startup_peak": amount(15), "steady_peak": amount(10),
                      "caps": {"ram_bytes": 30, "swap_bytes": 0, "cpu_millicores": 30, "pids": 10, "io_bytes_per_second": 30}}],
        "capacity": amount(100), "os_recovery_reserve": amount(20), "resident_overlap_peak": amount(20),
        "request_peak": amount(10), "max_active": 1, "max_queue": max_queue, "max_ledger_records": 1000,
        "max_input_tokens": max_input_tokens, "max_output_tokens": 256, "max_image_bytes": 256 * 1024,
        "max_deadline_seconds": 15, "consumer_routes": routes, "consumer_deadline_seconds": deadlines,
        "consumer_policy_sha256": policies, "free_storage_floor_bytes": 1, "health_max_age_seconds": 5,
        "receipt_max_age_seconds": 60, "measurement_artifact_sha256": "c" * 64})


class Verifier:
    def __init__(self):
        self.open = True

    def verify_admission(self, subject, epoch, now):
        if not self.open:
            raise RuntimeError("health receipt unavailable")
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
        text = json.dumps(FACTS) if "response_format" in value else json.dumps(
            {"schema_version": "lab-activity-1", "activity": "reading", "evidence_refs": []})
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


class LiveFixture:
    """Real ledger/controller/gateway composition around fakes."""
    def build(self, *, saved=True, live_min=4, saved_min=2, **kwargs):
        self.directory = tempfile.TemporaryDirectory()
        self.previous_temp = tempfile.tempdir
        tempfile.tempdir = self.directory.name
        self.manifest = manifest(saved=saved, **kwargs)
        self.ledger = AdmissionLedger(Path(self.directory.name) / "ledger.sqlite")
        self.verifier = Verifier()
        self.controller = AdmissionController(self.ledger, self.manifest, self.verifier)
        self.controller.enable(EPOCH)
        self.authority = Authority(self.manifest)
        self.upstream = Upstream()
        self.backend = live.LiveSceneBackend(self.manifest, EPOCH, attestor=self.authority, input_authority=self.authority,
                                             _connect=self.upstream.connect)
        self.gateway = live.compose_live_scene_gateway(self.controller, self.backend, live_min_execution_seconds=live_min,
                                                       saved_min_execution_seconds=saved_min if saved else None)
        self.live = live.LiveScenePerceiver(self.gateway)
        self.lab = lab.LabSavedActivity(self.gateway) if saved else None

    def close(self):
        self.ledger.close()
        tempfile.tempdir = self.previous_temp
        self.directory.cleanup()

    def profile(self, prompt=live.PROMPT_VERSION):
        return {"model_version": "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8", "served_model": "qwen3-vl-30b",
                "artifact_sha256": "a" * 64, "engine_epoch": EPOCH, "runtime_revision": "fixture-runtime",
                "adapter_revision": prompt}

    def body(self, *, images=3, context=None, image=None, offsets=None, system=None, **changes):
        context = context if context is not None else {"placement": live.PLACEMENT, "local_time": "evening 19:40",
                                                       live.PRIOR_KEY: None}
        offsets = offsets if offsets is not None else [round(1.5 * (images - 1 - i), 1) for i in range(images)]
        content = [{"type": "text", "text": canonical(context)}]
        for index, offset in enumerate(offsets):
            content += [{"type": "text", "text": canonical({"frame": index, "seconds_before_latest": offset})},
                        {"type": "image_url", "image_url": {"url": image or "data:image/jpeg;base64," + base64.b64encode(jpeg()).decode()}}]
        body = {"model": "qwen3-vl-30b", "messages": [{"role": "system", "content": system or live.instruction()},
                {"role": "user", "content": content}], "temperature": 0, "max_tokens": live.MAX_OUTPUT_TOKENS,
                "stream": False, "response_format": live.response_format()}
        body.update(changes)
        return body

    def envelope(self, body=None, deadline=None, **changes):
        body = body if body is not None else self.body()
        value = {"request_id": uuid.uuid4().hex * 2, "site_id": "victoria", "session_id": "f" * 32, "profile": self.profile(),
                 "body": body, "body_sha256": canonical_digest(body), "prompt_version": live.PROMPT_VERSION,
                 "task": live.TASK, "deadline": deadline if deadline is not None else time.time() + 7}
        value.update(changes)
        return value

    def saved_envelope(self):
        content = [{"type": "text", "text": canonical({"window": {"cutoff": 5, "start": 0, "sample_times": [0, 5]}, "ha": [], "observations": []})},
                   {"type": "text", "text": canonical({"media_id": "%032x" % 1, "observed_at": 100, "available_at": 100.5})},
                   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg(64, 36)).decode()}}]
        body = {"model": "qwen3-vl-30b", "messages": [{"role": "system", "content": lab.instruction()},
                {"role": "user", "content": content}], "temperature": 0, "max_tokens": 256, "stream": False}
        return {"request_id": uuid.uuid4().hex * 2, "site_id": "victoria", "session_id": "f" * 32,
                "profile": self.profile(lab.PROMPT_VERSION), "body": body, "body_sha256": canonical_digest(body),
                "prompt_version": lab.PROMPT_VERSION, "task": lab.TASK, "deadline": time.time() + 10}

    def camera(self, request_id="camera-1", sequence=1):
        now = time.time()
        return {"version": 1, "request_id": request_id, "site_id": "victoria", "device_id": "den-adapter",
                "camera_id": "den_oak", "room_id": "den", "source_event_id": "event-1", "producer_session": "boot-1",
                "producer_sequence": sequence, "occupancy_generation": 1, "capture_session": "camera-1", "frame_id": "frame-%d" % sequence,
                "scheduled_at": now, "captured_at": now, "received_at": now, "clock_uncertainty_seconds": 0.1,
                "timestamp_quality": "validated_observation", "request_deadline": now + 15, "task_class": "interactive",
                "configuration_revision": "rev-1", "max_output_tokens": 128,
                "image_base64": base64.b64encode(jpeg()).decode()}

    def reference(self, envelope):
        return {k: v for k, v in envelope.items() if k != "body"}

    def ledger_id(self, envelope, binding=LIVE_BINDING):
        return canonical_digest(["victoria", binding.device_id, envelope["request_id"]])

    def rows(self):
        return self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    def refused(self, envelope, binding=LIVE_BINDING):
        with self.assertRaises(live.LiveSceneRefusal) as caught:
            self.live.submit(envelope, binding)
        return caught.exception.status, caught.exception.reason


class LiveScenePerceiverTests(LiveFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def tearDown(self):
        self.close()

    def test_background_request_forwards_the_exact_hashed_body_and_returns_a_bound_proof(self):
        envelope = self.envelope()
        queued = self.live.submit(envelope, LIVE_BINDING)
        self.assertEqual((queued["state"], queued["terminal"], queued["body_sha256"]), ("queued", False, envelope["body_sha256"]))
        record = self.ledger.get(self.ledger_id(envelope))
        self.assertEqual((record.task_class, record.consumer_id), ("background", live.CONSUMER_ID))
        self.assertLessEqual(record.deadline, time.time() + 7)
        self.gateway.dispatch_once()
        head, body = self.upstream.bodies[0]
        self.assertEqual(hashlib.sha256(body).hexdigest(), envelope["body_sha256"])
        sent = json.loads(body)
        self.assertEqual((sent["temperature"], sent["max_tokens"], sent["response_format"]),
                         (0, live.MAX_OUTPUT_TOKENS, live.response_format()))
        proof = self.live.lookup(self.reference(envelope), LIVE_BINDING)
        self.assertEqual((proof["state"], proof["terminal"], proof["engine_epoch"], proof["artifact_sha256"]),
                         ("completed", True, EPOCH, "a" * 64))
        self.assertEqual(json.loads(proof["response"]["choices"][0]["message"]["content"]), FACTS)
        self.assertEqual(self.live.submit(envelope, LIVE_BINDING)["state"], "completed")  # idempotent, never re-run
        self.assertEqual(len(self.upstream.bodies), 1)
        self.assertTrue(self.controller.readiness()["ready"])

    def test_skipped_while_interactive_work_is_queued_without_any_ledger_write(self):
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")
        before = self.rows()
        self.assertEqual(self.refused(self.envelope()), (429, "interactive_queued"))
        self.assertEqual(self.rows(), before)
        self.gateway.dispatch_once()
        self.assertIn(PROMPT.encode(), self.upstream.bodies[0][1])
        self.assertEqual(self.live.submit(self.envelope(), LIVE_BINDING)["state"], "queued")

    def test_queued_camera_chat_is_claimed_before_a_queued_perceiver_request(self):
        envelope = self.envelope()
        self.assertEqual(self.live.submit(envelope, LIVE_BINDING)["state"], "queued")
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")
        self.gateway.dispatch_once()
        self.assertIn(PROMPT.encode(), self.upstream.bodies[0][1])
        self.gateway.dispatch_once()
        self.assertEqual(hashlib.sha256(self.upstream.bodies[1][1]).hexdigest(), envelope["body_sha256"])
        self.assertEqual(self.upstream.peak, 1)

    def test_at_most_one_perceiver_request_is_outstanding(self):
        first = self.envelope()
        self.assertEqual(self.live.submit(first, LIVE_BINDING)["state"], "queued")
        before = self.rows()
        self.assertEqual(self.refused(self.envelope()), (429, "perceiver_busy"))
        self.assertEqual(self.rows(), before)
        # The same id is idempotent, not a second request.
        self.assertEqual(self.live.submit(first, LIVE_BINDING)["state"], "queued")
        self.gateway.dispatch_once()
        self.assertEqual(self.live.submit(self.envelope(), LIVE_BINDING)["state"], "queued")

    def test_never_takes_the_last_queue_slot(self):
        self.close()
        self.build(max_queue=3)
        self.assertEqual(self.lab.submit(self.saved_envelope(), LAB_BINDING)["state"], "queued")
        self.assertEqual(self.lab.submit(self.saved_envelope(), LAB_BINDING)["state"], "queued")
        before = self.rows()
        self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")

    def test_closed_admission_is_a_typed_skip_not_a_tombstone(self):
        self.verifier.open = False
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(self.rows(), 0)

    def test_deadline_is_clamped_to_the_la_bound_and_requires_headroom_without_a_ledger_write(self):
        lead = self.envelope(deadline=time.time() + 7.5)  # a Victoria clock slightly ahead of the LA
        self.assertEqual(self.live.submit(lead, LIVE_BINDING)["state"], "queued")
        self.assertLessEqual(self.ledger.get(self.ledger_id(lead)).deadline, time.time() + 7)
        self.gateway.dispatch_once()
        before = self.rows()
        self.assertEqual(self.refused(self.envelope(deadline=time.time() + 3)), (409, "deadline_insufficient"))
        self.assertEqual(self.refused(self.envelope(deadline=time.time() - 1)), (409, "deadline_insufficient"))
        self.assertEqual(self.rows(), before)

    def test_a_late_claim_expires_as_never_sent_and_keeps_admission_open(self):
        envelope = self.envelope()
        self.assertEqual(self.live.submit(envelope, LIVE_BINDING)["state"], "queued")
        self.assertEqual(self.gateway.submit(self.camera(), CAMERA_BINDING)["status"], "queued")
        self.gateway.dispatch_once()  # the interactive camera request is claimed first
        # Time passes while it runs: less than the measured execution time is left.
        self.gateway._headroom[live.CONSUMER_ID] = 7.5
        self.assertIsNone(self.gateway.dispatch_once())
        proof = self.live.lookup(self.reference(envelope), LIVE_BINDING)
        self.assertEqual((proof["state"], proof["terminal"]), ("queue_expired", True))
        self.assertEqual(len(self.upstream.bodies), 1)
        self.assertTrue(self.controller.readiness()["ready"])
        self.assertEqual(self.live.submit(self.envelope(), LIVE_BINDING)["state"], "queued")

    def test_length_stop_is_a_terminal_answer_and_keeps_admission_open(self):
        self.upstream.finish = "length"
        envelope = self.envelope()
        self.live.submit(envelope, LIVE_BINDING)
        self.gateway.dispatch_once()
        proof = self.live.lookup(self.reference(envelope), LIVE_BINDING)
        self.assertEqual((proof["state"], proof["response"]["choices"][0]["finish_reason"]), ("completed", "length"))
        self.assertTrue(self.controller.readiness()["ready"])

    def test_refusals_happen_before_any_ledger_access(self):
        context = {"placement": live.PLACEMENT, "local_time": "evening 19:40", live.PRIOR_KEY: None}
        prior_text = dict(FACTS, activity_hypothesis="the owner is reading a novel")
        noncanonical = self.body()
        noncanonical["messages"][1]["content"][0]["text"] = json.dumps(context)
        png = "data:image/png;base64," + base64.b64encode(jpeg(fmt="PNG")).decode()
        small = "data:image/jpeg;base64," + base64.b64encode(jpeg(64, 36)).decode()
        cases = [
            (self.body(images=1, offsets=[0.0]), "invalid_request"),
            (self.body(images=5, offsets=[4.0, 3.0, 2.0, 1.0, 0.0]), "invalid_request"),
            (self.body(offsets=[0.0, 1.5, 3.0]), "invalid_request"),  # newest must be last
            (self.body(offsets=[9.0, 1.5, 0.0]), "invalid_request"),  # span bound
            (self.body(image=png), "invalid_request"),
            (self.body(image=small), "invalid_request"),  # exactly 640x360
            (self.body(context={**context, "ha": {"computer_active": "on"}}), "invalid_request"),
            (self.body(context={**context, "detections": ["book"]}), "invalid_request"),
            (self.body(context={**context, "placement": "living room"}), "invalid_request"),
            (self.body(context={**context, "local_time": "Tuesday at 7pm"}), "invalid_request"),
            (self.body(context={**context, live.PRIOR_KEY: prior_text}), "invalid_request"),
            (self.body(context={**context, live.PRIOR_KEY: {**FACTS, "note": "x"}}), "invalid_request"),
            (noncanonical, "invalid_request"),
            (self.body(system="Describe the person's identity."), "invalid_request"),
            (self.body(temperature=1), "invalid_request"),
            (self.body(max_tokens=256), "invalid_request"),
            (self.body(response_format={"type": "json_object"}), "invalid_request"),
            ({k: v for k, v in self.body().items() if k != "response_format"}, "invalid_request"),
            (self.body(stop=["}"]), "invalid_request"),
        ]
        envelopes = [(self.envelope(body), reason) for body, reason in cases]
        envelopes += [(self.envelope(body_sha256="0" * 64), "invalid_request"),
                      (self.envelope(profile={**self.profile(), "engine_epoch": "older-epoch"}), "profile_mismatch"),
                      (self.envelope(prompt_version="lab-live-scene-prompt-0"), "prompt_version_unsupported"),
                      (self.envelope(task=lab.TASK), "prompt_version_unsupported"),
                      (self.envelope(session_id="not-a-session"), "invalid_request"),
                      (self.envelope(extra=True), "invalid_request")]
        for envelope, reason in envelopes:
            self.assertEqual(self.refused(envelope), (400, reason), reason)
        self.assertEqual(self.refused(self.envelope(), LAB_BINDING), (403, "consumer_not_registered"))
        self.assertEqual(self.rows(), 0)
        self.assertEqual(self.upstream.bodies, [])

    def test_input_bound_is_checked_before_admission(self):
        self.close()
        self.build(max_input_tokens=1200)
        self.assertEqual(self.refused(self.envelope(self.body(images=4, offsets=[3.0, 2.0, 1.0, 0.0]))),
                         (400, "input_bound_exceeded"))
        self.assertEqual(self.rows(), 0)

    def test_a_valid_prior_output_is_accepted_as_enum_context_only(self):
        context = {"placement": live.PLACEMENT, "local_time": "night 02:05", live.PRIOR_KEY: FACTS}
        self.assertEqual(self.live.submit(self.envelope(self.body(context=context)), LIVE_BINDING)["state"], "queued")

    def test_lookup_distinguishes_absent_work_and_binds_identity(self):
        absent = self.envelope()
        pending = self.live.lookup(self.reference(absent), LIVE_BINDING)
        self.assertEqual((pending["state"], pending["terminal"], pending["engine_epoch"]), ("not_found", False, EPOCH))
        later = live.LiveScenePerceiver(self.gateway, clock=lambda: absent["deadline"] + 3)
        ended = later.lookup(self.reference(absent), LIVE_BINDING)
        self.assertEqual((ended["state"], ended["terminal"]), ("not_admitted", True))
        self.live.submit(absent, LIVE_BINDING)
        with self.assertRaises(RequestConflict):
            self.live.lookup({**self.reference(absent), "body_sha256": "1" * 64}, LIVE_BINDING)
        with self.assertRaises(live.LiveSceneRefusal):
            self.live.lookup({**self.reference(absent), "body": {}}, LIVE_BINDING)

    def test_both_lab_consumers_share_one_gateway_with_their_own_headroom(self):
        saved = self.saved_envelope()
        self.assertEqual(self.lab.submit(saved, LAB_BINDING)["state"], "queued")
        self.gateway.dispatch_once()
        proof = self.lab.lookup({k: v for k, v in saved.items() if k != "body"}, LAB_BINDING)
        self.assertEqual(json.loads(proof["response"]["choices"][0]["message"]["content"])["activity"], "reading")
        self.assertEqual(self.gateway._headroom, {live.CONSUMER_ID: 4.0, lab.CONSUMER_ID: 2.0})
        self.assertEqual(self.gateway.min_execution_seconds, 2.0)

    def test_completion_without_a_retained_answer_is_terminal_failure_not_invention(self):
        envelope = self.envelope()
        self.live.submit(envelope, LIVE_BINDING)
        self.gateway.dispatch_once()
        self.backend._lab_outputs.clear()
        proof = self.live.lookup(self.reference(envelope), LIVE_BINDING)
        self.assertEqual((proof["state"], proof["reason"], proof["terminal"]), ("failed", "response_not_retained", True))
        self.assertNotIn("response", proof)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = LiveFixture()

    def tearDown(self):
        if hasattr(self.fixture, "ledger"):
            self.fixture.close()

    def test_live_only_composition_needs_no_saved_consumer(self):
        self.fixture.build(saved=False)
        self.assertIsNone(self.fixture.gateway.min_execution_seconds)
        self.assertEqual(self.fixture.live.submit(self.fixture.envelope(), LIVE_BINDING)["state"], "queued")

    def test_headroom_and_short_deadline_are_required(self):
        f = self.fixture
        f.build()
        for value in (0, 7, 30, float("nan")):
            with self.assertRaises(ValueError):
                live.LiveSceneGateway(f.controller, f.backend, clock=time.time, live_min_execution_seconds=value,
                                      saved_min_execution_seconds=2)
        with self.assertRaises(ValueError):  # saved consumer routed without its headroom
            live.LiveSceneGateway(f.controller, f.backend, clock=time.time, live_min_execution_seconds=4)
        from home_shared.gateway import Gateway
        with self.assertRaises(ValueError):
            live.LiveScenePerceiver(Gateway(f.controller, f.backend))
        f.close()
        with self.assertRaises(ValueError):  # a 15 s perceiver deadline is not short
            f.build(live_deadline=15)
        f.close()
        with self.assertRaises(ValueError):  # headroom for a consumer that is not routed
            f.build(saved=False)
            live.LiveSceneGateway(f.controller, f.backend, clock=time.time, live_min_execution_seconds=4,
                                  saved_min_execution_seconds=2)
        with self.assertRaises(ValueError):
            live.compose_live_scene_gateway(f.controller, object(), live_min_execution_seconds=4)

    def test_consumer_requires_a_manifest_bound_policy(self):
        with self.assertRaisesRegex(ValueError, "policy"):
            self.fixture.build(policy="9" * 64)

    def test_policy_digest_binds_the_prompt_schema_and_limits(self):
        self.assertEqual(live.POLICY["instruction_sha256"], canonical_digest(live.instruction()))
        self.assertEqual(live.POLICY["response_format_sha256"], canonical_digest(live.response_format()))
        self.assertEqual((live.POLICY["task_class"], live.POLICY["max_outstanding"], live.POLICY["max_output_tokens"]),
                         ("background", 1, 192))
        self.assertNotEqual(live.POLICY_SHA256, lab.POLICY_SHA256)
        self.assertNotIn("{{", live.instruction())
        for field, values in live.FIELDS.items():
            self.assertEqual(live.response_format()["json_schema"]["schema"]["properties"][field]["enum"], list(values))


class Harness(live.LiveSceneRoutes, lab.LabRoutes, _Handler):
    pass


class LiveRouteTests(LiveFixture, unittest.TestCase):
    def setUp(self):
        self.build()
        credentials = {hashlib.sha256(LIVE_TOKEN).hexdigest(): LIVE_BINDING,
                       hashlib.sha256(LAB_TOKEN).hexdigest(): LAB_BINDING,
                       hashlib.sha256(CAMERA_TOKEN).hexdigest(): CAMERA_BINDING}
        self.server = GatewayHTTPServer(("127.0.0.1", 0), self.gateway, credentials)
        self.server.RequestHandlerClass = Harness
        self.server.lab, self.server.live = self.lab, self.live
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.close()

    def request(self, method, path, value=None, token=LIVE_TOKEN, raw=None, length=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = raw if raw is not None else (json.dumps(value).encode() if value is not None else b"")
        connection.putrequest(method, path)
        connection.putheader("Authorization", "Bearer " + token.decode())
        if method == "POST":
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(length if length is not None else len(body)))
        connection.endheaders()
        if method == "POST" and length is None:
            connection.send(body)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_routes_return_typed_refusals_and_bound_proofs(self):
        envelope = self.envelope()
        status, value = self.request("POST", live.SUBMIT_PATH, envelope)
        self.assertEqual((status, value["state"]), (202, "queued"))
        self.assertEqual(self.request("POST", live.SUBMIT_PATH, self.envelope()), (429, {"reason": "perceiver_busy"}))
        self.gateway.dispatch_once()
        status, value = self.request("POST", live.LOOKUP_PATH, self.reference(envelope))
        self.assertEqual((status, value["state"]), (200, "completed"))
        self.assertEqual(self.request("POST", live.SUBMIT_PATH, self.envelope(prompt_version="old")),
                         (400, {"reason": "prompt_version_unsupported"}))
        self.assertEqual(self.request("POST", live.SUBMIT_PATH, envelope, token=LAB_TOKEN), (403, {"reason": "consumer_not_registered"}))
        self.assertEqual(self.request("POST", live.SUBMIT_PATH, envelope, token=CAMERA_TOKEN), (403, {"reason": "consumer_not_registered"}))
        self.assertEqual(self.request("POST", "/v1/inference", self.camera(), token=LIVE_TOKEN), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.request("POST", lab.SUBMIT_PATH, self.saved_envelope(), token=LIVE_TOKEN), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.request("GET", "/v1/inference/camera-1", token=LIVE_TOKEN), (403, {"reason": "consumer_route_denied"}))
        self.assertEqual(self.request("POST", live.SUBMIT_PATH, None, raw=b"{not json"), (400, {"reason": "invalid_request"}))
        self.assertEqual(self.request("POST", live.LOOKUP_PATH, None, raw=b"{}", length=live.MAX_REFERENCE_BYTES + 1),
                         (413, {"reason": "body_too_large"}))
        # The saved consumer and camera routes behave as before on the shared handler.
        self.assertEqual(self.request("POST", lab.SUBMIT_PATH, self.saved_envelope(), token=LAB_TOKEN)[0], 202)
        self.assertEqual(self.request("POST", "/v1/inference", self.camera(), token=CAMERA_TOKEN)[0], 202)

    def test_saved_paths_are_refused_when_the_saved_consumer_is_not_composed(self):
        self.server.lab = None
        self.assertEqual(self.request("POST", lab.SUBMIT_PATH, self.saved_envelope(), token=LAB_TOKEN),
                         (403, {"reason": "consumer_not_registered"}))

    def test_every_typed_refusal_is_in_the_declared_pre_execution_set(self):
        seen = set()
        self.request("POST", live.SUBMIT_PATH, self.envelope())
        seen.add(self.request("POST", live.SUBMIT_PATH, self.envelope())[1]["reason"])
        seen.add(self.request("POST", live.SUBMIT_PATH, self.envelope(prompt_version="x"))[1]["reason"])
        seen.add(self.request("POST", live.SUBMIT_PATH, self.envelope(extra=1))[1]["reason"])
        seen.add(self.request("POST", live.SUBMIT_PATH, self.envelope(profile={**self.profile(), "engine_epoch": "e2"}))[1]["reason"])
        self.assertTrue(seen <= {reason for _, reason in live.PRE_EXECUTION})
        statuses = {reason: status for status, reason in live.PRE_EXECUTION}
        self.assertEqual(len(statuses), len(live.PRE_EXECUTION))


if __name__ == "__main__":
    unittest.main()
