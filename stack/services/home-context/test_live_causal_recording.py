"""Public journal recording behavior with fictional observations only."""
import tempfile
import unittest
from pathlib import Path
from journal import Journal, JournalError
from live_test_helpers import NOW, PROFILE, SCOPE, TOKEN, configuration, event, fact, grant, receipt_resolver


class LiveCausalRecordingTests(unittest.TestCase):
    def make(self):
        work = tempfile.TemporaryDirectory(prefix="fictional-recorder-", dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        config = configuration()
        journal = Journal(Path(work.name) / "session.sqlite", grants=[grant()], profile=PROFILE,
                          clock=lambda: NOW + .2, live_config=config, live_receipt_resolver=receipt_resolver)
        self.addCleanup(journal.close)
        return journal, journal.authenticate(TOKEN)

    def test_recorded_admission_contains_original_body_receipts_and_atomic_result(self):
        journal, authority = self.make()
        records = []
        journal.attach_recorder(records.append)
        original = event(fact("recorded-fact", seq=1))
        accepted = journal.append_event(original, authority)
        self.assertEqual([r["kind"] for r in records], ["journal_prepare", "journal_commit"])
        prepare, committed = [r["data"] for r in records]
        self.assertEqual(prepare["events"], [original])
        self.assertEqual(prepare["receipts"], [accepted])
        self.assertEqual(prepare["publications"][0]["payload"], original["payload"])
        self.assertTrue(prepare["original_receipts"])
        self.assertEqual(prepare["tx_id"], committed["tx_id"])
        self.assertEqual(committed["result"], accepted)
        self.assertEqual(journal.append_event(original, authority), accepted)
        self.assertEqual(len(records), 2, "exact retry is not a new admission")
        with self.assertRaises(ValueError):
            journal.attach_recorder(records.append)

    def test_recorder_failure_stops_new_admission_instead_of_acknowledging_unrecorded_evidence(self):
        journal, authority = self.make()
        def broken(_):
            raise OSError("fictional archive full")
        journal.attach_recorder(broken)
        with self.assertRaises(JournalError) as failed:
            journal.append_event(event(fact("unrecorded-fact", seq=1)), authority)
        self.assertEqual(failed.exception.code, "recorder_unavailable")
        with self.assertRaises(JournalError) as later:
            journal.snapshot(authority, SCOPE["site_id"], SCOPE["room_id"])
        self.assertEqual(later.exception.code, "recorder_unavailable")


if __name__ == "__main__":
    unittest.main()
