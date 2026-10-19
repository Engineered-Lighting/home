"""Synthetic workstation admission cases. No values here are deployment caps."""

from copy import deepcopy
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.admission import (
    AdmissionClosed, AdmissionController, AdmissionError, AdmissionLedger,
    CapacityExceeded, LedgerLocked, RECEIPTS, RequestConflict, ResourceManifest,
    VerifiedReceipt, canonical_digest,
)


def resources(amount):
    return {"ram_bytes": amount, "vram_bytes": amount,
            "cpu_millicores": amount, "io_bytes_per_second": amount}


def manifest_fixture():
    """Small abstract capacities, deliberately unrelated to either real host."""
    # Independent test processes have independent in-memory fake engines. Keep
    # production fencing global while avoiding false cross-suite contention.
    engine_id = "fake-engine-" + str(os.getpid())
    return {
        "version": 2, "engine_id": engine_id, "model_alias": "fake-model",
        "model_revision": "a" * 64, "upstream": "http://127.0.0.1:19999/v1",
        "services": [{"id": engine_id, "image_sha256": "b" * 64,
                      "model_sha256": "a" * 64, "startup_peak": resources(15),
                      "steady_peak": resources(10),
                      "caps": {"ram_bytes": 30, "swap_bytes": 0, "cpu_millicores": 30,
                               "pids": 10, "io_bytes_per_second": 30}}],
        "capacity": resources(100), "os_recovery_reserve": resources(20),
        "resident_overlap_peak": resources(20), "request_peak": resources(10),
        "max_active": 1, "max_queue": 3, "max_ledger_records": 1000, "max_input_tokens": 1000,
        "max_output_tokens": 128, "max_image_bytes": 256 * 1024,
        "max_deadline_seconds": 15,
        "consumer_routes": {"fake-echo": "echo", "fake-victoria": "victoria"},
        "free_storage_floor_bytes": 1, "health_max_age_seconds": 5,
        "receipt_max_age_seconds": 60, "measurement_artifact_sha256": "c" * 64,
    }


class FakeVerifier:
    """Explicitly injected test-only authority with no production connector."""

    def __init__(self):
        self.age = 0
        self.omit = None
        self.mismatch = False

    def verify_admission(self, manifest, engine_epoch, now):
        return tuple(VerifiedReceipt(kind, "0" * 64 if self.mismatch else manifest.digest,
                                     engine_epoch, now - self.age, now + 30, "d" * 64)
                     for kind in RECEIPTS if kind != self.omit)

    def verify_terminal(self, manifest, engine_epoch, request_id, evidence):
        return evidence == (engine_epoch, request_id, "finished")


class ManifestTests(unittest.TestCase):
    def test_requires_complete_immutable_manifest(self):
        self.assertEqual(ResourceManifest.from_dict(manifest_fixture()).max_active, 1)
        for field in manifest_fixture():
            data = manifest_fixture()
            del data[field]
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)
        for field, bad in (("model_revision", "latest"), ("version", True),
                           ("version", 1), ("max_active", 0), ("max_image_bytes", 256 * 1024 + 1),
                           ("max_deadline_seconds", 16)):
            data = manifest_fixture()
            data[field] = bad
            with self.subTest(field=field), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)

    def test_metadata_cap_is_explicit_positive_bounded_integer(self):
        for cap in (None, False, True, 0, -1, 1.5, float("nan"), float("inf"), "100", 1_000_001):
            data = manifest_fixture()
            data["max_ledger_records"] = cap
            with self.subTest(cap=cap), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)
        for cap in (1, 1_000_000):
            data = manifest_fixture()
            data["max_ledger_records"] = cap
            self.assertEqual(ResourceManifest.from_dict(data).max_ledger_records, cap)

    def test_bounds_startup_steady_overlap_and_enforced_caps(self):
        variants = []
        data = manifest_fixture()
        data["max_active"] = 100
        variants.append(data)
        data = manifest_fixture()
        data["services"][0]["caps"]["ram_bytes"] = 5
        variants.append(data)
        data = manifest_fixture()
        service = deepcopy(data["services"][0])
        service["id"] = "speech"
        data["services"].append(service)
        variants.append(data)
        data = manifest_fixture()
        data["services"][0]["steady_peak"]["vram_bytes"] = float("nan")
        variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)

    def test_fixed_upstream_and_binding_immutable(self):
        for url in ("http://192.168.0.10/v1", "https://user:secret@server/v1",
                    "https://server/v1?redirect=foo", "file:///etc/passwd"):
            data = manifest_fixture()
            data["upstream"] = url
            with self.subTest(url=url), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)
        data = manifest_fixture()
        manifest = ResourceManifest.from_dict(data)
        data["consumer_routes"]["fake-echo"] = "victoria"
        self.assertEqual(manifest.consumer_routes["fake-echo"], "echo")
        with self.assertRaises(TypeError):
            manifest.consumer_routes["fake-echo"] = "victoria"


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = AdmissionLedger(self.path)
        self.now = 1000.0
        self.verifier = FakeVerifier()
        self.manifest = ResourceManifest.from_dict(manifest_fixture())
        self.controller = AdmissionController(self.ledger, self.manifest, self.verifier,
                                              clock=lambda: self.now)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def submit(self, request="req-1", task="background", site="victoria", consumer="fake-victoria"):
        return self.controller.submit(request, site, consumer, canonical_digest({"id": request}),
                                      task, self.now + 15)

    def test_default_closed_and_each_gate_required(self):
        self.assertFalse(self.controller.readiness()["ready"])
        with self.assertRaises(AdmissionClosed):
            self.submit()
        for kind in RECEIPTS:
            self.verifier.omit = kind
            with self.subTest(kind=kind), self.assertRaises(AdmissionClosed):
                self.controller.enable("epoch-1")
        self.verifier.omit = None
        self.controller.enable("epoch-1")
        self.assertTrue(self.controller.readiness()["ready"])

    def test_stale_mismatched_and_future_receipts_close_gate(self):
        for age in (6, -1):
            self.verifier.age = age
            with self.subTest(age=age), self.assertRaises(AdmissionClosed):
                self.controller.enable("epoch-1")
        self.verifier.age = 0
        self.controller.enable("epoch-1")
        self.verifier.mismatch = True
        with self.assertRaises(AdmissionClosed):
            self.submit()
        self.assertFalse(self.controller.readiness()["ready"])

    def test_no_verifier_cannot_enable(self):
        for manifest in (None, self.manifest):
            controller = AdmissionController(self.ledger, manifest)
            with self.assertRaises(AdmissionClosed):
                controller.enable("epoch-1")

    def test_nonfinite_admission_clock_rejected(self):
        self.now = float("nan")
        with self.assertRaises(AdmissionError):
            self.controller.enable("epoch-1")

    def test_lifetime_lock_excludes_second_dispatcher(self):
        with self.assertRaises(LedgerLocked):
            AdmissionLedger(self.path)

    def test_engine_lock_also_excludes_accidentally_separate_ledgers(self):
        self.controller.enable("epoch-1")
        with AdmissionLedger(Path(self.temp.name) / "other-ledger.sqlite3") as second:
            other = AdmissionController(second, self.manifest, self.verifier, clock=lambda: self.now)
            with self.assertRaises(LedgerLocked):
                other.enable("epoch-1")

    def test_namespaced_consumer_and_idempotency(self):
        self.controller.enable("epoch-1")
        with self.assertRaises(AdmissionClosed):
            self.submit(site="echo")
        first = self.submit()
        self.assertEqual(self.submit(), first)
        with self.assertRaises(RequestConflict):
            self.controller.submit("req-1", "victoria", "fake-victoria", "f" * 64,
                                   "background", self.now + 15)

    def test_priority_queue_and_reservation_commit_precedes_dispatch(self):
        self.controller.enable("epoch-1")
        self.submit("background")
        self.submit("interactive", "interactive", "echo", "fake-echo")
        request = self.controller.claim_next()
        self.assertEqual(request.request_id, "interactive")
        self.assertEqual(request.state, "dispatching")
        self.assertEqual(dict(request.reservation), resources(10))
        # Separate read-only connection sees the FULL-synchronous committed row.
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute("SELECT state FROM requests WHERE request_id='interactive'").fetchone()[0],
                             "dispatching")
        self.assertIsNone(self.controller.claim_next())
        self.controller.mark_running("interactive")
        self.controller.finish("interactive", ("epoch-1", "interactive", "finished"))
        self.assertEqual(self.controller.claim_next().request_id, "background")

    def test_bounded_queue_and_expiry(self):
        self.controller.enable("epoch-1")
        for number in range(3):
            self.submit(str(number))
        with self.assertRaises(CapacityExceeded):
            self.submit("overflow")
        self.now += 16
        self.assertIsNone(self.controller.claim_next())
        self.assertEqual(self.ledger.get("0").outcome, "queue_expired")
        self.submit("new")

    def test_expiry_does_not_release_running_reservation(self):
        self.controller.enable("epoch-1")
        self.submit()
        self.controller.claim_next()
        self.controller.mark_running("req-1")
        self.now += 60
        self.assertIsNone(self.controller.claim_next())
        self.assertEqual(self.ledger.get("req-1").state, "running")
        with self.assertRaises(AdmissionClosed):
            self.controller.finish("req-1", ("wrong-epoch", "req-1", "finished"))
        self.assertTrue(self.ledger.get("req-1").reservation)

    def test_restart_keeps_dispatch_and_running_charged_until_proven_terminal(self):
        for initial in ("dispatching", "running"):
            self.controller.enable("epoch-1")
            self.submit(initial)
            self.controller.claim_next()
            if initial == "running":
                self.controller.mark_running(initial)
            self.submit("queued-" + initial)
            self.ledger.close()
            self.ledger = AdmissionLedger(self.path)
            self.controller = AdmissionController(self.ledger, self.manifest, self.verifier, clock=lambda: self.now)
            record = self.ledger.get(initial)
            self.assertEqual(record.state, "indeterminate")
            self.assertTrue(record.reservation)
            self.assertEqual(self.ledger.get("queued-" + initial).outcome, "restart_before_dispatch")
            self.assertFalse(self.controller.readiness()["ready"])
            with self.assertRaises(AdmissionClosed):
                self.controller.enable("epoch-2")
            self.controller.finish(initial, ("epoch-1", initial, "finished"))
            self.controller.enable("epoch-2")
            self.assertEqual(self.ledger.get(initial).reservation, {})

    def test_ledger_write_failure_closes_admission(self):
        self.controller.enable("epoch-1")
        self.ledger.db.execute("PRAGMA query_only=ON")
        with self.assertRaises(AdmissionClosed):
            self.submit()
        self.assertTrue(self.ledger.broken)
        self.assertFalse(self.controller.readiness()["ready"])

    def test_metadata_ledger_contains_no_payload(self):
        self.controller.enable("epoch-1")
        secret_body = {"prompt": "private-camera-secret-123", "image": "private-jpeg"}
        self.controller.submit("req", "echo", "fake-echo", canonical_digest(secret_body),
                               "interactive", self.now + 15)
        row = self.ledger.db.execute("SELECT * FROM requests").fetchone()
        self.assertNotIn(secret_body["prompt"], str(tuple(row)))

    def reject(self, request="req-1", outcome="rejected_capacity"):
        return self.controller.reject_if_absent(request, "victoria", "fake-victoria",
                                                canonical_digest({"id": request}), "background", self.now + 15, outcome)

    def test_rejection_is_committed_terminal_tombstone_without_resource_charge(self):
        record = self.reject()
        self.assertEqual((record.state, record.outcome, dict(record.reservation), record.engine_epoch),
                         ("terminal", "rejected_capacity", {}, None))
        with closing(sqlite3.connect(self.path)) as observer:
            self.assertEqual(observer.execute("SELECT outcome FROM requests").fetchone()[0], "rejected_capacity")
        self.controller.enable("epoch-1")
        self.assertEqual(self.submit(), record)
        self.assertIsNone(self.controller.claim_next())
        self.assertEqual(self.reject(outcome="rejected_closed"), record)
        with self.assertRaises(RequestConflict):
            self.controller.reject_if_absent("req-1", "victoria", "fake-victoria", "f" * 64,
                                              "background", self.now + 15, "rejected_closed")

    def test_rejection_cannot_overwrite_any_accepted_state(self):
        self.controller.enable("epoch-1")
        self.submit()
        self.assertEqual(self.reject().state, "queued")
        self.controller.claim_next()
        self.assertEqual(self.reject().state, "dispatching")
        self.controller.mark_running("req-1")
        self.assertEqual(self.reject().state, "running")
        self.controller.mark_indeterminate("req-1")
        self.assertEqual(self.reject().state, "indeterminate")
        self.assertTrue(self.ledger.get("req-1").reservation)

    def test_acceptance_and_rejection_race_has_one_immutable_decision(self):
        self.controller.enable("epoch-1")
        start = threading.Barrier(2)
        records, errors = [], []
        def run(operation):
            try:
                start.wait(2)
                records.append(operation())
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=(operation,)) for operation in (self.submit, self.reject)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], records[1])
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_commit_uncertainty_returns_no_receipt_before_or_after_actual_commit(self):
        real = self.ledger.db
        class FaultConnection:
            def __init__(self, after):
                self.after = after
            def __getattr__(self, name):
                return getattr(real, name)
            def execute(self, statement, *args):
                if statement == "COMMIT":
                    if self.after:
                        real.execute(statement)
                    raise sqlite3.OperationalError("simulated commit uncertainty")
                return real.execute(statement, *args)
        for after in (False, True):
            self.ledger.db = FaultConnection(after)
            with self.subTest(after_commit=after), self.assertRaises(AdmissionClosed):
                self.reject(request="after" if after else "before")
            self.assertTrue(self.ledger.broken)
            self.ledger.db = real
            # Simulate authoritative reopen after a failed process, not a
            # production failure reset; commit state is inspected on disk.
            self.ledger.close()
            self.ledger = AdmissionLedger(self.path)
            real = self.ledger.db
            self.controller = AdmissionController(self.ledger, self.manifest, clock=lambda: self.now)
            record = self.ledger.get("after" if after else "before")
            self.assertEqual(record is not None, after)
            if after:
                self.assertEqual(record.outcome, "rejected_capacity")

    def test_rejection_readback_failure_returns_no_receipt(self):
        original = self.ledger.get
        calls = []
        def failure(request_id):
            calls.append(request_id)
            if len(calls) == 2:
                raise AdmissionClosed("readback unavailable")
            return original(request_id)
        with patch.object(self.ledger, "get", side_effect=failure), self.assertRaises(AdmissionClosed):
            self.reject()
        self.assertEqual(original("req-1").state, "terminal")

    def test_no_manifest_cannot_allocate_new_rejection_but_existing_record_is_readable(self):
        stored = self.reject()
        self.controller.manifest = None
        self.assertEqual(self.reject(), stored)
        with self.assertRaises(AdmissionClosed):
            self.reject("new")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_rejections_obey_storage_floor_and_zero_free_does_not_write(self):
        with patch("home_shared.admission.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaises(AdmissionClosed):
                self.reject()
        self.assertFalse(self.ledger.broken)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def test_acceptance_rechecks_storage_floor_inside_transaction_before_insert(self):
        self.controller.enable("epoch-1")
        measurements = []
        def headroom(_):
            self.assertTrue(self.ledger.db.in_transaction)
            measurements.append(True)
            return SimpleNamespace(free=100 if len(measurements) == 1 else 0)
        with patch("home_shared.admission.shutil.disk_usage", side_effect=headroom), self.assertRaises(AdmissionClosed):
            self.submit()
        self.assertEqual(len(measurements), 2)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def test_rejected_tombstones_exhaust_shared_record_cap_without_pruning(self):
        data = manifest_fixture()
        data["max_ledger_records"] = 2
        self.controller.manifest = ResourceManifest.from_dict(data)
        first = self.reject("one")
        self.reject("two")
        with self.assertRaises(AdmissionClosed):
            self.reject("three")
        self.controller.enable("epoch-1")
        with self.assertRaises(AdmissionClosed):
            self.submit("accepted")
        self.assertEqual(self.reject("one"), first)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 2)

    def test_accepted_and_rejected_rows_share_cap_while_terminal_reconciliation_remains_available(self):
        data = manifest_fixture()
        data["max_ledger_records"] = 2
        self.controller.manifest = ResourceManifest.from_dict(data)
        self.controller.enable("epoch-1")
        self.submit()
        rejected = self.reject("rejected")
        active = self.controller.claim_next()
        with self.assertRaises(AdmissionClosed):
            self.reject("overflow")
        with patch("home_shared.admission.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            self.assertEqual(self.reject("rejected"), rejected)
            self.assertEqual(self.submit().state, "dispatching")
            self.controller.finish(active.request_id, ("epoch-1", active.request_id, "finished"))
        self.assertEqual(self.ledger.get(active.request_id).state, "terminal")
        self.assertEqual(self.ledger.get(active.request_id).reservation, {})


if __name__ == "__main__":
    unittest.main()
