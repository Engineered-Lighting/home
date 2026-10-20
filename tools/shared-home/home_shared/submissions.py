"""Durable Victoria submission accounting; no media, prompts or result text.

The adapter commits a request digest before its first gateway POST. An ambiguous
POST, expiry or process restart never clears that entry. Only the authenticated
gateway's matching terminal status closes it. This ledger complements, and never
releases, the gateway's independent engine/resource reservations.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from typing import Iterator

from .adapter import finite
from .admission import _LifetimeLock, canonical_digest
from .rejections import REJECTION_REASONS, validate_rejection


class SubmissionClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class Submission:
    request_id: str
    digest: str
    adapter_session: str
    created_at: float
    deadline: float
    state: str
    outcome: str | None


STATES = frozenset({"dispatching", "queued", "running", "indeterminate", "terminal"})
OUTCOMES = frozenset({"completed", "completed_after_deadline", "queue_expired", "restart_before_dispatch",
                      "preflight_rejected", "engine_stopped"}) | frozenset(REJECTION_REASONS)


class SubmissionJournal:
    """Single-process, single-outstanding source ledger with explicit bounds.

    gateway_identity is a provisioned stable ingress identity, not a caller URL.
    Changing that identity/device requires deliberate reconciliation; a new
    server's 'not found' response is never proof that old work has ended.
    """

    def __init__(self, path: str | Path, *, device_id: str, gateway_identity: str,
                 max_records: int, free_space_floor_bytes: int):
        if (any(not isinstance(v, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", v) is None
                for v in (device_id, gateway_identity))
                or type(max_records) is not int or not 1 <= max_records <= 1_000_000
                or type(free_space_floor_bytes) is not int or free_space_floor_bytes < 0):
            raise ValueError("provisioned journal binding and explicit storage bounds required")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.device_id = device_id
        self.gateway_identity = gateway_identity
        self.max_records, self.free_space_floor_bytes = max_records, free_space_floor_bytes
        self.broken = False
        self._closed = False
        self._lock = _LifetimeLock(Path(str(self.path) + ".lock"))
        try:
            self.db = sqlite3.connect(self.path, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in (0, 1) or (tables and "submission_binding" not in tables):
                raise SubmissionClosed("not a supported Victoria submission journal")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=1000")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS submission_binding(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submissions(
                  request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,adapter_session TEXT NOT NULL,
                  created_at REAL NOT NULL,deadline REAL NOT NULL,state TEXT NOT NULL,outcome TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_outstanding ON submissions((1)) WHERE state!='terminal';
                CREATE TABLE IF NOT EXISTS refresh_requests(
                  request_id TEXT PRIMARY KEY, principal_namespace TEXT NOT NULL,
                  accepted_at REAL NOT NULL, deadline REAL NOT NULL,
                  state TEXT NOT NULL, outcome TEXT);
            """)
            binding = json.dumps(["victoria", device_id, gateway_identity])
            with self._transaction():
                row = self.db.execute("SELECT binding FROM submission_binding WHERE id=1").fetchone()
                if row and row[0] != binding:
                    raise SubmissionClosed("submission journal source/ingress binding changed")
                self.db.execute("INSERT OR IGNORE INTO submission_binding VALUES(1,?)", (binding,))
                self.db.execute("UPDATE submissions SET state='indeterminate',outcome=NULL WHERE state!='terminal'")
                # A queued UI request with no durable dispatch record never
                # reached the gateway. Do not replay it after a restart.
                self.db.execute("""UPDATE refresh_requests SET state='terminal',outcome='restart_before_dispatch'
                    WHERE state='queued' AND request_id NOT IN (SELECT request_id FROM submissions)""")
                self.db.execute("PRAGMA user_version=1")
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self._closed or self.broken:
            raise SubmissionClosed("submission journal unavailable")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.execute("COMMIT")
        except sqlite3.Error:
            self.broken = True
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise SubmissionClosed("submission journal failed; new submissions closed") from None
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def outstanding(self) -> Submission | None:
        if self._closed or self.broken:
            raise SubmissionClosed("submission journal unavailable")
        try:
            row = self.db.execute("SELECT * FROM submissions WHERE state!='terminal'").fetchone()
        except sqlite3.Error:
            self.broken = True
            raise SubmissionClosed("submission journal read failed") from None
        return Submission(**dict(row)) if row else None

    def prepare(self, payload: dict) -> bool:
        """Return True only for the first durable preparation; duplicates never send."""
        for field in ("request_id", "producer_session"):
            value = payload.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
                raise ValueError("submission identity invalid")
        if (payload.get("site_id") != "victoria" or payload.get("device_id") != self.device_id
                or not finite(payload.get("scheduled_at")) or not finite(payload.get("request_deadline"))
                or payload["request_deadline"] != payload["scheduled_at"] + 15):
            raise ValueError("submission does not match source timing/binding")
        digest = canonical_digest(payload)
        with self._transaction():
            existing = self.db.execute("SELECT digest FROM submissions WHERE request_id=?", (payload["request_id"],)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise SubmissionClosed("request ID already binds different content")
                return False
            if self.outstanding():
                raise SubmissionClosed("previous submission has no affirmative terminal status")
            if (self.db.execute("SELECT count(*) FROM submissions").fetchone()[0] >= self.max_records
                    or shutil.disk_usage(self.path.parent).free < self.free_space_floor_bytes):
                raise SubmissionClosed("submission storage capacity exhausted")
            self.db.execute("INSERT INTO submissions VALUES(?,?,?,?,?,'dispatching',NULL)",
                            (payload["request_id"], digest, payload["producer_session"],
                             payload["scheduled_at"], payload["request_deadline"]))
        return True

    @staticmethod
    def _refresh_identity(principal_namespace: str, request_id: str):
        # Namespace comes from authenticated server context, never the body.
        if (not isinstance(principal_namespace, str)
                or re.fullmatch(r"[a-f0-9]{64}", principal_namespace) is None
                or not isinstance(request_id, str)):
            raise ValueError("invalid refresh identity")
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError("canonical UUID required")
        except (ValueError, AttributeError):
            raise ValueError("canonical UUID required") from None

    def refresh_status(self, principal_namespace: str, request_id: str) -> dict | None:
        self._refresh_identity(principal_namespace, request_id)
        with self._transaction():
            row = self.db.execute("""SELECT r.request_id,r.accepted_at,r.deadline,
                COALESCE(s.state,r.state) AS state,COALESCE(s.outcome,r.outcome) AS outcome
                FROM refresh_requests r LEFT JOIN submissions s ON r.request_id=s.request_id
                WHERE r.request_id=? AND r.principal_namespace=?""", (request_id, principal_namespace)).fetchone()
            return dict(row) if row else None

    def latest_refresh_time(self) -> float | None:
        """Retain the site-wide request cooldown across process restarts."""
        with self._transaction():
            return self.db.execute('SELECT MAX(accepted_at) FROM refresh_requests').fetchone()[0]

    def reserve_refresh(self, principal_namespace: str, request_id: str, *, now: float) -> bool:
        """Commit before queue mutation; True is the only permission to enqueue.

        This is idempotency/accounting, not authentication or GPU admission.
        The ingress must validate the current principal and refresh capability.
        """
        self._refresh_identity(principal_namespace, request_id)
        if not finite(now) or now <= 0:
            raise ValueError("finite positive admission time required")
        with self._transaction():
            row = self.db.execute("SELECT principal_namespace FROM refresh_requests WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row[0] != principal_namespace:
                    raise ValueError("refresh identity unavailable")
                return False
            if (self.db.execute("SELECT count(*) FROM refresh_requests").fetchone()[0] >= self.max_records
                    or shutil.disk_usage(self.path.parent).free < self.free_space_floor_bytes):
                raise SubmissionClosed("refresh storage capacity exhausted")
            self.db.execute("INSERT INTO refresh_requests VALUES(?,?,?,?,'queued',NULL)",
                            (request_id, principal_namespace, now, now + 15))
        return True

    def refresh_not_queued(self, request_id: str) -> None:
        """Only for a trusted local queue failure before any dispatch record."""
        with self._transaction():
            if self.db.execute("SELECT 1 FROM submissions WHERE request_id=?", (request_id,)).fetchone():
                raise SubmissionClosed("refresh already has dispatch accounting")
            self.db.execute("UPDATE refresh_requests SET state='terminal',outcome='client_not_sent' WHERE request_id=? AND state='queued'", (request_id,))

    def accept_status(self, response: dict) -> bool:
        """Called only with a response from the provisioned authenticated gateway."""
        if (not isinstance(response, dict) or type(response.get("version")) is not int or response.get("version") != 1
                or response.get("site_id") != "victoria" or not isinstance(response.get("status"), str)
                or response.get("status") not in STATES
                or (response.get("outcome") is not None and not isinstance(response.get("outcome"), str))
                or (response["status"] == "terminal" and response.get("outcome") not in OUTCOMES)
                or (response["status"] != "terminal" and response.get("outcome") is not None
                    and (not isinstance(response["outcome"], str)
                         or re.fullmatch(r"[a-z_]{1,64}", response["outcome"]) is None))):
            return False
        with self._transaction():
            active = self.outstanding()
            if not active or active.request_id != response.get("request_id"):
                return False
            if response.get("outcome") in REJECTION_REASONS:
                if not validate_rejection(response, device_id=self.device_id, request_digest=active.digest):
                    return False
            elif "rejection" in response:
                return False
            # Delayed non-terminal replies never reopen terminal work. Regressing
            # non-terminal states remains conservatively outstanding.
            self.db.execute("UPDATE submissions SET state=?,outcome=? WHERE request_id=?",
                            (response["status"], response.get("outcome"), active.request_id))
        return True

    def expire_refreshes(self, *, now: float) -> None:
        if not finite(now):
            raise ValueError("finite expiry time required")
        with self._transaction():
            self.db.execute("""UPDATE refresh_requests SET state='terminal',outcome='queue_expired'
                WHERE state='queued' AND deadline<=? AND request_id NOT IN (SELECT request_id FROM submissions)""", (now,))

    def record_not_sent(self, request_id: str) -> None:
        """Trusted client proved failure BEFORE its network worker launched.

        Never call this for timeout, HTTP error, unknown status or lost response.
        It closes only local dispatch preparation, not a gateway reservation.
        """
        with self._transaction():
            active = self.outstanding()
            if not active or active.request_id != request_id or active.state != "dispatching":
                raise SubmissionClosed("no matching unsent preparation")
            self.db.execute("UPDATE submissions SET state='terminal',outcome='client_not_sent' WHERE request_id=?", (request_id,))

    def prune(self, *, before_unix: float) -> int:
        if not finite(before_unix):
            raise ValueError("finite pruning cutoff required")
        with self._transaction():
            self.db.execute("""DELETE FROM refresh_requests WHERE deadline<? AND
                (state='terminal' OR request_id IN (SELECT request_id FROM submissions WHERE state='terminal'))""", (before_unix,))
            return self.db.execute("DELETE FROM submissions WHERE state='terminal' AND deadline<?", (before_unix,)).rowcount

    def close(self) -> None:
        if not self._closed:
            self.db.close()
            self._closed = True
            self._lock.close()
