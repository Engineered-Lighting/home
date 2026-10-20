"""Local-only fake engine tests; never invokes a real model or production API."""

import base64
from copy import deepcopy
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from home_shared.admission import (AdmissionClosed, AdmissionController, AdmissionError,
                                   AdmissionLedger, RequestConflict, ResourceManifest)
from home_shared.admission import canonical_digest
from home_shared.gateway import (BackendResult, CredentialBinding, Gateway, GatewayHTTPServer,
                                 MAX_IMAGE_BYTES, jpeg_dimensions, validate_inference_request)
from test_admission import FakeVerifier, manifest_fixture


def header_fixture(width=640, height=360):
    """Marker-only JPEG fixture, suitable for a fake decoder, not a real image."""
    sof = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    sos = b"\x01\x01\x00\x00\x3f\x00"
    return (b"\xff\xd8\xff\xc0" + (len(sof) + 2).to_bytes(2, "big") + sof +
            b"\xff\xda" + (len(sos) + 2).to_bytes(2, "big") + sos + b"\x01\xff\xd9")


def binding_fixture(site="victoria"):
    return CredentialBinding(site, "den-adapter", "fake-" + site, {"den_oak": "den"}, "rev-1", True)


def request_fixture(now=1000.0, request_id="request-1", site="victoria"):
    return {
        "version": 1, "request_id": request_id, "site_id": site, "device_id": "den-adapter",
        "camera_id": "den_oak", "room_id": "den", "source_event_id": "event-1",
        "producer_session": "adapter-boot-1", "producer_sequence": 1, "occupancy_generation": 1,
        "capture_session": "camera-boot-1", "frame_id": "frame-1", "scheduled_at": now,
        "captured_at": now, "received_at": now, "clock_uncertainty_seconds": 0.1,
        "timestamp_quality": "validated_observation", "request_deadline": now + 15,
        "task_class": "background", "configuration_revision": "rev-1", "max_output_tokens": 128,
        "image_base64": base64.b64encode(header_fixture()).decode(),
    }


class FakeBackend:
    def __init__(self, controller):
        self.controller = controller
        self.calls = []
        self.fail = False
        self.on_execute = None

    def execute(self, request, deadline):
        # This is the fake acceptance boundary: reservation must be durable
        # before execute is entered, the point where real bytes could be sent.
        record = self.controller.ledger.get(request.ledger_id)
        assert record.state == "running" and record.reservation
        self.calls.append(request)
        if self.fail:
            raise TimeoutError("private upstream error must not reach user")
        if self.on_execute:
            self.on_execute(request)
        return BackendResult("A person is seated in the den.", ({"label": "person", "count": 1},),
                             self.controller.manifest.model_revision,
                             (record.engine_epoch, request.ledger_id, "finished"))


class ValidationTests(unittest.TestCase):
    def test_complete_request_bound_to_exact_credential(self):
        request = validate_inference_request(request_fixture(), binding_fixture(), 1000)
        self.assertEqual(request.valid_until, 1180)
        self.assertEqual(request.image, header_fixture())
        for field in request_fixture():
            payload = request_fixture()
            del payload[field]
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                validate_inference_request(payload, binding_fixture(), 1000)
        for field, value in (("site_id", "echo"), ("device_id", "different-device"),
                             ("camera_id", "unknown"), ("room_id", "bedroom"),
                             ("configuration_revision", "rev-2"), ("version", True)):
            payload = request_fixture()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                validate_inference_request(payload, binding_fixture(), 1000)

    def test_no_url_or_prompt_escape(self):
        for field in ("url", "image_url", "prompt", "upstream", "tools"):
            payload = request_fixture()
            payload[field] = "http://some-secret-service/"
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                validate_inference_request(payload, binding_fixture(), 1000)

    def test_freshness_timestamp_uncertainty_deadline_and_tokens(self):
        variants = [
            {"captured_at": 996.9}, {"captured_at": 1002}, {"received_at": 999},
            {"clock_uncertainty_seconds": 1.1}, {"clock_uncertainty_seconds": -0.1},
            {"clock_uncertainty_seconds": float("nan")}, {"request_deadline": 1016},
            {"timestamp_quality": "packaged_at"}, {"max_output_tokens": 129},
            {"producer_sequence": True}, {"producer_sequence": -1},
        ]
        for fields in variants:
            payload = request_fixture()
            payload.update(fields)
            with self.subTest(fields=fields), self.assertRaises(AdmissionError):
                validate_inference_request(payload, binding_fixture(), 1000, max_output_tokens=128)
        with self.assertRaises(AdmissionError):
            validate_inference_request(request_fixture(), binding_fixture(), 1015)

    def test_header_bounds_without_unbounded_decoder(self):
        self.assertEqual(jpeg_dimensions(header_fixture()), (640, 360))
        for image in (b"not JPEG", header_fixture(65535, 65535), header_fixture()[:-1],
                      b"\xff\xd8\xff\xc0\xff\xff\xff\xd9", b"x" * (MAX_IMAGE_BYTES + 1)):
            with self.subTest(length=len(image)), self.assertRaises(AdmissionError):
                jpeg_dimensions(image)
        for encoded in ("not_base64!", "a" * (4 * ((MAX_IMAGE_BYTES + 2) // 3) + 1)):
            payload = request_fixture()
            payload["image_base64"] = encoded
            with self.assertRaises(AdmissionError):
                validate_inference_request(payload, binding_fixture(), 1000)

    def test_shared_device_cannot_promote_task_priority(self):
        binding = CredentialBinding("victoria", "den-adapter", "fake-victoria", {"den_oak": "den"}, "rev-1")
        payload = request_fixture()
        payload["task_class"] = "interactive"
        with self.assertRaises(AdmissionError):
            validate_inference_request(payload, binding, 1000)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.db"
        self.ledger = AdmissionLedger(self.path)
        self.now = 1000.0
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest_fixture()),
                                              FakeVerifier(), clock=lambda: self.now)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: self.now,
                               image_validator=jpeg_dimensions)
        self.binding = binding_fixture()

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_disabled_gateway_never_calls_backend(self):
        self.assertEqual(self.gateway.submit(request_fixture(), self.binding)["outcome"], "rejected_closed")
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.gateway.readiness()["ready"])
        self.controller.enable("epoch-1")
        disabled = Gateway(self.controller, clock=lambda: self.now)
        self.assertEqual(disabled.submit(request_fixture(request_id="disabled"), self.binding)["outcome"], "rejected_closed")
        self.assertFalse(disabled.readiness()["ready"])

    def test_default_decoder_rejects_header_only_image_before_backend(self):
        self.controller.enable("epoch-1")
        default = Gateway(self.controller, self.backend, clock=lambda: self.now)
        response = default.submit(request_fixture(), self.binding)
        self.assertEqual(response["outcome"], "rejected_invalid_image")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests WHERE state='queued'").fetchone()[0], 0)

    def test_default_decoder_accepts_real_bounded_jpeg(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("positive full JPEG decoder test requires Pillow")
        self.controller.enable("epoch-1")
        output = io.BytesIO()
        Image.new("RGB", (640, 360), "black").save(output, format="JPEG", quality=85)
        payload = request_fixture()
        payload["image_base64"] = base64.b64encode(output.getvalue()).decode()
        default = Gateway(self.controller, self.backend, clock=lambda: self.now)
        self.assertEqual(default.submit(payload, self.binding)["status"], "queued")
        self.assertIn("observation", default.dispatch_once())

    def test_end_to_end_fake_preserves_identity_and_expiry(self):
        self.controller.enable("epoch-1")
        submitted = self.gateway.submit(request_fixture(), self.binding)
        self.assertEqual(submitted["status"], "queued")
        response = self.gateway.dispatch_once()
        self.assertEqual(response["status"], "terminal")
        observation = response["observation"]
        for key in ("site_id", "device_id", "camera_id", "room_id", "producer_session", "producer_sequence",
                    "occupancy_generation", "capture_session", "frame_id", "configuration_revision"):
            self.assertEqual(observation[key], request_fixture()[key])
        self.assertEqual(observation["observed_at"], 1000)
        self.assertEqual(observation["valid_until"], 1180)
        self.assertEqual(observation["uncertainty_seconds"], 0.1)
        self.assertIn("seated", observation["description"])
        self.now = 1180
        self.assertNotIn("observation", self.gateway.status("request-1", self.binding))

    def test_identical_retries_expired_or_closed_do_not_repeat_upstream(self):
        self.controller.enable("epoch-1")
        payload = request_fixture()
        self.gateway.submit(payload, self.binding)
        self.gateway.dispatch_once()
        self.now += 60
        self.controller.close_admission("maintenance")
        self.assertEqual(self.gateway.submit(payload, self.binding)["status"], "terminal")
        self.assertEqual(len(self.backend.calls), 1)
        changed = deepcopy(payload)
        changed["frame_id"] = "different-frame"
        with self.assertRaises(RequestConflict):
            self.gateway.submit(changed, self.binding)

    def test_site_device_namespaces_and_status_authorization(self):
        self.controller.enable("epoch-1")
        self.gateway.submit(request_fixture(), self.binding)
        with self.assertRaises(AdmissionError):
            self.gateway.status("request-1", binding_fixture("echo"))
        self.gateway.submit(request_fixture(site="echo"), binding_fixture("echo"))
        self.assertEqual(self.gateway.dispatch_once()["site_id"], "victoria")
        self.assertEqual(self.gateway.dispatch_once()["site_id"], "echo")
        self.assertEqual(len(self.backend.calls), 2)

    def test_unknown_upstream_outcome_keeps_charge_and_closes(self):
        self.controller.enable("epoch-1")
        self.backend.fail = True
        self.gateway.submit(request_fixture(), self.binding)
        response = self.gateway.dispatch_once()
        self.assertEqual(response["status"], "indeterminate")
        self.assertFalse(self.gateway.readiness()["ready"])
        request = self.backend.calls[0]
        self.assertTrue(self.ledger.get(request.ledger_id).reservation)
        self.assertNotIn("private", json.dumps(response))
        self.assertEqual(self.gateway.submit(request_fixture(), self.binding)["status"], "indeterminate")
        self.assertEqual(len(self.backend.calls), 1)

    def test_expired_result_never_published(self):
        self.controller.enable("epoch-1")
        self.backend.on_execute = lambda request: setattr(self, "now", 1016)
        self.gateway.submit(request_fixture(), self.binding)
        response = self.gateway.dispatch_once()
        self.assertEqual(response["status"], "terminal")
        self.assertNotIn("observation", response)

    def test_superseded_result_discarded_and_out_of_order_request_rejected(self):
        self.controller.enable("epoch-1")
        self.gateway.submit(request_fixture(), self.binding)
        newer = request_fixture(request_id="newer")
        newer["producer_sequence"] = 2
        newer["occupancy_generation"] = 2
        self.gateway.submit(newer, self.binding)
        response = self.gateway.dispatch_once()
        self.assertNotIn("observation", response)
        self.assertIn("observation", self.gateway.dispatch_once())
        older = request_fixture(request_id="older")
        self.assertEqual(self.gateway.submit(older, self.binding)["outcome"], "rejected_superseded")

    def test_rejection_survives_lost_response_restart_and_admission_reopening(self):
        payload = request_fixture()
        rejected = self.gateway.submit(payload, self.binding)
        self.assertEqual(rejected["rejection"], {"version": 1, "device_id": "den-adapter",
                                               "request_digest": canonical_digest(payload), "reason": "closed"})
        self.ledger.close()
        self.ledger = AdmissionLedger(self.path)
        self.controller = AdmissionController(self.ledger, ResourceManifest.from_dict(manifest_fixture()),
                                              FakeVerifier(), clock=lambda: self.now)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: self.now, image_validator=jpeg_dimensions)
        self.controller.enable("epoch-2")
        self.assertEqual(self.gateway.status("request-1", self.binding), rejected)
        self.assertEqual(self.gateway.submit(payload, self.binding), rejected)
        self.assertIsNone(self.gateway.dispatch_once())
        self.assertFalse(self.backend.calls)
        wrong_device = CredentialBinding("victoria", "different", "fake-victoria", {"den_oak": "den"}, "rev-1")
        with self.assertRaises(AdmissionError):
            self.gateway.status("request-1", wrong_device)
        changed = {**payload, "frame_id": "different"}
        with self.assertRaises(RequestConflict):
            self.gateway.submit(changed, self.binding)

    def test_queue_capacity_rejection_has_no_reservation_and_fresh_work_can_follow(self):
        self.controller.enable("epoch-1")
        for n in range(3):
            payload = request_fixture(request_id=f"r-{n}")
            payload["producer_sequence"] = n + 1
            self.gateway.submit(payload, self.binding)
        overflow = request_fixture(request_id="overflow")
        overflow["producer_sequence"] = 4
        response = self.gateway.submit(overflow, self.binding)
        self.assertEqual(response["outcome"], "rejected_capacity")
        record = self.ledger.get(canonical_digest(["victoria", "den-adapter", "overflow"]))
        self.assertEqual(record.reservation, {})
        self.assertIsNone(record.engine_epoch)
        self.gateway.dispatch_once()
        self.assertEqual(self.gateway.submit(overflow, self.binding), response)
        self.assertEqual(self.gateway.submit({**overflow, "request_id": "fresh"}, self.binding)["status"], "queued")

    def test_expired_new_request_rejects_but_existing_matching_digest_ignores_new_policy(self):
        self.controller.enable("epoch-1")
        original = request_fixture()
        queued = self.gateway.submit(original, self.binding)
        data = manifest_fixture()
        data["max_output_tokens"] = 1
        self.controller.manifest = ResourceManifest.from_dict(data)
        self.controller.close_admission("changed policy")
        self.assertEqual(self.gateway.submit(original, self.binding), queued)
        self.now += 16
        expired = request_fixture(request_id="expired")
        self.assertEqual(self.gateway.submit(expired, self.binding)["outcome"], "rejected_expired")

    def test_ledger_failure_never_fabricates_rejection_proof(self):
        self.ledger.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(AdmissionClosed):
            self.gateway.submit(request_fixture(), self.binding)
        self.assertTrue(self.ledger.broken)
        self.assertFalse(self.backend.calls)

    def test_no_storage_manifest_cannot_create_receipt_but_existing_status_still_resolves(self):
        existing = self.gateway.submit(request_fixture(), self.binding)
        self.controller.manifest = None
        with self.assertRaises(AdmissionClosed):
            self.gateway.submit(request_fixture(request_id="unbudgeted"), self.binding)
        self.assertEqual(self.gateway.status("request-1", self.binding), existing)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_storage_floor_failure_does_not_recurse_into_unbudgeted_rejection(self):
        self.controller.enable("epoch-1")
        with patch("home_shared.admission.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(AdmissionClosed):
                self.gateway.submit(request_fixture(), self.binding)
            self.assertEqual(disk.call_count, 2)  # Admission guard, then receipt guard; no retry loop.
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
        self.assertFalse(self.backend.calls)

    def test_full_metadata_ledger_leaves_new_request_ambiguous_and_existing_proof_available(self):
        data = manifest_fixture()
        data["max_ledger_records"] = 1
        self.controller.manifest = ResourceManifest.from_dict(data)
        proof = self.gateway.submit(request_fixture(), self.binding)
        with self.assertRaises(AdmissionClosed):
            self.gateway.submit(request_fixture(request_id="overflow"), self.binding)
        self.assertEqual(self.gateway.status("request-1", self.binding), proof)
        self.assertEqual(self.gateway.submit(request_fixture(), self.binding), proof)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_accepted_operation_wins_over_late_rejection_attempt(self):
        self.controller.enable("epoch-1")
        original = self.controller.submit
        def accepted_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise AdmissionClosed("late failure after durable acceptance")
        with patch.object(self.controller, "submit", side_effect=accepted_then_error):
            response = self.gateway.submit(request_fixture(), self.binding)
        self.assertEqual(response["status"], "queued")
        self.assertNotIn("rejection", response)

    def test_timeout_requires_late_terminal_proof_before_reenable(self):
        self.controller.enable("epoch-1")
        entered = threading.Event()
        release = threading.Event()

        def delay(request):
            entered.set()
            release.wait(2)

        self.backend.on_execute = delay
        payload = request_fixture()
        self.gateway.submit(payload, self.binding)
        self.now = 1014.99
        response = self.gateway.dispatch_once()
        self.assertTrue(entered.is_set())
        self.assertEqual(response["status"], "indeterminate")
        with self.assertRaises(AdmissionClosed):
            self.controller.enable("epoch-1")
        release.set()
        for _ in range(100):
            if self.gateway.reconcile_late_completions():
                break
            time.sleep(0.005)
        self.assertEqual(self.gateway.status("request-1", self.binding)["status"], "terminal")
        self.assertNotIn("observation", self.gateway.status("request-1", self.binding))
        self.assertFalse(self.gateway.readiness()["ready"])
        self.controller.enable("epoch-1")
        self.assertTrue(self.gateway.readiness()["ready"])


class HTTPTests(unittest.TestCase):
    setUp = GatewayTests.setUp
    tearDown = GatewayTests.tearDown

    def test_loopback_readiness_auth_body_and_no_compatibility_claim(self):
        token = "test-only-long-device-token"
        digest = hashlib.sha256(token.encode()).hexdigest()
        server = GatewayHTTPServer(("127.0.0.1", 0), self.gateway, {digest: self.binding})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def call(method, path, body=None, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                result = response.status, json.loads(response.read())
                connection.close()
                return result

            self.assertEqual(call("GET", "/healthz")[0], 200)
            self.assertEqual(call("GET", "/readyz")[0], 503)
            self.assertEqual(call("POST", "/v1/chat/completions", "{}")[0], 503)
            self.assertEqual(call("POST", "/v1/inference", "{}")[0], 401)
            headers = {"Authorization": "Bearer " + token}
            self.assertEqual(call("POST", "/v1/inference", '{"version":1,"version":1}', headers)[0], 400)
            status, rejected = call("POST", "/v1/inference", json.dumps(request_fixture()), headers)
            self.assertEqual(status, 202)
            self.assertEqual(rejected["outcome"], "rejected_closed")
            self.controller.enable("epoch-1")
            self.assertEqual(call("POST", "/v1/inference", json.dumps(request_fixture()), headers)[0], 202)
            self.assertEqual(call("GET", "/v1/inference/request-1", headers=headers), (200, rejected))
            self.assertEqual(call("POST", "/v1/inference", json.dumps(request_fixture(request_id="fresh")), headers)[1]["status"], "queued")
            self.assertEqual(len(self.backend.calls), 0)  # HTTP itself is not a hidden dispatcher.
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_no_lan_binding(self):
        with self.assertRaises(AdmissionError):
            GatewayHTTPServer(("0.0.0.0", 0), self.gateway)


if __name__ == "__main__":
    unittest.main()
