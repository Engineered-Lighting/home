"""Versioned person protocol reaches the real pilot model seam using fakes."""
import io
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from home_shared import lab_live_person_scene as person
from home_shared import lab_live_pilot as pilot_live
from home_shared import lab_live_scene as old
from test_lab_live_pilot import PilotLiveFixture, config_value, completion
from test_gateway import binding_fixture

SERVICE = Path(__file__).resolve().parents[3] / "stack/services/perception-lab"
sys.path.insert(0, str(SERVICE))
import qwen_person_scene as scene
from test_qwen_person_scene import SUBJECTS, reply


class PersonPilotTests(PilotLiveFixture, unittest.TestCase):
    def setUp(self):
        self.build(protocol=person.RESULT_SCHEMA, policy_sha256=person.POLICY_SHA256, max_output_tokens=512,
                   max_input_tokens=12000)

    def tearDown(self):
        self.teardown()

    def person_envelope(self, image_format="JPEG"):
        from PIL import Image
        encoded = io.BytesIO()
        Image.new("RGB", (640, 360), (1, 2, 3)).save(encoded, format=image_format)
        body = scene.request_body(scene.model_context(scene.local_time_label(12, 4), SUBJECTS), [encoded.getvalue()] * 2, [100, 101])
        envelope = self.envelope()
        envelope.update(body=body, body_sha256=scene.digest(body), task=scene.TASK, prompt_version=scene.PROMPT_VERSION)
        envelope["profile"]["adapter_revision"] = scene.PROMPT_VERSION
        return envelope

    def test_producer_consumer_protocol_parity_and_exact_model_bytes(self):
        self.assertEqual(person.instruction(), scene.instruction())
        self.assertEqual(person.response_format(), scene.response_format())
        self.assertNotEqual(person.POLICY_SHA256, old.POLICY_SHA256)
        self.assertEqual(self.binding.consumer_id, person.CONSUMER_ID)
        envelope = self.person_envelope()
        self.model.result = completion(json.dumps(reply()))
        self.live.submit(envelope, self.binding)
        self.finish()
        self.assertEqual(self.model.calls[0][0], scene.canonical(envelope["body"]).encode())
        result = self.live.lookup(self.reference(envelope), self.binding)
        self.assertEqual(result["state"], "completed")
        parsed, reason = scene.parse_reply(result["response"]["choices"][0]["message"]["content"],
                                          finish_reason="stop", slots=["person-1", "person-2"])
        self.assertIsNone(reason)
        self.assertEqual(parsed, reply())

    def test_exact_png_originals_reach_actual_pilot_model_bytes(self):
        import base64
        envelope = self.person_envelope("PNG")
        originals = [base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                     for item in envelope["body"]["messages"][1]["content"][2::2]]
        self.live.submit(envelope, self.binding)
        self.finish()
        self.assertEqual(self.model.calls[0][0], scene.canonical(envelope["body"]).encode())
        sent = json.loads(self.model.calls[0][0])["messages"][1]["content"][2::2]
        self.assertTrue(all(item["image_url"]["url"].startswith("data:image/png;base64,") for item in sent))
        self.assertEqual([base64.b64decode(item["image_url"]["url"].split(",", 1)[1]) for item in sent], originals)

    def test_image_mime_shape_size_and_polyglots_refused_before_ledger(self):
        import base64
        from PIL import Image
        png = self.person_envelope("PNG")
        jpeg = self.person_envelope()
        png_raw = base64.b64decode(png["body"]["messages"][1]["content"][2]["image_url"]["url"].split(",", 1)[1])
        jpeg_raw = base64.b64decode(jpeg["body"]["messages"][1]["content"][2]["image_url"]["url"].split(",", 1)[1])
        samples = [("image/jpeg", png_raw), ("image/png", jpeg_raw), ("image/png", png_raw + b"PK\x03\x04zip"),
                   ("image/png", png_raw + png_raw), ("image/jpeg", jpeg_raw + png_raw + b"\xff\xd9"),
                   ("image/png", png_raw + b"x" * person.MAX_IMAGE_BYTES)]
        for mode, size in (("RGB", (639, 360)), ("RGBA", (640, 360)), ("P", (640, 360))):
            encoded = io.BytesIO()
            Image.new(mode, size).save(encoded, format="PNG")
            samples.append(("image/png", encoded.getvalue()))
        animated = io.BytesIO()
        Image.new("RGB", (640, 360)).save(animated, format="PNG", save_all=True,
                                         append_images=[Image.new("RGB", (640, 360), (10, 20, 30))])
        samples.append(("image/png", animated.getvalue()))
        for mime, raw in samples:
            envelope = self.person_envelope("PNG")
            envelope["body"]["messages"][1]["content"][2]["image_url"]["url"] = "data:" + mime + ";base64," + base64.b64encode(raw).decode()
            envelope["body_sha256"] = scene.digest(envelope["body"])
            with self.subTest(mime=mime, size=len(raw)):
                self.assertEqual(self.refused(envelope), (400, "invalid_request"))
        self.assertEqual(self.rows(), 0)

    def test_single_frame_rgb_and_l_png_jpeg_are_decoded_without_conversion(self):
        import base64
        from PIL import Image
        for mode in ("RGB", "L"):
            for image_format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
                encoded = io.BytesIO()
                Image.new(mode, (640, 360)).save(encoded, format=image_format)
                raw = encoded.getvalue()
                self.assertEqual(person._image("data:" + mime + ";base64," + base64.b64encode(raw).decode(), 262144), (640, 360))

    def test_old_protocol_and_injected_context_refused_before_ledger(self):
        self.assertEqual(self.refused(self.envelope()), (400, "prompt_version_unsupported"))
        for alteration in ("track_ref", "prior", "instance_id"):
            envelope = self.person_envelope()
            context = json.loads(envelope["body"]["messages"][1]["content"][0]["text"])
            context[alteration] = "foreign"
            envelope["body"]["messages"][1]["content"][0]["text"] = scene.canonical(context)
            envelope["body_sha256"] = scene.digest(envelope["body"])
            self.assertEqual(self.refused(envelope), (400, "invalid_request"))
        self.assertEqual(self.rows(), 0)

    def test_explicit_protocol_requires_new_policy_and_budget(self):
        for changes in ({"policy_sha256": old.POLICY_SHA256}, {"max_output_tokens": 192}, {"protocol": "dynamic.module"}):
            value = config_value(protocol=person.RESULT_SCHEMA, policy_sha256=person.POLICY_SHA256, max_output_tokens=512)
            value.update(changes)
            with self.assertRaises(ValueError):
                self.load(value, "bad.json")

    def test_new_ingress_selects_only_new_routes_without_opening_a_socket(self):
        with patch.object(pilot_live.PrivateTLSIngress, "__init__", return_value=None):
            ingress = pilot_live.PilotLiveIngress(("127.0.0.1", 0), self.pilot, self.live,
                                                 identities={"camera": self.pilot.binding, "person": self.binding})
        handler = object.__new__(ingress.RequestHandlerClass)
        handler._binding = lambda: self.binding
        self.assertEqual(handler._live_binding(), self.binding)
        self.assertIn(person.LivePersonSceneRoutes, ingress.RequestHandlerClass.__mro__)
        self.assertNotIn(old.LiveSceneRoutes, ingress.RequestHandlerClass.__mro__)
        handler._binding = binding_fixture
        self.assertIsNone(handler._live_binding())

    def test_new_consumer_keeps_shared_one_outstanding_and_engine_guards(self):
        import threading
        self.model.hold = threading.Event()
        envelope = self.person_envelope()
        self.live.submit(envelope, self.binding)
        other = self.person_envelope()
        other["request_id"] = "9" * 64
        self.assertEqual(self.refused(other), (429, "perceiver_busy"))
        self.assertEqual(self.rows(), 1)
        self.model.hold.set()
        self.finish()
        bad = self.person_envelope()
        bad["profile"]["model_version"] = "foreign-model"
        self.assertEqual(self.refused(bad), (400, "profile_mismatch"))
        self.assertEqual(self.rows(), 1)


if __name__ == "__main__":
    unittest.main()
