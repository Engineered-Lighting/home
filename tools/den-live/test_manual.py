"""manual.py: the shape of Home's den-manual-1 event, the parts of a fixture it touches, and the durable log."""

import json
import tempfile
import unittest
from pathlib import Path

import manual


def event(**overrides):
    return {"schema": "den-manual-1", "event_id": "ev-0000001", "fixture_id": "F3",
            "before": {"spotlight": {"brightness": .6}}, "after": {"spotlight": {"brightness": .4}},
            "at_ms": 1763150000000, "snapshot": {"epoch": "e", "seq": 12}, **overrides}


class ParseTest(unittest.TestCase):
    def test_a_good_event_is_normalised(self):
        parsed = manual.parse(event(after={"spotlight": {"brightness": .4}}, subject={"track_id": "t1"}))
        self.assertEqual(parsed["after"], {"spotlight": {"brightness": .4}})
        self.assertEqual(parsed["snapshot"], {"epoch": "e", "seq": 12})
        self.assertEqual(parsed["subject"], {"track_id": "t1"})
        self.assertIsNone(parsed["situation"])

    def test_every_shape_home_sends(self):
        state = {"pan_deg": 10, "tilt_deg": -40.5,
                 "target": {"point": [1, 2, .7], "normal": [0, 0, 1], "surface_id": "sofa", "kind": "registered_mesh"},
                 "spotlight": {"on": True, "brightness": .5, "color_temp_kelvin": 2700},
                 "downward": {"on": False},
                 "radials": [{"number": 1, "on": True, "brightness": .3, "color_temp_kelvin": 3000}]}
        parsed = manual.parse(event(before=state, after={**state, "target": None}))
        self.assertIsNone(parsed["after"]["target"])
        self.assertEqual(manual.parts(parsed["after"]), ["downward", "radials", "spot"])
        self.assertEqual(manual.parts({"radials": []}), ["radials"])

    def test_the_bodies_home_444_sends(self):
        """#444's handDiff, as built (eafd0885): whole keys with null where it had no value, a target with null
        surface_id and kind, radials zone by zone with `before` only for zones it had a state for, and an echoed
        null subject."""
        aim = event(before={"pan_deg": 3, "tilt_deg": 30, "target": None},
                    after={"pan_deg": 8, "tilt_deg": 41, "target": {"point": [1, 2, .7], "normal": [0, 0, 1],
                                                                    "surface_id": None, "kind": None}},
                    subject=None, event_id="home-6f1c2c86-0f3e-4a43-9b56-6c1d0c7e9a10")
        self.assertEqual(manual.parse(aim)["after"]["target"]["point"], [1.0, 2.0, .7])
        paint = event(before={"spotlight": {"on": True, "brightness": .5, "color_temp_kelvin": None},
                              "downward": None, "radials": []},
                      after={"spotlight": {"on": True, "brightness": .8, "color_temp_kelvin": None},
                             "downward": {"on": True, "brightness": .3, "color_temp_kelvin": 2700},
                             "radials": [{"number": 1, "on": True, "brightness": .9, "color_temp_kelvin": None}]})
        parsed = manual.parse(paint)
        self.assertEqual(parsed["before"]["spotlight"], {"on": True, "brightness": .5})
        self.assertIsNone(parsed["before"]["downward"])
        self.assertEqual(parsed["before"]["radials"], [])
        self.assertEqual(parsed["after"]["radials"], [{"number": 1, "on": True, "brightness": .9}])

    def test_bad_events_are_refused_with_a_reason(self):
        bad = [
            "not an object",
            event(schema="den-manual-0"),
            event(event_id="short"),
            event(fixture_id="../F3"),
            event(at_ms=-1),
            event(at_ms=1.5),
            event(snapshot={"epoch": "e"}),
            event(after={"spotlight": {"brightness": 1.5}}),
            event(after={"spotlight": {"color_temp_kelvin": 500}}),
            event(after={"spotlight": {"glow": 1}}),
            event(after={"pan_deg": float("nan")}),
            event(after={"target": {"point": [1, 2]}}),
            event(after={"radials": [{"on": True}]}),
            event(after={"tilt_deg": 3}),   # before names spotlight, after tilt
            event(extra=1),
            event(subject={"pad": "x" * 9000}),
        ]
        for data in bad:
            with self.subTest(data=str(data)[:60]):
                with self.assertRaises(ValueError):
                    manual.parse(data)


class LogTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.log = manual.Log(self.folder)

    def test_lines_come_back_in_order_and_a_torn_last_line_is_skipped(self):
        self.assertEqual(self.log.read(), [])
        for n in range(3):
            self.log.append({"event": {"event_id": f"ev-{n:07d}"}, "learn": None})
        with self.log.path.open("a", encoding="utf-8") as torn:
            torn.write('{"event": {"event_id": "ev-torn')
        self.assertEqual([line["event"]["event_id"] for line in self.log.read()],
                         ["ev-0000000", "ev-0000001", "ev-0000002"])
        self.assertTrue(all(json.loads(raw) for raw in self.log.path.read_text(encoding="utf-8").splitlines()[:3]))

    def test_a_torn_tail_never_swallows_the_next_line(self):
        """Review L1/L4: a crash or a full disk left half a line; the next append starts on its own line."""
        self.log.append({"event": {"event_id": "ev-0000000"}})
        with self.log.path.open("ab") as torn:
            torn.write(b'{"event": {"event_id": "ev-torn')
        self.log.append({"event": {"event_id": "ev-0000001"}})
        self.assertEqual([line["event"]["event_id"] for line in self.log.read()], ["ev-0000000", "ev-0000001"])
        self.assertEqual(self.log.skipped, 1)

    def test_bad_bytes_are_skipped_never_fatal(self):
        self.log.path.write_bytes(b'\xff\xfe\x00garbage\n[1, 2]\n{"event": {"event_id": "ev-0000002"}}\n')
        self.assertEqual([line["event"]["event_id"] for line in self.log.read()], ["ev-0000002"])
        self.assertEqual(self.log.skipped, 2)

    def test_clear_keeps_the_old_log_whole_and_starts_empty(self):
        self.log.append({"event": {"event_id": "ev-0000000"}})
        self.log.archive("20261114T200000Z")
        self.assertEqual(self.log.read(), [])
        kept = self.folder / "manual-events-20261114T200000Z.jsonl"
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8"))["event"]["event_id"], "ev-0000000")
        self.log.archive("20261114T200001Z")   # nothing to keep: no error, no file
        self.assertFalse((self.folder / "manual-events-20261114T200001Z.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
