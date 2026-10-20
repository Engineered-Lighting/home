"""Storage tests only; no claim about actual engine or GPU completion."""
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading

from home_shared.engine_receipts import EngineReceiptReader, EngineReceiptStore, ReceiptConflict, ReceiptUnavailable

BINDING = dict(manifest_digest="a" * 64, engine_id="engine", engine_epoch="epoch-1", model_revision="revision")
ARGS = ("ledger-1", "b" * 64, "c" * 64, ("internal-1", "internal-2"))


class EngineReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "receipts.db"
        self.authority = object()
        self.store = self.open(create=True)

    def tearDown(self):
        self.store.close()

    def open(self, **kwargs):
        return EngineReceiptStore(self.path, **BINDING, cleanup_authority=self.authority, **kwargs)

    def cleanup(self, **kwargs):
        return self.store.record_cleanup(*ARGS, authority=self.authority,
                                         evidence_sha256=kwargs.get("evidence", "d" * 64))

    def test_durable_registration_cleanup_and_reader_visibility(self):
        reader = EngineReceiptReader(self.path, **BINDING)
        self.addCleanup(reader.close)
        self.assertIsNone(reader.lookup("ledger-1"))
        registered = self.store.register(*ARGS)
        self.assertIsNone(registered.cleanup_evidence_sha256)
        self.assertEqual(reader.lookup("ledger-1"), registered)
        self.assertEqual(self.store.register(*ARGS), registered)
        finished = self.cleanup()
        self.assertEqual(self.cleanup(), finished)
        self.assertEqual(reader.lookup("ledger-1"), finished)
        self.assertEqual(finished.registration_digest, registered.registration_digest)
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.lookup("ledger-1"), finished)
        self.assertEqual(self.store.register(*ARGS), finished)

    def test_dispatch_permission_only_on_insert_never_on_restart_or_cleanup(self):
        first = self.store.register_for_dispatch(*ARGS)
        self.assertTrue(first.dispatch_permitted)
        duplicate = self.store.register_for_dispatch(*ARGS)
        self.assertFalse(duplicate.dispatch_permitted)
        self.assertEqual(duplicate.receipt, first.receipt)
        self.store.close()
        self.store = self.open()
        self.assertFalse(self.store.register_for_dispatch(*ARGS).dispatch_permitted)
        finished = self.cleanup()
        duplicate = self.store.register_for_dispatch(*ARGS)
        self.assertFalse(duplicate.dispatch_permitted)
        self.assertEqual(duplicate.receipt, finished)

    def test_concurrent_registration_has_one_dispatch_decision(self):
        second = self.open()
        self.addCleanup(second.close)
        barrier = threading.Barrier(2)

        def register(store):
            barrier.wait(timeout=2)
            return store.register_for_dispatch(*ARGS)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(register, store) for store in (self.store, second)]
            decisions = [future.result(timeout=5) for future in futures]
        self.assertEqual(sum(item.dispatch_permitted for item in decisions), 1)
        self.assertEqual(decisions[0].receipt, decisions[1].receipt)

    def test_registration_conflict_poisoned_instance_retains_original(self):
        original = self.store.register(*ARGS)
        with self.assertRaises(ReceiptConflict):
            self.store.register(ARGS[0], "f" * 64, *ARGS[2:])
        with self.assertRaises(ReceiptUnavailable):
            self.store.lookup(ARGS[0])
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.lookup(ARGS[0]), original)

    def test_internal_ids_cannot_be_shared(self):
        self.store.register(*ARGS)
        with self.assertRaises(ReceiptConflict):
            self.store.register("ledger-2", *ARGS[1:])

    def test_cleanup_requires_identity_and_trusted_caller(self):
        self.store.register(*ARGS)
        with self.assertRaises(ReceiptUnavailable):
            self.store.record_cleanup(*ARGS, authority=object(), evidence_sha256="d" * 64)
        self.assertIsNone(self.store.lookup(ARGS[0]).cleanup_evidence_sha256)
        with self.assertRaises(ReceiptConflict):
            self.store.record_cleanup(*ARGS[:3], ("internal-1",), authority=self.authority, evidence_sha256="d" * 64)

    def test_terminal_receipt_immutable(self):
        self.store.register(*ARGS)
        self.cleanup()
        with self.assertRaises(ReceiptConflict):
            self.cleanup(evidence="e" * 64)

    def test_capacity_does_not_delete_outstanding_work(self):
        self.store.close()
        self.store = self.open(max_records=1)
        original = self.store.register(*ARGS)
        with self.assertRaises(ReceiptUnavailable):
            self.store.register("ledger-2", *ARGS[1:3], ("new",))
        self.store.close()
        self.store = self.open()
        self.assertEqual(self.store.lookup(ARGS[0]), original)

    def test_missing_database_and_binding_mismatch(self):
        with self.assertRaises(ReceiptUnavailable):
            EngineReceiptReader(self.path.with_name("missing.db"), **BINDING)
        self.assertFalse(self.path.with_name("missing.db").exists())
        with self.assertRaises(ReceiptUnavailable):
            EngineReceiptReader(self.path, **{**BINDING, "engine_epoch": "other"})

    def test_corruption_is_rejected(self):
        self.store.register(*ARGS)
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET checksum=?", ("0" * 64,))
        db.close()
        with self.assertRaises(ReceiptUnavailable):
            self.open()

    def test_index_corruption_is_rejected(self):
        self.store.register(*ARGS)
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM internal_ids")
        db.close()
        with self.assertRaises(ReceiptUnavailable):
            self.open()

    def test_read_only_api_and_sql_connection(self):
        reader = EngineReceiptReader(self.path, **BINDING)
        self.addCleanup(reader.close)
        with self.assertRaises(ReceiptUnavailable):
            reader.register(*ARGS)
        with self.assertRaises(sqlite3.OperationalError):
            reader._db.execute("DELETE FROM requests")

    def test_storage_failure_poisoned_without_cleanup(self):
        self.store.register(*ARGS)
        self.store._db.execute("PRAGMA query_only=ON")
        with self.assertRaises(ReceiptUnavailable):
            self.cleanup()
        self.store.close()
        self.store = self.open()
        self.assertIsNone(self.store.lookup(ARGS[0]).cleanup_evidence_sha256)

    def test_process_crash_after_registration_leaves_no_invented_terminal(self):
        self.store.close()
        script = """
import os, sys
from home_shared.engine_receipts import EngineReceiptStore
s = EngineReceiptStore(sys.argv[1], manifest_digest='a'*64, engine_id='engine', engine_epoch='epoch-1', model_revision='revision', cleanup_authority=object())
s.register('ledger-1', 'b'*64, 'c'*64, ('internal-1','internal-2'))
os._exit(17)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.store = self.open()
        self.assertIsNone(self.store.lookup(ARGS[0]).cleanup_evidence_sha256)

    def test_crash_during_uncommitted_cleanup_rolls_back(self):
        registered = self.store.register(*ARGS)
        self.store.close()
        script = """
import os, sqlite3, sys
db=sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE requests SET payload='broken'")
os._exit(18)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 18, result.stderr)
        self.store = self.open()
        self.assertEqual(self.store.lookup(ARGS[0]), registered)

    def test_crash_after_terminal_commit_keeps_receipt(self):
        self.store.register(*ARGS)
        self.store.close()
        script = """
import os, sys
from home_shared.engine_receipts import EngineReceiptStore
authority=object()
s=EngineReceiptStore(sys.argv[1], manifest_digest='a'*64, engine_id='engine', engine_epoch='epoch-1', model_revision='revision', cleanup_authority=authority)
s.record_cleanup('ledger-1','b'*64,'c'*64,('internal-1','internal-2'),authority=authority,evidence_sha256='d'*64)
os._exit(19)
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.path)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 19, result.stderr)
        self.store = self.open()
        self.assertEqual(self.store.lookup(ARGS[0]).cleanup_evidence_sha256, "d" * 64)

    def test_database_page_exhaustion_is_atomic(self):
        self.store.close()
        self.store = self.open(max_database_bytes=65536)
        committed = []
        for i in range(100):
            try:
                record = self.store.register(f"ledger-{i}", "b" * 64, "c" * 64,
                                             tuple(f"internal-{i}-{j}" for j in range(64)))
            except ReceiptUnavailable:
                break
            committed.append(record)
        else:
            self.fail("expected SQLite page limit exhaustion")
        self.assertTrue(committed)
        self.assertLessEqual(self.path.stat().st_size, 65536)
        self.store.close()
        self.store = self.open(max_database_bytes=65536)
        for record in committed:
            self.assertEqual(self.store.lookup(record.ledger_id), record)
        self.assertIsNone(self.store.lookup(f"ledger-{len(committed)}"))


if __name__ == "__main__":
    unittest.main()
