"""Pure injected-clock qualification tests, no service or network operations."""
import unittest

from live_service_clock import QualifiedServiceClock, ServiceClockUnqualified


def policy(**changes):
    return {'schema': 'home-live-service-clock-policy-1', 'version': 1,
        'qualification_ref': {'id': 'authored-external-clock-qualification', 'digest': 'a' * 64},
        'valid_until': 200, 'uncertainty_s': .01, 'max_anchor_divergence_s': .1,
        'max_round_trip_s': .05, 'max_mapping_age_s': 1, **changes}


class QualifiedServiceClockTests(unittest.TestCase):
    def setUp(self):
        self.utc, self.mono = 100., 10.

    def clock(self, **changes):
        return QualifiedServiceClock(policy(**changes), anchor_epoch='authored-anchor-1',
                                     wall=lambda: self.utc, monotonic=lambda: self.mono)

    def test_missing_or_malformed_qualification_never_creates_clock(self):
        for value in (None, {}, {**policy(), 'qualification_ref': None},
                      {**policy(), 'uncertainty_s': -1}, {**policy(), 'max_mapping_age_s': 0}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                QualifiedServiceClock(value, anchor_epoch='authored-1')

    def test_ordinary_progress_preserves_external_qualification_and_original_anchor(self):
        clock = self.clock()
        self.utc += 2
        self.mono += 2
        sample = clock.sample()
        self.assertTrue(sample['qualified'])
        self.assertEqual(sample['utc_at'], 102)
        self.assertEqual(sample['uncertainty_s'], .01)
        self.assertEqual(sample['valid_until'], 103)
        self.assertEqual(sample['anchor_epoch'], 'authored-anchor-1')
        self.assertEqual(clock.anchor_utc, 100)

    def test_clock_step_latches_even_after_wall_is_put_back(self):
        for step in (-2, 2):
            self.utc, self.mono = 100., 10.
            clock = self.clock()
            self.utc += step
            sample = clock.sample()
            self.assertFalse(sample['qualified'])
            self.assertEqual(sample['reason'], 'service_clock_anchor_diverged')
            self.utc -= step
            self.assertFalse(clock.sample()['qualified'])
            with self.assertRaises(ServiceClockUnqualified):
                clock()

    def test_unaccounted_suspend_and_backward_monotonic_latch(self):
        for elapsed in (0, -1):
            self.utc, self.mono = 100., 10.
            clock = self.clock()
            self.utc += 2
            self.mono += elapsed
            self.assertFalse(clock.sample()['qualified'])

    def test_expiry_is_exclusive_and_cannot_be_recovered_by_wall_regression(self):
        clock = self.clock()
        self.utc, self.mono = 199.99, 109.99
        self.assertEqual(clock.sample()['reason'], 'service_clock_qualification_expired')
        self.utc, self.mono = 100., 10.
        self.assertFalse(clock.sample()['qualified'])

    def test_initial_expired_qualification_refuses(self):
        with self.assertRaises(ServiceClockUnqualified):
            self.clock(valid_until=100.01)

    def test_small_divergence_consumes_uncertainty_instead_of_extending_lease(self):
        clock = self.clock()
        self.utc += .05
        sample = clock.sample()
        self.assertTrue(sample['qualified'])
        self.assertEqual(sample['utc_at'], 100.)
        self.assertAlmostEqual(sample['uncertainty_s'], .06)
        self.assertEqual(sample['valid_until'], 101.)

    def test_local_clock_read_round_trip_is_bounded_and_accounted(self):
        values = iter((10., 10.02, 11., 11.04))
        clock = QualifiedServiceClock(policy(), anchor_epoch='authored-anchor-1',
            wall=lambda: self.utc, monotonic=lambda: next(values))
        self.utc = 101.01
        sample = clock.sample()
        self.assertTrue(sample['qualified'])
        self.assertAlmostEqual(sample['uncertainty_s'], .03)
        values = iter((10., 10., 11., 12.))
        clock = QualifiedServiceClock(policy(), anchor_epoch='authored-anchor-2',
            wall=lambda: 100., monotonic=lambda: next(values))
        self.assertEqual(clock.sample()['reason'], 'service_clock_read_deadline_exceeded')

    def test_constant_absolute_offset_requires_the_external_qualification(self):
        # No local two-clock check can prove an absolute UTC offset. A supplied
        # authored qualification asserts this wall value; it is not measured.
        self.utc = 98.
        clock = self.clock()
        self.assertEqual(clock(), 98.)
        self.assertEqual(clock.sample()['qualification_ref'], policy()['qualification_ref'])


if __name__ == '__main__':
    unittest.main()
