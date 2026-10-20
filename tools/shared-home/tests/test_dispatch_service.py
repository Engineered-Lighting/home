"""Real local gateway/SQLite lifecycle tests; fake model, no live host."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.dispatch_service import DispatchPump
from home_shared.gateway import Gateway, jpeg_dimensions
from test_admission import FakeVerifier, manifest_fixture
from test_gateway import FakeBackend, binding_fixture, request_fixture


class DispatchPumpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = AdmissionLedger(Path(self.temp.name) / "ledger.db")
        self.controller = AdmissionController(
            self.ledger, ResourceManifest.from_dict(manifest_fixture()), FakeVerifier(), clock=lambda: 1000)
        self.backend = FakeBackend(self.controller)
        self.gateway = Gateway(self.controller, self.backend, clock=lambda: 1000,
                               image_validator=jpeg_dimensions)
        self.pump = DispatchPump(self.gateway, poll_seconds=0.01)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.assertTrue(self.pump.stop(timeout_seconds=2))
        self.ledger.close()
        self.temp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.01)
        self.fail("expected lifecycle transition did not occur")

    def enqueue(self, site="victoria", priority="background"):
        payload = request_fixture(site=site)
        payload["task_class"] = priority
        return self.gateway.submit(payload, binding_fixture(site))

    def test_snapshot_processed_without_http_chat_or_manual_dispatch(self):
        self.controller.enable("epoch-1")
        self.enqueue()
        self.pump.start()
        self.wait_for(lambda: self.gateway.status("request-1", binding_fixture())["status"] == "terminal")
        self.assertEqual(len(self.backend.calls), 1)
        self.assertFalse(self.pump.failed)

    def test_existing_queue_prioritizes_interactive_across_sites(self):
        self.controller.enable("epoch-1")
        self.enqueue()
        self.enqueue("echo", "interactive")
        self.pump.start()
        self.wait_for(lambda: len(self.backend.calls) == 2)
        self.assertEqual([r.site_id for r in self.backend.calls], ["echo", "victoria"])

    def test_closed_gateway_never_executes_or_enables(self):
        self.controller.enable("epoch-1")
        self.enqueue()
        self.controller.close_admission("maintenance")
        observed = threading.Event()
        original = self.gateway.reconcile_late_completions
        def reconcile():
            result = original()
            observed.set()
            return result
        with patch.object(self.gateway, "reconcile_late_completions", side_effect=reconcile):
            self.pump.start()
            self.assertTrue(observed.wait(1))
            self.assertTrue(self.pump.stop(timeout_seconds=1))
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.controller.readiness()["ready"])

    def test_duplicate_owner_and_restart_rejected_even_after_stop(self):
        with self.assertRaises(RuntimeError):
            DispatchPump(self.gateway, poll_seconds=0.01)
        self.pump.start()
        with self.assertRaises(RuntimeError):
            self.pump.start()
        self.assertTrue(self.pump.stop(timeout_seconds=1))
        with self.assertRaises(RuntimeError):
            DispatchPump(self.gateway, poll_seconds=0.01)
        with self.assertRaises(RuntimeError):
            self.pump.start()

    def test_slow_job_stop_keeps_reservation_owner_and_ledger_then_joins(self):
        entered = threading.Event()
        def slow(request):
            entered.set()
            self.release.wait(2)
        self.backend.on_execute = slow
        self.controller.enable("epoch-1")
        self.enqueue()
        self.pump.start()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertFalse(self.pump.stop(timeout_seconds=0.01))
        self.assertLess(time.monotonic() - started, 0.5)
        record = self.ledger.get(self.backend.calls[0].ledger_id)
        self.assertEqual(record.state, "running")
        self.assertTrue(record.reservation)
        self.assertFalse(self.ledger._closed)
        self.assertIs(self.gateway._dispatch_pump, self.pump)
        self.assertFalse(self.controller.readiness()["ready"])
        self.release.set()
        self.assertTrue(self.pump.stop(timeout_seconds=1))
        self.assertEqual(self.ledger.get(record.request_id).state, "terminal")
        self.assertFalse(self.ledger._closed)

    def test_error_closes_and_stops_without_exposing_exception(self):
        self.controller.enable("epoch-1")
        with patch.object(self.gateway, "reconcile_late_completions", side_effect=RuntimeError("private-secret")):
            self.pump.start()
            self.wait_for(lambda: self.pump.failed)
            self.assertEqual(self.controller.readiness()["reason"], "dispatch pump failed")
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.ledger._closed)

    def test_late_completion_reconciled_while_closed_without_reopening(self):
        entered = threading.Event()
        def late(request):
            entered.set()
            self.release.wait(2)
        self.backend.on_execute = late
        self.controller.enable("epoch-1")
        self.enqueue()
        # The worker has no deadline time remaining once dispatch has begun.
        # Its independent ledger clock still permits claiming this queued job.
        self.gateway.clock = lambda: 1015
        self.pump.start()
        self.assertTrue(entered.wait(1))
        record_id = self.backend.calls[0].ledger_id
        self.wait_for(lambda: self.ledger.get(record_id).state == "indeterminate")
        self.assertTrue(self.ledger.get(record_id).reservation)
        self.assertFalse(self.controller.readiness()["ready"])
        self.release.set()
        self.wait_for(lambda: self.ledger.get(record_id).state == "terminal")
        self.assertFalse(self.controller.readiness()["ready"])
        self.assertFalse(self.ledger.get(record_id).reservation)
        self.assertEqual(self.gateway._results, {})
        self.assertFalse(self.pump.failed)

    def test_finite_bounded_lifecycle_arguments(self):
        for value in (True, -1, float("nan"), float("inf"), 61, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.pump.stop(timeout_seconds=value)
        for value in (True, 0, -1, float("nan"), float("inf"), 6, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DispatchPump(self.gateway, poll_seconds=value)


if __name__ == "__main__":
    unittest.main()
