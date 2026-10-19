"""cadence_ab.py against fake hosts: scripted remote steps and a fake clock; no ssh, no network, no sleeping."""
import ast
import contextlib
import hashlib
import inspect
import io
import json
import math
import os
import re
import signal
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

import cadence_ab
import harvest
import report

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "stack/services/perception-lab"
SHARED = ROOT / "tools/shared-home/home_shared"
T0 = 1791547200.0  # 2026-10-09T12:00:00Z
SHA = "a" * 64
WORKING = cadence_ab.BASE + "/releases/8eb731cd0fc684b1/service"
FACT_DIR = cadence_ab.BASE + "/data/la-fact-log"
BASELINE = {"interval_seconds": 4.0, "max_per_minute": 15, "hold_seconds": 10.0}
CANDIDATE = {"interval_seconds": 1.0, "max_per_minute": 90, "hold_seconds": 5.0}
JETSON_STEPS = {"preflight", "idle", "backup", "write_arm", "restart", "health", "started", "status", "collect"}
LA_STEPS = {"install_probe", "start_probe", "stop_probe", "probe_ready", "host_health"}
RESOLVED = "http://172.18.0.5:8000/metrics"


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def sleeper(clock, interrupt_at=None):
    count = [0]

    def sleep(seconds):
        count[0] += 1
        if count[0] == interrupt_at:
            raise KeyboardInterrupt
        clock.now += seconds
    return sleep


def preflight_reply(**changes):
    value = {"unit": {"ActiveState": "active", "SubState": "running", "MainPID": "4242", "NRestarts": "0"},
             "working_directories": [WORKING], "running_directory": WORKING,
             "app": {"app": "managed_detector_app:create_managed_detector_app", "host": "192.168.1.168", "port": "8789"},
             "worker_main_pid": "0", "sessions": {"sessions": {}, "jobs": {}},
             "machine": {"disk_free_bytes": 40 * 1024 ** 3, "mem_available_bytes": 3 * 1024 ** 3,
                         "max_temperature_c": 51.5, "kernel_log_readable": True, "kernel_fault_lines": 0,
                         "failed_units": []},
             "live": {"sha256": SHA, "mode": "0600", "cadence": dict(BASELINE),
                      "perceiver": {"enabled": True, "fact_log_directory": FACT_DIR}, "reader": True,
                      "pumps": {"qwen_live": {"enabled": False, "occupancy_hold_seconds": None},
                                "vjepa_live": {"enabled": True, "occupancy_hold_seconds": None}},
                      "mtime": T0 - 86400.0},  # written a day before the controller started
             "perceiver_line": "la-live-perceiver started: interval=4.0s capture=0.5s indeterminate_reprobe=60s",
             "journal": "readable", "process_started": T0 - 3600.0,
             "newest_fact": "2026-10-09T11:59:58.120Z"}
    value.update(changes)
    return value


class World:
    """What the hosts would measure, per arm: voice TTFT, camera backend, GPU °C,
    seconds between live requests (None: no live request at all), every
    busy_every-th candidate gauge read with a waiting request, and every
    fail_every-th candidate voice probe timing out."""

    def __init__(self, ttft=(0.050, 0.053), camera=(0.90, 0.93), gpu=(55, 60), busy_every=0, camera_every=60,
                 live_step=(4.0, 1.1), fail_every=0):
        arms = ("baseline", "candidate")
        self.ttft, self.camera, self.gpu = dict(zip(arms, ttft)), dict(zip(arms, camera)), dict(zip(arms, gpu))
        self.live_step = dict(zip(arms, live_step))
        self.busy_every, self.camera_every, self.fail_every = busy_every, camera_every, fail_every


def jline(t, message):
    return "%s victoria python3[77]: %s" % (harvest.format_time(t), message)


class Run:
    """subprocess.run as the remote steps call it: records argv and keywords, replies (code, stdout, stderr)."""

    def __init__(self, stdout="", stderr="", returncode=0, reply=None):
        self.calls, self.reply = [], reply or (lambda argv: (returncode, stdout, stderr))

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        code, out, err = self.reply(list(argv))
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class Hosts:
    """Both hosts behind ssh: the live config's state, the controller and the probe."""

    def __init__(self, clock, world=None, **preflight):
        self.clock, self.world = clock, world or World()
        self.calls, self.params = [], {}
        self.file, self.running, self.pid = "original", "original", 4242
        self.restarts = []
        self.preflight_value = preflight_reply(**preflight)
        self.la_check = {"python": "3.12.3", "url": "http://192.168.0.100:8000", "sidecar_healthz": True,
                         "gpu_temp_c": 55.0, "tctl_c": 70.1,
                         "vllm_gauges": {"num_requests_running": 0.0, "num_requests_waiting": 0.0},
                         "vllm_metrics_url": RESOLVED}
        self.pilot = "active"
        self.la_health = {"failed_units": [], "failed_user_units": [], "kernel_log_readable": True,
                          "kernel_fault_lines": 0}
        self.la_health_from = {}  # host_health call number -> changes from then on
        self.ready = {"rows": 12, "gauges": 10, "outcomes": 1, "ok": 1, "state": "active"}
        self.refuse, self.unhealthy, self.busy_from_restart, self.refuse_writes_from = {}, set(), None, None
        self.drop = {}  # step -> call numbers (1-based) that ssh ends without a reply, before the step runs
        self.uploaded = None
        # The controller journal as the service account sees it, restarts (1-based) after which no fact
        # comes (a vacant den), and per restart the cadence the live config shows instead of the file's.
        self.journal, self.vacant, self.config_shows = "readable", set(), {}
        # Per restart: the journal access journal_access finds then, the perceiver line a readable
        # journal shows instead of the arm's start line, and (fallback) an unknown process start.
        self.journal_at, self.start_lines, self.unattributed = {}, {}, set()

    # --- transport ------------------------------------------------------
    def __call__(self, argv, data=None, timeout=None):
        assert argv[:5] == list(cadence_ab.SSH), argv
        host, command = argv[5], argv[6:]
        if command[:2] == [cadence_ab.PYTHON, "-"] and "--check" in command:
            assert host == "hav-ubuntu" and data == (Path(cadence_ab.__file__).with_name("voice_probe.py")).read_bytes()
            self.calls.append((host, "check"))
            return 0, json.dumps({**self.la_check, "time": self.clock(), "time_end": self.clock()}) + "\n", ""
        if command[:2] == [cadence_ab.PYTHON, "-"]:
            text = data.decode()
            ast.parse(text, feature_version=(3, 10))
            step = re.match(r"# la-cadence-ab:(\w+)\n", text).group(1)
            params = json.loads(ast.literal_eval(re.search(r"\nPARAMS=json\.loads\((.*)\)\n", text).group(1)))
            assert host == (cadence_ab.JETSON if step in JETSON_STEPS else "hav-ubuntu"), (host, step)
            self.calls.append((host, step))
            self.params.setdefault(step, []).append(params)
            if len(self.params[step]) in self.drop.get(step, ()):
                return 255, "", "Connection reset by peer"
            message = self.refuse.pop(step, None)
            if message is None and step == "write_arm" and self.refuse_writes_from is not None:
                if len(self.params["write_arm"]) >= self.refuse_writes_from:
                    message = "live config changed outside this run"
            if message:
                return 0, json.dumps({"refused": message}) + "\n", ""
            return 0, json.dumps({"ok": getattr(self, step)(**params)}) + "\n", ""
        assert host == "hav-ubuntu", argv
        self.calls.append((host, command[0].split()[0]))
        if command[0].startswith("mkdir -p -m 700 la-latency/runs/cadence-ab-"):
            self.uploaded = data
            return 0, "", ""
        if command[0] == "journalctl":
            return 0, self.pilot_journal(), ""
        if command[0] == "cat":
            return 0, self.probe_jsonl(), ""
        raise AssertionError(argv)

    # --- Jetson ---------------------------------------------------------
    def preflight(self, base, unit, worker_unit, path):
        assert path == cadence_ab.LIVE_CONFIG
        return {**self.preflight_value, "started": self.clock(), "time": self.clock()}

    def idle(self, base, worker_unit):
        busy = self.busy_from_restart is not None and len(self.restarts) >= self.busy_from_restart
        return {"sessions": {"sessions": {"recording": 1} if busy else {}, "jobs": {}}, "worker_main_pid": "0",
                "time": self.clock()}

    def backup(self, path, target, sha256):
        assert sha256 == SHA and target.startswith(cadence_ab.LIVE_CONFIG + ".before-cadence-ab-")
        return {"backup": target, "sha256": SHA}

    def write_arm(self, path, backup, sha256, arm, candidate):
        assert candidate == CANDIDATE and arm in (None, CANDIDATE)
        target = "original" if arm is None else "candidate"
        written, self.file = self.file != target, target
        return {"sha256": SHA if arm is None else "c" * 64, "written": written}

    def restart(self, base, unit, worker_unit, max_temperature_c):
        assert max_temperature_c == 80
        self.clock.now += 2
        self.running, self.pid = self.file, self.pid + 1
        self.restarts.append((self.clock.now, "baseline" if self.file == "original" else "candidate"))
        return {"restarted_at": self.clock.now}

    def health(self, unit, new, host, port):
        assert (new, host, port) == (WORKING, "192.168.1.168", "8789")
        self.clock.now += 20
        healthy = len(self.restarts) not in self.unhealthy
        return {"active_state": "active", "main_pid": str(self.pid), "healthy": healthy}

    def started(self, unit, since, seconds, path=None, directory=None, journal_only=False):
        """As the remote step: 'error' or (journal only) 'unreadable' at once; the journal's line when
        the plan read it or it reads now; else the fallback."""
        assert (path, directory, seconds) == (cadence_ab.LIVE_CONFIG, FACT_DIR, cadence_ab.START_WAIT_S)
        plan = self.preflight_value
        assert journal_only == (plan["journal"] == "readable" or plan["perceiver_line"] is not None), journal_only
        arm = BASELINE if self.running == "original" else CANDIDATE
        restart = len(self.restarts)
        access = self.journal_at.get(restart, self.journal)
        if access == "error" or (journal_only and access == "unreadable"):
            return {"journal": access, "line": None}
        if journal_only or access == "readable":
            line = None
            if access == "readable":
                line = self.start_lines.get(restart, "la-live-perceiver started: interval=%.1fs capture=0.5s "
                                                     "indeterminate_reprobe=60s" % arm["interval_seconds"])
            if line is None:
                self.clock.now += seconds  # polled the journal to the deadline
            return {"journal": access, "line": line}
        fact = None
        if restart in self.vacant:
            self.clock.now += seconds  # waited for a fact that never came
        else:
            fact = harvest.format_time(next(t for t in self.fact_times() if t >= since))
        return {"journal": access, "line": None, "fact": fact, "since": since,
                "process_started": None if restart in self.unattributed else since + 0.5, "enabled": True,
                "cadence": dict(self.config_shows.get(restart, arm)), "sha256": SHA}

    def status(self, base, unit):
        return {"unit": {"ActiveState": "active", "SubState": "running", "MainPID": str(self.pid), "NRestarts": "0"},
                "sessions": {"sessions": {}, "jobs": {}}, "time": self.clock()}

    def collect(self, unit, since, until, directory):
        assert directory == FACT_DIR
        return {"journal": self.controller_journal(), "facts": self.facts(), "time": self.clock()}

    # --- LA -------------------------------------------------------------
    def install_probe(self, upload, target, sha256):
        assert upload == target + ".upload" and target.startswith("~/la-latency/voice_probe-")
        assert sha256 == cadence_ab.hashlib.sha256(self.uploaded).hexdigest()
        return {"probe": target, "installed": True}

    def start_probe(self, unit, target, directory, argv, runtime_s):
        # A fixed gauge address: no docker command runs inside the bounded unit.
        assert unit.startswith("la-voice-probe-") and "--vllm-container" not in argv and "--duration" in argv
        assert argv[argv.index("--vllm-metrics-url") + 1] == RESOLVED
        return {"unit": unit, "output": directory + "/voice-probe.jsonl", "started_at": self.clock()}

    def stop_probe(self, unit):
        return {"state": "inactive"}

    def probe_ready(self, unit, output, seconds):
        assert output.startswith("la-latency/runs/cadence-ab-") and seconds == cadence_ab.PROBE_READY_S
        return dict(self.ready)

    def host_health(self, faults, pilot_unit, probe_unit=None, output=None, fresh_seconds=120):
        assert faults == cadence_ab.DEPLOY.LIB["FAULTS"] and pilot_unit == "victoria-camera-pilot.service"
        value = {"pilot": self.pilot, **self.la_health, "time": self.clock()}
        if probe_unit is not None:
            value.update(probe="active", gpu_temp_c=55.0, tctl_c=70.0, gauge_age_s=1.0, outcome_age_s=6.0)
        for number, changes in sorted(self.la_health_from.items()):
            if len(self.params["host_health"]) >= number:
                value.update(changes)
        return value

    # --- the world's measurements ----------------------------------------
    def segments(self):
        marks = self.restarts + [(self.clock.now, None)]
        for (start, arm), (stop, _) in zip(marks, marks[1:]):
            yield start, stop, arm

    def fact_times(self):
        for start, stop, arm in self.segments():
            t, step = start + 6, self.world.live_step[arm]
            while step is not None and t < stop:
                yield t
                t += step

    def controller_journal(self):
        if self.journal != "readable":
            return ""  # collect reads the same journal
        lines = []
        for start, stop, arm in self.segments():
            lines.append(jline(start + 3, "la-live-perceiver started: interval=%.1fs capture=0.5s "
                                          "indeterminate_reprobe=60s" % (4.0 if arm == "baseline" else 1.0)))
            lines.append(jline(start + 5.2, "occupancy edge: entity=binary_sensor.den_oak_person_occupancy "
                                            "from=unknown to=on observed_at=%.3f" % (start + 5)))
        return "\n".join(lines) + "\n"

    def facts(self):
        return [{"schema": "la-fact-log-1", "source_id": "la_qwen_den_oak", "observed_at": harvest.format_time(t - 1.3),
                 "sent_at": harvest.format_time(t - 0.9), "available_at": harvest.format_time(t),
                 "outcome": "completed", "latency_s": 1.3} for t in self.fact_times()]

    def pilot_journal(self):
        lines = [jline(t - 0.1, "live scene backend seconds=0.700 outcome=completed health_ms=0 exec_ms=700 "
                                "total_ms=700") for t in self.fact_times()]
        for start, stop, arm in self.segments():
            t = start + 30
            while t < stop and self.world.camera_every:
                seconds = self.world.camera[arm]
                lines.append(jline(t, "camera pilot backend seconds=%.3f result=ok health_ms=0 exec_ms=%d total_ms=%d"
                                   % (seconds, seconds * 1000, seconds * 1000)))
                t += self.world.camera_every
        lines.sort()
        return "\n".join(lines) + "\n"

    def probe_jsonl(self):
        rows, number, reads = [], 0, 0

        def add(pipeline, metric, value, t, kind, n, method):
            rows.append({"pipeline": pipeline, "metric": metric, "value_s": round(value, 6),
                         "t": harvest.format_time(t), "source": "%s:voice-probe.jsonl:%d" % (kind, n),
                         "method": method})
        for start, stop, arm in self.segments():
            t = start + 5
            while t < stop:
                number += 1
                ttft = self.world.ttft[arm] + (number % 7) / 1000
                if arm == "candidate" and self.world.fail_every and number % self.world.fail_every == 0:
                    add("voice_probe", "outcome:timeout", 1, t + 10, "voice-probe", number, "voice_probe_outcome")
                    t += 15
                    continue
                add("voice_probe", "ttft", ttft, t + ttft, "voice-probe", number,
                    "voice_probe_stream_first_content_chunk")
                add("voice_probe", "e2e", ttft + .004, t + ttft + .004, "voice-probe", number, "voice_probe_stream_done")
                add("voice_probe", "outcome:ok", 1, t + ttft + .004, "voice-probe", number, "voice_probe_outcome")
                add("la_gpu", "gpu_temp_c", self.world.gpu[arm], t + .1, "sidecar-metrics", number,
                    "sidecar_metrics_gpu_temp_c")
                t += 15
            t = start + 5.5
            while t < stop:
                reads += 1
                busy = arm == "candidate" and self.world.busy_every and reads % self.world.busy_every == 0
                add("vllm", "num_requests_running", 1, t, "vllm-load", reads, "voice_probe_vllm_gauge")
                add("vllm", "num_requests_waiting", 1 if busy else 0, t, "vllm-load", reads, "voice_probe_vllm_gauge")
                t += 5
        return "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run"
        self.clock = Clock()

    def main(self, hosts, *extra, interrupt_at=None):
        lines = []
        code = cadence_ab.main(["--out", str(self.out), "--window-minutes", "10", "--min-occupied-minutes", "20",
                                *extra], runner=hosts, out=lines.append, clock=self.clock,
                               sleep=sleeper(self.clock, interrupt_at))
        return code, lines

    def steps(self, hosts):
        return [step for _, step in hosts.calls]


class PlanTests(Harness):
    def test_dry_run_is_read_only_and_prints_the_schedule_and_restore_commands(self):
        hosts = Hosts(self.clock)
        code, lines = self.main(hosts)
        self.assertEqual(code, 0)
        self.assertEqual(self.steps(hosts), ["preflight", "check", "host_health"])
        plan = json.loads(lines[0])
        self.assertEqual(plan["mode"], "dry-run")
        self.assertEqual(plan["refusals"], [])
        self.assertEqual(plan["schedule"], ["baseline", "candidate"] * 3)
        self.assertEqual(plan["arms"], {"baseline": BASELINE, "candidate": CANDIDATE})
        backup = cadence_ab.LIVE_CONFIG + ".before-cadence-ab-20261009T120000Z"
        self.assertEqual(plan["restore"], [
            "ssh %s \"install -m 600 '%s' '%s'\"" % (cadence_ab.JETSON, backup, cadence_ab.LIVE_CONFIG),
            "ssh %s 'systemctl --user restart perception-lab-managed.service'" % cadence_ab.JETSON,
            "ssh hav-ubuntu 'systemctl --user stop la-voice-probe-20261009t120000z'"])
        self.assertIn("--timeout", plan["probe"]["argv"])
        self.assertEqual(plan["settings"]["guard"], {"guard_ratio_max": 1.10, "gpu_temp_p95_max_c": 70,
                                                     "queue_busy_share_max": 0.30})
        self.assertFalse(self.out.exists())

    def refusals(self, hosts, *extra):
        code, lines = self.main(hosts, *extra)
        self.assertEqual(code, 2)
        return json.loads(lines[0])["refusals"]

    def test_refusals(self):
        cases = [
            (dict(sessions=None), "Lab session state unknown"),
            (dict(perceiver_line="la-live-perceiver refused: the Lab qwen_live block is enabled"),
             "la-live-perceiver is not running at the baseline interval"),
            (dict(live={**preflight_reply()["live"], "cadence": dict(BASELINE, interval_seconds=1.0)}),
             "the live config is not the baseline arm"),
            (dict(live={**preflight_reply()["live"], "pumps": {"vjepa_live": {"enabled": True,
                                                                              "occupancy_hold_seconds": 10}}}),
             "vjepa_live names occupancy_hold_seconds 10"),
            (dict(live={**preflight_reply()["live"], "pumps": {"qwen_live": {"enabled": True,
                                                                             "occupancy_hold_seconds": None}}}),
             "qwen_live is enabled"),
            (dict(live={**preflight_reply()["live"], "perceiver": None}), "la_live_perceiver is absent"),
            (dict(machine={**preflight_reply()["machine"], "kernel_fault_lines": 2}), "new kernel fault records"),
            (dict(unit={"ActiveState": "failed"}), "perception-lab-managed.service is not active"),
        ]
        for changes, expected in cases:
            with self.subTest(expected=expected):
                found = self.refusals(Hosts(self.clock, **changes))
                self.assertTrue(any(expected in r for r in found), found)

    def test_la_refusals(self):
        hosts = Hosts(self.clock)
        hosts.la_check.update(vllm_gauges={"error": "ConnectionRefusedError"}, gpu_temp_c=None)
        hosts.pilot = "inactive"
        found = self.refusals(hosts)
        self.assertTrue(any("vLLM gauges unreadable from LA (ConnectionRefusedError)" in r for r in found))
        self.assertTrue(any("no gpu_temp_c" in r for r in found))
        self.assertTrue(any("victoria-camera-pilot.service on LA is inactive" in r for r in found))
        hosts = Hosts(self.clock)
        hosts.la_check.update(vllm_metrics_url="http://172.18.0.5:8000/metrics?x=1;id")
        self.assertTrue(any("no usable vLLM metrics address" in r for r in self.refusals(hosts)))
        hosts = Hosts(self.clock)
        original = hosts.__call__

        def skewed(argv, data=None, timeout=None):
            code, out, err = original(argv, data, timeout)
            if "--check" in argv:
                value = json.loads(out)
                value["time"] += 30
                value["time_end"] += 30
                out = json.dumps(value) + "\n"
            return code, out, err
        found = self.refusals(skewed)
        self.assertTrue(any("LA clock differs from this workstation by 30.0 s" in r for r in found))

    def test_la_host_health_refusals(self):
        """AGENTS.md: failed units, new kernel faults and temperatures before LA takes the load."""
        cases = [
            (dict(failed_units=["snap-firefox-8819.mount"]), "failed system units on LA: snap-firefox-8819.mount"),
            (dict(kernel_fault_lines=1), "new kernel fault records on LA"),
        ]
        for changes, expected in cases:
            with self.subTest(expected=expected):
                hosts = Hosts(self.clock)
                hosts.la_health.update(changes)
                found = self.refusals(hosts)
                self.assertTrue(any(expected in r for r in found), found)
        for changes, expected in ((dict(gpu_temp_c=70.0), "LA GPU at 70 °C"), (dict(tctl_c=88.4), "LA Tctl at 88.4 °C")):
            with self.subTest(expected=expected):
                hosts = Hosts(self.clock)
                hosts.la_check.update(changes)
                found = self.refusals(hosts)
                self.assertTrue(any(expected in r for r in found), found)
        hosts = Hosts(self.clock)
        hosts.la_health["kernel_log_readable"] = False
        code, lines = self.main(hosts)
        self.assertEqual(code, 0)
        self.assertTrue(any("LA kernel log not readable" in w for w in json.loads(lines[0])["warnings"]))

    def test_known_failed_system_units_are_ignored_as_the_pilot_ignores_them(self):
        """The 2026-10-09 dry run: observer-health and observer-recover failed, which the pilot ignores."""
        hosts = Hosts(self.clock)
        hosts.la_health["failed_units"] = sorted(cadence_ab.PILOT_KNOWN_FAILED_UNITS)
        code, lines = self.main(hosts)
        plan = json.loads(lines[0])
        self.assertEqual((code, plan["refusals"], plan["warnings"]), (0, [], []))
        self.assertEqual(plan["preflight"]["la_health"]["failed_units"], sorted(cadence_ab.PILOT_KNOWN_FAILED_UNITS))

    def test_an_unknown_failed_system_unit_is_refused_beside_known_ones(self):
        hosts = Hosts(self.clock)
        hosts.la_health["failed_units"] = ["observer-health.service", "observer-recover.service", "nginx.service",
                                           "snap-firefox-8821.mount"]
        found = self.refusals(hosts)
        units = [r for r in found if r.startswith("failed system units on LA:")]
        self.assertEqual(len(units), 1, found)
        self.assertTrue(units[0].startswith("failed system units on LA: nginx.service, snap-firefox-8821.mount ("),
                        units[0])
        self.assertNotIn("observer-", units[0])
        self.assertIn("sudo systemctl reset-failed <unit>", units[0])  # the snap-mount note
        hosts = Hosts(self.clock)
        hosts.la_health["failed_units"] = ["nginx.service"]
        found = self.refusals(hosts)
        self.assertTrue(any(r.startswith("failed system units on LA: nginx.service (") for r in found), found)
        self.assertFalse(any("reset-failed" in r for r in found))  # no snap mount, no snap note

    def test_failed_user_units_are_a_warning_never_a_refusal(self):
        """The owner's own Unreal and animation jobs: the pilot never checks user units."""
        user = ["camp-animation-render-20261009.service", "camp-characters-bake.service",
                "snap.firefox.firefox-0f1e2d3c.scope"]
        hosts = Hosts(self.clock)
        hosts.la_health["failed_user_units"] = user
        code, lines = self.main(hosts)
        plan = json.loads(lines[0])
        self.assertEqual((code, plan["refusals"]), (0, []))
        self.assertEqual(plan["warnings"], ["failed user units on LA (the pilot never checks user units, so they are "
                                            "listed only): " + ", ".join(user)])
        # Before each window too: user units never abort a run.
        self.clock = Clock()
        hosts = Hosts(self.clock)
        hosts.la_health_from = {2: {"failed_user_units": user}}
        self.assertEqual(self.main(hosts, "--apply")[0], 0)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertEqual(self.steps(hosts).count("host_health"), 1 + 6)

    def unreadable(self, **changes):
        """The 2026-10-09 dry run: journalctl --user finds no journal files, the perceiver runs."""
        return Hosts(self.clock, **dict(dict(perceiver_line=None, journal="unreadable"), **changes))

    def test_unreadable_journal_and_recent_facts_use_the_fact_log_fallback(self):
        hosts = self.unreadable(process_started=T0 - 3600.0)
        code, lines = self.main(hosts)
        plan = json.loads(lines[0])
        self.assertEqual((code, plan["refusals"]), (0, []))
        evidence = plan["perceiver_evidence"]
        self.assertEqual((evidence["source"], evidence["journal"], evidence["line"]), ("fact-log", "unreadable", None))
        self.assertEqual(evidence["config"], {"enabled": True, "cadence": BASELINE, "holds_arm": True})
        self.assertEqual((evidence["newest_fact"], evidence["newest_fact_age_s"]), ("2026-10-09T11:59:58.120Z", 1.9))
        self.assertTrue(evidence["fact_from_this_process"] and evidence["verified"])
        self.assertEqual(plan["preflight"]["controller_journal"], "unreadable")
        self.assertEqual(len(plan["warnings"]), 1, plan["warnings"])
        self.assertIn("perceiver liveness comes from the fact-log fallback", plan["warnings"][0])
        self.assertIn("Optional owner step: add the service account to group systemd-journal so controller logs "
                      "are readable", plan["warnings"][0])
        self.assertIn("cannot reach PASS", plan["warnings"][0])  # the occupancy edges are in that journal too
        self.assertIn("--apply refuses unless --allow-unreadable-journal is given", plan["warnings"][0])
        self.assertEqual((evidence["process_started"], evidence["config_mtime"], evidence["config_older_than_process"]),
                         (T0 - 3600.0, T0 - 86400.0, True))
        # The journal, when it shows the start line, is the evidence.
        code, lines = self.main(Hosts(self.clock))
        self.assertEqual(json.loads(lines[0])["perceiver_evidence"], {
            "source": "journal", "journal": "readable",
            "line": "la-live-perceiver started: interval=4.0s capture=0.5s indeterminate_reprobe=60s",
            "process_started": T0 - 3600.0, "config_mtime": T0 - 86400.0})

    def test_apply_refuses_an_unreadable_journal_unless_allowed(self):
        """The occupancy edges are in the controller journal: without it a run can only be INSUFFICIENT."""
        for access in ("unreadable", "empty"):
            with self.subTest(access=access):
                hosts = self.unreadable(journal=access, process_started=T0 - 3600.0)
                found = self.refusals(hosts, "--apply")
                self.assertEqual(self.steps(hosts), ["preflight", "check", "host_health"])  # nothing changed
                self.assertEqual(len(found), 1, found)
                self.assertTrue(found[0].startswith("--apply with the controller journal %s: the occupancy edge lines "
                                                    "the analysis reads are in it" % access), found)
                self.assertIn("pass --allow-unreadable-journal to run anyway", found[0])
        # A readable journal that only rotated the start line out still has the edges: no gate.
        code, lines = self.main(Hosts(self.clock, perceiver_line=None), "--apply")
        self.assertEqual(code, 0)

    def test_a_journalctl_that_does_not_finish_decides_nothing(self):
        found = self.refusals(Hosts(self.clock, journal="error"))
        self.assertTrue(any(r.startswith("journalctl on the controller did not finish (a timeout)") for r in found),
                        found)
        found = self.refusals(self.unreadable(journal="error"))
        self.assertTrue(any(r.startswith("journalctl on the controller did not finish") for r in found), found)

    def test_a_config_changed_after_the_controller_started_is_refused(self):
        """The controller reads managed-live.json once, at start: the restore would otherwise take the running
        process (say a candidate left by a restore that could not restart) for the original."""
        live = dict(preflight_reply()["live"], mtime=T0 - 60.0)
        for hosts in (Hosts(self.clock, live=live), self.unreadable(live=live)):
            with self.subTest(journal=hosts.preflight_value["journal"]):
                found = self.refusals(hosts)
                self.assertTrue(any(r.startswith("managed-live.json changed after the controller started (file "
                                                 "2026-10-09T11:59:00.000Z, process 2026-10-09T11:00:00.000Z)")
                                    for r in found), found)
        # Within /proc's slack of the start: the write that preceded it.
        live = dict(preflight_reply()["live"], mtime=T0 - 3600.0 + 1.5)
        self.assertEqual(self.main(self.unreadable(live=live))[0], 0)
        # Without the journal, an unknown process start or mtime leaves nothing to show the process runs the file.
        for changes in (dict(process_started=None), dict(live=dict(preflight_reply()["live"], mtime=None))):
            with self.subTest(changes=changes):
                found = self.refusals(self.unreadable(**changes))
                self.assertTrue(any(r.startswith("the controller process's start (/proc) or managed-live.json's mtime "
                                                 "is unknown") for r in found), found)
        # With the journal, its start line is the evidence: an unknown process start is not refused.
        self.assertEqual(self.main(Hosts(self.clock, process_started=None))[0], 0)

    def test_unreadable_journal_and_no_recent_facts_is_a_warning_not_a_refusal(self):
        cases = [dict(newest_fact="2026-10-09T11:40:00.000Z"),  # 20 min old: the den may be vacant
                 dict(newest_fact=None),
                 dict(process_started=T0 - 1.0),  # the newest fact is the previous process's
                 dict(process_started=T0 - 3.0)]  # within /proc's slack of the start: not proof either
        for changes in cases:
            with self.subTest(changes=changes):
                code, lines = self.main(self.unreadable(**changes))
                plan = json.loads(lines[0])
                self.assertEqual((code, plan["refusals"]), (0, []))
                self.assertFalse(plan["perceiver_evidence"]["verified"])
                self.assertTrue(any(w.startswith("perceiver liveness unverified: no la-fact-log-1 line from this "
                                                 "controller process in the last 10 min") for w in plan["warnings"]),
                                plan["warnings"])
        # The config itself must still hold the baseline with the perceiver enabled.
        live = dict(preflight_reply()["live"], cadence=dict(BASELINE, hold_seconds=5.0))
        found = self.refusals(self.unreadable(live=live))
        self.assertTrue(any("the live config is not the baseline arm" in r for r in found), found)
        found = self.refusals(self.unreadable(live=dict(preflight_reply()["live"], perceiver={"enabled": False})))
        self.assertTrue(any("la_live_perceiver is absent or not enabled" in r for r in found), found)

    def test_a_readable_journal_line_stays_the_stronger_evidence(self):
        """A readable journal whose newest perceiver line is not the baseline start still refuses, facts or not."""
        found = self.refusals(Hosts(self.clock, perceiver_line="la-live-perceiver started: interval=1.0s capture=0.5s "
                                                               "indeterminate_reprobe=60s"))
        self.assertTrue(any("not running at the baseline interval" in r for r in found), found)
        # A readable journal that rotated the start line out falls back, without the owner step.
        code, lines = self.main(Hosts(self.clock, perceiver_line=None))
        plan = json.loads(lines[0])
        self.assertEqual((code, plan["perceiver_evidence"]["source"]), (0, "fact-log"))
        self.assertTrue(plan["warnings"][0].startswith("no la-live-perceiver line in the controller journal"))
        self.assertIn("every restart still needs its start line", plan["warnings"][0])
        self.assertFalse(any("systemd-journal" in w for w in plan["warnings"]))

    def test_a_failing_listing_of_la_failed_units(self):
        """The pilot lists failed system units with check=True: a listing that fails refuses there too."""
        hosts = Hosts(self.clock)
        hosts.la_health["failed_units_error"] = "exit 1: Failed to connect to bus: No such file or directory"
        found = self.refusals(hosts)
        self.assertTrue(any(r.startswith("LA could not list its failed system units (exit 1: Failed to connect to "
                                         "bus: No such file or directory): the pilot refuses") for r in found), found)
        # User units: the pilot never lists them, so a failing listing is a warning.
        hosts = Hosts(self.clock)
        hosts.la_health["failed_user_units_error"] = "exit 1: Failed to connect to bus: No medium found"
        code, lines = self.main(hosts)
        plan = json.loads(lines[0])
        self.assertEqual((code, plan["refusals"]), (0, []))
        self.assertEqual(plan["warnings"], ["LA could not list its failed user units (exit 1: Failed to connect to "
                                            "bus: No medium found); the pilot never checks them"])

    def test_clock_offsets_compare_midpoints(self):
        """A slow preflight stamps its end late; its own midpoint keeps the offset honest."""
        hosts = Hosts(self.clock)

        def slow(base, unit, worker_unit, path):
            started = self.clock.now + 0.1
            self.clock.now += 20
            return {**hosts.preflight_value, "started": started, "time": self.clock.now}
        hosts.preflight = slow
        code, lines = self.main(hosts)
        plan = json.loads(lines[0])
        self.assertEqual(code, 0, plan["refusals"])
        self.assertEqual(plan["warnings"], [])
        self.assertLess(abs(plan["preflight"]["clock_offsets_s"]["jetson"]), 0.1)

    def test_remote_arguments_are_shell_quoted(self):
        seen = []
        ctx = cadence_ab.Context(None, lambda argv, data, timeout: seen.append(argv) or (0, "", ""), print, None, None)
        ctx.ssh("hav-ubuntu", ["cat", "--", "a b;touch x", "plain/path-1.jsonl"])
        self.assertEqual(seen[0][5:], ["hav-ubuntu", "cat", "--", "'a b;touch x'", "plain/path-1.jsonl"])

    def test_the_probe_outlives_the_worst_case_waits(self):
        hosts = Hosts(self.clock)
        code, lines = self.main(hosts, "--session-wait", "3600")
        plan = json.loads(lines[0])
        argv = plan["probe"]["argv"]
        duration = int(argv[argv.index("--duration") + 1])
        self.assertGreaterEqual(duration, (6 + 1) * 3 * 3600)
        self.assertEqual(plan["probe"]["runtime_s"], duration + 600)

    def test_a_session_now_is_a_warning_the_run_waits_for(self):
        hosts = Hosts(self.clock, sessions={"sessions": {"recording": 1}, "jobs": {}})
        code, lines = self.main(hosts)
        self.assertEqual(code, 0)
        self.assertIn("a Lab session or job is active now; --apply waits for it before each restart",
                      json.loads(lines[0])["warnings"])

    def test_a_fixed_vllm_metrics_url_replaces_the_container_lookup(self):
        hosts = Hosts(self.clock)
        seen = []
        original = hosts.__call__

        def recording(argv, data=None, timeout=None):
            seen.append(argv)
            return original(argv, data, timeout)
        code, lines = self.main(recording, "--vllm-metrics-url", "http://127.0.0.1:18000/metrics")
        self.assertEqual(code, 0)
        check = [argv for argv in seen if "--check" in argv][0]
        self.assertEqual(check[-2:], ["--vllm-metrics-url", "http://127.0.0.1:18000/metrics"])
        self.assertNotIn("--vllm-container", check)
        argv = json.loads(lines[0])["probe"]["argv"]
        self.assertEqual(argv[argv.index("--vllm-metrics-url") + 1], "http://127.0.0.1:18000/metrics")
        self.assertNotIn("--vllm-container", argv)

    def test_the_probe_unit_gets_the_address_the_check_resolved(self):
        """docker (Go runtime threads) never runs inside the TasksMax=16 unit."""
        hosts = Hosts(self.clock)
        code, lines = self.main(hosts)
        self.assertEqual(code, 0)
        plan = json.loads(lines[0])
        argv = plan["probe"]["argv"]
        self.assertEqual(argv[argv.index("--vllm-metrics-url") + 1], RESOLVED)
        self.assertNotIn("--vllm-container", argv)

    def test_arguments(self):
        for bad in (["--candidate", "0.4/90/5"], ["--candidate", "1/121/5"], ["--candidate", "1/90/2"],
                    ["--candidate", "4/15/10"], ["--windows", "1"], ["--probe-url", "https://x"],
                    ["--analyze", "x", "--apply"], ["--probe-url", "http://x;touch${IFS}p:8000"],
                    ["--probe-url", "http://x$(id):8000"], ["--vllm-metrics-url", "http://h:8000/metrics?a=1&b=2"],
                    ["--vllm-metrics-url", "http://h;id/metrics"], ["--vllm-metrics-url", "https://h/metrics"],
                    ["--vllm-container", "hav-vllm;id"]):
            with self.subTest(bad=bad), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                cadence_ab.parse_args(bad)


class ApplyTests(Harness):
    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))

    def done(self, lines):
        return json.loads(lines[-1])

    def test_a_full_run_restores_the_original_and_passes(self):
        hosts = Hosts(self.clock)
        handler = signal.getsignal(signal.SIGINT)
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 0, lines[-3:])
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertEqual([arm for _, arm in hosts.restarts], ["baseline", "candidate"] * 3 + ["baseline"])
        steps = self.steps(hosts)
        self.assertEqual(steps[:8], ["preflight", "check", "host_health", "backup", "mkdir", "install_probe",
                                     "start_probe", "probe_ready"])
        self.assertEqual(steps[8:14], ["idle", "host_health", "write_arm", "restart", "health", "started"])
        self.assertEqual(steps.count("host_health"), 1 + 6)  # the plan, then before every window
        tail = steps[steps.index("stop_probe") - 5:]
        self.assertEqual(tail, ["write_arm", "idle", "restart", "health", "started", "stop_probe", "collect",
                                "journalctl", "cat"])
        self.assertIs(signal.getsignal(signal.SIGINT), handler)  # Ctrl-C works again after the run
        self.assertEqual(json.loads(lines[1])["step"], "restore-commands")
        done = self.done(lines)
        self.assertEqual((done["verdict"], done["completed"], done["failure"]), ("PASS", True, None))
        self.assertIsNone(done["restore_commands"])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual([w["arm"] for w in summary["windows"]], ["baseline", "candidate"] * 3)
        candidate = summary["arms"]["candidate"]
        self.assertGreater(candidate["occupied_minutes"], 29)
        self.assertEqual(candidate["queue_busy"][0], 0)
        self.assertGreater(candidate["queue_busy"][1], 300)
        self.assertGreater(candidate["facts"] / candidate["occupied_minutes"],
                           3 * summary["arms"]["baseline"]["facts"] / summary["arms"]["baseline"]["occupied_minutes"])
        self.assertEqual(candidate["occupied_minutes"], candidate["edge_occupied_minutes"])
        text = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertIn("**Verdict: PASS.** Baseline: 4 s, 15 per minute, hold 10 s. Candidate: 1 s, 90 per minute, "
                      "hold 5 s.", text)
        self.assertIn("| voice TTFT p95 while occupied: candidate ≤ 1.10 × baseline | 0.056 s (n=", text)
        self.assertIn("| live load: candidate ≥ 30 completed live requests per occupied minute (50% of its 60), "
                      "baseline above 0 |", text)
        manifest = self.manifest()
        self.assertEqual(manifest["outcome"]["restore"], {"file": "restored", "controller": "restarted on the original"})
        self.assertTrue(all(w["complete"] for w in manifest["windows"]))
        # samples.jsonl is report.py input.
        rows = report.load_samples([self.out / "samples.jsonl"])
        self.assertTrue({("voice_probe", "ttft"), ("camera_chat_la", "backend"), ("qwen_live_victoria", "request_to_fact"),
                         ("la_gpu", "gpu_temp_c")} <= {(r["pipeline"], r["metric"]) for r in rows})
        # --analyze recomputes the same verdict offline.
        lines = []
        self.assertEqual(cadence_ab.main(["--analyze", str(self.out)], out=lines.append), 0)
        self.assertEqual(json.loads(lines[0])["verdict"], "PASS")

    def test_a_guard_failure_restores_and_says_fail(self):
        hosts = Hosts(self.clock, World(ttft=(0.050, 0.062)))
        code, lines = self.main(hosts, "--apply", "--keep-candidate-on-pass")
        self.assertEqual(code, 1)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["guard"][0][3], report.FAILS)

    def test_candidate_voice_timeouts_fail_and_the_candidate_is_never_kept(self):
        """10 of about 120 candidate probes time out; the rest stay within 1.10x. Not a PASS."""
        hosts = Hosts(self.clock, World(fail_every=12))
        code, _ = self.main(hosts, "--apply", "--keep-candidate-on-pass")
        self.assertEqual(code, 1)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "FAIL")
        candidate = summary["arms"]["candidate"]
        self.assertGreaterEqual(candidate["voice_failed_occupied"], 9)
        voice = summary["guard"][0]
        self.assertEqual(voice[3], report.FAILS)
        self.assertTrue(voice[2].startswith("failed (n="), voice)
        failures = [row for row in summary["guard"] if row[0].startswith("voice probe failures")][0]
        self.assertEqual(failures[3], report.FAILS)

    def test_no_candidate_live_load_is_insufficient_and_the_candidate_is_never_kept(self):
        """A kill switch, a paused perceiver or an all-yield LA: the candidate cadence never ran."""
        for live_step in ((4.0, None), (4.0, 30.0)):
            with self.subTest(live_step=live_step):
                self.clock = Clock()
                self.out = Path(self.tmp.name) / ("run-%s" % live_step[1])
                hosts = Hosts(self.clock, World(live_step=live_step))
                code, lines = self.main(hosts, "--apply", "--keep-candidate-on-pass")
                self.assertEqual(code, 1)
                self.assertEqual((hosts.file, hosts.running), ("original", "original"))
                self.assertNotIn("kept", self.manifest()["outcome"])
                summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary["verdict"], "INSUFFICIENT")
                live = [row for row in summary["guard"] if row[0].startswith("live load")][0]
                self.assertTrue(live[3].startswith(report.NO_DATA), live)

    def test_a_probe_without_samples_aborts_before_any_change(self):
        hosts = Hosts(self.clock)
        hosts.ready = {"rows": 3, "gauges": 0, "outcomes": 1, "ok": 1, "state": "active"}
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        steps = self.steps(hosts)
        self.assertNotIn("write_arm", steps)
        self.assertNotIn("restart", steps)
        self.assertEqual(steps[steps.index("probe_ready"):steps.index("probe_ready") + 2], ["probe_ready", "stop_probe"])
        outcome = self.manifest()["outcome"]
        self.assertIn("the voice probe wrote no vLLM gauge row", outcome["failure"])
        self.assertEqual(outcome["restore"], {"file": "unchanged (the original)",
                                              "controller": "already runs the original"})
        self.assertIsNone(self.done(lines)["restore_commands"])

    def test_an_unhealthy_la_before_a_window_aborts_and_restores(self):
        hosts = Hosts(self.clock)
        hosts.la_health_from = {4: {"failed_units": ["snap-firefox-8820.mount"]}}  # before window 3
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        manifest = self.manifest()
        self.assertEqual(len(manifest["windows"]), 3)
        self.assertIn("LA: failed system units on LA: snap-firefox-8820.mount", manifest["outcome"]["failure"])
        self.assertNotIn("config_sha256", manifest["windows"][2])
        for changes, expected in ((dict(failed_units_error="exit 1: Failed to connect to bus"),
                                   "LA could not list its failed system units (exit 1: Failed to connect to bus)"),
                                  (dict(probe="inactive"), "the voice probe unit on LA is inactive"),
                                  (dict(gauge_age_s=600.0), "the probe's newest vLLM gauge read is 600 s old"),
                                  (dict(outcome_age_s=None), "the probe's newest voice probe outcome is missing")):
            with self.subTest(expected=expected):
                self.clock = Clock()
                self.out = Path(self.tmp.name) / ("run-%s" % len(expected))
                hosts = Hosts(self.clock)
                hosts.la_health_from = {3: changes}
                self.assertEqual(self.main(hosts, "--apply")[0], 3)
                self.assertIn(expected, self.manifest()["outcome"]["failure"])
                self.assertEqual((hosts.file, hosts.running), ("original", "original"))

    def unreadable(self):
        """The 2026-10-09 host: journalctl --user finds no journal files, here and after every restart."""
        hosts = Hosts(self.clock, perceiver_line=None, journal="unreadable")
        hosts.journal = "unreadable"
        return hosts

    def test_an_unreadable_journal_accepts_each_restart_on_config_and_first_fact(self):
        """No start line after a restart: healthy, the config holds the arm, the new process's first fact."""
        hosts = self.unreadable()
        code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
        manifest = self.manifest()
        self.assertEqual(manifest["outcome"]["failure"], None)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertTrue(all(w["complete"] for w in manifest["windows"]))
        for window, (restart_at, _) in zip(manifest["windows"], hosts.restarts):
            self.assertEqual(window["perceiver_start"], {"evidence": "fact-log", "journal": "unreadable",
                                                         "config_sha256": SHA, "process_started": restart_at + 0.5,
                                                         "fact_at": harvest.format_time(restart_at + 6)})
            self.assertIsNone(window["perceiver_line"])
        self.assertEqual(manifest["outcome"]["restore"]["controller"], "restarted on the original")
        self.assertEqual(manifest["outcome"]["restore"]["perceiver_start"]["evidence"], "fact-log")
        self.assertEqual(manifest["plan"]["perceiver_evidence"]["source"], "fact-log")
        self.assertTrue(manifest["plan"]["settings"]["allow_unreadable_journal"])
        # The occupancy edges live in the same journal: the run restores but cannot pass.
        self.assertEqual((code, self.done(lines)["verdict"]), (1, "INSUFFICIENT"))

    def test_a_fact_whose_process_start_is_unknown_is_unattributed(self):
        """/proc unread after a restart: the first fact after the restart call may be the old process's."""
        hosts = self.unreadable()
        hosts.unattributed = {2}
        code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
        manifest = self.manifest()
        self.assertIsNone(manifest["outcome"]["failure"])
        second = manifest["windows"][1]["perceiver_start"]
        self.assertEqual((second["fact_at"], second["process_started"]), (None, None))
        self.assertEqual(second["unattributed_fact_at"], harvest.format_time(hosts.restarts[1][0] + 6))
        self.assertTrue(second["note"].startswith("perceiver start unattributed: the new controller process's start "
                                                  "(/proc) is unknown"))
        self.assertIn(second["note"], manifest["windows"][1]["notes"])
        self.assertIsNotNone(manifest["windows"][0]["perceiver_start"]["fact_at"])

    def test_a_journalctl_that_does_not_finish_after_a_restart_aborts_and_restores(self):
        for number, make in enumerate((lambda: Hosts(self.clock), self.unreadable)):
            self.clock, self.out = Clock(), Path(self.tmp.name) / ("run-%d" % number)
            hosts = make()
            hosts.journal_at = {3: "error"}
            code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
            with self.subTest(journal=hosts.journal):
                self.assertEqual(code, 3)
                manifest = self.manifest()
                self.assertIn("la-live-perceiver unverified after the restart: journalctl on the controller did not "
                              "finish", manifest["outcome"]["failure"])
                self.assertNotIn("perceiver_start", manifest["windows"][2])
                self.assertEqual(manifest["outcome"]["restore"]["controller"], "restarted on the original")
                self.assertEqual((hosts.file, hosts.running), ("original", "original"))
                self.assertIsNone(self.done(lines)["restore_commands"])

    def test_a_readable_journal_without_the_arms_start_line_aborts_and_restores(self):
        """The strict branch: no start line, another arm's, or a refusal after a restart."""
        cases = [({2: None}, "la-live-perceiver did not start at 1s (line: None; controller journal readable)"),
                 ({2: "la-live-perceiver started: interval=4.0s capture=0.5s indeterminate_reprobe=60s"},
                  "la-live-perceiver did not start at 1s (line: 'la-live-perceiver started: interval=4.0s"),
                 ({2: "la-live-perceiver refused: the Lab qwen_live block is enabled"},
                  "la-live-perceiver did not start at 1s (line: 'la-live-perceiver refused:"),
                 ({1: "la-live-perceiver started: interval=1.0s capture=0.5s indeterminate_reprobe=60s"},
                  "la-live-perceiver did not start at 4s")]
        for number, (lines_at, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                self.clock, self.out = Clock(), Path(self.tmp.name) / ("run-%d" % number)
                hosts = Hosts(self.clock)
                hosts.start_lines = lines_at
                code, lines = self.main(hosts, "--apply")
                self.assertEqual(code, 3)
                manifest = self.manifest()
                self.assertIn(expected, manifest["outcome"]["failure"])
                window = manifest["windows"][max(lines_at) - 1]
                self.assertNotIn("perceiver_start", window)
                self.assertEqual(manifest["outcome"]["restore"]["controller"], "restarted on the original")
                self.assertNotIn("perceiver_start", manifest["outcome"]["restore"])  # the journal's start line
                self.assertEqual((hosts.file, hosts.running), ("original", "original"))
                self.assertIsNone(self.done(lines)["restore_commands"])

    def test_a_journal_the_plan_read_never_falls_back_after_a_restart(self):
        """The plan read the start line; a restart whose journal is unreadable or empty now aborts (the window
        could be one where the perceiver logged 'refused'), and a restore that cannot see it is not restored."""
        for access in ("unreadable", "empty"):
            with self.subTest(access=access):
                self.clock, self.out = Clock(), Path(self.tmp.name) / ("run-" + access)
                hosts = Hosts(self.clock)
                hosts.journal_at = {3: access}
                code, lines = self.main(hosts, "--apply")
                self.assertEqual(code, 3)
                manifest = self.manifest()
                self.assertIn("la-live-perceiver did not start at 4s (line: None; controller journal %s; the plan "
                              "read it, so there is no fallback)" % access, manifest["outcome"]["failure"])
                self.assertEqual(manifest["outcome"]["restore"]["controller"], "restarted on the original")
                self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.clock, self.out = Clock(), Path(self.tmp.name) / "run-restore"
        hosts = Hosts(self.clock)
        hosts.journal_at = {3: "unreadable", 4: "unreadable"}  # the restore's restart too
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertTrue(self.manifest()["outcome"]["restore"]["controller"].startswith(
            "NOT restarted: Abort: la-live-perceiver did not start at 4s"))
        self.assertIsNotNone(self.done(lines)["restore_commands"])
        # A plan whose journal is readable but rotated the start line out still requires it after each restart.
        self.clock, self.out = Clock(), Path(self.tmp.name) / "run-rotated"
        hosts = Hosts(self.clock, perceiver_line=None)
        hosts.journal_at = {2: "unreadable"}
        code, _ = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertIn("la-live-perceiver did not start at 1s", self.manifest()["outcome"]["failure"])
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))

    def test_a_vacant_den_after_a_restart_is_recorded_not_aborted(self):
        hosts = self.unreadable()
        hosts.vacant = {3, 7}  # window 3, and the restore's restart
        code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
        manifest = self.manifest()
        self.assertIsNone(manifest["outcome"]["failure"])
        self.assertEqual(len(manifest["windows"]), 6)
        third = manifest["windows"][2]
        self.assertIsNone(third["perceiver_start"]["fact_at"])
        self.assertTrue(third["perceiver_start"]["note"].startswith("perceiver start unverified (vacant)"))
        self.assertIn(third["perceiver_start"]["note"], third["notes"])
        self.assertTrue(all("note" not in w["perceiver_start"] for i, w in enumerate(manifest["windows"]) if i != 2))
        restore = manifest["outcome"]["restore"]
        self.assertEqual(restore["controller"], "restarted on the original")
        self.assertTrue(restore["perceiver_start"]["note"].startswith("perceiver start unverified (vacant)"))
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertIsNone(self.done(lines)["restore_commands"])
        self.assertIn("- Window 3: perceiver start unverified (vacant)",
                      (self.out / "summary.md").read_text(encoding="utf-8"))

    def test_an_unreadable_journal_and_a_config_without_the_arm_aborts_and_restores(self):
        hosts = self.unreadable()
        hosts.config_shows = {2: BASELINE}  # the candidate restart sees the baseline
        code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
        self.assertEqual(code, 3)
        manifest = self.manifest()
        self.assertIn("la-live-perceiver unverified after the restart: the controller journal is unreadable and the "
                      "live config does not hold this arm", manifest["outcome"]["failure"])
        self.assertEqual(manifest["outcome"]["restore"]["file"], "restored")
        self.assertEqual(manifest["outcome"]["restore"]["controller"], "restarted on the original")
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertIsNone(self.done(lines)["restore_commands"])
        # A restore restart whose config does not hold the original is never taken as restored.
        self.clock, self.out = Clock(), Path(self.tmp.name) / "run-2"
        hosts = self.unreadable()
        hosts.config_shows = {2: BASELINE, 3: CANDIDATE}
        code, lines = self.main(hosts, "--apply", "--allow-unreadable-journal")
        self.assertEqual(code, 3)
        self.assertTrue(self.manifest()["outcome"]["restore"]["controller"].startswith(
            "NOT restarted: Abort: la-live-perceiver unverified after the restart"))
        self.assertIsNotNone(self.done(lines)["restore_commands"])

    def test_the_restore_rides_out_a_flaky_link(self):
        hosts = Hosts(self.clock)
        hosts.drop = {"write_arm": {3, 4}, "restart": {3}, "stop_probe": {1}}  # the restore's first attempts
        code, lines = self.main(hosts, "--apply", interrupt_at=6)
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertEqual(self.manifest()["outcome"]["restore"], {"file": "restored",
                                                                 "controller": "restarted on the original"})
        retries = [json.loads(line) for line in lines if '"retry"' in line]
        self.assertEqual([r["what"] for r in retries], ["write the original live config"] * 2 +
                         ["restart the controller on the original", "stop the voice probe"])
        self.assertEqual([r["wait_s"] for r in retries], [15, 30, 15, 15])
        self.assertIsNone(self.done(lines)["restore_commands"])

    def test_a_restore_that_never_gets_through_says_so(self):
        hosts = Hosts(self.clock)
        hosts.drop = {"write_arm": set(range(3, 20))}
        code, lines = self.main(hosts, "--apply", interrupt_at=6)
        self.assertEqual(code, 3)
        self.assertEqual(len(hosts.params["write_arm"]), 2 + 5)
        self.assertTrue(self.manifest()["outcome"]["restore"]["file"].startswith(
            "NOT restored: remote step write_arm ended without a verdict"))
        self.assertIsNotNone(self.done(lines)["restore_commands"])

    def test_unanswered_status_checks_are_noted_then_abort(self):
        hosts = Hosts(self.clock)
        hosts.drop = {"status": {1, 2}}
        code, _ = self.main(hosts, "--apply")
        self.assertEqual(code, 0)
        notes = self.manifest()["windows"][0]["notes"]
        self.assertEqual(len([n for n in notes if n.startswith("controller status unread")]), 2)
        self.clock, self.out = Clock(), Path(self.tmp.name) / "run-2"
        hosts = Hosts(self.clock)
        hosts.drop = {"status": {1, 2, 3}}
        code, _ = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertIn("3 controller status checks in a row", self.manifest()["outcome"]["failure"])

    def test_a_manifest_that_cannot_be_saved_never_skips_the_restore(self):
        """Antivirus holding manifest.json, or a full disk: os.replace raises on every later save."""
        hosts = Hosts(self.clock)
        real, calls = cadence_ab.write_json, []

        def flaky(path, value):
            calls.append(path)
            if Path(path).name == "manifest.json" and len(calls) > 1:
                raise PermissionError(13, "Access is denied", str(path))
            return real(path, value)
        with unittest.mock.patch.object(cadence_ab, "write_json", flaky):
            code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 0)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertIn("stop_probe", self.steps(hosts))
        done = self.done(lines)
        self.assertEqual(done["verdict"], "PASS")
        self.assertIn("PermissionError", done["manifest_error"])
        # The analysis used the manifest in memory, not the stale file.
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(len(summary["windows"]), 6)

    def test_an_unwritable_run_directory_changes_nothing(self):
        hosts = Hosts(self.clock)
        with unittest.mock.patch.object(cadence_ab, "write_json", side_effect=PermissionError(13, "denied")):
            code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual(self.steps(hosts), ["preflight", "check", "host_health"])
        self.assertEqual(json.loads(lines[-1])["changed"], "nothing")

    def test_the_first_ctrl_c_ignores_every_later_one(self):
        previous = signal.getsignal(signal.SIGINT)
        try:
            with self.assertRaises(KeyboardInterrupt):
                cadence_ab.interrupt_once(signal.SIGINT, None)
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        finally:
            signal.signal(signal.SIGINT, previous)

    def test_too_few_camera_questions_is_insufficient_not_pass(self):
        hosts = Hosts(self.clock, World(camera_every=0))
        code, _ = self.main(hosts, "--apply", "--keep-candidate-on-pass")
        self.assertEqual(code, 1)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "INSUFFICIENT")
        self.assertTrue(summary["guard"][1][3].startswith(report.NO_DATA))

    def test_a_queue_busy_share_over_30_percent_fails(self):
        hosts = Hosts(self.clock, World(busy_every=3))
        code, _ = self.main(hosts, "--apply")
        self.assertEqual(code, 1)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["guard"][2][3], report.FAILS)

    def test_keep_candidate_only_after_a_pass(self):
        hosts = Hosts(self.clock)
        code, lines = self.main(hosts, "--apply", "--keep-candidate-on-pass")
        self.assertEqual(code, 0)
        self.assertEqual((hosts.file, hosts.running), ("candidate", "candidate"))
        self.assertEqual(len(hosts.restarts), 6)  # the last window was the candidate: no extra restart
        self.assertEqual(self.manifest()["outcome"]["kept"], "kept; the controller already runs it")
        self.assertEqual(self.steps(hosts)[-4:], ["stop_probe", "collect", "journalctl", "cat"])

    def test_keep_after_a_baseline_last_window_switches_once(self):
        hosts = Hosts(self.clock)
        code, _ = self.main(hosts, "--apply", "--keep-candidate-on-pass", "--windows", "5")
        self.assertEqual(code, 0)
        self.assertEqual((hosts.file, hosts.running), ("candidate", "candidate"))
        self.assertEqual([arm for _, arm in hosts.restarts][-2:], ["baseline", "candidate"])

    def test_ctrl_c_mid_candidate_restores_and_restarts(self):
        hosts = Hosts(self.clock)
        code, lines = self.main(hosts, "--apply", interrupt_at=6)
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        manifest = self.manifest()
        self.assertEqual(manifest["outcome"]["failure"], "interrupted (Ctrl-C)")
        self.assertEqual([w["arm"] for w in manifest["windows"]], ["baseline", "candidate"])
        self.assertFalse(manifest["windows"][1]["complete"])
        self.assertIn("partial: interrupted (Ctrl-C)", manifest["windows"][1]["notes"])
        self.assertEqual(self.done(lines)["failure"], "interrupted (Ctrl-C)")
        self.assertIsNone(self.done(lines)["restore_commands"])

    def test_an_unhealthy_restart_aborts_and_restores(self):
        hosts = Hosts(self.clock)
        hosts.unhealthy = {2}
        code, _ = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertIn("controller not healthy after the restart", self.manifest()["outcome"]["failure"])

    def test_a_recording_that_never_ends_leaves_the_file_restored_and_says_restart_by_hand(self):
        hosts = Hosts(self.clock)
        hosts.busy_from_restart = 2  # a recording starts during the first candidate window and never ends
        code, lines = self.main(hosts, "--apply", "--session-wait", "60")
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "candidate"))
        restore = self.manifest()["outcome"]["restore"]
        self.assertEqual(restore["file"], "restored")
        self.assertTrue(restore["controller"].startswith("NOT restarted: Abort: a Lab session, job or worker stayed "
                                                         "active for 900 s"))
        self.assertIn("ssh %s 'systemctl --user restart perception-lab-managed.service'" % cadence_ab.JETSON,
                      self.done(lines)["restore_commands"])

    def test_a_config_changed_by_someone_else_is_never_overwritten(self):
        hosts = Hosts(self.clock)
        hosts.refuse_writes_from = 3
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        restore = self.manifest()["outcome"]["restore"]
        self.assertEqual(restore, {"file": "NOT restored: Refused: live config changed outside this run"})
        self.assertIsNotNone(self.done(lines)["restore_commands"])

    def test_an_unexpected_reply_still_restores(self):
        hosts = Hosts(self.clock)
        status = hosts.status

        def broken(base, unit):
            if len(hosts.restarts) == 2:
                return {"unit": None, "sessions": None, "time": self.clock()}  # AttributeError in measure()
            return status(base, unit)
        hosts.status = broken
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))
        self.assertTrue(self.manifest()["outcome"]["failure"].startswith("AttributeError"))

    def test_the_final_summary_records_the_restore(self):
        hosts = Hosts(self.clock, World(ttft=(0.050, 0.062)))
        self.main(hosts, "--apply", "--keep-candidate-on-pass")
        text = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertIn('"controller": "restarted on the original"', text)
        self.assertIn('"verdict": "FAIL"', text)

    def test_a_refused_backup_changes_nothing(self):
        hosts = Hosts(self.clock)
        hosts.refuse["backup"] = "backup already exists"
        code, lines = self.main(hosts, "--apply")
        self.assertEqual(code, 3)
        self.assertEqual(self.steps(hosts), ["preflight", "check", "host_health", "backup"])
        self.assertEqual(json.loads(lines[-1])["changed"], "nothing")

    def test_a_restart_racing_a_new_recording_waits_and_retries(self):
        hosts = Hosts(self.clock)
        hosts.refuse["restart"] = "a Lab session or job is active"
        code, lines = self.main(hosts, "--apply", "--windows", "2")
        self.assertEqual(self.steps(hosts)[8:15], ["idle", "host_health", "write_arm", "restart", "idle", "restart",
                                                   "health"])
        # Two 10-minute windows complete and restore, but are too short to decide.
        self.assertEqual(code, 1)
        self.assertEqual((self.done(lines)["failure"], self.done(lines)["verdict"]), (None, "INSUFFICIENT"))
        self.assertEqual((hosts.file, hosts.running), ("original", "original"))


def window(arm, start, end, restart):
    return {"index": 1, "arm": arm, "measure_from": start, "measure_until": end, "restart_at": restart,
            "complete": True, "notes": []}


def row(pipeline, metric, value, t, method="synthetic", source=None):
    return {"pipeline": pipeline, "metric": metric, "value_s": value, "t": harvest.format_time(t),
            "source": source or "test:%s" % metric, "method": method, "_t": harvest.parse_time(harvest.format_time(t))}


class AnalysisTests(unittest.TestCase):
    def test_occupied_intervals_follow_one_controller_process(self):
        edges = [(90.0, "on"), (110.0, "on"), (150.0, "off"), (170.0, "on"), (260.0, "off")]
        # Edges before this process started (90 s) are the old process's; it starts vacant.
        self.assertEqual(cadence_ab.occupied(edges, 100.0, 120.0, 250.0), [(120.0, 150.0), (170.0, 250.0)])
        self.assertEqual(cadence_ab.occupied(edges, 100.0, 155.0, 165.0), [])
        self.assertEqual(cadence_ab.occupied([], 100.0, 120.0, 250.0), [])

    def test_queue_busy_leaves_out_the_in_flight_live_and_probe_requests(self):
        rows = [row("qwen_live_la", "total", 1.0, 11.0), row("voice_probe", "e2e", 0.5, 20.5)]
        reads = [(10.5, 3, 0), (12.0, 3, 0), (20.2, 3, 0), (30.0, 2, 1), (40.0, 4, 0)]
        for n, (t, running, waiting) in enumerate(reads, 1):
            rows.append(row("vllm", "num_requests_running", running, t, "voice_probe_vllm_gauge", "vllm-load:x:%d" % n))
            rows.append(row("vllm", "num_requests_waiting", waiting, t, "voice_probe_vllm_gauge", "vllm-load:x:%d" % n))
        data = cadence_ab.Data(rows, [], [], [], [])
        # 10.5: live in flight (3-1); 12.0: busy (3); 20.2: probe in flight (3-1); 30.0: waiting; 40.0: busy.
        self.assertEqual(cadence_ab.queue_busy(data, 0, 100, [(0, 100)], 3), (3, 5))
        self.assertEqual(cadence_ab.queue_busy(data, 0, 100, [(0, 25)], 3), (1, 3))

    def test_thermal_onsets_and_seconds_shed(self):
        lines = [(50.0, "shed", None, 89.5), (80.0, None, 55.0, 80.0), (150.0, "shed", 77.0, None),
                 (160.0, "stop", 79.0, None), (300.0, None, 70.0, None)]
        data = cadence_ab.Data([], [], lines, [], [])
        self.assertEqual(cadence_ab.thermal(data, 60.0, 200.0), (1, 20.0 + 50.0))
        self.assertEqual(cadence_ab.thermal(data, 0.0, 40.0), (0, 0.0))
        # Two lines in the same instant (a rung and its clear) never compare None with a word.
        cadence_ab.Data([], [], [(5.0, "shed", 77.0, None), (5.0, None, 70.0, None)], [], [])

    def test_yield_metric_outages_are_noted_as_a_lower_bound(self):
        live = [row("qwen_live_victoria", "outcome:completed", 1, t) for t in range(12, 100, 4)]
        data = cadence_ab.Data(live, [(5.0, "on")], [], [(20.0, "unavailable: WorkerNotSent"), (30.0, "available")],
                               [])
        item = cadence_ab.window_metrics(data, window("candidate", 10.0, 100.0, 0.0), {"yield_threshold": 3})
        self.assertEqual((item["yield_unavailable"], item["occupied_minutes"]), (1, 1.5))
        self.assertEqual(item["shed_seconds_occupied"], 10.0)
        summary = {"run": "r", "verdict": "INSUFFICIENT", "guard": [], "windows": [item],
                   "arms": {"baseline": cadence_ab.pool([], "baseline"), "candidate": cadence_ab.pool([item],
                                                                                                      "candidate")},
                   "settings": {**self.settings()}, "arm_text": {"baseline": "b", "candidate": "c"}, "notes": []}
        text = cadence_ab.render(summary)
        self.assertIn("- Window 1: 1 'live scene yield metrics unavailable' lines;", text)
        self.assertIn("| candidate | 1 | 1.5 |", text)

    def test_an_on_edge_with_no_live_requests_is_not_occupied(self):
        """on -> unknown logs no edge: a closed gate looks occupied until the next 'off'."""
        live = [row("qwen_live_victoria", "outcome:completed", 1, t) for t in range(120, 300, 4)]
        live += [row("qwen_live_la", "outcome:completed", 1, t + 0.2) for t in range(120, 300, 4)]
        data = cadence_ab.Data(live, [(105.0, "on")], [], [], [])
        item = cadence_ab.window_metrics(data, window("candidate", 110.0, 1310.0, 100.0), {"yield_threshold": 3})
        # Live from 120 to 296; nothing for the other 1014 s of the 'on' interval.
        self.assertEqual(item["edge_occupied_minutes"], 20.0)
        self.assertAlmostEqual(item["occupied_minutes"], (296.2 - 110.0) / 60)
        self.assertEqual(cadence_ab.live_occupied(data, [(110.0, 1310.0)], 3), [(110.0, 296.2)])
        summary = {"run": "r", "verdict": "INSUFFICIENT", "guard": [], "windows": [item],
                   "arms": {"baseline": cadence_ab.pool([], "baseline"),
                            "candidate": cadence_ab.pool([item], "candidate")},
                   "settings": {**self.settings()}, "arm_text": {"baseline": "b", "candidate": "c"}, "notes": []}
        self.assertIn("- Window 1: 16.9 of 20.0 edge-occupied minutes had no live request for over 60 s",
                      cadence_ab.render(summary))
        # Short gaps between live requests (yields, slow answers) stay occupied.
        sparse = cadence_ab.Data([row("qwen_live_la", "outcome:completed", 1, t) for t in range(110, 1310, 50)],
                                 [(105.0, "on")], [], [], [])
        self.assertEqual(cadence_ab.live_occupied(sparse, [(110.0, 1310.0)], 3), [(110.0, 1310.0)])

    def test_a_silence_la_explains_stays_occupied(self):
        """While LA sheds or its queue is busy the gate is open but nothing reaches LA: still occupied,
        so the shedding and queue_busy criteria see it."""
        shed = cadence_ab.Data([], [(5.0, "on")], [(20.0, "shed", None, 89.5), (200.0, None, 55.0, 80.0)], [], [])
        self.assertEqual(cadence_ab.live_occupied(shed, [(10.0, 250.0)], 3), [(10.0, 250.0)])
        rows = []
        for n, t in enumerate(range(12, 240, 2), 1):
            rows.append(row("vllm", "num_requests_running", 3, t, "voice_probe_vllm_gauge", "vllm-load:x:%d" % n))
            rows.append(row("vllm", "num_requests_waiting", 1, t, "voice_probe_vllm_gauge", "vllm-load:x:%d" % n))
        busy = cadence_ab.Data(rows, [(5.0, "on")], [], [], [])
        self.assertEqual(cadence_ab.live_occupied(busy, [(10.0, 250.0)], 3), [(10.0, 250.0)])
        idle = cadence_ab.Data([dict(r, value_s=0) for r in rows], [(5.0, "on")], [], [], [])
        self.assertEqual(cadence_ab.live_occupied(idle, [(10.0, 250.0)], 3), [])

    def test_probe_results_count_every_probe_that_did_not_end_ok(self):
        rows = []
        for n, (outcome, ttft) in enumerate((("ok", .05), ("timeout", None), ("stream_incomplete", .04),
                                              ("http_429", None), ("ok", None)), 1):
            source = "voice-probe:voice-probe.jsonl:%d" % n
            if ttft is not None:
                rows.append(row("voice_probe", "ttft", ttft, 10.0 * n + ttft, source=source))
            rows.append(row("voice_probe", "outcome:" + outcome, 1, 10.0 * n + 1, source=source))
        rows.append(row("voice_probe", "ttft", .03, 70.03, source="voice-probe:voice-probe.jsonl:6"))  # cut off
        found = cadence_ab.probe_results(rows)
        self.assertEqual([ttft for _, ttft in found], [.05, None, None, None, None])

    def settings(self, **changes):
        value = {"min_samples": 20, "guard": {"guard_ratio_max": 1.10, "gpu_temp_p95_max_c": 70,
                                              "queue_busy_share_max": 0.30},
                 "yield_threshold": 3, "min_occupied_minutes": 30}
        value.update(changes)
        return value

    def arms(self, ttft=0.053, camera=0.93, gpu=60, busy=(10, 100), minutes=60.0, failed=0, camera_failed=0,
             live=(900, 3300), shed=(0.0, 0.0)):
        base = {"ttft_occupied": [0.050] * 120, "camera": [0.90] * 25, "gpu": [55] * 40, "queue_busy": [5, 100],
                "occupied_minutes": 60.0, "voice_failed_occupied": 0, "camera_failed": 0,
                "facts_occupied": live[0], "live_calls_occupied": live[0], "shed_seconds_occupied": shed[0]}
        cand = {"ttft_occupied": [ttft] * (120 - failed), "camera": [camera] * (25 - camera_failed), "gpu": [gpu] * 40,
                "queue_busy": list(busy), "occupied_minutes": minutes, "voice_failed_occupied": failed,
                "camera_failed": camera_failed, "facts_occupied": live[1], "live_calls_occupied": 0,
                "shed_seconds_occupied": shed[1]}
        return {"baseline": base, "candidate": cand}

    def guard(self, arms):
        return cadence_ab.guard(arms, self.settings(), {"baseline": BASELINE, "candidate": CANDIDATE})

    def test_guard_verdicts(self):
        self.assertEqual(self.guard(self.arms())[1], "PASS")
        self.assertEqual(self.guard(self.arms(ttft=0.0551))[1], "FAIL")
        self.assertEqual(self.guard(self.arms(camera=1.0))[1], "FAIL")
        self.assertEqual(self.guard(self.arms(busy=(30, 100)))[1], "FAIL")
        self.assertEqual(self.guard(self.arms(gpu=71))[1], "FAIL")
        self.assertEqual(self.guard(self.arms(minutes=12.0))[1], "INSUFFICIENT")
        self.assertEqual(self.guard(self.arms(busy=(0, 10)))[1], "INSUFFICIENT")
        short = self.arms()
        short["candidate"]["camera"] = [0.9] * 19
        rows, verdict = self.guard(short)
        self.assertEqual((verdict, rows[1][3]), ("INSUFFICIENT", "insufficient data (needs 20 samples per arm)"))
        # A failure decides even when another criterion lacks data.
        short["candidate"]["gpu"] = [75] * 40
        self.assertEqual(self.guard(short)[1], "FAIL")

    def test_failed_requests_count_as_unbounded(self):
        # 10 of 120 candidate probes failed; the 110 that answered are within 1.10x.
        rows, verdict = self.guard(self.arms(failed=10))
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(rows[0][3], report.FAILS)
        self.assertEqual(rows[0][2], "failed (n=120, 10 failed), inf×")
        # 3 of 120 (2.5%) leaves the p95 alone but not the failure share.
        rows, verdict = self.guard(self.arms(failed=3))
        self.assertEqual((rows[0][3], verdict), (report.HOLDS, "FAIL"))
        self.assertEqual(rows[5], ("voice probe failures while occupied: candidate share ≤ baseline + 2 points",
                                   "0.0% (n=120)", "2.5% (n=120)", report.FAILS))
        self.assertEqual(self.guard(self.arms(failed=2))[1], "PASS")
        # Camera: 2 failed of 25 make the p95 a failure.
        rows, verdict = self.guard(self.arms(camera_failed=2))
        self.assertEqual((rows[1][3], verdict), (report.FAILS, "FAIL"))
        # A baseline whose own p95 is a failure cannot judge the candidate.
        arms = self.arms()
        arms["baseline"]["voice_failed_occupied"] = 10
        rows, verdict = self.guard(arms)
        self.assertEqual((rows[0][3], verdict), ("insufficient data (the baseline p95 is a failed request)",
                                                 "INSUFFICIENT"))

    def test_the_live_load_must_have_run(self):
        # The candidate's 60 per minute (1 s, cap 90): at least 30 completed per occupied minute.
        for live, expected in (((900, 1800), "PASS"), ((900, 1700), "INSUFFICIENT"), ((0, 3300), "INSUFFICIENT"),
                               ((900, 0), "INSUFFICIENT")):
            with self.subTest(live=live):
                self.assertEqual(self.guard(self.arms(live=live))[1], expected)
        # LA's own completed lines count when the Jetson's fact log is missing.
        arms = self.arms(live=(900, 0))
        arms["candidate"]["live_calls_occupied"] = 3300
        self.assertEqual(self.guard(arms)[1], "PASS")

    def test_la_shedding_makes_a_window_insufficient(self):
        self.assertEqual(self.guard(self.arms(shed=(0.0, 360.0)))[1], "PASS")  # 10% of 60 min
        rows, verdict = self.guard(self.arms(shed=(0.0, 400.0)))
        self.assertEqual((rows[-1][3], verdict), ("insufficient data (LA held the live load back)", "INSUFFICIENT"))
        self.assertEqual(self.guard(self.arms(shed=(500.0, 0.0)))[1], "INSUFFICIENT")

    def test_a_torn_probe_line_is_skipped_and_noted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice-probe.jsonl"
            good = {k: v for k, v in row("voice_probe", "ttft", .05, T0).items() if k != "_t"}
            path.write_text(json.dumps(good) + "\n" + '{"pipeline": "voice_pro', encoding="utf-8")
            notes = []
            self.assertEqual(len(cadence_ab.load_samples(path, notes)), 1)
            self.assertEqual(notes, ["voice-probe.jsonl:2: not a sample; skipped"])


class RemoteLibTests(unittest.TestCase):
    """The Jetson steps, run locally on temporary files."""

    def setUp(self):
        self.lib = {}
        exec(compile(cadence_ab.JETSON_LIB, "cadence-remote", "exec"), self.lib)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "managed-live.json")
        self.config = {"schema_version": 1, "occupancy_reader": {"origin": "https://jetson.example.ts.net:10001",
                                                                 "credential_file": "/x/ha.secret", "hold_seconds": 10},
                       "la_live_perceiver": {"enabled": True, "transport_file": "/x/t.json",
                                             "artifact_sha256": "b" * 64, "runtime_revision": "r1",
                                             "fact_log_directory": "/x/facts"},
                       "qwen_live": {"enabled": False}}
        self.raw = (json.dumps(self.config) + "\n").encode()
        self.write(self.path, self.raw)
        self.sha = self.lib["sha"](self.raw)

    def write(self, path, data):
        with open(path, "wb") as out:
            out.write(data)
        os.chmod(path, 0o600)

    def test_state_reports_the_effective_cadence_and_never_the_file(self):
        state = self.lib["live_state"](self.path)
        self.assertEqual(state["cadence"], {"interval_seconds": 4.0, "max_per_minute": 15, "hold_seconds": 10})
        self.assertEqual(state["sha256"], self.sha)
        self.assertNotIn("transport_file", json.dumps(state))
        self.assertNotIn("credential_file", json.dumps(state))

    def test_the_candidate_sets_only_the_three_keys(self):
        candidate = json.loads(self.lib["arm_bytes"](self.raw, CANDIDATE))
        expected = json.loads(self.raw)
        expected["la_live_perceiver"].update(interval_seconds=1.0, max_per_minute=90)
        expected["occupancy_reader"]["hold_seconds"] = 5.0
        self.assertEqual(candidate, expected)
        self.assertIs(type(candidate["la_live_perceiver"]["max_per_minute"]), int)
        self.assertEqual(self.lib["arm_bytes"](self.raw, None), self.raw)
        for bad in (dict(CANDIDATE, interval_seconds=0.4), dict(CANDIDATE, max_per_minute=90.0),
                    dict(CANDIDATE, hold_seconds=16)):
            with self.assertRaises(self.lib["Refused"]):
                self.lib["arm_bytes"](self.raw, bad)

    def test_backup_write_and_restore(self):
        backup = self.path + ".before-cadence-ab-x"
        self.assertEqual(self.lib["backup"](self.path, backup, self.sha), {"backup": backup, "sha256": self.sha})
        with self.assertRaisesRegex(self.lib["Refused"], "backup already exists"):
            self.lib["backup"](self.path, backup, self.sha)
        write = self.lib["write_arm"]
        reply = write(self.path, backup, self.sha, CANDIDATE, CANDIDATE)
        self.assertTrue(reply["written"])
        self.assertEqual(json.loads(Path(self.path).read_bytes())["la_live_perceiver"]["interval_seconds"], 1.0)
        self.assertFalse(write(self.path, backup, self.sha, CANDIDATE, CANDIDATE)["written"])
        self.assertTrue(write(self.path, backup, self.sha, None, CANDIDATE)["written"])
        self.assertEqual(Path(self.path).read_bytes(), self.raw)
        self.assertFalse(os.path.exists(self.path + ".cadence-ab.staged"))
        # Anyone else's edit is never overwritten.
        self.write(self.path, self.raw.replace(b'"r1"', b'"r2"'))
        with self.assertRaisesRegex(self.lib["Refused"], "changed outside this run"):
            write(self.path, backup, self.sha, None, CANDIDATE)
        with self.assertRaisesRegex(self.lib["Refused"], "changed since the plan"):
            self.lib["backup"](self.path, backup + "2", self.sha)

    @unittest.skipUnless(os.name == "posix", "POSIX modes")
    def test_the_private_file_rule(self):
        os.chmod(self.path, 0o640)
        with self.assertRaisesRegex(self.lib["Refused"], "group or other accessible"):
            self.lib["live_state"](self.path)

    def test_fact_rows_carry_times_and_outcome_only(self):
        facts = os.path.join(self.tmp.name, "facts")
        os.mkdir(facts)
        rows = [{"schema": "la-fact-log-1", "source_id": "la_qwen_den_oak", "request_id": "r%d" % i,
                 "observed_at": harvest.format_time(T0 + i - 1.3), "sent_at": harvest.format_time(T0 + i - 0.9),
                 "available_at": harvest.format_time(T0 + i), "outcome": "completed", "latency_s": 1.3,
                 "observation": {"facts": {"activity": "typing"}}} for i in range(-5, 5)]
        with open(os.path.join(facts, "la-fact-log-1-2026-10-09.jsonl"), "w") as out:
            out.write("".join(json.dumps(r) + "\n" for r in rows) + "{torn")
        found = self.lib["fact_rows"](facts, T0, T0 + 3)
        self.assertEqual([r["available_at"] for r in found], [harvest.format_time(T0 + i) for i in range(4)])
        self.assertEqual(set(found[0]), {"schema", "source_id", "observed_at", "sent_at", "available_at", "outcome",
                                         "latency_s"})
        self.assertNotIn("typing", json.dumps(found))
        self.assertEqual(self.lib["newest_fact"](facts), harvest.format_time(T0 + 4))

    def jetson_lib(self, **stubs):
        lib = {}
        exec(compile(cadence_ab.JETSON_LIB, "cadence-remote", "exec"), lib)
        lib.update(stubs)
        return lib

    def test_restart_rechecks_then_restarts_only_this_unit(self):
        run = Run()
        healthy = dict(session_counts=lambda base: {"sessions": {}, "jobs": {}}, unit_show=lambda unit: {"MainPID": "0"},
                       machine=lambda base: {"max_temperature_c": 50.0, "kernel_fault_lines": 0},
                       subprocess=SimpleNamespace(run=run), time=SimpleNamespace(time=lambda: T0))
        lib = self.jetson_lib(**healthy)
        self.assertEqual(lib["restart"](cadence_ab.BASE, cadence_ab.UNIT, cadence_ab.WORKER_UNIT, 80),
                         {"restarted_at": T0})
        self.assertEqual(run.calls, [(["systemctl", "--user", "restart", cadence_ab.UNIT],
                                      {"check": True, "timeout": 120})])
        cases = [(dict(session_counts=lambda base: None), "session state unknown"),
                 (dict(session_counts=lambda base: {"sessions": {"recording": 1}, "jobs": {}}),
                  "a Lab session or job is active"),
                 (dict(session_counts=lambda base: {"sessions": {}, "jobs": {"running": 1}}),
                  "a Lab session or job is active"),
                 (dict(unit_show=lambda unit: {"MainPID": "4711"}), "a Lab worker is running"),
                 (dict(machine=lambda base: {"max_temperature_c": 80.0, "kernel_fault_lines": 0}),
                  "controller host temperature is high"),
                 (dict(machine=lambda base: {"max_temperature_c": 50.0, "kernel_fault_lines": 1}),
                  "new kernel fault records")]
        for changes, message in cases:
            with self.subTest(message=message):
                run.calls.clear()
                lib = self.jetson_lib(**dict(healthy, **changes))
                with self.assertRaisesRegex(lib["Refused"], message):
                    lib["restart"](cadence_ab.BASE, cadence_ab.UNIT, cadence_ab.WORKER_UNIT, 80)
                self.assertEqual(run.calls, [])

    def test_started_returns_the_first_line_after_the_restart_or_none_at_the_deadline(self):
        clock = Clock(0.0)
        fake_time = SimpleNamespace(monotonic=clock, sleep=lambda s: setattr(clock, "now", clock.now + s),
                                    time=lambda: T0)
        answers = iter(["", "", "la-live-perceiver started: interval=1.0s capture=0.5s indeterminate_reprobe=60s\n"
                                "la-live-perceiver refused: later\n"])
        calls = []

        def journal(unit, *args, timeout=60):
            calls.append((unit,) + args)
            return next(answers)
        lib = self.jetson_lib(journal=journal, time=fake_time)
        self.assertEqual(lib["started"](cadence_ab.UNIT, T0 + 0.7, 90),
                         {"line": "la-live-perceiver started: interval=1.0s capture=0.5s indeterminate_reprobe=60s"})
        self.assertEqual(calls[0], (cadence_ab.UNIT, "--since", "@%d" % int(T0 + 0.7), "-o", "cat", "--grep",
                                    "la-live-perceiver (started|refused)"))
        self.assertEqual(clock.now, 6.0)
        lib = self.jetson_lib(journal=lambda unit, *args, timeout=60: "", time=fake_time)
        clock.now = 0.0
        self.assertEqual(lib["started"](cadence_ab.UNIT, T0, 9), {"line": None})
        self.assertGreaterEqual(clock.now, 9.0)

    def test_the_journal_reads_only_this_users_unit(self):
        run = Run(stdout="x\n")
        lib = self.jetson_lib(subprocess=SimpleNamespace(run=run))
        self.assertEqual(lib["journal"](cadence_ab.UNIT, "--since", "@1"), "x\n")
        self.assertEqual(run.calls, [(["journalctl", "--user", "-u", cadence_ab.UNIT, "--no-pager", "--since", "@1"],
                                      {"capture_output": True, "text": True, "timeout": 60})])

    NO_FILES = (0, "", "No journal files were found.\n")

    def test_a_volatile_journal_is_read_by_user_unit(self):
        """No per-user files in a volatile journal: --user finds none, --user-unit reads the system journal."""
        def reply(argv):
            return self.NO_FILES if argv[1] == "--user" else (0, "y\n", "")
        run = Run(reply=reply)
        lib = self.jetson_lib(subprocess=SimpleNamespace(run=run))
        self.assertEqual(lib["journal"](cadence_ab.UNIT, "--since", "@1"), "y\n")
        self.assertEqual([argv for argv, _ in run.calls], [
            ["journalctl", "--user", "-u", cadence_ab.UNIT, "--no-pager", "--since", "@1"],
            ["journalctl", "--user-unit", cadence_ab.UNIT, "--no-pager", "--since", "@1"]])

    def test_journal_access(self):
        errors = cadence_ab.subprocess.SubprocessError

        def lib(reply):
            return self.jetson_lib(subprocess=SimpleNamespace(run=Run(reply=reply), SubprocessError=errors))
        hint = "Hint: You are currently not seeing messages from other users and the system.\n"
        cases = [(lambda argv: (0, "Oct 09 12:00:00 victoria python3[77]: x\n", ""), "readable"),
                 (lambda argv: (0, "Oct 09 12:00:00 victoria python3[77]: x\n", hint) if argv[1] != "--user"
                  else self.NO_FILES, "readable"),  # own files absent, the system journal readable
                 (lambda argv: self.NO_FILES, "unreadable"),  # the 2026-10-09 dry run
                 (lambda argv: (0, "-- No entries --\n", hint) if argv[1] != "--user" else self.NO_FILES,
                  "unreadable"),
                 (lambda argv: (0, "-- No entries --\n", ""), "empty")]
        for reply, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(lib(reply)["journal_access"](cadence_ab.UNIT), expected)

        def raising(error):
            def run(argv, **kwargs):
                raise error
            return self.jetson_lib(subprocess=SimpleNamespace(run=run, SubprocessError=errors))
        self.assertEqual(raising(FileNotFoundError("journalctl"))["journal_access"](cadence_ab.UNIT), "unreadable")
        # A journalctl that does not finish (or cannot start for another reason) decides nothing.
        timeout = cadence_ab.subprocess.TimeoutExpired(["journalctl"], 60)
        self.assertEqual(raising(timeout)["journal_access"](cadence_ab.UNIT), "error")
        self.assertEqual(raising(BlockingIOError(11, "Resource temporarily unavailable"))["journal_access"](
            cadence_ab.UNIT), "error")

    @unittest.skipUnless(os.path.exists("/proc/self/stat"), "Linux /proc")
    def test_process_started(self):
        started = self.lib["process_started"](os.getpid())
        self.assertLess(started, time.time() + 2)  # btime is whole seconds
        self.assertGreater(started, time.time() - 3600)
        self.assertIsNone(self.lib["process_started"]("0"))

    def facts_dir(self, times, day="2026-10-09"):
        facts = os.path.join(self.tmp.name, "facts")
        os.makedirs(facts, exist_ok=True)
        with open(os.path.join(facts, "la-fact-log-1-%s.jsonl" % day), "a") as out:
            out.write("".join(json.dumps({"schema": "la-fact-log-1", "available_at": harvest.format_time(t),
                                          "outcome": "completed", "observation": {"facts": {"x": "words"}}}) + "\n"
                              for t in times))
        return facts

    def test_first_fact_after_a_moment(self):
        facts = self.facts_dir([T0 - 8, T0 - 4, T0 + 3, T0 + 7])
        self.assertEqual(self.lib["first_fact"](facts, T0), harvest.format_time(T0 + 3))
        self.assertIsNone(self.lib["first_fact"](facts, T0 + 8))
        self.assertIsNone(self.lib["first_fact"](None, T0))
        # Across UTC midnight: the first line after since may be in the older of the two newest files.
        midnight = 1791590400.0  # 2026-10-10T00:00:00Z
        facts = self.facts_dir([midnight - 2], "2026-10-09")
        self.facts_dir([midnight + 2], "2026-10-10")
        self.assertEqual(self.lib["first_fact"](facts, midnight - 5), harvest.format_time(midnight - 2))

    def test_started_without_a_readable_journal_reports_config_and_first_fact(self):
        clock = Clock(0.0)
        fake_time = SimpleNamespace(monotonic=clock, sleep=lambda s: setattr(clock, "now", clock.now + s),
                                    time=lambda: T0)
        facts = self.facts_dir([T0 - 1.0, T0 + 4.0])
        journals = []

        def lib(access, begin=None):
            return self.jetson_lib(journal_access=lambda unit: access, time=fake_time,
                                   journal=lambda unit, *args, timeout=60: journals.append(args) or "",
                                   unit_show=lambda unit: {"MainPID": "4243"}, process_started=lambda pid: begin)
        reply = lib("unreadable")["started"](cadence_ab.UNIT, T0 - 2.0, 90, self.path, facts)
        self.assertEqual(reply, {"journal": "unreadable", "line": None, "fact": harvest.format_time(T0 - 1.0),
                                 "since": T0 - 2.0, "process_started": None, "enabled": True, "sha256": self.sha,
                                 "cadence": {"interval_seconds": 4.0, "max_per_minute": 15, "hold_seconds": 10}})
        self.assertEqual((journals, clock.now), ([], 0.0))  # the journal is never polled
        # The new process's start, not the restart call: the old process's last fact (a cancelled request
        # it wrote while stopping, just before /proc's start plus its slack) is not evidence.
        self.facts_dir([T0 + 1.0])
        reply = lib("unreadable", begin=T0 + 0.5)["started"](cadence_ab.UNIT, T0 - 2.0, 90, self.path, facts)
        self.assertEqual((reply["fact"], reply["since"], reply["process_started"]),
                         (harvest.format_time(T0 + 4.0), T0 + 0.5 + cadence_ab.PROCESS_START_SLACK_S, T0 + 0.5))
        self.assertEqual(self.lib["PROCESS_START_SLACK_S"], cadence_ab.PROCESS_START_SLACK_S)
        # A vacant den: no fact by the deadline.
        reply = lib("empty")["started"](cadence_ab.UNIT, T0 + 5.0, 9, self.path, facts)
        self.assertEqual((reply["journal"], reply["fact"], reply["enabled"]), ("empty", None, True))
        self.assertGreaterEqual(clock.now, 9.0)
        # A readable journal: the start line, as before, now with the access it found.
        lines = "la-live-perceiver started: interval=4.0s capture=0.5s indeterminate_reprobe=60s\n"
        readable = self.jetson_lib(journal_access=lambda unit: "readable", time=fake_time,
                                   journal=lambda unit, *args, timeout=60: lines)
        self.assertEqual(readable["started"](cadence_ab.UNIT, T0, 90, self.path, facts),
                         {"journal": "readable", "line": lines.strip()})
        readable["journal"] = lambda unit, *args, timeout=60: "-- No entries --\n"
        clock.now = 0.0
        self.assertEqual(readable["started"](cadence_ab.UNIT, T0, 9, self.path, facts),
                         {"journal": "readable", "line": None})

    def test_started_never_falls_back_when_the_plan_read_the_journal(self):
        clock = Clock(0.0)
        fake_time = SimpleNamespace(monotonic=clock, sleep=lambda s: setattr(clock, "now", clock.now + s),
                                    time=lambda: T0)
        facts = self.facts_dir([T0 + 4.0])
        polls = []

        def lib(access, answers=("",)):
            answers = iter(answers)
            return self.jetson_lib(journal_access=lambda unit: access, time=fake_time,
                                   journal=lambda unit, *args, timeout=60: polls.append(args) or next(answers, ""),
                                   unit_show=lambda unit: {"MainPID": "4243"}, process_started=lambda pid: T0)
        # A journal that stopped reading, or journalctl not finishing: no line, no fact, at once.
        for access, journal_only in (("unreadable", True), ("error", True), ("error", False)):
            with self.subTest(access=access, journal_only=journal_only):
                reply = lib(access)["started"](cadence_ab.UNIT, T0, 90, self.path, facts, journal_only)
                self.assertEqual((reply, polls, clock.now), ({"journal": access, "line": None}, [], 0.0))
        # Empty now (or still): the journal is polled for the line to the deadline, never the fact log.
        reply = lib("empty")["started"](cadence_ab.UNIT, T0, 9, self.path, facts, True)
        self.assertEqual(reply, {"journal": "empty", "line": None})
        self.assertGreaterEqual(len(polls), 3)
        self.assertGreaterEqual(clock.now, 9.0)
        clock.now = 0.0
        line = "la-live-perceiver started: interval=1.0s capture=0.5s indeterminate_reprobe=60s"
        reply = lib("empty", ("", line + "\n"))["started"](cadence_ab.UNIT, T0, 90, self.path, facts, True)
        self.assertEqual(reply, {"journal": "empty", "line": line})

    def test_preflight_reports_journal_access_process_start_and_config_mtime(self):
        show = {"ActiveState": "active", "SubState": "running", "MainPID": "4242", "NRestarts": "0", "ExecStart": "x"}
        os.utime(self.path, (T0 - 100, T0 - 100))
        lib = self.jetson_lib(unit_show=lambda unit: show, unit_sources=lambda s: [],
                              working_directories=lambda files: [], running_directory=lambda pid: None,
                              application=lambda e: {}, session_counts=lambda base: None,
                              machine=lambda base: {}, journal=lambda unit, *args, timeout=60: "-- No entries --\n",
                              journal_access=lambda unit: "unreadable",
                              process_started=lambda pid: T0 - 3600 if pid == "4242" else None)
        reply = lib["preflight"](cadence_ab.BASE, cadence_ab.UNIT, cadence_ab.WORKER_UNIT, self.path)
        self.assertEqual((reply["journal"], reply["process_started"], reply["perceiver_line"]),
                         ("unreadable", T0 - 3600, None))
        self.assertEqual(reply["live"]["mtime"], T0 - 100)

    def test_preflight_idle_status_and_collect(self):
        stamps = iter([T0, T0 + 4.5] + [T0 + 9] * 5)
        lines = "la-live-perceiver started: interval=4.0s x\n" + "la-live-perceiver stopped: " + "y" * 300 + "\n"
        journals = []

        def journal(unit, *args, timeout=60):
            journals.append((args, timeout))
            return lines if "cat" in args else "occupancy edge: entity=e from=unknown to=on observed_at=1.000\n"
        show = {"ActiveState": "active", "SubState": "running", "MainPID": "4242", "NRestarts": "0", "ExecStart": "x"}
        lib = self.jetson_lib(unit_show=lambda unit: dict(show, MainPID="0") if unit == cadence_ab.WORKER_UNIT else show,
                              unit_sources=lambda s: ["/u.service"],
                              working_directories=lambda files: [{"file": files[0], "value": WORKING}],
                              running_directory=lambda pid: WORKING, application=lambda e: {"host": "h", "port": "1"},
                              session_counts=lambda base: {"sessions": {}, "jobs": {}},
                              machine=lambda base: {"max_temperature_c": 50.0}, journal=journal,
                              time=SimpleNamespace(time=lambda: next(stamps)))
        reply = lib["preflight"](cadence_ab.BASE, cadence_ab.UNIT, cadence_ab.WORKER_UNIT, self.path)
        self.assertEqual((reply["started"], reply["time"]), (T0, T0 + 4.5))
        self.assertEqual(reply["perceiver_line"], ("la-live-perceiver stopped: " + "y" * 300)[:200])
        self.assertEqual(journals[0], (("-b", "-o", "cat", "--grep", "la-live-perceiver (started|refused|stopped)"), 60))
        self.assertEqual((reply["worker_main_pid"], reply["working_directories"], reply["live"]["sha256"]),
                         ("0", [WORKING], self.sha))
        self.assertEqual(lib["idle"](cadence_ab.BASE, cadence_ab.WORKER_UNIT)["worker_main_pid"], "0")
        self.assertEqual(lib["status"](cadence_ab.BASE, cadence_ab.UNIT)["unit"]["MainPID"], "4242")
        facts = os.path.join(self.tmp.name, "facts")
        os.mkdir(facts)
        lib["time"] = SimpleNamespace(time=lambda: T0)
        reply = lib["collect"](cadence_ab.UNIT, T0 - 100.5, T0, facts)
        self.assertEqual(reply["facts"], [])
        self.assertEqual(journals[-1], (("--since", "@%d" % int(T0 - 100.5), "--until", "@%d" % (int(T0) + 1), "-o",
                                         "short-iso-precise", "--grep",
                                         "occupancy edge|occupancy reader|la-live-perceiver"), 120))

    def test_remote_scripts_are_python_310(self):
        for lib, step in ((cadence_ab.JETSON_LIB, "preflight"), (cadence_ab.LA_LIB, "start_probe"),
                          (cadence_ab.LA_LIB, "host_health")):
            ast.parse(cadence_ab.remote_script(lib, step, {"a": 1}), feature_version=(3, 10))
        ast.parse(Path(cadence_ab.__file__).read_text(encoding="utf-8"), feature_version=(3, 10))

    def test_the_deploy_tool_steps_reused(self):
        lib = cadence_ab.DEPLOY.LIB
        self.assertEqual(list(inspect.signature(lib["health"]).parameters), ["unit", "new", "host", "port", "seconds"])
        self.assertEqual(list(inspect.signature(lib["session_counts"]).parameters), ["base"])
        self.assertEqual(cadence_ab.DEPLOY.MAX_TEMPERATURE_C, 80)
        self.assertEqual(cadence_ab.JETSON, "engineeredlightingllm@192.168.1.168")


class LaLibTests(unittest.TestCase):
    """The LA steps, run locally on temporary files with systemctl and systemd-run stubbed."""

    UNIT = "la-voice-probe-20261009t120000z"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def lib(self, **stubs):
        lib = {}
        exec(compile(cadence_ab.LA_LIB, "la-remote", "exec"), lib)
        lib.update(stubs)
        return lib

    def test_install_probe_checks_the_upload_and_never_replaces_a_different_probe(self):
        lib = self.lib()
        upload, target = os.path.join(self.dir, "p.py.upload"), os.path.join(self.dir, "p.py")
        sha = hashlib.sha256(b"probe").hexdigest()
        Path(upload).write_bytes(b"probe")
        with self.assertRaisesRegex(lib["Refused"], "uploaded probe checksum mismatch"):
            lib["install_probe"](upload, target, "0" * 64)
        self.assertFalse(os.path.exists(upload) or os.path.exists(target))
        Path(upload).write_bytes(b"probe")
        self.assertEqual(lib["install_probe"](upload, target, sha), {"probe": target, "installed": True})
        self.assertEqual((Path(target).read_bytes(), os.path.exists(upload)), (b"probe", False))
        if os.name == "posix":
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
        Path(upload).write_bytes(b"probe")
        self.assertEqual(lib["install_probe"](upload, target, sha), {"probe": target, "installed": False})
        self.assertFalse(os.path.exists(upload))
        Path(target).write_bytes(b"other")
        Path(upload).write_bytes(b"probe")
        with self.assertRaisesRegex(lib["Refused"], "an installed probe with this name differs"):
            lib["install_probe"](upload, target, sha)
        self.assertEqual((Path(target).read_bytes(), os.path.exists(upload)), (b"other", False))

    def test_start_probe_runs_one_bounded_transient_unit(self):
        run = Run()
        states = iter(["inactive", "active"])
        lib = self.lib(subprocess=SimpleNamespace(run=run), _state=lambda unit: next(states),
                       time=SimpleNamespace(sleep=lambda s: None, time=lambda: T0))
        output = os.path.join(self.dir, "voice-probe.jsonl")
        reply = lib["start_probe"](self.UNIT, "/p/voice_probe-x.py", self.dir, ["--url", "http://h:8000"], 4321)
        self.assertEqual(reply, {"unit": self.UNIT, "output": output, "started_at": T0})
        self.assertEqual(run.calls[0][0], [
            "systemd-run", "--user", "--unit", self.UNIT, "--collect", "--quiet", "--working-directory", self.dir,
            "-p", "MemoryMax=128M", "-p", "TasksMax=16", "-p", "CPUQuota=25%", "-p", "RuntimeMaxSec=4321",
            "/usr/bin/python3", "-I", "/p/voice_probe-x.py", "--url", "http://h:8000", "--output", output])
        self.assertEqual(len(run.calls), 1)
        cases = [(["active"], Run(), "probe unit already running"),
                 (["inactive"], Run(returncode=1, stderr="Failed to start\n"), "systemd-run failed: Failed to start"),
                 (["inactive", "failed"], Run(), "probe unit is failed after start")]
        for sequence, run, message in cases:
            with self.subTest(message=message):
                states = iter(sequence)
                lib = self.lib(subprocess=SimpleNamespace(run=run), _state=lambda unit: next(states),
                               time=SimpleNamespace(sleep=lambda s: None, time=lambda: T0))
                with self.assertRaisesRegex(lib["Refused"], message):
                    lib["start_probe"](self.UNIT, "/p/x.py", self.dir, [], 60)
        Path(output).write_text("")
        lib = self.lib(_state=lambda unit: "inactive")
        with self.assertRaisesRegex(lib["Refused"], "probe output already exists"):
            lib["start_probe"](self.UNIT, "/p/x.py", self.dir, [], 60)

    def test_stop_probe(self):
        run = Run()
        lib = self.lib(subprocess=SimpleNamespace(run=run), _state=lambda unit: "inactive")
        self.assertEqual(lib["stop_probe"](self.UNIT), {"state": "inactive"})
        self.assertEqual(run.calls[0][0], ["systemctl", "--user", "stop", self.UNIT])

    def sample(self, pipeline, metric, value, t):
        return json.dumps({"pipeline": pipeline, "metric": metric, "value_s": value, "t": harvest.format_time(t),
                           "source": "x:voice-probe.jsonl:1", "method": "m"}) + "\n"

    def test_probe_ready_waits_for_a_gauge_row_and_an_ok_probe(self):
        output = os.path.join(self.dir, "voice-probe.jsonl")
        clock = Clock(0.0)
        fake_time = SimpleNamespace(monotonic=clock, sleep=lambda s: setattr(clock, "now", clock.now + s),
                                    time=lambda: T0)
        Path(output).write_text(self.sample("vllm", "num_requests_running", 0, T0) +
                                self.sample("voice_probe", "outcome:timeout", 1, T0) + '{"pipeline": "vo')
        lib = self.lib(_state=lambda unit: "active", time=fake_time)
        self.assertEqual(lib["probe_ready"](self.UNIT, output, 45),
                         {"rows": 2, "gauges": 1, "outcomes": 1, "ok": 0, "state": "active"})
        self.assertGreaterEqual(clock.now, 45)
        with open(output, "a") as out:
            out.write("\n" + self.sample("voice_probe", "outcome:ok", 1, T0))
        clock.now = 0.0
        self.assertEqual(lib["probe_ready"](self.UNIT, output, 45)["ok"], 1)
        self.assertEqual(clock.now, 0.0)
        lib = self.lib(_state=lambda unit: "failed", time=fake_time)
        self.assertEqual(lib["probe_ready"](self.UNIT, os.path.join(self.dir, "none.jsonl"), 45),
                         {"rows": 0, "gauges": 0, "outcomes": 0, "ok": 0, "state": "failed"})

    def test_host_health_reads_units_kernel_faults_and_the_probes_own_rows(self):
        def reply(argv):
            if argv[:2] == ["systemctl", "--failed"]:
                return 0, "snap-firefox-8819.mount loaded failed failed Mount unit\n", ""
            if argv[:3] == ["systemctl", "--user", "--failed"]:
                return 0, "", ""
            if argv[0] == "journalctl":
                return 1, "-- No entries --\n", ""
            raise AssertionError(argv)
        run = Run(reply=reply)
        lib = self.lib(subprocess=SimpleNamespace(run=run), _state=lambda unit: "active",
                       time=SimpleNamespace(time=lambda: T0))
        output = os.path.join(self.dir, "voice-probe.jsonl")
        Path(output).write_text(self.sample("la_gpu", "gpu_temp_c", 61.0, T0 - 500) +
                                self.sample("la_cpu", "tctl_c", 71.5, T0 - 10) +
                                self.sample("vllm", "num_requests_waiting", 0, T0 - 2) +
                                self.sample("voice_probe", "outcome:ok", 1, T0 - 7))
        health = lib["host_health"]("MCE|panic", "victoria-camera-pilot.service", self.UNIT, output, 120)
        self.assertEqual(health, {"pilot": "active", "failed_units": ["snap-firefox-8819.mount"],
                                  "failed_units_error": None, "failed_user_units": [], "failed_user_units_error": None,
                                  "kernel_log_readable": True, "kernel_fault_lines": 0,
                                  "time": T0, "probe": "active", "gpu_temp_c": None, "tctl_c": 71.5,
                                  "gauge_age_s": 2.0, "outcome_age_s": 7.0})
        journal = [argv for argv, _ in run.calls if argv[0] == "journalctl"][0]
        self.assertEqual(journal, ["journalctl", "-k", "-b", "--since", "20 minutes ago", "--no-pager", "--grep",
                                   "MCE|panic"])
        self.assertNotIn("-q", journal)  # it would hide the unreadable-journal warning

        def unreadable(argv):
            if argv[0] == "journalctl":
                return 1, "", "Hint: You are currently not seeing messages from other users and the system.\n"
            return 0, "", ""
        lib = self.lib(subprocess=SimpleNamespace(run=Run(reply=unreadable)), _state=lambda unit: "active",
                       time=SimpleNamespace(time=lambda: T0))
        health = lib["host_health"]("MCE", "victoria-camera-pilot.service")
        self.assertEqual((health["kernel_log_readable"], "probe" in health, "gpu_temp_c" in health), (False, False, False))

    def test_host_health_reports_a_listing_of_failed_units_that_fails(self):
        """The pilot lists with check=True: an empty stdout from a failing systemctl is no 'nothing failed'."""
        def reply(argv):
            if argv[0] == "systemctl":
                return 1, "", "Failed to connect to bus: No such file or directory\n"
            return 0, "", ""
        lib = self.lib(subprocess=SimpleNamespace(run=Run(reply=reply)), _state=lambda unit: "active",
                       time=SimpleNamespace(time=lambda: T0))
        health = lib["host_health"]("MCE", "victoria-camera-pilot.service")
        self.assertEqual((health["failed_units"], health["failed_units_error"]),
                         ([], "exit 1: Failed to connect to bus: No such file or directory"))
        self.assertEqual(health["failed_user_units_error"], "exit 1: Failed to connect to bus: No such file or directory")
        self.assertEqual(cadence_ab.la_problems(health, 70), [
            "LA could not list its failed system units (exit 1: Failed to connect to bus: No such file or directory): "
            "the pilot refuses every request while that listing fails"])
        self.assertEqual(cadence_ab.la_warnings(health), [
            "LA could not list its failed user units (exit 1: Failed to connect to bus: No such file or directory); "
            "the pilot never checks them"])


class SourcePinTests(unittest.TestCase):
    """The keys, bounds and lines this tool relies on exist in the controller and pilot sources."""

    @classmethod
    def setUpClass(cls):
        if not LAB.is_dir():
            raise unittest.SkipTest("perception-lab sources not present")
        if str(LAB) not in sys.path:
            sys.path.append(str(LAB))

    def test_the_rewritten_block_passes_the_controller_validators(self):
        import la_live_perceiver
        import occupancy_gate
        import occupancy_reader
        lib = {}
        exec(compile(cadence_ab.JETSON_LIB, "cadence-remote", "exec"), lib)
        config = {"occupancy_reader": {"origin": "https://j.example.ts.net:10001", "credential_file": "/x/s"},
                  "la_live_perceiver": {"enabled": True, "transport_file": "/x/t", "artifact_sha256": "b" * 64,
                                        "runtime_revision": "r1", "fact_log_directory": "/x/f"}}
        for arm in (BASELINE, CANDIDATE):
            value = json.loads(lib["arm_bytes"](json.dumps(config).encode(), arm))
            settings = la_live_perceiver.check_config(value["la_live_perceiver"])
            self.assertEqual((settings["pump"]["interval_seconds"], settings["pump"]["max_per_minute"]),
                             (arm["interval_seconds"], arm["max_per_minute"]))
            self.assertEqual(occupancy_reader.settings(value["occupancy_reader"])["hold_seconds"], arm["hold_seconds"])
        # The defaults an absent key takes, and the bounds this tool checks.
        self.assertEqual(la_live_perceiver.qwen_live.DEFAULTS["interval_seconds"], 4.0)
        self.assertEqual(la_live_perceiver.qwen_live.DEFAULTS["max_per_minute"], 15)
        self.assertEqual(occupancy_reader.DEFAULTS["hold_seconds"], 10.0)
        self.assertEqual(occupancy_reader.BOUNDS["hold_seconds"], cadence_ab.BOUNDS["hold_seconds"])
        pump = la_live_perceiver.qwen_live.pump_config
        pump({"interval_seconds": 0.5, "max_per_minute": 120})
        for bad in ({"interval_seconds": 0.49}, {"max_per_minute": 121}, {"max_per_minute": 90.0}):
            with self.assertRaises(ValueError):
                pump(bad)
        # Why the plan refuses a pump that names its own hold: the controller would refuse to start.
        reader = {"entity": "binary_sensor.den_oak_person_occupancy", "hold_seconds": 5.0, "grace_seconds": 5.0}
        self.assertEqual(occupancy_gate.reader_mismatch(reader, {"hold_seconds": 10}), "hold_seconds")

    def test_journal_lines_parse(self):
        perceiver = (LAB / "la_live_perceiver.py").read_text(encoding="utf-8")
        self.assertIn('"la-live-perceiver started: interval=%.1fs capture=%.1fs indeterminate_reprobe=%ds"', perceiver)
        line = "la-live-perceiver started: interval=%.1fs capture=%.1fs indeterminate_reprobe=%ds" % (1.0, 0.5, 60)
        self.assertTrue(cadence_ab.started_at(line, 1.0))
        self.assertTrue(cadence_ab.started_at("WARNING:perception_lab.la_live:" + line, 1.0))
        self.assertFalse(cadence_ab.started_at(line, 4.0))
        reader = (LAB / "occupancy_reader.py").read_text(encoding="utf-8")
        self.assertIn('"occupancy edge: entity=%s from=%s to=%s observed_at=%.3f"', reader)
        found = cadence_ab.EDGE.search("occupancy edge: entity=%s from=%s to=%s observed_at=%.3f"
                                       % ("binary_sensor.den_oak_person_occupancy", "unknown", "on", T0))
        self.assertEqual((found.group(1), float(found.group(2))), ("on", T0))
        if not SHARED.is_dir():
            self.skipTest("shared-home sources not present")
        pilot = (SHARED / "lab_live_pilot.py").read_text(encoding="utf-8")
        self.assertIn('"live scene thermal_shed %s gpu_c=%s tctl_c=%s"', pilot)
        self.assertIn('"level=" + level if level else "cleared", gpu, tctl', pilot)
        for level in ("level=shed", "level=stop", "cleared"):
            found = cadence_ab.THERMAL.search("live scene thermal_shed %s gpu_c=%s tctl_c=%s" % (level, 77, None))
            self.assertEqual(found.group(1), None if level == "cleared" else level[6:])
        self.assertIn('"live scene yield metrics " + ("available" if failure is None else "unavailable: " + failure)',
                      pilot)
        self.assertIn('"""(429, queue_busy) while vLLM has a waiting request or at least', pilot)
        self.assertIn("counts[\"waiting\"] > 0 or counts[\"running\"] >= self._runtime.yield_running_threshold", pilot)


class PilotPinTests(unittest.TestCase):
    """The LA pilot's failed-unit rule (camera_pilot_service._host_health) is the one this tool applies."""

    def test_the_known_failed_units_are_the_pilots(self):
        source = SHARED / "camera_pilot_service.py"
        if not source.is_file():
            self.skipTest("shared-home sources not present")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        health = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_host_health"]
        self.assertEqual(len(health), 1)
        constants = [node for node in ast.walk(health[0]) if isinstance(node, (ast.Set, ast.List))
                     and node.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts)]
        known = [{e.value for e in node.elts} for node in constants if isinstance(node, ast.Set)]
        self.assertEqual(known, [set(cadence_ab.PILOT_KNOWN_FAILED_UNITS)])
        # The units it lists: the system manager's failed units, never --user; the first column is the name.
        argv = [[e.value for e in node.elts] for node in constants if isinstance(node, ast.List)]
        self.assertIn(["systemctl", "list-units", "--failed", "--plain", "--no-legend"], argv)
        self.assertFalse(any("--user" in a for a in argv))
        # check=True: a listing that fails raises there, so the pilot refuses (la_problems' failed_units_error).
        listing = [node for node in ast.walk(health[0]) if isinstance(node, ast.Call) and node.args
                   and isinstance(node.args[0], ast.List) and [getattr(e, "value", None) for e in node.args[0].elts]
                   == ["systemctl", "list-units", "--failed", "--plain", "--no-legend"]]
        self.assertEqual(len(listing), 1)
        self.assertIn(("check", True), [(k.arg, getattr(k.value, "value", None)) for k in listing[0].keywords])
        text = ast.get_source_segment(source.read_text(encoding="utf-8"), health[0])
        self.assertIn("line.split()[0] not in {", text)
        self.assertIn("raise ValueError('new failed system unit')", text)

    def test_la_lists_the_same_failed_units_the_pilot_does(self):
        """'systemctl --failed' is 'systemctl list-units --failed': list-units is systemctl's default verb."""
        run = Run()
        lib = {}
        exec(compile(cadence_ab.LA_LIB, "la-remote", "exec"), lib)
        lib.update(subprocess=SimpleNamespace(run=run), _state=lambda unit: "active",
                   time=SimpleNamespace(time=lambda: T0))
        lib["host_health"]("MCE", "victoria-camera-pilot.service")
        listed = [argv for argv, _ in run.calls if argv[0] == "systemctl"]
        self.assertEqual(listed, [["systemctl", "--failed", "--no-legend", "--plain"],
                                  ["systemctl", "--user", "--failed", "--no-legend", "--plain"]])


if __name__ == "__main__":
    unittest.main()
