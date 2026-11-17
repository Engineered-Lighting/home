"""The optional pinned adapter's bounds and class, wrist and polygon validation."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

import held_seg as H


def square(x=280, y=180, size=40):
    return np.array([[x, y], [x + size, y], [x + size, y + size], [x, y + size]], np.float32)


def result(*items):
    def tensor(values):
        return SimpleNamespace(tolist=lambda: list(values))
    return SimpleNamespace(names={0: "person", 57: "couch", 67: "cell phone", 73: "book"},
                           boxes=SimpleNamespace(cls=tensor(i[0] for i in items), conf=tensor(i[1] for i in items)),
                           masks=SimpleNamespace(xy=[i[2] for i in items]))


def row(wrist=(300, 200, 0.9)):
    points = [None] * 17
    points[9] = list(wrist)
    return {"detections": [{"class": "person", "keypoints": points}]}


class Masks(unittest.TestCase):
    def test_person_masks_and_only_holdable_near_a_supported_wrist(self):
        masks = H.instances(result((0, .8, square()), (73, .9, square()), (57, .9, square()),
                                   (73, .9, square(20, 20)), (67, .2, square())), row(), cv2)
        self.assertEqual(len(masks["person_masks"]), 1)
        self.assertEqual(masks["person_masks"][0]["box_xyxy"], [280, 180, 320, 220])
        self.assertEqual([m["label"] for m in masks["held_masks"]], ["book"])

    def test_person_needs_no_wrist_but_held_object_does(self):
        masks = H.instances(result((0, .8, square()), (73, .9, square())), row((300, 200, .1)), cv2)
        self.assertEqual(len(masks["person_masks"]), 1)
        self.assertEqual(masks["held_masks"], [])

    def test_nonfinite_bad_and_oversized_outputs_are_bounded(self):
        angles = np.linspace(0, 2 * np.pi, 1000, endpoint=False)
        circle = np.stack([300 + 30 * np.cos(angles), 200 + 30 * np.sin(angles)], axis=1)
        items = [(73, .9, circle)] * 20 + [(0, .8, square())] * 20
        items += [(0, float("nan"), square()), (73, .9, square() * float("nan"))]
        masks = H.instances(result(*items), row(), cv2)
        self.assertEqual(len(masks["held_masks"]), H.MAX_HELD_MASKS)
        self.assertEqual(len(masks["person_masks"]), H.MAX_PERSON_MASKS)
        self.assertTrue(all(len(m["polygon"]) == H.MAX_POLYGON for m in masks["held_masks"]))
        for wrist in ((float("nan"), 20, .9), (300, 200, 1.1), (-1, 200, .9)):
            self.assertEqual(H.instances(result((73, .9, square())), row(wrist), cv2)["held_masks"], [])

    def test_missing_or_wrong_weights_never_import_or_start_model(self):
        import detect_worker
        with tempfile.TemporaryDirectory() as folder, patch("held_async.Masks") as start:
            masks, fields = detect_worker.optional_masks(folder, folder)
            self.assertIsNone(masks)
            self.assertFalse(fields["held"])
            start.assert_not_called()
            path = Path(folder) / H.HELD_WEIGHTS
            path.write_bytes(b"synthetic weights")
            loaded = []
            factory = lambda path, task: loaded.append((Path(path).name, task)) or SimpleNamespace(
                predict=lambda image, **kwargs: [result()])
            self.assertIsNone(H.load(folder, yolo=factory)[0])
            pin = hashlib.sha256(path.read_bytes()).hexdigest()
            model, why = H.load(folder, pinned=pin, yolo=factory)
            self.assertIsNone(why)
            self.assertEqual((model.device, loaded), ("cpu", [(H.HELD_WEIGHTS, "segment")]))
        self.assertEqual(H.HELD_SHA256, "3da1d83e31caec96f9300eb4064f4f62882c133c7c264d63dfe61a7c197837a4")

    def test_live_predict_arguments_and_first_run_failure_are_preserved(self):
        calls = []
        model = SimpleNamespace(predict=lambda image, **kwargs: calls.append((image.shape, kwargs)) or [result()])
        segmenter = H.Segmenter(model, "cpu", state=".")
        segmenter(np.zeros((360, 640, 3), np.uint8), row(), cv2)
        self.assertEqual(calls, [((360, 640, 3), {"verbose": False, "device": "cpu", "conf": H.SCORE_MIN})])

        def broken(image, **kwargs):
            raise PermissionError("private source path")

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / H.HELD_WEIGHTS
            path.write_bytes(b"synthetic weights")
            pin = hashlib.sha256(path.read_bytes()).hexdigest()
            model, why = H.load(folder, pinned=pin, yolo=lambda *a, **k: SimpleNamespace(predict=broken), state=folder)
            self.assertIsNone(model)
            self.assertEqual(why, "the held-object pass failed its first run (PermissionError)")

    def test_live_failure_log_keeps_type_counts_and_minute_cadence(self):
        now = [0.0]
        failures = H.Failures(clock=lambda: now[0])
        for _ in range(5):
            now[0] += 2
            self.assertIsNone(failures.count("PermissionError"))
        now[0] = 70
        self.assertEqual(failures.count("PermissionError"),
                         "den-live: in the last 70 s the held-object pass failed 6 times (PermissionError 6); "
                         "those frames held nothing")
        now[0] = 131
        self.assertIsNone(failures.count())


if __name__ == "__main__":
    unittest.main()
