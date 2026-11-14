"""den-live's sender against c12-service-1 as the firmware chat built it (P5, fitted at 4b9b1c7): the fit-up's points.

- `velocity_cdeg_s`, given at all, sets has_velocity: it goes only when den-live's own MOVE has velocity.
- `send_failed` has bytes but nothing on the air; `seq_unknown`, `dropped_expired` and `dropped_stopped` send nothing.
- A STOP with seq 0 means "no seq" and never moves last_seq; the integration's count skips 0.
- `stop` and `resume` take a fixture, or `all: true`.
- Nothing reports 0xFC13's attributes yet: the Stop copy ignores a latch that changed before den-live's own Resume.
"""
import datetime
import struct
import unittest

import c12_replay
import c12_send
import desk_report
import fake_c12_ha
import ha_link
import preview_trace
import test_c12_send
import test_desk
import test_preview
import test_trace_v2

DEVICE, DEVICE_2, TOKEN = test_c12_send.DEVICE, test_c12_send.DEVICE_2, test_c12_send.TOKEN
DESK = {"fixture_id": "F3", "device": DEVICE, "light": True}


def moving(velocity=(0, 0)):
    """A dark move that, given a velocity, arrives with it (a profile with terminal velocity)."""
    return dict(test_c12_send.move(), velocity_cdeg_s=velocity, terminal_velocity=True,
                max_velocity_cdeg_s=(3000, 3000))


def iso(seconds_from_now):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=seconds_from_now)).isoformat()


class Canned:
    """A client whose every call answers the same."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call(self, domain, service, data, *, response=True, timeout_s=None):
        self.calls.append((service, data))
        return self.answer

    def close(self):
        pass


class ServiceFitTest(unittest.TestCase):
    def rig(self, **kw):
        """test_c12_send's rig, its trace bound to a room model and a profile as the preview's is, with the desk."""
        r = test_c12_send.SenderTest.rig(self, **kw)
        r.trace = preview_trace.PreviewTrace("run-a", r.sender.after(r.rows.append), clock=r.clock,
                                             wall=lambda: 1_763_000_000.0 + r.clock(), binding=test_preview.BINDING,
                                             profiles={fid: {"label": fid} for fid in r.fixtures}, desk=DESK)
        return r

    def rows(self, r):
        """The run as LA's day file holds it: its header, its encoded rows, then its send rows."""
        return [r.trace.header, *r.rows, *r.sent]

    def replay(self, rows):
        archive = test_trace_v2.firmware_zip()
        if archive is None:
            self.skipTest("the pinned firmware bundle isn't here")
        wire = c12_replay.WireCheck(c12_replay.firmware_codec(archive)[0])
        c12_replay.verify_trace(test_trace_v2.jsonl(rows), wire.codec, wire=wire)
        return wire

    def test_velocity_goes_only_when_den_lives_move_has_it(self):
        r = self.rig()
        still = r.emit("aim_move", decision_id="d1", move=moving())
        r.pump()
        sent_data = r.ha.calls[-1][1]
        self.assertNotIn("velocity_cdeg_s", sent_data, "given at all, it would set has_velocity")
        self.assertEqual(bytes.fromhex(r.sent[-1]["wire"]["payload_hex"])[2], still["effective"]["flags"])
        fast = r.emit("aim_move", decision_id="d2", move=moving((500, -200)))
        r.pump()
        self.assertEqual(fast["effective"]["flags"] & c12_send.HAS_VELOCITY, c12_send.HAS_VELOCITY)
        self.assertEqual(r.ha.calls[-1][1]["velocity_cdeg_s"], [500, -200])
        self.assertTrue(all(row["matches_trace"] for row in r.sent), "the wire is the trace, but for the seq")
        flagged = r.integration.move(dict(sent_data, velocity_cdeg_s=[0, 0]))   # what the old sender sent
        self.assertEqual(bytes.fromhex(flagged["payload_hex"])[2] & c12_send.HAS_VELOCITY, c12_send.HAS_VELOCITY)
        self.replay(self.rows(r))

    def test_send_failed_has_bytes_but_nothing_on_the_air(self):
        r = self.rig()
        lit = r.light()
        r.integration.refuse_radio[DEVICE] = 1
        keep = r.keep(lit)
        self.assertEqual((keep["outcome"], keep["sent"], keep["answer"]["status"]), ("refused", False, "send_failed"))
        r.integration.refuse_radio[DEVICE] = 2   # the move's two tries
        failed = r.light(target=(1500, -300))
        row = r.sent[-1]
        self.assertEqual((row["outcome"], row["sent"], row["answer"]["result"]), ("refused", False, "send_failed"))
        self.assertIsNotNone(row["wire"], "the bytes the integration tried")
        self.assertEqual(r.released[-1], ("F3", failed["trace_id"], "c12_send_failed"))
        wire = self.replay(self.rows(r))
        on_air = [frame for frames in wire.frames.values() for frame in frames if frame.delivered]
        self.assertEqual(len(on_air), len(r.integration.air), "only what the radio took counts as sent")
        lines, ok = desk_report.report(self.rows(r))
        self.assertTrue(ok, lines)

    def test_answers_that_send_nothing_are_refusals(self):
        for result in ("seq_unknown", "dropped_expired", "dropped_stopped"):
            with self.subTest(result=result):
                r = self.rig()
                canned = Canned({"seq": None, "payload_hex": None, "result": result, "predicted_ms": None})
                r.sender.clients[c12_send.CONTROL] = r.sender.clients["F3"] = canned
                move = r.emit("aim_move", decision_id="d1", move=test_c12_send.move())
                r.pump()
                self.assertEqual((r.sent[-1]["outcome"], r.sent[-1]["sent"], r.sent[-1]["wire"]),
                                 ("refused", False, None))
                self.assertEqual(r.released, [("F3", move["trace_id"], "c12_" + result)])
                self.assertEqual(r.copied, [], "only a move refused stopped means a Stop den-live didn't send")

    def test_a_stop_with_seq_zero_never_moves_the_count(self):
        r = self.rig()
        board = r.boards[DEVICE]
        board.last = 65000
        r.integration.start()
        r.light()
        r.integration.read_failures = 1
        r.integration.seq.pop(DEVICE)   # the integration restarted and can't read last_seq yet
        r.emit("stop")
        r.pump()
        self.assertEqual(r.sent[-1]["wire"]["seq"], 0, "Stop still goes, with seq 0: no seq")
        self.assertEqual(board.last_seq(), 65001, "from v2-b, a STOP with seq 0 leaves last_seq alone")
        r.emit("resume")
        r.pump()
        r.light(target=(1500, -300))
        self.assertEqual((r.sent[-1]["wire"]["seq"], r.sent[-1]["answer"]["result"]), (65003, "accepted"))
        self.replay(self.rows(r))   # check 4: the STOP's seq 0 isn't taken as the count going back
        lines, ok = desk_report.report(self.rows(r))
        self.assertTrue(ok, lines)

    def test_the_count_skips_zero(self):
        r = self.rig()
        r.boards[DEVICE].last = 65535
        r.integration.start()
        r.light()
        self.assertEqual((r.sent[-1]["wire"]["seq"], r.sent[-1]["answer"]["result"]), (1, "accepted"))

    def test_stop_and_resume_take_a_fixture_or_all(self):
        r = self.rig(devices=(DEVICE, DEVICE_2))
        both = r.integration.stop({"all": True})["fixtures"]
        self.assertEqual(set(both), {DEVICE, DEVICE_2})
        self.assertTrue(all(entry["stop_latched"] for entry in both.values()))
        self.assertEqual(set(r.integration.resume({"fixture": "all"})["fixtures"]), {DEVICE, DEVICE_2})
        with self.assertRaises(fake_c12_ha.BadRequest):
            r.integration.stop({"all": True, "fixture": DEVICE})
        session = ha_link.HaSession(r.ha.url, TOKEN)
        self.addCleanup(session.close)
        with self.assertRaisesRegex(ha_link.HaError, "said 400"):
            session.call("c12", "stop", {"all": "yes"}, response=True)

    def test_the_stop_copy_ignores_a_latch_that_changed_before_dens_own_resume(self):
        wall = test_c12_send.Clock(1_763_000_000.0)
        r = self.rig()
        r.sender.wall = wall
        r.emit("stop")
        r.emit("resume")
        r.pump()   # den-live's own Stop and Resume, answered at wall 1_763_000_000
        r.clock.now += 3
        r.sender.observe("F3", True, wall() - 3600)   # an hour-old value nothing has reported since
        r.sender.observe("F3", True, wall() + .5)     # inside the clocks' skew of the Resume
        self.assertEqual(r.copied, [])
        r.sender.observe("F3", True, wall() + 5)      # changed after it: a Stop den-live didn't send
        self.assertEqual(r.copied, [("F3", "stop_latched_read")])

    def test_the_desk_reads_when_the_latch_changed(self):
        harness = test_desk.DeskTest("test_the_desk_file_is_checked")
        test_desk.DeskTest.setUpClass()
        self.addCleanup(test_desk.DeskTest.doClassCleanups)
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        on, _board, ha = harness.desk_preview()
        h = harness.h
        h.light()
        _, stopped = h.preview.stop({})
        self.assertEqual(h.preview.resume(stopped)[0], 200)   # den-live's own Stop and Resume
        h.now += 3
        latch = test_desk.READBACK["stop_latched"]
        ha.states[latch], ha.changed[latch] = "on", iso(-3600)   # stale: nothing reports 0xFC13 yet
        on.poller.poll_once()
        self.assertFalse(h.preview.stopped, "an old latch value doesn't stop Home after its own Resume")
        ha.changed[latch] = iso(5)
        on.poller.poll_once()
        self.assertTrue(h.preview.stopped, "a latch that changed after it is a Stop den-live didn't send")
        session = ha_link.HaSession(ha.url, TOKEN)
        self.addCleanup(session.close)
        self.assertEqual(session.state(latch)["last_changed"], ha.changed[latch])


if __name__ == "__main__":
    unittest.main()
