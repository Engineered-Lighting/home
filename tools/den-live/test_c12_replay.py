"""Replay tests. Set C12_TEST_FIRMWARE_ZIP for pinned-codec conformance tests."""

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import struct
import unittest

import c12_fc13
import c12_replay


SYNTHETIC_IDENTITY = {"registration_revision": "synthetic-rev-1", "profile_sha256": "a" * 64}


def golden_trace():
    data = json.loads((Path(__file__).parent / "fixtures" / "c12-preview-golden.json").read_text(encoding="utf-8"))
    rows, replies = [], []
    joints, move_seq, move_decision_id = data["start_joints_cdeg"], None, None
    for index, step in enumerate(data["steps"], 1):
        intent, seq = step["intent"], step["seq"]
        action, decision_id = intent["action"], intent.get("decision_id")
        encoded = c12_fc13.encode(intent, data["profile"], seq=seq, current_joints=joints, token=move_seq)
        if action == "aim_move":
            joints = [intent["pan_cdeg"], intent["tilt_cdeg"]]
            move_seq, move_decision_id = seq, decision_id
        elif action in ("stop", "resume"):
            move_seq, move_decision_id = None, None
        frame = {name: encoded[name] for name in ("endpoint", "cluster", "command_id")}
        frame.update(seq=seq, payload_hex=encoded["payload"].hex())
        if frame != step["frame"]:
            raise AssertionError(f"encoder changed golden frame {index}")
        rows.append(dict(schema=c12_replay.TRACE_SCHEMA, run_id="synthetic-run", event_index=index,
                         fixture_id=data["fixture_id"], status="encoded_only", action=action, sent=False,
                         frame=frame, effective=encoded["effective"], move_seq=move_seq,
                         move_decision_id=move_decision_id, decision_id=decision_id, **SYNTHETIC_IDENTITY))
        replies.append(step["sim_reply"])
    return rows, replies


def jsonl(rows):
    return io.BytesIO("".join(json.dumps(row) + "\n" for row in rows).encode("utf-8"))


class EnvelopeTests(unittest.TestCase):
    def test_bad_rows_are_private_and_cannot_claim_transport(self):
        for stream, message in (
            (io.BytesIO(b'{"run_id":"private","run_id":"duplicate"}\n'), "duplicate keys"),
            (io.BytesIO(b'{"person":"private","time":NaN}\n'), "non-finite constant"),
            (jsonl([dict(schema=c12_replay.TRACE_SCHEMA, run_id="private", fixture_id="F3", event_index=1,
                         status="refused", action="aim_move", sent=True, **SYNTHETIC_IDENTITY)]), "cannot claim a transmission"),
            (jsonl([dict(schema=c12_replay.TRACE_SCHEMA, run_id="private", fixture_id="F3", event_index=2,
                         status="refused", action="aim_move", **SYNTHETIC_IDENTITY)]), "must be consecutive"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(c12_replay.ReplayError, f"^line 1: .*{message}"):
                c12_replay.verify_trace(stream, None)

    def test_golden_recreates_pinned_frames(self):
        rows, _ = golden_trace()
        self.assertEqual([row["frame"]["command_id"] for row in rows], [0, 1, 2, 3, 0])


class PinnedFirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = os.environ.get("C12_TEST_FIRMWARE_ZIP")
        if not archive:
            raise unittest.SkipTest("set C12_TEST_FIRMWARE_ZIP to run pinned codec and simulator conformance")
        cls.codec, cls.vectors = c12_replay.firmware_codec(archive)

    def test_vectors_and_golden_trace(self):
        self.assertEqual(c12_replay.verify_vectors(self.codec, self.vectors), 28)
        rows, replies = golden_trace()
        counts, groups = c12_replay.verify_trace(jsonl(rows), self.codec, collect_sim=True)
        self.assertEqual(counts, {"encoded_only": 5, "refused": 0, "state": 0, "send": 0, "device": 0, "wire": 0})
        frames = groups[("synthetic-run", "F3")]
        self.assertEqual([frame.expected_reply for frame in frames], replies)
        self.assertEqual([frame.payload_hex for frame in frames], [row["frame"]["payload_hex"] for row in rows])

    def test_rejects_payload_drift_and_old_keep_lit(self):
        rows, _ = golden_trace()
        edited = deepcopy(rows)
        edited[0]["effective"]["pan_cdeg"] += 1
        with self.assertRaisesRegex(c12_replay.ReplayError, "line 1: effective MOVE fields disagree"):
            c12_replay.verify_trace(jsonl(edited), self.codec)
        ended = dict(rows[1], status="state", action="expired", frame=None)
        late_keep = dict(rows[1], event_index=3)
        with self.assertRaisesRegex(c12_replay.ReplayError, "line 3: KEEP_LIT does not renew"):
            c12_replay.verify_trace(jsonl([rows[0], ended, late_keep]), self.codec)

    def test_rejects_fixture_identity_change_within_run(self):
        rows, _ = golden_trace()
        for field, value in (("registration_revision", "different-rev"), ("profile_sha256", "b" * 64)):
            with self.subTest(field=field), self.assertRaisesRegex(c12_replay.ReplayError, "line 2: fixture registration or profile changed"):
                c12_replay.verify_trace(jsonl([rows[0], dict(rows[1], **{field: value})]), self.codec)

    def test_wraps_and_keeps_run_and_fixture_state_separate(self):
        rows, _ = golden_trace()
        first = deepcopy(rows[0])
        first["frame"].update(seq=65535, payload_hex="ffff" + first["frame"]["payload_hex"][4:])
        first["move_seq"] = 65535
        keep = deepcopy(rows[1])
        keep["frame"].update(seq=0, payload_hex=struct.pack("<HH", 0, 65535).hex())
        keep["effective"]["token"] = keep["move_seq"] = 65535
        other_fixture = dict(first, fixture_id="F4", event_index=2)
        other_run = dict(first, run_id="another-run")
        counts, groups = c12_replay.verify_trace(jsonl([first, other_fixture, other_run, dict(keep, event_index=3)]),
                                                 self.codec, collect_sim=True)
        self.assertEqual(counts["encoded_only"], 4)
        self.assertEqual([len(group) for group in groups.values()], [2, 1, 1])

    def test_stop_cannot_be_bypassed_by_a_move(self):
        rows, _ = golden_trace()
        first_stop = dict(rows[2], event_index=1)
        next_move = deepcopy(rows[-1])
        next_move["event_index"] = 2
        next_move["frame"].update(seq=10, payload_hex="0a00" + next_move["frame"]["payload_hex"][4:])
        next_move["move_seq"] = 10
        with self.assertRaisesRegex(c12_replay.ReplayError, "line 2: MOVE emitted while preview Stop is latched"):
            c12_replay.verify_trace(jsonl([first_stop, next_move]), self.codec)

    def test_optional_native_simulator(self):
        executable = os.environ.get("C12_TEST_SIM")
        digest = os.environ.get("C12_TEST_SIM_SHA256")
        if not (executable and digest):
            self.skipTest("set C12_TEST_SIM and C12_TEST_SIM_SHA256 to run the locally built simulator")
        rows, _ = golden_trace()
        rows += [dict(row, run_id="independent-run") for row in rows]
        _, groups = c12_replay.verify_trace(jsonl(rows), self.codec, collect_sim=True)
        self.assertEqual(c12_replay.replay_simulator(executable, digest, groups), 10)
        with self.assertRaisesRegex(c12_replay.ReplayError, "simulator SHA-256 differs"):
            c12_replay.replay_simulator(executable, "0" * 64, groups)


if __name__ == "__main__":
    unittest.main()
