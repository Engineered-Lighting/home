"""S1.1 health on demand: the cached verdict, the kernel-scan interval and the
journal mode, with fake host commands and clocks. The kernel-fault scan's
persisted window, retry interval and owner receipt use the same fakes; only
JournalSessionTests start real processes (Linux, and sudo -n where it works)."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from home_shared import camera_pilot_service as service
from home_shared import model_pin
from home_shared.camera_pilot import BeforeDispatchRejected
from home_shared.pilot_health import HealthCache, HealthVerdictFailed
from home_shared.pilot_runtime import RuntimeOptions
from home_shared.service_config import protected_file
from test_model_pin import inspect, write_pin


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class HealthCacheTests(unittest.TestCase):
    def test_a_fresh_verdict_is_reused_and_an_older_one_refreshed_once(self):
        clock, calls = Clock(), []
        cache = HealthCache(lambda readings: calls.append(clock.now), max_age_seconds=2, clock=clock)
        cache.require()
        clock.now += 1.9
        cache.require()
        self.assertEqual(calls, [100.0])
        clock.now += .2
        cache.require()
        cache.require()
        self.assertEqual(calls, [100.0, 100.0 + 1.9 + .2])
        self.assertEqual(cache.refreshes, 2)

    def test_concurrent_requests_share_one_refresh(self):
        entered, release, calls = threading.Event(), threading.Event(), []

        def check(readings):
            calls.append(1)
            entered.set()
            release.wait(5)
            readings["gpu_c"] = 50
        cache = HealthCache(check, max_age_seconds=2)
        results, threads = [], [threading.Thread(target=lambda: results.append(cache.require().gpu_c))
                                for _ in range(6)]
        threads[0].start()
        self.assertTrue(entered.wait(5))
        for thread in threads[1:]:
            thread.start()
        time.sleep(.1)   # the others now wait on the refresh in progress
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual((len(calls), results), (1, [50] * 6))

    def test_a_refresh_begun_too_long_before_a_request_is_not_shared(self):
        clock, entered, release, calls = Clock(0.0), threading.Event(), threading.Event(), []

        def check(readings):
            calls.append(clock.now)
            if len(calls) == 1:
                entered.set()
                release.wait(5)
        cache = HealthCache(check, max_age_seconds=2, clock=clock)
        first = threading.Thread(target=cache.require)
        first.start()
        self.assertTrue(entered.wait(5))
        clock.now = 2.5          # this request arrives 2.5 s after that refresh began
        second = threading.Thread(target=cache.require)
        second.start()
        time.sleep(.1)
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(calls, [0.0, 2.5])

    def test_a_failed_verdict_raises_is_cached_and_keeps_its_readings(self):
        clock, calls = Clock(), []

        def check(readings):
            calls.append(1)
            readings["gpu_c"] = 81
            raise ValueError("pilot resource guard closed")
        cache = HealthCache(check, max_age_seconds=2, clock=clock)
        with self.assertRaisesRegex(ValueError, "pilot resource guard closed"):
            cache.require()   # the refreshing request sees the check's own error
        with self.assertRaises(HealthVerdictFailed):
            cache.require()   # a cached failure refuses too (fail-closed)
        self.assertEqual(len(calls), 1)
        self.assertEqual((cache.latest().ok, cache.latest().gpu_c, cache.latest().error), (False, 81, "ValueError"))
        clock.now += 2.5
        with self.assertRaises(ValueError):
            cache.require()
        self.assertEqual(len(calls), 2)

    def test_zero_age_checks_on_every_request_and_bounds_are_enforced(self):
        calls = []
        cache = HealthCache(lambda readings: calls.append(1), max_age_seconds=0, clock=Clock())
        for _ in range(3):
            cache.require()
        self.assertEqual(len(calls), 3)
        for age in (-1, 3.5, float("nan"), True, "2"):
            with self.subTest(age=age), self.assertRaises(ValueError):
                HealthCache(lambda readings: None, max_age_seconds=age)


FLOOR = int(service._utc(service._KERNEL_BASELINE))
CLEAN = (1, "-- No entries --\n", "")


class FakeTime:
    """The service's clock: wall time for the journal window, monotonic for bounds and retries."""

    def __init__(self, now):
        self.now, self.mono = now, 5000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.mono


class HostFixture:
    """The host commands _host_health runs, faked by argv, and the files it reads.

    journal is a (returncode, stdout, stderr) result for every journalctl run,
    or a function of its argv returning one (or raising). The kernel-fault
    state directory is fresh on every host() call, and the clock starts an hour
    past the floor.
    """

    def host(self, gpu="20000, 50", journal=CLEAN, options=RuntimeOptions()):
        self.commands, self.journal_timeouts = [], []
        self.gpu, self.journal = gpu, journal
        self.state = Path(tempfile.mkdtemp(dir=self.temp.name))
        self.clock = FakeTime(FLOOR + 3600.0)
        hwmon = Path(self.temp.name) / "hwmon"
        for name, files in (("hwmon0", {"name": "nvme", "temp1_label": "Composite", "temp1_input": "41850"}),
                            ("hwmon1", {"name": "k10temp", "temp1_label": "Tctl", "temp1_input": "73125",
                                        "temp3_label": "Tccd1", "temp3_input": "73875"})):
            (hwmon / name).mkdir(parents=True, exist_ok=True)
            for key, text in files.items():
                (hwmon / name / key).write_text(text + "\n")
        meminfo = SimpleNamespace(read_text=lambda: "MemTotal: 131072000 kB\nMemAvailable: 64000000 kB")
        real_path = Path
        stack = contextlib.ExitStack()
        for target in (patch.object(service.subprocess, "run", self.host_command),
                       patch.object(service, "_journal", self.journal_command),
                       patch.object(service, "Path", lambda value: meminfo if value == "/proc/meminfo" else real_path(value)),
                       patch.object(service.shutil, "disk_usage", return_value=SimpleNamespace(free=200 * 1024**3)),
                       patch.object(service, "HWMON", hwmon), patch.object(service, "OPTIONS", options),
                       patch.object(service, "time", self.clock), patch.object(service, "KERNEL_STATE", self.state),
                       # Ownership is checked on the host; here only the file shape is.
                       patch.object(service, "protected_file", lambda path, **_: protected_file(path)),
                       patch.object(service, "_NON_DEPENDENCY_FAILED", ()),
                       patch.dict(service._KERNEL_CLEAN, {"until": None, "retry_at": None, "session": None})):
            stack.enter_context(target)
        return stack

    def host_command(self, args, **kwargs):
        self.commands.append(list(args))
        if args[0] == "systemctl":
            return SimpleNamespace(stdout="")
        if args[0] == "nvidia-smi":
            return SimpleNamespace(stdout=self.gpu + "\n")
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([inspect()]))
        raise AssertionError("unexpected host command %r" % (args[:3],))

    def journal_command(self, command, timeout):
        self.commands.append(list(command))
        self.journal_timeouts.append(timeout)
        return self.journal(command) if callable(self.journal) else self.journal

    def journals(self):
        return [command for command in self.commands if "journalctl" in command]

    @staticmethod
    def window(command):
        """(since, until) epochs of one journalctl argv; until is None when open-ended."""
        since = int(command[command.index("--since") + 1].lstrip("@"))
        until = command[command.index("--until") + 1] if "--until" in command else None
        return since, None if until is None else int(until.lstrip("@"))

    def checkpoint(self):
        path = self.state / service.KERNEL_CHECKPOINT
        return json.loads(path.read_text()) if path.exists() else None

    def write(self, name, value):
        (self.state / name).write_text(value if isinstance(value, str) else json.dumps(value))

    def restart(self):
        """A new pilot process: nothing in memory, the files kept."""
        service._KERNEL_CLEAN.update(until=None, retry_at=None, session=None)


class HostHealthTests(HostFixture, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_unrelated_haos_failures_do_not_close_den_inference(self):
        failures = ('ha-health.service', 'ha-reachable.service', 'haos-precious-sync.service',
                    'home-agent-bff-egress-verify.service', 'home-agent-lighting-egress-verify.service',
                    'fwupd-refresh.service')
        for unit in failures:
            with self.subTest(unit=unit), self.host():
                original = self.host_command
                def command(args, **kwargs):
                    if args[0] == 'systemctl':
                        return SimpleNamespace(stdout=unit+' loaded failed failed unrelated dependency\n')
                    return original(args, **kwargs)
                with patch.object(service.subprocess, 'run', command):
                    service._host_health()
                self.assertTrue(self.journals())

    def test_unrelated_failures_never_skip_the_safety_checks(self):
        with self.host(gpu='20000, 81'):
            original = self.host_command
            def command(args, **kwargs):
                if args[0] == 'systemctl':
                    return SimpleNamespace(stdout='ha-health.service loaded failed failed HA offline\n')
                return original(args, **kwargs)
            with patch.object(service.subprocess, 'run', command):
                with self.assertRaisesRegex(ValueError, 'resource guard closed'):
                    service._host_health()
        with self.host(journal=(0, 'kernel: soft lockup\n', '')):
            original = self.host_command
            def command(args, **kwargs):
                if args[0] == 'systemctl':
                    return SimpleNamespace(stdout='ha-health.service loaded failed failed HA offline\n')
                return original(args, **kwargs)
            with patch.object(service.subprocess, 'run', command):
                with self.assertRaisesRegex(ValueError, 'kernel fault'):
                    service._host_health()

    def test_ops_and_inference_dependency_classification_match(self):
        import ast
        path = Path(__file__).resolve().parents[3] / 'stack/ops/den-activity-lighting/monitor/den_ops/monitor.py'
        tree = ast.parse(path.read_text())
        assigned = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == 'DEN_NON_DEPENDENCY_UNITS' for t in n.targets))
        self.assertEqual(service.DEN_NON_DEPENDENCY_UNITS, ast.literal_eval(assigned))

    def test_unknown_or_den_failures_still_close_inference(self):
        for unit in ('lighting-brain.service', 'victoria-camera-pilot.service', 'ssh.service',
                     'ha-health-impostor.service'):
            with self.subTest(unit=unit), self.host():
                with patch.object(service.subprocess, 'run', return_value=SimpleNamespace(
                        stdout=unit+' loaded failed failed failure\n')):
                    with self.assertRaisesRegex(ValueError, 'failed system unit'):
                        service._host_health()

    def test_readings_include_gpu_temperature_and_tctl(self):
        readings = {}
        with self.host():
            service._host_health(readings)
        self.assertEqual(readings, {"gpu_c": 50, "tctl_c": 73.125})

    def test_tctl_is_a_reading_never_a_check(self):
        with self.host():
            for broken in ("missing", "garbage"):
                with self.subTest(broken=broken):
                    label = service.HWMON / "hwmon1" / "temp1_input"
                    if broken == "missing":
                        label.unlink()
                    else:
                        label.write_text("hot\n")
                    readings = {}
                    service._host_health(readings)
                    self.assertEqual(readings, {"gpu_c": 50})
        with patch.object(service, "HWMON", Path(self.temp.name) / "absent"):
            self.assertIsNone(service._tctl())

    def test_the_cached_preflight_refuses_a_reading_above_80_before_dispatch(self):
        clock = Clock()
        engine = model_pin.load_engine(write_pin(Path(self.temp.name) / model_pin.PIN_NAME), log=lambda line: None)
        cache = HealthCache(service._check_health, max_age_seconds=2, clock=clock)
        request = SimpleNamespace(request_deadline=time.time() + 15, max_output_tokens=128, image=b"fixture")
        with self.host(gpu="20000, 81"), patch.object(service, "ENGINE", engine), patch.object(service, "HEALTH", cache), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            for _ in range(2):
                with self.assertRaises(BeforeDispatchRejected):
                    service.infer(request)
        self.assertEqual([command[0] for command in self.commands], ["docker", "systemctl", "nvidia-smi"])
        self.assertNotIn("exec", [part for command in self.commands for part in command])
        self.assertEqual(cache.latest().gpu_c, 81)
        self.assertEqual(stderr.getvalue(), "camera pilot preflight rejected: ValueError\n"
                                            "camera pilot preflight rejected: HealthVerdictFailed\n")

    def test_main_s_cache_serves_requests_within_two_seconds_from_one_check(self):
        clock = Clock()
        engine = model_pin.load_engine(write_pin(Path(self.temp.name) / model_pin.PIN_NAME), log=lambda line: None)
        cache = HealthCache(service._check_health, max_age_seconds=2, clock=clock)
        with self.host(), patch.object(service, "ENGINE", engine), patch.object(service, "HEALTH", cache):
            service.model_health()
            clock.now += 1.5
            service.model_health()
            inspects = [command for command in self.commands if command[:2] == ["docker", "inspect"]]
            self.assertEqual(len(inspects), 1)
            self.assertEqual((cache.latest().gpu_c, cache.latest().tctl_c), (50, 73.125))
            clock.now += 1
            service.model_health()
            self.assertEqual(len([command for command in self.commands if command[:2] == ["docker", "inspect"]]), 2)
        # A model worker start never takes the cached verdict.
        with self.host(), patch.object(service, "ENGINE", engine), patch.object(service, "HEALTH", cache):
            service.model_health()
            service.fresh_health()
            self.assertEqual(len([command for command in self.commands if command[:2] == ["docker", "inspect"]]), 1)
            service.fresh_health()
            self.assertEqual(len([command for command in self.commands if command[:2] == ["docker", "inspect"]]), 2)
        # Without main()'s cache every call checks afresh, as before.
        with self.host(), patch.object(service, "ENGINE", engine), patch.object(service, "HEALTH", None):
            service.model_health()
            service.model_health()
            self.assertEqual(len([command for command in self.commands if command[:2] == ["docker", "inspect"]]), 2)


HOUR = 3600
FAULT = (0, "Nov 11 23:00:00 kernel: soft lockup - CPU#3 stuck for 22s!\n", "")


def utc(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))


class KernelScanTests(HostFixture, unittest.TestCase):
    """_kernel_scan: the 30 s interval, the persisted window, retries, the owner receipt."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stderr = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def faults_at(self, *moments):
        """A journal with fault lines at these epochs: a scan whose window holds one fails."""
        def journal(command):
            since, until = self.window(command)
            return FAULT if any(since <= moment and (until is None or moment <= until) for moment in moments) else CLEAN
        return journal

    def test_the_scan_runs_at_most_every_30_seconds_and_resumes_with_a_5_second_overlap(self):
        with self.host():
            start = self.clock.now
            for offset in (0, 10, 29.9):
                self.clock.now = start + offset
                service._host_health()
            self.assertEqual([self.window(command) for command in self.journals()], [(FLOOR, None)])
            self.clock.now = start + 30
            service._host_health()
            self.assertEqual(self.window(self.journals()[1]), (int(start) - 5, None))

    def test_only_the_first_scan_in_a_process_gets_the_long_bound(self):
        # 2026-10-09: the full window took 36.5 s against a flat 15 s bound.
        with self.host():
            service._host_health()
            self.clock.now += 30
            service._host_health()
        self.assertEqual(self.journal_timeouts, [180, 15])
        self.assertEqual((service._KERNEL_BASELINE_TIMEOUT, service._KERNEL_SCAN_TIMEOUT), (180, 15))
        self.assertEqual(self.stderr.getvalue(), "camera pilot kernel scan since=@%d chunks=1 seconds=0.0\n" % FLOOR)

    def test_the_checkpoint_is_written_only_after_a_clean_scan(self):
        with self.host():
            service._kernel_scan()
            self.assertEqual(self.checkpoint(), {"version": 1, "clean_until": FLOOR + 3600.0})
        earlier = {"version": 1, "clean_until": FLOOR + 1800.0}

        def timeout(command):
            raise subprocess.TimeoutExpired(command, 180)
        for journal in (FAULT, (1, "", "sudo: a password is required\n"),
                        (1, "", 'sudo: The "no new privileges" flag is set, which prevents sudo from running as root.\n'),
                        (2, "", "Failed to open journal\n"), (1, "garbage\n", ""), timeout):
            with self.subTest(journal=journal), self.host(journal=journal):
                self.write(service.KERNEL_CHECKPOINT, earlier)
                with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    service._kernel_scan()
                self.assertEqual(self.checkpoint(), earlier)
                self.assertIsNone(service._KERNEL_CLEAN["until"])

    def test_a_restart_resumes_from_the_checkpoint(self):
        with self.host():
            service._kernel_scan()
            first = self.clock.now
            self.restart()
            self.clock.now += 3 * HOUR
            self.clock.mono += 3 * HOUR
            service._kernel_scan()
            self.assertEqual([self.window(command) for command in self.journals()],
                             [(FLOOR, None), (int(first) - 5, None)])
            self.assertEqual(self.journal_timeouts, [180, 180])   # each the first in its process
            self.assertEqual(self.checkpoint(), {"version": 1, "clean_until": first + 3 * HOUR})

    def test_an_unusable_checkpoint_scans_from_the_floor_never_later(self):
        for text in ("{", '{"version": 1}', '{"version": 2, "clean_until": %d}' % FLOOR,
                     '{"version": 1, "clean_until": "%d"}' % FLOOR, '{"version": 1, "clean_until": NaN}',
                     '{"version": 1, "clean_until": %d, "note": "x"}' % FLOOR,
                     '{"version": 1, "clean_until": %d}' % (FLOOR + 2 * HOUR)):   # later than now
            with self.subTest(text=text), self.host():
                self.write(service.KERNEL_CHECKPOINT, text)
                service._kernel_scan()
                self.assertEqual(self.window(self.journals()[0]), (FLOOR, None))
                self.assertEqual(self.checkpoint(), {"version": 1, "clean_until": FLOOR + 3600.0})
        self.assertIn("camera pilot kernel checkpoint unusable: ", self.stderr.getvalue())

    def test_a_long_idle_is_read_in_6_hour_chunks_each_recorded_once_clean(self):
        # 2026-11-11: an idle process's window reached back 17 days and never
        # fit its 15 s bound again; every request relaunched it.
        with self.host():
            self.write(service.KERNEL_CHECKPOINT, {"version": 1, "clean_until": FLOOR + 100})
            self.clock.now = FLOOR + 100 + 20 * HOUR
            recorded = []

            def journal(command):
                recorded.append(self.checkpoint()["clean_until"])
                return CLEAN
            self.journal = journal
            service._kernel_scan()
        start = FLOOR + 95
        ends = [start + 6 * HOUR, start + 12 * HOUR - 5, start + 18 * HOUR - 10]
        self.assertEqual([self.window(command) for command in self.journals()],
                         [(start, ends[0]), (ends[0] - 5, ends[1]), (ends[1] - 5, ends[2]), (ends[2] - 5, None)])
        self.assertEqual(recorded, [FLOOR + 100, *ends])   # each chunk is recorded before the next starts
        self.assertEqual(self.checkpoint()["clean_until"], FLOOR + 100 + 20 * HOUR)
        self.assertIn("chunks=4", self.stderr.getvalue())

    def test_a_scan_out_of_budget_keeps_its_clean_chunks_for_the_next(self):
        with self.host():
            self.write(service.KERNEL_CHECKPOINT, {"version": 1, "clean_until": FLOOR + 100})
            self.clock.now = FLOOR + 100 + 20 * HOUR
            service._KERNEL_CLEAN["until"] = FLOOR   # not the first scan in this process: 15 s

            def journal(command):
                self.clock.mono += 6                 # each chunk takes 6 s
                return CLEAN
            self.journal = journal
            with self.assertRaisesRegex(ValueError, "incomplete"):
                service._kernel_scan()
            third = FLOOR + 95 + 18 * HOUR - 10
            self.assertEqual(self.journal_timeouts, [15, 9, 3])
            self.assertEqual(self.checkpoint()["clean_until"], third)
            self.clock.mono += service._KERNEL_RETRY_SECONDS
            service._kernel_scan()
            self.assertEqual(self.window(self.journals()[3]), (third - 5, None))
            self.assertEqual(self.checkpoint()["clean_until"], self.clock.now)

    def test_a_fault_mid_window_keeps_the_clean_chunks_and_admission_closed(self):
        with self.host():
            self.write(service.KERNEL_CHECKPOINT, {"version": 1, "clean_until": FLOOR + 100})
            self.clock.now = FLOOR + 100 + 20 * HOUR
            first_end = FLOOR + 95 + 6 * HOUR
            self.journal = self.faults_at(first_end + HOUR)
            with self.assertRaisesRegex(ValueError, "kernel fault"):
                service._kernel_scan()
            self.assertEqual(self.checkpoint()["clean_until"], first_end)
            self.restart()   # neither a restart nor a retry gets past it
            with self.assertRaisesRegex(ValueError, "kernel fault"):
                service._kernel_scan()
            self.clock.mono += service._KERNEL_RETRY_SECONDS
            self.clock.now += service._KERNEL_RETRY_SECONDS
            with self.assertRaisesRegex(ValueError, "kernel fault"):
                service._kernel_scan()
            self.assertEqual([self.window(command)[0] for command in self.journals()[2:]], [first_end - 5] * 2)
            self.assertEqual(self.checkpoint()["clean_until"], first_end)
            self.assertIsNone(service._KERNEL_CLEAN["until"])

    def test_a_fault_is_never_cached_as_clean_and_is_rescanned_after_30_seconds(self):
        with self.host(journal=FAULT):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    service._host_health()
            self.assertEqual(len(self.journals()), 1)   # the second check came within the retry interval
            self.clock.mono += service._KERNEL_RETRY_SECONDS
            with self.assertRaises(ValueError):
                service._host_health()
            self.assertEqual(len(self.journals()), 2)
            self.assertIsNone(self.checkpoint())

    def test_a_timed_out_scan_is_not_relaunched_for_30_seconds(self):
        def slow(command):
            raise subprocess.TimeoutExpired(command, 180)
        with self.host(journal=slow):
            with self.assertRaises(subprocess.TimeoutExpired):
                service._kernel_scan()
            for _ in range(14):   # a health refresh every 2 s
                self.clock.mono += 2
                self.clock.now += 2
                with self.assertRaisesRegex(ValueError, "retry pending"):
                    service._kernel_scan()
            self.assertEqual(len(self.journals()), 1)
            self.clock.mono += 2
            with self.assertRaises(subprocess.TimeoutExpired):
                service._kernel_scan()
            self.assertEqual(len(self.journals()), 2)
            self.assertIsNone(self.checkpoint())

    def test_no_scan_starts_while_a_timed_out_one_is_still_running(self):
        alive = [True]
        with self.host(), patch.object(service, "_session_alive", lambda session: alive[0]):
            service._KERNEL_CLEAN["session"] = 4242
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "still running"):
                    service._kernel_scan()
                self.clock.mono += service._KERNEL_RETRY_SECONDS
            self.assertEqual(self.journals(), [])
            alive[0] = False
            service._kernel_scan()
            self.assertEqual(len(self.journals()), 1)
            self.assertIsNone(service._KERNEL_CLEAN["session"])

    def test_only_an_owner_receipt_moves_the_window_past_an_inspected_fault(self):
        fault = FLOOR + 1800
        with self.host():
            self.journal = self.faults_at(fault)
            with self.assertRaises(ValueError):
                service._kernel_scan()
            self.restart()
            with self.assertRaises(ValueError):
                service._kernel_scan()
            self.assertIsNone(self.checkpoint())
            self.write(service.KERNEL_RECEIPT, {"version": 1, "inspected_through": utc(fault),
                                                "note": "memcg OOM confined to one service cgroup"})
            self.clock.mono += service._KERNEL_RETRY_SECONDS   # read by the next scan: no release, no restart
            service._kernel_scan()
            self.assertEqual(self.window(self.journals()[-1]), (fault + 1, None))
            self.assertEqual(self.checkpoint()["clean_until"], self.clock.now)
            # A fault after the receipt still refuses.
            later = self.clock.now + 10
            self.journal = self.faults_at(fault, later)
            self.clock.now += 60
            with self.assertRaisesRegex(ValueError, "kernel fault"):
                service._kernel_scan()
            self.assertEqual(self.checkpoint()["clean_until"], later - 10)

    def test_an_invalid_or_future_receipt_refuses_without_scanning(self):
        through = utc(FLOOR + 600)
        for value in ("{", {"version": 1, "inspected_through": through},
                      {"version": 1, "inspected_through": through.replace(" UTC", "Z"), "note": "x"},
                      {"version": 1, "inspected_through": through, "note": " "},
                      {"version": 2, "inspected_through": through, "note": "x"},
                      {"version": 1, "inspected_through": through, "note": "x", "by": "y"},
                      {"version": 1, "inspected_through": utc(FLOOR + 3600 + 60), "note": "later than now"}):
            with self.subTest(value=value), self.host():
                self.write(service.KERNEL_RECEIPT, value)
                with self.assertRaisesRegex(ValueError, "receipt invalid"):
                    service._kernel_scan()
                self.assertEqual(self.journals(), [])

    def test_zero_interval_scans_on_every_check(self):
        with self.host(options=RuntimeOptions(kernel_scan_seconds=0)):
            for _ in range(3):
                service._host_health()
        self.assertEqual(len(self.journals()), 3)

    def test_sudo_is_the_default_and_adm_reads_the_journal_without_it(self):
        with self.host():
            service._host_health()
        self.assertEqual(self.journals()[0][:4], ["sudo", "-n", "journalctl", "-k"])
        with self.host(options=RuntimeOptions(kernel_journal="adm")):
            service._host_health()
        self.assertEqual(self.journals()[0][:2], ["journalctl", "-k"])
        self.assertNotIn("sudo", self.journals()[0])
        # Without the group grant journalctl explains on stderr: no proof of a clean journal.
        hint = "Hint: You are currently not seeing messages from other users and the system.\n"
        with self.host(journal=(1, "-- No entries --\n", hint), options=RuntimeOptions(kernel_journal="adm")):
            with self.assertRaises(ValueError):
                service._host_health()
        # sudo still runs journalctl after its /etc/hosts warning; any other sudo line refuses.
        with self.host(journal=(1, "", "sudo: unable to resolve host EngineeredLightingServer1: Name or service not known\n")):
            service._host_health()
        with self.host(journal=(1, "", "sudo: a terminal is required to read the password\n")):
            with self.assertRaises(ValueError):
                service._host_health()


@unittest.skipUnless(sys.platform == "linux", "process sessions and /proc are Linux")
class JournalSessionTests(unittest.TestCase):
    """_journal with real process trees: a scan past its bound leaves nothing running."""

    def setUp(self):
        self.enterContext(patch.dict(service._KERNEL_CLEAN, {"until": None, "retry_at": None, "session": None}))
        self.enterContext(patch.object(service, "_KERNEL_KILL_GRACE", .5))
        self.marker = "37.%06d" % (time.monotonic_ns() // 1000 % 10**6)   # a unique `sleep` argument

    def processes(self):
        """Live processes whose argv is exactly `sleep <marker>`."""
        found = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % name, "rb") as source:
                    argv = source.read().split(b"\0")[:-1]
            except OSError:
                continue
            if argv == [b"sleep", self.marker.encode()]:
                found.append(int(name))
        return found

    def watch(self):
        """Set once the scan's sleep is seen running, so a pass is never vacuous."""
        seen = threading.Event()

        def look():
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not seen.is_set():
                if self.processes():
                    seen.set()
                time.sleep(.01)
        threading.Thread(target=look, daemon=True).start()
        return seen

    def require_sudo(self):
        try:
            if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0:
                self.addCleanup(lambda: [subprocess.run(["sudo", "-n", "kill", "-KILL", str(pid)], capture_output=True)
                                         for pid in self.processes()])
                return
        except (OSError, subprocess.SubprocessError):
            pass
        self.skipTest("needs passwordless sudo -n, as the pilot's host has for journalctl")

    def gone(self, session):
        deadline = time.monotonic() + 5
        while service._session_alive(session) and time.monotonic() < deadline:
            time.sleep(.05)
        return not service._session_alive(session)

    def test_a_timeout_ends_every_process_of_the_scan(self):
        # The scan's own child ignores SIGTERM: SIGKILL to the session ends it.
        script = ("import signal,subprocess;subprocess.Popen(['sh','-c','trap \"\" TERM; exec sleep %s']);"
                  "signal.pause()" % self.marker)
        seen = self.watch()
        with self.assertRaises(subprocess.TimeoutExpired):
            service._journal([sys.executable, "-c", script], 1)
        self.assertTrue(seen.is_set())
        self.assertEqual(self.processes(), [])
        self.assertIsNone(service._KERNEL_CLEAN["session"])

    def test_a_sudo_scan_is_ended_through_sudo(self):
        # 2026-11-11: subprocess.run's timeout SIGKILLed sudo alone, and its
        # root journalctl ran on. This user can't signal that child; sudo
        # relays the session's SIGTERM to it.
        self.require_sudo()
        seen = self.watch()
        with self.assertRaises(subprocess.TimeoutExpired):
            service._journal(["sudo", "-n", "sleep", self.marker], 1)
        self.assertTrue(seen.is_set())
        self.assertEqual(self.processes(), [])
        self.assertIsNone(service._KERNEL_CLEAN["session"])

    def test_a_scan_this_user_cannot_end_blocks_the_next_until_it_is_gone(self):
        self.require_sudo()
        seen = self.watch()
        with self.assertRaises(subprocess.TimeoutExpired):
            service._journal(["sudo", "-n", "sh", "-c", 'trap "" TERM; exec sleep ' + self.marker], 1)
        self.assertTrue(seen.is_set())
        survivors = self.processes()
        self.assertEqual(len(survivors), 1)   # root, ignoring the relayed SIGTERM
        session = service._KERNEL_CLEAN["session"]
        self.assertTrue(service._session_alive(session))
        with patch.object(service, "OPTIONS", RuntimeOptions()), patch.object(service, "_journal") as journal:
            with self.assertRaisesRegex(ValueError, "still running"):
                service._kernel_scan()
        journal.assert_not_called()
        subprocess.run(["sudo", "-n", "kill", "-KILL", str(survivors[0])], check=True, capture_output=True)
        self.assertTrue(self.gone(session))


if __name__ == "__main__":
    unittest.main()
