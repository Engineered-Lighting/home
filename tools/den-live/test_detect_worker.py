"""detect_worker's row helpers, which need no detector venv: a refused row's reason, and when a row was ready."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect_worker  # noqa: E402


class RowError(ValueError):
    """den-observe's detections.RowError, by name: the worker tells it by its type name."""


class RowHelpers(unittest.TestCase):
    def test_a_refused_row_says_why_without_its_detection_id(self):
        self.assertEqual(detect_worker.row_reason(RowError("detection 17: box_xyxy must lie inside the frame")),
                         "detection <id>: box_xyxy must lie inside the frame")
        self.assertEqual(detect_worker.row_reason(RowError("too many detections in one frame")), "too many detections in one frame")
        self.assertLessEqual(len(detect_worker.row_reason(RowError("x" * 500))), 160)
        self.assertEqual(detect_worker.row_reason(RowError("detection rgb-812:3: box_xyxy must lie inside the frame")),
                         "detection <id>: box_xyxy must lie inside the frame", "a live id carries a colon of its own")

    def test_a_row_is_never_ready_before_its_capture(self):
        self.assertEqual(detect_worker.available(10.0, 0.2, 0.05), 10.25)
        self.assertEqual(detect_worker.available(10.0, -0.3, 0.05), 10.0, "a capture mapped ahead of the host's clock")


    def test_the_guards_are_said_once_a_minute_and_only_when_one_fired(self):
        now = [0.0]
        guards = detect_worker.Guards(clock=lambda: now[0])
        self.assertIsNone(guards.count(True, 0))
        now[0] = 30.0
        self.assertIsNone(guards.count(False, 5))
        now[0] = 61.0
        self.assertEqual(guards.count(True, 1), "den-detect: in the last 61 s, 2 rows raised to their capture time, 6 boxes dropped to fit the row")
        now[0] = 130.0
        self.assertIsNone(guards.count(False, 0), "a quiet minute says nothing")


if __name__ == "__main__":
    unittest.main()
