"""Tests for the cue-to-light instrument (cue_light.py), on made-up events: no camera, Home or LA.

    python -m pytest -q test_cue_light.py
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cue_light  # noqa: E402


def snapshot(seq, lit, epoch="e1"):
    return {"schema": "den-virtual-spots-1", "epoch": epoch, "seq": seq, "on": True, "stopped": False, "valid_until_mono": 9.0,
            "binding": {"transform_id": "t"}, "fixtures": {"F1": {"on": lit, "level": 0.8 if lit else 0.0, "pan_deg": 12.5,
                                                                 "tilt_deg": -40.0, "target_m": [1.0, 2.0, 0.6]}}}


class SpotRowTest(unittest.TestCase):
    def test_numbers_only_never_a_target_or_an_angle(self):
        row = cue_light.spot_row(100.0, snapshot(3, True))
        self.assertEqual(row, {"arrival_unix": 100.0, "epoch": "e1", "seq": 3, "on": True, "stopped": False, "lit": True,
                               "level_max": 0.8})
        self.assertNotIn("target", json.dumps(row))


class SubscriberTest(unittest.TestCase):
    def test_it_keeps_people_and_spot_snapshots(self):
        sub = cue_light.SpotSubscriber("http://127.0.0.1:1/x")
        stream = io.BytesIO(("event: people\ndata: %s\n\nevent: spot_clock\ndata: {}\n\nevent: spot_snapshot\ndata: %s\n\n"
                             % (json.dumps({"tracks": []}), json.dumps(snapshot(1, False)))).encode())
        sub.read(stream)
        self.assertEqual(len(sub.events), 1)
        self.assertEqual([(r["seq"], r["lit"]) for r in sub.spot_rows()], [(1, False)])


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.spots = [cue_light.spot_row(t, snapshot(i, lit)) for i, (t, lit) in
                      enumerate([(99.5, False), (101.0, False), (102.3, True), (103.0, True), (131.0, True), (132.4, False)])]
        self.cues = [("read", 100.0), ("phone", 130.0)]

    def test_the_first_change_after_each_cue(self):
        lines = cue_light.report_lines(self.cues, self.spots)
        self.assertIn("lit: snapshot +2.30 s", lines[1])
        self.assertIn("dark: snapshot +2.40 s", lines[2])

    def test_with_homes_paint_stamps(self):
        paints = [{"epoch": "e1", "seq": 2, "lit": True, "paint_unix": 102.38}, {"epoch": "e1", "seq": 5, "lit": False, "paint_unix": 132.45}]
        lines = cue_light.report_lines(self.cues, self.spots, paints)
        self.assertIn("paint +2.38 s (Home 0.08 s after the snapshot)", lines[1])
        self.assertIn("paint +2.45 s", lines[2])

    def test_a_light_that_never_changes(self):
        lines = cue_light.report_lines([("leave", 200.0)], [cue_light.spot_row(199.0, snapshot(1, False)),
                                                           cue_light.spot_row(201.0, snapshot(2, False))])
        self.assertIn("light stayed dark", lines[1])

    def test_saved_numbers_and_a_takes_cues_read_back(self):
        folder = Path(tempfile.mkdtemp(prefix="cue-light-"))
        cue_light.save(folder, self.cues, self.spots)
        self.assertEqual(cue_light.load_cues(folder), self.cues)
        take = folder / "take.json"
        take.write_text(json.dumps({"cues": [{"cue": "begin", "pc_unix": 100.0, "pc_mono_s": 5.0, "text": "x"}]}), encoding="utf-8")
        self.assertEqual(cue_light.load_cues(take=take), [("begin", 100.0)])
        paint = folder / "paint.json"
        paint.write_text("﻿" + json.dumps([{"epoch": "e1", "seq": 2, "lit": True, "paint_unix_ms": 102380}]), encoding="utf-8")
        self.assertEqual(cue_light.load_paint(paint)[0]["paint_unix"], 102.38)
        out = io.StringIO()
        sys.stdout, saved = out, sys.stdout
        try:
            self.assertEqual(cue_light.main(["report", str(folder), "--paint", str(paint)]), 0)
        finally:
            sys.stdout = saved
        self.assertIn("read:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
