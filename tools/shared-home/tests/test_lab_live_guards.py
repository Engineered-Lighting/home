"""Speed plan S1.4 on the LA live-scene consumer: the thermal ladder, the yield
to vLLM's queue and bounded retention (in service and the offline prune).
Temp SQLite, fake verdicts and vLLM counts only: no model, host or credential."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import camera_pilot_service as service
from home_shared import lab_live_pilot as pilot_live
from home_shared import lab_live_prune
from home_shared import lab_live_scene as live
from home_shared.camera_pilot import CameraPilot
from home_shared.model_worker import WorkerNotSent
from home_shared.pilot_health import HealthCache
from home_shared.pilot_runtime import RuntimeOptions
from test_gateway import binding_fixture
from test_lab_live_pilot import CONTAINER, EPOCH, PilotLiveFixture, completion, config_value


def verdict(gpu_c=None, tctl_c=None, error=None):
    readings = {key: value for key, value in (("gpu_c", gpu_c), ("tctl_c", tctl_c)) if value is not None}
    return SimpleNamespace(gpu_c=gpu_c, tctl_c=tctl_c, ok=error is None, error=error, readings=readings)


class Counts:
    """Fake execute.metrics: vLLM running/waiting, or an error; records calls."""

    def __init__(self, running=0.0, waiting=0.0):
        self.value, self.error, self.calls, self.under_lock = {"running": running, "waiting": waiting}, None, [], []
        self.pilot = None

    def __call__(self, timeout=1.0, seconds=None):
        self.calls.append((timeout, seconds))
        if self.pilot is not None:
            # Another thread can take pilot.lock: never called under it.
            free = []
            thread = threading.Thread(target=lambda: free.append(self.pilot.lock.acquire(timeout=1)
                                                                  and self.pilot.lock.release() is None))
            thread.start()
            thread.join(2)
            self.under_lock.append(free != [True])
        if self.error is not None:
            raise self.error
        return dict(self.value) if isinstance(self.value, dict) else self.value


class GuardFixture(PilotLiveFixture):
    def guarded(self, runtime=None, thermal=True, metrics=True):
        if getattr(self, "_built", False):
            self.teardown()
        self.verdict = verdict(gpu_c=60, tctl_c=55.0)
        self.counts = Counts()
        self.tick = [100.0]
        options = {"runtime": runtime or RuntimeOptions(), "monotonic": lambda: self.tick[0]}
        if thermal:
            options["thermal"] = lambda: self.verdict
        if metrics:
            options["metrics"] = self.counts
        self.build(live_options=options)
        self.counts.pilot = self.pilot
        self._built = True

    def tearDown(self):
        if getattr(self, "_built", False):
            self.teardown()

    def expired_rows(self, count=3):
        rows = [("old%d" % i, "q", "d", self.now - 30 * 86400, self.now, "terminal", "completed", EPOCH, self.now)
                for i in range(count)]
        self.pilot.db.executemany("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,?,?)", rows)

    def ledger(self):
        return self.pilot.db.execute("SELECT id,state,outcome FROM live_requests ORDER BY id").fetchall()

    def admitted(self):
        proof = self.live.submit(self.envelope(), self.binding)
        self.finish()
        self.assertEqual(proof["state"], "dispatching")


class ThermalLadderTests(GuardFixture, unittest.TestCase):
    def setUp(self):
        self.guarded(metrics=False)

    def test_each_rung_refuses_typed_before_any_ledger_write(self):
        self.expired_rows()
        before = self.ledger()
        for reading in (verdict(gpu_c=77, tctl_c=50.0), verdict(gpu_c=60, tctl_c=88.0), verdict(gpu_c=79),
                        verdict(gpu_c=81, error="ValueError")):   # a failed check's readings still count
            self.verdict = reading
            with self.subTest(reading=reading):
                self.assertEqual(self.refused(self.envelope()), (503, "thermal_shed"))
        self.assertEqual(self.ledger(), before)   # no reservation, and no pruning either
        self.assertEqual(self.model.calls, [])
        self.assertIn((503, "thermal_shed"), live.PRE_EXECUTION)

    def test_below_every_rung_is_admitted(self):
        self.verdict = verdict(gpu_c=76, tctl_c=87.9)
        self.admitted()
        self.assertEqual(len(self.model.calls), 1)

    def test_no_reading_is_no_decision(self):
        # The backend's own fail-closed preflight still runs before any byte.
        for reading in (None, verdict(), SimpleNamespace(gpu_c=float("nan"), tctl_c=True)):
            self.verdict = reading
            self.now += 1
            self.admitted()

        def broken():
            raise RuntimeError("no cache")
        self.live._thermal = broken
        self.admitted()

    def test_rung_changes_are_logged_once_each(self):
        for gpu in (77, 77, 79, 79, 70, 70):
            self.verdict = verdict(gpu_c=gpu, tctl_c=50.0)
            try:
                self.live.submit(self.envelope(), self.binding)
                self.finish()
            except live.LiveSceneRefusal:
                pass
        lines = [line for line in self.logs if line.startswith("live scene thermal_shed")]
        self.assertEqual(lines, ["live scene thermal_shed level=shed gpu_c=77 tctl_c=50.0",
                                 "live scene thermal_shed level=stop gpu_c=79 tctl_c=50.0",
                                 "live scene thermal_shed cleared gpu_c=70 tctl_c=50.0"])

    def test_thresholds_come_from_the_runtime_options(self):
        self.guarded(RuntimeOptions(live_shed_gpu_c=70, live_stop_gpu_c=72, live_shed_tctl_c=60.0), metrics=False)
        for reading, refused in ((verdict(gpu_c=69, tctl_c=59.0), False), (verdict(gpu_c=70), True),
                                 (verdict(gpu_c=40, tctl_c=60.0), True), (verdict(gpu_c=72), True)):
            self.verdict = reading
            with self.subTest(reading=reading):
                if refused:
                    self.assertEqual(self.refused(self.envelope()), (503, "thermal_shed"))
                else:
                    self.admitted()

    def test_camera_chat_is_untouched_by_the_ladder(self):
        self.verdict = verdict(gpu_c=79, tctl_c=95.0)
        self.assertEqual(self.refused(self.envelope()), (503, "thermal_shed"))
        status = self.pilot.submit(self.camera(), binding_fixture())
        self.assertEqual(status["status"], "dispatching")
        self.pilot.worker.join(5)
        self.assertEqual(self.pilot.status("camera-1", binding_fixture())["outcome"], "completed")
        self.assertFalse(self.pilot.closed or self.live.closed)


class YieldTests(GuardFixture, unittest.TestCase):
    def setUp(self):
        self.guarded(thermal=False)

    def test_waiting_or_three_running_yield_before_any_ledger_write(self):
        self.expired_rows()
        before = self.ledger()
        for counts in ({"running": 0.0, "waiting": 1.0}, {"running": 3.0, "waiting": 0.0},
                       {"running": 4.0, "waiting": 2.0}):
            self.counts.value = counts
            self.tick[0] += 2   # past the 1 s cache
            with self.subTest(counts=counts):
                self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.model.calls, [])
        self.counts.value = {"running": 2.0, "waiting": 0.0}
        self.tick[0] += 2
        self.admitted()

    def test_counts_are_refreshed_outside_the_lock_within_the_headroom(self):
        self.admitted()
        timeout, seconds = self.counts.calls[0]
        # deadline now + 7, min_execution 4: 3 s of headroom for the refresh.
        self.assertEqual((timeout, round(seconds, 3)), (1.0, 3.0))
        self.assertEqual(self.counts.under_lock, [False])

    def test_counts_at_most_one_second_old_are_reused(self):
        self.admitted()
        self.tick[0] += .9
        self.now += 1
        self.admitted()
        self.assertEqual(len(self.counts.calls), 1)
        self.tick[0] += .2
        self.now += 1
        self.admitted()
        self.assertEqual(len(self.counts.calls), 2)

    def test_counts_that_cannot_be_refreshed_yield(self):
        for error in (WorkerNotSent("worker busy"), RuntimeError("x")):
            self.counts.error = error
            self.tick[0] += 2
            self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.counts.error, self.counts.value = None, {"running": 1.0}
        self.tick[0] += 2
        self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))   # malformed counts
        self.counts.value = {"running": 0.0, "waiting": 0.0}
        self.tick[0] += 2
        self.admitted()
        self.assertEqual([line for line in self.logs if "yield metrics" in line],
                         ["live scene yield metrics unavailable: WorkerNotSent",
                          "live scene yield metrics unavailable: RuntimeError",
                          "live scene yield metrics unavailable: ValueError",
                          "live scene yield metrics available"])
        self.assertEqual(self.rows(), 1)

    def test_ledger_refusals_come_first_and_never_ask_vllm(self):
        self.model.hold = threading.Event()
        self.live.submit(self.envelope(), self.binding)
        calls = len(self.counts.calls)
        self.tick[0] += 2
        self.assertEqual(self.refused(self.envelope()), (429, "perceiver_busy"))
        self.model.hold.set()
        self.finish()
        self.live.close_admission()
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(len(self.counts.calls), calls)

    def test_the_running_threshold_is_config_and_the_yield_can_be_off(self):
        self.guarded(RuntimeOptions(yield_running_threshold=1), thermal=False)
        self.counts.value = {"running": 1.0, "waiting": 0.0}
        self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.guarded(RuntimeOptions(yield_enabled=False), thermal=False)
        self.counts.value = {"running": 4.0, "waiting": 9.0}
        self.admitted()
        self.assertEqual(self.counts.calls, [])

    def test_heat_is_checked_before_the_queue(self):
        self.guarded()
        self.verdict = verdict(gpu_c=78)
        self.assertEqual(self.refused(self.envelope()), (503, "thermal_shed"))
        self.assertEqual(self.counts.calls, [])

    def test_a_failed_engine_check_is_admission_closed_not_queue_busy(self):
        # The live worker is gone (a vLLM restart killed it) and its fresh,
        # epoch-bound check refuses a start: nothing could be sent, so Victoria
        # gets the backoff refusal, not a load signal.
        refused = WorkerNotSent("engine check before a worker start failed")
        refused.engine_check = True
        refused.__cause__ = ValueError("pinned model incarnation changed or unhealthy")
        self.counts.error = refused
        for _ in range(3):
            self.tick[0] += 2
            self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertIn((503, "admission_closed"), live.PRE_EXECUTION)
        self.assertEqual((self.rows(), self.model.calls, self.live.closed), (0, [], False))
        lines = [line for line in self.logs if "preflight rejected" in line or "yield metrics" in line]
        self.assertEqual(lines, ["live scene preflight rejected: ValueError before admission"])   # rate-limited
        self.tick[0] += pilot_live.HEALTH_LOG_SECONDS
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual(sum("preflight rejected" in line for line in self.logs), 2)   # still visible
        # A plain refresh failure on a healthy engine stays queue_busy.
        self.counts.error = WorkerNotSent("worker busy")
        self.tick[0] += 2
        self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.counts.error = None
        self.tick[0] += 2
        self.admitted()

    def test_a_failed_health_verdict_is_admission_closed_without_asking_vllm(self):
        self.guarded()
        self.verdict = verdict(gpu_c=60, tctl_c=55.0, error="HealthVerdictFailed")
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual((self.counts.calls, self.rows()), ([], 0))
        self.assertIn("live scene preflight rejected: HealthVerdictFailed before admission", self.logs)
        self.verdict = verdict(gpu_c=60, tctl_c=55.0)
        self.admitted()

    def test_a_dead_worker_and_a_failing_fresh_check_write_nothing(self):
        from home_shared.model_worker import ModelWorker

        def failing():
            raise ValueError("pinned model incarnation changed or unhealthy")
        worker = ModelWorker("live", spawn=lambda *a, **k: self.fail("must not start"), log=self.logs.append)
        self.addCleanup(worker.close)
        backend = pilot_live.docker_backend(CONTAINER, lambda: None, worker=worker, fresh_health=failing)
        self.live._metrics = backend.metrics
        self.assertEqual(self.refused(self.envelope()), (503, "admission_closed"))
        self.assertEqual((self.rows(), worker.starts), (0, 0))
        self.assertIn("live scene preflight rejected: ValueError before admission", self.logs)

    def test_a_slow_refresh_that_eats_the_headroom_is_deadline_insufficient(self):
        def slow(timeout=1.0, seconds=None):
            self.now += 4   # the refresh took the whole headroom
            return {"running": 0.0, "waiting": 0.0}
        self.live._metrics = slow
        self.assertEqual(self.refused(self.envelope()), (409, "deadline_insufficient"))
        self.assertEqual(self.rows(), 0)


class LiveColdStartTests(GuardFixture, unittest.TestCase):
    """The live worker's start no longer has to fit inside one tick's metrics
    budget: a start the yield cannot wait out is kept and a later tick adopts
    it, instead of every tick killing a half-started worker."""

    def test_a_start_longer_than_the_yield_budget_is_adopted_by_a_later_tick(self):
        from home_shared.model_worker import ModelWorker
        from test_model_worker import scripted
        script = scripted("import json\nBODY=%r\ntime.sleep(1.0)\nready()\nwhile 1:\n value=json.loads(take())\n"
                          " reply={'ok':1,'running':0.0,'waiting':0.0} if value['op']=='metrics' else {'ok':1,'body':BODY}\n"
                          " data=json.dumps(reply).encode();out.write(struct.pack('>I',len(data))+data);out.flush()\n"
                          % completion().decode())
        spawned, logs = [], []

        def command(container):
            spawned.append(container)
            return script(container)
        worker = ModelWorker("live", command=command, log=logs.append)
        self.addCleanup(worker.close)
        backend = pilot_live.docker_backend(CONTAINER, lambda: None, clock=lambda: self.now, worker=worker)
        self.tick = [100.0]
        # 0.5 s of headroom for vLLM's counts (deadline 7 s, min_execution 6.5 s): less than the start.
        self.build(live_options={"backend": backend, "metrics": backend.metrics, "monotonic": lambda: self.tick[0]},
                   min_execution_seconds=6.5)
        self._built = True
        self.assertEqual(self.refused(self.envelope()), (429, "queue_busy"))
        self.assertEqual((self.rows(), worker.starts, len(spawned)), (0, 0, 1))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (worker._running is not None and worker._running.ready.is_set()):
            time.sleep(.02)
        self.tick[0] += 2
        self.now += 1
        self.admitted()
        self.assertEqual(self.pilot.db.execute("SELECT outcome FROM live_requests").fetchone()[0], "completed")
        self.assertEqual((worker.starts, len(spawned)), (1, 1))   # one start, adopted; nothing killed
        self.assertFalse(any("discarded" in line for line in logs))


class RetentionTests(GuardFixture, unittest.TestCase):
    def setUp(self):
        self.guarded(thermal=False, metrics=False)

    def test_the_s1_4_sizing_is_accepted(self):
        config = self.load(config_value(max_records=200000, retention_days=2))
        self.assertEqual((config.max_records, config.retention_days), (200000, 2))
        self.assertIsNone(pilot_live.read_live_config(Path(self.temp.name) / "absent.json"))
        path = Path(self.temp.name) / "disabled.json"
        path.write_text(json.dumps(config_value(enabled=False, retention_days=2)))
        self.assertIsNone(pilot_live.load_live_config(path))
        self.assertEqual(pilot_live.read_live_config(path).retention_days, 2)
        for changes in ({"max_records": 999}, {"max_records": 1_000_001}, {"retention_days": 0},
                        {"retention_days": 31}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(config_value(**changes))

    def test_one_admission_deletes_one_bounded_batch_oldest_first(self):
        old = self.now - 8 * 86400
        rows = [("t%04d" % i, "q", "d", old + i, old, "terminal", "completed", EPOCH, old) for i in range(1200)]
        self.pilot.db.executemany("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,?,?)", rows)
        self.pilot.db.execute("INSERT INTO pilot_requests VALUES('camera-old','d',?,?,'terminal','completed')",
                              (old - 40 * 86400, old))
        statements = []
        self.pilot.db.set_trace_callback(lambda sql: statements.append(sql) if sql.startswith("DELETE") else None)
        self.admitted()
        self.pilot.db.set_trace_callback(None)
        self.assertEqual(len(statements), 1)   # one bounded statement under pilot.lock
        self.assertIn("LIMIT", statements[0])
        remaining = {row[0] for row in self.pilot.db.execute("SELECT id FROM live_requests")}
        batch = pilot_live.PRUNE_BATCH_ROWS
        self.assertTrue({"t0000", "t%04d" % (batch - 1)}.isdisjoint(remaining))   # oldest first
        self.assertIn("t%04d" % batch, remaining)
        self.assertEqual(len(remaining), 1200 - batch + 1)
        # Camera rows keep their own 30-day rule (camera_pilot.py), untouched here.
        self.assertEqual(self.pilot.db.execute("SELECT COUNT(*) FROM pilot_requests").fetchone()[0], 1)

    def test_prune_never_touches_unresolved_or_recent_rows(self):
        old = self.now - 8 * 86400
        self.pilot.db.executemany("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,?,?)", [
            ("a", "q", "d", old, old, "terminal", "completed", EPOCH, old),
            ("b", "q", "d", old, old, "indeterminate", "upstream_outcome_unknown", EPOCH, old),
            ("c", "q", "d", self.now, self.now, "terminal", "completed", EPOCH, self.now)])
        self.assertEqual(pilot_live.prune_live_rows(self.pilot.db, self.now - 86400, 10), 1)
        self.assertEqual({row[0] for row in self.pilot.db.execute("SELECT id FROM live_requests")}, {"b", "c"})


class PruneCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name)
        self.config = self.private / "live-scene.json"
        self.config.write_text(json.dumps(config_value(retention_days=2, max_records=200000)))
        self.now = time.time()
        pilot = CameraPilot(self.private / "admission.sqlite", binding=binding_fixture(), backend=lambda _: "x",
                            model_revision="pinned", image_validator=lambda _: None, free_space_floor=0)
        live_scene = pilot_live.PilotLiveScene(pilot, pilot_live.load_live_config(self.config), backend=lambda *a: b"")
        old, recent = self.now - 3 * 86400, self.now - 3600
        rows = [("o%04d" % i, "q", "d", old, old, "terminal", "completed", EPOCH, old) for i in range(250)]
        rows += [("r%04d" % i, "q", "d", recent, recent, "terminal", "completed", EPOCH, recent) for i in range(5)]
        rows.append(("stuck", "q", "d", old, old, "indeterminate", "upstream_outcome_unknown", EPOCH, old))
        pilot.db.executemany("INSERT INTO live_requests VALUES(?,?,?,?,?,?,?,?,?)", rows)
        pilot.db.execute("INSERT INTO pilot_requests VALUES('camera-old','d',?,?,'terminal','completed')", (old, old))
        self.pilot, self.live = pilot, live_scene
        self.addCleanup(self.stop_pilot)   # idempotent; before the directory cleanup

    def stop_pilot(self):
        self.live.close()
        self.pilot.close()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lab_live_prune.main(["--config", str(self.config), *args], enforce_linux=False, clock=lambda: self.now)
        return code, out.getvalue(), err.getvalue()

    def ids(self):
        import sqlite3
        connection = sqlite3.connect(self.private / "admission.sqlite")
        try:
            return ({row[0] for row in connection.execute("SELECT id FROM live_requests")},
                    connection.execute("SELECT COUNT(*) FROM pilot_requests").fetchone()[0])
        finally:
            connection.close()

    def test_refused_while_the_pilot_holds_the_ledger(self):
        code, out, err = self.run_main()
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "live ledger prune refused: the camera pilot is running (ledger locked)\n")
        self.stop_pilot()
        self.assertEqual(len(self.ids()[0]), 256)

    def test_dry_run_counts_and_deletes_nothing(self):
        self.stop_pilot()
        code, out, _ = self.run_main("--dry-run")
        self.assertEqual((code, out), (0, "live ledger prune (dry run): expired=250 deleted=0 batches=0 "
                                          "remaining=256 max_records=200000\n"))
        self.assertEqual(len(self.ids()[0]), 256)

    def test_deletes_expired_terminal_rows_in_bounded_batches_only(self):
        self.stop_pilot()
        with patch.object(lab_live_prune, "BATCH_ROWS", 100):
            result = lab_live_prune.prune(self.config, clock=lambda: self.now, batch_rows=lab_live_prune.BATCH_ROWS)
        self.assertEqual(result, {"expired": 250, "deleted": 250, "remaining": 6, "batches": 3, "max_records": 200000})
        ids, cameras = self.ids()
        self.assertEqual(ids, {"stuck"} | {"r%04d" % i for i in range(5)})
        self.assertEqual(cameras, 1)
        code, out, _ = self.run_main()
        self.assertEqual((code, out), (0, "live ledger prune: expired=0 deleted=0 batches=0 remaining=6 "
                                          "max_records=200000\n"))

    def test_a_disabled_config_still_prunes_and_bad_inputs_refuse(self):
        self.stop_pilot()
        self.config.write_text(json.dumps(config_value(enabled=False, retention_days=2)))
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(len(self.ids()[0]), 6)
        self.config.write_text('{"version": 1}')
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("live ledger prune refused: ValueError"))
        self.config.write_text(json.dumps(config_value()))
        (self.private / "admission.sqlite").rename(self.private / "moved.sqlite")
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("pilot ledger not found", err)
        self.assertFalse((self.private / "admission.sqlite").exists())   # never creates one
        for argv in (["--dry-run"], ["--config"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                lab_live_prune.main(argv)

    def test_a_ledger_without_live_rows_is_nothing_to_do(self):
        self.stop_pilot()
        import sqlite3
        connection = sqlite3.connect(self.private / "admission.sqlite")
        connection.execute("DROP TABLE live_requests")
        connection.commit()
        connection.close()
        self.assertEqual(self.run_main("--dry-run")[1], "live ledger prune (dry run): expired=0 deleted=0 batches=0 "
                                                        "remaining=0 max_records=200000\n")


class HealthVerdictTests(unittest.TestCase):
    """camera_pilot_service.health_verdict: the thermal ladder's reading."""

    def test_cached_refreshed_and_failed_verdicts_keep_their_readings(self):
        tick, calls = [0.0], []

        def check(readings):
            calls.append(tick[0])
            readings["gpu_c"], readings["tctl_c"] = 81 if len(calls) > 1 else 70, 60.5
            if readings["gpu_c"] > 80:
                raise ValueError("pilot resource guard closed")
        cache = HealthCache(check, max_age_seconds=2, clock=lambda: tick[0])
        with patch.object(service, "HEALTH", None):
            self.assertIsNone(service.health_verdict())
        with patch.object(service, "HEALTH", cache):
            self.assertEqual((service.health_verdict().gpu_c, len(calls)), (70, 1))
            tick[0] = 1.5
            self.assertEqual((service.health_verdict().gpu_c, len(calls)), (70, 1))   # cached
            tick[0] = 2.5
            failed = service.health_verdict()
            self.assertEqual((failed.ok, failed.gpu_c, failed.tctl_c, len(calls)), (False, 81, 60.5, 2))
            self.assertEqual(service.health_verdict().gpu_c, 81)   # the failure is cached too
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
