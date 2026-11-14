"""The FC13 sender (plan 05, step 05-2) against a fake Home Assistant whose `c12` integration owns the wire seq.

The boards: fake_c12_ha.ModelBoard (P3's rules: MOVE accepted, the 3 s lease) always, and the firmware's own v2-a host
simulator as well when C12_TEST_SIM names it (its SHA-256 must be preview_trace.SIMULATOR_SHA256). v2-a answers a
MOVE `lights_only`, so on it nothing lights and no keep-lit goes; the seq, Stop and reboot rules are the board's own.
The pinned firmware codec (test_trace_v2.firmware_zip) checks every frame on the air when it's here.
"""
import hashlib
import json
import os
import struct
import time
import unittest
from pathlib import Path

import c12_replay
import c12_send
import fake_c12_ha
import ha_link
import preview_trace
import test_preview
import test_trace_v2


TOKEN = "fake-bearer-token-for-tests"
DEVICE, DEVICE_2 = "fake-device-light-1", "fake-device-light-2"
SUBJECT = {"track_id": "t1", "camera_epoch": 1, "association_revision": 2, "request_id": "r1"}


def move(level=.5, target=(1200, -300), valid_ms=1000):
    return {"start_cdeg": (0, 0), "target_cdeg": target, "range_cdeg": ((-17000, 17000), (-9000, 9000)),
            "level": level, "cct_k": 2700, "travel": "dark", "valid_ms": valid_ms, "lit_motion": False}


def simulator():
    path = os.environ.get("C12_TEST_SIM")
    if not path or not Path(path).is_file():
        return None
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != preview_trace.SIMULATOR_SHA256:
        raise AssertionError("C12_TEST_SIM is not the pinned v2-a simulator build")
    return path


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class Rig:
    """One den-live run's trace and sender, a fake HA, its integration and the boards."""

    def __init__(self, test, boards, *, threaded=False, clock=None):
        self.clock = clock or Clock()
        self.boards = boards
        self.integration = fake_c12_ha.Integration(boards)
        self.ha = fake_c12_ha.FakeHa(self.integration, TOKEN).start()
        test.addCleanup(self.ha.close)
        self.sent, self.released, self.copied, self.rows = [], [], [], []
        self.fixtures = dict(zip(("F3", "F4"), boards))
        self.sender = c12_send.Sender(self.fixtures, self.session, record=self.sent.append,
                                      on_release=lambda *a: self.released.append(a),
                                      on_board_stop=lambda *a: self.copied.append(a),
                                      clock=self.clock, threaded=threaded).start()
        test.addCleanup(self.sender.close)
        self.trace = preview_trace.PreviewTrace("run-a", self.sender.after(self.rows.append), clock=self.clock,
                                                wall=lambda: 0.0)

    def session(self):
        return ha_link.HaSession(self.ha.url, TOKEN)

    def emit(self, action, fixture="F3", **kw):
        return self.trace.emit(fixture, action, at=self.clock(), subject=SUBJECT, **kw)

    def pump(self):
        return self.sender.pump()

    def light(self, fixture="F3", **kw):
        row = self.emit("aim_move", fixture, decision_id="d1", move=move(**kw))
        self.pump()
        return row

    def keep(self, lit):
        self.clock.now += 1.0
        self.emit("keep_lit", lit["fixture_id"], decision_id="d1", move_token=lit["sequence"])
        self.pump()
        return self.sent[-1]

    def air(self, device=DEVICE):
        return [frame for frame in self.integration.air if frame["device"] == device]


class SenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preview.PreviewTest.setUpClass()   # den-core's pinned helpers, once
        cls.addClassCleanup(test_preview.PreviewTest.doClassCleanups)

    def preview_harness(self):
        h = test_preview.PreviewTest("test_one_confident_yes_lights")
        h.setUp()
        self.addCleanup(h.doCleanups)
        return h

    def boards(self, clock):
        """(name, board, what v2-a or P3 answers a valid MOVE with)."""
        found = [("model", fake_c12_ha.ModelBoard(clock=clock), "accepted")]
        sim = simulator()
        if sim:
            found.append(("sim", fake_c12_ha.SimBoard(sim), "lights_only"))
        for _name, board, _ok in found:
            self.addCleanup(board.close)
        return found

    def rig(self, *, devices=(DEVICE,), threaded=False, clock=None, board=None):
        clock = clock or Clock()
        boards = {device: board or fake_c12_ha.ModelBoard(clock=clock) for device in devices}
        return Rig(self, boards, threaded=threaded, clock=clock)

    def test_a_lit_move_and_its_keep_lit_reach_the_board_with_the_traced_bytes(self):
        r = self.rig()
        lit = r.light()
        sent_keep = r.keep(lit)
        sent_move = r.sent[0]
        self.assertEqual((sent_move["outcome"], sent_keep["outcome"]), ("acked", "acked"))
        self.assertEqual((sent_move["sent"], sent_move["matches_trace"], sent_keep["matches_trace"]),
                         (True, True, True))
        frames = r.air()
        self.assertEqual(frames[0]["payload_hex"][4:], lit["payload_hex"][4:], "the wire differs only in its seq")
        self.assertEqual(struct.unpack("<HH", bytes.fromhex(frames[1]["payload_hex"]))[1], sent_move["wire"]["seq"],
                         "the keep-lit names the lit move's wire seq, not its trace seq")
        self.assertEqual(sent_move["answer"], {"result": "accepted", "predicted_ms": 0})
        board = r.boards[DEVICE]
        r.clock.now += 2.9   # 3.9 s after the move, 2.9 s after the keep-lit the board received
        self.assertTrue(board.spot_lit())
        r.clock.now += .2
        self.assertFalse(board.spot_lit(), "dark 3 s after the last keep-lit")
        self.assertEqual(r.released, [])
        self.assertNotIn(TOKEN, json.dumps(r.sent))

    def test_a_den_live_restart_mid_run_is_never_refused_as_stale(self):
        for name, board, ok in self.boards(Clock()):
            with self.subTest(board=name):
                h = self.preview_harness()
                if isinstance(board, fake_c12_ha.ModelBoard):
                    board.clock = lambda: h.now
                integration = fake_c12_ha.Integration({DEVICE: board})
                ha = fake_c12_ha.FakeHa(integration, TOKEN).start()
                self.addCleanup(ha.close)
                sent, encoded = [], []

                def run():
                    """One den-live run: its Preview's trace rows go through its own sender, in step."""
                    sender = c12_send.Sender({"F3": DEVICE}, lambda: ha_link.HaSession(ha.url, TOKEN),
                                             record=sent.append, clock=lambda: h.now, threaded=False)
                    self.addCleanup(sender.close)
                    store = h.preview.trace.append

                    def write(row):
                        store(row)
                        encoded.append(row)
                        sender.take(row)
                        sender.pump()
                    h.preview.trace.append = write
                    h.light()

                run()
                h.preview = h.make_preview()   # den-live restarts: a new epoch, its trace seq from 0 again
                h.preview.configure({"fade_in_s": 0, "pre_aim": False})
                run()
                runs = {row["run_id"] for row in encoded}
                self.assertEqual(len(runs), 2)
                self.assertEqual(min(row["sequence"] for row in encoded if row["run_id"] != encoded[0]["run_id"]), 0)
                moves = [row for row in sent if row["action"] == "aim_move"]
                self.assertEqual({row["run_id"] for row in moves}, runs, "each run sent its own moves")
                self.assertTrue(all(row["answer"]["result"] == ok for row in moves), [r["answer"] for r in moves])
                last = None
                for frame in integration.air:
                    seq = struct.unpack_from("<H", bytes.fromhex(frame["payload_hex"]))[0]
                    self.assertEqual(fake_c12_ha.classify_seq(last, seq), "new")
                    last = seq
                if ok == "lights_only":   # v2-a lights nothing, so nothing is kept lit
                    self.assertFalse(any(frame["cmd"] == fake_c12_ha.KEEP_LIT for frame in integration.air))
                else:
                    keeps = [row for row in sent if row["action"] == "keep_lit"]
                    self.assertTrue(keeps and all(row["outcome"] == "acked" for row in keeps))

    def test_an_integration_restart_counts_on_from_the_boards_last_seq(self):
        clock = Clock()
        for name, board, ok in self.boards(clock):
            with self.subTest(board=name):
                r = self.rig(board=board, clock=clock)
                lit = r.light()
                r.keep(lit)
                last = board.last_seq()
                r.integration.start()   # Home Assistant, or the integration, restarts
                r.light(target=(1500, -300))
                self.assertEqual((r.sent[-1]["wire"]["seq"], r.sent[-1]["answer"]["result"]), (last + 1, ok))
                # last_seq can't be read: no move goes, and Stop still does
                r.integration.read_failures = 3
                r.integration.start()
                refused = r.light(target=(1600, -300))
                self.assertEqual((r.sent[-1]["outcome"], r.sent[-1]["sent"], r.sent[-1]["answer"]["result"]),
                                 ("refused", False, "seq_unknown"))
                self.assertEqual(r.released[-1], ("F3", refused["trace_id"], "c12_seq_unknown"))
                r.emit("stop")
                r.pump()
                self.assertEqual(r.sent[-1]["outcome"], "acked")
                self.assertTrue(board.stop_latched())
                r.emit("resume")
                r.pump()   # the read works again
                self.assertEqual(r.sent[-1]["outcome"], "acked")
                self.assertFalse(board.stop_latched())
                r.light(target=(1700, -300))
                self.assertEqual(r.sent[-1]["answer"]["result"], ok)

    def test_a_board_reboot_takes_the_first_command_and_keeps_the_latch(self):
        clock = Clock()
        for name, board, ok in self.boards(clock):
            with self.subTest(board=name):
                r = self.rig(board=board, clock=clock)
                for target in ((1000, 0), (1100, 0), (1200, 0)):
                    r.light(target=target)
                board.reboot()   # last_seq is RAM only; the integration still counts on from 3
                self.assertFalse(board.spot_lit(), "the spot boots dark")
                r.light(target=(1300, 0))
                self.assertEqual(r.sent[-1]["answer"]["result"], ok)
                r.integration.stop({"fixture": "all"})   # HA's own Stop button, no LA
                board.reboot()
                self.assertTrue(board.stop_latched(), "the latch survives a power cut")
                r.light(target=(1400, 0))
                self.assertEqual(r.sent[-1]["answer"]["result"], "stopped")
                r.emit("resume")
                r.pump()
                r.light(target=(1500, 0))
                self.assertEqual(r.sent[-1]["answer"]["result"], ok)
                answers = [row["answer"]["result"] for row in r.sent if row["action"] == "aim_move"]
                self.assertNotIn("stale_seq", answers)

    def test_stop_goes_first_and_cancels_what_waits(self):
        r = self.rig(devices=(DEVICE, DEVICE_2))
        lit = r.emit("aim_move", decision_id="d1", move=move())
        r.emit("keep_lit", decision_id="d1", move_token=lit["sequence"])
        r.emit("aim_move", "F4", decision_id="d1", move=move())
        r.emit("stop")
        r.pump()
        first = r.integration.air[0]
        self.assertEqual((first["device"], first["cmd"]), (DEVICE, fake_c12_ha.STOP))
        self.assertEqual([frame["cmd"] for frame in r.air()], [fake_c12_ha.STOP])
        outcomes = [(row["fixture_id"], row["action"], row["outcome"], row["sent"]) for row in r.sent]
        self.assertEqual(outcomes, [("F3", "keep_lit", "dropped_superseded", False),
                                    ("F3", "aim_move", "dropped_superseded", False),
                                    ("F3", "stop", "acked", True),
                                    ("F4", "aim_move", "acked", True)])

    def test_stop_never_waits_behind_a_move_in_flight(self):
        r = self.rig(threaded=True, clock=time.monotonic)
        r.ha.delay_s["move"] = .6   # c12.move waiting for its MOVE_ACK
        r.emit("aim_move", decision_id="d1", move=move())
        time.sleep(.15)
        r.emit("stop")
        self.assertTrue(r.sender.drain(5))
        stop = next(row for row in r.sent if row["action"] == "stop")
        lit = next(row for row in r.sent if row["action"] == "aim_move")
        self.assertLess(stop["answered_mono"], lit["called_mono"] + .6)
        self.assertEqual(r.air()[0]["cmd"], fake_c12_ha.STOP)
        self.assertEqual(lit["answer"]["result"], "stopped")

    def test_a_move_waits_for_its_fixtures_unanswered_resume(self):
        r = self.rig(threaded=True, clock=time.monotonic)
        r.emit("stop")
        self.assertTrue(r.sender.drain(5))
        r.ha.delay_s["resume"] = .3
        r.emit("resume")
        r.emit("aim_move", decision_id="d1", move=move())
        self.assertTrue(r.sender.drain(5))
        self.assertEqual([frame["cmd"] for frame in r.air()],
                         [fake_c12_ha.STOP, fake_c12_ha.RESUME, fake_c12_ha.MOVE])
        self.assertEqual(r.sent[-1]["answer"]["result"], "accepted")
        self.assertEqual(r.copied, [])

    def test_an_expired_move_is_never_sent(self):
        r = self.rig()
        late = r.emit("aim_move", decision_id="d1", move=move(valid_ms=800))
        r.clock.now += .8
        r.pump()
        self.assertEqual((r.sent[0]["outcome"], r.sent[0]["sent"], r.sent[0]["wire"]), ("dropped_expired", False, None))
        self.assertEqual(r.released, [("F3", late["trace_id"], "expired_before_send")])
        self.assertEqual((r.integration.air, r.ha.calls), ([], []))
        r.emit("aim_move", decision_id="d2", move=move(valid_ms=800))
        r.clock.now += .799
        r.pump()
        self.assertEqual(r.sent[-1]["outcome"], "acked")

    def test_two_failed_keep_lits_in_a_row_release_the_row(self):
        r = self.rig()
        lit = r.light()
        self.assertEqual(r.keep(lit)["outcome"], "acked")
        r.ha.down = True   # the link between the homes
        self.assertEqual(r.keep(lit)["outcome"], "no_answer")
        self.assertEqual(r.released, [])
        r.ha.down = False
        self.assertEqual(r.keep(lit)["outcome"], "acked", "a keep-lit that lands starts the count again")
        r.ha.down = True
        r.keep(lit)
        self.assertEqual(r.released, [])
        r.keep(lit)
        self.assertEqual(r.released, [("F3", lit["trace_id"], "keep_lit_failed")])
        self.assertTrue(r.boards[DEVICE].spot_lit(), "the board's lease still holds from the last keep-lit it got")
        r.clock.now += 1.0
        self.assertFalse(r.boards[DEVICE].spot_lit(), "and runs out 3 s after it")
        self.assertNotIn(TOKEN, json.dumps(r.sent))

    def test_a_stop_den_live_didnt_send_is_copied(self):
        r = self.rig()
        r.light()
        r.integration.stop({"fixture": "all"})   # the Stop button in the den home's HA
        retarget = r.light(target=(1500, -300))
        self.assertEqual(r.sent[-1]["answer"]["result"], "stopped")
        self.assertEqual(r.released[-1], ("F3", retarget["trace_id"], "c12_stopped"))
        self.assertEqual(r.copied, [("F3", "move_refused_stopped")])
        r.copied.clear()
        r.sender.observe("F3", True)   # the readback sees the latch
        self.assertEqual(r.copied, [("F3", "stop_latched_read")])
        r.copied.clear()
        r.emit("resume")   # den-live's own Resume: a latch read soon after its answer may predate it
        r.pump()
        r.sender.observe("F3", True)
        r.clock.now += 2.0
        r.sender.observe("F3", True)
        self.assertEqual(r.copied, [])
        r.clock.now += .1
        r.sender.observe("F3", True)
        self.assertEqual(r.copied, [("F3", "stop_latched_read")])
        r.copied.clear()
        r.emit("resume")   # unanswered: nothing is copied while it's out
        r.sender.observe("F3", True)
        self.assertEqual(r.copied, [])

    def test_the_copy_presses_homes_stop_once_and_never_while_off(self):
        h = self.preview_harness()
        copy = c12_send.copy_stop_into(h.preview)
        counter = h.preview.stop_counter
        copy("F3", "move_refused_stopped")
        self.assertEqual((h.preview.stopped, h.preview.stop_counter), (True, counter + 1))
        copy("F3", "stop_latched_read")
        self.assertEqual(h.preview.stop_counter, counter + 1, "Home already stopped: nothing to copy")
        self.assertEqual(h.preview.resume({"stop_counter": counter + 1})[0], 200)
        h.preview.switch({"on": False})
        copy("F3", "stop_latched_read")
        self.assertFalse(h.preview.stopped, "Off already latches the board: nothing to copy")

    def test_one_connection_carries_the_calls_and_nothing_follows_a_redirect(self):
        r = self.rig()
        session = ha_link.HaSession(r.ha.url, TOKEN)
        self.addCleanup(session.close)
        for _ in range(5):
            session.call("c12", "stop", {"fixture": DEVICE}, response=True)
        self.assertEqual((session.connections, r.ha.connections), (1, 1))
        r.ha.down = True
        with self.assertRaises(ha_link.HaError) as failed:
            session.call("c12", "stop", {"fixture": DEVICE}, response=True)
        self.assertEqual(str(failed.exception), "Home Assistant didn't answer")
        r.ha.down = False
        session.call("c12", "stop", {"fixture": DEVICE}, response=True)
        session.used -= session.IDLE_S + 1
        session.call("c12", "stop", {"fixture": DEVICE}, response=True)
        self.assertEqual(session.connections, 3, "a new connection after a failure, and after idling")
        r.ha.redirect = True
        with self.assertRaises(ha_link.HaError) as moved:
            session.call("c12", "stop", {"fixture": DEVICE}, response=True)
        self.assertEqual(str(moved.exception), "Home Assistant said 302")
        r.ha.redirect = False
        stranger = ha_link.HaSession(r.ha.url, "wrong")
        self.addCleanup(stranger.close)
        with self.assertRaises(ha_link.HaError) as refused:
            stranger.call("c12", "stop", {"fixture": DEVICE}, response=True)
        self.assertEqual(str(refused.exception), "Home Assistant said 401")
        for error in (failed, moved, refused):
            self.assertNotIn(TOKEN, str(error.exception))
            self.assertNotIn(r.ha.url, str(error.exception))

    def test_every_frame_on_the_air_parses_with_the_pinned_firmware_codec(self):
        archive = test_trace_v2.firmware_zip()
        if archive is None:
            self.skipTest("the pinned firmware bundle isn't here")
        codec, vectors = c12_replay.firmware_codec(archive)
        for item in vectors["seq"]:
            self.assertEqual(fake_c12_ha.classify_seq(item["last"], item["seq"]), item["class"])
        r = self.rig()
        lit = r.light()
        r.keep(lit)
        r.emit("stop")
        r.emit("resume")
        r.pump()
        r.light(level=0)
        self.assertEqual([frame["cmd"] for frame in r.air()], [0, 1, 2, 3, 0])
        for frame in r.air():
            payload = bytes.fromhex(frame["payload_hex"])
            self.assertTrue(codec.payload_ok(frame["cmd"], payload))
            if frame["cmd"] == codec.CMD_MOVE:
                self.assertEqual(codec.encode_move(codec.parse_move(payload)), payload)
            elif frame["cmd"] == codec.CMD_KEEP_LIT:
                self.assertEqual(codec.encode_keep_lit(*struct.unpack("<HH", payload)), payload)


if __name__ == "__main__":
    unittest.main()
