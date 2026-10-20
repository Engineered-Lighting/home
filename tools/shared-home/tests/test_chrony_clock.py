import unittest
from dataclasses import replace
from home_shared.chrony_clock import ChronyClockConfig, sample
from home_shared.ntp_clock import _ClockPoint
from test_chrony_tracking import REPORT


class ChronyClockTests(unittest.TestCase):
    def setUp(self):
        self.config = ChronyClockConfig('216.232.132.102', 4096, 1000, 150000, 1, 1, .75)
        self.start = _ClockPoint(1000000000, 2000000000, 1790464008000000000, 1000001000)
        self.end = _ClockPoint(1010000000, 2010000000, 1790464008010000000, 1010001000)

    def run_sample(self, config=None, end=None, report=REPORT, boots=('same', 'same')):
        points = iter((self.start, end or self.end))
        boot = iter(boots)
        return sample(config or self.config, _query=lambda timeout: report,
                      _point=lambda: next(points), _boot_id=lambda: next(boot))

    def test_fresh_estimate_has_original_short_expiry(self):
        receipt = self.run_sample()
        self.assertEqual(receipt.checked_unix_ns, self.end.unix_ns)
        self.assertEqual(receipt.valid_until_monotonic_ns, self.end.before_ns + 1000000000)
        self.assertGreater(receipt.uncertainty_seconds, .3635)
        self.assertTrue(receipt.validation_id.startswith('chrony:'))

    def test_stale_reference_boot_suspend_step_and_budget_fail(self):
        cases = [dict(config=replace(self.config, max_reference_age_seconds=.5)),
                 dict(boots=('old', 'new')),
                 dict(end=replace(self.end, boot_ns=self.end.boot_ns+1000000000)),
                 dict(end=replace(self.end, unix_ns=self.end.unix_ns+1000000000)),
                 dict(config=replace(self.config, max_uncertainty_seconds=.3)),
                 dict(report=REPORT.replace('2026', '2027'))]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.run_sample(**case)


if __name__ == '__main__':
    unittest.main()
