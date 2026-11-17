"""Pure specialist crop, binding and registered lift regressions using synthetic inputs only."""
import copy
import math
import unittest
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

from contracts import FrameRef, PlaneRef
import specialist_spatial as S

PROFILE = "a" * 64
FRAME = FrameRef("run", 2, 17, "camera-session", "rgb", PlaneRef(41, 8.0, 100.0, 640, 360), None,
                 PlaneRef(23, 8.0, 100.0, 640, 400), "b" * 64, "camera@registered-1", "scan-1",
                 100.0, 100.05, 2000.0)
BINDING = S.SpecialistBinding(FRAME, "person-1", 3, 0, "box-41", ("observation-41",), PROFILE,
                              "request-1", "engine-1", 101.5, 0.75)
GEOMETRY = S.CropAffine((640, 360), (640, 360), (640, 360), (0, 0, 640, 360), ((1, 0, 0), (0, 1, 0)))
LAYOUT = S.Coco17Layout("coco17_identity_v1", PROFILE)


class Adapter(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(max_gap_s=0.067, score_min=0.4, pixel_sigma_px=4.0)
        self.camera = SimpleNamespace(transform_id=FRAME.transform_id, rig=SimpleNamespace(rgb_size=(640, 360)))
        self.depth = SimpleNamespace(width=640, height=400, sequence=23, t_host=100.0)
        self.calls = []

    def lift(self, camera, settings, depth, depth_ok, index, point, size):
        self.calls.append((camera, settings, depth, depth_ok, index, point, size))
        return {"j": index, "xyz": [1.0, 2.0, 3.0], "cov": [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 0.03]],
                "status": "supported" if depth_ok else "uncertain", "lift": "stereo" if depth_ok else "plane:1.55"}, None

    def result(self, points=None, **kwargs):
        return S.SpecialistResult(kwargs.get("binding", BINDING), kwargs.get("crop", GEOMETRY),
                                  kwargs.get("layout_token", LAYOUT.token),
                                  points if points is not None else (S.ModelLandmark(0, (20, 30), ((1, 0), (0, 1)), 0.9),))

    def adapt(self, result=None, **kwargs):
        arguments = dict(expected=BINDING, geometry=GEOMETRY, layout=LAYOUT, now_mono=100.1,
                         camera=self.camera, depth=self.depth, settings=self.settings, lift_joint=self.lift)
        arguments.update(kwargs)
        return S.adapt(self.result() if result is None else result, **arguments)

    def test_rotation_nonuniform_scale_shear_offset_and_covariance(self):
        geometry = S.CropAffine((640, 360), (640, 360), (192, 256), (100, 50, 500, 300),
                                ((0, -2, 410), (3, 0.5, 40)))
        point = S.ModelLandmark(5, (20, 30), ((4, 1), (1, 9)), 0.9)
        result = self.adapt(self.result((point,), crop=geometry), geometry=geometry)
        self.assertIsNone(result.reason)
        mapped = result.landmarks[5]
        self.assertEqual(mapped.pixel, (350, 115))
        self.assertEqual(mapped.covariance, ((36, -15), (-15, 41.25)))
        expected_sigma = math.sqrt((77.25 + math.hypot(36 - 41.25, -30)) / 2)
        self.assertAlmostEqual(self.calls[0][1].pixel_sigma_px, expected_sigma)
        self.assertEqual((mapped.status, mapped.lift, mapped.provenance), ("supported", "stereo", "measured"))
        self.assertEqual((self.calls[0][5], self.calls[0][6]), ((350, 115, 0.9), (640, 360)))

    def test_floor_is_native_psd_minimal_diagonal_shift_with_cross_term_retained(self):
        covariance = ((16, 0), (0, 16))
        affine = ((0.25, 0.1, 0), (0, 0.5, 0))
        native = S.native_covariance(covariance, affine)
        self.assertAlmostEqual(native[0][1], 0.8)
        minimum = (native[0][0] + native[1][1] - math.hypot(native[0][0] - native[1][1], 2 * native[0][1])) / 2
        self.assertAlmostEqual(minimum, 16)
        self.assertAlmostEqual(native[0][0] - 1.16, native[1][1] - 4.0)
        identity = S.native_covariance(((0.01, 0), (0, 0.01)), ((1, 0, 0), (0, 1, 0)))
        self.assertEqual(identity, ((16, 0), (0, 16)))
        settings = replace_namespace(self.settings, pixel_sigma_px=8.0)
        output = self.adapt(settings=settings)
        self.assertEqual(output.landmarks[0].covariance, ((64, 0), (0, 64)))
        self.assertGreaterEqual(self.calls[-1][1].pixel_sigma_px, 8.0)

    def test_crop_origin_is_in_affine_and_letterbox_padding_is_rejected(self):
        geometry = S.CropAffine((640, 360), (640, 360), (192, 256), (100, 50, 356, 306),
                                ((2, 0, 100), (0, 2, -14)))
        points = (S.ModelLandmark(0, (5, 40), ((1, 0), (0, 1)), 0.9),
                  S.ModelLandmark(1, (5, 10), ((1, 0), (0, 1)), 0.9),
                  S.ModelLandmark(2, (-0.1, 40), ((1, 0), (0, 1)), 0.9))
        result = self.adapt(self.result(points, crop=geometry), geometry=geometry)
        self.assertEqual(result.landmarks[0].pixel, (110, 66), "crop origin is added once by the affine")
        self.assertEqual(result.landmarks[1].reason, "outside_crop")
        self.assertEqual(result.landmarks[2].reason, "outside_model")
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(result.landmarks[1].xyz_room)
        different = replace(geometry, source_size=(1280, 720))
        self.assertEqual(self.adapt(self.result(crop=different), geometry=different).reason, "different_source_size")
        with self.assertRaisesRegex(S.AdapterRefused, "geometry_invalid"):
            replace(geometry, crop_xyxy=(-1, 0, 100, 100))
        with self.assertRaisesRegex(S.AdapterRefused, "geometry_invalid"):
            replace(geometry, source_from_model=((1, 2, 0), (2, 4, 0)))

    def test_exact_owner_frame_registration_and_request_are_required(self):
        changes = (dict(track_id="other-person"), dict(association_revision=4), dict(detection_index=1),
                   dict(detection_id="other-box"), dict(observation_ids=("other-observation",)),
                   dict(profile_sha256="c" * 64), dict(request_id="other-request"), dict(engine_epoch="other-engine"),
                   dict(frame=replace(FRAME, camera_session="other-camera")),
                   dict(frame=replace(FRAME, frame_id=18)), dict(frame=replace(FRAME, camera_epoch=3)),
                   dict(frame=replace(FRAME, transform_id="other-transform")),
                   dict(frame=replace(FRAME, depth_calibration_sha256="d" * 64)),
                   dict(frame=replace(FRAME, scan_revision="other-scan")))
        for change in changes:
            with self.subTest(change=tuple(change)):
                result = self.adapt(self.result(binding=replace(BINDING, **change)))
                self.assertEqual(result.reason, "binding_mismatch")
                self.assertEqual(result.landmarks, ())
        geometry = replace(GEOMETRY, source_from_model=((1, 0, 1), (0, 1, 0)))
        self.assertEqual(self.adapt(self.result(crop=geometry)).reason, "geometry_mismatch")
        self.assertEqual(self.adapt(camera=replace_namespace(self.camera, transform_id="wrong")).reason, "camera_mismatch")
        self.assertEqual(self.adapt(depth=replace_namespace(self.depth, sequence=24)).reason, "depth_mismatch")

    def test_deadline_is_capture_anchored_and_unsafe_association_abstains(self):
        result = self.adapt(now_mono=100.749)
        self.assertEqual(result.valid_until_mono, 100.75)
        self.assertEqual(self.adapt(now_mono=100.75).reason, "expired")
        self.assertEqual(self.adapt(now_mono=99.9).reason, "expired")
        earlier = replace(BINDING, parent_valid_until_mono=100.4)
        self.assertEqual(self.adapt(self.result(binding=earlier), expected=earlier).valid_until_mono, 100.4)
        unsafe = replace(BINDING, association_status="ambiguous")
        self.assertEqual(self.adapt(self.result(binding=unsafe), expected=unsafe).reason, "association_unsafe")
        for change in (dict(deadline_s=1.501), dict(parent_valid_until_mono=101.501), dict(deadline_s=10.0)):
            with self.assertRaisesRegex(S.AdapterRefused, "binding_invalid"):
                replace(BINDING, **change)

    def test_nonnegative_timing_and_arrival_estimates_have_explicit_boundaries(self):
        frames = (replace(FRAME, capture_mono=-0.001), replace(FRAME, arrival_mono=-1),
                  replace(FRAME, rgb=replace(FRAME.rgb, host_s=-1)),
                  replace(FRAME, rgb=replace(FRAME.rgb, device_s=-1)),
                  replace(FRAME, depth=replace(FRAME.depth, host_s=-1)))
        for frame in frames:
            with self.assertRaisesRegex(S.AdapterRefused, "binding_invalid"):
                replace(BINDING, frame=frame)
        earlier_arrival = replace(BINDING, frame=replace(FRAME, arrival_mono=99.999))
        output = self.adapt(self.result(binding=earlier_arrival), expected=earlier_arrival)
        self.assertIsNone(output.reason, "capture estimation does not establish a new arrival-order assumption")
        self.assertEqual(self.adapt(now_mono=-0.001).reason, "expired")

    def test_wholebody_layout_maps_only_coco17_and_missing_weak_points_stay_unsupported(self):
        layout = S.Coco17Layout("coco_wholebody133_first17_v1", PROFILE)
        points = tuple(S.ModelLandmark(i, (20, 30), ((1, 0), (0, 1)), 0.9) for i in range(133))
        output = self.adapt(self.result(points, layout_token=layout.token), layout=layout)
        self.assertEqual(len(output.landmarks), 17)
        self.assertEqual(len(self.calls), 17)
        self.assertEqual([c[4] for c in self.calls], list(range(17)))
        self.calls.clear()
        points = (S.ModelLandmark(5, (20, 30), ((1, 0), (0, 1)), 0.9),
                  S.ModelLandmark(16, (25, 35), ((1, 0), (0, 1)), 0.2))
        output = self.adapt(self.result(points))
        self.assertEqual(output.landmarks[0].reason, "missing_landmark")
        self.assertEqual(output.landmarks[16].reason, "low_score")
        self.assertEqual(output.landmarks[5].provenance, "measured")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaisesRegex(S.AdapterRefused, "layout_invalid"):
            S.Coco17Layout("face_landmarker478", PROFILE)
        self.assertEqual(self.adapt(self.result(layout_token="face_landmarker478")).reason, "layout_mismatch")
        self.assertEqual(self.adapt(self.result((points[0], points[0]))).reason, "landmark_indices")
        with self.assertRaisesRegex(S.AdapterRefused, "landmark_count"):
            self.result((points[0],) * 134)

    def test_stereo_only_is_measured_and_plane_is_explicitly_inferred(self):
        measured = self.adapt().landmarks[0]
        self.assertEqual((measured.provenance, measured.lift), ("measured", "stereo"))
        inferred = self.adapt(depth=None).landmarks[0]
        self.assertEqual((inferred.provenance, inferred.status, inferred.lift), ("inferred", "uncertain", "plane:1.55"))
        frame = replace(FRAME, depth=replace(FRAME.depth, host_s=99.8))
        binding = replace(BINDING, frame=frame)
        depth = replace_namespace(self.depth, t_host=99.8)
        inferred = self.adapt(self.result(binding=binding), expected=binding, depth=depth).landmarks[0]
        self.assertEqual(inferred.provenance, "inferred")
        broken = lambda *args: ({"j": 0, "xyz": [0, 0, 1], "cov": [[1, 2, 0], [2, 1, 0], [0, 0, 1]],
                                "status": "supported", "lift": "plane:1.55"}, None)
        self.assertEqual(self.adapt(lift_joint=broken).landmarks[0].reason, "lift_invalid")

    def test_source_evidence_is_immutable_and_settings_inputs_are_not_changed(self):
        point = S.ModelLandmark(0, [20, 30], [[1, 0.25], [0.25, 1]], 0.9)
        result = self.result([point])
        original = copy.deepcopy(result)
        settings = copy.deepcopy(self.settings)
        output = self.adapt(result)
        self.assertEqual(result, original)
        self.assertEqual(vars(self.settings), vars(settings))
        self.assertIsNot(self.calls[0][1], self.settings)
        self.assertGreaterEqual(self.calls[0][1].pixel_sigma_px, 4.0)
        with self.assertRaises(FrozenInstanceError):
            output.landmarks[0].score = 0.1
        with self.assertRaises(TypeError):
            result.crop.source_from_model[0][0] = 5
        with self.assertRaises(FrozenInstanceError):
            result.binding.track_id = "other"

    def test_per_point_invalid_covariance_and_lift_exceptions_have_fixed_reasons(self):
        point = S.ModelLandmark(0, (20, 30), ((1, 2), (2, 1)), 0.9)
        output = self.adapt(self.result((point,)))
        self.assertEqual(output.landmarks[0].reason, "covariance_invalid")
        self.assertEqual(self.calls, [])
        def failed(*args):
            raise RuntimeError("private synthetic error content")
        output = self.adapt(lift_joint=failed)
        self.assertEqual(output.landmarks[0].reason, "lift_failed")
        self.assertNotIn("private", repr(output))

    def test_missing_covariance_does_not_promote_a_joint_or_weaken_other_joints(self):
        points = (S.ModelLandmark(0, (20, 30), ((1, 0), (0, 1)), 0.45),
                  S.ModelLandmark(16, (25, 35), None, 0.9))
        output = self.adapt(self.result(points))
        self.assertEqual(output.landmarks[0].provenance, "measured", "the observer's 0.4 gate is preserved")
        self.assertEqual(output.landmarks[16].reason, "covariance_invalid")
        self.assertEqual(output.landmarks[16].provenance, "unsupported")
        self.assertIsNone(output.landmarks[16].xyz_room)
        self.assertEqual(len(self.calls), 1)

    def test_unrepresentable_covariance_floor_and_overflow_are_fixed_rejections(self):
        for covariance, affine in ((((1e150, 1e150), (1e150, 1e150)), ((1, 0, 0), (0, 1, 0))),
                                   (((1, 0), (0, 1)), ((1e308, 0, 0), (0, 1e308, 0)))):
            with self.assertRaisesRegex(S.AdapterRefused, "covariance_invalid"):
                S.native_covariance(covariance, affine)
        native = S.native_covariance(((1e12, 1e12), (1e12, 1e12)), ((1, 0, 0), (0, 1, 0)))
        self.assertGreaterEqual(S._minimum_eigenvalue(native[0][0], native[0][1], native[1][1]), 16)

    def test_malformed_protocol_shapes_are_rejected_before_recursive_freezing(self):
        nested = 0
        for _ in range(1500):
            nested = [nested]
        for ids in (("observation",) * 65, (nested,)):
            with self.assertRaisesRegex(S.AdapterRefused, "binding_invalid"):
                replace(BINDING, observation_ids=ids)
        changes = (dict(native_size=(640, 360, 1)), dict(source_size=(nested, 360)),
                   dict(crop_xyxy=(0, 0, 640, 360, 1)),
                   dict(source_from_model=((1, 0, 0), (0, 1, 0), (0, 0, 1))),
                   dict(source_from_model=((nested, 0, 0), (0, 1, 0))))
        for change in changes:
            with self.assertRaisesRegex(S.AdapterRefused, "geometry_invalid"):
                replace(GEOMETRY, **change)
        with self.assertRaisesRegex(S.AdapterRefused, "landmark_invalid"):
            S.ModelLandmark(0, (nested, 30), ((1, 0), (0, 1)), 0.9)
        with self.assertRaisesRegex(S.AdapterRefused, "landmark_invalid"):
            self.result((nested,))


def replace_namespace(value, **changes):
    return SimpleNamespace(**{**vars(value), **changes})


class RegisteredLift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import fixture_rgb
        cls.scene = fixture_rgb.Scene()
        cls.addClassCleanup(cls.scene.close)

    def test_actual_observer_uses_distorted_rgb_extrinsics_and_conservative_noise(self):
        p = self.scene.perceiver()
        bundle, body_row = self.scene.sample(100, 1)
        original_row = copy.deepcopy(body_row)
        prepared = p.prepare(bundle, 100)
        frame = replace(prepared.ref, scan_revision="synthetic-scan")
        binding = replace(BINDING, frame=frame)
        geometry = S.CropAffine((640, 360), (640, 360), (640, 720), (30, 20, 640, 360),
                                ((2, 0, 30), (0, 0.5, 20)))
        u, v = self.scene.pixels["left_shoulder"]
        point = S.ModelLandmark(5, ((u - 30) / 2, (v - 20) / 0.5), ((4, 1), (1, 4)), 0.9)
        result = S.SpecialistResult(binding, geometry, LAYOUT.token, (point,))
        settings_before = copy.deepcopy(vars(p.settings))
        adapted = S.adapt(result, expected=binding, geometry=geometry, layout=LAYOUT, now_mono=100.1,
                          camera=p.camera, depth=prepared.depth, settings=p.settings, lift_joint=p.t.observe.lift_joint)
        self.assertIsNone(adapted.reason)
        actual = adapted.landmarks[5]
        self.assertEqual(actual.provenance, "measured")
        self.assertTrue(any(p.camera.rig.rgb_d), "the synthetic fixture exercises RGB distortion")
        self.assertTrue(any(p.camera.rig.translation), "the RGB sensor is offset from rectified-left depth")
        self.assertAlmostEqual(actual.pixel[0], u)
        self.assertAlmostEqual(actual.pixel[1], v)
        local = copy.copy(p.settings)
        (xx, xy), (_, yy) = actual.covariance
        local.pixel_sigma_px = math.sqrt((xx + yy + math.hypot(xx - yy, 2 * xy)) / 2)
        direct, _ = p.t.observe.lift_joint(p.camera, local, prepared.depth, True, 5, (u, v, 0.9), (640, 360))
        self.assertEqual(actual.xyz_room, tuple(direct["xyz"]))
        self.assertEqual(actual.covariance_room, tuple(tuple(row) for row in direct["cov"]))
        self.assertEqual(vars(p.settings), settings_before)
        self.assertEqual(body_row, original_row, "the original body-17 detection row is untouched")


if __name__ == "__main__":
    unittest.main()
