"""Request outcomes survive intervening hints and bounded identity eviction."""
import hashlib
import unittest
from unittest.mock import patch

import walking_intent_receipts as receipts


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ReceiptLedgerTest(unittest.TestCase):
    def test_intervening_receipt_does_not_forget_identity_or_original_outcome(self):
        ledger = receipts.ReceiptLedger()
        first, second = fingerprint("first"), fingerprint("second")
        self.assertEqual(ledger.check("request-A", first), (False, False, None))
        ledger.record("request-A", first, True)
        ledger.record("request-B", second, True)
        before = list(ledger._recent.items())
        self.assertEqual(ledger.check("request-A", first), (True, True, "idempotent_retry"))
        self.assertEqual(list(ledger._recent.items()), before)
        self.assertEqual(ledger.check("request-A", fingerprint("changed")),
                         (True, False, "request_id_conflict"))
        with self.assertRaisesRegex(ValueError, "^request_id_conflict$"):
            ledger.record("request-A", fingerprint("changed"), True)
        self.assertEqual(list(ledger._recent.items()), before)

    def test_a_rejected_outcome_cannot_be_promoted_by_reusing_its_identity(self):
        ledger = receipts.ReceiptLedger()
        stamp = fingerprint("older parsed receipt")
        ledger.record("request-A", stamp, False)
        ledger.record("request-B", fingerprint("newer"), True)
        ledger.record("request-A", stamp, True)
        self.assertEqual(ledger.check("request-A", stamp), (True, False, "idempotent_retry"))

    def test_eviction_retains_a_tombstone_without_unbounded_receipts(self):
        ledger = receipts.ReceiptLedger()
        original = fingerprint("original")
        ledger.record("request-A", original, True)
        for index in range(receipts.MAX_RECENT):
            ledger.record("request-%04d" % index, fingerprint(str(index)), bool(index % 2))
        self.assertEqual(len(ledger._recent), receipts.MAX_RECENT)
        self.assertNotIn("request-A", ledger._recent)
        for stamp in (original, fingerprint("changed after eviction")):
            self.assertEqual(ledger.check("request-A", stamp),
                             (False, False, "request_identity_unavailable"))
            with self.assertRaisesRegex(ValueError, "^request_identity_unavailable$"):
                ledger.record("request-A", stamp, True)
        self.assertEqual(len(ledger._tombstones), 32 * 1024)

    def test_forced_filter_collision_declines_without_claiming_certain_reuse(self):
        ledger = receipts.ReceiptLedger()
        with patch.object(ledger, "_indices", return_value=(0, 1, 2, 3)):
            ledger.record("request-A", fingerprint("first"), True)
            self.assertEqual(ledger.check("different-request", fingerprint("second")),
                             (False, False, "request_identity_unavailable"))
            with self.assertRaisesRegex(ValueError, "^request_identity_unavailable$"):
                ledger.record("different-request", fingerprint("second"), True)
        self.assertEqual(len(ledger._recent), 1)

    def test_storage_contains_only_bounded_opaque_ids_fingerprints_and_outcomes(self):
        ledger = receipts.ReceiptLedger()
        private_payload = "private raw model text / clip / roi / https://private.invalid"
        ledger.record("opaque-request", fingerprint(private_payload), True)
        self.assertEqual(set(vars(ledger)), {"_recent", "_tombstones"})
        self.assertEqual(list(ledger._recent), ["opaque-request"])
        self.assertEqual(ledger._recent["opaque-request"], (fingerprint(private_payload), True))
        self.assertNotIn("private", repr(ledger._recent))
        self.assertEqual(len(ledger._tombstones), receipts.TOMBSTONE_BITS // 8)
        self.assertEqual(len(ledger._indices("opaque-request")), receipts.HASH_COUNT)

    def test_invalid_inputs_are_fixed_diagnostics_and_do_not_mutate_storage(self):
        ledger = receipts.ReceiptLedger()
        stamp = fingerprint("valid")
        for request_id, digest in ((None, stamp), ({"private": "payload"}, stamp), ("", stamp),
                                   ("x" * 257, stamp), ("https://private.invalid", stamp),
                                   ("request", None), ("request", "A" * 64), ("request", "a" * 65)):
            self.assertEqual(ledger.check(request_id, digest), (False, False, "invalid_request_identity"))
            with self.assertRaisesRegex(ValueError, "^invalid_request_identity$"):
                ledger.record(request_id, digest, True)
        for outcome in (None, 1, "true", {}):
            with self.assertRaisesRegex(ValueError, "^invalid_receipt_outcome$"):
                ledger.record("request", stamp, outcome)
        self.assertEqual(len(ledger._recent), 0)
        self.assertFalse(any(ledger._tombstones))

    def test_a_new_scope_has_fresh_identity_memory(self):
        old, new = receipts.ReceiptLedger(), receipts.ReceiptLedger()
        stamp = fingerprint("same receipt")
        old.record("request-A", stamp, False)
        self.assertEqual(new.check("request-A", stamp), (False, False, None))


if __name__ == "__main__":
    unittest.main()
