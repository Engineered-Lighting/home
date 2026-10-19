"""vjepa_den_encode without torch or a GPU: hands box, grid pooling, and a full
cache written through a fake adapter that the Victoria evaluator can read."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for entry in (HERE, ROOT / "stack/services/perception-lab"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import numpy as np  # noqa: E402

import vjepa_den_encode as enc  # noqa: E402
import vjepa_pins as pins  # noqa: E402
import vjepa_tune_eval as tune_eval  # noqa: E402


def jpeg(colour):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", pins.IMAGE_SIZE, colour).save(out, format="JPEG")
    return out.getvalue()


ARM = {"left_wrist": [300, 250], "left_elbow": [280, 200], "right_wrist": [340, 252], "right_elbow": [360, 205]}


class FakeAdapter:
    def __init__(self):
        self.boxes = []

    def encode(self, frames, box):
        self.boxes.append(box)
        assert len(frames) == 8 and frames[0].shape == (360, 640, 3)
        rng = np.random.default_rng(len(self.boxes))
        return rng.normal(0, 1, (2304, 768)).astype(np.float32)

    def heads(self, raw, single_person):
        return {"posture": {p: 1 / 6 for p in pins.POSTURES}, "reading": 0.3, "activity": {},
                "zero_shot": {"reading": .2, "computer_work": .1, "phone_use": .1} if single_person else None}


class HandsBoxTests(unittest.TestCase):
    def test_square_box_around_the_forearms_inside_the_frame(self):
        box = enc.hands_box([ARM] * 8, scale=2.2, min_side=96)
        side = box[2] - box[0]
        self.assertAlmostEqual(side, box[3] - box[1])
        self.assertAlmostEqual(side, 2.2 * 80)
        self.assertTrue(box[0] <= 280 and box[2] >= 360 and box[1] <= 200 and box[3] >= 252)
        edge = enc.hands_box([{"left_wrist": [630, 350], "left_elbow": [600, 340]}] * 8, scale=2.2, min_side=96)
        self.assertTrue(0 <= edge[0] and edge[2] <= 640 and edge[3] <= 360)

    def test_too_few_frames_with_wrist_and_elbow_is_none(self):
        self.assertIsNone(enc.hands_box([ARM] * 3 + [{"left_wrist": [1, 1]}] * 5, scale=2.2, min_side=96))


class PoolTests(unittest.TestCase):
    def test_block_average_keeps_time_and_space_order(self):
        raw = np.arange(4 * 24 * 24, dtype=np.float32).reshape(-1, 1).repeat(2, axis=1)
        pooled = enc.pool_grid(raw, [4, 24, 24], [4, 12, 12])
        self.assertEqual(pooled.shape, (576, 2))
        grid = raw[:, 0].reshape(4, 24, 24)
        self.assertAlmostEqual(pooled[0, 0], grid[0, :2, :2].mean())
        self.assertAlmostEqual(pooled[-1, 0], grid[3, 22:, 22:].mean())
        with self.assertRaises(ValueError):
            enc.pool_grid(raw, [4, 24, 24], [4, 5, 5])


class EncodeTests(unittest.TestCase):
    def test_cache_rows_crops_skips_and_reader_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir, out = Path(directory) / "clips", Path(directory) / "cache"
            (clips_dir / "frames").mkdir(parents=True)
            out.mkdir()
            frames = []
            for i in range(8):
                (clips_dir / "frames" / ("m%d.jpg" % i)).write_bytes(jpeg((10 * i, 20, 30)))
                frames.append({"media_id": "m%d" % i, "observed_at": 100 + i * .5})
            clips = [{"clip_id": "c1", "session_id": "s" * 32, "day": "2026-10-01", "anchor_at": 103.5,
                      "anchor_rel_s": 3.5, "span_variant": "3.5s_8f", "box": [200, 50, 420, 350], "single_person": True,
                      "frames": frames, "joints": [ARM] * 8, "label": {"gold": "reading"}},
                     {"clip_id": "c2", "session_id": "s" * 32, "day": "2026-10-01", "anchor_at": 105.5,
                      "anchor_rel_s": 5.5, "span_variant": "3.5s_8f", "box": [200, 50, 420, 350], "single_person": False,
                      "frames": frames, "joints": [{}] * 8, "label": {}}]
            adapter = FakeAdapter()
            written, skipped = enc.encode(adapter, clips_dir, clips, out, dict(enc.DEFAULTS), max_clips=10,
                                          max_runtime_s=60, log=lambda *_: None)
            self.assertEqual((written, skipped), (3, {"hands_unavailable": 1}))
            self.assertEqual(adapter.boxes[0], [200, 50, 420, 350])
            header, rows, tokens = tune_eval.load_cache(out)
            self.assertEqual(tokens.shape, (3, 576, 768))
            self.assertEqual(tokens.dtype, np.float16)
            self.assertEqual([(r["clip_id"], r["crop"]) for r in rows], [("c1", "union"), ("c1", "hands"), ("c2", "union")])
            self.assertEqual(rows[0]["label"], {"gold": "reading"})
            self.assertIsNone(rows[2]["baseline"]["zero_shot"])
            self.assertEqual(header["artifact_sha256"], pins.ARTIFACT_SHA256)
            del tokens  # release the memmap before the directory is removed (Windows)

    def test_runtime_limit_stops_encoding_and_shrinks_the_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            clips_dir, out = Path(directory) / "clips", Path(directory) / "cache"
            (clips_dir / "frames").mkdir(parents=True)
            out.mkdir()
            frames = []
            for i in range(8):
                (clips_dir / "frames" / ("m%d.jpg" % i)).write_bytes(jpeg((5, 5, 5)))
                frames.append({"media_id": "m%d" % i, "observed_at": i * .5})
            clip = {"clip_id": "c", "session_id": "s" * 32, "anchor_at": 3.5, "span_variant": "3.5s_8f",
                    "box": [0, 0, 100, 100], "single_person": True, "frames": frames}
            ticks = iter([0, 0, 999])  # start, first clip, second clip
            written, skipped = enc.encode(FakeAdapter(), clips_dir, [clip, dict(clip, clip_id="d")], out,
                                          dict(enc.DEFAULTS, crops=["union"]), max_clips=10, max_runtime_s=10,
                                          clock=lambda: next(ticks), log=lambda *_: None)
            self.assertEqual((written, skipped), (1, {"runtime_limit": 1}))
            tokens = tune_eval.load_cache(out)[2]
            self.assertEqual(tokens.shape[0], 1)
            del tokens


if __name__ == "__main__":
    unittest.main()
