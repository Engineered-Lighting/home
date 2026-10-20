import unittest
from home_shared.chrony_tracking import parse_tracking

REPORT = '''Reference ID : D8E88466 (216.232.132.102)
Stratum : 2
Ref time (UTC) : Sat Sep 26 23:06:47 2026
System time : 0.002000000 seconds slow of NTP time
Last offset : +0.002 seconds
RMS offset : 0.003 seconds
Frequency : 35.812 ppm fast
Residual freq : +0.003 ppm
Skew : 0.129 ppm
Root delay : 0.020 seconds
Root dispersion : 0.200 seconds
Update interval : 2048.0 seconds
Leap status : Normal
'''


class TrackingTests(unittest.TestCase):
    def parse(self, report=REPORT):
        return parse_tracking(report, '216.232.132.102', max_error_seconds=0.75)

    def test_charges_remaining_correction_and_root_distance(self):
        result = self.parse()
        self.assertAlmostEqual(result.estimated_error_seconds, 0.212)
        self.assertLess(result.system_offset_seconds, 0)

    def test_rejects_unusable_reports(self):
        for before, after in [('Normal', 'Not synchronised'), ('Normal', 'Insert second'),
                              ('D8E88466', '7F7F0101'), ('0.200 seconds', '0.900 seconds'),
                              ('0.200 seconds', 'nan seconds'), ('0.200 seconds', '-0.2 seconds'),
                              ('Stratum : 2', 'Stratum : 0')]:
            with self.subTest(after=after), self.assertRaises(ValueError):
                self.parse(REPORT.replace(before, after))
        with self.assertRaises(ValueError):
            self.parse(REPORT + 'Leap status : Normal\n')


if __name__ == '__main__':
    unittest.main()
