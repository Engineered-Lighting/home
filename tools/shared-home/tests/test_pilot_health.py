"""S1.1 health on demand: the cached verdict, the kernel-scan interval and the
journal mode, with fake host commands and clocks only."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
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


class HostFixture:
    """The host commands _host_health runs, faked by argv, and the files it reads."""

    def host(self, gpu="20000, 50", journal=(1, "-- No entries --\n", ""), options=RuntimeOptions()):
        self.commands = []
        self.gpu, self.journal = gpu, journal
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
                       patch.object(service, "Path", lambda value: meminfo if value == "/proc/meminfo" else real_path(value)),
                       patch.object(service.shutil, "disk_usage", return_value=SimpleNamespace(free=200 * 1024**3)),
                       patch.object(service, "HWMON", hwmon), patch.object(service, "OPTIONS", options),
                       patch.dict(service._KERNEL_CLEAN, {"until": None})):
            stack.enter_context(target)
        return stack

    def host_command(self, args, **kwargs):
        self.commands.append(list(args))
        self.timeouts = getattr(self, "timeouts", []) + [kwargs.get("timeout")]
        if args[0] == "systemctl":
            return SimpleNamespace(stdout="")
        if args[0] == "nvidia-smi":
            return SimpleNamespace(stdout=self.gpu + "\n")
        if "journalctl" in args:
            code, stdout, stderr = self.journal
            return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
        if args[:2] == ["docker", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([inspect()]))
        raise AssertionError("unexpected host command %r" % (args[:3],))

    def journals(self):
        return [command for command in self.commands if "journalctl" in command]


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

    def test_kernel_scan_runs_at_most_every_30_seconds(self):
        now = [1000.0]
        with self.host(), patch.object(service.time, "time", lambda: now[0]):
            for moment in (1000.0, 1010.0, 1029.9):
                now[0] = moment
                service._host_health()
            self.assertEqual(len(self.journals()), 1)
            self.assertIn(service._KERNEL_BASELINE, self.journals()[0])
            now[0] = 1030.0
            service._host_health()
            self.assertEqual(len(self.journals()), 2)
            self.assertIn("@995", self.journals()[1])   # since the last clean scan, 5 s overlap

    def test_only_the_baseline_scan_gets_the_long_bound(self):
        # 2026-10-09: the full window took 36.5 s against a flat 15 s bound, so
        # no process could ever finish its baseline scan and every check failed.
        now = [1000.0]
        with self.host(), patch.object(service.time, "time", lambda: now[0]), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            service._host_health()
            now[0] = 1030.0
            service._host_health()
        bounds = [bound for command, bound in zip(self.commands, self.timeouts) if "journalctl" in command]
        self.assertEqual(bounds, [service._KERNEL_BASELINE_TIMEOUT, service._KERNEL_SCAN_TIMEOUT])
        self.assertEqual((service._KERNEL_BASELINE_TIMEOUT, service._KERNEL_SCAN_TIMEOUT), (180, 15))
        self.assertEqual(stderr.getvalue().count("camera pilot kernel baseline scan seconds="), 1)

    def test_a_baseline_scan_past_its_bound_is_never_clean(self):
        def slow(args, **kwargs):
            if "journalctl" in args:
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return self.host_command(args, **kwargs)
        with self.host(), patch.object(service.subprocess, "run", slow):
            for _ in range(2):
                with self.assertRaises(subprocess.TimeoutExpired):
                    service._kernel_scan()
            self.assertIsNone(service._KERNEL_CLEAN["until"])

    def test_zero_interval_scans_on_every_check(self):
        with self.host(options=RuntimeOptions(kernel_scan_seconds=0)):
            for _ in range(3):
                service._host_health()
        self.assertEqual(len(self.journals()), 3)

    def test_a_fault_is_never_cached_as_clean(self):
        with self.host(journal=(0, "Oct 08 kernel: soft lockup - CPU#3 stuck\n", "")):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    service._host_health()
        self.assertEqual(len(self.journals()), 2)

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


if __name__ == "__main__":
    unittest.main()
