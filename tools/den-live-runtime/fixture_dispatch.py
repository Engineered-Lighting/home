"""Durable Home dispatch intent and original reports, separate from HA's ledger.

The caller supplies the admitted authority validator and the canonical PCB
codec. This journal never allocates wire IDs, signs grants, reconnects, retries
uncertain effects, or implements firmware state. Lost state is a refusal.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import threading

from fixture_reports import (FixtureRuntimeRefusal, canonical_bytes, decimal_u64, digest,
                             parse_original, require, retain_report, sha256, validate_scope)

CAP_FIELDS = ("evidence_not_after_home_us", "protection_not_after_home_us", "authority_not_after_home_us",
              "clock_qualification_not_after_home_us")
TERMINAL = {"ADMITTED", "DUPLICATE", "REJECTED", "EXPIRED", "CONTEXT_MISMATCH", "NO_SESSION",
            "CHANGED_DUPLICATE", "REPLAY", "UNAUTHORIZED", "NO_KEY", "MALFORMED", "BUSY", "INCOMPLETE",
            "CONFLICT", "OUT_OF_RANGE", "OK", "NOT_COMMITTED"}


class FixtureOutbox:
    """One exact source/context session; FULL synchronous commits before send.

    Storage is bounded by record/operation/payload budgets and SQLite's maximum
    page count. Each prepared operation reserves space for its attempt and
    terminal outcome, so report traffic cannot consume its causal receipt slots.
    Connection ownership is process local; this is not HA's shared ID allocator.
    """
    def __init__(self, path, scope, *, create=False, validate_admission=None, encode_plan=None,
                 encode_context=None, grant_canonical_bytes=canonical_bytes, validate_reconciliation=None,
                 clock_home_us=None, max_operations=10000,
                 max_records=50000, max_record_bytes=65536, max_archive_bytes=32 * 1024 * 1024,
                 max_disk_bytes=64 * 1024 * 1024):
        validate_scope(scope)
        require(type(max_operations) is int and 1 <= max_operations <= 100000, "operation_bound_required")
        require(type(max_records) is int and 3 <= max_records <= 500000, "record_bound_required")
        require(type(max_record_bytes) is int and 4096 <= max_record_bytes <= 131072, "record_byte_bound_required")
        require(type(max_archive_bytes) is int and max_record_bytes * 3 <= max_archive_bytes <= 256 * 1024 * 1024,
                "archive_bound_required")
        require(type(max_disk_bytes) is int and 1024 * 1024 <= max_disk_bytes <= 512 * 1024 * 1024,
                "disk_bound_required")
        self.path = Path(path)
        existed = self.path.exists()
        require(existed or create is True, "outbox_missing_no_automatic_reset")
        require(not (existed and create), "outbox_cannot_replace_existing")
        require(not existed or self.path.stat().st_size <= max_disk_bytes, "outbox_disk_bound_exceeded")
        self.scope = copy.deepcopy(scope)
        self._scope_bytes = canonical_bytes(scope)
        self._validate_admission = validate_admission
        self._encode_plan = encode_plan
        self._encode_context = encode_context
        self._grant_bytes = grant_canonical_bytes
        self._validate_reconciliation = validate_reconciliation
        self._clock_home_us = clock_home_us
        self._lock = threading.RLock()
        self._limits = {"max_operations": max_operations, "max_records": max_records,
                        "max_record_bytes": max_record_bytes, "max_archive_bytes": max_archive_bytes,
                        "max_disk_bytes": max_disk_bytes}
        self.db = sqlite3.connect(str(self.path), timeout=0, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        require(self.db.execute("PRAGMA max_page_count=" + str(max_disk_bytes // page_size)).fetchone()[0]
                <= max_disk_bytes // page_size, "outbox_page_bound_exceeded")
        try:
            if not existed:
                with self._tx():
                    self.db.execute("CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), scope BLOB NOT NULL, limits BLOB NOT NULL, quiesced INTEGER NOT NULL)")
                    self.db.execute("INSERT INTO meta VALUES (1,?,?,0)", (self._scope_bytes, canonical_bytes(self._limits)))
                    self.db.execute("CREATE TABLE operations (request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, identity_sha TEXT NOT NULL, payload BLOB NOT NULL, state TEXT NOT NULL, outcome BLOB, reserved_records INTEGER NOT NULL)")
                    self.db.execute("CREATE TABLE records (sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL, available_home_us TEXT, payload BLOB NOT NULL, previous_sha TEXT NOT NULL, record_sha TEXT NOT NULL)")
            meta = self.db.execute("SELECT * FROM meta WHERE id=1").fetchone()
            require(meta is not None and bytes(meta["scope"]) == self._scope_bytes, "outbox_scope_mismatch")
            require(bytes(meta["limits"]) == canonical_bytes(self._limits), "outbox_limits_mismatch")
            # A crash between durable ATTEMPTING and outcome is never a safe retry.
            with self._lock, self._tx():
                for row in self.db.execute("SELECT request_id FROM operations WHERE state='attempting'").fetchall():
                    self._finish(row["request_id"], "unknown", {"reason": "restart_after_dispatch_attempt", "effect": "unconfirmed",
                                                               "original_receipt_time_unavailable": True}, None)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _get(self, request_id):
        row = self.db.execute("SELECT * FROM operations WHERE request_id=?", (request_id,)).fetchone()
        require(row is not None, "outbox_identity_unknown")
        return dict(row)

    @staticmethod
    def _identity(value):
        require(type(value) is str and 0 < len(value) <= 128 and all(32 <= ord(char) < 127 for char in value),
                "immutable_request_identity_required")

    def _append(self, kind, entity_id, payload, available_home_us, *, future_records=0):
        if available_home_us is not None:
            decimal_u64(available_home_us)
        else:
            require(kind == "outcome" and payload.get("state") == "unknown"
                    and payload.get("outcome", {}).get("original_receipt_time_unavailable") is True,
                    "missing_original_receipt_clock")
        raw = canonical_bytes(payload)
        require(len(raw) <= self._limits["max_record_bytes"], "outbox_record_size_exhausted")
        count, used = self.db.execute("SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM records").fetchone()
        reserved = self.db.execute("SELECT COALESCE(SUM(reserved_records),0) FROM operations").fetchone()[0]
        reserved += future_records
        require(count + 1 + reserved <= self._limits["max_records"], "outbox_record_capacity_exhausted")
        require(used + len(raw) + reserved * self._limits["max_record_bytes"] <= self._limits["max_archive_bytes"],
                "outbox_archive_capacity_exhausted")
        last = self.db.execute("SELECT record_sha FROM records ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = last[0] if last else "0" * 64
        body = {"sequence": count + 1, "kind": kind, "entity_id": entity_id,
                "available_home_us": available_home_us, "payload": payload, "previous_sha256": previous}
        record_sha = sha256(canonical_bytes(body))
        self.db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                        (count + 1, kind, entity_id, available_home_us, raw, previous, record_sha))

    def _contract_ready(self):
        require(all(callable(item) for item in (self._validate_admission, self._encode_plan, self._encode_context,
                                               self._grant_bytes)), "admitted_authority_and_codec_unconfigured")

    def _validate_original(self, payload):
        self._contract_ready()
        plan, grant, reservation = payload["plan"], payload["grant_body"], payload["reservation"]
        wire = bytes.fromhex(payload["wire_hex"])
        context = bytes.fromhex(payload["context_hex"])
        require(0 < len(wire) <= 512 and 0 < len(context) <= 512, "wire_or_context_bound")
        require(self._encode_plan(copy.deepcopy(plan)) == wire, "original_wire_codec_mismatch")
        require(self._encode_context() == context, "original_context_codec_mismatch")
        require(sha256(context) == self.scope["context_digest"] == plan.get("context_digest"), "original_context_mismatch")
        require(sha256(wire) == payload["wire_sha256"], "original_wire_digest_mismatch")
        require(sha256(self._grant_bytes(grant)) == payload["grant_body_sha256"] == plan.get("grant_body_sha256"),
                "original_grant_digest_mismatch")
        require(grant.get("schema") == "home-fixture-execution-grant-1" and grant.get("version") == 1,
                "grant_family_mismatch")
        for name in ("fixture_id", "device_id", "context_digest", "pins", "ownership_generation",
                     "source_generation", "authority_generation", "domain_generations"):
            require(canonical_bytes(grant.get(name)) == canonical_bytes(self.scope[name]), "grant_scope_mismatch_" + name)
        for name in ("grant_id", "revision_id"):
            require(decimal_u64(reservation.get(name)) > 0 and reservation[name] == plan.get(name) == grant.get(name),
                    "reservation_plan_grant_identity_mismatch")
        require(plan.get("domain_generations") == self.scope["domain_generations"], "plan_domain_generation_mismatch")
        require(self._validate_admission(copy.deepcopy(grant), copy.deepcopy(reservation), copy.deepcopy(plan),
                                         copy.deepcopy(self.scope)) is True, "grant_not_durably_admitted")

    def prepare(self, request_id, reservation, admitted, *, available_home_us):
        """Persist a root-admitted object. Exact duplicate returns original state.

        ``admitted`` is root authority output: grant_body, grant_body_sha256,
        plan, wire_hex, wire_sha256, context_hex. It is independently checked by
        the trusted validator; a structurally plausible caller dictionary fails.
        """
        self._identity(request_id)
        decimal_u64(available_home_us)
        require(type(reservation) is dict and type(admitted) is dict, "admitted_object_required")
        require(set(reservation) == {"request_id", "intent_sha", "mode", "grant_id", "revision_id", "content_sha",
                                     "grant_sha", "state", "disposition", "barrier"}, "reservation_fields_invalid")
        required = {"grant_body", "grant_body_sha256", "plan", "wire_hex", "wire_sha256", "context_hex"}
        require(required <= set(admitted) <= required | {"decision_ref", "reservation", "physical_permission"},
                "admitted_object_fields")
        require(admitted.get("physical_permission", False) is False, "physical_permission_unsupported")
        if "reservation" in admitted:
            require(canonical_bytes(admitted["reservation"]) == canonical_bytes(reservation), "admitted_reservation_mismatch")
        require(reservation.get("request_id") == request_id, "reservation_request_mismatch")
        payload = {**copy.deepcopy(admitted), "reservation": copy.deepcopy(reservation),
                   "original_available_home_us": available_home_us}
        return self._prepare(request_id, "submit", payload, available_home_us)

    def prepare_withdraw(self, request_id, *, domain_mask, available_home_us, close=False):
        self._identity(request_id)
        decimal_u64(available_home_us)
        require(type(domain_mask) is int and 0 < domain_mask <= 255 and type(close) is bool
                and (not close or domain_mask == 255), "withdrawal_scope_invalid")
        payload = {"scope": copy.deepcopy(self.scope), "domain_mask": domain_mask, "close": close,
                   "original_available_home_us": available_home_us}
        return self._prepare(request_id, "withdraw", payload, available_home_us)

    def _prepare(self, request_id, operation, payload, available_home_us):
        raw = canonical_bytes(payload)
        # Receipt time is not part of operation identity. A retry must retain the
        # original first receipt rather than treating a later clock as freshness.
        identity = copy.deepcopy(payload)
        del identity["original_available_home_us"]
        identity_sha = sha256(canonical_bytes({"operation": operation, "body": identity}))
        with self._lock, self._tx():
            old = self.db.execute("SELECT * FROM operations WHERE request_id=?", (request_id,)).fetchone()
            if old:
                require(old["operation"] == operation and old["identity_sha"] == identity_sha, "changed_duplicate_outbox_identity")
                return self.status(request_id)
            require(not self.db.execute("SELECT quiesced FROM meta WHERE id=1").fetchone()[0], "outbox_quiesced")
            require(self.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] < self._limits["max_operations"],
                    "outbox_operation_capacity_exhausted")
            if operation == "submit":
                self._validate_original(payload)
                expiry = min(decimal_u64(payload["plan"][name]) for name in (*CAP_FIELDS, "support_expiry_home_us"))
                require(decimal_u64(available_home_us) < expiry, "original_authority_expired")
            self._append("prepared", request_id, {"operation": operation, "original": payload}, available_home_us, future_records=2)
            self.db.execute("INSERT INTO operations VALUES (?,?,?,?,?,NULL,2)",
                            (request_id, operation, identity_sha, raw, "prepared"))
            return self.status(request_id)

    def _finish(self, request_id, state, outcome, available_home_us):
        self.db.execute("UPDATE operations SET reserved_records=0 WHERE request_id=?", (request_id,))
        self._append("outcome", request_id, {"state": state, "outcome": outcome}, available_home_us)
        self.db.execute("UPDATE operations SET state=?,outcome=? WHERE request_id=?",
                        (state, canonical_bytes(outcome), request_id))

    def _now(self):
        require(callable(self._clock_home_us), "qualified_receipt_clock_unconfigured")
        value = self._clock_home_us()
        if type(value) is int:
            value = str(value)
        decimal_u64(value)
        return value

    def dispatch(self, request_id, client, *, now_home_us=None):
        started_home_us = self._now()
        if now_home_us is not None:
            require(decimal_u64(now_home_us) == decimal_u64(started_home_us), "caller_cannot_retimestamp_dispatch")
        now_home_us = started_home_us
        with self._lock, self._tx():
            row = self._get(request_id)
            if row["state"] != "prepared":
                return self.status(request_id)
            require(canonical_bytes(client.scope) == self._scope_bytes, "client_scope_mismatch")
            payload = parse_original(bytes(row["payload"]), max_bytes=self._limits["max_record_bytes"])
            try:
                if row["operation"] == "submit":
                    self._validate_original(payload)
                    expiry = min(decimal_u64(payload["plan"][name]) for name in (*CAP_FIELDS, "support_expiry_home_us"))
                    require(decimal_u64(now_home_us) < expiry, "original_authority_expired")
                require(not self.db.execute("SELECT quiesced FROM meta WHERE id=1").fetchone()[0], "outbox_quiesced")
            except ValueError as exc:
                self._finish(request_id, "refused", {"reason": getattr(exc, "reason", "admitted_authority_refused"),
                                                      "effect": "not_attempted"}, now_home_us)
                return self.status(request_id)
            self.db.execute("UPDATE operations SET state='attempting',reserved_records=1 WHERE request_id=?", (request_id,))
            self._append("attempt", request_id, {"operation": row["operation"], "original_identity_sha256": row["identity_sha"]}, now_home_us)
        # This call occurs only AFTER the FULL synchronous attempt commit. A
        # crash/timeout/lost acknowledgement remains unknown, including restart.
        try:
            if row["operation"] == "submit":
                outcome = client.submit(copy.deepcopy(payload["plan"]))
            else:
                outcome = client.withdraw(payload["domain_mask"], close=payload["close"])
            require(len(canonical_bytes(outcome)) <= 32768, "adapter_outcome_size_bound")
            require(outcome.get("evidence_source") == "simulated" and outcome.get("physical_permission") is False,
                    "adapter_outcome_provenance_unconfirmed")
            if row["operation"] == "submit":
                require(outcome.get("wire_sha256") == payload["wire_sha256"]
                        and outcome.get("semantic_revision_id") == payload["plan"]["revision_id"]
                        and type(outcome.get("finalization_complete")) is bool,
                        "adapter_outcome_identity_unconfirmed")
            else:
                require(outcome.get("domain_mask") == payload["domain_mask"], "withdrawal_outcome_scope_unconfirmed")
            disposition = outcome.get("device_disposition")
            require(disposition in TERMINAL | {"UNCERTAIN"}, "adapter_disposition_unconfirmed")
            state = "unknown" if disposition == "UNCERTAIN" or outcome.get("finalization_complete", True) is not True else "terminal"
        except Exception as exc:
            state = "unknown"
            outcome = {"reason": getattr(exc, "reason", "dispatch_exception_unconfirmed"), "effect": "unconfirmed"}
        try:
            received_home_us = self._now()
            if decimal_u64(received_home_us) < decimal_u64(started_home_us):
                state, outcome = "unknown", {"reason": "receipt_clock_rollback_unconfirmed", "effect": "unconfirmed",
                                             "original_adapter_receipt": outcome}
        except Exception:
            received_home_us = None
            state, outcome = "unknown", {"reason": "receipt_clock_unavailable_unconfirmed", "effect": "unconfirmed",
                                         "original_adapter_receipt": outcome, "original_receipt_time_unavailable": True}
        with self._lock, self._tx():
            require(self._get(request_id)["state"] == "attempting", "dispatch_state_changed_unconfirmed")
            self._finish(request_id, state, outcome, received_home_us)
            return self.status(request_id)

    def record_report(self, report_id, raw, *, first_received_home_us, category=None, sampled_device_us=None, admitted=None):
        report = retain_report(report_id, raw, scope=self.scope, first_received_home_us=first_received_home_us,
                               category=category, sampled_device_us=sampled_device_us, admitted=admitted)
        with self._lock, self._tx():
            old = self.db.execute("SELECT payload FROM records WHERE kind='report' AND entity_id=?", (report_id,)).fetchone()
            if old:
                original = parse_original(bytes(old[0]))
                comparison = dict(report, first_received_home_us=original["first_received_home_us"])
                require(canonical_bytes(comparison) == canonical_bytes(original), "changed_duplicate_report_identity")
                return original
            self._append("report", report_id, report, first_received_home_us)
        return report

    def reconcile(self, request_id, receipt, *, available_home_us):
        """Record an independently authenticated ORIGINAL disposition, never resend.

        STATUS alone cannot establish the disposition of an old evicted command.
        A fixed trusted verifier must bind this original receipt to the durable
        HA reservation and original command. No default reconciliation exists.
        """
        decimal_u64(available_home_us)
        require(callable(self._validate_reconciliation), "original_reconciliation_verifier_unconfigured")
        with self._lock, self._tx():
            row = self._get(request_id)
            require(row["state"] == "unknown", "only_unknown_effect_can_reconcile")
            original = parse_original(bytes(row["payload"]), max_bytes=self._limits["max_record_bytes"])
            require(self._validate_reconciliation(copy.deepcopy(original), copy.deepcopy(receipt),
                                                   copy.deepcopy(self.scope)) is True,
                    "original_reconciliation_unconfirmed")
            require(type(receipt) is dict and receipt.get("device_disposition") in TERMINAL
                    and receipt.get("physical_permission") is False and receipt.get("evidence_source") == "simulated",
                    "original_reconciliation_receipt_invalid")
            if row["operation"] == "submit":
                require(receipt.get("wire_sha256") == original["wire_sha256"]
                        and receipt.get("semantic_revision_id") == original["plan"]["revision_id"]
                        and receipt.get("finalization_complete") is True, "original_reconciliation_identity_invalid")
            else:
                require(receipt.get("domain_mask") == original["domain_mask"], "original_reconciliation_scope_invalid")
            self._append("reconciliation", request_id, {"original_receipt": receipt}, available_home_us)
            self.db.execute("UPDATE operations SET state='terminal',outcome=? WHERE request_id=?",
                            (canonical_bytes(receipt), request_id))
            return self.status(request_id)

    def status(self, request_id):
        with self._lock:
            row = self._get(request_id)
            return {"request_id": request_id, "operation": row["operation"], "state": row["state"],
                    "original_identity_sha256": row["identity_sha"],
                    "outcome": parse_original(bytes(row["outcome"])) if row["outcome"] is not None else None,
                    "physical_permission": False}

    def drain(self, *, available_home_us):
        """Quiesce admission; never infer cancellation or stop from this receipt."""
        decimal_u64(available_home_us)
        with self._lock, self._tx():
            self.db.execute("UPDATE meta SET quiesced=1 WHERE id=1")
            for row in self.db.execute("SELECT request_id FROM operations WHERE state='prepared'").fetchall():
                self._finish(row[0], "abandoned", {"reason": "drain_before_attempt", "effect": "not_attempted"}, available_home_us)
            pending = self.db.execute("SELECT COUNT(*) FROM operations WHERE state IN ('attempting','unknown')").fetchone()[0]
            return {"status": "incomplete" if pending else "drained", "unconfirmed_operations": pending,
                    "records": self.db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                    "physical_stop_confirmed": False}

    def export_archive(self):
        with self._lock:
            records = []
            for row in self.db.execute("SELECT * FROM records ORDER BY sequence"):
                records.append({"sequence": row["sequence"], "kind": row["kind"], "entity_id": row["entity_id"],
                                "available_home_us": row["available_home_us"], "payload": parse_original(bytes(row["payload"])),
                                "previous_sha256": row["previous_sha"], "record_sha256": row["record_sha"]})
            return {"schema": "home-fixture-runtime-archive-1", "version": 1, "scope": copy.deepcopy(self.scope),
                    "quiesced": bool(self.db.execute("SELECT quiesced FROM meta WHERE id=1").fetchone()[0]), "records": records}

    def close(self):
        with self._lock:
            self.db.close()
