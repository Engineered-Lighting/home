"""desk_report.py (the D-1 check on LA) and desk.py --check (the PC's check before the install).

Desk runs are made as LA makes them: PreviewTrace with the desk mapping, the sender, the fake Home Assistant and the P3
board model, written to a day file in a scratch state folder.
"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import c12_send
import desk
import desk_report
import fake_c12_ha
import ha_link
import preview_trace
import test_c12_send
import test_install_desk

DEVICE, TOKEN, move = test_c12_send.DEVICE, test_c12_send.TOKEN, test_c12_send.move
DAY = "2026-11-15"


class DeskReportTest(unittest.TestCase):
    def desk_run(self, *, light=True, desk_on=True):
        """One run's rows in file order: its header, its encoded rows and its send rows."""
        clock = test_c12_send.Clock()
        board = fake_c12_ha.ModelBoard(clock=clock)
        ha = fake_c12_ha.FakeHa(fake_c12_ha.Integration({DEVICE: board}), TOKEN).start()
        self.addCleanup(ha.close)
        log = []
        sender = c12_send.Sender({"F3": DEVICE}, lambda: ha_link.HaSession(ha.url, TOKEN), record=log.append,
                                 clock=clock, threaded=False, light=light)
        self.addCleanup(sender.close)
        mapping = {"fixture_id": "F3", "device": DEVICE, "light": light} if desk_on else None
        trace = preview_trace.PreviewTrace("run-a", sender.after(log.append), clock=clock,
                                           wall=lambda: 1_763_000_000.0 + clock(), desk=mapping)
        log.append(trace.header)
        lit = trace.emit("F3", "aim_move", decision_id="d1", move=move(), at=clock(), subject=test_c12_send.SUBJECT)
        sender.pump()
        clock.now += 1
        trace.emit("F3", "keep_lit", decision_id="d1", move_token=lit["sequence"], at=clock())
        sender.pump()
        trace.emit("F3", "stop", at=clock())
        trace.emit("F3", "resume", at=clock())
        sender.pump()
        return log

    def day_file(self, rows):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "days" / DAY / desk_report.FILE
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return Path(folder.name)

    def run_main(self, state):
        out = io.StringIO()
        with redirect_stdout(out):
            code = desk_report.main(["--day", DAY, "--state", str(state)])
        return code, out.getvalue()

    def test_a_lit_desk_run_passes_and_says_what_was_sent(self):
        code, text = self.run_main(self.day_file(self.desk_run()))
        self.assertEqual(code, 0, text)
        self.assertIn("the desk, F3, lit", text)
        self.assertIn("aim_move acked 1", text)
        self.assertIn("move answered accepted 1", text)
        for name in ("one fixture", "the dark rule", "the bytes", "no stale seq", "Stop"):
            self.assertIn("PASS " + name, text)
        self.assertNotIn(DEVICE, text, "never the device reference")

    def test_a_dark_run_passes_only_while_nothing_lights(self):
        rows = self.desk_run(light=False)
        code, text = self.run_main(self.day_file(rows))
        self.assertEqual(code, 0, text)
        self.assertIn("the desk, F3, dark", text)
        self.assertIn("keep_lit dropped_dark 1", text)
        broken = copy.deepcopy(rows)
        send = next(row for row in broken if row.get("kind") == "send" and row["action"] == "aim_move")
        payload = bytearray(bytes.fromhex(send["wire"]["payload_hex"]))
        payload[7] = 127
        send["wire"]["payload_hex"] = payload.hex()
        lines, ok = desk_report.report(broken)
        self.assertFalse(ok)
        self.assertIn("FAIL the dark rule: a dark run sent a MOVE with its spot lit", lines)

    def test_a_virtual_run_that_sent_fails_one_fixture(self):
        rows = self.desk_run(desk_on=True)
        rows[0] = dict(rows[0], desk=None)
        lines, ok = desk_report.report(rows)
        self.assertFalse(ok)
        self.assertIn("FAIL one fixture: a virtual run sent a command", lines)

    def test_a_count_that_went_back_fails_no_stale_seq(self):
        rows = self.desk_run()
        sends = [row for row in rows if row.get("kind") == "send" and row["wire"] and row["action"] != "stop"]
        later = sends[-1]
        payload = bytearray(bytes.fromhex(later["wire"]["payload_hex"]))
        payload[0:2] = (0).to_bytes(2, "little")
        later["wire"].update(seq=0, payload_hex=payload.hex())
        lines, ok = desk_report.report(rows)
        self.assertFalse(ok)
        self.assertTrue(any(line.startswith("FAIL no stale seq") for line in lines), lines)

    def test_no_trace_yet_says_so(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        code, text = self.run_main(Path(folder.name))
        self.assertEqual(code, 2)
        self.assertIn("No fixture trace for %s yet" % DAY, text)


class NowTest(unittest.TestCase):
    def test_now_says_home_and_the_board_in_words_only(self):
        state = {"on": True, "stopped": False, "stop_counter": 3, "server_mono": 120.5, "snapshot": {
            "fixtures": {"F3": {"on": True, "reason": "task", "target_m": [1.23456, 2.34567, 0.9], "pan_deg": 12.5}},
            "devices": {"F3": {"device": DEVICE, "light": False, "read_mono": 120.0, "sent": 7, "released": 0,
                               "problem": None, "values": {"stop_latched": False, "state": "holding",
                                                           "fault_code": 0, "pan_cdeg": 1250}}}}}
        lines = desk_report.now_lines(state)
        self.assertEqual(lines[0], "Home: on, not stopped, Stop counter 3.")
        self.assertEqual(lines[1], "The desk: F3, dark. Home shows it lit (task). The board: latched False, state "
                                   "holding, fault 0, read 0.5 s ago; 7 commands acknowledged, 0 released.")
        text = " ".join(lines)
        for private in (DEVICE, "1.23456", "12.5", "1250"):
            self.assertNotIn(private, text)
        self.assertEqual(desk_report.now_lines({"on": False, "stopped": True, "stop_counter": 4, "snapshot": {}})[1],
                         "The desk: none (virtual only).")


class DeskCheckTest(unittest.TestCase):
    def files(self, desk_doc, geometry):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "desk.json").write_text(json.dumps(desk_doc), encoding="utf-8-sig")
        (root / "export.json").write_text(json.dumps(geometry), encoding="utf-8")
        return root / "desk.json", root / "export.json"

    def geometry(self, **changes):
        return {"schema": "lighting-brain-geometry-1", "physical_commands": False,
                "fixtures": [{"fixture_id": "test-fixture"}], **changes}

    def test_a_good_desk_file_checks_in_words_only(self):
        code, line = desk.check(*self.files(test_install_desk.desk_doc(), self.geometry()))
        self.assertEqual(code, 0)
        self.assertEqual(line, "The desk file checks: test-fixture's commands go to its board, dark (light false: the "
                               "dark rehearsal); measured profile light-1-measured-for-tests; readback none.")
        self.assertNotIn(test_install_desk.DEVICE, line)

    def test_a_bad_desk_file_or_geometry_says_why(self):
        for desk_doc, geometry, words in (
                (test_install_desk.desk_doc(fixture_id="F9"), self.geometry(), "doesn't register"),
                (test_install_desk.desk_doc(), self.geometry(physical_commands=True),
                 "physical_commands must be false"),
                (test_install_desk.desk_doc(light=None), self.geometry(), "true or false")):
            with self.subTest(words=words):
                code, line = desk.check(*self.files(desk_doc, geometry))
                self.assertEqual(code, 2)
                self.assertIn(words, line)


if __name__ == "__main__":
    unittest.main()
