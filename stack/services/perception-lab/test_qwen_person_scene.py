"""Pure subject-protocol tests; no model or network."""
import json
import base64
import unittest

import qwen_person_scene as scene


SUBJECTS = [{"subject_slot": "person-1", "box": {"x": 10, "y": 20, "w": 100, "h": 200}},
            {"subject_slot": "person-2", "box": {"x": 300, "y": 25, "w": 90, "h": 180}}]


def facts(**changes):
    value = {key: "unclear" for key in scene.FIELDS}
    value["visible_basis"] = []
    value.update(changes)
    return value


def reply():
    return {"scene": {"people_visible": "multiple"}, "subjects": [
        {"subject_slot": "person-1", "facts": facts(activity_hypothesis="computer_work", typing_visible="yes")},
        {"subject_slot": "person-2", "facts": facts()}]}


class PersonSceneTests(unittest.TestCase):
    def parse(self, value, **options):
        return scene.parse_reply(json.dumps(value), finish_reason="stop", slots=["person-1", "person-2"], **options)

    def test_two_subjects_keep_independent_unknowns(self):
        parsed, reason = self.parse(reply())
        self.assertIsNone(reason)
        self.assertEqual(parsed, reply())
        self.assertEqual(parsed["subjects"][1]["facts"]["activity_hypothesis"], "unclear")

    def test_membership_is_exact_and_never_repaired(self):
        for slots in (["person-1"], ["person-1", "person-1"], ["person-1", "person-3"]):
            value = reply()
            value["subjects"] = [{"subject_slot": slot, "facts": facts()} for slot in slots]
            self.assertEqual(self.parse(value), (None, "subject_membership_mismatch"))
        value = reply()
        value["subjects"].reverse()
        self.assertEqual(self.parse(value)[0], reply())

    def test_malformed_json_keys_values_and_basis_are_refused(self):
        self.assertEqual(scene.parse_reply('{"scene":{},"scene":{}}', finish_reason="stop", slots=["person-1"])[1], "not_json")
        value = reply()
        value["subjects"][1]["facts"]["identity"] = "owner"
        self.assertEqual(self.parse(value)[1], "keys_mismatch")
        value = reply()
        value["subjects"][1]["facts"]["visible_basis"] = ["book_in_hands"] * 2
        self.assertEqual(self.parse(value)[1], "basis_invalid")
        self.assertEqual(scene.parse_reply(json.dumps(reply()), finish_reason="length", slots=["person-1", "person-2"])[1], "finish_not_stop")

    def test_context_has_only_bounded_boxes_and_no_continuity_or_prior(self):
        context = scene.model_context(scene.local_time_label(12, 4), SUBJECTS)
        self.assertEqual(set(context), {"placement", "local_time", "subjects"})
        self.assertEqual(context["subjects"], SUBJECTS)
        for change in ({"x": -1}, {"w": 0}, {"x": 600, "w": 100}, {"h": True}):
            subjects = json.loads(json.dumps(SUBJECTS))
            subjects[0]["box"].update(change)
            with self.assertRaises(ValueError):
                scene.model_context(scene.local_time_label(12, 4), subjects)
        with self.assertRaises(ValueError):
            scene.model_context(scene.local_time_label(12, 4), SUBJECTS + SUBJECTS[:1])

    def test_request_uses_existing_whole_frame_budget_and_new_version(self):
        body = scene.request_body(scene.model_context(scene.local_time_label(12, 4), SUBJECTS), [b"\xff\xd8x"] * 2, [10, 11])
        self.assertEqual(body["max_tokens"], 512)
        self.assertEqual(body["response_format"], scene.response_format())
        self.assertIn("latest frame only", body["messages"][0]["content"])
        self.assertNotIn("earlier_model_output", body["messages"][1]["content"][0]["text"])

    def test_png_originals_are_forwarded_with_exact_bytes_and_mime(self):
        raw = b"\x89PNG\r\n\x1a\noriginal-png-bytes"
        body = scene.request_body(scene.model_context(scene.local_time_label(12, 4), SUBJECTS), [raw] * 2, [10, 11])
        images = body["messages"][1]["content"][2::2]
        for image in images:
            url = image["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(url.split(",", 1)[1]), raw)


if __name__ == "__main__":
    unittest.main()
