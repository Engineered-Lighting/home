"""den-observe tests: synthetic data only, no camera, no network, no numpy.

    python -B -m unittest discover -s tools/den-observe -v
"""
import json
import math
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import camgeom as g  # noqa: E402
import detections  # noqa: E402
import fake_detector  # noqa: E402
import lift  # noqa: E402
import synth  # noqa: E402
import takes  # noqa: E402

_REAL_CONNECT = socket.socket.connect
_CACHE = {}


def loopback_only(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if host != "127.0.0.1":
        raise AssertionError(f"tests may only connect to 127.0.0.1, not {host}")
    return _REAL_CONNECT(self, address, *args, **kwargs)


def setUpModule():
    global _guard, _tmp
    _guard = mock.patch.object(socket.socket, "connect", loopback_only)
    _guard.start()
    _tmp = Path(tempfile.mkdtemp(prefix="den-observe-test-"))


def tearDownModule():
    _guard.stop()
    shutil.rmtree(_tmp, ignore_errors=True)


def scene_depth(**kwargs):
    """Rendered depth for a Scene, cached per arguments (rendering takes ~0.4 s)."""
    key = json.dumps(kwargs, sort_keys=True)
    if key not in _CACHE:
        holes = kwargs.pop("holes", ())
        _CACHE[key] = synth.Scene(**kwargs).depth_mm(holes=[tuple(h) for h in holes])
    return _CACHE[key]


def rig():
    return lift.Rig(left_k=synth.LEFT_K, rgb_k=synth.RGB_K, rgb_distortion=synth.RGB_DISTORTION,
                    rgb_from_left=synth.RGB_FROM_LEFT, rgb_size=synth.RGB_SIZE, depth_size=synth.DEPTH_SIZE)


def new_dir(name):
    path = _tmp / name
    n = 1
    while path.exists():
        n += 1
        path = _tmp / f"{name}-{n}"
    return path


# ---- shapes of perception-lab's current outputs (Ultralytics + RTMPose), synthetic ----

def ultralytics_like(frame_id, boxes):
    """As ultralytics_worker.normalize_result: x, y, w, h, label, confidence, instance_id."""
    out = {"schema_version": 1, "frame_id": frame_id, "coordinate_space": "source_pixels",
           "objects": [], "boxes": [], "quality": {"image_width": 640, "image_height": 360}}
    for index, (x1, y1, x2, y2, label, score) in enumerate(boxes):
        instance = f"{frame_id}:{index}"
        out["objects"].append({"label": label, "confidence": score, "instance_id": instance})
        out["boxes"].append({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1, "label": label,
                             "confidence": score, "instance_id": instance})
    return out


def rtmpose_like(instance, crop, pixels, threshold=0.3):
    """As rtmpose_worker.normalize_pose: one coco17 instance with all 17 joint slots."""
    joints = []
    for index, (pixel, score) in enumerate(pixels):
        valid = pixel is not None and score is not None
        joints.append({"joint_id": index, "name": detections.COCO17[index], "pixel": list(pixel) if valid else None,
                       "score": score, "status": "supported" if valid and score >= threshold else "uncertain",
                       "reason": None if valid and score >= threshold else ("nonfinite" if not valid else "low_response")})
    return {"instance_id": instance, "crop_xyxy": crop, "layout": "coco17", "joints": joints,
            "score_semantics": "SimCC_response_not_probability"}


class DetectionRowTests(unittest.TestCase):
    def test_fake_detector_rows_follow_the_format(self):
        row = fake_detector.row(5120.25, rgb_seq=812)
        self.assertIs(detections.validate(row), row)
        self.assertEqual(len(row["detections"][0]["keypoints"]), 17)

    def test_rows_round_trip_through_jsonl_with_crlf(self):
        rows = [fake_detector.row(10.0 + i * 0.25, rgb_seq=i) for i in range(3)]
        path = new_dir("rows").with_suffix(".jsonl")
        detections.write_rows(path, rows)
        self.assertNotIn(b"\r", path.read_bytes())
        self.assertEqual(detections.read_rows(path), rows)
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))  # BOM and CRLF
        self.assertEqual(detections.read_rows(path), rows)

    def test_refuses_bad_rows(self):
        good = fake_detector.row(1.0, rgb_seq=1)
        cases = {
            "schema": dict(good, schema="den-detections-0"),
            "no capture time": dict(good, frame={k: v for k, v in good["frame"].items() if k != "capture_ts"}),
            "unknown clock": dict(good, frame=dict(good["frame"], clock="unix")),
            "box outside": dict(good, detections=[dict(good["detections"][0], box_xyxy=[0, 0, 700, 100])]),
            "16 keypoints": dict(good, detections=[dict(good["detections"][0],
                                                        keypoints=good["detections"][0]["keypoints"][:16])]),
            "score above 1": dict(good, detections=[dict(good["detections"][0], score=1.5)]),
            "nan keypoint": dict(good, detections=[dict(good["detections"][0],
                                                        keypoints=[[float("nan"), 1.0, 0.5]] * 17)]),
            "repeated id": dict(good, detections=[good["detections"][0]] * 2),
        }
        for name, row in cases.items():
            with self.subTest(name), self.assertRaises(detections.RowError):
                detections.validate(row)

    def test_rows_must_move_forward_in_time(self):
        path = new_dir("backwards").with_suffix(".jsonl")
        path.write_text("\n".join(json.dumps(fake_detector.row(t, rgb_seq=i)) for i, t in enumerate((2.0, 1.0))),
                        encoding="utf-8")
        with self.assertRaises(detections.RowError):
            detections.read_rows(path)


class AdapterTests(unittest.TestCase):
    def result(self):
        out = ultralytics_like("f9", [(200.0, 40.0, 380.0, 352.0, "person", 0.91),
                                      (500.0, 200.0, 560.0, 260.0, "cup", 0.55)])
        pixels = [((290.0 + i, 80.0 + 10 * i), 0.8) for i in range(17)]
        pixels[3] = (None, None)          # a joint RTMPose gave no finite pixel for
        pixels[4] = ((300.0, 90.0), 0.1)  # a low response keeps its pixel and raw score
        out["pose_instances"] = [rtmpose_like("f9:0", [200.0, 40.0, 380.0, 352.0], pixels)]
        return out

    def test_maps_boxes_and_joined_poses_without_running_a_model(self):
        row = detections.from_perception_lab(self.result(), capture_ts=5120.5, rgb_seq=77)
        self.assertEqual(row["frame"], {"capture_ts": 5120.5, "clock": "host_monotonic", "width": 640,
                                        "height": 360, "rgb_seq": 77})
        self.assertEqual(row["detector"], {"boxes": "yolo26s", "keypoint_layout": "coco17", "keypoints": "rtmpose-m"})
        person, cup = row["detections"]
        self.assertEqual((person["id"], person["class"], person["score"]), ("f9:0", "person", 0.91))
        self.assertEqual(person["box_xyxy"], [200.0, 40.0, 380.0, 352.0])
        self.assertEqual(person["keypoints"][0], [290.0, 80.0, 0.8])
        self.assertIsNone(person["keypoints"][3])
        self.assertEqual(person["keypoints"][4], [300.0, 90.0, 0.1])
        self.assertEqual((cup["class"], cup["keypoints"]), ("cup", None))

    def test_a_pose_joins_only_by_instance_id(self):
        result = self.result()
        result["pose_instances"][0]["instance_id"] = "f9:7"
        with self.assertRaises(detections.RowError):
            detections.from_perception_lab(result, capture_ts=1.0)

    def test_needs_a_capture_time(self):
        with self.assertRaises(detections.RowError):
            detections.from_perception_lab(self.result(), capture_ts=None)

    def test_boxes_alone_need_no_keypoint_model(self):
        result = self.result()
        del result["pose_instances"]
        row = detections.from_perception_lab(result, capture_ts=1.0)
        self.assertNotIn("keypoints", row["detector"])


class TakeReaderTests(unittest.TestCase):
    def test_reads_rgb_and_depth_times_and_depth(self):
        folder = synth.make_take(new_dir("reader"), [(5000.0, 4999.99), (5000.25, 5000.25)], scene_depth())
        take = takes.Take(folder)
        self.assertEqual(len(take), 2)
        meta, depth = take.frame(0)
        self.assertEqual((meta["t_host"], meta["t_depth"]), (5000.0, 4999.99))
        self.assertEqual((depth.width, depth.height), (640, 400))
        self.assertGreater(depth.at(320, 200), 0)
        self.assertEqual(depth.at(-1, 0), 0)

    def test_refuses_an_unfinished_take(self):
        folder = synth.make_take(new_dir("unfinished"), [(1.0, 1.0)], scene_depth())
        record = json.loads((folder / "take.json").read_text(encoding="utf-8"))
        record["cues"] = []
        (folder / "take.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(takes.TakeRefused):
            takes.Take(folder)

    def test_refuses_depth_that_is_not_rectified_left_millimetres(self):
        meta = synth.metadata("s", 1, 1.0, 1.0)
        meta["depth_alignment"] = "rgb"
        with self.assertRaises(takes.BundleError):
            takes.read_frame(synth.bundle(meta, scene_depth()))


class LiftTests(unittest.TestCase):
    def test_every_figure_joint_lifts_to_its_true_point(self):
        scene, depth = synth.Scene(), takes.Depth(640, 400, takes.depth_from_bytes(scene_depth()), 0.0, 1)
        for name in synth.JOINTS:
            with self.subTest(name):
                result = lift.lift_pixel(rig(), depth, *scene.joint_rgb(name))
                self.assertEqual(result["status"], "measured")
                self.assertLess(math.dist(result["xyz"], scene.joint_left(name)), 0.01)
                self.assertGreaterEqual(result["support"], lift.MIN_JOINT_SUPPORT)

    def test_a_joint_over_a_depth_hole_has_no_support(self):
        scene = synth.Scene()
        u, v = scene.joint_rgb("nose")
        depth = takes.Depth(640, 400, takes.depth_from_bytes(scene_depth(holes=[[250, 0, 400, 160]])), 0.0, 1)
        self.assertEqual(lift.lift_pixel(rig(), depth, u, v)["reason"], "no_depth_support")

    def test_front_surface_wins_over_the_wall_behind(self):
        scene = synth.Scene()
        depth = takes.Depth(640, 400, takes.depth_from_bytes(scene_depth()), 0.0, 1)
        # The panel's top edge: the patch straddles the panel (3 m) and the wall (5 m).
        top = scene.left_from_room
        u, v = g.project(g.apply(*synth.RGB_FROM_LEFT, g.apply(*top, [0.0, synth.PANEL_Y, synth.PANEL_TOP])),
                         g.intrinsics(synth.RGB_K), g.distortion(synth.RGB_DISTORTION))
        result = lift.lift_pixel(rig(), depth, u, v + 1.0)
        self.assertEqual(result["status"], "measured")
        self.assertLess(result["xyz"][2], 3.6)

    def test_the_torso_region_lifts_to_the_panel(self):
        scene = synth.Scene()
        depth = takes.Depth(640, 400, takes.depth_from_bytes(scene_depth()), 0.0, 1)
        joints = [{"name": n, "status": "supported", "pixel": list(scene.joint_rgb(n))} for n in synth.JOINTS]
        kind, polygon = lift.person_region([0, 0, 640, 360], joints)
        self.assertEqual(kind, "torso")
        result = lift.lift_region(rig(), depth, polygon)
        self.assertEqual(result["status"], "measured")
        room = g.apply(*scene.room_from_left, result["xyz"])
        self.assertAlmostEqual(room[1], synth.PANEL_Y, delta=0.01)
        self.assertTrue(0.95 <= room[2] <= 1.45)


if __name__ == "__main__":
    unittest.main()
