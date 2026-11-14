"""The desk switch (plan 05, step 05-4): desk.json maps one fixture to its board, or den-live stays virtual.

A real Preview with its own day-file storage, the fake Home Assistant (fake_c12_ha) and the P3 board model. The
replay's checks need the pinned firmware codec (test_trace_v2.firmware_zip); those tests skip without it.
"""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import c12_replay
import desk
import fake_c12_ha
import ha_link
import preview_trace
import run_service
import test_c12_send
import test_preview
import test_trace_v2

DEVICE, TOKEN = test_c12_send.DEVICE, test_c12_send.TOKEN
BINDING, FIXTURES = test_preview.BINDING, test_preview.FIXTURES
READBACK = {"stop_latched": "binary_sensor.light_1_stop_latched", "state": "sensor.light_1_state",
            "pan_cdeg": "sensor.light_1_pan_cdeg", "tilt_cdeg": "sensor.light_1_tilt_cdeg"}


class DeskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preview.PreviewTest.setUpClass()
        cls.addClassCleanup(test_preview.PreviewTest.doClassCleanups)
        archive = test_trace_v2.firmware_zip()
        cls.codec = c12_replay.firmware_codec(archive)[0] if archive else None

    def setUp(self):
        self.h = test_preview.PreviewTest("test_one_confident_yes_lights")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.profile = dict(copy.deepcopy(self.h.core.scurve.FICTIONAL_PROFILE), fictional=False,
                            label="light-1-measured-for-tests")

    def content(self, **changes):
        return {"schema": desk.SCHEMA, "fixture_id": "F3", "device": DEVICE, "light": True,
                "profile": copy.deepcopy(self.profile), "readback": dict(READBACK), **changes}

    def credentials(self, content=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        if content is not None:
            text = content if isinstance(content, str) else json.dumps(content)
            (Path(folder.name) / desk.FILE).write_text(text, encoding="utf-8")
        return Path(folder.name)

    def fake_ha(self, board):
        ha = fake_c12_ha.FakeHa(fake_c12_ha.Integration({DEVICE: board}), TOKEN).start()
        self.addCleanup(ha.close)
        return ha

    def desk_preview(self, **changes):
        """A Preview on its own day files, with the desk on it, in step: each row's calls run before the next row."""
        h = self.h
        board = fake_c12_ha.ModelBoard(clock=lambda: h.now)
        ha = self.fake_ha(board)
        mapping = desk.parse(self.content(**changes), FIXTURES)
        fixtures = {"F3": dict(FIXTURES["F3"], profile=mapping["profile"])}
        h.preview = test_preview.preview.Preview(h.core, binding=BINDING, fixtures=fixtures, state=h.path,
                                                 clock=lambda: h.now, threaded=False)
        h.preview.configure({"fade_in_s": 0, "pre_aim": False})
        on = desk.Desk(h.preview, mapping, lambda: ha_link.HaSession(ha.url, TOKEN), threaded=False).start()
        self.addCleanup(on.close)
        written = h.preview.trace.append

        def in_step(row):
            written(row)
            on.sender.pump()
        h.preview.trace.append = in_step
        return on, board, ha

    def rows(self):
        files = sorted((Path(self.h.path) / "days").glob("*/coordinator-fixture_calls.jsonl"))
        return [json.loads(line) for path in files for line in path.read_text(encoding="utf-8").splitlines()]

    def replay(self, rows):
        if self.codec is None:
            self.skipTest("the pinned firmware bundle isn't here")
        return c12_replay.verify_trace(test_trace_v2.jsonl(rows), self.codec)

    def starter(self, credentials, fixed):
        settings = {"preview": True, "fixtures": FIXTURES, "binding": BINDING, "seat_mark": None}
        tree = next(Path(self.h.tree.name).glob("coordinator-*"))
        return run_service.coordinator_starter(settings, fixed, state=self.h.path, tree=tree, credentials=credentials)

    def test_no_desk_file_means_virtual_with_no_write_client(self):
        fixed = {"ha_url": "https://192.0.2.9:8123", "token": "synthetic"}
        for credentials in (None, self.credentials()):
            with patch.object(ha_link, "HaSession", side_effect=AssertionError("HA write client opened")), \
                    patch.object(ha_link, "HaClient", side_effect=AssertionError("HA write client opened")):
                service, close = self.starter(credentials, fixed)()
                self.addCleanup(close)
            self.assertEqual(service.trace.append, service._append, "the preview's own storage, unwrapped")
            self.assertIsNone(service.trace.header["desk"])
            self.assertNotIn("devices", service.latest)
            self.assertIs(service.rows["F3"]["profile"]["fictional"], True, "the fictional profile, as before")

    def test_the_desk_file_is_checked(self):
        self.assertEqual(desk.parse(self.content(), FIXTURES)["fixture_id"], "F3")
        for changes, reason in (({"schema": "den-c12-desk-0"}, "isn't den-c12-desk-1"),
                                ({"fixture_id": "F9"}, "doesn't register"),
                                ({"device": "has spaces"}, "device reference"),
                                ({"light": "yes"}, "true or false"),
                                ({"profile": self.h.core.scurve.FICTIONAL_PROFILE}, "measured profile"),
                                ({"profile": dict(self.profile, range={"pan": [10, -10], "tilt": [-85, 85]})},
                                 "impossible value"),
                                ({"readback": {"stop_latched": "not an entity"}}, "readback"),
                                ({"also": 1}, "missing or unknown key")):
            with self.subTest(changes=list(changes)), self.assertRaisesRegex(desk.DeskError, reason):
                desk.parse(self.content(**changes), FIXTURES)
        self.assertIsNone(desk.load(self.credentials(), FIXTURES))
        with self.assertRaisesRegex(desk.DeskError, "doesn't read"):
            desk.load(self.credentials("{not json"), FIXTURES)

    def test_the_coordinator_stays_off_for_a_bad_desk_file_or_no_home_assistant(self):
        said = []
        with patch.object(run_service, "say", said.append):
            self.assertIsNone(self.starter(self.credentials(self.content(light="yes")), None)())
            self.assertIsNone(self.starter(self.credentials(self.content()), None)())
        self.assertIn("light must be true or false", said[0])
        self.assertIn("needs the asks block's Home Assistant link", said[1])

    def test_a_desk_run_sends_only_its_fixture_and_replays(self):
        on, board, _ha = self.desk_preview()
        h = self.h
        h.light()
        _, stopped = h.preview.stop({})
        self.assertTrue(board.stop_latched())
        self.assertEqual(h.preview.resume(stopped)[0], 200)
        self.assertFalse(board.stop_latched())
        rows = self.rows()
        header = rows[0]
        self.assertEqual(header["desk"], {"fixture_id": "F3", "device": DEVICE, "light": True})
        self.assertEqual(header["profile_sha256"]["F3"], preview_trace.PreviewTrace(
            "x", None, profiles={"F3": self.profile}).profile_hashes["F3"], "the run header names the measured profile")
        sends = [row for row in rows if row.get("kind") == "send"]
        self.assertEqual({row["fixture_id"] for row in sends}, {"F3"})
        self.assertEqual({row["action"]: row["outcome"] for row in sends},
                         {"aim_move": "acked", "keep_lit": "acked", "stop": "acked", "resume": "acked"})
        self.assertIn("devices", h.preview.latest)
        on.sender.take({**sends[0], "kind": None, "fixture_id": "F4", "status": "encoded_only"})
        self.assertEqual(on.sender.pump(), 0, "a fixture the desk doesn't map never reaches the sender's lanes")
        counts, _ = self.replay(rows)
        self.assertEqual(counts["send"], len(sends))

    def test_the_dark_rehearsal_sends_level_zero_and_no_keep_lit(self):
        _on, board, _ha = self.desk_preview(light=False)
        self.h.light()
        self.assertTrue(self.h.row()["on"], "Home still shows the virtual spot")
        self.assertFalse(board.spot_lit(), "the board's spot stays dark")
        rows = self.rows()
        sends = [row for row in rows if row.get("kind") == "send"]
        moves = [row for row in sends if row["action"] == "aim_move"]
        self.assertTrue(moves and all(bytes.fromhex(row["wire"]["payload_hex"])[7] == 0 for row in moves))
        self.assertTrue(any(row["action"] == "aim_move" and row["effective"]["spot_level"] > 0
                            for row in rows if row.get("status") == "encoded_only"), "the trace keeps the lit request")
        keeps = [row for row in sends if row["action"] == "keep_lit"]
        self.assertTrue(keeps and all((row["outcome"], row["sent"]) == ("dropped_dark", False) for row in keeps))
        self.replay(rows)
        lit = copy.deepcopy(rows)
        lit[0]["desk"]["light"] = True   # read as a lit run, the level-0 wire no longer matches
        with self.assertRaises(c12_replay.ReplayError):
            self.replay(lit)

    def test_a_latch_read_becomes_homes_stop_and_device_rows_follow_changes(self):
        on, board, ha = self.desk_preview()
        h = self.h
        h.light()
        ha.states.update({READBACK["stop_latched"]: "off", READBACK["state"]: "holding", READBACK["pan_cdeg"]: "1200",
                          READBACK["tilt_cdeg"]: "unavailable"})
        on.poller.poll_once()
        on.poller.poll_once()   # unchanged: no second row
        counter = h.preview.stop_counter
        ha.integration.stop({"fixture": "all"})   # the Stop button in the den home's HA
        ha.states[READBACK["stop_latched"]] = "on"
        on.poller.poll_once()
        self.assertEqual((h.preview.stopped, h.preview.stop_counter), (True, counter + 1), "Home's Stop, copied")
        on.poller.poll_once()
        self.assertEqual(h.preview.stop_counter, counter + 1, "copied once")
        devices = [row for row in self.rows() if row.get("kind") == "device"]
        self.assertEqual([row["values"]["stop_latched"] for row in devices], [False, True])
        self.assertEqual(devices[0]["values"], {"stop_latched": False, "state": "holding", "pan_cdeg": 1200,
                                                "tilt_cdeg": None})
        h.preview._publish(h.now)
        self.assertEqual(h.preview.latest["devices"]["F3"]["values"]["stop_latched"], True)
        self.replay(self.rows())

    def test_the_desk_goes_on_before_the_first_row(self):
        h = self.h
        h.preview.stop({})
        with self.assertRaisesRegex(desk.DeskError, "before the trace's first row"):
            desk.Desk(h.preview, desk.parse(self.content(), FIXTURES), lambda: None, threaded=False)

    def test_the_service_starts_with_the_desk_and_its_stop_reaches_the_board(self):
        board = fake_c12_ha.ModelBoard()
        ha = self.fake_ha(board)
        said = []
        with patch.object(run_service, "say", said.append):
            service, close = self.starter(self.credentials(self.content(readback={})),
                                          {"ha_url": ha.url, "token": TOKEN})()
        self.addCleanup(close)
        self.assertIn("The desk: F3's commands go to its board", said[-1])
        self.assertEqual(service.trace.header["desk"]["device"], DEVICE)
        self.assertIs(service.rows["F3"]["profile"]["fictional"], False)
        self.assertEqual(service.stop({})[0], 200)
        end = time.monotonic() + 5
        while not board.stop_latched() and time.monotonic() < end:
            time.sleep(.02)
        self.assertTrue(board.stop_latched(), "Home's Stop reached the board through the service's own sender")


if __name__ == "__main__":
    unittest.main()
