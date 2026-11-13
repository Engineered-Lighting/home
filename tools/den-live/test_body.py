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
