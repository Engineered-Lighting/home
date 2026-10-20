import unittest
from unittest.mock import patch

from home_shared.media import EncodedGop, GopRing, MAX_BYTES


def gop(start, end=None, *, session='camera-1', size=4):
    return EncodedGop(session, float(start), float(start + 1 if end is None else end), b'x' * size)


def select(ring, anchor=10, now=25, **kwargs):
    parameters = dict(session='camera-1', anchor=anchor, event_received=anchor + 5,
                      now=now, mapping_validation_id='fixture-only', mapping_uncertainty_seconds=0)
    parameters.update(kwargs)
    return ring.select(**parameters)


class GopRingTests(unittest.TestCase):
    def test_complete_clip_uses_first_detection_anchor_not_arrival_debounce(self):
        ring = GopRing()
        for index in range(25):
            ring.append(gop(index))
        clip = select(ring)
        self.assertTrue(clip.complete)
        self.assertEqual((clip.actual_start, clip.actual_end), (5, 20))
        self.assertEqual(len(clip.joined_annex_b()), 60)
        self.assertEqual((clip.requested_start, clip.requested_end), (5, 20))

    def test_whole_gop_eviction_on_time_or_bytes(self):
        ring = GopRing(max_seconds=3, max_bytes=10)
        for index in range(5):
            ring.append(gop(index))
        self.assertEqual(ring.byte_size, 8)
        self.assertEqual(ring.duration, 2)
        duration_ring = GopRing(max_seconds=3)
        for index in range(10):
            duration_ring.append(gop(index))
        self.assertEqual(duration_ring.duration, 3)
        self.assertEqual(duration_ring.byte_size, 12)

    def test_tiny_gops_cannot_escape_memory_bound_through_metadata(self):
        with patch('home_shared.media.MAX_BYTES', 4096):
            ring = GopRing(max_bytes=4096)
            for index in range(100):
                ring.append(gop(index / 1000, (index + 1) / 1000, size=1))
                self.assertLessEqual(ring.resident_bytes, 4096)
            self.assertLess(ring.byte_size, 100)

    def test_missing_pre_roll_post_roll_and_gap_are_labelled(self):
        ring = GopRing()
        for index in range(8, 16):
            ring.append(gop(index))
        clip = select(ring, now=16)
        self.assertFalse(clip.complete)
        self.assertIn('pre_roll_missing', clip.partial_reasons)
        self.assertIn('post_roll_pending', clip.partial_reasons)
        self.assertIn('post_roll_missing', clip.partial_reasons)
        ring.append(gop(18))
        clip = select(ring)
        self.assertIn('source_gap', clip.partial_reasons)
        self.assertEqual(clip.actual_end, 16)

    def test_clock_uncertainty_never_claims_exact_boundary_coverage(self):
        ring = GopRing()
        for index in range(25):
            ring.append(gop(index))
        clip = select(ring, mapping_uncertainty_seconds=.1)
        self.assertFalse(clip.complete)
        self.assertIn('pre_roll_missing', clip.partial_reasons)
        self.assertIn('post_roll_missing', clip.partial_reasons)

    def test_wrong_or_reconnected_session_does_not_reuse_pre_roll(self):
        ring = GopRing()
        for index in range(25):
            ring.append(gop(index))
        ring.append(gop(26, session='camera-2'))
        self.assertEqual(ring.byte_size, 4)
        clip = select(ring)
        self.assertEqual(clip.gops, ())
        self.assertIn('no_decodable_coverage', clip.partial_reasons)

    def test_rejects_unknown_pts_late_event_and_out_of_order_gop(self):
        ring = GopRing()
        ring.append(gop(2))
        with self.assertRaises(ValueError):
            ring.append(gop(1))
        for parameters in ({'event_received': 21}, {'mapping_validation_id': ''},
                           {'mapping_uncertainty_seconds': 1.1}, {'now': 9}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                select(ring, **parameters)

    def test_ring_and_clip_limits_are_hard_bounds(self):
        with self.assertRaises(ValueError):
            GopRing(max_seconds=31)
        with self.assertRaises(ValueError):
            GopRing(max_bytes=MAX_BYTES + 1)
        ring = GopRing(max_bytes=8)
        ring.append(gop(0))
        with self.assertRaises(ValueError):
            ring.append(gop(1, size=9))
        self.assertEqual(ring.byte_size, 0)
        ring = GopRing()
        ring.append(gop(0, 25))
        clip = select(ring)
        self.assertEqual(clip.gops, ())
        self.assertIn('duration_limit', clip.partial_reasons)

    def test_gops_are_immutable_decodable_video_primitives(self):
        with self.assertRaises(ValueError):
            EncodedGop('s', 0, 1, b'frame', independently_decodable=False)
        with self.assertRaises(ValueError):
            EncodedGop('s', 0, 1, bytearray(b'frame'))
        with self.assertRaises(ValueError):
            EncodedGop('s', 0, float('nan'), b'frame')


if __name__ == '__main__':
    unittest.main()
