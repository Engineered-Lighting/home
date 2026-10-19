from pathlib import Path
from copy import deepcopy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from home_shared.admission import LedgerLocked, canonical_digest
from home_shared.submissions import SubmissionClosed, SubmissionJournal
from home_shared.rejections import REJECTION_REASONS
from test_gateway import request_fixture


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "submission.sqlite"
        self.journal = self.open()
        self.addCleanup(lambda: self.journal.close())
        self.payload = request_fixture()

    def open(self, **kw):
        config = dict(device_id="den-adapter", gateway_identity="registered-ingress", max_records=10, free_space_floor_bytes=0)
        config.update(kw)
        return SubmissionJournal(self.path, **config)

    def terminal(self):
        return {"version": 1, "site_id": "victoria", "request_id": self.payload["request_id"],
                "status": "terminal", "outcome": "completed"}

    def test_recovery_and_predispatch_status_close_accounting_without_replay(self):
        for index,outcome in enumerate(('engine_stopped','preflight_rejected')):
            self.payload['request_id']='recovery-'+str(index)
            self.assertTrue(self.journal.prepare(self.payload))
            response={**self.terminal(),'outcome':outcome}
            self.assertTrue(self.journal.accept_status(response))
            self.assertIsNone(self.journal.outstanding())
            self.assertFalse(self.journal.prepare(self.payload))
            row=self.journal.db.execute('SELECT outcome FROM submissions WHERE request_id=?',(self.payload['request_id'],)).fetchone()
            self.assertEqual(row[0],outcome)

    def test_refresh_restart_never_requeues(self):
        request_id = "01234567-1234-4234-8234-0123456789ab"
        self.assertTrue(self.journal.reserve_refresh("a" * 64, request_id, now=1000))
        self.assertFalse(self.journal.reserve_refresh("a" * 64, request_id, now=1001))
        self.journal.close()
        self.journal = self.open()
        result = self.journal.refresh_status("a" * 64, request_id)
        self.assertEqual(result["deadline"], 1015)
        self.assertEqual(result["outcome"], "restart_before_dispatch")
        self.assertIsNone(self.journal.refresh_status("b" * 64, request_id))
        self.assertFalse(self.journal.reserve_refresh("a" * 64, request_id, now=1020))

    def test_refresh_expiry_does_not_release_uncertain_dispatch(self):
        request_id = "01234567-1234-4234-8234-0123456789ab"
        self.journal.reserve_refresh("a" * 64, request_id, now=self.payload["scheduled_at"])
        self.payload["request_id"] = request_id
        self.journal.prepare(self.payload)
        self.journal.expire_refreshes(now=self.payload["request_deadline"] + 1)
        self.assertEqual(self.journal.refresh_status("a" * 64, request_id)["state"], "dispatching")
        self.journal.close()
        self.journal = self.open()
        self.assertEqual(self.journal.refresh_status("a" * 64, request_id)["state"], "indeterminate")
        self.assertIsNotNone(self.journal.outstanding())

    def rejection(self, payload=None, outcome="rejected_capacity"):
        payload = self.payload if payload is None else payload
        return {"version": 1, "site_id": "victoria", "request_id": payload["request_id"],
                "status": "terminal", "outcome": outcome,
                "rejection": {"version": 1, "device_id": payload["device_id"],
                              "request_digest": canonical_digest(payload), "reason": REJECTION_REASONS[outcome]}}

    def test_commit_before_send_has_digest_but_no_image_or_description(self):
        self.assertTrue(self.journal.prepare(self.payload))
        active = self.journal.outstanding()
        self.assertEqual(active.digest, canonical_digest(self.payload))
        self.assertEqual(active.state, "dispatching")
        observer = sqlite3.connect(self.path)
        try:
            rows = observer.execute("SELECT * FROM submissions").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertNotIn(self.payload["image_base64"], repr(rows))
            self.assertNotIn("image_base64", repr(observer.execute("PRAGMA table_info(submissions)").fetchall()))
        finally:
            observer.close()

    def test_identical_duplicate_never_returns_permission_to_send(self):
        self.assertTrue(self.journal.prepare(self.payload))
        self.assertFalse(self.journal.prepare(self.payload))
        self.journal.accept_status(self.terminal())
        self.assertFalse(self.journal.prepare(self.payload))
        changed = dict(self.payload, max_output_tokens=42)
        with self.assertRaises(SubmissionClosed):
            self.journal.prepare(changed)

    def test_restart_and_expiry_retain_outstanding_without_payload_replay(self):
        self.journal.prepare(self.payload)
        self.journal.close()
        self.journal = self.open()
        self.assertEqual(self.journal.outstanding().state, "indeterminate")
        self.assertEqual(self.journal.prune(before_unix=999999), 0)
        with self.assertRaises(SubmissionClosed):
            self.journal.prepare(dict(self.payload, request_id="second"))
        self.assertTrue(self.journal.accept_status(self.terminal()))
        self.assertIsNone(self.journal.outstanding())
        self.assertTrue(self.journal.prepare(dict(self.payload, request_id="second")))

    def test_wrong_identity_unknown_terminal_or_not_found_cannot_release(self):
        self.journal.prepare(self.payload)
        for change in ({"site_id": "echo"}, {"request_id": "wrong"}, {"outcome": "not_found"}, {"version": 2}, {"status": "404"}):
            self.assertFalse(self.journal.accept_status(dict(self.terminal(), **change)))
            self.assertIsNotNone(self.journal.outstanding())

    def test_storage_capacity_closes_new_admission_but_allows_terminal_evidence(self):
        self.journal.prepare(self.payload)
        with patch("home_shared.submissions.shutil.disk_usage") as disk:
            disk.return_value.free = -1
            self.assertTrue(self.journal.accept_status(self.terminal()))
            with self.assertRaises(SubmissionClosed):
                self.journal.prepare(dict(self.payload, request_id="second"))
        self.assertIsNone(self.journal.outstanding())

    def test_row_cap_and_terminal_only_pruning(self):
        self.journal.max_records = 1
        self.journal.prepare(self.payload)
        self.journal.accept_status(self.terminal())
        with self.assertRaises(SubmissionClosed):
            self.journal.prepare(dict(self.payload, request_id="second"))
        self.assertEqual(self.journal.prune(before_unix=1016), 1)
        self.assertTrue(self.journal.prepare(dict(self.payload, request_id="second")))

    def test_process_lifetime_fence_and_provisioning_binding(self):
        with self.assertRaises(LedgerLocked):
            self.open()
        self.journal.prepare(self.payload)
        self.journal.close()
        with self.assertRaises(SubmissionClosed):
            self.open(gateway_identity="different-ingress")
        self.journal = self.open()
        self.assertIsNotNone(self.journal.outstanding())

    def test_database_failure_stays_closed(self):
        self.journal.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(SubmissionClosed):
            self.journal.prepare(self.payload)
        self.journal.db.execute("PRAGMA query_only=OFF")
        with self.assertRaises(SubmissionClosed):
            self.journal.prepare(self.payload)

    def test_only_dispatch_preparation_can_be_locally_proven_unsent(self):
        self.journal.prepare(self.payload)
        self.journal.record_not_sent(self.payload["request_id"])
        self.assertIsNone(self.journal.outstanding())
        second = dict(self.payload, request_id="second")
        self.journal.prepare(second)
        self.journal.accept_status({"version": 1, "site_id": "victoria", "request_id": "second", "status": "running", "outcome": None})
        with self.assertRaises(SubmissionClosed):
            self.journal.record_not_sent("second")
        self.assertIsNotNone(self.journal.outstanding())

    def test_affirmative_rejection_closes_only_exact_digest_and_allows_new_work(self):
        for index, outcome in enumerate(REJECTION_REASONS):
            payload = dict(self.payload, request_id=f"rejected-{index}")
            self.assertTrue(self.journal.prepare(payload))
            self.assertTrue(self.journal.accept_status(self.rejection(payload, outcome)))
            self.assertIsNone(self.journal.outstanding())
            self.assertFalse(self.journal.prepare(payload))
        self.assertTrue(self.journal.prepare(dict(self.payload, request_id="fresh-future-request")))

    def test_missing_wrong_or_malformed_rejection_proof_cannot_release(self):
        self.journal.prepare(self.payload)
        original = self.rejection()
        cases = [dict(original, rejection=None), dict(original, observation={}), dict(original, outcome="rejected_other"),
                 dict(original, status=[]), dict(original, outcome=[])]
        for field, bad in (("version", True), ("device_id", "other-device"), ("request_digest", "0" * 64),
                           ("reason", "closed"), ("unknown", "extra")):
            candidate = deepcopy(original)
            candidate["rejection"][field] = bad
            cases.append(candidate)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(self.journal.accept_status(candidate))
                self.assertIsNotNone(self.journal.outstanding())

    def test_restart_can_consume_rejection_without_original_image(self):
        self.journal.prepare(self.payload)
        proof = self.rejection()
        self.journal.close()
        self.journal = self.open()
        self.assertTrue(self.journal.accept_status(proof))
        self.assertIsNone(self.journal.outstanding())
        self.assertFalse(self.journal.accept_status(proof))

    def test_unexpected_receipt_does_not_change_other_terminal_semantics(self):
        self.journal.prepare(self.payload)
        response = self.terminal()
        response["rejection"] = self.rejection()["rejection"]
        self.assertFalse(self.journal.accept_status(response))
        self.assertTrue(self.journal.accept_status(self.terminal()))

    def test_local_commit_failure_keeps_rejection_unacknowledged(self):
        self.journal.prepare(self.payload)
        self.journal.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(SubmissionClosed):
            self.journal.accept_status(self.rejection())
        self.journal.close()
        self.journal = self.open()
        self.assertIsNotNone(self.journal.outstanding())
        self.assertTrue(self.journal.accept_status(self.rejection()))


if __name__ == "__main__":
    unittest.main()
