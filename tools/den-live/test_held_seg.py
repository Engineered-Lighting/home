"""The held-object pass's own rules: which instances it keeps, and when its weights load. A fake Ultralytics result."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

import held_seg

NAMES = {0: "person", 57: "couch", 67: "cell phone", 73: "book"}


def keypoints(wrist=(300.0, 200.0), score=0.9):
    points = [[100.0, 100.0, 0.9]] * 17
    points[9] = [wrist[0], wrist[1], score]
    points[10] = [wrist[0] + 40, wrist[1], score]
    return points


def row(**kwargs):
    return {"detections": [{"class": "person", "score": 0.9, "box_xyxy": [0, 0, 1, 1], "keypoints": keypoints(**kwargs)},
                           {"class": "person", "score": 0.4, "box_xyxy": [0, 0, 1, 1], "keypoints": None}]}


def square(x, y, size=40):
    return np.array([[x, y], [x + size, y], [x + size, y + size], [x, y + size]], np.float32)


def result(*instances):
    """instances: (class id, score, polygon)."""
    tensor = lambda values: SimpleNamespace(tolist=lambda: list(values))
    return SimpleNamespace(names=NAMES, boxes=SimpleNamespace(cls=tensor(i[0] for i in instances),
                                                              conf=tensor(i[1] for i in instances)),
                           masks=SimpleNamespace(xy=[i[2] for i in instances]))


class Instances(unittest.TestCase):
    def test_keeps_a_holdable_thing_at_a_wrist_and_nothing_else(self):
        got = held_seg.instances(result((73, 0.9, square(290, 190)),        # a book in the hand
                                        (67, 0.9, square(10, 10)),           # a phone across the room
                                        (57, 0.9, square(280, 180)),         # the couch under the hand
                                        (73, 0.2, square(300, 200)),         # a book the model barely saw
                                        (0, 0.9, square(290, 190))), row(), cv2)
        self.assertEqual([(i["label"], i["score"]) for i in got], [("book", 0.9)])
        self.assertEqual(len(got[0]["polygon"]), 4)

    def test_no_scored_wrist_or_no_masks_keeps_nothing(self):
        self.assertEqual(held_seg.instances(result((73, 0.9, square(290, 190))), row(score=0.1), cv2), [])
        self.assertEqual(held_seg.instances(SimpleNamespace(names=NAMES, boxes=None, masks=None), row(), cv2), [])
        self.assertEqual(held_seg.instances(result((73, 0.9, square(290, 190))), {"detections": []}, cv2), [])

    def test_a_long_outline_keeps_at_most_max_polygon_vertices(self):
        angles = np.linspace(0, 2 * np.pi, 1000, endpoint=False)
        circle = np.stack([300 + 30 * np.cos(angles), 200 + 30 * np.sin(angles)], axis=1).astype(np.float32)
        got = held_seg.instances(result((73, 0.8, circle)), row(), cv2)
        self.assertEqual(len(got[0]["polygon"]), held_seg.MAX_POLYGON)


class Loading(unittest.TestCase):
    def test_off_until_pinned_present_and_matching(self):
        with tempfile.TemporaryDirectory() as folder:
            models = Path(folder)
            self.assertEqual(held_seg.load(models, "cpu", pinned=None), (None, "no held-object weights are pinned"))
            self.assertIsNone(held_seg.load(models, "cpu", pinned="0" * 64)[0])
            weights = models / held_seg.HELD_WEIGHTS
            weights.write_bytes(b"fictional weights")
            self.assertEqual(held_seg.load(models, "cpu", pinned="0" * 64)[1], "the held-object weights aren't the pinned ones")
            pinned = hashlib.sha256(weights.read_bytes()).hexdigest()
            loaded = []
            segmenter, why = held_seg.load(models, "cuda:0", pinned=pinned,
                                           yolo=lambda path, task: loaded.append((Path(path).name, task)) or "model")
            self.assertIsNone(why)
            self.assertEqual(loaded, [(held_seg.HELD_WEIGHTS, "segment")])
            self.assertEqual((segmenter.model, segmenter.device), ("model", "cuda:0"))

    def test_the_pin_is_the_published_weights(self):
        self.assertEqual(held_seg.HELD_SHA256, "3da1d83e31caec96f9300eb4064f4f62882c133c7c264d63dfe61a7c197837a4")
        local = Path(sys.prefix) / "models" / held_seg.HELD_WEIGHTS   # the detector venv's, where it was downloaded
        if local.is_file():
            self.assertEqual(held_seg.sha256(local), held_seg.HELD_SHA256)

    def test_a_segmenter_runs_one_pass_and_keeps_its_instances(self):
        calls = []
        model = SimpleNamespace(predict=lambda image, **kw: calls.append(kw) or [result((73, 0.9, square(290, 190)))])
        got = held_seg.Segmenter(model, "cuda:0")(np.zeros((360, 640, 3), np.uint8), row(), cv2)
        self.assertEqual([i["label"] for i in got], ["book"])
        self.assertEqual(calls, [{"verbose": False, "device": "cuda:0", "conf": held_seg.SCORE_MIN}])


class ReadyLine(unittest.TestCase):
    """detect_worker's ready line says whether the held-object pass is on, and why not."""

    def test_on_with_the_pinned_file_off_without(self):
        import detect_worker
        from functools import partial
        with tempfile.TemporaryDirectory() as folder:
            models = Path(folder)
            fake = lambda path, task: "model"
            segmenter, fields = detect_worker.held_pass(models, {"boxes": "cuda:0"}, load=partial(held_seg.load, yolo=fake))
            self.assertIsNone(segmenter)
            self.assertEqual(fields, {"held": False, "held_why": "the held-object weights aren't in the models folder"})
            (models / held_seg.HELD_WEIGHTS).write_bytes(b"fictional weights")
            pinned = hashlib.sha256(b"fictional weights").hexdigest()
            segmenter, fields = detect_worker.held_pass(models, {"boxes": "cuda:0"},
                                                        load=partial(held_seg.load, pinned=pinned, yolo=fake))
            self.assertEqual((fields, segmenter.device), ({"held": True}, "cuda:0"))
            _, fields = detect_worker.held_pass(models, None, load=partial(held_seg.load, yolo=fake))
            self.assertEqual(fields, {"held": False, "held_why": "the held-object weights aren't the pinned ones"})
            _, fields = detect_worker.held_pass(models, None, load=partial(held_seg.load, pinned=None, yolo=fake))
            self.assertEqual(fields, {"held": False, "held_why": "no held-object weights are pinned"})

    def test_a_load_that_raises_is_off_with_its_type_name_only(self):
        import detect_worker

        def broken(models, device):
            raise RuntimeError("text that never reaches the line")
        segmenter, fields = detect_worker.held_pass(Path("."), None, load=broken)
        self.assertIsNone(segmenter)
        self.assertEqual(fields, {"held": False, "held_why": "the held-object pass didn't load (RuntimeError)"})


if __name__ == "__main__":
    unittest.main()
