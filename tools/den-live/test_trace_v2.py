"""Trace v2 (contract 5): the preview's own rows, written as LA writes them, through the pinned codec and the simulator.

Contract 5's committed test: rows a real Preview wrote, lit by one confident reply, go through c12_replay.py's checks
against the pinned firmware codec and, when it's here, the v2-a host simulator.
- The codec: C12_TEST_FIRMWARE_ZIP or DEN_C12_CODEC_ZIP, else the firmware bundle test_preview_trace.py looks for.
- The simulator: C12_TEST_SIM, whose SHA-256 must be preview_trace.SIMULATOR_SHA256.
Without them those tests skip; the rows' own v2 fields are checked either way.
"""
import calendar
import io
import json
import os
import unittest
from pathlib import Path

import c12_replay
import preview_trace
import test_preview
import test_preview_trace

HERE = Path(__file__).resolve().parent
BINDING = test_preview.BINDING


def firmware_zip():
    given = os.environ.get("C12_TEST_FIRMWARE_ZIP") or os.environ.get("DEN_C12_CODEC_ZIP")
    if given:
        return Path(given)
    return next((p / test_preview_trace.BUNDLE for p in HERE.parents if (p / test_preview_trace.BUNDLE).is_file()), None)


def jsonl(rows):
    return io.BytesIO(b"".join(json.dumps(row, allow_nan=False, separators=(",", ":")).encode() + b"\n" for row in rows))


class TraceV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preview.PreviewTest.setUpClass()   # den-core's pinned helpers, once

    def harness(self, **overrides):
        """test_preview's harness: a Preview with its fake tracks and replies (light() reaches a lit F3)."""
        h = test_preview.PreviewTest("test_one_confident_yes_lights")
        h.setUp()
        self.addCleanup(h.doCleanups)
        if overrides:
            h.preview = h.make_preview(**overrides)
            h.preview.configure({"fade_in_s": 0, "pre_aim": False})
        return h

    def lit_rows(self):
        h = self.harness()
        h.light()
        return h, [h.preview.trace.header, *h.encoded]

    def test_a_lit_move_names_its_room_model_its_subject_and_its_reply(self):
        h, rows = self.lit_rows()
        header = rows[0]
        self.assertEqual((header["schema"], header["kind"], header["run_id"]),
                         (preview_trace.SCHEMA, "run_header", h.preview.epoch))
        self.assertEqual(header["binding"], {key: BINDING[key] for key in
                                             ("scan_revision", "transform_id", "equipment_revision", "equipment_digest")})
        self.assertEqual((header["codec_sha256"], header["simulator_sha256"]),
                         (preview_trace.CODEC_SHA256, preview_trace.SIMULATOR_SHA256))
        lit = [row for row in rows[1:] if row["action"] == "aim_move" and row["status"] == "encoded_only"
               and row["effective"]["spot_level"]]
        self.assertTrue(lit, "light() should have encoded a lit MOVE")
        for row in rows[1:]:
            self.assertEqual(row["schema"], preview_trace.SCHEMA)
            self.assertEqual({k: row[k] for k in preview_trace.BINDING_FIELDS},
                             {k: BINDING[k] for k in preview_trace.BINDING_FIELDS})
        for row in lit:
            self.assertEqual((row["track_id"], row["camera_epoch"], row["association_revision"]), ("t1", 1, 2))
            self.assertIsInstance(row["request_id"], str)
            self.assertTrue(row["request_id"])

    def test_each_day_file_starts_with_the_run_header(self):
        h = self.harness(append=None)   # the preview's own storage, as on LA
        late = calendar.timegm((2026, 11, 13, 23, 59, 50, 0, 0, 0))
        h.preview.wall = lambda: late
        h.light()
        h.preview.wall = lambda: late + 20   # past UTC midnight, same run
        h.preview.stop({})
        days = Path(h.path) / "days"
        files = [days / day / "coordinator-fixture_calls.jsonl" for day in ("2026-11-13", "2026-11-14")]
        lines = [[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] for path in files]
        for rows in lines:
            self.assertEqual(rows[0], h.preview.trace.header)
            self.assertTrue(all(row.get("kind") != "run_header" for row in rows[1:]))
        indexes = [row["event_index"] for rows in lines for row in rows[1:]]
        self.assertEqual(indexes, list(range(1, len(indexes) + 1)))
        self.assertEqual(lines[1][-1]["action"], "stop")
        bundle = firmware_zip()
        if bundle is None:
            self.skipTest("pinned C12 firmware bundle absent")
        codec, _vectors = c12_replay.firmware_codec(bundle)
        with c12_replay.Chained(files) as handle:
            counts, _ = c12_replay.verify_trace(handle, codec)
        self.assertGreater(counts["encoded_only"], 0)
        # The command line, as the offline replay runs it: the run's two day files, in order.
        args = [*map(str, files), "--firmware-zip", str(bundle)]
        simulator = os.environ.get("C12_TEST_SIM")
        if simulator:
            args += ["--sim", simulator, "--sim-sha256", preview_trace.SIMULATOR_SHA256]
        self.assertEqual(c12_replay.main(args), 0)
        if simulator:
            self.assertEqual(c12_replay.main([*args[:-1], "0" * 64]), 1)   # another simulator build is refused


class ReplayV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle = firmware_zip()
        if bundle is None:
            raise unittest.SkipTest("pinned C12 firmware bundle absent")
        cls.codec, cls.vectors = c12_replay.firmware_codec(bundle)
        test_preview.PreviewTest.setUpClass()

    def setUp(self):
        self.h, self.rows = TraceV2Test.lit_rows(self)

    def harness(self, **overrides):
        return TraceV2Test.harness(self, **overrides)

    def verify(self, rows, **kw):
        return c12_replay.verify_trace(jsonl(rows), self.codec, **kw)

    def test_the_preview_trace_passes_the_pinned_codec_and_the_simulator(self):
        self.assertGreater(c12_replay.verify_vectors(self.codec, self.vectors), 0)
        counts, groups = self.verify(self.rows, collect_sim=True)
        self.assertGreater(counts["encoded_only"], 0)
        simulator = os.environ.get("C12_TEST_SIM")
        if not simulator:
            self.skipTest("set C12_TEST_SIM to the v2-a host simulator to replay the frames through it")
        replayed = c12_replay.replay_simulator(simulator, preview_trace.SIMULATOR_SHA256, groups)
        self.assertEqual(replayed, counts["encoded_only"])

    def first_lit(self):
        return next(i for i, row in enumerate(self.rows) if row.get("action") == "aim_move"
                    and row.get("status") == "encoded_only" and row["effective"]["spot_level"])

    def test_a_lit_move_with_no_reply_is_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[self.first_lit()]["request_id"] = None
        with self.assertRaisesRegex(c12_replay.ReplayError, "lit MOVE names no reply"):
            self.verify(rows)

    def test_a_move_on_another_room_model_is_refused(self):
        rows = [dict(row) for row in self.rows]
        rows[self.first_lit()]["equipment_revision"] = BINDING["equipment_revision"] + 1
        with self.assertRaisesRegex(c12_replay.ReplayError, "binding disagrees with its run header"):
            self.verify(rows)

    def test_rows_need_their_run_header_first(self):
        with self.assertRaisesRegex(c12_replay.ReplayError, "before its run header"):
            self.verify(self.rows[1:])

    def test_a_changed_header_in_the_next_day_file_is_refused(self):
        changed = dict(self.rows[0], at_ts=self.rows[0]["at_ts"] + 1)
        with self.assertRaisesRegex(c12_replay.ReplayError, "header changed between its day files"):
            self.verify([*self.rows, changed])


if __name__ == "__main__":
    unittest.main()
