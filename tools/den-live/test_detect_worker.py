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

    def test_a_row_is_never_ready_before_its_capture(self):
        self.assertEqual(detect_worker.available(10.0, 0.2, 0.05), 10.25)
        self.assertEqual(detect_worker.available(10.0, -0.3, 0.05), 10.0, "a capture mapped ahead of the host's clock")


if __name__ == "__main__":
    unittest.main()
