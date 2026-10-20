import io
import json
import unittest
from contextlib import redirect_stdout

import occupancy_flaps as flaps


def line(t, a, b):
    return "x: occupancy edge: entity=binary_sensor.den_oak_person_occupancy from=%s to=%s observed_at=%.3f" % (a, b, t)


class FlapTests(unittest.TestCase):
    def test_stretches_short_gaps_and_rates(self):
        t0 = 1_000_000.0
        lines = [line(t0, "off", "on"), line(t0 + 600, "on", "off"), line(t0 + 605, "off", "on"),
                 line(t0 + 1200, "on", "off"), line(t0 + 1225, "off", "on"), line(t0 + 1230, "on", "off"),
                 line(t0 + 5000, "off", "on")]
        spans = flaps.stretches(flaps.parse(lines))
        result = flaps.stats(spans)
        self.assertEqual(result["on_stretches"]["n"], 3)
        self.assertEqual(result["off_gaps"]["n"], 3)
        self.assertEqual(result["off_gaps_le_10s"], 1)
        self.assertEqual(result["off_gaps_le_30s"], 2)
        self.assertEqual(result["on_blips_le_10s"], 1)
        self.assertAlmostEqual(result["occupied_hours"], round((600 + 595 + 5) / 3600, 2))

    def test_dirty_span_and_window_assignment(self):
        t0 = 2_000_000.0
        spans = [("on", t0, t0 + 50), ("off", t0 + 50, t0 + 55), ("on", t0 + 55, t0 + 500)]
        kept = flaps.window(spans, t0, t0 + 1000, [(t0 + 52, t0 + 60)])
        self.assertEqual(kept, [("on", t0, t0 + 50)])   # both spans touching the dirty span are dropped

    def test_cli_windows(self):
        lines = "\n".join([line(1791500000, "off", "on"), line(1791500100, "on", "off"),
                           line(1791500105, "off", "on"), line(1791503000, "on", "off")])
        out = io.StringIO()
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as handle:
            handle.write(lines)
        try:
            with redirect_stdout(out):
                flaps.main([handle.name, "--window", "all=2026-01-01T00:00+00:00..2027-01-01T00:00+00:00"])
        finally:
            os.unlink(handle.name)
        report = json.loads(out.getvalue())
        self.assertEqual(report["edges"], 4)
        self.assertEqual(report["windows"]["all"]["off_gaps_le_10s"], 1)


if __name__ == "__main__":
    unittest.main()
