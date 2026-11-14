"""Offline checks for blind annotations and the shared RGB head mask."""

import hashlib
import io
import json
import math
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import sofa_mask_score as mask
import test_body_head as heads
import test_body_rgb as rgb
from sofa_check import archive
from source import Bundle


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.heads = heads.HeadEvidenceTests()
        cls.heads.setUpClass()
        cls.addClassCleanup(cls.heads.doClassCleanups)

    def setUp(self):
        self.camera = self.heads.camera
        self.frames = [mask.PreparedFrame(cue, ("test-session", index, index), f"{index:064x}",
                                          self.heads.pose(), "supported_pose")
                       for index, cue in enumerate(mask.POSE_CUES)]
        # Independent physical points on the full head and upper neck, with
        # head-side points beyond the face anchors and a sloping neck.
        points = [[(0.02, 1.96, 1.58), (-0.13, 1.97, 1.48), (0.13, 2.01, 1.48), (0.01, 2.12, 1.47)],
                  [(-0.10, 2.03, 1.22), (-0.19, 2.06, 1.22), (-0.08, 2.14, 1.25)]]
        geom = self.heads.geom
        to_depth = geom.inverse(*self.camera.room_from_left)
        regions = []
        for samples in points:
            xs, ys = zip(*(geom.project(geom.apply(*to_depth, point), self.camera.rig.left_k) for point in samples))
            regions.append([math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))])
        self.annotations = {"schema": mask.ANNOTATION_SCHEMA,
                            "frames": [dict(key=list(frame.key), sha256=frame.sha256, status="visible",
                                            head_bounds_px=regions[0].copy(), neck_bounds_px=regions[1].copy())
                                       for frame in self.frames]}
        self.separations = {frame.key: 0.055 for frame in self.frames}

    def score(self, *, separations=True):
        return mask.score(self.frames, self.annotations, self.camera, profile=heads.PROFILE,
                          separations=self.separations if separations else None)

    def test_same_frame_head_and_neck_are_covered_for_each_cue(self):
        result = self.score()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["basis"], "measured_separation")
        self.assertEqual(result["sampled_frames"], len(mask.POSE_CUES))
        self.assertTrue(all(tally["covered"] == 1 for tally in result["counts_by_cue"].values()))
        self.assertNotIn(self.frames[0].key[0], json.dumps(result))
        result = self.score(separations=False)
        self.assertEqual(result["basis"], "candidate_separation")
        self.assertEqual(result["timing_unavailable_frames"], len(mask.POSE_CUES))

    def test_upper_neck_must_fit_even_when_head_is_covered(self):
        self.annotations["frames"][1]["neck_bounds_px"][3] = 395
        result = self.score()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["counts_by_cue"]["turn-left"]["upper_neck_undercovered"], 1)
        self.assertNotIn("head_undercovered", result["counts_by_cue"]["turn-left"])

    def test_incomplete_visible_and_unobservable_are_different(self):
        self.frames[0] = replace(self.frames[0], pose=None, status="no_detection")
        result = self.score()
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["counts_by_cue"]["book"], {"visible_withheld": 1, "no_detection": 1})
        annotation = self.annotations["frames"][1]
        annotation.update(status="unobservable", head_bounds_px=None, neck_bounds_px=None)
        result = self.score()
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["counts_by_cue"]["turn-left"]["unsafe_mask"], 1)
        self.frames[1] = replace(self.frames[1], pose=None, status="unsupported_pose")
        self.assertEqual(self.score()["counts_by_cue"]["turn-left"]["unobservable_withheld"], 1)

    def cut_session(self, hips):
        """Frames with depth: an empty room at 4 m, then the same head and neck points the regions were drawn from,
        nearer than the room, on each pose cue. The face is gone from every pose, so only the cut can cover."""
        geom, camera = self.heads.geom, self.camera
        to_depth = geom.inverse(*camera.room_from_left)
        background = np.full((400, 640), 4000, np.uint16)
        person = background.copy()
        centres = [(0.0, 2.0, 1.47), (-0.11, 2.06, 1.24)]
        for centre, radius in zip(centres, (0.13, 0.07)):
            for dx in np.linspace(-radius, radius, 15):
                for dy in np.linspace(-radius, radius, 15):
                    for dz in np.linspace(-radius, radius, 15):
                        x, y, z = geom.apply(*to_depth, (centre[0] + dx, centre[1] + dy, centre[2] + dz))
                        col, row = geom.project((x, y, z), camera.rig.left_k)
                        if 0 <= row < 400 and 0 <= col < 640:
                            person[int(row), int(col)] = min(person[int(row), int(col)], round(z * 1000))
        frames = []
        for frame in self.frames:
            pose = self.heads.with_hips(self.heads.pose(), hips)
            for joint in pose["joints_room"]:
                if joint["j"] < 5:
                    joint.update(status="unsupported", lift="none")
            frames.append(replace(frame, pose=pose, depth_mm=person))
        frames.append(mask.PreparedFrame(mask.EMPTY_CUE, ("test-session", 99, 99), f"{99:064x}", None, "no_detection",
                                         background))
        self.annotations["frames"].append(dict(key=["test-session", 99, 99], sha256=f"{99:064x}", status="empty",
                                               head_bounds_px=None, neck_bounds_px=None))
        return frames

    def test_the_shoulder_line_cut_covers_a_faceless_head_and_a_wrong_cut_leaks(self):
        profile = replace(heads.PROFILE, torso_cut_m=0.08)
        frames = self.cut_session(((-0.15, 2.1, 0.70), (0.05, 2.1, 0.70)))
        result = mask.score(frames, self.annotations, self.camera, profile=profile, separations=self.separations)
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(all(tally.get("by_cut") == tally.get("covered") == 1
                            for cue, tally in result["counts_by_cue"].items() if cue != mask.EMPTY_CUE))
        self.assertEqual(result["counts_by_cue"][mask.EMPTY_CUE], {"empty_withheld": 1})
        # Hips placed above the head would flip the axis: the face is then on the hips' side, so the cut is refused
        # and every frame is withheld, never covered.
        self.setUp()
        frames = self.cut_session(((-0.15, 2.1, 1.90), (0.05, 2.1, 1.90)))
        result = mask.score(frames, self.annotations, self.camera, profile=profile, separations=self.separations)
        self.assertEqual(result["verdict"], "incomplete")
        self.assertFalse(any(tally.get("covered") for cue, tally in result["counts_by_cue"].items()))
        # A cut that keeps the marked region is counted as a leak.
        frame = frames[0]
        region = self.annotations["frames"][0]["head_bounds_px"]
        background = mask.empty_background(frames)
        everything = ((0.0, 0.0), (0.0, 1.0), 0.0, 3.0)    # the head side is all of the image below row 0
        nothing = ((0.0, 0.0), (0.0, -1.0), 0.0, 3.0)      # the head side is above the image: nothing removed
        self.assertEqual(mask.cut_leaks(frame, region, None, everything, background, self.camera), 0)
        self.assertGreater(mask.cut_leaks(frame, region, None, nothing, background, self.camera), 0)
        low_ceiling = ((0.0, 0.0), (0.0, -1.0), 0.0, -0.5)   # nothing removed by the line, everything by the ceiling
        self.assertEqual(mask.cut_leaks(frame, region, None, low_ceiling, background, self.camera), 0)
        # Without an empty room to compare with, the cut is never counted as covering.
        self.setUp()
        frames = self.cut_session(((-0.15, 2.1, 0.70), (0.05, 2.1, 0.70)))[:-1]
        self.annotations["frames"].pop()
        result = mask.score(frames, self.annotations, self.camera, profile=profile, separations=self.separations)
        self.assertEqual(result["verdict"], "incomplete")

    def test_wrong_bundle_missing_duplicate_and_bad_regions_are_rejected(self):
        original = self.annotations["frames"][0]
        for bad in (dict(original, sha256="other"), dict(original, key=["restarted", 0, 0]),
                    dict(original, key=[original["key"][0], False, 0]), dict(original, neck_bounds_px=[2, 4, 5, 401])):
            with self.subTest(annotation=bad), self.assertRaises(ValueError):
                self.annotations["frames"][0] = bad
                self.score()
        self.annotations["frames"][0] = original
        self.annotations["frames"].append(original)
        with self.assertRaises(ValueError):
            self.score()
        self.annotations["frames"] = self.annotations["frames"][1:-1]
        with self.assertRaises(ValueError):
            self.score()

    def test_pairing_bound_above_profile_withholds_the_mask(self):
        self.separations[self.frames[-1].key] = heads.PROFILE.max_separation_s + 0.001
        result = self.score()
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["counts_by_cue"][self.frames[-1].cue]["head_evidence_withheld"], 1)


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        cls.root = Path(scratch.name)
        cls.fixture = rgb.Fixture(cls.root)
        cls.addClassCleanup(cls.fixture.link.close)

    def bundle(self, *, luma=110, depth=None, sequence=17, rgb_distortion=None, rgb_k=None, **changes):
        fixture = self.fixture
        meta = fixture.synth.metadata("fixture-camera", sequence, 5500.0, 5499.985,
                                      {"rgb": rgb_k or fixture.synth.RGB_K, "depth": fixture.synth.LEFT_K})
        meta["frames"]["rgb"]["source_distortion_coefficients"] = (
            fixture.synth.RGB_DISTORTION if rgb_distortion is None else rgb_distortion)
        meta["depth_correction"] = rgb.CORRECTION
        meta.update(changes)
        data = fixture.synth.bundle(meta, fixture.depth if depth is None else depth)
        out = io.BytesIO()
        nv12 = bytes([luma]) * (640 * 360) + bytes([128]) * (640 * 180)
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                body = fixture.synth.npy("|u1", (540, 640), nv12) if name == "rgb_nv12.npy" else source.read(name)
                target.writestr(name, body)
        return out.getvalue()

    def test_blind_page_contains_native_left_pixels_and_rejects_wrong_stereo_pair(self):
        session = self.root / "private-session"
        session.mkdir()
        file = session / "frames" / "0001.npz"
        file.parent.mkdir()
        data = self.bundle()
        file.write_bytes(data)
        rows = [{"file": "frames/0001.npz", "key": ["fixture-camera", 17, 17], "cue": "book",
                 "sha256": hashlib.sha256(data).hexdigest()}]
        html = mask.annotation_html(session, rows, tools_root=self.root)
        self.assertIn(rows[0]["sha256"], html)
        self.assertIn(mask.ANNOTATION_SCHEMA, html)
        self.assertIn("width=\"640\" height=\"400\"", html)
        self.assertIn("default-src 'none'", html)
        del rows[0]["sha256"]
        meta, _ = self.fixture.tools.takes.read_frame(data)
        frames = meta["raw"]["frames"]
        frames["left"]["sequence"] += 1
        file.write_bytes(self.bundle(frames=frames))
        with self.assertRaisesRegex(ValueError, "unusable rectified-left"):
            mask.annotation_html(session, rows, tools_root=self.root)

    def test_native_bundle_replay_runs_the_real_lift_and_handles_withholding(self):
        fixture = self.fixture
        tools, camera = fixture.tools, fixture.perceiver.camera
        observe = archive(self.root, "observe")
        settings = tools.observe.Settings(tools.observe_method.load(observe / "tools" / "den-observe" / "METHOD.md"))
        settings.max_age_s = mask.FRESH_S
        row = fixture.rows.row(5500.0, rgb_seq=17, pixels=fixture.pixels)
        detector = SimpleNamespace(np=np, check=tools.takes.check, pose=SimpleNamespace(manifest={"model_version": "fixture"}),
                                   decode=lambda _, w, h: np.zeros((h, w, 3), np.uint8),
                                   run=lambda *_: (None, None, 0.02), row_of=lambda *_, **kw: row)

        def replay(data):
            meta, depth = tools.takes.read_frame(data)
            bundle = Bundle(data, "fixture-camera", 17, meta["t_host"], 0, 0)
            return mask.pose_for_mask(bundle, meta, depth, detector, tools, camera, settings, fixture.provider)

        pose, status = replay(self.bundle())
        self.assertEqual(status, "supported_pose")
        self.assertEqual(pose["joints_room"][0]["lift"], "stereo")
        self.assertEqual(replay(self.bundle(luma=3)), (None, "unusable_rgb"))
        self.assertEqual(replay(self.bundle(depth=bytes(640 * 400 * 2))), (None, "unusable_depth"))
        row["detections"].append({"id": "rival", "class": "person", "score": 0.55,
                                  "box_xyxy": [8.0, 12.0, 90.0, 65.0], "keypoints": None})
        self.assertEqual(replay(self.bundle()), (None, "multiple_people"))
        row["detections"].pop()
        row["detections"][0]["box_xyxy"][0] = 0   # an edge-touching box is partial: head_evidence decides the head
        pose, status = replay(self.bundle())
        self.assertEqual((pose["visibility"], status), ("partial", "supported_pose"))
        row["detections"].clear()
        self.assertEqual(replay(self.bundle()), (None, "no_detection"))

    def test_camera_relative_trial_replays_without_a_room_model(self):
        fixture = self.fixture
        identity, rgb_offset = fixture.synth.RGB_FROM_LEFT
        baseline = fixture.synth.BASELINE_M

        def extrinsics(target, offset):
            return {"extrinsics": {"toCameraSocket": target, "rotationMatrix": identity,
                                   "translation": {"x": offset * 100, "y": 0, "z": 0}}}

        eeprom = {"cameraData": [[1, extrinsics(2, -baseline)], [2, extrinsics(0, baseline + rgb_offset[0])]],
                  "stereoRectificationData": {"leftCameraSocket": 1, "rightCameraSocket": 2,
                                              "rectifiedRotationLeft": identity}}
        profile = replace(fixture.provider.profile, transform_id=mask.CAMERA_TRIAL_TRANSFORM)
        detector = SimpleNamespace(np=np, check=fixture.tools.takes.check,
                                   pose=SimpleNamespace(manifest={"model_version": "fixture"}),
                                   decode=lambda _, w, h: np.zeros((h, w, 3), np.uint8),
                                   run=lambda *_: (None, None, 0.02),
                                   row_of=lambda *_, **kw: fixture.rows.row(5500.0, rgb_seq=kw["rgb_seq"],
                                                                           pixels=fixture.pixels))
        with tempfile.TemporaryDirectory(dir=self.root) as scratch:
            session = Path(scratch)
            (session / "frames").mkdir()
            calibration = session / "camera-calibration.json"
            calibration.write_text(json.dumps({"eeprom": eeprom}), encoding="utf-8")

            def save(sequence, data):
                name = f"frames/{sequence}.npz"
                (session / name).write_bytes(data)
                return {"file": name, "key": ["fixture-camera", sequence, sequence], "cue": "book"}

            def replay(rows):
                return mask.prepare_session(session, rows, tools_root=self.root, profile=profile)

            first = save(17, self.bundle())
            with patch.object(mask.detect, "guard"), patch.object(mask.detect, "contain"), \
                    patch.object(mask.detect, "unlocked", return_value=[]), \
                    patch.object(mask.detect, "Detector", return_value=detector):
                (frame,), camera = replay([first])
                self.assertEqual(frame.status, "supported_pose")
                self.assertEqual(frame.pose["joints_room"][0]["lift"], "stereo")
                self.assertEqual(camera.transform_id, mask.CAMERA_TRIAL_TRANSFORM)

                def exclusion(pose, cam):
                    binding = {"transform_id": cam.transform_id}
                    evidence = mask.head_evidence(pose, cam, frame_key=frame.key, binding=binding,
                                                  profile=profile.head, separation_s=0.055)
                    return mask.head_exclusion(evidence, frame_key=frame.key, binding=binding)

                (registered,), registered_camera = mask.prepare_session(
                    session, [first], tools_root=self.root, profile=fixture.provider.profile,
                    room_model=fixture.model, registration=fixture.receipt)
                trial_mask = exclusion(frame.pose, camera)
                self.assertIsNotNone(trial_mask)
                self.assertEqual(trial_mask, exclusion(registered.pose, registered_camera))

                changed_k = dict(fixture.synth.RGB_K, cx=fixture.synth.RGB_K["cx"] + 4)
                second = save(18, self.bundle(sequence=18, rgb_k=changed_k))
                with self.assertRaisesRegex(ValueError, "does not match"):
                    replay([first, second])
                second = save(18, self.bundle(sequence=18, rgb_distortion=[0.0] * 8))
                with self.assertRaisesRegex(ValueError, "RGB geometry changed"):
                    replay([first, second])
                calibration.write_text('{"eeprom":{}}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "camera_calibration_invalid"):
                    replay([first])


if __name__ == "__main__":
    unittest.main()
