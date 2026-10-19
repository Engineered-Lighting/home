#!/usr/bin/env python3
"""Bounded cadence A/B on the always-on LA live perceiver (plan S1.4 step 3).

Workstation only: Python 3.10+, standard library only. It drives the Jetson
controller (engineeredlightingllm@192.168.1.168) and LA (hav-ubuntu) over ssh
in BatchMode; every remote argument is shell-quoted. Without --apply it
changes nothing: a read-only preflight on both hosts (on LA: the pilot unit,
failed system units outside the pilot's own known set or a listing of them
that fails, new kernel fault records, GPU °C and Tctl; failed user units are
only listed, as the pilot never checks them), then the plan, the guard and the
restore commands; exit 2 on any refusal.

Perceiver liveness: the controller's 'la-live-perceiver started:
interval=<arm>' line when the service account can read the controller's
journal (the stronger evidence). A volatile journal keeps no per-user files,
so without group systemd-journal it reads none; then the fact-log fallback:
managed-live.json holding the arm with la_live_perceiver enabled, and
la-fact-log-1 lines this controller process wrote (a vacant den writes none,
so their absence is a warning, never a refusal). The dry-run JSON names the
evidence used (perceiver_evidence). Either way the plan refuses a
managed-live.json changed after the controller process started (it reads the
file once, at start). A journalctl that does not finish decides nothing: a
refusal in the dry run, an abort after a restart. The occupancy edge lines the
analysis reads are in that journal too, so --apply refuses an unreadable one
unless --allow-unreadable-journal is given (the verdict can then only be
INSUFFICIENT).

--apply:
1. Backs up the Jetson's private managed-live.json once, next to it
   (<file>.before-cadence-ab-<run>, 0600, never overwritten).
2. Installs voice_probe.py on LA (~/la-latency/voice_probe-<sha16>.py,
   checksum-verified) and runs it for the whole run as a transient user unit
   (systemd-run: MemoryMax 128M, TasksMax 16, CPUQuota 25%, RuntimeMaxSec
   covering the worst-case session waits). It reads vLLM's gauges at the
   address the dry-run check resolved, so no docker command runs under those
   limits. The run aborts before any config change unless the probe writes a
   gauge row and an ok voice probe within PROBE_READY_S.
3. For each of --windows alternating windows, baseline first:
   - waits until no Lab session or job is active and no Lab worker runs
     (the deploy tool's own read-only session check,
     tools/deploy-perception-lab-controller.py), at most --session-wait;
   - re-checks LA as in the dry run, plus the probe unit and the freshness of
     its gauge and voice rows (abort when any fails);
   - writes the arm atomically: the baseline is the original bytes; the
     candidate is the original with only la_live_perceiver.interval_seconds,
     la_live_perceiver.max_per_minute and occupancy_reader.hold_seconds set.
     The file must still hold the original or this run's candidate;
   - restarts perception-lab-managed.service (idle, host temperature and
     kernel faults re-checked first), waits for the deploy tool's health
     verdict and for 'la-live-perceiver started: interval=<arm>', required
     whenever the plan read the controller journal (it never falls back
     then); with the plan's journal unreadable, for the config holding the
     arm (else it aborts) and the first la-fact-log-1 line the new process
     wrote, whose time it records; none within START_WAIT_S is recorded as
     'perceiver start unverified (vacant)', not an abort;
   - settles --settle-seconds, then measures --window-minutes (one or two
     unanswered controller status checks in a row are noted, a third aborts).
4. On every exit (completion, error, Ctrl-C) it writes the original bytes back
   and restarts the controller once no Lab session records, then stops the
   probe. The first Ctrl-C ends the windows and every later one is ignored
   until that is done; manifest writes are best effort, so a locked or full
   disk never skips it; a step that ssh ended without a verdict is retried
   with backoff (RETRY_PAUSES_S; the steps are idempotent, a refusal is never
   retried). The candidate stays only with --keep-candidate-on-pass AND a
   PASS. The restore commands are printed before anything changes.
5. Collects the LA pilot journal, the probe JSONL, the controller's occupancy
   and perceiver lines, and the la-fact-log-1 times and outcomes (observation
   words never leave the Jetson). It writes manifest.json, samples.jsonl
   (harvest.py format, for report.py), summary.json and summary.md into
   --out.

--analyze RUN_DIR recomputes the summary from a collected run, offline.

Per window and per arm: voice TTFT p50/p95 (all, and while the den is
occupied) and failed probes, camera-chat backend p95 and failed calls, live
requests per occupied minute, the estimated queue_busy share, thermal_shed
rung changes and seconds shed, and LA GPU °C and Tctl. Occupancy is the
controller's own occupancy edge lines less every stretch of more than
QUIET_GAP_S with no live request reaching LA (no la-fact-log-1 line and no LA
live backend line), unless LA explains the silence (thermal_shed, yield
metrics unavailable, or a busy queue for at least half of it): the controller
logs no edge for on -> unknown, so an 'on' edge alone can count a closed gate
as occupied.

Guard (slo.json decision.D2; plan S1.4 step 3), candidate against baseline:
voice TTFT p95 while occupied and camera-chat backend p95 at most
guard_ratio_max (1.10) x baseline, where a probe or camera call that did not
end ok counts as unbounded; voice probe failures while occupied at most
VOICE_FAILURE_MARGIN above the baseline share; queue_busy share under
queue_busy_share_max (30%) of occupied candidate time; LA GPU p95 at most
gpu_temp_p95_max_c (70 °C); the candidate cadence exercised for at least
--min-occupied-minutes; live load: the candidate at least LIVE_RATE_MIN_SHARE
of its expected rate (min(60 / interval, max_per_minute) completed live
requests per occupied minute) and the baseline above 0; and LA shedding
(thermal_shed plus yield metrics unavailable) at most SHED_SHARE_MAX of
occupied time in both arms. Every statistic needs min_samples_per_statistic
samples. PASS: all hold. FAIL: any does not. Otherwise INSUFFICIENT.

queue_busy is never logged per refusal (LA answers 429 and writes nothing;
the Jetson only counts it in memory), so its share is estimated from vLLM's
gauges sampled on LA every --load-interval: the pilot's own yield rule
(waiting > 0, or running >= --yield-threshold) with the in-flight live
request (from the LA timing lines) and the probe's own request left out,
over occupied candidate time.

Exit: 0 PASS (dry run: a clean plan); 1 FAIL or INSUFFICIENT; 2 refused
before any change; 3 aborted (the restore result is printed) or a restore
(probe stop included) that did not finish.
"""
import argparse
import bisect
import hashlib
import importlib.util
import json
import math
import operator
import os
import posixpath
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import harvest
import report
import voice_probe

HERE = Path(__file__).resolve().parent
DEPLOY_TOOL = HERE.parent / "deploy-perception-lab-controller.py"


def _deploy_tool():
    spec = importlib.util.spec_from_file_location("lab_controller_deploy", DEPLOY_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOY = _deploy_tool()
SSH = tuple(DEPLOY.SSH)
JETSON = DEPLOY.HOST
BASE = DEPLOY.BASE
UNIT = DEPLOY.UNIT
WORKER_UNIT = DEPLOY.WORKER_UNIT
MAX_TEMPERATURE_C = DEPLOY.MAX_TEMPERATURE_C
FAULTS = DEPLOY.LIB["FAULTS"]  # the deploy tool's kernel fault pattern (AGENTS.md's list), reused on LA
LA = "hav-ubuntu"
LIVE_CONFIG = BASE + "/private/managed-live.json"
LA_PILOT_UNIT = "victoria-camera-pilot.service"
VLLM_CONTAINER = "hav-vllm"
PYTHON = "/usr/bin/python3"
MARKER = "la-cadence-ab"
SCHEMA = "la-cadence-ab-1"
# The three keys an arm sets, their controller bounds (qwen_live.pump_config,
# occupancy_reader.BOUNDS) and the controller defaults when a key is absent.
KEYS = ("interval_seconds", "max_per_minute", "hold_seconds")
BOUNDS = {"interval_seconds": (0.5, 10), "max_per_minute": (1, 120), "hold_seconds": (3, 15)}
BASELINE = "4/15/10"
CANDIDATE = "1/90/5"
RESTART_BUDGET_S = 240
CHECK_SECONDS = 300
STATUS_MISSES_MAX = 2  # unanswered status checks in a row a window tolerates
IDLE_POLL_SECONDS = 15
RESTORE_WAIT_MIN_S = 900
RETRY_PAUSES_S = (15, 30, 60, 120)  # 5 attempts over about 4 minutes, plus the attempts' own time
PROBE_READY_S = 45  # two probe slots at the default 15 s interval, each with its 10 s timeout
LA_TCTL_MAX_C = 88  # the pilot's live-Qwen Tctl rung (heat ladder 77 / 79 / 88 °C)
FRESH_S = 120  # a probe temperature older than this is not a current reading
START_WAIT_S = 90  # after a restart: for the perceiver's start line or, without the journal, its first fact
FACT_RECENT_S = 600  # dry run without the controller journal: a newer la-fact-log-1 line shows the perceiver live
# /proc gives a process start up to about a second early (btime is whole seconds, the ticks are
# truncated): a fact or a config write counts as after the start only past this much more.
PROCESS_START_SLACK_S = 2.0
# The LA pilot's exact failed-unit rule (tools/shared-home/home_shared/camera_pilot_service.py
# _host_health): it refuses every request only while 'systemctl list-units --failed --plain
# --no-legend' (system units; never --user) lists a unit outside this set. A source-pin test
# holds the two equal.
PILOT_KNOWN_FAILED_UNITS = frozenset({
    "observer-health.service", "observer-recover.service",
    "home-agent-preference-offhost-20260928.service", "home-agent-preference-offhost-20260928-r2.service"})
SNAP_MOUNT = re.compile(r"snap-.+\.mount")  # left 'not-found failed' by a snapd refresh (runbook 2.1)
# Analysis.
QUIET_GAP_S = 60.0  # longer with no live request reaching LA: not occupied, unless LA explains it
LIVE_RATE_MIN_SHARE = 0.5  # of the candidate's expected completed live requests per occupied minute
SHED_SHARE_MAX = 0.10  # of occupied time with LA shedding live Qwen or its yield metrics unavailable
VOICE_FAILURE_MARGIN = 0.02  # candidate voice probe failure share over the baseline's
PASS, FAIL, INSUFFICIENT = "PASS", "FAIL", "INSUFFICIENT"
RESTORE_NOTE = ("If this tool dies, run these by hand. Restart the controller only when no Lab session records "
                "(a dry run of this tool or of tools/deploy-perception-lab-controller.py reports sessions).")

# Controller lines (stack/services/perception-lab: occupancy_reader.py, la_live_perceiver.py).
EDGE = re.compile(r"occupancy edge: entity=\S+ from=\S+ to=(on|off) observed_at=(\d+(?:\.\d+)?)")
STARTED = re.compile(r"la-live-perceiver started: interval=(\d+(?:\.\d+)?)s")
# LA pilot lines (tools/shared-home/home_shared/lab_live_pilot.py _thermal_level, _yield).
THERMAL = re.compile(r"live scene thermal_shed (?:level=(shed|stop)|cleared) gpu_c=(\S+) tctl_c=(\S+)")
YIELD = re.compile(r"live scene yield metrics (available|unavailable: \S+)")

# Runs on the Jetson (python3.10) after the deploy tool's REMOTE_LIB, whose
# session_counts, unit_show, machine and health it reuses. Replies carry
# digests, the three cadence values, states and times: never the file.
CADENCE_LIB = "PROCESS_START_SLACK_S = %r\n" % PROCESS_START_SLACK_S + r'''
import datetime
KEYS = (("la_live_perceiver", "interval_seconds", 4.0), ("la_live_perceiver", "max_per_minute", 15),
        ("occupancy_reader", "hold_seconds", 10.0))
BOUNDS = {"interval_seconds": (0.5, 10), "max_per_minute": (1, 120), "hold_seconds": (3, 15)}
FACT_FIELDS = ("schema", "source_id", "observed_at", "sent_at", "available_at", "outcome", "latency_s")
def sha(data):
    return hashlib.sha256(data).hexdigest()
def read_private(path):
    """Bytes and mode, held to the controller's private_json rule."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise Refused("live config is not a regular file")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise Refused("live config is not owned by the controller user")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise Refused("live config is group or other accessible")
    with open(path, "rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise Refused("live config exceeds the controller's 64 KiB bound")
    return data, stat.S_IMODE(info.st_mode)
def cadence(value):
    return {key: (value.get(block) if isinstance(value.get(block), dict) else {}).get(key, default)
            for block, key, default in KEYS}
def arm_bytes(original, arm):
    """The original bytes (arm None), or the original with only the three keys set."""
    if arm is None:
        return original
    for key, (low, high) in BOUNDS.items():
        number = arm.get(key)
        if type(number) not in (int, float) or not low <= number <= high:
            raise Refused("cadence %s outside its bound" % key)
    if type(arm["max_per_minute"]) is not int:
        raise Refused("max_per_minute must be an integer")
    value = json.loads(original)
    for block, key, _ in KEYS:
        if not isinstance(value.get(block), dict):
            raise Refused(block + " block missing")
        value[block][key] = arm[key] if key == "max_per_minute" else float(arm[key])
    return (json.dumps(value, indent=2) + "\n").encode()
def live_state(path):
    data, mode = read_private(path)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise Refused("live config is not an object")
    perceiver = value.get("la_live_perceiver")
    pumps = {}
    for name in ("qwen_live", "vjepa_live"):
        if isinstance(value.get(name), dict):
            pumps[name] = {"enabled": value[name].get("enabled"),
                           "occupancy_hold_seconds": value[name].get("occupancy_hold_seconds")}
    return {"sha256": sha(data), "mode": "%04o" % mode, "cadence": cadence(value),
            "perceiver": {"enabled": perceiver.get("enabled"),
                          "fact_log_directory": perceiver.get("fact_log_directory")} if isinstance(perceiver, dict) else None,
            "reader": isinstance(value.get("occupancy_reader"), dict), "pumps": pumps,
            "mtime": os.lstat(path).st_mtime}
JOURNAL_HIDDEN = ("no journal files were found", "no journal files were opened", "insufficient permissions",
                  "not seeing messages from other users")
def journalctl(unit, args, timeout):
    """This user's lines of unit, from its own journal files (--user). A volatile journal keeps
    none ('No journal files were found'): then the system journal by --user-unit (still only
    this user's unit), which group systemd-journal or adm can read."""
    result = subprocess.run(["journalctl", "--user", "-u", unit, "--no-pager", *args],
                            capture_output=True, text=True, timeout=timeout)
    if "no journal files were found" in result.stderr.lower():
        result = subprocess.run(["journalctl", "--user-unit", unit, "--no-pager", *args],
                                capture_output=True, text=True, timeout=timeout)
    return result
def journal(unit, *args, timeout=60):
    return journalctl(unit, args, timeout).stdout
def messages(text):
    """Journal lines less journalctl's own '-- ... --' markers."""
    return [line for line in text.splitlines() if line.strip() and not line.startswith("-- ")]
def journal_access(unit):
    """'readable' when this user's journal shows a line of unit from this boot; 'unreadable'
    when journalctl says it cannot read one (or is not there to run); 'error' when it did not
    finish (a timeout) or could not start for another reason: no verdict either way; else
    'empty'."""
    try:
        result = journalctl(unit, ("-b", "-n", "1", "-o", "short"), 60)
    except (FileNotFoundError, PermissionError):
        return "unreadable"
    except (OSError, subprocess.SubprocessError):
        return "error"
    if messages(result.stdout):
        return "readable"
    return "unreadable" if any(text in result.stderr.lower() for text in JOURNAL_HIDDEN) else "empty"
def process_started(pid):
    """When the process started (epoch seconds, /proc; to about a second), or None."""
    try:
        with open("/proc/%d/stat" % int(pid)) as source:
            ticks = int(source.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as source:
            boot = next(int(line.split()[1]) for line in source if line.startswith("btime "))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, TypeError, IndexError, StopIteration, AttributeError):
        return None
def newest_fact(directory):
    """available_at of the newest la-fact-log-1 line; no other field is read."""
    if not isinstance(directory, str):
        return None
    for path in sorted(pathlib.Path(directory).glob("la-fact-log-1-*.jsonl"), reverse=True):
        with open(path, "rb") as source:
            source.seek(max(0, os.path.getsize(path) - 65536))
            lines = source.read().splitlines()
        for raw in reversed(lines):
            try:
                at = json.loads(raw).get("available_at")
            except (ValueError, AttributeError):
                continue
            if isinstance(at, str):
                return at
    return None
def first_fact(directory, since):
    """available_at of the first la-fact-log-1 line at or after since, from the last 256 KiB of
    the newest two day files (since is a moment ago); no other field is read."""
    if not isinstance(directory, str):
        return None
    low, found = stamp(since), []
    for path in sorted(pathlib.Path(directory).glob("la-fact-log-1-*.jsonl"), reverse=True)[:2]:
        with open(path, "rb") as source:
            source.seek(max(0, os.path.getsize(path) - 262144))
            lines = source.read().splitlines()
        for raw in lines:
            try:
                at = json.loads(raw).get("available_at")
            except (ValueError, AttributeError):
                continue
            if isinstance(at, str) and at[:23] >= low:
                found.append(at)
    return min(found) if found else None
def preflight(base, unit, worker_unit, path):
    """Read-only; 'started' and 'time' bracket it, so their midpoint is this host's clock halfway through."""
    started = time.time()
    show = unit_show(unit)
    state = live_state(path)
    lines = messages(journal(unit, "-b", "-o", "cat", "--grep", "la-live-perceiver (started|refused|stopped)"))
    return {"unit": {key: show.get(key) for key in ("ActiveState", "SubState", "MainPID", "NRestarts")},
            "working_directories": [w["value"] for w in working_directories(unit_sources(show))],
            "running_directory": running_directory(show.get("MainPID", "0")), "app": application(show.get("ExecStart")),
            "worker_main_pid": unit_show(worker_unit).get("MainPID"), "sessions": session_counts(base),
            "machine": machine(base), "live": state, "perceiver_line": lines[-1][:200] if lines else None,
            "journal": journal_access(unit), "process_started": process_started(show.get("MainPID", "0")),
            "newest_fact": newest_fact((state["perceiver"] or {}).get("fact_log_directory")), "started": started,
            "time": time.time()}
def idle(base, worker_unit):
    return {"sessions": session_counts(base), "worker_main_pid": unit_show(worker_unit).get("MainPID"),
            "time": time.time()}
def backup(path, target, sha256):
    data, _ = read_private(path)
    if sha(data) != sha256:
        raise Refused("live config changed since the plan")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Refused("backup already exists") from None
    with os.fdopen(descriptor, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    return {"backup": target, "sha256": sha(data)}
def write_arm(path, backup, sha256, arm, candidate):
    """One arm, from the backup, by an atomic whole-file replace. The file
    must hold the original or this run's candidate."""
    original, _ = read_private(backup)
    if sha(original) != sha256:
        raise Refused("backup does not hold the original")
    current, mode = read_private(path)
    target = arm_bytes(original, arm)
    if sha(current) not in (sha256, sha(arm_bytes(original, candidate))):
        raise Refused("live config changed outside this run")
    if current == target:
        return {"sha256": sha(target), "written": False}
    staged = path + ".cadence-ab.staged"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(staged, flags, 0o600), "wb") as out:
        out.write(target)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(staged, mode)
    os.replace(staged, path)
    if os.name == "posix":
        directory = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return {"sha256": sha(target), "written": True}
def restart(base, unit, worker_unit, max_temperature_c):
    """Re-check idle and the host at the last moment, then restart only this unit."""
    counts = session_counts(base)
    if counts is None:
        raise Refused("session state unknown")
    if sum(counts["sessions"].values()) or sum(counts["jobs"].values()):
        raise Refused("a Lab session or job is active")
    if unit_show(worker_unit).get("MainPID") != "0":
        raise Refused("a Lab worker is running")
    host = machine(base)
    if host["max_temperature_c"] is not None and host["max_temperature_c"] >= max_temperature_c:
        raise Refused("controller host temperature is high")
    if host["kernel_fault_lines"]:
        raise Refused("new kernel fault records in the last 20 minutes")
    started = time.time()
    subprocess.run(["systemctl", "--user", "restart", unit], check=True, timeout=120)
    return {"restarted_at": started}
def started(unit, since, seconds, path=None, directory=None, journal_only=False):
    """After a restart at since: the perceiver's first start or refusal line. Given the live
    config (path) and its fact log directory, journal_access decides how. 'error' (journalctl
    did not finish) returns at once with no line: no verdict. journal_only (the plan read the
    controller's journal) waits for that line only and never falls back, so a journal that
    stopped answering shows none. Else, with the journal unreadable or empty: the config's
    perceiver settings and the first la-fact-log-1 line the new process wrote, waited for as
    long instead (a vacant den writes none)."""
    access = "readable" if path is None else journal_access(unit)
    reply = {} if path is None else {"journal": access}
    if access == "error" or (journal_only and access == "unreadable"):
        return dict(reply, line=None)
    fallback = access != "readable" and not journal_only
    deadline = time.monotonic() + seconds
    begin = None
    if fallback:
        # The new process, not the restart call: the old one may still write a fact (a cancelled
        # request) while it stops. /proc puts the start up to about a second early, hence the slack.
        begin = process_started(unit_show(unit).get("MainPID", "0"))
        if begin is not None:
            since = max(since, begin + PROCESS_START_SLACK_S)
    while True:
        if not fallback:
            lines = messages(journal(unit, "--since", "@%d" % int(since), "-o", "cat", "--grep",
                                     "la-live-perceiver (started|refused)"))
            if lines:
                return dict(reply, line=lines[0][:200])
            fact = None
        else:
            fact = first_fact(directory, since)
        if fact is not None or time.monotonic() >= deadline:
            break
        time.sleep(3)
    if not fallback:
        return dict(reply, line=None)
    state = live_state(path)
    return dict(reply, line=None, fact=fact, since=since, process_started=begin,
                enabled=(state["perceiver"] or {}).get("enabled"), cadence=state["cadence"], sha256=state["sha256"])
def status(base, unit):
    show = unit_show(unit)
    return {"unit": {key: show.get(key) for key in ("ActiveState", "SubState", "MainPID", "NRestarts")},
            "sessions": session_counts(base), "time": time.time()}
def stamp(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]
def fact_rows(directory, since, until):
    """Times and outcome of la-fact-log-1 lines with available_at in the span
    (UTC day files); observation words never leave this host."""
    if not isinstance(directory, str):
        return []
    low, high, rows = stamp(since), stamp(until), []
    day = datetime.datetime.fromtimestamp(since, datetime.timezone.utc).date()
    last = datetime.datetime.fromtimestamp(until, datetime.timezone.utc).date()
    while day <= last:
        path = pathlib.Path(directory) / ("la-fact-log-1-%s.jsonl" % day.isoformat())
        if path.is_file():
            with open(path, "rb") as source:
                for raw in source:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    at = row.get("available_at") if isinstance(row, dict) else None
                    if isinstance(at, str) and low <= at[:23] <= high:
                        rows.append({key: row.get(key) for key in FACT_FIELDS})
        day += datetime.timedelta(days=1)
    return rows
def collect(unit, since, until, directory):
    text = journal(unit, "--since", "@%d" % int(since), "--until", "@%d" % (int(until) + 1),
                   "-o", "short-iso-precise", "--grep", "occupancy edge|occupancy reader|la-live-perceiver", timeout=120)
    return {"journal": text, "facts": fact_rows(directory, since, until), "time": time.time()}
'''
JETSON_LIB = DEPLOY.REMOTE_LIB + CADENCE_LIB

# Runs on LA (python3.12): installs and runs the probe as a bounded transient user unit.
LA_LIB = r'''
import datetime, hashlib, json, os, subprocess, time
class Refused(Exception):
    pass
def _sha(path):
    with open(path, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()
def _state(unit):
    return subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True,
                          timeout=15).stdout.strip() or "unknown"
def install_probe(upload, target, sha256):
    upload, target = os.path.expanduser(upload), os.path.expanduser(target)
    if _sha(upload) != sha256:
        os.unlink(upload)
        raise Refused("uploaded probe checksum mismatch")
    if os.path.exists(target):
        os.unlink(upload)
        if _sha(target) != sha256:
            raise Refused("an installed probe with this name differs")
        return {"probe": target, "installed": False}
    os.chmod(upload, 0o600)
    os.replace(upload, target)
    return {"probe": target, "installed": True}
def start_probe(unit, target, directory, argv, runtime_s):
    target, directory = os.path.expanduser(target), os.path.expanduser(directory)
    output = os.path.join(directory, "voice-probe.jsonl")
    if os.path.exists(output):
        raise Refused("probe output already exists")
    if _state(unit) in ("active", "activating"):
        raise Refused("probe unit already running")
    command = ["systemd-run", "--user", "--unit", unit, "--collect", "--quiet", "--working-directory", directory,
               "-p", "MemoryMax=128M", "-p", "TasksMax=16", "-p", "CPUQuota=25%", "-p", "RuntimeMaxSec=%d" % runtime_s,
               "/usr/bin/python3", "-I", target] + list(argv) + ["--output", output]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise Refused("systemd-run failed: " + result.stderr.strip()[:300])
    time.sleep(3)
    state = _state(unit)
    if state != "active":
        raise Refused("probe unit is %s after start; see journalctl --user -u %s" % (state, unit))
    return {"unit": unit, "output": output, "started_at": time.time()}
def stop_probe(unit):
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=60)
    return {"state": _state(unit)}
def _tail(output, limit=262144):
    """The probe's newest sample rows (its last limit bytes); a torn or cut line is skipped."""
    try:
        with open(os.path.expanduser(output), "rb") as source:
            size = os.fstat(source.fileno()).st_size
            source.seek(max(0, size - limit))
            lines = source.read().splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for raw in lines[1:] if size > limit else lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
def _counts(rows):
    return {"rows": len(rows), "gauges": sum(1 for r in rows if r.get("pipeline") == "vllm"),
            "outcomes": sum(1 for r in rows if str(r.get("metric")).startswith("outcome:")),
            "ok": sum(1 for r in rows if r.get("pipeline") == "voice_probe" and r.get("metric") == "outcome:ok")}
def probe_ready(unit, output, seconds):
    """Until the probe has written a vLLM gauge row and an ok voice probe, its unit
    is no longer active, or seconds pass. Counts only, never a value."""
    deadline = time.monotonic() + seconds
    while True:
        state, counts = _state(unit), _counts(_tail(output))
        if state != "active" or (counts["gauges"] and counts["ok"]) or time.monotonic() >= deadline:
            return dict(counts, state=state)
        time.sleep(2)
def _age(text):
    try:
        return time.time() - datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None
def _newest(rows, pipeline, metric):
    """(value, age in seconds) of the newest such row, or (None, None)."""
    for row in reversed(rows):
        if row.get("pipeline") == pipeline and (row.get("metric") == metric or metric is None):
            return row.get("value_s"), _age(row.get("t"))
    return None, None
def host_health(faults, pilot_unit, probe_unit=None, output=None, fresh_seconds=120):
    """What AGENTS.md asks before each operation on this host: failed system and
    user units, new kernel fault records, the pilot; with the probe running, its
    unit and the newest of its own GPU °C, Tctl, gauge and voice rows."""
    def failed(scope):
        """The failed units of one manager, and why listing them failed (None when it did not):
        the pilot lists the system units with check=True, so a failing listing refuses there too."""
        listing = subprocess.run(["systemctl"] + scope + ["--failed", "--no-legend", "--plain"],
                                 capture_output=True, text=True, timeout=15)
        units = sorted(line.split()[0] for line in listing.stdout.splitlines() if line.strip())
        error = None if listing.returncode == 0 else "exit %d: %s" % (listing.returncode,
                                                                      listing.stderr.strip()[:200])
        return units, error
    system, system_error = failed([])
    user, user_error = failed(["--user"])
    # No -q: it would also hide the warning that the system journal is unreadable.
    kernel = subprocess.run(["journalctl", "-k", "-b", "--since", "20 minutes ago", "--no-pager", "--grep", faults],
                            capture_output=True, text=True, timeout=30)
    hidden = ("insufficient permissions", "not seeing messages from other users and the system")
    result = {"pilot": _state(pilot_unit), "failed_units": system, "failed_units_error": system_error,
              "failed_user_units": user, "failed_user_units_error": user_error,
              "kernel_log_readable": not any(text in kernel.stderr.lower() for text in hidden),
              "kernel_fault_lines": len([line for line in kernel.stdout.splitlines()
                                         if line.strip() and not line.startswith("--")]), "time": time.time()}
    if probe_unit is not None:
        result["probe"] = _state(probe_unit)
    if output is not None:
        rows = _tail(output)
        for key, pipeline, metric in (("gpu_temp_c", "la_gpu", "gpu_temp_c"), ("tctl_c", "la_cpu", "tctl_c")):
            value, age = _newest(rows, pipeline, metric)
            result[key] = value if age is not None and age <= fresh_seconds else None
        result["gauge_age_s"] = _newest(rows, "vllm", None)[1]
        outcomes = [r for r in rows if r.get("pipeline") == "voice_probe" and str(r.get("metric")).startswith("outcome:")]
        result["outcome_age_s"] = _age(outcomes[-1].get("t")) if outcomes else None
    return result
'''


class Refused(Exception):
    """A check refused the step; that step changed nothing."""


class Incomplete(Exception):
    """A remote step ended without a verdict; anything it changed is unknown."""


class Abort(Exception):
    """The run cannot continue."""


def run_command(argv, data=None, timeout=180):
    # Its own process group: a terminal Ctrl-C reaches this tool (which then
    # restores), not an ssh in the middle of a restore.
    extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    result = subprocess.run(argv, input=data, capture_output=True, timeout=timeout, **extra)
    return result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")


def remote_script(lib, step, params):
    return ("# %s:%s\n%s\nPARAMS=json.loads(%s)\n"
            "try:print(json.dumps({'ok':%s(**PARAMS)}))\n"
            "except Refused as error:print(json.dumps({'refused':str(error)}))\n"
            % (MARKER, step, lib, json.dumps(json.dumps(params)), step))


def remote(runner, host, lib, step, timeout=180, /, **params):
    """One step of lib on host. Positional-only, so a step may take 'host' or 'step' itself (health does)."""
    code, out, _ = runner([*SSH, host, PYTHON, "-"], remote_script(lib, step, params).encode(), timeout)
    try:
        reply = json.loads((out.strip().splitlines() or [""])[-1])
    except ValueError:
        reply = {}
    if not isinstance(reply, dict):
        reply = {}
    if "refused" in reply:
        raise Refused(reply["refused"])
    if code or "ok" not in reply:
        raise Incomplete(step, code)
    return reply["ok"]


def describe(error):
    if isinstance(error, Incomplete):
        return "remote step %s ended without a verdict (exit %s); its effect is unknown" % tuple(error.args[:2])
    if isinstance(error, subprocess.TimeoutExpired):
        return "a remote step timed out after %s s; its effect is unknown" % error.timeout
    return "%s: %s" % (type(error).__name__, error)


def arm(text):
    """'interval/max_per_minute/hold', within the controller's bounds."""
    try:
        interval, per_minute, hold = text.split("/")
        value = {"interval_seconds": float(interval), "max_per_minute": int(per_minute), "hold_seconds": float(hold)}
    except ValueError:
        raise argparse.ArgumentTypeError("interval/max_per_minute/hold, e.g. 4/15/10") from None
    for key, (low, high) in BOUNDS.items():
        if not math.isfinite(value[key]) or not low <= value[key] <= high:
            raise argparse.ArgumentTypeError("%s outside the controller bound %g-%g" % (key, low, high))
    return value


def arm_text(value):
    return "%g s, %d per minute, hold %g s" % (value["interval_seconds"], value["max_per_minute"],
                                                value["hold_seconds"])


def same_cadence(a, b):
    return all(isinstance(a.get(k), (int, float)) and float(a[k]) == float(b[k]) for k in KEYS)


def set_sigint(handler):
    """The previous SIGINT handler; None off the main thread, where nothing changes."""
    try:
        return signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread
        return None


def ignore_sigint():
    return set_sigint(signal.SIG_IGN)


def interrupt_once(signum, frame):
    """The first Ctrl-C raises; it ignores every later one first, so none can cut into the restore."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def restore_sigint(previous):
    if previous is not None:
        signal.signal(signal.SIGINT, previous)


def retried(ctx, what, step):
    """step(), again after a pause while ssh ends it without a verdict (Incomplete
    or a timeout): the restore steps are idempotent. A refusal or an abort is
    never retried."""
    for pause in RETRY_PAUSES_S + (None,):
        try:
            return step()
        except (Incomplete, subprocess.TimeoutExpired) as error:
            if pause is None:
                raise
            ctx.say(step="retry", what=what, error=describe(error), wait_s=pause)
            ctx.sleep(pause)


def write_json(path, value):
    staged = Path(str(path) + ".tmp")
    staged.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)


class Context:
    def __init__(self, args, runner, out, clock, sleep):
        self.args, self.runner, self.out, self.clock, self.sleep = args, runner, out, clock, sleep

    def jetson(self, step, timeout=180, /, **params):
        return remote(self.runner, self.args.jetson, JETSON_LIB, step, timeout, **params)

    def la(self, step, timeout=180, /, **params):
        return remote(self.runner, self.args.la, LA_LIB, step, timeout, **params)

    def ssh(self, host, argv, data=None, timeout=180):
        """argv on host. ssh joins its arguments into one remote shell line, so
        each is quoted: nothing in a value can act as shell syntax there."""
        return self.runner([*SSH, host, *map(shlex.quote, argv)], data, timeout)

    def ssh_line(self, host, line, data=None, timeout=180):
        """A remote shell line this tool composed itself, every value in it quoted."""
        return self.runner([*SSH, host, line], data, timeout)

    def say(self, **fields):
        self.out(json.dumps(fields, sort_keys=True))


# --- plan ------------------------------------------------------------------

def probe_names(run, source):
    digest = hashlib.sha256(source).hexdigest()
    directory = "la-latency/runs/cadence-ab-" + run
    return {"sha256": digest, "file": "la-latency/voice_probe-%s.py" % digest[:16], "directory": directory,
            "output": directory + "/voice-probe.jsonl", "unit": "la-voice-probe-" + run.lower()}


def plan(ctx):
    """Read-only preflight of both hosts, the schedule and the refusals."""
    args = ctx.args
    run = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ctx.clock()))
    refusals, warnings = [], []
    slo = json.loads(Path(args.slo).read_text(encoding="utf-8"))
    d2 = slo["decision"]["D2"]
    # Each offset compares the remote clock halfway through its step (two stamps)
    # with this workstation's clock halfway through the ssh call.
    before = ctx.clock()
    jet = ctx.jetson("preflight", base=BASE, unit=UNIT, worker_unit=WORKER_UNIT, path=args.live_config)
    jet_offset = (jet.get("started", jet["time"]) + jet["time"]) / 2 - (before + ctx.clock()) / 2
    source = (HERE / "voice_probe.py").read_bytes()
    before = ctx.clock()
    code, text, _ = ctx.ssh(args.la, [PYTHON, "-", "--check", "--url", args.probe_url, *gauge_source(args)],
                            source, 60)
    after = ctx.clock()
    try:
        check = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check = None
    la_health = ctx.la("host_health", 60, faults=FAULTS, pilot_unit=LA_PILOT_UNIT)

    unit = jet["unit"]
    if unit.get("ActiveState") != "active":
        refusals.append(UNIT + " is not active")
    found = jet["working_directories"]
    working = found[0] if len(found) == 1 else None
    if working is None or posixpath.normpath(jet["running_directory"] or "") != posixpath.normpath(working):
        refusals.append("unit WorkingDirectory is not the running directory")
    app = jet["app"]
    if not app.get("host") or not app.get("port"):
        refusals.append("unit ExecStart application not recognized")
    counts = jet["sessions"]
    if counts is None:
        refusals.append("Lab session state unknown")
    elif sum(counts["sessions"].values()) or sum(counts["jobs"].values()):
        warnings.append("a Lab session or job is active now; --apply waits for it before each restart")
    if jet["worker_main_pid"] != "0":
        warnings.append("a Lab worker is running now; --apply waits for it before each restart")
    host = jet["machine"]
    if host["max_temperature_c"] is not None and host["max_temperature_c"] >= MAX_TEMPERATURE_C:
        refusals.append("controller host temperature is high")
    if host["kernel_fault_lines"]:
        refusals.append("new kernel fault records in the last 20 minutes")
    if not host["kernel_log_readable"]:
        warnings.append("kernel log not readable by the controller user")
    live = jet["live"]
    perceiver = live["perceiver"] or {}
    if perceiver.get("enabled") is not True:
        refusals.append("la_live_perceiver is absent or not enabled")
    if not live["reader"]:
        refusals.append("occupancy_reader block absent")
    for name, pump in sorted(live["pumps"].items()):
        if pump.get("enabled") is True and name == "qwen_live":
            refusals.append("qwen_live is enabled, so the perceiver is refused")
        hold = pump.get("occupancy_hold_seconds")
        if pump.get("enabled") is True and hold is not None and any(
                hold != a["hold_seconds"] for a in (args.baseline, args.candidate)):
            refusals.append("%s names occupancy_hold_seconds %s: the controller refuses a reader hold that differs"
                            % (name, hold))
    if not same_cadence(live["cadence"], args.baseline):
        refusals.append("the live config is not the baseline arm (%s)" % json.dumps(live["cadence"], sort_keys=True))
    evidence, problems, notes = perceiver_evidence(jet, args.baseline)
    refusals += problems
    warnings += notes
    if args.apply and jet["journal"] in ("unreadable", "empty") and not args.allow_unreadable_journal:
        refusals.append("--apply with the controller journal %s: the occupancy edge lines the analysis reads are "
                        "in it, so the run could only end INSUFFICIENT, after %d controller restarts. Make it "
                        "readable (add the service account to group systemd-journal), or pass "
                        "--allow-unreadable-journal to run anyway" % (jet["journal"], args.windows + 1))
    metrics = None
    if check is None:
        refusals.append("the LA probe check did not answer (exit %s)" % code)
        la_offset = None
    else:
        la_offset = (check["time"] + check.get("time_end", check["time"])) / 2 - (before + after) / 2
        if not check.get("sidecar_healthz"):
            refusals.append("the metrics sidecar does not answer at %s" % args.probe_url)
        if check.get("gpu_temp_c") is None:
            refusals.append("the sidecar reports no gpu_temp_c: the GPU guard could not decide")
        gauges = check.get("vllm_gauges")
        if not isinstance(gauges, dict) or "error" in gauges:
            reason = gauges.get("error") if isinstance(gauges, dict) else "not read"
            refusals.append("vLLM gauges unreadable from LA (%s): the queue_busy share could not be estimated"
                            % reason)
        else:
            # The probe unit gets this fixed address: a docker command inside it
            # (Go runtime threads) could exceed its TasksMax.
            metrics = args.vllm_metrics_url or check.get("vllm_metrics_url")
            try:
                voice_probe.metrics_url(metrics or "")
            except ValueError:
                refusals.append("LA reported no usable vLLM metrics address (%r)" % metrics)
                metrics = None
        if check.get("tctl_c") is None:
            warnings.append("no k10temp Tctl on LA: Tctl comes only from thermal_shed lines")
    refusals += la_problems(dict(la_health, gpu_temp_c=(check or {}).get("gpu_temp_c"),
                                 tctl_c=(check or {}).get("tctl_c")), d2["gpu_temp_p95_max_c"])
    warnings += la_warnings(la_health)
    if not la_health.get("kernel_log_readable"):
        warnings.append("LA kernel log not readable by the LA user: new kernel faults there were not checked")
    for name, offset in (("Jetson", jet_offset), ("LA", la_offset)):
        if offset is not None and abs(offset) > 10:
            refusals.append("%s clock differs from this workstation by %.1f s" % (name, offset))
        elif offset is not None and abs(offset) > 2:
            warnings.append("%s clock differs from this workstation by %.1f s" % (name, offset))
    if jet["newest_fact"] is None:
        warnings.append("no la-fact-log-1 line found: check the la-live-inference-1 grant and occupancy")

    schedule = ["baseline" if index % 2 == 0 else "candidate" for index in range(args.windows)]
    estimate = args.windows * (args.window_minutes * 60 + args.settle_seconds + RESTART_BUDGET_S)
    probe = probe_names(run, source)
    # The probe outlives the worst case: up to three session waits per restart
    # (wait_idle, then two activate retries) for every window and the restore,
    # and the restore's retries. finish stops it explicitly anyway.
    waits = (args.windows + 1) * 3 * max(args.session_wait, RESTORE_WAIT_MIN_S) + 2 * sum(RETRY_PAUSES_S)
    duration = min(7 * 86400, int(estimate * 1.5 + 1800 + waits))
    probe["argv"] = ["--url", args.probe_url, "--interval", "%g" % args.probe_interval,
                     "--timeout", "%g" % min(10.0, args.probe_interval - 1), "--duration", str(duration),
                     *(["--vllm-metrics-url", metrics] if metrics else gauge_source(args)),
                     "--load-interval", "%g" % args.load_interval]
    probe["runtime_s"] = duration + 600
    backup = "%s.before-cadence-ab-%s" % (args.live_config, run)
    out = args.out or str(Path.home() / "la-latency-runs" / ("cadence-ab-" + run))
    if Path(out).exists():
        refusals.append("the run directory already exists: " + out)
    return {
        "run": run, "jetson": args.jetson, "la": args.la, "live_config": args.live_config, "backup": backup,
        "out": out, "original_sha256": live["sha256"], "working_directory": working, "app": app,
        "fact_log_directory": perceiver.get("fact_log_directory"),
        "arms": {"baseline": args.baseline, "candidate": args.candidate},
        "arm_text": {"baseline": arm_text(args.baseline), "candidate": arm_text(args.candidate)},
        "schedule": schedule, "estimated_seconds": estimate, "probe": probe, "perceiver_evidence": evidence,
        "settings": {"window_minutes": args.window_minutes, "settle_seconds": args.settle_seconds,
                     "yield_threshold": args.yield_threshold, "min_occupied_minutes": args.min_occupied_minutes,
                     "min_samples": args.min_samples or slo["min_samples_per_statistic"], "guard": d2,
                     "keep_candidate_on_pass": args.keep_candidate_on_pass, "session_wait": args.session_wait,
                     "probe_interval": args.probe_interval, "load_interval": args.load_interval,
                     "allow_unreadable_journal": args.allow_unreadable_journal},
        "preflight": {"unit": unit, "sessions": counts, "worker_main_pid": jet["worker_main_pid"],
                      "jetson_max_temperature_c": host["max_temperature_c"], "cadence_now": live["cadence"],
                      "perceiver_line": jet["perceiver_line"], "controller_journal": jet["journal"],
                      "newest_fact": jet["newest_fact"],
                      "la_check": check, "la_health": la_health,
                      "clock_offsets_s": {"jetson": round(jet_offset, 3),
                                          "la": None if la_offset is None else round(la_offset, 3)}},
        "refusals": refusals, "warnings": warnings,
        "restore": restore_commands(args.jetson, args.la, args.live_config, backup, probe["unit"]),
        "restore_note": RESTORE_NOTE}


def gauge_source(args):
    """The probe's vLLM gauge source: a fixed URL when given, else the container's address."""
    if args.vllm_metrics_url:
        return ["--vllm-metrics-url", args.vllm_metrics_url]
    return ["--vllm-container", args.vllm_container]


def la_problems(health, gpu_max_c, outcome_limit_s=None, gauge_limit_s=None):
    """Why LA should not carry the A/B now (AGENTS.md: recheck failed units, new
    kernel faults and temperatures before each operation on the AI host). Failed
    units under the pilot's own rule only: a failed system unit outside
    PILOT_KNOWN_FAILED_UNITS (the pilot then refuses every request); failed user
    units are la_warnings'. A listing of the system units that fails is a
    refusal too: the pilot lists them with check=True and refuses while it
    fails. With the probe running, also its unit and the freshness of its own
    rows."""
    problems = []
    if health.get("pilot") != "active":
        problems.append("%s on LA is %s" % (LA_PILOT_UNIT, health.get("pilot")))
    if health.get("failed_units_error"):
        problems.append("LA could not list its failed system units (%s): the pilot refuses every request while that "
                        "listing fails" % health["failed_units_error"])
    unknown = [unit for unit in health.get("failed_units") or () if unit not in PILOT_KNOWN_FAILED_UNITS]
    if unknown:
        text = ("failed system units on LA: %s (diagnose them first: the pilot refuses every request while a failed "
                "system unit outside its known set is listed; runbook 2.1)" % ", ".join(unknown[:5]))
        if any(SNAP_MOUNT.fullmatch(unit) for unit in unknown):
            text += ("; a snap-<name>-<rev>.mount is usually left 'not-found failed' by a snapd refresh: once "
                     "diagnosed, the owner clears it with 'sudo systemctl reset-failed <unit>' (runbook 2.1, "
                     "recurring fault)")
        problems.append(text)
    if health.get("kernel_fault_lines"):
        problems.append("new kernel fault records on LA in the last 20 minutes")
    gpu, tctl = health.get("gpu_temp_c"), health.get("tctl_c")
    if gpu is not None and gpu >= gpu_max_c:
        problems.append("LA GPU at %.0f °C (the guard allows a candidate p95 of %g °C)" % (gpu, gpu_max_c))
    if tctl is not None and tctl >= LA_TCTL_MAX_C:
        problems.append("LA Tctl at %.1f °C (the pilot sheds live Qwen at %d °C)" % (tctl, LA_TCTL_MAX_C))
    if "probe" in health and health["probe"] != "active":
        problems.append("the voice probe unit on LA is %s" % health["probe"])
    for key, label, limit in (("outcome_age_s", "voice probe outcome", outcome_limit_s),
                              ("gauge_age_s", "vLLM gauge read", gauge_limit_s)):
        if limit is not None and key in health and (health[key] is None or health[key] > limit):
            problems.append("the probe's newest %s is %s" % (
                label, "missing" if health[key] is None else "%.0f s old" % health[key]))
    return problems


def la_warnings(health):
    """Failed user units on LA: the owner's own jobs (Unreal and animation runs,
    snap scopes). The pilot never checks user units, so they are listed, never
    refused; nor is a listing of them that fails."""
    warnings = []
    if health.get("failed_user_units_error"):
        warnings.append("LA could not list its failed user units (%s); the pilot never checks them"
                        % health["failed_user_units_error"])
    units = health.get("failed_user_units") or []
    if units:
        more = " and %d more" % (len(units) - 10) if len(units) > 10 else ""
        warnings.append("failed user units on LA (the pilot never checks user units, so they are listed only): %s%s"
                        % (", ".join(units[:10]), more))
    return warnings


def started_at(line, interval):
    """Whether a perceiver line is its start line at this interval."""
    found = STARTED.search(line or "")
    return found is not None and found.group(1) == "%.1f" % interval


def journal_required(planned):
    """Whether every restart must show the perceiver's start line: the plan read the controller's
    journal, so a restart that cannot is an abort, never the weaker fact-log fallback."""
    return (planned["preflight"]["controller_journal"] == "readable"
            or planned["perceiver_evidence"]["source"] == "journal")


def perceiver_evidence(jet, cadence):
    """(evidence, refusals, warnings): how the dry run knows the perceiver runs
    this arm. With a perceiver line in the controller's journal, that line,
    which must be its start at the arm's interval (the stronger evidence).
    Without one (the journal unreadable or empty, or the line rotated out), the
    fact-log fallback: managed-live.json holding the arm with the perceiver
    enabled (plan refuses either when not), not changed since this controller
    process started (refused when it was, or when either time is unknown), and
    a la-fact-log-1 line from this process within FACT_RECENT_S. That line's
    absence is only a warning: a vacant den writes none. The controller reads
    the file once, at start: a later change is refused with the journal too,
    as the restore would otherwise take the running process for the original."""
    line, access = jet["perceiver_line"], jet["journal"]
    live, now, process = jet["live"], jet["time"], jet.get("process_started")
    mtime = live.get("mtime")
    refusals, warnings = [], []
    if access == "error":
        refusals.append("journalctl on the controller did not finish (a timeout): whether its journal is readable, "
                        "and so which perceiver evidence applies, is undecided; plan again")
    changed = process is not None and mtime is not None and mtime > process + PROCESS_START_SLACK_S
    if changed:
        refusals.append("managed-live.json changed after the controller started (file %s, process %s): the running "
                        "perceiver may not run it. Restart the controller on the current file first (while no Lab "
                        "session records), then plan again" % (harvest.format_time(mtime),
                                                               harvest.format_time(process)))
    times = {"process_started": process, "config_mtime": mtime}
    if line is not None:
        if not started_at(line, cadence["interval_seconds"]):
            refusals.append("la-live-perceiver is not running at the baseline interval (last line: %r)" % line)
        return dict({"source": "journal", "journal": access, "line": line}, **times), refusals, warnings
    enabled = (live["perceiver"] or {}).get("enabled")
    try:
        fact = harvest.parse_time(jet["newest_fact"])
    except harvest.InputError:
        fact = None
    holds = enabled is True and same_cadence(live["cadence"], cadence)
    fresh = (fact is not None and now - fact <= FACT_RECENT_S and process is not None
             and fact >= process + PROCESS_START_SLACK_S)
    runs_file = process is not None and mtime is not None and not changed
    evidence = dict({"source": "fact-log", "journal": access, "line": None,
                     "config": {"enabled": enabled, "cadence": live["cadence"], "holds_arm": holds},
                     "newest_fact": jet["newest_fact"],
                     "newest_fact_age_s": None if fact is None else round(now - fact, 1),
                     "fact_from_this_process": fresh, "config_older_than_process": runs_file,
                     "verified": holds and fresh and runs_file}, **times)
    if process is None or mtime is None:
        refusals.append("the controller process's start (/proc) or managed-live.json's mtime is unknown: without its "
                        "journal nothing shows the running perceiver runs that file")
    if access == "readable":
        warnings.append("no la-live-perceiver line in the controller journal this boot (rotated out?): perceiver "
                        "liveness comes from the fact-log fallback (managed-live.json and la-fact-log-1) here; "
                        "every restart still needs its start line")
    elif access != "error":
        why = ("cannot read the controller journal (a volatile journal keeps no per-user files)"
               if access == "unreadable" else "sees no controller line in its journal this boot")
        warnings.append(
            "the service account %s: perceiver liveness comes from the fact-log fallback (managed-live.json and "
            "la-fact-log-1), here and after every restart. The occupancy edge lines the analysis reads are in that "
            "journal too: without them an --apply run collects no occupied time and cannot reach PASS, so --apply "
            "refuses unless --allow-unreadable-journal is given. Optional owner step: add the service account to "
            "group systemd-journal so controller logs are readable" % why)
    if not fresh:
        warnings.append("perceiver liveness unverified: no la-fact-log-1 line from this controller process in the "
                        "last %d min (newest: %s); the den may be vacant, and each window's restart checks again"
                        % (FACT_RECENT_S // 60, jet["newest_fact"]))
    return evidence, refusals, warnings


def restore_commands(jetson, la, live_config, backup, unit):
    return ["ssh %s \"install -m 600 '%s' '%s'\"" % (jetson, backup, live_config),
            "ssh %s 'systemctl --user restart %s'" % (jetson, UNIT),
            "ssh %s 'systemctl --user stop %s'" % (la, unit)]


# --- apply -----------------------------------------------------------------

def wait_idle(ctx, limit):
    """Until no Lab session or job is active and no Lab worker runs."""
    start, said = ctx.clock(), False
    while True:
        reply = ctx.jetson("idle", base=BASE, worker_unit=WORKER_UNIT)
        counts = reply["sessions"]
        if counts is None:
            raise Abort("Lab session state unknown")
        if not (sum(counts["sessions"].values()) or sum(counts["jobs"].values())) and reply["worker_main_pid"] == "0":
            return
        if ctx.clock() - start >= limit:
            raise Abort("a Lab session, job or worker stayed active for %d s" % limit)
        if not said:
            ctx.say(step="waiting", reason="a Lab session, job or worker is active", sessions=counts,
                    worker_main_pid=reply["worker_main_pid"])
            said = True
        ctx.sleep(IDLE_POLL_SECONDS)


def activate(ctx, planned, cadence):
    """Restart the controller on the file as written, then the deploy tool's
    health verdict and the perceiver's start: its start line at the arm's
    interval, required whenever the plan read the controller journal
    (journal_required: no fallback then, whatever the journal says now) or the
    journal reads now; else the fact-log fallback: the live config holding the
    arm with the perceiver enabled (required), and the first la-fact-log-1 line
    of the new process as the start evidence, or, with none in START_WAIT_S (a
    vacant den writes none), 'perceiver start unverified (vacant)'. A
    journalctl that did not finish is an abort either way."""
    for attempt in range(3):
        try:
            restarted = ctx.jetson("restart", base=BASE, unit=UNIT, worker_unit=WORKER_UNIT,
                                   max_temperature_c=MAX_TEMPERATURE_C)
            break
        except Refused as error:
            if attempt == 2 or not ("active" in str(error) or "running" in str(error)):
                raise
            wait_idle(ctx, ctx.args.session_wait)
    health = ctx.jetson("health", unit=UNIT, new=planned["working_directory"], host=planned["app"]["host"],
                        port=planned["app"]["port"])
    if not health.get("healthy"):
        raise Abort("controller not healthy after the restart: " + json.dumps(health, sort_keys=True))
    strict = journal_required(planned)
    reply = ctx.jetson("started", unit=UNIT, since=restarted["restarted_at"], seconds=START_WAIT_S,
                       path=planned["live_config"], directory=planned["fact_log_directory"], journal_only=strict)
    line, access = reply.get("line"), reply.get("journal", "readable")
    if access == "error":
        raise Abort("la-live-perceiver unverified after the restart: journalctl on the controller did not finish "
                    "(a timeout)")
    if strict or access == "readable":
        if not started_at(line, cadence["interval_seconds"]):
            raise Abort("la-live-perceiver did not start at %gs (line: %r; controller journal %s%s)"
                        % (cadence["interval_seconds"], line, access,
                           "; the plan read it, so there is no fallback" if access != "readable" else ""))
        start = {"evidence": "journal", "line": line}
    else:
        if reply.get("enabled") is not True or not same_cadence(reply.get("cadence") or {}, cadence):
            raise Abort("la-live-perceiver unverified after the restart: the controller journal is %s and the live "
                        "config does not hold this arm with the perceiver enabled (enabled %r, %s)"
                        % (access, reply.get("enabled"), json.dumps(reply.get("cadence"), sort_keys=True)))
        fact, begin = reply.get("fact"), reply.get("process_started")
        start = {"evidence": "fact-log", "journal": access, "config_sha256": reply.get("sha256"),
                 "process_started": begin, "fact_at": fact if begin is not None else None}
        if fact is None:
            start["note"] = ("perceiver start unverified (vacant): no la-fact-log-1 line within %d s of the restart; "
                             "the controller is healthy and the config holds this arm" % START_WAIT_S)
        elif begin is None:
            start["unattributed_fact_at"] = fact
            start["note"] = ("perceiver start unattributed: the new controller process's start (/proc) is unknown, so "
                             "the first la-fact-log-1 line after the restart (%s) may be the old process's; the "
                             "controller is healthy and the config holds this arm" % fact)
    return {"restart_at": restarted["restarted_at"], "healthy_at": ctx.clock(), "main_pid": health.get("main_pid"),
            "perceiver_line": line, "perceiver_start": start}


def measure(ctx, window, save):
    """Sleep through the window, checking the controller every CHECK_SECONDS. A
    status check ssh ends without a verdict is noted; the measurement itself
    needs no link, so only STATUS_MISSES_MAX + 1 in a row abort."""
    misses = 0
    while True:
        remaining = window["measure_until"] - ctx.clock()
        if remaining <= 0:
            return
        ctx.sleep(min(remaining, CHECK_SECONDS))
        try:
            reply = ctx.jetson("status", base=BASE, unit=UNIT)
        except (Incomplete, subprocess.TimeoutExpired) as error:
            misses += 1
            window["notes"].append("controller status unread at %s: %s" % (harvest.format_time(ctx.clock()),
                                                                           describe(error)))
            if misses > STATUS_MISSES_MAX:
                raise Abort("%d controller status checks in a row ended without a verdict" % misses) from None
            save()
            continue
        misses = 0
        unit = reply["unit"]
        if unit.get("ActiveState") != "active":
            raise Abort("%s is %s mid-window" % (UNIT, unit.get("ActiveState")))
        if unit.get("MainPID") != window.get("main_pid"):
            window["notes"].append("the controller restarted by itself (MainPID %s -> %s)"
                                   % (window.get("main_pid"), unit.get("MainPID")))
            window["main_pid"] = unit.get("MainPID")
        counts = reply["sessions"] or {"sessions": {}, "jobs": {}}
        note = "a Lab session or job was active during this window"
        if (sum(counts["sessions"].values()) or sum(counts["jobs"].values())) and note not in window["notes"]:
            window["notes"].append(note)
        save()


def write_arm(ctx, planned, state, name):
    state["file"] = "unknown"
    reply = ctx.jetson("write_arm", path=planned["live_config"], backup=planned["backup"],
                       sha256=planned["original_sha256"], arm=None if name == "baseline" else planned["arms"]["candidate"],
                       candidate=planned["arms"]["candidate"])
    state["file"] = "original" if name == "baseline" else "candidate"
    return reply


def restore(ctx, planned, state):
    """The original bytes, then a restart unless the controller already runs
    them; never while a Lab session records. Ctrl-C is ignored meanwhile. A
    flaky link is the likeliest reason a run aborts, so each half is retried
    (RETRY_PAUSES_S): writing the original is idempotent, and a repeated
    restart re-checks idle and runs the same file."""
    previous = ignore_sigint()
    result = {}
    try:
        if state["file"] == "original":
            result["file"] = "unchanged (the original)"
        else:
            try:
                retried(ctx, "write the original live config", lambda: write_arm(ctx, planned, state, "baseline"))
                result["file"] = "restored"
            except Exception as error:
                result["file"] = "NOT restored: " + describe(error)
                return result
        if state["running"] == "original":
            result["controller"] = "already runs the original"
            return result

        def restart():
            wait_idle(ctx, max(ctx.args.session_wait, RESTORE_WAIT_MIN_S))
            state["running"] = "unknown"
            return activate(ctx, planned, planned["arms"]["baseline"])
        try:
            start = retried(ctx, "restart the controller on the original", restart)["perceiver_start"]
            state["running"] = "original"
            result["controller"] = "restarted on the original"
            if start["evidence"] != "journal":  # the fact-log fallback: what it saw
                result["perceiver_start"] = start
        except Exception as error:
            result["controller"] = "NOT restarted: " + describe(error)
        return result
    finally:
        restore_sigint(previous)


def keep_candidate(ctx, planned, state):
    if state["file"] == "candidate" and state["running"] == "candidate":
        return "kept; the controller already runs it"
    wait_idle(ctx, ctx.args.session_wait)
    write_arm(ctx, planned, state, "candidate")
    state["running"] = "unknown"
    activate(ctx, planned, planned["arms"]["candidate"])
    state["running"] = "candidate"
    return "switched to the candidate and kept"


def start_probe(ctx, planned, manifest):
    probe = planned["probe"]
    source = (HERE / "voice_probe.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != probe["sha256"]:
        raise Abort("voice_probe.py changed since the plan")
    code, _, _ = ctx.ssh_line(planned["la"], "mkdir -p -m 700 %s && umask 077 && cat > %s" % (
        shlex.quote(probe["directory"]), shlex.quote(probe["file"] + ".upload")), source, 120)
    if code:
        raise Incomplete("upload", code)
    manifest["probe"] = {"install": ctx.la("install_probe", upload="~/" + probe["file"] + ".upload",
                                           target="~/" + probe["file"], sha256=probe["sha256"])}
    manifest["probe"]["unit"] = probe["unit"]  # stopped at the end even if the start check below refuses
    manifest["probe"].update(ctx.la("start_probe", unit=probe["unit"], target="~/" + probe["file"],
                                    directory="~/" + probe["directory"], argv=probe["argv"],
                                    runtime_s=probe["runtime_s"]))
    # Samples must flow before the first window, while the config is untouched.
    ready = ctx.la("probe_ready", PROBE_READY_S + 60, unit=probe["unit"], output=probe["output"],
                   seconds=PROBE_READY_S)
    manifest["probe"]["ready"] = ready
    if ready.get("state") != "active" or not ready.get("gauges") or not ready.get("ok"):
        raise Abort("the voice probe wrote no vLLM gauge row or no ok probe within %d s: %s"
                    % (PROBE_READY_S, json.dumps(ready, sort_keys=True)))


def check_la(ctx, planned, probe):
    """LA before each window: the dry run's checks, the probe unit and its rows still flowing."""
    settings = planned["settings"]
    health = ctx.la("host_health", 60, faults=FAULTS, pilot_unit=LA_PILOT_UNIT, probe_unit=probe["unit"],
                    output=probe["output"], fresh_seconds=FRESH_S)
    problems = la_problems(health, settings["guard"]["gpu_temp_p95_max_c"],
                           outcome_limit_s=4 * settings["probe_interval"] + 30,
                           gauge_limit_s=max(60.0, 10 * settings["load_interval"]))
    if problems:
        raise Abort("LA: " + "; ".join(problems))
    return health


def stop_probe(ctx, manifest):
    """Stop the LA probe once (best effort, retried); True when it is not running."""
    probe = manifest.get("probe") or {}
    if not probe.get("unit"):
        return True
    if "stop" not in probe:
        try:
            probe["stop"] = retried(ctx, "stop the voice probe", lambda: ctx.la("stop_probe", unit=probe["unit"]))
        except Exception as error:
            probe["stop"] = "failed: " + describe(error)
    stop = probe["stop"]
    return isinstance(stop, dict) and stop.get("state") not in ("active", "activating", "deactivating", "reloading")


def collect(ctx, planned, manifest, directory):
    """Everything the analysis reads, into the run directory (best effort, noted)."""
    notes = manifest.setdefault("collect_notes", [])
    started = [w["restart_at"] for w in manifest["windows"] if "restart_at" in w]
    since = (min(started) if started else manifest["started_at"]) - 120
    until = ctx.clock() + 60
    try:
        reply = ctx.jetson("collect", 600, unit=UNIT, since=since, until=until,
                           directory=planned["fact_log_directory"])
        (directory / "jetson-controller.txt").write_text(reply["journal"], encoding="utf-8")
        with open(directory / "fact-log-times.jsonl", "w", encoding="utf-8", newline="\n") as handle:
            for row in reply["facts"]:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception as error:
        notes.append("Jetson collection failed: " + describe(error))
    commands = (("la-pilot.txt", ["journalctl", "--user", "-u", LA_PILOT_UNIT, "--since", "@%d" % int(since),
                                  "--until", "@%d" % (int(until) + 1), "-o", "short-iso-precise", "--no-pager"]),
                ("voice-probe.jsonl", ["cat", "--", planned["probe"]["output"]]))
    for name, command in commands:
        try:
            code, text, _ = ctx.ssh(planned["la"], command, None, 600)
            if code:
                raise Incomplete(command[0], code)
            (directory / name).write_text(text, encoding="utf-8", newline="\n")
        except Exception as error:
            notes.append("LA %s failed: %s" % (name, describe(error)))


def finish(ctx, planned, manifest, directory, save):
    """Stop the probe, collect and analyse; returns the summary (or None)."""
    stop_probe(ctx, manifest)
    collect(ctx, planned, manifest, directory)
    save()
    try:
        return analyze_run(directory, manifest)
    except Exception as error:
        manifest.setdefault("collect_notes", []).append("analysis failed: " + describe(error))
        save()
        return None


def apply(ctx, planned):
    """The run. Ctrl-C: the first one ends the windows (interrupt_once) and is
    ignored from then until the original runs (or the candidate is kept) and the
    probe is stopped; only the collection after that may be skipped with one more."""
    directory = Path(planned["out"])
    manifest = {"schema": SCHEMA, "run": planned["run"], "plan": planned, "started_at": ctx.clock(), "windows": [],
                "probe": None, "backup": None}
    unsaved = []

    def save():
        """Best effort once the run starts: the manifest in memory is the record and
        each save writes it whole, so a locked or full disk never skips the restore."""
        try:
            write_json(directory / "manifest.json", manifest)
        except OSError as error:
            unsaved[:] = [describe(error)]
        else:
            unsaved.clear()
    try:
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / "manifest.json", manifest)
    except OSError as error:
        ctx.say(step="aborted", reason="the run directory is not writable: " + describe(error), changed="nothing")
        return 3
    ctx.say(step="restore-commands", commands=planned["restore"], note=RESTORE_NOTE)
    previous = set_sigint(interrupt_once)
    try:
        return run_windows(ctx, planned, manifest, directory, save, unsaved)
    finally:
        restore_sigint(previous)


def run_windows(ctx, planned, manifest, directory, save, unsaved):
    args = ctx.args
    state = {"file": "original", "running": "original"}
    try:
        manifest["backup"] = ctx.jetson("backup", path=planned["live_config"], target=planned["backup"],
                                        sha256=planned["original_sha256"])
    except (Exception, KeyboardInterrupt) as error:
        ignore_sigint()
        reason = ("interrupted (Ctrl-C)" if isinstance(error, KeyboardInterrupt)
                  else "backup failed: " + describe(error))
        manifest["outcome"] = {"aborted": reason, "restore": "nothing was changed"}
        save()
        ctx.say(step="aborted", reason=reason, changed="nothing")
        return 3
    save()
    completed, failure, window = False, None, None
    try:
        try:
            start_probe(ctx, planned, manifest)
            save()
            ctx.say(step="probe", unit=manifest["probe"]["unit"], stop="ssh %s 'systemctl --user stop %s'"
                    % (planned["la"], manifest["probe"]["unit"]))
            for index, name in enumerate(planned["schedule"]):
                window = {"index": index + 1, "arm": name, "cadence": planned["arms"][name], "complete": False,
                          "notes": []}
                manifest["windows"].append(window)
                save()
                wait_idle(ctx, args.session_wait)
                window["la"] = check_la(ctx, planned, manifest["probe"])  # just before LA's load changes
                window["config_sha256"] = write_arm(ctx, planned, state, name)["sha256"]
                state["running"] = "unknown"
                window.update(activate(ctx, planned, planned["arms"][name]))
                state["running"] = state["file"]
                if window["perceiver_start"].get("note"):
                    window["notes"].append(window["perceiver_start"]["note"])
                window["measure_from"] = window["healthy_at"] + args.settle_seconds
                window["measure_until"] = window["measure_from"] + args.window_minutes * 60
                save()
                ctx.say(step="window", index=index + 1, of=len(planned["schedule"]), arm=name,
                        cadence=planned["arm_text"][name], measure_until=harvest.format_time(window["measure_until"]))
                measure(ctx, window, save)
                window["complete"] = True
                save()
            completed = True
            ignore_sigint()
        except Exception as error:  # whatever went wrong, the original is restored below
            failure = describe(error)
            ignore_sigint()
    except KeyboardInterrupt:  # raised once by interrupt_once, which already ignores the next
        ignore_sigint()
        failure = "interrupted (Ctrl-C)" if failure is None else failure + "; then interrupted (Ctrl-C)"
    # Ctrl-C is ignored from here until the original runs (or the candidate is kept) and the probe stops.
    summary, kept = None, None
    manifest["outcome"] = outcome = {"completed": completed, "failure": failure}
    try:
        if window is not None and not window["complete"] and "measure_from" in window:
            window["measure_until"] = min(window["measure_until"], ctx.clock())
            window["notes"].append("partial: " + (failure or "stopped"))
        save()
        if completed and failure is None and args.keep_candidate_on_pass:
            try:
                summary = finish(ctx, planned, manifest, directory, save)
                if summary is not None and summary["verdict"] == PASS:
                    kept = keep_candidate(ctx, planned, state)
            except Exception as error:
                outcome["keep_failure"] = describe(error)
    except Exception as error:  # bookkeeping never blocks the restore
        outcome["bookkeeping_failure"] = describe(error)
    finally:
        try:
            if kept is None:
                outcome["restore"] = restore(ctx, planned, state)
            else:
                outcome["kept"] = kept
        finally:
            probe_stopped = stop_probe(ctx, manifest)
        save()
        ctx.say(step="restore" if kept is None else "keep", result=outcome.get("restore") or kept,
                probe=(manifest.get("probe") or {}).get("stop"))
    if summary is None:
        try:
            set_sigint(interrupt_once)  # one Ctrl-C skips the collection
            summary = finish(ctx, planned, manifest, directory, save)
            ignore_sigint()
        except KeyboardInterrupt:
            manifest.setdefault("collect_notes", []).append(
                "collection interrupted; copy the files by hand and run --analyze on this directory")
    outcome["verdict"] = summary["verdict"] if summary else None
    outcome["state"] = dict(state)
    save()
    if summary is not None:
        try:
            analyze_run(directory, manifest)  # again, so summary.md records the restore
        except Exception as error:
            manifest.setdefault("collect_notes", []).append("final summary failed: " + describe(error))
            save()
    restored = (kept is not None or (state["file"] == "original" and state["running"] == "original")) and probe_stopped
    ctx.say(step="done", verdict=outcome["verdict"], completed=completed, failure=failure, state=state,
            summary=str(directory / "summary.md"), restore_commands=None if restored else planned["restore"],
            manifest_error=unsaved[0] if unsaved else None)
    if not restored or failure:
        return 3
    return 0 if summary and summary["verdict"] == PASS else 1


# --- analysis --------------------------------------------------------------

class Spans:
    """(start, end) per timed row, end = its t and start = t - value: when
    an LA live call or a probe request was in flight."""

    def __init__(self, rows):
        self.found = sorted((r["_t"] - r["value_s"], r["_t"]) for r in rows)
        self.starts = [start for start, _ in self.found]

    def __contains__(self, t):
        i = bisect.bisect_right(self.starts, t) - 1
        return any(self.found[j][0] <= t <= self.found[j][1] for j in range(max(0, i - 1), i + 1))


def inside(t, intervals):
    return any(a <= t < b for a, b in intervals)


class Data:
    def __init__(self, rows, edges, thermal, yields, notes):
        self.series = {}
        for row in rows:
            self.series.setdefault((row["pipeline"], row["metric"]), []).append(row)
        for series in self.series.values():
            series.sort(key=lambda r: r["_t"])
        # By time only: a thermal rung may be None, which never compares with a word.
        self.edges, self.thermal = sorted(edges, key=operator.itemgetter(0)), sorted(thermal, key=operator.itemgetter(0))
        self.yields, self.notes = sorted(yields, key=operator.itemgetter(0)), notes
        # Any LA live execution holds a vLLM slot: the S0.4 total when logged, else the backend seconds.
        timed = ("total", "total_other") if ("qwen_live_la", "total") in self.series else ("backend", "backend_other")
        self.live_spans = Spans([r for m in timed for r in self.series.get(("qwen_live_la", m), [])])
        self.probe_spans = Spans(self.series.get(("voice_probe", "e2e"), []))
        self.probes = probe_results(rows)
        # A live request reached LA: a la-fact-log-1 line (any outcome) or an LA live backend line.
        self.activity = sorted(r["_t"] for (pipeline, metric), series in self.series.items()
                               if pipeline in ("qwen_live_victoria", "qwen_live_la") and metric.startswith("outcome:")
                               for r in series)

    def rows(self, pipeline, metric, start, end):
        return [r for r in self.series.get((pipeline, metric), []) if start <= r["_t"] < end]

    def values(self, pipeline, metric, start, end, intervals=None):
        return [r["value_s"] for r in self.rows(pipeline, metric, start, end)
                if intervals is None or inside(r["_t"], intervals)]


def probe_results(rows):
    """(t, ttft) per finished voice probe, by time: its TTFT when it ended ok,
    else None, a failure the guard counts as unbounded (a timeout or an error
    is the slowest voice answer there is). A probe cut off before its outcome
    line is left out."""
    found = {}
    for row in rows:
        if row["pipeline"] == "voice_probe" and (row["metric"] == "ttft" or row["metric"].startswith("outcome:")):
            found.setdefault(row["source"], {})["ttft" if row["metric"] == "ttft" else "outcome"] = row
    results = []
    for probe in found.values():
        outcome, ttft = probe.get("outcome"), probe.get("ttft")
        if outcome is None:
            continue
        if outcome["metric"] == "outcome:ok" and ttft is not None:
            results.append((ttft["_t"], ttft["value_s"]))
        else:
            results.append((outcome["_t"], None))
    return sorted(results, key=operator.itemgetter(0))


def occupied(edges, process_start, start, end):
    """Occupied intervals in [start, end) from one controller process's
    edges (its reader starts with no reading: vacant until an 'on' edge)."""
    on = False
    for t, to in edges:
        if process_start <= t < start:
            on = to == "on"
    intervals, since = [], start
    for t, to in edges:
        if not start <= t < end:
            continue
        if on and to == "off":
            intervals.append((since, t))
            on = False
        elif not on and to == "on":
            since, on = t, True
    if on:
        intervals.append((since, end))
    return intervals


def queue_busy(data, start, end, intervals, threshold):
    """(busy, n): gauge reads while occupied that meet the pilot's yield rule,
    leaving out the in-flight live request and the probe's own."""
    def pick(metric):
        return {r["source"]: r for r in data.rows("vllm", metric, start, end) if r["method"] == "voice_probe_vllm_gauge"}
    running, waiting = pick("num_requests_running"), pick("num_requests_waiting")
    busy = n = 0
    for source, row in running.items():
        other = waiting.get(source)
        if other is None or not inside(row["_t"], intervals):
            continue
        own = int(row["_t"] in data.live_spans) + int(row["_t"] in data.probe_spans)
        n += 1
        if other["value_s"] > 0 or row["value_s"] - own >= threshold:
            busy += 1
    return busy, n


def thermal(data, start, end):
    """(rung onsets, seconds at shed or stop) in [start, end)."""
    level = None
    for t, rung, _, _ in data.thermal:
        if t < start:
            level = rung
    onsets, shed, since = 0, 0.0, start
    for t, rung, _, _ in data.thermal:
        if not start <= t < end:
            continue
        if level is not None:
            shed += t - since
        if rung is not None and level is None:
            onsets += 1
        level, since = rung, t
    if level is not None:
        shed += end - since
    return onsets, shed


def outage(data, start, end):
    """Seconds in [start, end) while the pilot logged its yield metrics
    unavailable: it then yields every live tick."""
    down = False
    for t, status in data.yields:
        if t < start:
            down = status != "available"
    total, since = 0.0, start
    for t, status in data.yields:
        if not start <= t < end:
            continue
        if down:
            total += t - since
        down, since = status != "available", t
    if down:
        total += end - since
    return total


def explained(data, start, end, threshold):
    """Whether LA accounts for a silence: shedding or yield metrics unavailable
    for at least half of it, or at least half of its gauge reads busy (the
    gate was open and LA refused). Then it stays occupied, and the shedding
    and queue_busy criteria see it."""
    if thermal(data, start, end)[1] + outage(data, start, end) >= (end - start) / 2:
        return True
    busy, n = queue_busy(data, start, end, [(start, end)], threshold)
    return n > 0 and busy >= n / 2


def live_occupied(data, intervals, threshold):
    """The edge-occupied intervals less every stretch of more than QUIET_GAP_S
    with no live request reaching LA that LA does not explain. The controller
    logs no edge for on -> unknown (stale polls, an unavailable entity), so the
    gate can be closed long before the next 'off' edge."""
    kept = []
    for a, b in intervals:
        points = [a] + data.activity[bisect.bisect_right(data.activity, a):bisect.bisect_left(data.activity, b)] + [b]
        since = a
        for p, q in zip(points, points[1:]):
            if q - p > QUIET_GAP_S and not explained(data, p, q, threshold):
                if p > since:
                    kept.append((since, p))
                since = q
        if b > since:
            kept.append((since, b))
    return kept


def window_metrics(data, window, settings):
    start, end = window["measure_from"], window["measure_until"]
    edge = occupied(data.edges, window["restart_at"], start, end)
    occ = live_occupied(data, edge, settings["yield_threshold"])
    minutes = sum(b - a for a, b in occ) / 60
    outcomes = {}
    for (pipeline, metric), rows in data.series.items():
        if pipeline == "voice_probe" and metric.startswith("outcome:"):
            count = sum(1 for r in rows if start <= r["_t"] < end)
            if count:
                outcomes[metric[len("outcome:"):]] = count
    probes = [(t, ttft) for t, ttft in data.probes if start <= t < end]
    facts = data.rows("qwen_live_victoria", "outcome:completed", start, end)
    calls = data.rows("qwen_live_la", "backend", start, end)
    la_outcomes = sum(len(data.rows(p, m, start, end)) for p, m in data.series
                      if p == "qwen_live_victoria" and m.startswith("outcome:"))
    busy, n = queue_busy(data, start, end, occ, settings["yield_threshold"])
    onsets, shed = thermal(data, start, end)
    return {"index": window.get("index"), "arm": window["arm"], "from": start, "until": end,
            "complete": window.get("complete", False), "notes": window.get("notes", []),
            "occupied_minutes": minutes, "edge_occupied_minutes": sum(b - a for a, b in edge) / 60,
            "ttft_all": [ttft for _, ttft in probes if ttft is not None],
            "ttft_occupied": [ttft for t, ttft in probes if ttft is not None and inside(t, occ)],
            "voice_failed_occupied": sum(1 for t, ttft in probes if ttft is None and inside(t, occ)),
            "outcomes": outcomes,
            "camera": data.values("camera_chat_la", "backend", start, end),
            "camera_failed": len(data.rows("camera_chat_la", "backend_other", start, end)),
            "facts": len(facts), "la_outcomes": la_outcomes, "live_calls": len(calls),
            "facts_occupied": sum(1 for r in facts if inside(r["_t"], occ)),
            "live_calls_occupied": sum(1 for r in calls if inside(r["_t"], occ)),
            "queue_busy": [busy, n],
            "thermal_onsets": onsets, "thermal_shed_seconds": shed,
            "shed_seconds_occupied": sum(thermal(data, a, b)[1] + outage(data, a, b) for a, b in occ),
            "thermal_lines": [[g, c] for t, _, g, c in data.thermal if start <= t < end],
            "yield_unavailable": sum(1 for t, s in data.yields if start <= t < end and s != "available"),
            "gpu": data.values("la_gpu", "gpu_temp_c", start, end),
            "tctl": data.values("la_cpu", "tctl_c", start, end)}


def pool(windows, arm):
    chosen = [w for w in windows if w["arm"] == arm]
    merged = {"arm": arm, "windows": len(chosen), "outcomes": {}, "queue_busy": [0, 0]}
    for key in ("ttft_all", "ttft_occupied", "camera", "gpu", "tctl", "thermal_lines"):
        merged[key] = [v for w in chosen for v in w[key]]
    for key in ("occupied_minutes", "edge_occupied_minutes", "voice_failed_occupied", "camera_failed", "facts",
                "facts_occupied", "la_outcomes", "live_calls", "live_calls_occupied", "thermal_onsets",
                "thermal_shed_seconds", "shed_seconds_occupied", "yield_unavailable"):
        merged[key] = sum(w[key] for w in chosen)
    for w in chosen:
        merged["queue_busy"] = [merged["queue_busy"][0] + w["queue_busy"][0], merged["queue_busy"][1] + w["queue_busy"][1]]
        for outcome, count in w["outcomes"].items():
            merged["outcomes"][outcome] = merged["outcomes"].get(outcome, 0) + count
    return merged


def p95(values, minimum):
    return report.percentile(values, .95) if len(values) >= minimum else None


def guard(arms, settings, cadences):
    """[(criterion, baseline, candidate, result)] and the verdict. cadences:
    the plan's arms, for each arm's expected live rate."""
    minimum, d2 = settings["min_samples"], settings["guard"]
    base, cand = arms["baseline"], arms["candidate"]
    rows = []

    def ratio(label, key, failed_key):
        # A request that did not end ok is the slowest of all: leaving it out
        # would judge the candidate only on the requests it let through.
        b = base[key] + [math.inf] * base[failed_key]
        c = cand[key] + [math.inf] * cand[failed_key]
        criterion = "%s: candidate ≤ %.2f × baseline" % (label, d2["guard_ratio_max"])
        if len(b) < minimum or len(c) < minimum:
            rows.append((criterion, "n=%d" % len(b), "n=%d" % len(c),
                         "%s (needs %d samples per arm)" % (report.NO_DATA, minimum)))
            return
        b95, c95 = report.percentile(b, .95), report.percentile(c, .95)

        def text(p, item):
            failed = ", %d failed" % item[failed_key] if item[failed_key] else ""
            return "%s (n=%d%s)" % ("failed" if math.isinf(p) else report.seconds(p), len(item[key]) +
                                    item[failed_key], failed)
        if math.isinf(b95):
            rows.append((criterion, text(b95, base), text(c95, cand),
                         "%s (the baseline p95 is a failed request)" % report.NO_DATA))
            return
        value = c95 / b95 if b95 > 0 else math.inf
        rows.append((criterion, text(b95, base), "%s, %.2f×" % (text(c95, cand), value),
                     report.HOLDS if value <= d2["guard_ratio_max"] else report.FAILS))
    ratio("voice TTFT p95 while occupied", "ttft_occupied", "voice_failed_occupied")
    ratio("camera-chat backend p95", "camera", "camera_failed")
    busy, n = cand["queue_busy"]
    criterion = "queue_busy share (estimated) < %.0f%% of occupied candidate time" % (100 * d2["queue_busy_share_max"])
    base_busy = base["queue_busy"]
    base_text = "%.1f%% (n=%d)" % (100 * base_busy[0] / base_busy[1], base_busy[1]) if base_busy[1] else "n=0"
    if n < minimum:
        rows.append((criterion, base_text, "n=%d" % n, "%s (needs %d gauge reads)" % (report.NO_DATA, minimum)))
    else:
        rows.append((criterion, base_text, "%.1f%% (n=%d)" % (100 * busy / n, n),
                     report.HOLDS if busy / n < d2["queue_busy_share_max"] else report.FAILS))
    gpu = p95(cand["gpu"], minimum)
    criterion = "LA GPU temperature p95 ≤ %g °C (candidate)" % d2["gpu_temp_p95_max_c"]
    base_gpu = p95(base["gpu"], minimum)
    base_text = "%.0f °C" % base_gpu if base_gpu is not None else "n=%d" % len(base["gpu"])
    if gpu is None:
        rows.append((criterion, base_text, "n=%d" % len(cand["gpu"]), "%s (needs %d reads)" % (report.NO_DATA, minimum)))
    else:
        rows.append((criterion, base_text, "%.0f °C (max %.0f)" % (gpu, max(cand["gpu"])),
                     report.HOLDS if gpu <= d2["gpu_temp_p95_max_c"] else report.FAILS))
    need = settings["min_occupied_minutes"]
    rows.append(("candidate cadence exercised: occupied ≥ %g min" % need, "%.1f min" % base["occupied_minutes"],
                 "%.1f min" % cand["occupied_minutes"],
                 report.HOLDS if cand["occupied_minutes"] >= need else "%s (too little occupancy)" % report.NO_DATA))
    # Voice failures below the 5% that move a p95: a share over the baseline's.
    shares = []
    for item in (base, cand):
        total = len(item["ttft_occupied"]) + item["voice_failed_occupied"]
        shares.append((item["voice_failed_occupied"] / total if total else None, total))
    criterion = "voice probe failures while occupied: candidate share ≤ baseline + %g points" % (
        100 * VOICE_FAILURE_MARGIN)
    cells = ["n=%d" % total if share is None else "%.1f%% (n=%d)" % (100 * share, total) for share, total in shares]
    if min(total for _, total in shares) < minimum:
        rows.append((criterion, cells[0], cells[1], "%s (needs %d probes per arm)" % (report.NO_DATA, minimum)))
    else:
        rows.append((criterion, cells[0], cells[1],
                     report.HOLDS if shares[1][0] <= shares[0][0] + VOICE_FAILURE_MARGIN else report.FAILS))
    # The live load the candidate is judged on must have run: a kill switch, a
    # paused perceiver or an all-yield LA would leave both arms without it.
    expected = min(60 / cadences["candidate"]["interval_seconds"], cadences["candidate"]["max_per_minute"])
    rates = [max(item["facts_occupied"], item["live_calls_occupied"]) / item["occupied_minutes"]
             if item["occupied_minutes"] >= 1 else None for item in (base, cand)]
    criterion = ("live load: candidate ≥ %.0f completed live requests per occupied minute (%.0f%% of its %g), "
                 "baseline above 0" % (LIVE_RATE_MIN_SHARE * expected, 100 * LIVE_RATE_MIN_SHARE, expected))
    rows.append((criterion, *("n/a" if rate is None else "%.1f per min" % rate for rate in rates),
                 report.HOLDS if None not in rates and rates[0] > 0 and rates[1] >= LIVE_RATE_MIN_SHARE * expected
                 else "%s (the cadence was not exercised)" % report.NO_DATA))
    shed = [item["shed_seconds_occupied"] / (60 * item["occupied_minutes"]) if item["occupied_minutes"] > 0 else None
            for item in (base, cand)]
    rows.append(("LA live shedding (thermal_shed, yield metrics unavailable) ≤ %.0f%% of occupied time, both arms"
                 % (100 * SHED_SHARE_MAX), *("n/a" if s is None else "%.1f%%" % (100 * s) for s in shed),
                 report.HOLDS if None not in shed and max(shed) <= SHED_SHARE_MAX
                 else "%s (LA held the live load back)" % report.NO_DATA))
    results = [row[3] for row in rows]
    if report.FAILS in results:
        verdict = FAIL
    elif all(result == report.HOLDS for result in results):
        verdict = PASS
    else:
        verdict = INSUFFICIENT
    return rows, verdict


def journal_lines(path, name, notes):
    """(epoch, message) from a journal export; unreadable lines are noted."""
    if not path.is_file():
        notes.append("%s missing" % name)
        return []
    try:
        return [(t, message) for t, message, _, _ in
                harvest.journal_records(path.read_text(encoding="utf-8", errors="replace").splitlines(), name)]
    except harvest.InputError as error:
        notes.append(str(error))
        return []


def load_samples(path, notes):
    """Harvest-format samples; a malformed line (a torn last line) is noted and skipped."""
    rows = []
    if not path.is_file():
        notes.append("%s missing" % path.name)
        return rows
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or set(row) != set(harvest.SAMPLE_KEYS) or not harvest.finite(row["value_s"]):
                raise ValueError
            rows.append({**row, "_t": harvest.parse_time(row["t"])})
        except (ValueError, TypeError, harvest.InputError):
            notes.append("%s:%d: not a sample; skipped" % (path.name, number))
    return rows


def as_rows(samples):
    return [{**s, "_t": harvest.parse_time(s["t"])} for s in samples]


def load_run(directory):
    directory = Path(directory)
    notes = []
    rows = load_samples(directory / "voice-probe.jsonl", notes)
    pilot = journal_lines(directory / "la-pilot.txt", "la-pilot.txt", notes)
    la = harvest.Harvest()
    try:
        la.la_journal((directory / "la-pilot.txt").read_text(encoding="utf-8", errors="replace").splitlines()
                      if (directory / "la-pilot.txt").is_file() else [], "la-pilot.txt")
    except harvest.InputError as error:
        notes.append(str(error))
    rows += as_rows(la.samples)
    facts = harvest.Harvest()
    path = directory / "fact-log-times.jsonl"
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                facts.fact_log([line], "fact-log-times.jsonl")
            except harvest.InputError as error:
                notes.append("fact-log-times.jsonl:%d: %s" % (number, error))
    else:
        notes.append("fact-log-times.jsonl missing")
    rows += as_rows(facts.samples)
    notes += la.notes + facts.notes
    thermal_rows, yields = [], []
    for t, message in pilot:
        found = THERMAL.search(message)
        if found:
            numbers = [float(v) if re.fullmatch(r"-?\d+(?:\.\d+)?", v) else None for v in found.group(2, 3)]
            thermal_rows.append((t, found.group(1), numbers[0], numbers[1]))
        found = YIELD.search(message)
        if found:
            yields.append((t, found.group(1)))
    edges = []
    for _, message in journal_lines(directory / "jetson-controller.txt", "jetson-controller.txt", notes):
        found = EDGE.search(message)
        if found:
            edges.append((float(found.group(2)), found.group(1)))
    return Data(rows, edges, thermal_rows, yields, notes)


def analyze(manifest, data):
    settings = manifest["plan"]["settings"]
    windows = [window_metrics(data, w, settings) for w in manifest["windows"]
               if "measure_from" in w and w["measure_until"] > w["measure_from"]]
    arms = {name: pool(windows, name) for name in ("baseline", "candidate")}
    rows, verdict = guard(arms, settings, manifest["plan"]["arms"])
    return {"schema": SCHEMA, "run": manifest["run"], "verdict": verdict, "guard": rows, "windows": windows,
            "arms": arms, "settings": settings, "arm_text": manifest["plan"]["arm_text"],
            "outcome": manifest.get("outcome"), "notes": data.notes + manifest.get("collect_notes", [])}


def _pair(values, minimum, fmt=report.seconds):
    if len(values) < minimum:
        return "n=%d" % len(values)
    return "%s / %s (n=%d)" % (fmt(report.percentile(values, .5)), fmt(report.percentile(values, .95)), len(values))


def _temps(values, minimum):
    if not values:
        return "n=0"
    return "%s / %.0f" % ("%.0f" % report.percentile(values, .95) if len(values) >= minimum else "n=%d" % len(values),
                         max(values))


def _cells(item, minimum):
    busy, n = item["queue_busy"]
    errors = sum(v for k, v in item["outcomes"].items() if k != "ok")
    live = max(item["facts_occupied"], item["live_calls_occupied"])
    per_minute = live / item["occupied_minutes"] if item["occupied_minutes"] >= 1 else None
    return ["%.1f" % item["occupied_minutes"], _pair(item["ttft_occupied"], minimum), _pair(item["ttft_all"], minimum),
            "%d of %d" % (errors, sum(item["outcomes"].values())),
            (report.seconds(report.percentile(item["camera"], .95)) + " (n=%d)" % len(item["camera"])
             if item["camera"] else "n=0") + (", %d failed" % item["camera_failed"] if item["camera_failed"] else ""),
            "%.1f" % per_minute if per_minute is not None else "n/a",
            "%.1f%% (n=%d)" % (100 * busy / n, n) if n else "n=0",
            "%d / %.0f s" % (item["thermal_onsets"], item["thermal_shed_seconds"]),
            _temps(item["gpu"], minimum), _temps(item["tctl"], minimum)]


HEADER = ("Occupied min | Voice TTFT p50 / p95, occupied | Voice TTFT p50 / p95, all | Voice errors | "
          "Camera backend p95 | Live requests per occupied min | queue_busy (est.) | thermal_shed onsets / shed | "
          "GPU °C p95 / max | Tctl °C p95 / max")


def render(summary):
    minimum, d2 = summary["settings"]["min_samples"], summary["settings"]["guard"]
    out = ["# S1.4 cadence A/B, run %s" % summary["run"], "",
           "**Verdict: %s.** Baseline: %s. Candidate: %s." % (
               summary["verdict"], summary["arm_text"]["baseline"], summary["arm_text"]["candidate"]),
           "Guard (slo.json D2): ratio ≤ %.2f, queue_busy < %.0f%%, GPU p95 ≤ %g °C; at least %d samples per "
           "statistic; percentiles nearest-rank." % (d2["guard_ratio_max"], 100 * d2["queue_busy_share_max"],
                                                     d2["gpu_temp_p95_max_c"], minimum), "",
           "## Guard", "", "| Criterion | Baseline | Candidate | Result |", "|---|---|---|---|"]
    out += ["| %s | %s | %s | %s |" % tuple(row) for row in summary["guard"]]
    out += ["", "## Per arm", "", "| Arm | Windows | " + HEADER + " |", "|---" * 12 + "|"]
    for name in ("baseline", "candidate"):
        item = summary["arms"][name]
        out.append("| %s | %d | %s |" % (name, item["windows"], " | ".join(_cells(item, minimum))))
    out += ["", "## Per window", "", "| # | Arm | Measured (UTC) | " + HEADER + " |", "|---" * 13 + "|"]
    for item in summary["windows"]:
        span = "%s to %s%s" % (harvest.format_time(item["from"])[:19], harvest.format_time(item["until"])[11:19],
                               "" if item["complete"] else " (partial)")
        out.append("| %s | %s | %s | %s |" % (item["index"], item["arm"], span, " | ".join(_cells(item, minimum))))
    out += ["", "## Method", "",
            "- Occupied: the controller's occupancy edge lines, less every stretch of more than %g s with no live "
            "request reaching LA (no la-fact-log-1 line, no LA live backend line) unless LA shed, had its yield "
            "metrics unavailable or a busy queue for at least half of it. The controller logs no edge for on -> "
            "unknown." % QUIET_GAP_S,
            "- Voice TTFT: tools/la-latency/voice_probe.py on LA through the metrics sidecar (HA voice's route), "
            "one minimal streaming completion per slot; 'occupied' keeps only probes inside the occupied "
            "intervals. A probe that did not end ok counts as unbounded in the guard's p95.",
            "- Camera chat: the LA pilot's 'camera pilot backend seconds=' lines in each window; a result other "
            "than ok counts as unbounded in the guard's p95.",
            "- Live requests: la-fact-log-1 'completed' outcomes (available_at), or the LA pilot's completed live "
            "backend lines when more, per occupied minute.",
            "- queue_busy (estimated): vLLM gauges read on LA; a read counts when waiting > 0 or running, less the "
            "in-flight live and probe requests, is at least %d (the pilot's yield rule), over occupied time. LA "
            "logs no line per refusal." % summary["settings"]["yield_threshold"],
            "- thermal_shed: the pilot's rung lines; onsets and seconds at shed or stop.",
            "- GPU °C: the sidecar's gpu_temp_c per probe slot; Tctl: k10temp on LA."]
    for item in summary["windows"]:
        for note in item["notes"]:
            out.append("- Window %s: %s" % (item["index"], note))
        quiet = item["edge_occupied_minutes"] - item["occupied_minutes"]
        if quiet >= 1:
            out.append("- Window %s: %.1f of %.1f edge-occupied minutes had no live request for over %g s and were "
                       "left out (the gate was probably closed: occupancy unknown)."
                       % (item["index"], quiet, item["edge_occupied_minutes"], QUIET_GAP_S))
        if item["yield_unavailable"]:
            out.append("- Window %s: %d 'live scene yield metrics unavailable' lines; every live tick yields while "
                       "they last, so the queue_busy estimate is a lower bound." % (item["index"],
                                                                                    item["yield_unavailable"]))
    outcome = summary.get("outcome") or {}
    if outcome:
        out += ["", "## Run", "", "```", json.dumps(outcome, indent=2, sort_keys=True), "```"]
    if summary["notes"]:
        out += ["", "## Notes", ""] + ["- " + note for note in summary["notes"][:40]]
        if len(summary["notes"]) > 40:
            out.append("- (%d more)" % (len(summary["notes"]) - 40))
    return "\n".join(out) + "\n"


def analyze_run(directory, manifest=None):
    """The summary of a collected run; a live run passes its manifest from memory
    (the file on disk may lag when a save failed)."""
    directory = Path(directory)
    if manifest is None:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    data = load_run(directory)
    summary = analyze(manifest, data)
    with open(directory / "samples.jsonl", "w", encoding="utf-8", newline="\n") as handle:
        harvest.write_samples([r for series in data.series.values() for r in series], handle)
    write_json(directory / "summary.json", summary)
    (directory / "summary.md").write_text(render(summary), encoding="utf-8", newline="\n")
    return summary


# --- command line ----------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the plan; without it nothing is changed")
    parser.add_argument("--analyze", metavar="RUN_DIR", help="recompute the summary of a collected run, offline")
    parser.add_argument("--windows", type=int, default=6, help="alternating windows, baseline first (2-12)")
    parser.add_argument("--window-minutes", type=float, default=30.0, help="measured minutes per window (5-60)")
    parser.add_argument("--settle-seconds", type=float, default=60.0, help="left out after each restart (0-600)")
    parser.add_argument("--baseline", type=arm, default=arm(BASELINE), help="interval/max_per_minute/hold (%s)"
                        % BASELINE)
    parser.add_argument("--candidate", type=arm, default=arm(CANDIDATE), help="interval/max_per_minute/hold (%s)"
                        % CANDIDATE)
    parser.add_argument("--keep-candidate-on-pass", action="store_true",
                        help="leave the candidate in place, but only after a PASS")
    parser.add_argument("--allow-unreadable-journal", action="store_true",
                        help="--apply although the controller journal is unreadable: each restart is accepted on the "
                             "fact-log fallback, but without the journal's occupancy edges the verdict can only be "
                             "INSUFFICIENT")
    parser.add_argument("--probe-url", default=voice_probe.DEFAULT_URL, help="HA voice's base URL, as seen from LA")
    parser.add_argument("--probe-interval", type=float, default=15.0, help="seconds per voice probe (5-300)")
    parser.add_argument("--load-interval", type=float, default=1.0, help="seconds per vLLM gauge read (0.5-60)")
    parser.add_argument("--vllm-container", default=VLLM_CONTAINER, help="read vLLM's gauges at this container's "
                                                                         "compose-network address (default %(default)s)")
    parser.add_argument("--vllm-metrics-url", help="read vLLM's gauges here instead (an http:// URL reachable from LA)")
    parser.add_argument("--yield-threshold", type=int, default=3, help="the pilot's yield_running_threshold (1-16)")
    parser.add_argument("--min-occupied-minutes", type=float, default=30.0,
                        help="occupied candidate minutes needed for a verdict")
    parser.add_argument("--min-samples", type=int, help="per statistic (default: slo.json)")
    parser.add_argument("--session-wait", type=float, default=900.0,
                        help="seconds to wait for a Lab session to end before a restart (60-3600)")
    parser.add_argument("--out", help="run directory (default ~/la-latency-runs/cadence-ab-<run>)")
    parser.add_argument("--jetson", default=JETSON)
    parser.add_argument("--la", default=LA)
    parser.add_argument("--live-config", default=LIVE_CONFIG)
    parser.add_argument("--slo", default=str(HERE / "slo.json"))
    args = parser.parse_args(argv)
    if args.analyze and args.apply:
        parser.error("--analyze reads a collected run; it cannot be combined with --apply")
    if not 2 <= args.windows <= 12 or not 5 <= args.window_minutes <= 60 or not 0 <= args.settle_seconds <= 600:
        parser.error("--windows 2-12, --window-minutes 5-60, --settle-seconds 0-600")
    if not 5 <= args.probe_interval <= 300 or not 0.5 <= args.load_interval <= 60:
        parser.error("--probe-interval 5-300, --load-interval 0.5-60")
    if not 1 <= args.yield_threshold <= 16 or not 60 <= args.session_wait <= 3600:
        parser.error("--yield-threshold 1-16, --session-wait 60-3600")
    if args.min_samples is not None and args.min_samples < 1:
        parser.error("--min-samples must be positive")
    if same_cadence(args.baseline, args.candidate):
        parser.error("the candidate must differ from the baseline")
    try:
        voice_probe.base_origin(args.probe_url)
        if args.vllm_metrics_url:
            voice_probe.metrics_url(args.vllm_metrics_url)
    except ValueError as error:
        parser.error(str(error))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", args.vllm_container):
        parser.error("--vllm-container must be a container name")
    return args


def main(argv=None, runner=run_command, out=print, clock=time.time, sleep=time.sleep):
    args = parse_args(argv)
    if args.analyze:
        try:
            summary = analyze_run(args.analyze)
        except (OSError, ValueError, KeyError) as error:
            out(json.dumps({"error": describe(error)}))
            return 2
        out(json.dumps({"verdict": summary["verdict"], "summary": str(Path(args.analyze) / "summary.md")}))
        return 0 if summary["verdict"] == PASS else 1
    ctx = Context(args, runner, out, clock, sleep)
    try:
        planned = plan(ctx)
    except (Refused, Incomplete, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as error:
        out(json.dumps({"refused": describe(error)}))
        return 2
    out(json.dumps({"mode": "apply" if args.apply else "dry-run", **planned}, indent=2, sort_keys=True))
    if planned["refusals"]:
        return 2
    if not args.apply:
        return 0
    return apply(ctx, planned)


if __name__ == "__main__":
    sys.exit(main())
