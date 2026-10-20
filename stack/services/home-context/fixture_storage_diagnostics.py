"""Opt-in local synthetic diagnostics; no SQL text or payload error disclosure."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

from journal import Journal

INTERFACE_VERSION = "home-context-fixture-storage-diagnostics-inprocess-1"
MAXIMUM_AGGREGATES = 128
SOURCE_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
STORED_COLUMNS = (
    ("publications", ("body",)),
    ("receipts", ("receipt", "event")),
    ("state", ("body",)),
    ("adaptive_feedback_origins", ("request_json", "first_receipt_json")),
    ("feedback_worker_commit_originals", ("request_blob",)),
    ("feedback_read_cuts", ("body_blob",)),
)


def _error_code(error):
    code = getattr(error, "sqlite_errorcode", None)
    name = getattr(error, "sqlite_errorname", None)
    if type(code) is not int or not 0 < code <= 2147483647:
        code = None
    # Keep only the stdlib's bounded numeric-code label; never str(error).
    if (type(name) is not str or not 1 <= len(name) <= 80
            or not name.startswith("SQLITE_")
            or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in name)):
        name = None
    return {"sqlite_errorcode": code, "sqlite_errorname": name}


class StorageDiagnosticJournal(Journal):
    """Use the original transaction implementation and retain terminal diagnostics."""

    def __init__(self, *args, **kwargs):
        self._fixture_storage_failure = None
        self._fixture_storage_failure_count = 0
        self._fixture_storage_capture_failed = False
        super().__init__(*args, **kwargs)

    def _transaction(self, operation):
        # Retain the genuine RLock across original rollback and observation;
        # another owner cannot close/change the DB in a release/reacquire gap.
        with self.lock:
            try:
                return super()._transaction(operation)
            except BaseException as error:
                try:
                    cause = error if isinstance(error, sqlite3.Error) else error.__cause__
                    if isinstance(cause, sqlite3.Error):
                        self._fixture_storage_failure_count = min(
                            2147483647, self._fixture_storage_failure_count + 1)
                        self._fixture_storage_failure = None
                        self._fixture_storage_capture_failed = False
                        self._fixture_storage_failure = {
                            "operation": "journal_transaction",
                            "transaction_active_after_error": bool(self.db.in_transaction),
                            **_error_code(cause),
                        }
                except BaseException:
                    # Diagnostics cannot replace the original public failure.
                    self._fixture_storage_capture_failed = True
                raise

    def fixture_storage_diagnostics(self):
        """Actual allocation aggregates, read only; unavailable is never zero."""
        with self.lock:
            result = {
                "interface_version": INTERFACE_VERSION,
                "scope": "private_synthetic_fixture_diagnostics",
                "sqlite_failure_count": self._fixture_storage_failure_count,
                "failure_capture_available": not self._fixture_storage_capture_failed,
                "last_sqlite_failure": (dict(self._fixture_storage_failure)
                                        if self._fixture_storage_failure else None),
                "allocation": None, "aggregates": None,
                "allocation_status": "unavailable", "aggregate_status": "unavailable",
                "diagnostic_error": None,
                "stored_columns": None,
                "actuated": False, "acceptance_enabled": False,
            }
            try:
                allocation = {}
                for key in ("page_count", "page_size", "freelist_count", "max_page_count"):
                    value = self.db.execute("PRAGMA " + key).fetchone()[0]
                    if type(value) is not int or value < 0:
                        raise ValueError("invalid allocation aggregate")
                    allocation[key] = value
                allocation["allocated_bytes"] = allocation["page_count"] * allocation["page_size"]
                result["allocation"] = allocation
                result["allocation_status"] = "available"
                # Fixed column aggregates are stored bytes, never table pages
                # or decoded-publication quota accounting. Missing tables stay
                # explicitly unavailable. No schema-discovered SQL is executed.
                stored = []
                for table, columns in STORED_COLUMNS:
                    try:
                        terms = ",".join("coalesce(sum(length(cast(" + column
                                         + " AS BLOB))),0)" for column in columns)
                        row = self.db.execute("SELECT count(*)," + terms + " FROM " + table).fetchone()
                        if any(type(v) is not int or v < 0 for v in row):
                            raise ValueError("invalid stored-column aggregate")
                        stored.append({"table": table, "status": "available", "rows": row[0],
                                       "bytes": dict(zip(columns, row[1:]))})
                    except sqlite3.Error as error:
                        stored.append({"table": table, "status": "unavailable", "rows": None,
                                       "bytes": None, **_error_code(error)})
                result["stored_columns"] = {
                    "scope": "stored_column_bytes_not_table_pages_or_decoded_publication_quota",
                    "tables": stored,
                }
                rows = self.db.execute(
                    "SELECT name,count(*),sum(pgsize),sum(payload),sum(unused) "
                    "FROM dbstat GROUP BY name ORDER BY name LIMIT ?",
                    (MAXIMUM_AGGREGATES + 1,)).fetchall()
                if len(rows) > MAXIMUM_AGGREGATES:
                    result["diagnostic_error"] = {"reason": "aggregate_count_bound"}
                    return result
                aggregates = []
                for row in rows:
                    name, pages, allocated, payload, unused = tuple(row)
                    if (type(name) is not str or not 1 <= len(name) <= 160
                            or any(type(v) is not int or v < 0
                                   for v in (pages, allocated, payload, unused))):
                        raise ValueError("invalid table aggregate")
                    aggregates.append({"name": name, "pages": pages,
                                       "allocated_bytes": allocated,
                                       "payload_bytes": payload, "unused_bytes": unused})
                result["aggregates"] = aggregates
                result["aggregate_status"] = "available"
            except sqlite3.Error as error:
                result["diagnostic_error"] = {
                    "reason": "aggregate_query_unavailable", **_error_code(error)}
            except ValueError:
                result["diagnostic_error"] = {"reason": "aggregate_value_invalid"}
            return result
