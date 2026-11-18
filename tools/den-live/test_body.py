"""Synthetic measured-body source and retained-bundle pipe fixtures."""
import base64
import io
import json
import struct
import subprocess
import sys
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import body as B  # noqa: E402

FX = FY = 400.0
CX, CY = 320.0, 200.0
R = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
T = [0.0, 0.0, 1.4]
BINDING = {"profile": "synthetic-depth", "transform_id": "fixture-transform", "content_revision": "fixture-scan"}
SETUP = {"kind": "setup_body", "setup": {"left_k": [FX, FY, CX, CY], "room_from_left": [R, T], "binding": BINDING}}


def world(cols, rows, depth_mm):
    depth = np.asarray(depth_mm) / 1000
    return np.stack(((cols - CX) * depth / FX, depth, T[2] - (rows - CY) * depth / FY), axis=-1)


def scene(pose="seated", offset=0):
    """Known depth rays, independent head labels, and a sofa surface behind the person."""
    bg = np.full((B.HEIGHT, B.WIDTH), 4000, dtype=np.uint16)
    bg[165:305, 245:410] = 3100
    depth, head = bg.copy(), np.zeros(bg.shape, dtype=bool)
    r, c = (154, 287 + offset) if pose == "reclined" else (125, 292 + offset)
    bottom = 340 if pose == "standing" else 240
    depth[r:bottom, c:c + 64] = 2000
    depth[185:211, c + 58:c + 90] = 2030  # a visible arm
    if pose == "seated":
        depth[227:285, c + 35:c + 70] = 1995  # a visible leg

    if pose == "reclined":  # head folded sideways, level with the torso rather than above it
        head[147:180, c - 23:c + 3] = True
        bounds = [c - 23, 147, c + 2, 179]
    else:
        head[r - 35:r + 3, c + 15:c + 45] = True
        bounds = [c + 15, r - 35, c + 44, r + 2]
    depth[head] = 1975
    joints = {"left_shoulder": [world(c + 9, r + 27, 2000).tolist(), "supported"],
              "right_shoulder": [world(c + 50, r + 27, 2000).tolist(), "supported"],
              "left_hip": [world(c + 13, 213, 2000).tolist(), "supported"],
              "right_hip": [world(c + 50, 213, 2000).tolist(), "supported"],
              "nose": [world(c + 25, r - 3, 1975).tolist(), "supported"]}
    return bg, depth, head, bounds, joints


def subject(fixture, frame_key="fixture-7"):
    _, _, _, bounds, joints = fixture
    return {"track_id": "fixture-track", "joints": joints, "facing_deg": 0,
            "head_evidence": {"frame_key": frame_key, "binding": BINDING, "bounds_px": bounds,
                              "alignment_px": 2, "localization_px": 2, "motion_px": 1}}


def pixel(xyz):
    camera = (np.asarray(xyz, float) - T) @ np.asarray(R, float)
    return np.array([FX * camera[0] / camera[2] + CX, FY * camera[1] / camera[2] + CY])


def cut_subject(fixture, margin=0.05, frame_key="fixture-7", ceiling=3.0):
    """Evidence with the shoulder-line cut only: no depth-pixel envelope."""
    _, _, _, _, joints = fixture
    shoulders = [pixel(joints["left_shoulder"][0]), pixel(joints["right_shoulder"][0])]
    sh = (shoulders[0] + shoulders[1]) / 2
    hip = (pixel(joints["left_hip"][0]) + pixel(joints["right_hip"][0])) / 2
    axis = (sh - hip) / np.linalg.norm(sh - hip)
    return {"track_id": "fixture-track", "joints": joints, "facing_deg": 0,
            "head_evidence": {"frame_key": frame_key, "binding": BINDING,
                              "cut": {"shoulder_px": sh.tolist(), "axis_px": axis.tolist(), "margin_m": margin,
                                      "shoulders_px": [p.tolist() for p in shoulders], "ceiling_m": ceiling}}}


def cut_px(fixture, person, frame_key="fixture-7"):
    return B.cut_pixels(B.torso_cut(person["head_evidence"], frame_key=frame_key, binding=BINDING), fixture[1], FX)


def decode(frame):
    p = frame["points"]
    return {key: np.frombuffer(base64.b64decode(p[key]), dtype).reshape(-1, stride)
            for key, dtype, stride in (("key", "<u4", 1), ("position_mm", "<i2", 3),
                                        ("normal", "i1", 3), ("kind", "u1", 1))}


def bundle(depth):
    with io.BytesIO() as output, io.BytesIO() as array:
        np.save(array, depth)
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("depth_mm.npy", array.getvalue())
        return output.getvalue()


def body_header(key, request=1, **changes):
    return {"kind": "body", "id": 1, "request_id": request, "binding": BINDING,
            "frame_key": key, "now": 0, "observation": "empty", **changes}


class BodyTest(unittest.TestCase):
    def maker(self):
        return B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T))

    def learned(self, fixture):
        maker = self.maker()
        maker.step(fixture[0], observation="empty", now=0)
        for i in range(B.LEARN_FRAMES):
            reason = "empty" if i == B.LEARN_FRAMES - 1 else "warming_background"
            self.assertEqual(maker.step(fixture[0], observation="empty", now=B.EMPTY_S + i * 0.2), (None, reason))
        self.assertIsNotNone(maker.background)
        return maker

    def render(self, maker, fixture, person=None, key="fixture-7"):
        if person is None:
            person = subject(fixture, frame_key=key)
        return maker.step(fixture[1], observation="person", person=person, now=20, frame_key=key, binding=BINDING)

    def test_each_standing_seated_reclined_and_moved_sample_is_the_current_measured_ray(self):
        for pose, offset, mode in (("standing", 0, "rgb"), ("seated", 0, "infrared"),
                                   ("reclined", 0, "infrared"), ("standing", 17, "rgb")):
            with self.subTest(pose=pose, offset=offset, mode=mode):
                fixture = scene(pose, offset)
                maker = self.learned(fixture)
                key = [mode, 10 + offset]
                frame, why = self.render(maker, fixture, key=key)
                self.assertIsNone(why)
                self.assertEqual(set(frame), {"track_id", "points", "facing_deg", "shoulder_midpoint_m", "pelvis_z_m"})
                self.assertEqual(set(frame["points"]), {"count", "key", "position_mm", "normal", "kind"})
                actual = decode(frame)
                keys = actual["key"].ravel()
                self.assertGreater(len(keys), 300)
                self.assertEqual(frame["points"]["count"], len(keys))
                self.assertTrue(np.all(np.diff(keys.astype(np.int64)) > 0))
                self.assertTrue(np.all(keys < B.N))
                self.assertTrue(np.all(actual["kind"] == 0))
                rows, cols = np.divmod(keys, B.WIDTH)
                self.assertFalse(fixture[2][rows, cols].any(), "independently labelled head rays are absent")
                expected = np.rint(world(cols, rows, fixture[1][rows, cols]) * 1000)
                np.testing.assert_array_equal(actual["position_mm"], expected)

    def test_the_figure_keeps_over_a_quarter_of_the_persons_pixels_at_desk_distance(self):
        """2 m from the camera, as at the desk: depth pixels are 5 mm apart here. 13 mm kept about one in six and the
        figure read as wisps; 8 mm keeps more than a quarter of them, every one a real pixel."""
        fixture = scene("standing")
        maker = self.learned(fixture)
        frame, why = self.render(maker, fixture)
        self.assertIsNone(why)
        body_pixels = int(((fixture[1] < fixture[0]) & ~fixture[2]).sum())
        self.assertGreater(frame["points"]["count"], 0.25 * body_pixels)

    def test_head_depth_cannot_affect_points_or_normals(self):
        fixture = scene("reclined")
        depth = fixture[1].copy()
        head = fixture[2]
        depth[head] = 1500
        variant = fixture[0], depth, head, *fixture[3:]
        maker = self.learned(fixture)
        self.assertEqual(self.render(maker, fixture), self.render(maker, variant))

    def test_expands_private_envelope_for_motion_and_alignment(self):
        fixture = scene()
        bg, depth, head, bounds, _ = fixture
        bounds = list(bounds)
        # Private predicted extent is three pixels behind the actual moving head.
        bounds[0] += 3
        bounds[2] += 3
        person = subject(fixture)
        person["head_evidence"]["bounds_px"] = bounds
        maker = self.learned(fixture)
        frame, why = self.render(maker, fixture, person)
        self.assertIsNone(why)
        keys = decode(frame)["key"].ravel()
        self.assertFalse(head.ravel()[keys].any())
        left, top, right, bottom = B.head_exclusion(person["head_evidence"], frame_key="fixture-7", binding=BINDING)
        self.assertLessEqual(left, bounds[0] - 5)
        self.assertGreaterEqual(right, bounds[2] + 5)
        self.assertLess(top, bottom)

    def test_the_shoulder_line_cut_alone_removes_everything_above_it(self):
        for pose in ("standing", "seated", "reclined"):
            with self.subTest(pose=pose):
                fixture = scene(pose)
                maker = self.learned(fixture)
                person = cut_subject(fixture)
                frame, why = self.render(maker, fixture, person)
                self.assertIsNone(why)
                actual = decode(frame)
                rows, cols = np.divmod(actual["key"].ravel(), B.WIDTH)
                self.assertGreater(len(rows), 300)
                self.assertFalse(fixture[2][rows, cols].any(), "independently labelled head rays are absent")
                self.assertFalse(B.cut_side(cols, rows, cut_px(fixture, person)).any(),
                                 "no point on the head's side or within the margin of the line")
                self.assertTrue(np.all(actual["kind"] == 0))

    def test_shoulders_the_cut_removes_still_give_heading_and_midpoint_never_points(self):
        fixture = scene()
        maker = self.learned(fixture)
        frame, why = self.render(maker, fixture, cut_subject(fixture, margin=0.06))
        self.assertIsNone(why)
        self.assertEqual(frame["facing_deg"], 0)
        joints = fixture[4]
        expected = (np.asarray(joints["left_shoulder"][0]) + np.asarray(joints["right_shoulder"][0])) / 2
        np.testing.assert_allclose(frame["shoulder_midpoint_m"], expected, atol=1e-9)
        self.assertIsNotNone(frame["pelvis_z_m"])
        person = cut_subject(fixture, margin=0.06)
        person["joints"]["left_hip"][1] = "uncertain"
        frame, why = self.render(maker, fixture, person)
        self.assertIsNone(frame["facing_deg"] if frame else None, why)

    def test_cut_and_envelope_together_remove_the_union(self):
        fixture = scene("reclined")
        maker = self.learned(fixture)
        person = subject(fixture)
        person["head_evidence"]["cut"] = cut_subject(fixture)["head_evidence"]["cut"]
        both, why = self.render(maker, fixture, person)
        self.assertIsNone(why)
        alone, _ = self.render(maker, fixture, cut_subject(fixture))
        self.assertLessEqual(both["points"]["count"], alone["points"]["count"])
        rows, cols = np.divmod(decode(both)["key"].ravel(), B.WIDTH)
        self.assertFalse(fixture[2][rows, cols].any())

    def test_a_malformed_or_unbound_cut_clears(self):
        fixture = scene()
        maker = self.learned(fixture)
        for label, change in {
            "axis not unit": lambda cut: cut.update(axis_px=[0, 2]),
            "no margin": lambda cut: cut.update(margin_m=0),
            "huge margin": lambda cut: cut.update(margin_m=5),
            "nan shoulder": lambda cut: cut.update(shoulder_px=[float("nan"), 100]),
            "far shoulder": lambda cut: cut.update(shoulder_px=[5000, 100]),
            "short shoulder": lambda cut: cut.update(shoulder_px=[100]),
            "no shoulder band": lambda cut: cut.pop("shoulders_px"),
            "one shoulder in the band": lambda cut: cut.update(shoulders_px=[[100, 100]]),
            "no ceiling": lambda cut: cut.pop("ceiling_m"),
            "nan ceiling": lambda cut: cut.update(ceiling_m=float("nan")),
        }.items():
            with self.subTest(label=label):
                person = cut_subject(fixture)
                change(person["head_evidence"]["cut"])
                self.assertEqual(self.render(maker, fixture, person), (None, "head_mask_unavailable"))
        person = cut_subject(fixture)
        person["head_evidence"]["binding"] = "wrong"
        self.assertEqual(self.render(maker, fixture, person), (None, "head_mask_unavailable"))
        self.assertTrue(B.head_mask_available(cut_subject(fixture)["head_evidence"], frame_key="fixture-7", binding=BINDING))
        self.assertFalse(B.head_mask_available({"frame_key": "fixture-7", "binding": BINDING},
                                               frame_key="fixture-7", binding=BINDING))

    def test_a_cut_without_measured_depth_at_the_shoulders_clears(self):
        fixture = scene()
        maker = self.learned(fixture)
        person = cut_subject(fixture)
        cut = person["head_evidence"]["cut"]
        cut["shoulder_px"] = [5, 5]   # a corner with nothing measured there
        cut["shoulders_px"] = [[3, 5], [7, 5]]
        hole = fixture[1].copy()
        hole[:40, :40] = 0
        self.assertEqual(maker.step(hole, observation="person", person=person, now=20, frame_key="fixture-7",
                                    binding=BINDING), (None, "head_mask_unavailable"))
        # Nor does it fall back to the face envelope when one is there too.
        person["head_evidence"].update({key: value for key, value in subject(fixture)["head_evidence"].items()
                                        if key not in ("frame_key", "binding")})
        self.assertEqual(maker.step(hole, observation="person", person=person, now=20, frame_key="fixture-7",
                                    binding=BINDING), (None, "head_mask_unavailable"))

    def test_a_cut_wider_than_the_cap_clears_rather_than_thinning(self):
        near = np.full((B.HEIGHT, B.WIDTH), 400, np.uint16)   # someone half a metre from the camera
        cut = ((320.0, 200.0), (0.0, -1.0), 0.30, ((300.0, 200.0), (340.0, 200.0)), 3.0)
        self.assertIsNone(B.cut_pixels(cut, near, FX), "0.30 m at 0.4 m is 300 px: past the cap")
        cut = ((320.0, 200.0), (0.0, -1.0), 0.15, ((300.0, 200.0), (340.0, 200.0)), 3.0)
        self.assertAlmostEqual(B.cut_pixels(cut, near, FX)[2], 150.0)

    def test_a_cut_that_does_not_read_never_falls_back_to_the_envelope(self):
        fixture = scene()
        maker = self.learned(fixture)
        person = subject(fixture)   # a good envelope
        person["head_evidence"]["cut"] = dict(cut_subject(fixture)["head_evidence"]["cut"], axis_px=[0, 2])
        self.assertEqual(self.render(maker, fixture, person), (None, "head_mask_unavailable"))
        self.assertFalse(B.head_mask_available(person["head_evidence"], frame_key="fixture-7", binding=BINDING))
        person["head_evidence"].pop("cut")
        self.assertIsNone(self.render(maker, fixture, person)[1], "the envelope alone, where no cut was ever made")

    def test_nothing_above_the_tracked_shoulders_is_kept(self):
        fixture = scene("standing")
        maker = self.learned(fixture)
        joints = fixture[4]
        ceiling = float(joints["left_hip"][0][2]) + 0.10   # well under the shoulders: the cut's own ceiling is higher
        frame, why = self.render(maker, fixture, cut_subject(fixture, ceiling=ceiling))
        self.assertIsNone(why)
        self.assertLessEqual(decode(frame)["position_mm"][:, 2].max(), ceiling * 1000 + 1)
        high, _ = self.render(maker, fixture, cut_subject(fixture))
        self.assertGreater(high["points"]["count"], frame["points"]["count"])

    def test_with_the_shoulders_cut_the_figure_may_anchor_on_other_measured_joints(self):
        fixture = scene("seated")
        maker = self.learned(fixture)
        person = cut_subject(fixture, margin=0.06)
        joints = person["joints"]
        _, depth, *_ = fixture
        # A seated reader: the hips sink into the sofa's depth, but an arm and a knee stand out.
        joints["left_hip"][1] = joints["right_hip"][1] = "uncertain"
        joints["left_elbow"] = [world(366, 197, 2030).tolist(), "supported"]
        joints["left_knee"] = [world(345, 255, 1995).tolist(), "supported"]
        frame, why = self.render(maker, fixture, person)
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        self.assertFalse(B.cut_side(cols, rows, cut_px(fixture, person)).any())
        self.assertFalse(fixture[2][rows, cols].any())
        self.assertIsNone(frame["facing_deg"], "no heading without the hips in the cloud")
        joints.pop("left_knee")   # one joint can't anchor a part on its own
        self.assertEqual(self.render(maker, fixture, person)[1], "pose_unavailable")

    def test_a_profile_that_shows_the_head_keeps_the_whole_measured_person(self):
        fixture = scene("standing")
        maker = self.learned(fixture)
        person = subject(fixture)
        person["head_evidence"] = {"frame_key": "fixture-7", "binding": BINDING, "head": B.HEAD_SHOWN}
        self.assertTrue(B.head_mask_available(person["head_evidence"], frame_key="fixture-7", binding=BINDING))
        frame, why = self.render(maker, fixture, person)
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        self.assertTrue(fixture[2][rows, cols].any(), "the head's measured pixels are in the cloud")
        masked, _ = self.render(maker, fixture)
        self.assertGreater(frame["points"]["count"], masked["points"]["count"])
        for changes in ({"binding": "wrong"}, {"frame_key": "wrong"}, {"head": "hidden"}):
            with self.subTest(changes=changes):
                evidence = dict(person["head_evidence"], **changes)
                self.assertFalse(B.head_mask_available(evidence, frame_key="fixture-7", binding=BINDING))
                self.assertEqual(self.render(maker, fixture, dict(person, head_evidence=evidence)),
                                 (None, "head_mask_unavailable"))
        # "shown" beside a mask isn't shown: the mask's own rules hold.
        broken = dict(cut_subject(fixture)["head_evidence"], head=B.HEAD_SHOWN)
        broken["cut"] = dict(broken["cut"], axis_px=[0, 2])
        self.assertEqual(self.render(maker, fixture, dict(person, head_evidence=broken)), (None, "head_mask_unavailable"))

    def test_missing_uncertain_or_unbound_head_evidence_clears(self):
        fixture = scene("reclined")
        maker = self.learned(fixture)
        for changes in ({"binding": "wrong"}, {"frame_key": "wrong"}, {"motion_px": -1},
                        {"localization_px": float("inf")}, {"bounds_px": [1, 2, 1, 4]}, {"bounds_px": None}):
            with self.subTest(changes=changes):
                person = subject(fixture)
                person["head_evidence"].update(changes)
                self.assertEqual(self.render(maker, fixture, person), (None, "head_mask_unavailable"))
        person = subject(fixture)
        person.pop("head_evidence")
        self.assertEqual(self.render(maker, fixture, person), (None, "head_mask_unavailable"))

    def test_depth_discontinuity_excludes_touching_furniture(self):
        fixture = scene()
        changed = fixture[1].copy()
        changed[150:182, 260:292] = 2240  # attached in image space, behind a depth edge
        variant = fixture[0], changed, *fixture[2:]
        frame, why = self.render(self.learned(fixture), variant)
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        self.assertFalse(np.any((rows >= 150) & (rows < 182) & (cols < 292)))

    def test_sparse_night_depth_and_unknown_background_leave_holes(self):
        fixture = scene()
        sparse = np.zeros_like(fixture[1])
        sparse[148:156, 300:304] = 2000
        variant = fixture[0], sparse, *fixture[2:]
        maker = self.learned(fixture)
        self.assertEqual(self.render(maker, variant), (None, "insufficient_depth"))
        maker.background[181:195, 309:329] = 0
        frame, why = self.render(maker, fixture)
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        self.assertFalse(np.any((rows >= 181) & (rows < 195) & (cols >= 309) & (cols < 329)))

    def test_holes_on_old_sampling_lattice_do_not_drop_whole_cloud(self):
        fixture = scene()
        depth = fixture[1].copy()
        depth[126:240:3, 294:355:3] = 0
        frame, why = self.render(self.learned(fixture), (fixture[0], depth, *fixture[2:]))
        self.assertIsNone(why)
        self.assertGreater(frame["points"]["count"], 300)

    def test_incomplete_torso_omits_heading_without_dropping_the_figure(self):
        fixture = scene()
        maker = self.learned(fixture)
        frame, why = self.render(maker, fixture)
        self.assertIsNone(why)
        self.assertEqual(frame["facing_deg"], 0)
        person = subject(fixture)
        person["joints"]["left_hip"][1] = "uncertain"
        frame, why = self.render(maker, fixture, person)
        self.assertIsNone(why)
        self.assertIsNone(frame["facing_deg"])
        self.assertIsNone(frame["pelvis_z_m"])
        self.assertIsNotNone(frame["shoulder_midpoint_m"])

    def test_unknown_interrupts_empty_learning_and_pose_does_not_fill(self):
        fixture = scene()
        maker = self.maker()
        self.assertEqual(self.render(maker, fixture), (None, "warming_background"))
        maker.step(fixture[0], observation="empty", now=0)
        maker.step(fixture[0], observation="empty", now=B.EMPTY_S)
        self.assertEqual(len(maker.learning), 1)
        self.assertEqual(maker.step(None, observation="unknown", now=9), (None, "pose_unavailable"))
        self.assertEqual(maker.learning, [])
        maker.step(fixture[0], observation="empty", now=10)
        self.assertIsNone(maker.background)

    def test_bounded_fixture_compute_time(self):
        fixture = scene()
        maker = self.learned(fixture)
        start = time.perf_counter()
        for _ in range(10):
            self.assertIsNone(self.render(maker, fixture)[1])
        per_frame = (time.perf_counter() - start) / 10
        self.assertLess(per_frame, 0.12, f"{per_frame * 1000:.0f} ms per frame")


RGB = {"k": [FX, FY, CX, CY, 0.0], "d": [], "from_left": [[[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0.0, 0.0, 0.0]],
       "size": [B.WIDTH, B.HEIGHT]}   # an RGB camera that sees exactly as the depth camera does


def holding(pose="standing"):
    """A person holding a book in front of the chest, near the left wrist, and a phone lying across the room."""
    bg, depth, head, bounds, joints = scene(pose)
    depth = depth.copy()
    c = 292
    depth[175:200, c + 12:c + 42] = 1850                       # the book, 15 cm in front of the torso
    depth[300:315, 60:100] = 2500                              # the phone on a table, far from any hand
    joints = {**joints, "left_wrist": [world(c + 27, 188, 1860).tolist(), "supported"]}
    book = {"label": "book", "score": 0.91, "polygon": [[c + 10, 173], [c + 44, 173], [c + 44, 202], [c + 10, 202]]}
    phone = {"label": "cell phone", "score": 0.88, "polygon": [[58, 298], [102, 298], [102, 317], [58, 317]]}
    return (bg, depth, head, bounds, joints), book, phone


class HeldObjectTest(unittest.TestCase):
    def maker(self):
        return B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T), rgb=RGB)

    learned = BodyTest.learned

    def held_frame(self, maker, fixture, held, key="fixture-7", track="fixture-track"):
        sequence = getattr(maker, "fixture_sequence", 0) + 1
        maker.fixture_sequence = sequence
        key = ["fixture-run", 1, sequence]
        person = dict(subject(fixture, frame_key=key), track_id=track, association_revision=1,
                      association_status="confident", mode="rgb")
        masks = None if held is None else {"frame_key": key, "segmentation_status": "ok",
                                         "held_masks": held, "person_masks": []}
        return maker.step(fixture[1], observation="person", person=person, now=20 + sequence * 0.2,
                          frame_key=key, binding=BINDING, masks=masks)

    def test_a_held_book_becomes_blue_points_with_its_label_from_the_second_frame(self):
        fixture, book, phone = holding()
        maker = self.learned(fixture)
        first, why = self.held_frame(maker, fixture, [book, phone])
        self.assertIsNone(why)
        self.assertIsNone(first["object"], "one frame isn't a hold yet")
        self.assertFalse((decode(first)["kind"] == B.KIND_OBJECT).any())
        second, _ = self.held_frame(maker, fixture, [book, phone])
        self.assertEqual(second["object"], {"label": "book", "confidence": 0.91, "source": "detector"})
        points = decode(second)
        objects = points["kind"].ravel() == B.KIND_OBJECT
        self.assertGreater(int(objects.sum()), 10)
        rows, cols = np.divmod(points["key"].ravel(), B.WIDTH)
        self.assertTrue(((rows[objects] >= 175) & (rows[objects] < 200)).all(), "only the book's own depth pixels")
        self.assertFalse(((rows[~objects] >= 175) & (rows[~objects] < 200) & (cols[~objects] >= 304)
                          & (cols[~objects] < 334)).any(), "the body keeps none of them")
        self.assertEqual(len(set(points["key"].ravel().tolist())), len(points["key"]), "every key once")
        self.assertTrue((np.diff(points["key"].ravel().astype(np.int64)) > 0).all(), "row-major order")

    def test_a_phone_on_the_table_is_never_held(self):
        fixture, _book, phone = holding()
        maker = self.learned(fixture)
        for _ in range(3):
            frame, _ = self.held_frame(maker, fixture, [phone])
            self.assertIsNone(frame["object"])
            self.assertFalse((decode(frame)["kind"] == B.KIND_OBJECT).any())

    def test_two_of_the_last_three_frames(self):
        fixture, book, _phone = holding()
        maker = self.learned(fixture)
        labels = [self.held_frame(maker, fixture, held)[0]["object"] for held in ([book], [], [book], [], [], [book])]
        # A frame without the mask has no object points to show; the label needs this frame's mask and one more.
        self.assertEqual([None if o is None else o["label"] for o in labels], [None, None, "book", None, None, None])
        maker = self.learned(fixture)
        labels = [self.held_frame(maker, fixture, held)[0]["object"] for held in ([book], [book], [], [book])]
        self.assertEqual([None if o is None else o["label"] for o in labels], [None, "book", None, "book"])
        maker = self.learned(fixture)
        self.held_frame(maker, fixture, [book])
        self.assertIsNone(self.held_frame(maker, fixture, [book], track="someone-else")[0]["object"],
                          "a new track starts its own count")

    def test_not_holdable_or_malformed_instances_are_ignored(self):
        fixture, book, _phone = holding()
        maker = self.learned(fixture)
        bad = [dict(book, label="couch"), dict(book, score=1.5), dict(book, polygon=[[1, 2], [3, 4]]),
               dict(book, polygon=[[float("nan"), 1]] * 4), "book", None]
        for _ in range(3):
            self.assertIsNone(self.held_frame(maker, fixture, bad)[0]["object"])

    def test_with_the_pass_off_the_frame_is_exactly_as_before(self):
        fixture, book, _phone = holding()
        maker = self.learned(fixture)
        frame, _ = self.held_frame(maker, fixture, None)
        self.assertNotIn("object", frame)
        self.assertFalse((decode(frame)["kind"] != 0).any())
        plain = B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T))
        self.assertIsNone(plain.rgb, "no RGB camera in the setup: the pass can't run")
        with self.assertRaises(ValueError):
            B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T), rgb=dict(RGB, k=[0, 0, 0, 0, 0]))


def phone_in_hand(phone=True):
    """A standing person whose left forearm, wrist and knees are measured, holding a phone just past the hand (or not)."""
    bg, depth, head, bounds, joints = scene("standing")
    depth = depth.copy()
    c = 292
    if phone:
        depth[170:194, c + 92:c + 108] = 1950                  # 8 by 12 cm, 8 cm nearer than the hand
    joints = {**joints, "left_elbow": [world(c + 62, 198, 2030).tolist(), "supported"],
              "left_wrist": [world(c + 88, 198, 2030).tolist(), "supported"],
              "left_knee": [world(c + 13, 300, 2000).tolist(), "supported"],
              "right_knee": [world(c + 50, 300, 2000).tolist(), "supported"]}
    return bg, depth, head, bounds, joints


class DepthHeldTest(unittest.TestCase):
    """What the hand holds by depth alone, when the seg pass names nothing there (a phone is too small for it)."""
    maker = HeldObjectTest.maker
    learned = BodyTest.learned
    held_frame = HeldObjectTest.held_frame

    def kinds(self, frame):
        return decode(frame)["kind"].ravel()

    def test_a_held_phone_with_no_seg_instance_is_held_unnamed(self):
        fixture = phone_in_hand()
        maker = self.learned(fixture)
        first, why = self.held_frame(maker, fixture, [])
        self.assertIsNone(why)
        self.assertFalse((self.kinds(first) == B.KIND_OBJECT).any(), "one frame isn't a hold yet")
        second, _ = self.held_frame(maker, fixture, [])
        self.assertIsNone(second["object"], "unnamed: Home marks it as a held object")
        points = decode(second)
        held = points["kind"].ravel() == B.KIND_OBJECT
        self.assertGreater(int(held.sum()), 10)
        rows, cols = np.divmod(points["key"].ravel(), B.WIDTH)
        self.assertTrue(((rows[held] >= 170) & (rows[held] < 194) & (cols[held] >= 384) & (cols[held] < 400)).all(),
                        "only the phone's own pixels")

    def test_an_empty_hand_holds_nothing(self):
        fixture = phone_in_hand(phone=False)
        maker = self.learned(fixture)
        for _ in range(3):
            frame, why = self.held_frame(maker, fixture, [])
            self.assertIsNone(why)
            self.assertIsNone(frame["object"])
            self.assertFalse((self.kinds(frame) == B.KIND_OBJECT).any())

    def test_moving_hands_hold_nothing_by_depth(self):
        fixture = phone_in_hand()
        maker = self.learned(fixture)
        bg, depth, head, bounds, joints = fixture
        for step in range(3):
            moved = {**joints, "left_wrist": [(np.asarray(joints["left_wrist"][0]) + [0, 0, 0.2 * (step % 2)]).tolist(),
                                               "supported"]}
            key = ["fixture-run", 1, step + 1]
            person = dict(subject((bg, depth, head, bounds, moved), frame_key=key), track_id="fixture-track",
                          association_revision=1, association_status="confident", mode="rgb")
            masks = {"frame_key": key, "segmentation_status": "ok", "held_masks": [], "person_masks": []}
            frame, _ = maker.step(depth, observation="person", person=person, now=20 + step * 0.2, frame_key=key,
                                  binding=BINDING, masks=masks)
            self.assertFalse((self.kinds(frame) == B.KIND_OBJECT).any(), "a hand moving 1 m/s isn't holding still")

    def test_a_seg_named_object_comes_first(self):
        fixture = phone_in_hand()
        maker = self.learned(fixture)
        c = 292
        cup = {"label": "cup", "score": 0.8, "polygon": [[c + 90, 168], [c + 110, 168], [c + 110, 196], [c + 90, 196]]}
        self.held_frame(maker, fixture, [cup])
        frame, _ = self.held_frame(maker, fixture, [cup])
        self.assertEqual(frame["object"], {"label": "cup", "confidence": 0.8, "source": "detector"})

    def test_a_held_phone_or_remote_is_held_unnamed(self):
        """A held phone is about 20 px in the RGB frame: the seg model never calls it "cell phone" there, and calls it
        "remote" on some frames, a few cm from the wrist at 0.36-0.67 (the sofa benchmark, 2026-11-14). For that pair the
        mask's pixels are the object's points, but the name isn't sent: Home shows "Held object", never REMOTE."""
        fixture = phone_in_hand()
        c = 292
        box = [[c + 90, 168], [c + 110, 168], [c + 110, 196], [c + 90, 196]]
        for labels in (["remote", "remote"], ["cell phone", "cell phone"], ["remote", "cell phone"]):
            maker = self.learned(fixture)
            frames = [self.held_frame(maker, fixture, [{"label": label, "score": 0.6, "polygon": box}])[0]
                      for label in labels]
            self.assertFalse((self.kinds(frames[0]) == B.KIND_OBJECT).any(), "%s: one frame isn't a hold yet" % labels)
            self.assertIsNone(frames[1]["object"], "%s: unnamed" % labels)
            points = decode(frames[1])
            held = points["kind"].ravel() == B.KIND_OBJECT
            self.assertGreater(int(held.sum()), 10, "%s: the object's own points" % labels)
            rows, cols = np.divmod(points["key"].ravel(), B.WIDTH)
            self.assertTrue(((rows[held] >= 168) & (rows[held] < 196) & (cols[held] >= c + 90)
                             & (cols[held] < c + 110)).all(), "%s: inside the mask" % labels)

    def test_a_named_phone_and_a_depth_hold_count_as_one_unnamed_object(self):
        fixture = phone_in_hand()
        maker = self.learned(fixture)
        c = 292
        remote = {"label": "remote", "score": 0.6, "polygon": [[c + 90, 168], [c + 110, 168], [c + 110, 196], [c + 90, 196]]}
        self.held_frame(maker, fixture, [remote])
        frame, _ = self.held_frame(maker, fixture, [])
        self.assertIsNone(frame["object"])
        self.assertTrue((self.kinds(frame) == B.KIND_OBJECT).any(), "the seg frame and the depth frame are two of three")

    def test_with_the_pass_off_nothing_is_held_by_depth_either(self):
        fixture = phone_in_hand()
        maker = self.learned(fixture)
        for _ in range(3):
            frame, _ = self.held_frame(maker, fixture, None)
            self.assertNotIn("object", frame)
            self.assertFalse((self.kinds(frame) != 0).any())


class KeptBackgroundTest(unittest.TestCase):
    """The learned empty room survives a restart: a new run in the same room from the same camera starts from it."""

    def setUp(self):
        import tempfile
        folder = tempfile.TemporaryDirectory(prefix="den-body-bg-")
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        self.wall = 1_800_000_000.0

    def setup(self, run="run-1", **changes):
        binding = {**BINDING, "run_id": run, "track_epoch": 1}
        return {**SETUP, "setup": {**SETUP["setup"], "binding": binding, **changes}}

    def learn(self, channel, fixture):
        packet = (1, bundle(fixture[0]), "fixture-7")
        for i in range(B.LEARN_FRAMES + 2):
            channel.handle({**body_header("fixture-7", now=B.EMPTY_S * (i > 0) + i * 0.2),
                            "binding": channel.binding}, packet)
        self.assertIsNotNone(channel.maker.background)

    def channel(self, at=0.0):
        return B.BodyChannel(self.state, wall=lambda: self.wall + at)

    def test_a_restart_in_the_same_room_starts_from_the_learned_background(self):
        fixture = scene("standing")
        first = self.channel()
        first.handle(self.setup(), None)
        self.learn(first, fixture)
        self.assertTrue((self.state / B.BACKGROUND_FILE).is_file())
        later = self.channel(at=600)
        self.assertEqual(later.handle(self.setup(run="run-2"), None)["body_ready"], True)
        self.assertIsNotNone(later.maker.background, "a new run, the same room and camera: no warming")
        self.assertTrue(np.array_equal(later.maker.background, first.maker.background))
        self.assertIsNone(later.maker.learned_at, "the next empty spell still learns it afresh")
        frame, why = later.maker.step(fixture[1], observation="person", person=subject(fixture), now=20,
                                      frame_key="fixture-7", binding=BINDING)   # the fixture's head evidence's
        self.assertIsNone(why)
        self.assertGreater(frame["points"]["count"], 0)

    def test_another_room_camera_or_an_old_one_starts_warming(self):
        fixture = scene("standing")
        first = self.channel()
        first.handle(self.setup(), None)
        self.learn(first, fixture)
        for changes, at in (({"binding": {**BINDING, "run_id": "r", "content_revision": "another-scan"}}, 60),
                            ({"room_from_left": [R, [0.3, 0.0, 1.4]]}, 60), ({"depth_scale": 0.95}, 60),
                            ({}, B.KEPT_MAX_S + 1)):
            with self.subTest(changes=changes, at=at):
                later = self.channel(at=at)
                later.handle(self.setup(run="run-2", **changes), None)
                self.assertIsNone(later.maker.background)

    def test_a_damaged_file_or_no_state_folder_keeps_nothing(self):
        (self.state / B.BACKGROUND_FILE).write_bytes(b"not a background")
        damaged = self.channel()
        self.assertEqual(damaged.handle(self.setup(), None)["body_ready"], True)
        self.assertIsNone(damaged.maker.background)
        plain = B.BodyChannel()
        plain.handle(self.setup(), None)
        self.learn(plain, scene("standing"))
        self.assertEqual((self.state / B.BACKGROUND_FILE).read_bytes(), b"not a background", "no state: nothing kept")


class MaskGeometryTest(unittest.TestCase):
    learned = BodyTest.learned

    def test_the_live_head_shown_permission_is_preserved_and_stays_bound_to_its_frame(self):
        fixture = scene("standing")
        maker = self.learned(fixture)
        key = ["fixture-run", 1, 1]
        person = subject(fixture, frame_key=key)
        person["head_evidence"] = {"frame_key": key, "binding": BINDING, "head": B.HEAD_SHOWN}
        frame, why = maker.step(fixture[1], observation="person", person=person, now=20,
                                frame_key=key, binding=BINDING)
        self.assertIsNone(why)
        self.assertTrue(fixture[2].ravel()[decode(frame)["key"].ravel()].any())
        person["head_evidence"]["frame_key"] = ["fixture-run", 1, 2]
        self.assertEqual(maker.step(fixture[1], observation="person", person=person, now=20,
                                    frame_key=key, binding=BINDING), (None, "head_mask_unavailable"))
    def maker(self):
        rgb = {"k": [FX, FY, CX, CY, 0], "d": [],
               "from_left": [np.eye(3).tolist(), [0, 0, 0]], "size": [B.WIDTH, B.HEIGHT]}
        return B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T), rgb=rgb)

    def held_scene(self):
        fixture = scene()
        fixture[1][204:228, 364:392] = 1900
        fixture[4]["right_elbow"] = [world(365, 195, 2030).tolist(), "supported"]
        fixture[4]["right_wrist"] = [world(375, 216, 1900).tolist(), "supported"]
        return fixture

    def sample(self, maker, fixture, sequence, *, revision=1, mode="rgb", binding=BINDING, status="confident",
               held=None, person_masks=None, mask_key=None, mask_status=None):
        key = ["fixture-run", 1, sequence]
        person = subject(fixture, frame_key=key)
        person.update(association_revision=revision, association_status=status, mode=mode)
        if mask_status is not None:
            person["mask_association_status"] = mask_status
        person["head_evidence"]["binding"] = binding
        masks = {"frame_key": key if mask_key is None else mask_key, "segmentation_status": "ok",
                 "held_masks": [{"label": "book", "score": 0.91,
                                  "polygon": [[364, 204], [391, 204], [391, 227], [364, 227]]}] if held is None else held,
                 "person_masks": [] if person_masks is None else person_masks}
        return maker.step(fixture[1], observation="person", person=person, now=20 + sequence * 0.2,
                          frame_key=key, binding=binding, masks=masks)

    def test_same_instance_needs_two_distinct_current_frames_and_every_object_point_is_measured(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        first, why = self.sample(maker, fixture, 1)
        self.assertIsNone(why)
        self.assertFalse(np.any(decode(first)["kind"] == B.KIND_OBJECT))
        repeated, _ = self.sample(maker, fixture, 1)
        self.assertFalse(np.any(decode(repeated)["kind"] == B.KIND_OBJECT))
        frame, why = self.sample(maker, fixture, 2)
        self.assertIsNone(why)
        self.assertEqual(frame["object"], {"label": "book", "confidence": 0.91, "source": "detector"})
        actual = decode(frame)
        keys = actual["key"].ravel()
        self.assertGreater(int((actual["kind"] == B.KIND_OBJECT).sum()), 20)
        self.assertTrue(np.all(np.diff(keys.astype(np.int64)) > 0))
        rows, cols = np.divmod(keys, B.WIDTH)
        np.testing.assert_array_equal(actual["position_mm"], np.rint(world(cols, rows, fixture[1][rows, cols]) * 1000))
        self.assertFalse(fixture[2].ravel()[keys].any())

    def test_revision_mode_scope_and_unsafe_association_break_object_confirmation(self):
        for change in ({"revision": 2}, {"mode": "infrared"}, {"binding": {**BINDING, "profile": "new"}},
                       {"status": "ambiguous"}):
            with self.subTest(change=change):
                fixture = self.held_scene()
                maker = self.learned(fixture)
                self.sample(maker, fixture, 1)
                frame, why = self.sample(maker, fixture, 2, **change)
                if change.get("status") == "ambiguous":
                    self.assertEqual(why, "ambiguous")
                else:
                    self.assertIsNone(why)
                    self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))
                frame, _ = self.sample(maker, fixture, 3)
                self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))

    def test_wrong_frame_masks_cannot_confirm_or_draw_an_object(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        self.sample(maker, fixture, 1)
        frame, why = self.sample(maker, fixture, 2, mask_key=["fixture-run", 1, 1])
        self.assertIsNone(why)
        self.assertNotIn("object", frame)
        self.assertTrue(np.all(decode(frame)["kind"] == 0))
        frame, _ = self.sample(maker, fixture, 3)
        self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))

    def test_ambiguous_mask_association_clears_objects_while_raw_body_association_remains_confident(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        self.sample(maker, fixture, 1)
        confirmed, _ = self.sample(maker, fixture, 2)
        self.assertTrue(np.any(decode(confirmed)["kind"] == B.KIND_OBJECT))
        frame, why = self.sample(maker, fixture, 3, mask_status="ambiguous")
        self.assertIsNone(why)
        self.assertGreater(frame["points"]["count"], 300)
        self.assertNotIn("object", frame)
        self.assertTrue(np.all(decode(frame)["kind"] == 0))
        frame, why = self.sample(maker, fixture, 4)
        self.assertIsNone(why)
        self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT), "the ambiguous frame reset confirmation")

    def test_competing_instances_and_missing_current_depth_cannot_keep_an_object(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        self.sample(maker, fixture, 1)
        mask = {"label": "book", "score": 0.9, "polygon": [[364, 204], [391, 204], [391, 227], [364, 227]]}
        frame, why = self.sample(maker, fixture, 2, held=[mask, dict(mask)])
        self.assertIsNone(why)
        self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))
        frame, _ = self.sample(maker, fixture, 3)
        self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))
        frame, _ = self.sample(maker, fixture, 4)
        self.assertTrue(np.any(decode(frame)["kind"] == B.KIND_OBJECT))
        fixture[1][204:228, 364:392] = 0
        frame, why = self.sample(maker, fixture, 5)
        self.assertIsNone(why)
        self.assertFalse(np.any(decode(frame)["kind"] == B.KIND_OBJECT))

    def test_phone_remote_confusion_has_measured_points_and_a_generic_marker(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        for sequence, label in enumerate(("remote", "cell phone"), 1):
            mask = {"label": label, "score": 0.6, "polygon": [[364, 204], [391, 204], [391, 227], [364, 227]]}
            frame, why = self.sample(maker, fixture, sequence, held=[mask])
        self.assertIsNone(why)
        self.assertIsNone(frame["object"])
        self.assertTrue(np.any(decode(frame)["kind"] == B.KIND_OBJECT))

    def test_optional_masks_cannot_bypass_the_shoulder_cut(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        for sequence in (1, 2):
            key = ["fixture-run", 1, sequence]
            person = cut_subject(fixture, frame_key=key)
            person.update(association_revision=1, association_status="confident", mode="rgb")
            masks = {"frame_key": key, "segmentation_status": "ok", "person_masks": [],
                     "held_masks": [{"label": "book", "score": 0.9,
                                     "polygon": [[364, 204], [391, 204], [391, 227], [364, 227]]},
                                    {"label": "book", "score": 0.99,
                                     "polygon": [[290, 80], [350, 80], [350, 145], [290, 145]]}]}
            frame, why = maker.step(fixture[1], observation="person", person=person, now=20 + sequence * 0.2,
                                    frame_key=key, binding=BINDING, masks=masks)
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        cut = B.cut_pixels(B.torso_cut(person["head_evidence"], frame_key=key, binding=BINDING), fixture[1], FX)
        self.assertFalse(B.cut_side(cols, rows, cut).any())
        self.assertFalse(fixture[2][rows, cols].any())

    def test_same_label_on_another_surface_does_not_reuse_identity(self):
        history = B.ObjectHistory()
        owner = ("run", 1, "track", 1, "rgb", "profile")
        self.assertFalse(history.observe(owner, 1, 1.0, ((0.0, 0.0, 2.0), frozenset({1, 2}), "left_wrist")))
        self.assertFalse(history.observe(owner, 2, 1.2, ((0.2, 0.0, 2.0), frozenset({3, 4}), "left_wrist")))
        self.assertTrue(history.observe(owner, 3, 1.4, ((0.21, 0.0, 2.0), frozenset({3, 4}), "left_wrist")))
        self.assertFalse(history.observe(owner, 4, 1.6, ((0.21, 0.0, 2.0), frozenset({3, 4}), "right_wrist")))
        self.assertFalse(history.observe(owner, 5, 2.3, ((0.21, 0.0, 2.0), frozenset({3, 4}), "right_wrist")))

    def test_one_common_cap_keeps_both_kinds_and_unique_pixel_keys(self):
        fixture = self.held_scene()
        maker = self.learned(fixture)
        with patch.object(B, "MAX_POINTS", 600), patch.object(B, "OBJECT_MAX_POINTS", 250):
            self.sample(maker, fixture, 1)
            frame, why = self.sample(maker, fixture, 2)
        self.assertIsNone(why)
        actual = decode(frame)
        self.assertLessEqual(frame["points"]["count"], 600)
        self.assertGreater((actual["kind"] == 0).sum(), 0)
        self.assertGreater((actual["kind"] == B.KIND_OBJECT).sum(), 0)
        self.assertTrue(np.all(np.diff(actual["key"].ravel().astype(np.int64)) > 0))

    def test_person_mask_removes_touching_furniture_and_competing_masks_clear(self):
        fixture = self.held_scene()
        fixture[1][150:182, 260:292] = 2000
        maker = self.learned(fixture)
        mask = {"score": 0.9, "polygon": [[292, 130], [394, 130], [394, 290], [292, 290]]}
        frame, why = self.sample(maker, fixture, 1, person_masks=[mask])
        self.assertIsNone(why)
        rows, cols = np.divmod(decode(frame)["key"].ravel(), B.WIDTH)
        self.assertFalse(np.any((rows >= 150) & (rows < 182) & (cols < 292)))
        self.assertEqual(self.sample(maker, fixture, 2, person_masks=[mask, mask]), (None, "ambiguous"))

    def test_registered_rgb_projection_uses_extrinsics_and_visibility(self):
        maker = self.maker()
        maker.rgb["t"][0] = 0.1
        depth = np.full((B.HEIGHT, B.WIDTH), 2000, np.float32)
        mask = np.zeros((B.HEIGHT, B.WIDTH), np.uint8)
        mask[200:205, 340:345] = 1
        selected = maker._mask_pixels(depth, mask, 0, 0)
        self.assertTrue(selected[202, 322])
        self.assertFalse(selected[202, 342])
        # Several left rays land in the same RGB pixel. Only the nearer measured surface survives.
        maker.rgb["k"] = [1.0, 1.0, 320.0, 200.0, 0.0]
        maker.rgb["t"][0] = 0.0
        depth.fill(0)
        depth[200, 320], depth[200, 321] = 2000, 3000
        mask.fill(1)
        selected = maker._mask_pixels(depth, mask, 0, 0)
        self.assertTrue(selected[200, 320])
        self.assertFalse(selected[200, 321])



class ChannelTest(unittest.TestCase):
    def test_reply_binds_request_frame_and_calibration_and_rebind_drops_background(self):
        ch = B.BodyChannel()
        self.assertEqual(ch.handle(SETUP, None), {"body_ready": True, "binding": BINDING})
        bg = bundle(scene()[0])
        kept = (1, bg, "fixture-7")
        self.assertEqual(ch.handle(body_header("fixture-8", request=12), kept),
                         {"body_id": 1, "request_id": 12, "frame_key": "fixture-8", "binding": BINDING,
                          "frame": None, "why": "stale"})
        self.assertEqual(ch.handle(body_header("fixture-7", binding="wrong"), kept)["why"], "scope_changed")
        self.assertEqual(ch.handle(body_header("fixture-7"), (1, b"bad", "fixture-7"))["error"], "BadZipFile")
        ch.handle(body_header("fixture-7"), kept)
        ch.handle(body_header("fixture-7", now=B.EMPTY_S), kept)
        self.assertEqual(len(ch.maker.learning), 1)
        ch.handle(body_header("fixture-7", now=B.EMPTY_S + 1, reset_learning=True), kept)
        self.assertEqual(ch.maker.learning, [])
        ch.maker.background = scene()[0]
        new = {**SETUP, "setup": {**SETUP["setup"], "binding": "new", "room_from_left": [R, [0.3, 0.0, 1.4]]}}
        self.assertEqual(ch.handle(new, None), {"body_ready": True, "binding": "new"})
        self.assertIsNone(ch.maker.background)
        self.assertEqual(ch.handle(body_header("fixture-7"), kept)["why"], "scope_changed")

    def test_the_figures_depth_gets_step_2s_depth_scale(self):
        """The setup's depth_scale multiplies the retained bundle's depth before the maker sees it, as perceive.py
        scales the lift's depth; without one, or at 1.0, the depth is the bundle's own. Outside 0.90-1.10 it's refused."""
        seen = []

        class Maker:
            np = np
            stats = {}

            def reset_objects(self):
                pass

            def step(self, depth, **kw):
                seen.append(depth)
                return None, "warming_background"

            def interrupt_learning(self):
                pass
        depth = scene()[0].copy()
        depth[0, 0] = 0
        kept = (1, bundle(depth), "fixture-7")
        for scale, expected in ((None, depth), (1.0, depth), (0.97, np.where(depth > 0, np.rint(depth * 0.97), 0))):
            ch = B.BodyChannel()
            setup = dict(SETUP["setup"], **({} if scale is None else {"depth_scale": scale}))
            self.assertEqual(ch.handle({"kind": "setup_body", "setup": setup}, None), {"body_ready": True, "binding": BINDING})
            ch.maker = Maker()
            ch.handle(body_header("fixture-7"), kept)
            np.testing.assert_array_equal(seen[-1], expected)
            self.assertEqual(seen[-1].dtype, np.dtype("<u2"))
        self.assertEqual(seen[-1][0, 0], 0, 'no depth stays no depth')
        refused = B.BodyChannel().handle({"kind": "setup_body", "setup": dict(SETUP["setup"], depth_scale=1.2)}, None)
        self.assertEqual(refused, {"body_refused": "ValueError", "binding": BINDING})

    def test_real_worker_protocol_reader_round_trip_on_subprocess_pipe(self):
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
import body, detect_worker
channel, kept = body.BodyChannel(), None
while (message := detect_worker.read_frame(sys.stdin.buffer)) is not None:
    header, data = message
    if data is not None:
        kept = header["id"], data, header.get("frame_key")
        print(json.dumps({"id": header["id"]}), flush=True)
    else:
        print(json.dumps(channel.handle(header, kept)), flush=True)
"""
        def packet(header, data=None):
            values = [json.dumps(header).encode()]
            if data is not None:
                values.append(data)
            return b"".join(struct.pack(">I", len(value)) + value for value in values)

        frames = [packet(SETUP)]
        fixture = scene("reclined")
        empty = bundle(fixture[0])
        for i in range(B.LEARN_FRAMES + 1):
            key = ["infrared", i]
            frames.append(packet({"id": i + 1, "frame_key": key}, empty))
            frames.append(packet(body_header(key, id=i + 1, now=0 if i == 0 else B.EMPTY_S + i * 0.2)))
        final_key = ["infrared", 20]
        frames.append(packet({"id": 50, "frame_key": final_key}, bundle(fixture[1])))
        frames.append(packet(body_header(final_key, id=50, request=91, now=20, observation="person",
                                         person=subject(fixture, frame_key=final_key))))
        result = subprocess.run([sys.executable, "-B", "-c", code, str(HERE)], input=b"".join(frames),
                                capture_output=True, check=True, timeout=20)
        reply = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual((reply["request_id"], reply["frame_key"], reply["binding"]), (91, final_key, BINDING))
        self.assertIsNone(reply["why"])
        self.assertGreater(reply["frame"]["points"]["count"], 300)


if __name__ == "__main__":
    unittest.main()
