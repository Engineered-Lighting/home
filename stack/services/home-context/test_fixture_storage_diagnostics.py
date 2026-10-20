"""Method-level actual SQLite checks; not native fixture commissioning."""
import sqlite3
import threading
from types import SimpleNamespace
import unittest

from journal import Journal, JournalError
from fixture_storage_diagnostics import StorageDiagnosticJournal, _error_code


class StorageDiagnosticsTests(unittest.TestCase):
    def method_target(self, cls=StorageDiagnosticJournal):
        # Explicit method fixture: the full Journal constructor is not tested here.
        target = object.__new__(cls)
        target.db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(target.db.close)
        target.lock = threading.RLock()
        target.limits = SimpleNamespace(max_response_bytes=1048576)
        target._fixture_storage_failure = None
        target._fixture_storage_failure_count = 0
        target._fixture_storage_capture_failed = False
        target.db.execute("CREATE TABLE values_fixture(value INTEGER CHECK(value>=0))")
        return target

    def test_exact_success_and_commit_match_original(self):
        for cls in (Journal, StorageDiagnosticJournal):
            target = self.method_target(cls)
            def insert():
                target.db.execute("INSERT INTO values_fixture VALUES(5)")
                return {"same": [1, 2, 3]}
            self.assertEqual(target._transaction(insert), {"same": [1, 2, 3]})
            self.assertEqual(target.db.execute("SELECT value FROM values_fixture").fetchall(), [(5,)])
            self.assertFalse(target.db.in_transaction)

    def test_sqlite_error_keeps_original_public_error_and_rollback(self):
        for cls in (Journal, StorageDiagnosticJournal):
            target = self.method_target(cls)
            def fail_insert():
                target.db.execute("INSERT INTO values_fixture VALUES(5)")
                target.db.execute("INSERT INTO values_fixture VALUES(-1)")
            with self.assertRaises(JournalError) as raised:
                target._transaction(fail_insert)
            self.assertEqual(raised.exception.code, "journal_unavailable")
            self.assertEqual(raised.exception.status, 507)
            self.assertEqual(str(raised.exception), "durable journal admission failed")
            self.assertFalse(target.db.in_transaction)
            self.assertEqual(target.db.execute("SELECT count(*) FROM values_fixture").fetchone()[0], 0)
            if cls is StorageDiagnosticJournal:
                observed = target.fixture_storage_diagnostics()
                self.assertEqual(observed["sqlite_failure_count"], 1)
                self.assertEqual(observed["last_sqlite_failure"]["sqlite_errorname"], "SQLITE_CONSTRAINT_CHECK")
                self.assertFalse(observed["last_sqlite_failure"]["transaction_active_after_error"])
                self.assertEqual(set(observed["last_sqlite_failure"]), {
                    "operation", "transaction_active_after_error", "sqlite_errorcode", "sqlite_errorname"})

    def test_non_sqlite_error_propagates_without_false_storage_diagnosis(self):
        target = self.method_target()
        specific = RuntimeError("fictional secret error text")
        def fail():
            target.db.execute("INSERT INTO values_fixture VALUES(5)")
            raise specific
        with self.assertRaises(RuntimeError) as raised:
            target._transaction(fail)
        self.assertIs(raised.exception, specific)
        self.assertEqual(target.fixture_storage_diagnostics()["sqlite_failure_count"], 0)
        self.assertFalse(target.db.in_transaction)
        self.assertEqual(target.db.execute("SELECT count(*) FROM values_fixture").fetchone()[0], 0)

    def test_diagnostic_capture_closed_db_cannot_replace_original_error(self):
        target = self.method_target()
        actual = target.db
        class CloseAfterRollback:
            @property
            def in_transaction(self):
                return actual.in_transaction
            def execute(self, sql, parameters=()):
                result = actual.execute(sql, parameters)
                if sql == "ROLLBACK":
                    actual.close()
                return result
        target.db = CloseAfterRollback()
        with self.assertRaises(JournalError) as raised:
            target._transaction(lambda: target.db.execute("INSERT INTO values_fixture VALUES(-1)"))
        self.assertEqual(raised.exception.code, "journal_unavailable")
        self.assertEqual(raised.exception.status, 507)
        self.assertTrue(target._fixture_storage_capture_failed)

    def test_diagnostic_capture_failure_is_observational_and_lock_is_retained(self):
        target = self.method_target()
        actual = target.db
        state = {"rolled_back": False, "owned": False}
        class FailCaptureAfterRollback:
            @property
            def in_transaction(self):
                if state["rolled_back"]:
                    state["owned"] = target.lock._is_owned()
                    raise RuntimeError("explicit diagnostic branch failure")
                return actual.in_transaction
            def execute(self, sql, parameters=()):
                result = actual.execute(sql, parameters)
                if sql == "ROLLBACK":
                    state["rolled_back"] = True
                return result
        target.db = FailCaptureAfterRollback()
        with self.assertRaises(JournalError) as raised:
            target._transaction(lambda: target.db.execute("INSERT INTO values_fixture VALUES(-1)"))
        self.assertEqual(raised.exception.code, "journal_unavailable")
        self.assertTrue(state["owned"])
        self.assertTrue(target._fixture_storage_capture_failed)

    def test_actual_allocation_aggregates_are_read_only(self):
        target = self.method_target()
        before = target.db.total_changes
        result = target.fixture_storage_diagnostics()
        self.assertEqual(result["allocation_status"], "available")
        allocation = result["allocation"]
        self.assertEqual(allocation["allocated_bytes"], allocation["page_count"] * allocation["page_size"])
        dbstat_available = any("ENABLE_DBSTAT_VTAB" in row[0]
                               for row in target.db.execute("PRAGMA compile_options"))
        if dbstat_available:
            self.assertEqual(result["aggregate_status"], "available")
            row = next(x for x in result["aggregates"] if x["name"] == "values_fixture")
            self.assertGreaterEqual(row["allocated_bytes"], 4096)
        else:
            self.assertEqual(result["aggregate_status"], "unavailable")
            self.assertIsNone(result["aggregates"])
            self.assertEqual(result["diagnostic_error"]["sqlite_errorname"], "SQLITE_ERROR")
        self.assertEqual(target.db.total_changes, before)
        self.assertFalse(result["actuated"])
        self.assertFalse(result["acceptance_enabled"])

    def test_unavailable_aggregates_are_null_not_zero(self):
        target = self.method_target()
        target.db.close()
        result = target.fixture_storage_diagnostics()
        self.assertEqual(result["allocation_status"], "unavailable")
        self.assertIsNone(result["allocation"])
        self.assertIsNone(result["aggregates"])
        self.assertEqual(result["diagnostic_error"]["reason"], "aggregate_query_unavailable")

    def test_bad_error_labels_do_not_disclose_exception_text(self):
        error = sqlite3.OperationalError("private payload text")
        self.assertEqual(_error_code(error), {"sqlite_errorcode": None, "sqlite_errorname": None})
        error.sqlite_errorcode = True
        error.sqlite_errorname = "SQLITE_SECRET\nprivate payload text"
        self.assertEqual(_error_code(error), {"sqlite_errorcode": None, "sqlite_errorname": None})

    def test_aggregate_count_bound_is_explicit(self):
        target = self.method_target()
        actual_db = target.db
        class ExplicitDbstatBranchDouble:
            def execute(self, sql, parameters=()):
                if "FROM dbstat" in sql:
                    self.asserted_limit = parameters
                    return SimpleNamespace(fetchall=lambda: [("fixture", 1, 4096, 0, 0)] * 129)
                return actual_db.execute(sql, parameters)
        double = ExplicitDbstatBranchDouble()
        target.db = double
        result = target.fixture_storage_diagnostics()
        self.assertEqual(double.asserted_limit, (129,))
        self.assertEqual(result["allocation_status"], "available")
        self.assertEqual(result["aggregate_status"], "unavailable")
        self.assertIsNone(result["aggregates"])
        self.assertEqual(result["diagnostic_error"], {"reason": "aggregate_count_bound"})

    def test_stored_column_bytes_are_actual_and_separate_from_page_accounting(self):
        target = self.method_target()
        target.db.execute("CREATE TABLE publications(body BLOB)")
        target.db.execute("INSERT INTO publications VALUES(?)", ("fictional unicode é",))
        target.db.execute("INSERT INTO publications VALUES(?)", (b"binary",))
        result = target.fixture_storage_diagnostics()
        self.assertIn("not_table_pages", result["stored_columns"]["scope"])
        rows = result["stored_columns"]["tables"]
        pub = next(x for x in rows if x["table"] == "publications")
        self.assertEqual(pub, {"table": "publications", "status": "available", "rows": 2,
                               "bytes": {"body": len("fictional unicode é".encode("utf-8")) + 6}})
        absent = next(x for x in rows if x["table"] == "adaptive_feedback_origins")
        self.assertEqual(absent["status"], "unavailable")
        self.assertIsNone(absent["rows"])
        self.assertIsNone(absent["bytes"])


if __name__ == "__main__":
    unittest.main()
