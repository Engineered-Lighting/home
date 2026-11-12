"""body.py on synthetic depth: a wall, a floor and a standing person, rendered through a pinhole camera. No camera.

    <detector venv python> -B test_body.py
"""
import base64
import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body as B  # noqa: E402

FX = FY = 400.0
CX, CY = 320.0, 200.0
LENS_M = 1.4
# camera (x right, y down, z forward) -> room (x right, y forward, z up), lens 1.4 m up
R = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
T = [0.0, 0.0, LENS_M]
v, u = np.mgrid[0:B.HEIGHT, 0:B.WIDTH]
RX, RY = (u - CX) / FX, (v - CY) / FY


def room_of(cam):
    return np.asarray(cam, float) @ np.asarray(R, float).T + T


def render(*, person=None, book=False, wall_m=4.0):
    """Depth in mm: a wall at wall_m, the floor, and optionally a person (a vertical cylinder) and a book."""
    s = np.full(RX.shape, wall_m)
    with np.errstate(divide="ignore", invalid="ignore"):
        floor = np.where(RY > 0, LENS_M / RY, np.inf)
    s = np.minimum(s, floor)
    if person is not None:
        x0, y0, r, h = person          # room centre x, y; radius; height
        # x_cam = x, z_cam = y (forward); ray (RX s, RY s, s): (RX s - x0)^2 + (s - y0)^2 = r^2
        a, b, c = RX ** 2 + 1, -2 * (RX * x0 + y0), x0 ** 2 + y0 ** 2 - r ** 2
        disc = b * b - 4 * a * c
        with np.errstate(invalid="ignore"):
            hit = (-b - np.sqrt(disc)) / (2 * a)
        zroom = LENS_M - RY * hit
        ok = (disc >= 0) & (zroom >= 0) & (zroom <= h)
        s = np.where(ok & (hit < s), hit, s)
    if book:                            # a 20 x 20 cm book held upright, 30 cm in front of the chest, overlapping it
        zb = 2.2
        x, y = RX * zb, RY * zb
        ok = (x >= 0.06) & (x <= 0.26) & (y >= 0.35) & (y <= 0.55)
        s = np.where(ok & (zb < s), zb, s)
    return np.clip(np.rint(s * 1000), 0, 65535).astype(np.uint16)


def joints(still=True, t=0.0):
    J = {"left_shoulder": (0.18, 2.5, 1.4), "right_shoulder": (-0.18, 2.5, 1.4), "left_hip": (0.1, 2.5, 0.9), "right_hip": (-0.1, 2.5, 0.9),
         "left_ear": (0.07, 2.5, 1.58), "right_ear": (-0.07, 2.5, 1.58), "nose": (0.0, 2.4, 1.56),
         "left_elbow": (0.24, 2.42, 1.12), "right_elbow": (-0.24, 2.42, 1.12), "left_wrist": (0.24, 2.25, 0.96), "right_wrist": (0.14, 2.25, 0.96),
         "left_knee": (0.1, 2.5, 0.5), "right_knee": (-0.1, 2.5, 0.5), "left_ankle": (0.1, 2.5, 0.08), "right_ankle": (-0.1, 2.5, 0.08)}
    if not still:
        for k in ("left_wrist", "right_wrist"):
            x, y, z = J[k]
            J[k] = (x + 0.12 * math.sin(t * 9), y, z + 0.12 * math.cos(t * 9))
    return {k: (list(p), "supported") for k, p in J.items()}


def decode(frame):
    p = frame["points"]
    get = lambda name, dtype: np.frombuffer(base64.b64decode(p[name]), dtype)
    return {"key": get("key", "<u4"), "pos": get("position_mm", "<i2").reshape(-1, 3) / 1000.0, "kind": get("kind", "u1"),
            "normal": get("normal", "i1").reshape(-1, 3) / 127.0, "head": get("head", "u1")}


class BodyTest(unittest.TestCase):
    def maker(self):
        return B.BodyMaker(np, cv2, left_k=(FX, FY, CX, CY), room_from_left=(R, T))

    def learned(self):
        m = self.maker()
        empty = render()
        t = 0.0
        for _ in range(int(B.EMPTY_S / 0.2) + B.LEARN_FRAMES + 2):
            t += 0.2
            frame, why = m.step(empty, person=None, now=t)
            self.assertIsNone(frame)
        self.assertIsNotNone(m.background, "an empty den is learned after EMPTY_S")
        return m, t

    def person(self, **kw):
        return {"track_id": "e1-trk-1", "xy_m": [0.0, 2.5], "joints": joints(**kw)}

    def test_before_the_empty_room_is_learned_the_pose_alone_draws_a_faint_figure(self):
        m = self.maker()
        frame, why = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=1.0)
        self.assertIsNone(why)
        self.assertEqual(frame["source"], "joints")
        d = decode(frame)
        self.assertEqual(set(d["kind"].tolist()), {3}, "every point a guess")
        self.assertTrue(np.all(d["key"] >= 4 * B.N))
        self.assertGreater(len(d["key"]), 300)
        self.assertGreater(d["pos"][:, 2].max() - d["pos"][:, 2].min(), 1.3, "shins to head")
        self.assertTrue(np.any(d["head"] == 255))
        torso = d["pos"][(d["pos"][:, 2] > 0.95) & (d["pos"][:, 2] < 1.35)]
        self.assertLess(np.median(np.hypot(torso[:, 0], torso[:, 1] - 2.5)), 0.2, "the torso stands where the pose is")

    def test_without_the_torso_there_is_no_figure(self):
        m = self.maker()
        person = {"track_id": "e1-trk-1", "xy_m": [0, 2.5], "joints": {k: v for k, v in joints().items() if "hip" not in k}}
        frame, why = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=person, now=1.0)
        self.assertIsNone(frame)
        self.assertEqual(why, "learning the empty room")
        frame, why = m.step(render(), person={"track_id": "e1-trk-1", "xy_m": [0, 2.5], "joints": {}}, now=1.2)
        self.assertEqual((frame, why), (None, "learning the empty room"))

    def test_thin_depth_falls_back_to_the_figure(self):
        m, t = self.learned()
        frame, why = m.step(render(), person=self.person(), now=t + 0.2)   # the depth shows nobody: the night's 6 % valid
        self.assertIsNone(why)
        self.assertEqual(frame["source"], "joints")
        self.assertEqual(set(decode(frame)["kind"].tolist()), {3})
        frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=t + 0.4)
        self.assertNotIn("source", frame, "measured depth is back: the figure from depth again")
        self.assertIn(0, decode(frame)["kind"].tolist())

    def test_a_standing_person_is_a_valid_frame_on_their_cylinder(self):
        m, t = self.learned()
        frame, why = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=t + 0.2, capture_ts=1000.5, seq=7)
        self.assertIsNone(why)
        self.assertEqual(frame["schema"], "den-live-body-1")
        self.assertEqual((frame["track_id"], frame["seq"], frame["capture_ts"]), ("e1-trk-1", 7, 1000.5))
        d = decode(frame)
        self.assertEqual(len(d["key"]), frame["points"]["count"])
        self.assertTrue(np.all(np.diff(d["key"].astype(np.int64)) > 0), "keys strictly ascending")
        self.assertTrue(set(d["kind"].tolist()) <= {0, 1, 3, 5})
        front = d["pos"][d["kind"] == 0]
        self.assertGreater(len(front), 300)
        radial = np.hypot(front[:, 0], front[:, 1] - 2.5)
        self.assertLess(np.median(np.abs(radial - 0.15)), 0.02, "the front lies on the person's surface")
        self.assertTrue(np.all((front[:, 2] >= 0.02) & (front[:, 2] <= 1.62)))
        self.assertLess(d["key"][d["kind"] == 0].max(), B.N, "front keys are depth-pixel indices")
        nz = d["normal"][d["kind"] == 0]
        known = np.linalg.norm(nz, axis=1) > 0.5
        self.assertGreater(known.mean(), 0.7, "most front points have a normal")
        facing = np.einsum("ij,ij->i", nz[known], np.c_[-front[:, 0], -front[:, 1], LENS_M - front[:, 2]][known]) > 0
        self.assertGreater(facing.mean(), 0.97, "front normals face the camera")

    def test_the_back_is_filled_behind_the_front_and_never_past_the_wall(self):
        m, t = self.learned()
        frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=t + 0.2)
        d = decode(frame)
        back = d["pos"][d["kind"] == 1]
        self.assertGreater(len(back), 100)
        self.assertGreater(np.median(back[:, 1]), 2.5, "the back is farther from the camera than the centre")
        self.assertLess(back[:, 1].max(), 4.0)
        bk = d["key"][d["kind"] == 1]
        self.assertTrue(np.all(bk >= B.N))

    def test_limbs_nothing_measured_covers_are_guessed(self):
        m, t = self.learned()
        frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=t + 0.2)
        d = decode(frame)
        guessed = d["pos"][d["kind"] == 3]
        self.assertGreater(len(guessed), 20, "the arms are guessed: the cylinder has none")
        self.assertTrue(np.all(d["key"][d["kind"] == 3] >= 4 * B.N))
        self.assertTrue(any(j["name"] == "left_wrist" and j["status"] == "measured" for j in frame["joints"]))

    def test_the_head_region_is_marked(self):
        m, t = self.learned()
        frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6)), person=self.person(), now=t + 0.2)
        d = decode(frame)
        top = d["pos"][:, 2] > 1.5
        self.assertTrue(np.all(d["head"][top & (np.linalg.norm(d["pos"] - [0, 2.5, 1.58], axis=1) < 0.12)] == 255))
        self.assertTrue(np.all(d["head"][d["pos"][:, 2] < 1.2] == 0))

    def test_a_held_book_shows_after_four_still_frames(self):
        m, t = self.learned()
        seen = []
        for i in range(6):
            t += 0.2
            frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6), book=True), person=self.person(), now=t)
            seen.append(int((decode(frame)["kind"] == 5).sum()))
        self.assertEqual(seen[:3], [0, 0, 0])
        self.assertGreater(seen[3], 50)
        self.assertIn("object", frame)
        book = decode(frame)["pos"][decode(frame)["kind"] == 5]
        self.assertLess(abs(np.median(book[:, 1]) - 2.2), 0.03, "the object is the book's plane")
        self.assertFalse(np.any(decode(frame)["kind"][np.isin(decode(frame)["key"], decode(frame)["key"][decode(frame)["kind"] == 5] + B.N)] == 1),
                         "no back fill behind the book")

    def test_fast_hands_make_no_object(self):
        m, t = self.learned()
        for i in range(8):
            t += 0.2
            frame, _ = m.step(render(person=(0, 2.5, 0.15, 1.6), book=True), person=self.person(still=False, t=t), now=t)
        self.assertEqual(int((decode(frame)["kind"] == 5).sum()), 0)
        self.assertNotIn("object", frame)

    def test_near_a_limb_4_cm_is_enough(self):
        m = self.maker()
        wall = np.full((B.HEIGHT, B.WIDTH), 3000, np.uint16)
        t = 0.0
        for _ in range(int(B.EMPTY_S / 0.2) + B.LEARN_FRAMES + 2):
            t += 0.2
            m.step(wall, person=None, now=t)
        scene = wall.copy()
        scene[60:240, 290:350] = 2500                    # the torso, half a metre out
        scene[240:330, 300:330] = 2930                   # a leg 7 cm in front of the "sofa"
        cam = lambda uu, vv, zz: [(uu - CX) / FX * zz, (vv - CY) / FY * zz, zz]
        J = {"left_hip": room_of(cam(315, 245, 2.98)), "left_knee": room_of(cam(315, 285, 2.98)), "left_ankle": room_of(cam(315, 325, 2.98)),
             "left_shoulder": room_of(cam(300, 80, 2.55)), "right_shoulder": room_of(cam(340, 80, 2.55)), "right_hip": room_of(cam(330, 230, 2.55))}
        with_joints = {"track_id": "e1-trk-1", "xy_m": list(room_of(cam(320, 150, 2.5))[:2]), "joints": {k: (list(p), "supported") for k, p in J.items()}}
        without = {"track_id": "e1-trk-1", "xy_m": with_joints["xy_m"], "joints": {}}
        for k in range(4):                               # the depth smoothing settles over a few frames
            f1, _ = m.step(scene, person=with_joints, now=t + 0.2 * (k + 1))
        f0, _ = m.step(scene, person=without, now=t + 1.2)
        low = lambda f: int(np.sum(decode(f)["pos"][decode(f)["kind"] == 0][:, 2] < room_of(cam(315, 240, 2.98))[2] - 0.05))
        self.assertGreater(low(f1), 20, "the leg against the sofa is kept near its capsule")
        self.assertEqual(low(f0), 0, "without the pose, 7 cm is not enough")

    def test_something_far_from_the_person_is_not_them(self):
        m, t = self.learned()
        scene = render(person=(0, 2.5, 0.15, 1.6))
        far = render(person=(1.6, 2.5, 0.15, 1.6))       # a second shape 1.6 m away
        frame, _ = m.step(np.minimum(scene, far), person=self.person(), now=t + 0.2)
        front = decode(frame)["pos"][decode(frame)["kind"] == 0]
        self.assertLess(np.abs(front[:, 0]).max(), 0.5)

    def test_runs_in_well_under_a_frame(self):
        import time
        m, t = self.learned()
        scene = render(person=(0, 2.5, 0.15, 1.6), book=True)
        start = time.perf_counter()
        for i in range(10):
            m.step(scene, person=self.person(), now=t + 0.2 * (i + 1))
        per = (time.perf_counter() - start) / 10
        self.assertLess(per, 0.12, "%.0f ms a frame" % (per * 1000))


class ChannelTest(unittest.TestCase):
    """body.BodyChannel: the body's side of detect_worker.py's protocol, on the bundle the worker kept."""

    @staticmethod
    def bundle(depth):
        import io
        import zipfile
        buf, npy = io.BytesIO(), io.BytesIO()
        np.save(npy, depth)
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("depth_mm.npy", npy.getvalue())
        return buf.getvalue()

    def test_set_up_then_bodies_for_the_kept_frame(self):
        ch = B.BodyChannel()
        self.assertEqual(ch.handle({"kind": "body", "id": 1, "now": 0, "person": None}, None), {"body_id": 1, "error": "NotSetUp"})
        self.assertEqual(ch.handle({"kind": "setup_body", "setup": {"left_k": [FX, FY, CX, CY], "room_from_left": [R, T], "home": "den"}}, None),
                         {"body_ready": True})
        empty, t = self.bundle(render()), 0.0
        for i in range(int(B.EMPTY_S / 0.2) + B.LEARN_FRAMES + 2):
            t += 0.2
            reply = ch.handle({"kind": "body", "id": i, "now": t, "person": None}, (i, empty))
            self.assertEqual((reply["body_id"], reply["frame"], reply["why"]), (i, None, "nobody tracked"))
        person = {"track_id": "e1-trk-1", "xy_m": [0, 2.5], "joints": joints()}
        scene = self.bundle(render(person=(0, 2.5, 0.15, 1.6)))
        reply = ch.handle({"kind": "body", "id": 99, "now": t + 0.2, "capture_ts": 5.0, "seq": 3, "person": person}, (99, scene))
        self.assertEqual(reply["body_id"], 99)
        self.assertEqual(reply["frame"]["schema"], "den-live-body-1")
        self.assertGreater(reply["frame"]["points"]["count"], 1000)
        self.assertLess(reply["compute_s"], 0.12)
        self.assertEqual(list(reply)[0], "body_id", "the reply's first key tells the loop's reader it is the body's")

    def test_a_request_for_another_frame_or_a_bad_bundle_is_a_reply_not_a_stop(self):
        ch = B.BodyChannel()
        ch.handle({"kind": "setup_body", "setup": {"left_k": [FX, FY, CX, CY], "room_from_left": [R, T]}}, None)
        self.assertEqual(ch.handle({"kind": "body", "id": 5, "now": 1, "person": None}, (4, b"")),
                         {"body_id": 5, "frame": None, "why": "frame not kept"})
        self.assertEqual(ch.handle({"kind": "body", "id": 6, "now": 1, "person": None}, (6, b"not a zip")), {"body_id": 6, "error": "BadZipFile"})
        self.assertEqual(B.BodyChannel().handle({"kind": "setup_body", "setup": {}}, None), {"body_refused": "KeyError"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
