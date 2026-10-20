"""Bounded SQLite FULL reproduction; unrelated to the historical t57 cause."""
import sqlite3
import unittest

from journal import JournalError
from test_fixture_storage_diagnostics import StorageDiagnosticsTests


class FullStorageDiagnosticTests(StorageDiagnosticsTests):
    # Own one additional focused case. The inherited eight method tests also
    # run when this class is invoked; they still use their explicit fixtures.
    def test_actual_sqlite_full_is_recorded_without_changing_public_failure(self):
        target = self.method_target()
        target.db.execute("CREATE TABLE bounded_full_fixture(value BLOB)")
        target.db.execute("PRAGMA max_page_count=8")
        self.assertLessEqual(target.db.execute("PRAGMA max_page_count").fetchone()[0], 8)
        def fill():
            target.db.execute("INSERT INTO bounded_full_fixture VALUES(?)", (b"x" * 65536,))
            return {"unreachable": True}
        with self.assertRaises(JournalError) as raised:
            target._transaction(fill)
        self.assertEqual(raised.exception.code, "journal_unavailable")
        self.assertEqual(raised.exception.status, 507)
        self.assertIsInstance(raised.exception.__cause__, sqlite3.Error)
        result = target.fixture_storage_diagnostics()
        self.assertEqual(result["last_sqlite_failure"]["sqlite_errorcode"], sqlite3.SQLITE_FULL)
        self.assertEqual(result["last_sqlite_failure"]["sqlite_errorname"], "SQLITE_FULL")
        self.assertFalse(target.db.in_transaction)
        self.assertEqual(target.db.execute("SELECT count(*) FROM bounded_full_fixture").fetchone()[0], 0)
        self.assertLessEqual(result["allocation"]["allocated_bytes"], 8 * result["allocation"]["page_size"])


if __name__ == "__main__":
    unittest.main()
