"""Virtual manual ownership ordering and durable recovery through Preview's real entry points."""
import copy
import json
import unittest
from unittest.mock import patch

import test_preview_manual as base


class ManualAuthorityTest(base.PreviewManualTest):
    def test_failed_edit_and_resume_are_unpublished_and_retryable(self):
        name = self.light()
        data = self.event(name)
        before = copy.deepcopy(self.preview.latest)
        with patch.object(self.preview.manual_log, "append", side_effect=OSError("full")):
            self.assertEqual(self.preview.manual_change(data)[0], 503)
        self.assertEqual(self.preview.latest, before)
        self.assertEqual(self.preview.manual_change(data)[0], 200)
        hand = copy.deepcopy(self.fixtures()[name]["hand"])
        resume = {"fixture_id": name, "hold_id": data["hold_id"], "expected_revision": hand["revision"],
                  "request_id": "resume-0001"}
        with patch.object(self.preview.manual_log, "append", side_effect=OSError("full")):
            self.assertEqual(self.preview.manual_resume(resume)[0], 503)
        self.assertEqual(self.fixtures()[name]["hand"], hand)
        self.preview = self.make_preview()
        self.assertEqual(self.fixtures()[name]["hand"]["hold_id"], data["hold_id"])
        self.assertEqual(self.preview.manual_resume(resume)[0], 200)

    def test_edit_during_recovery_keeps_hold_and_automatic_release_failure_keeps_owner(self):
        name = self.light()
        self.assertEqual(self.preview.manual_change(self.event(name))[0], 200)
        self.preview = self.make_preview()
        newer = self.event(name, seq=2, revision=0, level=.1)
        self.assertEqual(self.preview.manual_change(newer)[0], 200)
        self.assertIsNotNone(self.fixtures()[name]["hand"])
        self.assertTrue(self.fixtures()[name]["hand"]["recovering"])
        encoded = len(self.encoded)
        with patch.object(self.preview.manual_log, "append", side_effect=OSError("full")):
            self.stand()
        self.assertIsNotNone(self.fixtures()[name]["hand"])
        self.assertEqual(len(self.encoded), encoded)
        self.stand()
        self.assertIsNone(self.fixtures()[name]["hand"])

    def test_unreadable_journal_stops_automatic_authority(self):
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
            restarted = self.make_preview()
        self.assertTrue(restarted.stopped)
        self.assertIn("unreadable", restarted.problem)
        self.assertEqual(restarted.resume({"stop_counter": restarted.stop_counter})[0], 503)
        self.assertTrue(restarted.stopped)
        self.assertEqual(restarted.manual_resume({"fixture_id": "F3"})[0], 503)
        self.assertEqual(restarted.manual_change(self.event("F3"))[0], 503)

    def test_invalid_authority_cannot_be_bypassed_with_resume(self):
        self.preview.manual_log.append({"schema": "den-manual-checkpoint-1", "authority": {"fixtures": {}}})
        restarted = self.make_preview()
        self.assertTrue(restarted.stopped)
        self.assertEqual(restarted.resume({"stop_counter": restarted.stop_counter})[0], 503)
        self.assertEqual(restarted.learned_clear()[0], 503)

    def test_malformed_legacy_receipts_are_skipped_and_bad_resume_receipt_latches(self):
        self.preview.manual_log.append({"event": {"event_id": "broken-0001"}})
        self.preview.manual_log.append({"event": {"event_id": "broken-0002", "fixture_id": "F9"}})
        restarted = self.make_preview()
        self.assertFalse(restarted.stopped)
        self.assertEqual(restarted.manual_log.skipped, 2)
        restarted.manual_log.append({**restarted._manual_checkpoint(), "resume": {}})
        recovered = self.make_preview()
        self.assertTrue(recovered.stopped)
        self.assertEqual(recovered.resume({"stop_counter": recovered.stop_counter})[0], 503)

    def test_mixed_parts_report_latest_event_and_learning_is_one_correction_after_restart(self):
        name = self.light()
        first = self.event(name)
        self.assertEqual(self.preview.manual_change(first)[0], 200)
        second = self.event(name, seq=2, revision=0)
        second["before"], second["after"] = {"downward": {"brightness": .5}}, {"downward": {"brightness": .1}}
        self.assertEqual(self.preview.manual_change(second)[0], 200)
        self.assertEqual(self.fixtures()[name]["hand"]["event_id"], second["event_id"])
        self.preview = self.make_preview()
        self.seat()
        self.read()
        self.frames(3)
        third = self.event(name, seq=3, revision=0, level=.1)
        third["before"]["spotlight"]["brightness"] = .2
        self.assertEqual(self.preview.manual_change(third)[0], 200)
        self.assertEqual(self.preview.learning.entries["task/evening"]["spot"]["n"], 1)
        self.assertEqual(self.make_preview().learning.public(), self.preview.learning.public())

    def test_legacy_log_migration_does_not_resurrect_holds(self):
        name = self.light()
        self.dim(name)
        path = self.preview.manual_log.path
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            record.pop("authority", None)
        path.write_text("\n".join(json.dumps(row) for row in records) + "\n")
        self.preview = self.make_preview()
        self.assertIsNone(self.fixtures()[name]["hand"])
        self.assertTrue(self.learned())

    def test_unknown_fixture_reused_event_id_is_rejected(self):
        name = self.light()
        data = self.event(name)
        self.assertEqual(self.preview.manual_change(data)[0], 200)
        data["fixture_id"] = "F9"
        self.assertEqual(self.preview.manual_change(data)[0], 400)

    def test_repeat_on_preserves_a_newer_manual_hold(self):
        name = self.light()
        self.assertEqual(self.preview.manual_change(self.event(name))[0], 200)
        hand = copy.deepcopy(self.fixtures()[name]["hand"])
        revision = self.preview.control_revision
        self.assertEqual(self.preview.switch({"on": True})[0], 200)
        self.assertEqual(self.fixtures()[name]["hand"], hand)
        self.assertEqual(self.preview.control_revision, revision)

    def test_ordering_fields_are_all_required_and_strictly_typed(self):
        import manual
        data = self.event("F3")
        self.assertEqual(manual.parse(data)["client_seq"], 1)
        for update in ({"client_seq": True}, {"expected_revision": False}, {"client_seq": 0},
                       {"hold_id": "bad"}, {"client_id": ""}, {"expected_revision": -1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                manual.parse({**data, **update})
        for key in ("client_id", "client_seq", "hold_id", "expected_revision"):
            incomplete = dict(data)
            incomplete.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                manual.parse(incomplete)

    def test_legacy_retry_with_changed_body_is_a_conflict(self):
        name = self.light()
        data = self.event(name)
        for key in ("client_id", "client_seq", "hold_id", "expected_revision"):
            data.pop(key)
        self.assertEqual(self.preview.manual_change(data)[0], 200)
        data["after"]["spotlight"]["brightness"] = .9
        self.assertEqual(self.preview.manual_change(data)[0], 409)

    def event(self, name, seq=1, hold="hold-0001", revision=None, level=.2):
        row = self.fixtures()[name]
        return {"schema": "den-manual-1", "event_id": "event-%04d-%s" % (seq, hold), "fixture_id": name,
                "before": {"spotlight": {"brightness": row["level"]}},
                "after": {"spotlight": {"brightness": level}}, "at_ms": 1,
                "snapshot": {"epoch": self.preview.epoch, "seq": self.preview.latest["seq"]},
                "client_id": "client-0001", "client_seq": seq, "hold_id": hold,
                "expected_revision": self.fixtures()[name].get("manual_revision", 0) if revision is None else revision}

    def test_newer_edit_wins_out_of_order_and_retry_body_is_checked(self):
        name = self.light()
        older, newer = self.event(name), self.event(name, seq=2, level=.3)
        self.assertEqual(self.preview.manual_change(newer)[0], 200)
        self.assertEqual(self.preview.manual_change(older)[0], 409)
        first = self.preview.manual_change(newer)
        self.assertEqual(first[0], 200)
        changed = copy.deepcopy(newer)
        changed["after"]["spotlight"]["brightness"] = .9
        self.assertEqual(self.preview.manual_change(changed)[0], 409)
        self.assertEqual(self.fixtures()[name]["hand"]["state"]["spotlight"]["brightness"], .3)

    def test_stale_resume_cannot_release_newer_edit_and_closure_survives_restart(self):
        name = self.light()
        event = self.event(name)
        self.assertEqual(self.preview.manual_change(event)[0], 200)
        stale = {"fixture_id": name, "hold_id": event["hold_id"], "expected_revision": 1,
                 "request_id": "resume-0001"}
        newer = self.event(name, seq=2, revision=0, level=.1)
        self.assertEqual(self.preview.manual_change(newer)[0], 200)
        self.assertEqual(self.preview.manual_resume(stale)[0], 409)
        self.assertEqual(self.preview.manual_resume({"fixture_id": name})[0], 409)
        current = {**stale, "expected_revision": 2, "request_id": "resume-0002"}
        answer = self.preview.manual_resume(current)
        self.assertEqual(answer[0], 200)
        self.preview = self.make_preview()
        self.assertEqual(self.preview.manual_resume(current)[0], 200)
        self.assertEqual(self.preview.manual_change(self.event(name, seq=3, revision=0))[0], 409)
        self.assertIsNone(self.fixtures()[name]["hand"])

    def test_restart_restores_hold_values_without_a_move_and_clear_preserves_owner(self):
        name = self.light()
        event = self.event(name)
        self.assertEqual(self.preview.manual_change(event)[0], 200)
        learned = self.learned()
        self.preview = self.make_preview()
        hand = self.fixtures()[name]["hand"]
        self.assertIsNotNone(hand)
        self.assertEqual(hand["hold_id"], event["hold_id"])
        self.assertEqual(hand["state"]["spotlight"]["brightness"], .2)
        self.assertEqual(self.learned(), learned)
        self.assertEqual(self.preview.learned_clear()[0], 200)
        self.preview = self.make_preview()
        self.assertEqual(self.learned(), {})
        self.assertEqual(self.fixtures()[name]["hand"]["hold_id"], event["hold_id"])
        self.assertEqual(self.preview.manual_change(self.event(name, revision=0))[0], 409)

    def test_stop_persists_closure_and_old_log_does_not_infer_holds(self):
        name = self.light()
        event = self.event(name)
        self.assertEqual(self.preview.manual_change(event)[0], 200)
        self.assertEqual(self.preview.stop({})[0], 200)
        self.preview = self.make_preview()
        self.assertIsNone(self.fixtures()[name]["hand"])
        self.assertEqual(self.preview.manual_change(self.event(name, seq=2, revision=0))[0], 409)

    def test_stop_control_file_fences_old_edit_when_manual_journal_write_fails(self):
        name = self.light()
        self.assertEqual(self.preview.manual_change(self.event(name))[0], 200)
        late = self.event(name, seq=2, revision=1)
        with patch.object(self.preview.manual_log, "append", side_effect=OSError("full")):
            self.assertEqual(self.preview.stop({})[0], 200)
        self.preview = self.make_preview()
        self.assertTrue(self.preview.stopped)
        self.assertIsNone(self.fixtures()[name]["hand"])
        self.assertGreater(self.fixtures()[name]["manual_revision"], 1)
        self.assertEqual(self.preview.manual_change(late)[0], 409)


if __name__ == "__main__":
    unittest.main()
