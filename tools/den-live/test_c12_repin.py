"""The codec re-pin to firmware 62ea043 (the timed MOVE), and the sender's rule that a timed MOVE goes only to a board
that advertises it. Needs the pinned bundle (test_trace_v2.firmware_zip); the simulator parts need C12_TEST_SIM.
"""
import copy
import hashlib
import unittest
from pathlib import Path

import c12_fc13
import c12_replay
import fake_c12_ha
import preview_trace
import test_c12_replay
import test_c12_send
import test_desk
import test_preview
import test_trace_v2

DEVICE, TOKEN = test_c12_send.DEVICE, test_c12_send.TOKEN
PROFILE = {"frame": c12_fc13.FRAME, "range_cdeg": {"pan": [-17500, 17500], "tilt": [-9000, 8500]},
           "host_inset_cdeg": 0, "motion_mode": 1, "lit_motion": True}


def intent_for(fields):
    """A preview's aim_move intent for one of the firmware's MOVE vectors."""
    flags = fields["flags"]
    intent = {"fixture_id": "F3", "action": "aim_move", "decision_id": "d", "pan_cdeg": fields["pan_cdeg"],
              "tilt_cdeg": fields["tilt_cdeg"], "level": fields["spot_level"] / 254,
              "cct_k": 1_000_000 / fields["spot_mired"], "valid_for_ms": fields["valid_ms"],
              "lit_during_move": bool(flags & 1), "motion_style": "lit" if flags & 1 else "dark"}
    if flags & c12_fc13.TIMED:
        intent["timed"] = {"min_duration_ms": fields["min_duration_ms"], "fade_ms": fields["fade_ms"],
                           "fade_curve": c12_fc13.FADE_CURVES[(flags >> 5) & 3]}
    return intent


def timed_move(**timed):
    return dict(test_c12_send.move(), timed=timed or {"min_duration_ms": 4000, "fade_ms": 1500,
                                                       "fade_curve": "ease_out"})


class RepinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = test_trace_v2.firmware_zip()
        if archive is None:
            raise unittest.SkipTest("the pinned firmware bundle isn't here")
        cls.codec, cls.vectors = c12_replay.firmware_codec(archive)

    def rig(self, board=None):
        r = test_c12_send.SenderTest.rig(self, board=board)
        r.trace = preview_trace.PreviewTrace("run-a", r.sender.after(r.rows.append), clock=r.clock,
                                             wall=lambda: 1_763_000_000.0 + r.clock(), binding=test_preview.BINDING,
                                             profiles={"F3": {"label": "F3"}},
                                             desk={"fixture_id": "F3", "device": DEVICE, "light": True})
        return r

    def test_the_pins_are_the_new_firmwares(self):
        self.assertEqual(c12_replay.verify_vectors(self.codec, self.vectors), 37)
        header = preview_trace.PreviewTrace("run-a", lambda _row: None).header
        self.assertEqual((header["firmware"], header["simulator_sha256"]),
                         ("62ea043", "9daa2b2f1517a6386ed3d7c5aaee0e2710d3142699d4e682219b05f3fa577bf4"))
        source = Path(c12_fc13.__file__).read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(header["codec_sha256"], hashlib.sha256(source).hexdigest())

    def test_den_lives_encoder_makes_the_firmwares_timed_vectors(self):
        vectors = {item["name"]: item for item in self.vectors["moves"]}
        for name in ("lit_move", "timed_fade", "bloom_ease_in_out"):
            with self.subTest(name=name):
                fields = vectors[name]["fields"]
                start = [0, 0] if (fields["pan_cdeg"], fields["tilt_cdeg"]) != (0, 0) else [100, 0]
                frame = c12_fc13.encode(intent_for(fields), PROFILE, seq=fields["seq"], current_joints=start)
                self.assertEqual(frame["payload"].hex(), vectors[name]["hex"])
                effective = frame["effective"]
                if fields["flags"] & c12_fc13.TIMED:
                    self.assertEqual((effective["timed"], effective["min_duration_ms"], effective["fade_ms"],
                                      effective["fade_curve"], effective["fade_supported"]),
                                     (True, fields["min_duration_ms"], fields["fade_ms"],
                                      c12_fc13.FADE_CURVES[(fields["flags"] >> 5) & 3], True))
                else:
                    self.assertNotIn("timed", effective, "an untimed MOVE's effective fields are as before")
                    self.assertIs(effective["fade_supported"], False)

    def test_a_timed_move_is_refused_where_the_firmware_would_refuse_it(self):
        base = intent_for({"flags": 8, "pan_cdeg": 1200, "tilt_cdeg": -300, "spot_level": 127, "spot_mired": 370,
                           "valid_ms": 1000})
        velocity = dict(PROFILE, terminal_velocity=True, max_velocity_cdeg_s=[3000, 3000])
        for timed, profile, extra, reason in (
                ({"min_duration_ms": 4000}, velocity, {"velocity_cdeg_s": [500, 0]}, "timed_with_velocity"),
                ({"fade_curve": "ease_out"}, PROFILE, {}, "fade_curve_without_fade"),
                ({"fade_ms": 900, "fade_curve": "sudden"}, PROFILE, {}, "invalid_fade_curve"),
                ({"fade_ms": 900, "speed": 2}, PROFILE, {}, "invalid_timed"),
                ({"min_duration_ms": 70000}, PROFILE, {}, "invalid_min_duration_ms")):
            with self.subTest(reason=reason), self.assertRaisesRegex(c12_fc13.Refusal, reason):
                c12_fc13.encode(dict(base, timed=timed, **extra), profile, seq=1, current_joints=[0, 0])

    def test_the_trace_carries_a_timed_move_and_the_replay_checks_it(self):
        r = self.rig()
        row = r.emit("aim_move", decision_id="d1", move=timed_move())
        self.assertEqual(row["effective"]["flags"] & c12_fc13.TIMED, c12_fc13.TIMED)
        self.assertEqual(row["effective"]["fade_s"], 1.5)
        plain = r.emit("aim_move", decision_id="d2", move=test_c12_send.move(target=(1500, -300)))
        rows = [r.trace.header, *r.rows]
        c12_replay.verify_trace(test_trace_v2.jsonl(rows), self.codec)
        claimed = copy.deepcopy(rows)
        next(x for x in claimed if x.get("trace_id") == plain["trace_id"])["effective"]["timed"] = True
        with self.assertRaisesRegex(c12_replay.ReplayError, "claims a timed MOVE"):
            c12_replay.verify_trace(test_trace_v2.jsonl(claimed), self.codec)

    def test_a_timed_move_goes_only_to_a_board_that_advertises_it(self):
        old = self.rig(board=fake_c12_ha.ModelBoard(capabilities=None))   # v2-a: no capabilities attribute
        old.sender.set_capabilities("F3", old.integration.get_profile({"fixture": DEVICE})["capabilities"])
        row = old.emit("aim_move", decision_id="d1", move=timed_move())
        old.pump()
        self.assertEqual((old.sent[-1]["outcome"], old.sent[-1]["sent"]), ("dropped_unsupported", False))
        self.assertEqual((old.integration.air, old.released), ([], [("F3", row["trace_id"], "timed_move_unsupported")]))
        old.emit("aim_move", decision_id="d2", move=test_c12_send.move())
        old.pump()
        self.assertEqual(old.sent[-1]["answer"]["result"], "accepted", "the untimed MOVE still goes")

        new = self.rig()   # from v2-b: capabilities 15
        new.sender.set_capabilities("F3", new.integration.get_profile({"fixture": DEVICE})["capabilities"])
        new.emit("aim_move", decision_id="d1", move=timed_move())
        new.pump()
        data = new.ha.calls[-1][1]
        self.assertEqual((data["min_duration_ms"], data["fade_ms"], data["fade_curve"]), (4000, 1500, "ease_out"))
        self.assertNotIn("velocity_cdeg_s", data)
        self.assertEqual((new.sent[-1]["answer"]["result"], new.sent[-1]["matches_trace"]), ("accepted", True))
        c12_replay.verify_trace(test_trace_v2.jsonl([new.trace.header, *new.rows, *new.sent]), self.codec)

        wrong = self.rig(board=fake_c12_ha.ModelBoard(capabilities=None))   # told it can, but it's v2-a
        wrong.sender.set_capabilities("F3", {"bits": 15, "timed_move": True})
        wrong.emit("aim_move", decision_id="d1", move=timed_move())
        wrong.pump()
        self.assertEqual(wrong.sent[-1]["answer"]["result"], "bad_payload")
        wrong.emit("aim_move", decision_id="d2", move=timed_move(min_duration_ms=2000))
        wrong.pump()
        self.assertEqual(wrong.sent[-1]["outcome"], "dropped_unsupported", "a bad_payload takes the capability back")

    def test_the_desk_reads_the_boards_capabilities_at_its_start(self):
        harness = test_desk.DeskTest("test_the_desk_file_is_checked")
        test_desk.DeskTest.setUpClass()
        self.addCleanup(test_desk.DeskTest.doClassCleanups)
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        on, board, _ha = harness.desk_preview()
        self.assertEqual((on.capabilities["bits"], on.sender.timed["F3"]), (15, True))
        self.assertEqual(on.devices()["F3"]["capabilities"], 15)
        board.capabilities = None   # Light 1 back on v2-a
        on.read_capabilities()
        self.assertEqual((on.capabilities, on.sender.timed["F3"], on.devices()["F3"]["timed"]), (None, False, False))
        profile = dict(harness.profile, capabilities={"bits": 15, "timed_move": True, "fade_curves": True,
                                                      "join_flight": True, "at_rest": True})
        self.assertEqual(test_desk.desk.parse(harness.content(profile=profile), test_desk.FIXTURES)["profile"],
                         profile, "the desk profile may carry the capabilities the direction plays by")

    def test_the_pinned_simulator_takes_a_timed_move_once_commissioned(self):
        sim = test_c12_send.simulator()
        if not sim:
            self.skipTest("set C12_TEST_SIM to the pinned simulator")
        board = fake_c12_ha.SimBoard(sim, c12_replay.vector_record(self.vectors))
        self.addCleanup(board.close)
        self.assertEqual(board.capabilities, 15)
        r = self.rig(board=board)
        r.sender.set_capabilities("F3", r.integration.get_profile({"fixture": DEVICE})["capabilities"])
        r.light()
        r.emit("aim_move", decision_id="d2", move=dict(timed_move(), target_cdeg=(1500, -300)))
        r.pump()
        self.assertEqual([row["answer"]["result"] for row in r.sent], ["accepted", "accepted"])
        bare = fake_c12_ha.SimBoard(sim)
        self.addCleanup(bare.close)
        unready = self.rig(board=bare)
        unready.light()
        self.assertEqual(unready.sent[-1]["answer"]["result"], "needs_commissioning", "a board with no record")
        rows, _replies = test_c12_replay.golden_trace()
        _counts, groups = c12_replay.verify_trace(test_trace_v2.jsonl(rows), self.codec, collect_sim=True)
        record = c12_replay.vector_record(self.vectors)
        self.assertEqual(c12_replay.replay_simulator(sim, preview_trace.SIMULATOR_SHA256, groups), 5)
        self.assertEqual(c12_replay.replay_simulator(sim, preview_trace.SIMULATOR_SHA256, groups, record), 5)


if __name__ == "__main__":
    unittest.main()
