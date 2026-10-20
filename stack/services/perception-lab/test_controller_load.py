"""Content-free controller load instrumentation for the next live test."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import controller_load
from config import Settings
from controller import Controller, STORAGE_RECONCILE_INTERVAL_S
from controller_load import LoadMonitor, MAX_SAMPLES, PHASES
from store import Store


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class LoadMonitorTests(unittest.TestCase):
    def test_cpu_percent_of_one_core_rss_and_phase_percentiles(self):
        cpu, wall = Clock(10.0), Clock(100.0)
        monitor = LoadMonitor(process_time=cpu, monotonic=wall, rss=lambda: 300*1024**2)
        self.assertIsNone(monitor.live_view())
        for ms in (5, 1, 3, 2, 4):
            monitor.observe("live_handler_ms", ms)
        monitor.observe("graph_handler_ms", 40)
        for ignored in (("unknown_phase", 1), ("live_handler_ms", -1), ("live_handler_ms", float("nan")),
                        ("live_handler_ms", "7"), ("live_handler_ms", True)):
            monitor.observe(*ignored)
        cpu.value, wall.value = 12.5, 105.0
        summary = monitor.sample()
        self.assertEqual(summary["cpu_pct_1core"], 50.0)
        self.assertEqual(summary["cpu_s"], 2.5)
        self.assertEqual(summary["interval_s"], 5.0)
        self.assertEqual(summary["rss_mb"], 300.0)
        self.assertEqual(set(summary["phases"]), set(PHASES))
        self.assertEqual(summary["phases"]["live_handler_ms"], {"n": 5, "p50": 3.0, "p95": 5.0, "max": 5.0})
        self.assertEqual(summary["phases"]["reasoning_build_ms"], {"n": 0, "p50": None, "p95": None, "max": None})
        self.assertEqual(monitor.live_view(), {"cpu_pct_1core": 50.0, "rss_mb": 300.0, "live_handler_ms_p50": 3.0,
                                               "graph_handler_ms_p50": 40.0, "reasoning_build_ms_p50": None})
        # The next interval starts empty.
        cpu.value, wall.value = 12.6, 110.0
        self.assertEqual(monitor.sample()["phases"]["live_handler_ms"]["n"], 0)

    def test_samples_are_bounded_per_interval(self):
        monitor = LoadMonitor(rss=lambda: None)
        for i in range(MAX_SAMPLES*3):
            monitor.observe("graph_handler_ms", i)
        phase = monitor.sample()["phases"]["graph_handler_ms"]
        self.assertEqual(phase["n"], MAX_SAMPLES*3)
        self.assertEqual(phase["max"], MAX_SAMPLES*3-1)
        self.assertEqual(len(monitor.samples["graph_handler_ms"]), 0)

    def test_timed_records_elapsed_milliseconds(self):
        monitor = LoadMonitor(rss=lambda: None)
        with monitor.timed("live_room_ms"):
            pass
        phase = monitor.sample()["phases"]["live_room_ms"]
        self.assertEqual(phase["n"], 1)
        self.assertGreaterEqual(phase["p50"], 0)


class Runtime:
    def live(self, session):
        return {"arms": {}, "state": "ready"}


class ControllerLoadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.now = 1000.0
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0, quota_bytes=64*1024**2),
                           clock=lambda: self.now)
        self.cpu, self.wall = Clock(1.0), Clock(0.0)
        self.monitor = LoadMonitor(process_time=self.cpu, monotonic=self.wall, rss=lambda: 64*1024**2)
        self.controller = Controller(self.store, Runtime(), load_monitor=self.monitor, monotonic=self.wall)
        session, _ = self.store.create_session("den", "detection", "Fixture", "request1")
        self.sid = session["id"]

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def loads(self):
        return [e["payload"] for e in self.store.list_events(self.sid) if e["kind"] == "controller.load"]

    def test_periodic_receipt_only_while_recording_and_live_exposes_the_latest(self):
        self.wall.value = 6.0
        self.controller.expire()
        self.assertEqual(self.loads(), [], "no receipt for a session that is not recording")
        self.store.transition(self.sid, "starting")
        self.store.transition(self.sid, "recording")
        self.controller.live(self.sid)
        self.controller.live(self.sid)
        self.wall.value, self.cpu.value = 9.0, 1.5
        self.controller.expire()
        self.assertEqual(self.loads(), [], "not yet due: one receipt per 5 s")
        self.store.add_media(self.sid, b"x"*100, "image/jpeg", {})
        self.wall.value, self.cpu.value = 11.0, 2.0
        self.controller.expire()
        [payload] = self.loads()
        self.assertEqual(set(payload), {"interval_s", "cpu_s", "cpu_pct_1core", "rss_mb", "phases", "storage", "upkeep_failures",
                                        "available_at"})
        self.assertEqual(payload["upkeep_failures"], {"expiry": 0, "reconcile": 0, "load": 0})
        self.assertEqual(payload["cpu_pct_1core"], 20.0)
        self.assertEqual(payload["rss_mb"], 64.0)
        self.assertEqual(payload["phases"]["live_handler_ms"]["n"], 2)
        self.assertEqual(payload["phases"]["live_runtime_ms"]["n"], 2)
        self.assertEqual(payload["phases"]["live_room_ms"]["n"], 2)
        self.assertEqual(set(payload["storage"]), {"checks", "full_scans", "drift_corrections", "scan_failures", "last_scan_ms",
                                                   "unowned_entries"})
        self.assertGreaterEqual(payload["storage"]["checks"], 1)
        self.assertEqual(payload["storage"]["full_scans"], 0, "no scan in the interval: writes use the running total")
        # Content-free: no identifiers, paths or payload text.
        encoded = json.dumps(payload)
        for text in (self.sid, self.directory.name.replace("\\", "\\\\"), "den", "Fixture"):
            self.assertNotIn(text, encoded)
        live = self.controller.live(self.sid)
        self.assertEqual(live["controller_load"], {"cpu_pct_1core": 20.0, "rss_mb": 64.0,
            "live_handler_ms_p50": payload["phases"]["live_handler_ms"]["p50"], "graph_handler_ms_p50": None,
            "reasoning_build_ms_p50": None})
        self.wall.value = 16.0
        self.controller.expire()
        self.assertEqual(len(self.loads()), 2)

    def test_graph_handler_time_is_recorded(self):
        with patch("graph.graph", return_value={}):
            self.controller.graph(self.sid)
        self.assertEqual(self.monitor.sample()["phases"]["graph_handler_ms"]["n"], 1)

    def test_background_reconciliation_runs_on_its_interval_only(self):
        before = self.store.accounting["reconciliations"]
        self.wall.value = STORAGE_RECONCILE_INTERVAL_S-1
        self.controller.expire()
        self.assertEqual(self.store.accounting["reconciliations"], before)
        (Path(self.directory.name)/"media"/"external").write_bytes(b"e"*21)
        self.wall.value = STORAGE_RECONCILE_INTERVAL_S
        self.controller.expire()
        self.assertEqual(self.store.accounting["reconciliations"], before+1)
        self.assertEqual(self.store.accounting["last_drift_bytes"], 21)
        self.wall.value = STORAGE_RECONCILE_INTERVAL_S+30
        self.controller.expire()
        self.assertEqual(self.store.accounting["reconciliations"], before+1)

    def test_an_upkeep_error_never_stops_duration_limits_reconciliation_or_load(self):
        from store import LabError
        self.store.transition(self.sid, "starting")
        self.store.transition(self.sid, "recording")
        failing = LabError("fixture private text", 503, "fixture")
        self.wall.value = STORAGE_RECONCILE_INTERVAL_S
        with patch.object(self.store, "reconcile_storage", side_effect=failing), \
                patch.object(self.monitor, "sample", side_effect=RuntimeError("fixture private text")):
            with self.assertLogs("perception_lab.controller", "WARNING") as logged:
                self.controller.expire()  # never raises
        self.assertEqual(self.controller.upkeep_failures, {"expiry": 0, "reconcile": 1, "load": 1})
        self.assertNotIn("fixture private text", " ".join(logged.output))
        self.assertIn("RuntimeError", " ".join(logged.output))
        # Duration-limit enforcement still runs even when the next steps fail.
        self.now = self.store.get_session(self.sid)["started_at"]+self.store.settings.max_session_seconds
        self.wall.value = 2*STORAGE_RECONCILE_INTERVAL_S
        with patch.object(self.store, "reconcile_storage", side_effect=OSError("fixture")), \
                patch.object(self.monitor, "sample", side_effect=RuntimeError("fixture")):
            self.controller.expire()
        self.assertEqual(self.store.get_session(self.sid)["state"], "stopping")  # stub runtime: stop unconfirmed
        self.assertEqual(self.store.requested_stop(self.sid), "duration_limit")
        # A failing session lookup is counted, and the rest of upkeep still runs.
        self.wall.value = 3*STORAGE_RECONCILE_INTERVAL_S
        with patch.object(self.store, "recording_sessions", side_effect=RuntimeError("fixture")):
            with patch.object(self.store, "reconcile_storage") as reconcile:
                self.controller.expire()
        reconcile.assert_called_once_with("interval")
        self.assertEqual(self.controller.upkeep_failures["expiry"], 1)
        self.assertGreaterEqual(self.controller.upkeep_failures["load"], 2)

    def test_expiry_reads_only_recording_sessions(self):
        for i in range(5):
            self.store.create_session(f"cam{i}", "detection", "Other", f"other{i}")
        calls = []
        real = self.store.get_session
        self.store.get_session = lambda sid: calls.append(sid) or real(sid)
        self.controller.expire()
        self.assertEqual(calls, [], "no session is recording: none is read")


class ReasoningTimingTests(unittest.TestCase):
    def test_scheduler_offer_and_jev_build_and_send_are_timed(self):
        from test_reasoning_load import Simulation
        monitor = LoadMonitor(rss=lambda: None)
        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with patch.object(controller_load, "MONITOR", monitor):
            sim = Simulation(directory.name, filler_files=0)
            try:
                sim.preview(); sim.output("a"); sim.tick()
            finally:
                sim.close()
                directory.cleanup()
        phases = monitor.sample()["phases"]
        for name in ("reasoning_offer_ms", "reasoning_build_ms", "reasoning_send_ms"):
            self.assertEqual(phases[name]["n"], 1, name)


if __name__ == "__main__":
    unittest.main()
