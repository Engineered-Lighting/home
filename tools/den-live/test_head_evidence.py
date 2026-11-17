"""RGB head candidates through the pinned observer and current track association."""
import unittest
from unittest.mock import patch

import fixture_rgb
from contracts import Orientation3D


class HeadEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = fixture_rgb.Scene()
        cls.addClassCleanup(cls.scene.close)

    def setUp(self):
        self.p = self.scene.perceiver()
        self.stereo = patch('person_fields.head_orientation', return_value=None)
        self.stereo_head = self.stereo.start()
        self.addCleanup(self.stereo.stop)

    def advance(self, seq, *, extra=False, candidate=True, wrong_frame=False, wrong_id=False):
        at = 100.0 + seq / 5
        bundle, reply = self.scene.sample(at, seq, extra=extra)
        prepared = self.p.prepare(bundle, at)
        if candidate:
            rgb_from_left = self.p.camera.rig.rotation
            room_from_left = self.p.camera.room_from_left[0]

            def rgb_axis(room_axis):
                left = [sum(room_from_left[j][i] * room_axis[j] for j in range(3)) for i in range(3)]
                return [sum(row[i] * left[i] for i in range(3)) for row in rgb_from_left]

            key = list(prepared.ref.key)
            if wrong_frame:
                key[2] -= 1
            reply['head_candidates'] = {'candidates': [{
                'detection_index': 0,
                'detection_id': 'wrong-id' if wrong_id else reply['row']['detections'][0]['id'],
                'frame_key': key, 'transform_id': self.p.camera.transform_id,
                'forward_rgb': rgb_axis((0, 1, 0)), 'up_rgb': rgb_axis((0, 0, 1)),
                'angular_sd_deg': 25.0, 'source_joint_ids': [0, 1, 2, 3, 4],
                'basis': 'rgb_coco5_pnp_model', 'fit_rmse_px': 1.0,
            }], 'reasons': {}}
        self.p.finish(prepared, reply, {'status': 'ok'}, at + 0.04)
        return self.p.snapshot.tracks

    def confirmed(self):
        for seq in range(17):
            tracks = self.advance(seq)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].association_status, 'confident')
        return tracks[0]

    def test_current_fit_reaches_only_current_person_and_room_axes(self):
        track = self.confirmed()
        direction = track.head_orientation
        self.assertIsNotNone(direction)
        self.assertEqual(direction.basis, 'rgb_coco5_pnp_model')
        self.assertAlmostEqual(direction.as_of_mono, self.p.snapshot.frame.capture_mono)
        for actual, expected in zip(direction.forward, (0, 1, 0)):
            self.assertAlmostEqual(actual, expected, places=8)
        for actual, expected in zip(direction.up, (0, 0, 1)):
            self.assertAlmostEqual(actual, expected, places=8)
        self.assertIsNotNone(track.head_point_m)
        self.assertIsNotNone(self.p.body_input)

    def test_missing_or_wrong_frame_or_detection_never_carries_old_direction(self):
        for kwargs in ({'candidate': False}, {'wrong_frame': True}, {'wrong_id': True}):
            with self.subTest(kwargs=kwargs):
                self.p = self.scene.perceiver()
                self.confirmed()
                tracks = self.advance(17, **kwargs)
                self.assertIsNone(tracks[0].head_orientation)

    def test_unsafe_association_abstains_immediately_and_recovers_after_grace(self):
        self.confirmed()
        tracks = self.advance(17, extra=True)
        self.assertTrue(all(track.head_orientation is None for track in tracks))
        self.advance(18, extra=True)
        tracks = self.advance(19)
        self.assertTrue(all(track.head_orientation is None for track in tracks))
        tracks = self.advance(20)
        self.assertIsNotNone(tracks[0].head_orientation)

    def test_inverted_stereo_axis_is_rejected_before_rgb_fallback(self):
        self.confirmed()
        self.stereo_head.return_value = Orientation3D(
            (0, 1, 0), (0, 0, -1), 0, 0, 25, 103.4, (0, 1, 2, 3, 4), 'stereo_face_axis')
        tracks = self.advance(17)
        self.assertEqual(tracks[0].head_orientation.basis, 'rgb_coco5_pnp_model')
        tracks = self.advance(18, candidate=False)
        self.assertIsNone(tracks[0].head_orientation)


if __name__ == '__main__':
    unittest.main()
