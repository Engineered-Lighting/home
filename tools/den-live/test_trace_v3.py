"""Trace v3 (plan 05, step 05-3): rows a real Preview wrote and the FC13 sender's send rows, through c12_replay.

Two den-live runs on one board, with a restart between them: each run lights F3, then Home's Stop and Resume. The
sender calls the fake Home Assistant (fake_c12_ha), whose integration owns the wire seq. The replay's send-row checks
(1-4) run on the P3 board model; check 5 replays the frames through the v2-a host simulator, which is also the board,
when C12_TEST_SIM is set. Each check is shown refusing its own fault. The pinned firmware codec is needed
(test_trace_v2.firmware_zip); without it these tests skip.
"""
import copy
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import c12_replay
import c12_send
import fake_c12_ha
import ha_link
import preview_trace
import test_c12_send
import test_preview
import test_trace_v2

HERE = Path(__file__).resolve().parent
DEVICE = test_c12_send.DEVICE
TOKEN = test_c12_send.TOKEN
DESK = {"fixture_id": "F3", "device": DEVICE, "light": True}
EPOCH = 1_763_000_000.0   # the wall time the harness's clock starts from: one host clock for both runs


def jsonl(rows):
    return test_trace_v2.jsonl(rows)


class TraceV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_preview.PreviewTest.setUpClass()
        cls.addClassCleanup(test_preview.PreviewTest.doClassCleanups)
        archive = test_trace_v2.firmware_zip()
        cls.archive = archive
        cls.codec = c12_replay.firmware_codec(archive)[0] if archive else None

    def setUp(self):
        if self.codec is None:
            self.skipTest("the pinned firmware bundle isn't here")

    def record(self, board, *, desk=DESK):
        """Two den-live runs on one board, rows in file order: each run's header, its encoded rows, its send rows."""
        h = test_preview.PreviewTest("test_one_confident_yes_lights")
        h.setUp()
        self.addCleanup(h.doCleanups)
        if isinstance(board, fake_c12_ha.ModelBoard):
            board.clock = lambda: h.now
        integration = fake_c12_ha.Integration({DEVICE: board})
        ha = fake_c12_ha.FakeHa(integration, TOKEN).start()
        self.addCleanup(ha.close)
        runs = []

        def run():
            log = []
            header = h.preview.trace.header
            header.update(desk=desk, at_ts=EPOCH + header["at_mono"])   # 05-4 sets desk from desk.json
            log.append(header)
            sender = c12_send.Sender({"F3": DEVICE}, lambda: ha_link.HaSession(ha.url, TOKEN), record=log.append,
                                     clock=lambda: h.now, threaded=False)
            self.addCleanup(sender.close)
            store = h.preview.trace.append

            def write(row):
                store(row)
                log.append(row)
                sender.take(row)
                sender.pump()
            h.preview.trace.append = write
            h.light()
            h.now += .5
            _, stopped = h.preview.stop({})   # Home's Stop, then its Resume
            h.now += .5
            self.assertEqual(h.preview.resume(stopped)[0], 200)
            runs.append(log)

        run()
        h.now += 1
        h.preview = h.make_preview()   # den-live restarts: a new run, its trace seq from 0 again
        h.preview.configure({"fade_in_s": 0, "pre_aim": False})
        run()
        return runs, integration

    def verify(self, rows, wire=None):
        return c12_replay.verify_trace(jsonl(rows), self.codec, wire=wire)

    def sends(self, rows, action=None):
        return [row for row in rows if row.get("kind") == "send" and action in (None, row["action"])]

    def test_two_runs_on_one_board_pass_the_send_checks(self):
        runs, integration = self.record(fake_c12_ha.ModelBoard())
        rows = runs[0] + runs[1]
        self.assertEqual({row["schema"] for row in rows}, {preview_trace.SCHEMA})
        counts, _groups = self.verify(rows)
        self.assertEqual(counts["send"], len(self.sends(rows)))
        self.assertEqual(counts["wire"], len(integration.air), "every frame on the air has its send row")
        for log in runs:
            actions = {row["action"]: row["outcome"] for row in self.sends(log)}
            self.assertEqual(actions, {"aim_move": "acked", "keep_lit": "acked", "stop": "acked", "resume": "acked"})
        first = [row["sequence"] for row in runs[1] if row.get("status") == "encoded_only"]
        self.assertEqual(first[0], 0, "the second run's trace seq starts from 0 again")
        seqs = [row["wire"]["seq"] for row in self.sends(rows) if row["sent"]]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)), "the integration's count runs on across the restart")

    def test_each_send_check_refuses_its_fault(self):
        runs, _integration = self.record(fake_c12_ha.ModelBoard())
        rows = runs[0] + runs[1]

        def refused(change, reason):
            broken = copy.deepcopy(rows)
            change(broken)
            with self.assertRaisesRegex(c12_replay.ReplayError, reason):
                self.verify(broken)

        def first(broken, action, run=None):
            return next(row for row in self.sends(broken, action) if run in (None, row["run_id"]))

        def no_such_row(broken):
            first(broken, "aim_move")["trace_id"] += "-elsewhere"
        refused(no_such_row, "names no earlier encoded row")   # check 1

        def too_early(broken):
            send = first(broken, "aim_move")
            broken.remove(send)
            broken.insert(1, send)
        refused(too_early, "names no earlier encoded row")

        def other_aim(broken):
            wire = first(broken, "aim_move")["wire"]
            payload = bytearray(bytes.fromhex(wire["payload_hex"]))
            payload[3] ^= 1   # pan, low byte
            wire["payload_hex"] = payload.hex()
        refused(other_aim, "differs from the encoded payload")   # check 2

        def other_lease(broken):
            wire = first(broken, "keep_lit")["wire"]
            seq, lit_seq = struct.unpack("<HH", bytes.fromhex(wire["payload_hex"]))
            wire["payload_hex"] = struct.pack("<HH", seq, lit_seq + 1).hex()
        refused(other_lease, "lit_seq isn't the wire seq")   # check 3

        def count_from_zero(broken):   # an integration that restarted without reading last_seq
            second, renumbered = runs[1][0]["run_id"], {}
            for number, send in enumerate((s for s in self.sends(broken) if s["run_id"] == second and s["wire"]), 1):
                payload = bytearray(bytes.fromhex(send["wire"]["payload_hex"]))
                renumbered[send["wire"]["seq"]] = number
                payload[0:2] = struct.pack("<H", number)
                if send["action"] == "keep_lit":   # its lit_seq follows its move, so only check 4 can see it
                    payload[2:4] = struct.pack("<H", renumbered[struct.unpack_from("<H", payload, 2)[0]])
                send["wire"].update(seq=number, payload_hex=payload.hex())
        refused(count_from_zero, "refuse this wire seq as stale")   # check 4

        def repeated(broken):
            moves = [s for s in self.sends(broken) if s["wire"] and s["action"] != "stop"]
            earlier, later = moves[0], moves[1]
            payload = bytearray(bytes.fromhex(later["wire"]["payload_hex"]))
            payload[0:2] = struct.pack("<H", earlier["wire"]["seq"])
            later["wire"].update(seq=earlier["wire"]["seq"], payload_hex=payload.hex())
        refused(repeated, "repeats for another command")

        def virtual(broken):
            broken[0]["desk"] = None
        refused(virtual, "no desk mapping sent a command")

        def other_fixture(broken):
            broken[0]["desk"] = dict(DESK, device="another-device")
        refused(other_fixture, "other than its run's desk mapping")

    def test_calls_that_overlapped_are_ordered_by_the_integrations_count(self):
        runs, _integration = self.record(fake_c12_ha.ModelBoard())
        rows = runs[0]
        moves = [s for s in self.sends(rows) if s["wire"] and s["action"] in ("aim_move", "keep_lit")]
        a, b = moves[0], moves[1]   # b's seq is one after a's
        overlapped = copy.deepcopy(rows)
        later = next(s for s in overlapped if s.get("kind") == "send" and s["trace_id"] == b["trace_id"])
        later.update(called_mono=a["called_mono"] - .01, answered_mono=a["called_mono"] + .001)
        self.verify(overlapped)   # b was called first, but the calls overlapped: the count says a went first
        apart = copy.deepcopy(rows)
        later = next(s for s in apart if s.get("kind") == "send" and s["trace_id"] == b["trace_id"])
        later.update(called_mono=a["called_mono"] - .02, answered_mono=a["called_mono"] - .01)
        with self.assertRaisesRegex(c12_replay.ReplayError, "stale"):
            self.verify(apart)

    def test_the_boards_answers_replay_in_the_simulator(self):
        sim = test_c12_send.simulator()
        if not sim:
            self.skipTest("set C12_TEST_SIM to the pinned v2-a simulator")
        board = fake_c12_ha.SimBoard(sim)
        self.addCleanup(board.close)
        runs, integration = self.record(board)
        rows = runs[0] + runs[1]
        wire = c12_replay.WireCheck(self.codec)
        self.verify(rows, wire)
        delivered = sum(frame.delivered for frames in wire.frames.values() for frame in frames)
        self.assertEqual(c12_replay.replay_wire(sim, preview_trace.SIMULATOR_SHA256, wire), delivered)
        self.assertEqual(delivered, len(integration.air))
        answers = {row["answer"]["result"] for row in self.sends(rows, "aim_move") if row["answer"]}
        self.assertEqual(answers, {"lights_only"}, "v2-a sets no spot from a MOVE")
        self.assertTrue(all(row["outcome"] == "dropped_superseded" for row in self.sends(rows, "keep_lit")))

        for action, key, value, reason in (("aim_move", "result", "accepted", "answers this MOVE otherwise"),
                                           ("stop", "stop_latched", False, "Stop latch differs")):
            broken = copy.deepcopy(rows)
            next(row for row in self.sends(broken, action) if row["answer"])["answer"][key] = value
            other = c12_replay.WireCheck(self.codec)
            self.verify(broken, other)   # checks 1-4 can't see it: only the simulator knows the board's answer
            with self.assertRaisesRegex(c12_replay.ReplayError, reason):
                c12_replay.replay_wire(sim, preview_trace.SIMULATOR_SHA256, other)

        with tempfile.TemporaryDirectory() as folder:   # the command line, a file per run as LA writes them
            files = []
            for index, log in enumerate(runs):
                path = Path(folder) / f"run-{index}.jsonl"
                path.write_bytes(jsonl(log).getvalue())
                files.append(str(path))
            done = subprocess.run([sys.executable, "-B", str(HERE / "c12_replay.py"), *files, "--firmware-zip",
                                   str(self.archive), "--sim", sim, "--sim-sha256", preview_trace.SIMULATOR_SHA256],
                                  capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"{delivered} of them replayed per board", done.stdout)
        self.assertNotIn(TOKEN, json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
